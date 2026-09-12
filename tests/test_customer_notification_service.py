import asyncio
import json
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import requests

from onebss_core import (
    IncidentClassification,
    IncidentDecisionReason,
    IncidentFailureKind,
    IncidentPrecheckBatchMetrics,
    IncidentPrecheckBatchResult,
    IncidentPrecheckDecision,
)

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

try:
    from do_chu_dong_api.alert_db import AlertRepository
    from do_chu_dong_api.customer_notification_service import (
        build_customer_alert_request_id,
        check_customer_alert_eligibility,
        format_customer_outage_message,
        format_duration,
        get_customer_alert_cutoff,
        get_customer_alert_end_cutoff,
        get_nvkt_contact_phone,
        load_nvkt_phone_mapping,
        process_customer_outage_alerts,
        send_customer_outage_message,
    )
except ModuleNotFoundError:
    from alert_db import AlertRepository
    from customer_notification_service import (
        build_customer_alert_request_id,
        check_customer_alert_eligibility,
        format_customer_outage_message,
        format_duration,
        get_customer_alert_cutoff,
        get_customer_alert_end_cutoff,
        get_nvkt_contact_phone,
        load_nvkt_phone_mapping,
        process_customer_outage_alerts,
        send_customer_outage_message,
    )


@pytest.fixture
def temp_repo(tmp_path):
    db_file = tmp_path / "onu_measurements.db"
    src_file = tmp_path / "database.db"

    # Create dummy source db
    with sqlite3.connect(src_file) as conn:
        conn.execute("CREATE TABLE danhba (ID INTEGER PRIMARY KEY)")

    repo = AlertRepository(str(db_file), str(src_file))
    repo.ensure_schema()
    return repo


def test_format_duration():
    assert format_duration(0) == "0 phút"
    assert format_duration(45) == "45 phút"
    assert format_duration(60) == "1 giờ"
    assert format_duration(75) == "1 giờ 15 phút"
    assert format_duration(120) == "2 giờ"
    assert format_duration(150) == "2 giờ 30 phút"
    assert format_duration(-5) == "0 phút"


def test_build_customer_alert_request_id():
    fot = datetime(2026, 9, 11, 14, 30, 0)
    req_id = build_customer_alert_request_id("STY_0_1_1:1", fot)
    assert req_id == "cust-off-STY_0_1_1_1-20260911143000"

    # With string timestamp
    req_id_str = build_customer_alert_request_id("SUB1", "2026-09-11T10:15:00")
    assert req_id_str == "cust-off-SUB1-20260911101500"


def test_nvkt_phone_mapping():
    mapping = load_nvkt_phone_mapping()
    assert "nguyễn lâm bách" in mapping
    assert mapping["nguyễn lâm bách"] == "0838662568"
    assert "trần bình minh" in mapping
    assert mapping["trần bình minh"] == "0918424833"
    assert "lê văn tuấn" in mapping
    assert mapping["lê văn tuấn"] == "0942468464"

    # Test get_nvkt_contact_phone
    config = {"customer_alert_hotline": "0822036382"}
    assert get_nvkt_contact_phone("Nguyễn Lâm Bách", config, mapping) == "0838662568"
    assert get_nvkt_contact_phone("Trần Bình Minh", config, mapping) == "0918424833"
    assert get_nvkt_contact_phone("Người Lạ", config, mapping) == "0822036382"


def test_format_customer_outage_message():
    row = {
        "ma_tb": "TB001",
        "ten_tb": "Nguyen Van A",
        "diachi_ld": "12 Pho Hue",
        "ten_nvkt_db": "Nguyễn Lâm Bách",
        "first_off_time": "2026-09-11T14:30:00",
        "duration_minutes": 90,
    }
    config = {
        "customer_alert_hotline": "0822036382",
    }
    phone_map = {"nguyễn lâm bách": "0838662568"}
    msg = format_customer_outage_message(row, config, phone_mapping=phone_map)
    assert "VNPT Sơn Tây trân trọng thông báo:" in msg
    assert "TB001" in msg
    assert "Nguyen Van A" in msg
    assert "12 Pho Hue" in msg
    assert "14:30 11/09/2026" in msg
    assert "1 giờ 30 phút" in msg
    assert "Nguyễn Lâm Bách" in msg
    assert "0838662568" in msg

    # Fallback test
    row_unknown = {**row, "ten_nvkt_db": "Chưa Phân Công"}
    msg_unknown = format_customer_outage_message(row_unknown, config, phone_mapping=phone_map)
    assert "0822036382" in msg_unknown
    assert "Chưa Phân Công" in msg_unknown


