# Podping Hivepinger FastAPI Boilerplate

This repository contains a minimal FastAPI application with startup/shutdown handling, a single query-based endpoint, and a Dockerfile. It uses the `uv` package manager and imports podping schema tooling from PyPI.

## Features

- `/` GET endpoint with `url`, `reason`, `medium` query parameters
- `InternalPodping` validation model using `podping_schemas`
- Application lifecycle events (`startup`/`shutdown`)
- Dockerfile using `uv` to install dependencies and run the server

## Usage

### Local development

```bash
# install uv if you don't have it
pip install uv

# install dependencies
uv install

# run the app
uv run app.main:app --host 0.0.0.0 --port 8000
```

### Docker

```bash
docker build -t podping-fastapi .
docker run -p 8000:8000 podping-fastapi
```

Endpoint example:

```
GET http://localhost:8000/?url=https://feeds.example.org/livestream/rss&reason=live&medium=music
```

Response:

```json
{"message":"boilerplate response","podping":{...}}
```
