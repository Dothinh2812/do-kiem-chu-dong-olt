import csv
import json
from datetime import datetime
from pathlib import Path
import re

import requests

try:
    from .app import prepare_input_files
    from .config import Config
    from .login import login_baocao_hanoi
except ImportError:
    from app import prepare_input_files
    from config import Config
    from login import login_baocao_hanoi


PORT_STATUS_OUTPUT_DIR = Path("runtime")
SESSION_SNAPSHOT_FILENAME = "cts_port_status_session.json"
PORT_STATUS_NO_PATTERN = re.compile(r"(\d+)/(\d+)/(\d+)$")


def build_port_status_slot_tasks(port_tasks):
    slot_tasks = []
    seen = set()

    for task in port_tasks:
        slot_task = {
            "deviceIp": str(task.get("deviceIp", "")).strip(),
            "frame": str(task.get("frame", "")).strip(),
            "slot": str(task.get("slot", "")).strip(),
            "olt_name": str(task.get("olt_name", "")).strip(),
        }
        task_key = (slot_task["deviceIp"], slot_task["frame"], slot_task["slot"])
        if not all(task_key) or task_key in seen:
            continue
        seen.add(task_key)
        slot_tasks.append(slot_task)

    return slot_tasks


def _extract_port_numbers(raw_value):
    match = PORT_STATUS_NO_PATTERN.search(str(raw_value or "").strip())
    if not match:
        return None, None, None
    return match.group(1), match.group(2), match.group(3)


def normalize_port_status_row(slot_task, row):
    slot_no = str(row.get("slotNo", "") or "").strip()
    if_name = str(row.get("ifName", "") or "").strip()

    frame, slot, port = _extract_port_numbers(slot_no)
    if not port:
        frame, slot, port = _extract_port_numbers(if_name)
    if not frame:
        frame = str(slot_task.get("frame", "") or "").strip()
    if not slot:
        slot = str(slot_task.get("slot", "") or "").strip()

    return {
        "olt_name": str(slot_task.get("olt_name", "") or "").strip(),
        "device_ip": str(slot_task.get("deviceIp", "") or "").strip(),
        "frame": frame,
        "slot": slot,
        "port": port or "",
        "slot_no": slot_no,
        "if_name": if_name,
        "if_status": str(row.get("ifStatus", "") or "").strip(),
        "if_descr": str(row.get("ifDescr", "") or "").strip(),
        "key": str(row.get("key", "") or "").strip(),
        "tx": row.get("tx"),
        "tx_xgspon": row.get("txXgspon"),
    }


def save_session_snapshot(session_data, output_dir=PORT_STATUS_OUTPUT_DIR):
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)
    session_path = output_path / SESSION_SNAPSHOT_FILENAME
    session_path.write_text(json.dumps(session_data, ensure_ascii=False, indent=2), encoding="utf-8")
    return session_path


def export_port_status_snapshot(rows, output_dir=PORT_STATUS_OUTPUT_DIR):
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    json_path = output_path / "port_status_snapshot.json"
    csv_path = output_path / "port_status_snapshot.csv"
    json_path.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")

    fieldnames = [
        "olt_name",
        "device_ip",
        "frame",
        "slot",
        "port",
        "slot_no",
        "if_name",
        "if_status",
        "if_descr",
        "key",
        "tx",
        "tx_xgspon",
    ]
    with csv_path.open("w", encoding="utf-8", newline="") as file_obj:
        writer = csv.DictWriter(file_obj, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    return json_path, csv_path


def login_and_build_session(headless=True):
    original_headless = Config.BROWSER_HEADLESS
    Config.BROWSER_HEADLESS = headless
    page = browser = playwright = None

    try:
        page, browser, playwright = login_baocao_hanoi()
        cookies = {cookie["name"]: cookie["value"] for cookie in page.context.cookies()}
        headers = {
            "Accept": "application/json, text/plain, */*",
            "X-Requested-With": "XMLHttpRequest",
            "Referer": "https://cts.vnpt.vn/Linetest/Test/TestGpon",
            "User-Agent": page.evaluate("navigator.userAgent"),
        }
        session_data = {
            "captured_at": datetime.now().isoformat(timespec="seconds"),
            "cookies": cookies,
            "headers": headers,
        }

        session = requests.Session()
        session.headers.update(headers)
        session.cookies.update(cookies)
        return session, session_data
    finally:
        Config.BROWSER_HEADLESS = original_headless
        if browser is not None:
            browser.close()
        if playwright is not None:
            playwright.stop()


def fetch_port_status_for_slot(session, slot_task):
    data_url = "https://cts.vnpt.vn/Linetest/Test/GetL2PortListBySlot"
    params = {
        "deviceIp": slot_task.get("deviceIp"),
        "frame": "-1",
        "slot": slot_task.get("slot"),
    }
    response = session.get(data_url, params=params, timeout=30)
    response.raise_for_status()

    content_type = response.headers.get("content-type", "").lower()
    if "application/json" not in content_type:
        body_preview = str(getattr(response, "text", "") or "").strip().replace("\n", " ")[:200]
        raise RuntimeError(
            "GetL2PortListBySlot returned non-JSON response after CTS login. "
            f"HTTP {response.status_code}, content-type={content_type}, body={body_preview!r}"
        )

    rows = response.json()
    if not isinstance(rows, list):
        raise ValueError("Expected list payload from GetL2PortListBySlot")

    return [normalize_port_status_row(slot_task, row) for row in rows]


def scan_all_olt_port_statuses(session, port_tasks=None):
    if port_tasks is None:
        port_tasks = prepare_input_files()

    slot_tasks = build_port_status_slot_tasks(port_tasks)
    all_rows = []
    for slot_task in slot_tasks:
        all_rows.extend(fetch_port_status_for_slot(session, slot_task))
    return all_rows


def login_and_scan_all_olt_port_statuses(output_dir=PORT_STATUS_OUTPUT_DIR, headless=True):
    session, session_data = login_and_build_session(headless=headless)
    session_path = save_session_snapshot(session_data, output_dir=output_dir)
    rows = scan_all_olt_port_statuses(session)
    json_path, csv_path = export_port_status_snapshot(rows, output_dir=output_dir)
    return rows, session_path, json_path, csv_path
