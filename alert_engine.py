from collections import defaultdict
from datetime import datetime
from time import perf_counter
from typing import Dict, Iterable, List, Optional

try:
    from .alert_db import AlertRepository
    from .doi_vt_mapping import normalize_doi_vt_name
    from .alert_models import (
        ALERT_OFF,
        PENDING_OFF,
        RECOVERED,
        STABLE_OFF,
        STABLE_ON,
        WATCHING,
        OutageAlert,
        RecoveryAlert,
        SubscriberSnapshot,
        WideAreaAlert,
    )
    from .notification_bridge import dispatch_batch_notifications
    from .pattern_exclusion import get_pattern_exclusion_list, update_exclusion_table
except ImportError:
    from alert_db import AlertRepository
    from doi_vt_mapping import normalize_doi_vt_name
    from alert_models import (
        ALERT_OFF,
        PENDING_OFF,
        RECOVERED,
        STABLE_OFF,
        STABLE_ON,
        WATCHING,
        OutageAlert,
        RecoveryAlert,
        SubscriberSnapshot,
        WideAreaAlert,
    )
    from notification_bridge import dispatch_batch_notifications
    from pattern_exclusion import get_pattern_exclusion_list, update_exclusion_table


MIN_STABLE_ON_CYCLES = 2
DEFAULT_WIDE_AREA_THRESHOLD = 5


def _emit(log, message: str):
    if log:
        log(message)


def normalize_status(raw_status) -> str:
    status = str(raw_status or "").upper()
    if "OFF" in status:
        return "OFF"
    if "ON" in status:
        return "ON"
    return "UNKNOWN"


def get_parent_port_key(subscriber_key: str) -> str:
    return subscriber_key.rsplit(":", 1)[0] if ":" in subscriber_key else subscriber_key


def get_olt_name(subscriber_key: str) -> str:
    return subscriber_key.split("_", 1)[0] if "_" in subscriber_key else subscriber_key


def _parse_measured_at(row: Dict) -> datetime:
    return datetime.fromisoformat(f"{row['NgayDo']}T{row['ThoiGianDo']}")


def _parse_optional_dt(value):
    if not value:
        return None
    if isinstance(value, datetime):
        return value
    return datetime.fromisoformat(str(value))


def _snapshot_from_row(row: Dict, metadata: Dict) -> SubscriberSnapshot:
    subscriber_key = row["subscriber_key"]
    doi_vt = normalize_doi_vt_name(metadata.get("DOI_VT") or "")
    return SubscriberSnapshot(
        subscriber_key=subscriber_key,
        parent_port_key=get_parent_port_key(subscriber_key),
        olt_name=get_olt_name(subscriber_key),
        batch_id=row["batch_id"],
        status=normalize_status(row.get("onuStatusStr")),
        measured_at=_parse_measured_at(row),
        ma_tb=(metadata.get("Ma_Tb") or "").strip(),
        ten_tb=(metadata.get("Ten_Tb") or "").strip(),
        ma_men=(metadata.get("Ma_Men") or "").strip(),
        doi_vt=doi_vt,
        diachi_ld=(metadata.get("DIACHI_LD") or "").strip(),
        dienthoai_lh=(metadata.get("DIENTHOAI_LH") or "").strip(),
        ten_nvkt_db=(metadata.get("TEN_NVKT_DB") or "").strip(),
        account_fiber=(row.get("accountFiber") or "").strip(),
    )


def _base_state(snapshot: SubscriberSnapshot, current_state: str, last_status: str, on_count: int, off_count: int,
                first_on_time, first_off_time, alert_sent_time, recovery_sent_time):
    return {
        "subscriber_key": snapshot.subscriber_key,
        "parent_port_key": snapshot.parent_port_key,
        "ma_tb": snapshot.ma_tb,
        "ten_tb": snapshot.ten_tb,
        "ma_men": snapshot.ma_men,
        "olt_name": snapshot.olt_name,
        "doi_vt": snapshot.doi_vt,
        "diachi_ld": snapshot.diachi_ld,
        "dienthoai_lh": snapshot.dienthoai_lh,
        "ten_nvkt_db": snapshot.ten_nvkt_db,
        "account_fiber": snapshot.account_fiber,
        "current_state": current_state,
        "last_status": last_status,
        "consecutive_on_count": on_count,
        "consecutive_off_count": off_count,
        "first_on_time": first_on_time,
        "first_off_time": first_off_time,
        "alert_sent_time": alert_sent_time,
        "recovery_sent_time": recovery_sent_time,
        "last_batch_id": snapshot.batch_id,
        "last_measure_time": snapshot.measured_at,
    }


