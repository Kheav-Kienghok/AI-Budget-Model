import logging
import os

from dotenv import load_dotenv


load_dotenv()

BOT_NAME = "Expense Buddy"
DEFAULT_DB_PATH = os.getenv("EXPENSE_BUDDY_DB_PATH", "expense_buddy.sqlite3")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
EXTERNAL_API_URL = os.getenv("EXTERNAL_API_URL", "http://127.0.0.1:8000/classify")


def configure_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(name)s | %(levelname)s | %(message)s",
    )
