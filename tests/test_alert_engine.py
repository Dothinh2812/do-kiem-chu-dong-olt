import json
import sqlite3
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

try:
    from do_chu_dong_api.alert_db import AlertRepository
    from do_chu_dong_api import alert_engine
    from do_chu_dong_api.alert_engine import normalize_status, process_completed_batch
    from do_chu_dong_api.pattern_exclusion import (
        get_pattern_exclusion_list,
        update_exclusion_table,
    )
except ModuleNotFoundError:
    from alert_db import AlertRepository
    import alert_engine
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


def insert_measurement_row(
    measurement_db,
    subscriber_key,
    batch_id,
    status,
    measured_at,
    account_fiber="acc",
    onu_last_off="",
    onu_last_on="",
):
    olt_name, rest = subscriber_key.split("_", 1)
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
                onu_last_off,
                onu_last_on,
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


def insert_recovery_history_row(measurement_db, subscriber_key, batch_id, ma_tb, outage_time, duration_minutes=15):
    parent_port_key = subscriber_key.rsplit(":", 1)[0]
    with sqlite3.connect(measurement_db) as conn:
        conn.execute(
            """
            INSERT INTO recovery_alerts (
                subscriber_key, parent_port_key, batch_id, ma_tb, ten_tb, olt_name,
                outage_time, recovery_time, outage_duration_minutes, notification_sent
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0)
            """,
            (
                subscriber_key,
                parent_port_key,
                batch_id,
                ma_tb,
                "Ten TB",
                subscriber_key.split("_", 1)[0],
                outage_time.isoformat(),
                (outage_time + timedelta(minutes=duration_minutes)).isoformat(),
                duration_minutes,
            ),
        )
        conn.commit()


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
            insert_measurement_row(
                measurement_db,
                subscriber_key,
                batch_id,
                status,
                when,
                onu_last_off=when.strftime("%Y-%m-%d %H:%M:%S") if status == "OFF" else "",
            )
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


def test_wide_area_alert_requires_onu_last_off_cluster_within_five_minutes(db_paths):
    measurement_db, source_db = db_paths
    subscribers = [f"HNI.BVI.TLH.OLT.AL.2.1_1-1-15:{i}" for i in range(1, 7)]
    last_offs = [
        "2026-04-25 12:00:00",
        "2026-04-25 12:01:00",
        "2026-04-25 12:02:00",
        "2026-04-25 12:03:00",
        "2026-04-25 12:04:00",
        "2026-04-25 12:04:30",
    ]
    for idx, subscriber_key in enumerate(subscribers, start=1):
        insert_danhba_row(source_db, subscriber_key, f"TB{idx:03d}")
        insert_measurement_row(
            measurement_db,
            subscriber_key,
            "b1",
            "OFF",
            datetime(2026, 4, 25, 12, 10, 0),
            onu_last_off=last_offs[idx - 1],
        )

    result = run_batch(measurement_db, source_db, "b1", datetime(2026, 4, 25, 12, 10, 5), send_notifications=False)

    wide_area = fetch_one(
        measurement_db,
        "SELECT subscriber_count FROM wide_area_alerts WHERE batch_id = ?",
        ("b1",),
    )
    assert result["wide_area_alerts_created"] == 1
    assert wide_area["subscriber_count"] == 6


def test_wide_area_alert_persists_start_time_and_duration(db_paths):
    measurement_db, source_db = db_paths
    subscribers = [f"HNI.BVI.TLH.OLT.AL.2.1_1-1-17:{i}" for i in range(1, 7)]

    for idx, subscriber_key in enumerate(subscribers, start=1):
        insert_danhba_row(source_db, subscriber_key, f"TBY{idx:03d}")
        insert_measurement_row(
            measurement_db,
            subscriber_key,
            "b1",
            "OFF",
            datetime(2026, 4, 25, 12, 0, 0),
            onu_last_off="2026-04-25 11:58:00",
        )

    run_batch(measurement_db, source_db, "b1", datetime(2026, 4, 25, 12, 0, 5), send_notifications=False)

    for subscriber_key in subscribers:
        insert_measurement_row(
            measurement_db,
            subscriber_key,
            "b2",
            "OFF",
            datetime(2026, 4, 25, 12, 10, 0),
            onu_last_off="2026-04-25 11:58:00",
        )

    result = run_batch(measurement_db, source_db, "b2", datetime(2026, 4, 25, 12, 10, 5), send_notifications=False)

    wide_area = fetch_one(
        measurement_db,
        "SELECT first_off_time, off_duration_minutes FROM wide_area_alerts WHERE batch_id = ?",
        ("b2",),
    )

    assert result["wide_area_alerts_created"] == 1
    assert wide_area["first_off_time"] == "2026-04-25T12:00:00"
    assert wide_area["off_duration_minutes"] == 10


