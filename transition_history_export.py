import csv
import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Set, Tuple

CSV_ENCODING = "utf-8-sig"


CSV_FIELDNAMES = [
    "ma_tb",
    "ten_tb",
    "subscriber_key",
    "doi_vt",
    "ten_nvkt_db",
    "transition_type",
    "from_status",
    "to_status",
    "from_batch_id",
    "to_batch_id",
    "from_time",
    "to_time",
    "transition_time",
    "cycle_duration_hours",
    "on_to_off_count",
    "off_to_on_count",
    "total_valid_transitions",
]

SUMMARY_CSV_FIELDNAMES = [
    "ma_tb",
    "ten_tb",
    "subscriber_key",
    "doi_vt",
    "ten_nvkt_db",
    "on_to_off_count",
    "off_to_on_count",
    "total_valid_transitions",
    "latest_transition_type",
    "latest_transition_time",
    "latest_cycle_duration_hours",
    "latest_from_batch_id",
    "latest_to_batch_id",
]


def _connect_readonly(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{Path(db_path).resolve()}?mode=ro", uri=True, timeout=60)
    conn.row_factory = sqlite3.Row
    return conn


def _normalize_status(raw_status) -> str:
    status = str(raw_status or "").upper()
    if status == "PORT_DOWN":
        return "PORT_DOWN"
    if "OFF" in status:
        return "OFF"
    if "ON" in status:
        return "ON"
    return "UNKNOWN"


def _parse_measured_at(row: Dict) -> datetime:
    return datetime.fromisoformat(f"{row['NgayDo']}T{row['ThoiGianDo']}")


def _load_wide_area_keys(db_path: str) -> Set[Tuple[str, str]]:
    keys: Set[Tuple[str, str]] = set()
    with _connect_readonly(db_path) as conn:
        try:
            for row in conn.execute(
                "SELECT batch_id, subscriber_key FROM outage_alerts WHERE suppressed_by_wide_area = 1"
            ):
                keys.add((row["subscriber_key"], row["batch_id"]))

            for row in conn.execute("SELECT batch_id, subscriber_keys_json FROM wide_area_alerts"):
                try:
                    subscriber_keys = json.loads(row["subscriber_keys_json"] or "[]")
                except json.JSONDecodeError:
                    subscriber_keys = []
                for subscriber_key in subscriber_keys:
                    keys.add((subscriber_key, row["batch_id"]))
        except sqlite3.OperationalError:
            return keys
    return keys


def _load_danhba_map(source_db_path: str) -> Dict[str, Dict]:
    danhba_map: Dict[str, Dict] = {}
    with _connect_readonly(source_db_path) as conn:
        for row in conn.execute(
            """
            SELECT
                sub AS subscriber_key,
                COALESCE(Ma_Tb, '') AS ma_tb,
                COALESCE(Ten_Tb, '') AS ten_tb,
                COALESCE(DOI_VT, '') AS doi_vt,
                COALESCE(TEN_NVKT_DB, '') AS ten_nvkt_db
            FROM danhba
            WHERE COALESCE(sub, '') <> ''
            """
        ):
            danhba_map[row["subscriber_key"]] = dict(row)
    return danhba_map


def _iter_measurement_rows(db_path: str, source_db_path: str) -> Iterable[Dict]:
    danhba_map = _load_danhba_map(source_db_path)
    with _connect_readonly(db_path) as conn:
        cursor = conn.execute(
            """
            SELECT
                "Cổng" AS subscriber_key,
                m.batch_id,
                m.onuStatusStr,
                m.NgayDo,
                m.ThoiGianDo
            FROM onu_measurements m
            ORDER BY "Cổng", NgayDo, ThoiGianDo, id
            """
        )
        for row in cursor:
            merged = dict(row)
            metadata = danhba_map.get(merged["subscriber_key"])
            if not metadata:
                continue
            merged.update(metadata)
            yield merged


def _build_transition_row(
    metadata: Dict,
    previous_row: Dict,
    current_row: Dict,
    transition_type: str,
) -> Dict:
    cycle_duration_hours = (
        current_row["measured_at"] - previous_row["measured_at"]
    ).total_seconds() / 3600
    return {
        "ma_tb": metadata["ma_tb"],
        "ten_tb": metadata["ten_tb"],
        "subscriber_key": metadata["subscriber_key"],
        "doi_vt": metadata["doi_vt"],
        "ten_nvkt_db": metadata["ten_nvkt_db"],
        "transition_type": transition_type,
        "from_status": previous_row["status"],
        "to_status": current_row["status"],
        "from_batch_id": previous_row["batch_id"],
        "to_batch_id": current_row["batch_id"],
        "from_time": previous_row["measured_at"].isoformat(),
        "to_time": current_row["measured_at"].isoformat(),
        "transition_time": current_row["measured_at"].isoformat(),
        "cycle_duration_hours": cycle_duration_hours,
    }


def _finalize_subscriber_rows(rows: List[Dict], on_to_off_count: int, off_to_on_count: int) -> List[Dict]:
    if on_to_off_count < 3 or off_to_on_count < 3:
        return []

    total_valid_transitions = on_to_off_count + off_to_on_count
    finalized = []
    for row in rows:
        enriched = dict(row)
        enriched["on_to_off_count"] = on_to_off_count
        enriched["off_to_on_count"] = off_to_on_count
        enriched["total_valid_transitions"] = total_valid_transitions
        finalized.append(enriched)
    return finalized


def _as_excel_text(value: str) -> str:
    escaped = str(value or "").replace('"', '""')
    return f'="{escaped}"'


def _prepare_rows_for_csv(rows: List[Dict]) -> List[Dict]:
    prepared = []
    for row in rows:
        item = dict(row)
        item["ma_tb"] = _as_excel_text(item.get("ma_tb", ""))
        prepared.append(item)
    return prepared


def collect_qualified_transition_rows(measurement_db_path: str, source_db_path: str) -> List[Dict]:
    wide_area_keys = _load_wide_area_keys(measurement_db_path)
    output_rows: List[Dict] = []

    current_subscriber_key: Optional[str] = None
    current_metadata: Optional[Dict] = None
    previous_valid_row: Optional[Dict] = None
    current_rows: List[Dict] = []
    current_outage_is_wide_area = False
    on_to_off_count = 0
    off_to_on_count = 0

    for raw_row in _iter_measurement_rows(measurement_db_path, source_db_path):
        subscriber_key = raw_row["subscriber_key"]
        if current_subscriber_key != subscriber_key:
            if current_subscriber_key is not None:
                output_rows.extend(
                    _finalize_subscriber_rows(current_rows, on_to_off_count, off_to_on_count)
                )

            current_subscriber_key = subscriber_key
            current_metadata = {
                "ma_tb": raw_row["ma_tb"],
                "ten_tb": raw_row["ten_tb"],
                "subscriber_key": subscriber_key,
                "doi_vt": raw_row["doi_vt"],
                "ten_nvkt_db": raw_row["ten_nvkt_db"],
            }
            previous_valid_row = None
            current_rows = []
            current_outage_is_wide_area = False
            on_to_off_count = 0
            off_to_on_count = 0

        status = _normalize_status(raw_row["onuStatusStr"])
        if status == "UNKNOWN":
            continue
        if status == "PORT_DOWN":
            previous_valid_row = None
            continue

        current_row = {
            "batch_id": raw_row["batch_id"],
            "status": status,
            "measured_at": _parse_measured_at(raw_row),
        }

        if previous_valid_row is None:
            previous_valid_row = current_row
            continue

        if previous_valid_row["status"] == current_row["status"]:
            previous_valid_row = current_row
            continue

        if previous_valid_row["status"] == "ON" and current_row["status"] == "OFF":
            current_outage_is_wide_area = (
                current_metadata["subscriber_key"],
                current_row["batch_id"],
            ) in wide_area_keys
            if not current_outage_is_wide_area:
                current_rows.append(
                    _build_transition_row(current_metadata, previous_valid_row, current_row, "ON->OFF")
                )
                on_to_off_count += 1
        elif previous_valid_row["status"] == "OFF" and current_row["status"] == "ON":
            if not current_outage_is_wide_area:
                current_rows.append(
                    _build_transition_row(current_metadata, previous_valid_row, current_row, "OFF->ON")
                )
                off_to_on_count += 1
            current_outage_is_wide_area = False

        previous_valid_row = current_row

    if current_subscriber_key is not None:
        output_rows.extend(_finalize_subscriber_rows(current_rows, on_to_off_count, off_to_on_count))

    return output_rows


def export_transition_history_csv(
    measurement_db_path: str,
    source_db_path: str,
    output_path: str,
) -> int:
    rows = collect_qualified_transition_rows(measurement_db_path, source_db_path)
    csv_rows = _prepare_rows_for_csv(rows)
    output_file = Path(output_path)
    output_file.parent.mkdir(parents=True, exist_ok=True)

    with output_file.open("w", newline="", encoding=CSV_ENCODING) as file_obj:
        writer = csv.DictWriter(file_obj, fieldnames=CSV_FIELDNAMES)
        writer.writeheader()
        writer.writerows(csv_rows)

    return len(rows)


def build_summary_rows(transition_rows: List[Dict]) -> List[Dict]:
    summary_by_subscriber: Dict[str, Dict] = {}
    for row in transition_rows:
        subscriber_key = row["subscriber_key"]
        summary = summary_by_subscriber.get(subscriber_key)
        if summary is None:
            summary = {
                "ma_tb": row["ma_tb"],
                "ten_tb": row["ten_tb"],
                "subscriber_key": subscriber_key,
                "doi_vt": row["doi_vt"],
                "ten_nvkt_db": row["ten_nvkt_db"],
                "on_to_off_count": int(row["on_to_off_count"]),
                "off_to_on_count": int(row["off_to_on_count"]),
                "total_valid_transitions": int(row["total_valid_transitions"]),
                "latest_transition_type": row["transition_type"],
                "latest_transition_time": row["transition_time"],
                "latest_cycle_duration_hours": float(row["cycle_duration_hours"]),
                "latest_from_batch_id": row["from_batch_id"],
                "latest_to_batch_id": row["to_batch_id"],
            }
            summary_by_subscriber[subscriber_key] = summary
            continue

        if row["transition_time"] > summary["latest_transition_time"]:
            summary["latest_transition_type"] = row["transition_type"]
            summary["latest_transition_time"] = row["transition_time"]
            summary["latest_cycle_duration_hours"] = float(row["cycle_duration_hours"])
            summary["latest_from_batch_id"] = row["from_batch_id"]
            summary["latest_to_batch_id"] = row["to_batch_id"]

    return sorted(summary_by_subscriber.values(), key=lambda item: (item["ma_tb"], item["subscriber_key"]))


def export_transition_summary_csv(
    measurement_db_path: str,
    source_db_path: str,
    output_path: str,
) -> int:
    transition_rows = collect_qualified_transition_rows(measurement_db_path, source_db_path)
    rows = build_summary_rows(transition_rows)
    csv_rows = _prepare_rows_for_csv(rows)
    output_file = Path(output_path)
    output_file.parent.mkdir(parents=True, exist_ok=True)

    with output_file.open("w", newline="", encoding=CSV_ENCODING) as file_obj:
        writer = csv.DictWriter(file_obj, fieldnames=SUMMARY_CSV_FIELDNAMES)
        writer.writeheader()
        writer.writerows(csv_rows)

    return len(rows)
