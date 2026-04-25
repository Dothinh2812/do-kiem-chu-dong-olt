import sqlite3
import sys
from datetime import datetime
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

try:
    from do_chu_dong_api.alert_db import AlertRepository
    from do_chu_dong_api.alert_engine import normalize_status, process_completed_batch
    from do_chu_dong_api.pattern_exclusion import (
        get_pattern_exclusion_list,
        update_exclusion_table,
    )
except ModuleNotFoundError:
    from alert_db import AlertRepository
    from alert_engine import normalize_status, process_completed_batch
    from pattern_exclusion import get_pattern_exclusion_list, update_exclusion_table


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
def db_paths(tmp_path):
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

    return measurement_db, source_db


def insert_danhba_row(source_db, subscriber_key, ma_tb, ten_tb="Ten TB", doi_vt="Tổ Kỹ thuật Địa bàn Sơn Tây"):
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
                ten_tb,
                "Dia chi",
                "0900000000",
                "VNPT - Nguyen Van A",
                doi_vt,
            ),
        )
        conn.commit()


def insert_measurement_row(measurement_db, subscriber_key, batch_id, status, measured_at, account_fiber="acc"):
    olt_name, rest = subscriber_key.split("_", 1)
    port_part, onu_index = rest.split(":", 1)
    frame_no, slot_no, port_no = [int(part) for part in port_part.split("-")]
    with sqlite3.connect(measurement_db) as conn:
        conn.execute(
            """
            INSERT INTO onu_measurements (
                "Cổng", batch_id, onuStatusStr, frameNo, slotNo, portNo, onuIndex,
                accountFiber, NgayDo, ThoiGianDo
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                subscriber_key,
                batch_id,
                status,
                frame_no,
                slot_no,
                port_no,
                int(onu_index),
                account_fiber,
                measured_at.strftime("%Y-%m-%d"),
                measured_at.strftime("%H:%M:%S"),
            ),
        )
        conn.commit()


def mark_batch_completed(measurement_db, batch_id, when, expected_ports=1):
    repo = AlertRepository(str(measurement_db), str(measurement_db))
    repo.ensure_schema()
    repo.mark_batch_started(batch_id, when, expected_ports)
    repo.mark_batch_completed(batch_id, when, expected_ports, expected_ports, 0, 0, 0)


def run_batch(measurement_db, source_db, batch_id, when, send_notifications=False):
    mark_batch_completed(measurement_db, batch_id, when)
    return process_completed_batch(
        db_path=str(measurement_db),
        batch_id=batch_id,
        source_db_path=str(source_db),
        send_notifications=send_notifications,
    )


def fetch_one(measurement_db, sql, params=()):
    with sqlite3.connect(measurement_db) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(sql, params).fetchone()
        return dict(row) if row else None


def fetch_all(measurement_db, sql, params=()):
    with sqlite3.connect(measurement_db) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(sql, params).fetchall()
        return [dict(row) for row in rows]


def test_normalize_status():
    assert normalize_status("ON") == "ON"
    assert normalize_status("offline") == "OFF"
    assert normalize_status("Power ON") == "ON"
    assert normalize_status(None) == "UNKNOWN"


def test_individual_outage_requires_two_consecutive_off_batches(db_paths):
    measurement_db, source_db = db_paths
    subscriber_key = "HNI.STY.STY.OLT.AL.2.1_1-1-1:1"
    insert_danhba_row(source_db, subscriber_key, "TB001")

    insert_measurement_row(measurement_db, subscriber_key, "b1", "ON", datetime(2026, 4, 24, 8, 0, 0))
    run_batch(measurement_db, source_db, "b1", datetime(2026, 4, 24, 8, 0, 5))

    insert_measurement_row(measurement_db, subscriber_key, "b2", "ON", datetime(2026, 4, 24, 8, 5, 0))
    run_batch(measurement_db, source_db, "b2", datetime(2026, 4, 24, 8, 5, 5))

    state = fetch_one(
        measurement_db,
        "SELECT current_state, consecutive_on_count FROM subscriber_status_state WHERE subscriber_key = ?",
        (subscriber_key,),
    )
    assert state["current_state"] == "STABLE_ON"
    assert state["consecutive_on_count"] >= 2

    insert_measurement_row(measurement_db, subscriber_key, "b3", "OFF", datetime(2026, 4, 24, 8, 10, 0))
    run_batch(measurement_db, source_db, "b3", datetime(2026, 4, 24, 8, 10, 5))

    state = fetch_one(
        measurement_db,
        "SELECT current_state FROM subscriber_status_state WHERE subscriber_key = ?",
        (subscriber_key,),
    )
    assert state["current_state"] == "PENDING_OFF"
    assert fetch_one(measurement_db, "SELECT * FROM outage_alerts WHERE subscriber_key = ?", (subscriber_key,)) is None

    insert_measurement_row(measurement_db, subscriber_key, "b4", "OFF", datetime(2026, 4, 24, 8, 15, 0))
    run_batch(measurement_db, source_db, "b4", datetime(2026, 4, 24, 8, 15, 5))

    outage = fetch_one(
        measurement_db,
        "SELECT suppressed_by_pattern, suppressed_by_wide_area FROM outage_alerts WHERE subscriber_key = ?",
        (subscriber_key,),
    )
    assert outage["suppressed_by_pattern"] == 0
    assert outage["suppressed_by_wide_area"] == 0


def test_wide_area_alert_is_immediate_and_suppresses_confirmed_individuals(db_paths):
    measurement_db, source_db = db_paths
    subscribers = [f"HNI.STY.STY.OLT.AL.2.1_1-1-1:{i}" for i in range(1, 7)]
    for idx, subscriber_key in enumerate(subscribers, start=1):
        insert_danhba_row(source_db, subscriber_key, f"TB{idx:03d}")

    for batch_id, status, when in [
        ("b1", "ON", datetime(2026, 4, 24, 9, 0, 0)),
        ("b2", "ON", datetime(2026, 4, 24, 9, 5, 0)),
        ("b3", "OFF", datetime(2026, 4, 24, 9, 10, 0)),
        ("b4", "OFF", datetime(2026, 4, 24, 9, 15, 0)),
    ]:
        for subscriber_key in subscribers:
            insert_measurement_row(measurement_db, subscriber_key, batch_id, status, when)
        run_batch(measurement_db, source_db, batch_id, when)

    wide_area = fetch_one(
        measurement_db,
        "SELECT subscriber_count FROM wide_area_alerts WHERE batch_id = ?",
        ("b3",),
    )
    assert wide_area["subscriber_count"] == 6

    outage_rows = fetch_all(
        measurement_db,
        "SELECT suppressed_by_wide_area FROM outage_alerts WHERE batch_id = ? ORDER BY subscriber_key",
        ("b4",),
    )
    assert len(outage_rows) == 6
    assert all(row["suppressed_by_wide_area"] == 1 for row in outage_rows)


def test_pattern_exclusion_suppresses_individual_outage(db_paths):
    measurement_db, source_db = db_paths
    subscriber_key = "HNI.STY.STY.OLT.AL.2.1_1-2-1:1"
    insert_danhba_row(source_db, subscriber_key, "TB777")

    repo = AlertRepository(str(measurement_db), str(source_db))
    repo.ensure_schema()
    repo.upsert_pattern_exclusion(
        ma_tb="TB777",
        ten_tb="Ten TB",
        pattern_type="NIGHT_OFF",
        total_events=3,
        pattern_score=0.9,
        notes="seeded by test",
    )

    for batch_id, status, when in [
        ("b1", "ON", datetime(2026, 4, 24, 10, 0, 0)),
        ("b2", "ON", datetime(2026, 4, 24, 10, 5, 0)),
        ("b3", "OFF", datetime(2026, 4, 24, 10, 10, 0)),
        ("b4", "OFF", datetime(2026, 4, 24, 10, 15, 0)),
    ]:
        insert_measurement_row(measurement_db, subscriber_key, batch_id, status, when)
        run_batch(measurement_db, source_db, batch_id, when)

    outage = fetch_one(
        measurement_db,
        "SELECT suppressed_by_pattern, suppression_reason FROM outage_alerts WHERE subscriber_key = ? AND batch_id = ?",
        (subscriber_key, "b4"),
    )
    assert outage["suppressed_by_pattern"] == 1
    assert outage["suppression_reason"] == "pattern_exclusion"


def test_pattern_analyzer_updates_exclusion_table_from_recoveries(db_paths):
    measurement_db, _ = db_paths
    repo = AlertRepository(str(measurement_db), str(measurement_db))
    repo.ensure_schema()

    with sqlite3.connect(measurement_db) as conn:
        conn.executemany(
            """
            INSERT INTO recovery_alerts (
                subscriber_key, parent_port_key, batch_id, ma_tb, ten_tb, olt_name,
                outage_time, recovery_time, outage_duration_minutes, notification_sent
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0)
            """,
            [
                (
                    "HNI.STY.STY.OLT.AL.2.1_1-3-1:1",
                    "HNI.STY.STY.OLT.AL.2.1_1-3-1",
                    "b1",
                    "TB900",
                    "Khach hang 1",
                    "HNI.STY.STY.OLT.AL.2.1",
                    "2026-04-24T20:05:00",
                    "2026-04-24T20:20:00",
                    15,
                ),
                (
                    "HNI.STY.STY.OLT.AL.2.1_1-3-1:1",
                    "HNI.STY.STY.OLT.AL.2.1_1-3-1",
                    "b2",
                    "TB900",
                    "Khach hang 1",
                    "HNI.STY.STY.OLT.AL.2.1",
                    "2026-04-25T20:10:00",
                    "2026-04-25T20:25:00",
                    15,
                ),
                (
                    "HNI.STY.STY.OLT.AL.2.1_1-3-1:1",
                    "HNI.STY.STY.OLT.AL.2.1_1-3-1",
                    "b3",
                    "TB900",
                    "Khach hang 1",
                    "HNI.STY.STY.OLT.AL.2.1",
                    "2026-04-26T21:00:00",
                    "2026-04-26T21:30:00",
                    30,
                ),
            ],
        )
        conn.commit()

    updated = update_exclusion_table(str(measurement_db))
    exclusion = get_pattern_exclusion_list(str(measurement_db))

    assert updated == 1
    assert "TB900" in exclusion


def test_wide_area_alert_normalizes_short_doi_vt_names(db_paths):
    measurement_db, source_db = db_paths
    subscribers = [f"HNI.STY.STY.OLT.AL.2.1_1-9-1:{i}" for i in range(1, 7)]
    for idx, subscriber_key in enumerate(subscribers, start=1):
        insert_danhba_row(source_db, subscriber_key, f"TB9{idx:02d}", doi_vt="Sơn Tây")

    for subscriber_key in subscribers:
        insert_measurement_row(measurement_db, subscriber_key, "b1", "OFF", datetime(2026, 4, 24, 11, 0, 0))

    run_batch(measurement_db, source_db, "b1", datetime(2026, 4, 24, 11, 0, 5))

    wide_area = fetch_one(
        measurement_db,
        "SELECT doi_vt FROM wide_area_alerts WHERE batch_id = ?",
        ("b1",),
    )
    assert wide_area["doi_vt"] == "Tổ Kỹ thuật Địa bàn Sơn Tây"


def test_wide_area_alert_skips_off_snapshot_when_same_ma_tb_is_on_same_batch(db_paths):
    measurement_db, source_db = db_paths

    conflict_on_key = "HNI.BVI.BVI.OLT.AL.2.1_1-3-2:19"
    conflict_off_key = "HNI.BVI.BVI.OLT.AL.2.1_1-3-2:13"
    insert_danhba_row(source_db, conflict_on_key, "loc90cm")
    insert_danhba_row(source_db, conflict_off_key, "loc90cm")

    other_keys = [
        "HNI.BVI.BVI.OLT.AL.2.1_1-3-2:14",
        "HNI.BVI.BVI.OLT.AL.2.1_1-3-2:15",
        "HNI.BVI.BVI.OLT.AL.2.1_1-3-2:16",
        "HNI.BVI.BVI.OLT.AL.2.1_1-3-2:20",
        "HNI.BVI.BVI.OLT.AL.2.1_1-3-2:21",
        "HNI.BVI.BVI.OLT.AL.2.1_1-3-2:3",
        "HNI.BVI.BVI.OLT.AL.2.1_1-3-2:7",
    ]
    for idx, key in enumerate(other_keys, start=1):
        insert_danhba_row(source_db, key, f"TB{idx:03d}")

    insert_measurement_row(measurement_db, conflict_on_key, "b1", "ON", datetime(2026, 4, 24, 19, 50, 30))
    insert_measurement_row(measurement_db, conflict_off_key, "b1", "OFF", datetime(2026, 4, 24, 19, 50, 30), account_fiber="")
    for idx, key in enumerate(other_keys, start=1):
        insert_measurement_row(measurement_db, key, "b1", "OFF", datetime(2026, 4, 24, 19, 50, 30), account_fiber="")

    result = run_batch(measurement_db, source_db, "b1", datetime(2026, 4, 24, 19, 50, 35), send_notifications=False)

    wide_area = fetch_one(
        measurement_db,
        "SELECT subscriber_count, subscriber_summary_json FROM wide_area_alerts WHERE batch_id = ?",
        ("b1",),
    )
    assert wide_area["subscriber_count"] == 7
    assert "loc90cm" not in wide_area["subscriber_summary_json"]
    assert result["wide_area_alerts_created"] == 1


def test_process_completed_batch_reports_progress_and_summary(db_paths):
    measurement_db, source_db = db_paths
    subscribers = [f"HNI.STY.STY.OLT.AL.2.1_1-8-1:{i}" for i in range(1, 7)]
    for idx, subscriber_key in enumerate(subscribers, start=1):
        insert_danhba_row(source_db, subscriber_key, f"TB8{idx:02d}")
        insert_measurement_row(measurement_db, subscriber_key, "b1", "OFF", datetime(2026, 4, 24, 12, 0, 0))

    mark_batch_completed(measurement_db, "b1", datetime(2026, 4, 24, 12, 0, 5), expected_ports=6)

    messages = []
    result = process_completed_batch(
        db_path=str(measurement_db),
        batch_id="b1",
        source_db_path=str(source_db),
        send_notifications=False,
        progress_every=2,
        log=messages.append,
    )

    assert result["state_rows_processed"] == 6
    assert result["current_off_count"] == 6
    assert result["wide_area_alerts_created"] == 1
    assert "duration_seconds" in result
    assert any("start post-processing" in message for message in messages)
    assert any("processing state machine 2/6" in message for message in messages)
    assert any("wide-area detected: 1 ports" in message for message in messages)
    assert any("finished: snapshots=6" in message for message in messages)
