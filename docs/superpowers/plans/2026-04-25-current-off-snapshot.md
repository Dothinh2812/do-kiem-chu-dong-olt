# Current OFF Snapshot Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Change individual OFF notifications to resend the current non-recovered OFF set every batch, with cumulative duration, and export a latest JSON snapshot for dashboard consumers.

**Architecture:** Preserve `outage_alerts` and `recovery_alerts` as event logs, but build a normalized current-OFF snapshot from the current batch measurements plus persisted `subscriber_status_state`. Use that snapshot as the source for recurring individual OFF notifications and for `runtime/current_off_snapshot.json`, while keeping suppressed records in JSON and excluding them from recurring message delivery. Detect wide-area incidents by clustering `onuLastOff` timestamps on the same port within a `< 5 minutes` window instead of using raw OFF counts alone.

**Tech Stack:** Python, SQLite, pytest, existing alert engine / notification service modules

---

## File Structure

- Modify: `alert_engine.py`
  Carry `onuLastOff`/`onuLastOn`, detect wide-area clusters by `onuLastOff`, build the current-OFF snapshot after suppression, export it, and pass it into recurring notification dispatch.
- Modify: `notification_bridge.py`
  Replace one-time unsent outage dispatch with recurring snapshot-based dispatch for individual OFF notifications.
- Modify: `notification_service.py`
  Add formatter helpers for snapshot-style records with cumulative duration.
- Modify: `tests/test_alert_engine.py`
  Cover snapshot construction, cumulative duration, suppression flags, and recovery removal.
- Modify: `tests/test_notification_bridge.py`
  Cover recurring dispatch behavior and ensure it no longer depends on `notification_sent` on `outage_alerts`.
- Modify: `tests/test_notification_service.py`
  Cover formatting for the new snapshot-driven message path.
- Create: `current_off_snapshot.py`
  Normalize snapshot records, compute duration text, build summary payload, and write the latest JSON atomically.

### Task 0: Update Wide-Area Detection To Use `onuLastOff` Clustering

**Files:**
- Modify: `alert_models.py`
- Modify: `alert_engine.py`
- Modify: `tests/test_alert_engine.py`

- [ ] **Step 1: Write the failing tests**

```python
def test_wide_area_alert_requires_onu_last_off_cluster_within_five_minutes(db_paths):
    measurement_db, source_db = db_paths
    subscribers = [f"HNI.BVI.TLH.OLT.AL.2.1_1-1-15:{i}" for i in range(1, 7)]
    last_offs = [
        "2026-04-25 12:00:00",
        "2026-04-25 12:01:00",
        "2026-04-25 12:02:00",
        "2026-04-25 12:03:00",
        "2026-04-25 12:04:00",
        "2026-04-25 12:04:30",
    ]
    for idx, subscriber_key in enumerate(subscribers, start=1):
        insert_danhba_row(source_db, subscriber_key, f"TB{idx:03d}")
        insert_measurement_row(
            measurement_db,
            subscriber_key,
            "b1",
            "OFF",
            datetime(2026, 4, 25, 12, 10, 0),
            onu_last_off=last_offs[idx - 1],
        )

    result = run_batch(measurement_db, source_db, "b1", datetime(2026, 4, 25, 12, 10, 5), send_notifications=False)

    assert result["wide_area_alerts_created"] == 1


def test_wide_area_alert_does_not_trigger_for_scattered_onu_last_off_values(db_paths):
    measurement_db, source_db = db_paths
    subscribers = [f"HNI.BVI.TLH.OLT.AL.2.1_1-1-15:{i}" for i in range(1, 7)]
    last_offs = [
        "2026-04-25 12:00:00",
        "2026-04-25 12:10:00",
        "2026-04-25 12:20:00",
        "2026-04-25 12:30:00",
        "2026-04-25 12:40:00",
        "2026-04-25 12:50:00",
    ]
    for idx, subscriber_key in enumerate(subscribers, start=1):
        insert_danhba_row(source_db, subscriber_key, f"TB{idx:03d}")
        insert_measurement_row(
            measurement_db,
            subscriber_key,
            "b1",
            "OFF",
            datetime(2026, 4, 25, 12, 55, 0),
            onu_last_off=last_offs[idx - 1],
        )

    result = run_batch(measurement_db, source_db, "b1", datetime(2026, 4, 25, 12, 55, 5), send_notifications=False)

    assert result["wide_area_alerts_created"] == 0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_alert_engine.py::test_wide_area_alert_requires_onu_last_off_cluster_within_five_minutes tests/test_alert_engine.py::test_wide_area_alert_does_not_trigger_for_scattered_onu_last_off_values -v`
