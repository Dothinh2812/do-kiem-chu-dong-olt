import sqlite3
import sys
import json
from datetime import datetime
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

try:
    from do_chu_dong_api.alert_db import AlertRepository
    from do_chu_dong_api.alert_models import OutageAlert, WideAreaAlert
    from do_chu_dong_api.notification_bridge import dispatch_batch_notifications
except ModuleNotFoundError:
    from alert_db import AlertRepository
    from alert_models import OutageAlert, WideAreaAlert
    from notification_bridge import dispatch_batch_notifications


RAW_SCHEMA = """
CREATE TABLE IF NOT EXISTS onu_measurements (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    "Cổng" TEXT NOT NULL,
    batch_id TEXT NOT NULL,
    onuLastOff TEXT,
    onuLastOn TEXT,
    oltPowerRx REAL,
    onuPowerRx REAL,
    slid TEXT,
    onuSN TEXT,
    softVersion TEXT,
    onuStatusStr TEXT,
    frameNo INTEGER,
    slotNo INTEGER,
    portNo INTEGER,
    onuIndex INTEGER,
    accountFiber TEXT,
    NgayDo TEXT NOT NULL,
    ThoiGianDo TEXT NOT NULL
)
"""


@pytest.fixture
def repo_paths(tmp_path):
    measurement_db = tmp_path / "onu_measurements.db"
    source_db = tmp_path / "database.db"

    with sqlite3.connect(measurement_db) as conn:
        conn.execute(RAW_SCHEMA)
        conn.commit()

    with sqlite3.connect(source_db) as conn:
        conn.execute(
            """
            CREATE TABLE danhba (
                sub TEXT,
                Ma_Tb TEXT,
                Ma_Men TEXT,
                Ten_Tb TEXT,
                DIACHI_LD TEXT,
                DIENTHOAI_LH TEXT,
                TEN_NVKT_DB TEXT,
                DOI_VT TEXT
            )
            """
        )
        conn.commit()

    repo = AlertRepository(str(measurement_db), str(source_db))
    repo.ensure_schema()
    repo.mark_batch_started("b1", datetime(2026, 4, 24, 12, 30, 0), 1)
    repo.mark_batch_completed("b1", datetime(2026, 4, 24, 12, 30, 5), 1, 1, 0, 0, 0)
    return repo, measurement_db


def test_dispatch_batch_notifications_returns_detailed_breakdown_and_logs(repo_paths, monkeypatch):
    repo, measurement_db = repo_paths

    repo.insert_outage_alert(
        OutageAlert(
            subscriber_key="HNI.BVI.BVI.OLT.AL.2.1_1-1-1:1",
            parent_port_key="HNI.BVI.BVI.OLT.AL.2.1_1-1-1",
            batch_id="b1",
            ma_tb="TB001",
            ten_tb="Ten TB",
            olt_name="HNI.BVI.BVI.OLT.AL.2.1",
            doi_vt="Tổ Kỹ thuật Địa bàn Quảng Oai",
            ten_nvkt_db="VNPT - Nguyen Van A",
            first_off_time=datetime(2026, 4, 24, 12, 10, 0),
            alert_time=datetime(2026, 4, 24, 12, 30, 0),
            off_duration_minutes=20,
        )
    )

    try:
        from do_chu_dong_api import notification_bridge
    except ModuleNotFoundError:
        import notification_bridge

    monkeypatch.setattr(
        notification_bridge.notification_service,
        "load_config",
        lambda: {"enable_telegram": False, "enable_zalo": False},
    )

    messages = []
    result = dispatch_batch_notifications(repo, "b1", log=messages.append)

    assert result["wide_area"]["pending_alerts"] == 0
    assert result["outage"]["raw_pending_alerts"] == 1
    assert result["outage"]["filtered_pending_alerts"] == 1
    assert result["outage"]["marked_sent_alerts"] == 1
    assert result["recovery"]["pending_alerts"] == 0

    with sqlite3.connect(measurement_db) as conn:
        sent = conn.execute(
            "SELECT notification_sent FROM outage_alerts WHERE batch_id = 'b1'"
        ).fetchone()[0]
    assert sent == 1
    assert any("wide-area pending=0" in message for message in messages)
    assert any("individual outage pending_raw=1 pending_after_filters=1" in message for message in messages)