def _transition(snapshot: SubscriberSnapshot, prev: Optional[Dict]):
    outage = None
    recovery = None

    if prev is None:
        if snapshot.status == "ON":
            return (
                _base_state(
                    snapshot, WATCHING, "ON", 1, 0, snapshot.measured_at, None, None, None
                ),
                outage,
                recovery,
            )
        if snapshot.status == "OFF":
            return (
                _base_state(
                    snapshot, STABLE_OFF, "OFF", 0, 1, None, snapshot.measured_at, None, None
                ),
                outage,
                recovery,
            )
        return (
            _base_state(snapshot, WATCHING, "UNKNOWN", 0, 0, None, None, None, None),
            outage,
            recovery,
        )

    current_state = prev["current_state"]
    last_status = prev.get("last_status") or ""
    on_count = prev.get("consecutive_on_count", 0) or 0
    off_count = prev.get("consecutive_off_count", 0) or 0
    first_on_time = _parse_optional_dt(prev.get("first_on_time"))
    first_off_time = _parse_optional_dt(prev.get("first_off_time"))
    alert_sent_time = _parse_optional_dt(prev.get("alert_sent_time"))
    recovery_sent_time = _parse_optional_dt(prev.get("recovery_sent_time"))

    if snapshot.status == "ON":
        on_count += 1
        off_count = 0
        if current_state == WATCHING:
            next_state = STABLE_ON if on_count >= MIN_STABLE_ON_CYCLES else WATCHING
            first_on_time = first_on_time or snapshot.measured_at
        elif current_state == STABLE_OFF:
            next_state = WATCHING
            on_count = 1
            first_on_time = snapshot.measured_at
        elif current_state == PENDING_OFF:
            next_state = STABLE_ON
            on_count = 1
        elif current_state == ALERT_OFF:
            next_state = RECOVERED
            on_count = 1
            outage_start = first_off_time or alert_sent_time or snapshot.measured_at
            duration = int((snapshot.measured_at - outage_start).total_seconds() / 60) if outage_start else 0
            recovery = RecoveryAlert(
                subscriber_key=snapshot.subscriber_key,
                parent_port_key=snapshot.parent_port_key,
                batch_id=snapshot.batch_id,
                ma_tb=snapshot.ma_tb,
                ten_tb=snapshot.ten_tb,
                olt_name=snapshot.olt_name,
                doi_vt=snapshot.doi_vt,
                diachi_ld=snapshot.diachi_ld,
                dienthoai_lh=snapshot.dienthoai_lh,
                ten_nvkt_db=snapshot.ten_nvkt_db,
                outage_time=alert_sent_time or first_off_time,
                recovery_time=snapshot.measured_at,
                outage_duration_minutes=max(duration, 0),
            )
            recovery_sent_time = snapshot.measured_at
        elif current_state == RECOVERED:
            next_state = STABLE_ON if on_count >= MIN_STABLE_ON_CYCLES else RECOVERED
        else:
            next_state = STABLE_ON

        return (
            _base_state(
                snapshot,
                next_state,
                "ON",
                on_count,
                off_count,
                first_on_time or snapshot.measured_at,
                first_off_time,
                alert_sent_time,
                recovery_sent_time,
            ),
            outage,
            recovery,
        )

    if snapshot.status == "OFF":
        off_count += 1
        on_count = 0
        if current_state == STABLE_ON:
            next_state = PENDING_OFF
            first_off_time = snapshot.measured_at
        elif current_state == PENDING_OFF:
            next_state = ALERT_OFF
            duration = int((snapshot.measured_at - first_off_time).total_seconds() / 60) if first_off_time else 0
            outage = OutageAlert(
                subscriber_key=snapshot.subscriber_key,
                parent_port_key=snapshot.parent_port_key,
                batch_id=snapshot.batch_id,
                ma_tb=snapshot.ma_tb,
                ten_tb=snapshot.ten_tb,
                ma_men=snapshot.ma_men,
                olt_name=snapshot.olt_name,
                doi_vt=snapshot.doi_vt,
                diachi_ld=snapshot.diachi_ld,
                dienthoai_lh=snapshot.dienthoai_lh,
                ten_nvkt_db=snapshot.ten_nvkt_db,
                first_on_time=first_on_time,
                first_off_time=first_off_time,
                alert_time=snapshot.measured_at,
                off_duration_minutes=max(duration, 0),
                consecutive_on_count=prev.get("consecutive_on_count", 0) or 0,
            )
            alert_sent_time = snapshot.measured_at
        elif current_state == WATCHING:
            next_state = STABLE_OFF if last_status == "OFF" else WATCHING
            first_off_time = first_off_time or snapshot.measured_at
        elif current_state == RECOVERED:
            next_state = PENDING_OFF
            first_off_time = snapshot.measured_at
        elif current_state == ALERT_OFF:
            next_state = ALERT_OFF
        else:
            next_state = STABLE_OFF
            first_off_time = first_off_time or snapshot.measured_at

        return (
            _base_state(
                snapshot,
                next_state,
                "OFF",
                on_count,
                off_count,
                first_on_time,
                first_off_time,
                alert_sent_time,
                recovery_sent_time,
            ),
            outage,
            recovery,
        )

    return (
        _base_state(
            snapshot,
            current_state,
            "UNKNOWN",
            on_count,
            off_count,
            first_on_time,
            first_off_time,
            alert_sent_time,
            recovery_sent_time,
        ),
        outage,
        recovery,
    )