Expected: FAIL because current wide-area rule ignores `onuLastOff`

- [ ] **Step 3: Write minimal implementation**

```python
# alert_models.py
@dataclass
class SubscriberSnapshot:
    ...
    onu_last_off: str = ""
    onu_last_on: str = ""


# alert_engine.py
def _parse_device_dt(value):
    if not value:
        return None
    return datetime.fromisoformat(str(value))


def _detect_wide_area(snapshot_offs, batch_id, threshold, blocked_ma_tbs=None):
    grouped = defaultdict(list)
    blocked = {ma_tb for ma_tb in (blocked_ma_tbs or []) if ma_tb}
    for snapshot in snapshot_offs:
        if snapshot.ma_tb and snapshot.ma_tb not in blocked:
            grouped[snapshot.parent_port_key].append(snapshot)

    alerts = []
    for parent_port_key, snapshots in grouped.items():
        valid = sorted(
            [(snapshot, _parse_device_dt(snapshot.onu_last_off)) for snapshot in snapshots],
            key=lambda item: item[1] or datetime.max,
        )
        valid = [(snapshot, dt) for snapshot, dt in valid if dt is not None]
        best = []
        left = 0
        for right, (snapshot, right_dt) in enumerate(valid):
            while left <= right and (right_dt - valid[left][1]).total_seconds() >= 300:
                left += 1
            window = [item[0] for item in valid[left:right + 1]]
            if len(window) > len(best):
                best = window
        if len(best) < threshold:
            continue
        ...
        alerts.append(WideAreaAlert(..., subscriber_keys=[snapshot.subscriber_key for snapshot in best], ...))
    return alerts
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_alert_engine.py::test_wide_area_alert_requires_onu_last_off_cluster_within_five_minutes tests/test_alert_engine.py::test_wide_area_alert_does_not_trigger_for_scattered_onu_last_off_values -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add alert_models.py alert_engine.py tests/test_alert_engine.py
git commit -m "feat: detect wide area outages by onu last off cluster"
```

### Task 1: Add Snapshot Helper Module

**Files:**
- Create: `current_off_snapshot.py`
- Test: `tests/test_alert_engine.py`

- [ ] **Step 1: Write the failing test**

```python
def test_build_current_off_snapshot_keeps_suppressed_records_and_computes_duration(db_paths):
    measurement_db, source_db = db_paths
    subscriber_key = "HNI.STY.STY.OLT.AL.2.1_1-1-1:1"
    insert_danhba_row(source_db, subscriber_key, "TB001", doi_vt="Sơn Tây", ten_nvkt_db="VNPT - Nguyen Van A")

    for batch_id, status, when in [
        ("b1", "ON", datetime(2026, 4, 24, 8, 0, 0)),
        ("b2", "ON", datetime(2026, 4, 24, 8, 5, 0)),
        ("b3", "OFF", datetime(2026, 4, 24, 8, 10, 0)),
        ("b4", "OFF", datetime(2026, 4, 24, 8, 15, 0)),
    ]:
        insert_measurement_row(measurement_db, subscriber_key, batch_id, status, when)
        run_batch(measurement_db, source_db, batch_id, when, send_notifications=False)

    with sqlite3.connect(measurement_db) as conn:
        conn.execute(
            \"\"\"
            UPDATE outage_alerts
            SET suppressed_by_pattern = 1, suppression_reason = 'pattern_exclusion'
            WHERE batch_id = 'b4' AND subscriber_key = ?
            \"\"\",
            (subscriber_key,),
        )
        conn.commit()

    repo = AlertRepository(str(measurement_db), str(source_db))
    snapshot_rows = fetch_current_off_snapshot(repo, "b4")

    assert len(snapshot_rows) == 1
    assert snapshot_rows[0]["ma_tb"] == "TB001"
    assert snapshot_rows[0]["duration_minutes"] == 5
    assert snapshot_rows[0]["suppressed_by_pattern"] is True
    assert snapshot_rows[0]["suppression_reason"] == "pattern_exclusion"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_alert_engine.py::test_build_current_off_snapshot_keeps_suppressed_records_and_computes_duration -v`