def test_check_customer_alert_eligibility_disabled(temp_repo):
    row = {
        "subscriber_key": "SUB1",
        "ma_tb": "TB001",
        "first_off_time": "2026-09-11T10:00:00",
    }
    config = {"enable_customer_outage_alert": False}
    eligible, reason = check_customer_alert_eligibility(temp_repo, row, config)
    assert not eligible
    assert reason == "feature_disabled"


def test_check_customer_alert_eligibility_quiet_hours(temp_repo):
    row = {
        "subscriber_key": "SUB1",
        "ma_tb": "TB001",
        "first_off_time": "2026-09-11T23:00:00",
    }
    config = {
        "enable_customer_outage_alert": True,
        "customer_alert_time_window": "07:00-21:00",
    }
    night_time = datetime(2026, 9, 11, 23, 15, 0)
    eligible, reason = check_customer_alert_eligibility(
        temp_repo, row, config, now=night_time
    )
    assert not eligible
    assert reason == "quiet_hours"


def test_check_customer_alert_eligibility_idempotency(temp_repo):
    row = {
        "subscriber_key": "SUB1",
        "ma_tb": "TB001",
        "first_off_time": "2026-09-11T10:00:00",
    }
    config = {
        "enable_customer_outage_alert": True,
        "customer_alert_time_window": "07:00-21:00",
        "customer_alert_max_per_day": 5,
        "customer_alert_max_per_week": 10,
    }
    now = datetime(2026, 9, 11, 10, 30, 0)

    # First time: eligible
    eligible, reason = check_customer_alert_eligibility(temp_repo, row, config, now=now)
    assert eligible
    assert reason == "eligible"

    # Record as SENT
    temp_repo.claim_customer_outage_alert_precheck(
        subscriber_key="SUB1",
        ma_tb="TB001",
        first_off_time="2026-09-11T10:00:00",
        claimed_at=now,
        batch_id="batch1",
        request_id="cust-off-SUB1-20260911100000",
        lease_seconds=120,
    )
    temp_repo.finalize_customer_outage_alert_precheck(
        request_id="cust-off-SUB1-20260911100000",
        batch_id="batch1",
        expected_claimed_at=now,
        status="SENT",
        finalized_at=now,
    )

    # Next check with same first_off_time: blocked
    eligible2, reason2 = check_customer_alert_eligibility(
        temp_repo, row, config, now=now + timedelta(minutes=45)
    )
    assert not eligible2
    assert reason2 == "already_sent_for_incident"


def test_check_customer_alert_eligibility_daily_limit(temp_repo):
    config = {
        "enable_customer_outage_alert": True,
        "customer_alert_time_window": "07:00-21:00",
        "customer_alert_max_per_day": 1,
        "customer_alert_max_per_week": 3,
    }
    now = datetime(2026, 9, 11, 15, 0, 0)

    # First incident sent earlier today
    sent_t1 = now - timedelta(hours=5)
    temp_repo.claim_customer_outage_alert_precheck(
        subscriber_key="SUB_LIMIT",
        ma_tb="TB_LIMIT",
        first_off_time="2026-09-11T08:00:00",
        claimed_at=sent_t1,
        batch_id="batch1",
        request_id="req1",
        lease_seconds=120,
    )
    temp_repo.finalize_customer_outage_alert_precheck(
        request_id="req1",
        batch_id="batch1",
        expected_claimed_at=sent_t1,
        status="SENT",
        finalized_at=sent_t1,
    )

    # New incident today with different first_off_time
    new_row = {
        "subscriber_key": "SUB_LIMIT",
        "ma_tb": "TB_LIMIT",
        "first_off_time": "2026-09-11T14:00:00",
    }
    eligible, reason = check_customer_alert_eligibility(temp_repo, new_row, config, now=now)
    assert not eligible
    assert reason == "daily_limit_exceeded"


def test_check_customer_alert_eligibility_weekly_limit(temp_repo):
    config = {
        "enable_customer_outage_alert": True,
        "customer_alert_time_window": "07:00-21:00",
        "customer_alert_max_per_day": 2,
        "customer_alert_max_per_week": 3,
    }
    now = datetime(2026, 9, 11, 15, 0, 0)

    # 3 incidents sent over the last 5 days
    for i in range(1, 4):
        sent_ti = now - timedelta(days=i)
        temp_repo.claim_customer_outage_alert_precheck(
            subscriber_key="SUB_WEEK",
            ma_tb="TB_WEEK",
            first_off_time=f"2026-09-0{i}T08:00:00",
            claimed_at=sent_ti,
            batch_id=f"batch{i}",
            request_id=f"req{i}",
            lease_seconds=120,
        )
        temp_repo.finalize_customer_outage_alert_precheck(
            request_id=f"req{i}",
            batch_id=f"batch{i}",
            expected_claimed_at=sent_ti,
            status="SENT",
            finalized_at=sent_ti,
        )

    # 4th incident: should exceed weekly limit of 3
    new_row = {
        "subscriber_key": "SUB_WEEK",
        "ma_tb": "TB_WEEK",
        "first_off_time": "2026-09-11T14:00:00",
    }
    eligible, reason = check_customer_alert_eligibility(temp_repo, new_row, config, now=now)
    assert not eligible
    assert reason == "weekly_limit_exceeded"


