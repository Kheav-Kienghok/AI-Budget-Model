from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


logger = logging.getLogger(__name__)


@dataclass
class DatabaseConfig:
    path: Path


class Database:
    def __init__(self, config: DatabaseConfig) -> None:
        self._config = config
        self._connection: Optional[sqlite3.Connection] = None

    def connect(self) -> None:
        if self._connection is None:
            self._connection = sqlite3.connect(self._config.path)
            self._connection.row_factory = sqlite3.Row
            logger.info("Connected to SQLite DB at %s", self._config.path)
            self._ensure_schema()

    def _ensure_schema(self) -> None:
        assert self._connection is not None
        cur = self._connection.cursor()
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL UNIQUE,
                username TEXT,
                first_name TEXT,
                last_name TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS expenses (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                amount REAL NOT NULL,
                description TEXT NOT NULL,
                category TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users(user_id)
            )
            """
        )
        self._connection.commit()
        logger.info("Ensured database schema exists")

    def ensure_user(self, user_id: int, username: str | None, first_name: str | None, last_name: str | None) -> None:
        if self._connection is None:
            self.connect()
        assert self._connection is not None
        cur = self._connection.cursor()
        cur.execute(
            """
            INSERT INTO users (user_id, username, first_name, last_name)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                username=excluded.username,
                first_name=excluded.first_name,
                last_name=excluded.last_name
            """,
            (user_id, username, first_name, last_name),
        )
        self._connection.commit()

    def close(self) -> None:
        if self._connection is not None:
            self._connection.close()
            logger.info("Closed SQLite connection")
            self._connection = None