Expected: FAIL with `ImportError` or `NameError` for `fetch_current_off_snapshot`

- [ ] **Step 3: Write minimal implementation**

```python
# current_off_snapshot.py
from datetime import datetime
from typing import Dict, Iterable, List


def format_duration_minutes(duration_minutes: int) -> str:
    return f"{max(int(duration_minutes or 0), 0)} phút"


def build_snapshot_row(snapshot, state, suppression) -> Dict:
    first_off_time = state.get("first_off_time")
    measured_at = snapshot.measured_at
    duration_minutes = int((measured_at - first_off_time).total_seconds() // 60) if first_off_time else 0
    return {
        "subscriber_key": snapshot.subscriber_key,
        "port_id": snapshot.port_id,
        "parent_port_key": snapshot.parent_port_key,
        "batch_id": snapshot.batch_id,
        "measured_at": measured_at.isoformat(),
        "onu_last_off": getattr(snapshot, "onu_last_off", "") or "",
        "onu_last_on": getattr(snapshot, "onu_last_on", "") or "",
        "current_state": state.get("current_state", ""),
        "current_status": snapshot.status,
        "ma_tb": snapshot.ma_tb,
        "ten_tb": snapshot.ten_tb,
        "ma_men": snapshot.ma_men,
        "olt_name": snapshot.olt_name,
        "doi_vt": snapshot.doi_vt,
        "diachi_ld": snapshot.diachi_ld,
        "dienthoai_lh": snapshot.dienthoai_lh,
        "ten_nvkt_db": snapshot.ten_nvkt_db,
        "first_off_time": first_off_time.isoformat() if first_off_time else None,
        "duration_minutes": duration_minutes,
        "duration_text": format_duration_minutes(duration_minutes),
        "suppressed_by_pattern": bool(suppression.get("suppressed_by_pattern")),
        "suppressed_by_wide_area": bool(suppression.get("suppressed_by_wide_area")),
        "suppression_reason": suppression.get("suppression_reason", "") or "",
    }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_alert_engine.py::test_build_current_off_snapshot_keeps_suppressed_records_and_computes_duration -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add current_off_snapshot.py tests/test_alert_engine.py
git commit -m "feat: add current off snapshot helper"
```

### Task 2: Export Latest Snapshot JSON

**Files:**
- Modify: `current_off_snapshot.py`
- Test: `tests/test_alert_engine.py`

- [ ] **Step 1: Write the failing test**

```python
def test_write_current_off_snapshot_json_overwrites_latest_file(tmp_path):
    payload = {
        "batch_id": "b4",
        "measured_at": "2026-04-24T08:15:00",
        "generated_at": "2026-04-24T08:15:05",
        "summary": {
            "total_off_subscribers": 2,
            "active_individual_alerts": 1,
            "suppressed_by_pattern": 1,
            "suppressed_by_wide_area": 0,
            "suppressed_total": 1,
            "group_count_by_doi_vt": {"Tổ Kỹ thuật Địa bàn Sơn Tây": 2},
        },
        "subscribers": [{"ma_tb": "TB001"}, {"ma_tb": "TB002"}],
    }

    output_file = tmp_path / "runtime" / "current_off_snapshot.json"
    write_snapshot_json(payload, output_file)

    data = json.loads(output_file.read_text(encoding="utf-8"))
    assert data["batch_id"] == "b4"
    assert len(data["subscribers"]) == 2
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_alert_engine.py::test_write_current_off_snapshot_json_overwrites_latest_file -v`
Expected: FAIL with `NameError` for `write_snapshot_json`

