# Precision-first individual OFF alert gate review

Scope reviewed read-only: the requested gate files, tests, README, and the gate plan. Unrelated dirty-worktree changes were ignored.

## Findings

### P2 — Invalid gate configuration silently changes enforcement behavior

- Evidence: [`notification_service.py`](../../../notification_service.py#L139) normalizes an invalid mode to `enforce` and an invalid/non-positive duration to `60` at [lines 149–159](../../../notification_service.py#L149), but neither path emits a warning.
- Cause: An operator typo such as `INDIVIDUAL_OFF_ALERT_GATE_MODE=enfroce` silently activates strict filtering. This fails the Phase 1 success criterion requiring a fallback warning, and removes the operator signal needed to distinguish deliberate enforcement from a bad setting.
- Test: Capture stdout/logging for invalid mode and duration and assert a single non-sensitive fallback warning accompanies the `enforce`/`60` result.

### P2 — Shadow-mode dispatch telemetry reports all candidates as eligible and zero blocked

- Evidence: [`notification_bridge.py`](../../../notification_bridge.py#L428) retains all rows in `shadow` ([lines 35–38](../../../notification_bridge.py#L35)), then reports `eligible=len(gated_outage_alerts)` and `blocked=active-gated` at [lines 435–440](../../../notification_bridge.py#L435). Consequently, with one decision-eligible and one decision-blocked active row, shadow logs `eligible=2 blocked=0`.
- Cause: The values measure dispatch candidates rather than eligibility decisions. This contradicts the Phase 2 requirement that shadow log would-block decisions and makes its aggregate observability misleading during a rollback/diagnostic run.
- Test: Dispatch two active snapshot rows in `shadow`, one with `alert_eligible=True` and one false; assert delivery remains two rows while gate telemetry reports eligible `1`, blocked `1` (or explicit candidate/would-block counters with those values).

## Verified behavior

- Default config is `enforce` with a 60-minute floor; floor uses integer seconds `// 60`, so 59:59 cannot round up.
- Decision precedence hard-blocks wide-area and likely-self-power rows; at 60 minutes it permits likely individual faults and no-history rows, while sparse-history uncertain rows remain blocked.
- The bridge computes `gated_outage_alerts` once and passes that exact pre-policy set to both group and personal branches. Wide-area, weak-signal, and recovery branches occur outside the gate.
- Snapshot rows preserve decision metadata and summary counts; gate filtering adds no database migration. `off` restores the legacy active-row set.
- Fresh verification: `pytest -q tests/test_subscriber_off_scoring.py tests/test_notification_service.py tests/test_alert_engine.py tests/test_notification_bridge.py` — **95 passed**. `git diff --check` for scoped files passed.

Status: DONE
Summary: Two P2 observability/configuration findings; enforcement-path behavior and focused tests otherwise satisfy the stated gate contract.
Concerns/Blockers: No blocking correctness finding found; fix the two P2 findings before relying on invalid-config or shadow-mode operational telemetry.
