import argparse
import csv
import json
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from statistics import median
from typing import Dict, Iterable, List, Optional, Sequence, Set, Tuple


DEFAULT_DB_PATH = "onu_measurements.db"
CSV_ENCODING = "utf-8-sig"

WIDE_AREA_OR_PORT_INCIDENT = "WIDE_AREA_OR_PORT_INCIDENT"
LIKELY_SELF_POWER_OFF = "LIKELY_SELF_POWER_OFF"
LIKELY_INDIVIDUAL_FAULT = "LIKELY_INDIVIDUAL_FAULT"
UNCERTAIN = "UNCERTAIN"

MIN_HISTORY_EVENTS_FOR_SELF_POWER_OFF = 3
MIN_SELF_POWER_OFF_SCORE = 70
WIDE_AREA_PORT_OFF_THRESHOLD = 5
MAJOR_INCIDENT_OFF_COUNT_THRESHOLD = 800
DEFAULT_INDIVIDUAL_OFF_MIN_DURATION_MINUTES = 60
INDIVIDUAL_OFF_ALERT_RULE_VERSION = "precision-v1"

SCORE_CSV_FIELDNAMES = [
    "ma_tb",
    "ten_tb",
    "subscriber_key",
    "parent_port_key",
    "batch_id",
    "classification",
    "confidence",
    "self_poweroff_score",
    "individual_fault_score",
    "wide_area_score",
    "history_event_count",
    "most_common_off_hour",
    "off_hour_consistency",
    "median_off_duration_hours",
    "p90_off_duration_hours",
    "current_off_duration_hours",
    "current_off_duration_minutes",
    "current_off_duration_vs_p90",
    "same_port_off_count",
    "alert_eligible",
    "alert_eligibility_reason",
    "alert_rule_version",
    "reasons",
]


@dataclass
class OFFHistoryEvent:
    outage_time: datetime
    recovery_time: datetime
    outage_duration_minutes: int


@dataclass
class OffEventContext:
    subscriber_key: str
    parent_port_key: str
    batch_id: str
    ma_tb: str
    ten_tb: str
    first_off_time: datetime
    reference_time: datetime
    same_port_off_count: int = 1
    is_wide_area: bool = False


@dataclass
class OffScoringResult:
    subscriber_key: str
    parent_port_key: str
    batch_id: str
    ma_tb: str
    ten_tb: str
    classification: str
    confidence: int
    self_poweroff_score: int
    individual_fault_score: int
    wide_area_score: int
    features: Dict[str, float] = field(default_factory=dict)
    reasons: List[str] = field(default_factory=list)
    alert_eligible: bool = False
    alert_eligibility_reason: str = "decision_unavailable"
    alert_rule_version: str = INDIVIDUAL_OFF_ALERT_RULE_VERSION

    def to_csv_row(self) -> Dict:
        return {
            "ma_tb": _as_excel_text(self.ma_tb),
            "ten_tb": self.ten_tb,
            "subscriber_key": self.subscriber_key,
            "parent_port_key": self.parent_port_key,
            "batch_id": self.batch_id,
            "classification": self.classification,
            "confidence": self.confidence,
            "self_poweroff_score": self.self_poweroff_score,
            "individual_fault_score": self.individual_fault_score,
            "wide_area_score": self.wide_area_score,
            "history_event_count": self.features.get("history_event_count", 0),
            "most_common_off_hour": self.features.get("most_common_off_hour", ""),
            "off_hour_consistency": self.features.get("off_hour_consistency", 0),
            "median_off_duration_hours": self.features.get("median_off_duration_hours", 0),
            "p90_off_duration_hours": self.features.get("p90_off_duration_hours", 0),
            "current_off_duration_hours": self.features.get("current_off_duration_hours", 0),
            "current_off_duration_minutes": self.features.get("current_off_duration_minutes", 0),
            "current_off_duration_vs_p90": self.features.get("current_off_duration_vs_p90", 0),
            "same_port_off_count": self.features.get("same_port_off_count", 0),
            "alert_eligible": self.alert_eligible,
            "alert_eligibility_reason": self.alert_eligibility_reason,
            "alert_rule_version": self.alert_rule_version,
            "reasons": "; ".join(self.reasons),
        }


