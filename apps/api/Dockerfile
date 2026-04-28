FROM python:3.11-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential curl \
  && rm -rf /var/lib/apt/lists/*

RUN pip install uv

COPY pyproject.toml ./
RUN uv pip install --system --no-cache -r <(uv pip compile pyproject.toml --quiet) \
  || pip install fastapi uvicorn[standard] pydantic pydantic-settings \
       "sqlalchemy[asyncio]" asyncpg alembic anthropic httpx openpyxl pandas \
       python-multipart minio redis sse-starlette orjson

COPY . .

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