def test_dispatch_batch_notifications_writes_jsonl_delivery_log(repo_paths, monkeypatch, tmp_path):
    repo, _measurement_db = repo_paths

    inserted_id = repo.insert_outage_alert(
        OutageAlert(
            subscriber_key="HNI.BVI.BVI.OLT.AL.2.1_1-1-1:1",
            parent_port_key="HNI.BVI.BVI.OLT.AL.2.1_1-1-1",
            batch_id="b1",
            ma_tb="TB001",
            ten_tb="Ten TB",
            olt_name="HNI.BVI.BVI.OLT.AL.2.1",
            doi_vt="Tổ Kỹ thuật Địa bàn Quảng Oai",
            ten_nvkt_db="VNPT - Nguyen Van A",
            first_off_time=datetime(2026, 4, 24, 12, 10, 0),
            alert_time=datetime(2026, 4, 24, 12, 30, 0),
            off_duration_minutes=20,
        )
    )

    try:
        from do_chu_dong_api import notification_bridge
    except ModuleNotFoundError:
        import notification_bridge

    log_file = tmp_path / "log_message" / "notification_delivery.jsonl"
    original_append = notification_bridge.notification_service.append_notification_delivery_log
    monkeypatch.setattr(
        notification_bridge.notification_service,
        "append_notification_delivery_log",
        lambda entry: original_append(entry, log_file=str(log_file)),
    )
    monkeypatch.setattr(
        notification_bridge.notification_service,
        "load_config",
        lambda: {
            "enable_telegram": True,
            "enable_zalo": False,
            "telegram_bot_token": "test-bot",
            "telegram_chat_id": "test-chat",
        },
    )

    async def fake_send_telegram_message(message, bot_token=None, chat_id=None, parse_mode="Markdown"):
        assert "TB001" in message
        return True

    monkeypatch.setattr(
        notification_bridge.notification_service,
        "send_telegram_message",
        fake_send_telegram_message,
    )

    result = dispatch_batch_notifications(repo, "b1", log=lambda _message: None)

    assert result["outage"]["marked_sent_alerts"] == 1
    assert log_file.exists()

    payloads = [json.loads(line) for line in log_file.read_text(encoding="utf-8").splitlines()]
    assert len(payloads) == 1
    payload = payloads[0]
    assert payload["batch_id"] == "b1"
    assert payload["channel"] == "telegram"
    assert payload["alert_type"] == "outage"
    assert payload["status"] == "SUCCESS"
    assert payload["target_id"] == "test-chat"
    assert payload["alert_ids"] == [inserted_id]
    assert payload["alert_count"] == 1
    assert "TB001" in payload["message_full"]
    assert "timestamp" in payload


def test_dispatch_batch_notifications_logs_zalo_error_details_for_wide_area(repo_paths, monkeypatch, tmp_path):
    repo, _measurement_db = repo_paths

    inserted_id = repo.insert_wide_area_alert(
        WideAreaAlert(
            batch_id="b1",
            parent_port_key="HNI.BVI.BVI.OLT.AL.2.1_1-1-2",
            olt_name="HNI.BVI.BVI.OLT.AL.2.1",
            port="1-1-2",
            subscriber_count=8,
            subscriber_keys=["HNI.BVI.BVI.OLT.AL.2.1_1-1-2:1"],
            subscriber_list=[{"ma_tb": "TB001", "ten_tb": "Ten TB", "ten_nvkt_db": "VNPT - Nguyen Van A"}],
            doi_vt="Tổ Kỹ thuật Địa bàn Quảng Oai",
            alert_time=datetime(2026, 4, 24, 12, 30, 0),
        )
    )

    try:
        from do_chu_dong_api import notification_bridge
    except ModuleNotFoundError:
        import notification_bridge

    log_file = tmp_path / "log_message" / "notification_delivery.jsonl"
    original_append = notification_bridge.notification_service.append_notification_delivery_log
    monkeypatch.setattr(
        notification_bridge.notification_service,
        "append_notification_delivery_log",
        lambda entry: original_append(entry, log_file=str(log_file)),
    )
    monkeypatch.setattr(
        notification_bridge.notification_service,
        "load_config",
        lambda: {"enable_telegram": False, "enable_zalo": True},
    )

    async def fake_send_zalo_message_to_thread_detailed(message, thread_id, client=None):
        return {
            "success": False,
            "thread_id": thread_id,
            "message": message,
            "error": "group not found",
            "stdout": "stdout details",
            "stderr": "stderr details",
            "returncode": 17,
            "command": ["/node", "/openzca", "--profile", "zalo2", "msg", "send", thread_id],
        }

    monkeypatch.setattr(
        notification_bridge.notification_service,
        "send_zalo_message_to_thread_detailed",
        fake_send_zalo_message_to_thread_detailed,
    )

    result = dispatch_batch_notifications(repo, "b1", log=lambda _message: None)

    assert result["wide_area"]["zalo_messages_failed"] == 1
    payloads = [json.loads(line) for line in log_file.read_text(encoding="utf-8").splitlines()]
    assert len(payloads) == 1
    payload = payloads[0]
    assert payload["channel"] == "zalo"
    assert payload["alert_type"] == "wide_area"
    assert payload["status"] == "FAILED"
    assert payload["target_id"] == "7968537750365285360"
    assert payload["alert_ids"] == [inserted_id]
    assert payload["error"] == "group not found"
    assert payload["stdout"] == "stdout details"
    assert payload["stderr"] == "stderr details"
    assert payload["returncode"] == 17
    assert payload["command"] == ["/node", "/openzca", "--profile", "zalo2", "msg", "send", "7968537750365285360"]
