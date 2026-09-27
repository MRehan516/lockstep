#!/usr/bin/env python3
"""
Lockstep — Benchmark runner (FR2.1).

Runs all labeled fixture files through the full detector pipeline and
measures precision and recall against their `expected_conflicts` labels.

Usage:
    python benchmarks/run_benchmarks.py

Exits with code 0 if precision = recall = 1.0, non-zero otherwise.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile

# Add workspace root to path so lockstep package is importable
_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _root not in sys.path:
    sys.path.insert(0, _root)

from lockstep.ingester import ingest_plan
from lockstep.detector import detect_conflicts

FIXTURES_DIR = os.path.join(os.path.dirname(__file__), "fixtures")
REPO_ROOT = _root

# ---------------------------------------------------------------------------
# Fixture categories and expected labels
# ---------------------------------------------------------------------------

FIXTURES = [
    "section10_plan.json",       # true conflict
    "looks_risky_but_safe.json", # no conflict (read-read)
    "resource_conflict.json",    # resource conflict
    "no_relation.json",          # no conflict (unrelated symbols)
]


def run_fixture(fixture_path: str) -> dict:
    """Run the detector on a single fixture and return results."""
    with open(fixture_path) as f:
        plan = json.load(f)

    expected = plan.get("expected_conflicts", 0)

    # Fresh temp DB per fixture (fully isolated)
    tmp = tempfile.mktemp(suffix=".db")
    try:
        ingest_plan(plan, tmp)
        conflicts = detect_conflicts(plan["run_id"], tmp, repo_root=REPO_ROOT)
        actual = len(conflicts)
    finally:
        try:
            os.unlink(tmp)
        except OSError:
            pass

    return {
        "fixture": os.path.basename(fixture_path),
        "description": plan.get("description", ""),
        "expected_conflicts": expected,
        "actual_conflicts": actual,
        "pass": (actual > 0) == (expected > 0),
        "conflicts": [
            {
                "symbol": c.symbol_name,
                "severity": c.severity_score,
                "task_a": c.task_a,
                "task_b": c.task_b,
            }
            for c in conflicts
        ],
    }


def compute_metrics(results: list[dict]) -> dict:
    """Compute precision and recall from results."""
    tp = sum(1 for r in results if r["expected_conflicts"] > 0 and r["actual_conflicts"] > 0)
    fp = sum(1 for r in results if r["expected_conflicts"] == 0 and r["actual_conflicts"] > 0)
    fn = sum(1 for r in results if r["expected_conflicts"] > 0 and r["actual_conflicts"] == 0)

    precision = tp / (tp + fp) if (tp + fp) > 0 else 1.0
    recall    = tp / (tp + fn) if (tp + fn) > 0 else 1.0
    return {"tp": tp, "fp": fp, "fn": fn, "precision": precision, "recall": recall}


def main() -> int:
    print("=" * 64)
    print("LOCKSTEP BENCHMARK SUITE")
    print("=" * 64)

    results = []
    for fname in FIXTURES:
        fpath = os.path.join(FIXTURES_DIR, fname)
        if not os.path.isfile(fpath):
            print(f"  [SKIP] {fname} — file not found")
            continue
        result = run_fixture(fpath)
        results.append(result)

        status = "PASS" if result["pass"] else "FAIL"
        print(f"\n  [{status}] {result['fixture']}")
        print(f"         expected_conflicts={result['expected_conflicts']}  actual={result['actual_conflicts']}")
        if result["description"]:
            print(f"         {result['description']}")
        if result["conflicts"]:
            for c in result["conflicts"]:
                print(f"         -> {c['symbol']}  severity={c['severity']:.4f}  ({c['task_a']} <-> {c['task_b']})")

    print("\n" + "=" * 64)
    metrics = compute_metrics(results)
    print(f"  TP={metrics['tp']}  FP={metrics['fp']}  FN={metrics['fn']}")
    print(f"  PRECISION = {metrics['precision']:.3f}")
    print(f"  RECALL    = {metrics['recall']:.3f}")
    print("=" * 64)

    all_pass = all(r["pass"] for r in results)
    precision_ok = abs(metrics["precision"] - 1.0) < 1e-9
    recall_ok    = abs(metrics["recall"]    - 1.0) < 1e-9

    if all_pass and precision_ok and recall_ok:
        print("\n  RESULT: ALL PASS — precision=1.000 recall=1.000")
        return 0
    else:
        failed = [r["fixture"] for r in results if not r["pass"]]
        print(f"\n  RESULT: FAIL — failed fixtures: {failed}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