def _as_excel_text(value: str) -> str:
    escaped = str(value or "").replace('"', '""')
    return f'="{escaped}"'


def _clamp_score(value: float) -> int:
    return max(0, min(100, int(round(value))))


def _parse_dt(value) -> Optional[datetime]:
    if not value:
        return None
    if isinstance(value, datetime):
        return value
    return datetime.fromisoformat(str(value))


def _percentile(values: Sequence[float], percentile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * percentile
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    fraction = position - lower
    return ordered[lower] + (ordered[upper] - ordered[lower]) * fraction


def _hour_distance(left: int, right: int) -> int:
    diff = abs(left - right)
    return min(diff, 24 - diff)


MIN_COMMON_HOUR_COUNT = 3


def _history_features(context: OffEventContext, history: Sequence[OFFHistoryEvent]) -> Dict[str, float]:
    durations_hours = [
        max(event.outage_duration_minutes, 0) / 60
        for event in history
        if event.outage_duration_minutes is not None
    ]
    hours = [event.outage_time.hour for event in history if event.outage_time]
    hour_counts: Dict[int, int] = {}
    for hour in hours:
        hour_counts[hour] = hour_counts.get(hour, 0) + 1
    most_common_hour = max(hour_counts, key=hour_counts.get) if hour_counts else None
    hour_consistency = hour_counts[most_common_hour] / len(hours) if most_common_hour is not None else 0.0
    common_hours = sorted(h for h, c in hour_counts.items() if c >= MIN_COMMON_HOUR_COUNT)
    current_hour = context.first_off_time.hour
    if common_hours:
        current_hour_distance = min(_hour_distance(current_hour, h) for h in common_hours)
    elif most_common_hour is not None:
        current_hour_distance = _hour_distance(current_hour, most_common_hour)
    else:
        current_hour_distance = 24
    current_duration_seconds = max(
        (context.reference_time - context.first_off_time).total_seconds(),
        0,
    )
    current_duration_hours = current_duration_seconds / 3600
    p90_duration_hours = _percentile(durations_hours, 0.9)
    duration_vs_p90 = current_duration_hours / p90_duration_hours if p90_duration_hours else 0.0

    return {
        "history_event_count": len(history),
        "most_common_off_hour": most_common_hour if most_common_hour is not None else "",
        "common_off_hours": common_hours,
        "off_hour_consistency": round(hour_consistency, 4),
        "median_off_duration_hours": round(median(durations_hours), 4) if durations_hours else 0.0,
        "p90_off_duration_hours": round(p90_duration_hours, 4),
        "current_off_duration_hours": current_duration_hours,
        "current_off_duration_minutes": max(int(current_duration_seconds // 60), 0),
        "current_off_duration_vs_p90": round(duration_vs_p90, 4),
        "current_hour_distance": current_hour_distance,
        "same_port_off_count": context.same_port_off_count,
    }


def apply_individual_alert_eligibility(
    result: OffScoringResult,
    min_duration_minutes: int = DEFAULT_INDIVIDUAL_OFF_MIN_DURATION_MINUTES,
) -> OffScoringResult:
    """Attach a strict, explainable notification decision to a scoring result."""
    threshold = max(int(min_duration_minutes or DEFAULT_INDIVIDUAL_OFF_MIN_DURATION_MINUTES), 1)
    duration_minutes = max(
        int(result.features.get("current_off_duration_minutes", 0) or 0),
        0,
    )
    history_count = max(int(result.features.get("history_event_count", 0) or 0), 0)

    result.alert_eligible = False
    result.alert_rule_version = INDIVIDUAL_OFF_ALERT_RULE_VERSION
    if result.classification == WIDE_AREA_OR_PORT_INCIDENT:
        result.alert_eligibility_reason = "wide_area_or_port_incident"
    elif result.classification == LIKELY_SELF_POWER_OFF:
        result.alert_eligibility_reason = "likely_self_power_off"
    elif duration_minutes < threshold:
        result.alert_eligibility_reason = "minimum_duration_not_reached"
    elif result.classification == LIKELY_INDIVIDUAL_FAULT:
        result.alert_eligible = True
        result.alert_eligibility_reason = "classified_individual_fault"
    elif history_count == 0:
        result.alert_eligible = True
        result.alert_eligibility_reason = "no_history_absolute_duration"
    else:
        result.alert_eligibility_reason = "uncertain_fault_evidence"
    return result


def classify_off_event(
    context: OffEventContext,
    history: Sequence[OFFHistoryEvent],
) -> OffScoringResult:
    features = _history_features(context, history)
    reasons: List[str] = []

    wide_area_score = 100 if context.is_wide_area else 0
    if not context.is_wide_area and context.same_port_off_count >= WIDE_AREA_PORT_OFF_THRESHOLD:
        wide_area_score = min(100, 55 + context.same_port_off_count * 8)
        reasons.append(f"{context.same_port_off_count} OFF subscribers on same port")
    if context.is_wide_area:
        reasons.append("marked as wide-area or port incident")

    history_count = int(features["history_event_count"])
    hour_consistency = float(features["off_hour_consistency"])
    current_hour_distance = int(features["current_hour_distance"])
    duration_vs_p90 = float(features["current_off_duration_vs_p90"])

    self_score = 0
    if history_count >= MIN_HISTORY_EVENTS_FOR_SELF_POWER_OFF:
        self_score += 30
        reasons.append(f"{history_count} historical OFF->ON events")
    elif history_count == 2:
        self_score += 15
    elif history_count == 1:
        self_score += 5

    has_strong_two_event_pattern = (
        history_count >= 2
        and hour_consistency >= 1.0
        and current_hour_distance <= 1
        and (duration_vs_p90 == 0 or duration_vs_p90 <= 1.5)
    )

    if has_strong_two_event_pattern:
        self_score += 55
        reasons.append("2 highly consistent historical OFF->ON events")
    elif history_count >= MIN_HISTORY_EVENTS_FOR_SELF_POWER_OFF and current_hour_distance <= 1:
        self_score += 30 if hour_consistency >= 0.5 else 20
        reasons.append("current OFF hour matches historical pattern")
    elif history_count >= MIN_HISTORY_EVENTS_FOR_SELF_POWER_OFF and current_hour_distance <= 2:
        self_score += 15

    if history_count >= MIN_HISTORY_EVENTS_FOR_SELF_POWER_OFF:
        if duration_vs_p90 == 0 or duration_vs_p90 <= 1.5:
            self_score += 25
            reasons.append("current OFF duration is within historical range")
        elif duration_vs_p90 <= 2:
            self_score += 15

    if context.same_port_off_count <= 1:
        self_score += 10
    elif context.same_port_off_count <= 3:
        self_score += 5

    fault_score = 0
    if history_count == 0:
        fault_score += 35
        reasons.append("no historical self power-off pattern")
    elif history_count < MIN_HISTORY_EVENTS_FOR_SELF_POWER_OFF:
        fault_score += 20

    if duration_vs_p90 >= 6:
        fault_score += 50
        reasons.append("current OFF duration greatly exceeds historical p90")
    elif duration_vs_p90 >= 3:
        fault_score += 35
    elif duration_vs_p90 >= 2:
        fault_score += 20

    if history_count >= MIN_HISTORY_EVENTS_FOR_SELF_POWER_OFF and current_hour_distance >= 4:
        fault_score += 25
        reasons.append("current OFF hour differs from historical pattern")

    if context.same_port_off_count <= 2:
        fault_score += 10

    if self_score < 40:
        fault_score += 10

    self_score = _clamp_score(self_score)
    fault_score = _clamp_score(fault_score)
    wide_area_score = _clamp_score(wide_area_score)

    if wide_area_score >= 80:
        classification = WIDE_AREA_OR_PORT_INCIDENT
        confidence = wide_area_score
    elif self_score >= MIN_SELF_POWER_OFF_SCORE and self_score >= fault_score + 10:
        classification = LIKELY_SELF_POWER_OFF
        confidence = self_score
    elif fault_score >= 70 and fault_score >= self_score + 10:
        classification = LIKELY_INDIVIDUAL_FAULT
        confidence = fault_score
    else:
        classification = UNCERTAIN
        confidence = max(self_score, fault_score, wide_area_score)

    result = OffScoringResult(
        subscriber_key=context.subscriber_key,
        parent_port_key=context.parent_port_key,
        batch_id=context.batch_id,
        ma_tb=context.ma_tb,
        ten_tb=context.ten_tb,
        classification=classification,
        confidence=confidence,
        self_poweroff_score=self_score,
        individual_fault_score=fault_score,
        wide_area_score=wide_area_score,
        features=features,
        reasons=reasons,
    )
    return apply_individual_alert_eligibility(result)


def _connect(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path, timeout=60)
    conn.row_factory = sqlite3.Row
    return conn


def _table_exists(conn: sqlite3.Connection, table_name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
        (table_name,),
    ).fetchone()
    return row is not None


def _ensure_pattern_table(conn: sqlite3.Connection) -> None:
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


def _rows_to_history(rows: Iterable[sqlite3.Row]) -> Dict[str, List[OFFHistoryEvent]]:
    history: Dict[str, List[OFFHistoryEvent]] = {}
    for row in rows:
        outage_time = _parse_dt(row["outage_time"])
        recovery_time = _parse_dt(row["recovery_time"])
        if not outage_time or not recovery_time:
            continue
        history.setdefault(row["subscriber_key"], []).append(
            OFFHistoryEvent(
                outage_time=outage_time,
                recovery_time=recovery_time,
                outage_duration_minutes=row["outage_duration_minutes"] or 0,
            )
        )
    return history


def _history_event_key(subscriber_key: str, outage_time) -> Optional[Tuple[str, str]]:
    parsed = _parse_dt(outage_time)
    if not subscriber_key or not parsed:
        return None
    return (subscriber_key, parsed.isoformat())


def _load_major_incident_batch_ids(
    conn: sqlite3.Connection,
    off_count_threshold: int = MAJOR_INCIDENT_OFF_COUNT_THRESHOLD,
) -> Set[str]:
    if (
        off_count_threshold <= 0
        or not _table_exists(conn, "onu_measurements")
    ):
        return set()

    if _table_exists(conn, "measurement_batches"):
        rows = conn.execute(
            """
            SELECT m.batch_id
            FROM onu_measurements m
            JOIN measurement_batches mb ON mb.batch_id = m.batch_id
            WHERE mb.status IN ('completed', 'alerted')
            GROUP BY m.batch_id
            HAVING SUM(CASE WHEN UPPER(TRIM(m.onuStatusStr)) = 'OFF' THEN 1 ELSE 0 END) >= ?
            """,
            (off_count_threshold,),
        ).fetchall()
    else:
        rows = conn.execute(
            """
            SELECT batch_id
            FROM onu_measurements
            GROUP BY batch_id
            HAVING SUM(CASE WHEN UPPER(TRIM(onuStatusStr)) = 'OFF' THEN 1 ELSE 0 END) >= ?
            """,
            (off_count_threshold,),
        ).fetchall()
    return {row["batch_id"] for row in rows if row["batch_id"]}


def _load_excluded_history_event_keys(
    conn: sqlite3.Connection,
    subscriber_keys: Sequence[str],
    off_count_threshold: int = MAJOR_INCIDENT_OFF_COUNT_THRESHOLD,
) -> Set[Tuple[str, str]]:
    if not subscriber_keys or not _table_exists(conn, "outage_alerts"):
        return set()

    unique_keys = sorted(set(subscriber_keys))
    placeholders = ",".join("?" for _ in unique_keys)
    major_incident_batch_ids = _load_major_incident_batch_ids(conn, off_count_threshold)

    where_parts = ["suppressed_by_wide_area = 1"]
    params: List = list(unique_keys)
    if major_incident_batch_ids:
        batch_placeholders = ",".join("?" for _ in major_incident_batch_ids)
        where_parts.append(f"batch_id IN ({batch_placeholders})")
        params.extend(sorted(major_incident_batch_ids))

    rows = conn.execute(
        f"""
        SELECT subscriber_key, first_off_time, alert_time
        FROM outage_alerts
        WHERE subscriber_key IN ({placeholders})
          AND ({' OR '.join(where_parts)})
        """,
        tuple(params),
    ).fetchall()

    excluded_keys: Set[Tuple[str, str]] = set()
    for row in rows:
        for candidate_time in (row["first_off_time"], row["alert_time"]):
            key = _history_event_key(row["subscriber_key"], candidate_time)
            if key:
                excluded_keys.add(key)
    return excluded_keys


def _load_history_by_subscriber(conn: sqlite3.Connection) -> Dict[str, List[OFFHistoryEvent]]:
    if not _table_exists(conn, "recovery_alerts"):
        return {}
    rows = conn.execute(
        """
        SELECT subscriber_key, outage_time, recovery_time, outage_duration_minutes
        FROM recovery_alerts
        WHERE COALESCE(subscriber_key, '') <> ''
          AND COALESCE(outage_time, '') <> ''
        ORDER BY subscriber_key, outage_time
        """
    ).fetchall()
    subscriber_keys = [row["subscriber_key"] for row in rows]
    excluded_event_keys = _load_excluded_history_event_keys(conn, subscriber_keys)
    if excluded_event_keys:
        rows = [
            row
            for row in rows
            if _history_event_key(row["subscriber_key"], row["outage_time"]) not in excluded_event_keys
        ]
    return _rows_to_history(rows)


def _load_history_for_subscribers(
    conn: sqlite3.Connection,
    subscriber_keys: Sequence[str],
    history_days: int = 30,
    reference_time: Optional[datetime] = None,
) -> Dict[str, List[OFFHistoryEvent]]:
    if not subscriber_keys or not _table_exists(conn, "recovery_alerts"):
        return {}

    unique_keys = sorted(set(subscriber_keys))
    placeholders = ",".join("?" for _ in unique_keys)
    params: List = list(unique_keys)
    date_filter = ""
    if reference_time and history_days > 0:
        date_filter = "AND outage_time >= ?"
        params.append((reference_time - timedelta(days=history_days)).isoformat())

    rows = conn.execute(
        f"""
        SELECT subscriber_key, outage_time, recovery_time, outage_duration_minutes
        FROM recovery_alerts
        WHERE subscriber_key IN ({placeholders})
          AND COALESCE(outage_time, '') <> ''
          {date_filter}
        ORDER BY subscriber_key, outage_time
        """,
        tuple(params),
    ).fetchall()
    excluded_event_keys = _load_excluded_history_event_keys(conn, unique_keys)
    if excluded_event_keys:
        rows = [
            row
            for row in rows
            if _history_event_key(row["subscriber_key"], row["outage_time"]) not in excluded_event_keys
        ]
    return _rows_to_history(rows)


def _latest_history_metadata(conn: sqlite3.Connection) -> Dict[str, Dict]:
    if not _table_exists(conn, "recovery_alerts"):
        return {}
    rows = conn.execute(
        """
        SELECT subscriber_key, parent_port_key, batch_id, ma_tb, ten_tb, outage_time, recovery_time
        FROM recovery_alerts
        WHERE COALESCE(ma_tb, '') <> ''
          AND COALESCE(outage_time, '') <> ''
        ORDER BY subscriber_key, outage_time
        """
    ).fetchall()
    subscriber_keys = [row["subscriber_key"] for row in rows]
    excluded_event_keys = _load_excluded_history_event_keys(conn, subscriber_keys)
    metadata = {}
    for row in rows:
        if _history_event_key(row["subscriber_key"], row["outage_time"]) in excluded_event_keys:
            continue
        metadata[row["subscriber_key"]] = dict(row)
    return metadata


def _same_port_counts(conn: sqlite3.Connection, batch_id: Optional[str]) -> Dict[str, int]:
    if not batch_id or not _table_exists(conn, "outage_alerts"):
        return {}
    rows = conn.execute(
        """
        SELECT parent_port_key, COUNT(*) AS count
        FROM outage_alerts
        WHERE batch_id = ?
        GROUP BY parent_port_key
        """,
        (batch_id,),
    ).fetchall()
    return {row["parent_port_key"]: row["count"] for row in rows}


def _outage_contexts(
    conn: sqlite3.Connection,
    batch_id: Optional[str],
    reference_time: Optional[datetime],
) -> List[OffEventContext]:
    if not _table_exists(conn, "outage_alerts"):
        return []
    params: List[str] = []
    where = "WHERE COALESCE(ma_tb, '') <> ''"
    if batch_id:
        where += " AND batch_id = ?"
        params.append(batch_id)
    rows = conn.execute(
        f"""
        SELECT subscriber_key, parent_port_key, batch_id, ma_tb, ten_tb,
               first_off_time, alert_time, suppressed_by_wide_area
        FROM outage_alerts
        {where}
        ORDER BY batch_id, subscriber_key
        """,
        tuple(params),
    ).fetchall()

    counts_by_batch_port = {}
    for row in rows:
        key = (row["batch_id"], row["parent_port_key"])
        counts_by_batch_port[key] = counts_by_batch_port.get(key, 0) + 1

    contexts = []
    for row in rows:
        first_off_time = _parse_dt(row["first_off_time"]) or _parse_dt(row["alert_time"])
        if not first_off_time:
            continue
        ref_time = reference_time or _parse_dt(row["alert_time"]) or first_off_time
        contexts.append(
            OffEventContext(
                subscriber_key=row["subscriber_key"],
                parent_port_key=row["parent_port_key"],
                batch_id=row["batch_id"],
                ma_tb=row["ma_tb"] or "",
                ten_tb=row["ten_tb"] or "",
                first_off_time=first_off_time,
                reference_time=ref_time,
                same_port_off_count=counts_by_batch_port.get((row["batch_id"], row["parent_port_key"]), 1),
                is_wide_area=bool(row["suppressed_by_wide_area"]),
            )
        )
    return contexts


def score_outage_alerts(
    db_path: str = DEFAULT_DB_PATH,
    batch_id: Optional[str] = None,
    reference_time: Optional[datetime] = None,
    history_days: int = 30,
) -> List[OffScoringResult]:
    with _connect(db_path) as conn:
        contexts = _outage_contexts(conn, batch_id, reference_time)
        subscriber_keys = [context.subscriber_key for context in contexts]
        history_reference_time = reference_time or max(
            (context.reference_time for context in contexts),
            default=None,
        )
        history_by_subscriber = _load_history_for_subscribers(
            conn,
            subscriber_keys,
            history_days=history_days,
            reference_time=history_reference_time,
        )
        return [
            classify_off_event(context, history_by_subscriber.get(context.subscriber_key, []))
            for context in contexts
        ]


def score_pattern_suppression_for_batch(
    db_path: str,
    batch_id: str,
    reference_time: Optional[datetime] = None,
    min_self_poweroff_score: int = MIN_SELF_POWER_OFF_SCORE,
    history_days: int = 30,
) -> tuple[List[str], List[OffScoringResult]]:
    results = score_outage_alerts(
        db_path,
        batch_id=batch_id,
        reference_time=reference_time,
        history_days=history_days,
    )
    subscriber_keys = [
        result.subscriber_key
        for result in results
        if result.classification == LIKELY_SELF_POWER_OFF
        and result.self_poweroff_score >= min_self_poweroff_score
    ]
    return subscriber_keys, results


def score_current_off_snapshots(
    db_path: str,
    current_offs: Sequence,
    state_rows: Dict[str, Dict],
    *,
    batch_id: str,
    wide_area_subscriber_keys: Optional[Set[str]] = None,
    reference_time: Optional[datetime] = None,
    history_days: int = 30,
    min_alert_duration_minutes: int = DEFAULT_INDIVIDUAL_OFF_MIN_DURATION_MINUTES,
) -> List[OffScoringResult]:
    wide_area_set = set(wide_area_subscriber_keys or set())
    port_counts: Dict[str, int] = {}
    for snapshot in current_offs:
        port_counts[snapshot.parent_port_key] = port_counts.get(snapshot.parent_port_key, 0) + 1

    contexts: List[OffEventContext] = []
    for snapshot in current_offs:
        state = state_rows.get(snapshot.subscriber_key) or {}
        first_off_time = _parse_dt(state.get("first_off_time")) or snapshot.measured_at
        ref_time = reference_time or snapshot.measured_at
        contexts.append(
            OffEventContext(
                subscriber_key=snapshot.subscriber_key,
                parent_port_key=snapshot.parent_port_key,
                batch_id=batch_id,
                ma_tb=snapshot.ma_tb,
                ten_tb=snapshot.ten_tb,
                first_off_time=first_off_time,
                reference_time=ref_time,
                same_port_off_count=port_counts.get(snapshot.parent_port_key, 1),
                is_wide_area=snapshot.subscriber_key in wide_area_set,
            )
        )

    with _connect(db_path) as conn:
        subscriber_keys = [context.subscriber_key for context in contexts]
        history_reference_time = reference_time or max(
            (context.reference_time for context in contexts),
            default=None,
        )
        history_by_subscriber = _load_history_for_subscribers(
            conn,
            subscriber_keys,
            history_days=history_days,
            reference_time=history_reference_time,
        )

    return [
        apply_individual_alert_eligibility(
            classify_off_event(context, history_by_subscriber.get(context.subscriber_key, [])),
            min_duration_minutes=min_alert_duration_minutes,
        )
        for context in contexts
    ]


def score_pattern_suppression_for_current_offs(
    db_path: str,
    current_offs: Sequence,
    state_rows: Dict[str, Dict],
    *,
    batch_id: str,
    wide_area_subscriber_keys: Optional[Set[str]] = None,
    reference_time: Optional[datetime] = None,
    min_self_poweroff_score: int = MIN_SELF_POWER_OFF_SCORE,
    history_days: int = 30,
    min_alert_duration_minutes: int = DEFAULT_INDIVIDUAL_OFF_MIN_DURATION_MINUTES,
) -> tuple[List[str], List[OffScoringResult]]:
    results = score_current_off_snapshots(
        db_path,
        current_offs,
        state_rows,
        batch_id=batch_id,
        wide_area_subscriber_keys=wide_area_subscriber_keys,
        reference_time=reference_time,
        history_days=history_days,
        min_alert_duration_minutes=min_alert_duration_minutes,
    )
    subscriber_keys = [
        result.subscriber_key
        for result in results
        if result.classification == LIKELY_SELF_POWER_OFF
        and result.self_poweroff_score >= min_self_poweroff_score
    ]
    return subscriber_keys, results


def update_exclusion_table_from_results(
    db_path: str,
    scoring_results: Sequence[OffScoringResult],
    min_self_poweroff_score: int = MIN_SELF_POWER_OFF_SCORE,
) -> int:
    upserted_by_ma_tb: Dict[str, OffScoringResult] = {}
    for result in scoring_results:
        if (
            result.classification == LIKELY_SELF_POWER_OFF
            and result.self_poweroff_score >= min_self_poweroff_score
            and result.ma_tb
        ):
            upserted_by_ma_tb[result.ma_tb] = result

    with _connect(db_path) as conn:
        _ensure_pattern_table(conn)
        for result in upserted_by_ma_tb.values():
            _upsert_pattern_row(conn, result)
        conn.commit()
    return len(upserted_by_ma_tb)


def _historical_pattern_scores(
    conn: sqlite3.Connection,
    reference_time: Optional[datetime],
) -> List[OffScoringResult]:
    history_by_subscriber = _load_history_by_subscriber(conn)
    metadata_by_subscriber = _latest_history_metadata(conn)
    results = []
    for subscriber_key, history in history_by_subscriber.items():
        metadata = metadata_by_subscriber.get(subscriber_key)
        if not metadata or len(history) < MIN_HISTORY_EVENTS_FOR_SELF_POWER_OFF:
            continue
        latest_event = history[-1]
        context = OffEventContext(
            subscriber_key=subscriber_key,
            parent_port_key=metadata.get("parent_port_key") or "",
            batch_id=metadata.get("batch_id") or "",
            ma_tb=metadata.get("ma_tb") or "",
            ten_tb=metadata.get("ten_tb") or "",
            first_off_time=latest_event.outage_time,
            reference_time=reference_time or latest_event.recovery_time,
            same_port_off_count=1,
        )
        results.append(classify_off_event(context, history))
    return results


def _upsert_pattern_row(conn: sqlite3.Connection, result: OffScoringResult) -> None:
    now = datetime.now().isoformat()
    notes = json.dumps(
        {
            "classification": result.classification,
            "confidence": result.confidence,
            "self_poweroff_score": result.self_poweroff_score,
            "individual_fault_score": result.individual_fault_score,
            "wide_area_score": result.wide_area_score,
            "features": result.features,
            "reasons": result.reasons,
        },
        ensure_ascii=False,
    )
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
        (
            result.ma_tb,
            result.ten_tb,
            result.classification,
            int(result.features.get("history_event_count", 0)),
            result.self_poweroff_score / 100,
            now,
            now,
            notes,
        ),
    )


def update_exclusion_table(
    db_path: str = DEFAULT_DB_PATH,
    batch_id: Optional[str] = None,
    reference_time: Optional[datetime] = None,
    min_self_poweroff_score: int = MIN_SELF_POWER_OFF_SCORE,
    history_days: int = 30,
) -> int:
    with _connect(db_path) as conn:
        _ensure_pattern_table(conn)
        scored_results = score_outage_alerts(
            db_path,
            batch_id=batch_id,
            reference_time=reference_time,
            history_days=history_days,
        )
        if not scored_results and batch_id is None:
            scored_results = _historical_pattern_scores(conn, reference_time)

        upserted_by_ma_tb: Dict[str, OffScoringResult] = {}
        for result in scored_results:
            if (
                result.classification == LIKELY_SELF_POWER_OFF
                and result.self_poweroff_score >= min_self_poweroff_score
                and result.ma_tb
            ):
                upserted_by_ma_tb[result.ma_tb] = result

        for result in upserted_by_ma_tb.values():
            _upsert_pattern_row(conn, result)
        conn.commit()
        return len(upserted_by_ma_tb)


def get_pattern_exclusion_list(db_path: str = DEFAULT_DB_PATH) -> Set[str]:
    with _connect(db_path) as conn:
        _ensure_pattern_table(conn)
        rows = conn.execute(
            "SELECT ma_tb FROM pattern_exclusion_list WHERE is_active = 1"
        ).fetchall()
        return {row["ma_tb"] for row in rows}


def export_scoring_csv(
    db_path: str = DEFAULT_DB_PATH,
    output_path: str = "runtime/off_scoring.csv",
    batch_id: Optional[str] = None,
    reference_time: Optional[datetime] = None,
    history_days: int = 30,
) -> int:
    rows = [
        result.to_csv_row()
        for result in score_outage_alerts(
            db_path,
            batch_id=batch_id,
            reference_time=reference_time,
            history_days=history_days,
        )
    ]
    output_file = Path(output_path)
    output_file.parent.mkdir(parents=True, exist_ok=True)
    with output_file.open("w", newline="", encoding=CSV_ENCODING) as file_obj:
        writer = csv.DictWriter(file_obj, fieldnames=SCORE_CSV_FIELDNAMES)
        writer.writeheader()
        writer.writerows(rows)
    return len(rows)


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Score OFF subscribers by likely cause.")
    parser.add_argument("--db", default=DEFAULT_DB_PATH)
    parser.add_argument("--batch-id")
    parser.add_argument("--history-days", type=int, default=30)
    parser.add_argument("--output", default="runtime/off_scoring.csv")
    args = parser.parse_args(argv)

    row_count = export_scoring_csv(
        args.db,
        args.output,
        batch_id=args.batch_id,
        history_days=args.history_days,
    )
    print(f"Wrote {row_count} scoring rows to {Path(args.output).resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
