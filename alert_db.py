import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence

try:
    from .alert_models import OutageAlert, RecoveryAlert, WideAreaAlert
except ImportError:
    from alert_models import OutageAlert, RecoveryAlert, WideAreaAlert


def _dt(value):
    if not value:
        return None
    if isinstance(value, datetime):
        return value
    return datetime.fromisoformat(str(value))


def _iso(value):
    if not value:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def _chunks(values: Sequence, size: int = 900):
    for start in range(0, len(values), size):
        yield values[start : start + size]


class AlertRepository:
    def __init__(self, db_path: str, source_db_path: str):
        self.db_path = str(db_path)
        self.source_db_path = str(source_db_path)

    def connect(self):
        conn = sqlite3.connect(self.db_path, timeout=60)
        conn.row_factory = sqlite3.Row
        return conn

    def _ensure_column(self, conn, table_name: str, column_name: str, definition: str):
        existing_columns = {
            row["name"]
            for row in conn.execute(f"PRAGMA table_info({table_name})").fetchall()
        }
        if column_name in existing_columns:
            return
        conn.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {definition}")

    def ensure_schema(self):
        with self.connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS measurement_batches (
                    batch_id TEXT PRIMARY KEY,
                    started_at DATETIME NOT NULL,
                    finished_at DATETIME,
                    expected_ports INTEGER DEFAULT 0,
                    processed_ports INTEGER DEFAULT 0,
                    success_ports INTEGER DEFAULT 0,
                    empty_ports INTEGER DEFAULT 0,
                    filtered_ports INTEGER DEFAULT 0,
                    error_ports INTEGER DEFAULT 0,
                    status TEXT NOT NULL,
                    notes TEXT
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS subscriber_status_state (
                    subscriber_key TEXT PRIMARY KEY,
                    parent_port_key TEXT NOT NULL,
                    ma_tb TEXT,
                    ten_tb TEXT,
                    ma_men TEXT,
                    olt_name TEXT,
                    doi_vt TEXT,
                    diachi_ld TEXT,
                    dienthoai_lh TEXT,
                    ten_nvkt_db TEXT,
                    account_fiber TEXT,
                    current_state TEXT NOT NULL,
                    last_status TEXT,
                    consecutive_on_count INTEGER DEFAULT 0,
                    consecutive_off_count INTEGER DEFAULT 0,
                    first_on_time DATETIME,
                    first_off_time DATETIME,
                    alert_sent_time DATETIME,
                    recovery_sent_time DATETIME,
                    last_batch_id TEXT,
                    last_measure_time DATETIME,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS outage_alerts (
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
            conn.execute(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS idx_outage_alerts_batch_subscriber
                ON outage_alerts(batch_id, subscriber_key)
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS recovery_alerts (
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
                CREATE UNIQUE INDEX IF NOT EXISTS idx_recovery_alerts_batch_subscriber
                ON recovery_alerts(batch_id, subscriber_key)
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_recovery_alerts_subscriber_outage
                ON recovery_alerts(subscriber_key, outage_time)
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS wide_area_alerts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    batch_id TEXT NOT NULL,
                    parent_port_key TEXT NOT NULL,
                    olt_name TEXT NOT NULL,
                    port TEXT NOT NULL,
                    subscriber_count INTEGER NOT NULL,
                    incident_type TEXT DEFAULT 'wide_area',
                    subscriber_keys_json TEXT NOT NULL,
                    subscriber_summary_json TEXT NOT NULL,
                    doi_vt TEXT,
                    alert_time DATETIME NOT NULL,
                    first_off_time DATETIME,
                    off_duration_minutes INTEGER DEFAULT 0,
                    notification_sent BOOLEAN DEFAULT FALSE,
                    notification_time DATETIME
                )
                """
            )
            self._ensure_column(conn, "wide_area_alerts", "first_off_time", "DATETIME")
            self._ensure_column(conn, "wide_area_alerts", "off_duration_minutes", "INTEGER DEFAULT 0")
            self._ensure_column(conn, "wide_area_alerts", "incident_type", "TEXT DEFAULT 'wide_area'")
            conn.execute(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS idx_wide_area_alerts_batch_port
                ON wide_area_alerts(batch_id, parent_port_key)
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS pattern_exclusion_list (
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
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_exclusion_active ON pattern_exclusion_list(is_active)"
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS notification_runtime_state (
                    key TEXT PRIMARY KEY,
                    value TEXT,
                    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            conn.commit()

    def get_batch_status(self, batch_id: str) -> Optional[str]:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT status FROM measurement_batches WHERE batch_id = ?",
                (batch_id,),
            ).fetchone()
            return row["status"] if row else None

    def mark_batch_started(self, batch_id: str, started_at: datetime, expected_ports: int):
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO measurement_batches(batch_id, started_at, expected_ports, status)
                VALUES (?, ?, ?, 'running')
                ON CONFLICT(batch_id) DO UPDATE SET
                    started_at = excluded.started_at,
                    expected_ports = excluded.expected_ports,
                    status = 'running'
                """,
                (batch_id, _iso(started_at), expected_ports),
            )
            conn.commit()

    def mark_batch_completed(
        self,
        batch_id: str,
        finished_at: datetime,
        processed_ports: int,
        success_ports: int,
        empty_ports: int,
        filtered_ports: int,
        error_ports: int,
        notes: str = "",
    ):
        with self.connect() as conn:
            conn.execute(
                """
                UPDATE measurement_batches
                SET finished_at = ?, processed_ports = ?, success_ports = ?, empty_ports = ?,
                    filtered_ports = ?, error_ports = ?, notes = ?, status = 'completed'
                WHERE batch_id = ?
                """,
                (
                    _iso(finished_at),
                    processed_ports,
                    success_ports,
                    empty_ports,
                    filtered_ports,
                    error_ports,
                    notes,
                    batch_id,
                ),
            )
            conn.commit()

    def mark_batch_alerted(self, batch_id: str):
        with self.connect() as conn:
            conn.execute(
                "UPDATE measurement_batches SET status = 'alerted' WHERE batch_id = ?",
                (batch_id,),
            )
            conn.commit()

    def delete_incomplete_batches(self) -> List[str]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT batch_id FROM measurement_batches WHERE status = 'running' ORDER BY started_at"
            ).fetchall()
            batch_ids = [row["batch_id"] for row in rows]
            if not batch_ids:
                return []

            placeholders = ",".join("?" for _ in batch_ids)
            conn.execute(
                f'DELETE FROM onu_measurements WHERE batch_id IN ({placeholders})',
                batch_ids,
            )
            conn.execute(
                f"DELETE FROM outage_alerts WHERE batch_id IN ({placeholders})",
                batch_ids,
            )
            conn.execute(
                f"DELETE FROM recovery_alerts WHERE batch_id IN ({placeholders})",
                batch_ids,
            )
            conn.execute(
                f"DELETE FROM wide_area_alerts WHERE batch_id IN ({placeholders})",
                batch_ids,
            )
            conn.execute(
                f"DELETE FROM measurement_batches WHERE batch_id IN ({placeholders})",
                batch_ids,
            )
            conn.commit()
            return batch_ids

    def fetch_batch_snapshot_rows(self, batch_id: str) -> List[Dict]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT "Cổng" AS subscriber_key, batch_id, onuLastOff, onuLastOn, onuStatusStr, accountFiber,
                       NgayDo, ThoiGianDo
                FROM onu_measurements
                WHERE batch_id = ?
                ORDER BY subscriber_key
                """,
                (batch_id,),
            ).fetchall()
            return [dict(row) for row in rows]

    def fetch_metadata_map(self, subscriber_keys: Sequence[str]) -> Dict[str, Dict]:
        if not subscriber_keys:
            return {}
        source_path = Path(self.source_db_path)
        if not source_path.exists():
            return {}
        placeholders = ",".join("?" for _ in subscriber_keys)
        with sqlite3.connect(source_path, timeout=60) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                f"""
                SELECT sub, Ma_Tb, Ma_Men, Ten_Tb, DIACHI_LD, DIENTHOAI_LH, TEN_NVKT_DB, DOI_VT
                FROM danhba
                WHERE sub IN ({placeholders})
                """,
                tuple(subscriber_keys),
            ).fetchall()
            return {row["sub"]: dict(row) for row in rows}

    def fetch_authoritative_sub_by_ma_tb(self, ma_tbs: Sequence[str]) -> Dict[str, str]:
        unique_ma_tbs = sorted(set(str(ma_tb).strip() for ma_tb in ma_tbs if str(ma_tb or "").strip()))
        if not unique_ma_tbs:
            return {}
        source_path = Path(self.source_db_path)
        if not source_path.exists():
            return {}

        subs_by_ma_tb: Dict[str, set] = {}
        with sqlite3.connect(source_path, timeout=60) as conn:
            conn.row_factory = sqlite3.Row
            for ma_tb_chunk in _chunks(unique_ma_tbs):
                placeholders = ",".join("?" for _ in ma_tb_chunk)
                rows = conn.execute(
                    f"""
                    SELECT COALESCE(Ma_Tb, '') AS ma_tb, COALESCE(sub, '') AS sub
                    FROM danhba
                    WHERE Ma_Tb IN ({placeholders})
                    ORDER BY ma_tb, sub
                    """,
                    tuple(ma_tb_chunk),
                ).fetchall()
                for row in rows:
                    ma_tb = str(row["ma_tb"] or "").strip()
                    sub = str(row["sub"] or "").strip()
                    if ma_tb and sub:
                        subs_by_ma_tb.setdefault(ma_tb, set()).add(sub)
        return {
            ma_tb: next(iter(subs))
            for ma_tb, subs in subs_by_ma_tb.items()
            if len(subs) == 1
        }

    def get_state_row(self, subscriber_key: str) -> Optional[Dict]:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM subscriber_status_state WHERE subscriber_key = ?",
                (subscriber_key,),
            ).fetchone()
            return dict(row) if row else None

    def fetch_state_map(self, subscriber_keys: Sequence[str]) -> Dict[str, Dict]:
        unique_keys = sorted(set(key for key in subscriber_keys if key))
        if not unique_keys:
            return {}

        rows = []
        with self.connect() as conn:
            for key_chunk in _chunks(unique_keys):
                placeholders = ",".join("?" for _ in key_chunk)
                rows.extend(
                    conn.execute(
                        f"""
                        SELECT *
                        FROM subscriber_status_state
                        WHERE subscriber_key IN ({placeholders})
                        """,
                        tuple(key_chunk),
                    ).fetchall()
                )
        return {row["subscriber_key"]: dict(row) for row in rows}

    def fetch_latest_port_recovery_times(
        self,
        parent_port_keys: Sequence[str],
        before_time: datetime,
        threshold: int,
    ) -> Dict[str, datetime]:
        unique_keys = sorted(set(key for key in parent_port_keys if key))
        if not unique_keys or not before_time:
            return {}

        recovery_times: Dict[str, datetime] = {}
        parent_expr = (
            "CASE WHEN instr(\"Cổng\", ':') > 0 "
            "THEN substr(\"Cổng\", 1, instr(\"Cổng\", ':') - 1) "
            "ELSE \"Cổng\" END"
        )
        status_expr = "UPPER(COALESCE(onuStatusStr, ''))"
        with self.connect() as conn:
            for key_chunk in _chunks(unique_keys):
                placeholders = ",".join("?" for _ in key_chunk)
                rows = conn.execute(
                    f"""
                    SELECT parent_port_key, MAX(batch_measured_at) AS recovered_at
                    FROM (
                        SELECT
                            {parent_expr} AS parent_port_key,
                            batch_id,
                            MAX(NgayDo || 'T' || ThoiGianDo) AS batch_measured_at,
                            SUM(
                                CASE
                                    WHEN {status_expr} = 'PORT_DOWN' OR {status_expr} LIKE '%OFF%' THEN 1
                                    ELSE 0
                                END
                            ) AS off_count
                        FROM onu_measurements
                        WHERE {parent_expr} IN ({placeholders})
                        GROUP BY parent_port_key, batch_id
                    )
                    WHERE off_count <= ? AND batch_measured_at < ?
                    GROUP BY parent_port_key
                    """,
                    (*key_chunk, threshold, _iso(before_time)),
                ).fetchall()
                for row in rows:
                    recovered_at = _dt(row["recovered_at"])
                    if recovered_at:
                        recovery_times[row["parent_port_key"]] = recovered_at
        return recovery_times

    def _state_params(self, state: Dict) -> tuple:
        return (
            state["subscriber_key"],
            state["parent_port_key"],
            state.get("ma_tb", ""),
            state.get("ten_tb", ""),
            state.get("ma_men", ""),
            state.get("olt_name", ""),
            state.get("doi_vt", ""),
            state.get("diachi_ld", ""),
            state.get("dienthoai_lh", ""),
            state.get("ten_nvkt_db", ""),
            state.get("account_fiber", ""),
            state["current_state"],
            state.get("last_status", ""),
            state.get("consecutive_on_count", 0),
            state.get("consecutive_off_count", 0),
            _iso(state.get("first_on_time")),
            _iso(state.get("first_off_time")),
            _iso(state.get("alert_sent_time")),
            _iso(state.get("recovery_sent_time")),
            state.get("last_batch_id", ""),
            _iso(state.get("last_measure_time")),
        )

    def _save_state_sql(self) -> str:
        return """
            INSERT INTO subscriber_status_state (
                subscriber_key, parent_port_key, ma_tb, ten_tb, ma_men, olt_name,
                doi_vt, diachi_ld, dienthoai_lh, ten_nvkt_db, account_fiber,
                current_state, last_status, consecutive_on_count, consecutive_off_count,
                first_on_time, first_off_time, alert_sent_time, recovery_sent_time,
                last_batch_id, last_measure_time, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(subscriber_key) DO UPDATE SET
                parent_port_key = excluded.parent_port_key,
                ma_tb = excluded.ma_tb,
                ten_tb = excluded.ten_tb,
                ma_men = excluded.ma_men,
                olt_name = excluded.olt_name,
                doi_vt = excluded.doi_vt,
                diachi_ld = excluded.diachi_ld,
                dienthoai_lh = excluded.dienthoai_lh,
                ten_nvkt_db = excluded.ten_nvkt_db,
                account_fiber = excluded.account_fiber,
                current_state = excluded.current_state,
                last_status = excluded.last_status,
                consecutive_on_count = excluded.consecutive_on_count,
                consecutive_off_count = excluded.consecutive_off_count,
                first_on_time = excluded.first_on_time,
                first_off_time = excluded.first_off_time,
                alert_sent_time = excluded.alert_sent_time,
                recovery_sent_time = excluded.recovery_sent_time,
                last_batch_id = excluded.last_batch_id,
                last_measure_time = excluded.last_measure_time,
                updated_at = CURRENT_TIMESTAMP
            """

    def save_state(self, state: Dict):
        with self.connect() as conn:
            conn.execute(self._save_state_sql(), self._state_params(state))
            conn.commit()

    def save_states_bulk(self, states: Sequence[Dict]):
        if not states:
            return
        with self.connect() as conn:
            conn.executemany(
                self._save_state_sql(),
                [self._state_params(state) for state in states],
            )
            conn.commit()

    def insert_outage_alert(self, alert: OutageAlert) -> Optional[int]:
        with self.connect() as conn:
            cursor = conn.execute(self._insert_outage_alert_sql(), self._outage_alert_params(alert))
            conn.commit()
            if cursor.lastrowid:
                return cursor.lastrowid
            row = conn.execute(
                "SELECT id FROM outage_alerts WHERE batch_id = ? AND subscriber_key = ?",
                (alert.batch_id, alert.subscriber_key),
            ).fetchone()
            return row["id"] if row else None

    def _insert_outage_alert_sql(self) -> str:
        return """
            INSERT OR IGNORE INTO outage_alerts (
                subscriber_key, parent_port_key, batch_id, ma_tb, ten_tb, ma_men,
                olt_name, doi_vt, diachi_ld, dienthoai_lh, ten_nvkt_db,
                first_on_time, first_off_time, alert_time, off_duration_minutes,
                consecutive_on_count, notification_sent, notification_time,
                suppressed_by_pattern, suppressed_by_wide_area, suppression_reason
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """

    def _outage_alert_params(self, alert: OutageAlert) -> tuple:
        return (
            alert.subscriber_key,
            alert.parent_port_key,
            alert.batch_id,
            alert.ma_tb,
            alert.ten_tb,
            alert.ma_men,
            alert.olt_name,
            alert.doi_vt,
            alert.diachi_ld,
            alert.dienthoai_lh,
            alert.ten_nvkt_db,
            _iso(alert.first_on_time),
            _iso(alert.first_off_time),
            _iso(alert.alert_time),
            alert.off_duration_minutes,
            alert.consecutive_on_count,
            int(alert.notification_sent),
            _iso(alert.notification_time),
            int(alert.suppressed_by_pattern),
            int(alert.suppressed_by_wide_area),
            alert.suppression_reason,
        )

    def insert_outage_alerts_bulk(self, alerts: Sequence[OutageAlert]):
        if not alerts:
            return
        with self.connect() as conn:
            conn.executemany(
                self._insert_outage_alert_sql(),
                [self._outage_alert_params(alert) for alert in alerts],
            )
            conn.commit()

    def insert_recovery_alert(self, alert: RecoveryAlert) -> Optional[int]:
        with self.connect() as conn:
            cursor = conn.execute(self._insert_recovery_alert_sql(), self._recovery_alert_params(alert))
            conn.commit()
            if cursor.lastrowid:
                return cursor.lastrowid
            row = conn.execute(
                "SELECT id FROM recovery_alerts WHERE batch_id = ? AND subscriber_key = ?",
                (alert.batch_id, alert.subscriber_key),
            ).fetchone()
            return row["id"] if row else None

    def _insert_recovery_alert_sql(self) -> str:
        return """
            INSERT OR IGNORE INTO recovery_alerts (
                subscriber_key, parent_port_key, batch_id, ma_tb, ten_tb, olt_name,
                doi_vt, diachi_ld, dienthoai_lh, ten_nvkt_db,
                outage_time, recovery_time, outage_duration_minutes,
                notification_sent, notification_time
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """

    def _recovery_alert_params(self, alert: RecoveryAlert) -> tuple:
        return (
            alert.subscriber_key,
            alert.parent_port_key,
            alert.batch_id,
            alert.ma_tb,
            alert.ten_tb,
            alert.olt_name,
            alert.doi_vt,
            alert.diachi_ld,
            alert.dienthoai_lh,
            alert.ten_nvkt_db,
            _iso(alert.outage_time),
            _iso(alert.recovery_time),
            alert.outage_duration_minutes,
            int(alert.notification_sent),
            _iso(alert.notification_time),
        )

    def insert_recovery_alerts_bulk(self, alerts: Sequence[RecoveryAlert]):
        if not alerts:
            return
        with self.connect() as conn:
            conn.executemany(
                self._insert_recovery_alert_sql(),
                [self._recovery_alert_params(alert) for alert in alerts],
            )
            conn.commit()

    def persist_state_machine_results(
        self,
        states: Sequence[Dict],
        outage_alerts: Sequence[OutageAlert],
        recovery_alerts: Sequence[RecoveryAlert],
    ):
        if not states and not outage_alerts and not recovery_alerts:
            return
        with self.connect() as conn:
            if states:
                conn.executemany(
                    self._save_state_sql(),
                    [self._state_params(state) for state in states],
                )
            if outage_alerts:
                conn.executemany(
                    self._insert_outage_alert_sql(),
                    [self._outage_alert_params(alert) for alert in outage_alerts],
                )
            if recovery_alerts:
                conn.executemany(
                    self._insert_recovery_alert_sql(),
                    [self._recovery_alert_params(alert) for alert in recovery_alerts],
                )
            conn.commit()

    def insert_wide_area_alert(self, alert: WideAreaAlert) -> Optional[int]:
        with self.connect() as conn:
            cursor = conn.execute(
                """
                INSERT OR IGNORE INTO wide_area_alerts (
                    batch_id, parent_port_key, olt_name, port, subscriber_count, incident_type,
                    subscriber_keys_json, subscriber_summary_json, doi_vt,
                    alert_time, first_off_time, off_duration_minutes,
                    notification_sent, notification_time
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    alert.batch_id,
                    alert.parent_port_key,
                    alert.olt_name,
                    alert.port,
                    alert.subscriber_count,
                    alert.incident_type,
                    json.dumps(alert.subscriber_keys, ensure_ascii=False),
                    json.dumps(alert.subscriber_list, ensure_ascii=False),
                    alert.doi_vt,
                    _iso(alert.alert_time),
                    _iso(alert.first_off_time),
                    alert.off_duration_minutes,
                    int(alert.notification_sent),
                    _iso(alert.notification_time),
                ),
            )
            conn.commit()
            if cursor.lastrowid:
                return cursor.lastrowid
            row = conn.execute(
                "SELECT id FROM wide_area_alerts WHERE batch_id = ? AND parent_port_key = ?",
                (alert.batch_id, alert.parent_port_key),
            ).fetchone()
            return row["id"] if row else None

    def suppress_outage_alerts(self, batch_id: str, subscriber_keys: Iterable[str], reason: str):
        subscriber_keys = list(subscriber_keys)
        if not subscriber_keys:
            return
        placeholders = ",".join("?" for _ in subscriber_keys)
        field = "suppressed_by_wide_area" if reason == "wide_area" else "suppressed_by_pattern"
        with self.connect() as conn:
            conn.execute(
                f"""
                UPDATE outage_alerts
                SET {field} = 1,
                    suppression_reason = CASE
                        WHEN suppression_reason IS NULL OR suppression_reason = '' THEN ?
                        WHEN instr(suppression_reason, ?) > 0 THEN suppression_reason
                        ELSE suppression_reason || ',' || ?
                    END
                WHERE batch_id = ? AND subscriber_key IN ({placeholders})
                """,
                (reason, reason, reason, batch_id, *subscriber_keys),
            )
            conn.commit()

    def list_unsent_outage_alerts(self, batch_id: str) -> List[OutageAlert]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM outage_alerts
                WHERE batch_id = ? AND notification_sent = FALSE
                  AND suppressed_by_pattern = FALSE
                  AND suppressed_by_wide_area = FALSE
                ORDER BY alert_time, subscriber_key
                """,
                (batch_id,),
            ).fetchall()
            return [self._row_to_outage_alert(row) for row in rows]

    def list_unsent_recovery_alerts(self, batch_id: str) -> List[RecoveryAlert]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM recovery_alerts
                WHERE batch_id = ? AND notification_sent = FALSE
                ORDER BY recovery_time, subscriber_key
                """,
                (batch_id,),
            ).fetchall()
            return [self._row_to_recovery_alert(row) for row in rows]

    def list_unsent_wide_area_alerts(self, batch_id: str) -> List[WideAreaAlert]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM wide_area_alerts
                WHERE batch_id = ? AND notification_sent = FALSE
                ORDER BY alert_time, parent_port_key
                """,
                (batch_id,),
            ).fetchall()
            return [self._row_to_wide_area_alert(row) for row in rows]

    def mark_outage_alerts_sent(self, ids: Iterable[int]):
        ids = [id_ for id_ in ids if id_]
        if not ids:
            return
        placeholders = ",".join("?" for _ in ids)
        with self.connect() as conn:
            conn.execute(
                f"""
                UPDATE outage_alerts
                SET notification_sent = TRUE, notification_time = ?
                WHERE id IN ({placeholders})
                """,
                (_iso(datetime.now()), *ids),
            )
            conn.commit()

    def mark_recovery_alerts_sent(self, ids: Iterable[int]):
        ids = [id_ for id_ in ids if id_]
        if not ids:
            return
        placeholders = ",".join("?" for _ in ids)
        with self.connect() as conn:
            conn.execute(
                f"""
                UPDATE recovery_alerts
                SET notification_sent = TRUE, notification_time = ?
                WHERE id IN ({placeholders})
                """,
                (_iso(datetime.now()), *ids),
            )
            conn.commit()

    def mark_wide_area_alerts_sent(self, ids: Iterable[int]):
        ids = [id_ for id_ in ids if id_]
        if not ids:
            return
        placeholders = ",".join("?" for _ in ids)
        with self.connect() as conn:
            conn.execute(
                f"""
                UPDATE wide_area_alerts
                SET notification_sent = TRUE, notification_time = ?
                WHERE id IN ({placeholders})
                """,
                (_iso(datetime.now()), *ids),
            )
            conn.commit()

    def get_runtime_state(self, key: str, default: Optional[str] = None) -> Optional[str]:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT value FROM notification_runtime_state WHERE key = ?",
                (key,),
            ).fetchone()
            return row["value"] if row else default

    def set_runtime_state(self, key: str, value: Optional[str]):
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO notification_runtime_state(key, value, updated_at)
                VALUES (?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(key) DO UPDATE SET
                    value = excluded.value,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (key, value),
            )
            conn.commit()

    def advance_notification_cycle(self, cycle_name: str, batch_id: str) -> int:
        counter_key = f"{cycle_name}_counter"
        last_batch_key = f"{cycle_name}_last_batch_id"

        with self.connect() as conn:
            last_batch_row = conn.execute(
                "SELECT value FROM notification_runtime_state WHERE key = ?",
                (last_batch_key,),
            ).fetchone()
            if last_batch_row and last_batch_row["value"] == batch_id:
                counter_row = conn.execute(
                    "SELECT value FROM notification_runtime_state WHERE key = ?",
                    (counter_key,),
                ).fetchone()
                try:
                    return int(counter_row["value"]) if counter_row and counter_row["value"] is not None else 0
                except (TypeError, ValueError):
                    return 0

            counter_row = conn.execute(
                "SELECT value FROM notification_runtime_state WHERE key = ?",
                (counter_key,),
            ).fetchone()
            try:
                counter_value = int(counter_row["value"]) if counter_row and counter_row["value"] is not None else 0
            except (TypeError, ValueError):
                counter_value = 0

            counter_value += 1
            conn.execute(
                """
                INSERT INTO notification_runtime_state(key, value, updated_at)
                VALUES (?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(key) DO UPDATE SET
                    value = excluded.value,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (counter_key, str(counter_value)),
            )
            conn.execute(
                """
                INSERT INTO notification_runtime_state(key, value, updated_at)
                VALUES (?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(key) DO UPDATE SET
                    value = excluded.value,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (last_batch_key, batch_id),
            )
            conn.commit()
            return counter_value

    def upsert_pattern_exclusion(
        self,
        ma_tb: str,
        ten_tb: str,
        pattern_type: str,
        total_events: int,
        pattern_score: float,
        notes: str = "",
    ):
        now = datetime.now().isoformat()
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO pattern_exclusion_list (
                    ma_tb, ten_tb, pattern_type, total_events, pattern_score,
                    first_detected, last_updated, is_active, notes
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?)
                ON CONFLICT(ma_tb) DO UPDATE SET
                    ten_tb = excluded.ten_tb,
                    pattern_type = excluded.pattern_type,
                    total_events = excluded.total_events,
                    pattern_score = excluded.pattern_score,
                    last_updated = excluded.last_updated,
                    is_active = 1,
                    notes = excluded.notes
                """,
                (ma_tb, ten_tb, pattern_type, total_events, pattern_score, now, now, notes),
            )
            conn.commit()

    def get_exclusion_list(self) -> List[str]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT ma_tb FROM pattern_exclusion_list WHERE is_active = 1"
            ).fetchall()
            return [row["ma_tb"] for row in rows]

    def _row_to_outage_alert(self, row: sqlite3.Row) -> OutageAlert:
        return OutageAlert(
            id=row["id"],
            subscriber_key=row["subscriber_key"],
            parent_port_key=row["parent_port_key"],
            batch_id=row["batch_id"],
            ma_tb=row["ma_tb"] or "",
            ten_tb=row["ten_tb"] or "",
            ma_men=row["ma_men"] or "",
            olt_name=row["olt_name"] or "",
            doi_vt=row["doi_vt"] or "",
            diachi_ld=row["diachi_ld"] or "",
            dienthoai_lh=row["dienthoai_lh"] or "",
            ten_nvkt_db=row["ten_nvkt_db"] or "",
            first_on_time=_dt(row["first_on_time"]),
            first_off_time=_dt(row["first_off_time"]),
            alert_time=_dt(row["alert_time"]),
            off_duration_minutes=row["off_duration_minutes"] or 0,
            consecutive_on_count=row["consecutive_on_count"] or 0,
            notification_sent=bool(row["notification_sent"]),
            notification_time=_dt(row["notification_time"]),
            suppressed_by_pattern=bool(row["suppressed_by_pattern"]),
            suppressed_by_wide_area=bool(row["suppressed_by_wide_area"]),
            suppression_reason=row["suppression_reason"] or "",
        )

    def _row_to_recovery_alert(self, row: sqlite3.Row) -> RecoveryAlert:
        return RecoveryAlert(
            id=row["id"],
            subscriber_key=row["subscriber_key"],
            parent_port_key=row["parent_port_key"],
            batch_id=row["batch_id"],
            ma_tb=row["ma_tb"] or "",
            ten_tb=row["ten_tb"] or "",
            olt_name=row["olt_name"] or "",
            doi_vt=row["doi_vt"] or "",
            diachi_ld=row["diachi_ld"] or "",
            dienthoai_lh=row["dienthoai_lh"] or "",
            ten_nvkt_db=row["ten_nvkt_db"] or "",
            outage_time=_dt(row["outage_time"]),
            recovery_time=_dt(row["recovery_time"]),
            outage_duration_minutes=row["outage_duration_minutes"] or 0,
            notification_sent=bool(row["notification_sent"]),
            notification_time=_dt(row["notification_time"]),
        )

    def _row_to_wide_area_alert(self, row: sqlite3.Row) -> WideAreaAlert:
        return WideAreaAlert(
            id=row["id"],
            batch_id=row["batch_id"],
            parent_port_key=row["parent_port_key"],
            olt_name=row["olt_name"],
            port=row["port"],
            subscriber_count=row["subscriber_count"],
            incident_type=(row["incident_type"] or "wide_area"),
            subscriber_keys=json.loads(row["subscriber_keys_json"] or "[]"),
            subscriber_list=json.loads(row["subscriber_summary_json"] or "[]"),
            doi_vt=row["doi_vt"] or "",
            alert_time=_dt(row["alert_time"]),
            first_off_time=_dt(row["first_off_time"]),
            off_duration_minutes=row["off_duration_minutes"] or 0,
            notification_sent=bool(row["notification_sent"]),
            notification_time=_dt(row["notification_time"]),
        )
