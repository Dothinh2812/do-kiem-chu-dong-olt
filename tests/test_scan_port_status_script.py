import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts import scan_port_status


def test_main_calls_scan_and_prints_paths(tmp_path, monkeypatch, capsys):
    rows = [{"if_status": "Down"}]
    session_path = tmp_path / "cts_port_status_session.json"
    json_path = tmp_path / "port_status_snapshot.json"
    csv_path = tmp_path / "port_status_snapshot.csv"

    monkeypatch.setattr(
        scan_port_status,
        "login_and_scan_all_olt_port_statuses",
        lambda output_dir=None, headless=True: (rows, session_path, json_path, csv_path),
    )
    monkeypatch.setattr(
        sys,
        "argv",
        ["scan_port_status.py", "--output-dir", str(tmp_path)],
    )

    scan_port_status.main()

    output = capsys.readouterr().out
    assert "Scanned 1 port status rows" in output
    assert str(session_path.resolve()) in output
    assert str(json_path.resolve()) in output
    assert str(csv_path.resolve()) in output


def test_main_with_headful_flag_passes_headless_false(tmp_path, monkeypatch):
    calls = []

    monkeypatch.setattr(
        scan_port_status,
        "login_and_scan_all_olt_port_statuses",
        lambda output_dir=None, headless=True: calls.append((Path(output_dir), headless)) or (
            [],
            tmp_path / "cts_port_status_session.json",
            tmp_path / "port_status_snapshot.json",
            tmp_path / "port_status_snapshot.csv",
        ),
    )
    monkeypatch.setattr(
        sys,
        "argv",
        ["scan_port_status.py", "--headful", "--output-dir", str(tmp_path)],
    )

    scan_port_status.main()

    assert calls == [(tmp_path, False)]
