# Port Precheck And PORT_DOWN Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add port-status precheck before ONU measurement so `Down` ports create `PORT_DOWN` wide-area subscriber measurements instead of direct ONU reads.

**Architecture:** Keep the main batch loop in `app.py`, add a precheck branch that consults port status per task, expands port-down subscribers from `danhba`, writes synthetic `onu_measurements` rows with `PORT_DOWN`, and extends post-processing to treat that source as wide-area. Reuse the standalone CTS port-status session code as the canonical port-status fetcher, without coupling the batch flow to its CLI wrapper.

**Tech Stack:** Python, requests, Playwright login reuse, SQLite, pytest

---

### Task 1: Subscriber Expansion Helpers For Port Down

**Files:**
- Modify: `app.py`
- Test: `tests/test_port_down_precheck.py`

- [ ] **Step 1: Write the failing test**

```python
def test_lookup_subscribers_by_parent_port_returns_matching_danhba_rows(...):
    rows = app.lookup_subscribers_by_parent_port(
        source_db_path,
        "HNI.STY.XSZ.OLT.ZT.1.1_1-1-11",
    )
    assert [row["subscriber_key"] for row in rows] == [
        "HNI.STY.XSZ.OLT.ZT.1.1_1-1-11:1",
        "HNI.STY.XSZ.OLT.ZT.1.1_1-1-11:2",
    ]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_port_down_precheck.py::test_lookup_subscribers_by_parent_port_returns_matching_danhba_rows -q`
Expected: FAIL with missing helper

- [ ] **Step 3: Write minimal implementation**

```python
def build_parent_port_key(olt_name, frame_no, slot_no, port_no):
    return f"{olt_name}_{frame_no}-{slot_no}-{port_no}"


def lookup_subscribers_by_parent_port(source_db_path, parent_port_key):
    ...
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_port_down_precheck.py::test_lookup_subscribers_by_parent_port_returns_matching_danhba_rows -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add app.py tests/test_port_down_precheck.py
git commit -m "feat: add parent port subscriber lookup"
```

### Task 2: Synthetic PORT_DOWN Measurement Rows

**Files:**
- Modify: `app.py`
- Test: `tests/test_port_down_precheck.py`

- [ ] **Step 1: Write the failing test**

```python
def test_build_port_down_measurement_records_creates_port_down_rows(...):
    rows = app.build_port_down_measurement_records(
        subscriber_rows=subscriber_rows,
        batch_id="202604271230",
        measured_date="2026-04-27",
        measured_time="12:30:00",
    )
    assert len(rows) == 2
    assert rows[0][9] == "PORT_DOWN"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_port_down_precheck.py::test_build_port_down_measurement_records_creates_port_down_rows -q`
Expected: FAIL with missing helper

- [ ] **Step 3: Write minimal implementation**

```python
def build_port_down_measurement_records(subscriber_rows, batch_id, measured_date, measured_time):
    ...
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_port_down_precheck.py::test_build_port_down_measurement_records_creates_port_down_rows -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add app.py tests/test_port_down_precheck.py
git commit -m "feat: create synthetic PORT_DOWN measurements"
```

### Task 3: Port Precheck Branch In Batch Download

**Files:**
- Modify: `app.py`
- Test: `tests/test_port_down_precheck.py`

- [ ] **Step 1: Write the failing tests**

```python
def test_download_single_port_skips_onu_fetch_and_writes_port_down_rows_when_port_down(...):
    ...


def test_download_single_port_ignores_down_port_without_subscribers(...):
    ...
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_port_down_precheck.py::test_download_single_port_skips_onu_fetch_and_writes_port_down_rows_when_port_down tests/test_port_down_precheck.py::test_download_single_port_ignores_down_port_without_subscribers -q`
Expected: FAIL on old flow

- [ ] **Step 3: Write minimal implementation**

```python
port_state = fetch_single_port_state(...)
if port_state == "Down":
    subscriber_rows = lookup_subscribers_by_parent_port(...)
    if not subscriber_rows:
        ...
    records = build_port_down_measurement_records(...)
    insert_measurement_records(records)
    return "port_down"
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_port_down_precheck.py::test_download_single_port_skips_onu_fetch_and_writes_port_down_rows_when_port_down tests/test_port_down_precheck.py::test_download_single_port_ignores_down_port_without_subscribers -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add app.py tests/test_port_down_precheck.py
git commit -m "feat: add port precheck branch before onu fetch"
```

