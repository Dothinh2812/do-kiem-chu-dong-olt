import sqlite3
import sys
import threading
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

try:
    from do_chu_dong_api import app, alert_engine
    from do_chu_dong_api.current_off_snapshot import build_current_off_snapshot
    from do_chu_dong_api.alert_models import SubscriberSnapshot
    from do_chu_dong_api.transition_history_export import collect_qualified_transition_rows
except ModuleNotFoundError:
    import app
    import alert_engine
    from current_off_snapshot import build_current_off_snapshot
    from alert_models import SubscriberSnapshot
    from transition_history_export import collect_qualified_transition_rows


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


def create_source_db(path):
    with sqlite3.connect(path) as conn:
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


def insert_danhba_row(source_db, subscriber_key, ma_tb):
    with sqlite3.connect(source_db) as conn:
        conn.execute(
            """
            INSERT INTO danhba (
                sub, Ma_Tb, Ma_Men, Ten_Tb, DIACHI_LD, DIENTHOAI_LH, TEN_NVKT_DB, DOI_VT
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                subscriber_key,
                ma_tb,
                f"MM-{ma_tb}",
                f"Ten {ma_tb}",
                "Dia chi",
                "0900000000",
                "VNPT - Nguyen Van A",
                "Tổ Kỹ thuật Địa bàn Sơn Tây",
            ),
        )
        conn.commit()


def insert_measurement_row(measurement_db, subscriber_key, batch_id, status, measured_at):
    _olt_name, rest = subscriber_key.split("_", 1)
    port_part, onu_index = rest.split(":", 1)
    frame_no, slot_no, port_no = [int(part) for part in port_part.split("-")]
    with sqlite3.connect(measurement_db) as conn:
        conn.execute(
            """
            INSERT INTO onu_measurements (
                "Cổng", batch_id, onuLastOff, onuLastOn, onuStatusStr, frameNo, slotNo, portNo, onuIndex,
                accountFiber, NgayDo, ThoiGianDo
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                subscriber_key,
                batch_id,
                "",
                "",
                status,
                frame_no,
                slot_no,
                port_no,
                int(onu_index),
                "acc",
                measured_at.strftime("%Y-%m-%d"),
                measured_at.strftime("%H:%M:%S"),
            ),
        )
        conn.commit()


def test_lookup_subscribers_by_parent_port_returns_matching_danhba_rows(tmp_path):
    source_db = tmp_path / "database.db"
    create_source_db(source_db)
    insert_danhba_row(source_db, "HNI.STY.XSZ.OLT.ZT.1.1_1-1-11:1", "TB001")
    insert_danhba_row(source_db, "HNI.STY.XSZ.OLT.ZT.1.1_1-1-11:2", "TB002")
    insert_danhba_row(source_db, "HNI.STY.XSZ.OLT.ZT.1.1_1-1-12:1", "TB003")

    rows = app.lookup_subscribers_by_parent_port(
        str(source_db),
        "HNI.STY.XSZ.OLT.ZT.1.1_1-1-11",
    )

    assert [row["subscriber_key"] for row in rows] == [
        "HNI.STY.XSZ.OLT.ZT.1.1_1-1-11:1",
        "HNI.STY.XSZ.OLT.ZT.1.1_1-1-11:2",
    ]


def test_build_port_down_measurement_records_creates_port_down_rows():
    subscriber_rows = [
        {"subscriber_key": "HNI.STY.XSZ.OLT.ZT.1.1_1-1-11:1", "account_fiber": "", "onu_index": 1},
        {"subscriber_key": "HNI.STY.XSZ.OLT.ZT.1.1_1-1-11:2", "account_fiber": "", "onu_index": 2},
    ]

    rows = app.build_port_down_measurement_records(
        subscriber_rows=subscriber_rows,
        batch_id="202604271230",
        measured_date="2026-04-27",
        measured_time="12:30:00",
    )

    assert len(rows) == 2
    assert rows[0][0] == "HNI.STY.XSZ.OLT.ZT.1.1_1-1-11:1"
    assert rows[0][9] == "PORT_DOWN"
    assert rows[1][13] == 2


def test_download_single_port_skips_onu_fetch_and_writes_port_down_rows_when_port_down(tmp_path, monkeypatch):
    monkeypatch.setattr(app, "MEASUREMENT_LOG_DIR", tmp_path)
    monkeypatch.setattr(app, "issue_log_lock", threading.Lock())
    monkeypatch.setattr(app, "device_semaphores", {"10.31.8.69": threading.Semaphore(1)})
    monkeypatch.setattr(app, "SOURCE_DATABASE_PATH", str(tmp_path / "database.db"))
    monkeypatch.setattr(app, "lookup_subscribers_by_parent_port", lambda *args, **kwargs: [
        {"subscriber_key": "HNI.STY.XSZ.OLT.ZT.1.1_1-1-11:1", "account_fiber": "", "onu_index": 1}
    ])
    monkeypatch.setattr(app, "fetch_single_port_status", lambda task: "Down")
    captured = {}
    monkeypatch.setattr(app, "insert_measurement_records", lambda records: captured.setdefault("records", records))
    monkeypatch.setattr(
        app.requests,
        "get",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("ONU detail API must not be called")),
    )

    task = {
        "deviceIp": "10.31.8.69",
        "frame": "1",
        "slot": "1",
        "port": "11",
        "olt_name": "HNI.STY.XSZ.OLT.ZT.1.1",
    }

    result = app.download_single_port(1, 1, task, "202604271230")

    assert result == "port_down"
    assert captured["records"][0][9] == "PORT_DOWN"


