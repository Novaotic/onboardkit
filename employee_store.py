"""SQLite current-state inventory for onboarded employees.

WAL mode is enabled so readers do not block writers. That creates
``onboardkit.db-wal`` and ``onboardkit.db-shm`` beside the main file — the
process must be able to create and delete files inside ``DATA_DIR``, not only
write the ``.db`` itself.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import uuid
from datetime import datetime
from typing import Any

import paths

log = logging.getLogger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS employees (
    id TEXT PRIMARY KEY,
    requested_by_username TEXT NOT NULL,
    requested_by_display TEXT NOT NULL,
    employee_first TEXT NOT NULL,
    employee_last TEXT NOT NULL,
    employee_title TEXT NOT NULL DEFAULT '',
    search_blob TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_employees_owner
    ON employees (requested_by_username);
CREATE INDEX IF NOT EXISTS idx_employees_search
    ON employees (search_blob);
"""


def _connect() -> sqlite3.Connection:
    paths.DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(paths.DB_FILE), timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn


def init_db() -> None:
    """Create data directory and employees table if missing."""
    with _connect() as conn:
        conn.executescript(_SCHEMA)
    log.info("Employee inventory ready at %s", paths.DB_FILE)


def _norm_name(value: str) -> str:
    return (value or "").strip().casefold()


def build_search_blob(employee: dict) -> str:
    parts = [
        employee.get("first_name", ""),
        employee.get("last_name", ""),
        employee.get("preferred_name", ""),
        employee.get("title", ""),
    ]
    return " ".join(p.strip() for p in parts if p and str(p).strip()).casefold()


def _row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "requested_by_username": row["requested_by_username"],
        "requested_by_display": row["requested_by_display"],
        "employee_first": row["employee_first"],
        "employee_last": row["employee_last"],
        "employee_title": row["employee_title"],
        "search_blob": row["search_blob"],
        "payload": json.loads(row["payload_json"]),
        "updated_at": row["updated_at"],
    }


def get_employee(employee_id: str) -> dict[str, Any] | None:
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM employees WHERE id = ?",
            (employee_id,),
        ).fetchone()
    return _row_to_dict(row) if row else None


def search_employees(
    query: str,
    *,
    username: str,
    is_admin: bool,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """Need-to-know search: managers see only their rows; admins see all."""
    q = (query or "").strip().casefold()
    if not q:
        return []

    like = f"%{q}%"
    sql = "SELECT * FROM employees WHERE search_blob LIKE ?"
    params: list[Any] = [like]
    if not is_admin:
        sql += " AND requested_by_username = ?"
        params.append(username)
    sql += " ORDER BY updated_at DESC LIMIT ?"
    params.append(limit)

    with _connect() as conn:
        rows = conn.execute(sql, params).fetchall()
    return [_row_to_dict(r) for r in rows]


def list_all_employees(*, limit: int = 500) -> list[dict[str, Any]]:
    """Admin global roster — no owner filter."""
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM employees ORDER BY updated_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [_row_to_dict(r) for r in rows]


def _find_own_by_name(
    conn: sqlite3.Connection,
    username: str,
    first: str,
    last: str,
) -> sqlite3.Row | None:
    first_n = _norm_name(first)
    last_n = _norm_name(last)
    if not first_n or not last_n:
        return None
    rows = conn.execute(
        "SELECT * FROM employees WHERE requested_by_username = ?",
        (username,),
    ).fetchall()
    for row in rows:
        if _norm_name(row["employee_first"]) == first_n and _norm_name(row["employee_last"]) == last_n:
            return row
    return None


def upsert_employee(
    *,
    username: str,
    display: str,
    payload: dict,
    employee_id: str | None = None,
) -> str:
    """Insert or replace a current-state row. Owner is always the submitter."""
    emp = payload.get("employee") or {}
    first = (emp.get("first_name") or "").strip()
    last = (emp.get("last_name") or "").strip()
    title = (emp.get("title") or "").strip()
    blob = build_search_blob(emp)
    now = datetime.now().isoformat()
    payload_text = json.dumps(payload, ensure_ascii=False)
    owner_user = (username or "").strip()
    owner_display = (display or owner_user).strip()

    with _connect() as conn:
        existing: sqlite3.Row | None = None
        if employee_id:
            existing = conn.execute(
                "SELECT * FROM employees WHERE id = ?",
                (employee_id,),
            ).fetchone()
        if existing is None:
            existing = _find_own_by_name(conn, owner_user, first, last)

        if existing:
            row_id = existing["id"]
            conn.execute(
                """
                UPDATE employees SET
                    requested_by_username = ?,
                    requested_by_display = ?,
                    employee_first = ?,
                    employee_last = ?,
                    employee_title = ?,
                    search_blob = ?,
                    payload_json = ?,
                    updated_at = ?
                WHERE id = ?
                """,
                (
                    owner_user,
                    owner_display,
                    first,
                    last,
                    title,
                    blob,
                    payload_text,
                    now,
                    row_id,
                ),
            )
            return row_id

        row_id = str(uuid.uuid4())
        conn.execute(
            """
            INSERT INTO employees (
                id, requested_by_username, requested_by_display,
                employee_first, employee_last, employee_title,
                search_blob, payload_json, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                row_id,
                owner_user,
                owner_display,
                first,
                last,
                title,
                blob,
                payload_text,
                now,
            ),
        )
        return row_id


def delete_employee(employee_id: str) -> bool:
    with _connect() as conn:
        cur = conn.execute("DELETE FROM employees WHERE id = ?", (employee_id,))
        return cur.rowcount > 0
