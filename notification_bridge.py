import asyncio
import json
import os
from collections import defaultdict
from datetime import datetime
from typing import Dict, List

try:
    from .alert_db import AlertRepository
    from . import notification_service
    from . import customer_notification_service
    from .current_off_snapshot import get_snapshot_output_path, load_snapshot_rows_for_batch
except ImportError:
    from alert_db import AlertRepository
    import notification_service
    import customer_notification_service
    from current_off_snapshot import get_snapshot_output_path, load_snapshot_rows_for_batch

def _emit(log, message: str):
    if log:
        log(message)


def load_current_off_snapshot_rows(repo: AlertRepository, batch_id: str) -> List[Dict]:
    snapshot_path = get_snapshot_output_path(os.path.dirname(repo.db_path) or ".")
    return load_snapshot_rows_for_batch(snapshot_path, batch_id)


def filter_active_current_off_snapshot_rows(rows: List[Dict]) -> List[Dict]:
    return [
        row
        for row in rows
        if not row.get("suppressed_by_pattern") and not row.get("suppressed_by_wide_area")
    ]


def filter_individual_off_alert_gate_rows(rows: List[Dict], mode: str) -> List[Dict]:
    if mode in {"off", "shadow"}:
        return list(rows)
    return [row for row in rows if row.get("alert_eligible") is True]


def _subscriber_identity(row: Dict) -> str:
    return str(row.get("subscriber_key") or row.get("ma_tb") or "").strip()


def _load_sent_subscriber_identities(repo: AlertRepository, state_key: str) -> set[str]:
    raw_value = repo.get_runtime_state(state_key, "[]")
    try:
        payload = json.loads(raw_value or "[]")
    except (TypeError, ValueError):
        return set()
    if not isinstance(payload, list):
        return set()
    return {str(value).strip() for value in payload if str(value).strip()}


def _store_sent_subscriber_identities(repo: AlertRepository, state_key: str, identities: set[str]):
    repo.set_runtime_state(state_key, json.dumps(sorted(identities), ensure_ascii=False))


def build_individual_alert_sent_state_key(config: Dict = None, now: datetime = None) -> str:
    active_config = config or notification_service.load_config()
    cutoff = notification_service.get_current_off_alert_cutoff(
        active_config,
        now=now,
        start_time_key="individual_alert_start_time",
    )
    current = now or datetime.now()
    start_label = str(active_config.get("individual_alert_start_time", "") or "all").strip() or "all"
    day = (cutoff.date() if cutoff else current.date()).isoformat()
    return f"individual_alert_sent_subscribers:{day}:{start_label}"


def build_weak_signal_sent_state_key(config: Dict = None, now: datetime = None) -> str:
    current = now or datetime.now()
    return f"weak_signal_individual_sent_subscribers:{current.date().isoformat()}"


def filter_unsent_individual_alert_rows(repo: AlertRepository, rows: List[Dict], state_key: str) -> List[Dict]:
    sent_identities = _load_sent_subscriber_identities(repo, state_key)
    return [row for row in rows if _subscriber_identity(row) and _subscriber_identity(row) not in sent_identities]


def mark_individual_alert_rows_sent(repo: AlertRepository, rows: List[Dict], state_key: str):
    sent_identities = _load_sent_subscriber_identities(repo, state_key)
    sent_identities.update(_subscriber_identity(row) for row in rows if _subscriber_identity(row))
    _store_sent_subscriber_identities(repo, state_key, sent_identities)


