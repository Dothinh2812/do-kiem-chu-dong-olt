---
title: "Precision-First Individual OFF Alert Gate"
description: "Add an observable, reversible eligibility gate that sends only high-confidence single-subscriber OFF incidents while preserving unrelated alerts."
status: in-progress
priority: P1
effort: 5d
issue: null
branch: checkpoint/off-pattern-20260622
tags: [feature, backend, database, critical]
blockedBy: []
blocks: []
created: 2026-08-29
---

# Precision-First Individual OFF Alert Gate

## Overview

Improve confirmed-incident precision for single-subscriber OFF alerts, accepting delayed or missed low-confidence cases. Reuse the interpretable 30-day recovery-history scorer, add absolute OFF-duration evidence, carry the decision into the current-OFF snapshot, and apply one shared gate to team/group and personal NVKT delivery.

## Brainstorm Contract

### Desired Outcome

- Increase `confirmed true single-subscriber incidents / single-subscriber incidents alerted`.
- Default minimum OFF duration: 60 minutes.
- `enforce` is enabled by default immediately; only high-confidence fault cases notify and uncertain cases remain observable.

### Constraints

- SQLite and current 30-day recovery history remain the data sources; no reliable ground-truth labels exist.
- Wide-area, weak-signal, recovery, time-window, batching, cycle, deduplication, and sent-state behavior remain unchanged.
- Gate both team/group current-OFF messages and personal NVKT current-OFF delivery.
- Preserve the dirty worktree and existing dual-import conventions.

### Non-Goals

- Machine learning, score-as-probability claims, ticket/CRM integration, or UI.
- Database migration unless snapshot/JSONL telemetry proves insufficient.
- Reworking wide-area or weak-signal classification and delivery.

### Acceptance Criteria

- Default `enforce` sends only eligible rows immediately; `off` remains the one-switch rollback and `shadow` remains optional diagnostics.
- A backward-compatible result exposes absolute duration and a named reason; a no-history OFF eventually becomes eligible unless an existing hard blocker applies.
- One eligible row set feeds team/group and personal delivery before existing policy, cycle, cutoff, dedup, and sent-state filters.
- Current snapshot and aggregate logs retain eligible and blocked rows with reason, mode, rule version, and counts; no schema change or unbounded per-row JSONL.
- Post-enforcement review uses explicit KPI denominators and a random, stratified NVKT sample to tune thresholds without delaying rollout.
- One config switch restores current behavior without data rollback.

## Decision Contract

1. Existing wide-area and current-event likely-self-power suppression remain hard blockers.
2. Before 60 minutes, a row is not gate-eligible.
3. At/after 60 minutes, `LIKELY_INDIVIDUAL_FAULT` is eligible.
4. At/after 60 minutes, a no-history non-wide-area/non-self-power row is eligible through `no_history_absolute_duration`, so new subscribers cannot remain permanently uncertain.
5. Other `UNCERTAIN` rows stay observable and ineligible in enforce. Integer scores remain heuristics, not calibrated probabilities.

## Phases

| Phase | Name | Status | Dependency |
|---|---|---|---|
| 1 | [Decision Contract and Scoring Eligibility](./phase-01-start.md) | Pending | None |
| 2 | [Precision Alert Gate Integration](./phase-02-precision-alert-gate.md) | Pending | Phase 1 |
| 3 | [Shadow Validation and Rollout](./phase-03-shadow-validation-and-rollout.md) | Pending | Phase 2 |

## Dependencies

- Existing state, recovery, scoring, snapshot, config, and delivery-log contracts.
- NVKT reviewers and operational monitoring are post-enforcement tuning inputs, not rollout blockers.

## Validation Commands

```bash
pytest -q tests/test_subscriber_off_scoring.py tests/test_notification_service.py
pytest -q tests/test_alert_engine.py tests/test_notification_bridge.py
pytest -q
```

## Rollback

Set `INDIVIDUAL_OFF_ALERT_GATE_MODE=off` and restart/reload through the existing procedure. `off` ignores eligibility for dispatch; no database or sent-state rollback. The shipped default remains `enforce` unless explicitly overridden.

## Unresolved Questions

- What precision target and NVKT sample should drive later threshold tuning?

<!-- slug: off-pattern-precision-first-alert-gate -->
