"""
Lockstep — Wave runner (ST-15 / PRD §5 pipeline).

Bridges the scheduler's wave output to actual subagent execution.
For each wave it calls agent_fn() for every task concurrently (asyncio.gather),
collects execution results, runs the verifier, and emits WebSocket events.

agent_fn signature:
    async def agent_fn(task_dict: dict) -> dict
        task_dict:  {"task_id": ..., "description": ..., "files_json": ...}
        returns:    {"task_id": ..., "actual_files": [...],
                     "started_at": ..., "ended_at": ...}
"""
from __future__ import annotations

import asyncio
import json
from typing import Any, Callable, Awaitable

from lockstep.models import get_connection
from lockstep.verifier import verify_run


async def execute_waves(
    run_id: str,
    waves: list[list[str]],
    db_path: str,
    repo_root: str,
    agent_fn: Callable[[dict], Awaitable[dict]],
    ws_broadcast: Callable[[str, dict], Awaitable[None]] | None = None,
) -> list[dict]:
    """Execute tasks wave by wave, calling agent_fn for each task.

    Parameters
    ----------
    run_id:       The run being executed.
    waves:        Ordered list of waves (output of schedule_run).
    db_path:      Path to the Lockstep SQLite database.
    repo_root:    Path to the target repository root.
    agent_fn:     Async callable that executes a task.  Receives a task dict
                  and returns an execution_result dict.
    ws_broadcast: Optional async callable (run_id, event_dict) → None used
                  to emit WebSocket events to connected dashboard clients.

    Returns
    -------
    Flat list of all execution_result dicts in wave order.
    """
    conn = get_connection(db_path)
    task_rows = {
        r["task_id"]: dict(r)
        for r in conn.execute(
            "SELECT task_id, description, files_json FROM tasks WHERE run_id = ?",
            (run_id,),
        ).fetchall()
    }
    conn.close()

    all_results: list[dict] = []

    for wave_index, wave in enumerate(waves):
        # Build task dicts for this wave
        wave_tasks = [
            {
                "task_id": tid,
                "description": task_rows.get(tid, {}).get("description", ""),
                "files": json.loads(task_rows.get(tid, {}).get("files_json", "[]")),
            }
            for tid in wave
        ]

        # Execute all tasks in this wave concurrently
        wave_results: list[dict] = await asyncio.gather(
            *[agent_fn(task) for task in wave_tasks]
        )

        # Verify this wave's results immediately
        verify_run(run_id, list(wave_results), db_path)

        all_results.extend(wave_results)

        # Emit WebSocket event
        if ws_broadcast:
            await ws_broadcast(
                run_id,
                {
                    "event": f"wave_{wave_index}_done",
                    "wave_index": wave_index,
                    "tasks": wave,
                    "results_count": len(wave_results),
                },
            )

    return all_results
