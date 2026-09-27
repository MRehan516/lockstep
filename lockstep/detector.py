"""
Lockstep — Conflict detector (FR1.3 + FR2.2).

Detects pairwise conflicts between tasks sharing a symbol where at least
one access is write or signature_change.  Each conflict carries a weighted
severity score computed from four factors:

  access_severity    (weight 0.40)
  blast_radius       (weight 0.20) — normalised call-site count
  centrality         (weight 0.20) — NetworkX degree centrality
  confidence_penalty (weight 0.20) — low confidence raises severity

Resolution defaults to 'serialize' (changed to 'shim' by the shim module).
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

import networkx as nx

from lockstep.models import get_connection
from lockstep.extractor import extract_touch_set, find_call_sites


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------

@dataclass
class Conflict:
    conflict_id: int | None
    run_id: str
    task_a: str
    task_b: str
    symbol_id: int
    symbol_name: str
    severity_score: float
    resolution: str = "serialize"


# ---------------------------------------------------------------------------
# Severity helpers
# ---------------------------------------------------------------------------

_ACCESS_SEVERITY: dict[str, float] = {
    "signature_change": 1.0,
    "write": 0.7,
    "read": 0.4,
}


def _access_severity(access_a: str, access_b: str) -> float:
    """Return the severity weight for the more severe of the two accesses."""
    return max(_ACCESS_SEVERITY.get(access_a, 0.4), _ACCESS_SEVERITY.get(access_b, 0.4))


def _blast_radius(symbol_name: str, repo_root: str, max_sites: int = 5) -> float:
    """Normalised call-site count for a symbol (0–1).

    max_sites is calibrated to small-to-medium repos (default 5).
    Any symbol called 5+ times saturates at 1.0.
    """
    if not repo_root or not os.path.isdir(repo_root):
        return 0.0
    sites = find_call_sites(symbol_name, repo_root)
    return min(len(sites) / max_sites, 1.0)


def _centrality_score(symbol_id: int, graph: nx.Graph) -> float:
    """NetworkX degree centrality for a symbol node (0–1)."""
    node = f"sym:{symbol_id}"
    if node not in graph:
        return 0.0
    centrality = nx.degree_centrality(graph)
    return centrality.get(node, 0.0)


def _confidence_penalty(confidence_a: float, confidence_b: float) -> float:
    """Low confidence raises effective severity: 1 - min(conf_a, conf_b)."""
    return 1.0 - min(confidence_a, confidence_b)


def compute_severity(
    access_a: str,
    access_b: str,
    confidence_a: float,
    confidence_b: float,
    symbol_name: str,
    symbol_id: int,
    graph: nx.Graph,
    repo_root: str,
) -> float:
    """Compute a weighted severity score in [0, 1].

    Formula:
        0.40 * access_severity
      + 0.20 * blast_radius
      + 0.20 * centrality
      + 0.20 * confidence_penalty
    """
    score = (
        0.40 * _access_severity(access_a, access_b)
        + 0.20 * _blast_radius(symbol_name, repo_root)
        + 0.20 * _centrality_score(symbol_id, graph)
        + 0.20 * _confidence_penalty(confidence_a, confidence_b)
    )
    return round(min(score, 1.0), 4)


# ---------------------------------------------------------------------------
# Touch-set graph builder
# ---------------------------------------------------------------------------

def _build_touch_graph(touches: list[dict]) -> nx.Graph:
    """Build an undirected bipartite graph: task nodes ↔ symbol nodes."""
    G = nx.Graph()
    for t in touches:
        task_node = f"task:{t['task_id']}"
        sym_node = f"sym:{t['symbol_id']}"
        G.add_node(task_node, kind="task")
        G.add_node(sym_node, kind="symbol")
        G.add_edge(task_node, sym_node, access_type=t["access_type"])
    return G


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def detect_conflicts(run_id: str, db_path: str, repo_root: str = "") -> list[Conflict]:
    """Detect pairwise conflicts for a run and persist them to the DB.

    Parameters
    ----------
    run_id:    The run to analyse.
    db_path:   Path to the Lockstep SQLite database.
    repo_root: Path to the target repository root (used for blast-radius
               calculation). Pass empty string to skip.

    Returns
    -------
    List of Conflict dataclass instances (also persisted to DB).
    """
    conn = get_connection(db_path)

    # Load all touches for this run
    rows = conn.execute(
        """
        SELECT tst.task_id, tst.symbol_id, tst.access_type, tst.confidence,
               s.name AS symbol_name, s.kind AS symbol_kind
        FROM task_symbol_touches tst
        JOIN tasks t ON tst.task_id = t.task_id
        JOIN symbols s ON tst.symbol_id = s.symbol_id
        WHERE t.run_id = ?
        """,
        (run_id,),
    ).fetchall()

    # Group touches by symbol_id → list of {task_id, access_type, confidence}
    sym_touches: dict[int, list[dict[str, Any]]] = {}
    for row in rows:
        sid = row["symbol_id"]
        sym_touches.setdefault(sid, []).append(
            {
                "task_id": row["task_id"],
                "symbol_id": sid,
                "symbol_name": row["symbol_name"],
                "access_type": row["access_type"],
                "confidence": row["confidence"],
            }
        )

    # Build touch graph for centrality calculation
    all_touches = [item for items in sym_touches.values() for item in items]
    touch_graph = _build_touch_graph(all_touches)

    conflicts: list[Conflict] = []

    for sym_id, touches in sym_touches.items():
        if len(touches) < 2:
            continue  # only one task touches this symbol — no conflict possible

        sym_name: str = touches[0]["symbol_name"]

        # Check all pairs
        for i in range(len(touches)):
            for j in range(i + 1, len(touches)):
                ta = touches[i]
                tb = touches[j]

                if ta["task_id"] == tb["task_id"]:
                    continue  # same task — not a conflict

                # Conflict rule: at least one touch must be write or signature_change
                write_access = {"write", "signature_change"}
                if ta["access_type"] not in write_access and tb["access_type"] not in write_access:
                    continue  # both reads — no conflict

                severity = compute_severity(
                    access_a=ta["access_type"],
                    access_b=tb["access_type"],
                    confidence_a=ta["confidence"],
                    confidence_b=tb["confidence"],
                    symbol_name=sym_name,
                    symbol_id=sym_id,
                    graph=touch_graph,
                    repo_root=repo_root,
                )

                # Persist conflict
                with conn:
                    cursor = conn.execute(
                        """
                        INSERT INTO conflicts
                            (run_id, task_a, task_b, symbol_id, severity_score, resolution)
                        VALUES (?, ?, ?, ?, ?, ?)
                        """,
                        (run_id, ta["task_id"], tb["task_id"], sym_id, severity, "serialize"),
                    )
                    conflict_id = cursor.lastrowid

                conflicts.append(
                    Conflict(
                        conflict_id=conflict_id,
                        run_id=run_id,
                        task_a=ta["task_id"],
                        task_b=tb["task_id"],
                        symbol_id=sym_id,
                        symbol_name=sym_name,
                        severity_score=severity,
                        resolution="serialize",
                    )
                )

    conn.close()
    return conflicts
