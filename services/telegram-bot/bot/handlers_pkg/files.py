from __future__ import annotations

import asyncio
import logging
from io import BytesIO, StringIO
import csv
from datetime import date, datetime

import httpx

from telegram import Update
from telegram.ext import ContextTypes

from ..db_pkg import Database
from ..external import send_json_payload
from ..utils_pkg import get_user_identifiers
from .commands import _build_insights_payload, _csv_followup_keyboard

logger = logging.getLogger(__name__)


def _normalized_cell(row: dict[str, str], *candidate_keys: str) -> str:
    """Read a CSV cell by key with light header normalization.

    This supports common variants like BOM-prefixed headers and extra spaces.
    """

    normalized: dict[str, str] = {
        str(k).replace("\ufeff", "").strip().lower(): (v or "")
        for k, v in row.items()
        if k is not None
    }
    for key in candidate_keys:
        value = normalized.get(key.strip().lower())
        if value is not None:
            return value.strip()
    return ""


def _parse_csv_date(date_str: str) -> date | None:
    """Parse CSV date only in strict DD/MM/YYYY format."""

    raw = date_str.strip()
    if not raw:
        return None

    try:
        return datetime.strptime(raw, "%d/%m/%Y").date()
    except ValueError:
        return None


def _parse_csv_amount(amount_str: str) -> float | None:
    """Parse amount values from common bank export formats.

    Supports:
    - 1234.56
    - 1,234.56
    - 1.234,56
    - (123.45) for negatives
    """

    raw = amount_str.strip()
    if not raw:
        return None

    negative = raw.startswith("(") and raw.endswith(")")
    cleaned = raw.strip("()")
    cleaned = cleaned.replace("$", "").replace(" ", "")

    # If both separators exist, infer decimal separator by last occurrence.
    if "," in cleaned and "." in cleaned:
        if cleaned.rfind(",") > cleaned.rfind("."):
            # 1.234,56 -> 1234.56
            cleaned = cleaned.replace(".", "")
            cleaned = cleaned.replace(",", ".")
        else:
            # 1,234.56 -> 1234.56
            cleaned = cleaned.replace(",", "")
    elif "," in cleaned:
        # Treat a lone comma as decimal separator when it looks like cents.
        left, right = cleaned.rsplit(",", 1)
        if right.isdigit() and len(right) in (1, 2):
            cleaned = f"{left}.{right}".replace(",", "")
        else:
            cleaned = cleaned.replace(",", "")

    try:
        value = float(cleaned)
    except ValueError:
        return None

    return -value if negative else value


def _detect_csv_dialect(raw_text: str) -> type[csv.Dialect]:
    """Detect CSV dialect to support comma and semicolon exports."""

    sample = raw_text[:2048]
    try:
        return csv.Sniffer().sniff(sample, delimiters=",;")
    except csv.Error:
        return csv.excel


