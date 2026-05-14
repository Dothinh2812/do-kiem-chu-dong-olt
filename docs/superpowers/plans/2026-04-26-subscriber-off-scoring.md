# Subscriber OFF Scoring Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the coarse `pattern_exclusion.py` pattern detector with a scoring program that classifies each OFF event as likely customer power-off, likely individual fault, wide-area/port incident, or uncertain.

**Architecture:** Add `subscriber_off_scoring.py` as the scoring engine and keep `pattern_exclusion.py` as a compatibility wrapper for the current `alert_engine` calls. The scorer reads historical `recovery_alerts`, current `outage_alerts`, and wide-area suppression flags, computes interpretable features, writes likely customer power-off cases into `pattern_exclusion_list`, and exposes a CLI for CSV inspection.

**Tech Stack:** Python, sqlite3, dataclasses, csv, argparse, pytest

---

### Task 1: Add scoring tests

**Files:**
- Create: `tests/test_subscriber_off_scoring.py`

- [ ] **Step 1: Write failing tests for pure scoring**

Cover:
- repeated same-hour, normal-duration OFF history becomes `LIKELY_SELF_POWER_OFF`
- long/unusual current OFF becomes `LIKELY_INDIVIDUAL_FAULT`
- explicit wide-area context becomes `WIDE_AREA_OR_PORT_INCIDENT`

- [ ] **Step 2: Write failing test for compatibility table update**

Create a temp SQLite DB with `recovery_alerts`, `outage_alerts`, and `pattern_exclusion_list`, then assert `update_exclusion_table()` upserts only likely self-power-off rows.

- [ ] **Step 3: Run tests**

Run: `pytest tests/test_subscriber_off_scoring.py -v`

Expected: FAIL because `subscriber_off_scoring.py` does not exist.

### Task 2: Implement scoring engine

**Files:**
- Create: `subscriber_off_scoring.py`
- Modify: `pattern_exclusion.py`

- [ ] **Step 1: Define categories and result dataclass**

Categories:
- `WIDE_AREA_OR_PORT_INCIDENT`
- `LIKELY_SELF_POWER_OFF`
- `LIKELY_INDIVIDUAL_FAULT`
- `UNCERTAIN`

- [ ] **Step 2: Implement feature extraction**

Features:
- historical OFF recovery count
- common OFF hour and hour consistency
- median and p90 historical OFF duration
- current OFF duration
- current OFF duration versus p90
- same-port OFF count
- wide-area flag

- [ ] **Step 3: Implement rule-based scores**

Scores:
- `self_poweroff_score`
- `individual_fault_score`
- `wide_area_score`
- `confidence`

- [ ] **Step 4: Implement DB integration**

Functions:
- `score_outage_alerts(db_path, batch_id=None, reference_time=None)`
- `update_exclusion_table(db_path, batch_id=None, min_self_poweroff_score=70)`
- `get_pattern_exclusion_list(db_path)`

### Task 3: Add CLI script

**Files:**
- Create: `scripts/score_off_subscribers.py`

- [ ] **Step 1: Add argparse CLI**

Inputs:
- `--db`
- `--batch-id`
- `--output`

- [ ] **Step 2: Export UTF-8 BOM CSV**

Columns:
- `ma_tb`
- `ten_tb`
- `subscriber_key`
- `batch_id`
- `classification`
- `confidence`
- `self_poweroff_score`
- `individual_fault_score`
- `wide_area_score`
- feature columns
- `reasons`

### Task 4: Verify

- [ ] **Step 1: Run targeted tests**

Run: `pytest tests/test_subscriber_off_scoring.py tests/test_alert_engine.py::test_pattern_analyzer_updates_exclusion_table_from_recoveries -v`

- [ ] **Step 2: Run a sample export on the real DB**

Run: `python3 scripts/score_off_subscribers.py --db onu_measurements.db --output runtime/off_scoring.csv`

- [ ] **Step 3: Inspect output**

Confirm CSV exists, uses UTF-8 BOM, and has rows or a valid header-only file.
