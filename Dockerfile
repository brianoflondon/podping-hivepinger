# --- build stage: install dependencies with uv ---
FROM python:3.13-slim AS builder

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

WORKDIR /hivepinger

# tools required for uv to fetch git-sourced dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
  && rm -rf /var/lib/apt/lists/*

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

WORKDIR /hivepinger

# copy the venv from the builder
COPY --from=builder /hivepinger/.venv /hivepinger/.venv

# copy source and project metadata required for version detection
COPY --from=builder /hivepinger/src /hivepinger/src
COPY --from=builder /hivepinger/pyproject.toml /hivepinger/pyproject.toml

# ensure the venv python is first on PATH
ENV PATH="/hivepinger/.venv/bin:$PATH"
ENV PYTHONPATH="/hivepinger/src"

# create the data directory for the SQLite queue; the app expects
# ``data`` relative to the working directory (/hivepinger)
RUN mkdir -p /hivepinger/data

EXPOSE 8000

# runtime command is supplied by Docker Compose or `docker run`
