"""
Tests for Lockstep shim generator (FR2.5).
"""
import ast
import json
import os
import tempfile

import pytest

from lockstep.ingester import ingest_plan
from lockstep.detector import detect_conflicts
from lockstep.shim import generate_shim, ShimResult
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


def _setup_conflict(db: str) -> int:
    """Ingest + detect section10, return conflict_id."""
    plan = _load_fixture("section10_plan.json")
    ingest_plan(plan, db)
    conflicts = detect_conflicts("run-section10", db, repo_root=REPO_ROOT)
    assert len(conflicts) >= 1
    return conflicts[0].conflict_id


# ---------------------------------------------------------------------------
# Shim generation
# ---------------------------------------------------------------------------

def test_shim_generates_valid_python():
    db = _fresh_db()
    try:
        cid = _setup_conflict(db)
        result = generate_shim(
            conflict_id=cid,
            db_path=db,
            repo_root=REPO_ROOT,
            new_param_description="Add a required `region` parameter to calculateShipping()",
        )
        # Must be parseable
        ast.parse(result.shim_source)
    finally:
        os.unlink(db)


def test_shim_file_written():
    db = _fresh_db()
    try:
        cid = _setup_conflict(db)
        result = generate_shim(cid, db, REPO_ROOT,
                               "Add a required `region` parameter")
        assert os.path.isfile(result.shim_file)
    finally:
        os.unlink(db)


def test_shim_contains_symbol_name():
    db = _fresh_db()
    try:
        cid = _setup_conflict(db)
        result = generate_shim(cid, db, REPO_ROOT,
                               "Add a required `region` parameter")
        assert "calculateShipping" in result.shim_source
    finally:
        os.unlink(db)


def test_shim_old_args_accepted():
    """Calling the shim with the old 2-arg signature must not raise TypeError."""
    db = _fresh_db()
    try:
        cid = _setup_conflict(db)
        result = generate_shim(cid, db, REPO_ROOT,
                               "Add a required `region` parameter to calculateShipping()")
        # Execute the shim source in an isolated namespace
        ns: dict = {}
        exec(result.shim_source, ns)
        shim_fn = ns["calculateShipping"]
        # Old 2-arg call
        cost = shim_fn(5.0, "domestic")
        assert isinstance(cost, float)
    finally:
        os.unlink(db)


def test_shim_resolution_updated_in_db():
    db = _fresh_db()
    try:
        cid = _setup_conflict(db)
        generate_shim(cid, db, REPO_ROOT, "Add `region` parameter")
        conn = get_connection(db)
        row = conn.execute(
            "SELECT resolution FROM conflicts WHERE conflict_id = ?", (cid,)
        ).fetchone()
        conn.close()
        assert row["resolution"] == "shim"
    finally:
        os.unlink(db)


def test_shim_cleanup_task_created():
    db = _fresh_db()
    try:
        cid = _setup_conflict(db)
        result = generate_shim(cid, db, REPO_ROOT, "Add `region` parameter")
        conn = get_connection(db)
        row = conn.execute(
            "SELECT task_id FROM tasks WHERE task_id = ?", (result.cleanup_task_id,)
        ).fetchone()
        conn.close()
        assert row is not None
    finally:
        os.unlink(db)
