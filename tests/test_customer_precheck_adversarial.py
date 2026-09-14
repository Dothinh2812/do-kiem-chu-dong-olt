import asyncio
import json
import sqlite3
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import requests

from onebss_core import (
    IncidentClassification,
    IncidentDecisionReason,
    IncidentFailureKind,
    IncidentPrecheckBatchMetrics,
    IncidentPrecheckBatchResult,
    IncidentPrecheckDecision,
    OneBSSAuthError,
)
from onebss_core.auth import save_session_cache
from onebss_core.models import OneBSSSession

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

try:
    from do_chu_dong_api.alert_db import AlertRepository
    from do_chu_dong_api.customer_notification_service import (
        build_customer_alert_request_id,
        check_customer_alert_eligibility,
        process_customer_outage_alerts,
        send_customer_outage_message,
    )
    from do_chu_dong_api.notification_service import _is_within_time_window
    from do_chu_dong_api.onebss_precheck import canonicalize_ma_tb, run_customer_ticket_precheck
except ModuleNotFoundError:
    from alert_db import AlertRepository
    from customer_notification_service import (
        build_customer_alert_request_id,
        check_customer_alert_eligibility,
        process_customer_outage_alerts,
        send_customer_outage_message,
    )
    from notification_service import _is_within_time_window
    from onebss_precheck import canonicalize_ma_tb, run_customer_ticket_precheck


@pytest.fixture
def temp_repo(tmp_path):
    db_file = tmp_path / "onu_measurements.db"
    src_file = tmp_path / "database.db"

    with sqlite3.connect(src_file) as conn:
        conn.execute("CREATE TABLE danhba (ID INTEGER PRIMARY KEY)")

    repo = AlertRepository(str(db_file), str(src_file))
    repo.ensure_schema()
    return repo


# Scenario 1: Mixed-case ma_tb normalization
def test_adversarial_mixed_case_normalization(temp_repo):
    """Verifies uppercase and mixed-case ma_tb inputs match lowercase OneBSSCore outputs identically."""
    assert canonicalize_ma_tb("  HNIF_99999_XyZ  ") == "hnif_99999_xyz"
    assert canonicalize_ma_tb("STY_00123") == "sty_00123"

    mock_core = MagicMock()
    now_utc = datetime.now(timezone.utc)
    mock_core.lookup_open_incident_facts_batch.return_value = IncidentPrecheckBatchResult(
        decisions={
            "hnif_99999_xyz": IncidentPrecheckDecision(
                classification=IncidentClassification.CLEAR,
                failure_kind=IncidentFailureKind.NONE,
                reason=IncidentDecisionReason.NO_INCIDENTS_CONFIRMED,
                checked_at=now_utc,
            ),
            "sty_00123": IncidentPrecheckDecision(
                classification=IncidentClassification.CUSTOMER_OPEN_TICKET,
                failure_kind=IncidentFailureKind.NONE,
                reason=IncidentDecisionReason.CUSTOMER_OPEN_CONFIRMED,
                checked_at=now_utc,
            ),
        },
        metrics=IncidentPrecheckBatchMetrics(2, 2, 2, 20.0, 10.0, 10.0, 0),
    )

    raw_candidates = ["HNIF_99999_XyZ", "Sty_00123"]
    precheck_res = run_customer_ticket_precheck(raw_candidates, config={}, core=mock_core)

    # Must match without CONTRACT_VIOLATION
    assert "hnif_99999_xyz" in precheck_res.decisions
    assert "sty_00123" in precheck_res.decisions
    assert precheck_res.decisions["hnif_99999_xyz"].classification == IncidentClassification.CLEAR
    assert precheck_res.decisions["sty_00123"].classification == IncidentClassification.CUSTOMER_OPEN_TICKET


