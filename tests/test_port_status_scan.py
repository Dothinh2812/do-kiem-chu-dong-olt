import csv
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

try:
    from do_chu_dong_api import app
except ModuleNotFoundError:
    import app


class FakeLocator:
    def __init__(self, selector, visible_selectors, calls):
        self.selector = selector
        self.visible_selectors = visible_selectors
        self.calls = calls

    def wait_for(self, state=None, timeout=None):
        self.calls.append((self.selector, state, timeout))
        if self.selector not in self.visible_selectors:
            raise TimeoutError(self.selector)


class FakePage:
    def __init__(self, visible_selectors):
        self.visible_selectors = set(visible_selectors)
        self.calls = []

    def locator(self, selector):
        return FakeLocator(selector, self.visible_selectors, self.calls)


def read_csv_rows(path):
    with path.open("r", encoding="utf-8", newline="") as file_obj:
        return list(csv.DictReader(file_obj))


def test_first_visible_locator_uses_shorter_timeout_for_fallback_selectors():
    page = FakePage(visible_selectors={"#passOTP"})

    locator = app._first_visible_locator(page, ("missing-selector", "#passOTP"))

    assert locator.selector == "#passOTP"
    assert page.calls == [
        ("missing-selector", "visible", 30000),
        ("#passOTP", "visible", 5000),
    ]


def test_otp_confirm_button_prefers_new_cts_selector():
    assert app.OTP_CONFIRM_BUTTON_SELECTORS[0] == '//*[@id="loginForm"]/section/button'
    assert '//*[@id="loginForm"]/div[1]/button' in app.OTP_CONFIRM_BUTTON_SELECTORS


def test_build_port_status_slot_tasks_deduplicates_frame_slot():
    port_tasks = [
        {
            "deviceIp": "10.31.8.69",
            "frame": "1",
            "slot": "1",
            "port": "1",
            "olt_name": "HNI.STY.XSZ.OLT.ZT.1.1",
        },
        {
            "deviceIp": "10.31.8.69",
            "frame": "1",
            "slot": "1",
            "port": "11",
            "olt_name": "HNI.STY.XSZ.OLT.ZT.1.1",
        },
        {
            "deviceIp": "10.31.8.69",
            "frame": "1",
            "slot": "2",
            "port": "1",
            "olt_name": "HNI.STY.XSZ.OLT.ZT.1.1",
        },
    ]

    slot_tasks = app.build_port_status_slot_tasks(port_tasks)

    assert slot_tasks == [
        {
            "deviceIp": "10.31.8.69",
            "frame": "1",
            "slot": "1",
            "olt_name": "HNI.STY.XSZ.OLT.ZT.1.1",
        },
        {
            "deviceIp": "10.31.8.69",
            "frame": "1",
            "slot": "2",
            "olt_name": "HNI.STY.XSZ.OLT.ZT.1.1",
        },
    ]


def test_normalize_port_status_row_extracts_port_fields():
    slot_task = {
        "deviceIp": "10.31.8.69",
        "frame": "1",
        "slot": "1",
        "olt_name": "HNI.STY.XSZ.OLT.ZT.1.1",
    }
    raw_row = {
        "key": "268438016",
        "ifDescr": "",
        "ifStatus": "Down",
        "ifName": "gpon_1/1/11",
        "slotNo": "1/1/11",
        "tx": 3.41,
        "txXgspon": 0.0,
    }

    normalized = app.normalize_port_status_row(slot_task, raw_row)

    assert normalized["olt_name"] == "HNI.STY.XSZ.OLT.ZT.1.1"
    assert normalized["device_ip"] == "10.31.8.69"
    assert normalized["frame"] == "1"
    assert normalized["slot"] == "1"
    assert normalized["port"] == "11"
    assert normalized["slot_no"] == "1/1/11"
    assert normalized["if_name"] == "gpon_1/1/11"
    assert normalized["if_status"] == "Down"
    assert normalized["tx"] == 3.41
    assert normalized["tx_xgspon"] == 0.0


