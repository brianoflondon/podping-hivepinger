"""Simple blockchain watcher for custom_json operations.

This module provides a CLI powered by Typer that streams the Hive blockchain
and prints any ``custom_json`` operations whose ``id`` begins with the
specified prefix.  Because ``nectar`` exposes a *blocking* generator for the
stream, the implementation runs the stream in a background thread and pushes
matching items onto an ``asyncio.Queue`` so that the main task can remain
async/await-friendly.  A small helper parameter ``max_ops`` makes testing
easier by allowing the watcher to exit after a finite number of messages.

Now includes a live function that watches for 3speak broadcasts and sends
a live real podping for each one.

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

from models.podping import Medium, Podping, Reason

cli = typer.Typer(help="Hive blockchain watcher for custom_json ops")

CALL_URL = "http://localhost:1820/"
# CALL_URL = "http://yoga-v4vapp:1820/"
# CALL_URL = "https://hivepinger.podping.org/"

THREESPEAK_PODPING_SEND = True


@cli.command()
def watch(
    podping_prefix: str = typer.Option("pp", help="Prefix for Hive operation IDs to filter"),
    node: Optional[str] = typer.Option(None, help="Optional Hive node URL"),
    max_ops: Optional[int] = typer.Option(
        None, help="Stop after this many matching ops (useful for tests)"
    ),
    threespeak: bool = typer.Option(
        True,
        help="Whether to send podpings for 3speak broadcasts (requires CALL_URL to be set to a valid endpoint)",
    ),
    all_pings: bool = typer.Option(
        True,
        help="Whether to send all podpings on to gossip and no_broadcast them to Hive",
    ),
    call_url: str = typer.Option(
        CALL_URL,
        help="URL to send test podpings to for 3speak broadcasts (ignored if --threespeak is false)",
    ),
):
    """Start watching the chain for ``custom_json`` operations.

    ``podping_prefix`` works the same way as the parameter in :mod:`hivepinger.api`.
    The watcher will run indefinitely unless ``max_ops`` is provided, in which
    case the command will exit after collecting that many matching operations.
    """

    asyncio.run(
        async_watch(
            podping_prefix,
            node=node,
            max_ops=max_ops,
            threespeak_podping_send=threespeak,
            all_pings=all_pings,
            call_url=call_url,
        )
    )


async def async_watch(
    podping_prefix: str,
    node: str | None = None,
    hive_client: Hive | None = None,
    max_ops: int | None = None,
    threespeak_podping_send: bool = True,
    all_pings: bool = True,
    call_url: str = CALL_URL,
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
    threespeak_podping_send:
        Whether to send a podping for 3speak broadcasts.  This is primarily for testing since it requires a valid endpoint at ``call_url``.
    all_pings:
        Whether to send all podpings on to gossip and no_broadcast them to Hive.  This is primarily for testing to verify the watcher is correctly parsing and sending all valid podpings
    call_url:
        URL to send test podping requests to for 3speak broadcasts.
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
            for op in blockchain.stream(opNames=["custom_json"], raw_ops=False, start=105308498):
                op_id = op.get("id", "")
                if threespeak_podping_send and op_id == "3speak-publish":
                    logging.info(f"3speak-publish op: {op.get('')}")
                    loop.call_soon_threadsafe(queue.put_nowait, op)
                if all_pings and op_id.startswith(podping_prefix):
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
                if op.get("id", "") == "3speak-publish":
                    c_json = json.loads(op.get("json", "{}"))
                    author = c_json.get("author", "")
                    logging.info(f"3speak-publish op: {author}")
                    podping = Podping(
                        version="1.1",
                        medium=Medium.VIDEO,
                        reason=Reason.UPDATE,
                        iris=[f"https://legacy.3speak.tv/rss/{author}.xml"],
                        sessionId=0,
                        no_broadcast=False,
                    )
                else:
                    podping = Podping.model_validate(json.loads(op.get("json", "{}")))
                    podping.no_broadcast = True
            except ValidationError as exc:
                logging.warning(
                    f"received custom_json trx_id {op.get('trx_id', '')} but failed to parse as Podping"
                )
                logging.warning(exc)
                continue
            # include the operation id in output for easier debugging/testing
            logging.info(f"{podping} id={op.get('id', '')}")
            logging.info(f"{posting_account} in trx {op.get('trx_id', '')}")
            for iri in podping.iris:
                logging.info(f"  - {iri}")
                await send_test_podping(
                    url=iri,
                    medium=podping.medium.value,
                    reason=podping.reason.value,
                    http_client=client,
                    no_broadcast=podping.no_broadcast or False,
                    call_url=call_url,
                )
            count += 1
            if max_ops is not None and count >= max_ops:
                logging.info("received %d ops; exiting", count)
                break


async def send_test_podping(
    url: str,
    medium: str,
    reason: str,
    http_client: httpx.AsyncClient,
    no_broadcast: bool = True,
    call_url: str = CALL_URL,
) -> None:
    try:
        params: dict[str, str | bool] = {
            "url": url,
            "reason": reason,
            "medium": medium,
            "no_broadcast": no_broadcast,
            "detailed_response": True,
        }
        response = await http_client.get(call_url, params=params, timeout=1.0)
        response_data = response.json()
        message = response_data.get("message", "failed")
        response.raise_for_status()
        logging.info(f"{message} {call_url} with params {params}")
    except httpx.RequestError as exc:
        logging.warning(f"Failed to send test podping for {url}: {exc}")
    except httpx.HTTPStatusError as exc:
        logging.warning(f"Received error response when sending test podping for {url}: {exc}")


if __name__ == "__main__":  # allow ``python -m hivewatcher.watch``
    try:
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s %(levelname)-8s %(module)-25s %(lineno)4d : %(message)s",
            datefmt="%m-%dT%H:%M:%S%z",
        )
        cli()
    except (KeyboardInterrupt, SystemExit):
        logging.info("Watcher stopped by user")
    except Exception as exc:
        logging.exception(f"Watcher encountered an unexpected exception: {exc}")
        raise
