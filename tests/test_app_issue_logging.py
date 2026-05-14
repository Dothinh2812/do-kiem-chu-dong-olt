import csv
import sys
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

try:
    from do_chu_dong_api import app
except ModuleNotFoundError:
    import app


def read_csv_rows(path):
    with path.open("r", encoding="utf-8", newline="") as file_obj:
        return list(csv.DictReader(file_obj))


def test_append_port_issue_log_creates_batch_csv(tmp_path, monkeypatch):
    monkeypatch.setattr(app, "MEASUREMENT_LOG_DIR", tmp_path)
    monkeypatch.setattr(app, "issue_log_lock", threading.Lock())

    task = {
        "deviceIp": "10.31.10.250",
        "frame": "1",
        "slot": "1",
        "port": "7",
        "olt_name": "HNI.STY.STY.OLT.AL.2.1",
    }

    app.append_port_issue_log(
        batch_id="202604260930",
        task=task,
        status="filtered",
        reason="No matching danhba.sub entries",
        olt_name="HNI.STY.STY.OLT.AL.2.1",
    )

    log_path = tmp_path / "202604260930_port_issues.csv"
    assert log_path.exists()

    rows = read_csv_rows(log_path)
    assert len(rows) == 1
    assert rows[0]["batch_id"] == "202604260930"
    assert rows[0]["status"] == "filtered"
    assert rows[0]["olt_name"] == "HNI.STY.STY.OLT.AL.2.1"
    assert rows[0]["device_ip"] == "10.31.10.250"
    assert rows[0]["frame"] == "1"
    assert rows[0]["slot"] == "1"
    assert rows[0]["port"] == "7"
    assert rows[0]["port_label"] == "10.31.10.250_F1_S1_P7"
    assert rows[0]["reason"] == "No matching danhba.sub entries"


def test_download_single_port_logs_filtered_issue(tmp_path, monkeypatch):
    monkeypatch.setattr(app, "MEASUREMENT_LOG_DIR", tmp_path)
    monkeypatch.setattr(app, "issue_log_lock", threading.Lock())
    monkeypatch.setattr(app, "device_semaphores", {"10.31.10.250": threading.Semaphore(1)})
    monkeypatch.setattr(app, "global_cookies", {})
    monkeypatch.setattr(app, "global_headers", {})
    monkeypatch.setattr(app, "build_measurement_records", lambda *args, **kwargs: [])

    class FakeResponse:
        status_code = 200
        headers = {"content-type": "application/json"}

        def json(self):
            return [{"frameNo": 1, "slotNo": 1, "portNo": 7, "onuIndex": 1}]

    monkeypatch.setattr(app.requests, "get", lambda *args, **kwargs: FakeResponse())

    task = {
        "deviceIp": "10.31.10.250",
        "frame": "1",
        "slot": "1",
        "port": "7",
        "olt_name": "HNI.STY.STY.OLT.AL.2.1",
    }

    result = app.download_single_port(1, 1, task, "202604260931")

    assert result == "filtered"
    rows = read_csv_rows(tmp_path / "202604260931_port_issues.csv")
    assert len(rows) == 1
    assert rows[0]["status"] == "filtered"
    assert "No matching danhba.sub entries" in rows[0]["reason"]


def test_download_single_port_logs_invalid_json_as_error(tmp_path, monkeypatch):
    monkeypatch.setattr(app, "MEASUREMENT_LOG_DIR", tmp_path)
    monkeypatch.setattr(app, "issue_log_lock", threading.Lock())
    monkeypatch.setattr(app, "device_semaphores", {"10.31.10.250": threading.Semaphore(1)})
    monkeypatch.setattr(app, "global_cookies", {})
    monkeypatch.setattr(app, "global_headers", {})

    class FakeResponse:
        status_code = 200
        headers = {"content-type": "application/json"}

        def json(self):
            raise ValueError("invalid json")

    monkeypatch.setattr(app.requests, "get", lambda *args, **kwargs: FakeResponse())

    task = {
        "deviceIp": "10.31.10.250",
        "frame": "1",
        "slot": "1",
        "port": "7",
        "olt_name": "HNI.STY.STY.OLT.AL.2.1",
    }

    result = app.download_single_port(1, 1, task, "202604260932")

    assert result == "error"
    rows = read_csv_rows(tmp_path / "202604260932_port_issues.csv")
    assert len(rows) == 1
    assert rows[0]["status"] == "error"
    assert "Invalid JSON response" in rows[0]["reason"]