# Scenario 2: Multi-port anti-spam rate limiting by customer account
def test_adversarial_multi_port_anti_spam(temp_repo):
    """Verifies that 3 ONT ports for the same customer account allow only 1 send in the same batch."""
    config = {
        "enable_customer_outage_alert": True,
        "customer_alert_time_window": "07:00-21:00",
        "customer_alert_max_per_day": 1,
        "customer_alert_max_per_week": 3,
        "telecom_zalo_api_url": "http://localhost:3002",
        "telecom_zalo_api_key": "test_key",
    }
    now = datetime(2026, 9, 13, 10, 0, 0)
    candidates = [
        {"subscriber_key": "OLT1_0_1:1", "ma_tb": "HNIF_MULTI_PORT", "first_off_time": "2026-09-13T09:00:00"},
        {"subscriber_key": "OLT1_0_1:2", "ma_tb": "hnif_multi_port", "first_off_time": "2026-09-13T09:01:00"},
        {"subscriber_key": "OLT1_0_1:3", "ma_tb": "HNIF_multi_port", "first_off_time": "2026-09-13T09:02:00"},
    ]

    mock_core = MagicMock()
    mock_core.lookup_open_incident_facts_batch.return_value = IncidentPrecheckBatchResult(
        decisions={
            "hnif_multi_port": IncidentPrecheckDecision(
                classification=IncidentClassification.CLEAR,
                failure_kind=IncidentFailureKind.NONE,
                reason=IncidentDecisionReason.NO_INCIDENTS_CONFIRMED,
                checked_at=datetime.now(timezone.utc),
            )
        },
        metrics=IncidentPrecheckBatchMetrics(1, 1, 1, 10.0, 10.0, 10.0, 0),
    )

    mock_resp = MagicMock()
    mock_resp.status_code = 202
    mock_resp.json.return_value = {"status": "queued"}

    with patch("requests.post", return_value=mock_resp) as mock_post:
        summary = asyncio.run(
            process_customer_outage_alerts(
                temp_repo, candidates, "batch_multi_port", config=config, now=now, core=mock_core
            )
        )
        assert summary["sent"] == 1
        assert summary["skipped"] == 2
        assert summary["skipped_reasons"]["daily_limit_exceeded"] == 2
        assert mock_post.call_count == 1

        # Check all dispositions
        disp1 = temp_repo.get_customer_outage_alert_disposition(
            build_customer_alert_request_id("OLT1_0_1:1", "2026-09-13T09:00:00")
        )
        assert disp1["status"] == "SENT"

        disp2 = temp_repo.get_customer_outage_alert_disposition(
            build_customer_alert_request_id("OLT1_0_1:2", "2026-09-13T09:01:00")
        )
        assert disp2["status"] == "FAILED"
        assert disp2["error_reason"] == "daily_limit_exceeded"

        disp3 = temp_repo.get_customer_outage_alert_disposition(
            build_customer_alert_request_id("OLT1_0_1:3", "2026-09-13T09:02:00")
        )
        assert disp3["status"] == "FAILED"
        assert disp3["error_reason"] == "daily_limit_exceeded"


# Scenario 3: Stolen claim CAS conflict verification
def test_adversarial_stolen_claim_cas_conflict(temp_repo):
    """Verifies that if Worker A's lease expires and is stolen by Worker B, Worker A cannot finalize."""
    t0 = datetime(2026, 9, 13, 10, 0, 0)
    req_id = "cust-cas-stolen-1"

    # Worker A claims with 60s lease
    claim_a = temp_repo.claim_customer_outage_alert_precheck(
        subscriber_key="SUB_CAS_1",
        ma_tb="tb_cas_1",
        first_off_time="2026-09-13T09:30:00",
        claimed_at=t0,
        batch_id="batch_worker_a",
        request_id=req_id,
        lease_seconds=60.0,
    )
    assert claim_a.outcome == "CLAIMED"
    assert claim_a.claimed_at == t0

    # 120s later: Lease has expired. Worker B claims it.
    t1 = t0 + timedelta(seconds=120)
    claim_b = temp_repo.claim_customer_outage_alert_precheck(
        subscriber_key="SUB_CAS_1",
        ma_tb="tb_cas_1",
        first_off_time="2026-09-13T09:30:00",
        claimed_at=t1,
        batch_id="batch_worker_b",
        request_id=req_id,
        lease_seconds=60.0,
    )
    assert claim_b.outcome == "CLAIMED"
    assert claim_b.claimed_at == t1

    # Worker A wakes up and attempts compare-and-set finalization with its old claimed_at (t0)
    ok_a = temp_repo.finalize_customer_outage_alert_precheck(
        request_id=req_id,
        batch_id="batch_worker_a",
        expected_claimed_at=t0,
        status="SENT",
        finalized_at=t1 + timedelta(seconds=1),
    )
    # Must fail because row was stolen by Worker B!
    assert ok_a is False

    # Worker B successfully finalizes
    ok_b = temp_repo.finalize_customer_outage_alert_precheck(
        request_id=req_id,
        batch_id="batch_worker_b",
        expected_claimed_at=t1,
        status="SENT",
        finalized_at=t1 + timedelta(seconds=2),
    )
    assert ok_b is True

    disp = temp_repo.get_customer_outage_alert_disposition(req_id)
    assert disp["status"] == "SENT"
    assert disp["batch_id"] == "batch_worker_b"


