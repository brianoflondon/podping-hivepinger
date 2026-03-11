#!/usr/bin/env python3
"""
Standalone healthcheck script for Docker Compose.

This script performs a simple HTTP GET request to a status endpoint and exits with code 0 on success or 1 on failure.
It replicates the behavior of the Docker healthcheck command:
test: ["CMD", "python", "-c", "import urllib.request, sys; urllib.request.urlopen('http://localhost:6001/status'); sys.exit(0)"]

Usage:
    python healthcheck.py --host localhost --port 6001

Or in Docker Compose:
    healthcheck:
      test: ["CMD", "python", "healthcheck.py", "--host", "localhost", "--port", "6001"]
"""

import json
import sys
import time

import httpx
import typer

app = typer.Typer()


@app.command()
def healthcheck(
    host: str = typer.Option("localhost", "--host", help="Host to check (default: localhost)"),
    path: str = typer.Option("/status", "--path", help="Path to check (default: /status)"),
    port: int = typer.Option(1820, "--port", help="Port to check (default: 1820)"),
    timeout: int = typer.Option(
        10, "--timeout", help="Timeout in seconds for the health check request"
    ),
    retries: int = typer.Option(3, "--retries", help="Number of retry attempts"),
    retry_delay: float = typer.Option(
        5.0, "--retry-delay", help="Delay in seconds between retries"
    ),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Print the response body"),
    pretty: bool = typer.Option(False, "--pretty", "-p", help="Pretty print JSON output"),
):
    """
    Perform a health check by attempting to open the /status endpoint.
    Exits with 0 on success, 1 on failure.
    """
    url = f"http://{host}:{port}{path}"
    last_error = None

    for attempt in range(retries):
        try:
            response = httpx.get(url, timeout=timeout)
            response.raise_for_status()
            if verbose or pretty:
                if pretty:
                    try:
                        data = response.json()
                        typer.echo(json.dumps(data, indent=2))
                    except Exception:
                        typer.echo(response.text)
                else:
                    typer.echo(response.text)
            sys.exit(0)
        except httpx.HTTPStatusError as e:
            last_error = e
            typer.echo(f"Health check failed: HTTP {e.response.status_code}")
            if attempt < retries - 1:
                time.sleep(retry_delay)
        except Exception as e:
            last_error = e
            if attempt < retries - 1:
                time.sleep(retry_delay)

    typer.echo(f"Health check failed after {retries} attempts: {last_error}")
    sys.exit(1)


if __name__ == "__main__":
    app()
