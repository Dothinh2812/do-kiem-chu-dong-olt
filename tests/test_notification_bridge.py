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


def test_alert_repository_ensure_schema_adds_wide_area_timing_columns(tmp_path):
    measurement_db = tmp_path / "onu_measurements.db"
    source_db = tmp_path / "database.db"

    with sqlite3.connect(measurement_db) as conn:
        conn.execute(RAW_SCHEMA)
        conn.execute(
            """
            CREATE TABLE wide_area_alerts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                batch_id TEXT NOT NULL,
                parent_port_key TEXT NOT NULL,
                olt_name TEXT NOT NULL,
                port TEXT NOT NULL,
                subscriber_count INTEGER NOT NULL,
                subscriber_keys_json TEXT NOT NULL,
                subscriber_summary_json TEXT NOT NULL,
                doi_vt TEXT,
                alert_time DATETIME NOT NULL,
                notification_sent BOOLEAN DEFAULT FALSE,
                notification_time DATETIME
            )
            """
        )
        conn.commit()

    with sqlite3.connect(source_db) as conn:
        conn.execute("CREATE TABLE danhba (sub TEXT)")
        conn.commit()

    repo = AlertRepository(str(measurement_db), str(source_db))
    repo.ensure_schema()

    with sqlite3.connect(measurement_db) as conn:
        columns = {
            row[1]: row[2]
            for row in conn.execute("PRAGMA table_info(wide_area_alerts)").fetchall()
        }

    assert "first_off_time" in columns
    assert "off_duration_minutes" in columns


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
    assert any("group outage pending_raw=1 pending_after_filters=1" in message for message in messages)


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


def test_dispatch_batch_notifications_wide_area_message_includes_start_time_and_duration(repo_paths, monkeypatch):
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
            first_off_time=datetime(2026, 4, 24, 12, 10, 0),
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
        lambda: {"enable_telegram": False, "enable_zalo": True},
    )

    sent_messages = []

    async def fake_send_zalo_message_to_thread_detailed(message, thread_id, client=None):
        sent_messages.append((thread_id, message))
        return {
            "success": True,
            "thread_id": thread_id,
            "message": message,
            "response": "ok",
            "error": "",
            "stdout": "ok",
            "stderr": "",
            "returncode": 0,
            "command": [],
        }

    monkeypatch.setattr(
        notification_bridge.notification_service,
        "send_zalo_message_to_thread_detailed",
        fake_send_zalo_message_to_thread_detailed,
    )

    result = dispatch_batch_notifications(repo, "b1", log=lambda _msg: None)

    assert result["wide_area"]["zalo_messages_sent"] == 1
    assert result["wide_area"]["marked_sent_alerts"] == 1
    assert len(sent_messages) == 1
    assert "Bắt đầu: 24/04/2026 12:10" in sent_messages[0][1]
    assert "Kéo dài: 20 phút" in sent_messages[0][1]


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


def test_dispatch_batch_notifications_sends_individual_alerts_only_on_configured_cycle(repo_paths, monkeypatch):
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
            "individual_alert_send_every_batches": 3,
        },
    )

    first_result = dispatch_batch_notifications(repo, "b2", log=lambda _message: None)
    second_result = dispatch_batch_notifications(repo, "b3", log=lambda _message: None)
    third_result = dispatch_batch_notifications(repo, "b4", log=lambda _message: None)

    assert first_result["outage"]["raw_pending_alerts"] == 1
    assert first_result["outage"]["filtered_pending_alerts"] == 0
    assert first_result["outage"]["filtered_out_alerts"] == 1
    assert first_result["outage"]["marked_sent_alerts"] == 0

    assert second_result["outage"]["raw_pending_alerts"] == 1
    assert second_result["outage"]["filtered_pending_alerts"] == 0
    assert second_result["outage"]["filtered_out_alerts"] == 1
    assert second_result["outage"]["marked_sent_alerts"] == 0

    assert third_result["outage"]["raw_pending_alerts"] == 1
    assert third_result["outage"]["filtered_pending_alerts"] == 1
    assert third_result["outage"]["filtered_out_alerts"] == 0
    assert third_result["outage"]["marked_sent_alerts"] == 1


