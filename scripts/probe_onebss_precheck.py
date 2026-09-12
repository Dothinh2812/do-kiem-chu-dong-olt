#!/usr/bin/env python3
"""Non-sending OneBSS precheck probe.

Invokes OneBSSCore.lookup_open_incident_facts_batch() and prints ONLY:
- Labeled fixture outcomes: classification, failure_kind, reason, checked_at
- Aggregate counts and durations
Never prints canonical keys, raw values, or per-incident facts.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Dict

# Dual import / sys.path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

try:
    from onebss_core import OneBSSCore
    from onebss_precheck import canonicalize_ma_tb
except ImportError as exc:
    print(f"Import error: {exc}", file=sys.stderr)
    sys.exit(1)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Non-sending OneBSS incident precheck probe for operator fixture verification."
    )
    parser.add_argument(
        "--fixture",
        action="append",
        nargs=2,
        metavar=("LABEL", "MA_TB"),
        help="Labeled fixture, e.g. --fixture OPEN_CUSTOMER <ma_tb>",
    )
    parser.add_argument(
        "--fixtures-json",
        help="JSON file mapping label -> MA_TB, e.g. {'OPEN_CUSTOMER': '...', ...}",
    )
    parser.add_argument(
        "--request-timeout",
        type=float,
        default=2.5,
        help="Request timeout in seconds",
    )
    parser.add_argument(
        "--batch-timeout",
        type=float,
        default=8.0,
        help="Batch timeout in seconds",
    )
    parser.add_argument(
        "--max-workers",
        type=int,
        default=4,
        help="Maximum concurrent workers",
    )

    args = parser.parse_args(argv)

    fixtures: Dict[str, str] = {}
    if args.fixtures_json:
        path = Path(args.fixtures_json)
        if not path.is_file():
            print(f"Error: fixtures file not found: {path}", file=sys.stderr)
            return 1
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
            if not isinstance(data, dict):
                print("Error: fixtures-json must be a JSON object mapping label -> ma_tb", file=sys.stderr)
                return 1
            for label, ma_tb in data.items():
                fixtures[str(label)] = str(ma_tb)

    if args.fixture:
        for label, ma_tb in args.fixture:
            fixtures[str(label)] = str(ma_tb)

    if not fixtures:
        print("Error: No fixtures provided. Use --fixture LABEL MA_TB or --fixtures-json FILE", file=sys.stderr)
        return 1

    key_to_labels: Dict[str, list[str]] = {}
    canonical_keys: list[str] = []
    for label, raw_ma_tb in fixtures.items():
        c_key = canonicalize_ma_tb(raw_ma_tb)
        if not c_key:
            print(f"Error: Invalid MA_TB for fixture '{label}'", file=sys.stderr)
            return 1
        if c_key not in key_to_labels:
            key_to_labels[c_key] = []
            canonical_keys.append(c_key)
        key_to_labels[c_key].append(label)

    try:
        core = OneBSSCore.from_env()
    except Exception as exc:
        print(f"Failed to initialize OneBSSCore: {type(exc).__name__}", file=sys.stderr)
        return 1

    try:
        result = core.lookup_open_incident_facts_batch(
            canonical_keys,
            request_timeout_seconds=args.request_timeout,
            batch_timeout_seconds=args.batch_timeout,
            max_workers=args.max_workers,
        )
    except Exception as exc:
        print(f"Batch precheck failed: {type(exc).__name__}", file=sys.stderr)
        return 1

    print("=== ONEBSS INCIDENT PRECHECK PROBE RESULTS ===")
    print("Precheck state: ENFORCED")
    print(f"Requested keys: {result.metrics.requested_key_count}")
    print(f"Completed keys: {result.metrics.completed_key_count}")
    print(f"Batch duration: {result.metrics.batch_duration_ms:.1f}ms")
    print()

    for c_key in canonical_keys:
        decision = result.decisions.get(c_key)
        labels = key_to_labels[c_key]
        for label in labels:
            if decision:
                print(f"[{label}]")
                print(f"  Classification: {decision.classification.value}")
                print(f"  Failure Kind:   {decision.failure_kind.value}")
                print(f"  Reason:         {decision.reason.value}")
                print(f"  Checked At:     {decision.checked_at.isoformat()}")
            else:
                print(f"[{label}]")
                print("  Error: Missing decision in result")
            print()

    classification_counts: Dict[str, int] = {}
    failure_kind_counts: Dict[str, int] = {}
    for d in result.decisions.values():
        classification_counts[d.classification.value] = classification_counts.get(d.classification.value, 0) + 1
        failure_kind_counts[d.failure_kind.value] = failure_kind_counts.get(d.failure_kind.value, 0) + 1

    print("=== AGGREGATE SUMMARY ===")
    print(f"Classification counts: {json.dumps(classification_counts)}")
    print(f"Failure kind counts:   {json.dumps(failure_kind_counts)}")
    print(f"Requests:              {result.metrics.request_count}")
    print(f"Request duration max:  {result.metrics.request_duration_ms_max:.1f}ms")
    print(f"Deadline expired:      {result.metrics.deadline_expired_count}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
