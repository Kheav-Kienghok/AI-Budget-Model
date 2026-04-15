from __future__ import annotations

import logging
from io import BytesIO

from telegram import Update
from telegram.ext import ContextTypes

from ..external import send_file_payload


logger = logging.getLogger(__name__)


async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message is None or update.message.document is None:
        return

    document = update.message.document
    filename = document.file_name or "upload"
    lower_name = filename.lower()

    if not (lower_name.endswith(".json") or lower_name.endswith(".csv")):
        await update.message.reply_text("Please send a .json or .csv file.")
        return

    try:
        file = await context.bot.get_file(document.file_id)
        buffer = BytesIO()
        await file.download_to_memory(out=buffer)
        file_bytes = buffer.getvalue()

        mime_type = document.mime_type or "application/octet-stream"
        response = await send_file_payload(file_bytes, filename, mime_type)

        await update.message.reply_text(response)

        
    except Exception:  # noqa: BLE001
        logger.exception("Failed to forward file to backend API")
        await update.message.reply_text("Failed to forward file to backend API.")