def test_dispatch_batch_notifications_counts_individual_cycle_globally_across_empty_batches(repo_paths, monkeypatch):
    repo, _measurement_db = repo_paths

    try:
        from do_chu_dong_api import notification_bridge
    except ModuleNotFoundError:
        import notification_bridge

    snapshot_rows_by_batch = {
        "b2": [],
        "b3": [
            {
                "batch_id": "b3",
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
        ],
    }

    monkeypatch.setattr(
        notification_bridge,
        "load_current_off_snapshot_rows",
        lambda repo, batch_id: snapshot_rows_by_batch.get(batch_id, []),
    )
    monkeypatch.setattr(
        notification_bridge.notification_service,
        "load_config",
        lambda: {
            "enable_telegram": False,
            "enable_zalo": False,
            "individual_alert_send_every_batches": 2,
        },
    )

    empty_batch_result = dispatch_batch_notifications(repo, "b2", log=lambda _message: None)
    next_batch_result = dispatch_batch_notifications(repo, "b3", log=lambda _message: None)

    assert empty_batch_result["outage"]["raw_pending_alerts"] == 0
    assert empty_batch_result["outage"]["filtered_pending_alerts"] == 0
    assert empty_batch_result["outage"]["marked_sent_alerts"] == 0

    assert next_batch_result["outage"]["raw_pending_alerts"] == 1
    assert next_batch_result["outage"]["filtered_pending_alerts"] == 1
    assert next_batch_result["outage"]["filtered_out_alerts"] == 0
    assert next_batch_result["outage"]["marked_sent_alerts"] == 1


def test_dispatch_batch_notifications_skips_personal_alerts_when_disabled(repo_paths, monkeypatch):
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
    assert result["outage"]["filtered_pending_alerts"] == 1
    assert result["outage"]["filtered_out_alerts"] == 0
    assert result["outage"]["marked_sent_alerts"] == 1
    assert result["personal_outage"]["raw_pending_alerts"] == 1
    assert result["personal_outage"]["filtered_pending_alerts"] == 0
    assert result["personal_outage"]["filtered_out_alerts"] == 1
    assert result["personal_outage"]["marked_sent_alerts"] == 0
    assert any("individual outage notifications disabled by config" in message for message in messages)


def test_dispatch_batch_notifications_keeps_group_alerts_when_personal_disabled(repo_paths, monkeypatch):
    repo, _measurement_db = repo_paths

    try:
        from do_chu_dong_api import notification_bridge
    except ModuleNotFoundError:
        import notification_bridge

    snapshot_rows = [
        {
            "batch_id": "b2",
            "subscriber_key": "sub-1",
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

    result = dispatch_batch_notifications(repo, "b2", log=lambda _message: None)

    assert result["outage"]["filtered_pending_alerts"] == 1
    assert result["outage"]["marked_sent_alerts"] == 1
    assert result["personal_outage"]["filtered_pending_alerts"] == 0
    assert result["personal_outage"]["marked_sent_alerts"] == 0


def test_dispatch_batch_notifications_keeps_personal_alerts_when_group_disabled(repo_paths, monkeypatch):
    repo, _measurement_db = repo_paths

    try:
        from do_chu_dong_api import notification_bridge
    except ModuleNotFoundError:
        import notification_bridge

    snapshot_rows = [
        {
            "batch_id": "b2",
            "subscriber_key": "sub-1",
            "ma_tb": "TB001",
            "ten_tb": "Ten TB",
            "doi_vt": "Tổ Kỹ thuật Địa bàn Quảng Oai",
            "ten_nvkt_db": "VNPT - Nguyen Van A",
            "dienthoai_lh": "0912345678",
            "diachi_ld": "Dia chi",
            "port_id": "HNI.BVI.BVI.OLT.AL.2.1_1-1-1:1",
            "first_off_time": "2026-05-14T06:05:00",
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
            "enable_zalo": True,
            "enable_group_alert_notifications": False,
            "enable_individual_alert_notifications": True,
            "group_alert_send_every_batches": 1,
            "individual_alert_send_every_batches": 1,
            "group_alert_start_time": "06:00",
            "individual_alert_start_time": "06:00",
        },
    )
    monkeypatch.setattr(
        notification_bridge.notification_service,
        "get_zalo_user_by_nvkt",
        lambda nvkt, config=None: "user-a",
    )

    async def fake_send_zalo_message_to_user_detailed(message, user_id, client=None):
        return {
            "success": True,
            "thread_id": user_id,
            "message": message,
            "stdout": "ok",
            "stderr": "",
            "returncode": 0,
            "command": [],
        }

    async def fail_group_sender(alerts):
        raise AssertionError("group sender should not be called when group alerts are disabled")

    monkeypatch.setattr(
        notification_bridge.notification_service,
        "send_zalo_message_to_user_detailed",
        fake_send_zalo_message_to_user_detailed,
    )
    monkeypatch.setattr(
        notification_bridge.notification_service,
        "send_current_off_snapshot_by_doi_vt",
        fail_group_sender,
    )

    result = dispatch_batch_notifications(repo, "b2", log=lambda _message: None)

    assert result["outage"]["filtered_pending_alerts"] == 0
    assert result["outage"]["marked_sent_alerts"] == 0
    assert result["personal_outage"]["filtered_pending_alerts"] == 1
    assert result["personal_outage"]["marked_sent_alerts"] == 1


def test_dispatch_batch_notifications_uses_independent_group_and_personal_cycles(repo_paths, monkeypatch):
    repo, _measurement_db = repo_paths

    try:
        from do_chu_dong_api import notification_bridge
    except ModuleNotFoundError:
        import notification_bridge

    snapshot_rows = [
        {
            "batch_id": "b2",
            "subscriber_key": "sub-1",
            "ma_tb": "TB001",
            "ten_tb": "Ten TB",
            "doi_vt": "Tổ Kỹ thuật Địa bàn Quảng Oai",
            "ten_nvkt_db": "VNPT - Nguyen Van A",
            "dienthoai_lh": "0912345678",
            "diachi_ld": "Dia chi",
            "port_id": "HNI.BVI.BVI.OLT.AL.2.1_1-1-1:1",
            "first_off_time": "2026-05-14T06:05:00",
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
            "enable_group_alert_notifications": True,
            "enable_individual_alert_notifications": True,
            "group_alert_send_every_batches": 2,
            "individual_alert_send_every_batches": 3,
            "group_alert_start_time": "06:00",
            "individual_alert_start_time": "06:00",
        },
    )

    first_result = dispatch_batch_notifications(repo, "b2", log=lambda _message: None)
    second_result = dispatch_batch_notifications(repo, "b3", log=lambda _message: None)
    third_result = dispatch_batch_notifications(repo, "b4", log=lambda _message: None)

    assert first_result["outage"]["filtered_pending_alerts"] == 0
    assert first_result["personal_outage"]["filtered_pending_alerts"] == 0
    assert second_result["outage"]["filtered_pending_alerts"] == 1
    assert second_result["personal_outage"]["filtered_pending_alerts"] == 0
    assert third_result["outage"]["filtered_pending_alerts"] == 0
    assert third_result["personal_outage"]["filtered_pending_alerts"] == 1


def test_dispatch_batch_notifications_sends_personal_alerts_once_per_day(repo_paths, monkeypatch):
    repo, _measurement_db = repo_paths

    try:
        from do_chu_dong_api import notification_bridge
    except ModuleNotFoundError:
        import notification_bridge

    snapshot_rows_by_batch = {
        "b2": [
            {
                "batch_id": "b2",
                "subscriber_key": "sub-1",
                "ma_tb": "TB001",
                "ten_tb": "Ten TB",
                "doi_vt": "Tổ Kỹ thuật Địa bàn Quảng Oai",
                "ten_nvkt_db": "VNPT - Nguyen Van A",
                "dienthoai_lh": "0912345678",
                "diachi_ld": "Dia chi",
                "port_id": "HNI.BVI.BVI.OLT.AL.2.1_1-1-1:1",
                "first_off_time": "2026-04-24T06:05:00",
                "duration_minutes": 25,
                "suppressed_by_pattern": False,
                "suppressed_by_wide_area": False,
            },
            {
                "batch_id": "b2",
                "subscriber_key": "sub-old",
                "ma_tb": "OLD",
                "ten_tb": "Old TB",
                "doi_vt": "Tổ Kỹ thuật Địa bàn Quảng Oai",
                "ten_nvkt_db": "VNPT - Nguyen Van A",
                "dienthoai_lh": "0912345678",
                "diachi_ld": "Dia chi",
                "port_id": "HNI.BVI.BVI.OLT.AL.2.1_1-1-1:2",
                "first_off_time": "2026-04-24T05:59:00",
                "duration_minutes": 30,
                "suppressed_by_pattern": False,
                "suppressed_by_wide_area": False,
            },
        ],
        "b3": [
            {
                "batch_id": "b3",
                "subscriber_key": "sub-1",
                "ma_tb": "TB001",
                "ten_tb": "Ten TB",
                "doi_vt": "Tổ Kỹ thuật Địa bàn Quảng Oai",
                "ten_nvkt_db": "VNPT - Nguyen Van A",
                "dienthoai_lh": "0912345678",
                "diachi_ld": "Dia chi",
                "port_id": "HNI.BVI.BVI.OLT.AL.2.1_1-1-1:1",
                "first_off_time": "2026-04-24T06:05:00",
                "duration_minutes": 35,
                "suppressed_by_pattern": False,
                "suppressed_by_wide_area": False,
            },
            {
                "batch_id": "b3",
                "subscriber_key": "sub-2",
                "ma_tb": "TB002",
                "ten_tb": "Ten TB 2",
                "doi_vt": "Tổ Kỹ thuật Địa bàn Quảng Oai",
                "ten_nvkt_db": "VNPT - Nguyen Van A",
                "dienthoai_lh": "0987654321",
                "diachi_ld": "Dia chi 2",
                "port_id": "HNI.BVI.BVI.OLT.AL.2.1_1-1-1:3",
                "first_off_time": "2026-04-24T06:25:00",
                "duration_minutes": 15,
                "suppressed_by_pattern": False,
                "suppressed_by_wide_area": False,
            },
        ],
    }

    monkeypatch.setattr(
        notification_bridge,
        "load_current_off_snapshot_rows",
        lambda repo, batch_id: snapshot_rows_by_batch.get(batch_id, []),
    )
    monkeypatch.setattr(
        notification_bridge.notification_service,
        "load_config",
        lambda: {
            "enable_telegram": False,
            "enable_zalo": True,
            "enable_individual_alert_notifications": True,
            "current_off_alert_start_time": "06:00",
            "individual_alert_time_window": "06:00-21:00",
            "individual_alert_send_every_batches": 1,
        },
    )
    monkeypatch.setattr(
        notification_bridge.notification_service,
        "get_zalo_user_by_nvkt",
        lambda nvkt, config=None: "user-a" if nvkt in {"VNPT - Nguyen Van A", "Nguyen Van A"} else None,
    )
    async def fake_send_current_off_snapshot_by_doi_vt(alerts):
        return {"sent": 1, "failed": 0, "no_thread": 0, "deliveries": []}

    monkeypatch.setattr(
        notification_bridge.notification_service,
        "send_current_off_snapshot_by_doi_vt",
        fake_send_current_off_snapshot_by_doi_vt,
    )
    original_filter = notification_bridge.notification_service.filter_current_off_alerts_by_cutoff
    monkeypatch.setattr(
        notification_bridge.notification_service,
        "filter_current_off_alerts_by_cutoff",
        lambda alerts, config=None, now=None, **kwargs: original_filter(
            alerts,
            config=config,
            now=datetime(2026, 4, 24, 7, 30, 0),
            start_time_key=kwargs.get("start_time_key", "current_off_alert_start_time"),
        ),
    )
    original_key = notification_bridge.build_individual_alert_sent_state_key
    monkeypatch.setattr(
        notification_bridge,
        "build_individual_alert_sent_state_key",
        lambda config=None, now=None: original_key(
            config=config,
            now=datetime(2026, 4, 24, 7, 30, 0),
        ),
    )

    sent_messages = []

    async def fake_send_zalo_message_to_user_detailed(message, user_id, client=None):
        sent_messages.append((user_id, message))
        return {
            "success": True,
            "thread_id": user_id,
            "message": message,
            "stdout": "ok",
            "stderr": "",
            "returncode": 0,
            "command": [],
        }

    monkeypatch.setattr(
        notification_bridge.notification_service,
        "send_zalo_message_to_user_detailed",
        fake_send_zalo_message_to_user_detailed,
    )

    first_result = dispatch_batch_notifications(repo, "b2", log=lambda _message: None)
    second_result = dispatch_batch_notifications(repo, "b3", log=lambda _message: None)

    assert first_result["personal_outage"]["raw_pending_alerts"] == 2
    assert first_result["personal_outage"]["filtered_pending_alerts"] == 1
    assert first_result["personal_outage"]["marked_sent_alerts"] == 1
    assert second_result["personal_outage"]["raw_pending_alerts"] == 2
    assert second_result["personal_outage"]["filtered_pending_alerts"] == 1
    assert second_result["personal_outage"]["marked_sent_alerts"] == 1
    assert len(sent_messages) == 2
    assert "TB001" in sent_messages[0][1]
    assert "OLD" not in sent_messages[0][1]
    assert "TB002" in sent_messages[1][1]
    assert "TB001" not in sent_messages[1][1]


def test_dispatch_batch_notifications_filters_current_off_rows_by_today_cutoff(repo_paths, monkeypatch):
    repo, _measurement_db = repo_paths

    try:
        from do_chu_dong_api import notification_bridge
    except ModuleNotFoundError:
        import notification_bridge

    snapshot_rows = [
        {
            "batch_id": "b2",
            "ma_tb": "OLD",
            "ten_tb": "Old TB",
            "doi_vt": "Tổ Kỹ thuật Địa bàn Quảng Oai",
            "ten_nvkt_db": "VNPT - Nguyen Van A",
            "dienthoai_lh": "0912345678",
            "diachi_ld": "Dia chi",
            "port_id": "HNI.BVI.BVI.OLT.AL.2.1_1-1-1:1",
            "first_off_time": "2026-04-26T05:59:00",
            "duration_minutes": 25,
            "suppressed_by_pattern": False,
            "suppressed_by_wide_area": False,
        },
        {
            "batch_id": "b2",
            "ma_tb": "KEEP",
            "ten_tb": "Keep TB",
            "doi_vt": "Tổ Kỹ thuật Địa bàn Quảng Oai",
            "ten_nvkt_db": "VNPT - Nguyen Van A",
            "dienthoai_lh": "0912345678",
            "diachi_ld": "Dia chi",
            "port_id": "HNI.BVI.BVI.OLT.AL.2.1_1-1-1:2",
            "first_off_time": "2026-04-26T06:01:00",
            "duration_minutes": 25,
            "suppressed_by_pattern": False,
            "suppressed_by_wide_area": False,
        },
    ]

    monkeypatch.setattr(notification_bridge, "load_current_off_snapshot_rows", lambda repo, batch_id: snapshot_rows)
    monkeypatch.setattr(
        notification_bridge.notification_service,
        "load_config",
        lambda: {
            "enable_telegram": False,
            "enable_zalo": False,
            "current_off_alert_start_time": "06:00",
        },
    )
    original_filter = notification_bridge.notification_service.filter_current_off_alerts_by_cutoff
    monkeypatch.setattr(
        notification_bridge.notification_service,
        "filter_current_off_alerts_by_cutoff",
        lambda alerts, config=None, now=None, **kwargs: original_filter(
            alerts,
            config=config,
            now=datetime(2026, 4, 26, 7, 30, 0),
            start_time_key=kwargs.get("start_time_key", "current_off_alert_start_time"),
        ),
    )

    messages = []
    result = dispatch_batch_notifications(repo, "b2", log=messages.append)

    assert result["outage"]["raw_pending_alerts"] == 2
    assert result["outage"]["filtered_pending_alerts"] == 1
    assert result["outage"]["filtered_out_alerts"] == 1
    assert result["outage"]["marked_sent_alerts"] == 1
    assert any("group outage cutoff=" in message and "filtered_by_cutoff=1" in message for message in messages)


def test_dispatch_batch_notifications_excludes_suppressed_current_off_rows(repo_paths, monkeypatch):
    repo, _measurement_db = repo_paths

    try:
        from do_chu_dong_api import notification_bridge
    except ModuleNotFoundError:
        import notification_bridge

    snapshot_rows = [
        {
            "batch_id": "b2",
            "ma_tb": "SUPPRESS",
            "ten_tb": "Suppressed TB",
            "doi_vt": "Tổ Kỹ thuật Địa bàn Quảng Oai",
            "ten_nvkt_db": "VNPT - Nguyen Van A",
            "dienthoai_lh": "0912345678",
            "diachi_ld": "Dia chi",
            "port_id": "HNI.BVI.BVI.OLT.AL.2.1_1-1-1:1",
            "first_off_time": "2026-04-26T06:01:00",
            "duration_minutes": 25,
            "suppressed_by_pattern": True,
            "suppressed_by_wide_area": False,
        },
        {
            "batch_id": "b2",
            "ma_tb": "KEEP",
            "ten_tb": "Keep TB",
            "doi_vt": "Tổ Kỹ thuật Địa bàn Quảng Oai",
            "ten_nvkt_db": "VNPT - Nguyen Van A",
            "dienthoai_lh": "0912345678",
            "diachi_ld": "Dia chi",
            "port_id": "HNI.BVI.BVI.OLT.AL.2.1_1-1-1:2",
            "first_off_time": "2026-04-26T06:02:00",
            "duration_minutes": 25,
            "suppressed_by_pattern": False,
            "suppressed_by_wide_area": False,
        },
    ]

    monkeypatch.setattr(notification_bridge, "load_current_off_snapshot_rows", lambda repo, batch_id: snapshot_rows)
    monkeypatch.setattr(
        notification_bridge.notification_service,
        "load_config",
        lambda: {
            "enable_telegram": False,
            "enable_zalo": False,
        },
    )

    result = dispatch_batch_notifications(repo, "b2", log=lambda _message: None)

    assert result["outage"]["raw_pending_alerts"] == 2
    assert result["outage"]["filtered_pending_alerts"] == 1
    assert result["outage"]["filtered_out_alerts"] == 1
    assert result["outage"]["marked_sent_alerts"] == 1


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


def test_dispatch_batch_notifications_skips_small_port_down_alerts_but_marks_sent(repo_paths, monkeypatch):
    repo, measurement_db = repo_paths

    inserted_id = repo.insert_wide_area_alert(
        WideAreaAlert(
            batch_id="b1",
            parent_port_key="HNI.BVI.BVI.OLT.AL.2.1_1-1-2",
            olt_name="HNI.BVI.BVI.OLT.AL.2.1",
            port="1-1-2",
            subscriber_count=2,
            incident_type="port_down",
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

    async def fail_if_called(*args, **kwargs):
        raise AssertionError("small port_down alert should not be sent")

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
        "load_config",
        lambda: {
            "enable_telegram": True,
            "enable_zalo": True,
            "enable_wide_area_alert_notifications": True,
        },
    )

    messages = []
    result = dispatch_batch_notifications(repo, "b1", log=messages.append)

    assert result["wide_area"]["pending_alerts"] == 1
    assert result["wide_area"]["marked_sent_alerts"] == 1
    assert result["wide_area"]["telegram_sent"] is False
    assert result["wide_area"]["zalo_messages_sent"] == 0
    assert any("port_down_below_threshold=1" in message for message in messages)

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