def test_send_customer_outage_message_success():
    config = {
        "telecom_zalo_api_url": "http://localhost:3002",
        "telecom_zalo_api_key": "test_key",
    }
    mock_resp = MagicMock()
    mock_resp.status_code = 202
    mock_resp.json.return_value = {"status": "queued"}

    with patch("requests.post", return_value=mock_resp) as mock_post:
        res = asyncio.run(
            send_customer_outage_message("TB001", "req-1", "msg", config)
        )
        assert res["success"] is True
        assert res["status"] == "SENT"
        assert res["status_code"] == 202
        mock_post.assert_called_once()


def test_send_customer_outage_message_failure():
    config = {
        "telecom_zalo_api_url": "http://localhost:3002",
        "telecom_zalo_api_key": "test_key",
    }
    mock_resp = MagicMock()
    mock_resp.status_code = 404
    mock_resp.json.return_value = {"error": "Subscriber or message request not found"}

    with patch("requests.post", return_value=mock_resp):
        res = asyncio.run(
            send_customer_outage_message("TB001", "req-1", "msg", config)
        )
        assert res["success"] is False
        assert res["status"] == "FAILED"
        assert "404" in res["error"]


def test_send_customer_outage_message_timeout():
    config = {
        "telecom_zalo_api_url": "http://localhost:3002",
        "telecom_zalo_api_key": "test_key",
        "customer_alert_send_timeout_seconds": 1.0,
    }
    with patch("requests.post", side_effect=requests.Timeout("timed out")):
        res = asyncio.run(
            send_customer_outage_message("TB001", "req-1", "msg", config)
        )
        assert res["success"] is False
        assert res["status"] == "FAILED"
        assert "timeout" in res["error"].lower()


def test_process_customer_outage_alerts_batch(temp_repo):
    config = {
        "enable_customer_outage_alert": True,
        "customer_alert_time_window": "07:00-21:00",
        "customer_alert_hotline": "0822036382",
        "customer_alert_max_per_day": 1,
        "customer_alert_max_per_week": 3,
        "telecom_zalo_api_url": "http://localhost:3002",
        "telecom_zalo_api_key": "test_key",
    }
    now = datetime(2026, 9, 11, 10, 0, 0)
    candidates = [
        {
            "subscriber_key": "SUB_BATCH_1",
            "ma_tb": "TB001",
            "ten_tb": "Nguyen A",
            "diachi_ld": "Dia chi 1",
            "ten_nvkt_db": "NVKT 1",
            "first_off_time": "2026-09-11T09:00:00",
            "duration_minutes": 60,
        },
        {
            "subscriber_key": "SUB_BATCH_2",
            "ma_tb": "",  # missing ma_tb -> skipped
            "first_off_time": "2026-09-11T09:00:00",
        },
    ]

    mock_resp = MagicMock()
    mock_resp.status_code = 202
    mock_resp.json.return_value = {"status": "queued"}

    mock_core = MagicMock()
    mock_core.lookup_open_incident_facts_batch.return_value = IncidentPrecheckBatchResult(
        decisions={
            "TB001": IncidentPrecheckDecision(
                classification=IncidentClassification.CLEAR,
                failure_kind=IncidentFailureKind.NONE,
                reason=IncidentDecisionReason.NO_INCIDENTS_CONFIRMED,
                checked_at=datetime.now(timezone.utc),
            )
        },
        metrics=IncidentPrecheckBatchMetrics(1, 1, 1, 10.0, 10.0, 10.0, 0),
    )

    with patch("requests.post", return_value=mock_resp):
        summary = asyncio.run(
            process_customer_outage_alerts(
                temp_repo,
                candidates,
                "batch_100",
                config=config,
                now=now,
                core=mock_core,
            )
        )
        assert summary["pending"] == 2
        assert summary["eligible"] == 1
        assert summary["sent"] == 1
        assert summary["failed"] == 0
        assert summary["skipped"] == 1
        assert summary["skipped_reasons"]["missing_ma_tb"] == 1

        # Check DB entry
        assert temp_repo.has_customer_outage_alert_sent(
            "SUB_BATCH_1", "2026-09-11T09:00:00"
        )


