---
title: Plan OneBSS customer ticket precheck
date: 2026-09-12
summary: Planned a fail-closed OneBSS gate before customer Zalo outage sends.
---

# Plan OneBSS customer ticket precheck

## What happened

- Traced the existing precision-gated customer notification pipeline and the OneBSS incident-history API.
- Created and validated `plans/260912-0933-customer-ticket-precheck/` with three phases.
- Red-team review hardened rate limits, deadlines, retry semantics, contract validation, persistence races, session-cache safety, and aggregate observability.

## Decisions

- Suppress only conclusively open customer-reported tickets; proactive or closed tickets remain sendable.
- Fail closed on OneBSS errors, timeout, partial or ambiguous evidence; retry later.
- `SKIPPED_CUSTOMER_TICKET` remains terminal for the full current OFF incident.
- OneBSS Core returns an allowlisted batch decision DTO; the notification app owns send/suppress policy.

## Verification

- `ak plan validate` reports the plan directory valid.
- Whole-plan consistency sweep found zero unresolved contradictions.
- Planning-time live OneBSS probe returned `auth_error`; implementation enforcement remains gated on four operator-reviewed anonymized fixtures.

## Next steps

- Implement Phase 1 in OneBSS Core, then Phase 2 in the notification app, then run Phase 3 controlled rollout.

> Historical work record — not durable authority. Prefer docs/specs/ADRs for current decisions.
