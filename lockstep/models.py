"""
Lockstep — SQLite data model using SQLAlchemy Core (no ORM, no migrations).

Tables
------
runs               One per planning session.
tasks              One per subagent declared in the plan.
symbols            A code or non-code resource named in any task.
task_symbol_touches  task × symbol × access_type junction.
conflicts          Detected pairwise conflicts with severity score.
execution_logs     Actual execution results per task (post-run).
reports            Assembled per-run summary report.
"""
import sqlite3
from pathlib import Path


# ---------------------------------------------------------------------------
# Connection helper
# ---------------------------------------------------------------------------

def get_connection(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


# ---------------------------------------------------------------------------
# Table definitions
# ---------------------------------------------------------------------------

_DDL = [
    # Run — one per planning session
    """
    CREATE TABLE IF NOT EXISTS runs (
        run_id      TEXT PRIMARY KEY,
        created_at  TEXT NOT NULL DEFAULT (datetime('now')),
        status      TEXT NOT NULL DEFAULT 'pending'
    )
    """,

    # Task — one per subagent declared in the plan.
    # task_id is stored as "{run_id}::{original_task_id}" to guarantee global
    # uniqueness while preserving the original task_id separately.
    """
    CREATE TABLE IF NOT EXISTS tasks (
        task_id          TEXT PRIMARY KEY,
        original_task_id TEXT NOT NULL,
        run_id           TEXT NOT NULL REFERENCES runs(run_id),
        subagent_id      TEXT NOT NULL,
        description      TEXT NOT NULL,
        files_json       TEXT NOT NULL DEFAULT '[]',
        wave             INTEGER
    )
    """,

    # Symbol — a function/class/route/column/config_key/migration_file
    # kind values: function | class | route | column | config_key | migration_file
    # Uniqueness is on name alone so that the same symbol referenced by multiple
    # tasks (from different files) resolves to the same symbol_id.
    """
    CREATE TABLE IF NOT EXISTS symbols (
        symbol_id   INTEGER PRIMARY KEY AUTOINCREMENT,
        name        TEXT NOT NULL,
        kind        TEXT NOT NULL,
        file_path   TEXT NOT NULL DEFAULT '',
        UNIQUE(name)
    )
    """,

    # TaskSymbolTouch — task × symbol × access_type
    # access_type: read | write | signature_change
    """
    CREATE TABLE IF NOT EXISTS task_symbol_touches (
        touch_id    INTEGER PRIMARY KEY AUTOINCREMENT,
        task_id     TEXT NOT NULL REFERENCES tasks(task_id),
        symbol_id   INTEGER NOT NULL REFERENCES symbols(symbol_id),
        access_type TEXT NOT NULL,
        line_number INTEGER,
        confidence  REAL NOT NULL DEFAULT 1.0,
        UNIQUE(task_id, symbol_id, access_type)
    )
    """,

    # Conflict — detected pairwise conflict
    # resolution: serialize | notify | shim
    """
    CREATE TABLE IF NOT EXISTS conflicts (
        conflict_id     INTEGER PRIMARY KEY AUTOINCREMENT,
        run_id          TEXT NOT NULL REFERENCES runs(run_id),
        task_a          TEXT NOT NULL REFERENCES tasks(task_id),
        task_b          TEXT NOT NULL REFERENCES tasks(task_id),
        symbol_id       INTEGER NOT NULL REFERENCES symbols(symbol_id),
        severity_score  REAL NOT NULL DEFAULT 0.0,
        resolution      TEXT NOT NULL DEFAULT 'serialize'
    )
    """,

    # ExecutionLog — actual execution results per task
    """
    CREATE TABLE IF NOT EXISTS execution_logs (
        log_id          INTEGER PRIMARY KEY AUTOINCREMENT,
        task_id         TEXT NOT NULL REFERENCES tasks(task_id),
        started_at      TEXT,
        ended_at        TEXT,
        actual_files    TEXT NOT NULL DEFAULT '[]',
        plan_deviation  INTEGER NOT NULL DEFAULT 0
    )
    """,

    # Report — per-run assembled summary
    """
    CREATE TABLE IF NOT EXISTS reports (
        report_id   INTEGER PRIMARY KEY AUTOINCREMENT,
        run_id      TEXT NOT NULL REFERENCES runs(run_id),
        summary     TEXT NOT NULL DEFAULT '',
        timing_json TEXT NOT NULL DEFAULT '{}'
    )
    """,
]


def create_tables(db_path: str) -> None:
    """Initialise all Lockstep tables in a fresh or existing SQLite file."""
    conn = get_connection(db_path)
    with conn:
        for ddl in _DDL:
            conn.execute(ddl)
    conn.close()
