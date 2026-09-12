# -*- coding: utf-8 -*-
import json
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest

from onebss_core import (
    IncidentClassification,
    IncidentDecisionReason,
    IncidentFailureKind,
    IncidentPrecheckBatchMetrics,
    IncidentPrecheckBatchResult,
    IncidentPrecheckDecision,
    OneBSSAmbiguousError,
    OneBSSApiError,
    OneBSSAuthError,
    OneBSSBatchTimeoutError,
    OneBSSContractError,
    OneBSSPartialError,
    OneBSSRequestTimeoutError,
)

try:
    from do_chu_dong_api.onebss_precheck import (
        SENDABLE_CLEAR_REASONS,
        canonicalize_ma_tb,
        run_customer_ticket_precheck,
    )
except ModuleNotFoundError:
    from onebss_precheck import (
        SENDABLE_CLEAR_REASONS,
        canonicalize_ma_tb,
        run_customer_ticket_precheck,
    )


def test_canonicalize_ma_tb():
    assert canonicalize_ma_tb("  TB001  ") == "TB001"
    assert canonicalize_ma_tb("") == ""
    assert canonicalize_ma_tb(None) == ""


def test_run_customer_ticket_precheck_empty_keys():
    res = run_customer_ticket_precheck([], config={})
    assert len(res.decisions) == 0
    assert res.metrics.requested_key_count == 0

    res2 = run_customer_ticket_precheck(["   ", ""], config={})
    assert len(res2.decisions) == 0
    assert res2.metrics.requested_key_count == 0


def test_run_customer_ticket_precheck_valid_core():
    mock_core = MagicMock()
    now_utc = datetime.now(timezone.utc)
    mock_core.lookup_open_incident_facts_batch.return_value = IncidentPrecheckBatchResult(
        decisions={
            "TB001": IncidentPrecheckDecision(
                classification=IncidentClassification.CUSTOMER_OPEN_TICKET,
                failure_kind=IncidentFailureKind.NONE,
                reason=IncidentDecisionReason.CUSTOMER_OPEN_CONFIRMED,
                checked_at=now_utc,
            ),
            "TB002": IncidentPrecheckDecision(
                classification=IncidentClassification.CLEAR,
                failure_kind=IncidentFailureKind.NONE,
                reason=IncidentDecisionReason.ALL_INCIDENTS_TERMINAL,
                checked_at=now_utc,
            ),
        },
        metrics=IncidentPrecheckBatchMetrics(
            requested_key_count=2,
            completed_key_count=2,
            request_count=2,
            request_duration_ms_total=50.0,
            request_duration_ms_max=30.0,
            batch_duration_ms=60.0,
            deadline_expired_count=0,
        ),
    )

    res = run_customer_ticket_precheck([" TB001 ", "TB002"], config={}, core=mock_core)
    assert len(res.decisions) == 2
    assert res.decisions["TB001"].classification == IncidentClassification.CUSTOMER_OPEN_TICKET
    assert res.decisions["TB001"].failure_kind == IncidentFailureKind.NONE
    assert res.decisions["TB002"].classification == IncidentClassification.CLEAR
    assert res.decisions["TB002"].reason == IncidentDecisionReason.ALL_INCIDENTS_TERMINAL


def test_run_customer_ticket_precheck_extra_key_invalidates_all():
    mock_core = MagicMock()
    now_utc = datetime.now(timezone.utc)
    mock_core.lookup_open_incident_facts_batch.return_value = IncidentPrecheckBatchResult(
        decisions={
            "TB001": IncidentPrecheckDecision(
                classification=IncidentClassification.CLEAR,
                failure_kind=IncidentFailureKind.NONE,
                reason=IncidentDecisionReason.NO_INCIDENTS_CONFIRMED,
                checked_at=now_utc,
            ),
            "EXTRA_KEY": IncidentPrecheckDecision(
                classification=IncidentClassification.CLEAR,
                failure_kind=IncidentFailureKind.NONE,
                reason=IncidentDecisionReason.NO_INCIDENTS_CONFIRMED,
                checked_at=now_utc,
            ),
        },
        metrics=IncidentPrecheckBatchMetrics(1, 1, 1, 10.0, 10.0, 10.0, 0),
    )

    res = run_customer_ticket_precheck(["TB001"], config={}, core=mock_core)
    assert len(res.decisions) == 1
    assert "TB001" in res.decisions
    # Must be invalidated to CONTRACT failure
    assert res.decisions["TB001"].classification == IncidentClassification.INDETERMINATE
    assert res.decisions["TB001"].failure_kind == IncidentFailureKind.CONTRACT
    assert res.decisions["TB001"].reason == IncidentDecisionReason.CONTRACT_VIOLATION


def test_run_customer_ticket_precheck_missing_key_filled():
    mock_core = MagicMock()
    now_utc = datetime.now(timezone.utc)
    mock_core.lookup_open_incident_facts_batch.return_value = IncidentPrecheckBatchResult(
        decisions={
            "TB001": IncidentPrecheckDecision(
                classification=IncidentClassification.CLEAR,
                failure_kind=IncidentFailureKind.NONE,
                reason=IncidentDecisionReason.NO_INCIDENTS_CONFIRMED,
                checked_at=now_utc,
            ),
        },
        metrics=IncidentPrecheckBatchMetrics(2, 1, 1, 10.0, 10.0, 10.0, 0),
    )

    res = run_customer_ticket_precheck(["TB001", "TB002"], config={}, core=mock_core)
    assert len(res.decisions) == 2
    assert res.decisions["TB001"].classification == IncidentClassification.CLEAR
    assert res.decisions["TB002"].classification == IncidentClassification.INDETERMINATE
    assert res.decisions["TB002"].failure_kind == IncidentFailureKind.CONTRACT
    assert res.decisions["TB002"].reason == IncidentDecisionReason.CONTRACT_VIOLATION


def test_run_customer_ticket_precheck_malformed_decision_replaced():
    mock_core = MagicMock()
    now_utc = datetime.now(timezone.utc)
    # Malformed enum pairing: CLEAR with AUTH
    bad_decision = IncidentPrecheckDecision(
        classification=IncidentClassification.CLEAR,
        failure_kind=IncidentFailureKind.AUTH,
        reason=IncidentDecisionReason.AUTH_UNAVAILABLE,
        checked_at=now_utc,
    )
    mock_core.lookup_open_incident_facts_batch.return_value = IncidentPrecheckBatchResult(
        decisions={"TB001": bad_decision},
        metrics=IncidentPrecheckBatchMetrics(1, 1, 1, 10.0, 10.0, 10.0, 0),
    )

    res = run_customer_ticket_precheck(["TB001"], config={}, core=mock_core)
    assert res.decisions["TB001"].classification == IncidentClassification.INDETERMINATE
    assert res.decisions["TB001"].failure_kind == IncidentFailureKind.CONTRACT
    assert res.decisions["TB001"].reason == IncidentDecisionReason.CONTRACT_VIOLATION


def test_run_customer_ticket_precheck_future_checked_at_replaced():
    mock_core = MagicMock()
    future_utc = datetime.now(timezone.utc) + timedelta(hours=2)
    bad_decision = IncidentPrecheckDecision(
        classification=IncidentClassification.CLEAR,
        failure_kind=IncidentFailureKind.NONE,
        reason=IncidentDecisionReason.NO_INCIDENTS_CONFIRMED,
        checked_at=future_utc,
    )
    mock_core.lookup_open_incident_facts_batch.return_value = IncidentPrecheckBatchResult(
        decisions={"TB001": bad_decision},
        metrics=IncidentPrecheckBatchMetrics(1, 1, 1, 10.0, 10.0, 10.0, 0),
    )

    res = run_customer_ticket_precheck(["TB001"], config={}, core=mock_core)
    assert res.decisions["TB001"].classification == IncidentClassification.INDETERMINATE
    assert res.decisions["TB001"].failure_kind == IncidentFailureKind.CONTRACT


def test_run_customer_ticket_precheck_typed_errors():
    mock_core = MagicMock()

    # Auth error
    mock_core.lookup_open_incident_facts_batch.side_effect = OneBSSAuthError()
    res = run_customer_ticket_precheck(["TB001"], config={}, core=mock_core)
    assert res.decisions["TB001"].failure_kind == IncidentFailureKind.AUTH
    assert res.decisions["TB001"].reason == IncidentDecisionReason.AUTH_UNAVAILABLE

    # Request timeout
    mock_core.lookup_open_incident_facts_batch.side_effect = OneBSSRequestTimeoutError()
    res = run_customer_ticket_precheck(["TB001"], config={}, core=mock_core)
    assert res.decisions["TB001"].failure_kind == IncidentFailureKind.REQUEST_TIMEOUT
    assert res.decisions["TB001"].reason == IncidentDecisionReason.REQUEST_DEADLINE_EXCEEDED
    assert res.metrics.deadline_expired_count == 1

    # Batch timeout
    mock_core.lookup_open_incident_facts_batch.side_effect = OneBSSBatchTimeoutError()
    res = run_customer_ticket_precheck(["TB001"], config={}, core=mock_core)
    assert res.decisions["TB001"].failure_kind == IncidentFailureKind.BATCH_TIMEOUT
    assert res.decisions["TB001"].reason == IncidentDecisionReason.BATCH_DEADLINE_EXCEEDED

    # Api error
    mock_core.lookup_open_incident_facts_batch.side_effect = OneBSSApiError()
    res = run_customer_ticket_precheck(["TB001"], config={}, core=mock_core)
    assert res.decisions["TB001"].failure_kind == IncidentFailureKind.API
    assert res.decisions["TB001"].reason == IncidentDecisionReason.API_UNAVAILABLE

    # Partial error
    mock_core.lookup_open_incident_facts_batch.side_effect = OneBSSPartialError()
    res = run_customer_ticket_precheck(["TB001"], config={}, core=mock_core)
    assert res.decisions["TB001"].failure_kind == IncidentFailureKind.PARTIAL
    assert res.decisions["TB001"].reason == IncidentDecisionReason.PARTIAL_EVIDENCE

    # Ambiguous error
    mock_core.lookup_open_incident_facts_batch.side_effect = OneBSSAmbiguousError()
    res = run_customer_ticket_precheck(["TB001"], config={}, core=mock_core)
    assert res.decisions["TB001"].failure_kind == IncidentFailureKind.AMBIGUOUS
    assert res.decisions["TB001"].reason == IncidentDecisionReason.AMBIGUOUS_EVIDENCE

    # Contract error
    mock_core.lookup_open_incident_facts_batch.side_effect = OneBSSContractError()
    res = run_customer_ticket_precheck(["TB001"], config={}, core=mock_core)
    assert res.decisions["TB001"].failure_kind == IncidentFailureKind.CONTRACT
    assert res.decisions["TB001"].reason == IncidentDecisionReason.CONTRACT_VIOLATION

    # Unexpected exception with secret string
    mock_core.lookup_open_incident_facts_batch.side_effect = RuntimeError("secret_token_12345")
    res = run_customer_ticket_precheck(["TB001"], config={}, core=mock_core)
    assert res.decisions["TB001"].failure_kind == IncidentFailureKind.CONTRACT
    assert res.decisions["TB001"].reason == IncidentDecisionReason.CONTRACT_VIOLATION
    assert "secret_token_12345" not in repr(res)


def test_run_customer_ticket_precheck_non_dict_decisions_fails_closed():
    mock_core = MagicMock()
    # Mock returning an object where decisions is None or list, not dict
    fake_result = MagicMock()
    fake_result.decisions = None
    fake_result.metrics = IncidentPrecheckBatchMetrics(1, 0, 0, 0.0, 0.0, 0.0, 0)
    mock_core.lookup_open_incident_facts_batch.return_value = fake_result

    res = run_customer_ticket_precheck(["TB001"], config={}, core=mock_core)
    assert len(res.decisions) == 1
    assert res.decisions["TB001"].classification == IncidentClassification.INDETERMINATE
    assert res.decisions["TB001"].failure_kind == IncidentFailureKind.CONTRACT
    assert res.decisions["TB001"].reason == IncidentDecisionReason.CONTRACT_VIOLATION


