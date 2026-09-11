---
phase: 3
title: "Pipeline Integration & Verification"
status: in-progress
priority: P1
effort: "1h"
dependencies: [2]
---

# Phase 3: Pipeline Integration & Verification

## Overview
Connect the customer outage notification service into the alert pipeline in `notification_bridge.py`, ensure comprehensive unit testing, and verify with `pytest -q`.

## Requirements
- Functional:
  - In `notification_bridge.py`, call `process_customer_outage_alerts` during `_dispatch` for gated individual outage candidates.
  - Return customer notification metrics in the dispatch results dictionary (`customer_outage`).
  - Do not block or disrupt existing group, wide-area, or personal alerts.
  - Comprehensive unit test coverage for anti-spam filters, duration calculations, gateway errors, and bridge dispatching.
- Non-functional:
  - Full test suite passing without regression.

## Architecture
- `notification_bridge.py`:
  - Hook customer alert processing after evaluating individual outage candidates.
  - Record results under `results["customer_outage"]`.
- `tests/test_customer_notification_service.py`:
  - Test quiet hours detection (in-window vs out-of-window).
  - Test idempotency by `first_off_time`.
  - Test 24h daily limit enforcement.
  - Test 7d weekly limit enforcement.
  - Test message rendering and duration formatting.
  - Test HTTP dispatch mock (success, 404 contact not found, 500 error, timeout).
- `tests/test_notification_bridge.py`:
  - Test pipeline execution with customer outage enabled/disabled.

## Related Code Files
- Modify: `notification_bridge.py`
- Create: `tests/test_customer_notification_service.py`
- Modify: `tests/test_notification_bridge.py`

## Implementation Steps
1. Update `notification_bridge.py` to trigger customer outage notifications when enabled.
2. Write unit tests in `tests/test_customer_notification_service.py`.
3. Add integration test in `tests/test_notification_bridge.py`.
4. Run `pytest -q` and verify 100% test pass rate.

## Success Criteria
- [x] Customer outage alerts are triggered in the notification bridge.
- [x] All unit tests in `test_customer_notification_service.py` pass.
- [x] Existing test suite remains green.

