---
phase: 1
title: "Harden the OneBSS Batch Fact Contract"
status: completed
priority: P1
effort: "1.5d"
dependencies: []
---

# Phase 1: Harden the OneBSS Batch Fact Contract

## Context Links

- [Plan overview](./plan.md)
- [OneBSS contract evidence](agent://OneBSSContract)
- Next: [Phase 2 — Integrate the Two-Pass Customer Alert Gate](./phase-02-customer-alert-integration.md)

## Overview

Add one live-only, one-session Core operation that returns only allowlisted decisions and aggregate timings. Make the entire operation—including session-cache lock/load/liveness, login/OTP/navigation/capture, subscriber requests, scheduling, cancellation, and shutdown—deadline-aware. Harden the reusable session cache because the notification daemon introduces sustained cross-process use.

## Requirements

- Functional: Add the exact public method `OneBSSCore.lookup_open_incident_facts_batch(ma_tb_values, *, request_timeout_seconds, batch_timeout_seconds, max_workers) -> IncidentPrecheckBatchResult` in `/home/vtst/onebss_core/src/onebss_core/service.py`.
- Functional: Normalize/deduplicate non-empty `MA_TB` values once and return a decision for every requested canonical key. Authenticate once and fetch only subscriber identity plus the incident list carrying status, completion, and `kenh_tn`; never run processing-history, stock, employee, or unrelated enrichment.
- Contract: Public `IncidentPrecheckDecision` fields are exactly `classification`, `failure_kind`, `reason`, and timezone-aware UTC `checked_at`. Detailed ticket IDs, completion values, status/channel fields, reporter/customer details, history, headers, and raw payloads remain private locals/internal types.
- Contract: `IncidentClassification` is closed to `CUSTOMER_OPEN_TICKET | CLEAR | INDETERMINATE`. `IncidentFailureKind` is closed to `NONE | AUTH | API | REQUEST_TIMEOUT | BATCH_TIMEOUT | PARTIAL | AMBIGUOUS | CONTRACT`.
- Contract: `IncidentDecisionReason` is closed to `CUSTOMER_OPEN_CONFIRMED | NO_INCIDENTS_CONFIRMED | ALL_INCIDENTS_TERMINAL | OPEN_INCIDENTS_PROACTIVE_ONLY | AUTH_UNAVAILABLE | API_UNAVAILABLE | REQUEST_DEADLINE_EXCEEDED | BATCH_DEADLINE_EXCEEDED | PARTIAL_EVIDENCE | AMBIGUOUS_EVIDENCE | CONTRACT_VIOLATION`. Only the first four may accompany `failure_kind=NONE`.
- Contract: `IncidentPrecheckBatchMetrics` exposes only requested/completed/request counts, request-duration total/max, batch duration, and deadline-expired count. `IncidentPrecheckBatchResult` exposes only `decisions: Mapping[str, IncidentPrecheckDecision]` plus those metrics.
- Safety: Any missing/extra/noncanonical key, wrong DTO type, unknown enum, malformed record, or invalid/future `checked_at` is `INDETERMINATE + CONTRACT + CONTRACT_VIOLATION`; Phase 2 must independently validate this boundary. No unknown value can become `CLEAR`.
- Safety: Contradictory evidence is evaluated before terminal/open/origin classification. `CLEAR` for no incidents requires a fixture-reviewed, schema-valid envelope containing an explicit empty collection; absent/wrong-typed `data`, non-dict rows, or missing/unparseable incident IDs are contract failures.
- Safety: Origin is an exact, case-sensitive `kenh_tn` allowlist decision after whitespace normalization. `CUSTOMER_CHANNEL_VALUES = {"Qua tổng đài báo hỏng 119"}`. `PROACTIVE_CHANNEL_VALUES = {"Qua OB GHTT", "Xuất theo file", "Qua công cụ xuất phiếu chủ động"}`. An open incident in the proactive set contributes `CLEAR`, never suppression; an unknown open-ticket channel is `INDETERMINATE`. Do not use `dvg`, processing history, reporter identity, or response order to infer origin.
- Non-functional: Start a monotonic overall deadline before cache/token validation and `PlaywrightAuthenticator.create_session()`. Every blocking wait/request receives `min(configured_request_timeout, remaining_budget)`; no work starts when no budget remains.
- Non-functional: Keep at most `max_workers` subscriber futures in flight. Do not submit the whole input. On deadline, stop refilling, mark unscheduled/unfinished keys `BATCH_TIMEOUT`, cancel queued futures, and bound active-worker shutdown by the request timeout.
- Security: Replace raw stringified upstream failures in the new path with typed Core errors carrying only `failure_kind` and `reason`; discard upstream bodies/messages and suppress exception chaining before a DTO, log, or caller can observe them.
- Security: Enforce a real `0700` session-cache directory and `0600` lock/cache files, expected effective UID, regular-file type, no symlink following, same-directory atomic publish, and an inter-process lock across load/liveness/clear/login/save with a re-read after lock acquisition.
- Compatibility: Optional deadlines/timeouts on existing auth/request helpers preserve legacy defaults for callers that omit them. This operation writes no artifact and keeps Core free of Zalo, SQLite, and application disposition policy.

## Architecture

### Closed Public DTO

Define the frozen enums/dataclasses in `/home/vtst/onebss_core/src/onebss_core/models.py` and export them from `onebss_core.__init__`:

```text
IncidentPrecheckDecision(classification, failure_kind, reason, checked_at)
IncidentPrecheckBatchMetrics(requested_key_count, completed_key_count,
    request_count, request_duration_ms_total, request_duration_ms_max,
    batch_duration_ms, deadline_expired_count)
IncidentPrecheckBatchResult(decisions: Mapping[canonical_ma_tb, IncidentPrecheckDecision], metrics)
```

`decisions` is a total immutable mapping over canonical requested keys. Authentication failure fans out the same safe `AUTH` decision to every key. A batch deadline fills all unscheduled/unfinished keys with `BATCH_TIMEOUT`; one per-key request timeout affects only that key unless the outer deadline also expired. Capture `checked_at` after the evidence used for that key is complete (or when its safe failure is determined), never at batch start.

### Evidence and Precedence

Live fixtures must establish `OPEN_INCIDENT_STATUSES`, `TERMINAL_INCIDENT_STATUSES`, and the explicit-empty envelope. The operator-confirmed `CUSTOMER_CHANNEL_VALUES` and `PROACTIVE_CHANNEL_VALUES` above are fixed; do not broaden them from Vietnamese names or unreviewed live values.

| Precedence | Validated observation | Decision contribution |
|---|---|---|
| 1 | Malformed envelope/row/ID, required call partial, or status/completion/origin contradiction | Stop key aggregation: `INDETERMINATE` with `CONTRACT`, `PARTIAL`, or `AMBIGUOUS` as appropriate. |
| 2 | Schema-valid incident envelope with explicit `data: []` (or the exact reviewed equivalent) | `CLEAR / NONE / NO_INCIDENTS_CONFIRMED`. |
| 3 | Reviewed open status with completion evidence, or contradictory status/completion fields | `INDETERMINATE / AMBIGUOUS`; contradiction wins before any closed/open rule. |
| 4 | Status/completion tuple matches a reviewed terminal rule | Incident is terminal; origin is unnecessary. |
| 5 | Reviewed open status, no completion evidence, and `kenh_tn` exactly matches the customer allowlist | Incident is conclusively customer-origin. |
| 6 | Reviewed open status, no completion evidence, and `kenh_tn` exactly matches the proactive allowlist | Incident is conclusively proactive; it must not suppress notification. Missing/unknown status, unsupported completion tuple, or unknown origin is `INDETERMINATE / AMBIGUOUS`. |

Aggregate only after every row passes structural and contradiction checks:

| Complete key evidence | Classification |
|---|---|
| At least one conclusively open customer-origin incident | `CUSTOMER_OPEN_TICKET` |
| Explicit valid empty collection | `CLEAR` |
| Every incident conclusively terminal, or every open incident conclusively proactive | `CLEAR` |
| Any contract, partial, contradictory, unknown, or otherwise incomplete evidence | `INDETERMINATE` |

### Deadline-Bounded Session and Scheduler

1. In `lookup_open_incident_facts_batch()`, set `deadline_monotonic = monotonic() + batch_timeout_seconds` before normalization, cache access, or authentication.
2. Thread the deadline through `PlaywrightAuthenticator.create_session(..., deadline_monotonic=None)`, `check_token_alive(..., timeout_seconds=None)`, `read_otp(..., deadline_monotonic=None)`, and `_capture_auth(..., deadline_monotonic=None)`. Cap lock wait, `urlopen`, Playwright navigation/waits, OTP polling sleeps, and capture polling by the remaining budget.
3. Create one `OneBSSApiClient`; each narrow helper passes a request timeout capped by remaining budget through `_request()`/`_json_request()`.
4. Submit at most `max_workers` tasks. Refill one slot only after a completion and only while budget remains. At expiry, stop scheduling, cancel not-started work with `cancel_futures=True`, fill every uncovered canonical key, and wait only for the at-most-`max_workers` active calls for no longer than the configured request timeout before closing the session once.
5. Record aggregate monotonic durations only. Never log a key, URL payload, response, token, cache content, or exception text.

### Session Cache Hardening

Add `_session_cache_lock(cache_path, deadline_monotonic)` in `auth.py`, backed by `fcntl.flock` on a private `0600` lock file. `PlaywrightAuthenticator.create_session()` acquires the process lock before any cache read, re-reads and validates the cache after acquisition, performs liveness/clear/login/save while holding it, and releases it on every exit. Retain `_AUTH_LOCK` only for same-process serialization if still useful; it is not the cross-process control.

Before read/clear/publish, use `lstat`/`os.open(..., O_NOFOLLOW)` checks: parent is an owned real directory with mode `0700`; cache and lock are owned regular files with mode `0600`; unsafe type, owner, or mode fails authentication closed. Publish JSON through an owned same-directory `0600` `O_CREAT|O_EXCL|O_NOFOLLOW` temp file, flush/fsync, `os.replace`, then fsync the directory. Never truncate the live cache. Contention tests must cover concurrent load, invalid-cache clear, one login, atomic publication, timeout waiting for the lock, and re-read-after-lock reuse.

## Typed Error Boundary

Create `OneBSSCoreError` and safe subclasses/mappers in `/home/vtst/onebss_core/src/onebss_core/errors.py`. The new client path converts `HTTPError`, `URLError`, JSON/envelope failures, and request deadline expiry without storing `str(exc)`, response body, upstream `error/message`, control characters, or chained raw exceptions. Tests inject sentinel customer data, token-like strings, and CR/LF into every upstream error surface and prove none appears in exceptions, DTO serialization, logs, or metrics.

## Related Code Files

- Create: `/home/vtst/onebss_core/src/onebss_core/errors.py` — typed safe error taxonomy.
- Modify: `/home/vtst/onebss_core/src/onebss_core/models.py` — closed enums and frozen public DTOs.
- Modify: `/home/vtst/onebss_core/src/onebss_core/auth.py` — deadline-aware auth plus private atomic inter-process-safe session cache.
- Modify: `/home/vtst/onebss_core/src/onebss_core/client.py` — narrow schema-valid incident calls, request deadline seam, safe errors, and aggregate timing hooks.
- Modify: `/home/vtst/onebss_core/src/onebss_core/service.py` — exact public batch method, private evidence rules, total-map aggregation, and bounded scheduler.
- Modify: `/home/vtst/onebss_core/src/onebss_core/__init__.py` — export the new method result types/enums/errors only.
- Modify: `/home/vtst/onebss_core/tests/test_core.py` — contract, evidence, deadline/backlog, cache contention, and no-leak regression coverage.
- Modify: `/home/vtst/onebss_core/README.md` — public signature/DTO, deadline semantics, evidence gate, and live-only/no-artifact guarantee.
- Verify only: `/home/vtst/onebss_core/src/onebss_core/settings.py` — `OneBSSSettings` remains the sole credential/path owner.

## Implementation Steps

1. Capture operator-reviewed anonymized fixtures for open customer, closed customer, each open proactive channel, no ticket, and open-status-plus-completion contradiction. Retain only synthetic IDs and minimum normalized fields.
2. Encode fixture-reviewed status sets, the operator-confirmed exact channel sets, explicit-empty envelope, and terminal/open precedence. Make every unreviewed channel or status ambiguous.
3. Add the public enums/DTOs and typed safe error boundary; keep incident evidence private and make total-map validation deterministic.
4. Harden cache directory/file validation and atomic publication, then place an inter-process deadline-aware lock around the full cache/session lifecycle with post-lock re-read.
5. Thread the monotonic deadline through cache liveness, login navigation, OTP polling, capture, and request helpers while preserving defaults for old callers.
6. Implement a targeted subscriber/incident helper with strict envelope/row/ID parsing and no legacy `_incident_rows()` empty-on-error behavior; do not call processing-history enrichment.
7. Implement contradiction-first per-incident classification, exact `kenh_tn` allowlists, key aggregation, and post-evidence `checked_at`.
8. Implement the refill-on-completion scheduler, deadline fill, queued cancellation, finite active shutdown, aggregate timings, and exactly-once session close.
9. Add deterministic tests for all four reviewed channels, unknown/missing channels, malformed envelopes/rows/IDs, contradictory completion, every classification/failure kind, one/duplicate/mixed inputs, and hundreds-key backlog.
10. Add multiprocess cache contention/permission/symlink/partial-write tests and sentinel no-leak tests; document the final contract.

## Todo List

- [x] Validate anonymized production status vocabulary, exact channel allowlists, explicit-empty schema, and terminal/open precedence.
- [x] Implement the typed private-evidence/closed-decision contract.
- [x] Enforce the auth-inclusive deadline and bounded scheduler.
- [x] Harden and contention-test the reusable session cache.
- [x] Document and prove no raw body, incident fact, or artifact escapes.

## Success Criteria

- [x] The decision map exactly covers canonical inputs; every malformed coverage/type/enum/time case becomes `INDETERMINATE + CONTRACT`.
- [x] Contradiction, schema-valid empty, terminal/open, and every reviewed/unknown proactive/customer channel case follow the tables exactly.
- [x] One batch authenticates/closes once; no more than `max_workers` tasks exist in flight; a hundreds-key backlog stops within the batch deadline plus at most one request-timeout shutdown margin.
- [x] Cache owner/type/mode/no-follow/atomicity and inter-process single-login behavior are deterministic under contention.
- [x] Public DTOs, errors, logs, and metrics contain only allowlisted enum/timing/count fields; sentinel raw values never survive.
- [x] Existing callers retain old timeout behavior when optional deadline arguments are omitted.

## Risk Assessment

| Risk | Observable signal | Pre-decided response |
|---|---|---|
| Live vocabulary/schema differs | Fixture becomes `CONTRACT`/`AMBIGUOUS` or disagrees with UI | Keep enforcement blocked; update only operator-reviewed constants/validators. |
| OneBSS adds or renames an origin channel | Open incident becomes `AMBIGUOUS` or disagrees with UI | Keep that key fail-closed; add a mapping only after explicit operator classification. Never infer from `dvg` or the display label. |
| Auth ignores remaining budget | Fake-clock auth returns after the batch bound | Thread the same monotonic deadline through every wait; do not wrap auth in an unbounded thread. |
| Executor drains queued work after expiry | Hundreds-key duration scales with key count | Use refill scheduling plus queued cancellation; never submit all keys. |
| Cache lock/file policy breaks reuse | Multiple logins, partial JSON, wrong-owner/mode acceptance, or lock timeout overrun | Fail closed, repair lock/atomic path, and keep daemon enforcement blocked. |
| Typed errors leak raw text | Sentinel body/message appears outside private request scope | Discard at client boundary and map from exception type/code only. |

## Security and Performance Gate

Credentials, OTP contents, cache/session values, raw OneBSS responses, incident IDs, and customer fields are forbidden from the new result/log/fixture path. Existing CTS `/home/vtst/do_chu_dong_api/login.py` OTP printing is a separate pre-existing issue and is not modified or claimed fixed here. Phase 3 must verify the ownership/type/mode of the configured OneBSS env, OTP, and cache paths without printing their values.

Deduplication occurs before I/O; the batch path never calls processing-history enrichment, and request payloads are not copied into decisions. Do not begin Phase 2 until focused Core tests and the anonymized fixture review pass:

```bash
cd /home/vtst/onebss_core
/usr/bin/python3 -m pytest -q tests/test_core.py -k "incident_precheck or session_cache or deadline"
```

## Next Steps

Phase 2 consumes only `IncidentPrecheckBatchResult`; it must not import private evidence helpers, parse OneBSS fields, stringify Core errors, or infer a missing decision.
