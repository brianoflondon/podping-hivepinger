import asyncio
import logging
import os
import uuid
from collections import deque
from time import time
from typing import Any, AsyncContextManager, Callable

import typer
import uvicorn
from fastapi import FastAPI, Request
from fastapi.concurrency import asynccontextmanager
from fastapi.responses import JSONResponse

# absolute import to support running as a script
from hivepinger import __version__
from hivepinger.gossip_client import GossipClient
from hivepinger.hive_actions import get_hive_client
from hivepinger.hive_writer import send_podping_to_hive, send_startup_podping
from hivepinger.podping_queue import PodpingQueue
from models.podping import CURRENT_PODPING_VERSION, HiveOperationId, Medium, Podping, Reason

DEFAULT_DB_PATH = "data/podping_queue.db"

# per-reason processing intervals (seconds).  Acts as a simple priority system
# where low numbers are processed more frequently.  Users can adjust these
# values as desired; new reasons added to the ``Reason`` enum will default to
# 10 seconds unless overridden here.
REASON_INTERVALS: dict[str, float] = {
    Reason.UPDATE.value: 10,
    Reason.LIVE.value: 1,
    Reason.LIVE_END.value: 30,
    Reason.NEW_IRI.value: 60,
}

MAX_IRIS_PER_PODPING = 120

RATE_LIMIT_MAX = 90  # calls
RATE_LIMIT_PERIOD = 60  # seconds


def create_lifespan(
    db_path: str,
    hive_account_name: str,
    hive_posting_key: str,
    no_broadcast: bool,
    podping_prefix: str,
    session_id: int,
) -> Callable[[FastAPI], AsyncContextManager[Any]]:
    """Factory function to create lifespan with queue init/teardown.

    The returned context manager is executed during FastAPI startup and
    shutdown.  We also attach an ``asyncio.Event`` to ``app.state.shutdown_event``
    which is triggered when the lifespan closes; the background loop listens
    for this event to exit promptly during shutdown.
    """
    """Factory function to create lifespan with queue init/teardown.

    The returned context manager is executed during FastAPI startup and
    shutdown.  We also send the startup podping here (instead of using the
    old ``@app.on_event("startup")`` decorator) so that the state we mutate
    (`app.state.fail_*`) is set while the lifespan context is active.
    """

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        logging.info("Starting API lifespan: initializing PodpingQueue")
        queue = PodpingQueue(db_path)
        await queue.open()
        app.state.queue = queue
        app.state.fail_state = (
            False  # used to signal unhealthy status in case of critical failures
        )
        app.state.fail_reason = ""
        # create an event that signals shutdown; background loop can watch it
        app.state.shutdown_event = asyncio.Event()

        # attempt to send startup podping while the app is still coming up;
        # any failure simply marks the service unhealthy but does not abort the
        # startup process.
        hive_client = get_hive_client(keys=[hive_posting_key], nobroadcast=no_broadcast)
        startup_result = await send_startup_podping(
            hive_account_name=hive_account_name,
            hive_posting_key=hive_posting_key,
            hive_client=hive_client,
            no_broadcast=no_broadcast,
            podping_prefix=podping_prefix,
            session_id=session_id,
            version=__version__,
        )
        app.state.fail_state = not startup_result.success
        app.state.fail_reason = startup_result.fail_reason
        app.state.gossip_enabled = os.getenv("GOSSIP_WRITER_ENABLED", "false").lower() in (
            "true",
            "1",
            "yes",
        )

        yield
        logging.info("Application shutdown: cleaning up resources")
        await queue.close()
        # signal any listeners that shutdown has begun
        try:
            app.state.shutdown_event.set()
        except Exception:
            pass

    return lifespan


# command-line interface
cli = typer.Typer()