def test_get_customer_alert_cutoff():
    ref_now = datetime(2026, 9, 11, 14, 30, 0)
    # Default 08:00
    cutoff = get_customer_alert_cutoff({}, now=ref_now)
    assert cutoff == datetime(2026, 9, 11, 8, 0, 0)

    # Custom time
    cutoff_custom = get_customer_alert_cutoff(
        {"customer_alert_start_time": "09:15"}, now=ref_now
    )
    assert cutoff_custom == datetime(2026, 9, 11, 9, 15, 0)

    # Empty string disables cutoff
    cutoff_empty = get_customer_alert_cutoff(
        {"customer_alert_start_time": ""}, now=ref_now
    )
    assert cutoff_empty is None


def test_get_customer_alert_end_cutoff():
    ref_now = datetime(2026, 9, 11, 14, 30, 0)
    # Default 16:00
    cutoff = get_customer_alert_end_cutoff({}, now=ref_now)
    assert cutoff == datetime(2026, 9, 11, 16, 0, 0)

    # Custom time
    cutoff_custom = get_customer_alert_end_cutoff(
        {"customer_alert_end_time": "17:30"}, now=ref_now
    )
    assert cutoff_custom == datetime(2026, 9, 11, 17, 30, 0)

    # Empty string disables end cutoff
    cutoff_empty = get_customer_alert_end_cutoff(
        {"customer_alert_end_time": ""}, now=ref_now
    )
    assert cutoff_empty is None


def test_check_customer_alert_eligibility_before_and_after_cutoff(temp_repo):
    config = {
        "enable_customer_outage_alert": True,
        "customer_alert_time_window": "07:00-21:00",
        "customer_alert_start_time": "08:00",
        "customer_alert_end_time": "16:00",
    }
    now = datetime(2026, 9, 11, 18, 0, 0)

    # Outage from previous day (e.g. 2026-09-10) -> blocked
    row_yesterday = {
        "subscriber_key": "SUB_YESTERDAY",
        "ma_tb": "TB_YESTERDAY",
        "first_off_time": "2026-09-10T15:00:00",
    }
    eligible, reason = check_customer_alert_eligibility(
        temp_repo, row_yesterday, config, now=now
    )
    assert not eligible
    assert reason == "before_cutoff"

    # Outage from months ago -> blocked
    row_months_ago = {
        "subscriber_key": "SUB_OLD",
        "ma_tb": "TB_OLD",
        "first_off_time": "2026-04-24T15:29:06",
    }
    eligible, reason = check_customer_alert_eligibility(
        temp_repo, row_months_ago, config, now=now
    )
    assert not eligible
    assert reason == "before_cutoff"

    # Outage from today before 08:00 (e.g. 07:15) -> blocked
    row_morning = {
        "subscriber_key": "SUB_MORNING",
        "ma_tb": "TB_MORNING",
        "first_off_time": "2026-09-11T07:15:00",
    }
    eligible, reason = check_customer_alert_eligibility(
        temp_repo, row_morning, config, now=now
    )
    assert not eligible
    assert reason == "before_cutoff"

    # Outage from today after 16:00 (e.g. 16:30) -> blocked
    row_late = {
        "subscriber_key": "SUB_LATE",
        "ma_tb": "TB_LATE",
        "first_off_time": "2026-09-11T16:30:00",
    }
    eligible, reason = check_customer_alert_eligibility(
        temp_repo, row_late, config, now=now
    )
    assert not eligible
    assert reason == "after_cutoff"

    # Outage from today at exactly 08:00 -> eligible
    row_exact_start = {
        "subscriber_key": "SUB_EXACT_START",
        "ma_tb": "TB_EXACT_START",
        "first_off_time": "2026-09-11T08:00:00",
    }
    eligible, reason = check_customer_alert_eligibility(
        temp_repo, row_exact_start, config, now=now
    )
    assert eligible
    assert reason == "eligible"

    # Outage from today at exactly 16:00 -> eligible
    row_exact_end = {
        "subscriber_key": "SUB_EXACT_END",
        "ma_tb": "TB_EXACT_END",
        "first_off_time": "2026-09-11T16:00:00",
    }
    eligible, reason = check_customer_alert_eligibility(
        temp_repo, row_exact_end, config, now=now
    )
    assert eligible
    assert reason == "eligible"

    # Outage from today within [08:00, 16:00] (e.g. 14:15) -> eligible
    row_in_window = {
        "subscriber_key": "SUB_IN_WINDOW",
        "ma_tb": "TB_IN_WINDOW",
        "first_off_time": "2026-09-11T14:15:00",
    }
    eligible, reason = check_customer_alert_eligibility(
        temp_repo, row_in_window, config, now=now
    )
    assert eligible
    assert reason == "eligible"


