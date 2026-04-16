import logging
import os

from dotenv import load_dotenv

load_dotenv()

BOT_NAME = "Expense Buddy"
DEFAULT_DB_PATH = os.getenv("EXPENSE_BUDDY_DB_PATH", "").strip()
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
EXTERNAL_API_URL = os.getenv("EXTERNAL_API_URL", "").strip()


def configure_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(name)s | %(levelname)s | %(message)s",
    )

    # Silence noisy HTTP client logs from external libraries like httpx.
    logging.getLogger("httpx").setLevel(logging.WARNING)
