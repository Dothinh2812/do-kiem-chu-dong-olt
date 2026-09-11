import os
from collections import defaultdict
from datetime import datetime
from time import perf_counter
from typing import Dict, Iterable, List, Optional, Tuple

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
    from . import notification_service
    from .notification_bridge import dispatch_batch_notifications
    from .subscriber_off_scoring import (
        LIKELY_SELF_POWER_OFF,
        score_pattern_suppression_for_current_offs,
        update_exclusion_table_from_results,
    )
    from .current_off_snapshot import (
        build_current_off_snapshot,
        build_snapshot_payload,
        get_snapshot_output_path,
        write_snapshot_json,
    )
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
    import notification_service
    from notification_bridge import dispatch_batch_notifications
    from subscriber_off_scoring import (
        LIKELY_SELF_POWER_OFF,
        score_pattern_suppression_for_current_offs,
        update_exclusion_table_from_results,
    )
    from current_off_snapshot import (
        build_current_off_snapshot,
        build_snapshot_payload,
        get_snapshot_output_path,
        write_snapshot_json,
    )


MIN_STABLE_ON_CYCLES = 2
DEFAULT_WIDE_AREA_THRESHOLD = 5


def _emit(log, message: str):
    if log:
        log(message)


def normalize_status(raw_status, olt_power_rx=None) -> str:
    status = str(raw_status or "").upper()
    if status == "PORT_DOWN":
        return "PORT_DOWN"
    if "OFF" in status:
        if olt_power_rx is not None and -30 <= olt_power_rx <= -15:
            return "ON"
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
    ma_tb = (metadata.get("Ma_Tb") or "").strip() or (row.get("accountFiber") or "").strip()
    return SubscriberSnapshot(
        subscriber_key=subscriber_key,
        parent_port_key=get_parent_port_key(subscriber_key),
        olt_name=get_olt_name(subscriber_key),
        batch_id=row["batch_id"],
        status=normalize_status(row.get("onuStatusStr"), row.get("oltPowerRx")),
        measured_at=_parse_measured_at(row),
        ma_tb=ma_tb,
        ten_tb=(metadata.get("Ten_Tb") or "").strip(),
        ma_men=(metadata.get("Ma_Men") or "").strip(),
        doi_vt=doi_vt,
        diachi_ld=(metadata.get("DIACHI_LD") or "").strip(),
        dienthoai_lh=(metadata.get("DIENTHOAI_LH") or "").strip(),
        ten_nvkt_db=(metadata.get("TEN_NVKT_DB") or "").strip(),
        account_fiber=(row.get("accountFiber") or "").strip(),
        onu_last_off=(row.get("onuLastOff") or "").strip(),
        onu_last_on=(row.get("onuLastOn") or "").strip(),
    )


def _partition_by_authoritative_assignment(
    snapshots: Iterable[SubscriberSnapshot],
    authoritative_sub_by_ma_tb: Dict[str, str],
) -> Tuple[List[SubscriberSnapshot], List[Tuple[SubscriberSnapshot, str]]]:
    valid_snapshots: List[SubscriberSnapshot] = []
    stale_snapshots: List[Tuple[SubscriberSnapshot, str]] = []
    for snapshot in snapshots:
        ma_tb = (snapshot.ma_tb or "").strip()
        authoritative_sub = authoritative_sub_by_ma_tb.get(ma_tb)
        if ma_tb and authoritative_sub and snapshot.subscriber_key != authoritative_sub:
            stale_snapshots.append((snapshot, authoritative_sub))
            continue
        valid_snapshots.append(snapshot)
    return valid_snapshots, stale_snapshots


def _parse_device_event_time(value) -> Optional[datetime]:
    raw_value = str(value or "").strip()
    if not raw_value:
        return None
    try:
        return datetime.fromisoformat(raw_value)
    except ValueError:
        return None


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
        if snapshot.status in {"OFF", "PORT_DOWN"}:
            return (
                _base_state(
                    snapshot, STABLE_OFF, snapshot.status, 0, 1, None, snapshot.measured_at, None, None
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

    if snapshot.status in {"OFF", "PORT_DOWN"}:
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
                snapshot.status,
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
    state_rows: Optional[Dict[str, Dict]] = None,
    blocked_ma_tbs: Optional[Iterable[str]] = None,
    port_recovery_times: Optional[Dict[str, datetime]] = None,
) -> List[WideAreaAlert]:
    grouped = defaultdict(list)
    state_rows = state_rows or {}
    blocked = {ma_tb for ma_tb in (blocked_ma_tbs or []) if ma_tb}
    port_recovery_times = port_recovery_times or {}
    for snapshot in snapshot_offs:
        if snapshot.ma_tb and snapshot.ma_tb not in blocked:
            grouped[snapshot.parent_port_key].append(snapshot)

    alerts = []
    for parent_port_key, snapshots in grouped.items():
        has_port_down = any(snapshot.status == "PORT_DOWN" for snapshot in snapshots)
        if not has_port_down and len(snapshots) <= threshold:
            continue
        if has_port_down:
            first = snapshots[0]
            port = parent_port_key.split("_", 1)[1] if "_" in parent_port_key else parent_port_key
            alerts.append(
                WideAreaAlert(
                    batch_id=batch_id,
                    parent_port_key=parent_port_key,
                    olt_name=first.olt_name,
                    port=port,
                    subscriber_count=len(snapshots),
                    incident_type="port_down",
                    subscriber_keys=[snapshot.subscriber_key for snapshot in snapshots],
                    subscriber_list=[
                        {
                            "ma_tb": snapshot.ma_tb,
                            "ten_tb": snapshot.ten_tb,
                            "ten_nvkt_db": snapshot.ten_nvkt_db,
                            "onu_last_off": snapshot.onu_last_off,
                        }
                        for snapshot in snapshots
                    ],
                    doi_vt=first.doi_vt,
                    alert_time=first.measured_at,
                    first_off_time=first.measured_at,
                    off_duration_minutes=0,
                )
            )
            continue
        blank_last_off_snapshots: List[SubscriberSnapshot] = []
        candidates = []
        for snapshot in snapshots:
            onu_last_off = _parse_device_event_time(snapshot.onu_last_off)
            if onu_last_off is None:
                blank_last_off_snapshots.append(snapshot)
                continue
            candidates.append((snapshot, onu_last_off))

        best_cluster: List[SubscriberSnapshot] = []
        if candidates:
            candidates.sort(key=lambda item: item[1])
            left = 0
            for right, (_snapshot, right_dt) in enumerate(candidates):
                while left <= right and (right_dt - candidates[left][1]).total_seconds() >= 300:
                    left += 1
                cluster = [item[0] for item in candidates[left : right + 1]]
                if len(cluster) > len(best_cluster):
                    best_cluster = cluster

        qualified_snapshots = best_cluster + blank_last_off_snapshots
        if len(qualified_snapshots) <= threshold:
            continue

        first = qualified_snapshots[0]
        first_off_candidates = [
            _parse_optional_dt((state_rows.get(snapshot.subscriber_key) or {}).get("first_off_time"))
            for snapshot in qualified_snapshots
        ]
        first_off_candidates = [dt for dt in first_off_candidates if dt is not None]
        recovery_boundary = port_recovery_times.get(parent_port_key)
        if recovery_boundary:
            first_off_candidates = [dt for dt in first_off_candidates if dt > recovery_boundary]
        first_off_time = min(first_off_candidates) if first_off_candidates else None
        if recovery_boundary and first_off_time is None:
            first_off_time = first.measured_at
        off_duration_minutes = 0
        if first_off_time and first.measured_at:
            off_duration_minutes = max(int((first.measured_at - first_off_time).total_seconds() // 60), 0)
        port = parent_port_key.split("_", 1)[1] if "_" in parent_port_key else parent_port_key
        alerts.append(
            WideAreaAlert(
                batch_id=batch_id,
                parent_port_key=parent_port_key,
                olt_name=first.olt_name,
                port=port,
                subscriber_count=len(qualified_snapshots),
                incident_type="wide_area",
                subscriber_keys=[snapshot.subscriber_key for snapshot in qualified_snapshots],
                subscriber_list=[
                    {
                        "ma_tb": snapshot.ma_tb,
                        "ten_tb": snapshot.ten_tb,
                        "ten_nvkt_db": snapshot.ten_nvkt_db,
                        "onu_last_off": snapshot.onu_last_off,
                    }
                    for snapshot in qualified_snapshots
                ],
                doi_vt=first.doi_vt,
                alert_time=first.measured_at,
                first_off_time=first_off_time,
                off_duration_minutes=off_duration_minutes,
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
    all_snapshots = [_snapshot_from_row(row, metadata_map.get(row["subscriber_key"], {})) for row in raw_rows]
    authoritative_sub_by_ma_tb = repo.fetch_authoritative_sub_by_ma_tb(
        snapshot.ma_tb for snapshot in all_snapshots
    )
    snapshots, stale_assignment_snapshots = _partition_by_authoritative_assignment(
        all_snapshots,
        authoritative_sub_by_ma_tb,
    )
    stale_assignment_suppressed = len(stale_assignment_snapshots)
    _emit(
        log,
        f"[ALERT] Batch {batch_id}: assignment filter valid={len(snapshots)} "
        f"stale_assignment_suppressed={stale_assignment_suppressed}",
    )
    if stale_assignment_snapshots:
        stale_examples = "; ".join(
            f"{snapshot.ma_tb} {snapshot.subscriber_key} -> {authoritative_sub}"
            for snapshot, authoritative_sub in stale_assignment_snapshots[:5]
        )
        _emit(log, f"[ALERT] Batch {batch_id}: stale assignment examples: {stale_examples}")

    outage_count = 0
    recovery_count = 0
    current_offs = []
    on_ma_tbs = set()
    processed_count = 0
    unknown_count = 0

    _emit(log, f"[ALERT] Batch {batch_id}: processing state machine...")
    previous_state_rows = repo.fetch_state_map(snapshot.subscriber_key for snapshot in snapshots)
    next_state_rows = {}
    states_to_save = []
    outage_alerts_to_insert = []
    recovery_alerts_to_insert = []
    for idx, snapshot in enumerate(snapshots, start=1):
        if snapshot.status == "UNKNOWN":
            unknown_count += 1
            continue
        prev = previous_state_rows.get(snapshot.subscriber_key)
        next_state, outage, recovery = _transition(snapshot, prev)
        states_to_save.append(next_state)
        next_state_rows[snapshot.subscriber_key] = next_state
        processed_count += 1
        if outage:
            outage_alerts_to_insert.append(outage)
            outage_count += 1
        if recovery:
            recovery_alerts_to_insert.append(recovery)
            recovery_count += 1
        if snapshot.status == "ON" and snapshot.ma_tb:
            on_ma_tbs.add(snapshot.ma_tb)
        if snapshot.status in {"OFF", "PORT_DOWN"}:
            current_offs.append(snapshot)
        if progress_every and idx % progress_every == 0:
            _emit(log, f"[ALERT] Batch {batch_id}: processing state machine {idx}/{len(snapshots)}")

    repo.persist_state_machine_results(
        states_to_save,
        outage_alerts_to_insert,
        recovery_alerts_to_insert,
    )

    _emit(
        log,
        "[ALERT] Batch "
        f"{batch_id}: state machine done processed={processed_count} "
        f"unknown_skipped={unknown_count} current_off={len(current_offs)} "
        f"outage_created={outage_count} recovery_created={recovery_count}",
    )

    state_rows = {
        snapshot.subscriber_key: next_state_rows[snapshot.subscriber_key]
        for snapshot in current_offs
        if snapshot.subscriber_key in next_state_rows
    }
    measured_at = max((snapshot.measured_at for snapshot in all_snapshots), default=None)
    port_recovery_times = repo.fetch_latest_port_recovery_times(
        [snapshot.parent_port_key for snapshot in current_offs],
        measured_at,
        wide_area_threshold,
    )

    _emit(log, f"[ALERT] Batch {batch_id}: detecting wide-area outages...")
    wide_area_alerts = _detect_wide_area(
        current_offs,
        batch_id,
        wide_area_threshold,
        state_rows=state_rows,
        blocked_ma_tbs=on_ma_tbs,
        port_recovery_times=port_recovery_times,
    )
    for alert in wide_area_alerts:
        repo.insert_wide_area_alert(alert)
        repo.suppress_outage_alerts(batch_id, alert.subscriber_keys, "wide_area")
    _emit(log, f"[ALERT] Batch {batch_id}: wide-area detected: {len(wide_area_alerts)} ports")

    wide_area_subscriber_keys = {
        subscriber_key
        for alert in wide_area_alerts
        for subscriber_key in alert.subscriber_keys
    }
    wide_area_timing_by_subscriber = {
        subscriber_key: {
            "first_off_time": alert.first_off_time,
            "duration_minutes": alert.off_duration_minutes,
        }
        for alert in wide_area_alerts
        for subscriber_key in alert.subscriber_keys
    }

    _emit(log, f"[ALERT] Batch {batch_id}: applying pattern exclusion...")
    notification_config = notification_service.load_config()
    alert_gate_mode = notification_service.get_individual_off_alert_gate_mode(
        notification_config
    )
    min_alert_duration_minutes = (
        notification_service.get_individual_off_min_duration_minutes(notification_config)
    )
    to_suppress, scoring_results = score_pattern_suppression_for_current_offs(
        db_path,
        current_offs,
        state_rows,
        batch_id=batch_id,
        wide_area_subscriber_keys=wide_area_subscriber_keys,
        reference_time=measured_at,
        min_alert_duration_minutes=min_alert_duration_minutes,
    )
    pattern_updates = update_exclusion_table_from_results(db_path, scoring_results)
    if to_suppress:
        repo.suppress_outage_alerts(batch_id, to_suppress, "pattern_exclusion")
    suppressed_keys = set(to_suppress)
    customer_poweroff_suppressed_results = [
        result
        for result in scoring_results
        if result.subscriber_key in suppressed_keys
        and result.classification == LIKELY_SELF_POWER_OFF
    ]
    customer_poweroff_suppressed_codes = {
        result.ma_tb or result.subscriber_key
        for result in customer_poweroff_suppressed_results
    }
    exclusion_list = {
        result.ma_tb
        for result in scoring_results
        if result.subscriber_key in suppressed_keys and result.ma_tb
    }
    _emit(
        log,
        f"[ALERT] Batch {batch_id}: pattern scoring rows={len(scoring_results)} "
        f"updated={pattern_updates} suppressed={len(to_suppress)} "
        f"customer_poweroff_suppressed={len(customer_poweroff_suppressed_codes)} "
        f"customer_poweroff_subscribers={len(customer_poweroff_suppressed_results)}",
    )
    current_off_snapshot_rows = build_current_off_snapshot(
        current_offs,
        state_rows,
        exclusion_list=exclusion_list,
        wide_area_subscriber_keys=wide_area_subscriber_keys,
        wide_area_timing_by_subscriber=wide_area_timing_by_subscriber,
        scoring_results=scoring_results,
        alert_gate_mode=alert_gate_mode,
    )
    current_off_snapshot_file = str(get_snapshot_output_path(os.path.dirname(db_path) or "."))
    current_off_snapshot_payload = build_snapshot_payload(
        batch_id,
        measured_at,
        current_off_snapshot_rows,
        stale_assignment_suppressed=stale_assignment_suppressed,
    )
    write_snapshot_json(current_off_snapshot_payload, current_off_snapshot_file)
    gate_summary = current_off_snapshot_payload["summary"]
    _emit(
        log,
        f"[ALERT] Batch {batch_id}: individual OFF gate mode={alert_gate_mode} "
        f"min_duration={min_alert_duration_minutes}m "
        f"eligible={gate_summary['gate_eligible']} blocked={gate_summary['gate_blocked']}",
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
        f"{batch_id}: finished: snapshots={len(raw_rows)} "
        f"state_rows_processed={processed_count} current_off={len(current_offs)} "
        f"stale_assignment_suppressed={stale_assignment_suppressed} "
        f"wide_area_created={len(wide_area_alerts)} outage_created={outage_count} "
        f"recovery_created={recovery_count} current_off_snapshot={len(current_off_snapshot_rows)} "
        f"duration={duration_seconds}s",
    )
    return {
        "batch_id": batch_id,
        "status": "processed",
        "snapshots": len(raw_rows),
        "state_rows_processed": processed_count,
        "unknown_snapshots_skipped": unknown_count,
        "stale_assignment_suppressed": stale_assignment_suppressed,
        "current_off_count": len(current_offs),
        "outage_alerts_created": outage_count,
        "recovery_alerts_created": recovery_count,
        "wide_area_alerts_created": len(wide_area_alerts),
        "pattern_updates": pattern_updates,
        "pattern_suppressed_count": len(to_suppress),
        "customer_poweroff_suppressed_count": len(customer_poweroff_suppressed_codes),
        "customer_poweroff_suppressed_subscribers": len(customer_poweroff_suppressed_results),
        "current_off_snapshot_total": len(current_off_snapshot_rows),
        "current_off_snapshot_active": current_off_snapshot_payload["summary"][
            "active_individual_alerts"
        ],
        "current_off_snapshot_file": current_off_snapshot_file,
        "notifications": notification_results,
        "duration_seconds": duration_seconds,
    }
