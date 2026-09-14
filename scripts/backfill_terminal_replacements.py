#!/usr/bin/env python3
"""
scripts/backfill_terminal_replacements.py

Backfills terminal (ONT/ONU) replacement logs from historical `onu_measurements` batches.
Reads via SQLite read-only mode to avoid locking production processes.
"""

import argparse
import os
import sqlite3
import sys
from datetime import datetime, timedelta
from typing import Dict, List


def is_valid_onu_sn(sn: str) -> bool:
    if not sn:
        return False
    sn = sn.strip()
    if len(sn) < 6:
        return False
    if set(sn) <= {'0', 'f', 'F', '-', ' '}:
        return False
    if sn.upper() in {"N/A", "NULL", "NONE", "UNKNOWN"}:
        return False
    return True


def parse_args():
    parser = argparse.ArgumentParser(description="Backfill terminal replacements from onu_measurements")
    parser.add_argument("--db-path", default="onu_measurements.db", help="Path to onu_measurements.db")
    parser.add_argument("--start-date", default="2026-09-01T00:00:00", help="Start date (ISO format) to record replacements")
    parser.add_argument("--baseline-hours", type=int, default=24, help="Hours prior to start-date for baseline initialization")
    parser.add_argument("--dry-run", action="store_true", help="Simulate run without writing to database")
    return parser.parse_args()


