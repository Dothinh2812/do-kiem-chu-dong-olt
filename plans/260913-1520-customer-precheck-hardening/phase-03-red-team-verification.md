---
phase: 3
title: "P0 Red Team Adversarial Verification & Validation"
status: completed
priority: P1
effort: "2h"
dependencies: [1, 2]
---

# Phase 3: P0 Red Team Adversarial Verification & Validation

## Overview

Execute comprehensive adversarial tests simulating production failure modes, verify all edge cases are closed, run full regression test suites, and validate daemon health.

## Requirements

- Functional:
  - Adversarial Test Suite covers all 7 Red Team scenarios from `plan.md`.
  - All unit and integration test suites pass with 100% green status.
  - Live daemon operation is checked and verified against systemd logs.
- Non-functional:
  - Zero sensitive data (tokens, passwords, OTPs, customer PII) exposed in test outputs, logs, or error codes.

## Implementation Steps

1. Create adversarial tests in `tests/test_customer_precheck_adversarial.py` covering:
   - **Test 1: Mixed-case `ma_tb`**: Pass `["HNIF_99999", "Sty_00123"]`, verify `OneBSSCore` decisions match without `CONTRACT_VIOLATION`.
   - **Test 2: Multi-port anti-spam**: Feed 3 candidate rows with different `subscriber_key` but identical `ma_tb`. Verify exactly 1 alert is eligible/sent, other 2 are skipped with `daily_limit_exceeded`.
   - **Test 3: Stolen claim CAS conflict**: Simulate worker A holding expired lease, worker B reclaiming it, worker A CAS finalization returns `False`. Verify worker A does not crash or double-count.
   - **Test 4: Session cache retention on timeout**: Simulate `socket.timeout` during token liveliness check, verify `session.json` is NOT deleted.
   - **Test 5: Stale outage retry cap**: Provide candidate row with `first_off_time` 36 hours ago in `PRECHECK_FAILED` state. Verify it is rejected with `outage_too_old`.
   - **Test 6: Non-blocking event loop**: Verify `send_customer_outage_message` yields control to concurrent tasks during HTTP latency.
2. Run full regression tests:
   - `pytest -q` in `/home/vtst/onebss_core` (expected: 45+ passed).
   - `pytest -q` in `/home/vtst/do_chu_dong_api` (expected: 165+ passed).
3. Validate daemon service status:
   - Check `systemctl status do-quang-chu-dong.service`.
   - Verify recent measurement batch log entries.

## Success Criteria

- [x] All 6 adversarial test scenarios pass with assertions verifying expected fail-closed and anti-spam defenses.
- [x] `pytest -q` in `onebss_core` passes with 0 failures.
- [x] `pytest -q` in `do_chu_dong_api` passes with 0 failures.
- [x] Systemd daemon runs cleanly with no unhandled exceptions.

## Risk Assessment

- **Risk**: Concurrent test database access interfering with production `onu_measurements.db`.
  - *Observable signal*: Database locked or test rows appearing in production tables.
  - *Mitigation*: All tests strictly use `tmp_path` fixtures for isolated temporary SQLite databases.
