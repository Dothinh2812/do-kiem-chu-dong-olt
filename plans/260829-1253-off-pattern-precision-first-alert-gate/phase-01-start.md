---
phase: 1
title: "Decision Contract and Scoring Eligibility"
status: pending
priority: P1
effort: "1.5d"
dependencies: []
---

# Phase 1: Decision Contract and Scoring Eligibility

## Overview

Turn the precision policy into an executable, backward-compatible result and config contract. Keep heuristic classification separate from auditable eligibility.

## Requirements

- Functional: Parse exactly `off`, `shadow`, `enforce`; default/fallback to `enforce` for immediate strict rollout. Explicit `off` remains rollback.
- Functional: Parse a positive minimum duration, default 60; invalid values warn and fall back.
- Functional: Extend `OffScoringResult` with defaulted metadata so old constructors/callers remain valid.
- Functional: Expose absolute duration, eligibility boolean, named reason, and stable rule version without calling score/confidence a probability.
- Functional: Hard blockers first; duration floor; likely-fault path; no-history absolute-duration fallback; uncertain otherwise blocked.
- Non-functional: Keep 30-day history and existing classification behavior; no table/migration.

## Architecture

Keep `classify_off_event()` responsible for features/categories. A pure evaluator consumes its result plus threshold and returns decision metadata. Config parsing stays with existing environment-to-dict helpers.

```text
wide-area or likely-self-power -> blocked
duration < minimum            -> blocked: minimum_duration
likely-individual-fault       -> eligible: classified_fault
no history                    -> eligible: no_history_absolute_duration
otherwise                     -> blocked: uncertain
```

## Related Code Files

- Modify: `/home/vtst/do_chu_dong_api/subscriber_off_scoring.py` — optional result fields, pure evaluator, CSV fields, absolute-duration feature.
- Modify: `/home/vtst/do_chu_dong_api/notification_service.py` — allow-listed mode and positive duration config.
- Modify: `/home/vtst/do_chu_dong_api/tests/test_subscriber_off_scoring.py` — decision table, boundaries, aging, compatibility, terminology.
- Modify: `/home/vtst/do_chu_dong_api/tests/test_notification_service.py` — defaults and invalid config parsing.

## Implementation Steps

1. Add failing table-driven tests at 59/60/61 minutes for likely-fault, likely-self-power, wide-area, no-history uncertain, and sparse-history uncertain cases.
2. Prove old `OffScoringResult` construction still works and new fields have neutral defaults.
3. Prove one no-history incident changes from blocked before 60 to eligible at 60 without fabricated history.
4. Add `current_off_duration_minutes` alongside existing hours and CSV output.
5. Implement reason constants/evaluator with explicit precedence; attach after classification.
6. Add `INDIVIDUAL_OFF_ALERT_GATE_MODE` (`enforce`) and `INDIVIDUAL_OFF_MIN_DURATION_MINUTES` (`60`) to default config; normalize input and warn/fallback on invalid values.
7. Verify new labels use “heuristic score,” never probability/calibrated confidence.
8. Run focused tests.

## Success Criteria

- [x] Existing constructors/tests remain compatible.
- [x] Exactly-60-minute behavior is tested.
- [x] No-history incidents eventually qualify; sparse-history uncertain incidents do not bypass the contract.
- [x] Likely-self-power and wide-area cases remain ineligible regardless of duration.
- [x] Invalid config cannot crash a batch and emits a fallback warning.
- [x] `pytest -q tests/test_subscriber_off_scoring.py tests/test_notification_service.py` passes.

## Risk Assessment

- No-history fallback may admit long customer power-offs. Signal: low manual precision in that shadow stratum. Response: remain out of enforce and raise only that fallback threshold or replan it.
- Existing consumers may imply probability. Signal: `%`/probability language in output. Response: correct presentation while preserving compatibility fields.
- Invalid config may change filtering. Signal: fallback warning. Response: fall back to strict `enforce`/60, notify operator, correct config; use explicit `off` only for rollback.

## Security Considerations

- Allow-list modes and bound positive integers. Do not log contact/address data in warnings/tests.

## Dependencies and Rollback

- Depends only on existing scorer/config loader. Phase 2 activates strict filtering because the default is `enforce`.
