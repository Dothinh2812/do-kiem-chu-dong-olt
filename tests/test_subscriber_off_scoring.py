import sqlite3
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from subscriber_off_scoring import (
    LIKELY_INDIVIDUAL_FAULT,
    LIKELY_SELF_POWER_OFF,
    WIDE_AREA_OR_PORT_INCIDENT,
    OFFHistoryEvent,
    OffEventContext,
    classify_off_event,
    get_pattern_exclusion_list,
    score_outage_alerts,
    update_exclusion_table,
)


def history_at_night():
    return [
        OFFHistoryEvent(
            outage_time=datetime(2026, 4, 20, 20, 5, 0),
            recovery_time=datetime(2026, 4, 20, 20, 20, 0),
            outage_duration_minutes=15,
        ),
        OFFHistoryEvent(
            outage_time=datetime(2026, 4, 21, 20, 10, 0),
            recovery_time=datetime(2026, 4, 21, 20, 28, 0),
            outage_duration_minutes=18,
        ),
        OFFHistoryEvent(
            outage_time=datetime(2026, 4, 22, 20, 0, 0),
            recovery_time=datetime(2026, 4, 22, 20, 16, 0),
            outage_duration_minutes=16,
        ),
    ]


def test_classify_repeated_same_hour_normal_duration_as_self_poweroff():
    context = OffEventContext(
        subscriber_key="sub-1",
        parent_port_key="port-1",
        batch_id="b4",
        ma_tb="TB900",
        ten_tb="Khach hang",
        first_off_time=datetime(2026, 4, 23, 20, 8, 0),
        reference_time=datetime(2026, 4, 23, 20, 25, 0),
        same_port_off_count=1,
    )

    result = classify_off_event(context, history_at_night())

    assert result.classification == LIKELY_SELF_POWER_OFF
    assert result.self_poweroff_score >= 70
    assert result.individual_fault_score < 60
    assert result.features["history_event_count"] == 3
    assert result.features["current_off_duration_hours"] == 17 / 60


def test_classify_two_highly_consistent_events_as_self_poweroff():
    context = OffEventContext(
        subscriber_key="sub-1",
        parent_port_key="port-1",
        batch_id="b4",
        ma_tb="TB900",
        ten_tb="Khach hang",
        first_off_time=datetime(2026, 4, 23, 7, 13, 0),
        reference_time=datetime(2026, 4, 23, 9, 13, 0),
        same_port_off_count=1,
    )
    history = [
        OFFHistoryEvent(
            outage_time=datetime(2026, 4, 21, 7, 18, 0),
            recovery_time=datetime(2026, 4, 21, 17, 33, 0),
            outage_duration_minutes=615,
        ),
        OFFHistoryEvent(
            outage_time=datetime(2026, 4, 22, 7, 16, 0),
            recovery_time=datetime(2026, 4, 22, 17, 31, 0),
            outage_duration_minutes=615,
        ),
    ]

    result = classify_off_event(context, history)

    assert result.classification == LIKELY_SELF_POWER_OFF
    assert result.self_poweroff_score >= 70
    assert result.features["history_event_count"] == 2


def test_classify_unusual_long_off_as_individual_fault():
    context = OffEventContext(
        subscriber_key="sub-1",
        parent_port_key="port-1",
        batch_id="b4",
        ma_tb="TB900",
        ten_tb="Khach hang",
        first_off_time=datetime(2026, 4, 23, 9, 0, 0),
        reference_time=datetime(2026, 4, 23, 15, 0, 0),
        same_port_off_count=1,
    )

    result = classify_off_event(context, history_at_night())

    assert result.classification == LIKELY_INDIVIDUAL_FAULT
    assert result.individual_fault_score >= 70
    assert result.self_poweroff_score < 70
    assert result.features["current_off_duration_vs_p90"] > 10
    assert result.alert_eligible is True
    assert result.alert_eligibility_reason == "classified_individual_fault"


def test_no_history_requires_absolute_sixty_minute_duration_for_alert():
    before_threshold = classify_off_event(
        OffEventContext(
            subscriber_key="sub-new",
            parent_port_key="port-1",
            batch_id="b1",
            ma_tb="TBNEW",
            ten_tb="Khach hang moi",
            first_off_time=datetime(2026, 4, 23, 8, 0, 0),
            reference_time=datetime(2026, 4, 23, 8, 59, 0),
            same_port_off_count=1,
        ),
        [],
    )
    at_threshold = classify_off_event(
        OffEventContext(
            subscriber_key="sub-new",
            parent_port_key="port-1",
            batch_id="b2",
            ma_tb="TBNEW",
            ten_tb="Khach hang moi",
            first_off_time=datetime(2026, 4, 23, 8, 0, 0),
            reference_time=datetime(2026, 4, 23, 9, 0, 0),
            same_port_off_count=1,
        ),
        [],
    )

    assert before_threshold.alert_eligible is False
    assert before_threshold.alert_eligibility_reason == "minimum_duration_not_reached"
    assert at_threshold.alert_eligible is True
    assert at_threshold.alert_eligibility_reason == "no_history_absolute_duration"


