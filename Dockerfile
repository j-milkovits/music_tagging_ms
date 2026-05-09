FROM ghcr.io/astral-sh/uv:python3.13-bookworm-slim

ARG GIT_SHA=unknown

ENV UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev --no-install-project

COPY src/ ./src/
COPY .env ./
RUN uv sync --frozen --no-dev

ENV PATH="/app/.venv/bin:$PATH" \
    TAGGING_MS_HOST=0.0.0.0 \
    GIT_SHA=${GIT_SHA}

RUN useradd --create-home --shell /usr/sbin/nologin appuser \
    && chown -R appuser:appuser /app

USER appuser

EXPOSE 8000

CMD ["uv", "run", "tagging-ms"]