### Task 4: Batch Logging For Port Precheck Outcomes

**Files:**
- Modify: `app.py`
- Test: `tests/test_port_down_precheck.py`

- [ ] **Step 1: Write the failing test**

```python
def test_append_port_precheck_log_records_down_with_subscribers(...):
    ...
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_port_down_precheck.py::test_append_port_precheck_log_records_down_with_subscribers -q`
Expected: FAIL with missing helper

- [ ] **Step 3: Write minimal implementation**

```python
def append_port_precheck_log(batch_id, task, status, reason, olt_name=None, subscriber_count=0):
    ...
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_port_down_precheck.py::test_append_port_precheck_log_records_down_with_subscribers -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add app.py tests/test_port_down_precheck.py
git commit -m "feat: log port precheck outcomes by batch"
```

### Task 5: Current Snapshot Support For PORT_DOWN

**Files:**
- Modify: `current_off_snapshot.py`
- Modify: `alert_engine.py`
- Test: `tests/test_port_down_precheck.py`

- [ ] **Step 1: Write the failing tests**

```python
def test_port_down_snapshot_row_keeps_current_status_port_down(...):
    ...


def test_process_completed_batch_marks_port_down_subscribers_as_wide_area(...):
    ...
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_port_down_precheck.py::test_port_down_snapshot_row_keeps_current_status_port_down tests/test_port_down_precheck.py::test_process_completed_batch_marks_port_down_subscribers_as_wide_area -q`
Expected: FAIL on current OFF-only assumptions

- [ ] **Step 3: Write minimal implementation**

```python
def normalize_status(raw_status):
    status = str(raw_status or "").upper()
    if status == "PORT_DOWN":
        return "PORT_DOWN"
    ...
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_port_down_precheck.py::test_port_down_snapshot_row_keeps_current_status_port_down tests/test_port_down_precheck.py::test_process_completed_batch_marks_port_down_subscribers_as_wide_area -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add alert_engine.py current_off_snapshot.py tests/test_port_down_precheck.py
git commit -m "feat: treat PORT_DOWN as wide-area snapshot source"
```

### Task 6: Regression Coverage For Existing Exports And Alerts

**Files:**
- Modify: `transition_history_export.py`
- Modify: `tests/test_transition_history_export.py`
- Modify: `tests/test_alert_engine.py`

- [ ] **Step 1: Write the failing tests**

```python
def test_transition_history_ignores_port_down_as_on_off_transition(...):
    ...


def test_normalize_status_preserves_port_down(...):
    ...
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_transition_history_export.py::test_transition_history_ignores_port_down_as_on_off_transition tests/test_alert_engine.py::test_normalize_status_preserves_port_down -q`
Expected: FAIL on current ON/OFF-only normalization

- [ ] **Step 3: Write minimal implementation**

```python
def _normalize_status(raw_status):
    status = str(raw_status or "").upper()
    if status == "PORT_DOWN":
        return "PORT_DOWN"
    ...
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_transition_history_export.py::test_transition_history_ignores_port_down_as_on_off_transition tests/test_alert_engine.py::test_normalize_status_preserves_port_down -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add transition_history_export.py tests/test_transition_history_export.py tests/test_alert_engine.py
git commit -m "feat: preserve port down state in exports and alerts"
```

### Task 7: Full Verification

**Files:**
- Modify: `app.py`
- Modify: `alert_engine.py`
- Modify: `current_off_snapshot.py`
- Modify: `transition_history_export.py`
- Modify: `tests/test_port_down_precheck.py`
- Modify: `tests/test_alert_engine.py`
- Modify: `tests/test_transition_history_export.py`

- [ ] **Step 1: Run focused verification**

Run: `pytest tests/test_port_down_precheck.py tests/test_alert_engine.py tests/test_transition_history_export.py -q`
Expected: PASS

- [ ] **Step 2: Run broader regression verification**

Run: `pytest tests/test_cts_port_status_scan.py tests/test_scan_port_status_script.py tests/test_port_status_scan.py tests/test_app_issue_logging.py tests/test_alert_engine.py tests/test_transition_history_export.py -q`
Expected: PASS

- [ ] **Step 3: Commit**

```bash
git add app.py alert_engine.py current_off_snapshot.py transition_history_export.py tests
git commit -m "feat: add port precheck driven port down handling"
```