def test_process_customer_outage_alerts_cutoff_filter(temp_repo):
    config = {
        "enable_customer_outage_alert": True,
        "customer_alert_time_window": "07:00-21:00",
        "customer_alert_start_time": "08:00",
        "customer_alert_end_time": "16:00",
        "telecom_zalo_api_url": "http://localhost:3002",
        "telecom_zalo_api_key": "test_key",
    }
    now = datetime(2026, 9, 11, 18, 0, 0)
    candidates = [
        {
            "subscriber_key": "SUB_OLD",
            "ma_tb": "TB_OLD",
            "first_off_time": "2026-09-10T22:00:00",
        },
        {
            "subscriber_key": "SUB_EARLY_TODAY",
            "ma_tb": "TB_EARLY",
            "first_off_time": "2026-09-11T07:30:00",
        },
        {
            "subscriber_key": "SUB_VALID",
            "ma_tb": "TB_VALID",
            "first_off_time": "2026-09-11T11:15:00",
            "duration_minutes": 105,
        },
        {
            "subscriber_key": "SUB_AFTER_16H",
            "ma_tb": "TB_AFTER_16H",
            "first_off_time": "2026-09-11T17:30:00",
            "duration_minutes": 30,
        },
    ]

    mock_resp = MagicMock()
    mock_resp.status_code = 202
    mock_resp.json.return_value = {"status": "queued"}

    mock_core = MagicMock()
    mock_core.lookup_open_incident_facts_batch.return_value = IncidentPrecheckBatchResult(
        decisions={
            "TB_VALID": IncidentPrecheckDecision(
                classification=IncidentClassification.CLEAR,
                failure_kind=IncidentFailureKind.NONE,
                reason=IncidentDecisionReason.NO_INCIDENTS_CONFIRMED,
                checked_at=datetime.now(timezone.utc),
            )
        },
        metrics=IncidentPrecheckBatchMetrics(1, 1, 1, 10.0, 10.0, 10.0, 0),
    )

    with patch("requests.post", return_value=mock_resp):
        summary = asyncio.run(
            process_customer_outage_alerts(
                temp_repo,
                candidates,
                "batch_cutoff",
                config=config,
                now=now,
                core=mock_core,
            )
        )
        assert summary["pending"] == 4
        assert summary["eligible"] == 1
        assert summary["sent"] == 1
        assert summary["skipped"] == 3
        assert summary["skipped_reasons"]["before_cutoff"] == 2
        assert summary["skipped_reasons"]["after_cutoff"] == 1
        assert temp_repo.has_customer_outage_alert_sent("SUB_VALID", "2026-09-11T11:15:00")
        assert not temp_repo.has_customer_outage_alert_sent("SUB_OLD", "2026-09-10T22:00:00")
        assert not temp_repo.has_customer_outage_alert_sent("SUB_AFTER_16H", "2026-09-11T17:30:00")


def test_process_customer_outage_alerts_precheck_customer_open_ticket(temp_repo):
    config = {
        "enable_customer_outage_alert": True,
        "customer_alert_time_window": "07:00-21:00",
        "customer_alert_start_time": "08:00",
        "customer_alert_end_time": "16:00",
    }
    now = datetime(2026, 9, 11, 10, 0, 0)
    candidates = [
        {
            "subscriber_key": "SUB_TICKET",
            "ma_tb": "TB_TICKET",
            "first_off_time": "2026-09-11T09:00:00",
        }
    ]

    mock_core = MagicMock()
    mock_core.lookup_open_incident_facts_batch.return_value = IncidentPrecheckBatchResult(
        decisions={
            "TB_TICKET": IncidentPrecheckDecision(
                classification=IncidentClassification.CUSTOMER_OPEN_TICKET,
                failure_kind=IncidentFailureKind.NONE,
                reason=IncidentDecisionReason.CUSTOMER_OPEN_CONFIRMED,
                checked_at=datetime.now(timezone.utc),
            )
        },
        metrics=IncidentPrecheckBatchMetrics(1, 1, 1, 10.0, 10.0, 10.0, 0),
    )

    with patch("requests.post") as mock_post:
        summary = asyncio.run(
            process_customer_outage_alerts(
                temp_repo, candidates, "batch_t1", config=config, now=now, core=mock_core
            )
        )
        assert summary["sent"] == 0
        assert summary["skipped"] == 1
        assert summary["skipped_reasons"]["existing_customer_ticket"] == 1
        assert summary["classification_counts"]["CUSTOMER_OPEN_TICKET"] == 1
        mock_post.assert_not_called()

        req_id = build_customer_alert_request_id("SUB_TICKET", "2026-09-11T09:00:00")
        disp = temp_repo.get_customer_outage_alert_disposition(req_id)
        assert disp is not None
        assert disp["status"] == "SKIPPED_CUSTOMER_TICKET"
        assert disp["error_reason"] == "customer_open_ticket"