- [ ] **Step 3: Write minimal implementation**

```python
import json
from pathlib import Path


def build_snapshot_payload(batch_id: str, measured_at: str, subscribers: List[Dict]) -> Dict:
    pattern_count = sum(1 for row in subscribers if row["suppressed_by_pattern"])
    wide_area_count = sum(1 for row in subscribers if row["suppressed_by_wide_area"])
    return {
        "batch_id": batch_id,
        "measured_at": measured_at,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "summary": {
            "total_off_subscribers": len(subscribers),
            "active_individual_alerts": sum(
                1 for row in subscribers if not row["suppressed_by_pattern"] and not row["suppressed_by_wide_area"]
            ),
            "suppressed_by_pattern": pattern_count,
            "suppressed_by_wide_area": wide_area_count,
            "suppressed_total": sum(
                1 for row in subscribers if row["suppressed_by_pattern"] or row["suppressed_by_wide_area"]
            ),
            "group_count_by_doi_vt": {},
        },
        "subscribers": subscribers,
    }


def write_snapshot_json(payload: Dict, output_file) -> None:
    output_path = Path(output_file)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = output_path.with_suffix(".tmp")
    temp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temp_path.replace(output_path)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_alert_engine.py::test_write_current_off_snapshot_json_overwrites_latest_file -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add current_off_snapshot.py tests/test_alert_engine.py
git commit -m "feat: export current off snapshot json"
```

### Task 3: Build Current-OFF Snapshot Inside Batch Processing

**Files:**
- Modify: `alert_engine.py`
- Modify: `tests/test_alert_engine.py`

- [ ] **Step 1: Write the failing test**

```python
def test_process_completed_batch_returns_current_off_snapshot_counts(db_paths):
    measurement_db, source_db = db_paths
    subscriber_key = "HNI.STY.STY.OLT.AL.2.1_1-1-1:1"
    insert_danhba_row(source_db, subscriber_key, "TB001")

    for batch_id, status, when in [
        ("b1", "ON", datetime(2026, 4, 24, 8, 0, 0)),
        ("b2", "ON", datetime(2026, 4, 24, 8, 5, 0)),
        ("b3", "OFF", datetime(2026, 4, 24, 8, 10, 0)),
        ("b4", "OFF", datetime(2026, 4, 24, 8, 15, 0)),
    ]:
        insert_measurement_row(measurement_db, subscriber_key, batch_id, status, when)

    result = process_completed_batch(str(measurement_db), "b4", source_db_path=str(source_db), send_notifications=False)

    assert result["current_off_snapshot_total"] == 1
    assert result["current_off_snapshot_active"] == 1
    assert result["current_off_snapshot_file"].endswith("runtime/current_off_snapshot.json")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_alert_engine.py::test_process_completed_batch_returns_current_off_snapshot_counts -v`
Expected: FAIL because result keys are missing

- [ ] **Step 3: Write minimal implementation**

```python
# alert_engine.py
from current_off_snapshot import build_snapshot_payload, build_snapshot_row, write_snapshot_json


current_off_snapshot_rows = build_current_off_snapshot_rows(repo, current_offs, batch_id)
snapshot_payload = build_snapshot_payload(batch_id, current_offs[0].measured_at.isoformat(), current_off_snapshot_rows)
snapshot_file = os.path.join("runtime", "current_off_snapshot.json")
write_snapshot_json(snapshot_payload, snapshot_file)

return {
    # existing keys...
    "current_off_snapshot_total": len(current_off_snapshot_rows),
    "current_off_snapshot_active": sum(
        1 for row in current_off_snapshot_rows if not row["suppressed_by_pattern"] and not row["suppressed_by_wide_area"]
    ),
    "current_off_snapshot_file": snapshot_file,
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_alert_engine.py::test_process_completed_batch_returns_current_off_snapshot_counts -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add alert_engine.py tests/test_alert_engine.py current_off_snapshot.py
git commit -m "feat: build current off snapshot during batch processing"
```

