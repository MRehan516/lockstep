"""
Lockstep — Post-execution verifier (FR1.5).

After subagents run, compares actual changed files against each task's
declared touch-set and flags plan deviations.
"""
from __future__ import annotations

import json
from datetime import datetime

from lockstep.models import get_connection


def verify_run(
    run_id: str,
    execution_results: list[dict],
    db_path: str,
) -> None:
    """Persist execution logs and flag plan deviations.

    Parameters
    ----------
    run_id:             The run being verified.
    execution_results:  List of dicts, each with:
                          task_id          str
                          actual_files     list[str]  — files actually changed
                          started_at       str | None — ISO timestamp
                          ended_at         str | None — ISO timestamp
    db_path:            Path to the Lockstep SQLite database.

    Side effects
    ------------
    Inserts rows into `execution_logs`.
    Sets `plan_deviation = 1` for tasks that touched undeclared files.
    Updates `runs.status` to 'completed'.
    """
    conn = get_connection(db_path)

    # Build map of original_task_id → composite task_id, and declared files
    task_rows = conn.execute(
        "SELECT task_id, original_task_id FROM tasks WHERE run_id = ?", (run_id,)
    ).fetchall()
    # Allow callers to pass either the composite or original task_id
    orig_to_composite: dict[str, str] = {
        r["original_task_id"]: r["task_id"] for r in task_rows
    }
    composite_set: set[str] = {r["task_id"] for r in task_rows}

    declared_files_by_task: dict[str, set[str]] = _load_declared_files(run_id, db_path)

    with conn:
        for result in execution_results:
            raw_task_id: str = result.get("task_id", "")
            # Resolve to composite task_id
            task_id: str = orig_to_composite.get(raw_task_id, raw_task_id)
            if task_id not in composite_set:
                continue  # unknown task — skip

            actual_files: list[str] = result.get("actual_files", [])
            started_at: str | None = result.get("started_at")
            ended_at: str | None = result.get("ended_at")

            declared = declared_files_by_task.get(task_id, set())
            extra_files = set(actual_files) - declared
            plan_deviation = 1 if extra_files else 0

            conn.execute(
                """
                INSERT INTO execution_logs
                    (task_id, started_at, ended_at, actual_files, plan_deviation)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    task_id,
                    started_at or datetime.utcnow().isoformat(),
                    ended_at or datetime.utcnow().isoformat(),
                    json.dumps(actual_files),
                    plan_deviation,
                ),
            )

        # Mark run as completed
        conn.execute(
            "UPDATE runs SET status = 'completed' WHERE run_id = ?", (run_id,)
        )

    conn.close()


def _load_declared_files(run_id: str, db_path: str) -> dict[str, set[str]]:
    """Load the declared file lists for each task in the run.

    The ingester stores a `files_json` column in tasks if available.
    If the column doesn't exist we return empty sets (deviation = any file).
    """
    conn = get_connection(db_path)
    result: dict[str, set[str]] = {}
    try:
        rows = conn.execute(
            "SELECT task_id, files_json FROM tasks WHERE run_id = ?", (run_id,)
        ).fetchall()
        for row in rows:
            files = json.loads(row["files_json"] or "[]")
            result[row["task_id"]] = set(files)
    except Exception:
        # Column doesn't exist yet — all tasks will show deviation=0
        rows = conn.execute(
            "SELECT task_id FROM tasks WHERE run_id = ?", (run_id,)
        ).fetchall()
        for row in rows:
            result[row["task_id"]] = set()
    conn.close()
    return result
