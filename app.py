import os
import json
import time
import re
import shutil
import sqlite3
import threading
import unicodedata
import csv
from datetime import datetime
from pathlib import Path
from collections import defaultdict, deque
from concurrent.futures import ThreadPoolExecutor, as_completed

import pandas as pd
import requests
from playwright.sync_api import sync_playwright

try:
    from .alert_db import AlertRepository
    from .alert_engine import process_completed_batch
    from .config import Config
    from .login import (
        OTP_CONFIRM_BUTTON_SELECTORS,
        OTP_FIELD_SELECTORS,
        _click_first_visible_button,
        _first_visible_locator,
        read_otp_from_file,
    )
except ImportError:
    from alert_db import AlertRepository
    from alert_engine import process_completed_batch
    from config import Config
    from login import (
        OTP_CONFIRM_BUTTON_SELECTORS,
        OTP_FIELD_SELECTORS,
        _click_first_visible_button,
        _first_visible_locator,
        read_otp_from_file,
    )

# Parallel download settings
NUM_THREADS = 64
MAX_THREADS_PER_DEVICE = 1
AUTH_RETRY_STATUS_CODES = {401, 403}
MAX_HTTP_500_RETRIES = 2
MAX_TIMEOUT_RETRIES = 3
TIMEOUT_SEQUENCE_SECONDS = [15, 30, 60]  # Increasing timeouts for each retry attempt
MEASUREMENT_LOOP_DELAY_SECONDS = 300
DATABASE_PATH = "onu_measurements.db"
SOURCE_DATABASE_PATH = "database.db"
MEASUREMENT_LOG_DIR = Path("logs/measurement_batches")
PORT_STATUS_OUTPUT_DIR = Path("runtime")
TABLE_NAME = "onu_measurements"
PORT_LABEL_COLUMN = "Cổng"
MEASUREMENT_COLUMNS = [
    "onuLastOff",
    "onuLastOn",
    "oltPowerRx",
    "onuPowerRx",
    "slid",
    "onuSN",
    "softVersion",
    "onuStatusStr",
    "frameNo",
    "slotNo",
    "portNo",
    "onuIndex",
    "accountFiber",
]

class FallbackConfig:
    BAOCAO_URL = "https://cts.vnpt.vn"
    BAOCAO_USERNAME = "your_username"
    BAOCAO_PASSWORD = "your_password"
    PAGE_LOAD_TIMEOUT = 60000


if "Config" not in globals():
    Config = FallbackConfig


global_cookies = {}
global_headers = {}
login_lock = threading.Lock()
db_lock = threading.Lock()
issue_log_lock = threading.Lock()
port_status_cache_lock = threading.Lock()
device_semaphores = {}
ip_to_olt_name = {}
allowed_subs = set()
port_status_slot_cache = {}

