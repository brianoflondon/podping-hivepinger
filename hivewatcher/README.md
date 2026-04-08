# Hive Watcher

This folder contains a separate Docker Compose setup for the Hive blockchain watcher. It is intended to run independently from the API service.

The watcher connects to Hive, streams `custom_json` operations, and forwards matching Podping messages to the configured API endpoint for testing.

## What it does

- streams the Hive blockchain for `custom_json` operations
- filters operations by `podping_prefix` (default: `pp`)
- sends matching Podping data to the configured `CALL_URL`
- treats standard Podping operations as `no_broadcast=true` by default
- optionally re-broadcasts `3speak-publish` events using `no_broadcast=false`
- can replay only a finite number of ops with `--max-ops`

## When to use it

Run this watcher if you want a standalone process that validates Hive blockchain events and forwards them to your local Podping API for testing, monitoring, or special 3speak replay behavior.

## Docker Compose

From the `hivewatcher/` folder:

```bash
cd hivewatcher
docker compose up -d
```

This uses the same repository image as the API service, but starts the watcher command instead of the API server.

## Configuration

The watcher uses the root `.env` file via `env_file: ../.env`.

### Important environment variable

- `CALL_URL`
  - default in the container is `http://host.docker.internal:${API_PORT:-1820}/`
  - this should point to the running API service endpoint
  - on macOS, `host.docker.internal` lets the watcher container reach the host machine
  - if you are running the API in a separate container and exposing `1820`, this default works as long as the API is reachable from the host

If you need a different API target, set `CALL_URL` in `.env` or override the environment value in `hivewatcher/docker-compose.yaml`.

## Special 3Speak behavior

The watcher has a built-in flag to process `3speak-publish` Hive operations:

- when `--threespeak=true`, the watcher will create a Podping object for each `3speak-publish` event
- these events are forwarded to `CALL_URL` with `no_broadcast=false`
- this means 3speak events can be tested or rebroadcast through the API as true broadcast traffic

## Running manually

If you prefer not to use Docker, the watcher can also be run from the repository root:

```bash
uv run python -m hivewatcher.watch watch --podping-prefix pp
```

### Useful watcher options

- `--podping-prefix`: filter by Hive operation ID prefix (default `pp`)
- `--node`: optional Hive node URL
- `--max-ops`: stop after processing a fixed number of matching ops
- `--threespeak`: enable forwarding of `3speak-publish` events
- `--all-pings`: send all matching podping IDs to `CALL_URL` with `no_broadcast=true`
- `--call-url`: override the destination API endpoint
- `--block`: start from a specific block number

## Notes

- The watcher is intentionally decoupled from the main API service.
- If you only want the API, use the root `docker-compose.yaml`.
- If you want the watcher, use `hivewatcher/docker-compose.yaml`.
