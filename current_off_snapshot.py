import json
from datetime import datetime
from pathlib import Path
from typing import Dict, Iterable, List, Optional


CURRENT_OFF_SNAPSHOT_RELATIVE_PATH = Path("runtime") / "current_off_snapshot.json"


def _coerce_datetime(value) -> Optional[datetime]:
    if not value:
        return None
    if isinstance(value, datetime):
        return value
    if hasattr(value, "strftime"):
        return value
    try:
        return datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None


def format_duration_minutes(duration_minutes: int) -> str:
    return f"{max(int(duration_minutes or 0), 0)} phút"


def get_snapshot_output_path(base_dir: str) -> Path:
    return Path(base_dir).resolve() / CURRENT_OFF_SNAPSHOT_RELATIVE_PATH


def build_snapshot_row(
    snapshot,
    state: Optional[Dict],
    *,
    suppressed_by_pattern: bool = False,
    suppressed_by_wide_area: bool = False,
    suppression_reason: str = "",
) -> Dict:
    state = state or {}
    first_off_time = _coerce_datetime(state.get("first_off_time")) or _coerce_datetime(getattr(snapshot, "measured_at", None))
    measured_at = _coerce_datetime(getattr(snapshot, "measured_at", None))
    if first_off_time and measured_at:
        duration_minutes = max(int((measured_at - first_off_time).total_seconds() // 60), 0)
    else:
        duration_minutes = 0
    return {
        "subscriber_key": snapshot.subscriber_key,
        "port_id": snapshot.port_id,
        "parent_port_key": snapshot.parent_port_key,
        "batch_id": snapshot.batch_id,
        "measured_at": measured_at.isoformat() if measured_at else None,
        "onu_last_off": getattr(snapshot, "onu_last_off", "") or "",
        "onu_last_on": getattr(snapshot, "onu_last_on", "") or "",
        "current_state": state.get("current_state", ""),
        "current_status": getattr(snapshot, "status", ""),
        "ma_tb": getattr(snapshot, "ma_tb", "") or "",
        "ten_tb": getattr(snapshot, "ten_tb", "") or "",
        "ma_men": getattr(snapshot, "ma_men", "") or "",
        "olt_name": getattr(snapshot, "olt_name", "") or "",
        "doi_vt": getattr(snapshot, "doi_vt", "") or "",
        "diachi_ld": getattr(snapshot, "diachi_ld", "") or "",
        "dienthoai_lh": getattr(snapshot, "dienthoai_lh", "") or "",
        "ten_nvkt_db": getattr(snapshot, "ten_nvkt_db", "") or "",
        "first_off_time": first_off_time.isoformat() if first_off_time else None,
        "duration_minutes": duration_minutes,
        "duration_text": format_duration_minutes(duration_minutes),
        "suppressed_by_pattern": bool(suppressed_by_pattern),
        "suppressed_by_wide_area": bool(suppressed_by_wide_area),
        "suppression_reason": suppression_reason or "",
    }


def build_current_off_snapshot(
    current_offs: Iterable,
    state_rows: Dict[str, Dict],
    *,
    exclusion_list: Optional[Iterable[str]] = None,
    wide_area_subscriber_keys: Optional[Iterable[str]] = None,
) -> List[Dict]:
    exclusion_set = {ma_tb for ma_tb in (exclusion_list or []) if ma_tb}
    wide_area_set = set(wide_area_subscriber_keys or [])
    rows: List[Dict] = []
    for snapshot in current_offs:
        ma_tb = getattr(snapshot, "ma_tb", "") or ""
        suppressed_by_pattern = ma_tb in exclusion_set
        suppressed_by_wide_area = snapshot.subscriber_key in wide_area_set
        reasons = []
        if suppressed_by_pattern:
            reasons.append("pattern_exclusion")
        if suppressed_by_wide_area:
            reasons.append("wide_area")
        rows.append(
            build_snapshot_row(
                snapshot,
                state_rows.get(snapshot.subscriber_key),
                suppressed_by_pattern=suppressed_by_pattern,
                suppressed_by_wide_area=suppressed_by_wide_area,
                suppression_reason=",".join(reasons),
            )
        )
    rows.sort(key=lambda row: (row["doi_vt"], row["ten_nvkt_db"], row["subscriber_key"]))
    return rows


def build_snapshot_payload(
    batch_id: str,
    measured_at,
    subscribers: List[Dict],
    *,
    generated_at: Optional[datetime] = None,
) -> Dict:
    measured_at_dt = _coerce_datetime(measured_at)
    pattern_count = sum(1 for row in subscribers if row.get("suppressed_by_pattern"))
    wide_area_count = sum(1 for row in subscribers if row.get("suppressed_by_wide_area"))
    group_count_by_doi_vt: Dict[str, int] = {}
    for row in subscribers:
        doi_vt = row.get("doi_vt") or "Không xác định"
        group_count_by_doi_vt[doi_vt] = group_count_by_doi_vt.get(doi_vt, 0) + 1
    return {
        "batch_id": batch_id,
        "measured_at": measured_at_dt.isoformat() if measured_at_dt else None,
        "generated_at": (generated_at or datetime.now()).isoformat(timespec="seconds"),
        "summary": {
            "total_off_subscribers": len(subscribers),
            "active_individual_alerts": sum(
                1
                for row in subscribers
                if not row.get("suppressed_by_pattern") and not row.get("suppressed_by_wide_area")
            ),
            "suppressed_by_pattern": pattern_count,
            "suppressed_by_wide_area": wide_area_count,
            "suppressed_total": sum(
                1
                for row in subscribers
                if row.get("suppressed_by_pattern") or row.get("suppressed_by_wide_area")
            ),
            "group_count_by_doi_vt": group_count_by_doi_vt,
        },
        "subscribers": subscribers,
    }


def write_snapshot_json(payload: Dict, output_file) -> None:
    output_path = Path(output_file)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = output_path.with_suffix(output_path.suffix + ".tmp")
    temp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temp_path.replace(output_path)


def load_snapshot_payload(output_file) -> Dict:
    output_path = Path(output_file)
    if not output_path.exists():
        return {}
    return json.loads(output_path.read_text(encoding="utf-8"))


def load_snapshot_rows_for_batch(output_file, batch_id: str) -> List[Dict]:
    payload = load_snapshot_payload(output_file)
    if payload.get("batch_id") != batch_id:
        return []
    rows = payload.get("subscribers", [])
    if isinstance(rows, list):
        return rows
    return []
