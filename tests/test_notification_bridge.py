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
    from do_chu_dong_api.alert_models import OutageAlert, RecoveryAlert, WideAreaAlert
    from do_chu_dong_api.notification_bridge import dispatch_batch_notifications
except ModuleNotFoundError:
    from alert_db import AlertRepository
    from alert_models import OutageAlert, RecoveryAlert, WideAreaAlert
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

    try:
        from do_chu_dong_api import notification_bridge
    except ModuleNotFoundError:
        import notification_bridge

    monkeypatch.setattr(
        notification_bridge,
        "load_current_off_snapshot_rows",
        lambda repo, batch_id: [
            {
                "batch_id": batch_id,
                "subscriber_key": "HNI.BVI.BVI.OLT.AL.2.1_1-1-1:1",
                "port_id": "HNI.BVI.BVI.OLT.AL.2.1_1-1-1:1",
                "ma_tb": "TB001",
                "ten_tb": "Ten TB",
                "olt_name": "HNI.BVI.BVI.OLT.AL.2.1",
                "doi_vt": "Tổ Kỹ thuật Địa bàn Quảng Oai",
                "ten_nvkt_db": "VNPT - Nguyen Van A",
                "dienthoai_lh": "0900000000",
                "diachi_ld": "Dia chi",
                "first_off_time": "2026-04-24T12:10:00",
                "duration_minutes": 20,
                "suppressed_by_pattern": False,
                "suppressed_by_wide_area": False,
            }
        ],
    )

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
        ).fetchall()
    assert sent == []
    assert any("wide-area pending=0" in message for message in messages)
    assert any("individual outage pending_raw=1 pending_after_filters=1" in message for message in messages)


def test_dispatch_batch_notifications_writes_jsonl_delivery_log(repo_paths, monkeypatch, tmp_path):
    repo, _measurement_db = repo_paths

    try:
        from do_chu_dong_api import notification_bridge
    except ModuleNotFoundError:
        import notification_bridge

    monkeypatch.setattr(
        notification_bridge,
        "load_current_off_snapshot_rows",
        lambda repo, batch_id: [
            {
                "batch_id": batch_id,
                "subscriber_key": "HNI.BVI.BVI.OLT.AL.2.1_1-1-1:1",
                "port_id": "HNI.BVI.BVI.OLT.AL.2.1_1-1-1:1",
                "ma_tb": "TB001",
                "ten_tb": "Ten TB",
                "olt_name": "HNI.BVI.BVI.OLT.AL.2.1",
                "doi_vt": "Tổ Kỹ thuật Địa bàn Quảng Oai",
                "ten_nvkt_db": "VNPT - Nguyen Van A",
                "dienthoai_lh": "0900000000",
                "diachi_ld": "Dia chi",
                "first_off_time": "2026-04-24T12:10:00",
                "duration_minutes": 20,
                "suppressed_by_pattern": False,
                "suppressed_by_wide_area": False,
            }
        ],
    )

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
    assert payload["alert_ids"] == []
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


def test_dispatch_batch_notifications_temporarily_skips_recovery_notifications(repo_paths, monkeypatch):
    repo, measurement_db = repo_paths

    repo.insert_recovery_alert(
        RecoveryAlert(
            subscriber_key="HNI.BVI.BVI.OLT.AL.2.1_1-1-1:1",
            parent_port_key="HNI.BVI.BVI.OLT.AL.2.1_1-1-1",
            batch_id="b1",
            ma_tb="TB001",
            ten_tb="Ten TB",
            olt_name="HNI.BVI.BVI.OLT.AL.2.1",
            doi_vt="Tổ Kỹ thuật Địa bàn Quảng Oai",
            diachi_ld="Dia chi",
            dienthoai_lh="0900000000",
            ten_nvkt_db="VNPT - Nguyen Van A",
            outage_time=datetime(2026, 4, 24, 12, 10, 0),
            recovery_time=datetime(2026, 4, 24, 12, 30, 0),
            outage_duration_minutes=20,
        )
    )

    try:
        from do_chu_dong_api import notification_bridge
    except ModuleNotFoundError:
        import notification_bridge

    async def fail_if_called(*args, **kwargs):
        raise AssertionError("recovery sender should not be called")

    monkeypatch.setattr(
        notification_bridge.notification_service,
        "send_telegram_message",
        fail_if_called,
    )
    monkeypatch.setattr(
        notification_bridge.notification_service,
        "send_recovery_alerts_by_doi_vt",
        fail_if_called,
    )
    monkeypatch.setattr(
        notification_bridge.notification_service,
        "load_config",
        lambda: {
            "enable_telegram": False,
            "enable_zalo": False,
            "enable_recovery_alert_notifications": False,
        },
    )

    messages = []
    result = dispatch_batch_notifications(repo, "b1", log=messages.append)

    assert result["recovery"]["pending_alerts"] == 1
    assert result["recovery"]["marked_sent_alerts"] == 0
    assert result["recovery"]["telegram_sent"] is False
    assert result["recovery"]["zalo_messages_sent"] == 0
    assert any("recovery notifications disabled by config" in message for message in messages)

    with sqlite3.connect(measurement_db) as conn:
        sent = conn.execute(
            "SELECT notification_sent FROM recovery_alerts WHERE batch_id = 'b1'"
        ).fetchone()[0]
    assert sent == 0


