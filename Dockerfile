# --- build stage: install dependencies with uv ---
FROM python:3.13-slim AS builder

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

WORKDIR /hivepinger

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

# copy source
COPY --from=builder /hivepinger/src /hivepinger/src

# ensure the venv python is first on PATH
ENV PATH="/hivepinger/.venv/bin:$PATH"
ENV PYTHONPATH="/hivepinger/src"

# create the data directory for the SQLite queue; the app expects
# ``data`` relative to the working directory (/hivepinger)
RUN mkdir -p /hivepinger/data

EXPOSE 1820

# run the API script; any arguments (host/port/prefix, etc.)
# are supplied by the caller (docker-compose or `docker run`).
ENTRYPOINT ["python", "src/hivepinger/api.py"]
# default to nothing, let caller set options
CMD []
