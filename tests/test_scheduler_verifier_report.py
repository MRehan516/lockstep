"""
Tests for Lockstep scheduler (FR1.4), verifier (FR1.5), and report (FR1.6 + FR2.6).
"""
import json
import os
import tempfile
from datetime import datetime, timedelta

import pytest

from lockstep.ingester import ingest_plan
from lockstep.detector import detect_conflicts
from lockstep.scheduler import schedule_run
from lockstep.verifier import verify_run
from lockstep.report import build_report
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


def _setup_run(db: str) -> str:
    """Ingest + detect section10 plan and return run_id."""
    plan = _load_fixture("section10_plan.json")
    ingest_plan(plan, db)
    detect_conflicts("run-section10", db, repo_root=REPO_ROOT)
    return "run-section10"


# ---------------------------------------------------------------------------
# Scheduler
# ---------------------------------------------------------------------------

def test_section10_produces_two_waves():
    db = _fresh_db()
    try:
        run_id = _setup_run(db)
        waves = schedule_run(run_id, db)
        assert len(waves) == 2, f"Expected 2 waves, got {len(waves)}: {waves}"
    finally:
        os.unlink(db)


def test_section10_task1_in_wave_0():
    db = _fresh_db()
    try:
        run_id = _setup_run(db)
        waves = schedule_run(run_id, db)
        # task_ids are stored as run_id::original_task_id
        assert any("task-1-signature-change" in tid for tid in waves[0]), (
            f"task-1-signature-change should be in wave 0, waves={waves}"
        )
    finally:
        os.unlink(db)


def test_section10_task2_in_wave_1():
    db = _fresh_db()
    try:
        run_id = _setup_run(db)
        waves = schedule_run(run_id, db)
        assert any("task-2-new-endpoint" in tid for tid in waves[1]), (
            f"task-2-new-endpoint should be in wave 1, waves={waves}"
        )
    finally:
        os.unlink(db)


def test_scheduler_updates_wave_column():
    db = _fresh_db()
    try:
        run_id = _setup_run(db)
        schedule_run(run_id, db)
        conn = get_connection(db)
        rows = conn.execute(
            "SELECT task_id, wave FROM tasks WHERE run_id = ?", (run_id,)
        ).fetchall()
        conn.close()
        for r in rows:
            assert r["wave"] is not None, f"Task {r['task_id']} has no wave assigned"
    finally:
        os.unlink(db)


def test_independent_tasks_share_wave():
    """Two tasks touching completely different symbols should share wave 0."""
    db = _fresh_db()
    try:
        plan = {
            "run_id": "run-independent",
            "tasks": [
                {
                    "task_id": "t-a",
                    "subagent_id": "a",
                    "description": "Change foo",
                    "files": ["galaxium/shipping.py"],
                    "symbols_touched": [{"name": "fooFunc", "kind": "function", "access_type": "write"}],
                },
                {
                    "task_id": "t-b",
                    "subagent_id": "b",
                    "description": "Change bar",
                    "files": ["galaxium/main.py"],
                    "symbols_touched": [{"name": "barFunc", "kind": "function", "access_type": "write"}],
                },
            ],
        }
        ingest_plan(plan, db)
        detect_conflicts("run-independent", db)
        waves = schedule_run("run-independent", db)
        assert len(waves) == 1, f"Expected 1 wave, got {waves}"
        # task_ids are stored as run_id::original_task_id
        assert set(waves[0]) == {"run-independent::t-a", "run-independent::t-b"}
    finally:
        os.unlink(db)


# ---------------------------------------------------------------------------
# Verifier
# ---------------------------------------------------------------------------

def _make_ts(offset_seconds: int = 0) -> str:
    return (datetime.utcnow() + timedelta(seconds=offset_seconds)).isoformat()