OLT_TEXT_DATA = """HNI.TTT.THH.OLT.HU.6.1	MA5801-GP8	10.10.60.106
HNI.BVI.PCG.OLT.HU.6.1	MA5801-GP8	10.10.60.114
HNI.BVI.PTH.OLT.HU.6.1	MA5801-GP8	10.10.60.115
HNI.BVI.DAC.OLT.HU.6.1	MA5801-GP8	10.10.60.186
HNI.PTO.LBQ.OLT.HU.6.1	MA5801-GP8	10.10.60.171
HNI.PTO.NPO.OLT.HU.6.1	MA5801-GP8	10.10.60.172
HNI.PTO.NHM.OLT.HU.6.1	MA5801-GP8	10.10.60.173
HNI.TTT.CBO.OLT.HU.6.1	MA5801-GP8	10.10.60.98
HNI.TTT.BPU.OLT.AL.2.1	ISAM7360_R65	10.31.10.10
HNI.PTO.PCT.OLT.AL.2.1	ISAM7360_R65	10.31.10.114
HNI.PTO.VXN.OLT.AL.2.1	ISAM7360_R65	10.31.10.116
HNI.PTO.TMH.OLT.AL.2.1	ISAM7360_R65	10.31.10.117
HNI.STY.STY.OLT.AL.2.1	ISAM7360_R65	10.31.10.250
HNI.STY.DLM.OLT.AL.2.1	ISAM7360_R65	10.31.10.251
HNI.STY.STY.OLT.AL.2.2	ISAM7360_R65	10.31.10.252
HNI.BVI.VTG.OLT.AL.2.1	ISAM7360_R65	10.31.14.34
HNI.DPG.TOA.OLT.AL.2.1	ISAM7360_R65	10.31.14.98
HNI.DPG.TOA.OLT.AL.2.2	ISAM7360_R65	10.31.14.99
HNI.STY.SLC.OLT.HU.2.1	MA5608T	10.31.17.114
HNI.STY.SLC.OLT.HU.2.2	MA5608T	10.31.17.116
HNI.BVI.MOC.OLT.HU.2.1	MA5608T	10.31.17.122
HNI.STY.STY.OLT.HU.1.1	MA5600T	10.31.17.162
HNI.STY.DLM.OLT.HU.2.1	MA5608T	10.31.17.164
HNI.STY.STY.OLT.HU.2.2	MA5608T	10.31.17.165
HNI.TTT.TTT.OLT.HU.1.1	MA5600T	10.31.17.178
HNI.TTT.CNU.OLT.HU.2.1	MA5608T	10.31.17.180
HNI.TTT.CNU.OLT.HU.2.2	MA5608T	10.31.17.181
HNI.TTT.LIT.OLT.HU.2.1	MA5608T	10.31.17.182
HNI.TTT.DID.OLT.HU.2.1	MA5608T	10.31.17.183
HNI.TTT.LIT.OLT.HU.2.2	MA5608T	10.31.17.184
HNI.TTT.HLC.OLT.ZT.1.2	MA5608T	10.31.8.147
HNI.TTT.TTT.OLT.HU.4.3	MA5800X7	10.31.17.185
HNI.TTT.BPU.OLT.HU.1.1	MA5600T	10.31.17.242
HNI.TTT.BPU.OLT.HU.2.2	MA5608T	10.31.17.243
HNI.TTT.BPU.OLT.HU.2.3	MA5608T	10.31.17.245
HNI.BVI.VTG.OLT.ZT.1.1	ZTEC320	10.31.20.18
HNI.BVI.VTG.OLT.ZT.1.2	ZTEC320	10.31.20.19
HNI.BVI.VTG.OLT.ZT.1.3	ZTEC320	10.31.20.20
HNI.BVI.PPG.OLT.ZT.1.1	ZTEC320	10.31.20.21
HNI.PTO.TMH.OLT.HU.4.1	MA5800X7	10.31.20.66
HNI.PTO.PCT.OLT.HU.4.1	MA5800X7	10.31.20.67
HNI.DPG.DPG.OLT.AL.2.1	ISAM7360_R58	10.31.21.18
HNI.DPG.THI.OLT.AL.2.1	ISAM7360_R65	10.31.21.195
HNI.BVI.TLH.OLT.AL.2.1	ISAM7360_R65	10.31.21.202
HNI.BVI.MOC.OLT.AL.2.1	ISAM7360_R65	10.31.21.203
HNI.BVI.VMG.OLT.AL.2.1	ISAM7360_R65	10.31.21.204
HNI.BVI.KTG.OLT.AL.2.1	ISAM7360_R65	10.31.21.206
HNI.STY.DGM.OLT.AL.2.1	ISAM7360_R65	10.31.21.210
HNI.STY.DGM.OLT.AL.2.2	ISAM7360_R65	10.31.21.211
HNI.DPG.DPG.OLT.AL.2.2	ISAM7360_R65	10.31.21.22
HNI.BVI.BVI.OLT.AL.2.1	ISAM7360_R65	10.31.21.84
HNI.TTT.HLC.OLT.AL.2.1	ISAM7360_R65	10.31.21.91
HNI.TTT.YBH.OLT.AL.2.1	ISAM7360_R65	10.31.21.94
HNI.BVI.SHI.OLT.AL.2.1	ISAM7360_R65	10.31.21.98
HNI.BVI.BVI.OLT.HU.4.1	MA5800X7	10.31.27.194
HNI.DPG.DPG.OLT.HU.4.1	MA5800X7	10.31.28.35
HNI.BVI.CTG.OLT.ZT.1.1	ZTEC320	10.31.8.107
HNI.BVI.BVI.OLT.ZT.2.3	ZTEC350	10.31.8.109
HNI.STY.DGM.OLT.ZT.1.1	ZTEC320	10.31.8.131
HNI.STY.DGM.OLT.ZT.1.2	ZTEC320	10.31.8.132
HNI.STY.XKH.OLT.ZT.1.1	ZTEC320	10.31.8.133
HNI.STY.KMS.OLT.ZT.1.1	ZTEC320	10.31.8.134
HNI.STY.NBC.OLT.DS.1.1	V5804	10.31.8.135
HNI.STY.SOD.OLT.ZT.1.1	ZTEC320	10.31.8.136
HNI.TTT.HLC.OLT.ZT.1.1	ZTEC320	10.31.8.146
HNI.TTT.HBG.OLT.ZT.1.2	ZTEC320	10.31.8.149
HNI.TTT.YBH.OLT.ZT.1.1	ZTEC320	10.31.8.150
HNI.TTT.TNX.OLT.ZT.1.1	ZTEC320	10.31.8.151
HNI.TTT.HBG.OLT.ZT.1.3	ZTEC320	10.31.8.153
HNI.TTT.DBI.OLT.DS.1.1	V5804	10.31.8.154
HNI.TTT.CHL.OLT.ZT.1.2	ZTEC320	10.31.8.155
HNI.DPG.DPG.OLT.ZT.2.2	ZTEC350	10.31.8.35
HNI.PTO.TMH.OLT.ZT.1.2	ZTEC320	10.31.8.37
HNI.PTO.VNM.OLT.ZT.1.1	ZTEC320	10.31.8.39
HNI.PTO.VNM.OLT.ZT.1.2	ZTEC320	10.31.8.40
HNI.PTO.VNM.OLT.ZT.1.4	ZTEC320	10.31.8.45
HNI.STY.XSZ.OLT.ZT.1.1	ZTEC320	10.31.8.69
HNI.BVI.TLH.OLT.ZT.1.1	ZTEC320	10.31.8.82
HNI.BVI.TLH.OLT.ZT.1.2	ZTEC320	10.31.8.83
HNI.BVI.TLH.OLT.ZT.1.3	ZTEC320	10.31.8.84
HNI.BVI.VMG.OLT.ZT.1.1	ZTEC320	10.31.8.85
HNI.BVI.VMG.OLT.ZT.1.2	ZTEC320	10.31.8.87
HNI.BVI.BVI.OLT.ZT.1.1	ZTEC320	10.31.8.98
HNI.BVI.SHI.OLT.ZT.1.1	ZTEC320	10.31.9.114
HNI.BVI.SDA.OLT.ZT.1.1	ZTEC320	10.31.9.116
HNI.BVI.TAN.OLT.ZT.1.1	ZTEC320	10.31.9.117
HNI.BVI.TBT.OLT.ZT.1.1	ZTEC320	10.31.9.118
HNI.BVI.SDA.OLT.ZT.1.2	ZTEC320	10.31.9.119
HNI.PTO.PCT.OLT.ZT.1.1	ZTEC320	10.31.9.130
HNI.PTO.PCT.OLT.ZT.1.2	ZTEC320	10.31.9.131
HNI.PTO.TMH.OLT.ZT.1.4	ZTEC320	10.31.9.135
HNI.PTO.VXN.OLT.ZT.1.1	ZTEC320	10.31.9.139
HNI.PTO.VXN.OLT.ZT.1.2	ZTEC320	10.31.9.140
HNI.PTO.TMH.OLT.ZT.1.1	ZTEC320	10.31.9.141
HNI.PTO.TMH.OLT.ZT.1.3	ZTEC320	10.31.9.142
HNI.PTO.VNM.OLT.ZT.1.3	ZTEC320	10.31.9.143
HNI.DPG.LNG.OLT.ZT.1.1	ZTEC320	10.31.9.18
HNI.DPG.LNG.OLT.ZT.1.2	ZTEC320	10.31.9.19
HNI.DPG.THI.OLT.ZT.1.1	ZTEC320	10.31.9.20
HNI.DPG.LNT.OLT.ZT.1.1	ZTEC320	10.31.9.21
HNI.DPG.THI.OLT.ZT.1.2	ZTEC320	10.31.9.22
HNI.DPG.TOA.OLT.ZT.1.1	ZTEC320	10.31.9.226
HNI.DPG.TOA.OLT.ZT.1.2	ZTEC320	10.31.9.227
HNI.DPG.LNT.OLT.ZT.1.2	ZTEC320	10.31.9.23
HNI.DPG.LNG.OLT.ZT.1.3	ZTEC320	10.31.9.24
HNI.DPG.THI.OLT.HU.4.1	MA5800X7	10.31.9.242"""
PORT_PATTERN = re.compile(r"_(\d+(?:-\d+){2}):")
PORT_STATUS_NO_PATTERN = re.compile(r"(\d+)/(\d+)/(\d+)$")


