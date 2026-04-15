from __future__ import annotations

import logging

from telegram import Update
from telegram.ext import ContextTypes

from ..db_pkg import Database
from ..utils_pkg import get_user_identifiers


logger = logging.getLogger(__name__)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id, username, first_name, last_name = get_user_identifiers(update)
    db: Database | None = context.application.bot_data.get("db")  # type: ignore[assignment]

    if user_id is not None and db is not None:
        try:
            db.ensure_user(user_id, username, first_name, last_name)
        except Exception:
            logger.exception("Failed to ensure user in database")

    if update.message is None:
        return

    welcome_lines = [
        "👋 Welcome to Expense Buddy!",
        "",
        "I help you track and categorize your expenses.",
        "",
        "Available commands:",
        "  /start - Show this welcome message",
        "  /help  - Show usage examples",
        "",
        "You can also send a .json or .csv file",
        "and I will forward it to the backend service.",
    ]
    await update.message.reply_text("\n".join(welcome_lines))


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message is None:
        return

    help_lines = [
        "📖 Expense Buddy Help",
        "",
        "Commands:",
        "  • /start - Show the welcome message",
        "  • /help  - Show this help",
        "",
        "File upload:",
        "  • Send a .json or .csv file",
        "    and I will forward it to the configured backend API.",
    ]
    await update.message.reply_text("\n".join(help_lines))
