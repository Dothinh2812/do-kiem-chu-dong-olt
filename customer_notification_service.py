# -*- coding: utf-8 -*-
import json
import os
import re
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple, Union

import requests

try:
    from .alert_db import AlertRepository
    from .notification_service import _is_within_time_window, load_config
except ImportError:
    from alert_db import AlertRepository
    from notification_service import _is_within_time_window, load_config

DEFAULT_CUSTOMER_ALERT_HOTLINE = "0822036382"
DEFAULT_CUSTOMER_ALERT_TIME_WINDOW = "07:00-21:00"
DEFAULT_TELECOM_ZALO_API_URL = "http://localhost:3002"
DEFAULT_CUSTOMER_ALERT_MAX_PER_DAY = 1
DEFAULT_CUSTOMER_ALERT_MAX_PER_WEEK = 3
DEFAULT_CUSTOMER_ALERT_TIMEOUT_SECONDS = 3.0
DEFAULT_NVKT_PHONE_MAPPING_FILE = "nvkt_phone_mapping.json"


def _normalize_name(value: str) -> str:
    return " ".join(str(value or "").strip().lower().split())


def load_nvkt_phone_mapping(config: Optional[Dict] = None) -> Dict[str, str]:
    active_config = config or load_config()
    mapping_file = str(
        active_config.get("nvkt_phone_mapping_file")
        or DEFAULT_NVKT_PHONE_MAPPING_FILE
    ).strip()
    if not mapping_file or not os.path.exists(mapping_file):
        return {}
    try:
        with open(mapping_file, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except Exception:
        return {}
    if not isinstance(payload, dict):
        return {}
    mapping = {}
    for name, sdt in payload.items():
        if name and sdt:
            clean_sdt = str(sdt).strip()
            if not clean_sdt.startswith("0") and len(clean_sdt) == 9:
                clean_sdt = "0" + clean_sdt
            mapping[_normalize_name(name)] = clean_sdt
    return mapping


def get_nvkt_contact_phone(
    nvkt_name: str,
    config: Optional[Dict] = None,
    phone_mapping: Optional[Dict[str, str]] = None,
) -> str:
    active_config = config or {}
    hotline = str(
        active_config.get("customer_alert_hotline") or DEFAULT_CUSTOMER_ALERT_HOTLINE
    ).strip()
    if not nvkt_name:
        return hotline
    mapping = (
        phone_mapping
        if phone_mapping is not None
        else load_nvkt_phone_mapping(active_config)
    )
    normalized = _normalize_name(nvkt_name)
    return mapping.get(normalized) or hotline


def _parse_datetime(value: Union[str, datetime, None]) -> Optional[datetime]:
    if not value:
        return None
    if isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(str(value))
    except (ValueError, TypeError):
        pass
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y/%m/%d %H:%M:%S", "%d/%m/%Y %H:%M:%S"):
        try:
            return datetime.strptime(str(value), fmt)
        except ValueError:
            continue
    return None


def format_duration(duration_minutes: int) -> str:
    minutes = max(int(duration_minutes or 0), 0)
    if minutes < 60:
        return f"{minutes} phút"
    hours = minutes // 60
    remainder = minutes % 60
    if remainder > 0:
        return f"{hours} giờ {remainder} phút"
    return f"{hours} giờ"


def build_customer_alert_request_id(
    subscriber_key: str,
    first_off_time: Union[str, datetime, None],
) -> str:
    clean_sub = re.sub(r"[^a-zA-Z0-9_-]", "_", str(subscriber_key or "").strip())
    fot_dt = _parse_datetime(first_off_time)
    if fot_dt:
        time_part = fot_dt.strftime("%Y%m%d%H%M%S")
    else:
        clean_raw = re.sub(r"[^a-zA-Z0-9_-]", "_", str(first_off_time or "").strip())
        time_part = clean_raw[:32] or "unknown"
    return f"cust-off-{clean_sub}-{time_part}"


def format_customer_outage_message(
    row: Dict,
    config: Optional[Dict] = None,
    now: Optional[datetime] = None,
    phone_mapping: Optional[Dict[str, str]] = None,
) -> str:
    active_config = config or {}

    ma_tb = str(row.get("ma_tb") or "").strip()
    ten_tb = str(row.get("ten_tb") or "").strip() or "Quý khách"
    diachi_ld = str(row.get("diachi_ld") or "").strip() or "địa chỉ lắp đặt"
    ten_nvkt = str(row.get("ten_nvkt_db") or "").strip() or "Nhân viên kỹ thuật"
    contact_phone = get_nvkt_contact_phone(ten_nvkt, active_config, phone_mapping)

    fot_val = row.get("first_off_time")
    fot_dt = _parse_datetime(fot_val)
    if fot_dt:
        fot_text = fot_dt.strftime("%H:%M %d/%m/%Y")
    else:
        fot_text = str(fot_val or "").strip()

    dur_min = row.get("duration_minutes")
    if dur_min is None and fot_dt:
        ref_now = now or datetime.now()
        dur_min = max(int((ref_now - fot_dt).total_seconds() // 60), 0)
    duration_text = format_duration(dur_min if dur_min is not None else 0)

    return (
        f"VNPT Sơn Tây trân trọng thông báo:\n"
        f"Hệ thống giám sát kỹ thuật ghi nhận đường truyền của thuê bao {ma_tb} ({ten_tb}) "
        f"tại địa chỉ {diachi_ld} đang gián đoạn tín hiệu từ {fot_text} (khoảng {duration_text}).\n\n"
        f"Nếu Quý khách không chủ động tắt nguồn thiết bị (hoặc nghi ngờ đường truyền gặp sự cố), Quý khách vui lòng:\n"
        f"1. Phản hồi trực tiếp tin nhắn Zalo này; HOẶC\n"
        f"2. Liên hệ NVKT địa bàn: {ten_nvkt} qua số máy {contact_phone}\n\n"
        f"để nhân viên kỹ thuật VNPT kiểm tra và hỗ trợ xử lý kịp thời. Xin cảm ơn Quý khách!"
    )


def check_customer_alert_eligibility(
    repo: AlertRepository,
    row: Dict,
    config: Optional[Dict] = None,
    now: Optional[datetime] = None,
) -> Tuple[bool, str]:
    active_config = config or load_config()
    current_time = now or datetime.now()

    if not active_config.get("enable_customer_outage_alert", False):
        return False, "feature_disabled"

    time_window = active_config.get(
        "customer_alert_time_window", DEFAULT_CUSTOMER_ALERT_TIME_WINDOW
    )
    if not _is_within_time_window(time_window, current_time):
        return False, "quiet_hours"

    ma_tb = str(row.get("ma_tb") or "").strip()
    if not ma_tb:
        return False, "missing_ma_tb"

    subscriber_key = str(row.get("subscriber_key") or "").strip()
    if not subscriber_key:
        return False, "missing_subscriber_key"

    first_off_time = row.get("first_off_time")
    if not first_off_time:
        return False, "missing_first_off_time"

    # Layer 2: Idempotency per outage incident
    if repo.has_customer_outage_alert_sent(subscriber_key, first_off_time):
        return False, "already_sent_for_incident"

    # Layer 3: Daily limit
    max_per_day = int(
        active_config.get("customer_alert_max_per_day", DEFAULT_CUSTOMER_ALERT_MAX_PER_DAY)
    )
    since_24h = current_time - timedelta(hours=24)
    if repo.count_recent_customer_outage_alerts(subscriber_key, since_24h) >= max_per_day:
        return False, "daily_limit_exceeded"

    # Layer 4: Weekly limit
    max_per_week = int(
        active_config.get("customer_alert_max_per_week", DEFAULT_CUSTOMER_ALERT_MAX_PER_WEEK)
    )
    since_7d = current_time - timedelta(days=7)
    if repo.count_recent_customer_outage_alerts(subscriber_key, since_7d) >= max_per_week:
        return False, "weekly_limit_exceeded"

    return True, "eligible"


async def send_customer_outage_message(
    ma_tb: str,
    request_id: str,
    content: str,
    config: Optional[Dict] = None,
) -> Dict:
    active_config = config or load_config()
    base_url = str(
        active_config.get("telecom_zalo_api_url") or DEFAULT_TELECOM_ZALO_API_URL
    ).rstrip("/")
    api_key = str(active_config.get("telecom_zalo_api_key") or "").strip()
    timeout = float(
        active_config.get("customer_alert_send_timeout_seconds")
        or DEFAULT_CUSTOMER_ALERT_TIMEOUT_SECONDS
    )

    url = f"{base_url}/api/public/messages/send-by-ma-tb"
    headers = {
        "X-API-Key": api_key,
        "Content-Type": "application/json",
    }
    payload = {
        "requestId": request_id,
        "maTb": ma_tb,
        "content": content,
    }

    try:
        resp = requests.post(url, json=payload, headers=headers, timeout=timeout)
        status_code = resp.status_code
        if status_code in (200, 201, 202):
            try:
                data = resp.json()
            except Exception:
                data = resp.text
            return {
                "success": True,
                "status": "SENT",
                "status_code": status_code,
                "response": data,
                "error": None,
            }
        else:
            try:
                err_body = resp.json()
                err_msg = err_body.get("error") or str(err_body)
            except Exception:
                err_msg = resp.text[:200]
            return {
                "success": False,
                "status": "FAILED",
                "status_code": status_code,
                "error": f"HTTP {status_code}: {err_msg}",
            }
    except requests.Timeout:
        return {
            "success": False,
            "status": "FAILED",
            "status_code": None,
            "error": f"Request timeout after {timeout}s",
        }
    except requests.RequestException as exc:
        return {
            "success": False,
            "status": "FAILED",
            "status_code": None,
            "error": str(exc),
        }


async def process_customer_outage_alerts(
    repo: AlertRepository,
    candidate_rows: List[Dict],
    batch_id: str,
    config: Optional[Dict] = None,
    now: Optional[datetime] = None,
    log=print,
) -> Dict:
    active_config = config or load_config()
    current_time = now or datetime.now()

    results = {
        "pending": len(candidate_rows),
        "eligible": 0,
        "sent": 0,
        "failed": 0,
        "skipped": 0,
        "skipped_reasons": {},
        "deliveries": [],
    }

    if not active_config.get("enable_customer_outage_alert", False):
        results["skipped"] = len(candidate_rows)
        results["skipped_reasons"]["feature_disabled"] = len(candidate_rows)
        return results

    time_window = active_config.get(
        "customer_alert_time_window", DEFAULT_CUSTOMER_ALERT_TIME_WINDOW
    )
    if not _is_within_time_window(time_window, current_time):
        results["skipped"] = len(candidate_rows)
        results["skipped_reasons"]["quiet_hours"] = len(candidate_rows)
        if candidate_rows:
            log(
                f"[NOTIFY] Batch {batch_id}: customer outage alert skipped "
                f"due to quiet hours ({time_window})"
            )
        return results

    phone_mapping = load_nvkt_phone_mapping(active_config)

    for row in candidate_rows:
        subscriber_key = str(row.get("subscriber_key") or "").strip()
        ma_tb = str(row.get("ma_tb") or "").strip()
        first_off_time = row.get("first_off_time")

        is_eligible, reason = check_customer_alert_eligibility(
            repo, row, active_config, now=current_time
        )
        if not is_eligible:
            results["skipped"] += 1
            results["skipped_reasons"][reason] = (
                results["skipped_reasons"].get(reason, 0) + 1
            )
            continue

        results["eligible"] += 1
        request_id = build_customer_alert_request_id(subscriber_key, first_off_time)
        message = format_customer_outage_message(
            row, active_config, now=current_time, phone_mapping=phone_mapping
        )

        delivery_result = await send_customer_outage_message(
            ma_tb, request_id, message, active_config
        )
        success = delivery_result.get("success", False)
        status = "SENT" if success else "FAILED"
        error_reason = delivery_result.get("error")

        # Record in database log
        try:
            repo.log_customer_outage_alert(
                subscriber_key=subscriber_key,
                ma_tb=ma_tb,
                first_off_time=first_off_time,
                sent_time=current_time,
                batch_id=batch_id,
                request_id=request_id,
                status=status,
                error_reason=error_reason,
            )
        except Exception as exc:
            log(f"⚠️ Failed to log customer outage alert to db: {exc}")

        if success:
            results["sent"] += 1
        else:
            results["failed"] += 1

        results["deliveries"].append(
            {
                "batch_id": batch_id,
                "subscriber_key": subscriber_key,
                "ma_tb": ma_tb,
                "request_id": request_id,
                "status": status,
                "error": error_reason,
                "status_code": delivery_result.get("status_code"),
            }
        )

    return results