def extract_port(sub_value):
    """
    Extract a port like 1/1/1 from danhba.sub values such as
    HNI.STY.STY.OLT.AL.2.1_1-5-10:30.
    """
    if not isinstance(sub_value, str):
        return None

    match = PORT_PATTERN.search(sub_value.strip())
    if not match:
        return None

    return match.group(1).replace("-", "/")


def port_sort_key(port):
    return tuple(int(part) for part in port.split("/"))


def normalize_text(value):
    if not isinstance(value, str):
        return ""

    normalized = unicodedata.normalize("NFKD", value)
    no_accents = "".join(ch for ch in normalized if not unicodedata.combining(ch))
    return re.sub(r"\s+", " ", no_accents).strip().lower()


def build_olt_input_dataframe(db_path, doi_vt=None):
    """
    Mirror generate_olt_input.py:
    - read danhba
    - optionally filter by DOI_VT
    - group ports by System into OLT_NAME / PORTS / ENABLED
    """
    with sqlite3.connect(db_path, timeout=60) as conn:
        df = pd.read_sql_query("SELECT System, sub, DOI_VT FROM danhba", conn)

    if df.empty:
        return pd.DataFrame(columns=["OLT_NAME", "PORTS", "ENABLED"])

    if doi_vt:
        target = normalize_text(doi_vt)
        df = df[df["DOI_VT"].astype(str).apply(normalize_text) == target].copy()

    df["PORT"] = df["sub"].apply(extract_port)
    df = df.dropna(subset=["System", "PORT"]).copy()

    grouped = (
        df.groupby("System", sort=True)["PORT"]
        .apply(lambda series: ", ".join(sorted(set(series), key=port_sort_key)))
        .reset_index()
        .rename(columns={"System": "OLT_NAME", "PORT": "PORTS"})
    )
    grouped["ENABLED"] = True
    return grouped


def parse_olt_ip_map():
    ip_map = {}
    for line in OLT_TEXT_DATA.strip().splitlines():
        parts = line.split()
        if len(parts) >= 3:
            ip_map[parts[0]] = parts[2]
    return ip_map


def build_port_status_slot_tasks(port_tasks):
    """
    Collapse per-port scan tasks into unique per-slot tasks for GetL2PortListBySlot.
    """
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
    """
    Convert one GetL2PortListBySlot payload row into a stable dictionary.
    """
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


def fetch_port_status_for_slot(slot_task):
    """
    Request all L2 port statuses for one OLT slot and return normalized rows.
    """
    data_url = "https://cts.vnpt.vn/Linetest/Test/GetL2PortListBySlot"
    params = {
        "deviceIp": slot_task.get("deviceIp"),
        "frame": "-1",
        "slot": slot_task.get("slot"),
    }

    with login_lock:
        current_cookies = global_cookies.copy()
        current_headers = global_headers.copy()

    res = requests.get(
        data_url,
        params=params,
        headers=current_headers,
        cookies=current_cookies,
        timeout=30,
    )
    res.raise_for_status()

    content_type = res.headers.get("content-type", "").lower()
    if "application/json" not in content_type:
        body_preview = str(getattr(res, "text", "") or "").strip().replace("\n", " ")[:200]
        raise RuntimeError(
            "GetL2PortListBySlot returned non-JSON response. "
            "This usually means the CTS session is missing or expired. "
            "Run perform_browser_login() first or use the CLI with --login. "
            f"HTTP {res.status_code}, content-type={content_type}, body={body_preview!r}"
        )

    rows = res.json()
    if not isinstance(rows, list):
        raise ValueError("Expected list payload from GetL2PortListBySlot")

    return [normalize_port_status_row(slot_task, row) for row in rows]


def scan_all_olt_port_statuses(port_tasks=None):
    """
    Scan all unique OLT slots and return normalized L2 port statuses.
    This function is intentionally standalone and is not wired into the
    continuous ONU measurement loop.
    """
    if port_tasks is None:
        port_tasks = prepare_input_files()

    slot_tasks = build_port_status_slot_tasks(port_tasks)
    all_rows = []
    for slot_task in slot_tasks:
        all_rows.extend(fetch_port_status_for_slot(slot_task))
    return all_rows


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


def scan_and_export_all_olt_port_statuses(port_tasks=None, output_dir=PORT_STATUS_OUTPUT_DIR):
    rows = scan_all_olt_port_statuses(port_tasks=port_tasks)
    json_path, csv_path = export_port_status_snapshot(rows, output_dir=output_dir)
    return rows, json_path, csv_path