def run_backfill(db_path: str, start_date_str: str, baseline_hours: int = 24, dry_run: bool = False):
    start_dt = datetime.fromisoformat(start_date_str)
    baseline_dt = start_dt - timedelta(hours=baseline_hours)
    baseline_str = baseline_dt.isoformat()

    print(f"[*] Connecting to {db_path} (read-only mode)...")
    ro_uri = f"file:{os.path.abspath(db_path)}?mode=ro"
    ro_conn = sqlite3.connect(ro_uri, uri=True)
    ro_conn.row_factory = sqlite3.Row

    # 1. Fetch batches
    cursor = ro_conn.execute(
        """
        SELECT batch_id, started_at
        FROM measurement_batches
        WHERE started_at >= ?
        ORDER BY started_at ASC
        """,
        (baseline_str,),
    )
    batches = cursor.fetchall()
    print(f"[*] Found {len(batches)} batches from {baseline_str} onwards.")

    if not batches:
        print("[!] No batches found. Exiting.")
        return

    # In-memory subscriber state tracking: subscriber_key -> {"sn": ..., "sv": ..., "ma_tb": ...}
    states: Dict[str, Dict[str, str]] = {}
    replacement_logs: List[Dict] = []
    
    baseline_batch_count = 0
    active_batch_count = 0

    print("[*] Processing batches...")
    for idx, b in enumerate(batches, start=1):
        batch_id = b["batch_id"]
        started_at = b["started_at"]
        is_baseline = started_at < start_date_str

        if is_baseline:
            baseline_batch_count += 1
        else:
            active_batch_count += 1

        rows = ro_conn.execute(
            """
            SELECT "Cổng" AS subscriber_key, accountFiber, onuSN, softVersion, NgayDo, ThoiGianDo
            FROM onu_measurements
            WHERE batch_id = ?
            """,
            (batch_id,),
        ).fetchall()

        for row in rows:
            sub_key = row["subscriber_key"]
            if not sub_key:
                continue

            sn = (row["onuSN"] or "").strip()
            sv = (row["softVersion"] or "").strip()
            ma_tb = (row["accountFiber"] or "").strip()
            ngay_do = row["NgayDo"] or ""
            thoi_gian_do = row["ThoiGianDo"] or ""
            measured_at = f"{ngay_do} {thoi_gian_do}".strip() or started_at

            prev = states.get(sub_key)
            valid_sn = is_valid_onu_sn(sn)

            if prev is None:
                if valid_sn:
                    states[sub_key] = {"sn": sn, "sv": sv, "ma_tb": ma_tb}
                continue

            prev_sn = prev.get("sn")
            prev_sv = prev.get("sv")

            if not is_baseline:
                if prev_sn and valid_sn and prev_sn != sn:
                    replacement_logs.append({
                        "subscriber_key": sub_key,
                        "ma_tb": ma_tb or prev.get("ma_tb") or "",
                        "old_onu_sn": prev_sn,
                        "new_onu_sn": sn,
                        "old_soft_version": prev_sv or "",
                        "new_soft_version": sv or "",
                        "changed_at": measured_at,
                        "batch_id": batch_id,
                    })
                    prev["sn"] = sn
                    prev["sv"] = sv
                    if ma_tb:
                        prev["ma_tb"] = ma_tb
                elif valid_sn:
                    prev["sn"] = sn
                    prev["sv"] = sv
                    if ma_tb:
                        prev["ma_tb"] = ma_tb
            else:
                # In baseline period, simply update tracking
                if valid_sn:
                    prev["sn"] = sn
                    prev["sv"] = sv
                    if ma_tb:
                        prev["ma_tb"] = ma_tb

        if idx % 50 == 0 or idx == len(batches):
            print(f"    - Processed {idx}/{len(batches)} batches (replacements found so far: {len(replacement_logs)})")

    ro_conn.close()

    print(f"\n[+] Processing finished:")
    print(f"    - Baseline batches (pre-seed): {baseline_batch_count}")
    print(f"    - Active tracked batches: {active_batch_count}")
    print(f"    - Unique active subscribers tracked: {len(states)}")
    print(f"    - Total ONT replacements identified: {len(replacement_logs)}")

    if not replacement_logs:
        print("[*] No ONT replacements to insert.")
        return

    if dry_run:
        print("[*] DRY-RUN enabled: skipping database write.")
        if replacement_logs:
            print("\nSample detected replacements:")
            for sample in replacement_logs[:5]:
                print(f"    {sample['changed_at']} | {sample['ma_tb']} ({sample['subscriber_key']}): {sample['old_onu_sn']} -> {sample['new_onu_sn']}")
        return

    # Write to database
    print(f"[*] Writing {len(replacement_logs)} replacement logs to {db_path}...")
    import time
    
    # 1. Insert terminal replacements with retry
    for attempt in range(5):
        try:
            with sqlite3.connect(db_path, timeout=60.0) as rw_conn:
                rw_conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS terminal_replacement_log (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        subscriber_key TEXT NOT NULL,
                        ma_tb TEXT,
                        old_onu_sn TEXT,
                        new_onu_sn TEXT,
                        old_soft_version TEXT,
                        new_soft_version TEXT,
                        changed_at DATETIME NOT NULL,
                        batch_id TEXT
                    )
                    """
                )
                rw_conn.executemany(
                    """
                    INSERT INTO terminal_replacement_log (
                        subscriber_key, ma_tb, old_onu_sn, new_onu_sn,
                        old_soft_version, new_soft_version, changed_at, batch_id
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    [
                        (
                            r["subscriber_key"],
                            r["ma_tb"],
                            r["old_onu_sn"],
                            r["new_onu_sn"],
                            r["old_soft_version"],
                            r["new_soft_version"],
                            r["changed_at"],
                            r["batch_id"],
                        )
                        for r in replacement_logs
                    ]
                )
                rw_conn.commit()
            print(f"[✓] Successfully committed {len(replacement_logs)} logs into terminal_replacement_log!")
            break
        except sqlite3.OperationalError as e:
            if "locked" in str(e).lower() and attempt < 4:
                print(f"    [!] DB locked on insert, retrying in 2s (attempt {attempt+1}/5)...")
                time.sleep(2)
            else:
                raise

    # 2. Update subscriber_status_state in small chunks
    print(f"[*] Updating latest last_onu_sn & last_soft_version in subscriber_status_state...")
    update_params = [
        (v["sn"], v["sv"], k)
        for k, v in states.items()
        if v.get("sn")
    ]
    chunk_size = 2000
    for i in range(0, len(update_params), chunk_size):
        chunk = update_params[i:i + chunk_size]
        for attempt in range(5):
            try:
                with sqlite3.connect(db_path, timeout=60.0) as rw_conn:
                    rw_conn.executemany(
                        """
                        UPDATE subscriber_status_state
                        SET last_onu_sn = ?, last_soft_version = ?
                        WHERE subscriber_key = ?
                        """,
                        chunk
                    )
                    rw_conn.commit()
                break
            except sqlite3.OperationalError as e:
                if "locked" in str(e).lower() and attempt < 4:
                    print(f"    [!] DB locked on chunk update, retrying in 2s (attempt {attempt+1}/5)...")
                    time.sleep(2)
                else:
                    raise

    print(f"[✓] Successfully updated {len(update_params)} subscriber states!")


if __name__ == "__main__":
    args = parse_args()
    run_backfill(
        db_path=args.db_path,
        start_date_str=args.start_date,
        baseline_hours=args.baseline_hours,
        dry_run=args.dry_run
    )
