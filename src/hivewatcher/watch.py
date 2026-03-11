"""Simple blockchain watcher for custom_json operations.

This module provides a CLI powered by Typer that streams the Hive blockchain
and prints any ``custom_json`` operations whose ``id`` begins with the
specified prefix.  Because ``nectar`` exposes a *blocking* generator for the
stream, the implementation runs the stream in a background thread and pushes
matching items onto an ``asyncio.Queue`` so that the main task can remain
async/await-friendly.  A small helper parameter ``max_ops`` makes testing
easier by allowing the watcher to exit after a finite number of messages.

Usage example (from project root):

```sh
python -m hivewatcher.watch watch --podping-prefix pp
```

"""

import asyncio
import json
import logging
import threading
from typing import Any, Dict, Optional

import httpx
import typer
from nectar.blockchain import Blockchain
from nectar.hive import Hive
from pydantic_core import ValidationError

from models.podping import Podping

cli = typer.Typer(help="Hive blockchain watcher for custom_json ops")


@cli.command()
def watch(
    podping_prefix: str = typer.Option("pp", help="Prefix for Hive operation IDs to filter"),
    node: Optional[str] = typer.Option(None, help="Optional Hive node URL"),
    max_ops: Optional[int] = typer.Option(
        None, help="Stop after this many matching ops (useful for tests)"
    ),
):
    """Start watching the chain for ``custom_json`` operations.

    ``podping_prefix`` works the same way as the parameter in :mod:`hivepinger.api`.
    The watcher will run indefinitely unless ``max_ops`` is provided, in which
    case the command will exit after collecting that many matching operations.
    """

    asyncio.run(async_watch(podping_prefix, node=node, max_ops=max_ops))


async def async_watch(
    podping_prefix: str,
    node: Optional[str] = None,
    hive_client: Optional[Hive] = None,
    max_ops: Optional[int] = None,
) -> None:
    """Internal coroutine which performs the actual watching.

    Parameters
    ----------
    podping_prefix:
        Operation id prefix to filter for.
    node:
        Optional Hive node URL; passed to :class:`nectar.hive.Hive` if a
        client isn't supplied.
    hive_client:
        If provided, this Hive instance will be used instead of creating a new
        one.  This is primarily for testing.
    max_ops:
        If not ``None`` the coroutine will return after printing this many
        matching operations.
    """

    # create or reuse client
    if hive_client is None:
        hive_client = Hive(node=[node] if node else None)

    queue: asyncio.Queue[Dict[str, Any]] = asyncio.Queue()
    loop = asyncio.get_running_loop()

    def _producer() -> None:
        logging.info("producer thread starting")
        try:
            # ``blockchain.stream`` is blocking; run in its own thread and push matches
            blockchain = Blockchain(hive_client)
            for op in blockchain.stream(opNames=["custom_json"], raw_ops=False):
                op_id = op.get("id", "")
                if op_id.startswith(podping_prefix):
                    # schedule adding to queue on the main loop
                    loop.call_soon_threadsafe(queue.put_nowait, op)
        except Exception as exc:  # pragma: no cover - defensive
            logging.exception("streaming thread raised an exception: %s", exc)

    thread = threading.Thread(target=_producer, daemon=True)
    thread.start()

    logging.info("watching for prefix %r", podping_prefix)

    count = 0
    async with httpx.AsyncClient() as client:
        while True:
            op = await queue.get()
            # simple output; could be replaced with richer handling later
            try:
                posting_account = op.get("required_posting_auths", [""])[0]
                podping = Podping.model_validate(json.loads(op.get("json", "{}")))
            except ValidationError as exc:
                logging.warning(
                    f"received custom_json trx_id {op.get('trx_id', '')} but failed to parse as Podping"
                )
                logging.warning(exc)
                continue
            # include the operation id in output for easier debugging/testing
            print(f"{podping} id={op.get('id', '')}")
            print(f"{posting_account} in trx {op.get('trx_id', '')}")
            for iri in podping.iris:
                print(f"  - {iri}")
                await send_test_podping(
                    url=iri,
                    medium=podping.medium.value,
                    reason=podping.reason.value,
                    http_client=client,
                )
            count += 1
            if max_ops is not None and count >= max_ops:
                logging.info("received %d ops; exiting", count)
                break


async def send_test_podping(
    url: str, medium: str, reason: str, http_client: httpx.AsyncClient
) -> None:
    try:
        params = {
            "url": url,
            "reason": reason,
            "medium": medium,
        }
        call_url = "http://localhost:1820/"
        response = await http_client.get(call_url, params=params, timeout=1.0)
        response.raise_for_status()
        logging.info(f"Sent test podping to {call_url} with params {params}")
    except httpx.RequestError as exc:
        logging.warning(f"Failed to send test podping for {url}: {exc}")
    except httpx.HTTPStatusError as exc:
        logging.warning(f"Received error response when sending test podping for {url}: {exc}")


if __name__ == "__main__":  # allow ``python -m hivewatcher.watch``
    try:
        cli()
    except (KeyboardInterrupt, SystemExit):
        logging.info("Watcher stopped by user")
    except Exception as exc:
        logging.exception(f"Watcher encountered an unexpected exception: {exc}")
        raise
