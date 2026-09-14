---
phase: 1
title: "P0 Contract Normalization, Anti-Spam & Lease Refresh"
status: completed
priority: P1
effort: "3h"
dependencies: []
---

# Phase 1: P0 Contract Normalization, Anti-Spam & Lease Refresh

## Overview

Fix three critical production flaws in the customer alert gating flow:
1. Normalize `canonicalize_ma_tb` to lowercase in `onebss_precheck.py` to match `OneBSSCore` casing.
2. Track rate limit reservations and query recent send counts by `ma_tb` rather than `subscriber_key`.
3. Use dynamic `datetime.now()` for lease refresh and verify compare-and-set (CAS) finalization.

## Requirements

- Functional:
  - `canonicalize_ma_tb("HNIF_123456")` returns `"hnif_123456"`.
  - Uppercase or mixed-case `ma_tb` in candidate rows matches `OneBSSCore` output keys identically without triggering `CONTRACT_VIOLATION`.
  - Anti-spam rate limiting restricts sends to `customer_alert_max_per_day` per customer account (`ma_tb`), regardless of how many ports or ONT entries exist for that customer.
  - `refresh_customer_outage_alert_precheck_claim` updates `sent_time` to the actual execution time, extending the lease cutoff.
  - `finalize_customer_outage_alert_precheck` return value is checked; if CAS update fails (e.g. lease stolen), warning is logged and state consistency is maintained.
- Non-functional:
  - Backward compatibility for `subscriber_key` queries in `alert_db.py`.
  - Zero PII in log messages.

## Architecture

- **Case Normalization Seam**: Both `do_chu_dong_api/onebss_precheck.py` and `onebss_core/service.py` apply identical `.strip().lower()` normalization, ensuring `requested_set == returned_keys`.
- **Anti-Spam Ledger**: `same_batch_success_counts`, `same_batch_reserved_counts`, `cycle_baseline_sent_24h`, and `cycle_baseline_sent_7d` key on `can_ma` (`str`).
- **Database Query**: Update `count_recent_customer_outage_alerts` in `alert_db.py` to support querying by `ma_tb`, or add `count_recent_customer_outage_alerts_by_ma_tb(ma_tb, since)`.
- **CAS Verification**: In Pass 2, `finalize_customer_outage_alert_precheck` result is inspected. If `False`, `results["failed"] += 1` and `_skip_row("cas_conflict")`.

## Related Code Files

- Modify: `do_chu_dong_api/onebss_precheck.py`
- Modify: `do_chu_dong_api/customer_notification_service.py`
- Modify: `do_chu_dong_api/alert_db.py`
- Modify: `do_chu_dong_api/tests/test_onebss_precheck.py`
- Modify: `do_chu_dong_api/tests/test_customer_notification_service.py`

## Implementation Steps

1. In `do_chu_dong_api/onebss_precheck.py`:
   - Update `canonicalize_ma_tb(ma_tb: Any) -> str` to `str(ma_tb or "").strip().lower()`.
2. In `do_chu_dong_api/alert_db.py`:
   - Update `count_recent_customer_outage_alerts(self, key: str, since: datetime, by_ma_tb: bool = False) -> int`: when `by_ma_tb=True`, query `WHERE ma_tb = ? AND sent_time >= ? AND status = 'SENT'`.
3. In `do_chu_dong_api/customer_notification_service.py`:
   - In Pass 1 and Pass 2 rate checks, query `repo.count_recent_customer_outage_alerts(can_ma, since, by_ma_tb=True)`.
   - Key `cycle_baseline_sent_24h`, `cycle_baseline_sent_7d`, `same_batch_success_counts`, and `same_batch_reserved_counts` by `can_ma`.
   - In Pass 2 lease refresh (lines 722-731), pass `refreshed_at=datetime.now()` instead of `current_time`.
   - In Pass 2 finalization (lines 821-828), capture `finalized_ok = repo.finalize_customer_outage_alert_precheck(...)`. If `not finalized_ok`, log CAS conflict warning.
4. Update unit tests in `test_onebss_precheck.py` and `test_customer_notification_service.py` with uppercase accounts and multi-port deduplication.

## Success Criteria

- [x] `canonicalize_ma_tb("HNIF_TEST") == "hnif_test"`.
- [x] Multiple candidate rows with identical `ma_tb` but different `subscriber_key` only allow 1 send in the same batch.
- [x] Lease refresh updates `sent_time` to fresh timestamp in SQLite.
- [x] All existing and new tests pass in `test_onebss_precheck.py` and `test_customer_notification_service.py`.

## Risk Assessment

- **Risk**: Existing historical records in `customer_outage_alert_log` may have mixed-case `ma_tb`.
  - *Observable signal*: Case-sensitive SQLite queries don't match prior sends.
  - *Mitigation*: In SQLite, `WHERE LOWER(ma_tb) = LOWER(?)` or ensure stored `ma_tb` is stored canonicalized lowercase.
