---
title: Precision-first OFF alert planning
date: 2026-08-29
summary: Planned a reversible 60-minute individual OFF alert eligibility gate with shadow validation before enforcement.
---

# Precision-first OFF alert planning

## What happened

Reviewed current OFF scoring, state machine, snapshot filtering, notification dispatch, tests, and live aggregate data. Confirmed individual outage rows can notify after two OFF batches, while pattern scoring is heuristic and true incident labels are unavailable.

Created the three-phase plan at `plans/260829-1253-off-pattern-precision-first-alert-gate/` without modifying application code or databases.

## Decision

Optimize confirmed-alert precision over detection recall. Keep wide-area and weak-signal flows unchanged. Add a configurable individual OFF eligibility gate with `off`, `shadow`, and `enforce` modes; default minimum duration 60 minutes; use shadow telemetry and manually reviewed NVKT labels before enforcement. Preserve one-switch rollback to `off`.

## Next steps

Review the two unresolved rollout thresholds: minimum confirmed precision and validation sample size. Then validate the plan or execute Phase 1.

> Historical work record — not durable authority. Prefer docs/specs/ADRs for current decisions.
