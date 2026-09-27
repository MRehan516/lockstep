"""
Tests for Lockstep conflict detector (FR1.3 + FR2.2).
"""
import json
import os
import tempfile

import pytest

from lockstep.ingester import ingest_plan
from lockstep.detector import detect_conflicts, Conflict
from lockstep.models import get_connection

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))


def _load_fixture(name: str) -> dict:
    path = os.path.join(REPO_ROOT, "benchmarks", "fixtures", name)
    with open(path) as f:
        return json.load(f)


def _fresh_db():
    f = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    f.close()
    return f.name


# ---------------------------------------------------------------------------
# Section 10 — guaranteed true positive
# ---------------------------------------------------------------------------

def test_section10_exactly_one_conflict():
    db = _fresh_db()
    try:
        plan = _load_fixture("section10_plan.json")
        ingest_plan(plan, db)
        conflicts = detect_conflicts("run-section10", db, repo_root=REPO_ROOT)
        # Must detect exactly one conflict on calculateShipping
        calc_conflicts = [c for c in conflicts if c.symbol_name == "calculateShipping"]
        assert len(calc_conflicts) == 1, (
            f"Expected 1 conflict on calculateShipping, got {len(calc_conflicts)}: "
            + str([(c.task_a, c.task_b, c.symbol_name) for c in conflicts])
        )
    finally:
        os.unlink(db)


def test_section10_conflict_severity_high():
    db = _fresh_db()
    try:
        plan = _load_fixture("section10_plan.json")
        ingest_plan(plan, db)
        conflicts = detect_conflicts("run-section10", db, repo_root=REPO_ROOT)
        calc = [c for c in conflicts if c.symbol_name == "calculateShipping"][0]
        assert calc.severity_score > 0.7, f"Expected severity > 0.7, got {calc.severity_score}"
    finally:
        os.unlink(db)


def test_section10_conflict_tasks_are_correct():
    db = _fresh_db()
    try:
        plan = _load_fixture("section10_plan.json")
        ingest_plan(plan, db)
        conflicts = detect_conflicts("run-section10", db, repo_root=REPO_ROOT)
        calc = [c for c in conflicts if c.symbol_name == "calculateShipping"][0]
        involved_tasks = {calc.task_a, calc.task_b}
        # task_ids are stored as run_id::original_task_id
        assert "run-section10::task-1-signature-change" in involved_tasks
        assert "run-section10::task-2-new-endpoint" in involved_tasks
    finally:
        os.unlink(db)


def test_section10_conflict_resolution_is_serialize():
    db = _fresh_db()
    try:
        plan = _load_fixture("section10_plan.json")
        ingest_plan(plan, db)
        conflicts = detect_conflicts("run-section10", db, repo_root=REPO_ROOT)
        for c in conflicts:
            assert c.resolution == "serialize"
    finally:
        os.unlink(db)


def test_section10_conflict_persisted_to_db():
    db = _fresh_db()
    try:
        plan = _load_fixture("section10_plan.json")
        ingest_plan(plan, db)
        detect_conflicts("run-section10", db, repo_root=REPO_ROOT)
        conn = get_connection(db)
        count = conn.execute(
            "SELECT COUNT(*) FROM conflicts WHERE run_id = ?", ("run-section10",)
        ).fetchone()[0]
        conn.close()
        assert count >= 1
    finally:
        os.unlink(db)


# ---------------------------------------------------------------------------
# Read-read — no conflict
# ---------------------------------------------------------------------------

def test_no_conflict_when_both_reads():
    db = _fresh_db()
    try:
        plan = {
            "run_id": "run-reads-only",
            "tasks": [
                {
                    "task_id": "t-read-a",
                    "subagent_id": "a",
                    "description": "Read shipping",
                    "files": ["galaxium/main.py"],
                    "symbols_touched": [{"name": "calculateShipping", "kind": "function", "access_type": "read"}],
                },
                {
                    "task_id": "t-read-b",
                    "subagent_id": "b",
                    "description": "Also read shipping",
                    "files": ["galaxium/mcp_server.py"],
                    "symbols_touched": [{"name": "calculateShipping", "kind": "function", "access_type": "read"}],
                },
            ],
        }
        ingest_plan(plan, db)
        conflicts = detect_conflicts("run-reads-only", db)
        assert conflicts == [], f"Expected no conflicts, got: {conflicts}"
    finally:
        os.unlink(db)
