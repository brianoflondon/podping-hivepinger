# --- build stage: install dependencies with uv ---
FROM python:3.13-slim AS builder

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

WORKDIR /app

# copy dependency metadata first for layer caching
COPY pyproject.toml uv.lock .python-version ./

# install project dependencies into a venv
RUN uv sync --frozen --no-dev --no-install-project

# copy source
COPY src/ src/
COPY README.md ./

# install the project itself
RUN uv sync --frozen --no-dev

# --- runtime stage ---
FROM python:3.13-slim

WORKDIR /app

# copy the venv from the builder
COPY --from=builder /app/.venv /app/.venv

# copy source
COPY --from=builder /app/src /app/src

# ensure the venv python is first on PATH
ENV PATH="/app/.venv/bin:$PATH"
ENV PYTHONPATH="/app/src"

# create the data directory for the SQLite queue
RUN mkdir -p /data

EXPOSE 1820

# run the CLI entrypoint
ENTRYPOINT ["python", "src/app/api.py", "serve"]
CMD ["--host", "0.0.0.0", "--port", "1820"]
