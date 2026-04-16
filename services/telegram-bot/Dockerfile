# ---------------------------
# Builder stage
# ---------------------------
FROM python:3.13-slim-bookworm AS builder

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=0

WORKDIR /app

# Install system deps + uv
RUN apt-get update && apt-get install -y curl \
    && curl -LsSf https://astral.sh/uv/install.sh | sh \
    && rm -rf /var/lib/apt/lists/*

ENV PATH="/root/.local/bin:$PATH"

# Copy dependency files first (for caching)
COPY pyproject.toml uv.lock ./

# Install dependencies only (cached)
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-install-project --no-dev

# Copy application code (this service lives under bot/)
COPY bot ./bot

# Install project (final layer)
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev


# ---------------------------
# Runtime stage
# ---------------------------
FROM python:3.13-slim-bookworm

WORKDIR /app

# Create non-root user
RUN addgroup --system appgroup \
    && adduser --system --ingroup appgroup appuser

# Copy built app + virtualenv from builder
COPY --from=builder /app /app

# Fix permissions
RUN chown -R appuser:appgroup /app

# Switch to non-root user
USER appuser

# Use virtualenv from uv
ENV PATH="/app/.venv/bin:$PATH"

CMD ["python", "-m", "bot.main"]