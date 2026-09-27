"""
Lockstep — Plan JSON ingester (FR1.1).

Parses and validates an incoming Plan JSON (Appendix A schema) and
persists Run, Task, Symbol, and TaskSymbolTouch records to SQLite.
"""
from __future__ import annotations

import json
import uuid
from typing import Any

from lockstep.models import create_tables, get_connection


# ---------------------------------------------------------------------------
# Error type
# ---------------------------------------------------------------------------

class LockstepIngestionError(ValueError):
    """Raised when a Plan JSON fails schema validation."""


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

_REQUIRED_TOP_LEVEL = {"run_id", "tasks"}
_REQUIRED_TASK_KEYS = {"task_id", "subagent_id", "description", "files", "symbols_touched"}
_REQUIRED_SYMBOL_KEYS = {"name", "kind", "access_type"}
_VALID_KINDS = {"function", "class", "route", "column", "config_key", "migration_file"}
_VALID_ACCESS = {"read", "write", "signature_change"}


def validate_plan(plan: dict[str, Any]) -> None:
    """Validate plan JSON against the Appendix A schema.

    Raises
    ------
    LockstepIngestionError  If any required field is absent or invalid.
    """
    missing_top = _REQUIRED_TOP_LEVEL - set(plan.keys())
    if missing_top:
        raise LockstepIngestionError(f"Plan missing required top-level keys: {missing_top}")

    if not isinstance(plan["tasks"], list) or len(plan["tasks"]) == 0:
        raise LockstepIngestionError("Plan 'tasks' must be a non-empty list")

    for i, task in enumerate(plan["tasks"]):
        missing_task = _REQUIRED_TASK_KEYS - set(task.keys())
        if missing_task:
            raise LockstepIngestionError(
                f"Task[{i}] missing required keys: {missing_task}"
            )
        if not isinstance(task["files"], list):
            raise LockstepIngestionError(f"Task[{i}] 'files' must be a list")
        if not isinstance(task["symbols_touched"], list):
            raise LockstepIngestionError(f"Task[{i}] 'symbols_touched' must be a list")

        for j, sym in enumerate(task["symbols_touched"]):
            missing_sym = _REQUIRED_SYMBOL_KEYS - set(sym.keys())
            if missing_sym:
                raise LockstepIngestionError(
                    f"Task[{i}].symbols_touched[{j}] missing keys: {missing_sym}"
                )
            if sym["kind"] not in _VALID_KINDS:
                raise LockstepIngestionError(
                    f"Task[{i}].symbols_touched[{j}] invalid kind '{sym['kind']}'. "
                    f"Must be one of {_VALID_KINDS}"
                )
            if sym["access_type"] not in _VALID_ACCESS:
                raise LockstepIngestionError(
                    f"Task[{i}].symbols_touched[{j}] invalid access_type "
                    f"'{sym['access_type']}'. Must be one of {_VALID_ACCESS}"
                )


# ---------------------------------------------------------------------------
# Ingestion
# ---------------------------------------------------------------------------

def ingest_plan(plan_json: dict[str, Any], db_path: str) -> str:
    """Validate and persist a Plan JSON to the Lockstep SQLite database.

    Parameters
    ----------
    plan_json:  A dict matching the Appendix A schema.
    db_path:    Path to the SQLite database file.

    Returns
    -------
    The run_id string for the ingested run.

    Raises
    ------
    LockstepIngestionError  If the plan fails schema validation.
    """
    validate_plan(plan_json)

    create_tables(db_path)
    conn = get_connection(db_path)

    run_id: str = plan_json["run_id"]

    with conn:
        # Insert run
        conn.execute(
            "INSERT OR IGNORE INTO runs (run_id, status) VALUES (?, ?)",
            (run_id, "pending"),
        )

        for task in plan_json["tasks"]:
            original_task_id: str = task["task_id"]
            # Composite key ensures the same task_id may be used across runs
            task_id: str = f"{run_id}::{original_task_id}"

            # Insert task (including files_json for verifier use)
            conn.execute(
                """
                INSERT OR IGNORE INTO tasks
                    (task_id, original_task_id, run_id, subagent_id, description, files_json)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (task_id, original_task_id, run_id,
                 task["subagent_id"], task["description"],
                 json.dumps(task.get("files", []))),
            )

            for sym in task["symbols_touched"]:
                sym_name: str = sym["name"]
                sym_kind: str = sym["kind"]
                # Use the first matching file from the task's files list as
                # the file_path hint; fall back to empty string.
                file_hint = _infer_file_path(sym_name, task.get("files", []))

                # Upsert symbol — unique on name only, so the same symbol
                # referenced by multiple tasks gets the same symbol_id.
                conn.execute(
                    """
                    INSERT OR IGNORE INTO symbols (name, kind, file_path)
                    VALUES (?, ?, ?)
                    """,
                    (sym_name, sym_kind, file_hint),
                )
                symbol_id: int = conn.execute(
                    "SELECT symbol_id FROM symbols WHERE name = ?",
                    (sym_name,),
                ).fetchone()["symbol_id"]

                # Insert touch (ignore duplicate task+symbol+access combos)
                conn.execute(
                    """
                    INSERT OR IGNORE INTO task_symbol_touches
                        (task_id, symbol_id, access_type, confidence)
                    VALUES (?, ?, ?, ?)
                    """,
                    (task_id, symbol_id, sym["access_type"], 1.0),
                )

    conn.close()
    return run_id


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _infer_file_path(symbol_name: str, files: list[str]) -> str:
    """Return the most likely file path for a symbol from the task's file list.

    Prefers a file whose basename (without extension) matches the symbol name,
    then falls back to the first file in the list, then to empty string.
    """
    if not files:
        return ""
    symbol_lower = symbol_name.lower()
    for f in files:
        stem = f.replace("\\", "/").split("/")[-1].rsplit(".", 1)[0].lower()
        if stem == symbol_lower:
            return f
    return files[0]