def test_process_customer_outage_alerts_precheck_indeterminate(temp_repo):
    config = {
        "enable_customer_outage_alert": True,
        "customer_alert_time_window": "07:00-21:00",
    }
    now = datetime(2026, 9, 11, 10, 0, 0)
    candidates = [
        {
            "subscriber_key": "SUB_INDET_AUTH",
            "ma_tb": "TB_AUTH",
            "first_off_time": "2026-09-11T09:00:00",
        },
        {
            "subscriber_key": "SUB_INDET_AMBIG",
            "ma_tb": "TB_AMBIG",
            "first_off_time": "2026-09-11T09:00:00",
        },
    ]

    mock_core = MagicMock()
    mock_core.lookup_open_incident_facts_batch.return_value = IncidentPrecheckBatchResult(
        decisions={
            "TB_AUTH": IncidentPrecheckDecision(
                classification=IncidentClassification.INDETERMINATE,
                failure_kind=IncidentFailureKind.AUTH,
                reason=IncidentDecisionReason.AUTH_UNAVAILABLE,
                checked_at=datetime.now(timezone.utc),
            ),
            "TB_AMBIG": IncidentPrecheckDecision(
                classification=IncidentClassification.INDETERMINATE,
                failure_kind=IncidentFailureKind.AMBIGUOUS,
                reason=IncidentDecisionReason.AMBIGUOUS_EVIDENCE,
                checked_at=datetime.now(timezone.utc),
            ),
        },
        metrics=IncidentPrecheckBatchMetrics(2, 2, 2, 20.0, 10.0, 20.0, 0),
    )

    summary = asyncio.run(
        process_customer_outage_alerts(
            temp_repo, candidates, "batch_indet", config=config, now=now, core=mock_core
        )
    )
    assert summary["sent"] == 0
    assert summary["skipped"] == 2
    assert summary["skipped_reasons"]["precheck_failed"] == 1
    assert summary["skipped_reasons"]["indeterminate"] == 1

    disp_auth = temp_repo.get_customer_outage_alert_disposition(
        build_customer_alert_request_id("SUB_INDET_AUTH", "2026-09-11T09:00:00")
    )
    assert disp_auth["status"] == "PRECHECK_FAILED"

    disp_ambig = temp_repo.get_customer_outage_alert_disposition(
        build_customer_alert_request_id("SUB_INDET_AMBIG", "2026-09-11T09:00:00")
    )
    assert disp_ambig["status"] == "INDETERMINATE"


def test_process_customer_outage_alerts_fact_aging_stale(temp_repo):
    config = {
        "enable_customer_outage_alert": True,
        "customer_alert_time_window": "07:00-21:00",
        "customer_ticket_precheck_max_fact_age_seconds": 60.0,
    }
    now = datetime(2026, 9, 11, 10, 0, 0)
    candidates = [
        {
            "subscriber_key": "SUB_STALE",
            "ma_tb": "TB_STALE",
            "first_off_time": "2026-09-11T09:00:00",
        }
    ]

    # Checked 120 seconds ago -> exceeds 60.0s max fact age
    old_checked_at = datetime.now(timezone.utc) - timedelta(seconds=120)
    mock_core = MagicMock()
    mock_core.lookup_open_incident_facts_batch.return_value = IncidentPrecheckBatchResult(
        decisions={
            "TB_STALE": IncidentPrecheckDecision(
                classification=IncidentClassification.CLEAR,
                failure_kind=IncidentFailureKind.NONE,
                reason=IncidentDecisionReason.NO_INCIDENTS_CONFIRMED,
                checked_at=old_checked_at,
            )
        },
        metrics=IncidentPrecheckBatchMetrics(1, 1, 1, 10.0, 10.0, 10.0, 0),
    )

    with patch("requests.post") as mock_post:
        summary = asyncio.run(
            process_customer_outage_alerts(
                temp_repo, candidates, "batch_stale", config=config, now=now, core=mock_core
            )
        )
        assert summary["sent"] == 0
        assert summary["skipped"] == 1
        assert summary["skipped_reasons"]["onebss_stale"] == 1
        assert summary["onebss_stale"] == 1
        mock_post.assert_not_called()

        disp = temp_repo.get_customer_outage_alert_disposition(
            build_customer_alert_request_id("SUB_STALE", "2026-09-11T09:00:00")
        )
        assert disp["status"] == "STALE"


