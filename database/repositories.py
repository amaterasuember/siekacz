from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from .db import get_connection


def record_project(path: str, client_name: str, order_number: str, material: str) -> None:
    with get_connection() as connection:
        connection.execute(
            """
            INSERT INTO project_history(path, client_name, order_number, material, saved_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (path, client_name, order_number, material, datetime.now().isoformat(timespec="seconds")),
        )


def recent_projects(limit: int = 8) -> list[dict[str, Any]]:
    with get_connection() as connection:
        rows = connection.execute(
            """
            SELECT path, client_name, order_number, material, MAX(saved_at) AS saved_at
            FROM project_history
            GROUP BY path
            ORDER BY saved_at DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    return [dict(row) for row in rows]


def save_leftovers(leftovers: list[dict[str, Any]]) -> None:
    if not leftovers:
        return
    with get_connection() as connection:
        for item in leftovers:
            connection.execute(
                """
                INSERT INTO leftovers(type, material, thickness, profile, width, height, length, source, status, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'available', ?)
                """,
                (
                    item.get("type"),
                    item.get("material"),
                    item.get("thickness"),
                    item.get("profile"),
                    item.get("width"),
                    item.get("height"),
                    item.get("length"),
                    item.get("source"),
                    datetime.now().isoformat(timespec="seconds"),
                ),
            )


def list_leftovers(status: str | None = None, limit: int = 500) -> list[dict[str, Any]]:
    with get_connection() as connection:
        if status:
            rows = connection.execute(
                "SELECT * FROM leftovers WHERE status=? ORDER BY created_at DESC LIMIT ?",
                (status, limit),
            ).fetchall()
        else:
            rows = connection.execute(
                "SELECT * FROM leftovers ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
    return [dict(row) for row in rows]


def update_leftover_status(leftover_id: int, status: str) -> None:
    with get_connection() as connection:
        connection.execute("UPDATE leftovers SET status=? WHERE id=?", (status, leftover_id))


def get_setting(key: str, default: Any = None) -> Any:
    with get_connection() as connection:
        row = connection.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    if not row:
        return default
    try:
        return json.loads(row["value"])
    except json.JSONDecodeError:
        return row["value"]


def set_setting(key: str, value: Any) -> None:
    with get_connection() as connection:
        connection.execute(
            "INSERT OR REPLACE INTO settings(key, value) VALUES (?, ?)",
            (key, json.dumps(value)),
        )