def test_wide_area_alert_does_not_trigger_for_scattered_onu_last_off_values(db_paths):
    measurement_db, source_db = db_paths
    subscribers = [f"HNI.BVI.TLH.OLT.AL.2.1_1-1-15:{i}" for i in range(1, 7)]
    last_offs = [
        "2026-04-25 12:00:00",
        "2026-04-25 12:10:00",
        "2026-04-25 12:20:00",
        "2026-04-25 12:30:00",
        "2026-04-25 12:40:00",
        "2026-04-25 12:50:00",
    ]
    for idx, subscriber_key in enumerate(subscribers, start=1):
        insert_danhba_row(source_db, subscriber_key, f"TB{idx:03d}")
        insert_measurement_row(
            measurement_db,
            subscriber_key,
            "b1",
            "OFF",
            datetime(2026, 4, 25, 12, 55, 0),
            onu_last_off=last_offs[idx - 1],
        )

    result = run_batch(measurement_db, source_db, "b1", datetime(2026, 4, 25, 12, 55, 5), send_notifications=False)

    assert result["wide_area_alerts_created"] == 0
    assert fetch_one(measurement_db, "SELECT id FROM wide_area_alerts WHERE batch_id = ?", ("b1",)) is None


def test_wide_area_alert_includes_blank_onu_last_off_when_cluster_exists(db_paths):
    measurement_db, source_db = db_paths
    subscribers = [f"HNI.BVI.TLH.OLT.AL.2.1_1-1-16:{i}" for i in range(1, 7)]
    last_offs = [
        "2026-04-25 12:00:00",
        "2026-04-25 12:01:00",
        "2026-04-25 12:02:00",
        "2026-04-25 12:03:00",
        "",
        "",
    ]
    for idx, subscriber_key in enumerate(subscribers, start=1):
        insert_danhba_row(source_db, subscriber_key, f"TBX{idx:03d}")
        insert_measurement_row(
            measurement_db,
            subscriber_key,
            "b1",
            "OFF",
            datetime(2026, 4, 25, 12, 10, 0),
            onu_last_off=last_offs[idx - 1],
        )

    result = run_batch(
        measurement_db,
        source_db,
        "b1",
        datetime(2026, 4, 25, 12, 10, 5),
        send_notifications=False,
    )

    wide_area = fetch_one(
        measurement_db,
        "SELECT subscriber_count, subscriber_summary_json FROM wide_area_alerts WHERE batch_id = ?",
        ("b1",),
    )
    assert result["wide_area_alerts_created"] == 1
    assert wide_area["subscriber_count"] == 6
    assert wide_area["subscriber_summary_json"].count('"onu_last_off": ""') == 2


