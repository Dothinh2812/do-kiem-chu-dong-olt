# Danh Ba Authoritative Port Assignment Design

## Goal

Prevent false OFF and wide-area alerts caused by subscribers moved to a new OLT port while stale measurement rows on the old port remain OFF.

`database.db:danhba` is the authoritative source for the current subscriber-to-port assignment. Alert processing must trust the current `danhba.sub` mapping over stale historical measurement locations.

## Problem

The alert pipeline currently keys subscriber state by `subscriber_key`, which has the form:

```text
<OLT>_<frame>-<slot>-<port>:<onuIndex>
```

When a subscriber is moved to another port, the old `subscriber_key` can still be measured as `OFF`. If the same `MA_TB` is now active on a new `subscriber_key`, the old OFF row can:

- create or keep an individual OFF alert
- inflate a wide-area port count
- make a wide-area incident appear to last for days
- appear in `runtime/current_off_snapshot.json`

This is a stale port assignment, not a current customer outage.

## Authoritative Rule

For rows with a known `MA_TB`, a measurement row is alert-eligible only if its `subscriber_key` equals the current `sub` for that `MA_TB` in `danhba`.

If a measured row has `MA_TB=TB001` but its measured `subscriber_key` differs from the current `danhba.sub` for `TB001`, the row is stale and must be excluded from alert processing.

Rows without a known `MA_TB` keep the current behavior. The system should not suppress unknown subscribers because it cannot safely match them to an authoritative assignment.

## Pipeline Behavior

Add an early filtering step after raw measurement rows are enriched with `danhba` metadata and before the state machine runs:

1. Load authoritative `MA_TB -> sub` assignments from `danhba`.
2. Build `SubscriberSnapshot` rows as today.
3. Partition snapshots into:
   - `valid_assignment_snapshots`: missing `MA_TB`, or `subscriber_key == danhba.sub` for that `MA_TB`
   - `stale_assignment_snapshots`: known `MA_TB`, but `subscriber_key != danhba.sub`
4. Run the state machine, individual OFF alerts, wide-area detection, pattern scoring, current OFF snapshot export, and notification dispatch only on `valid_assignment_snapshots`.
5. Log counts of stale rows by batch for operator visibility.

This makes `ON wins over OFF` unnecessary as the primary rule. If `danhba` is continuously updated and correct, the new port is the only eligible location. Any old OFF row for the same `MA_TB` is stale even if the new port is not present in the current batch.

## Existing State Handling

If a stale old `subscriber_key` already has persisted `ALERT_OFF`, `PENDING_OFF`, or `STABLE_OFF` state, it should not continue to appear as current OFF. The filtering step prevents it from being included in the current batch snapshot and notification paths.

For this change, leave the old persisted state untouched but inactive. The stale old `subscriber_key` is filtered out of current processing, so it stops producing current alerts without rewriting historical state.

Marking stale rows recovered or ignored with a distinct persisted reason is a separate cleanup feature and is out of scope here.

## Wide-Area Behavior

Wide-area detection must count only valid-assignment snapshots. Stale OFF rows from moved subscribers must not contribute to the port threshold and must not affect `first_off_time` or duration.

This directly fixes cases where a few old OFF subscribers make a fresh wide-area alert look like it started several days earlier.

## Current OFF Snapshot Behavior

`runtime/current_off_snapshot.json` must include only valid-assignment current OFF rows. Stale old-port OFF rows are omitted.

The summary must include `stale_assignment_suppressed` so the dashboard/operator can see that rows were ignored due to current danh ba assignment.

## Logging

Batch logs should include:

```text
stale_assignment_suppressed=<count>
```

Include a compact sample of up to 5 stale rows:

```text
MA_TB old_sub -> current_sub
```

This helps diagnose whether false alerts are being filtered because a subscriber moved ports.

## Testing

Add alert engine tests for:

1. A subscriber measured `OFF` on an old `subscriber_key`, while `danhba` maps the same `MA_TB` to a new `subscriber_key`: no outage alert is created for the old key.
2. Several stale old-port OFF rows on the same parent port: no wide-area alert is created from stale rows.
3. A valid current `subscriber_key` matching `danhba.sub` and measured `OFF`: existing individual and wide-area behavior is unchanged.
4. A row with missing `MA_TB`: existing behavior is unchanged.

## Non-Goals

- Do not rewrite historical `outage_alerts` or `recovery_alerts`.
- Do not infer port moves from measurement data alone.
- Do not suppress rows with missing `MA_TB`.
- Do not require the new port to be `ON` in the same batch.

## Acceptance Criteria

- Stale old-port OFF rows for moved subscribers no longer create individual OFF alerts.
- Stale old-port OFF rows no longer count toward wide-area thresholds.
- Current OFF snapshot excludes stale old-port rows.
- Existing behavior remains unchanged for rows whose `subscriber_key` matches current `danhba.sub`.
- Existing behavior remains unchanged for rows that cannot be matched by `MA_TB`.
