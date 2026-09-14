import asyncio
import json
import os
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple, Union

import requests

try:
    from .alert_db import AlertRepository
    from .alert_models import CustomerOutageClaim
    from .notification_service import (
        _is_within_time_window,
        _parse_hhmm,
        _parse_strict_bool,
        load_config,
    )
    from .onebss_precheck import (
        SENDABLE_CLEAR_REASONS,
        canonicalize_ma_tb,
        run_customer_ticket_precheck,
    )
except ImportError:
    from alert_db import AlertRepository
    from alert_models import CustomerOutageClaim
    from notification_service import (
        _is_within_time_window,
        _parse_hhmm,
        _parse_strict_bool,
        load_config,
    )
    from onebss_precheck import (
        SENDABLE_CLEAR_REASONS,
        canonicalize_ma_tb,
        run_customer_ticket_precheck,
    )

try:
    from onebss_core import (
        IncidentClassification,
        IncidentDecisionReason,
        IncidentFailureKind,
    )
except ImportError:
    from onebss_core import (
        IncidentClassification,
        IncidentDecisionReason,
        IncidentFailureKind,
    )

DEFAULT_CUSTOMER_ALERT_HOTLINE = "0822036382"
DEFAULT_CUSTOMER_ALERT_TIME_WINDOW = "07:00-21:00"
DEFAULT_CUSTOMER_ALERT_START_TIME = "08:00"
DEFAULT_CUSTOMER_ALERT_END_TIME = "16:00"
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


def get_customer_alert_cutoff(
    config: Optional[Dict] = None,
    now: Optional[datetime] = None,
) -> Optional[datetime]:
    active_config = config if config is not None else load_config()
    if "customer_alert_start_time" in active_config:
        start_time_raw = str(active_config.get("customer_alert_start_time", "") or "").strip()
    else:
        start_time_raw = DEFAULT_CUSTOMER_ALERT_START_TIME
    if not start_time_raw:
        return None
    try:
        start_time = _parse_hhmm(start_time_raw)
    except ValueError:
        return None
    current_day = (now or datetime.now()).date()
    return datetime.combine(current_day, start_time)


