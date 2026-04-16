FROM python:3.13-slim

ENV PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTE=1 \
    UV_LINK_MODE=copy

# Install uv
COPY --from=ghcr.io/astral-sh/uv:0.5.11 /uv /uvx /bin/

WORKDIR /app

# Make venv visible globally
ENV PATH="/app/.venv/bin:$PATH"

# Copy dependency files first (better caching)
COPY pyproject.toml uv.lock /app/

# Install dependencies (cached layer)
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-install-project --no-dev

# Copy application code
COPY ./app /app/app

# Final sync (reconcile locked deps; project install not required at runtime)
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-install-project --no-dev

# Expose FastAPI port
EXPOSE 8000

# Run production server (NO reload, NO watchfiles)
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