def test_verifier_no_deviation():
    db = _fresh_db()
    try:
        run_id = _setup_run(db)
        schedule_run(run_id, db)
        results = [
            {
                "task_id": "task-1-signature-change",
                "actual_files": ["galaxium/shipping.py", "galaxium/main.py", "galaxium/mcp_server.py"],
                "started_at": _make_ts(0),
                "ended_at": _make_ts(5),
            },
            {
                "task_id": "task-2-new-endpoint",
                "actual_files": ["galaxium/main.py"],
                "started_at": _make_ts(6),
                "ended_at": _make_ts(9),
            },
        ]
        verify_run(run_id, results, db)
        conn = get_connection(db)
        deviations = conn.execute(
            "SELECT COUNT(*) FROM execution_logs WHERE plan_deviation = 1"
        ).fetchone()[0]
        conn.close()
        assert deviations == 0
    finally:
        os.unlink(db)


def test_verifier_flags_extra_file():
    db = _fresh_db()
    try:
        run_id = _setup_run(db)
        schedule_run(run_id, db)
        results = [
            {
                "task_id": "task-1-signature-change",  # original task_id; verifier maps to composite
                "actual_files": ["galaxium/shipping.py", "galaxium/UNEXPECTED_FILE.py"],
                "started_at": _make_ts(0),
                "ended_at": _make_ts(3),
            },
        ]
        verify_run(run_id, results, db)
        conn = get_connection(db)
        # Composite task_id stored in DB
        row = conn.execute(
            "SELECT plan_deviation FROM execution_logs WHERE task_id = ?",
            (f"{run_id}::task-1-signature-change",),
        ).fetchone()
        conn.close()
        assert row is not None, "execution_log row not found"
        assert row["plan_deviation"] == 1
    finally:
        os.unlink(db)


def test_verifier_marks_run_completed():
    db = _fresh_db()
    try:
        run_id = _setup_run(db)
        verify_run(run_id, [], db)
        conn = get_connection(db)
        status = conn.execute(
            "SELECT status FROM runs WHERE run_id = ?", (run_id,)
        ).fetchone()["status"]
        conn.close()
        assert status == "completed"
    finally:
        os.unlink(db)


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------

def test_report_conflict_count():
    db = _fresh_db()
    try:
        run_id = _setup_run(db)
        schedule_run(run_id, db)
        verify_run(run_id, [], db)
        report = build_report(run_id, db)
        assert report["conflict_count"] == 1
    finally:
        os.unlink(db)


def test_report_wave_count():
    db = _fresh_db()
    try:
        run_id = _setup_run(db)
        schedule_run(run_id, db)
        verify_run(run_id, [], db)
        report = build_report(run_id, db)
        assert len(report["waves"]) == 2
    finally:
        os.unlink(db)


def test_report_persisted_to_db():
    db = _fresh_db()
    try:
        run_id = _setup_run(db)
        schedule_run(run_id, db)
        verify_run(run_id, [], db)
        build_report(run_id, db)
        conn = get_connection(db)
        count = conn.execute(
            "SELECT COUNT(*) FROM reports WHERE run_id = ?", (run_id,)
        ).fetchone()[0]
        conn.close()
        assert count == 1
    finally:
        os.unlink(db)


def test_report_timing_time_saved():
    """With parallel execution, time_saved_ms should be >= 0."""
    db = _fresh_db()
    try:
        run_id = _setup_run(db)
        schedule_run(run_id, db)
        # Simulate both tasks running sequentially (no time saved expected)
        t0 = datetime.utcnow()
        results = [
            {
                "task_id": "task-1-signature-change",
                "actual_files": ["galaxium/shipping.py"],
                "started_at": t0.isoformat(),
                "ended_at": (t0 + timedelta(milliseconds=200)).isoformat(),
            },
            {
                "task_id": "task-2-new-endpoint",
                "actual_files": ["galaxium/main.py"],
                "started_at": (t0 + timedelta(milliseconds=210)).isoformat(),
                "ended_at": (t0 + timedelta(milliseconds=400)).isoformat(),
            },
        ]
        verify_run(run_id, results, db)
        report = build_report(run_id, db)
        assert report["timing"]["time_saved_ms"] >= 0
    finally:
        os.unlink(db)
