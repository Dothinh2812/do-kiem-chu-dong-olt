---
title: "Customer-Reported OneBSS Ticket Precheck"
description: "Suppress customer Zalo outage messages when the same MA_TB has a conclusively open customer-reported OneBSS trouble ticket."
status: complete
priority: P1
effort: "4d"
issue: null
branch: checkpoint/off-pattern-20260622
tags: [alerts, onebss, zalo, idempotency, fail-closed]
blockedBy: []
blocks: []
created: 2026-09-12
---

# Customer-Reported OneBSS Ticket Precheck

## Overview

Add a customer-only gate immediately before Telecom Zalo dispatch. Cheap local eligibility/idempotency checks and atomic request claims run first; one deadline-bounded OneBSS batch then returns a closed decision DTO for every residual canonical `MA_TB`. Only exact `CLEAR` may send. A conclusive open customer-reported ticket is terminally suppressed; failures, stale facts, malformed contracts, and ambiguous evidence fail closed for this cycle and remain retryable.

## Fixed Decisions

- Core owns authenticated evidence normalization; `customer_notification_service.process_customer_outage_alerts()` owns send/suppress, fact-age, rate, cutoff-retry, and persistence policy. No raw incident facts cross this seam.
- Public classifications are exactly `CUSTOMER_OPEN_TICKET | CLEAR | INDETERMINATE`; only exact `CLEAR` with `failure_kind=NONE` and a fresh `checked_at` reaches send. There is no catch-all send branch.
- Origin is decided from the incident's reviewed `kenh_tn` allowlists. `Qua tổng đài báo hỏng 119` is customer-reported. `Qua OB GHTT`, `Xuất theo file`, and `Qua công cụ xuất phiếu chủ động` are proactive and must never suppress a notification. Any other open-ticket channel is `INDETERMINATE`; processing history is not an origin signal.
- The overall Core deadline starts before cache/token validation or session creation. Authentication, OTP/navigation/capture, every request, queue scheduling, and bounded shutdown consume that budget.
- Daily/weekly `SENT`-only policy remains, but allowance is re-evaluated/reserved immediately before every clear send and includes successful sends in the current batch.
- `SENT` and `SKIPPED_CUSTOMER_TICKET` are immutable terminal states. `PRECHECK_IN_PROGRESS` is an expiring atomic lease in the existing table; retryable states may be reclaimed without holding SQLite across network I/O.
- Quiet hours and the original customer first-attempt cutoff remain. Prior `PRECHECK_FAILED`, `INDETERMINATE`, or `STALE` incidents may retry on later eligible cycles, including across midnight.
- `BYPASSED` is an explicit application state, never synthesized as `CLEAR`. Bypass is strict-boolean, operator-authorized, and warned at startup and every cycle; no new audit infrastructure, root-owned config, or expiry is required.
- The precision gate and wide-area/group/personal/weak-signal/recovery behavior stay unchanged. This remains a separate plan; the landed precision output is a coordination constraint, not a dependency.
- Enforcement remains blocked until reviewed live fixtures define terminal/open vocabulary and the explicit-empty schema. The operator-confirmed channel mapping above is fixed; future unknown channels remain indeterminate until separately approved.

## Phases

| Phase | Name | Status | Dependency |
|---|---|---|---|
| 1 | [Harden the OneBSS Batch Fact Contract](./phase-01-start.md) | Complete | None |
| 2 | [Integrate the Two-Pass Customer Alert Gate](./phase-02-customer-alert-integration.md) | Complete | Phase 1 |
| 3 | [Verify, Gate, and Roll Out Enforcement](./phase-03-verification-and-rollout.md) | Complete | Phase 2 |

## Acceptance Criteria

- [x] The typed total-map contract, contradiction-first evidence rules, explicit-empty handling, exact channel allowlists, and private-fact boundary are proven with fixture-reviewed tests.
- [x] One authentication serves a deduplicated batch whose auth, requests, bounded in-flight scheduler, cancellation, and shutdown obey the overall deadline for a hundreds-key backlog.
- [x] Atomic lease/monotonic transition races, same-batch anti-spam, stale decisions, and cross-midnight retries are proven without a schema migration.
- [x] Aggregate classification/failure/timing/retry/stale/deadline/bypass metrics contain no identifiers or raw values.
- [x] Core is deployed first; `/usr/bin/python3` proves the exact new method signature/types before the application deploy and four live fixtures gate enforcement.

