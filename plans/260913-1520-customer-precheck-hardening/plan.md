---
title: "Customer Precheck Production Hardening & Anti-Spam Defense"
description: "Harden Customer OneBSS Ticket Precheck against case-mismatch contract violations, per-customer rate limit bypasses, lease expiry races, and cache evictions."
status: completed
priority: P1
effort: "1d"
tags: [onebss, alerts, anti-spam, red-team, hardening]
created: 2026-09-13
---

# Customer Precheck Production Hardening & Anti-Spam Defense

## Overview

Fix critical production edge cases discovered during adversarial code review of the Customer OneBSS Ticket Precheck feature:
1. Ensure case-insensitive key consistency (`canonicalize_ma_tb` lowercase normalization) so mixed-case `ma_tb` values do not trigger batch-wide `CONTRACT_VIOLATION`.
2. Enforce customer-level anti-spam rate limiting keyed by `ma_tb` instead of physical `subscriber_key`.
3. Fix claim lease extension using fresh timestamps and verify compare-and-set (CAS) finalization to prevent duplicate sends on long batches.
4. Protect valid session cache from deletion on network timeouts or deadline exhaustion.
5. Prioritize broadband Internet service (`dichvuvt_id == 9`) during multi-service identity lookup.
6. Cap retry age to 24h to avoid sending notifications for multi-day-old outages.
7. Wrap synchronous `requests.post` in `asyncio.to_thread` for non-blocking event-loop operation.

## Goals

| # | Goal | Priority |
|---|------|----------|
| 1 | Prevent contract violations on uppercase/mixed-case `ma_tb` | P0 |
| 2 | Enforce rate limiting per customer account (`ma_tb`) | P0 |
| 3 | Eliminate lease expiry races and check CAS finalization | P0 |
| 4 | Preserve session cache on network timeouts and prioritize Fiber broadband service | P1 |
| 5 | Cap retry age to 24 hours and make gateway dispatch non-blocking | P1 |
| 6 | Execute complete Red Team adversarial verification test matrix | P0 |

## Phases

| # | Phase | Status |
|---|-------|--------|
| 1 | [P0 Contract Normalization, Anti-Spam & Lease Refresh](./phase-01-start.md) | Completed |
| 2 | [P1 Reliability, Identity & Async Dispatch Hardening](./phase-02-reliability-and-identity-hardening.md) | Completed |
| 3 | [P0 Red Team Adversarial Verification & Validation](./phase-03-red-team-verification.md) | Completed |

## Red Team Review

| # | Concern | Attack / Failure Scenario | Adjudication & Defense |
|---|---------|---------------------------|-------------------------|
| 1 | Mixed-case `ma_tb` | Billing returns `HNIF_123456`, `onebss_core` normalizes to `hnif_123456`, precheck adapter sees extra key and rejects entire batch as `CONTRACT_VIOLATION`. | **Accepted (P0)**: Make `canonicalize_ma_tb` in `onebss_precheck.py` lowercase by default (`.strip().lower()`). |
| 2 | Rate Limit Bypass | Customer has multiple ONT records with same `ma_tb` but different `subscriber_key`. Both evaluate to 0 prior sends and send duplicate Zalo alerts. | **Accepted (P0)**: Key rate ledgers (`same_batch_success_counts`, `same_batch_reserved_counts`, `cycle_baseline_sent_24h`) and DB queries by `can_ma`. |
| 3 | Stolen Lease Race | Sequential send loop takes >120s; lease was refreshed with stale $T_0$, next batch steals claim, worker 1 ignores CAS failure and both send alert. | **Accepted (P0)**: Use `datetime.now()` for `refreshed_at` and `finalized_at`; verify boolean return from CAS finalization. |
| 4 | Cache Eviction Storm | Network timeout or deadline expiration causes `check_token_alive` to return `False`, unconditionally deleting valid `session.json` and forcing browser OTP login. | **Accepted (P1)**: Only delete session cache on explicit HTTP 401/403 or invalid token payload, never on timeout. |
| 5 | Multi-Service Blindspot | Customer has Fiber and MyTV under same `ma_tb`. Lookup takes `matching_rows[0]` (MyTV), sees no tickets, and alerts even though Fiber has open 119 ticket. | **Accepted (P1)**: Filter/sort `matching_rows` with `dichvuvt_id == 9` priority in `onebss_core`. |
| 6 | Days-Old Outage Alerts | Outage originated 3 days ago, repeated `INDETERMINATE` retries bypass start cutoff, service sends alert for ancient outage once OneBSS recovers. | **Accepted (P1)**: Enforce `max_retry_age_hours = 24.0`; skip/expire candidate if `first_off_time` is older than 24 hours. |
| 7 | Event Loop Blocking | Synchronous `requests.post` inside `send_customer_outage_message` blocks the single-threaded asyncio loop during Zalo dispatch. | **Accepted (P1)**: Execute network post in worker thread via `asyncio.to_thread`. |

## Success Criteria

- [x] Mixed-case `ma_tb` values (`HNIF_...`, `STY_...`) pass precheck without contract violation.
- [x] Multiple candidate rows for the same `ma_tb` send at most 1 alert within 24h.
- [x] Claim lease extension advances timestamp in DB; stolen claim CAS failure is logged and handled.
- [x] Network timeouts preserve `session.json` on disk.
- [x] Outages older than 24 hours are not retried.
- [x] Full regression test suite passes in both repositories (`pytest -q`).
- [x] Live daemon reloaded and running with clean logs.

<!-- slug: customer-precheck-hardening -->