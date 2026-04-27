# Transition History CSV Export Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Xuất CSV lịch sử chuyển trạng thái cho các thuê bao trong `danhba` có ít nhất 3 lần `ON -> OFF` và 3 lần `OFF -> ON`, sau khi loại các outage wide-area.

**Architecture:** Thêm một module Python nhỏ để đọc `danhba`, `onu_measurements`, `wide_area_alerts`, và `outage_alerts`, dựng transition history theo từng thuê bao, rồi xuất CSV bằng `csv.DictWriter`. Một script CLI mỏng sẽ gọi module này để chạy trên DB thật.

**Tech Stack:** Python, sqlite3, csv, argparse, pytest

---

### Task 1: Add failing tests for transition extraction and filtering

**Files:**
- Create: `tests/test_transition_history_export.py`
- Test: `tests/test_transition_history_export.py`

- [ ] **Step 1: Write the failing test**

```python
def test_collect_transitions_filters_out_wide_area_outages(...):
    ...


def test_export_transition_csv_only_keeps_subscribers_meeting_both_thresholds(...):
    ...
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_transition_history_export.py -v`
Expected: FAIL because the export module does not exist yet.

- [ ] **Step 3: Write minimal implementation**

```python
def collect_qualified_transition_rows(...):
    raise NotImplementedError
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_transition_history_export.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add tests/test_transition_history_export.py transition_history_export.py
git commit -m "test: cover transition history csv export"
```

### Task 2: Add the export module and CLI wrapper

**Files:**
- Create: `transition_history_export.py`
- Create: `scripts/export_transition_history_csv.py`
- Test: `tests/test_transition_history_export.py`

- [ ] **Step 1: Write the failing test**

```python
def test_cli_writer_creates_csv(...):
    ...
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_transition_history_export.py::test_export_transition_csv_only_keeps_subscribers_meeting_both_thresholds -v`
Expected: FAIL until the writer and CLI exist.

- [ ] **Step 3: Write minimal implementation**

```python
def export_transition_history_csv(measurement_db_path, source_db_path, output_path):
    ...
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_transition_history_export.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add transition_history_export.py scripts/export_transition_history_csv.py tests/test_transition_history_export.py
git commit -m "feat: add transition history csv export"
```

### Task 3: Verify against the real database

**Files:**
- Modify: `scripts/export_transition_history_csv.py`

- [ ] **Step 1: Run export on the real DB**

Run: `python3 scripts/export_transition_history_csv.py --measurement-db onu_measurements.db --source-db database.db --output runtime/transition_history_export.csv`
Expected: script exits 0 and prints the output path plus exported row count.

- [ ] **Step 2: Verify the CSV exists and has data**

Run: `python3 - <<'PY'\nimport csv\nfrom pathlib import Path\npath = Path('runtime/transition_history_export.csv')\nwith path.open(newline='', encoding='utf-8') as f:\n    rows = list(csv.DictReader(f))\nprint(path.exists(), len(rows))\nPY`
Expected: `True` and a non-negative row count.

- [ ] **Step 3: Commit**

```bash
git add runtime/transition_history_export.csv
git commit -m "chore: generate transition history csv export"
```