def _detect_wide_area(
    snapshot_offs: Iterable[SubscriberSnapshot],
    batch_id: str,
    threshold: int,
    blocked_ma_tbs: Optional[Iterable[str]] = None,
) -> List[WideAreaAlert]:
    grouped = defaultdict(list)
    blocked = {ma_tb for ma_tb in (blocked_ma_tbs or []) if ma_tb}
    for snapshot in snapshot_offs:
        if snapshot.ma_tb and snapshot.ma_tb not in blocked:
            grouped[snapshot.parent_port_key].append(snapshot)

    alerts = []
    for parent_port_key, snapshots in grouped.items():
        if len(snapshots) <= threshold:
            continue
        first = snapshots[0]
        port = parent_port_key.split("_", 1)[1] if "_" in parent_port_key else parent_port_key
        alerts.append(
            WideAreaAlert(
                batch_id=batch_id,
                parent_port_key=parent_port_key,
                olt_name=first.olt_name,
                port=port,
                subscriber_count=len(snapshots),
                subscriber_keys=[snapshot.subscriber_key for snapshot in snapshots],
                subscriber_list=[
                    {
                        "ma_tb": snapshot.ma_tb,
                        "ten_tb": snapshot.ten_tb,
                        "ten_nvkt_db": snapshot.ten_nvkt_db,
                    }
                    for snapshot in snapshots
                ],
                doi_vt=first.doi_vt,
                alert_time=first.measured_at,
            )
        )
    return alerts


