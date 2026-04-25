import asyncio
from datetime import datetime
from typing import Dict, List

try:
    from .alert_db import AlertRepository
    from . import notification_service
except ImportError:
    from alert_db import AlertRepository
    import notification_service


def _emit(log, message: str):
    if log:
        log(message)


def _apply_time_filters(alerts: List) -> List:
    filtered = []
    for alert in alerts:
        first_off_time = alert.first_off_time
        off_duration_minutes = alert.off_duration_minutes or 0
        if not first_off_time:
            filtered.append(alert)
            continue
        if isinstance(first_off_time, str):
            first_off_time = datetime.fromisoformat(first_off_time)
        hour = first_off_time.hour
        if hour < 6 or hour >= 18:
            continue
        if off_duration_minutes >= 720:
            continue
        filtered.append(alert)
    return filtered


async def _dispatch(repo: AlertRepository, batch_id: str, log=print) -> Dict:
    results = {
        "wide_area": {
            "pending_alerts": 0,
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
        },
        "recovery": {
            "pending_alerts": 0,
            "marked_sent_alerts": 0,
            "telegram_sent": False,
            "zalo_messages_sent": 0,
            "zalo_messages_failed": 0,
            "no_thread": 0,
        },
    }
    config = notification_service.load_config()

    wide_area_alerts = repo.list_unsent_wide_area_alerts(batch_id)
    results["wide_area"]["pending_alerts"] = len(wide_area_alerts)
    _emit(log, f"[NOTIFY] Batch {batch_id}: wide-area pending={len(wide_area_alerts)}")
    if wide_area_alerts:
        telegram_sent = False
        zalo_sent = False
        zalo_sent_count = 0
        zalo_failed_count = 0
        no_thread_count = 0
        if config.get("enable_telegram", True):
            message = notification_service.format_wide_area_outage_message(
                [alert.__dict__ | {"olt_port_key": alert.olt_port_key} for alert in wide_area_alerts]
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
                    "alert_ids": [alert.id for alert in wide_area_alerts if alert.id],
                    "alert_count": len(wide_area_alerts),
                }
            )
        if config.get("enable_zalo", True):
            for alert in wide_area_alerts:
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
            repo.mark_wide_area_alerts_sent(alert.id for alert in wide_area_alerts)
            results["wide_area"]["marked_sent_alerts"] = len(wide_area_alerts)
        _emit(
            log,
            "[NOTIFY] Batch "
            f"{batch_id}: wide-area sent={results['wide_area']['marked_sent_alerts']} "
            f"telegram_sent={telegram_sent} "
            f"zalo_sent={zalo_sent_count} failed={zalo_failed_count} no_thread={no_thread_count}",
        )

    raw_outage_alerts = repo.list_unsent_outage_alerts(batch_id)
    outage_alerts = _apply_time_filters(raw_outage_alerts)
    results["outage"]["raw_pending_alerts"] = len(raw_outage_alerts)
    results["outage"]["filtered_pending_alerts"] = len(outage_alerts)
    results["outage"]["filtered_out_alerts"] = len(raw_outage_alerts) - len(outage_alerts)
    _emit(
        log,
        "[NOTIFY] Batch "
        f"{batch_id}: individual outage pending_raw={len(raw_outage_alerts)} "
        f"pending_after_filters={len(outage_alerts)} filtered_out={len(raw_outage_alerts) - len(outage_alerts)}",
    )
    if outage_alerts:
        telegram_ok = True
        zalo_results = {"sent": 0}
        if config.get("enable_telegram", True):
            telegram_message = notification_service.format_consolidated_outage_by_nvkt(outage_alerts, for_zalo=False)
            telegram_ok = await notification_service.send_telegram_message(telegram_message)
            notification_service.append_notification_delivery_log(
                {
                    "batch_id": batch_id,
                    "channel": "telegram",
                    "alert_type": "outage",
                    "target_id": config.get("telegram_chat_id", ""),
                    "status": "SUCCESS" if telegram_ok else "FAILED",
                    "message_full": telegram_message,
                    "alert_ids": [alert.id for alert in outage_alerts if alert.id],
                    "alert_count": len(outage_alerts),
                }
            )
        if config.get("enable_zalo", True):
            zalo_results = await notification_service.send_consolidated_outage_by_doi_vt(outage_alerts)
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
        if telegram_ok or zalo_results.get("sent", 0) > 0 or (not config.get("enable_telegram", True) and not config.get("enable_zalo", True)):
            repo.mark_outage_alerts_sent(alert.id for alert in outage_alerts)
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

    recovery_alerts = repo.list_unsent_recovery_alerts(batch_id)
    results["recovery"]["pending_alerts"] = len(recovery_alerts)
    _emit(log, f"[NOTIFY] Batch {batch_id}: recovery pending={len(recovery_alerts)}")
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
