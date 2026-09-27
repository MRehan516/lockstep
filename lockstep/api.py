"""
Lockstep — FastAPI backend + WebSocket hub (FR1.7 + FR2.3).

Endpoints
---------
POST /runs                      Ingest plan, run full pipeline, return report.
GET  /runs/{run_id}             Return the assembled report for a run.
GET  /runs/{run_id}/waves       Return the wave schedule.
GET  /runs/{run_id}/conflicts   Return conflict list with severity scores.
WS   /ws/{run_id}               Real-time pipeline stage events.

Query params for POST /runs:
  generate_shim=true   Trigger shim generation for signature_change conflicts.
  execute=true         Actually execute waves via the wave runner (default: analysis only).
"""
from __future__ import annotations

import asyncio
import json
import os
from typing import Any

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles

from lockstep.ingester import ingest_plan, LockstepIngestionError
from lockstep.extractor import extract_touch_set, scan_non_code_resources
from lockstep.detector import detect_conflicts
from lockstep.scheduler import schedule_run
from lockstep.verifier import verify_run
from lockstep.report import build_report
from lockstep.shim import generate_shim as _generate_shim
from lockstep.runner import execute_waves
from lockstep.models import get_connection, create_tables

# Default DB and repo root (overridable via env)
DB_PATH = os.environ.get("LOCKSTEP_DB", "lockstep.db")
REPO_ROOT = os.environ.get("LOCKSTEP_REPO_ROOT", ".")

app = FastAPI(title="Lockstep", version="1.0.0")

# ---------------------------------------------------------------------------
# WebSocket connection manager
# ---------------------------------------------------------------------------

class ConnectionManager:
    """Fan-out WebSocket broadcaster.  Supports multiple clients per run_id."""

    def __init__(self) -> None:
        self._connections: dict[str, list[WebSocket]] = {}

    async def connect(self, run_id: str, ws: WebSocket) -> None:
        await ws.accept()
        self._connections.setdefault(run_id, []).append(ws)

    def disconnect(self, run_id: str, ws: WebSocket) -> None:
        conns = self._connections.get(run_id, [])
        if ws in conns:
            conns.remove(ws)

    async def broadcast(self, run_id: str, data: dict) -> None:
        dead: list[WebSocket] = []
        for ws in self._connections.get(run_id, []):
            try:
                await ws.send_json(data)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.disconnect(run_id, ws)


manager = ConnectionManager()


# ---------------------------------------------------------------------------
# Startup
# ---------------------------------------------------------------------------

@app.on_event("startup")
def _startup() -> None:
    create_tables(DB_PATH)


# ---------------------------------------------------------------------------
# Helper: emit a WebSocket event and also run a named pipeline stage
# ---------------------------------------------------------------------------

async def _emit(run_id: str, event: str, payload: dict | None = None) -> None:
    await manager.broadcast(run_id, {"event": event, **(payload or {})})


# ---------------------------------------------------------------------------
# POST /runs  — main pipeline entry point
# ---------------------------------------------------------------------------

@app.post("/runs", status_code=201)
async def create_run(
    plan_json: dict,
    generate_shim: bool = False,
    execute: bool = False,
) -> dict:
    """Ingest a Plan JSON, run the full analysis pipeline, and return the report.

    If generate_shim=true, shims are generated for signature_change conflicts.
    If execute=true, tasks are executed via the wave runner (analysis-only by default).
    """
    # 1. Ingest
    try:
        run_id = ingest_plan(plan_json, DB_PATH)
    except LockstepIngestionError as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    await _emit(run_id, "ingester_done", {"run_id": run_id})

    # 2. Extract touch-sets (enrich DB with call-site data)
    for task in plan_json.get("tasks", []):
        _enrich_touches(task, run_id)

    await _emit(run_id, "extractor_done")

    # 3. Detect conflicts
    conflicts = detect_conflicts(run_id, DB_PATH, repo_root=REPO_ROOT)
    await _emit(run_id, "detector_done", {"conflict_count": len(conflicts)})

    # 4. Schedule waves
    waves = schedule_run(run_id, DB_PATH)
    await _emit(run_id, "scheduler_done", {"wave_count": len(waves)})

    # 5. Optional shim generation
    if generate_shim:
        for conflict in conflicts:
            try:
                _generate_shim(
                    conflict_id=conflict.conflict_id,
                    db_path=DB_PATH,
                    repo_root=REPO_ROOT,
                )
            except Exception:
                pass  # shim generation is best-effort

    # 6. Optional wave execution
    if execute:
        # No-op agent_fn: records that each task "ran" without actually doing anything
        async def _noop_agent(task: dict) -> dict:
            from datetime import datetime
            return {
                "task_id": task["task_id"],
                "actual_files": task.get("files", []),
                "started_at": datetime.utcnow().isoformat(),
                "ended_at": datetime.utcnow().isoformat(),
            }

        await execute_waves(
            run_id=run_id,
            waves=waves,
            db_path=DB_PATH,
            repo_root=REPO_ROOT,
            agent_fn=_noop_agent,
            ws_broadcast=manager.broadcast,
        )
    else:
        # Record that the run completed without execution (analysis-only)
        verify_run(run_id, [], DB_PATH)

    await _emit(run_id, "verifier_done")

    # 7. Build report
    report = build_report(run_id, DB_PATH)
    await _emit(run_id, "report_done", {"report": report})

    return report


