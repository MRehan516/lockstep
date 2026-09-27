"""
Lockstep — Shim generator (FR2.5).

For signature_change conflicts, generates a temporary backward-compatible
compatibility shim so both tasks can proceed in true parallel.

The shim wraps the new function signature with defaults for the new
required parameters so that old call sites (without the new param) continue
to work until the follow-up cleanup task removes the shim.
"""
from __future__ import annotations

import ast
import os
import re
import textwrap
from dataclasses import dataclass
from pathlib import Path

from lockstep.models import get_connection


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------

@dataclass
class ShimResult:
    symbol_name: str
    shim_file: str          # path of the generated shim file
    cleanup_task_id: str    # ID of the auto-generated cleanup task in the DB
    shim_source: str        # the generated Python source


# ---------------------------------------------------------------------------
# AST helpers
# ---------------------------------------------------------------------------

def _find_function_node(
    source: str, func_name: str
) -> ast.FunctionDef | ast.AsyncFunctionDef | None:
    """Return the AST node for a function definition by name."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return None
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.name == func_name:
                return node
    return None


def _extract_params(node: ast.FunctionDef | ast.AsyncFunctionDef) -> list[str]:
    """Return a list of parameter names (excluding 'self'/'cls')."""
    args = node.args
    params: list[str] = []
    for arg in args.args:
        if arg.arg not in ("self", "cls"):
            params.append(arg.arg)
    return params


def _infer_new_params(description: str, existing_params: list[str]) -> list[str]:
    """Infer newly added parameter names from a task description.

    Looks for phrases like: "add a required `region` parameter" or
    "add `region` parameter" in the description text.
    Falls back to looking for backtick-quoted identifiers not already in
    existing_params.
    """
    new_params: list[str] = []
    # Match: add ... `param` ... parameter
    for match in re.finditer(r"`(\w+)`", description):
        candidate = match.group(1)
        if candidate not in existing_params and not candidate[0].isupper():
            new_params.append(candidate)
    return new_params


# ---------------------------------------------------------------------------
# Shim source generator
# ---------------------------------------------------------------------------

def _generate_shim_source(
    symbol_name: str,
    existing_params: list[str],
    new_params: list[str],
) -> str:
    """Generate Python source for a backward-compatible shim function.

    The shim accepts both the old signature (without new params) and the new
    signature (with new params).  When called with old args (new params=None),
    it forwards only the original params to the underlying function so that
    the old implementation (pre-Task-1) still works.
    """
    all_params = existing_params + [f"{p}=None" for p in new_params]
    sig = ", ".join(all_params)
    old_call = ", ".join(existing_params)

    # Build conditional forwarding body (4-space indent inside function)
    if new_params:
        new_kwargs = ", ".join(f"{p}={p}" for p in new_params)
        call_body = (
            f"    if all(p is None for p in [{', '.join(new_params)}]):\n"
            f"        return _{symbol_name}_impl({old_call})\n"
            f"    return _{symbol_name}_impl({old_call}, {new_kwargs})"
        )
    else:
        call_body = f"    return _{symbol_name}_impl({old_call})"

    lines = [
        '"""',
        "AUTO-GENERATED LOCKSTEP SHIM — DO NOT EDIT MANUALLY.",
        "",
        f"This shim provides backward compatibility for `{symbol_name}` while",
        "Task 1 (signature_change) and Task 2 (read) run in parallel.",
        "",
        "Remove this file once both tasks are merged (see cleanup task in DB).",
        '"""',
        f"from galaxium.shipping import {symbol_name} as _{symbol_name}_impl",
        "",
        "",
        f"def {symbol_name}({sig}):",
        f'    """Backward-compatible shim for {symbol_name}.',
        "",
        "    Accepts the old signature as well as the new extended signature.",
        "    New parameters default to None if not provided.",
        '    """',
        call_body,
    ]
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def generate_shim(
    conflict_id: int,
    db_path: str,
    repo_root: str,
    new_param_description: str = "",
) -> ShimResult:
    """Generate a compatibility shim for a signature_change conflict.

    Parameters
    ----------
    conflict_id:          The conflict to generate a shim for.
    db_path:              Path to the Lockstep SQLite database.
    repo_root:            Absolute path to the target repository root.
    new_param_description: Text hinting at new parameter names
                           (e.g. the task description). Falls back to
                           the stored task description if empty.

    Returns
    -------
    ShimResult with shim file path, source, and cleanup task ID.

    Side effects
    ------------
    Writes the shim file to galaxium/shims/.
    Inserts a follow-up cleanup Task into the DB.
    Updates conflicts.resolution to 'shim'.
    """
    conn = get_connection(db_path)

    # Load conflict + symbol info
    row = conn.execute(
        """
        SELECT c.run_id, c.task_a, c.task_b, s.name AS symbol_name, s.file_path,
               t.description AS task_a_desc
        FROM conflicts c
        JOIN symbols s ON c.symbol_id = s.symbol_id
        JOIN tasks t ON t.task_id = c.task_a
        WHERE c.conflict_id = ?
        """,
        (conflict_id,),
    ).fetchone()

    if not row:
        raise ValueError(f"No conflict found with conflict_id={conflict_id}")

    symbol_name: str = row["symbol_name"]
    run_id: str = row["run_id"]
    description = new_param_description or row["task_a_desc"] or ""

    # Find the symbol definition file
    symbol_file = row["file_path"]
    if symbol_file and not os.path.isabs(symbol_file):
        symbol_file = os.path.join(repo_root, symbol_file)

    existing_params: list[str] = []
    if symbol_file and os.path.isfile(symbol_file):
        source = Path(symbol_file).read_text(encoding="utf-8")
        func_node = _find_function_node(source, symbol_name)
        if func_node:
            existing_params = _extract_params(func_node)

    # Infer new params from description
    new_params = _infer_new_params(description, existing_params)

    # Generate shim source
    shim_source = _generate_shim_source(symbol_name, existing_params, new_params)

    # Write shim file
    shim_dir = os.path.join(repo_root, "galaxium", "shims")
    os.makedirs(shim_dir, exist_ok=True)
    shim_filename = f"shim_{symbol_name}.py"
    shim_path = os.path.join(shim_dir, shim_filename)
    Path(shim_path).write_text(shim_source, encoding="utf-8")

    # Create cleanup task in DB
    # Composite task_id: run_id::original to maintain FK integrity
    original_cleanup_id = f"cleanup-shim-{symbol_name}-{conflict_id}"
    cleanup_task_id = f"{run_id}::{original_cleanup_id}"
    with conn:
        conn.execute(
            """
            INSERT OR IGNORE INTO tasks (task_id, original_task_id, run_id, subagent_id, description, files_json)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                cleanup_task_id,
                original_cleanup_id,
                run_id,
                "lockstep-auto",
                f"Remove shim for {symbol_name} (galaxium/shims/{shim_filename})",
                f'["galaxium/shims/{shim_filename}"]',
            ),
        )
        # Update resolution
        conn.execute(
            "UPDATE conflicts SET resolution = 'shim' WHERE conflict_id = ?",
            (conflict_id,),
        )

    conn.close()

    return ShimResult(
        symbol_name=symbol_name,
        shim_file=shim_path,
        cleanup_task_id=cleanup_task_id,
        shim_source=shim_source,
    )
