---
phase: 1
title: "Database Schema & Configuration"
status: in-progress
priority: P1
effort: "1h"
dependencies: []
---

# Phase 1: Database Schema & Configuration

## Overview
Set up the storage foundation and configuration parameters for customer outage alerts via Telecom Zalo Gateway.

## Requirements
- Functional:
  - Table `customer_outage_alert_log` in `onu_measurements.db` to track customer alerts and prevent duplicates.
  - Query functions in `AlertRepository` to support idempotency and 24h / 7d rate limit checks.
  - Configuration variables for Telecom Zalo URL, API key, hotline, time windows, and limits.
- Non-functional:
  - Indexed queries for fast lookup under high volume.
  - Safe defaults (disabled by default unless configured).

## Architecture
- `alert_db.py`: Defines table creation in `init_db()` and adds repository methods:
  - `log_customer_outage_alert(...)`
  - `has_customer_outage_alert_sent(subscriber_key, first_off_time)`
  - `count_recent_customer_outage_alerts(subscriber_key, since_datetime)`
- `config.py`: Exposes environment configuration options.
- `.env`: Contains runtime connection info to Telecom Zalo Gateway and hotline.

## Related Code Files
- Modify: `alert_db.py`
- Modify: `config.py`
- Modify: `.env`

## Implementation Steps
1. Add `customer_outage_alert_log` DDL and indexes to `init_db` in `alert_db.py`.
2. Add repository methods in `AlertRepository` (`log_customer_outage_alert`, `has_customer_outage_alert_sent`, `count_recent_customer_outage_alerts`).
3. Add configuration loader functions in `config.py`.
4. Update `.env` with gateway settings.

## Success Criteria
- [x] Table `customer_outage_alert_log` is created in database initialization.
- [x] Repository methods query and insert logs correctly.
- [x] Configuration variables load with expected defaults.