def test_same_subscriber_two_incidents_max_per_day_reservation(temp_repo):
    config = {
        "enable_customer_outage_alert": True,
        "customer_alert_time_window": "07:00-21:00",
        "customer_alert_max_per_day": 1,
        "customer_alert_max_per_week": 3,
        "telecom_zalo_api_url": "http://localhost:3002",
        "telecom_zalo_api_key": "test_key",
    }
    now = datetime(2026, 9, 11, 10, 0, 0)
    candidates = [
        {
            "subscriber_key": "SUB_DUAL",
            "ma_tb": "TB_DUAL",
            "first_off_time": "2026-09-11T08:30:00",
        },
        {
            "subscriber_key": "SUB_DUAL",
            "ma_tb": "TB_DUAL",
            "first_off_time": "2026-09-11T09:30:00",
        },
    ]

    mock_core = MagicMock()
    mock_core.lookup_open_incident_facts_batch.return_value = IncidentPrecheckBatchResult(
        decisions={
            "TB_DUAL": IncidentPrecheckDecision(
                classification=IncidentClassification.CLEAR,
                failure_kind=IncidentFailureKind.NONE,
                reason=IncidentDecisionReason.NO_INCIDENTS_CONFIRMED,
                checked_at=datetime.now(timezone.utc),
            )
        },
        metrics=IncidentPrecheckBatchMetrics(1, 1, 1, 10.0, 10.0, 10.0, 0),
    )

    mock_resp = MagicMock()
    mock_resp.status_code = 202
    mock_resp.json.return_value = {"status": "queued"}

    with patch("requests.post", return_value=mock_resp) as mock_post:
        summary = asyncio.run(
            process_customer_outage_alerts(
                temp_repo, candidates, "batch_dual", config=config, now=now, core=mock_core
            )
        )
        assert summary["sent"] == 1
        assert summary["skipped"] == 1
        assert summary["skipped_reasons"]["daily_limit_exceeded"] == 1
        assert mock_post.call_count == 1

        disp1 = temp_repo.get_customer_outage_alert_disposition(
            build_customer_alert_request_id("SUB_DUAL", "2026-09-11T08:30:00")
        )
        assert disp1["status"] == "SENT"

        disp2 = temp_repo.get_customer_outage_alert_disposition(
            build_customer_alert_request_id("SUB_DUAL", "2026-09-11T09:30:00")
        )
        assert disp2["status"] == "FAILED"
        assert disp2["error_reason"] == "daily_limit_exceeded"


def test_multi_connection_claim_and_cas_races(temp_repo):
    now = datetime(2026, 9, 11, 10, 0, 0)
    req_id = "cust-race-1"

    # Worker 1 claims
    claim1 = temp_repo.claim_customer_outage_alert_precheck(
        subscriber_key="SUB_RACE",
        ma_tb="TB_RACE",
        first_off_time="2026-09-11T09:00:00",
        claimed_at=now,
        batch_id="b1",
        request_id=req_id,
        lease_seconds=120,
    )
    assert claim1.outcome == "CLAIMED"
    assert claim1.claimed_at == now

    # Worker 2 attempts to claim while lease is active
    claim2 = temp_repo.claim_customer_outage_alert_precheck(
        subscriber_key="SUB_RACE",
        ma_tb="TB_RACE",
        first_off_time="2026-09-11T09:00:00",
        claimed_at=now + timedelta(seconds=10),
        batch_id="b2",
        request_id=req_id,
        lease_seconds=120,
    )
    assert claim2.outcome == "LEASE_HELD"

    # Worker 1 finalizes
    fin1 = temp_repo.finalize_customer_outage_alert_precheck(
        request_id=req_id,
        batch_id="b1",
        expected_claimed_at=now,
        status="SENT",
        finalized_at=now + timedelta(seconds=5),
    )
    assert fin1 is True

    # Stale finalize from worker 2 fails compare-and-set
    fin2 = temp_repo.finalize_customer_outage_alert_precheck(
        request_id=req_id,
        batch_id="b2",
        expected_claimed_at=now,
        status="FAILED",
        finalized_at=now + timedelta(seconds=15),
    )
    assert fin2 is False

    # Once terminal (SENT), new claim attempts report TERMINAL and cannot be stolen
    claim3 = temp_repo.claim_customer_outage_alert_precheck(
        subscriber_key="SUB_RACE",
        ma_tb="TB_RACE",
        first_off_time="2026-09-11T09:00:00",
        claimed_at=now + timedelta(seconds=200),
        batch_id="b3",
        request_id=req_id,
        lease_seconds=120,
    )
    assert claim3.outcome == "TERMINAL"
    assert claim3.prior_status == "SENT"