## Red Team Review

The three review passes produced 23 raw findings, deduplicated to the following 15 adjudicated items: 13 accepted, 1 accepted with modification, and 1 rejected.

| # | Disposition | Consolidated correction |
|---|---|---|
| 1 | Accepted | Preserve daily/weekly allowance across both passes and same-batch sends. |
| 2 | Accepted | Start the overall deadline before auth; cap in-flight work, cancel queued work, and bound shutdown. |
| 3 | Accepted | Use a closed total decision DTO and exact-`CLEAR` send branch; contract defects fail closed. |
| 4 | Accepted | Give contradictory evidence indeterminate precedence. |
| 5 | Superseded | The later operator decision makes reviewed `kenh_tn` values authoritative; processing-step chronology and `dvg` are not used to infer origin. |
| 6 | Accepted | Treat only a schema-valid explicit empty incident collection as no-ticket clear. |
| 7 | Accepted | Add `checked_at`, maximum fact age, stale retry, and gateway-timeout load coverage. |
| 8 | Accepted | Preserve precheck retries across the customer day cutoff. |
| 9 | Accepted | Add atomic leases, monotonic transitions, and current `batch_id` attribution in the existing table. |
| 10 | Accepted | Add low-cardinality classification, failure, timing, retry, stale, deadline, and bypass metrics. |
| 11 | Accepted | Verify the exact deployed Core capability/signature under `/usr/bin/python3`, Core first. |
| 12 | Accepted | Harden the shared session cache permissions, atomicity, no-follow checks, and inter-process lock. |
| 13 | Accepted | Use typed safe Core errors and an allowlisted public DTO; discard raw upstream bodies/messages. |
| 14 | Accepted—modified | Represent rollback as `BYPASSED` with strict parsing and loud warnings, but require no new append-only store, root ownership, or expiry. |
| 15 | Rejected (scope) | Existing CTS `login.py` OTP printing is real but pre-existing and not created by this integration; track it separately and do not claim this plan remediates it. |

## Whole-Plan Consistency Sweep

Reread `plan.md` and all three phase files after adjudication and the later operator channel decision. Applied the original 14 decision deltas, then removed processing-history chronology from origin classification and fixed direct channel allowlists. Unresolved contradictions: **0**.

## Validation Log

### Session 1 — 2026-09-12

- **Trigger:** Post-red-team validation of terminal suppression semantics.
- **Question:** If the customer ticket closes while the same OFF incident (`first_off_time` unchanged) continues, should notification remain suppressed?
- **Answer:** Suppress for the entire current OFF incident.
- **Confirmed decision:** `SKIPPED_CUSTOMER_TICKET` remains immutable for the existing `request_id`; only a new OFF incident gets a new OneBSS check.
- **Impact:** Phase 2 terminal-state contract and Phase 3 closed-ticket scenario remain unchanged; no phase rewrite required.

### Session 2 — 2026-09-12

- **Trigger:** Live OneBSS probe exposed `Qua OB GHTT` and `Xuất theo file` origin values.
- **Operator decision:** Both values are proactive, not customer-reported, and must not suppress a notification.
- **Confirmed mapping:** Customer-reported is `Qua tổng đài báo hỏng 119`; proactive is `Qua OB GHTT`, `Xuất theo file`, or `Qua công cụ xuất phiếu chủ động`.
- **Impact:** Direct `kenh_tn` allowlists replace processing-history chronology and `dvg` origin inference. Unknown future channels remain retryable `INDETERMINATE`.

### Verification Results

- **Tier:** Standard; red-team evidence already covered Fact Checker and Contract Verifier roles.
- **Unverified markers checked:** 0.
- **Whole-plan consistency:** 4 files reread; 2 validation decisions checked; 0 stale chronology references; 0 unresolved contradictions.

## Evidence

- [Current alert flow](agent://CurrentAlertFlow) · [OneBSS contract](agent://OneBSSContract) · [Failure review](agent://PlanFailureReview) · [Assumption review](agent://PlanAssumptionReview) · [Security/retry review](agent://PlanSecurityRetry)

<!-- slug: customer-ticket-precheck -->
