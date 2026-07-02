# ── Builder ───────────────────────────────────────────────────────────
# Resolves and installs dependencies into a self-contained venv. Build deps
# (gcc, lxml headers) live only here and never reach the runtime image.
FROM python:3.11-slim AS builder

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc libxml2-dev libxslt1-dev && \
    rm -rf /var/lib/apt/lists/*

# uv builds the venv at /app/.venv
ENV UV_PROJECT_ENVIRONMENT=/app/.venv
COPY pyproject.toml uv.lock ./
RUN pip install --no-cache-dir uv && \
    uv sync --frozen --no-dev --no-install-project


# ── Runtime ───────────────────────────────────────────────────────────
# Slim image, runtime libs only, non-root user. No build toolchain, no uv.
FROM python:3.11-slim AS runtime

WORKDIR /app

# Runtime shared libs for lxml (no -dev headers, no compiler).
RUN apt-get update && apt-get install -y --no-install-recommends \
    libxml2 libxslt1.1 && \
    rm -rf /var/lib/apt/lists/* && \
    useradd --create-home --uid 10001 app

# Bring in the prebuilt venv and put it first on PATH.
COPY --from=builder /app/.venv /app/.venv
ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONPATH=/app \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

COPY pennywise/ pennywise/

USER app

EXPOSE 8000

# Production: no --reload. Worker count tunable via WEB_CONCURRENCY (default 2).
CMD ["sh", "-c", "uvicorn pennywise.api.app:create_app --factory --host 0.0.0.0 --port 8000 --workers ${WEB_CONCURRENCY:-2}"]
