FROM ghcr.io/astral-sh/uv:0.9.26 AS uv
FROM python:3.14.3-slim
COPY --from=uv /uv /usr/local/bin/uv
ENV PYTHONUNBUFFERED=1 UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy PATH="/app/.venv/bin:$PATH"
WORKDIR /app
RUN useradd --uid 10001 --create-home app && mkdir /app/data && chown app:app /app /app/data
COPY --chown=app:app pyproject.toml uv.lock ./
USER app
RUN uv sync --locked --no-dev --no-install-project
COPY --chown=app:app . .
RUN uv sync --locked --no-dev
EXPOSE 8000
CMD ["uvicorn", "rss_reader.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000", "--no-access-log"]
