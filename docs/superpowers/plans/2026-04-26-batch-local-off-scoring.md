# Batch-Local OFF Scoring Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make OFF scoring run per current batch and per current OFF alert, using only the history for subscribers in that batch, so the system can handle about 600 OFF subscribers per cycle without scanning the full 30-day raw measurement window.

**Architecture:** Keep the rule-based scoring engine in `subscriber_off_scoring.py`, but change its DB access pattern from "load all recovery history" to "load current batch outage contexts first, then load recovery history only for those subscriber keys." Change `alert_engine.py` to suppress current-batch outage alerts directly from current-batch scoring results, while keeping `pattern_exclusion_list` only as an audit/cache table rather than the decision source.

**Tech Stack:** Python, sqlite3, pytest, csv

---

## File Structure

- `subscriber_off_scoring.py`
  - Owns scoring rules, batch-local DB queries, scoring CSV export, and compatibility helpers.
- `pattern_exclusion.py`
  - Remains a thin compatibility wrapper for old imports.
- `alert_engine.py`
  - Calls batch-local scoring and suppresses only the current batch rows returned as `LIKELY_SELF_POWER_OFF`.
- `tests/test_subscriber_off_scoring.py`
  - Adds DB-query tests proving history is loaded only for current batch subscribers.
- `tests/test_alert_engine.py`
  - Adds/updates integration tests proving a previously self-power-off subscriber is not automatically suppressed on a later unusual OFF unless that current OFF scores as self-power-off.

## Task 1: Add Batch-Local Scoring Tests

**Files:**
- Modify: `tests/test_subscriber_off_scoring.py`

- [ ] **Step 1: Write failing test for current-batch subscriber filtering**

Add a test that creates:
- `outage_alerts` for `sub-current` in batch `b10`
- `outage_alerts` for `sub-other` in batch `b9`
- `recovery_alerts` history for both subscribers

Expected behavior:
- `score_outage_alerts(db_path, batch_id="b10")` returns only `sub-current`
- history features for `sub-current` ignore `sub-other`
- no full-table decision is used for current-batch suppress

Test sketch:

```python
def test_score_outage_alerts_loads_history_only_for_current_batch_subscribers(tmp_path):
    db_path = tmp_path / "onu_measurements.db"
    create_scoring_schema(db_path)
    with sqlite3.connect(db_path) as conn:
        for idx in range(3):
            insert_history(conn, "sub-current", "TB100", idx, hour=20, minutes=15)
            insert_history(conn, "sub-other", "TB200", idx, hour=20, minutes=15)
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_subscriber_off_scoring.py::test_score_outage_alerts_loads_history_only_for_current_batch_subscribers -v`

Expected: FAIL until `score_outage_alerts()` uses a batch-local history loader.

## Task 2: Implement Batch-Local History Loading

**Files:**
- Modify: `subscriber_off_scoring.py`

- [ ] **Step 1: Add helper to fetch outage contexts first**

Add a helper that returns the current contexts plus their subscriber keys:

```python
def _outage_contexts(...):
    ...
```

Keep the existing function shape, but make `score_outage_alerts()` call it before loading history.

- [ ] **Step 2: Add targeted history loader**

Add:

```python
def _load_history_for_subscribers(
    conn: sqlite3.Connection,
    subscriber_keys: Sequence[str],
    days: int = 30,
    reference_time: Optional[datetime] = None,
) -> Dict[str, List[OFFHistoryEvent]]:
    if not subscriber_keys:
        return {}
    placeholders = ",".join("?" for _ in subscriber_keys)
    params = list(subscriber_keys)
    where_time = ""
    if reference_time:
        where_time = "AND outage_time >= ?"
        params.append((reference_time - timedelta(days=days)).isoformat())
    rows = conn.execute(
        f"""
        SELECT subscriber_key, outage_time, recovery_time, outage_duration_minutes
        FROM recovery_alerts
        WHERE subscriber_key IN ({placeholders})
          AND COALESCE(outage_time, '') <> ''
          {where_time}
        ORDER BY subscriber_key, outage_time
        """,
        tuple(params),
    ).fetchall()
    ...
```

Use this only for batch-local scoring.

- [ ] **Step 3: Update `score_outage_alerts()`**

Target behavior:

```python
def score_outage_alerts(db_path=DEFAULT_DB_PATH, batch_id=None, reference_time=None):
    with _connect(db_path) as conn:
        contexts = _outage_contexts(conn, batch_id, reference_time)
        subscriber_keys = [context.subscriber_key for context in contexts]
        history_by_subscriber = _load_history_for_subscribers(
            conn,
            subscriber_keys,
            reference_time=reference_time,
        )
        return [
            classify_off_event(context, history_by_subscriber.get(context.subscriber_key, []))
            for context in contexts
        ]
```

- [ ] **Step 4: Run tests**

Run: `pytest tests/test_subscriber_off_scoring.py -v`

Expected: PASS.

## Task 3: Suppress Current Batch Directly From Scoring Results

**Files:**
- Modify: `subscriber_off_scoring.py`
- Modify: `alert_engine.py`
- Modify: `tests/test_alert_engine.py`

- [ ] **Step 1: Add helper returning subscriber keys to suppress**

Add:

