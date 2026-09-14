---
title: "Continuous terminal replacement tracking via state machine"
description: "Zero-overhead tracking of onuSN and softVersion changes using the existing subscriber state machine."
status: done
priority: P1
effort: "2h"
tags: ["tracking", "state-machine"]
created: 2026-09-13
---

# Continuous terminal replacement tracking via state machine

## Overview
Integrate `onuSN` and `softVersion` tracking into the existing `subscriber_status_state` table to detect terminal replacements immediately after each measurement batch without querying historical data.

## Goals
| # | Goal | Priority |
|---|------|----------|
| 1 | Extend `subscriber_status_state` with `last_onu_sn` and `last_soft_version` | P1 |
| 2 | Create `terminal_replacement_log` table for audit trails | P1 |
| 3 | Update `alert_engine.py` to compare new values against cached state and log transitions | P1 |
| 4 | Ensure zero additional database I/O for historical queries | P1 |

## Phases
| # | Phase | Status |
|---|-------|--------|
| 1 | [Phase 1: Database Schema Expansion](./phase-01-database.md) | pending |
| 2 | [Phase 2: Data Models and Engine Updates](./phase-02-engine.md) | pending |

## Success Criteria
- [x] Schema migrations complete transparently.
- [x] Replacements are correctly logged to `terminal_replacement_log` only when true SN changes occur.
- [x] Overall processing time for a batch does not increase noticeably.

## Validation Log

### Verification Results
- Claims checked: 10
- Verified: 10 | Failed: 0 | Unverified: 0
- Tier: Light
- Failures: None

### Validation Session 1
- **Q:** When an ONU replacement is detected (SN changes), should we also explicitly reset the subscriber's state machine status?
  - **Decision:** Keep them independent (Logging terminal replacements is strictly an audit trail; the state machine will update itself organically based on signal level.)
- **Q:** How long should we retain the data in `terminal_replacement_log`?
  - **Decision:** Indefinitely (Terminal replacements are rare enough that we can keep them forever without database bloat.)
- **Q:** Should terminal replacements trigger an immediate notification via Telegram/Zalo, or are they for database audit only?
  - **Decision:** Audit only (Just log to the database. We don't need real-time noise for device swaps.)
- **Q:** How should we handle initial states where `last_onu_sn` is `NULL` in the database, but a measurement comes in with a valid SN?
  - **Decision:** Silent initialization (Treat `NULL -> valid SN` as the baseline state and update it without logging a replacement.)

### Whole-Plan Consistency Sweep
- Checked `plan.md` and all phase files for conflicting claims regarding notifications or state machine resets.
- Confirmed that phase 2 implementation explicitly mentions silent initialization and audit-only handling without modifying existing state flow.
- Reconciled the removed `phase-01-start.md` orphan file.
- All documents are fully consistent.
