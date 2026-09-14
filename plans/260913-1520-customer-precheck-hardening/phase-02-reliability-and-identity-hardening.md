---
phase: 2
title: "P1 Reliability, Identity & Async Dispatch Hardening"
status: completed
priority: P1
effort: "4h"
dependencies: [1]
---

# Phase 2: P1 Reliability, Identity & Async Dispatch Hardening

## Overview

Harden session lifecycle, service identity selection, retry boundaries, and async gateway I/O:
1. Protect `session.json` from deletion when `check_token_alive` times out or deadline expires.
2. Prioritize broadband Internet (`dichvuvt_id == 9`) when multiple subscriptions exist for the same `ma_tb`.
3. Add a maximum retry age cutoff of 24h to prevent sending alerts for ancient multi-day outages.
4. Make `send_customer_outage_message` non-blocking using `asyncio.to_thread`.
5. Ensure quiet hours fail closed on invalid time window format for customer alerts.

## Requirements

- Functional:
  - In `onebss_core/auth.py`, `clear_session_cache` is ONLY invoked when the server explicitly rejects the token (e.g. HTTP 401/403 or explicit unauthorized response). Socket timeouts, URLError timeouts, and deadline exhaustion retain the cache file.
  - In `onebss_core/client.py`, if `lay_danhba_theo_matb_new` returns multiple rows, select row where `dichvuvt_id == 9` (Fiber Internet) if present, falling back to `rows[0]`.
  - In `customer_notification_service.py`, any retry candidate where `(current_time - fot_dt).total_seconds() > 24 * 3600` is skipped with reason `outage_too_old` and finalized as `FAILED`.
  - In `customer_notification_service.py`, `send_customer_outage_message` runs HTTP I/O inside `asyncio.to_thread` so the event loop remains responsive.
  - In `notification_service.py:330-332`, if `fail_closed=True` or when evaluating customer quiet hours, invalid format returns `False`.
- Non-functional:
  - All existing `onebss_core` tests (45 tests) must pass.
  - Zero performance regression on high-concurrency batches.

## Architecture

- **Session Resilience**: Token liveliness check distinguishes transient transport failure (`TimeoutError`, `socket.timeout`, deadline expired) from authentication failure (`HTTPError 401/403`).
- **Broadband Priority**: Fiber broadband is service type 9 (`dichvuvt_id = 9`). Outage monitoring alerts are for broadband loss, so ticket precheck must query broadband incident history.
- **Aging Cutoff**: `MAX_CUSTOMER_ALERT_RETRY_AGE_HOURS = 24.0` in configuration. Any outage older than 24 hours is rejected.
- **Threadpool Offload**: Wrap synchronous `requests.post` inside `asyncio.to_thread` to prevent event-loop starvation during batch dispatch.

## Related Code Files

- Modify: `onebss_core/src/onebss_core/auth.py`
- Modify: `onebss_core/src/onebss_core/client.py`
- Modify: `do_chu_dong_api/customer_notification_service.py`
- Modify: `do_chu_dong_api/notification_service.py`
- Modify: `onebss_core/tests/test_core.py`
- Modify: `do_chu_dong_api/tests/test_customer_notification_service.py`

## Implementation Steps

1. In `onebss_core/src/onebss_core/auth.py`:
   - Update `check_token_alive` to return an enum or tuple `(is_alive: bool, is_definitive_auth_failure: bool)`.
   - In `create_session`, only call `clear_session_cache` if `is_definitive_auth_failure` is True.
2. In `onebss_core/src/onebss_core/client.py`:
   - In `lookup_open_incident_facts`, when processing `matching_rows`:
     ```python
     fiber_rows = [r for r in matching_rows if str(r.get("DICHVUVT_ID", r.get("dichvuvt_id", ""))) in ("9",)]
     sub_row = fiber_rows[0] if fiber_rows else matching_rows[0]
     ```
3. In `do_chu_dong_api/customer_notification_service.py`:
   - In Pass 1 (retry check), check:
     ```python
     max_retry_age_hours = float(active_config.get("customer_alert_max_retry_age_hours") or 24.0)
     if is_retry and (current_time - fot_dt).total_seconds() > max_retry_age_hours * 3600:
         _skip_row("outage_too_old")
         repo.finalize_customer_outage_alert_precheck(
             request_id=req_id,
             batch_id=batch_id,
             expected_claimed_at=expected_claimed,
             status="FAILED",
             finalized_at=current_time,
             error_reason="outage_too_old",
         )
         continue
     ```
   - In `send_customer_outage_message`, wrap `requests.post(...)` inside `await asyncio.to_thread(_do_post, ...)`.
4. In `do_chu_dong_api/notification_service.py`:
   - In `_is_within_time_window`, add optional `fail_closed: bool = False`. Pass `fail_closed=True` for customer outage alerts.
5. Update tests in both repos to verify new behaviors.

## Success Criteria

- [x] Network timeout during `check_token_alive` does not delete `session.json`.
- [x] Multi-service subscriber selects `dichvuvt_id=9` row when available.
- [x] Outages with `first_off_time` older than 24h are skipped with reason `outage_too_old`.
- [x] Event loop benchmark shows non-blocking behavior during Zalo message dispatch.
- [x] All unit tests pass in `onebss_core` and `do_chu_dong_api`.

## Risk Assessment

- **Risk**: Some customers only have MyTV without Fiber under the same account.
  - *Observable signal*: `fiber_rows` is empty.
  - *Mitigation*: Fallback to `matching_rows[0]` preserves existing behavior for non-Fiber accounts.
