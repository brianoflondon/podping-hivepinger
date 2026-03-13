# Podping Hivepinger

### Who is this for?

This is for a Podcast Hosting Company which wants to broadcast podping updates without using anyone else's infrastructure (specifically the [Podping Cloud](https://podping.cloud) service run by [Podcasting 2.0](https://podcastindex.org/)).

## How to Deploy

The only extra thing you need is a Hive Account and a Posting Key to sign transactions with. This new account needs a little bit of Hive Power to cover transaction costs (resource credits). If you have a BTC Lightning wallet, you can use [Signup with v4v.app](https://v4v.app/signup) to create a Hive account and fund it with a small amount of Hive Power.

If you contact me, [Brian of London](mailto:gethive@podping.org), I will help set you up with an account.

Regardless of whether you need help with the Hive account, please let me know and I'll help make sure your account has enough resource credits (Hive Power) to always work.

### Once you have Hive credentials, you can deploy the service with these steps:

1. Clone the repo and navigate to the project directory.
2. Copy the `.env.sample` file to `.env` and fill in your Hive account name and Posting Key.
3. Run Docker Compose: `docker compose up -d`
4. Watch the logs with `docker compose logs -f` to see the service start up and process incoming podpings.


There are various options which are documented in the `.env.sample` file but the defaults are probably what you need.

Verbose logging is turned off but if you want a log of every podcast url which is sent to the service, you can set `VERBOSE=true` in the `.env` file.

The system runs a FastAPI server and you can see the health of the system at any time by visiting `http://<your-server-ip>:1820/health` or `http://<your-server-ip>:1820/status`.

### Health‑check helper script

A convenience script `health-check.sh` is included at the project root.

* Run `./health-check.sh <container-name>` to dump the health‑check log for a named container.
* Omit the argument and the script will prompt you to choose from the currently running containers.

The script is written without any bash‑4‑specific features, so it works on both Ubuntu and the stock macOS shell (bash 3.2). It simply calls `docker inspect` and formats the `.State.Health` output with `jq` — nothing else is required.

### On Your Hosting Side

Once the service is running on a local server within your infrastructure, your hosting system should send an HTTP GET request to the `/` endpoint with the required query parameters (`url`, `reason`, and `medium`) whenever a change happens in the feed of one of your customers.  By default the endpoint mirrors the behaviour of the original
[podping.cloud](https://podping.cloud) API and returns a bare text string `Success!` when the request was accepted.

A new optional boolean flag `detailed_response` may be supplied; when set to `true` the response body becomes a JSON object containing the usual `message`, `reason`, `medium` and `url` fields.  This is useful for debugging (the payload will show `"duplicate"` messages for repeated requests, for instance) but is disabled by default to maintain compatibility with existing callers.

Example:

```bash
curl -X 'GET' \
  'http://<local-ip-address>:1820/podping/?url=http%3A%2F%2Fexample.com%2Fcustomer.rss&reason=update&medium=podcast' \
  -H 'accept: application/json'
```

## About the project

A FastAPI service that receives podcast feed update notifications (podpings) via HTTP, queues them in a crash-resilient SQLite database, deduplicates them, and (eventually) broadcasts them to the Hive blockchain.

Built with **uv** for package management, **Typer** for the CLI, and **aiosqlite** for async disk-backed persistence.

## Features

- **`GET /`** endpoint accepting `url`, `reason`, and `medium` query parameters
- **Disk-backed queue** — incoming URLs are immediately written to a SQLite WAL-mode database; safe against crashes the moment the HTTP 200 is returned
- **Deduplication** — the same combination of URL, medium and reason will not be re-sent within a configurable window (default 180 seconds), both within a single batch and across batches
- **Batching interval** — each reason has a configurable delay (``REASON_INTERVALS``) during which the oldest pending URL is held in the queue.  this lets several feeds accumulate before a single podping is emitted, e.g. with a 10‑second interval you will see all updates arriving during that window grouped together.
- **Background processing loop** — every 10 seconds the queue is inspected and eligible batches are sent; the above interval controls eligibility rather than the loop period itself
- **Sent history with `trx_id`** — processed items are recorded with their Hive transaction ID; history is purged after 24 hours
- **Crash recovery** — pending items survive process restarts; on startup the queue reports how many items were carried over
- **Typer CLI** — `python src/hivepinger/api.py --host 0.0.0.0 --port 1820` runs both the FastAPI server and the background loop concurrently via `asyncio.gather`
- **Health endpoints** — `GET /health` and `GET /status` return version and server info
- **Reverse proxy support** — middleware trusts `X-Forwarded-Proto` and `X-Forwarded-Host` headers
- **Structured logging** — timestamps, module name, and line numbers in every log line

## Project structure

```
src/
  hivepinger/
    __init__.py        # version detection via importlib.metadata + single-source fallback
    api.py             # FastAPI app, Typer CLI, background loop
    podping_queue.py   # PodpingQueue — SQLite-backed queue with dedup
  models/
    podping.py         # Mediums/Reasons enums
tests/
  test_api.py          # API endpoint tests (lifespan-aware TestClient)
  test_queue.py        # Queue unit tests: enqueue, dedup, crash recovery, purge
  test_version.py      # validate ``__version__`` lookup logic
data/                  # SQLite database (created automatically, gitignored)
Dockerfile             # Multi-stage build with uv
docker-compose.yml     # Maps ./data:/data for persistent storage
pytest.ini             # Test configuration
pyproject.toml         # Dependencies and project metadata
```

## Usage

### Local development

```bash
# install dependencies (creates/upgrades .venv)
uv sync

# run via the Typer CLI (starts FastAPI + background loop)
uv run python src/hivepinger/api.py --host 0.0.0.0 --port 1820

# or run FastAPI alone with uvicorn (no background loop)
uv run uvicorn app.api:app --reload --host 0.0.0.0 --port 1820
```

### VS Code debugging

Two configurations are provided in `.vscode/launch.json`:

| Configuration | What it runs |
|---|---|
| **FastAPI** | `uvicorn app.api:app --reload` — API only, hot-reload |
| **FastAPI (CLI serve)** | `python src/hivepinger/api.py --host 0.0.0.0 --port 1820` — API + background loop |

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

The project uses `uv` to manage a reproducible virtual environment; once the
dependencies are synced you can run all commands through it instead of
activating the venv manually.

```bash
uv sync          # install/update dependencies
uv run pytest tests/ -v   # execute the test suite inside the UV env
# or, for a single file:
uv run pytest tests/test_queue.py::test_enqueue_and_dequeue -q
```

(You can still use `.venv/bin/pytest` directly if you prefer, but `uv run ...`
is a convenient shortcut.)

### Example request

```
GET http://localhost:1820/?url=https://feeds.example.org/livestream/rss&reason=live&medium=music
```

Response:

```json
{"message": "queued", "reason": "live", "medium": "music", "url": "https://feeds.example.org/livestream/rss"}
```

## Configuration

The application reads Hive credentials from environment variables.  A sample
file is provided at `.env.sample` (gitignored when copied to `.env`).  You
should copy this to `.env` or otherwise supply the variables in your shell
before starting the service.  Both variables **must** be set to valid values
from a Hive account that will be used to post podpings:

```ini
HIVE_ACCOUNT_NAME=<your hive account name>
HIVE_POSTING_KEY=<your posting key starting with STM…>
```

The posting key is used only for broadcast operations; it should **not** be
shared or checked into source control.


## Configuration

| Constant | Location | Default | Description |
|---|---|---|---|
| `DEFAULT_DB_PATH` | `api.py` | `data/podping_queue.db` | SQLite database file path |
| `BATCH_INTERVAL_SECONDS` | `api.py` | `10` | How often the background loop wakes up to inspect the queue |
| `REASON_INTERVALS` | `api.py` | see source | Minimum time each pending URL is held before being included in a
  podping.  The first item for a given reason is buffered for this interval so
  that subsequent arrivals are batched together. |
| `DEDUP_WINDOW_SECONDS` | `queue.py` | `180` | Ignore duplicate (url, medium, reason) tuples sent within this window |
| `PURGE_SENT_AFTER_SECONDS` | `queue.py` | `86400` | Remove sent history older than this (24h) |

## CLI options

```
python src/hivepinger/api.py --help

Options:
  --host TEXT               Host to run the server on [default: 0.0.0.0]
  --port INTEGER            Port to run the server on [default: 1820]
  --workers INTEGER         Number of worker processes [default: 1]
  --hive-account-name TEXT  Hive account name (optional)
  --hive-active-key TEXT    Hive active key (optional)
```
