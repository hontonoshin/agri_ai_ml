"""SQLite request and user-preference storage."""
from __future__ import annotations

import datetime as dt
import sqlite3
from pathlib import Path
from typing import Any


SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    user_id INTEGER PRIMARY KEY,
    language TEXT NOT NULL DEFAULT 'uz',
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS requests (
    request_id TEXT PRIMARY KEY,
    chat_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    latitude REAL NOT NULL,
    longitude REAL NOT NULL,
    field_id INTEGER,
    area_ha REAL,
    region_id TEXT,
    client TEXT,
    crop TEXT,
    language TEXT NOT NULL,
    status TEXT NOT NULL,
    data_source TEXT,
    latest_date TEXT,
    latest_ndvi REAL,
    anomaly_percentile REAL,
    confidence TEXT,
    report_path TEXT,
    error TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_requests_user ON requests(user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_requests_status ON requests(status, updated_at DESC);
"""


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


class Store:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as connection:
            connection.executescript(SCHEMA)
            columns = {row["name"] for row in connection.execute("PRAGMA table_info(requests)")}
            for name in ("region_id", "client"):
                if name not in columns:
                    connection.execute(f"ALTER TABLE requests ADD COLUMN {name} TEXT")
            connection.commit()

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=30)
        connection.row_factory = sqlite3.Row
        return connection

    def language(self, user_id: int) -> str:
        with self.connect() as connection:
            row = connection.execute("SELECT language FROM users WHERE user_id=?", (user_id,)).fetchone()
        return str(row["language"]) if row else "uz"

    def set_language(self, user_id: int, language: str) -> None:
        with self.connect() as connection:
            connection.execute(
                "INSERT INTO users(user_id, language, updated_at) VALUES(?,?,?) "
                "ON CONFLICT(user_id) DO UPDATE SET language=excluded.language, updated_at=excluded.updated_at",
                (user_id, language, utc_now()),
            )
            connection.commit()

    def create_request(self, request_id: str, chat_id: int, user_id: int,
                       latitude: float, longitude: float, language: str,
                       field_id: int | None, area_ha: float | None,
                       region_id: str | None = None, client: str | None = None) -> None:
        now = utc_now()
        with self.connect() as connection:
            connection.execute(
                """INSERT INTO requests(
                    request_id, chat_id, user_id, latitude, longitude, field_id,
                    area_ha, language, status, created_at, updated_at, region_id, client
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (request_id, chat_id, user_id, latitude, longitude, field_id,
                 area_ha, language, "awaiting_confirmation", now, now, region_id, client),
            )
            connection.commit()

    def get(self, request_id: str) -> dict[str, Any] | None:
        with self.connect() as connection:
            row = connection.execute("SELECT * FROM requests WHERE request_id=?", (request_id,)).fetchone()
        return dict(row) if row else None

    def update(self, request_id: str, **values: Any) -> None:
        allowed = {
            "field_id", "area_ha", "crop", "language", "status", "data_source",
            "latest_date", "latest_ndvi", "anomaly_percentile", "confidence",
            "report_path", "error",
            "region_id", "client",
        }
        clean = {key: value for key, value in values.items() if key in allowed}
        if not clean:
            return
        clean["updated_at"] = utc_now()
        assignments = ", ".join(f"{key}=?" for key in clean)
        with self.connect() as connection:
            connection.execute(
                f"UPDATE requests SET {assignments} WHERE request_id=?",
                (*clean.values(), request_id),
            )
            connection.commit()

    def latest_for_user(self, user_id: int, limit: int = 5) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM requests WHERE user_id=? ORDER BY created_at DESC LIMIT ?",
                (user_id, limit),
            ).fetchall()
        return [dict(row) for row in rows]

    def recent(self, limit: int = 500) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM requests ORDER BY created_at DESC LIMIT ?", (limit,)
            ).fetchall()
        return [dict(row) for row in rows]

    def delete_user(self, user_id: int) -> list[str]:
        with self.connect() as connection:
            paths = [
                str(row["report_path"]) for row in connection.execute(
                    "SELECT report_path FROM requests WHERE user_id=? AND report_path IS NOT NULL",
                    (user_id,),
                ).fetchall()
            ]
            connection.execute("DELETE FROM requests WHERE user_id=?", (user_id,))
            connection.execute("DELETE FROM users WHERE user_id=?", (user_id,))
            connection.commit()
        return paths
