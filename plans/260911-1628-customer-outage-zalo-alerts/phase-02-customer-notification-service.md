---
phase: 2
title: "Customer Notification Service"
status: in-progress
priority: P1
effort: "2h"
dependencies: [1]
---

# Phase 2: Customer Notification Service

## Overview
Implement the core `customer_notification_service.py` module responsible for message templating, 4-tier anti-spam validation, and HTTP dispatch via Telecom Zalo Gateway.

## Requirements
- Functional:
  - Format customer outage notification messages using subscriber metadata (Ma_Tb, Ten_Tb, Diachi_Ld, Ten_Nvkt_Db, Hotline, First_Off_Time, Duration).
  - Enforce 4-tier anti-spam filters:
    1. Quiet hours check (default 07:00 - 21:00).
    2. Incident idempotency check (`cust-off-{subscriber_key}-{first_off_time}`).
    3. Daily rate limit (max 1 send per 24 hours per subscriber).
    4. Weekly rate limit (max 3 sends per 7 days per subscriber).
  - Dispatch message to Telecom Zalo Gateway (`POST /api/public/messages/send-by-ma-tb`) with timeout and error handling.
  - Record audit log entry in `customer_outage_alert_log`.
- Non-functional:
  - Non-blocking execution with short network timeout (default 3s).
  - Resilient to network errors, gateway unavailability, or invalid subscriber mappings.

## Architecture
- Functions:
  - `format_customer_outage_message(row, hotline)`
  - `evaluate_customer_alert_eligibility(repo, row, config, now)` -> `(is_eligible, reason)`
  - `send_customer_outage_message(ma_tb, request_id, content, config)` -> `dict`
  - `process_customer_outage_alerts(repo, candidate_rows, batch_id, config, now)` -> summary dict

## Related Code Files
- Create: `customer_notification_service.py`

## Implementation Steps
1. Create `customer_notification_service.py` with message templating and duration formatting.
2. Implement 4-tier anti-spam logic and policy checks.
3. Implement HTTP client calling Telecom Zalo Gateway with `X-API-Key` and `requestId`.
4. Implement batch processor that iterates through eligible offline rows and logs results.

## Success Criteria
- [x] Message format matches the specified VNPT Sơn Tây template.
- [x] Anti-spam filters accurately block night-time, duplicate, and excessive messages.
- [x] Gateway client handles successful (202/200) and failed responses cleanly.

