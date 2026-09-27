"""
Lockstep — Report generator (FR1.6 + FR2.6).

Assembles the final per-run report from all persisted data and surfaces
"time saved" by comparing naive-serial vs. scheduled execution time.
"""
from __future__ import annotations

import json
from datetime import datetime

from lockstep.models import get_connection


def build_report(run_id: str, db_path: str) -> dict:
    """Assemble and persist the final report for a run.

    Parameters
    ----------
    run_id:   The run to report on.
    db_path:  Path to the Lockstep SQLite database.

    Returns
    -------
    A dict with keys:
      run_id, task_count, conflict_count, waves, conflicts (detail),
      deviations, timing (naive_serial_ms, scheduled_ms, time_saved_ms).

    Side effects
    ------------
    Inserts a row into the `reports` table.
    """
    conn = get_connection(db_path)

    # -- Tasks ----------------------------------------------------------------
    task_rows = conn.execute(
        "SELECT task_id, subagent_id, description, wave FROM tasks WHERE run_id = ?",
        (run_id,),
    ).fetchall()
    task_count = len(task_rows)
    waves_map: dict[int, list[str]] = {}
    for r in task_rows:
        wave = r["wave"] if r["wave"] is not None else 0
        waves_map.setdefault(wave, []).append(r["task_id"])
    # Sorted wave list [[task_ids in wave 0], [task_ids in wave 1], ...]
    waves_list = [waves_map[k] for k in sorted(waves_map.keys())]

    # -- Conflicts ------------------------------------------------------------
    conflict_rows = conn.execute(
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
    conflict_count = len(conflict_rows)
    conflicts_detail = [
        {
            "conflict_id": r["conflict_id"],
            "task_a": r["task_a"],
            "task_b": r["task_b"],
            "symbol": r["symbol_name"],
            "severity_score": r["severity_score"],
            "resolution": r["resolution"],
        }
        for r in conflict_rows
    ]

    # -- Execution logs & timing ----------------------------------------------
    log_rows = conn.execute(
        """
        SELECT el.task_id, el.started_at, el.ended_at,
               el.actual_files, el.plan_deviation
        FROM execution_logs el
        JOIN tasks t ON el.task_id = t.task_id
        WHERE t.run_id = ?
        """,
        (run_id,),
    ).fetchall()

    deviations = [
        {"task_id": r["task_id"], "actual_files": json.loads(r["actual_files"])}
        for r in log_rows
        if r["plan_deviation"]
    ]

    # Timing calculation
    naive_serial_ms, scheduled_ms, time_saved_ms = _compute_timing(log_rows, waves_list)

    timing = {
        "naive_serial_ms": naive_serial_ms,
        "scheduled_ms": scheduled_ms,
        "time_saved_ms": time_saved_ms,
    }

    # -- Assemble report ------------------------------------------------------
    report = {
        "run_id": run_id,
        "task_count": task_count,
        "conflict_count": conflict_count,
        "waves": waves_list,
        "conflicts": conflicts_detail,
        "deviations": deviations,
        "timing": timing,
    }

    summary = (
        f"Run {run_id}: {task_count} tasks across {len(waves_list)} waves, "
        f"{conflict_count} conflict(s) detected, "
        f"{len(deviations)} plan deviation(s)."
    )

    with conn:
        conn.execute(
            "INSERT INTO reports (run_id, summary, timing_json) VALUES (?, ?, ?)",
            (run_id, summary, json.dumps(timing)),
        )

    conn.close()
    return report


# ---------------------------------------------------------------------------
# Timing helpers
# ---------------------------------------------------------------------------

def _parse_ts(ts: str | None) -> datetime | None:
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts)
    except ValueError:
        return None


def _ms(delta) -> int:
    return int(delta.total_seconds() * 1000)


def _compute_timing(
    log_rows: list,
    waves_list: list[list[str]],
) -> tuple[int, int, int]:
    """Return (naive_serial_ms, scheduled_ms, time_saved_ms).

    naive_serial_ms = sum of individual task durations (as if serialised).
    scheduled_ms    = wall-clock time from first start to last end across all waves.
    time_saved_ms   = naive_serial_ms - scheduled_ms  (>=0).
    """
    durations: dict[str, int] = {}
    all_starts: list[datetime] = []
    all_ends: list[datetime] = []

    for row in log_rows:
        s = _parse_ts(row["started_at"])
        e = _parse_ts(row["ended_at"])
        if s and e and e >= s:
            dur = _ms(e - s)
            durations[row["task_id"]] = dur
            all_starts.append(s)
            all_ends.append(e)

    naive_serial_ms = sum(durations.values())
    if all_starts and all_ends:
        scheduled_ms = _ms(max(all_ends) - min(all_starts))
    else:
        scheduled_ms = naive_serial_ms  # no timing data

    time_saved_ms = max(0, naive_serial_ms - scheduled_ms)
    return naive_serial_ms, scheduled_ms, time_saved_ms
