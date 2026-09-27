"""
Tests for Lockstep AST extractor (FR1.2).
"""
import json
import os

from lockstep.extractor import (
    extract_touch_set,
    find_call_sites,
    find_symbol_definition,
    scan_non_code_resources,
    TouchRecord,
)

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
GALAXIUM_ROOT = os.path.join(REPO_ROOT, "galaxium")


def _load_fixture(name: str) -> dict:
    path = os.path.join(REPO_ROOT, "benchmarks", "fixtures", name)
    with open(path) as f:
        return json.load(f)


# ---------------------------------------------------------------------------
# find_symbol_definition
# ---------------------------------------------------------------------------

def test_find_calculateShipping_definition():
    defs = find_symbol_definition("calculateShipping", "function", GALAXIUM_ROOT)
    assert len(defs) >= 1
    files = [os.path.basename(f) for f, _ in defs]
    assert "shipping.py" in files


def test_find_nonexistent_symbol():
    defs = find_symbol_definition("nonExistentFunctionXYZ", "function", GALAXIUM_ROOT)
    assert defs == []


# ---------------------------------------------------------------------------
# find_call_sites
# ---------------------------------------------------------------------------

def test_find_call_sites_calculateShipping():
    sites = find_call_sites("calculateShipping", GALAXIUM_ROOT)
    # shipping.py defines it, main.py and mcp_server.py call it
    caller_basenames = {os.path.basename(f) for f, _ in sites}
    assert "main.py" in caller_basenames
    assert "mcp_server.py" in caller_basenames
    assert len(sites) >= 2  # at least 2 call sites outside definition


# ---------------------------------------------------------------------------
# extract_touch_set — Section 10 fixture
# ---------------------------------------------------------------------------

def test_extract_task1_signature_change():
    plan = _load_fixture("section10_plan.json")
    task1 = plan["tasks"][0]  # signature_change task
    # repo_root is workspace root; fixture files are relative to it (e.g. "galaxium/shipping.py")
    records = extract_touch_set(task1, REPO_ROOT)
    sig_change = [r for r in records if r.access_type == "signature_change" and r.symbol_name == "calculateShipping"]
    assert len(sig_change) >= 1
    assert all(r.confidence == 1.0 for r in sig_change)


def test_extract_task2_read():
    plan = _load_fixture("section10_plan.json")
    task2 = plan["tasks"][1]  # new endpoint task — reads calculateShipping
    records = extract_touch_set(task2, REPO_ROOT)
    calc_reads = [r for r in records if r.symbol_name == "calculateShipping" and r.access_type == "read"]
    assert len(calc_reads) >= 1


def test_extract_touch_set_confidence_is_float():
    plan = _load_fixture("section10_plan.json")
    for task in plan["tasks"]:
        records = extract_touch_set(task, REPO_ROOT)
        for r in records:
            assert isinstance(r.confidence, float)
            assert 0.0 <= r.confidence <= 1.0


# ---------------------------------------------------------------------------
# scan_non_code_resources
# ---------------------------------------------------------------------------

def test_scan_non_code_resources_env_file():
    task = {
        "task_id": "t-env",
        "subagent_id": "a",
        "description": "Update env config",
        "files": ["galaxium/.env.example"],
        "symbols_touched": [],
    }
    records = scan_non_code_resources(task, REPO_ROOT)
    assert len(records) == 1
    assert records[0].kind == "config_key"


def test_scan_non_code_resources_migration_file():
    task = {
        "task_id": "t-mig",
        "subagent_id": "a",
        "description": "Add migration",
        "files": ["galaxium/alembic/versions/0001_initial.py"],
        "symbols_touched": [],
    }
    records = scan_non_code_resources(task, REPO_ROOT)
    assert len(records) == 1
    assert records[0].kind == "migration_file"


def test_scan_non_code_resources_no_resources():
    task = {
        "task_id": "t-none",
        "subagent_id": "a",
        "description": "Pure code change",
        "files": ["galaxium/shipping.py"],
        "symbols_touched": [],
    }
    records = scan_non_code_resources(task, REPO_ROOT)
    assert records == []
