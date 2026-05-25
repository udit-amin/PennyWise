FROM python:3.11-slim

WORKDIR /app

# System deps for lxml
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc libxml2-dev libxslt1-dev && \
    rm -rf /var/lib/apt/lists/*

# Install uv for fast dependency resolution
COPY pyproject.toml uv.lock ./
RUN pip install --no-cache-dir uv && \
    uv sync --frozen --no-dev

COPY pennywise/ pennywise/

EXPOSE 8000

CMD ["uv", "run", "uvicorn", "pennywise.api.app:create_app", \
     "--host", "0.0.0.0", "--port", "8000", "--factory"]