def get_customer_alert_end_cutoff(
    config: Optional[Dict] = None,
    now: Optional[datetime] = None,
) -> Optional[datetime]:
    active_config = config if config is not None else load_config()
    if "customer_alert_end_time" in active_config:
        end_time_raw = str(active_config.get("customer_alert_end_time", "") or "").strip()
    else:
        end_time_raw = DEFAULT_CUSTOMER_ALERT_END_TIME
    if not end_time_raw:
        return None
    try:
        end_time = _parse_hhmm(end_time_raw)
    except ValueError:
        return None
    current_day = (now or datetime.now()).date()
    return datetime.combine(current_day, end_time)


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
    if not _is_within_time_window(time_window, current_time, fail_closed=True):
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

    fot_dt = _parse_datetime(first_off_time)
    if fot_dt is None:
        return False, "invalid_first_off_time"
    if fot_dt.tzinfo is not None:
        fot_dt = fot_dt.replace(tzinfo=None)

    start_cutoff = get_customer_alert_cutoff(active_config, now=current_time)
    if start_cutoff is not None and fot_dt < start_cutoff:
        return False, "before_cutoff"

    end_cutoff = get_customer_alert_end_cutoff(active_config, now=current_time)
    if end_cutoff is not None and fot_dt > end_cutoff:
        return False, "after_cutoff"

    max_retry_age_hours = float(
        active_config.get("customer_alert_max_retry_age_hours") or 24.0
    )
    if (current_time - fot_dt).total_seconds() > max_retry_age_hours * 3600:
        return False, "outage_too_old"

    # Layer 2: Idempotency per outage incident
    if repo.has_customer_outage_alert_sent(subscriber_key, first_off_time):
        return False, "already_sent_for_incident"

    can_ma = canonicalize_ma_tb(ma_tb)

    # Layer 3: Daily limit
    max_per_day = int(
        active_config.get("customer_alert_max_per_day", DEFAULT_CUSTOMER_ALERT_MAX_PER_DAY)
    )
    since_24h = current_time - timedelta(hours=24)
    if repo.count_recent_customer_outage_alerts(can_ma, since_24h, by_ma_tb=True) >= max_per_day:
        return False, "daily_limit_exceeded"

    # Layer 4: Weekly limit
    max_per_week = int(
        active_config.get("customer_alert_max_per_week", DEFAULT_CUSTOMER_ALERT_MAX_PER_WEEK)
    )
    since_7d = current_time - timedelta(days=7)
    if repo.count_recent_customer_outage_alerts(can_ma, since_7d, by_ma_tb=True) >= max_per_week:
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

    def _do_post():
        return requests.post(url, json=payload, headers=headers, timeout=timeout)

    try:
        resp = await asyncio.to_thread(_do_post)
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
    core: Any = None,
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
    }

    if not active_config.get("enable_customer_outage_alert", False):
        results["skipped"] = len(candidate_rows)
        results["skipped_reasons"]["feature_disabled"] = len(candidate_rows)
        return results

    time_window = active_config.get(
        "customer_alert_time_window", DEFAULT_CUSTOMER_ALERT_TIME_WINDOW
    )
    if not _is_within_time_window(time_window, current_time, fail_closed=True):
        results["skipped"] = len(candidate_rows)
        results["skipped_reasons"]["quiet_hours"] = len(candidate_rows)
        if candidate_rows:
            log(
                f"[NOTIFY] Batch {batch_id}: customer outage alert skipped "
                f"due to quiet hours ({time_window})"
            )
        return results

    def _skip_row(reason: str):
        results["skipped"] += 1
        results["skipped_reasons"][reason] = (
            results["skipped_reasons"].get(reason, 0) + 1
        )

    # Pass 1: Local eligibility and atomic claim
    prepared: List[Dict[str, Any]] = []
    lease_seconds = float(
        active_config.get("customer_ticket_precheck_claim_lease_seconds") or 120.0
    )

    for idx, row in enumerate(candidate_rows):
        subscriber_key = str(row.get("subscriber_key") or "").strip()
        if not subscriber_key:
            _skip_row("missing_subscriber_key")
            continue

        raw_ma_tb = row.get("ma_tb")
        canonical_ma = canonicalize_ma_tb(raw_ma_tb)
        if not canonical_ma:
            _skip_row("missing_ma_tb")
            continue

        first_off_time = row.get("first_off_time")
        if not first_off_time:
            _skip_row("missing_first_off_time")
            continue

        fot_dt = _parse_datetime(first_off_time)
        if fot_dt is None:
            _skip_row("invalid_first_off_time")
            continue
        if fot_dt.tzinfo is not None:
            fot_dt = fot_dt.replace(tzinfo=None)

        request_id = build_customer_alert_request_id(subscriber_key, first_off_time)

        prior = repo.get_customer_outage_alert_disposition(request_id)
        prior_status = prior.get("status") if prior else None

        if prior_status == "SKIPPED_CUSTOMER_TICKET":
            _skip_row("existing_customer_ticket")
            continue

        if prior_status == "SENT":
            _skip_row("already_sent_for_incident")
            continue

        if prior_status == "FAILED" and prior.get("error_reason") == "outage_too_old":
            _skip_row("outage_too_old")
            continue

        if repo.has_customer_outage_alert_sent(subscriber_key, first_off_time):
            _skip_row("already_sent_for_incident")
            continue

        is_retry = prior_status in {"PRECHECK_FAILED", "INDETERMINATE", "STALE"}

        # Outage aging cutoff: reject if older than customer_alert_max_retry_age_hours
        max_retry_age_hours = float(
            active_config.get("customer_alert_max_retry_age_hours") or 24.0
        )
        if (current_time - fot_dt).total_seconds() > max_retry_age_hours * 3600:
            _skip_row("outage_too_old")
            if is_retry:
                claim = repo.claim_customer_outage_alert_precheck(
                    subscriber_key=subscriber_key,
                    ma_tb=canonical_ma,
                    first_off_time=first_off_time,
                    claimed_at=current_time,
                    batch_id=batch_id,
                    request_id=request_id,
                    lease_seconds=lease_seconds,
                )
                if claim.outcome == "CLAIMED":
                    repo.finalize_customer_outage_alert_precheck(
                        request_id=request_id,
                        batch_id=batch_id,
                        expected_claimed_at=claim.claimed_at,
                        status="FAILED",
                        finalized_at=current_time,
                        error_reason="outage_too_old",
                    )
            continue

        if not is_retry:
            start_cutoff = get_customer_alert_cutoff(active_config, now=current_time)
            if start_cutoff is not None and fot_dt < start_cutoff:
                _skip_row("before_cutoff")
                continue

            end_cutoff = get_customer_alert_end_cutoff(active_config, now=current_time)
            if end_cutoff is not None and fot_dt > end_cutoff:
                _skip_row("after_cutoff")
                continue

        # Preliminary rate check against persisted SENT rows by customer account (ma_tb)
        max_per_day = int(
            active_config.get("customer_alert_max_per_day", DEFAULT_CUSTOMER_ALERT_MAX_PER_DAY)
        )
        since_24h = current_time - timedelta(hours=24)
        if repo.count_recent_customer_outage_alerts(canonical_ma, since_24h, by_ma_tb=True) >= max_per_day:
            _skip_row("daily_limit_exceeded")
            continue

        max_per_week = int(
            active_config.get("customer_alert_max_per_week", DEFAULT_CUSTOMER_ALERT_MAX_PER_WEEK)
        )
        since_7d = current_time - timedelta(days=7)
        if repo.count_recent_customer_outage_alerts(canonical_ma, since_7d, by_ma_tb=True) >= max_per_week:
            _skip_row("weekly_limit_exceeded")
            continue

        claim = repo.claim_customer_outage_alert_precheck(
            subscriber_key=subscriber_key,
            ma_tb=canonical_ma,
            first_off_time=first_off_time,
            claimed_at=current_time,
            batch_id=batch_id,
            request_id=request_id,
            lease_seconds=lease_seconds,
        )

        if claim.outcome == "TERMINAL":
            if claim.prior_status == "SKIPPED_CUSTOMER_TICKET":
                _skip_row("existing_customer_ticket")
            else:
                _skip_row("already_sent_for_incident")
            continue
        elif claim.outcome == "LEASE_HELD":
            _skip_row("precheck_in_progress")
            continue
        elif claim.outcome != "CLAIMED":
            _skip_row("claim_failed")
            continue

        prepared.append(
            {
                "original_index": idx,
                "row": row,
                "subscriber_key": subscriber_key,
                "canonical_ma_tb": canonical_ma,
                "first_off_time": first_off_time,
                "fot_dt": fot_dt,
                "request_id": request_id,
                "claimed_at": claim.claimed_at,
                "prior_status": claim.prior_status,
                "prior_sent_time": claim.prior_sent_time,
            }
        )

    # Count retried rows
    onebss_retried = 0
    for item in prepared:
        if item["prior_status"] in {"PRECHECK_FAILED", "INDETERMINATE", "STALE"}:
            onebss_retried += 1
    results["onebss_retried"] = onebss_retried

    # Sort retries first by oldest prior sent_time, then new rows by stable input order
    def _item_sort_key(item):
        is_ret = item["prior_status"] in {"PRECHECK_FAILED", "INDETERMINATE", "STALE"}
        pst = item["prior_sent_time"]
        if pst is not None and pst.tzinfo is not None:
            pst = pst.replace(tzinfo=None)
        prior_t = pst or datetime.min
        return (0 if is_ret else 1, prior_t if is_ret else item["original_index"])

    prepared.sort(key=_item_sort_key)

    enable_precheck = _parse_strict_bool(
        active_config.get("enable_customer_ticket_precheck", True),
        default=True,
        name="enable_customer_ticket_precheck",
    )

    unique_ma_tb_list = list(dict.fromkeys(item["canonical_ma_tb"] for item in prepared))

    if enable_precheck:
        results["precheck_state"] = "ENFORCED"
        if unique_ma_tb_list:
            precheck_result = await asyncio.to_thread(
                run_customer_ticket_precheck,
                unique_ma_tb_list,
                active_config,
                core,
            )
            results["onebss_checked"] = len(unique_ma_tb_list)
            if hasattr(precheck_result, "metrics") and precheck_result.metrics:
                m = precheck_result.metrics
                results["onebss_request_count"] = m.request_count
                results["onebss_request_duration_ms_total"] = m.request_duration_ms_total
                results["onebss_request_duration_ms_max"] = m.request_duration_ms_max
                results["onebss_batch_duration_ms"] = m.batch_duration_ms
                results["onebss_deadline_expired"] = m.deadline_expired_count
        else:
            precheck_result = None
    else:
        results["precheck_state"] = "BYPASSED"
        precheck_result = None
        log(f"[WARNING] Batch {batch_id}: customer ticket precheck is BYPASSED")

    # Pass 2: Sequential send loop
    phone_mapping = load_nvkt_phone_mapping(active_config)
    same_batch_success_counts: Dict[str, int] = {}
    same_batch_reserved_counts: Dict[str, int] = {}
    cycle_baseline_sent_24h: Dict[str, int] = {}
    cycle_baseline_sent_7d: Dict[str, int] = {}

    max_fact_age = float(
        active_config.get("customer_ticket_precheck_max_fact_age_seconds") or 60.0
    )

    for item in prepared:
        sub_key = item["subscriber_key"]
        can_ma = item["canonical_ma_tb"]
        req_id = item["request_id"]
        expected_claimed = item["claimed_at"]
        row = item["row"]

        if results["precheck_state"] == "ENFORCED":
            decision = (
                precheck_result.decisions.get(can_ma) if precheck_result else None
            )
            if decision is None:
                repo.finalize_customer_outage_alert_precheck(
                    request_id=req_id,
                    batch_id=batch_id,
                    expected_claimed_at=expected_claimed,
                    status="PRECHECK_FAILED",
                    finalized_at=current_time,
                    error_reason="contract_violation",
                )
                _skip_row("contract_violation")
                continue

            cls_val = (
                decision.classification.value
                if hasattr(decision.classification, "value")
                else str(decision.classification)
            )
            fail_val = (
                decision.failure_kind.value
                if hasattr(decision.failure_kind, "value")
                else str(decision.failure_kind)
            )
            if cls_val in results["classification_counts"]:
                results["classification_counts"][cls_val] += 1
            if fail_val in results["failure_kind_counts"]:
                results["failure_kind_counts"][fail_val] += 1

            if (
                decision.classification == IncidentClassification.CUSTOMER_OPEN_TICKET
                and decision.failure_kind == IncidentFailureKind.NONE
            ):
                repo.finalize_customer_outage_alert_precheck(
                    request_id=req_id,
                    batch_id=batch_id,
                    expected_claimed_at=expected_claimed,
                    status="SKIPPED_CUSTOMER_TICKET",
                    finalized_at=current_time,
                    error_reason="customer_open_ticket",
                )
                _skip_row("existing_customer_ticket")
                continue

            if decision.classification == IncidentClassification.INDETERMINATE:
                if decision.failure_kind in (
                    IncidentFailureKind.AUTH,
                    IncidentFailureKind.API,
                    IncidentFailureKind.REQUEST_TIMEOUT,
                    IncidentFailureKind.BATCH_TIMEOUT,
                    IncidentFailureKind.PARTIAL,
                    IncidentFailureKind.CONTRACT,
                ):
                    final_status = "PRECHECK_FAILED"
                    err_code = "precheck_failed"
                else:
                    final_status = "INDETERMINATE"
                    err_code = "indeterminate"
                repo.finalize_customer_outage_alert_precheck(
                    request_id=req_id,
                    batch_id=batch_id,
                    expected_claimed_at=expected_claimed,
                    status=final_status,
                    finalized_at=current_time,
                    error_reason=err_code,
                )
                _skip_row(err_code)
                continue

            if not (
                decision.classification == IncidentClassification.CLEAR
                and decision.failure_kind == IncidentFailureKind.NONE
                and decision.reason in SENDABLE_CLEAR_REASONS
            ):
                repo.finalize_customer_outage_alert_precheck(
                    request_id=req_id,
                    batch_id=batch_id,
                    expected_claimed_at=expected_claimed,
                    status="PRECHECK_FAILED",
                    finalized_at=current_time,
                    error_reason="contract_violation",
                )
                _skip_row("contract_violation")
                continue

            now_utc = datetime.now(timezone.utc)
            fact_age = (now_utc - decision.checked_at).total_seconds()
            if fact_age > max_fact_age:
                repo.finalize_customer_outage_alert_precheck(
                    request_id=req_id,
                    batch_id=batch_id,
                    expected_claimed_at=expected_claimed,
                    status="STALE",
                    finalized_at=current_time,
                    error_reason="onebss_stale",
                )
                _skip_row("onebss_stale")
                results["onebss_stale"] += 1
                continue

        # Refresh owned claim lease using fresh timestamp
        now_refresh = datetime.now()
        refreshed_at = repo.refresh_customer_outage_alert_precheck_claim(
            request_id=req_id,
            batch_id=batch_id,
            expected_claimed_at=expected_claimed,
            refreshed_at=now_refresh,
        )
        if refreshed_at is None:
            _skip_row("lease_lost")
            continue
        expected_claimed = refreshed_at

        # Re-query and reserve daily/weekly allowance by customer account (can_ma)
        if can_ma not in cycle_baseline_sent_24h:
            cycle_baseline_sent_24h[can_ma] = (
                repo.count_recent_customer_outage_alerts(
                    can_ma, current_time - timedelta(hours=24), by_ma_tb=True
                )
            )
            cycle_baseline_sent_7d[can_ma] = (
                repo.count_recent_customer_outage_alerts(
                    can_ma, current_time - timedelta(days=7), by_ma_tb=True
                )
            )

        fresh_24h = repo.count_recent_customer_outage_alerts(
            can_ma, current_time - timedelta(hours=24), by_ma_tb=True
        )
        fresh_7d = repo.count_recent_customer_outage_alerts(
            can_ma, current_time - timedelta(days=7), by_ma_tb=True
        )
        success_count = same_batch_success_counts.get(can_ma, 0)
        reserved_count = same_batch_reserved_counts.get(can_ma, 0)

        eff_24h = max(
            fresh_24h, cycle_baseline_sent_24h[can_ma] + success_count + reserved_count
        )
        eff_7d = max(
            fresh_7d, cycle_baseline_sent_7d[can_ma] + success_count + reserved_count
        )

        max_per_day = int(
            active_config.get(
                "customer_alert_max_per_day", DEFAULT_CUSTOMER_ALERT_MAX_PER_DAY
            )
        )
        max_per_week = int(
            active_config.get(
                "customer_alert_max_per_week", DEFAULT_CUSTOMER_ALERT_MAX_PER_WEEK
            )
        )

        if eff_24h >= max_per_day:
            repo.finalize_customer_outage_alert_precheck(
                request_id=req_id,
                batch_id=batch_id,
                expected_claimed_at=expected_claimed,
                status="FAILED",
                finalized_at=datetime.now(),
                error_reason="daily_limit_exceeded",
            )
            _skip_row("daily_limit_exceeded")
            continue

        if eff_7d >= max_per_week:
            repo.finalize_customer_outage_alert_precheck(
                request_id=req_id,
                batch_id=batch_id,
                expected_claimed_at=expected_claimed,
                status="FAILED",
                finalized_at=datetime.now(),
                error_reason="weekly_limit_exceeded",
            )
            _skip_row("weekly_limit_exceeded")
            continue

        # Provisional reservation
        same_batch_reserved_counts[can_ma] = reserved_count + 1

        results["eligible"] += 1
        msg_text = format_customer_outage_message(
            row, active_config, now=current_time, phone_mapping=phone_mapping
        )
        delivery_result = await send_customer_outage_message(
            can_ma, req_id, msg_text, active_config
        )
        same_batch_reserved_counts[can_ma] = max(
            0, same_batch_reserved_counts[can_ma] - 1
        )

        success = delivery_result.get("success", False)
        err_reason = delivery_result.get("error")
        if success:
            same_batch_success_counts[can_ma] = success_count + 1
            status_str = "SENT"
            results["sent"] += 1
        else:
            status_str = "FAILED"
            results["failed"] += 1

        finalized_ok = repo.finalize_customer_outage_alert_precheck(
            request_id=req_id,
            batch_id=batch_id,
            expected_claimed_at=expected_claimed,
            status=status_str,
            finalized_at=datetime.now(),
            error_reason=err_reason,
        )
        if not finalized_ok:
            log(f"[WARNING] Batch {batch_id}: CAS finalize failed for {req_id} (claim lease stolen or state conflict)")
            if success:
                results["sent"] = max(0, results["sent"] - 1)
                results["failed"] += 1
                status_str = "FAILED"
                err_reason = "cas_conflict"

        results["deliveries"].append(
            {
                "batch_id": batch_id,
                "subscriber_key": sub_key,
                "ma_tb": can_ma,
                "request_id": req_id,
                "status": status_str,
                "error": err_reason,
                "status_code": delivery_result.get("status_code"),
            }
        )

    return results
