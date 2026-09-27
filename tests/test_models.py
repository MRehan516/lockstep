"""
Tests for Lockstep data model — create_tables and round-trip inserts.
"""
import tempfile
import os
import json
import sqlite3

import pytest

from lockstep.models import create_tables, get_connection


def _fresh_db():
    f = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    f.close()
    return f.name


def test_create_tables_idempotent():
    db = _fresh_db()
    try:
        create_tables(db)
        create_tables(db)  # second call must not raise
    finally:
        os.unlink(db)


def test_round_trip_all_tables():
    db = _fresh_db()
    try:
        create_tables(db)
        conn = get_connection(db)
        with conn:
            # runs
            conn.execute("INSERT INTO runs (run_id, status) VALUES (?, ?)", ("run-1", "pending"))
            # tasks
            conn.execute(
                "INSERT INTO tasks (task_id, original_task_id, run_id, subagent_id, description) VALUES (?, ?, ?, ?, ?)",
                ("run-1::task-1", "task-1", "run-1", "agent-a", "Do something"),
            )
            # symbols
            conn.execute(
                "INSERT INTO symbols (name, kind, file_path) VALUES (?, ?, ?)",
                ("calculateShipping", "function", "galaxium/shipping.py"),
            )
            sym_id = conn.execute(
                "SELECT symbol_id FROM symbols WHERE name = ?", ("calculateShipping",)
            ).fetchone()["symbol_id"]
            # task_symbol_touches
            conn.execute(
                "INSERT INTO task_symbol_touches (task_id, symbol_id, access_type, confidence) VALUES (?, ?, ?, ?)",
                ("run-1::task-1", sym_id, "signature_change", 1.0),
            )
            # conflicts
            conn.execute(
                "INSERT INTO conflicts (run_id, task_a, task_b, symbol_id, severity_score, resolution) VALUES (?, ?, ?, ?, ?, ?)",
                ("run-1", "run-1::task-1", "run-1::task-1", sym_id, 0.9, "serialize"),
            )
            # execution_logs
            conn.execute(
                "INSERT INTO execution_logs (task_id, actual_files, plan_deviation) VALUES (?, ?, ?)",
                ("run-1::task-1", json.dumps(["galaxium/shipping.py"]), 0),
            )
            # reports
            conn.execute(
                "INSERT INTO reports (run_id, summary, timing_json) VALUES (?, ?, ?)",
                ("run-1", "All good", json.dumps({"naive_serial_ms": 100, "scheduled_ms": 60})),
            )

        # Assert each table has the inserted row
        assert conn.execute("SELECT COUNT(*) FROM runs").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM tasks").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM symbols").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM task_symbol_touches").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM conflicts").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM execution_logs").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM reports").fetchone()[0] == 1
        conn.close()
    finally:
        os.unlink(db)


def test_symbol_unique_constraint():
    """Same symbol name cannot be inserted twice (unique on name)."""
    db = _fresh_db()
    try:
        create_tables(db)
        conn = get_connection(db)
        with conn:
            conn.execute(
                "INSERT INTO symbols (name, kind, file_path) VALUES (?, ?, ?)",
                ("foo", "function", "bar.py"),
            )
        # Same name, different file — must still raise due to UNIQUE(name)
        with pytest.raises(sqlite3.IntegrityError):
            with conn:
                conn.execute(
                    "INSERT INTO symbols (name, kind, file_path) VALUES (?, ?, ?)",
                    ("foo", "function", "other.py"),
                )
        conn.close()
    finally:
        os.unlink(db)
