---
phase: 2
title: "Precision Alert Gate Integration"
status: pending
priority: P1
effort: "2d"
dependencies: [1]
---

# Phase 2: Precision Alert Gate Integration

## Overview

Carry eligibility into the current-OFF snapshot and apply one shared mode-aware filter before team/group and personal NVKT delivery, preserving all downstream policies.

## Requirements

- Functional: Map scoring results to snapshot rows by `subscriber_key`; retain classification, scores, duration, eligibility, reason, and rule version.
- Functional: default `enforce` passes only eligible rows immediately; `off` sends today's active rows for rollback; `shadow` is optional diagnostics.
- Functional: Both team/group and personal branches consume the same mode-filtered set.
- Functional: Blocked/uncertain rows remain observable in the current snapshot and aggregate batch logs.
- Non-functional: Gate after existing pattern/wide-area filtering and before time-window, cutoff, cycle, batching, daily dedup, and sent-state filters.
- Non-functional: Do not change wide-area, weak-signal, recovery, or two-batch state-machine behavior. No schema change.

## Architecture

`process_completed_batch()` already scores current OFF rows before snapshot construction. Pass a result map to `build_current_off_snapshot()`. Preserve all rows. In the bridge, keep existing active filtering, apply one mode filter, then feed that shared result to both current-OFF pipelines.

```text
state machine -> scoring -> snapshot + decision metadata
                         -> existing pattern/wide-area filter
                         -> off/shadow/enforce filter
                         -> shared candidates
                            |-> existing group policies/send
                            `-> existing personal policies/dedup/send
wide area + weak signal ------------------------------------> unchanged
```

## Related Code Files

- Modify: `/home/vtst/do_chu_dong_api/alert_engine.py` — pass result map and report gate counts.
- Modify: `/home/vtst/do_chu_dong_api/current_off_snapshot.py` — serialize decision metadata and summary counts without an unbounded history file.
- Modify: `/home/vtst/do_chu_dong_api/notification_bridge.py` — one shared mode-aware candidate set.
- Modify: `/home/vtst/do_chu_dong_api/notification_service.py` — expose normalized gate settings.
- Modify: `/home/vtst/do_chu_dong_api/tests/test_alert_engine.py` — snapshot metadata and state integration.
- Modify: `/home/vtst/do_chu_dong_api/tests/test_notification_bridge.py` — mode matrix and preservation integrations.
- Modify: `/home/vtst/do_chu_dong_api/tests/test_notification_service.py` — shared config/filter contract where owned.

## Implementation Steps

1. Add snapshot tests for eligible/blocked rows, metadata, rule/reason summary counts.
2. Extend builders with an optional result map. Missing decisions become `decision_unavailable`: sendable in `off`/`shadow`, blocked in `enforce`.
3. Add aggregate decision counts to existing batch logs and retain per-row mode, decision, reason, rule, duration, classification, and history count in the atomic current snapshot. Do not append an unbounded per-cycle JSONL.
4. Add a pure bridge filter: `off`/`shadow` return active rows; `shadow` logs would-block; `enforce` returns `alert_eligible is True`.
5. Compute shared candidates once, then preserve the current ordering of group and personal cutoff/cycle/dedup/sent-state logic.
6. Parameterize bridge tests with eligible, uncertain, pattern-suppressed, and wide-area rows across all modes.
7. Prove wide-area/weak-signal sends and counters do not vary by mode.
8. Re-run group-disabled, personal-disabled, independent-cycle, cutoff, and once-per-day dedup cases in enforce.
9. Run focused and full tests.

## Success Criteria

- [x] `off` candidate identities equal current behavior.
- [x] `shadow` changes neither delivery nor sent-state and records decisions.
- [x] `enforce` excludes ineligible rows from both group/team and personal delivery.
- [x] Existing suppressed rows remain observable and excluded.
- [x] Wide-area and weak-signal behavior is identical across modes.
- [x] Existing timing, cycles, channel results, dedup, and sent-state tests pass.
- [x] `pytest -q tests/test_alert_engine.py tests/test_notification_bridge.py tests/test_notification_service.py` passes.
- [x] `pytest -q` passes.

## Risk Assessment

- Branches may diverge. Signal: different pre-policy identities/counts. Response: switch off and centralize both on the shared filter before resuming.
- Missing decisions may suppress valid incidents. Signal: nonzero unexplained `decision_unavailable`. Response: switch shadow/off and fix mapping before enforce.
- Boundary may affect weak/wide alerts. Signal: mode-dependent preservation tests/counters. Response: stop rollout and move filter back inside current-OFF only.
- Snapshot metadata may add payload size. Signal: snapshot write latency rises materially. Response: retain only fields needed for filtering/audit; telemetry must not block dispatch.

## Security Considerations

- Telemetry uses technical identifiers only and omits contact/address fields. Atomic snapshot replacement remains; telemetry I/O failure logs but does not fail the batch.

## Dependencies and Rollback

- Depends on Phase 1. Immediate rollback: mode `off` returns the legacy active-row set; additive telemetry needs no rollback.