def test_cross_midnight_retry_bypasses_first_attempt_cutoff(temp_repo):
    # Today is Sep 12, 10:00. Cutoff window is 08:00 - 16:00
    now = datetime(2026, 9, 12, 10, 0, 0)
    yesterday_fot = "2026-09-11T14:00:00"

    config = {
        "enable_customer_outage_alert": True,
        "customer_alert_time_window": "07:00-21:00",
        "customer_alert_start_time": "08:00",
        "customer_alert_end_time": "16:00",
        "telecom_zalo_api_url": "http://localhost:3002",
        "telecom_zalo_api_key": "test_key",
    }

    # Seed prior PRECHECK_FAILED for sub1 from yesterday
    req_id_retry = build_customer_alert_request_id("SUB_RETRY", yesterday_fot)
    c_seed = temp_repo.claim_customer_outage_alert_precheck(
        subscriber_key="SUB_RETRY",
        ma_tb="TB_RETRY",
        first_off_time=yesterday_fot,
        claimed_at=datetime(2026, 9, 11, 15, 0, 0),
        batch_id="b_yesterday",
        request_id=req_id_retry,
        lease_seconds=120,
    )
    temp_repo.finalize_customer_outage_alert_precheck(
        request_id=req_id_retry,
        batch_id="b_yesterday",
        expected_claimed_at=c_seed.claimed_at,
        status="PRECHECK_FAILED",
        finalized_at=datetime(2026, 9, 11, 15, 0, 0),
        error_reason="precheck_failed",
    )

    candidates = [
        # Candidate 1: Retryable yesterday row -> should bypass cutoff and be checked
        {
            "subscriber_key": "SUB_RETRY",
            "ma_tb": "TB_RETRY",
            "first_off_time": yesterday_fot,
        },
        # Candidate 2: Fresh yesterday row (no prior status) -> fails first-attempt cutoff
        {
            "subscriber_key": "SUB_FRESH_OLD",
            "ma_tb": "TB_FRESH_OLD",
            "first_off_time": yesterday_fot,
        },
    ]

    mock_core = MagicMock()
    mock_core.lookup_open_incident_facts_batch.return_value = IncidentPrecheckBatchResult(
        decisions={
            "TB_RETRY": IncidentPrecheckDecision(
                classification=IncidentClassification.CLEAR,
                failure_kind=IncidentFailureKind.NONE,
                reason=IncidentDecisionReason.NO_INCIDENTS_CONFIRMED,
                checked_at=datetime.now(timezone.utc),
            )
        },
        metrics=IncidentPrecheckBatchMetrics(1, 1, 1, 10.0, 10.0, 10.0, 0),
    )

    mock_resp = MagicMock()
    mock_resp.status_code = 202
    mock_resp.json.return_value = {"status": "queued"}

    with patch("requests.post", return_value=mock_resp):
        summary = asyncio.run(
            process_customer_outage_alerts(
                temp_repo, candidates, "batch_midnight", config=config, now=now, core=mock_core
            )
        )
        assert summary["sent"] == 1
        assert summary["skipped"] == 1
        assert summary["skipped_reasons"]["before_cutoff"] == 1
        assert summary["onebss_retried"] == 1

        disp = temp_repo.get_customer_outage_alert_disposition(req_id_retry)
        assert disp["status"] == "SENT"


def test_process_customer_outage_alerts_explicit_bypass(temp_repo):
    config = {
        "enable_customer_outage_alert": True,
        "enable_customer_ticket_precheck": False,
        "customer_alert_time_window": "07:00-21:00",
        "telecom_zalo_api_url": "http://localhost:3002",
        "telecom_zalo_api_key": "test_key",
    }
    now = datetime(2026, 9, 11, 10, 0, 0)
    candidates = [
        {
            "subscriber_key": "SUB_BYPASS",
            "ma_tb": "TB_BYPASS",
            "first_off_time": "2026-09-11T09:00:00",
        }
    ]

    mock_resp = MagicMock()
    mock_resp.status_code = 202
    mock_resp.json.return_value = {"status": "queued"}

    mock_core = MagicMock()

    with patch("requests.post", return_value=mock_resp):
        summary = asyncio.run(
            process_customer_outage_alerts(
                temp_repo, candidates, "batch_byp", config=config, now=now, core=mock_core
            )
        )
        assert summary["sent"] == 1
        assert summary["precheck_state"] == "BYPASSED"
        assert summary["onebss_checked"] == 0
        mock_core.lookup_open_incident_facts_batch.assert_not_called()
