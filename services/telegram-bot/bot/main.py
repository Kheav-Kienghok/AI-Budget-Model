from __future__ import annotations

import logging
import sys

from telegram.ext import (
    Application,
    ApplicationBuilder,
    CallbackQueryHandler,
    CommandHandler,
    MessageHandler,
    filters,
)

from .config import (
    TELEGRAM_BOT_TOKEN,
    DEFAULT_DB_PATH,
    EXTERNAL_API_URL,
    configure_logging,
)
from .db_pkg import Database, DatabaseConfig
from .handlers_pkg import (
    start,
    help_command,
    handle_button_callback,
    handle_document,
    handle_manual_text,
)


def build_application() -> Application:
    missing_env: list[str] = []

    if not TELEGRAM_BOT_TOKEN:
        missing_env.append("TELEGRAM_BOT_TOKEN")

    if not EXTERNAL_API_URL:
        missing_env.append("EXTERNAL_API_URL")

    if not DEFAULT_DB_PATH:
        missing_env.append("EXPENSE_BUDDY_DB_PATH")

    if missing_env:
        raise RuntimeError(
            f"Missing required environment variable(s): {', '.join(missing_env)}."
        )

    configure_logging()
    logger = logging.getLogger(__name__)

    app = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).build()

    db = Database(DatabaseConfig(dsn=DEFAULT_DB_PATH))
    db.connect()
    app.bot_data["db"] = db

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(MessageHandler(filters.Document.ALL, handle_document))
    app.add_handler(CallbackQueryHandler(handle_button_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_manual_text))

    logger.info("Application and handlers configured")
    return app


def main() -> None:
    app = build_application()
    logger = logging.getLogger(__name__)

    try:
        logger.info("Starting bot polling")
        app.run_polling()
    except Exception:  # noqa: BLE001
        logger.exception("Error while running the bot")
        sys.exit(1)
    finally:
        db = app.bot_data.get("db")
        if isinstance(db, Database):
            db.close()


if __name__ == "__main__":
    main()