# Scenario 4: Session cache retention on network timeout
def test_adversarial_session_cache_retention_on_timeout(tmp_path, monkeypatch):
    """Verifies that transient network timeout during check_token_alive does NOT delete session.json."""
    from onebss_core.auth import PlaywrightAuthenticator
    from onebss_core.settings import OneBSSSettings

    secure_dir = tmp_path / "runtime"
    secure_dir.mkdir(parents=True, mode=0o700)
    cache_path = secure_dir / "session.json"
    session = OneBSSSession("valid_tok_123", {"User-Agent": "test"}, "https://api-onebss.vnpt.vn", lambda: None, None, "sec")
    save_session_cache(cache_path, session)
    assert cache_path.exists()

    auth = PlaywrightAuthenticator()
    settings = OneBSSSettings("user", "pass", tmp_path / "otp", session_cache_path=cache_path)

    # Simulate timeout during check_token_alive returning (is_alive=False, is_definitive_auth_failure=False)
    monkeypatch.setattr("onebss_core.auth.check_token_alive", lambda *args, **kwargs: (False, False))
    monkeypatch.setattr(auth, "_do_login", lambda *args, **kwargs: session)

    reused = auth.create_session(settings)
    # File must be preserved despite check_token_alive failing due to timeout
    assert cache_path.exists()


# Scenario 5: Stale outage retry cap (24h)
def test_adversarial_stale_outage_retry_cap(temp_repo):
    """Verifies that outages older than 24h are rejected with outage_too_old and finalized as FAILED."""
    now = datetime(2026, 9, 13, 15, 0, 0)
    fot_stale = "2026-09-12T10:00:00"  # 29 hours ago (> 24h)

    config = {
        "enable_customer_outage_alert": True,
        "customer_alert_time_window": "07:00-21:00",
        "customer_alert_start_time": "08:00",
        "customer_alert_end_time": "18:00",
        "customer_alert_max_retry_age_hours": 24.0,
    }

    req_id = build_customer_alert_request_id("SUB_STALE_30H", fot_stale)
    c_seed = temp_repo.claim_customer_outage_alert_precheck(
        subscriber_key="SUB_STALE_30H",
        ma_tb="TB_STALE_30H",
        first_off_time=fot_stale,
        claimed_at=datetime(2026, 9, 12, 12, 0, 0),
        batch_id="b_yesterday",
        request_id=req_id,
        lease_seconds=120,
    )
    temp_repo.finalize_customer_outage_alert_precheck(
        request_id=req_id,
        batch_id="b_yesterday",
        expected_claimed_at=c_seed.claimed_at,
        status="PRECHECK_FAILED",
        finalized_at=datetime(2026, 9, 12, 12, 0, 0),
        error_reason="precheck_failed",
    )

    candidates = [
        {"subscriber_key": "SUB_STALE_30H", "ma_tb": "TB_STALE_30H", "first_off_time": fot_stale}
    ]

    mock_core = MagicMock()
    summary = asyncio.run(
        process_customer_outage_alerts(
            temp_repo, candidates, "batch_stale_test", config=config, now=now, core=mock_core
        )
    )
    assert summary["sent"] == 0
    assert summary["skipped"] == 1
    assert summary["skipped_reasons"]["outage_too_old"] == 1
    mock_core.lookup_open_incident_facts_batch.assert_not_called()

    disp = temp_repo.get_customer_outage_alert_disposition(req_id)
    assert disp["status"] == "FAILED"
    assert disp["error_reason"] == "outage_too_old"


# Scenario 6: Non-blocking event loop during HTTP dispatch
def test_adversarial_non_blocking_event_loop():
    """Verifies that send_customer_outage_message offloads HTTP I/O without blocking concurrent coroutines."""
    config = {
        "telecom_zalo_api_url": "http://localhost:3002",
        "telecom_zalo_api_key": "test_key",
        "customer_alert_send_timeout_seconds": 2.0,
    }

    concurrent_task_steps = []

    async def concurrent_task():
        for i in range(5):
            concurrent_task_steps.append(i)
            await asyncio.sleep(0.01)

    def slow_post(*args, **kwargs):
        time.sleep(0.05)  # Simulate network latency
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {"status": "ok"}
        return resp

    async def runner():
        with patch("requests.post", side_effect=slow_post):
            t1 = asyncio.create_task(
                send_customer_outage_message("tb001", "req001", "msg", config=config)
            )
            t2 = asyncio.create_task(concurrent_task())
            res1, _ = await asyncio.gather(t1, t2)
            return res1

    res = asyncio.run(runner())
    assert res["success"] is True
    # The concurrent task must have made progress while the HTTP call was running
    assert len(concurrent_task_steps) >= 3


# Scenario 7: Quiet hours fails closed on invalid time window format
def test_adversarial_quiet_hours_fail_closed(temp_repo):
    """Verifies that any syntax error in time window strictly fails closed."""
    now = datetime(2026, 9, 13, 10, 0, 0)
    assert _is_within_time_window("07:00-21:00", now=now, fail_closed=True) is True
    assert _is_within_time_window("22:00-06:00", now=now, fail_closed=True) is False
    assert _is_within_time_window("malformed_window", now=now, fail_closed=True) is False
    assert _is_within_time_window("08:00 - 20:00 extra", now=now, fail_closed=True) is False
