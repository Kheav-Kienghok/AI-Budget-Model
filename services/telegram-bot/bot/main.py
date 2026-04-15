from __future__ import annotations

import logging
import sys

from telegram.ext import Application, ApplicationBuilder, CommandHandler, MessageHandler, filters

from .config import TELEGRAM_BOT_TOKEN, DEFAULT_DB_PATH, configure_logging
from .db_pkg import Database, DatabaseConfig
from .handlers_pkg import start, help_command, handle_document


def build_application() -> Application:
    if not TELEGRAM_BOT_TOKEN:
        raise RuntimeError("Missing TELEGRAM_BOT_TOKEN environment variable.")

    configure_logging()
    logger = logging.getLogger(__name__)

    app = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).build()

    db = Database(DatabaseConfig(path=DEFAULT_DB_PATH))
    db.connect()
    app.bot_data["db"] = db

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(MessageHandler(filters.Document.ALL, handle_document))

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
