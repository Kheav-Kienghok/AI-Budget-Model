.PHONY: run dev format format-check lint lint-fix

PYTHON ?= python3

run:
	$(PYTHON) -m bot.main

format:
	black bot

format-check:
	black --check bot

lint:
	ruff check bot

lint-fix:
	ruff check --fix bot

dev:
	$(MAKE) format-check lint