async def send_personal_current_off_alerts(alerts: List[Dict], batch_id: str, config: Dict) -> Dict:
    results = {"sent": 0, "failed": 0, "no_user": 0, "deliveries": [], "sent_rows": []}
    groups = defaultdict(list)
    for alert in alerts:
        nvkt = notification_service._short_nvkt(alert.get("ten_nvkt_db", "") or "")
        groups[nvkt or "Chưa gán NVKT"].append(alert)

    for nvkt, items in groups.items():
        user_id = notification_service.get_zalo_user_by_nvkt(nvkt, config=config)
        message = notification_service.format_current_off_snapshot_by_nvkt(items, for_zalo=True)
        if not user_id:
            results["no_user"] += 1
            results["deliveries"].append(
                {
                    "batch_id": batch_id,
                    "channel": "zalo",
                    "alert_type": "personal_outage",
                    "target_id": "",
                    "nvkt": nvkt,
                    "status": "NO_USER",
                    "message_full": message,
                    "alert_ids": [],
                    "alert_count": len(items),
                    "error": "NVKT not mapped",
                    "stdout": "",
                    "stderr": "",
                    "returncode": None,
                    "command": [],
                }
            )
            continue

        delivery_result = await notification_service.send_zalo_message_to_user_detailed(message, user_id)
        success = delivery_result.get("success", False)
        if success:
            results["sent"] += 1
            results["sent_rows"].extend(items)
        else:
            results["failed"] += 1
        results["deliveries"].append(
            {
                "batch_id": batch_id,
                "channel": "zalo",
                "alert_type": "personal_outage",
                "target_id": user_id,
                "nvkt": nvkt,
                "status": "SUCCESS" if success else "FAILED",
                "message_full": message,
                "alert_ids": [],
                "alert_count": len(items),
                "error": delivery_result.get("error", ""),
                "stdout": delivery_result.get("stdout", ""),
                "stderr": delivery_result.get("stderr", ""),
                "returncode": delivery_result.get("returncode"),
                "command": delivery_result.get("command", []),
            }
        )

    return results


def load_weak_signal_rows(repo: AlertRepository, batch_id: str) -> List[Dict]:
    raw_rows = repo.fetch_batch_weak_signal_rows(batch_id)
    metadata_map = repo.fetch_metadata_map([row["subscriber_key"] for row in raw_rows])
    rows = []
    for row in raw_rows:
        metadata = metadata_map.get(row["subscriber_key"], {})
        ma_tb = (metadata.get("Ma_Tb") or "").strip() or (row.get("accountFiber") or "").strip()
        rows.append(
            {
                **row,
                "ma_tb": ma_tb,
                "ten_tb": (metadata.get("Ten_Tb") or "").strip(),
                "ma_men": (metadata.get("Ma_Men") or "").strip(),
                "doi_vt": (metadata.get("DOI_VT") or "").strip(),
                "diachi_ld": (metadata.get("DIACHI_LD") or "").strip(),
                "dienthoai_lh": (metadata.get("DIENTHOAI_LH") or "").strip(),
                "ten_nvkt_db": (metadata.get("TEN_NVKT_DB") or "").strip(),
            }
        )
    return rows


async def send_personal_weak_signal_alerts(alerts: List[Dict], batch_id: str, config: Dict) -> Dict:
    results = {"sent": 0, "failed": 0, "no_user": 0, "deliveries": [], "sent_rows": []}
    groups = defaultdict(list)
    for alert in alerts:
        nvkt = notification_service._short_nvkt(alert.get("ten_nvkt_db", "") or "")
        groups[nvkt or "Chưa gán NVKT"].append(alert)

    for nvkt, items in groups.items():
        user_id = notification_service.get_zalo_user_by_nvkt(nvkt, config=config)
        message = notification_service.format_weak_signal_by_nvkt(items, for_zalo=True)
        if not user_id:
            results["no_user"] += 1
            results["deliveries"].append(
                {
                    "batch_id": batch_id,
                    "channel": "zalo",
                    "alert_type": "personal_weak_signal",
                    "target_id": "",
                    "nvkt": nvkt,
                    "status": "NO_USER",
                    "message_full": message,
                    "alert_ids": [],
                    "alert_count": len(items),
                    "error": "NVKT not mapped",
                    "stdout": "",
                    "stderr": "",
                    "returncode": None,
                    "command": [],
                }
            )
            continue

        delivery_result = await notification_service.send_zalo_message_to_user_detailed(message, user_id)
        success = delivery_result.get("success", False)
        if success:
            results["sent"] += 1
            results["sent_rows"].extend(items)
        else:
            results["failed"] += 1
        results["deliveries"].append(
            {
                "batch_id": batch_id,
                "channel": "zalo",
                "alert_type": "personal_weak_signal",
                "target_id": user_id,
                "nvkt": nvkt,
                "status": "SUCCESS" if success else "FAILED",
                "message_full": message,
                "alert_ids": [],
                "alert_count": len(items),
                "error": delivery_result.get("error", ""),
                "stdout": delivery_result.get("stdout", ""),
                "stderr": delivery_result.get("stderr", ""),
                "returncode": delivery_result.get("returncode"),
                "command": delivery_result.get("command", []),
            }
        )

    return results


async def _dispatch(repo: AlertRepository, batch_id: str, log=print) -> Dict:
    results = {
        "wide_area": {
            "pending_alerts": 0,
            "excluded_by_config": 0,
            "port_down_below_threshold": 0,
            "marked_sent_alerts": 0,
            "telegram_sent": False,
            "zalo_messages_sent": 0,
            "zalo_messages_failed": 0,
            "no_thread": 0,
        },
        "outage": {
            "raw_pending_alerts": 0,
            "filtered_pending_alerts": 0,
            "filtered_out_alerts": 0,
            "marked_sent_alerts": 0,
            "telegram_sent": False,
            "zalo_groups_sent": 0,
            "zalo_groups_failed": 0,
            "no_thread_groups": 0,
            "gate_eligible": 0,
            "gate_blocked": 0,
        },
        "personal_outage": {
            "raw_pending_alerts": 0,
            "filtered_pending_alerts": 0,
            "filtered_out_alerts": 0,
            "marked_sent_alerts": 0,
            "zalo_messages_sent": 0,
            "zalo_messages_failed": 0,
            "no_user": 0,
        },
        "personal_weak_signal": {
            "raw_pending_alerts": 0,
            "filtered_pending_alerts": 0,
            "filtered_out_alerts": 0,
            "marked_sent_alerts": 0,
            "zalo_messages_sent": 0,
            "zalo_messages_failed": 0,
            "no_user": 0,
        },
        "recovery": {
            "pending_alerts": 0,
            "marked_sent_alerts": 0,
            "telegram_sent": False,
            "zalo_messages_sent": 0,
            "zalo_messages_failed": 0,
            "no_thread": 0,
        },
        "customer_outage": {
            "pending": 0,
            "eligible": 0,
            "sent": 0,
            "failed": 0,
            "skipped": 0,
            "skipped_reasons": {},
            "classification_counts": {
                "CUSTOMER_OPEN_TICKET": 0,
                "CLEAR": 0,
                "INDETERMINATE": 0,
            },
            "failure_kind_counts": {
                "NONE": 0,
                "AUTH": 0,
                "API": 0,
                "REQUEST_TIMEOUT": 0,
                "BATCH_TIMEOUT": 0,
                "PARTIAL": 0,
                "AMBIGUOUS": 0,
                "CONTRACT": 0,
            },
            "onebss_checked": 0,
            "onebss_retried": 0,
            "onebss_stale": 0,
            "onebss_deadline_expired": 0,
            "onebss_request_count": 0,
            "onebss_request_duration_ms_total": 0.0,
            "onebss_request_duration_ms_max": 0.0,
            "onebss_batch_duration_ms": 0.0,
            "precheck_state": "ENFORCED",
        },
    }
    config = notification_service.load_config()

    wide_area_alerts = repo.list_unsent_wide_area_alerts(batch_id)
    results["wide_area"]["pending_alerts"] = len(wide_area_alerts)
    _emit(log, f"[NOTIFY] Batch {batch_id}: wide-area pending={len(wide_area_alerts)}")
    excluded_wide_area_alerts = [
        alert for alert in wide_area_alerts if notification_service.is_wide_area_alert_excluded(alert, config)
    ]
    min_port_down_subscribers = 5
    skipped_small_port_down_alerts = [
        alert
        for alert in wide_area_alerts
        if alert not in excluded_wide_area_alerts
        if str(getattr(alert, "incident_type", "wide_area")).lower() == "port_down"
        and int(getattr(alert, "subscriber_count", 0) or 0) < min_port_down_subscribers
    ]
    eligible_wide_area_alerts = [
        alert
        for alert in wide_area_alerts
        if not notification_service.is_wide_area_alert_excluded(alert, config)
        and alert not in skipped_small_port_down_alerts
    ]
    results["wide_area"]["excluded_by_config"] = len(excluded_wide_area_alerts)
    results["wide_area"]["port_down_below_threshold"] = len(skipped_small_port_down_alerts)
    if excluded_wide_area_alerts:
        excluded_ports = ", ".join(
            f"{notification_service.get_olt_display_name(alert.olt_name)}:{alert.port}"
            for alert in excluded_wide_area_alerts
        )
        _emit(
            log,
            f"[NOTIFY] Batch {batch_id}: wide-area excluded_by_config={len(excluded_wide_area_alerts)} ports={excluded_ports}",
        )
    if skipped_small_port_down_alerts:
        skipped_ports = ", ".join(
            f"{notification_service.get_olt_display_name(alert.olt_name)}:{alert.port}({alert.subscriber_count})"
            for alert in skipped_small_port_down_alerts
        )
        _emit(
            log,
            f"[NOTIFY] Batch {batch_id}: wide-area port_down_below_threshold={len(skipped_small_port_down_alerts)} ports={skipped_ports}",
        )

    wide_area_policy = notification_service.get_alert_policy_status("wide_area", config)
    if eligible_wide_area_alerts and not wide_area_policy["allowed"]:
        _emit(log, f"[NOTIFY] Batch {batch_id}: {wide_area_policy['message']}")
    if eligible_wide_area_alerts and wide_area_policy["allowed"]:
        telegram_sent = False
        zalo_sent = False
        zalo_sent_count = 0
        zalo_failed_count = 0
        no_thread_count = 0
        if config.get("enable_telegram", True):
            message = notification_service.format_wide_area_outage_message(
                [alert.__dict__ | {"olt_port_key": alert.olt_port_key} for alert in eligible_wide_area_alerts]
            )
            telegram_sent = await notification_service.send_telegram_message(message)
            notification_service.append_notification_delivery_log(
                {
                    "batch_id": batch_id,
                    "channel": "telegram",
                    "alert_type": "wide_area",
                    "target_id": config.get("telegram_chat_id", ""),
                    "status": "SUCCESS" if telegram_sent else "FAILED",
                    "message_full": message,
                    "alert_ids": [alert.id for alert in eligible_wide_area_alerts if alert.id],
                    "alert_count": len(eligible_wide_area_alerts),
                }
            )
        if config.get("enable_zalo", True):
            for alert in eligible_wide_area_alerts:
                thread_id = notification_service.get_zalo_thread_by_doi_vt(alert.doi_vt)
                message = notification_service.format_wide_area_outage_for_zalo(
                    alert.__dict__ | {"olt_port_key": alert.olt_port_key}
                )
                if not thread_id:
                    no_thread_count += 1
                    notification_service.append_notification_delivery_log(
                        {
                            "batch_id": batch_id,
                            "channel": "zalo",
                            "alert_type": "wide_area",
                            "doi_vt": alert.doi_vt,
                            "target_id": "",
                            "status": "NO_THREAD",
                            "message_full": message,
                            "alert_ids": [alert.id] if alert.id else [],
                            "alert_count": 1,
                            "error": "DOI_VT not mapped",
                            "stdout": "",
                            "stderr": "",
                            "returncode": None,
                            "command": [],
                        }
                    )
                    continue
                delivery_result = await notification_service.send_zalo_message_to_thread_detailed(message, thread_id)
                success = delivery_result["success"]
                if success:
                    zalo_sent = True
                    zalo_sent_count += 1
                else:
                    zalo_failed_count += 1
                notification_service.append_notification_delivery_log(
                    {
                        "batch_id": batch_id,
                        "channel": "zalo",
                        "alert_type": "wide_area",
                        "doi_vt": alert.doi_vt,
                        "target_id": thread_id,
                        "status": "SUCCESS" if success else "FAILED",
                        "message_full": message,
                        "alert_ids": [alert.id] if alert.id else [],
                        "alert_count": 1,
                        "error": delivery_result.get("error", ""),
                        "stdout": delivery_result.get("stdout", ""),
                        "stderr": delivery_result.get("stderr", ""),
                        "returncode": delivery_result.get("returncode"),
                        "command": delivery_result.get("command", []),
                    }
                )
        results["wide_area"].update(
            {
                "telegram_sent": telegram_sent,
                "zalo_messages_sent": zalo_sent_count,
                "zalo_messages_failed": zalo_failed_count,
                "no_thread": no_thread_count,
            }
        )
        if telegram_sent or zalo_sent or (not config.get("enable_telegram", True) and not config.get("enable_zalo", True)):
            repo.mark_wide_area_alerts_sent(alert.id for alert in eligible_wide_area_alerts if alert.id)
            results["wide_area"]["marked_sent_alerts"] += len(eligible_wide_area_alerts)
        _emit(
            log,
            "[NOTIFY] Batch "
            f"{batch_id}: wide-area sent={results['wide_area']['marked_sent_alerts']} "
            f"telegram_sent={telegram_sent} "
            f"zalo_sent={zalo_sent_count} failed={zalo_failed_count} no_thread={no_thread_count}",
        )
    if excluded_wide_area_alerts:
        repo.mark_wide_area_alerts_sent(alert.id for alert in excluded_wide_area_alerts if alert.id)
        results["wide_area"]["marked_sent_alerts"] += len(excluded_wide_area_alerts)
    if skipped_small_port_down_alerts:
        repo.mark_wide_area_alerts_sent(alert.id for alert in skipped_small_port_down_alerts if alert.id)
        results["wide_area"]["marked_sent_alerts"] += len(skipped_small_port_down_alerts)

    raw_outage_alerts = load_current_off_snapshot_rows(repo, batch_id)
    active_outage_alerts = filter_active_current_off_snapshot_rows(raw_outage_alerts)
    alert_gate_mode = notification_service.get_individual_off_alert_gate_mode(config)
    decision_eligible_count = sum(
        1 for row in active_outage_alerts if row.get("alert_eligible") is True
    )
    decision_blocked_count = len(active_outage_alerts) - decision_eligible_count
    gated_outage_alerts = filter_individual_off_alert_gate_rows(
        active_outage_alerts,
        alert_gate_mode,
    )
    _emit(
        log,
        f"[NOTIFY] Batch {batch_id}: individual OFF gate mode={alert_gate_mode} "
        f"active={len(active_outage_alerts)} decision_eligible={decision_eligible_count} "
        f"decision_blocked={decision_blocked_count} "
        f"dispatch_candidates={len(gated_outage_alerts)}",
    )
    group_policy = notification_service.get_alert_policy_status("group_outage", config)
    outage_cycle = repo.advance_notification_cycle("group_alert", batch_id)
    outage_interval = notification_service.get_group_alert_send_every_batches(config)
    cycle_allows_send = outage_cycle % outage_interval == 0
    cutoff_filtered_outage_alerts = (
        notification_service.filter_current_off_alerts_by_cutoff(
            gated_outage_alerts,
            config=config,
            start_time_key="group_alert_start_time",
        )
        if group_policy["allowed"]
        else []
    )
    outage_alerts = cutoff_filtered_outage_alerts if group_policy["allowed"] and cycle_allows_send else []
    results["outage"]["raw_pending_alerts"] = len(raw_outage_alerts)
    results["outage"]["gate_eligible"] = decision_eligible_count
    results["outage"]["gate_blocked"] = decision_blocked_count
    results["outage"]["filtered_pending_alerts"] = len(outage_alerts)
    results["outage"]["filtered_out_alerts"] = len(raw_outage_alerts) - len(outage_alerts)
    cutoff = notification_service.get_current_off_alert_cutoff(config, start_time_key="group_alert_start_time")
    if group_policy["allowed"] and cutoff is not None:
        _emit(
            log,
            "[NOTIFY] Batch "
            f"{batch_id}: group outage cutoff={cutoff} "
            f"filtered_by_cutoff={len(gated_outage_alerts) - len(outage_alerts)}",
        )
    if raw_outage_alerts and not group_policy["allowed"]:
        _emit(log, f"[NOTIFY] Batch {batch_id}: {group_policy['message']}")
    if group_policy["allowed"] and raw_outage_alerts:
        _emit(
            log,
            "[NOTIFY] Batch "
            f"{batch_id}: group outage cycle={outage_cycle} "
            f"interval={outage_interval} cycle_allows_send={cycle_allows_send}",
        )
    _emit(
        log,
        "[NOTIFY] Batch "
        f"{batch_id}: group outage pending_raw={len(raw_outage_alerts)} "
        f"pending_after_filters={len(outage_alerts)} filtered_out={len(raw_outage_alerts) - len(outage_alerts)}",
    )
    if cutoff_filtered_outage_alerts and group_policy["allowed"] and not cycle_allows_send:
        _emit(
            log,
            "[NOTIFY] Batch "
            f"{batch_id}: group outage notifications waiting for cycle "
            f"{outage_interval} (current_cycle={outage_cycle})",
        )
    if outage_alerts:
        telegram_ok = True
        zalo_results = {"sent": 0}
        if config.get("enable_telegram", True):
            telegram_message = notification_service.format_current_off_snapshot_by_nvkt(outage_alerts, for_zalo=False)
            telegram_ok = await notification_service.send_telegram_message(telegram_message)
            notification_service.append_notification_delivery_log(
                {
                    "batch_id": batch_id,
                    "channel": "telegram",
                    "alert_type": "outage",
                    "target_id": config.get("telegram_chat_id", ""),
                    "status": "SUCCESS" if telegram_ok else "FAILED",
                    "message_full": telegram_message,
                    "alert_ids": [],
                    "alert_count": len(outage_alerts),
                }
            )
        if config.get("enable_zalo", True):
            zalo_results = await notification_service.send_current_off_snapshot_by_doi_vt(outage_alerts)
            for delivery in zalo_results.get("deliveries", []):
                notification_service.append_notification_delivery_log(
                    {
                        "batch_id": batch_id,
                        "target_id": delivery.get("thread_id", ""),
                        **delivery,
                    }
                )
        results["outage"].update(
            {
                "telegram_sent": telegram_ok,
                "zalo_groups_sent": zalo_results.get("sent", 0),
                "zalo_groups_failed": zalo_results.get("failed", 0),
                "no_thread_groups": zalo_results.get("no_thread", 0),
            }
        )
        if telegram_ok or zalo_results.get("sent", 0) > 0 or (
            not config.get("enable_telegram", True) and not config.get("enable_zalo", True)
        ):
            results["outage"]["marked_sent_alerts"] = len(outage_alerts)
        _emit(
            log,
            "[NOTIFY] Batch "
            f"{batch_id}: individual outage sent={results['outage']['marked_sent_alerts']} "
            f"telegram_sent={telegram_ok} "
            f"zalo_groups_sent={zalo_results.get('sent', 0)} "
            f"failed={zalo_results.get('failed', 0)} "
            f"no_thread={zalo_results.get('no_thread', 0)}",
        )

    personal_policy = notification_service.get_alert_policy_status("outage", config)
    personal_cycle = repo.advance_notification_cycle("personal_individual_alert", batch_id)
    personal_interval = notification_service.get_individual_alert_send_every_batches(config)
    personal_cycle_allows_send = personal_cycle % personal_interval == 0
    personal_cutoff_filtered_alerts = (
        notification_service.filter_current_off_alerts_by_cutoff(
            gated_outage_alerts,
            config=config,
            start_time_key="individual_alert_start_time",
        )
        if personal_policy["allowed"]
        else []
    )
    personal_state_key = build_individual_alert_sent_state_key(config=config)
    personal_unsent_alerts = (
        filter_unsent_individual_alert_rows(repo, personal_cutoff_filtered_alerts, personal_state_key)
        if personal_policy["allowed"] and personal_cycle_allows_send
        else []
    )
    results["personal_outage"]["raw_pending_alerts"] = len(raw_outage_alerts)
    results["personal_outage"]["filtered_pending_alerts"] = len(personal_unsent_alerts)
    results["personal_outage"]["filtered_out_alerts"] = len(raw_outage_alerts) - len(personal_unsent_alerts)
    if raw_outage_alerts and not personal_policy["allowed"]:
        _emit(log, f"[NOTIFY] Batch {batch_id}: {personal_policy['message']}")
    if personal_policy["allowed"] and raw_outage_alerts:
        _emit(
            log,
            "[NOTIFY] Batch "
            f"{batch_id}: personal outage cycle={personal_cycle} interval={personal_interval} "
            f"cycle_allows_send={personal_cycle_allows_send} state_key={personal_state_key}",
        )
    if personal_cutoff_filtered_alerts and personal_policy["allowed"] and not personal_cycle_allows_send:
        _emit(
            log,
            "[NOTIFY] Batch "
            f"{batch_id}: personal outage notifications waiting for cycle "
            f"{personal_interval} (current_cycle={personal_cycle})",
        )
    if personal_unsent_alerts:
        personal_results = {"sent": 0, "failed": 0, "no_user": 0, "deliveries": [], "sent_rows": []}
        if config.get("enable_zalo", True):
            personal_results = await send_personal_current_off_alerts(personal_unsent_alerts, batch_id, config)
            for delivery in personal_results.get("deliveries", []):
                notification_service.append_notification_delivery_log(delivery)
        elif not config.get("enable_telegram", True):
            personal_results["sent_rows"] = list(personal_unsent_alerts)
        rows_to_mark_sent = personal_results.get("sent_rows", [])
        if rows_to_mark_sent:
            mark_individual_alert_rows_sent(repo, rows_to_mark_sent, personal_state_key)
        results["personal_outage"].update(
            {
                "marked_sent_alerts": len(rows_to_mark_sent),
                "zalo_messages_sent": personal_results.get("sent", 0),
                "zalo_messages_failed": personal_results.get("failed", 0),
                "no_user": personal_results.get("no_user", 0),
            }
        )
        _emit(
            log,
            "[NOTIFY] Batch "
            f"{batch_id}: personal outage sent={results['personal_outage']['marked_sent_alerts']} "
            f"zalo_sent={personal_results.get('sent', 0)} "
            f"failed={personal_results.get('failed', 0)} "
            f"no_user={personal_results.get('no_user', 0)}",
        )

    if config.get("enable_customer_outage_alert", False):
        customer_policy = notification_service.get_alert_policy_status("customer_outage", config)
        if not customer_policy["allowed"]:
            _emit(log, f"[NOTIFY] Batch {batch_id}: {customer_policy['message']}")
            results["customer_outage"]["pending"] = len(gated_outage_alerts)
            results["customer_outage"]["skipped"] = len(gated_outage_alerts)
            results["customer_outage"]["skipped_reasons"]["quiet_hours"] = len(gated_outage_alerts)
        else:
            cust_res = await customer_notification_service.process_customer_outage_alerts(
                repo=repo,
                candidate_rows=gated_outage_alerts,
                batch_id=batch_id,
                config=config,
                log=log,
            )
            results["customer_outage"].update(cust_res)
            _emit(
                log,
                f"[NOTIFY] Batch {batch_id}: customer outage sent={cust_res.get('sent', 0)} "
                f"failed={cust_res.get('failed', 0)} skipped={cust_res.get('skipped', 0)} "
                f"precheck={cust_res.get('precheck_state', 'ENFORCED')} "
                f"checked={cust_res.get('onebss_checked', 0)} "
                f"retried={cust_res.get('onebss_retried', 0)}",
            )
            if cust_res.get("precheck_state") == "BYPASSED":
                _emit(log, f"[WARNING] Batch {batch_id}: customer outage precheck is BYPASSED")

    weak_signal_policy = notification_service.get_alert_policy_status("outage", config)
    raw_weak_signal_alerts = load_weak_signal_rows(repo, batch_id)
    weak_signal_state_key = build_weak_signal_sent_state_key(config=config)
    weak_signal_unsent_alerts = (
        filter_unsent_individual_alert_rows(repo, raw_weak_signal_alerts, weak_signal_state_key)
        if weak_signal_policy["allowed"]
        else []
    )
    results["personal_weak_signal"]["raw_pending_alerts"] = len(raw_weak_signal_alerts)
    results["personal_weak_signal"]["filtered_pending_alerts"] = len(weak_signal_unsent_alerts)
    results["personal_weak_signal"]["filtered_out_alerts"] = len(raw_weak_signal_alerts) - len(weak_signal_unsent_alerts)
    if raw_weak_signal_alerts and not weak_signal_policy["allowed"]:
        _emit(log, f"[NOTIFY] Batch {batch_id}: weak signal {weak_signal_policy['message']}")
    if weak_signal_unsent_alerts:
        weak_signal_results = {"sent": 0, "failed": 0, "no_user": 0, "deliveries": [], "sent_rows": []}
        if config.get("enable_zalo", True):
            weak_signal_results = await send_personal_weak_signal_alerts(
                weak_signal_unsent_alerts,
                batch_id,
                config,
            )
            for delivery in weak_signal_results.get("deliveries", []):
                notification_service.append_notification_delivery_log(delivery)
        elif not config.get("enable_telegram", True):
            weak_signal_results["sent_rows"] = list(weak_signal_unsent_alerts)
        rows_to_mark_sent = weak_signal_results.get("sent_rows", [])
        if rows_to_mark_sent:
            mark_individual_alert_rows_sent(repo, rows_to_mark_sent, weak_signal_state_key)
        results["personal_weak_signal"].update(
            {
                "marked_sent_alerts": len(rows_to_mark_sent),
                "zalo_messages_sent": weak_signal_results.get("sent", 0),
                "zalo_messages_failed": weak_signal_results.get("failed", 0),
                "no_user": weak_signal_results.get("no_user", 0),
            }
        )
        _emit(
            log,
            "[NOTIFY] Batch "
            f"{batch_id}: personal weak signal sent={results['personal_weak_signal']['marked_sent_alerts']} "
            f"zalo_sent={weak_signal_results.get('sent', 0)} "
            f"failed={weak_signal_results.get('failed', 0)} "
            f"no_user={weak_signal_results.get('no_user', 0)}",
        )

    recovery_alerts = repo.list_unsent_recovery_alerts(batch_id)
    results["recovery"]["pending_alerts"] = len(recovery_alerts)
    _emit(log, f"[NOTIFY] Batch {batch_id}: recovery pending={len(recovery_alerts)}")
    recovery_policy = notification_service.get_alert_policy_status("recovery", config)
    if recovery_alerts and not recovery_policy["allowed"]:
        _emit(log, f"[NOTIFY] Batch {batch_id}: {recovery_policy['message']}")
        return results
    if recovery_alerts:
        telegram_ok = True
        zalo_results = {"sent": 0}
        if config.get("enable_telegram", True):
            telegram_message = notification_service.format_recovery_message(recovery_alerts)
            telegram_ok = await notification_service.send_telegram_message(telegram_message)
            notification_service.append_notification_delivery_log(
                {
                    "batch_id": batch_id,
                    "channel": "telegram",
                    "alert_type": "recovery",
                    "target_id": config.get("telegram_chat_id", ""),
                    "status": "SUCCESS" if telegram_ok else "FAILED",
                    "message_full": telegram_message,
                    "alert_ids": [alert.id for alert in recovery_alerts if alert.id],
                    "alert_count": len(recovery_alerts),
                }
            )
        if config.get("enable_zalo", True):
            zalo_results = await notification_service.send_recovery_alerts_by_doi_vt(recovery_alerts)
            for delivery in zalo_results.get("deliveries", []):
                notification_service.append_notification_delivery_log(
                    {
                        "batch_id": batch_id,
                        "target_id": delivery.get("thread_id", ""),
                        **delivery,
                    }
                )
        results["recovery"].update(
            {
                "telegram_sent": telegram_ok,
                "zalo_messages_sent": zalo_results.get("sent", 0),
                "zalo_messages_failed": zalo_results.get("failed", 0),
                "no_thread": zalo_results.get("no_thread", 0),
            }
        )
        if telegram_ok or zalo_results.get("sent", 0) > 0 or (not config.get("enable_telegram", True) and not config.get("enable_zalo", True)):
            repo.mark_recovery_alerts_sent(alert.id for alert in recovery_alerts)
            results["recovery"]["marked_sent_alerts"] = len(recovery_alerts)
        _emit(
            log,
            "[NOTIFY] Batch "
            f"{batch_id}: recovery sent={results['recovery']['marked_sent_alerts']} "
            f"telegram_sent={telegram_ok} "
            f"zalo_sent={zalo_results.get('sent', 0)} "
            f"failed={zalo_results.get('failed', 0)} "
            f"no_thread={zalo_results.get('no_thread', 0)}",
        )

    return results


def dispatch_batch_notifications(repo: AlertRepository, batch_id: str, log=print) -> Dict:
    return asyncio.run(_dispatch(repo, batch_id, log=log))
