---
title: "Customer Outage Zalo Alerts via Telecom Zalo Gateway"
description: "Send direct Zalo outage notifications to customers via Telecom Zalo Gateway with strict anti-spam rate limiting and idempotency."
status: completed
priority: P1
effort: "4h"
tags: [alerts, zalo, customer-notifications, telecom-zalo]
created: 2026-09-11
---

# Customer Outage Zalo Alerts via Telecom Zalo Gateway

## Overview

When individual subscriber outages are detected during OLT measurement cycles, optionally notify the customer directly via Zalo using the Telecom Zalo Gateway (`POST /api/public/messages/send-by-ma-tb`). To prevent spamming customers, the feature enforces a 4-tier filtering mechanism:
1. Quiet Hours filter (07:00 - 21:00 only).
2. Idempotency per outage incident (`first_off_time`).
3. Daily rate limit (max 1 message / 24 hours per subscriber).
4. Weekly rate limit (max 3 messages / 7 days per subscriber).
5. No recovery notifications to customers.

Messages display the central hotline `0822036382` along with the assigned local technician (`ten_nvkt_db`) to build customer trust.

## Goals

| # | Goal | Priority |
|---|------|----------|
| 1 | Database schema & configuration for customer alert logging and gateway integration | P1 |
| 2 | Customer notification service module with 4-tier anti-spam filters, formatting, and HTTP dispatch | P1 |
| 3 | Pipeline integration into `notification_bridge.py` and comprehensive test coverage | P1 |

## Phases

| # | Phase | Status |
|---|-------|--------|
| 1 | [Phase 1: Database Schema & Configuration](./phase-01-start.md) | Completed |
| 2 | [Phase 2: Customer Notification Service](./phase-02-customer-notification-service.md) | Completed |
| 3 | [Phase 3: Pipeline Integration & Verification](./phase-03-pipeline-integration-and-tests.md) | Completed |

## Success Criteria

- [x] `customer_outage_alert_log` table created with necessary indexes in SQLite `onu_measurements.db`.
- [x] Environment variables configured in `.env` and `config.py`.
- [x] `customer_notification_service.py` implements message formatting, 4-tier anti-spam filters, and Telecom Zalo API dispatch.
- [x] Integrated into `notification_bridge.py` without blocking the main measurement daemon loop.
- [x] All unit and regression tests pass (`pytest -q`).

<!-- slug: customer-outage-zalo-alerts -->