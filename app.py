import os
import json
import time
import re
import shutil
import sqlite3
import threading
import unicodedata
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
    from .login import read_otp_from_file
except ImportError:
    from alert_db import AlertRepository
    from alert_engine import process_completed_batch
    from config import Config
    from login import read_otp_from_file

# Parallel download settings
NUM_THREADS = 64
MAX_THREADS_PER_DEVICE = 1
AUTH_RETRY_STATUS_CODES = {401, 403}
MAX_HTTP_500_RETRIES = 2
MAX_TIMEOUT_RETRIES = 3
TIMEOUT_SEQUENCE_SECONDS = [15, 30, 60]  # Increasing timeouts for each retry attempt
MEASUREMENT_LOOP_DELAY_SECONDS = 25
DATABASE_PATH = "onu_measurements.db"
SOURCE_DATABASE_PATH = "database.db"
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
device_semaphores = {}
ip_to_olt_name = {}
allowed_subs = set()

OLT_TEXT_DATA = """HNI.TTT.THH.OLT.HU.6.1	MA5801-GP8	10.10.60.106
HNI.BVI.PCG.OLT.HU.6.1	MA5801-GP8	10.10.60.114
HNI.BVI.PTH.OLT.HU.6.1	MA5801-GP8	10.10.60.115
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
    context = browser.new_context()
    page = context.new_page()

    login_url = getattr(Config, "BAOCAO_URL", "https://cts.vnpt.vn")
    print(f"     Opening {login_url} ...")
    page.goto(login_url, timeout=getattr(Config, "PAGE_LOAD_TIMEOUT", 60000))
    page.wait_for_load_state("networkidle")

    try:
        page.locator('//*[@id="username"]').fill(getattr(Config, "BAOCAO_USERNAME", ""))
        time.sleep(1)
        page.locator('//*[@id="password"]').fill(getattr(Config, "BAOCAO_PASSWORD", ""))
        time.sleep(1)
        page.locator('//*[@id="fm1"]/section/button').click()
        time.sleep(3)

        otp_code = None
        if "read_otp_from_file" in globals():
            otp_code = read_otp_from_file()

        if otp_code:
            page.locator('//*[@id="passOTP"]').fill(otp_code)
            time.sleep(1)
            page.locator('//*[@id="loginForm"]/div[1]/button').click()
            time.sleep(5)
        else:
            print("     Please complete OTP/Captcha manually in the browser. Waiting 20s...")
            time.sleep(20)
    except Exception:
        print("     Please complete login manually in the browser. Waiting 20s...")
        time.sleep(20)

    page.wait_for_load_state("networkidle")
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

    port_label = f"{ip}_F{f_idx}_S{s_idx}_P{p_idx}"
    data_url = "https://cts.vnpt.vn/Linetest/Test/GetListByPonPortAsync"
    params = {"deviceIp": ip, "frame": f_idx, "slot": s_idx, "port": p_idx}
    server_error_retries = 0
    timeout_retries = 0

    device_semaphore = device_semaphores[ip]
    with device_semaphore:
        while True:
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
                        print(
                            f"[{idx}/{total_ports}] No matching danhba.sub entries for {port_label}. "
                            "Nothing was saved."
                        )
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
                    return "error_500"

                print(f"[{idx}/{total_ports}] HTTP {res.status_code} rejected: {port_label}")
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

            if res_status == "success":
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
