from __future__ import annotations

import logging
from io import BytesIO, StringIO
import csv

from telegram import Update
from telegram.ext import ContextTypes

from ..db_pkg import Database
from ..external import send_file_payload
from ..utils_pkg import get_user_identifiers
from .commands import _csv_followup_keyboard

logger = logging.getLogger(__name__)


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
        # Download the file bytes from Telegram.
        file = await context.bot.get_file(document.file_id)
        buffer = BytesIO()
        await file.download_to_memory(out=buffer)
        file_bytes = buffer.getvalue()

        # Parse CSV and store rows into the database per user.
        db: Database | None = context.application.bot_data.get("db")  # type: ignore[assignment]
        user_id, _, _, _ = get_user_identifiers(update)

        stored_rows = 0
        if db is not None and user_id is not None:
            # Decode and parse row-by-row into structured data.
            try:
                raw_text = file_bytes.decode("utf-8")
            except UnicodeDecodeError:
                raw_text = file_bytes.decode("utf-8", errors="ignore")

            # Parse it row-by-row into structured data.
            text_stream = StringIO(raw_text)
            reader = csv.DictReader(text_stream)

            for row in reader:
                try:
                    date_str = (
                        row.get("date") or row.get("Date") or ""
                    ).strip() or None
                    amount_str = (row.get("amount") or row.get("Amount") or "").strip()
                    description = (
                        row.get("description") or row.get("Description") or ""
                    ).strip()
                    category = row.get("category") or row.get("Category") or None

                    if not amount_str or not description:
                        continue

                    amount = float(amount_str)

                    # Determine a human-readable type for the transaction.
                    type_cell = (row.get("type") or row.get("Type") or "").strip()
                    if type_cell:
                        t = type_cell.lower()
                    elif category:
                        t = str(category).lower()
                    else:
                        t = ""

                    if t.startswith("inc"):
                        entry_type = "Income"
                    elif t.startswith("exp"):
                        entry_type = "Expense"
                    else:
                        # Default to Expense when not specified.
                        entry_type = "Expense"

                    # Store into the original expenses table for backwards compatibility.
                    db.add_expense(
                        user_id=user_id,
                        amount=amount,
                        description=description,
                        category=category,
                    )

                    # Also store into the new transactions table with the
                    # structure you requested: date, description, amount, type.
                    try:
                        db.add_transaction(
                            user_id=user_id,
                            date=date_str,
                            description=description,
                            amount=amount,
                            entry_type=entry_type,
                        )
                    except Exception:  # noqa: BLE001
                        logger.exception(
                            "Failed to store CSV row into transactions table",
                            exc_info=True,
                        )
                    stored_rows += 1
                except Exception:  # noqa: BLE001
                    # Skip bad rows but continue processing the rest.
                    logger.exception("Failed to store one CSV row", exc_info=True)

        mime_type = document.mime_type or "application/octet-stream"

        # Always send CSV files to the /insights endpoint.
        api_endpoint = "/insights"

        response = await send_file_payload(
            file_bytes, filename, mime_type, api_endpoint
        )

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