def prepare_input_files():
    """
    Build olt_input_full.xlsx and all_ports_ready.json from database.db.
    This inlines the logic of generate_olt_input.py and filter_ports.py so
    download_fast.py can prepare its own input files on startup.
    """
    source_db_path = Path(SOURCE_DATABASE_PATH)
    excel_path = Path("olt_input_full.xlsx")
    json_path = Path("all_ports_ready.json")
    backup_json_path = Path("all_ports_ready_old_scan.json")

    if not source_db_path.exists():
        raise FileNotFoundError(f"Missing source database: {source_db_path}")

    olt_df = build_olt_input_dataframe(source_db_path)
    olt_df.to_excel(excel_path, index=False)
    print(
        f"Generated {excel_path.resolve()} with {len(olt_df)} OLT rows from {source_db_path.resolve()}."
    )

    ip_map = parse_olt_ip_map()
    filtered_ports = []
    seen_ports = set()
    missing_ips = set()
    invalid_ports_rows = 0

    for _, row in olt_df.iterrows():
        if "ENABLED" in row.index and not bool(row["ENABLED"]):
            continue

        olt_name = str(row.get("OLT_NAME", "")).strip()
        if not olt_name:
            continue

        device_ip = ip_map.get(olt_name)
        if not device_ip:
            missing_ips.add(olt_name)
            continue

        ports_str = str(row.get("PORTS", "")).strip()
        if not ports_str or ports_str.lower() == "nan":
            invalid_ports_rows += 1
            continue

        for raw_port in (part.strip() for part in ports_str.split(",")):
            if not raw_port or "/" not in raw_port:
                continue

            parts = raw_port.split("/")
            if len(parts) < 3:
                continue

            task = {
                "deviceIp": device_ip,
                "frame": str(parts[0]).strip(),
                "slot": str(parts[1]).strip(),
                "port": str(parts[2]).strip(),
                "olt_name": olt_name,
            }
            task_key = (task["deviceIp"], task["frame"], task["slot"], task["port"])
            if task_key in seen_ports:
                continue

            seen_ports.add(task_key)
            filtered_ports.append(task)

    if json_path.exists():
        try:
            shutil.copy(json_path, backup_json_path)
        except Exception:
            pass

    with open(json_path, "w", encoding="utf-8") as file_obj:
        json.dump(filtered_ports, file_obj, indent=4, ensure_ascii=False)

    print(f"Generated {json_path.resolve()} with {len(filtered_ports)} port tasks.")
    if invalid_ports_rows:
        print(f"Skipped {invalid_ports_rows} rows with empty PORTS data.")
    if missing_ips:
        print(
            f"Missing IP mapping for {len(missing_ips)} OLTs. "
            f"Examples: {', '.join(sorted(missing_ips)[:5])}"
        )

    if not filtered_ports:
        raise RuntimeError("No port tasks were generated for all_ports_ready.json")

    return filtered_ports


def interleave_tasks_by_device(tasks):
    """
    Reorder tasks in round-robin order so each pass takes at most one port
    from each device. This spreads requests evenly across OLTs.
    """
    grouped_tasks = defaultdict(deque)
    device_order = []

    for task in tasks:
        device_ip = task["deviceIp"]
        if device_ip not in grouped_tasks:
            device_order.append(device_ip)
        grouped_tasks[device_ip].append(task)

    interleaved_tasks = []
    active_devices = deque(device_order)

    while active_devices:
        current_device = active_devices.popleft()
        interleaved_tasks.append(grouped_tasks[current_device].popleft())
        if grouped_tasks[current_device]:
            active_devices.append(current_device)

    return interleaved_tasks


def resolve_olt_name(task):
    """
    Prefer OLT name embedded in all_ports_ready.json.
    Fall back to the deviceIp -> OLT mapping built from the task list.
    """
    olt_name = str(task.get("olt_name", "")).strip()
    if olt_name:
        return olt_name
    return ip_to_olt_name.get(task.get("deviceIp"), "UNKNOWN")


def build_task_port_label(task):
    return f"{task.get('deviceIp')}_F{task.get('frame')}_S{task.get('slot')}_P{task.get('port')}"


def build_parent_port_key(olt_name, frame_no, slot_no, port_no):
    return f"{olt_name}_{frame_no}-{slot_no}-{port_no}"


def parse_subscriber_key(subscriber_key):
    olt_name, rest = str(subscriber_key).split("_", 1)
    port_part, onu_index = rest.split(":", 1)
    frame_no, slot_no, port_no = [int(part) for part in port_part.split("-")]
    return olt_name, frame_no, slot_no, port_no, int(onu_index)