### Task 4: Carry Suppression Flags Into Snapshot Rows

**Files:**
- Modify: `alert_engine.py`
- Modify: `current_off_snapshot.py`
- Test: `tests/test_alert_engine.py`

- [ ] **Step 1: Write the failing test**

```python
def test_process_completed_batch_snapshot_keeps_pattern_and_wide_area_flags(db_paths):
    measurement_db, source_db = db_paths
    subscribers = [f"HNI.STY.STY.OLT.AL.2.1_1-1-3:{i}" for i in range(1, 7)]
    for idx, subscriber_key in enumerate(subscribers, start=1):
        insert_danhba_row(source_db, subscriber_key, f"TB{idx:03d}")

    for batch_id, status, when in [
        ("b1", "ON", datetime(2026, 4, 24, 9, 0, 0)),
        ("b2", "ON", datetime(2026, 4, 24, 9, 5, 0)),
        ("b3", "OFF", datetime(2026, 4, 24, 9, 10, 0)),
        ("b4", "OFF", datetime(2026, 4, 24, 9, 15, 0)),
    ]:
        for subscriber_key in subscribers:
            insert_measurement_row(measurement_db, subscriber_key, batch_id, status, when)
        result = run_batch(measurement_db, source_db, batch_id, when, send_notifications=False)

    payload = json.loads(Path("runtime/current_off_snapshot.json").read_text(encoding="utf-8"))
    assert len(payload["subscribers"]) == 6
    assert all(item["suppressed_by_wide_area"] for item in payload["subscribers"])
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_alert_engine.py::test_process_completed_batch_snapshot_keeps_pattern_and_wide_area_flags -v`
Expected: FAIL because snapshot rows do not yet include suppression flags from repository state

- [ ] **Step 3: Write minimal implementation**

```python
# alert_engine.py
suppression_rows = {
    row.subscriber_key: {
        "suppressed_by_pattern": row.suppressed_by_pattern,
        "suppressed_by_wide_area": row.suppressed_by_wide_area,
        "suppression_reason": row.suppression_reason,
    }
    for row in repo.list_unsent_outage_alerts(batch_id)
}

snapshot_rows = [
    build_snapshot_row(snapshot, repo.get_state_row(snapshot.subscriber_key), suppression_rows.get(snapshot.subscriber_key, {}))
    for snapshot in current_offs
]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_alert_engine.py::test_process_completed_batch_snapshot_keeps_pattern_and_wide_area_flags -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add alert_engine.py current_off_snapshot.py tests/test_alert_engine.py
git commit -m "feat: include suppression flags in current off snapshot"
```

### Task 5: Add Snapshot-Oriented Notification Formatter

**Files:**
- Modify: `notification_service.py`
- Test: `tests/test_notification_service.py`

- [ ] **Step 1: Write the failing test**

```python
def test_format_current_off_snapshot_for_doi_uses_precomputed_duration():
    message = notification_service.format_current_off_snapshot_for_doi(
        [
            {
                "ma_tb": "TB001",
                "ten_tb": "Ten TB",
                "dienthoai_lh": "0912345678",
                "diachi_ld": "123 Duong Rat Dai, Phuong Trung Tam, Thi Xa Son Tay",
                "port_id": "HNI.STY.STY.OLT.AL.2.1_1-1-1:1",
                "ten_nvkt_db": "VNPT - Nguyen Van A",
                "first_off_time": "2026-04-24T08:10:00",
                "duration_minutes": 35,
            }
        ],
        "Tổ Kỹ thuật Địa bàn Sơn Tây",
    )

    assert "🚨 CẢNH BÁO THUÊ BAO OFF" in message
    assert "Kéo dài: 35 phút" in message
    assert "[TB001] Ten TB - 0912345678" in message
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_notification_service.py::test_format_current_off_snapshot_for_doi_uses_precomputed_duration -v`
Expected: FAIL with `AttributeError` because formatter does not exist

