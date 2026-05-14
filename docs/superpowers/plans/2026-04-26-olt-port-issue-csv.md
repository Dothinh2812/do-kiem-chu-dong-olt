# OLT Port Issue CSV Logging Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ghi file CSV theo từng batch cho các cổng đo OLT bị `filtered` hoặc `error`.

**Architecture:** Giữ logic phân loại trạng thái tại `download_single_port`, thêm helper xây đường dẫn và append CSV thread-safe trong `app.py`, rồi bao phủ bằng test nhỏ cho helper và hai nhánh worker đại diện.

**Tech Stack:** Python, pytest, csv, pathlib, threading, requests mocking via monkeypatch

---

### Task 1: Add failing tests for CSV issue logging

**Files:**
- Create: `tests/test_app_issue_logging.py`
- Test: `tests/test_app_issue_logging.py`

- [ ] **Step 1: Write the failing test**

```python
def test_append_port_issue_log_creates_batch_csv(...):
    ...

def test_download_single_port_logs_filtered_issue(...):
    ...

def test_download_single_port_logs_invalid_json_as_error(...):
    ...
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_app_issue_logging.py -v`
Expected: FAIL because the CSV logging helpers do not exist yet.

- [ ] **Step 3: Write minimal implementation**

```python
def get_batch_issue_log_path(batch_id):
    ...

def append_port_issue_log(batch_id, task, status, reason, olt_name=None):
    ...
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_app_issue_logging.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add tests/test_app_issue_logging.py app.py
git commit -m "feat: add batch csv logging for filtered and error ports"
```

### Task 2: Wire worker branches to CSV logging

**Files:**
- Modify: `app.py`
- Test: `tests/test_app_issue_logging.py`

- [ ] **Step 1: Write the failing worker behavior assertions**

```python
assert rows[0]["status"] == "filtered"
assert "No matching danhba.sub entries" in rows[0]["reason"]
```

- [ ] **Step 2: Run targeted tests to verify failure**

Run: `pytest tests/test_app_issue_logging.py::test_download_single_port_logs_filtered_issue tests/test_app_issue_logging.py::test_download_single_port_logs_invalid_json_as_error -v`
Expected: FAIL until worker branches call the CSV helper.

- [ ] **Step 3: Write minimal implementation**

```python
append_port_issue_log(batch_id, task, "filtered", reason, olt_name=olt_name)
append_port_issue_log(batch_id, task, "error", reason, olt_name=olt_name)
```

- [ ] **Step 4: Run targeted tests to verify they pass**

Run: `pytest tests/test_app_issue_logging.py::test_download_single_port_logs_filtered_issue tests/test_app_issue_logging.py::test_download_single_port_logs_invalid_json_as_error -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add app.py tests/test_app_issue_logging.py
git commit -m "feat: wire csv issue logging into olt measurement worker"
```

### Task 3: Verify regression safety

**Files:**
- Modify: `app.py`
- Test: `tests/test_app_issue_logging.py`

- [ ] **Step 1: Run the focused suite**

Run: `pytest tests/test_app_issue_logging.py tests/test_notification_bridge.py tests/test_notification_service.py -q`
Expected: PASS

- [ ] **Step 2: If needed, adjust message strings or helper behavior**

```python
# Keep reason strings readable and stable for operators opening CSV files.
```

- [ ] **Step 3: Re-run the focused suite**

Run: `pytest tests/test_app_issue_logging.py tests/test_notification_bridge.py tests/test_notification_service.py -q`
Expected: PASS

- [ ] **Step 4: Commit**

```bash
git add app.py tests/test_app_issue_logging.py
git commit -m "test: verify batch csv issue logging"
```
