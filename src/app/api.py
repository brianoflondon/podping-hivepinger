import asyncio
import logging
import os
import uuid
from time import time

import typer
import uvicorn
from fastapi import APIRouter, FastAPI, HTTPException, Query, Request
from fastapi.concurrency import asynccontextmanager
from pydantic import HttpUrl, ValidationError

# absolute import to support running as a script
from app import __version__
from app.hive_actions import get_hive_client, send_custom_json
from app.podping_queue import PodpingQueue
from models.podping import (
    CURRENT_PODPING_VERSION,
    HiveOperationId,
    Medium,
    Podping,
    Reason,
    StartupPodping,
)

ICON = "🤖"
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

BATCH_INTERVAL_SECONDS = 10


def create_lifespan(db_path: str = DEFAULT_DB_PATH):
    """Factory function to create lifespan with queue init/teardown"""

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        logging.info("Starting API lifespan: initializing PodpingQueue")
        queue = PodpingQueue(db_path)
        await queue.open()
        app.state.queue = queue
        yield
        logging.info("Application shutdown: cleaning up resources")
        await queue.close()

    return lifespan


# This will be replaced when we parse command line arguments
app = None

# command-line interface
cli = typer.Typer()

main_router = APIRouter(prefix="")


@main_router.get("/")
async def root(
    request: Request,
    url: HttpUrl = Query(..., description="URL to podping"),
    reason: Reason = Query(Reason.UPDATE, description="Reason string"),
    medium: Medium = Query(Medium.PODCAST, description="Medium string"),
):
    """Simple endpoint matching the request signature

    Example:
    GET https://podping.cloud/?url=https://feeds.example.org/livestream/rss&reason=live&medium=music
    """

    try:
        logging.info(f"Received request with url={url}, reason={reason}, medium={medium}")
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=exc.errors())

    # enqueue for background processing — crash-safe after this commit
    queue: PodpingQueue = request.app.state.queue
    row_id = await queue.enqueue(url, medium.value, reason.value)
    logging.info(f"Enqueued podping id={row_id}: {url}")

    return {"message": "queued", "reason": reason, "medium": medium, "url": url}


def create_app(db_path: str = DEFAULT_DB_PATH, session_id: int | None = None) -> FastAPI:
    """Create FastAPI app with the specified config file"""
    if not session_id:
        session_id = uuid.uuid4().int & (1 << 64) - 1
    app = FastAPI(
        lifespan=create_lifespan(db_path),
        title="Podping HivePinger API",
        description="The API to receive feed updates and send out Podping on Hive.",
        version=__version__ or "0.0.0",
        redirect_slashes=False,
    )

    # Add proxy middleware to trust headers from reverse proxy
    # This allows FastAPI to correctly detect HTTPS when behind nginx proxy
    @app.middleware("http")
    async def proxy_middleware(request: Request, call_next):
        # Trust common proxy headers
        if "x-forwarded-proto" in request.headers:
            request.scope["scheme"] = request.headers["x-forwarded-proto"]
        if "x-forwarded-host" in request.headers:
            request.scope["server"] = (request.headers["x-forwarded-host"], None)

        response = await call_next(request)
        return response

    @app.get("/health")
    @app.get("/status")
    async def health():
        # check length of queue to ensure DB is responsive; we don't want to return 200 if the queue is stuck
        queue: PodpingQueue = app.state.queue  # type: ignore
        try:
            pending_count = await queue.count_pending()
            if pending_count > 0:
                logging.warning(f"Health check: {pending_count} pending items in queue")
        except Exception as exc:
            logging.error(f"Health check failed: unable to access queue: {exc}")
            raise HTTPException(status_code=503, detail="Queue inaccessible")
        return {
            "message": "Welcome to Podping HivePinger API",
            "version": __version__,
            "status": "OK",
            "session_id": session_id,
            "documentation": "/docs",
            "pending_queue_length": pending_count,
        }

    app.include_router(main_router, tags=["main"])

    return app