def create_fast_api_app(
    db_path: str = DEFAULT_DB_PATH,
    session_id: int | None = None,
    hive_account_name: str = "",
    hive_posting_key: str = "",
    no_broadcast: bool = False,
    podping_prefix: str = "pp",
    verbose: bool = False,
) -> FastAPI:
    """Create FastAPI app with the specified configuration.

    Additional arguments control the Hive credentials used for the startup
    podping; they are passed through to the lifespan context so that the ping
    is sent during application startup.
    """
    if not session_id:
        session_id = uuid.uuid4().int & (1 << 64) - 1
    try:
        fast_api_app = FastAPI(
            lifespan=create_lifespan(
                db_path,
                hive_account_name,
                hive_posting_key,
                no_broadcast,
                podping_prefix,
                session_id,
            ),
            title="Podping HivePinger API",
            description="The API to receive feed updates and send out Podping on Hive.",
            version=__version__ or "0.0.0",
            redirect_slashes=False,
        )
    except Exception as exc:
        logging.exception(f"Error creating FastAPI app: {exc}")
        raise

    # Add proxy middleware to trust headers from reverse proxy
    # This allows FastAPI to correctly detect HTTPS when behind nginx proxy
    # simple in-memory rate limiting by IP (sliding window)
    # note: this is intentionally basic and will reset when the process
    # restarts; suitable for lightweight usage or tests.  If the service is
    # deployed behind multiple workers the limit applies per worker instance.

    _rate_window: dict[str, deque[float]] = {}

    def _check_rate(ip: str) -> bool:
        now = time()
        dq = _rate_window.setdefault(ip, deque())
        # purge stale entries
        cutoff = now - RATE_LIMIT_PERIOD
        while dq and dq[0] <= cutoff:
            dq.popleft()
        if len(dq) >= RATE_LIMIT_MAX:
            return False
        dq.append(now)
        return True

    @fast_api_app.middleware("http")
    async def proxy_middleware(request: Request, call_next):
        # Trust common proxy headers
        if "x-forwarded-proto" in request.headers:
            request.scope["scheme"] = request.headers["x-forwarded-proto"]
        if "x-forwarded-host" in request.headers:
            request.scope["server"] = (request.headers["x-forwarded-host"], None)
        client_ip = request.headers.get("cf-connecting-ip", None)
        if client_ip:
            if not _check_rate(client_ip):
                logging.warning(f"Rate limit exceeded for {client_ip}")
                return JSONResponse(status_code=429, content={"detail": "Rate limit exceeded"})

        response = await call_next(request)
        return response

    # delegate definition of the HTTP endpoints to a helper in a separate
    # module so the core ``api.py`` remains focused on wiring and setup.
    from hivepinger.api_routes import register_routes  # local import to avoid

    # circularity during early application import

    register_routes(fast_api_app, session_id, podping_prefix, no_broadcast, verbose)

    return fast_api_app


@cli.command()
def serve(
    host: str = typer.Option("0.0.0.0", help="Host to run the server on"),
    port: int = typer.Option(8000, help="Port to run the server on"),
    workers: int = typer.Option(1, help="Number of worker processes for handling requests"),
    hive_account_name: str = typer.Option(
        None, help="Hive account name for signing transactions (optional)"
    ),
    hive_posting_key: str = typer.Option(
        None, help="Hive posting key for signing transactions (optional)"
    ),
    podping_prefix: str = typer.Option(
        "pp", help="Prefix for Hive operation IDs (default: 'pp', use 'pplt' for testing)"
    ),
    verbose: bool = typer.Option(
        False,
        "--verbose",
        "-v",
        help="Enable verbose logging to see every received and sent podping url in the logs",
    ),
) -> None:
    """Run the FastAPI server and any additional async tasks."""
    if not hive_account_name:
        # get the name from OS .env
        hive_account_name = os.getenv("HIVE_ACCOUNT_NAME", "")
    if not hive_posting_key:
        # get the key from OS .env
        hive_posting_key = os.getenv("HIVE_POSTING_KEY", "")
    no_broadcast_str = os.getenv("NO_BROADCAST", "false")
    if no_broadcast_str.lower() in ("true", "1", "yes"):
        no_broadcast = True
        logging.warning(
            "NO_BROADCAST is set to true. Transactions will not be written to Hive (broadcast disabled)."
        )
    else:
        no_broadcast = False

    env_verbose = os.getenv("VERBOSE", "false")
    if env_verbose.lower() in ("true", "1", "yes"):
        verbose = True

    if not hive_account_name or not hive_posting_key:
        logging.warning(
            "Hive account name or posting key not provided. Hive operations will not be sent."
        )
    logging.info(
        f"Sending podpings with account={hive_account_name} no_broadcast={no_broadcast} prefix={podping_prefix} verbose={verbose}"
    )
    asyncio.run(
        _serve(
            host,
            port,
            workers,
            hive_account_name,
            hive_posting_key,
            no_broadcast,
            podping_prefix,
            verbose,
        )
    )


