from __future__ import annotations

import logging

from telegram import Update
from telegram.ext import ContextTypes

from ...db_pkg import Database
from ...utils_pkg import get_user_identifiers

logger = logging.getLogger(__name__)


async def destroy_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Delete the current user's records from expenses and transactions."""

    if update.message is None:
        return

    db: Database | None = context.application.bot_data.get("db")  # type: ignore[assignment]
    user_id, _, _, _ = get_user_identifiers(update)

    if db is None or user_id is None:
        await update.message.reply_text(
            "Sorry, I couldn't access your data right now. Please try again later."
        )
        return

    try:
        deleted_expenses, deleted_transactions = db.clear_user_financial_data(user_id)
    except Exception:
        logger.exception("Failed to clear user data for user_id=%s", user_id)
        await update.message.reply_text(
            "Something went wrong while deleting your data. Please try again."
        )
        return

    await update.message.reply_text(
        "✅ Your data has been destroyed.\n"
        f"- expenses deleted: {deleted_expenses}\n"
        f"- transactions deleted: {deleted_transactions}"
    )