async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message is None or update.message.document is None:
        return

    document = update.message.document
    filename = document.file_name or "upload"
    lower_name = filename.lower()

    # Only accept CSV files now.
    if not lower_name.endswith(".csv"):
        await update.message.reply_text("Please send a .csv file.")
        return

    try:
        if context.user_data is not None:
            context.user_data.pop("pending_csv_insights", None)

        # Download the file bytes from Telegram.
        file = await context.bot.get_file(document.file_id)
        buffer = BytesIO()
        await file.download_to_memory(out=buffer)
        file_bytes = buffer.getvalue()

        # Parse CSV and store rows into the database per user.
        db: Database | None = context.application.bot_data.get("db")  # type: ignore[assignment]
        user_id, username, first_name, last_name = get_user_identifiers(update)

        transaction_rows: list[tuple[int, date | None, str, float, str]] = []
        if db is not None and user_id is not None:
            # Ensure foreign key parent exists before writing transactions.
            db.ensure_user(user_id, username, first_name, last_name)

            # Decode and parse row-by-row into structured data.
            try:
                raw_text = file_bytes.decode("utf-8")
            except UnicodeDecodeError:
                raw_text = file_bytes.decode("utf-8", errors="ignore")

            # Parse it row-by-row into structured data.
            text_stream = StringIO(raw_text)
            dialect = _detect_csv_dialect(raw_text)
            reader = csv.DictReader(text_stream, dialect=dialect, skipinitialspace=True)
            parsed_rows = list(reader)

            invalid_date_rows: list[int] = []
            for row_idx, row in enumerate(parsed_rows, start=2):
                date_cell = _normalized_cell(
                    row,
                    "date",
                    "transaction date",
                    "posted date",
                    "value date",
                )
                if _parse_csv_date(date_cell) is None:
                    invalid_date_rows.append(row_idx)

            if invalid_date_rows:
                await update.message.reply_text(
                    "*CSV date format is incorrect.*\n"
                    "Please use *DD/MM/YYYY* for all rows (for example: *18/04/2026*).",
                    parse_mode="Markdown",
                )
                return

            for row in parsed_rows:
                try:
                    date_cell = _normalized_cell(
                        row,
                        "date",
                        "transaction date",
                        "posted date",
                        "value date",
                    )
                    amount_str = _normalized_cell(
                        row,
                        "amount",
                        "transaction amount",
                        "total",
                        "value",
                    )
                    description = _normalized_cell(
                        row,
                        "description",
                        "descriptio",
                        "details",
                        "transaction description",
                        "memo",
                        "narrative",
                    )

                    if not amount_str or not description:
                        continue

                    amount = _parse_csv_amount(amount_str)
                    if amount is None:
                        continue

                    parsed_date = _parse_csv_date(date_cell)

                    # Determine a human-readable type for the transaction.
                    type_cell = _normalized_cell(
                        row,
                        "type",
                        "category",
                        "transaction type",
                    )
                    if type_cell:
                        t = type_cell.lower()
                    else:
                        t = ""

                    if t.startswith("inc"):
                        entry_type = "Income"
                    elif t.startswith("exp"):
                        entry_type = "Expense"
                    else:
                        # Default to Expense when not specified.
                        entry_type = "Expense"

                    # CSV data goes only to transactions table (which preserves dates)
                    transaction_rows.append(
                        (
                            user_id,
                            parsed_date,
                            description,
                            amount,
                            entry_type,
                        )
                    )
                except Exception:  # noqa: BLE001
                    # Skip bad rows but continue processing the rest.
                    logger.exception("Failed to store one CSV row", exc_info=True)

            stored_rows = db.add_csv_rows(transaction_rows)
        else:
            stored_rows = 0

        # Build insights request data from DB tables instead of sending the raw CSV file.
        api_endpoint = "/insights"
        payload: list[dict[str, object]] = []
        if db is not None and user_id is not None:
            transactions = db.get_transactions_for_user(user_id)
            expenses = db.get_expenses_for_user(user_id)
            payload = _build_insights_payload(transactions, expenses)

        logger.info(
            "Sending DB-derived insights payload with %d items to %s endpoint",
            len(payload),
            api_endpoint,
        )
        try:
            response = await send_json_payload(payload, endpoint=api_endpoint)
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code != 422:
                raise

            await update.message.reply_text(
                "I am still processing your CSV on the backend. Please be patient while I try again in 30 seconds."
            )
            await asyncio.sleep(30)

            response = await send_json_payload(payload, endpoint=api_endpoint)

        # Report back to the user.
        msg_parts: list[str] = []
        if stored_rows:
            msg_parts.append(
                f"✅ Your CSV file has been stored successfully. {stored_rows} records have been saved."
            )
        else:
            msg_parts.append(
                "ℹ️ No rows were stored from the CSV (missing 'amount' or 'description' columns?)."
            )

        if isinstance(response, dict):
            if context.user_data is not None:
                context.user_data["pending_csv_insights"] = response

            msg_parts.append("")
            msg_parts.append(
                "Would you like to view insights now, or continue importing more CSV files?"
            )
            await update.message.reply_text(
                "\n".join(msg_parts),
                reply_markup=_csv_followup_keyboard(),
            )
        else:
            await update.message.reply_text(
                "\n".join(msg_parts)
                + "\n\nI got a response from the server, but it wasn't in the expected format.",
            )

    except Exception:  # noqa: BLE001
        logger.exception("Failed to process and forward file to backend API")
        await update.message.reply_text(
            "Failed to process and forward file to backend API."
        )