async def _serve(
    host: str,
    port: int,
    workers: int,
    hive_account_name: str,
    hive_posting_key: str,
    no_broadcast: bool = False,
    podping_prefix: str = "pp",
    verbose: bool = False,
) -> None:
    session_id = uuid.uuid4().int & (1 << 64) - 1
    fast_api_app = create_fast_api_app(
        session_id=session_id,
        hive_account_name=hive_account_name,
        hive_posting_key=hive_posting_key,
        no_broadcast=no_broadcast,
        podping_prefix=podping_prefix,
        verbose=verbose,
    )

    # create a Hive client for use by the background loop; the lifespan
    # context has already sent its own startup ping, so we just need a
    # reusable client for later batch operations.
    hive_client = get_hive_client(keys=[hive_posting_key], nobroadcast=no_broadcast)

    # optional gossip-writer integration (ZMQ + Cap'n Proto)
    gossip_enabled = os.getenv("GOSSIP_WRITER_ENABLED", "false").lower() in (
        "true",
        "1",
        "yes",
    )
    gossip_client = GossipClient()
    if gossip_enabled:
        gossip_addr = os.getenv("GOSSIP_WRITER_ZMQ", "tcp://127.0.0.1:9998")
        if gossip_client.connect(gossip_addr):
            logging.info(f"Gossip writer enabled, connected to {gossip_addr}")
        else:
            logging.warning("Gossip writer enabled but connection failed")
    else:
        logging.info("Gossip writer disabled (set GOSSIP_WRITER_ENABLED=true to enable)")

    config = uvicorn.Config(
        fast_api_app,
        host=host,
        port=port,
        workers=workers,
        log_config=None,
        log_level="warning",
        access_log=True,
    )
    server = uvicorn.Server(config)

    async def background_loop():
        """Process the podping queue according to per-reason intervals.

        Each reason/medium combination is treated as a separate operation id; items
        are batched together into a single ``Podping`` model and sent with the
        corresponding Hive operation id.  The loop keeps a single Hive client
        alive for 30 minutes at a time and will recreate it on error.
        """
        # wait for queue initialization from lifespan
        while not hasattr(fast_api_app.state, "queue"):
            await asyncio.sleep(0.1)
        queue: PodpingQueue = fast_api_app.state.queue  # type: ignore

        # the previous implementation recorded a "last_run" timestamp and
        # sent a batch as soon as the service started; that meant the very
        # first URL was dispatched immediately and no buffering occurred.  the
        # new approach delegates timing to the queue itself (via
        # :meth:`PodpingQueue.ready_to_send`), which examines the age of the
        # oldest pending entry and only returns ``True`` once it has been in
        # the queue for the configured interval.

        # helper to build/refresh hive client
        last_client_creation = time()

        def renew_client():
            nonlocal hive_client, last_client_creation
            hive_client = get_hive_client(keys=[hive_posting_key], nobroadcast=no_broadcast)
            logging.info(
                f"Hive client renewed: {hive_client.rpc.url if hive_client and hive_client.rpc else 'N/A'}"
            )
            last_client_creation = time()

        log_func = logging.info if verbose else logging.debug
        log_func("background_loop: checking for pending podpings to send")

        while True:
            # if the queue has been closed (shutdown) bail out early
            if getattr(queue, "_db", None) is None:
                logging.info("background_loop: queue closed, exiting")
                break

            # also respect explicit shutdown event if set
            if (
                hasattr(fast_api_app.state, "shutdown_event")
                and fast_api_app.state.shutdown_event.is_set()
            ):
                logging.info("background_loop: shutdown event set, exiting")
                break

            now = time()

            # recreate client periodically
            if now - last_client_creation > 1800:
                renew_client()

            try:
                # process each reason according to its configured interval
                for reason_str, interval in REASON_INTERVALS.items():
                    # only dequeue a batch when the oldest pending URL for that
                    # reason has been queued long enough; this allows us to
                    # accumulate additional URLs that arrive shortly after the
                    # first one.
                    if not await queue.ready_to_send(reason_str, interval):
                        continue

                    # fetch the batch but don't delete yet.  ``all_ids`` will be
                    # removed only after a successful send.  This prevents loss if
                    # the downstream transaction fails.
                    batch, all_ids = await queue.peek_batch(reason=reason_str)
                    if not batch:
                        continue

                    # group by medium so each op_id has consistent medium/reason
                    groups: dict[str, list] = {}  # type: ignore
                    for item in batch:
                        groups.setdefault(item["medium"], []).append(item)

                    # split groups.items() into blocks of upto MAX_IRIS_PER_PODPING to avoid hitting Hive's max json size limit;
                    # this is a simple approach that may result in uneven batches but keeps the implementation straightforward

                    for medium, items in groups.items():
                        for i in range(0, len(items), MAX_IRIS_PER_PODPING):
                            batch_items = items[i : i + MAX_IRIS_PER_PODPING]
                            iris = [item["url"] for item in batch_items]
                            podping_obj = Podping(
                                version=CURRENT_PODPING_VERSION,
                                medium=Medium(medium),
                                reason=Reason(reason_str),
                                iris=iris,
                                timestampNs=int(now * 1e9),
                                sessionId=session_id,
                            )

                            json_id = str(
                                HiveOperationId(
                                    prefix=podping_prefix,
                                    medium=Medium(medium),
                                    reason=Reason(reason_str),
                                )
                            )

                            result = await send_podping_to_hive(
                                podping_obj=podping_obj,
                                json_id=json_id,
                                hive_account_name=hive_account_name,
                                hive_client=hive_client,
                                hive_posting_key=hive_posting_key,
                                queue=queue,
                                batch_items=batch_items,
                                batch_count=len(items),
                                log_func=log_func,
                            )
                            if result.success:
                                fast_api_app.state.fail_state = False
                                fast_api_app.state.fail_reason = ""

                                # forward batch to gossip-writer (best-effort)
                                if gossip_client.is_connected:
                                    gossip_client.send_podping_writes(
                                        iris,
                                        Medium(medium),
                                        Reason(reason_str),
                                    )
                                    gossip_client.drain_replies()
                            else:
                                fast_api_app.state.fail_state = True
                                fast_api_app.state.fail_reason = result.fail_reason
                                if result.should_renew_client:
                                    renew_client()
                                continue

                # housekeeping
                await queue.purge_old_sent()
            except Exception:
                logging.exception("Error in background podping loop")
            # small sleep to allow other reasons to become eligible quickly
            await asyncio.sleep(1)

    # run server and background loop concurrently
    try:
        await asyncio.gather(server.serve(), background_loop())
    finally:
        gossip_client.close()
    print("Server shutdown complete")


if __name__ == "__main__":
    log_level = os.getenv("LOG_LEVEL", "info").lower()
    if log_level in ("debug", "1", "true"):
        level = logging.DEBUG
    elif log_level in ("warning", "2", "warn"):
        level = logging.WARNING
    else:
        level = logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)-8s %(module)-25s %(lineno)4d : %(message)s",
        datefmt="%m-%dT%H:%M:%S%z",
    )

    logging.info(f"podping-hivepinger startup: initializing resources {__version__}")
    cli()
else:
    # Create app with default config for module imports
    fast_api_app = create_fast_api_app()
