---
title: Immediate enforce OFF alert gate
date: 2026-08-29
summary: "Enabled strict 60-minute individual OFF alert gating, verified all tests, and restarted the monitoring daemon."
---

# Immediate enforce OFF alert gate

## What happened

Implemented the precision-first gate for individual OFF notifications. The default is `enforce`; only eligible rows reach group/team and personal NVKT delivery. Added explicit decision metadata to the current OFF snapshot, strict 60-minute boundary handling, config validation warnings, and accurate gate telemetry. Wide-area, weak-signal, recovery, existing batching, and dedup paths remain outside the gate.

Verified `125 passed` with `pytest -q`, then restarted `do-quang-chu-dong.service`. The service started successfully with the new process and resolves `enforce` / 60 minutes.

## Decision

Prioritize alert precision over recall immediately. `INDIVIDUAL_OFF_ALERT_GATE_MODE=off` remains the rollback switch. Phase 3 monitoring and threshold tuning remain open in the plan.

## Next steps

Observe the next completed batch for the gate summary. Review sent versus blocked incident counts with NVKT and tune only after real operating evidence.

> Historical work record — not durable authority. Prefer docs/specs/ADRs for current decisions.