def test_download_single_port_ignores_down_port_without_subscribers(tmp_path, monkeypatch):
    monkeypatch.setattr(app, "MEASUREMENT_LOG_DIR", tmp_path)
    monkeypatch.setattr(app, "issue_log_lock", threading.Lock())
    monkeypatch.setattr(app, "device_semaphores", {"10.31.8.69": threading.Semaphore(1)})
    monkeypatch.setattr(app, "SOURCE_DATABASE_PATH", str(tmp_path / "database.db"))
    monkeypatch.setattr(app, "lookup_subscribers_by_parent_port", lambda *args, **kwargs: [])
    monkeypatch.setattr(app, "fetch_single_port_status", lambda task: "Down")
    called = {"inserted": False}
    monkeypatch.setattr(app, "insert_measurement_records", lambda records: called.__setitem__("inserted", True))

    task = {
        "deviceIp": "10.31.8.69",
        "frame": "1",
        "slot": "1",
        "port": "11",
        "olt_name": "HNI.STY.XSZ.OLT.ZT.1.1",
    }

    result = app.download_single_port(1, 1, task, "202604271230")

    assert result == "filtered"
    assert called["inserted"] is False


def test_port_down_snapshot_row_keeps_current_status_port_down():
    snapshot = SubscriberSnapshot(
        subscriber_key="HNI.STY.XSZ.OLT.ZT.1.1_1-1-11:1",
        parent_port_key="HNI.STY.XSZ.OLT.ZT.1.1_1-1-11",
        olt_name="HNI.STY.XSZ.OLT.ZT.1.1",
        batch_id="b1",
        status="PORT_DOWN",
        measured_at=datetime(2026, 4, 27, 12, 30, 0),
        ma_tb="TB001",
    )

    rows = build_current_off_snapshot(
        [snapshot],
        {snapshot.subscriber_key: {"first_off_time": datetime(2026, 4, 27, 12, 20, 0)}},
        wide_area_subscriber_keys={snapshot.subscriber_key},
    )

    assert rows[0]["current_status"] == "PORT_DOWN"
    assert rows[0]["suppressed_by_wide_area"] is True


def test_alert_engine_normalize_status_preserves_port_down():
    assert alert_engine.normalize_status("PORT_DOWN") == "PORT_DOWN"


def test_process_completed_batch_marks_port_down_subscribers_as_wide_area(tmp_path):
    measurement_db = tmp_path / "onu_measurements.db"
    source_db = tmp_path / "database.db"
    with sqlite3.connect(measurement_db) as conn:
        conn.execute(RAW_SCHEMA)
        conn.commit()
    create_source_db(source_db)

    subscriber_key_1 = "HNI.STY.XSZ.OLT.ZT.1.1_1-1-11:1"
    subscriber_key_2 = "HNI.STY.XSZ.OLT.ZT.1.1_1-1-11:2"
    insert_danhba_row(source_db, subscriber_key_1, "TB001")
    insert_danhba_row(source_db, subscriber_key_2, "TB002")
    when = datetime(2026, 4, 27, 12, 30, 0)
    insert_measurement_row(measurement_db, subscriber_key_1, "b1", "PORT_DOWN", when)
    insert_measurement_row(measurement_db, subscriber_key_2, "b1", "PORT_DOWN", when)

    repo = alert_engine.AlertRepository(str(measurement_db), str(source_db))
    repo.ensure_schema()
    repo.mark_batch_started("b1", when, 1)
    repo.mark_batch_completed("b1", when, 1, 1, 0, 0, 0)

    result = alert_engine.process_completed_batch(
        db_path=str(measurement_db),
        batch_id="b1",
        source_db_path=str(source_db),
        send_notifications=False,
        wide_area_threshold=1,
        log=lambda *_args, **_kwargs: None,
    )

    assert result["wide_area_alerts_created"] == 1
    assert result["current_off_count"] == 2


def test_transition_history_ignores_port_down_as_on_off_transition(tmp_path):
    measurement_db = tmp_path / "onu_measurements.db"
    source_db = tmp_path / "database.db"
    with sqlite3.connect(measurement_db) as conn:
        conn.execute(RAW_SCHEMA)
        conn.commit()
    create_source_db(source_db)

    subscriber_key = "HNI.STY.XSZ.OLT.ZT.1.1_1-1-11:1"
    insert_danhba_row(source_db, subscriber_key, "TB001")
    insert_measurement_row(measurement_db, subscriber_key, "b1", "ON", datetime(2026, 4, 27, 12, 0, 0))
    insert_measurement_row(measurement_db, subscriber_key, "b2", "PORT_DOWN", datetime(2026, 4, 27, 12, 5, 0))
    insert_measurement_row(measurement_db, subscriber_key, "b3", "ON", datetime(2026, 4, 27, 12, 10, 0))

    rows = collect_qualified_transition_rows(str(measurement_db), str(source_db))

    assert rows == []
