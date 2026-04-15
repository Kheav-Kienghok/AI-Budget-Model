lint:
	uv run ruff check .

lint-fix:
	uv run ruff check . --fix

format:
	uv run black .
	uv run ruff check . --fix

check-format:
	uv run black . --check
	uv run ruff check .


fix:
	uv run black .
	uv run ruff check . --fix

all:
	make format
	make lint

dev:
	uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload