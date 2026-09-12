# -*- coding: utf-8 -*-
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

try:
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
        OneBSSCore,
        OneBSSCoreError,
        OneBSSPartialError,
        OneBSSRequestTimeoutError,
    )
except ImportError:
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
        OneBSSCore,
        OneBSSCoreError,
        OneBSSPartialError,
        OneBSSRequestTimeoutError,
    )


SENDABLE_CLEAR_REASONS = {
    IncidentDecisionReason.NO_INCIDENTS_CONFIRMED,
    IncidentDecisionReason.ALL_INCIDENTS_TERMINAL,
    IncidentDecisionReason.OPEN_INCIDENTS_PROACTIVE_ONLY,
}


def canonicalize_ma_tb(ma_tb: Any) -> str:
    return str(ma_tb or "").strip()


def _make_contract_decision(checked_at: Optional[datetime] = None) -> IncidentPrecheckDecision:
    return IncidentPrecheckDecision(
        classification=IncidentClassification.INDETERMINATE,
        failure_kind=IncidentFailureKind.CONTRACT,
        reason=IncidentDecisionReason.CONTRACT_VIOLATION,
        checked_at=checked_at or datetime.now(timezone.utc),
    )


def _is_valid_decision(decision: Any, now_utc: datetime) -> bool:
    if not isinstance(decision, IncidentPrecheckDecision):
        return False
    if decision.classification not in IncidentClassification:
        return False
    if decision.failure_kind not in IncidentFailureKind:
        return False
    if decision.reason not in IncidentDecisionReason:
        return False

    # Enum pairings
    if decision.classification == IncidentClassification.CUSTOMER_OPEN_TICKET:
        if decision.failure_kind != IncidentFailureKind.NONE:
            return False
        if decision.reason != IncidentDecisionReason.CUSTOMER_OPEN_CONFIRMED:
            return False
    elif decision.classification == IncidentClassification.CLEAR:
        if decision.failure_kind != IncidentFailureKind.NONE:
            return False
        if decision.reason not in SENDABLE_CLEAR_REASONS:
            return False
    elif decision.classification == IncidentClassification.INDETERMINATE:
        if decision.failure_kind == IncidentFailureKind.NONE:
            return False
        kind = decision.failure_kind
        reason = decision.reason
        if kind == IncidentFailureKind.AUTH and reason != IncidentDecisionReason.AUTH_UNAVAILABLE:
            return False
        elif kind == IncidentFailureKind.API and reason != IncidentDecisionReason.API_UNAVAILABLE:
            return False
        elif kind == IncidentFailureKind.REQUEST_TIMEOUT and reason != IncidentDecisionReason.REQUEST_DEADLINE_EXCEEDED:
            return False
        elif kind == IncidentFailureKind.BATCH_TIMEOUT and reason != IncidentDecisionReason.BATCH_DEADLINE_EXCEEDED:
            return False
        elif kind == IncidentFailureKind.PARTIAL and reason != IncidentDecisionReason.PARTIAL_EVIDENCE:
            return False
        elif kind == IncidentFailureKind.AMBIGUOUS and reason != IncidentDecisionReason.AMBIGUOUS_EVIDENCE:
            return False
        elif kind == IncidentFailureKind.CONTRACT and reason != IncidentDecisionReason.CONTRACT_VIOLATION:
            return False
        elif kind not in IncidentFailureKind or kind == IncidentFailureKind.NONE:
            return False
    else:
        return False

    # Datetime checks
    if not isinstance(decision.checked_at, datetime):
        return False
    if decision.checked_at.tzinfo is None:
        return False
    if decision.checked_at > now_utc + timedelta(seconds=5):
        return False

    return True


def run_customer_ticket_precheck(
    ma_tb_values: List[str],
    config: Optional[Dict] = None,
    core: Any = None,
) -> IncidentPrecheckBatchResult:
    cfg = config or {}
    clean_keys: List[str] = []
    for raw_k in ma_tb_values:
        k = canonicalize_ma_tb(raw_k)
        if k and k not in clean_keys:
            clean_keys.append(k)

    if not clean_keys:
        return IncidentPrecheckBatchResult(
            decisions={},
            metrics=IncidentPrecheckBatchMetrics(
                requested_key_count=0,
                completed_key_count=0,
                request_count=0,
                request_duration_ms_total=0.0,
                request_duration_ms_max=0.0,
                batch_duration_ms=0.0,
                deadline_expired_count=0,
            ),
        )

    req_timeout = float(cfg.get("customer_ticket_precheck_request_timeout_seconds") or 5.0)
    batch_timeout = float(cfg.get("customer_ticket_precheck_batch_timeout_seconds") or 30.0)
    max_workers = int(cfg.get("customer_ticket_precheck_max_workers") or 4)

    active_core = core
    if active_core is None:
        try:
            active_core = OneBSSCore.from_env()
        except Exception:
            now_utc = datetime.now(timezone.utc)
            decisions = {k: _make_contract_decision(now_utc) for k in clean_keys}
            return IncidentPrecheckBatchResult(
                decisions=decisions,
                metrics=IncidentPrecheckBatchMetrics(
                    requested_key_count=len(clean_keys),
                    completed_key_count=0,
                    request_count=0,
                    request_duration_ms_total=0.0,
                    request_duration_ms_max=0.0,
                    batch_duration_ms=0.0,
                    deadline_expired_count=0,
                ),
            )

    try:
        raw_result = active_core.lookup_open_incident_facts_batch(
            clean_keys,
            request_timeout_seconds=req_timeout,
            batch_timeout_seconds=batch_timeout,
            max_workers=max_workers,
        )
    except OneBSSAuthError:
        now_utc = datetime.now(timezone.utc)
        decisions = {
            k: IncidentPrecheckDecision(
                classification=IncidentClassification.INDETERMINATE,
                failure_kind=IncidentFailureKind.AUTH,
                reason=IncidentDecisionReason.AUTH_UNAVAILABLE,
                checked_at=now_utc,
            )
            for k in clean_keys
        }
        return IncidentPrecheckBatchResult(
            decisions=decisions,
            metrics=IncidentPrecheckBatchMetrics(
                requested_key_count=len(clean_keys),
                completed_key_count=0,
                request_count=0,
                request_duration_ms_total=0.0,
                request_duration_ms_max=0.0,
                batch_duration_ms=0.0,
                deadline_expired_count=0,
            ),
        )
    except OneBSSRequestTimeoutError:
        now_utc = datetime.now(timezone.utc)
        decisions = {
            k: IncidentPrecheckDecision(
                classification=IncidentClassification.INDETERMINATE,
                failure_kind=IncidentFailureKind.REQUEST_TIMEOUT,
                reason=IncidentDecisionReason.REQUEST_DEADLINE_EXCEEDED,
                checked_at=now_utc,
            )
            for k in clean_keys
        }
        return IncidentPrecheckBatchResult(
            decisions=decisions,
            metrics=IncidentPrecheckBatchMetrics(
                requested_key_count=len(clean_keys),
                completed_key_count=0,
                request_count=0,
                request_duration_ms_total=0.0,
                request_duration_ms_max=0.0,
                batch_duration_ms=0.0,
                deadline_expired_count=len(clean_keys),
            ),
        )
    except OneBSSBatchTimeoutError:
        now_utc = datetime.now(timezone.utc)
        decisions = {
            k: IncidentPrecheckDecision(
                classification=IncidentClassification.INDETERMINATE,
                failure_kind=IncidentFailureKind.BATCH_TIMEOUT,
                reason=IncidentDecisionReason.BATCH_DEADLINE_EXCEEDED,
                checked_at=now_utc,
            )
            for k in clean_keys
        }
        return IncidentPrecheckBatchResult(
            decisions=decisions,
            metrics=IncidentPrecheckBatchMetrics(
                requested_key_count=len(clean_keys),
                completed_key_count=0,
                request_count=0,
                request_duration_ms_total=0.0,
                request_duration_ms_max=0.0,
                batch_duration_ms=0.0,
                deadline_expired_count=len(clean_keys),
            ),
        )
    except OneBSSApiError:
        now_utc = datetime.now(timezone.utc)
        decisions = {
            k: IncidentPrecheckDecision(
                classification=IncidentClassification.INDETERMINATE,
                failure_kind=IncidentFailureKind.API,
                reason=IncidentDecisionReason.API_UNAVAILABLE,
                checked_at=now_utc,
            )
            for k in clean_keys
        }
        return IncidentPrecheckBatchResult(
            decisions=decisions,
            metrics=IncidentPrecheckBatchMetrics(
                requested_key_count=len(clean_keys),
                completed_key_count=0,
                request_count=0,
                request_duration_ms_total=0.0,
                request_duration_ms_max=0.0,
                batch_duration_ms=0.0,
                deadline_expired_count=0,
            ),
        )
    except OneBSSPartialError:
        now_utc = datetime.now(timezone.utc)
        decisions = {
            k: IncidentPrecheckDecision(
                classification=IncidentClassification.INDETERMINATE,
                failure_kind=IncidentFailureKind.PARTIAL,
                reason=IncidentDecisionReason.PARTIAL_EVIDENCE,
                checked_at=now_utc,
            )
            for k in clean_keys
        }
        return IncidentPrecheckBatchResult(
            decisions=decisions,
            metrics=IncidentPrecheckBatchMetrics(
                requested_key_count=len(clean_keys),
                completed_key_count=0,
                request_count=0,
                request_duration_ms_total=0.0,
                request_duration_ms_max=0.0,
                batch_duration_ms=0.0,
                deadline_expired_count=0,
            ),
        )
    except OneBSSAmbiguousError:
        now_utc = datetime.now(timezone.utc)
        decisions = {
            k: IncidentPrecheckDecision(
                classification=IncidentClassification.INDETERMINATE,
                failure_kind=IncidentFailureKind.AMBIGUOUS,
                reason=IncidentDecisionReason.AMBIGUOUS_EVIDENCE,
                checked_at=now_utc,
            )
            for k in clean_keys
        }
        return IncidentPrecheckBatchResult(
            decisions=decisions,
            metrics=IncidentPrecheckBatchMetrics(
                requested_key_count=len(clean_keys),
                completed_key_count=0,
                request_count=0,
                request_duration_ms_total=0.0,
                request_duration_ms_max=0.0,
                batch_duration_ms=0.0,
                deadline_expired_count=0,
            ),
        )
    except (OneBSSContractError, OneBSSCoreError):
        now_utc = datetime.now(timezone.utc)
        decisions = {k: _make_contract_decision(now_utc) for k in clean_keys}
        return IncidentPrecheckBatchResult(
            decisions=decisions,
            metrics=IncidentPrecheckBatchMetrics(
                requested_key_count=len(clean_keys),
                completed_key_count=0,
                request_count=0,
                request_duration_ms_total=0.0,
                request_duration_ms_max=0.0,
                batch_duration_ms=0.0,
                deadline_expired_count=0,
            ),
        )
    except Exception:
        now_utc = datetime.now(timezone.utc)
        decisions = {k: _make_contract_decision(now_utc) for k in clean_keys}
        return IncidentPrecheckBatchResult(
            decisions=decisions,
            metrics=IncidentPrecheckBatchMetrics(
                requested_key_count=len(clean_keys),
                completed_key_count=0,
                request_count=0,
                request_duration_ms_total=0.0,
                request_duration_ms_max=0.0,
                batch_duration_ms=0.0,
                deadline_expired_count=0,
            ),
        )

    # Hostile validation of raw_result
    now_utc = datetime.now(timezone.utc)
    safe_metrics = (
        raw_result.metrics
        if isinstance(getattr(raw_result, "metrics", None), IncidentPrecheckBatchMetrics)
        else IncidentPrecheckBatchMetrics(
            requested_key_count=len(clean_keys),
            completed_key_count=0,
            request_count=0,
            request_duration_ms_total=0.0,
            request_duration_ms_max=0.0,
            batch_duration_ms=0.0,
            deadline_expired_count=0,
        )
    )

    if not isinstance(raw_result, IncidentPrecheckBatchResult) or not hasattr(raw_result, "decisions"):
        decisions = {k: _make_contract_decision(now_utc) for k in clean_keys}
        return IncidentPrecheckBatchResult(decisions=decisions, metrics=safe_metrics)

    raw_decisions = raw_result.decisions
    if not isinstance(raw_decisions, dict):
        decisions = {k: _make_contract_decision(now_utc) for k in clean_keys}
        return IncidentPrecheckBatchResult(decisions=decisions, metrics=safe_metrics)

    requested_set = set(clean_keys)
    returned_keys = set(raw_decisions.keys())

    # Any extra key invalidates all requested decisions as contract failures
    extra_keys = returned_keys - requested_set
    if extra_keys:
        decisions = {k: _make_contract_decision(now_utc) for k in clean_keys}
        return IncidentPrecheckBatchResult(decisions=decisions, metrics=safe_metrics)

    # Any non-canonical key in returned keys invalidates all
    for ret_k in returned_keys:
        if not isinstance(ret_k, str) or ret_k != canonicalize_ma_tb(ret_k):
            decisions = {k: _make_contract_decision(now_utc) for k in clean_keys}
            return IncidentPrecheckBatchResult(decisions=decisions, metrics=safe_metrics)

    # Validate each requested key
    validated_decisions: Dict[str, IncidentPrecheckDecision] = {}
    for k in clean_keys:
        if k not in raw_decisions:
            validated_decisions[k] = _make_contract_decision(now_utc)
        else:
            decision = raw_decisions[k]
            if not _is_valid_decision(decision, now_utc):
                validated_decisions[k] = _make_contract_decision(now_utc)
            else:
                validated_decisions[k] = decision

    return IncidentPrecheckBatchResult(decisions=validated_decisions, metrics=safe_metrics)