def lookup_subscribers_by_parent_port(source_db_path, parent_port_key):
    with sqlite3.connect(source_db_path, timeout=60) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT
                sub AS subscriber_key,
                COALESCE(Ma_Tb, '') AS ma_tb,
                COALESCE(Ten_Tb, '') AS ten_tb,
                COALESCE(Ma_Men, '') AS ma_men,
                COALESCE(DOI_VT, '') AS doi_vt,
                COALESCE(DIACHI_LD, '') AS diachi_ld,
                COALESCE(DIENTHOAI_LH, '') AS dienthoai_lh,
                COALESCE(TEN_NVKT_DB, '') AS ten_nvkt_db
            FROM danhba
            WHERE sub LIKE ?
            ORDER BY sub
            """,
            (f"{parent_port_key}:%",),
        ).fetchall()

    subscribers = []
    for row in rows:
        item = dict(row)
        _olt_name, _frame_no, _slot_no, _port_no, onu_index = parse_subscriber_key(item["subscriber_key"])
        item["onu_index"] = onu_index
        item["account_fiber"] = ""
        subscribers.append(item)
    return subscribers


def build_port_down_measurement_records(subscriber_rows, batch_id, measured_date, measured_time):
    records = []
    for row in subscriber_rows:
        _olt_name, frame_no, slot_no, port_no, onu_index = parse_subscriber_key(row["subscriber_key"])
        records.append(
            (
                row["subscriber_key"],
                batch_id,
                "",
                "",
                None,
                None,
                "",
                "",
                "",
                "PORT_DOWN",
                frame_no,
                slot_no,
                port_no,
                onu_index,
                row.get("account_fiber", "") or "",
                measured_date,
                measured_time,
            )
        )
    return records


def _fetch_port_status_rows(task):
    cache_key = (str(task.get("deviceIp", "")).strip(), str(task.get("slot", "")).strip())
    with port_status_cache_lock:
        cached = port_status_slot_cache.get(cache_key)
    if cached is not None:
        return cached

    data_url = "https://cts.vnpt.vn/Linetest/Test/GetL2PortListBySlot"
    params = {
        "deviceIp": task.get("deviceIp"),
        "frame": "-1",
        "slot": task.get("slot"),
    }

    with login_lock:
        current_cookies = global_cookies.copy()
        current_headers = global_headers.copy()

    res = requests.get(
        data_url,
        params=params,
        headers=current_headers,
        cookies=current_cookies,
        timeout=30,
    )
    content_type = res.headers.get("content-type", "").lower()
    if "text/html" in content_type or res.status_code in AUTH_RETRY_STATUS_CODES:
        raise RuntimeError(f"Port status request rejected: HTTP {res.status_code}, content-type={content_type}")

    raise_for_status = getattr(res, "raise_for_status", None)
    if callable(raise_for_status):
        raise_for_status()
    rows = res.json()
    if not isinstance(rows, list):
        raise ValueError("Expected list payload from GetL2PortListBySlot")

    with port_status_cache_lock:
        port_status_slot_cache[cache_key] = rows
    return rows


def fetch_single_port_status(task):
    target_slot_no = f"{task.get('frame')}/{task.get('slot')}/{task.get('port')}"
    for row in _fetch_port_status_rows(task):
        slot_no = str(row.get("slotNo", "") or "").strip()
        if slot_no == target_slot_no:
            return str(row.get("ifStatus", "") or "").strip()
    return ""


def get_batch_issue_log_path(batch_id):
    return Path(MEASUREMENT_LOG_DIR) / f"{batch_id}_port_issues.csv"


def append_port_issue_log(batch_id, task, status, reason, olt_name=None):
    log_path = get_batch_issue_log_path(batch_id)
    log_path.parent.mkdir(parents=True, exist_ok=True)

    row = {
        "logged_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "batch_id": batch_id,
        "status": status,
        "olt_name": olt_name or resolve_olt_name(task),
        "device_ip": str(task.get("deviceIp", "") or ""),
        "frame": str(task.get("frame", "") or ""),
        "slot": str(task.get("slot", "") or ""),
        "port": str(task.get("port", "") or ""),
        "port_label": build_task_port_label(task),
        "reason": reason,
    }
    fieldnames = list(row.keys())

    with issue_log_lock:
        file_exists = log_path.exists()
        with log_path.open("a", encoding="utf-8", newline="") as file_obj:
            writer = csv.DictWriter(file_obj, fieldnames=fieldnames)
            if not file_exists:
                writer.writeheader()
            writer.writerow(row)


def build_port_label(olt_name, row):
    """
    Format: HNI.BVI.VMG.OLT.AL.2.1_1-3-4:1
    """
    return (
        f"{olt_name}_{row.get('frameNo')}-{row.get('slotNo')}-{row.get('portNo')}:{row.get('onuIndex')}"
    )


def init_database():
    """
    Create the SQLite database and indexes used for continuous measurement history.
    """
    with sqlite3.connect(DATABASE_PATH, timeout=60) as conn:
        conn.execute("PRAGMA journal_mode=DELETE")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {TABLE_NAME} (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                "{PORT_LABEL_COLUMN}" TEXT NOT NULL,
                batch_id TEXT NOT NULL,
                onuLastOff TEXT,
                onuLastOn TEXT,
                oltPowerRx REAL,
                onuPowerRx REAL,
                slid TEXT,
                onuSN TEXT,
                softVersion TEXT,
                onuStatusStr TEXT,
                frameNo INTEGER,
                slotNo INTEGER,
                portNo INTEGER,
                onuIndex INTEGER,
                accountFiber TEXT,
                NgayDo TEXT NOT NULL,
                ThoiGianDo TEXT NOT NULL
            )
            """
        )
        existing_columns = {
            row[1] for row in conn.execute(f"PRAGMA table_info({TABLE_NAME})").fetchall()
        }
        if "batch_id" not in existing_columns:
            conn.execute(f"ALTER TABLE {TABLE_NAME} ADD COLUMN batch_id TEXT")
        conn.execute(
            f"""
            CREATE INDEX IF NOT EXISTS idx_{TABLE_NAME}_port_time
            ON {TABLE_NAME}("{PORT_LABEL_COLUMN}", NgayDo, ThoiGianDo)
            """
        )
        conn.execute(
            f"""
            CREATE INDEX IF NOT EXISTS idx_{TABLE_NAME}_batch_id
            ON {TABLE_NAME}(batch_id)
            """
        )
        conn.execute(
            f"""
            CREATE INDEX IF NOT EXISTS idx_{TABLE_NAME}_onu_sn
            ON {TABLE_NAME}(onuSN)
            """
        )
        conn.commit()

    AlertRepository(DATABASE_PATH, SOURCE_DATABASE_PATH).ensure_schema()


def load_allowed_subs():
    """
    Read danhba.sub from database.db and keep only non-empty values.
    These values match the generated port label format, e.g. HNI..._1-3-4:1.
    """
    if not os.path.exists(SOURCE_DATABASE_PATH):
        raise FileNotFoundError(f"Missing source database: {SOURCE_DATABASE_PATH}")

    with sqlite3.connect(SOURCE_DATABASE_PATH, timeout=60) as conn:
        rows = conn.execute(
            """
            SELECT DISTINCT TRIM(sub)
            FROM danhba
            WHERE sub IS NOT NULL AND TRIM(sub) <> ''
            """
        ).fetchall()

    allowed_subs.clear()
    allowed_subs.update(row[0] for row in rows if row[0])


