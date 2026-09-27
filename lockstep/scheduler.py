"""
Lockstep — Wave scheduler (FR1.4).

Converts the conflict graph into a topologically ordered list of
parallel-safe execution waves.  Tasks with no conflict edges between
them share a wave; conflicting pairs are ordered writer-before-reader
across waves.

Uses NetworkX topological_generations() on a directed conflict DiGraph
where an edge A → B means "A must complete before B starts".
"""
from __future__ import annotations

import networkx as nx

from lockstep.models import get_connection


def schedule_run(run_id: str, db_path: str) -> list[list[str]]:
    """Compute parallel-safe execution waves for a run.

    Parameters
    ----------
    run_id:   The run to schedule.
    db_path:  Path to the Lockstep SQLite database.

    Returns
    -------
    Ordered list of waves; each wave is a list of task_id strings.
    Tasks within the same wave may run in true parallel.
    Tasks in wave N+1 must not start until all tasks in wave N are done.

    Side effects
    ------------
    Updates the `wave` column in the `tasks` table for every task in the run.
    """
    conn = get_connection(db_path)

    # Load all task IDs for this run
    task_rows = conn.execute(
        "SELECT task_id FROM tasks WHERE run_id = ?", (run_id,)
    ).fetchall()
    all_task_ids: list[str] = [r["task_id"] for r in task_rows]

    # Load all conflicts and determine edge direction: writer → reader
    conflict_rows = conn.execute(
        """
        SELECT c.task_a, c.task_b,
               ta.access_type AS access_a,
               tb.access_type AS access_b
        FROM conflicts c
        JOIN task_symbol_touches ta
            ON ta.task_id = c.task_a AND ta.symbol_id = c.symbol_id
        JOIN task_symbol_touches tb
            ON tb.task_id = c.task_b AND tb.symbol_id = c.symbol_id
        WHERE c.run_id = ?
        """,
        (run_id,),
    ).fetchall()

    # Build directed graph: writer → reader (writer runs first)
    G: nx.DiGraph = nx.DiGraph()
    G.add_nodes_from(all_task_ids)

    _write_types = {"write", "signature_change"}

    for row in conflict_rows:
        a, b = row["task_a"], row["task_b"]
        access_a, access_b = row["access_a"], row["access_b"]

        if access_a in _write_types and access_b not in _write_types:
            G.add_edge(a, b)  # a is writer → must precede b
        elif access_b in _write_types and access_a not in _write_types:
            G.add_edge(b, a)  # b is writer → must precede a
        else:
            # Both write or both signature_change — arbitrary ordering; a before b
            G.add_edge(a, b)

    # Produce waves using topological generations
    waves: list[list[str]] = []
    try:
        for generation in nx.topological_generations(G):
            waves.append(sorted(generation))  # sorted for determinism
    except nx.NetworkXUnfeasible:
        # Cycle detected (should not happen in a well-formed plan) — fall back
        waves = [[tid] for tid in all_task_ids]

    # Persist wave assignments
    with conn:
        for wave_index, wave in enumerate(waves):
            for task_id in wave:
                conn.execute(
                    "UPDATE tasks SET wave = ? WHERE task_id = ?",
                    (wave_index, task_id),
                )

    conn.close()
    return waves