def test_export_port_status_snapshot_writes_json_and_csv(tmp_path):
    rows = [
        {
            "olt_name": "HNI.STY.XSZ.OLT.ZT.1.1",
            "device_ip": "10.31.8.69",
            "frame": "1",
            "slot": "1",
            "port": "11",
            "slot_no": "1/1/11",
            "if_name": "gpon_1/1/11",
            "if_status": "Down",
            "if_descr": "",
            "key": "268438016",
            "tx": 3.41,
            "tx_xgspon": 0.0,
        }
    ]

    json_path, csv_path = app.export_port_status_snapshot(rows, output_dir=tmp_path)

    assert json_path == tmp_path / "port_status_snapshot.json"
    assert csv_path == tmp_path / "port_status_snapshot.csv"
    assert json.loads(json_path.read_text(encoding="utf-8")) == rows

    csv_rows = read_csv_rows(csv_path)
    assert len(csv_rows) == 1
    assert csv_rows[0]["device_ip"] == "10.31.8.69"
    assert csv_rows[0]["if_status"] == "Down"
    assert csv_rows[0]["port"] == "11"


def test_scan_and_export_all_olt_port_statuses_returns_rows_and_paths(tmp_path, monkeypatch):
    rows = [
        {
            "olt_name": "HNI.STY.XSZ.OLT.ZT.1.1",
            "device_ip": "10.31.8.69",
            "frame": "1",
            "slot": "1",
            "port": "11",
            "slot_no": "1/1/11",
            "if_name": "gpon_1/1/11",
            "if_status": "Down",
            "if_descr": "",
            "key": "268438016",
            "tx": 3.41,
            "tx_xgspon": 0.0,
        }
    ]

    monkeypatch.setattr(app, "scan_all_olt_port_statuses", lambda port_tasks=None: rows)

    result_rows, json_path, csv_path = app.scan_and_export_all_olt_port_statuses(output_dir=tmp_path)

    assert result_rows == rows
    assert json_path.exists()
    assert csv_path.exists()


def test_fetch_port_status_for_slot_raises_clear_error_for_html_login(monkeypatch):
    monkeypatch.setattr(app, "global_cookies", {})
    monkeypatch.setattr(app, "global_headers", {})

    class FakeResponse:
        status_code = 200
        headers = {"content-type": "text/html; charset=utf-8"}
        text = "<html><title>Login</title></html>"

        def raise_for_status(self):
            return None

    monkeypatch.setattr(app.requests, "get", lambda *args, **kwargs: FakeResponse())

    with __import__("pytest").raises(RuntimeError) as exc_info:
        app.fetch_port_status_for_slot(
            {
                "deviceIp": "10.31.8.69",
                "frame": "1",
                "slot": "1",
                "olt_name": "HNI.STY.XSZ.OLT.ZT.1.1",
            }
        )

    assert "non-JSON response" in str(exc_info.value)
    assert "Run perform_browser_login" in str(exc_info.value)


def test_fetch_port_status_for_slot_uses_frame_minus_one(monkeypatch):
    monkeypatch.setattr(app, "global_cookies", {})
    monkeypatch.setattr(app, "global_headers", {})
    captured = {}

    class FakeResponse:
        status_code = 200
        headers = {"content-type": "application/json; charset=utf-8"}

        def raise_for_status(self):
            return None

        def json(self):
            return []

    def fake_get(url, params=None, headers=None, cookies=None, timeout=None):
        captured["url"] = url
        captured["params"] = params
        return FakeResponse()

    monkeypatch.setattr(app.requests, "get", fake_get)

    app.fetch_port_status_for_slot(
        {
            "deviceIp": "10.31.8.69",
            "frame": "1",
            "slot": "1",
            "olt_name": "HNI.STY.XSZ.OLT.ZT.1.1",
        }
    )

    assert captured["url"].endswith("/GetL2PortListBySlot")
    assert captured["params"]["deviceIp"] == "10.31.8.69"
    assert captured["params"]["slot"] == "1"
    assert captured["params"]["frame"] == "-1"