def process_completed_batch(
    db_path: str,
    batch_id: str,
    source_db_path: str = "database.db",
    send_notifications: bool = True,
    wide_area_threshold: int = DEFAULT_WIDE_AREA_THRESHOLD,
    progress_every: int = 5000,
    log=print,
) -> Dict:
    started_at = perf_counter()
    repo = AlertRepository(db_path, source_db_path)
    repo.ensure_schema()

    if repo.get_batch_status(batch_id) == "alerted":
        return {"batch_id": batch_id, "status": "already_alerted"}

    _emit(log, f"[ALERT] Batch {batch_id}: start post-processing")
    _emit(log, f"[ALERT] Batch {batch_id}: loading batch snapshots...")
    raw_rows = repo.fetch_batch_snapshot_rows(batch_id)
    subscriber_keys = [row["subscriber_key"] for row in raw_rows]
    _emit(log, f"[ALERT] Batch {batch_id}: snapshots={len(raw_rows)}")
    _emit(log, f"[ALERT] Batch {batch_id}: enriching subscriber metadata...")
    metadata_map = repo.fetch_metadata_map(subscriber_keys)
    snapshots = [_snapshot_from_row(row, metadata_map.get(row["subscriber_key"], {})) for row in raw_rows]

    outage_count = 0
    recovery_count = 0
    current_offs = []
    on_ma_tbs = set()
    processed_count = 0
    unknown_count = 0

    _emit(log, f"[ALERT] Batch {batch_id}: processing state machine...")
    for idx, snapshot in enumerate(snapshots, start=1):
        if snapshot.status == "UNKNOWN":
            unknown_count += 1
            continue
        prev = repo.get_state_row(snapshot.subscriber_key)
        next_state, outage, recovery = _transition(snapshot, prev)
        repo.save_state(next_state)
        processed_count += 1
        if outage:
            repo.insert_outage_alert(outage)
            outage_count += 1
        if recovery:
            repo.insert_recovery_alert(recovery)
            recovery_count += 1
        if snapshot.status == "ON" and snapshot.ma_tb:
            on_ma_tbs.add(snapshot.ma_tb)
        if snapshot.status == "OFF":
            current_offs.append(snapshot)
        if progress_every and idx % progress_every == 0:
            _emit(log, f"[ALERT] Batch {batch_id}: processing state machine {idx}/{len(snapshots)}")

    _emit(
        log,
        "[ALERT] Batch "
        f"{batch_id}: state machine done processed={processed_count} "
        f"unknown_skipped={unknown_count} current_off={len(current_offs)} "
        f"outage_created={outage_count} recovery_created={recovery_count}",
    )

    _emit(log, f"[ALERT] Batch {batch_id}: detecting wide-area outages...")
    wide_area_alerts = _detect_wide_area(current_offs, batch_id, wide_area_threshold, blocked_ma_tbs=on_ma_tbs)
    for alert in wide_area_alerts:
        repo.insert_wide_area_alert(alert)
        repo.suppress_outage_alerts(batch_id, alert.subscriber_keys, "wide_area")
    _emit(log, f"[ALERT] Batch {batch_id}: wide-area detected: {len(wide_area_alerts)} ports")

    _emit(log, f"[ALERT] Batch {batch_id}: applying pattern exclusion...")
    pattern_updates = update_exclusion_table(db_path)
    exclusion_list = get_pattern_exclusion_list(db_path)
    to_suppress = []
    if exclusion_list:
        rows = repo.list_unsent_outage_alerts(batch_id)
        to_suppress = [row.subscriber_key for row in rows if row.ma_tb in exclusion_list]
        repo.suppress_outage_alerts(batch_id, to_suppress, "pattern_exclusion")
    _emit(
        log,
        f"[ALERT] Batch {batch_id}: pattern exclusion updated={pattern_updates} suppressed={len(to_suppress)}",
    )

    notification_results = {}
    if send_notifications:
        _emit(log, f"[ALERT] Batch {batch_id}: dispatching notifications...")
        notification_results = dispatch_batch_notifications(repo, batch_id, log=log)

    repo.mark_batch_alerted(batch_id)
    duration_seconds = round(perf_counter() - started_at, 2)
    _emit(
        log,
        "[ALERT] Batch "
        f"{batch_id}: finished: snapshots={len(snapshots)} "
        f"state_rows_processed={processed_count} current_off={len(current_offs)} "
        f"wide_area_created={len(wide_area_alerts)} outage_created={outage_count} "
        f"recovery_created={recovery_count} duration={duration_seconds}s",
    )
    return {
        "batch_id": batch_id,
        "status": "processed",
        "snapshots": len(snapshots),
        "state_rows_processed": processed_count,
        "unknown_snapshots_skipped": unknown_count,
        "current_off_count": len(current_offs),
        "outage_alerts_created": outage_count,
        "recovery_alerts_created": recovery_count,
        "wide_area_alerts_created": len(wide_area_alerts),
        "pattern_updates": pattern_updates,
        "pattern_suppressed_count": len(to_suppress),
        "notifications": notification_results,
        "duration_seconds": duration_seconds,
    }
