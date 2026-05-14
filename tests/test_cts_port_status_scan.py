import csv
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

try:
    from do_chu_dong_api import cts_port_status_scan
except ModuleNotFoundError:
    import cts_port_status_scan


def read_csv_rows(path):
    with path.open("r", encoding="utf-8", newline="") as file_obj:
        return list(csv.DictReader(file_obj))


def test_save_session_snapshot_writes_expected_json(tmp_path):
    session_data = {
        "captured_at": "2026-04-27T12:00:00",
        "cookies": {"sessionid": "abc"},
        "headers": {"User-Agent": "UA"},
    }

    output_path = cts_port_status_scan.save_session_snapshot(session_data, output_dir=tmp_path)

    assert output_path == tmp_path / "cts_port_status_session.json"
    assert json.loads(output_path.read_text(encoding="utf-8")) == session_data


def test_fetch_port_status_for_slot_uses_requests_session_and_frame_minus_one():
    captured = {}

    class FakeResponse:
        status_code = 200
        headers = {"content-type": "application/json; charset=utf-8"}

        def raise_for_status(self):
            return None

        def json(self):
            return [
                {
                    "key": "268438016",
                    "ifDescr": "",
                    "ifStatus": "Down",
                    "ifName": "gpon_1/1/11",
                    "slotNo": "1/1/11",
                    "tx": 3.41,
                    "txXgspon": 0.0,
                }
            ]

    class FakeSession:
        def get(self, url, params=None, timeout=None):
            captured["url"] = url
            captured["params"] = params
            return FakeResponse()

    rows = cts_port_status_scan.fetch_port_status_for_slot(
        FakeSession(),
        {
            "deviceIp": "10.31.8.69",
            "frame": "1",
            "slot": "1",
            "olt_name": "HNI.STY.XSZ.OLT.ZT.1.1",
        },
    )

    assert captured["url"].endswith("/GetL2PortListBySlot")
    assert captured["params"] == {"deviceIp": "10.31.8.69", "frame": "-1", "slot": "1"}
    assert rows[0]["if_status"] == "Down"
    assert rows[0]["port"] == "11"


def test_login_and_scan_all_olt_port_statuses_saves_session_and_exports(tmp_path, monkeypatch):
    rows = [{"if_status": "Down", "port": "11"}]
    session_data = {"cookies": {"sessionid": "abc"}, "headers": {"User-Agent": "UA"}}
    calls = []

    monkeypatch.setattr(
        cts_port_status_scan,
        "login_and_build_session",
        lambda headless=True: ("SESSION", session_data),
    )
    monkeypatch.setattr(
        cts_port_status_scan,
        "scan_all_olt_port_statuses",
        lambda session, port_tasks=None: rows if session == "SESSION" else None,
    )

    def fake_save(session_payload, output_dir):
        calls.append(("save", session_payload, Path(output_dir)))
        path = Path(output_dir) / "cts_port_status_session.json"
        path.write_text(json.dumps(session_payload), encoding="utf-8")
        return path

    def fake_export(payload_rows, output_dir):
        calls.append(("export", payload_rows, Path(output_dir)))
        json_path = Path(output_dir) / "port_status_snapshot.json"
        csv_path = Path(output_dir) / "port_status_snapshot.csv"
        json_path.write_text(json.dumps(payload_rows), encoding="utf-8")
        csv_path.write_text("if_status,port\nDown,11\n", encoding="utf-8")
        return json_path, csv_path

    monkeypatch.setattr(cts_port_status_scan, "save_session_snapshot", fake_save)
    monkeypatch.setattr(cts_port_status_scan, "export_port_status_snapshot", fake_export)

    result_rows, session_path, json_path, csv_path = cts_port_status_scan.login_and_scan_all_olt_port_statuses(
        output_dir=tmp_path
    )

    assert result_rows == rows
    assert session_path.exists()
    assert json_path.exists()
    assert csv_path.exists()
    assert calls[0][0] == "save"
    assert calls[1][0] == "export"


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

    json_path, csv_path = cts_port_status_scan.export_port_status_snapshot(rows, output_dir=tmp_path)

    assert json.loads(json_path.read_text(encoding="utf-8")) == rows
    csv_rows = read_csv_rows(csv_path)
    assert csv_rows[0]["if_status"] == "Down"
