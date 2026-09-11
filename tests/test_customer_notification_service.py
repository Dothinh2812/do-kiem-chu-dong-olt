import asyncio
import json
import sqlite3
import sys
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

try:
    from do_chu_dong_api.alert_db import AlertRepository
    from do_chu_dong_api.customer_notification_service import (
        build_customer_alert_request_id,
        check_customer_alert_eligibility,
        format_customer_outage_message,
        format_duration,
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
    temp_repo.log_customer_outage_alert(
        subscriber_key="SUB1",
        ma_tb="TB001",
        first_off_time="2026-09-11T10:00:00",
        sent_time=now,
        batch_id="batch1",
        request_id="cust-off-SUB1-20260911100000",
        status="SENT",
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
    temp_repo.log_customer_outage_alert(
        subscriber_key="SUB_LIMIT",
        ma_tb="TB_LIMIT",
        first_off_time="2026-09-11T08:00:00",
        sent_time=now - timedelta(hours=5),
        batch_id="batch1",
        request_id="req1",
        status="SENT",
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
        temp_repo.log_customer_outage_alert(
            subscriber_key="SUB_WEEK",
            ma_tb="TB_WEEK",
            first_off_time=f"2026-09-0{i}T08:00:00",
            sent_time=now - timedelta(days=i),
            batch_id=f"batch{i}",
            request_id=f"req{i}",
            status="SENT",
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

    with patch("requests.post", return_value=mock_resp):
        summary = asyncio.run(
            process_customer_outage_alerts(
                temp_repo, candidates, "batch_100", config=config, now=now
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