def test_dispatch_batch_notifications_sends_current_off_snapshot_every_batch(repo_paths, monkeypatch):
    repo, _measurement_db = repo_paths

    try:
        from do_chu_dong_api import notification_bridge
    except ModuleNotFoundError:
        import notification_bridge

    snapshot_rows = [
        {
            "batch_id": "b2",
            "ma_tb": "TB001",
            "ten_tb": "Ten TB",
            "doi_vt": "Tổ Kỹ thuật Địa bàn Quảng Oai",
            "ten_nvkt_db": "VNPT - Nguyen Van A",
            "dienthoai_lh": "0912345678",
            "diachi_ld": "Dia chi",
            "port_id": "HNI.BVI.BVI.OLT.AL.2.1_1-1-1:1",
            "first_off_time": "2026-04-24T12:05:00",
            "duration_minutes": 25,
            "suppressed_by_pattern": False,
            "suppressed_by_wide_area": False,
        }
    ]

    monkeypatch.setattr(notification_bridge, "load_current_off_snapshot_rows", lambda repo, batch_id: snapshot_rows)
    monkeypatch.setattr(
        notification_bridge.notification_service,
        "load_config",
        lambda: {"enable_telegram": False, "enable_zalo": False},
    )

    result = dispatch_batch_notifications(repo, "b2", log=lambda _message: None)

    assert result["outage"]["raw_pending_alerts"] == 1
    assert result["outage"]["filtered_pending_alerts"] == 1
    assert result["outage"]["filtered_out_alerts"] == 0
    assert result["outage"]["marked_sent_alerts"] == 1


def test_dispatch_batch_notifications_skips_individual_alerts_when_disabled(repo_paths, monkeypatch):
    repo, _measurement_db = repo_paths

    try:
        from do_chu_dong_api import notification_bridge
    except ModuleNotFoundError:
        import notification_bridge

    snapshot_rows = [
        {
            "batch_id": "b2",
            "ma_tb": "TB001",
            "ten_tb": "Ten TB",
            "doi_vt": "Tổ Kỹ thuật Địa bàn Quảng Oai",
            "ten_nvkt_db": "VNPT - Nguyen Van A",
            "dienthoai_lh": "0912345678",
            "diachi_ld": "Dia chi",
            "port_id": "HNI.BVI.BVI.OLT.AL.2.1_1-1-1:1",
            "first_off_time": "2026-04-24T12:05:00",
            "duration_minutes": 25,
            "suppressed_by_pattern": False,
            "suppressed_by_wide_area": False,
        }
    ]

    monkeypatch.setattr(notification_bridge, "load_current_off_snapshot_rows", lambda repo, batch_id: snapshot_rows)
    monkeypatch.setattr(
        notification_bridge.notification_service,
        "load_config",
        lambda: {
            "enable_telegram": False,
            "enable_zalo": False,
            "enable_individual_alert_notifications": False,
        },
    )

    messages = []
    result = dispatch_batch_notifications(repo, "b2", log=messages.append)

    assert result["outage"]["raw_pending_alerts"] == 1
    assert result["outage"]["filtered_pending_alerts"] == 0
    assert result["outage"]["filtered_out_alerts"] == 1
    assert result["outage"]["marked_sent_alerts"] == 0
    assert any("individual outage notifications disabled by config" in message for message in messages)


