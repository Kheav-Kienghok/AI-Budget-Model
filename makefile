PYTHON ?= python3

MODEL_API_DIR := services/model-api
TELEGRAM_BOT_DIR := services/telegram-bot

.PHONY: help run-api run-bot run-all

help:
	@echo "Available targets:"
	@echo "  help      - show this help message"
	@echo "  run-api   - start the model API service (port 8000)"
	@echo "  run-bot   - start the Telegram bot (requires TELEGRAM_BOT_TOKEN)"
	@echo "  run-all   - start both API and bot in parallel (two processes)"

# Run the FastAPI model API.
# Assumes you have activated a virtualenv with uvicorn & dependencies installed.
run-api:
	cd $(MODEL_API_DIR) && uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload

# Run the Telegram bot.
# Assumes TELEGRAM_BOT_TOKEN and MODEL_API_PREDICT_URL are set in the environment
# (for example via a .env file loaded by python-dotenv).
run-bot:
	cd $(TELEGRAM_BOT_DIR) && $(PYTHON) -m bot.main

# Convenience target to start both services at once.
# Note: they will share the same Python environment; open separate terminals
# if you prefer different virtualenvs.
run-all:
	$(MAKE) -j2 run-api run-bot


# -----------------------------
# Cleanup
# -----------------------------

.PHONY: clean
clean: ## Remove cache files
	find . -type d -name "__pycache__" -exec rm -rf {} +
	find . -type f -name "*.pyc" -delete
	rm -rf .pytest_cache .mypy_cache .ruff_cache
