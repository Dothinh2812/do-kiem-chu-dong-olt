---
phase: 2
title: "Phase 2: Data Models and Engine Updates"
status: done
priority: P1
effort: "1h"
dependencies: [1]
---

# Phase 2: Data Models and Engine Updates

## Overview
Connect the new database fields to the core logic in `alert_engine.py` and `alert_models.py` to identify and log physical device swaps inline with standard monitoring.

## Requirements
- Functional: Detect terminal changes (`onuSN` transition) immediately without false positives from empty measurements.
- Non-functional: Keep memory footprint small and preserve zero-overhead processing.

## Architecture
- Expand `fetch_batch_snapshot_rows` to fetch `onuSN` and `softVersion`.
- Pass these metrics into `SubscriberSnapshot` and then `subscriber_status_state`.
- Calculate the `onuSN` difference on-the-fly inside `process_completed_batch`.

## Related Code Files
- Modify: `alert_db.py`
- Modify: `alert_models.py`
- Modify: `alert_engine.py`

## Implementation Steps
1. Modify `fetch_batch_snapshot_rows` in `alert_db.py` to include `onuSN` and `softVersion` in the `SELECT` query.
2. Add `onu_sn: str = ""` and `soft_version: str = ""` to `SubscriberSnapshot` in `alert_models.py`.
3. Update `_snapshot_from_row` in `alert_engine.py` to populate these two new fields from `row.get("onuSN")` and `row.get("softVersion")`.
4. In `process_completed_batch()`, after `previous_state_rows = repo.fetch_state_map(...)`, loop through the valid `snapshots`.
5. For each snapshot, retrieve the previous state. Compare `snapshot.onu_sn` against `prev_state.get("last_onu_sn")`.
6. If both are valid (not null/empty) and they differ, append a log dictionary to a `terminal_replacements` list.
    * Note: Treat `NULL -> valid SN` as a silent initialization of the baseline state; do not log it as a replacement.
7. Update the dictionary passed into `states_to_update` with the current snapshot's `onu_sn` and `soft_version`.
8. Call the newly created `repo.insert_terminal_replacements(terminal_replacements)` at the end of the batch.

<!-- Updated: Validation Session 1 - Replacements are audit-only; do not dispatch notifications. Do not alter the core status of the state machine. -->

## Success Criteria
- [x] Changing an `onuSN` for a subscriber triggers a log entry in `terminal_replacement_log`.
- [x] Firmware changes are recorded alongside the SN changes.
- [x] Missing or empty measurements do not overwrite valid cached states or trigger false positive alerts.

## Risk Assessment
- **Risk**: False positives from API anomalies returning mismatched or partial strings.
- **Mitigation**: Statically filter `if prev_sn and curr_sn and prev_sn != curr_sn` before logging.
