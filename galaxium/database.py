"""
Galaxium Travels — database setup.
Uses stdlib sqlite3; no ORM.
"""
import sqlite3
import os
from pathlib import Path

DB_PATH = os.environ.get("GALAXIUM_DB", str(Path(__file__).parent / "galaxium.db"))


def get_connection(db_path: str = DB_PATH) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def create_tables(db_path: str = DB_PATH) -> None:
    """Create all Galaxium tables if they don't exist."""
    conn = get_connection(db_path)
    with conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS orders (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                destination     TEXT    NOT NULL,
                weight          REAL    NOT NULL,
                region          TEXT    NOT NULL,
                shipping_cost   REAL    NOT NULL,
                created_at      TEXT    NOT NULL DEFAULT (datetime('now'))
            )
            """
        )
    conn.close()


if __name__ == "__main__":
    create_tables()
    print(f"Database initialised at {DB_PATH}")
