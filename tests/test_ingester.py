"""
Tests for Lockstep ingester (FR1.1).
"""
import json
import os
import tempfile

import pytest

from lockstep.ingester import ingest_plan, validate_plan, LockstepIngestionError
from lockstep.models import create_tables, get_connection


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------

def _load_fixture(name: str) -> dict:
    fixture_path = os.path.join(
        os.path.dirname(__file__), "..", "benchmarks", "fixtures", name
    )
    with open(fixture_path) as f:
        return json.load(f)


def _fresh_db():
    f = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    f.close()
    return f.name


# ---------------------------------------------------------------------------
# validate_plan
# ---------------------------------------------------------------------------

def test_validate_plan_valid_section10():
    plan = _load_fixture("section10_plan.json")
    validate_plan(plan)  # must not raise


def test_validate_plan_missing_run_id():
    with pytest.raises(LockstepIngestionError, match="run_id"):
        validate_plan({"tasks": [{"task_id": "t1", "subagent_id": "a", "description": "x",
                                   "files": [], "symbols_touched": []}]})


def test_validate_plan_missing_tasks():
    with pytest.raises(LockstepIngestionError):
        validate_plan({"run_id": "r1"})


def test_validate_plan_empty_tasks():
    with pytest.raises(LockstepIngestionError):
        validate_plan({"run_id": "r1", "tasks": []})


def test_validate_plan_task_missing_field():
    plan = {
        "run_id": "r1",
        "tasks": [{"task_id": "t1", "subagent_id": "a"}],  # missing description/files/symbols
    }
    with pytest.raises(LockstepIngestionError, match="missing required keys"):
        validate_plan(plan)


def test_validate_plan_invalid_kind():
    plan = {
        "run_id": "r1",
        "tasks": [{
            "task_id": "t1", "subagent_id": "a", "description": "x",
            "files": [], "symbols_touched": [{"name": "foo", "kind": "INVALID", "access_type": "read"}],
        }],
    }
    with pytest.raises(LockstepIngestionError, match="invalid kind"):
        validate_plan(plan)


def test_validate_plan_invalid_access_type():
    plan = {
        "run_id": "r1",
        "tasks": [{
            "task_id": "t1", "subagent_id": "a", "description": "x",
            "files": [], "symbols_touched": [{"name": "foo", "kind": "function", "access_type": "INVALID"}],
        }],
    }
    with pytest.raises(LockstepIngestionError, match="invalid access_type"):
        validate_plan(plan)


# ---------------------------------------------------------------------------
# ingest_plan
# ---------------------------------------------------------------------------

def test_ingest_plan_section10_row_counts():
    db = _fresh_db()
    try:
        plan = _load_fixture("section10_plan.json")
        run_id = ingest_plan(plan, db)

        assert run_id == "run-section10"

        conn = get_connection(db)
        assert conn.execute("SELECT COUNT(*) FROM runs").fetchone()[0] == 1
        # 2 tasks
        assert conn.execute("SELECT COUNT(*) FROM tasks").fetchone()[0] == 2
        # 3 unique symbols: calculateShipping (x2 but deduped), getShippingEstimate
        # calculateShipping appears in task-1 (shipping.py) and task-2 (main.py) with different file hints
        symbol_count = conn.execute("SELECT COUNT(*) FROM symbols").fetchone()[0]
        assert symbol_count >= 2  # at least calculateShipping + getShippingEstimate
        # 3 touches total: 1 from task-1, 2 from task-2
        assert conn.execute("SELECT COUNT(*) FROM task_symbol_touches").fetchone()[0] == 3
        conn.close()
    finally:
        os.unlink(db)


def test_ingest_plan_idempotent():
    """Ingesting the same plan twice must not raise or duplicate rows."""
    db = _fresh_db()
    try:
        plan = _load_fixture("section10_plan.json")
        ingest_plan(plan, db)
        ingest_plan(plan, db)  # second call — INSERT OR IGNORE, no error
        conn = get_connection(db)
        assert conn.execute("SELECT COUNT(*) FROM runs").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM tasks").fetchone()[0] == 2
        conn.close()
    finally:
        os.unlink(db)