def test_dispatch_batch_notifications_skips_wide_area_alerts_outside_time_window(repo_paths, monkeypatch):
    repo, _measurement_db = repo_paths

    repo.insert_wide_area_alert(
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

    monkeypatch.setattr(
        notification_bridge.notification_service,
        "load_config",
        lambda: {
            "enable_telegram": False,
            "enable_zalo": False,
            "enable_wide_area_alert_notifications": True,
            "wide_area_alert_time_window": "06:00-18:00",
        },
    )

    original_policy_status = notification_bridge.notification_service.get_alert_policy_status
    monkeypatch.setattr(
        notification_bridge.notification_service,
        "get_alert_policy_status",
        lambda alert_type, config=None, now=None: original_policy_status(
            alert_type,
            config=config,
            now=datetime(2026, 4, 25, 22, 0, 0),
        ),
    )

    messages = []
    result = dispatch_batch_notifications(repo, "b1", log=messages.append)

    assert result["wide_area"]["pending_alerts"] == 1
    assert result["wide_area"]["marked_sent_alerts"] == 0
    assert any("wide-area notifications outside allowed window 06:00-18:00" in message for message in messages)


def test_dispatch_batch_notifications_skips_excluded_wide_area_ports_but_keeps_db_record(repo_paths, monkeypatch):
    repo, measurement_db = repo_paths

    inserted_id = repo.insert_wide_area_alert(
        WideAreaAlert(
            batch_id="b1",
            parent_port_key="HNI.STY.G22.OLT_0-1-13",
            olt_name="HNI.STY.G22.OLT",
            port="0-1-13",
            subscriber_count=12,
            subscriber_keys=["HNI.STY.G22.OLT_0-1-13:1"],
            subscriber_list=[{"ma_tb": "TB001", "ten_tb": "Ten TB", "ten_nvkt_db": "VNPT - Nguyen Van A"}],
            doi_vt="Tổ Kỹ thuật Địa bàn Sơn Tây",
            alert_time=datetime(2026, 4, 24, 12, 30, 0),
        )
    )

    try:
        from do_chu_dong_api import notification_bridge
    except ModuleNotFoundError:
        import notification_bridge

    async def fail_if_called(*args, **kwargs):
        raise AssertionError("wide-area sender should not be called for excluded ports")

    monkeypatch.setattr(
        notification_bridge.notification_service,
        "send_telegram_message",
        fail_if_called,
    )
    monkeypatch.setattr(
        notification_bridge.notification_service,
        "send_zalo_message_to_thread_detailed",
        fail_if_called,
    )
    monkeypatch.setattr(
        notification_bridge.notification_service,
        "get_olt_display_name",
        lambda olt_name: "STY.G22" if olt_name == "HNI.STY.G22.OLT" else olt_name,
    )
    monkeypatch.setattr(
        notification_bridge.notification_service,
        "load_config",
        lambda: {
            "enable_telegram": True,
            "enable_zalo": True,
            "enable_wide_area_alert_notifications": True,
            "wide_area_alert_excluded_ports": "OLT: STY.G22, Port: 0-1-13",
        },
    )

    messages = []
    result = dispatch_batch_notifications(repo, "b1", log=messages.append)

    assert result["wide_area"]["pending_alerts"] == 1
    assert result["wide_area"]["excluded_by_config"] == 1
    assert result["wide_area"]["marked_sent_alerts"] == 1
    assert any("wide-area excluded_by_config=1 ports=STY.G22:0-1-13" in message for message in messages)

    with sqlite3.connect(measurement_db) as conn:
        row = conn.execute(
            "SELECT id, notification_sent FROM wide_area_alerts WHERE id = ?",
            (inserted_id,),
        ).fetchone()

    assert row[0] == inserted_id
    assert row[1] == 1


def test_dispatch_batch_notifications_does_not_mark_outage_events_sent_for_recurring_snapshot(repo_paths, monkeypatch):
    repo, measurement_db = repo_paths

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

    monkeypatch.setattr(notification_bridge, "load_current_off_snapshot_rows", lambda repo, batch_id: [])
    monkeypatch.setattr(
        notification_bridge.notification_service,
        "load_config",
        lambda: {"enable_telegram": False, "enable_zalo": False},
    )

    dispatch_batch_notifications(repo, "b1", log=lambda _message: None)

    with sqlite3.connect(measurement_db) as conn:
        sent = conn.execute("SELECT notification_sent FROM outage_alerts WHERE id = ?", (inserted_id,)).fetchone()[0]
    assert sent == 0