def _enrich_touches(task: dict, run_id: str) -> None:
    """Run extractor on task and persist any new confidence values to DB.

    Currently a best-effort enrichment: updates confidence on existing
    task_symbol_touches rows where the extractor finds a direct AST hit.
    """
    try:
        records = extract_touch_set(task, REPO_ROOT)
        conn = get_connection(DB_PATH)
        with conn:
            for rec in records:
                if rec.confidence < 1.0:
                    conn.execute(
                        """
                        UPDATE task_symbol_touches
                        SET confidence = ?
                        WHERE task_id = ? AND symbol_id = (
                            SELECT symbol_id FROM symbols WHERE name = ?
                        )
                        """,
                        (rec.confidence, task["task_id"], rec.symbol_name),
                    )
        conn.close()
    except Exception:
        pass  # enrichment is best-effort


# ---------------------------------------------------------------------------
# GET /runs/{run_id}
# ---------------------------------------------------------------------------

@app.get("/runs/{run_id}")
def get_run(run_id: str) -> dict:
    """Return the assembled report for a run."""
    conn = get_connection(DB_PATH)
    row = conn.execute(
        "SELECT summary, timing_json FROM reports WHERE run_id = ? ORDER BY report_id DESC LIMIT 1",
        (run_id,),
    ).fetchone()
    if not row:
        # Report not yet built — return basic run info
        run_row = conn.execute(
            "SELECT run_id, status, created_at FROM runs WHERE run_id = ?", (run_id,)
        ).fetchone()
        conn.close()
        if not run_row:
            raise HTTPException(status_code=404, detail=f"Run '{run_id}' not found")
        return {"run_id": run_id, "status": run_row["status"]}
    conn.close()
    return {"run_id": run_id, "summary": row["summary"], "timing": json.loads(row["timing_json"])}


# ---------------------------------------------------------------------------
# GET /runs/{run_id}/waves
# ---------------------------------------------------------------------------

@app.get("/runs/{run_id}/waves")
def get_waves(run_id: str) -> dict:
    """Return the wave schedule with task details."""
    conn = get_connection(DB_PATH)
    rows = conn.execute(
        "SELECT task_id, subagent_id, description, wave FROM tasks WHERE run_id = ? ORDER BY wave",
        (run_id,),
    ).fetchall()
    conn.close()
    if not rows:
        raise HTTPException(status_code=404, detail=f"Run '{run_id}' not found")

    waves_map: dict[int, list[dict]] = {}
    for r in rows:
        wave = r["wave"] if r["wave"] is not None else 0
        waves_map.setdefault(wave, []).append(
            {"task_id": r["task_id"], "subagent_id": r["subagent_id"], "description": r["description"]}
        )
    return {"run_id": run_id, "waves": [waves_map[k] for k in sorted(waves_map)]}


# ---------------------------------------------------------------------------
# GET /runs/{run_id}/conflicts
# ---------------------------------------------------------------------------

@app.get("/runs/{run_id}/conflicts")
def get_conflicts(run_id: str) -> dict:
    """Return conflict list with severity scores."""
    conn = get_connection(DB_PATH)
    rows = conn.execute(
        """
        SELECT c.conflict_id, c.task_a, c.task_b, s.name AS symbol_name,
               c.severity_score, c.resolution
        FROM conflicts c
        JOIN symbols s ON c.symbol_id = s.symbol_id
        WHERE c.run_id = ?
        ORDER BY c.severity_score DESC
        """,
        (run_id,),
    ).fetchall()
    conn.close()
    return {
        "run_id": run_id,
        "conflicts": [
            {
                "conflict_id": r["conflict_id"],
                "task_a": r["task_a"],
                "task_b": r["task_b"],
                "symbol": r["symbol_name"],
                "severity_score": r["severity_score"],
                "resolution": r["resolution"],
            }
            for r in rows
        ],
    }


# ---------------------------------------------------------------------------
# WebSocket /ws/{run_id}
# ---------------------------------------------------------------------------

@app.websocket("/ws/{run_id}")
async def websocket_endpoint(websocket: WebSocket, run_id: str) -> None:
    """Real-time pipeline stage events for a run."""
    await manager.connect(run_id, websocket)
    try:
        while True:
            # Keep connection alive; client can send pings
            await websocket.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(run_id, websocket)


# ---------------------------------------------------------------------------
# Static files (dashboard)
# ---------------------------------------------------------------------------

_static_dir = os.path.join(os.path.dirname(__file__), "static")
if os.path.isdir(_static_dir):
    # Mount at /ui, NOT at "/" — a catch-all mount at "/" intercepts every
    # GET request (including /runs/...) before FastAPI's own route handlers
    # see them, causing the StaticFiles sub-app to return 404 for all API
    # calls and making the Load button appear to do nothing in DevTools.
    app.mount("/ui", StaticFiles(directory=_static_dir, html=True), name="static")
