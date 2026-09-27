"""
Lockstep — AST-based touch-set extractor (FR1.2).

For each task's declared symbols, walks the target repo's Python source
with the stdlib `ast` module to:
  - Confirm the symbol definition exists (function def / class statement).
  - Find all call sites.
  - Classify each touch as read / write / signature_change.
  - Assign a confidence score (1.0 = direct AST hit, 0.7 = heuristic).

Non-code resource scanning (config keys, migration files) is implemented
in scan_non_code_resources() per FR2.4 (ST-12).
"""
from __future__ import annotations

import ast
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------

@dataclass
class TouchRecord:
    symbol_name: str
    kind: str                     # function | class | route | column | config_key | migration_file
    access_type: str              # read | write | signature_change
    file_path: str
    line_number: int
    confidence: float = 1.0


# ---------------------------------------------------------------------------
# Internal AST helpers
# ---------------------------------------------------------------------------

def _iter_python_files(repo_root: str) -> Iterator[str]:
    """Yield absolute paths of all .py files under repo_root."""
    for dirpath, dirnames, filenames in os.walk(repo_root):
        # Skip hidden dirs and common non-source dirs
        dirnames[:] = [
            d for d in dirnames
            if not d.startswith(".") and d not in ("__pycache__", ".venv", "venv", "node_modules")
        ]
        for fname in filenames:
            if fname.endswith(".py"):
                yield os.path.join(dirpath, fname)


def _parse_file(file_path: str) -> ast.Module | None:
    """Parse a Python file; return None on syntax error."""
    try:
        with open(file_path, "r", encoding="utf-8", errors="replace") as f:
            source = f.read()
        return ast.parse(source, filename=file_path)
    except (SyntaxError, OSError):
        return None


def find_symbol_definition(
    symbol_name: str,
    kind: str,
    repo_root: str,
) -> list[tuple[str, int]]:
    """Return [(file_path, line_number)] for every definition of symbol_name.

    For kind='function', matches ast.FunctionDef and ast.AsyncFunctionDef.
    For kind='class', matches ast.ClassDef.
    Other kinds fall back to a name-based scan.
    """
    results: list[tuple[str, int]] = []
    for fpath in _iter_python_files(repo_root):
        tree = _parse_file(fpath)
        if tree is None:
            continue
        for node in ast.walk(tree):
            if kind in ("function", "route"):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    if node.name == symbol_name:
                        results.append((fpath, node.lineno))
            elif kind == "class":
                if isinstance(node, ast.ClassDef) and node.name == symbol_name:
                    results.append((fpath, node.lineno))
            else:
                # Heuristic: look for assignments with a matching name
                if isinstance(node, ast.Assign):
                    for target in node.targets:
                        if isinstance(target, ast.Name) and target.id == symbol_name:
                            results.append((fpath, node.lineno))
    return results


def find_call_sites(symbol_name: str, repo_root: str) -> list[tuple[str, int]]:
    """Return [(file_path, line_number)] for every call site of symbol_name."""
    results: list[tuple[str, int]] = []
    for fpath in _iter_python_files(repo_root):
        tree = _parse_file(fpath)
        if tree is None:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                func = node.func
                # Direct call: foo()
                if isinstance(func, ast.Name) and func.id == symbol_name:
                    results.append((fpath, node.lineno))
                # Attribute call: obj.foo()
                elif isinstance(func, ast.Attribute) and func.attr == symbol_name:
                    results.append((fpath, node.lineno))
    return results