@cli.command()
def serve(
    host: str = typer.Option("0.0.0.0", help="Host to run the server on"),
    port: int = typer.Option(1820, help="Port to run the server on"),
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
):
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
        logging.warning("NO_BROADCAST is set to true. Transactions will not be broadcasted.")
    else:
        no_broadcast = False

    if not hive_account_name or not hive_posting_key:
        logging.warning(
            "Hive account name or posting key not provided. Hive operations will not be sent."
        )
    asyncio.run(
        _serve(
            host, port, workers, hive_account_name, hive_posting_key, no_broadcast, podping_prefix
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
):
    session_id = uuid.uuid4().int & (1 << 64) - 1
    app = create_app(session_id=session_id)
    config = uvicorn.Config(
        app,
        host=host,
        port=port,
        workers=workers,
        log_config=None,
        log_level="warning",
        access_log=True,
    )
    server = uvicorn.Server(config)
    uuid_str = str(uuid.uuid4())
    hive_client = get_hive_client(keys=[hive_posting_key], nobroadcast=no_broadcast)
    if hive_client and hive_client.rpc and hive_client.rpc.url:
        rpc_url = hive_client.rpc.url
    else:
        rpc_url = "N/A"
    startup_podping = StartupPodping(
        server_account=hive_account_name,
        message="Podping HivePinger startup complete",
        uuid=uuid_str,
        hive=rpc_url,
        sessionId=session_id,
        v=__version__,
        pinging_app="hivepinger",
    )
    startup_op_id = str(HiveOperationId(prefix=podping_prefix, startup=True))
    startup_trx = await send_custom_json(
        json_data=startup_podping.model_dump(),
        send_account=hive_account_name,
        hive_client=hive_client,
        keys=[hive_posting_key],
        id=startup_op_id,
        nobroadcast=no_broadcast,
    )
    logging.info(
        f"Sent startup podping with uuid={uuid_str}, trx_id={startup_trx.get('trx_id', 'N/A')} {no_broadcast=}"
    )

    async def background_loop():
        """Process the podping queue according to per-reason intervals.

        Each reason/medium combination is treated as a separate operation id; items
        are batched together into a single ``Podping`` model and sent with the
        corresponding Hive operation id.  The loop keeps a single Hive client
        alive for 30 minutes at a time and will recreate it on error.
        """
        # wait for queue initialization from lifespan
        while not hasattr(app.state, "queue"):
            await asyncio.sleep(0.1)
        queue: PodpingQueue = app.state.queue

        # track last time each reason was processed
        last_run: dict[str, float] = {}

        # helper to build/refresh hive client
        last_client_creation = time()

        def renew_client():
            nonlocal hive_client, last_client_creation
            hive_client = get_hive_client(keys=[hive_posting_key], nobroadcast=no_broadcast)
            last_client_creation = time()

        while True:
            now = time()

            # recreate client periodically
            if now - last_client_creation > 1800:
                renew_client()

            try:
                # process each reason according to its configured interval
                for reason_str, interval in REASON_INTERVALS.items():
                    prev = last_run.get(reason_str, 0)
                    if now - prev < interval:
                        continue

                    batch = await queue.dequeue_batch(reason=reason_str)
                    if not batch:
                        continue

                    # group by medium so each op_id has consistent medium/reason
                    groups: dict[str, list] = {}
                    for item in batch:
                        groups.setdefault(item["medium"], []).append(item)

                    for medium, items in groups.items():
                        iris = [i["url"] for i in items]
                        podping_obj = Podping(
                            version=CURRENT_PODPING_VERSION,
                            medium=Medium(medium),
                            reason=Reason(reason_str),
                            iris=iris,
                            timestampNs=int(now * 1e9),
                            sessionId=session_id,
                        )

                        op_id = str(
                            HiveOperationId(
                                prefix=podping_prefix,
                                medium=Medium(medium),
                                reason=Reason(reason_str),
                            )
                        )

                        try:
                            trx = await send_custom_json(
                                json_data=podping_obj.model_dump(),
                                send_account=hive_account_name,
                                hive_client=hive_client,
                                keys=[hive_posting_key],
                                id=op_id,
                                nobroadcast=no_broadcast,
                            )
                            trx_id = trx.get("trx_id", "N/A")
                            logging.info(
                                f"PODPING sent op={op_id} count={len(items)} trx_id={trx_id}"
                            )
                        except Exception:
                            logging.exception(
                                "Error sending podping batch — renewing client and continuing"
                            )
                            # refresh client and leave items pending
                            renew_client()
                            continue

                        for item in items:
                            await queue.mark_sent(
                                item["url"], item["medium"], item["reason"], trx_id
                            )

                    last_run[reason_str] = now

                # housekeeping
                await queue.purge_old_sent()
            except Exception:
                logging.exception("Error in background podping loop")
            # small sleep to allow other reasons to become eligible quickly
            await asyncio.sleep(1)

    # run server and background loop concurrently
    await asyncio.gather(server.serve(), background_loop())


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-8s %(module)-25s %(lineno)4d : %(message)s",
        datefmt="%m-%dT%H:%M:%S.%f",
    )
    logging.info("Application startup: initializing resources")
    cli()
else:
    # Create app with default config for module imports
    app = create_app()
