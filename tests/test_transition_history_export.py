import csv
import sqlite3
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from transition_history_export import (
    build_summary_rows,
    collect_qualified_transition_rows,
    export_transition_history_csv,
)


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


def create_measurement_db(path):
    with sqlite3.connect(path) as conn:
        conn.executescript(RAW_SCHEMA)
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
        conn.execute(
            """
            CREATE TABLE outage_alerts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                subscriber_key TEXT NOT NULL,
                parent_port_key TEXT NOT NULL,
                batch_id TEXT NOT NULL,
                ma_tb TEXT,
                ten_tb TEXT,
                ma_men TEXT,
                olt_name TEXT,
                doi_vt TEXT,
                diachi_ld TEXT,
                dienthoai_lh TEXT,
                ten_nvkt_db TEXT,
                first_on_time DATETIME,
                first_off_time DATETIME,
                alert_time DATETIME NOT NULL,
                off_duration_minutes INTEGER DEFAULT 0,
                consecutive_on_count INTEGER DEFAULT 0,
                notification_sent BOOLEAN DEFAULT FALSE,
                notification_time DATETIME,
                suppressed_by_pattern BOOLEAN DEFAULT FALSE,
                suppressed_by_wide_area BOOLEAN DEFAULT FALSE,
                suppression_reason TEXT
            )
            """
        )
        conn.commit()


def insert_danhba_row(source_db, subscriber_key, ma_tb, ten_tb):
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
                "NVKT A",
                "To VT 1",
            ),
        )
        conn.commit()


def insert_measurement_row(measurement_db, subscriber_key, batch_id, status, measured_at):
    olt_name, rest = subscriber_key.split("_", 1)
    port_part, onu_index = rest.split(":", 1)
    frame_no, slot_no, port_no = [int(part) for part in port_part.split("-")]
    with sqlite3.connect(measurement_db) as conn:
        conn.execute(
            """
            INSERT INTO onu_measurements (
                "Cổng", batch_id, onuLastOff, onuLastOn, onuStatusStr, frameNo, slotNo, portNo, onuIndex,
                accountFiber, NgayDo, ThoiGianDo
            ) VALUES (?, ?, '', '', ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                subscriber_key,
                batch_id,
                status,
                frame_no,
                slot_no,
                port_no,
                int(onu_index),
                f"acc-{onu_index}",
                measured_at.strftime("%Y-%m-%d"),
                measured_at.strftime("%H:%M:%S"),
            ),
        )
        conn.commit()


def insert_wide_area(measurement_db, subscriber_key, batch_id, alert_time):
    parent_port_key = subscriber_key.rsplit(":", 1)[0]
    olt_name = subscriber_key.split("_", 1)[0]
    with sqlite3.connect(measurement_db) as conn:
        conn.execute(
            """
            INSERT INTO wide_area_alerts (
                batch_id, parent_port_key, olt_name, port, subscriber_count,
                subscriber_keys_json, subscriber_summary_json, doi_vt, alert_time
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                batch_id,
                parent_port_key,
                olt_name,
                subscriber_key,
                1,
                f'["{subscriber_key}"]',
                "[]",
                "To VT 1",
                alert_time.isoformat(),
            ),
        )
        conn.execute(
            """
            INSERT INTO outage_alerts (
                subscriber_key, parent_port_key, batch_id, alert_time, suppressed_by_wide_area
            ) VALUES (?, ?, ?, ?, 1)
            """,
            (
                subscriber_key,
                parent_port_key,
                batch_id,
                alert_time.isoformat(),
            ),
        )
        conn.commit()


def read_csv_rows(path):
    with path.open(newline="", encoding="utf-8-sig") as file_obj:
        return list(csv.DictReader(file_obj))


