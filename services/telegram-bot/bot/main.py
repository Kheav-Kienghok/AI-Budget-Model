import os

import httpx
from dotenv import load_dotenv
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes, MessageHandler, filters


load_dotenv()

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
MODEL_API_PREDICT_URL = os.getenv("MODEL_API_PREDICT_URL", "http://localhost:8000/predict")


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message:
        await update.message.reply_text(
            "Send me a transaction description and I will return a predicted category."
        )


async def predict_from_api(text: str) -> str:
    payload = {
        "description": text,
        "amount": 0.0,
        "transaction_type": "Expense",
        "date": "2026-01-01",
    }
    async with httpx.AsyncClient(timeout=20) as client:
        response = await client.post(MODEL_API_PREDICT_URL, json=payload)
        response.raise_for_status()
        data = response.json()
    return str(data.get("predicted_category", "Unknown"))


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not update.message.text:
        return

    text = update.message.text.strip()
    if not text:
        await update.message.reply_text("Please send a non-empty message.")
        return

    try:
        predicted_category = await predict_from_api(text)
        await update.message.reply_text(f"Predicted category: {predicted_category}")
    except Exception as exc:  # noqa: BLE001
        await update.message.reply_text(f"Prediction failed: {exc}")


def main() -> None:
    if not TELEGRAM_BOT_TOKEN:
        raise RuntimeError("Missing TELEGRAM_BOT_TOKEN environment variable.")

    app = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.run_polling()


if __name__ == "__main__":
    main()