```python
def score_pattern_suppression_for_batch(
    db_path: str,
    batch_id: str,
    reference_time: Optional[datetime] = None,
    min_self_poweroff_score: int = MIN_SELF_POWER_OFF_SCORE,
) -> Tuple[List[str], List[OffScoringResult]]:
    results = score_outage_alerts(db_path, batch_id=batch_id, reference_time=reference_time)
    subscriber_keys = [
        result.subscriber_key
        for result in results
        if result.classification == LIKELY_SELF_POWER_OFF
        and result.self_poweroff_score >= min_self_poweroff_score
    ]
    return subscriber_keys, results
```

- [ ] **Step 2: Update `alert_engine.py`**

Replace the current `pattern_exclusion_list` suppress decision:

```python
pattern_updates = update_exclusion_table(db_path, batch_id=batch_id)
exclusion_list = get_pattern_exclusion_list(db_path)
to_suppress = []
if exclusion_list:
    rows = repo.list_unsent_outage_alerts(batch_id)
    to_suppress = [row.subscriber_key for row in rows if row.ma_tb in exclusion_list]
    repo.suppress_outage_alerts(batch_id, to_suppress, "pattern_exclusion")
```

with current-batch scoring:

```python
to_suppress, scoring_results = score_pattern_suppression_for_batch(db_path, batch_id=batch_id)
pattern_updates = update_exclusion_table(db_path, batch_id=batch_id)
if to_suppress:
    repo.suppress_outage_alerts(batch_id, to_suppress, "pattern_exclusion")
exclusion_list = {
    result.ma_tb
    for result in scoring_results
    if result.subscriber_key in set(to_suppress)
}
```

Keep `exclusion_list` because `build_current_off_snapshot()` still expects a `ma_tb` set for marking pattern-suppressed rows in JSON.

- [ ] **Step 3: Add regression test for stale exclusion**

Create an integration test:
- Insert `TB777` into `pattern_exclusion_list` manually.
- Create a new OFF alert for `TB777` with no self-power-off score for the current batch.
- Assert current batch does not get `suppressed_by_pattern = 1` solely because `TB777` exists in old table.

Run: `pytest tests/test_alert_engine.py::<new_test_name> -v`

Expected: FAIL before alert-engine change, PASS after.

## Task 4: Add SQLite Indexes For Scoring

**Files:**
- Modify: `alert_db.py`
- Test: existing alert engine/scoring tests

- [ ] **Step 1: Add indexes in `AlertRepository.ensure_schema()`**

Add:

```sql
CREATE INDEX IF NOT EXISTS idx_recovery_alerts_subscriber_outage
ON recovery_alerts(subscriber_key, outage_time)
```

Add:

```sql
CREATE INDEX IF NOT EXISTS idx_outage_alerts_batch_subscriber
ON outage_alerts(batch_id, subscriber_key)
```

The unique index `idx_outage_alerts_batch_subscriber` already exists in current schema; confirm it remains. Add only missing supporting indexes.

- [ ] **Step 2: Run schema-related tests**

Run: `pytest tests/test_alert_engine.py::test_individual_outage_requires_two_consecutive_off_batches tests/test_subscriber_off_scoring.py -v`

Expected: PASS.

## Task 5: Keep Audit Export Useful

**Files:**
- Modify: `scripts/score_off_subscribers.py`
- Modify: `subscriber_off_scoring.py`

- [ ] **Step 1: Add CLI options**

Add:

```bash
--batch-id
--history-days 30
```

Behavior:
- Without `--batch-id`, export all `outage_alerts` audit rows.
- With `--batch-id`, export only scoring rows for that batch.

- [ ] **Step 2: Verify CSV formatting**

Run:

```bash
python3 scripts/score_off_subscribers.py --db onu_measurements.db --batch-id 202604261650 --output runtime/off_scoring_latest.csv
```

Expected:
- command exits 0
- file is UTF-8 BOM
- `ma_tb` is Excel text formatted as `="..."`

## Task 6: Performance Check On Real DB

**Files:**
- No code changes unless timing exposes a problem.

- [ ] **Step 1: Pick a real recent batch**

Run:

```bash
sqlite3 -readonly 'file:onu_measurements.db?mode=ro' "SELECT batch_id, COUNT(*) FROM outage_alerts GROUP BY batch_id ORDER BY batch_id DESC LIMIT 5;"
```

- [ ] **Step 2: Time batch-local scoring**

Run:

```bash
python3 - <<'PY'
from time import perf_counter
from subscriber_off_scoring import score_outage_alerts
batch_id = "REPLACE_WITH_RECENT_BATCH"
start = perf_counter()
rows = score_outage_alerts("onu_measurements.db", batch_id=batch_id)
print(len(rows), round(perf_counter() - start, 3))
PY
```

Expected:
- scores only the selected batch
- target runtime should be seconds, not minutes
- no full `onu_measurements` scan occurs

## Verification Commands

Run:

```bash
pytest tests/test_subscriber_off_scoring.py \
  tests/test_alert_engine.py::test_pattern_analyzer_updates_exclusion_table_from_recoveries \
  tests/test_alert_engine.py::test_pattern_exclusion_suppresses_individual_outage \
  tests/test_alert_engine.py::test_current_off_snapshot_keeps_suppressed_records_with_reason -v
```

Run:

```bash
python3 scripts/score_off_subscribers.py \
  --db onu_measurements.db \
  --output runtime/off_scoring.csv
```

Run:

```bash
python3 - <<'PY'
import csv
from collections import Counter
from pathlib import Path
path = Path("runtime/off_scoring.csv")
print(path.read_bytes()[:3].hex())
with path.open(newline="", encoding="utf-8-sig") as f:
    rows = list(csv.DictReader(f))
print(len(rows))
print(Counter(row["classification"] for row in rows))
PY
```