def test_collect_transitions_filters_out_wide_area_outages(tmp_path):
    measurement_db = tmp_path / "onu_measurements.db"
    source_db = tmp_path / "database.db"
    create_measurement_db(measurement_db)
    create_source_db(source_db)

    subscriber_key = "HNI.STY.STY.OLT.AL.2.1_1-1-1:1"
    insert_danhba_row(source_db, subscriber_key, "TB001", "Thue bao 1")

    started_at = datetime(2026, 4, 24, 8, 0, 0)
    statuses = ["ON", "OFF", "ON", "OFF", "ON", "OFF", "ON", "OFF", "ON"]
    for idx, status in enumerate(statuses, start=1):
        when = started_at + timedelta(minutes=5 * (idx - 1))
        insert_measurement_row(measurement_db, subscriber_key, f"b{idx}", status, when)

    insert_wide_area(measurement_db, subscriber_key, "b4", started_at + timedelta(minutes=15))

    rows = collect_qualified_transition_rows(str(measurement_db), str(source_db))

    assert len(rows) == 6
    assert {row["ma_tb"] for row in rows} == {"TB001"}
    assert {row["transition_type"] for row in rows} == {"ON->OFF", "OFF->ON"}
    assert {row["on_to_off_count"] for row in rows} == {3}
    assert {row["off_to_on_count"] for row in rows} == {3}
    assert "b4" not in {row["to_batch_id"] for row in rows}
    assert "b5" not in {row["to_batch_id"] for row in rows}
    on_to_off_rows = [row for row in rows if row["transition_type"] == "ON->OFF"]
    off_to_on_rows = [row for row in rows if row["transition_type"] == "OFF->ON"]
    assert {row["cycle_duration_hours"] for row in on_to_off_rows} == {5 / 60}
    assert {row["cycle_duration_hours"] for row in off_to_on_rows} == {5 / 60}


def test_export_transition_csv_only_keeps_subscribers_meeting_both_thresholds(tmp_path):
    measurement_db = tmp_path / "onu_measurements.db"
    source_db = tmp_path / "database.db"
    output_path = tmp_path / "transition_history.csv"
    create_measurement_db(measurement_db)
    create_source_db(source_db)

    qualified_key = "HNI.STY.STY.OLT.AL.2.1_1-1-1:1"
    unqualified_key = "HNI.STY.STY.OLT.AL.2.1_1-1-1:2"
    insert_danhba_row(source_db, qualified_key, "TB001", "Thue bao 1")
    insert_danhba_row(source_db, unqualified_key, "TB002", "Thue bao 2")

    started_at = datetime(2026, 4, 24, 9, 0, 0)
    qualified_statuses = ["ON", "OFF", "ON", "OFF", "ON", "OFF", "ON"]
    unqualified_statuses = ["ON", "OFF", "ON", "OFF", "ON", "OFF"]

    for idx, status in enumerate(qualified_statuses, start=1):
        when = started_at + timedelta(minutes=5 * (idx - 1))
        insert_measurement_row(measurement_db, qualified_key, f"q{idx}", status, when)

    for idx, status in enumerate(unqualified_statuses, start=1):
        when = started_at + timedelta(minutes=5 * (idx - 1))
        insert_measurement_row(measurement_db, unqualified_key, f"u{idx}", status, when)

    row_count = export_transition_history_csv(str(measurement_db), str(source_db), str(output_path))
    rows = read_csv_rows(output_path)

    assert row_count == 6
    assert len(rows) == 6
    assert {row["ma_tb"] for row in rows} == {'="TB001"'}
    assert {row["on_to_off_count"] for row in rows} == {"3"}
    assert {row["off_to_on_count"] for row in rows} == {"3"}


def test_build_summary_rows_returns_one_row_per_subscriber_with_latest_transition(tmp_path):
    measurement_db = tmp_path / "onu_measurements.db"
    source_db = tmp_path / "database.db"
    create_measurement_db(measurement_db)
    create_source_db(source_db)

    subscriber_key = "HNI.STY.STY.OLT.AL.2.1_1-1-1:1"
    insert_danhba_row(source_db, subscriber_key, "TB001", "Thue bao 1")

    started_at = datetime(2026, 4, 24, 9, 0, 0)
    statuses = ["ON", "OFF", "ON", "OFF", "ON", "OFF", "ON"]
    for idx, status in enumerate(statuses, start=1):
        when = started_at + timedelta(minutes=5 * (idx - 1))
        insert_measurement_row(measurement_db, subscriber_key, f"b{idx}", status, when)

    transition_rows = collect_qualified_transition_rows(str(measurement_db), str(source_db))
    summary_rows = build_summary_rows(transition_rows)

    assert len(summary_rows) == 1
    summary = summary_rows[0]
    assert summary["ma_tb"] == "TB001"
    assert summary["subscriber_key"] == subscriber_key
    assert summary["on_to_off_count"] == 3
    assert summary["off_to_on_count"] == 3
    assert summary["total_valid_transitions"] == 6
    assert summary["latest_transition_type"] == "OFF->ON"
    assert summary["latest_transition_time"] == "2026-04-24T09:30:00"
    assert summary["latest_cycle_duration_hours"] == 5 / 60