def build_measurement_records(data, olt_name, batch_id, measured_date, measured_time):
    """
    Convert the CTS JSON payload into SQLite-ready rows.
    """
    records = []
    for row in data:
        port_label = build_port_label(olt_name, row)
        if port_label not in allowed_subs:
            continue

        records.append(
            (
                port_label,
                batch_id,
                row.get("onuLastOff"),
                row.get("onuLastOn"),
                row.get("oltPowerRx"),
                row.get("onuPowerRx"),
                row.get("slid"),
                row.get("onuSN"),
                row.get("softVersion"),
                row.get("onuStatusStr"),
                row.get("frameNo"),
                row.get("slotNo"),
                row.get("portNo"),
                row.get("onuIndex"),
                row.get("accountFiber"),
                measured_date,
                measured_time,
            )
        )
    return records


def insert_measurement_records(records):
    """
    SQLite allows one writer at a time. Serialize writes and insert each port as one batch.
    """
    if not records:
        return

    with db_lock:
        with sqlite3.connect(DATABASE_PATH, timeout=60) as conn:
            conn.execute("PRAGMA journal_mode=DELETE")
            conn.executemany(
                f"""
                INSERT INTO {TABLE_NAME} (
                    "{PORT_LABEL_COLUMN}",
                    batch_id,
                    onuLastOff,
                    onuLastOn,
                    oltPowerRx,
                    onuPowerRx,
                    slid,
                    onuSN,
                    softVersion,
                    onuStatusStr,
                    frameNo,
                    slotNo,
                    portNo,
                    onuIndex,
                    accountFiber,
                    NgayDo,
                    ThoiGianDo
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                records,
            )
            conn.commit()


def perform_browser_login(headless=True):
    """
    Open Chromium, complete login, then refresh shared cookies/headers.
    Only one thread should run this function at a time.
    """
    global global_cookies, global_headers

    print("\n[LOGIN] Session expired or missing. Opening browser login flow...")
    playwright = sync_playwright().start()
    browser = playwright.chromium.launch(headless=headless)
    context = browser.new_context(accept_downloads=True)
    page = context.new_page()

    login_url = getattr(Config, "BAOCAO_URL", "https://cts.vnpt.vn")
    print(f"     Opening {login_url} ...")
    page.goto(login_url, timeout=getattr(Config, "PAGE_LOAD_TIMEOUT", 60000))
    page.wait_for_load_state("networkidle", timeout=getattr(Config, "PAGE_LOAD_TIMEOUT", 60000))

    try:
        username_field = _first_visible_locator(page, ('//*[@id="username"]', "#username"))
        username_field.fill(getattr(Config, "BAOCAO_USERNAME", ""))
        time.sleep(1)
        password_field = _first_visible_locator(page, ('//*[@id="password"]', "#password"))
        password_field.fill(getattr(Config, "BAOCAO_PASSWORD", ""))
        time.sleep(1)
        _click_first_visible_button(
            page,
            (
                '//*[@id="fm1"]/section/button',
                '#fm1 button[type="submit"]',
                '#fm1 button',
                'button[type="submit"]',
            ),
        )
        time.sleep(3)

        otp_code = None
        if "read_otp_from_file" in globals():
            otp_code = read_otp_from_file()

        if otp_code:
            otp_field = _first_visible_locator(page, OTP_FIELD_SELECTORS)
            otp_field.fill(otp_code)
            time.sleep(1)
            _click_first_visible_button(page, OTP_CONFIRM_BUTTON_SELECTORS)
            time.sleep(5)
        else:
            print("     Please complete OTP/Captcha manually in the browser. Waiting 20s...")
            time.sleep(20)
    except Exception:
        print("     Please complete login manually in the browser. Waiting 20s...")
        time.sleep(20)

    page.wait_for_load_state("networkidle", timeout=getattr(Config, "PAGE_LOAD_TIMEOUT", 60000))
    print("     Login complete. Refreshing shared cookies for worker threads...")

    new_cookies = {}
    for cookie in context.cookies():
        new_cookies[cookie["name"]] = cookie["value"]

    global_cookies = new_cookies
    global_headers = {
        "Accept": "application/json, text/plain, */*",
        "X-Requested-With": "XMLHttpRequest",
        "Referer": "https://cts.vnpt.vn/Linetest/Test/TestGpon",
        "User-Agent": page.evaluate("navigator.userAgent"),
    }

    browser.close()
    playwright.stop()


def download_single_port(idx, total_ports, task, batch_id):
    """
    Download one PON port and append the result rows into SQLite.
    """
    olt_name = resolve_olt_name(task)
    ip = task.get("deviceIp")
    f_idx = task.get("frame")
    s_idx = task.get("slot")
    p_idx = task.get("port")

    port_label = build_task_port_label(task)
    data_url = "https://cts.vnpt.vn/Linetest/Test/GetListByPonPortAsync"
    params = {"deviceIp": ip, "frame": f_idx, "slot": s_idx, "port": p_idx}
    server_error_retries = 0
    timeout_retries = 0

    device_semaphore = device_semaphores[ip]
    with device_semaphore:
        while True:
            port_status = ""
            try:
                port_status = fetch_single_port_status(task)
            except Exception as exc:
                append_port_issue_log(
                    batch_id,
                    task,
                    "warning",
                    f"Port status precheck failed, falling back to ONU detail: {exc}",
                    olt_name=olt_name,
                )

            if str(port_status).upper() == "DOWN":
                subscriber_rows = lookup_subscribers_by_parent_port(
                    SOURCE_DATABASE_PATH,
                    build_parent_port_key(olt_name, f_idx, s_idx, p_idx),
                )
                if not subscriber_rows:
                    append_port_issue_log(
                        batch_id,
                        task,
                        "filtered",
                        "Port Down but no subscribers found in danhba",
                        olt_name=olt_name,
                    )
                    return "filtered"

                measured_at = datetime.now()
                measured_date = measured_at.strftime("%Y-%m-%d")
                measured_time = measured_at.strftime("%H:%M:%S")
                records = build_port_down_measurement_records(
                    subscriber_rows,
                    batch_id,
                    measured_date,
                    measured_time,
                )
                insert_measurement_records(records)
                append_port_issue_log(
                    batch_id,
                    task,
                    "port_down",
                    f"Port Down precheck created {len(records)} PORT_DOWN rows",
                    olt_name=olt_name,
                )
                return "port_down"

            with login_lock:
                current_cookies = global_cookies.copy()
                current_headers = global_headers.copy()

            try:
                request_timeout_seconds = TIMEOUT_SEQUENCE_SECONDS[timeout_retries]
                res = requests.get(
                    data_url,
                    params=params,
                    headers=current_headers,
                    cookies=current_cookies,
                    timeout=request_timeout_seconds,
                )
                content_type = res.headers.get("content-type", "").lower()

                # Re-login when CTS returns the login page/rate-limit HTML or rejects the session.
                if "text/html" in content_type or res.status_code in AUTH_RETRY_STATUS_CODES:
                    acquired = login_lock.acquire(blocking=False)
                    if acquired:
                        try:
                            if res.status_code in AUTH_RETRY_STATUS_CODES:
                                print(
                                    f"[{idx}/{total_ports}] Session rejected with HTTP {res.status_code}. "
                                    "Refreshing login and retrying..."
                                )
                            else:
                                print(
                                    f"[{idx}/{total_ports}] Server returned HTML instead of JSON. "
                                    "Refreshing login and retrying..."
                                )
                            perform_browser_login(headless=True)
                        finally:
                            login_lock.release()
                    else:
                        # Another thread is already refreshing the shared session.
                        login_lock.acquire()
                        login_lock.release()

                    continue

                if res.status_code == 200:
                    try:
                        data = res.json()
                    except Exception:
                        print(f"[{idx}/{total_ports}] Invalid JSON response. Skipping {port_label}.")
                        append_port_issue_log(
                            batch_id,
                            task,
                            "error",
                            "Invalid JSON response from CTS API",
                            olt_name=olt_name,
                        )
                        return "error"

                    if not data:
                        print(f"[{idx}/{total_ports}] Empty port: {port_label}")
                        return "empty"

                    measured_at = datetime.now()
                    measured_date = measured_at.strftime("%Y-%m-%d")
                    measured_time = measured_at.strftime("%H:%M:%S")
                    records = build_measurement_records(
                        data,
                        olt_name,
                        batch_id,
                        measured_date,
                        measured_time,
                    )
                    if not records:
                        reason = "No matching danhba.sub entries for CTS payload"
                        print(
                            f"[{idx}/{total_ports}] No matching danhba.sub entries for {port_label}. "
                            "Nothing was saved."
                        )
                        append_port_issue_log(batch_id, task, "filtered", reason, olt_name=olt_name)
                        return "filtered"

                    insert_measurement_records(records)
                    print(
                        f"[{idx}/{total_ports}] Saved {len(records)} rows to SQLite: "
                        f"{port_label} at {measured_date} {measured_time} | batch_id={batch_id}"
                    )
                    return "success"

                if res.status_code == 500:
                    if server_error_retries < MAX_HTTP_500_RETRIES:
                        server_error_retries += 1
                        wait_seconds = 2 * server_error_retries
                        print(
                            f"[{idx}/{total_ports}] HTTP 500 for port: {port_label}. "
                            f"Retry {server_error_retries}/{MAX_HTTP_500_RETRIES} after {wait_seconds}s..."
                        )
                        time.sleep(wait_seconds)
                        continue

                    print(f"[{idx}/{total_ports}] HTTP 500 for port after retries: {port_label}")
                    append_port_issue_log(
                        batch_id,
                        task,
                        "error",
                        f"HTTP 500 after {MAX_HTTP_500_RETRIES} retries",
                        olt_name=olt_name,
                    )
                    return "error_500"

                print(f"[{idx}/{total_ports}] HTTP {res.status_code} rejected: {port_label}")
                append_port_issue_log(
                    batch_id,
                    task,
                    "error",
                    f"HTTP {res.status_code} rejected by CTS API",
                    olt_name=olt_name,
                )
                return "error"

            except requests.exceptions.Timeout:
                if timeout_retries < MAX_TIMEOUT_RETRIES - 1:
                    timeout_retries += 1
                    print(
                        f"[{idx}/{total_ports}] Timeout: {port_label}. "
                        f"Retry {timeout_retries}/{MAX_TIMEOUT_RETRIES} with server timeout "
                        f"{TIMEOUT_SEQUENCE_SECONDS[timeout_retries]}s..."
                    )
                    continue

                print(f"[{idx}/{total_ports}] Timeout after retries: {port_label}")
                append_port_issue_log(
                    batch_id,
                    task,
                    "error",
                    f"Timeout after {MAX_TIMEOUT_RETRIES} attempts",
                    olt_name=olt_name,
                )
                return "timeout"
            except Exception as exc:
                print(f"[{idx}/{total_ports}] Network error for {port_label}: {exc}")
                time.sleep(2)


def run_measurement_cycle(tasks, cycle_no):
    """
    Run one full scan of all configured ports and append results into SQLite.
    """
    total_ports = len(tasks)
    stats = {"success": 0, "empty": 0, "filtered": 0, "error": 0}
    with port_status_cache_lock:
        port_status_slot_cache.clear()
    cycle_started_dt = datetime.now()
    cycle_started_at = cycle_started_dt.strftime("%Y-%m-%d %H:%M:%S")
    batch_id = cycle_started_dt.strftime("%Y%m%d%H%M")

    print("\n" + "=" * 60)
    print(f"CYCLE {cycle_no} STARTED AT {cycle_started_at}")
    print(f"BATCH ID: {batch_id}")
    print(f"PARALLEL THREADS: {NUM_THREADS}")
    print(f"MAX THREADS PER DEVICE: {MAX_THREADS_PER_DEVICE}")
    print("=" * 60)

    repo = AlertRepository(DATABASE_PATH, SOURCE_DATABASE_PATH)
    repo.mark_batch_started(batch_id, cycle_started_dt, total_ports)

    with ThreadPoolExecutor(max_workers=NUM_THREADS) as executor:
        futures = {
            executor.submit(download_single_port, idx + 1, total_ports, task, batch_id): task
            for idx, task in enumerate(tasks)
        }

        for future in as_completed(futures):
            try:
                res_status = future.result()
            except Exception as exc:
                print(f"[FUTURE] Worker crashed: {exc}")
                stats["error"] += 1
                continue

            if res_status in {"success", "port_down"}:
                stats["success"] += 1
            elif res_status == "empty":
                stats["empty"] += 1
            elif res_status == "filtered":
                stats["filtered"] += 1
            else:
                stats["error"] += 1

    cycle_finished_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print("\n" + "=" * 50)
    print(f"CYCLE {cycle_no} FINISHED AT {cycle_finished_at}")
    print(f"Success : {stats['success']} ports")
    print(f"Empty   : {stats['empty']} ports")
    print(f"Filtered: {stats['filtered']} ports")
    print(f"Errors  : {stats['error']} ports")
    print("=" * 50)

    repo.mark_batch_completed(
        batch_id=batch_id,
        finished_at=datetime.now(),
        processed_ports=total_ports,
        success_ports=stats["success"],
        empty_ports=stats["empty"],
        filtered_ports=stats["filtered"],
        error_ports=stats["error"],
        notes=f"cycle_no={cycle_no}",
    )

    try:
        print(f"[ALERT] Batch {batch_id}: start post-processing after measurement cycle")
        alert_results = process_completed_batch(
            db_path=DATABASE_PATH,
            batch_id=batch_id,
            source_db_path=SOURCE_DATABASE_PATH,
            send_notifications=True,
        )
        notifications = alert_results.get("notifications", {})
        wide_area = notifications.get("wide_area", {})
        outage = notifications.get("outage", {})
        recovery = notifications.get("recovery", {})
        print(f"[ALERT] Batch {batch_id}: post-processing summary")
        print(
            f"[ALERT]   snapshots={alert_results.get('snapshots', 0)} "
            f"state_rows_processed={alert_results.get('state_rows_processed', 0)} "
            f"current_off={alert_results.get('current_off_count', 0)} "
            f"duration={alert_results.get('duration_seconds', 0)}s"
        )
        print(
            f"[ALERT]   wide_area_created={alert_results.get('wide_area_alerts_created', 0)} "
            f"marked_sent={wide_area.get('marked_sent_alerts', 0)} "
            f"zalo_sent={wide_area.get('zalo_messages_sent', 0)} "
            f"failed={wide_area.get('zalo_messages_failed', 0)} "
            f"no_thread={wide_area.get('no_thread', 0)}"
        )
        print(
            f"[ALERT]   outage_created={alert_results.get('outage_alerts_created', 0)} "
            f"customer_poweroff_suppressed={alert_results.get('customer_poweroff_suppressed_count', 0)} "
            f"pending_after_filters={outage.get('filtered_pending_alerts', 0)} "
            f"marked_sent={outage.get('marked_sent_alerts', 0)} "
            f"zalo_groups_sent={outage.get('zalo_groups_sent', 0)} "
            f"failed={outage.get('zalo_groups_failed', 0)} "
            f"no_thread={outage.get('no_thread_groups', 0)}"
        )
        print(
            f"[ALERT]   recovery_created={alert_results.get('recovery_alerts_created', 0)} "
            f"marked_sent={recovery.get('marked_sent_alerts', 0)} "
            f"zalo_sent={recovery.get('zalo_messages_sent', 0)} "
            f"failed={recovery.get('zalo_messages_failed', 0)} "
            f"no_thread={recovery.get('no_thread', 0)}"
        )
    except Exception as exc:
        print(f"❌ Alert engine failed for batch {batch_id}: {exc}")


def load_tasks():
    json_path = "all_ports_ready.json"
    if not os.path.exists(json_path):
        raise FileNotFoundError(f"Missing file: {json_path}")

    with open(json_path, "r", encoding="utf-8") as file_obj:
        tasks = json.load(file_obj)

    tasks = interleave_tasks_by_device(tasks)

    unique_ips = {task["deviceIp"] for task in tasks}
    device_semaphores.clear()
    device_semaphores.update(
        {ip: threading.Semaphore(MAX_THREADS_PER_DEVICE) for ip in unique_ips}
    )

    ip_to_olt_name.clear()
    ip_to_olt_name.update(
        {
            task["deviceIp"]: str(task.get("olt_name", "")).strip()
            for task in tasks
            if str(task.get("olt_name", "")).strip()
        }
    )

    return tasks


def main():
    init_database()
    repo = AlertRepository(DATABASE_PATH, SOURCE_DATABASE_PATH)
    deleted_batches = repo.delete_incomplete_batches()
    if deleted_batches:
        print(
            "[CLEANUP] Removed incomplete batches before restart: "
            + ", ".join(deleted_batches)
        )
    prepare_input_files()
    load_allowed_subs()
    tasks = load_tasks()
    print(f"Loaded {len(tasks)} port tasks.")
    print(f"SQLite database: {os.path.abspath(DATABASE_PATH)}")
    print(f"Allowed sub values loaded from {os.path.abspath(SOURCE_DATABASE_PATH)}: {len(allowed_subs)}")

    with login_lock:
        perform_browser_login(headless=True)

    cycle_no = 0
    try:
        while True:
            cycle_no += 1
            run_measurement_cycle(tasks, cycle_no)

            if MEASUREMENT_LOOP_DELAY_SECONDS > 0:
                print(
                    f"Sleeping {MEASUREMENT_LOOP_DELAY_SECONDS}s before next cycle..."
                )
                time.sleep(MEASUREMENT_LOOP_DELAY_SECONDS)
    except KeyboardInterrupt:
        print("\nStopped by user.")


if __name__ == "__main__":
    main()
