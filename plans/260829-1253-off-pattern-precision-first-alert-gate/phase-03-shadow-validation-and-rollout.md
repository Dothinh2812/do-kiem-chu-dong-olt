---
phase: 3
title: "Immediate Enforcement, Validation, and Tuning"
status: pending
priority: P1
effort: "1.5d"
dependencies: [2]
---

# Phase 3: Immediate Enforcement, Validation, and Tuning

## Overview

Deploy strict enforcement immediately, then validate with live decisions and manual NVKT labels. Tune thresholds from evidence; keep rollback to one switch.

## Requirements

- Functional: Use read-only SQLite plus decision/delivery logs; recovery speed is a proxy, not ground truth.
- Functional: Freeze KPI units, numerators, denominators, exclusions, unknown-label handling, and windows before calculation.
- Functional: Draw a reproducible random, stratified sample of would-send and would-block incidents for NVKT labels.
- Functional: Start in `enforce`; precision/sample thresholds guide later tuning and do not block rollout.
- Non-functional: Update only the smallest owning config/operator doc. Do not commit customer-level exports.

## Architecture

Use `(subscriber_key, first_off_time)` as the incident unit, not messages/repeated batches. Join decisions to successful delivery telemetry. Manual labels are `confirmed_network_fault`, `customer_power_or_non_fault`, or `unknown`, with reviewer/date/evidence.

| KPI | Numerator | Denominator |
|---|---|---|
| Confirmed precision | Alerted incidents labelled confirmed fault | Alerted incidents with definitive manual label; report unknowns separately |
| Volume reduction | Baseline incidents minus enforce-eligible incidents | Baseline candidates after existing pattern/wide-area filters |
| Eligibility rate | Enforce-eligible incidents | Baseline candidate incidents |
| Potential miss rate | Would-block incidents labelled confirmed fault | Would-block incidents with definitive manual label |
| Delivery success | Eligible incidents delivered to at least one intended target | Eligible incidents allowed by existing policy/cycle/cutoff |

Fast recovery may be a named proxy slice only; never the false-alert ground-truth label.

## Related Code Files

- Modify: `/home/vtst/do_chu_dong_api/scripts/score_off_subscribers.py` — if necessary, extend the existing audit CSV with Phase 1 fields instead of creating another analyzer.
- Modify: `/home/vtst/do_chu_dong_api/subscriber_off_scoring.py` — only if export coverage needs existing decision metadata.
- Modify: `/home/vtst/do_chu_dong_api/notification_service.py` — only if code owns operator-facing defaults.
- Modify: `/home/vtst/do_chu_dong_api/README.md` or discovered existing operator/config document — smallest owning surface for settings, telemetry, rollout, rollback.
- Modify: `/home/vtst/do_chu_dong_api/tests/test_subscriber_off_scoring.py` — export columns/format if changed.

## Implementation Steps

1. Freeze the KPI contract above in the validation report before examining outcomes; record timestamps and rule/config versions.
2. Run a read-only 30-day retrospective export. Report counts by classification, reason, history stratum (`0`, `1-2`, `3+`), duration band, and same-port count; do not infer labels from recovery.
3. Deploy `enforce` at 60 minutes immediately; capture candidate, eligible/blocked, reason, unavailable-decision, and latency counts from aggregate logs/current snapshot.
4. Deduplicate by incident key and reconcile decisions with deliveries; explain mismatches before enforce.
5. Draw a seeded random sample stratified across `classified_fault`, `no_history_absolute_duration`, `uncertain`, existing blockers, NVKT/team, and duration bands.
6. NVKT reviewers label would-send and would-block samples; calculate precision/miss estimates only on definitive labels, reporting raw denominators and unknown rate.
7. Obtain the business owner's minimum precision and total/per-stratum sample requirement for threshold tuning.
8. Keep `enforce` while decisions reconcile, wide/weak invariants pass, and latency is acceptable; use `off` immediately on a rollback signal.
9. Monitor candidate/eligible/blocked counts, delivery success, unavailable decisions, batch duration, and NVKT-reported misses/false alerts from the first enforced batch.
10. Roll back to `off` for any unexplained confirmed-fault suppression, mode-related wide/weak change, sustained decision errors, or KPI below target.
11. Update the smallest operator/config doc with settings, defaults, semantics, telemetry, checklist, and rollback.

## Validation Commands

```bash
python3 scripts/score_off_subscribers.py --db onu_measurements.db --output runtime/off-scoring-retrospective.csv
pytest -q tests/test_subscriber_off_scoring.py tests/test_alert_engine.py tests/test_notification_bridge.py tests/test_notification_service.py
pytest -q
```

## Success Criteria

- [ ] Reports state windows, incident unit, all numerators/denominators, versions, and exclusions.
- [ ] Sample is seeded/random/stratified and covers would-send/block; unknown labels remain visible.
- [ ] Proxy recovery metrics remain separate from manual labels.
- [ ] Enforcement starts at 60 minutes; target/sample are recorded for subsequent tuning.
- [ ] Zero unexplained unavailable decisions and zero gate-caused wide/weak changes.
- [ ] Rollout has monitoring and tested one-switch rollback.
- [ ] No customer-level validation artifact is committed.

## Risk Assessment

- Biased labels. Signal: high unknown/disagreement or missing strata. Response: rebalance/expand sample and stay shadow.
- Repeated batches inflate denominator. Signal: duplicate incident keys. Response: publish raw-row and distinct-incident counts; KPIs use distinct incidents.
- Proxy treated as truth. Signal: precision uses recovery time. Response: reject/rerun from frozen manual-label contract.
- Volume reduction hides faults. Signal: blocked sample/NVKT reports confirmed faults. Response: switch shadow/off, isolate reason stratum, adjust/replan.

## Security Considerations

- Keep raw samples/logs out of version control; redact names, phones, addresses. Publish aggregates unless incident evidence access is explicitly controlled.

## Dependencies and Rollback

- Depends on Phase 2 telemetry/filtering. Precision target/sample are post-rollout tuning inputs.
- Roll back by setting mode `off`, restart/reload, and verify the next batch reports legacy candidate counts.

## Unresolved Questions

- Minimum confirmed precision and sample size for later threshold tuning.
