---
phase: 1
title: "Phase 1: Database Schema Expansion"
status: done
priority: P1
effort: "1h"
dependencies: []
---

# Phase 1: Database Schema Expansion

## Overview
Add state tracking columns to the existing subscriber state machine and introduce a dedicated log table for equipment changes.

## Requirements
- Functional: Retain historical subscriber states seamlessly without breaking current alerting.
- Non-functional: Safe, backward-compatible SQL migrations using `ALTER TABLE ADD COLUMN`.

## Architecture
- Use `_ensure_column` in `alert_db.py` to append `last_onu_sn` and `last_soft_version` to `subscriber_status_state`.
- Define `terminal_replacement_log` to capture subscriber details, old/new SN and firmware, and timestamps.

## Related Code Files
- Modify: `alert_db.py`

## Implementation Steps
1. In `AlertRepository.ensure_schema()`, add `self._ensure_column` calls for `last_onu_sn` (TEXT) and `last_soft_version` (TEXT) on `subscriber_status_state`.
2. Add `CREATE TABLE IF NOT EXISTS terminal_replacement_log` containing fields for subscriber metadata, `old_onu_sn`, `new_onu_sn`, `old_soft_version`, `new_soft_version`, and `changed_at`.
3. Update `_save_state_sql()` to include the two new columns in the `INSERT` and `ON CONFLICT DO UPDATE SET` clauses.
4. Update `_state_params()` to supply these two new values from the state dictionary.
5. Create an `insert_terminal_replacements(logs)` method in `AlertRepository` to batch insert identified changes.

<!-- Updated: Validation Session 1 - Retention for terminal_replacement_log is indefinite; no cleanup cron needed. -->

## Success Criteria
- [x] Running `AlertRepository(..).ensure_schema()` successfully adds columns to existing database files without errors.
- [x] Inserting/Updating states via `persist_state_machine_results` works with the new parameters.

## Risk Assessment
- **Risk**: Existing SQLite DB might be locked if a running process conflicts during ALTER TABLE.
- **Mitigation**: Deploy this update during a quiet maintenance window or let the built-in timeout handles the retry loop smoothly.