def test_pattern_exclusion_suppresses_individual_outage(db_paths):
    measurement_db, source_db = db_paths
    subscriber_key = "HNI.STY.STY.OLT.AL.2.1_1-2-1:1"
    insert_danhba_row(source_db, subscriber_key, "TB777")

    repo = AlertRepository(str(measurement_db), str(source_db))
    repo.ensure_schema()
    for idx, outage_time in enumerate(
        [
            datetime(2026, 4, 20, 10, 5, 0),
            datetime(2026, 4, 21, 10, 10, 0),
            datetime(2026, 4, 22, 10, 0, 0),
        ],
        start=1,
    ):
        insert_recovery_history_row(
            measurement_db,
            subscriber_key,
            f"history-{idx}",
            "TB777",
            outage_time,
            duration_minutes=15,
        )

    for batch_id, status, when in [
        ("b1", "ON", datetime(2026, 4, 24, 10, 0, 0)),
        ("b2", "ON", datetime(2026, 4, 24, 10, 5, 0)),
        ("b3", "OFF", datetime(2026, 4, 24, 10, 10, 0)),
    ]:
        insert_measurement_row(measurement_db, subscriber_key, batch_id, status, when)
        run_batch(measurement_db, source_db, batch_id, when)

    insert_measurement_row(
        measurement_db,
        subscriber_key,
        "b4",
        "OFF",
        datetime(2026, 4, 24, 10, 15, 0),
    )
    mark_batch_completed(measurement_db, "b4", datetime(2026, 4, 24, 10, 15, 5))
    messages = []
    result = process_completed_batch(
        db_path=str(measurement_db),
        batch_id="b4",
        source_db_path=str(source_db),
        send_notifications=False,
        log=messages.append,
    )

    outage = fetch_one(
        measurement_db,
        "SELECT suppressed_by_pattern, suppression_reason FROM outage_alerts WHERE subscriber_key = ? AND batch_id = ?",
        (subscriber_key, "b4"),
    )
    assert outage["suppressed_by_pattern"] == 1
    assert outage["suppression_reason"] == "pattern_exclusion"
    assert result["customer_poweroff_suppressed_count"] == 1
    assert any("customer_poweroff_suppressed=1" in message for message in messages)


