from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date
from typing import Optional, cast, Any, Mapping
import json

import psycopg
from psycopg.rows import dict_row

logger = logging.getLogger(__name__)


@dataclass
class DatabaseConfig:
    dsn: str


class Database:
    def __init__(self, config: DatabaseConfig) -> None:
        self._config = config
        self._connection: Optional[psycopg.Connection] = None

    def connect(self) -> None:
        if self._connection is not None and not self._connection.closed:
            if not self._connection.broken:
                return

        self._connection = psycopg.connect(
            self._config.dsn, row_factory=cast(Any, dict_row)
        )
        logger.info("Connected to PostgreSQL DB")
        self._ensure_schema(self._connection)

    def _get_connection(self) -> psycopg.Connection:
        if (
            self._connection is None
            or self._connection.closed
            or self._connection.broken
        ):
            self.close()
            self.connect()

        assert self._connection is not None
        return self._connection

    def _ensure_schema(self, connection: psycopg.Connection) -> None:
        cur = connection.cursor()
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                id SERIAL PRIMARY KEY,
                user_id INTEGER NOT NULL UNIQUE,
                username TEXT,
                first_name TEXT,
                last_name TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """,
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS expenses (
                id SERIAL PRIMARY KEY,
                user_id INTEGER NOT NULL,
                amount REAL NOT NULL,
                description TEXT NOT NULL,
                category TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users(user_id)
            )
            """,
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS transactions (
                user_id INTEGER NOT NULL,
                date DATE,
                description TEXT,
                amount NUMERIC,
                type TEXT,
                FOREIGN KEY (user_id) REFERENCES users(user_id)
            )
            """,
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS user_budget_rules (
                id SERIAL PRIMARY KEY,
                user_id INTEGER NOT NULL UNIQUE,
                budget_rules JSONB DEFAULT '{}'::jsonb,
                savings_rule REAL DEFAULT 20.0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users(user_id)
            )
            """,
        )
        cur.execute(
            """
            ALTER TABLE user_budget_rules
            ALTER COLUMN budget_rules SET DEFAULT
            '{"Food": 20.0, "Transportation": 8.0, "Entertainment": 5.0, "Utilities": 8.0, "Rent": 25.0, "Other": 7.0}'::jsonb
            """,
        )
        cur.execute(
            """
            ALTER TABLE user_budget_rules
            ALTER COLUMN savings_rule SET DEFAULT 20.0
            """,
        )
        connection.commit()
        logger.info("Ensured database schema exists")

    def ensure_user(
        self,
        user_id: int,
        username: str | None,
        first_name: str | None,
        last_name: str | None,
    ) -> None:
        connection = self._get_connection()
        cur = connection.cursor()
        cur.execute(
            """
            INSERT INTO users (user_id, username, first_name, last_name)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (user_id) DO UPDATE SET
                username = EXCLUDED.username,
                first_name = EXCLUDED.first_name,
                last_name = EXCLUDED.last_name
            """,
            (user_id, username, first_name, last_name),
        )
        connection.commit()

    def add_expense(
        self, user_id: int, amount: float, description: str, category: str | None = None
    ) -> None:
        """Store a single expense or income entry for a user.

        Use a positive amount for income and a negative amount for expenses
        (or vice versa, depending on how you prepare the CSV).
        """

        connection = self._get_connection()
        cur = connection.cursor()
        cur.execute(
            """
            INSERT INTO expenses (user_id, amount, description, category)
            VALUES (%s, %s, %s, %s)
            """,
            (user_id, amount, description, category),
        )
        connection.commit()

    def add_transaction(
        self,
        user_id: int,
        date: str | None,
        description: str,
        amount: float,
        entry_type: str,
    ) -> None:
        """Store a single transaction row as shown in the spec.

        Columns: date, description, amount, type.
        """

        connection = self._get_connection()
        cur = connection.cursor()
        cur.execute(
            """
            INSERT INTO transactions (user_id, date, description, amount, type)
            VALUES (%s, %s, %s, %s, %s)
            """,
            (user_id, date, description, amount, entry_type),
        )
        connection.commit()

    def add_csv_rows(
        self,
        transaction_rows: list[tuple[int, date | None, str, float, str]],
    ) -> int:
        """Store CSV rows (only to transactions table) in one transaction.

        CSV data is written to transactions only since it has explicit dates.
        Manual entries go to expenses.
        """

        if not transaction_rows:
            return 0

        connection = self._get_connection()
        with connection.transaction():
            cur = connection.cursor()
            cur.executemany(
                """
                INSERT INTO transactions (user_id, date, description, amount, type)
                VALUES (%s, %s, %s, %s, %s)
                """,
                transaction_rows,
            )

        return len(transaction_rows)

    def get_expenses_for_user(self, user_id: int) -> list[dict]:
        """Return all expenses for a given user, newest first."""

        connection = self._get_connection()
        cur = connection.cursor()
        cur.execute(
            """
            SELECT user_id, amount, description, category, created_at
            FROM expenses
            WHERE user_id = %s
            ORDER BY created_at DESC, id DESC
            """,
            (user_id,),
        )
        rows = cur.fetchall()

        return [dict(row) for row in rows]

    def get_transactions_for_user(self, user_id: int) -> list[dict]:
        """Return all transactions for a given user, newest first."""

        connection = self._get_connection()
        cur = connection.cursor()
        cur.execute(
            """
            SELECT user_id, date, description, amount, type
            FROM transactions
            WHERE user_id = %s
            ORDER BY date DESC
            """,
            (user_id,),
        )
        rows = cur.fetchall()

        return [dict(row) for row in rows]

    def clear_user_financial_data(self, user_id: int) -> tuple[int, int]:
        """Delete a user's rows from expenses and transactions tables.

        Returns:
            A tuple of deleted row counts: (expenses_count, transactions_count)
        """

        connection = self._get_connection()
        cur = connection.cursor()

        cur.execute(
            """
            DELETE FROM expenses
            WHERE user_id = %s
            """,
            (user_id,),
        )
        deleted_expenses = cur.rowcount or 0

        cur.execute(
            """
            DELETE FROM transactions
            WHERE user_id = %s
            """,
            (user_id,),
        )
        deleted_transactions = cur.rowcount or 0

        connection.commit()
        return deleted_expenses, deleted_transactions

    def set_budget_rules(
        self, user_id: int, budget_rules: dict[str, float], savings_rule: float
    ) -> None:
        """Store or update budget rules and savings rule for a user.

        Args:
            user_id: The user's Telegram ID
            budget_rules: Dictionary of category -> budget amount (e.g., {"Food": 20, "Transport": 8})
            savings_rule: Savings percentage target (e.g., 25 for 25%)
        """
        import json

        connection = self._get_connection()
        cur = connection.cursor()

        rules_json = json.dumps(budget_rules)

        cur.execute(
            """
            INSERT INTO user_budget_rules (user_id, budget_rules, savings_rule, updated_at)
            VALUES (%s, %s, %s, CURRENT_TIMESTAMP)
            ON CONFLICT (user_id) DO UPDATE SET
                budget_rules = EXCLUDED.budget_rules,
                savings_rule = EXCLUDED.savings_rule,
                updated_at = CURRENT_TIMESTAMP
            """,
            (user_id, rules_json, savings_rule),
        )
        connection.commit()

    def get_budget_rules(self, user_id: int) -> tuple[dict[str, float], float] | None:
        """Retrieve budget rules and savings rule for a user.

        Returns:
            A tuple of (budget_rules_dict, savings_rule_percentage) or None if not found.
        """

        connection = self._get_connection()
        cur = connection.cursor()
        cur.execute(
            """
            SELECT budget_rules, savings_rule
            FROM user_budget_rules
            WHERE user_id = %s
            """,
            (user_id,),
        )
        row = cur.fetchone()

        if row is None:
            return None

        row = cast(Mapping[str, Any], row)

        raw_budget_rules = row["budget_rules"]
        if not raw_budget_rules:
            budget_rules: dict[str, float] = {}
        elif isinstance(raw_budget_rules, Mapping):
            budget_rules = {
                str(key): float(value)
                for key, value in raw_budget_rules.items()
                if value is not None
            }
        elif isinstance(raw_budget_rules, (str, bytes, bytearray)):
            loaded_rules = json.loads(raw_budget_rules)
            if isinstance(loaded_rules, Mapping):
                budget_rules = {
                    str(key): float(value)
                    for key, value in loaded_rules.items()
                    if value is not None
                }
            else:
                budget_rules = {}
        else:
            budget_rules = {}

        savings_rule = float(row["savings_rule"]) if row["savings_rule"] else 20.0

        return budget_rules, savings_rule

    def close(self) -> None:
        if self._connection is not None:
            self._connection.close()
            logger.info("Closed PostgreSQL connection")
            self._connection = None