def test_likely_self_power_off_stays_ineligible_after_duration_threshold():
    context = OffEventContext(
        subscriber_key="sub-pattern",
        parent_port_key="port-1",
        batch_id="b4",
        ma_tb="TBPATTERN",
        ten_tb="Khach hang",
        first_off_time=datetime(2026, 4, 23, 7, 13, 0),
        reference_time=datetime(2026, 4, 23, 9, 13, 0),
        same_port_off_count=1,
    )
    history = [
        OFFHistoryEvent(
            outage_time=datetime(2026, 4, 21, 7, 18, 0),
            recovery_time=datetime(2026, 4, 21, 17, 33, 0),
            outage_duration_minutes=615,
        ),
        OFFHistoryEvent(
            outage_time=datetime(2026, 4, 22, 7, 16, 0),
            recovery_time=datetime(2026, 4, 22, 17, 31, 0),
            outage_duration_minutes=615,
        ),
    ]

    result = classify_off_event(context, history)

    assert result.classification == LIKELY_SELF_POWER_OFF
    assert result.alert_eligible is False
    assert result.alert_eligibility_reason == "likely_self_power_off"


def test_classify_bimodal_off_pattern_matches_common_hours():
    history = [
        OFFHistoryEvent(
            outage_time=datetime(2026, 5, 1, 11, 5, 0),
            recovery_time=datetime(2026, 5, 1, 11, 20, 0),
            outage_duration_minutes=15,
        ),
        OFFHistoryEvent(
            outage_time=datetime(2026, 5, 2, 17, 10, 0),
            recovery_time=datetime(2026, 5, 2, 17, 25, 0),
            outage_duration_minutes=15,
        ),
        OFFHistoryEvent(
            outage_time=datetime(2026, 5, 3, 11, 8, 0),
            recovery_time=datetime(2026, 5, 3, 11, 23, 0),
            outage_duration_minutes=15,
        ),
        OFFHistoryEvent(
            outage_time=datetime(2026, 5, 4, 17, 5, 0),
            recovery_time=datetime(2026, 5, 4, 17, 20, 0),
            outage_duration_minutes=15,
        ),
        OFFHistoryEvent(
            outage_time=datetime(2026, 5, 5, 11, 10, 0),
            recovery_time=datetime(2026, 5, 5, 11, 25, 0),
            outage_duration_minutes=15,
        ),
        OFFHistoryEvent(
            outage_time=datetime(2026, 5, 6, 17, 8, 0),
            recovery_time=datetime(2026, 5, 6, 17, 23, 0),
            outage_duration_minutes=15,
        ),
    ]
    context = OffEventContext(
        subscriber_key="sub-bimodal",
        parent_port_key="port-1",
        batch_id="b1",
        ma_tb="TB700",
        ten_tb="Khach hang",
        first_off_time=datetime(2026, 5, 7, 11, 12, 0),
        reference_time=datetime(2026, 5, 7, 11, 30, 0),
        same_port_off_count=1,
    )

    result = classify_off_event(context, history)

    assert result.classification == LIKELY_SELF_POWER_OFF
    assert result.self_poweroff_score >= 70
    assert result.features["common_off_hours"] == [11, 17]
    assert result.features["current_hour_distance"] <= 1


def test_classify_wide_area_context_as_port_incident():
    context = OffEventContext(
        subscriber_key="sub-1",
        parent_port_key="port-1",
        batch_id="b4",
        ma_tb="TB900",
        ten_tb="Khach hang",
        first_off_time=datetime(2026, 4, 23, 20, 8, 0),
        reference_time=datetime(2026, 4, 23, 20, 25, 0),
        same_port_off_count=8,
        is_wide_area=True,
    )

    result = classify_off_event(context, history_at_night())

    assert result.classification == WIDE_AREA_OR_PORT_INCIDENT
    assert result.wide_area_score == 100
    assert result.confidence == 100


