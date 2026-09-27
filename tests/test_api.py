"""
Tests for Lockstep FastAPI backend (FR1.7).
"""
import json
import os
import tempfile

import pytest
from fastapi.testclient import TestClient

# Point the app at a temp DB before import
_tmp_db = tempfile.mktemp(suffix="_test_api.db")
_repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))

# Set env vars before importing the app
os.environ["LOCKSTEP_DB"] = _tmp_db
os.environ["LOCKSTEP_REPO_ROOT"] = _repo_root

import lockstep.api as _api_module
# Point module-level vars at temp DB/repo
_api_module.DB_PATH = _tmp_db
_api_module.REPO_ROOT = _repo_root

from lockstep.api import app


@pytest.fixture(scope="module")
def client():
    # Unmount static files for testing (dir may not exist)
    for route in list(app.routes):
        if hasattr(route, "name") and route.name == "static":
            app.routes.remove(route)
    with TestClient(app) as c:
        yield c


def _load_fixture(name: str) -> dict:
    path = os.path.join(_repo_root, "benchmarks", "fixtures", name)
    with open(path) as f:
        return json.load(f)


# ---------------------------------------------------------------------------
# POST /runs
# ---------------------------------------------------------------------------

def test_post_runs_returns_201(client):
    plan = _load_fixture("section10_plan.json")
    # Use a unique run_id to avoid conflicts with other test state
    plan["run_id"] = "api-test-run-1"
    resp = client.post("/runs", json=plan)
    assert resp.status_code == 201, resp.text


def test_post_runs_returns_run_id(client):
    plan = _load_fixture("section10_plan.json")
    plan["run_id"] = "api-test-run-2"
    resp = client.post("/runs", json=plan)
    body = resp.json()
    assert body["run_id"] == "api-test-run-2"


def test_post_runs_returns_conflict_count(client):
    plan = _load_fixture("section10_plan.json")
    plan["run_id"] = "api-test-run-3"
    resp = client.post("/runs", json=plan)
    body = resp.json()
    assert body["conflict_count"] == 1


def test_post_runs_invalid_plan_returns_422(client):
    resp = client.post("/runs", json={"bad": "plan"})
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# GET /runs/{run_id}
# ---------------------------------------------------------------------------

def test_get_run_returns_summary(client):
    plan = _load_fixture("section10_plan.json")
    plan["run_id"] = "api-test-run-4"
    client.post("/runs", json=plan)
    resp = client.get(f"/runs/{plan['run_id']}")
    assert resp.status_code == 200
    assert "summary" in resp.json() or "status" in resp.json()


def test_get_run_not_found_returns_404(client):
    resp = client.get("/runs/nonexistent-run-xyz")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# GET /runs/{run_id}/waves
# ---------------------------------------------------------------------------

def test_get_waves_returns_two_waves(client):
    plan = _load_fixture("section10_plan.json")
    plan["run_id"] = "api-test-run-5"
    client.post("/runs", json=plan)
    resp = client.get(f"/runs/{plan['run_id']}/waves")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["waves"]) == 2


# ---------------------------------------------------------------------------
# GET /runs/{run_id}/conflicts
# ---------------------------------------------------------------------------

def test_get_conflicts_returns_one(client):
    plan = _load_fixture("section10_plan.json")
    plan["run_id"] = "api-test-run-6"
    client.post("/runs", json=plan)
    resp = client.get(f"/runs/{plan['run_id']}/conflicts")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["conflicts"]) == 1
    assert body["conflicts"][0]["symbol"] == "calculateShipping"