def _classify_access(
    declared_access: str,
    file_path: str,
    task_files: list[str],
    repo_root: str,
) -> str:
    """Return the effective access type for a touch record.

    Rules (in priority order):
    1. If the task declares signature_change, honour it.
    2. If the task declares write, honour it.
    3. Otherwise treat as read.
    """
    # Normalise paths for comparison
    norm_task_files = {
        os.path.normpath(os.path.join(repo_root, f)) if not os.path.isabs(f) else os.path.normpath(f)
        for f in task_files
    }
    norm_file = os.path.normpath(file_path)
    in_task_scope = norm_file in norm_task_files

    if declared_access == "signature_change" and in_task_scope:
        return "signature_change"
    if declared_access == "write" and in_task_scope:
        return "write"
    return "read"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def extract_touch_set(task: dict, repo_root: str) -> list[TouchRecord]:
    """Resolve a task's declared symbols via AST and return TouchRecord list.

    Parameters
    ----------
    task:       A single task dict from the plan JSON (Appendix A schema).
    repo_root:  Absolute path to the root of the target repository.

    Returns
    -------
    List of TouchRecord — one per (symbol, file) combination found.
    """
    records: list[TouchRecord] = []
    task_files: list[str] = task.get("files", [])

    for sym in task.get("symbols_touched", []):
        sym_name: str = sym["name"]
        sym_kind: str = sym["kind"]
        declared_access: str = sym["access_type"]

        # Skip non-code resources here — handled by scan_non_code_resources()
        if sym_kind in ("config_key", "migration_file"):
            records.append(
                TouchRecord(
                    symbol_name=sym_name,
                    kind=sym_kind,
                    access_type=declared_access,
                    file_path="",
                    line_number=0,
                    confidence=1.0,
                )
            )
            continue

        # Find definition sites
        defs = find_symbol_definition(sym_name, sym_kind, repo_root)
        if defs:
            for fpath, lineno in defs:
                access = _classify_access(declared_access, fpath, task_files, repo_root)
                records.append(
                    TouchRecord(
                        symbol_name=sym_name,
                        kind=sym_kind,
                        access_type=access,
                        file_path=fpath,
                        line_number=lineno,
                        confidence=1.0,
                    )
                )
        else:
            # Symbol not found by AST — emit a heuristic record
            hint_file = task_files[0] if task_files else ""
            records.append(
                TouchRecord(
                    symbol_name=sym_name,
                    kind=sym_kind,
                    access_type=declared_access,
                    file_path=hint_file,
                    line_number=0,
                    confidence=0.7,
                )
            )

        # Also record each call site as a read touch (for blast-radius counting)
        call_sites = find_call_sites(sym_name, repo_root)
        for cs_file, cs_line in call_sites:
            # Don't duplicate a record we already emitted as signature_change/write
            already_covered = any(
                r.file_path == cs_file and r.symbol_name == sym_name
                for r in records
            )
            if not already_covered:
                records.append(
                    TouchRecord(
                        symbol_name=sym_name,
                        kind=sym_kind,
                        access_type="read",
                        file_path=cs_file,
                        line_number=cs_line,
                        confidence=1.0,
                    )
                )

    return records


# ---------------------------------------------------------------------------
# Non-code resource scanning (FR2.4)
# ---------------------------------------------------------------------------

_CONFIG_EXTENSIONS = {".env", ".env.example", ".yaml", ".yml", ".toml", ".ini", ".cfg"}
_MIGRATION_PATTERNS = ("alembic/versions/", "migrations/versions/", "migrate/")


def scan_non_code_resources(task: dict, repo_root: str) -> list[TouchRecord]:
    """Scan a task's file list for config keys and migration files.

    Returns one TouchRecord per matched non-code resource file using the
    task's declared access_type (defaulting to 'write' when not specified).
    """
    records: list[TouchRecord] = []
    task_files: list[str] = task.get("files", [])
    declared_access = "write"  # conservative default for resource files

    for f in task_files:
        norm = f.replace("\\", "/")
        ext = os.path.splitext(norm)[1].lower()

        if ext in _CONFIG_EXTENSIONS or norm.split("/")[-1].startswith(".env"):
            kind = "config_key"
        elif any(pat in norm for pat in _MIGRATION_PATTERNS):
            kind = "migration_file"
        else:
            continue

        abs_path = os.path.normpath(os.path.join(repo_root, f))
        records.append(
            TouchRecord(
                symbol_name=norm,
                kind=kind,
                access_type=declared_access,
                file_path=abs_path,
                line_number=0,
                confidence=1.0,
            )
        )

    return records