def create_scoring_schema(db_path):
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE measurement_batches (
                batch_id TEXT PRIMARY KEY,
                status TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE onu_measurements (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                "Cổng" TEXT NOT NULL,
                batch_id TEXT NOT NULL,
                onuStatusStr TEXT,
                NgayDo TEXT,
                ThoiGianDo TEXT
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE recovery_alerts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                subscriber_key TEXT NOT NULL,
                parent_port_key TEXT NOT NULL,
                batch_id TEXT NOT NULL,
                ma_tb TEXT,
                ten_tb TEXT,
                olt_name TEXT,
                doi_vt TEXT,
                diachi_ld TEXT,
                dienthoai_lh TEXT,
                ten_nvkt_db TEXT,
                outage_time DATETIME,
                recovery_time DATETIME NOT NULL,
                outage_duration_minutes INTEGER DEFAULT 0,
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
                first_off_time DATETIME,
                alert_time DATETIME NOT NULL,
                suppressed_by_wide_area BOOLEAN DEFAULT FALSE
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE pattern_exclusion_list (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ma_tb TEXT UNIQUE NOT NULL,
                ten_tb TEXT,
                pattern_type TEXT,
                total_events INTEGER,
                pattern_score REAL,
                first_detected DATETIME,
                last_updated DATETIME,
                is_active BOOLEAN DEFAULT 1,
                notes TEXT
            )
            """
        )
        conn.commit()


def insert_history(conn, subscriber_key, ma_tb, offset_days, hour=20, minutes=15):
    outage_time = datetime(2026, 4, 20, hour, 5, 0) + timedelta(days=offset_days)
    recovery_time = outage_time + timedelta(minutes=minutes)
    conn.execute(
        """
        INSERT INTO recovery_alerts (
            subscriber_key, parent_port_key, batch_id, ma_tb, ten_tb, olt_name,
            outage_time, recovery_time, outage_duration_minutes
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            subscriber_key,
            "port-1",
            f"r{offset_days}",
            ma_tb,
            "Khach hang",
            "OLT",
            outage_time.isoformat(),
            recovery_time.isoformat(),
            minutes,
        ),
    )


def insert_outage(conn, subscriber_key, ma_tb, batch_id, first_off_time, parent_port_key="port-1"):
    conn.execute(
        """
        INSERT INTO outage_alerts (
            subscriber_key, parent_port_key, batch_id, ma_tb, ten_tb, first_off_time, alert_time
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            subscriber_key,
            parent_port_key,
            batch_id,
            ma_tb,
            "Khach hang",
            first_off_time,
            first_off_time,
        ),
    )


def insert_batch_measurements(conn, batch_id, off_count, status="alerted"):
    conn.execute(
        "INSERT INTO measurement_batches (batch_id, status) VALUES (?, ?)",
        (batch_id, status),
    )
    rows = [
        (
            f"subscriber-{batch_id}-{idx}",
            batch_id,
            "OFF" if idx < off_count else "ON",
            "2026-04-23",
            "20:00:00",
        )
        for idx in range(off_count)
    ]
    conn.executemany(
        """
        INSERT INTO onu_measurements ("Cổng", batch_id, onuStatusStr, NgayDo, ThoiGianDo)
        VALUES (?, ?, ?, ?, ?)
        """,
        rows,
    )


def test_score_outage_alerts_uses_only_current_batch_and_30_day_history(tmp_path):
    db_path = tmp_path / "onu_measurements.db"
    create_scoring_schema(db_path)

    with sqlite3.connect(db_path) as conn:
        for idx in range(3):
            insert_history(conn, "sub-current", "TB100", idx, hour=20, minutes=15)
            insert_history(conn, "sub-other", "TB200", idx, hour=20, minutes=15)
        old_outage_time = datetime(2026, 2, 1, 20, 5, 0)
        conn.execute(
            """
            INSERT INTO recovery_alerts (
                subscriber_key, parent_port_key, batch_id, ma_tb, ten_tb, olt_name,
                outage_time, recovery_time, outage_duration_minutes
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "sub-current",
                "port-1",
                "old",
                "TB100",
                "Khach hang",
                "OLT",
                old_outage_time.isoformat(),
                (old_outage_time + timedelta(minutes=10)).isoformat(),
                10,
            ),
        )
        insert_outage(conn, "sub-current", "TB100", "b10", "2026-04-23T20:08:00")
        insert_outage(conn, "sub-other", "TB200", "b9", "2026-04-23T20:08:00")
        conn.commit()

    results = score_outage_alerts(
        str(db_path),
        batch_id="b10",
        reference_time=datetime(2026, 4, 23, 20, 25, 0),
    )

    assert [result.subscriber_key for result in results] == ["sub-current"]
    assert results[0].features["history_event_count"] == 3


def test_score_outage_alerts_ignores_wide_area_history_events(tmp_path):
    db_path = tmp_path / "onu_measurements.db"
    create_scoring_schema(db_path)

    with sqlite3.connect(db_path) as conn:
        insert_batch_measurements(conn, "incident-history", 700)
        insert_batch_measurements(conn, "normal-history-1", 700)
        insert_batch_measurements(conn, "normal-history-2", 700)
        insert_batch_measurements(conn, "b10", 700)
        for idx in range(3):
            insert_history(conn, "sub-current", "TB100", idx, hour=20, minutes=15)
        incident_outage_time = datetime(2026, 4, 20, 20, 5, 0).isoformat()
        conn.execute(
            """
            INSERT INTO outage_alerts (
                subscriber_key, parent_port_key, batch_id, ma_tb, ten_tb, first_off_time, alert_time, suppressed_by_wide_area
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "sub-current",
                "port-1",
                "incident-history",
                "TB100",
                "Khach hang",
                incident_outage_time,
                incident_outage_time,
                1,
            ),
        )
        insert_outage(conn, "sub-current", "TB100", "b10", "2026-04-23T20:08:00")
        conn.commit()

    results = score_outage_alerts(
        str(db_path),
        batch_id="b10",
        reference_time=datetime(2026, 4, 23, 20, 25, 0),
    )

    assert results[0].features["history_event_count"] == 2


def test_score_outage_alerts_ignores_major_incident_history_batches(tmp_path):
    db_path = tmp_path / "onu_measurements.db"
    create_scoring_schema(db_path)

    with sqlite3.connect(db_path) as conn:
        insert_batch_measurements(conn, "incident-history", 1000)
        insert_batch_measurements(conn, "normal-history-1", 700)
        insert_batch_measurements(conn, "normal-history-2", 700)
        insert_batch_measurements(conn, "b10", 700)
        for idx in range(3):
            insert_history(conn, "sub-current", "TB100", idx, hour=20, minutes=15)
        incident_outage_time = datetime(2026, 4, 20, 20, 5, 0).isoformat()
        conn.execute(
            """
            INSERT INTO outage_alerts (
                subscriber_key, parent_port_key, batch_id, ma_tb, ten_tb, first_off_time, alert_time
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "sub-current",
                "port-1",
                "incident-history",
                "TB100",
                "Khach hang",
                incident_outage_time,
                incident_outage_time,
            ),
        )
        insert_outage(conn, "sub-current", "TB100", "b10", "2026-04-23T20:08:00")
        conn.commit()

    results = score_outage_alerts(
        str(db_path),
        batch_id="b10",
        reference_time=datetime(2026, 4, 23, 20, 25, 0),
    )

    assert results[0].features["history_event_count"] == 2


def test_update_exclusion_table_scores_likely_self_poweroff_rows(tmp_path):
    db_path = tmp_path / "onu_measurements.db"
    create_scoring_schema(db_path)

    with sqlite3.connect(db_path) as conn:
        for idx, minutes in enumerate([15, 16, 18]):
            insert_history(conn, "sub-good", "TB900", idx, minutes=minutes)
        conn.execute(
            """
            INSERT INTO outage_alerts (
                subscriber_key, parent_port_key, batch_id, ma_tb, ten_tb, first_off_time, alert_time
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "sub-good",
                "port-1",
                "b4",
                "TB900",
                "Khach hang",
                "2026-04-23T20:08:00",
                "2026-04-23T20:10:00",
            ),
        )
        conn.execute(
            """
            INSERT INTO outage_alerts (
                subscriber_key, parent_port_key, batch_id, ma_tb, ten_tb, first_off_time, alert_time
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "sub-bad",
                "port-2",
                "b4",
                "TB901",
                "Khach hang 2",
                "2026-04-23T08:00:00",
                "2026-04-23T08:05:00",
            ),
        )
        conn.commit()

    updated = update_exclusion_table(str(db_path), reference_time=datetime(2026, 4, 23, 20, 25, 0))
    exclusion = get_pattern_exclusion_list(str(db_path))

    assert updated == 1
    assert exclusion == {"TB900"}

    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT pattern_type, total_events, pattern_score, notes FROM pattern_exclusion_list WHERE ma_tb = ?",
            ("TB900",),
        ).fetchone()

    assert row["pattern_type"] == LIKELY_SELF_POWER_OFF
    assert row["total_events"] == 3
    assert row["pattern_score"] >= 0.7
    assert "self_poweroff_score" in row["notes"]