def test_stale_pattern_exclusion_does_not_suppress_unscored_current_outage(db_paths):
    measurement_db, source_db = db_paths
    subscriber_key = "HNI.STY.STY.OLT.AL.2.1_1-2-1:2"
    insert_danhba_row(source_db, subscriber_key, "TB778")

    repo = AlertRepository(str(measurement_db), str(source_db))
    repo.ensure_schema()
    repo.upsert_pattern_exclusion(
        ma_tb="TB778",
        ten_tb="Ten TB",
        pattern_type="LIKELY_SELF_POWER_OFF",
        total_events=3,
        pattern_score=0.9,
        notes="stale row from previous scoring",
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
    assert outage["suppressed_by_pattern"] == 0
    assert outage["suppression_reason"] in (None, "")


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
        insert_measurement_row(
            measurement_db,
            subscriber_key,
            "b1",
            "OFF",
            datetime(2026, 4, 24, 11, 0, 0),
            onu_last_off="2026-04-24 11:00:00",
        )

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
    insert_measurement_row(
        measurement_db,
        conflict_off_key,
        "b1",
        "OFF",
        datetime(2026, 4, 24, 19, 50, 30),
        account_fiber="",
        onu_last_off="2026-04-24 19:50:30",
    )
    for idx, key in enumerate(other_keys, start=1):
        insert_measurement_row(
            measurement_db,
            key,
            "b1",
            "OFF",
            datetime(2026, 4, 24, 19, 50, 30),
            account_fiber="",
            onu_last_off="2026-04-24 19:50:30",
        )

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
        insert_measurement_row(
            measurement_db,
            subscriber_key,
            "b1",
            "OFF",
            datetime(2026, 4, 24, 12, 0, 0),
            onu_last_off="2026-04-24 12:00:00",
        )

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


def test_process_completed_batch_uses_bulk_state_access(monkeypatch, db_paths):
    measurement_db, source_db = db_paths
    subscriber_key = "HNI.STY.STY.OLT.AL.2.1_1-8-1:1"
    insert_danhba_row(source_db, subscriber_key, "TB801")
    insert_measurement_row(
        measurement_db,
        subscriber_key,
        "b1",
        "ON",
        datetime(2026, 4, 24, 12, 0, 0),
    )
    mark_batch_completed(measurement_db, "b1", datetime(2026, 4, 24, 12, 0, 5))

    class BulkOnlyRepository(AlertRepository):
        def get_state_row(self, subscriber_key):
            raise AssertionError("process_completed_batch should bulk-load state rows")

        def fetch_state_map(self, subscriber_keys):
            return {}

    monkeypatch.setattr(alert_engine, "AlertRepository", BulkOnlyRepository)

    result = process_completed_batch(
        db_path=str(measurement_db),
        batch_id="b1",
        source_db_path=str(source_db),
        send_notifications=False,
    )

    assert result["state_rows_processed"] == 1


def test_delete_incomplete_batches_removes_running_measurements_only(db_paths):
    measurement_db, source_db = db_paths
    repo = AlertRepository(str(measurement_db), str(source_db))
    repo.ensure_schema()

    running_time = datetime(2026, 4, 25, 8, 12, 0)
    completed_time = datetime(2026, 4, 25, 8, 20, 0)

    repo.mark_batch_started("b_running", running_time, 2)
    insert_measurement_row(
        measurement_db,
        "HNI.STY.STY.OLT.AL.2.1_1-1-1:1",
        "b_running",
        "OFF",
        running_time,
    )

    repo.mark_batch_started("b_completed", completed_time, 1)
    insert_measurement_row(
        measurement_db,
        "HNI.STY.STY.OLT.AL.2.1_1-1-1:2",
        "b_completed",
        "ON",
        completed_time,
    )
    repo.mark_batch_completed("b_completed", completed_time, 1, 1, 0, 0, 0)

    deleted = repo.delete_incomplete_batches()

    assert deleted == ["b_running"]
    assert fetch_one(
        measurement_db,
        "SELECT batch_id, status FROM measurement_batches WHERE batch_id = ?",
        ("b_running",),
    ) is None
    assert fetch_one(
        measurement_db,
        "SELECT batch_id FROM onu_measurements WHERE batch_id = ?",
        ("b_running",),
    ) is None

    completed_batch = fetch_one(
        measurement_db,
        "SELECT batch_id, status FROM measurement_batches WHERE batch_id = ?",
        ("b_completed",),
    )
    assert completed_batch["status"] == "completed"
    completed_row = fetch_one(
        measurement_db,
        "SELECT batch_id FROM onu_measurements WHERE batch_id = ?",
        ("b_completed",),
    )
    assert completed_row["batch_id"] == "b_completed"


def test_process_completed_batch_writes_current_off_snapshot_json(db_paths):
    measurement_db, source_db = db_paths
    subscriber_key = "HNI.STY.STY.OLT.AL.2.1_1-1-1:1"
    insert_danhba_row(source_db, subscriber_key, "TB001")

    for batch_id, status, when in [
        ("b1", "ON", datetime(2026, 4, 24, 8, 0, 0)),
        ("b2", "ON", datetime(2026, 4, 24, 8, 5, 0)),
        ("b3", "OFF", datetime(2026, 4, 24, 8, 10, 0)),
        ("b4", "OFF", datetime(2026, 4, 24, 8, 15, 0)),
    ]:
        insert_measurement_row(measurement_db, subscriber_key, batch_id, status, when)
        result = run_batch(measurement_db, source_db, batch_id, when, send_notifications=False)

    snapshot_file = Path(result["current_off_snapshot_file"])
    payload = json.loads(snapshot_file.read_text(encoding="utf-8"))

    assert result["current_off_snapshot_total"] == 1
    assert result["current_off_snapshot_active"] == 1
    assert payload["batch_id"] == "b4"
    assert payload["summary"]["total_off_subscribers"] == 1
    assert payload["summary"]["active_individual_alerts"] == 1
    assert payload["subscribers"][0]["ma_tb"] == "TB001"
    assert payload["subscribers"][0]["first_off_time"] == "2026-04-24T08:10:00"
    assert payload["subscribers"][0]["duration_minutes"] == 5
    assert payload["subscribers"][0]["duration_text"] == "5 phút"


def test_current_off_snapshot_keeps_suppressed_records_with_reason(db_paths):
    measurement_db, source_db = db_paths
    subscriber_key = "HNI.STY.STY.OLT.AL.2.1_1-2-1:1"
    insert_danhba_row(source_db, subscriber_key, "TB777")

    repo = AlertRepository(str(measurement_db), str(source_db))
    repo.ensure_schema()
    for idx, outage_time in enumerate(
        [
            datetime(2026, 4, 20, 10, 5, 0),
            datetime(2026, 4, 21, 10, 10, 0),
            datetime(2026, 4, 22, 10, 0, 0),
        ],
        start=1,
    ):
        insert_recovery_history_row(
            measurement_db,
            subscriber_key,
            f"history-{idx}",
            "TB777",
            outage_time,
            duration_minutes=15,
        )

    for batch_id, status, when in [
        ("b1", "ON", datetime(2026, 4, 24, 10, 0, 0)),
        ("b2", "ON", datetime(2026, 4, 24, 10, 5, 0)),
        ("b3", "OFF", datetime(2026, 4, 24, 10, 10, 0)),
        ("b4", "OFF", datetime(2026, 4, 24, 10, 15, 0)),
        ("b5", "OFF", datetime(2026, 4, 24, 10, 20, 0)),
    ]:
        insert_measurement_row(measurement_db, subscriber_key, batch_id, status, when)
        result = run_batch(measurement_db, source_db, batch_id, when, send_notifications=False)

    payload = json.loads(Path(result["current_off_snapshot_file"]).read_text(encoding="utf-8"))

    assert payload["summary"]["total_off_subscribers"] == 1
    assert payload["summary"]["active_individual_alerts"] == 0
    assert payload["summary"]["suppressed_by_pattern"] == 1
    assert payload["batch_id"] == "b5"
    assert payload["subscribers"][0]["ma_tb"] == "TB777"
    assert payload["subscribers"][0]["suppressed_by_pattern"] is True
    assert payload["subscribers"][0]["suppression_reason"] == "pattern_exclusion"


def test_current_off_snapshot_marks_wide_area_records_but_keeps_them(db_paths):
    measurement_db, source_db = db_paths
    subscribers = [f"HNI.STY.STY.OLT.AL.2.1_1-1-3:{i}" for i in range(1, 7)]
    for idx, subscriber_key in enumerate(subscribers, start=1):
        insert_danhba_row(source_db, subscriber_key, f"TB{idx:03d}")

    for batch_id, status, when in [
        ("b1", "ON", datetime(2026, 4, 24, 9, 0, 0)),
        ("b2", "ON", datetime(2026, 4, 24, 9, 5, 0)),
        ("b3", "OFF", datetime(2026, 4, 24, 9, 10, 0)),
        ("b4", "OFF", datetime(2026, 4, 24, 9, 15, 0)),
    ]:
        for subscriber_key in subscribers:
            insert_measurement_row(
                measurement_db,
                subscriber_key,
                batch_id,
                status,
                when,
                onu_last_off=when.strftime("%Y-%m-%d %H:%M:%S") if status == "OFF" else "",
            )
        result = run_batch(measurement_db, source_db, batch_id, when, send_notifications=False)

    payload = json.loads(Path(result["current_off_snapshot_file"]).read_text(encoding="utf-8"))

    assert payload["summary"]["total_off_subscribers"] == 6
    assert payload["summary"]["active_individual_alerts"] == 0
    assert payload["summary"]["suppressed_by_wide_area"] == 6
    assert all(item["suppressed_by_wide_area"] for item in payload["subscribers"])


def test_current_off_snapshot_is_cleared_after_recovery(db_paths):
    measurement_db, source_db = db_paths
    subscriber_key = "HNI.STY.STY.OLT.AL.2.1_1-1-9:1"
    insert_danhba_row(source_db, subscriber_key, "TB009")

    for batch_id, status, when in [
        ("b1", "ON", datetime(2026, 4, 24, 8, 0, 0)),
        ("b2", "ON", datetime(2026, 4, 24, 8, 5, 0)),
        ("b3", "OFF", datetime(2026, 4, 24, 8, 10, 0)),
        ("b4", "OFF", datetime(2026, 4, 24, 8, 15, 0)),
        ("b5", "ON", datetime(2026, 4, 24, 8, 20, 0)),
    ]:
        insert_measurement_row(measurement_db, subscriber_key, batch_id, status, when)
        result = run_batch(measurement_db, source_db, batch_id, when, send_notifications=False)

    payload = json.loads(Path(result["current_off_snapshot_file"]).read_text(encoding="utf-8"))

    assert payload["batch_id"] == "b5"
    assert payload["summary"]["total_off_subscribers"] == 0
    assert payload["subscribers"] == []
