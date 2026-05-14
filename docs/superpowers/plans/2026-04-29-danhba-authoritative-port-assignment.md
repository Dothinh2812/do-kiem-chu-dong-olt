# Danh Ba Authoritative Port Assignment Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Filter stale old-port measurements by treating `database.db:danhba` as the authoritative current subscriber-to-port assignment source.

**Architecture:** Add a repository lookup for current `MA_TB -> sub` assignments, then partition enriched `SubscriberSnapshot` rows before state-machine processing. Only valid assignment snapshots flow into individual alerts, wide-area detection, pattern scoring, current OFF snapshot JSON, and notifications.

**Tech Stack:** Python, SQLite, pytest, existing `AlertRepository` and `alert_engine` modules.

---

### Task 1: Add Failing Tests For Stale Assignment Filtering

**Files:**
- Modify: `tests/test_alert_engine.py`

- [ ] **Step 1: Write failing tests**

Add tests that create a stale measurement row whose `MA_TB` is currently assigned to a different `danhba.sub`, then verify it is excluded from individual and wide-area alerts. Add a control test proving valid assignments still alert.

- [ ] **Step 2: Run stale assignment tests**

Run: `pytest tests/test_alert_engine.py -k "stale_assignment or valid_assignment" -q`

Expected: FAIL because stale rows are still processed.

### Task 2: Add Authoritative Assignment Lookup

**Files:**
- Modify: `alert_db.py`

- [ ] **Step 1: Implement `fetch_authoritative_sub_by_ma_tb`**

Add an `AlertRepository` method that reads `danhba` from `source_db_path` and returns a dictionary mapping normalized `Ma_Tb` to current `sub`.

- [ ] **Step 2: Run repository-dependent tests**

Run: `pytest tests/test_alert_engine.py -k "stale_assignment or valid_assignment" -q`

Expected: Tests still FAIL until the alert engine uses the lookup.

### Task 3: Filter Snapshots Before Alert Processing

**Files:**
- Modify: `alert_engine.py`

- [ ] **Step 1: Implement snapshot partitioning**

After snapshots are built, call `repo.fetch_authoritative_sub_by_ma_tb(...)` and partition snapshots into valid and stale assignment rows. Use only valid snapshots for state machine, wide-area, pattern scoring, current OFF snapshot, and notifications.

- [ ] **Step 2: Add result counters and logs**

Include `stale_assignment_suppressed` in logs and returned result dictionary. Include up to five stale examples in logs as `MA_TB old_sub -> current_sub`.

- [ ] **Step 3: Run focused tests**

Run: `pytest tests/test_alert_engine.py -k "stale_assignment or valid_assignment" -q`

Expected: PASS.

### Task 4: Verify Existing Alert Engine Behavior

**Files:**
- No new files.

- [ ] **Step 1: Run alert engine tests**

Run: `pytest tests/test_alert_engine.py -q`

Expected: PASS.

- [ ] **Step 2: Run related scoring tests**

Run: `pytest tests/test_subscriber_off_scoring.py -q`

Expected: PASS.
