# Podping Hivepinger

A FastAPI service that receives podcast feed update notifications (podpings) via HTTP, queues them in a crash-resilient SQLite database, deduplicates them, and (eventually) broadcasts them to the Hive blockchain.

Built with **uv** for package management, **Typer** for the CLI, and **aiosqlite** for async disk-backed persistence.

## Features

- **`GET /`** endpoint accepting `url`, `reason`, and `medium` query parameters
- **Disk-backed queue** — incoming URLs are immediately written to a SQLite WAL-mode database; safe against crashes the moment the HTTP 200 is returned
- **Deduplication** — the same URL will not be re-sent within a configurable window (default 180 seconds), both within a single batch and across batches
- **Background processing loop** — every 10 seconds the queue is drained, deduplicated, and logged (placeholder for Hive broadcast)
- **Sent history with `trx_id`** — processed items are recorded with their Hive transaction ID; history is purged after 24 hours
- **Crash recovery** — pending items survive process restarts; on startup the queue reports how many items were carried over
- **Typer CLI** — `python src/app/api.py --host 0.0.0.0 --port 1820` runs both the FastAPI server and the background loop concurrently via `asyncio.gather`
- **Health endpoints** — `GET /health` and `GET /status` return version and server info
- **Reverse proxy support** — middleware trusts `X-Forwarded-Proto` and `X-Forwarded-Host` headers
- **Structured logging** — timestamps, module name, and line numbers in every log line

## Project structure

```
src/
  app/
    __init__.py        # version detection via single-source
    api.py             # FastAPI app, Typer CLI, background loop
    queue.py           # PodpingQueue — SQLite-backed queue with dedup
  models/
    podping.py         # Mediums/Reasons enums
tests/
  test_api.py          # API endpoint tests (lifespan-aware TestClient)
  test_queue.py        # Queue unit tests: enqueue, dedup, crash recovery, purge
data/                  # SQLite database (created automatically, gitignored)
Dockerfile             # Multi-stage build with uv
docker-compose.yml     # Maps ./data:/data for persistent storage
pytest.ini             # Test configuration
pyproject.toml         # Dependencies and project metadata
```

## Usage

### Local development

```bash
# install dependencies
uv sync

# run via the Typer CLI (starts FastAPI + background loop)
python src/app/api.py --host 0.0.0.0 --port 1820

# or run FastAPI alone with uvicorn (no background loop)
PYTHONPATH=src uvicorn app.api:app --reload --host 0.0.0.0 --port 1820
```

### VS Code debugging

Two configurations are provided in `.vscode/launch.json`:

| Configuration | What it runs |
|---|---|
| **FastAPI** | `uvicorn app.api:app --reload` — API only, hot-reload |
| **FastAPI (CLI serve)** | `python src/app/api.py --host 0.0.0.0 --port 1820` — API + background loop |

### Docker

```bash
# build and run with docker compose (persistent data volume)
docker compose up --build

# or build and run manually
docker build -t podping-hivepinger .
docker run -p 1820:1820 -v ./data:/data podping-hivepinger
```

The SQLite database is stored at `data/podping_queue.db` and mapped to the host via a volume so it survives container restarts.

### Testing

```bash
uv sync
.venv/bin/pytest tests/ -v
```

### Example request

```
GET http://localhost:1820/?url=https://feeds.example.org/livestream/rss&reason=live&medium=music
```

Response:

```json
{"message": "queued", "reason": "live", "medium": "music", "url": "https://feeds.example.org/livestream/rss"}
```

## Configuration

| Constant | Location | Default | Description |
|---|---|---|---|
| `DEFAULT_DB_PATH` | `api.py` | `data/podping_queue.db` | SQLite database file path |
| `BATCH_INTERVAL_SECONDS` | `api.py` | `10` | How often the background loop processes the queue |
| `DEDUP_WINDOW_SECONDS` | `queue.py` | `180` | Ignore duplicate URLs sent within this window |
| `PURGE_SENT_AFTER_SECONDS` | `queue.py` | `86400` | Remove sent history older than this (24h) |

## CLI options

```
python src/app/api.py --help

Options:
  --host TEXT               Host to run the server on [default: 0.0.0.0]
  --port INTEGER            Port to run the server on [default: 1820]
  --workers INTEGER         Number of worker processes [default: 1]
  --hive-account-name TEXT  Hive account name (optional)
  --hive-active-key TEXT    Hive active key (optional)
```