def test_normalize_precheck_config_non_numeric_strings():
    try:
        from do_chu_dong_api.notification_service import _normalize_customer_ticket_precheck_config
    except ModuleNotFoundError:
        from notification_service import _normalize_customer_ticket_precheck_config

    bad_config = {
        "customer_ticket_precheck_request_timeout_seconds": "invalid_five",
        "customer_ticket_precheck_batch_timeout_seconds": "bad_thirty",
        "customer_ticket_precheck_max_workers": "four_workers",
        "customer_ticket_precheck_max_fact_age_seconds": "sixty_sec",
        "customer_ticket_precheck_claim_lease_seconds": "not_a_number",
    }
    _normalize_customer_ticket_precheck_config(bad_config, warn=False)
    assert bad_config["customer_ticket_precheck_request_timeout_seconds"] == 5.0
    assert bad_config["customer_ticket_precheck_batch_timeout_seconds"] == 30.0
    assert bad_config["customer_ticket_precheck_max_workers"] == 4
    assert bad_config["customer_ticket_precheck_max_fact_age_seconds"] == 60.0
    # Lease must cover at least 30 + 5 + 60 + 3 = 98.0
    assert bad_config["customer_ticket_precheck_claim_lease_seconds"] >= 98.0


def test_probe_onebss_precheck_script(tmp_path, monkeypatch, capsys):
    from scripts.probe_onebss_precheck import main as probe_main
    mock_core = MagicMock()
    now_utc = datetime.now(timezone.utc)
    mock_core.lookup_open_incident_facts_batch.return_value = IncidentPrecheckBatchResult(
        decisions={
            "TB_OPEN": IncidentPrecheckDecision(
                classification=IncidentClassification.CUSTOMER_OPEN_TICKET,
                failure_kind=IncidentFailureKind.NONE,
                reason=IncidentDecisionReason.CUSTOMER_OPEN_CONFIRMED,
                checked_at=now_utc,
            ),
            "TB_CLEAR": IncidentPrecheckDecision(
                classification=IncidentClassification.CLEAR,
                failure_kind=IncidentFailureKind.NONE,
                reason=IncidentDecisionReason.NO_INCIDENTS_CONFIRMED,
                checked_at=now_utc,
            ),
        },
        metrics=IncidentPrecheckBatchMetrics(
            requested_key_count=2,
            completed_key_count=2,
            request_count=2,
            request_duration_ms_total=120.0,
            request_duration_ms_max=70.0,
            batch_duration_ms=80.0,
            deadline_expired_count=0,
        ),
    )

    monkeypatch.setattr("scripts.probe_onebss_precheck.OneBSSCore.from_env", lambda: mock_core)

    # Test via CLI arguments
    ret = probe_main(["--fixture", "OPEN_CUSTOMER", "TB_OPEN", "--fixture", "NO_TICKET", "TB_CLEAR"])
    assert ret == 0
    captured = capsys.readouterr().out
    assert "[OPEN_CUSTOMER]" in captured
    assert "CUSTOMER_OPEN_TICKET" in captured
    assert "[NO_TICKET]" in captured
    assert "CLEAR" in captured
    assert "TB_OPEN" not in captured
    assert "TB_CLEAR" not in captured
    assert "=== AGGREGATE SUMMARY ===" in captured

    # Test via JSON file
    fixtures_file = tmp_path / "fixtures.json"
    fixtures_file.write_text(json.dumps({"CLOSED_CUSTOMER": "TB_CLEAR"}), encoding="utf-8")
    ret2 = probe_main(["--fixtures-json", str(fixtures_file)])
    assert ret2 == 0
    captured2 = capsys.readouterr().out
    assert "[CLOSED_CUSTOMER]" in captured2
    assert "CLEAR" in captured2
    assert "TB_CLEAR" not in captured2

