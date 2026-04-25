# Current OFF Snapshot Design

## Goal

Change individual OFF alerting from a one-time event-notification model to a per-batch current-state snapshot model, while preserving `outage_alerts` as the event log for first detection and recovery tracking.

After this change:

- Every completed measurement batch sends the current list of still-OFF subscribers that have not recovered yet.
- The per-subscriber `Kéo dài` value is cumulative from the first OFF detection after the last ON state until the current batch send time.
- A latest JSON snapshot file is written on every batch for external dashboard consumption.
- The JSON snapshot includes both active individual alerts and suppressed records, with suppression flags and reasons, so downstream dashboards can render separate views.

## Current Problems

The current implementation treats individual OFF notifications as one-time messages:

- `OutageAlert` is created only on the transition into `ALERT_OFF`.
- Notification dispatch reads only `list_unsent_outage_alerts(batch_id)`.
- Successfully sent outage alerts are marked `notification_sent = TRUE`.
- A subscriber remaining OFF across later batches is not re-sent.
- The displayed duration is effectively fixed at event-creation time because it prefers stored `off_duration_minutes`.

This behavior no longer matches the required product behavior, where each batch should represent the current OFF state of the network.

## Design Summary

Keep the current event-log model intact:

- `outage_alerts` continues to represent first OFF detection events.
- `recovery_alerts` continues to represent recovery events.

Add a new current-state snapshot model:

- Build a batch-level snapshot of all subscribers that are currently OFF after state-machine processing completes.
- Apply the same wide-area and pattern-exclusion rules used by alerting.
- Retain suppressed subscribers in the snapshot with explicit flags and reasons.
- Send recurring OFF notifications from the non-suppressed portion of this snapshot, grouped by `doi_vt` and NVKT, on every batch.
- Export the full snapshot to a single latest JSON file that is overwritten every batch.

## Data Model

No new database table is required for the agreed scope.

The authoritative sources remain:

- `subscriber_state`: current subscriber lifecycle state and `first_off_time`
- `outage_alerts`: first-OFF event log
- `recovery_alerts`: recovery event log

Add an in-memory normalized snapshot record shape used for both notification formatting and JSON export.

Proposed snapshot fields per subscriber:

- `subscriber_key`
- `port_id`
- `parent_port_key`
- `batch_id`
- `measured_at`
- `current_state`
- `current_status`
- `ma_tb`
- `ten_tb`
- `ma_men`
- `olt_name`
- `doi_vt`
- `diachi_ld`
- `dienthoai_lh`
- `ten_nvkt_db`
- `first_off_time`
- `duration_minutes`
- `duration_text`
- `suppressed_by_pattern`
- `suppressed_by_wide_area`
- `suppression_reason`

Top-level JSON structure:

- `batch_id`
- `measured_at`
- `generated_at`
- `summary`
- `subscribers`

Proposed `summary` fields:

- `total_off_subscribers`
- `active_individual_alerts`
- `suppressed_by_pattern`
- `suppressed_by_wide_area`
- `suppressed_total`
- `group_count_by_doi_vt`

## Batch Processing Flow

### 1. State machine remains unchanged for event creation

The transition engine still:

- creates `OutageAlert` only on the first confirmed OFF transition
- creates `RecoveryAlert` only on recovery
- persists lifecycle state in `subscriber_state`

This preserves event history and avoids breaking recovery logic.

### 2. Build current OFF snapshot from current state, not from `outage_alerts`

After processing all rows in a batch:

- identify subscribers whose current measured status is `OFF`
- combine current batch snapshot data with the persisted state row
- use `first_off_time` from `subscriber_state` as the start of the outage window
- compute `duration_minutes = floor(measured_at - first_off_time)`

This makes `duration_minutes` cumulative and batch-relative rather than event-relative.

### 3. Apply existing suppression logic to the current snapshot

Keep the current rule order:

1. detect wide-area outages
2. suppress relevant individual alerts for wide-area
3. update pattern exclusion table
4. suppress relevant individual alerts for pattern exclusion

For the new snapshot:

- the same suppression decisions must be reflected in the snapshot records
- suppressed records remain in JSON output
- only non-suppressed records are eligible for recurring message delivery

### 4. Send recurring individual OFF notifications from the snapshot

Notification input changes from:

- unsent outage events for the current batch

to:

- all current OFF snapshot records for the current batch where:
  - `suppressed_by_pattern = false`
  - `suppressed_by_wide_area = false`

Grouping remains:

- group by `doi_vt`
- inside each group, group by shortened NVKT name

### 5. Export latest JSON snapshot

At the end of batch processing:

- write one JSON file to a stable path, for example `runtime/current_off_snapshot.json`
- overwrite the same file on every batch

This file becomes the integration point for the external dashboard API.

## Notification Behavior

### Required behavior

For every batch:

- all subscribers still OFF and not suppressed are included in the outgoing recurring message
- a subscriber that remains OFF across many batches is included every batch
- a subscriber disappears from the recurring message immediately after recovery
- `Kéo dài` always reflects cumulative outage time up to the current batch send time

### Message semantics

The message is now a batch-time state snapshot, not a first-event notification.

That means:

- the same subscriber can appear in multiple consecutive batch messages while still OFF
- the same subscriber should not appear after it returns to `ON`

### Duration source

For recurring individual OFF messages:

- do not use persisted `off_duration_minutes` from `outage_alerts`
- use `first_off_time` plus current batch time to compute duration dynamically

`outage_alerts.off_duration_minutes` stays meaningful as the first-event duration at alert creation time for event logging, but is no longer the source for recurring snapshot messaging.

## JSON Snapshot Semantics

The JSON file must contain all currently OFF subscribers, including suppressed records.

This enables dashboard consumers to render separate views such as:

- active individual OFF alerts
- OFF subscribers suppressed by pattern rule
- OFF subscribers suppressed by wide-area rule

Dashboard filtering rules:

- active alert list: records where both suppression flags are false
- pattern-suppressed list: records where `suppressed_by_pattern` is true
- wide-area-suppressed list: records where `suppressed_by_wide_area` is true

If a record has multiple suppression reasons over time, `suppression_reason` should preserve the current string aggregation behavior used by the existing repository layer.

## Code Changes

### `alert_engine.py`

Add a batch snapshot construction phase after state-machine processing and suppression application:

- derive current OFF records from the processed batch snapshots and current persisted state
- compute cumulative duration from `first_off_time`
- annotate suppression flags
- pass this snapshot into:
  - recurring notification dispatch
  - JSON export

The engine should return snapshot counters in the batch result payload for observability.

### `notification_bridge.py`

Add a recurring current-OFF dispatch path:

- input becomes normalized current OFF snapshot records for the batch
- only non-suppressed records are sent
- sending no longer depends on `notification_sent` flags from `outage_alerts`

Keep wide-area and recovery dispatch behavior unchanged unless follow-up requirements change.

The legacy one-time outage dispatch path should be removed or replaced so the code does not send both models in parallel.

### `notification_service.py`

Add formatter support for snapshot-style records:

- duration formatting should use precomputed `duration_minutes` from snapshot records
- no fallback to event-level `off_duration_minutes` for this path

Existing event-log formatters can remain if still used elsewhere, but recurring OFF batch messages should use the new snapshot-oriented formatter path.

### New helper module

Add a small helper module for current OFF snapshot responsibilities:

- normalize snapshot records
- compute duration text
- build top-level JSON payload
- write latest snapshot JSON atomically if practical

This keeps snapshot logic out of formatter and dispatch files.

## File Output

Recommended path:

- `runtime/current_off_snapshot.json`

Write behavior:

- overwrite on every processed batch
- UTF-8 encoding
- `ensure_ascii=False`
- stable field names for downstream API compatibility

If partial write safety is desired, write to a temp file in the same directory and then rename into place.

## Testing Strategy

Add or update tests to cover:

1. recurring delivery
- subscriber OFF in batch 1 appears in batch 1 message
- same subscriber still OFF in batch 2 appears again in batch 2 message

2. cumulative duration
- `duration_minutes` increases between batches based on `first_off_time`

3. recovery removal
- subscriber OFF in one batch and ON in the next is absent from the next current OFF snapshot

4. suppression handling
- pattern-suppressed subscriber is present in JSON with suppression flags
- wide-area-suppressed subscriber is present in JSON with suppression flags
- suppressed subscribers are excluded from recurring individual OFF message payloads

5. grouping behavior
- grouping by `doi_vt` and NVKT remains stable for recurring messages

6. JSON contract
- top-level summary exists
- subscriber records contain all agreed fields

## Backward Compatibility

Preserved:

- outage and recovery event log creation
- recovery notification behavior
- wide-area alerting behavior
- existing state-machine transitions

Changed:

- individual OFF notification semantics move from first-event send to recurring current-state send
- individual OFF message duration becomes cumulative to current batch time

## Risks

### Duplicate semantic paths

If the old unsent-outage-event dispatch path remains active alongside the new recurring snapshot path, users may receive duplicate or contradictory messages. The implementation must consolidate to one path for individual OFF delivery.

### Duration correctness

If `first_off_time` is missing or malformed for a current OFF state, duration output will be wrong or blank. Tests should verify expected fallback behavior, and logs should expose malformed state when encountered.

### Dashboard contract drift

The JSON snapshot becomes an external integration surface. Field names and nullability should be treated as stable once released.

## Recommendation

Implement the recurring OFF snapshot model without changing the event-log tables. This is the smallest change that satisfies:

- per-batch resend of still-OFF subscribers
- cumulative duration in messages
- a complete latest JSON snapshot for an external dashboard
- preservation of suppression visibility for downstream filtering