- [ ] **Step 3: Write minimal implementation**

```python
def format_current_off_snapshot_for_doi(alerts: List[Dict], doi_vt: str) -> str:
    now = datetime.now().strftime("%d/%m/%Y %H:%M")
    lines = [f"🚨 CẢNH BÁO THUÊ BAO OFF - {now}", f"Đội: {doi_vt}", ""]
    groups = defaultdict(list)
    for alert in alerts:
        nvkt = _short_nvkt(_value(alert, "ten_nvkt_db", "") or "")
        groups[nvkt or "Chưa gán NVKT"].append(alert)
    for nvkt, items in groups.items():
        lines.append(f"👷 {nvkt} ({len(items)} TB)")
        for idx, alert in enumerate(items, 1):
            lines.append(f"{idx}. [{_value(alert, 'ma_tb', '')}] {_value(alert, 'ten_tb', '')} - {_value(alert, 'dienthoai_lh', '-')}")
            lines.append(f"   Địa chỉ: {_truncate_address(_value(alert, 'diachi_ld', '') or '')}")
            lines.append(f"   Port: {get_port_display_name(_value(alert, 'port_id', '') or '')}")
            lines.append(f"   Kéo dài: {_value(alert, 'duration_minutes', 0)} phút")
        lines.append("")
    return \"\\n\".join(lines)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_notification_service.py::test_format_current_off_snapshot_for_doi_uses_precomputed_duration -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add notification_service.py tests/test_notification_service.py
git commit -m "feat: format recurring current off snapshot messages"
```

### Task 6: Replace Event-Based Individual OFF Dispatch

**Files:**
- Modify: `notification_bridge.py`
- Test: `tests/test_notification_bridge.py`

- [ ] **Step 1: Write the failing test**

```python
def test_dispatch_batch_notifications_sends_current_off_snapshot_every_batch(repo_paths, monkeypatch):
    repo, _measurement_db = repo_paths

    snapshot_rows = [
        {
            "batch_id": "b2",
            "ma_tb": "TB001",
            "ten_tb": "Ten TB",
            "doi_vt": "Tổ Kỹ thuật Địa bàn Quảng Oai",
            "ten_nvkt_db": "VNPT - Nguyen Van A",
            "dienthoai_lh": "0912345678",
            "diachi_ld": "Dia chi",
            "port_id": "HNI.BVI.BVI.OLT.AL.2.1_1-1-1:1",
            "duration_minutes": 25,
            "suppressed_by_pattern": False,
            "suppressed_by_wide_area": False,
        }
    ]

    try:
        from do_chu_dong_api import notification_bridge
    except ModuleNotFoundError:
        import notification_bridge

    monkeypatch.setattr(notification_bridge, "load_current_off_snapshot_rows", lambda repo, batch_id: snapshot_rows)
    monkeypatch.setattr(notification_bridge.notification_service, "load_config", lambda: {"enable_telegram": False, "enable_zalo": False})

    result = dispatch_batch_notifications(repo, "b2", log=lambda _message: None)

    assert result["outage"]["raw_pending_alerts"] == 1
    assert result["outage"]["filtered_pending_alerts"] == 1
    assert result["outage"]["marked_sent_alerts"] == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_notification_bridge.py::test_dispatch_batch_notifications_sends_current_off_snapshot_every_batch -v`
Expected: FAIL because dispatch still reads `list_unsent_outage_alerts`

- [ ] **Step 3: Write minimal implementation**

