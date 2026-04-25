import sqlite3
from dataclasses import dataclass
from datetime import datetime
from typing import List, Set, Tuple


DEFAULT_DB_PATH = "onu_measurements.db"
MIN_EVENTS_FOR_PATTERN = 3
NIGHT_OFF_THRESHOLD = 0.50
EVENING_OFF_THRESHOLD = 0.30
HOUR_CONSISTENCY_THRESHOLD = 0.60
MIN_MULTI_DAILY_DAYS = 2


@dataclass
class SubscriberPattern:
    ma_tb: str
    ten_tb: str
    total_events: int
    night_ratio: float
    evening_ratio: float
    morning_ratio: float
    avg_duration_min: float
    most_common_hour: int
    hour_consistency: float
    max_events_per_day: int
    days_with_multiple_events: int
    pattern_type: str = ""
    pattern_score: float = 0.0


def analyze_subscriber_patterns(db_path: str = DEFAULT_DB_PATH, min_events: int = MIN_EVENTS_FOR_PATTERN) -> List[SubscriberPattern]:
    with sqlite3.connect(db_path, timeout=60) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT ma_tb, ten_tb, outage_time, recovery_time, outage_duration_minutes
            FROM recovery_alerts
            WHERE COALESCE(ma_tb, '') <> ''
            ORDER BY ma_tb, outage_time
            """
        ).fetchall()

    if not rows:
        return []

    grouped = {}
    for row in rows:
        grouped.setdefault(row["ma_tb"], []).append(row)

    patterns = []
    for ma_tb, items in grouped.items():
        if len(items) < min_events:
            continue

        hours = []
        durations = []
        events_per_day = {}
        ten_tb = items[0]["ten_tb"] or ""
        for item in items:
            outage_time = datetime.fromisoformat(item["outage_time"])
            hour = outage_time.hour
            hours.append(hour)
            durations.append(item["outage_duration_minutes"] or 0)
            outage_date = outage_time.date().isoformat()
            events_per_day[outage_date] = events_per_day.get(outage_date, 0) + 1

        total = len(items)
        night_ratio = sum(1 for h in hours if h >= 20 or h <= 6) / total
        evening_ratio = sum(1 for h in hours if 17 <= h <= 19) / total
        morning_ratio = sum(1 for h in hours if 6 <= h <= 9) / total

        hour_counts = {}
        for hour in hours:
            hour_counts[hour] = hour_counts.get(hour, 0) + 1
        most_common_hour = max(hour_counts, key=hour_counts.get)
        hour_consistency = hour_counts[most_common_hour] / total

        max_events_per_day = max(events_per_day.values())
        days_with_multiple_events = sum(1 for count in events_per_day.values() if count > 1)

        patterns.append(
            SubscriberPattern(
                ma_tb=ma_tb,
                ten_tb=ten_tb,
                total_events=total,
                night_ratio=night_ratio,
                evening_ratio=evening_ratio,
                morning_ratio=morning_ratio,
                avg_duration_min=sum(durations) / total,
                most_common_hour=most_common_hour,
                hour_consistency=hour_consistency,
                max_events_per_day=max_events_per_day,
                days_with_multiple_events=days_with_multiple_events,
            )
        )
    return patterns


def classify_pattern(pattern: SubscriberPattern) -> Tuple[str, float]:
    patterns_detected = []
    total_score = 0.0

    if pattern.night_ratio >= NIGHT_OFF_THRESHOLD:
        patterns_detected.append("NIGHT_OFF")
        total_score += pattern.night_ratio

    if pattern.evening_ratio >= EVENING_OFF_THRESHOLD:
        patterns_detected.append("EVENING_OFF")
        total_score += pattern.evening_ratio

    if pattern.max_events_per_day >= 2 and pattern.days_with_multiple_events >= MIN_MULTI_DAILY_DAYS:
        patterns_detected.append("MULTI_DAILY")
        total_score += min(pattern.max_events_per_day / 4, 1.0)

    if pattern.hour_consistency >= HOUR_CONSISTENCY_THRESHOLD:
        patterns_detected.append("CONSISTENT_HOUR")
        total_score += pattern.hour_consistency

    if not patterns_detected:
        return "NONE", 0.0
    if len(patterns_detected) == 1:
        return patterns_detected[0], total_score
    return "MIXED", total_score


def update_exclusion_table(db_path: str = DEFAULT_DB_PATH) -> int:
    patterns = analyze_subscriber_patterns(db_path)
    now = datetime.now().isoformat()
    updated_count = 0

    with sqlite3.connect(db_path, timeout=60) as conn:
        for pattern in patterns:
            pattern_type, score = classify_pattern(pattern)
            if pattern_type == "NONE":
                continue

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
                    is_active = 1
                """,
                (
                    pattern.ma_tb,
                    pattern.ten_tb,
                    pattern_type,
                    pattern.total_events,
                    score,
                    now,
                    now,
                    "auto-updated from recovery_alerts",
                ),
            )
            updated_count += 1
        conn.commit()

    return updated_count


def get_pattern_exclusion_list(db_path: str = DEFAULT_DB_PATH) -> Set[str]:
    with sqlite3.connect(db_path, timeout=60) as conn:
        rows = conn.execute(
            "SELECT ma_tb FROM pattern_exclusion_list WHERE is_active = 1"
        ).fetchall()
        return {row[0] for row in rows}
