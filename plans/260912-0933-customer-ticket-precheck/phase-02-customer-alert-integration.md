---
phase: 2
title: "Integrate the Two-Pass Customer Alert Gate"
status: pending
priority: P1
effort: "1.5d"
dependencies: [1]
---

# Phase 2: Integrate the Two-Pass Customer Alert Gate

## Context Links

- [Plan overview](./plan.md)
- Previous: [Phase 1 — Harden the OneBSS Batch Fact Contract](./phase-01-start.md)
- Next: [Phase 3 — Verify, Gate, and Roll Out Enforcement](./phase-03-verification-and-rollout.md)
- [Current alert flow evidence](agent://CurrentAlertFlow)

## Overview

Refactor `customer_notification_service.process_customer_outage_alerts()` into a claimed two-pass flow. The first pass performs cheap structural, cutoff, idempotency, and preliminary rate checks and atomically claims each request. One adapter call checks only residual canonical `MA_TB` values. The second pass sends only exact, fresh `CLEAR` decisions after an immediate rate recheck/reservation. The customer service—not the bridge—owns the customer first-attempt cutoff so retryable precheck dispositions survive midnight.

## Requirements

- Functional: Create `onebss_precheck.py` with `run_customer_ticket_precheck(ma_tb_values, config) -> IncidentPrecheckBatchResult` as the only application wrapper around `OneBSSCore.lookup_open_incident_facts_batch()`. Keep the project package/standalone import fallback at the service boundary.
- Contract: Validate that `result.decisions` exactly covers the requested canonical keys and every record is the expected frozen DTO with known enums and a valid UTC `checked_at`. A missing key is filled as `INDETERMINATE + CONTRACT`; any extra/noncanonical/wrong key invalidates all requested decisions as contract failures; a malformed per-key record replaces that key with the same safe contract decision.
- Contract: Never stringify a Core exception or copy a raw OneBSS body/message. Typed errors map to stable failure/reason enums; an unexpected exception becomes `INDETERMINATE + CONTRACT + CONTRACT_VIOLATION` without its text.
- Functional: Add exact non-secret config keys `enable_customer_ticket_precheck`, `customer_ticket_precheck_request_timeout_seconds`, `customer_ticket_precheck_batch_timeout_seconds`, `customer_ticket_precheck_max_workers`, `customer_ticket_precheck_max_fact_age_seconds`, and `customer_ticket_precheck_claim_lease_seconds`. Validate positive bounds and require the lease to cover batch timeout + request-timeout shutdown margin + maximum fact age + gateway timeout.
- Rollback: `enable_customer_ticket_precheck` defaults to true and is parsed by `_parse_strict_bool()`—actual booleans or explicit allowlisted string tokens only. Invalid input is warned and forced to enabled. False yields application state `BYPASSED`; it never creates an `IncidentPrecheckDecision` or synthetic `CLEAR`.
- Ordering: Quiet hours always apply. First attempts retain the exact existing customer start/end cutoff. Prior `PRECHECK_FAILED`, `INDETERMINATE`, or `STALE` rows that are still in the active candidate snapshot bypass only that first-attempt cutoff and may retry in any later eligible cycle, including the next day.
- Ordering: Before OneBSS, run required-field/time parsing, current-candidate, terminal idempotency, first-attempt cutoff, existing `SENT` compatibility, and a preliminary daily/weekly check. Before each exact-clear send, re-read and reserve daily/weekly allowance immediately; count successful sends from this batch and keep the existing limits based on terminal `SENT` rows.
- Freshness: Add timezone-aware `checked_at` validation and enforce `customer_ticket_precheck_max_fact_age_seconds` immediately before each sequential send. A fact that ages out while earlier gateway calls consume their worst-case timeout is finalized `STALE`, skipped, and retried later; stale `CLEAR` never sends.
- Persistence: Replace the unconditional `log_customer_outage_alert()` upsert with atomic claim/refresh/finalize methods over the existing `customer_outage_alert_log` table. No schema migration and no SQLite transaction may span OneBSS or Telecom Zalo I/O.
- Persistence: `SENT` and `SKIPPED_CUSTOMER_TICKET` are immutable. `PRECHECK_IN_PROGRESS` is an expiring lease using `sent_time`; active leases cannot be stolen, expired leases and retryable states can be reclaimed, and every successful conflict claim/finalization writes the current `batch_id`.
- Persistence: Retain the existing Telecom Zalo `requestId` as final duplicate defense for a crash after gateway acceptance but before `SENT` finalization.
- Behavior: `CUSTOMER_OPEN_TICKET/NONE` finalizes `SKIPPED_CUSTOMER_TICKET`. `INDETERMINATE` with `AUTH|API|REQUEST_TIMEOUT|BATCH_TIMEOUT|PARTIAL|CONTRACT` finalizes `PRECHECK_FAILED`; `AMBIGUOUS` finalizes `INDETERMINATE`. Only `CLEAR/NONE` with an allowed reason and fresh time reaches rate reservation/send. Every other combination fails closed.
- Metrics: Add only aggregate low-cardinality values: classification counts, failure-kind counts, checked/retried/stale/deadline counts, request count/duration total/max, batch duration, and `precheck_state: ENFORCED | BYPASSED`. Emit no identifiers, raw values, or per-key decisions.
- Scope: Do not alter scoring, snapshots, precision logic, or wide-area/group/personal/weak-signal/recovery behavior. `notification_bridge._dispatch()` may change only its customer cutoff handoff, customer result fields, and customer aggregate warning.

## Architecture

### Application Boundary

`onebss_precheck.run_customer_ticket_precheck()` builds `OneBSSCore.from_env()`, invokes the exact Core method, validates the closed DTO/coverage, and returns an immutable safe result. `process_customer_outage_alerts()` runs this synchronous adapter via `asyncio.to_thread()`; the Core's own deadline bounds its worker even though cancelling the coroutine cannot stop a Python thread.

The adapter computes no send policy and never returns private evidence. Application classification/failure counters are derived from validated decisions; request/batch timing comes only from `IncidentPrecheckBatchMetrics`.

### Monotonic Claim Protocol

Add these exact repository methods in `AlertRepository`:

```text
get_customer_outage_alert_disposition(request_id) -> Optional[Dict]
claim_customer_outage_alert_precheck(subscriber_key, ma_tb, first_off_time,
    claimed_at, batch_id, request_id, lease_seconds) -> CustomerOutageClaim
refresh_customer_outage_alert_precheck_claim(request_id, batch_id,
    expected_claimed_at, refreshed_at) -> Optional[datetime]
finalize_customer_outage_alert_precheck(request_id, batch_id,
    expected_claimed_at, status, finalized_at, error_reason) -> bool
```

`CustomerOutageClaim` returns `outcome: CLAIMED | TERMINAL | LEASE_HELD`, `prior_status`, and the exact `claimed_at` lease version. Claim is one `INSERT ... ON CONFLICT DO UPDATE ... WHERE` transaction: insert `PRECHECK_IN_PROGRESS`, or replace only `PRECHECK_FAILED`, `INDETERMINATE`, `STALE`, `FAILED`, and expired `PRECHECK_IN_PROGRESS`. On every conflict update set `status`, `sent_time`, `batch_id`, and the allowlisted reason. Terminal rows and active leases make the conditional write affect zero rows.

The finalizer is a compare-and-set on `status='PRECHECK_IN_PROGRESS' AND batch_id=? AND sent_time=?`; therefore a stale worker cannot overwrite a refreshed/reclaimed claim. Refresh before a clear send and use its returned timestamp for finalization. No terminal state is ever in an update predicate.

| Current state | Allowed next state |
|---|---|
| Missing | `PRECHECK_IN_PROGRESS` |
| `PRECHECK_FAILED`, `INDETERMINATE`, `STALE`, `FAILED` | `PRECHECK_IN_PROGRESS` |
| Expired `PRECHECK_IN_PROGRESS` | Refreshed `PRECHECK_IN_PROGRESS` with current `batch_id`/`sent_time` |
| Owned `PRECHECK_IN_PROGRESS` | `SKIPPED_CUSTOMER_TICKET`, `PRECHECK_FAILED`, `INDETERMINATE`, `STALE`, `FAILED`, or `SENT` |
| Active foreign `PRECHECK_IN_PROGRESS` | No transition |
| `SKIPPED_CUSTOMER_TICKET`, `SENT` | No transition |

<!-- Updated: Validation Session 1 — `SKIPPED_CUSTOMER_TICKET` remains terminal for the full OFF incident; a closed OneBSS ticket does not reopen the same request_id. -->

`log_customer_outage_alert()` is removed after all app/tests use claim/finalize. `count_recent_customer_outage_alerts()` remains `status='SENT'` only. Claim/finalize reasons are closed codes, not exception/evidence text.

### Cutoff and Retry Ownership

`notification_bridge._dispatch()` passes all precision-gated active customer rows to `process_customer_outage_alerts()` and no longer calls `filter_current_off_alerts_by_cutoff()` for the customer branch. The service builds `request_id`, loads disposition, and applies:

- no prior precheck disposition: existing start/end first-off cutoff;
- prior `PRECHECK_FAILED`, `INDETERMINATE`, or `STALE`: structural/current-active/quiet-hours gates but not the first-attempt cutoff;
- terminal row: immediate no-call skip;
- active lease: `precheck_in_progress` skip;
- expired lease: atomic reclaim.

The bridge's other uses of `filter_current_off_alerts_by_cutoff()` remain untouched. Customer cutoff skip counts now come from the service, preserving result meanings without permanent retry loss.

### Two-Pass Gate Pseudocode

```text
config = load_and_strictly_normalize_config()
if quiet_hours: return aggregate quiet-hours skips

prepared = []
for row in candidate_rows:
    validate required fields and first_off_time; build request_id once
    prior = repo.get_customer_outage_alert_disposition(request_id)
    if prior.status == SKIPPED_CUSTOMER_TICKET: skip(existing_customer_ticket); continue
    if prior.status == SENT or existing_sent_compatibility_check(row): skip(already_sent_for_incident); continue
    retry_cutoff_override = prior.status in {PRECHECK_FAILED, INDETERMINATE, STALE}
    if structural_or_first_attempt_cutoff_fails(row, override=retry_cutoff_override): skip(reason); continue
    if preliminary_sent_only_rate_check_fails(row): skip(rate_reason); continue
    claim = repo.claim_customer_outage_alert_precheck(...)
    if claim.outcome != CLAIMED: skip(terminal_or_lease_reason); continue
    mark retried if claim.prior_status in {PRECHECK_FAILED, INDETERMINATE, STALE}
    prepared.append(row, request_id, claim.claimed_at, claim.prior_status)

sort retryable rows by oldest prior sent_time, then new rows by stable input order
if precheck_enabled:
    batch = await to_thread(run_customer_ticket_precheck, unique_canonical_ma_tb(prepared), config)
    precheck_state = ENFORCED
else:
    batch = None
    precheck_state = BYPASSED
    emit aggregate bypass warning

for claimed row in prepared:
    if precheck_state == ENFORCED:
        decision = validated total map[row.canonical_ma_tb]
        if decision.classification == CUSTOMER_OPEN_TICKET and decision.failure_kind == NONE:
            finalize owned claim as SKIPPED_CUSTOMER_TICKET; skip(existing_customer_ticket); continue
        if decision.classification == INDETERMINATE:
            finalize owned claim as PRECHECK_FAILED for operational/contract kinds,
                otherwise INDETERMINATE; skip(safe reason); continue
        if not (decision.classification == CLEAR and decision.failure_kind == NONE
                and decision.reason in sendable_clear_reasons):
            finalize owned claim as PRECHECK_FAILED/CONTRACT; skip; continue
        if fact_age(decision.checked_at) > configured_maximum:
            finalize owned claim as STALE; skip(onebss_stale); continue
    else:
        record BYPASSED only; do not fabricate a decision

    refreshed_claim = refresh owned lease immediately before send; if lost: skip; continue
    allowance = requery_and_reserve_daily_weekly_allowance(
        persisted_SENT_counts, same_batch_successes, same_batch_active_reservation)
    if not allowance: finalize owned claim as FAILED with allowlisted local rate reason; skip; continue
    format and call Telecom Zalo with the unchanged requestId
    on success: count same-batch success and finalize SENT
    on gateway failure: release reservation and finalize FAILED
```

The rate ledger computes `max(fresh_persisted_SENT_count, cycle_baseline_SENT_count + same_batch_success_count)` for daily and weekly windows, then takes a local provisional reservation before the awaited gateway call. Successful sends retain their count even if SQLite finalization fails; failures release it. Customer sends remain sequential, so this prevents two same-subscriber rows in one batch from both consuming a one-message allowance.

### Outcome Matrix

| Validated state | Send? | Final status | Later precheck retry |
|---|---:|---|---:|
| Conclusive open customer-reported ticket | No | `SKIPPED_CUSTOMER_TICKET` | No |
| Existing `SKIPPED_CUSTOMER_TICKET`/`SENT` | No, no OneBSS | Unchanged | No |
| `CLEAR/NONE` for open proactive only, all terminal, or explicit valid no-ticket | Yes, after freshness/rate/claim refresh | `SENT` or gateway `FAILED` | Gateway policy only |
| `AUTH`, `API`, `REQUEST_TIMEOUT`, `BATCH_TIMEOUT`, `PARTIAL`, `CONTRACT` | No | `PRECHECK_FAILED` | Yes, including next day |
| `AMBIGUOUS` evidence | No | `INDETERMINATE` | Yes, including next day |
| Clear fact exceeds maximum age before send | No | `STALE` | Yes, including next day |
| Unknown/missing classification, wrong enum pairing, malformed/partial map | No | `PRECHECK_FAILED` with safe contract reason | Yes |
| Active `PRECHECK_IN_PROGRESS` lease | No | Unchanged | After lease expiry |
| Precheck explicitly disabled and claim is owned | Legacy rate/send path, no decision | `SENT` or gateway `FAILED` | Existing behavior |

## Aggregate Result Contract

Extend `results["customer_outage"]` without changing existing meanings:

```text
classification_counts: {CUSTOMER_OPEN_TICKET, CLEAR, INDETERMINATE}
failure_kind_counts: {NONE, AUTH, API, REQUEST_TIMEOUT, BATCH_TIMEOUT,
                      PARTIAL, AMBIGUOUS, CONTRACT}
onebss_checked, onebss_retried, onebss_stale, onebss_deadline_expired
onebss_request_count, onebss_request_duration_ms_total,
onebss_request_duration_ms_max, onebss_batch_duration_ms
precheck_state: ENFORCED | BYPASSED
```

All enum buckets exist with zero defaults for stable dashboards. `onebss_checked` is unique canonical keys submitted; `onebss_retried` is claimed rows whose prior status was `PRECHECK_FAILED`, `INDETERMINATE`, or `STALE`; deadline count includes request/batch-deadline decisions. The cycle summary prints these aggregates only.

## Related Code Files

- Create: `/home/vtst/do_chu_dong_api/onebss_precheck.py` — Core invocation, strict total-map validation, safe failure mapping.
- Modify: `/home/vtst/do_chu_dong_api/customer_notification_service.py` — cutoff ownership, claim-aware two-pass gate, fact age, rate reservation, exact-clear dispatch, dispositions, counters.
- Modify: `/home/vtst/do_chu_dong_api/alert_db.py` — disposition lookup plus monotonic claim/refresh/finalize compare-and-set methods; remove unconditional upsert.
- Modify: `/home/vtst/do_chu_dong_api/notification_service.py` — strict boolean and bounded non-secret precheck config normalization.
- Modify: `/home/vtst/do_chu_dong_api/notification_bridge.py` — customer-only cutoff handoff, aggregate propagation, every-cycle bypass warning; no other branch change.
- Create: `/home/vtst/do_chu_dong_api/tests/test_onebss_precheck.py` — adapter coverage/type/enum/time/error and import-mode tests.
- Modify: `/home/vtst/do_chu_dong_api/tests/test_customer_notification_service.py` — two-pass, claim, rate, stale, cross-midnight, bypass, and outcome tests.
- Modify: `/home/vtst/do_chu_dong_api/tests/test_notification_bridge.py` — customer cutoff ownership/metrics/warning plus unrelated-branch invariants.
- Modify: `/home/vtst/do_chu_dong_api/README.md` — config contract and authorized bypass/re-enable procedure.

## Implementation Steps

1. Add strict config normalization and stable zero-valued aggregate buckets. Invalid booleans force enforcement and warn; bypass warns at config load/startup and in every `_dispatch()` cycle.
2. Implement the adapter and hostile contract validation for missing/extra/wrong/malformed decisions, enum pairings, and `checked_at`; never expose exception text.
3. Replace `log_customer_outage_alert()` with disposition lookup and conditional claim/refresh/finalize SQL. Update every caller and test fixture; keep the existing table and `SENT` count queries.
4. Move only the customer first-off cutoff from `notification_bridge._dispatch()` into the service, preserving quiet hours and first-attempt behavior while admitting the three named retry statuses across days.
5. Split local eligibility into cheap structural/idempotency/preliminary-rate checks and an immediate pre-send rate recheck/reservation. Preserve existing reason names where semantics are unchanged.
6. Build stable request IDs, atomically claim residual rows, order prior retries before new rows, and deduplicate canonical `MA_TB` only after claims.
7. Invoke Core once only when enforced. On bypass, create no decision/fact map and enter the explicitly labeled legacy branch.
8. Apply the outcome matrix with exhaustive enum matching. Exact fresh `CLEAR/NONE` is the only enforced send path; no final `else` sends.
9. Refresh claim, re-evaluate/reserve daily/weekly allowance, format, and send sequentially. Count each successful current-batch send before the next row; finalize through compare-and-set.
10. Propagate low-cardinality metrics through the customer bridge result/summary and remove all identifier/raw-value logging from new paths.
11. Add same-subscriber two-incident max-per-day=1 coverage, multi-connection claim races, terminal stale-writer attempts, batch attribution refresh, fact-aging under gateway timeout, and cross-midnight retry-to-clear/customer-open coverage.
12. Document the authorized bypass procedure without adding a new audit store, root-owned configuration requirement, or expiry mechanism.

## Todo List

- [ ] Add the strict adapter/config boundary and aggregate schema.
- [ ] Implement cutoff-aware two-pass eligibility and exact-clear/freshness/rate gates.
- [ ] Replace unconditional persistence with lease-based monotonic compare-and-set.
- [ ] Preserve retryable incidents across midnight and terminal immutability under races.
- [ ] Add focused adapter/service/repository/bridge coverage and operator docs.

## Success Criteria

- [ ] Only exact `CLEAR/NONE` with a sendable reason, fresh `checked_at`, owned claim, and reserved allowance sends; malformed/future values cannot reach the gateway.
- [ ] Two same-subscriber clear incidents with `customer_alert_max_per_day=1` produce one successful gateway call, including when the first `SENT` database finalization is delayed/fails.
- [ ] Terminal rows cannot downgrade; active claims cannot be stolen; expired/retryable claims refresh `batch_id`; stale workers fail compare-and-set in multi-connection races.
- [ ] Prior `PRECHECK_FAILED`, `INDETERMINATE`, and `STALE` rows retry across midnight while quiet hours, first-attempt cutoff, and unrelated cutoff consumers retain current behavior.
- [ ] Worst-case sequential gateway timeouts age later facts into retryable `STALE` without sending them.
- [ ] Bypass produces `precheck_state=BYPASSED`, zero checked decisions, a startup/every-cycle warning, and no fabricated `CLEAR`.
- [ ] Metrics/reasons contain only bounded enum/count/duration fields; no OneBSS identifiers, evidence, body, or exception text appears.

## Risk Assessment

| Risk | Observable signal | Pre-decided response |
|---|---|---|
| Two-pass flow weakens anti-spam | Two same-subscriber successes exceed day/week limit | Stop rollout; restore immediate pre-send requery/reservation and same-batch success accounting. |
| Retry is lost at midnight | Named retry status is counted `before_cutoff` next day | Keep customer cutoff in service and pass active precision-gated rows unchanged from bridge. |
| Claim lease expires during work | Refresh fails or overlapping worker finalizes | Skip/send nothing without ownership; increase only within validated bound formula, never weaken CAS. |
| Stale fact sends after slow gateway calls | Send occurs beyond maximum age | Final freshness check must precede every send; finalize `STALE` and prioritize it next cycle. |
| Contract drift fails open | Unknown key/enum/type reaches formatting | Invalidate/fill as `CONTRACT`; keep the only send predicate explicit and exhaustive. |
| Bypass becomes silent/sticky | Cycle reports `BYPASSED` without warning or false parses as disabled | Force invalid values enabled; warn at load and every cycle; follow authorized re-enable procedure. |
| Bridge scope leaks | Any non-customer branch identity/count changes | Revert non-customer edits; bridge changes are limited to the customer handoff/result block. |

## Security and Performance

The adapter and persistence store only stable enums/reasons and aggregate timings. Never include OneBSS ticket IDs, `dvg`, `kenh_tn`, status/completion values, raw responses, headers, credentials, OTP content/path, or exception messages in notification config, SQLite reasons, logs, or metrics. Existing established customer/gateway delivery fields are not widened.

The preliminary local pass avoids needless Core work. Core deduplicates keys and bounds its own threads; the application adds no payload cache. Sequential sends are intentionally bounded by freshness: once a decision is stale it is skipped, not refreshed ad hoc or sent. Validation for this phase is focused and must precede Phase 3:

```bash
cd /home/vtst/do_chu_dong_api
/usr/bin/python3 -m pytest -q tests/test_onebss_precheck.py tests/test_customer_notification_service.py tests/test_notification_bridge.py -k "onebss or precheck or customer_outage or cutoff"
```

## Authorized Bypass Procedure

An authorized on-call operator records the reason in the existing change/incident process, sets the resolved `enable_customer_ticket_precheck` to the exact boolean `false`, confirms startup and cycle summaries show `BYPASSED`, and verifies prior terminal skips remain untouched. Re-enable with exact boolean `true` after Core health/fixtures pass and confirm the next cycle reports `ENFORCED`. Do not auto-bypass on Core failure. This plan deliberately adds no new append-only system, root-owned config source, or time expiry.

## Next Steps

Phase 3 proves the cross-repository contract, load/race/security behavior, exact deployed Core capability, live mappings, Core-first rollout, explicit bypass, and unchanged unrelated branches.