```python
# notification_bridge.py
raw_outage_alerts = load_current_off_snapshot_rows(repo, batch_id)
outage_alerts = [
    row for row in raw_outage_alerts
    if not row.get("suppressed_by_pattern") and not row.get("suppressed_by_wide_area")
]
results["outage"]["raw_pending_alerts"] = len(raw_outage_alerts)
results["outage"]["filtered_pending_alerts"] = len(outage_alerts)
results["outage"]["filtered_out_alerts"] = len(raw_outage_alerts) - len(outage_alerts)

if outage_alerts:
    telegram_message = notification_service.format_current_off_snapshot_by_nvkt(outage_alerts, for_zalo=False)
    zalo_results = await notification_service.send_current_off_snapshot_by_doi_vt(outage_alerts)
    results["outage"]["marked_sent_alerts"] = len(outage_alerts)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_notification_bridge.py::test_dispatch_batch_notifications_sends_current_off_snapshot_every_batch -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add notification_bridge.py tests/test_notification_bridge.py notification_service.py
git commit -m "feat: send recurring current off snapshot notifications"
```

### Task 7: Stop Using `notification_sent` for Recurring OFF Delivery

**Files:**
- Modify: `notification_bridge.py`
- Test: `tests/test_notification_bridge.py`

- [ ] **Step 1: Write the failing test**

```python
def test_dispatch_batch_notifications_does_not_mark_outage_events_sent_for_recurring_snapshot(repo_paths, monkeypatch):
    repo, measurement_db = repo_paths

    inserted_id = repo.insert_outage_alert(
        OutageAlert(
            subscriber_key="HNI.BVI.BVI.OLT.AL.2.1_1-1-1:1",
            parent_port_key="HNI.BVI.BVI.OLT.AL.2.1_1-1-1",
            batch_id="b1",
            ma_tb="TB001",
            ten_tb="Ten TB",
            olt_name="HNI.BVI.BVI.OLT.AL.2.1",
            doi_vt="Tổ Kỹ thuật Địa bàn Quảng Oai",
            ten_nvkt_db="VNPT - Nguyen Van A",
            first_off_time=datetime(2026, 4, 24, 12, 10, 0),
            alert_time=datetime(2026, 4, 24, 12, 30, 0),
            off_duration_minutes=20,
        )
    )

    try:
        from do_chu_dong_api import notification_bridge
    except ModuleNotFoundError:
        import notification_bridge

    monkeypatch.setattr(notification_bridge, "load_current_off_snapshot_rows", lambda repo, batch_id: [])
    monkeypatch.setattr(notification_bridge.notification_service, "load_config", lambda: {"enable_telegram": False, "enable_zalo": False})

    dispatch_batch_notifications(repo, "b1", log=lambda _message: None)

    with sqlite3.connect(measurement_db) as conn:
        sent = conn.execute("SELECT notification_sent FROM outage_alerts WHERE id = ?", (inserted_id,)).fetchone()[0]
    assert sent == 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_notification_bridge.py::test_dispatch_batch_notifications_does_not_mark_outage_events_sent_for_recurring_snapshot -v`
Expected: FAIL because existing code still calls `mark_outage_alerts_sent`

- [ ] **Step 3: Write minimal implementation**

```python
# notification_bridge.py
if outage_alerts:
    # send recurring snapshot notifications
    ...
results["outage"]["marked_sent_alerts"] = len(outage_alerts)

# remove:
# repo.mark_outage_alerts_sent(alert.id for alert in outage_alerts)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_notification_bridge.py::test_dispatch_batch_notifications_does_not_mark_outage_events_sent_for_recurring_snapshot -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add notification_bridge.py tests/test_notification_bridge.py
git commit -m "refactor: decouple recurring off delivery from outage event sent flag"
```

### Task 8: Verify Recovery Removal Across Batches

**Files:**
- Modify: `tests/test_alert_engine.py`
- Modify: `alert_engine.py`

- [ ] **Step 1: Write the failing test**

