---
phase: 3
title: "Verify, Gate, and Roll Out Enforcement"
status: completed
priority: P1
effort: "1d"
dependencies: [2]
---

# Phase 3: Verify, Gate, and Roll Out Enforcement

## Context Links

- [Plan overview](./plan.md)
- Previous: [Phase 2 — Integrate the Two-Pass Customer Alert Gate](./phase-02-customer-alert-integration.md)
- [Current alert flow evidence](agent://CurrentAlertFlow)
- [OneBSS contract evidence](agent://OneBSSContract)

## Overview

Prove the cross-repository decision contract, auth-inclusive time bound, cache safety, monotonic persistence, retry/rate/freshness behavior, aggregate observability, and explicit bypass under the production interpreter. Deploy Core first and assert the exact new capability before deploying the application. Enable enforcement only after operator-reviewed anonymized fixtures agree with authoritative OneBSS state.

## Requirements

- Functional: Verify open customer terminal suppression; open proactive/all-terminal/explicit-empty sendability; every operational/contract/ambiguous failure's retryable skip; stale-fact skip; terminal no-call; and exhaustive rejection of unknown enum combinations.
- Functional: Verify contradictory status/completion evidence wins before closed/open/origin rules, malformed envelope/row/ID never becomes no-ticket, every reviewed channel maps exactly, and unknown/missing channels are indeterminate.
- Functional: Verify same-subscriber daily/weekly allowance across two passes, including two clear incidents in one batch at a limit of one and a successful send whose `SENT` finalization is delayed or fails.
- Functional: Verify `PRECHECK_FAILED`, `INDETERMINATE`, and `STALE` retry within the day and across midnight while quiet hours and the exact original first-attempt cutoff remain unchanged.
- Persistence: Verify absent/retry/expired-lease claims, active-lease exclusion, current `batch_id` refresh, compare-and-set ownership, immutable `SENT`/`SKIPPED_CUSTOMER_TICKET`, stale-writer rejection, and gateway `requestId` duplicate defense with independent SQLite connections.
- Deadline: Verify the monotonic deadline starts before cache/token/session work; lock wait, token check, Playwright navigation, OTP polling, capture, requests, scheduling, and shutdown all consume remaining budget.
- Load: With hundreds of canonical keys and `max_workers` blocked calls, prove at most `max_workers` futures are in flight, no post-deadline refill occurs, queued work is cancelled/filled, and completion is bounded by batch timeout plus at most one request-timeout shutdown margin.
- Freshness: Include a hundreds-row application timing scenario where each gateway call consumes its full configured timeout; later clear facts must become `STALE` and make zero gateway calls until a later eligible retry.
- Security: Verify session-cache directory/file ownership, type, `0700`/`0600` modes, no-follow behavior, atomic replacement, deadline-aware inter-process locking, and post-lock re-read. Verify configured OneBSS env and OTP paths are owned regular non-symlink files with no group/other access, without printing paths or contents.
- Security: Inject sentinel PII, ticket identifiers, token-like values, and CR/LF into upstream bodies/messages. Prove the new Core DTO, adapter, logs, metrics, and SQLite reasons contain only allowlisted fields/enums/counts/durations.
- Operational: Under `/usr/bin/python3` from `/home/vtst/do_chu_dong_api`, assert the exact `lookup_open_incident_facts_batch` parameter names/kinds and `IncidentPrecheckBatchResult` return type; importing only pre-existing classes is insufficient.
- Rollback: Verify strict boolean parsing, invalid-value force-enabled behavior, `BYPASSED` without synthetic decisions, startup/every-cycle warnings, retained terminal skips, and the authorized re-enable procedure. Do not add a new append-only audit system, root-owned config requirement, or expiry.
- Scope: Compare identities and counters for precision, wide-area, group, personal, weak-signal, and recovery branches; only the customer cutoff handoff and additive customer result fields may differ.

## Verification Architecture

1. **Deterministic Core layer:** private evidence validators, total DTO, typed errors, auth/cache deadline behavior, refill scheduler, hundreds-key backlog, aggregate timing, and no-leak tests.
2. **Deterministic application layer:** hostile DTO adapter, exact-clear gate, atomic claim/finalize races, same-batch anti-spam, checked-at aging, cross-midnight retry, bypass, bridge metrics, and unrelated branch invariants.
3. **Installed capability layer:** deploy Core first; use the exact service interpreter/working directory to inspect the new method and new DTO types before any app deployment.
4. **Read-only live fixture layer:** an authorized operator supplies ephemeral known examples; the public Core method emits only decisions and aggregates. Compare to the UI and discard inputs/results.
5. **Controlled app layer:** deploy the app in explicitly authorized `BYPASSED`, confirm warnings/aggregate health and unchanged terminal rows, then switch to `ENFORCED` only after all gates pass. Core errors never auto-bypass; they fail closed and retry.

## Deterministic Outcome Matrix

| Scenario | Required observable result |
|---|---|
| Open customer-origin, no contradiction | `CUSTOMER_OPEN_TICKET/NONE`; claim finalizes immutable `SKIPPED_CUSTOMER_TICKET`; no gateway call. |
| Open proactive only, all terminal, explicit valid empty `data` | `CLEAR/NONE`; send only while fresh/claimed/rate-allowed. |
| Open status plus completion, or unknown/missing `kenh_tn` | `INDETERMINATE/AMBIGUOUS`; retryable `INDETERMINATE`; no send. |
| Absent/wrong `data`, non-dict row, missing/unparseable incident ID | `INDETERMINATE/CONTRACT`; retryable `PRECHECK_FAILED`; no send. |
| Missing/extra/wrong key, wrong DTO, unknown enum, bad/future `checked_at` | Adapter substitutes safe contract failure; no send and no raw value. |
| Auth/API/request/batch/partial failure | Exact failure bucket, retryable `PRECHECK_FAILED`, no send. |
| Clear fact ages past configured maximum during prior gateway calls | `STALE`, retry counter/metric increments later, no stale send. |
| Prior precheck failure at last eligible cycle, still-active outage next day | Re-enters after quiet hours despite first-off cutoff; may reach `SENT` or `SKIPPED_CUSTOMER_TICKET`. |
| Two same-subscriber clear incidents, daily max one | Exactly one successful gateway call; second is rate-skipped after immediate recheck/reservation. |
| Concurrent claims/stale finalizer | One owner; terminal row cannot downgrade; latest successful claim carries current `batch_id`. |
| Explicit precheck false | `precheck_state=BYPASSED`, zero checked/decision counts, warning, legacy rate/send path; terminal skips remain terminal. |
| Invalid bypass value | Forced `ENFORCED` plus safe warning; never truthiness-based bypass. |

## Live Smoke Prerequisites

- Authorized OneBSS access and a healthy reusable session under the service account; no credentials, OTP material, paths, headers, or cache contents are displayed.
- Operator-confirmed, currently valid examples labeled only `OPEN_CUSTOMER`, `CLOSED_CUSTOMER`, `OPEN_PROACTIVE`, and `NO_TICKET` outside committed files.
- Operator approval of the exact open/terminal vocabulary, explicit-empty envelope, direct `kenh_tn` origin values, and contradiction precedence. The confirmed customer set is `Qua tổng đài báo hỏng 119`; the confirmed proactive set is `Qua OB GHTT`, `Xuất theo file`, and `Qua công cụ xuất phiếu chủ động`. Use additional ephemeral contradiction examples when available; never guess missing semantics.
- A non-sending probe that invokes only `OneBSSCore.lookup_open_incident_facts_batch()` and prints classification/failure/reason plus aggregate counts/durations—no canonical key, raw value, or per-incident fact.
- An agreed maintenance window and authorized bypass/re-enable record in the existing operational change process.

## Expected Live Outcomes

| Fixture label | Expected classification | Enforcement behavior |
|---|---|---|
| `OPEN_CUSTOMER` | `CUSTOMER_OPEN_TICKET / NONE` | Terminal suppress |
| `CLOSED_CUSTOMER` | `CLEAR / NONE` | Sendable only after app freshness/rate/claim checks |
| `OPEN_PROACTIVE` | `CLEAR / NONE` | Sendable only after app freshness/rate/claim checks |
| `NO_TICKET` | `CLEAR / NONE` from reviewed explicit-empty envelope | Sendable only after app freshness/rate/claim checks |

Any `INDETERMINATE`, non-`NONE` failure, UI disagreement, unknown value, malformed shape, raw-data leak, or timing-bound violation fails the live gate. Do not widen an allowlist to make a fixture pass.

## Aggregate Observability Contract

Every customer cycle exposes fixed zero-filled `classification_counts` and `failure_kind_counts`, `onebss_checked`, `onebss_retried`, `onebss_stale`, `onebss_deadline_expired`, request count/duration total/max, batch duration, and `precheck_state`. Startup and every cycle emit an aggregate warning while bypassed. No metric/log label contains `MA_TB`, subscriber/request/ticket ID, upstream field/value, error text, path, or customer detail.

Release stop conditions are exact: any false clear/suppress, terminal downgrade, duplicate accepted `requestId`, stale send, same-batch rate excess, missing/extra decision, raw-value leak, active futures above `max_workers`, post-deadline scheduling, batch duration above batch timeout + request-timeout shutdown margin, live fixture mismatch, or non-customer branch delta blocks/halts enforcement. Nonzero Core failures during normal operation remain fail-closed/retryable; they do not silently or automatically activate bypass.

## Related Code Files

- Verify: `/home/vtst/onebss_core/src/onebss_core/errors.py`
- Verify: `/home/vtst/onebss_core/src/onebss_core/models.py`
- Verify: `/home/vtst/onebss_core/src/onebss_core/auth.py`
- Verify: `/home/vtst/onebss_core/src/onebss_core/settings.py`
- Verify: `/home/vtst/onebss_core/src/onebss_core/client.py`
- Verify: `/home/vtst/onebss_core/src/onebss_core/service.py`
- Verify: `/home/vtst/onebss_core/src/onebss_core/__init__.py`
- Verify: `/home/vtst/onebss_core/tests/test_core.py`
- Verify: `/home/vtst/onebss_core/README.md`
- Verify: `/home/vtst/do_chu_dong_api/onebss_precheck.py`
- Verify: `/home/vtst/do_chu_dong_api/customer_notification_service.py`
- Verify: `/home/vtst/do_chu_dong_api/alert_db.py`
- Verify: `/home/vtst/do_chu_dong_api/notification_service.py`
- Verify: `/home/vtst/do_chu_dong_api/notification_bridge.py`
- Verify: `/home/vtst/do_chu_dong_api/tests/test_onebss_precheck.py`
- Verify: `/home/vtst/do_chu_dong_api/tests/test_customer_notification_service.py`
- Verify: `/home/vtst/do_chu_dong_api/tests/test_notification_bridge.py`
- Verify: `/home/vtst/do_chu_dong_api/README.md`
- Verify only: `/etc/systemd/system/do-quang-chu-dong.service` — interpreter/working-directory evidence; do not modify.
- Explicitly out of scope: `/home/vtst/do_chu_dong_api/login.py` — its pre-existing OTP prints require separate remediation; this rollout must not claim they were fixed.

## Implementation and Rollout Steps

1. Complete the reviewed Core constants/validators and both focused deterministic suites. Reject implementation-only assertions; retain observable contract/race/deadline/security tests.
2. Exercise fake-clock/fake-transport Core authentication and hundreds-key backlog. Prove the first deadline timestamp precedes cache/session work, cancellation/refill behavior, shutdown margin, and exactly-once session close.
3. Exercise multiprocess cache contention plus owner/type/mode/symlink/atomic-publish failures. Verify the configured env/OTP/cache path policy without displaying path values.
4. Exercise the application outcome matrix, same-batch daily/weekly rate boundary, stale-fact gateway-timeout load, multi-connection claims, terminal stale writes, current-batch attribution, and cross-midnight retry to both clear and customer-open outcomes.
5. Exercise bridge result identities for all established precision modes and unrelated alert branches; compare before/after outputs except the named customer fields/handoff.
6. Deploy the reviewed `onebss_core` package first. From the service working directory, run the exact non-authenticating signature/type capability check below; block the app deploy on any mismatch.
7. Run the application focused suite against that deployed Core, then deploy the application with a separately approved exact-boolean `BYPASSED` setting. Confirm startup and every-cycle bypass warnings, zero decision checks, unchanged terminal skips, and complete aggregate buckets.
8. Run the non-sending four-fixture probe and have the authorized reviewer approve UI agreement plus status/schema/channel vocabulary. Discard ephemeral fixture inputs/output.
9. Change only the precheck switch to exact boolean true. Confirm the next cycle reports `ENFORCED`; observe classification/failure/timing/retry/stale/deadline counts and unrelated branch invariants.
10. Exercise the authorized manual bypass and re-enable procedure once. Never auto-bypass because OneBSS is unhealthy; absent an explicit operator action, failures stay fail-closed and retryable.

## Todo List

- [x] Pass Core contract/deadline/backlog/cache/security verification.
- [x] Pass application contract/rate/freshness/cutoff/claim/bridge verification.
- [x] Deploy Core first and prove the exact capability under `/usr/bin/python3`.
- [x] Approve all live fixture semantics without retaining raw data.
- [x] Deploy bypassed, enable deliberately, observe aggregates, and prove manual rollback/re-enable.

## Validation Commands

Focused Core verification:

```bash
cd /home/vtst/onebss_core
/usr/bin/python3 -m pytest -q tests/test_core.py -k "incident_precheck or session_cache or deadline"
```

After Core deployment, exact non-authenticating capability check from the systemd working directory:

```bash
cd /home/vtst/do_chu_dong_api
/usr/bin/python3 - <<'PY'
import inspect
from typing import get_type_hints
from onebss_core import IncidentPrecheckBatchResult, IncidentPrecheckDecision, IncidentClassification, IncidentFailureKind, IncidentDecisionReason, OneBSSCore
method = OneBSSCore.lookup_open_incident_facts_batch
signature = inspect.signature(method)
assert tuple(signature.parameters) == ("self", "ma_tb_values", "request_timeout_seconds", "batch_timeout_seconds", "max_workers")
for name in ("request_timeout_seconds", "batch_timeout_seconds", "max_workers"):
    assert signature.parameters[name].kind is inspect.Parameter.KEYWORD_ONLY
assert get_type_hints(method)["return"] is IncidentPrecheckBatchResult
assert set(IncidentClassification.__members__) == {"CUSTOMER_OPEN_TICKET", "CLEAR", "INDETERMINATE"}
assert set(IncidentFailureKind.__members__) == {"NONE", "AUTH", "API", "REQUEST_TIMEOUT", "BATCH_TIMEOUT", "PARTIAL", "AMBIGUOUS", "CONTRACT"}
assert IncidentPrecheckDecision.__dataclass_fields__.keys() == {"classification", "failure_kind", "reason", "checked_at"}
assert IncidentDecisionReason.CONTRACT_VIOLATION
print("onebss incident-precheck capability ok")
PY
```

Focused application verification against the deployed Core:

```bash
cd /home/vtst/do_chu_dong_api
/usr/bin/python3 -m pytest -q tests/test_onebss_precheck.py tests/test_customer_notification_service.py tests/test_notification_bridge.py -k "onebss or precheck or customer_outage or cutoff"
```

The non-sending live fixture gate runs only after these pass. It must not print canonical keys, paths, or raw facts.

## Risk Assessment

| Risk | Observable signal | Pre-decided response |
|---|---|---|
| Deployed app sees old editable Core | Exact method/type/signature assertion fails | Stop; deploy Core first and repeat capability check before app deployment. |
| Authentication/cache remains unsafe or slow | Permission/contention test fails, repeated login, or auth exceeds remaining budget | Keep enforcement blocked/bypassed by explicit operator decision; repair Core and repeat gates. |
| Live semantics differ | Any fixture mismatch or unknown status/channel | Keep enforcement blocked; return to Phase 1 reviewed constants/fixtures. |
| OneBSS degrades after enable | Failure/deadline aggregates rise but remain within bounded cycle | Continue fail-closed retries while investigating; bypass only through authorized procedure. |
| False clear/suppress or stale send | Operator/customer evidence contradicts decision | Activate authorized bypass, preserve terminal data, stop enforcement, correct Core rules, and review affected rows before manual action. |
| Persistence/rate race | Terminal downgrade, duplicate accepted request, or limit excess | Stop rollout; fix claim/CAS/reservation logic without holding DB across network. |
| Sensitive value escapes | Sentinel or live raw value appears in new DTO/log/DB/metric | Stop rollout, remove the new sink, rotate exposed secrets if applicable, and repeat security gates. |
| Unrelated notification delta | Non-customer identity/count differs | Roll back the app integration; do not modify precision policy to compensate. |

## Security Scope and Rollback

Live examples remain out of committed files, use anonymous labels in review output, and are discarded. New code must never log credential/OTP/session/cache values, upstream body/message, customer/ticket identifiers, or private evidence. Existing CTS `login.py` OTP output is acknowledged but rejected from this integration's scope and must be tracked separately; this plan makes no claim of system-wide OTP log remediation.

Rollback is deliberate, not automatic: an authorized operator records the reason in the existing process, sets the resolved switch to exact boolean false, verifies `BYPASSED` warnings and immutable terminal rows, then re-enables with exact boolean true after health/fixture gates. No new append-only infrastructure, root-owned configuration, or expiry is required by this plan.

## Success Criteria

- [x] Every deterministic/live outcome matches the tables, and only exact fresh `CLEAR/NONE` can call Telecom Zalo.
- [x] Hundreds-key auth/request scheduling and shutdown remain within the stated deadline bound; sequential gateway delay produces stale skips, never stale sends.
- [x] Same-batch anti-spam, cross-midnight retries, atomic leases, batch attribution, terminal monotonicity, and requestId defense hold under races/failures.
- [x] Session cache and configured secret paths pass owner/type/mode/no-follow/contention checks without revealing values.
- [x] All aggregate buckets are present and low-cardinality; no sentinel/raw data appears.
- [x] Core-first capability check, application tests, four live fixtures, controlled enable, manual bypass, and re-enable all pass with no unrelated branch delta.

## Next Steps

After successful enforcement, retain aggregate monitoring and the authorized bypass procedure. New OneBSS status/schema/channel vocabulary returns to fixture review and remains indeterminate until approved; it is never mapped in the application.