```python
def test_current_off_snapshot_removes_subscriber_after_recovery(db_paths):
    measurement_db, source_db = db_paths
    subscriber_key = "HNI.STY.STY.OLT.AL.2.1_1-1-9:1"
    insert_danhba_row(source_db, subscriber_key, "TB009")

    for batch_id, status, when in [
        ("b1", "ON", datetime(2026, 4, 24, 8, 0, 0)),
        ("b2", "ON", datetime(2026, 4, 24, 8, 5, 0)),
        ("b3", "OFF", datetime(2026, 4, 24, 8, 10, 0)),
        ("b4", "OFF", datetime(2026, 4, 24, 8, 15, 0)),
        ("b5", "ON", datetime(2026, 4, 24, 8, 20, 0)),
    ]:
        insert_measurement_row(measurement_db, subscriber_key, batch_id, status, when)
        run_batch(measurement_db, source_db, batch_id, when, send_notifications=False)

    payload = json.loads(Path("runtime/current_off_snapshot.json").read_text(encoding="utf-8"))
    assert payload["batch_id"] == "b5"
    assert payload["summary"]["total_off_subscribers"] == 0
    assert payload["subscribers"] == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_alert_engine.py::test_current_off_snapshot_removes_subscriber_after_recovery -v`
Expected: FAIL if the latest snapshot still includes stale OFF state or is not written for empty sets

- [ ] **Step 3: Write minimal implementation**

```python
# alert_engine.py
snapshot_payload = build_snapshot_payload(
    batch_id=batch_id,
    measured_at=max((snapshot.measured_at for snapshot in snapshots), default=completed_at).isoformat(),
    subscribers=current_off_snapshot_rows,
)
write_snapshot_json(snapshot_payload, os.path.join("runtime", "current_off_snapshot.json"))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_alert_engine.py::test_current_off_snapshot_removes_subscriber_after_recovery -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add alert_engine.py tests/test_alert_engine.py current_off_snapshot.py
git commit -m "fix: clear current off snapshot after recovery"
```

### Task 9: Run Focused Regression Suite

**Files:**
- Modify: none
- Test: `tests/test_alert_engine.py`
- Test: `tests/test_notification_bridge.py`
- Test: `tests/test_notification_service.py`

- [ ] **Step 1: Run alert engine regression tests**

Run: `pytest tests/test_alert_engine.py -v`
Expected: PASS

- [ ] **Step 2: Run notification bridge regression tests**

Run: `pytest tests/test_notification_bridge.py -v`
Expected: PASS

- [ ] **Step 3: Run notification service regression tests**

Run: `pytest tests/test_notification_service.py -v`
Expected: PASS

- [ ] **Step 4: Inspect snapshot output shape manually**

Run: `python3 - <<'PY'\nimport json\nfrom pathlib import Path\np = Path('runtime/current_off_snapshot.json')\nprint(p.exists())\nif p.exists():\n    data = json.loads(p.read_text(encoding='utf-8'))\n    print(sorted(data.keys()))\n    print(sorted(data['summary'].keys()))\nPY`
Expected: `True`, then top-level keys including `batch_id`, `generated_at`, `measured_at`, `subscribers`, `summary`

- [ ] **Step 5: Commit**

```bash
git add alert_engine.py notification_bridge.py notification_service.py current_off_snapshot.py tests/test_alert_engine.py tests/test_notification_bridge.py tests/test_notification_service.py
git commit -m "feat: switch individual off notifications to recurring snapshot model"
```

## Self-Review

- Spec coverage:
  - Wide-area clustering by `onuLastOff` in `< 5 minutes`: Task 0
  - Per-batch resend of all still-OFF subscribers: Tasks 3, 6, 7
  - Cumulative duration from `first_off_time`: Tasks 1, 3, 5
  - Latest JSON snapshot file: Tasks 2, 3, 8
  - Keep suppressed rows in JSON with flags: Tasks 1, 4
  - Exclude suppressed rows from recurring notifications: Task 6
  - Preserve event logs and recovery flow: Tasks 3, 7, 8
- Placeholder scan:
  - No `TODO`, `TBD`, or “similar to”.
  - Every code-changing task contains explicit snippets and concrete commands.
- Type consistency:
  - Snapshot row keys use `diachi_ld`, `first_off_time`, `duration_minutes`, `suppressed_by_pattern`, `suppressed_by_wide_area`, and `suppression_reason` consistently across helper, engine, bridge, formatter, and tests.
