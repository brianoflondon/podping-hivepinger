import argparse
import logging
import socket

import uvicorn
from fastapi import APIRouter, FastAPI, HTTPException, Query, Request
from fastapi.concurrency import asynccontextmanager
from pydantic import ValidationError

from models.podping import Mediums, Reasons

from . import __version__

ICON = "🤖"


def create_lifespan():
    """Factory function to create lifespan with the correct config filename"""

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        logging.basicConfig(level=logging.INFO)
        logging.info("Application startup: initializing resources")
        yield
        logging.info("Application shutdown: cleaning up resources")

    return lifespan


# This will be replaced when we parse command line arguments
app = None

main_router = APIRouter(prefix="")


@main_router.get("/")
async def root(
    url: str = Query(..., description="URL to podping"),
    reason: Reasons = Query(..., description="Reason string"),
    medium: Mediums = Query(..., description="Medium string"),
):
    """Simple endpoint matching the request signature

    Example:
    GET https://podping.cloud/?url=https://feeds.example.org/livestream/rss&reason=live&medium=music
    """

    try:
        # we don't actually use url in the model, but we validate the remaining
        logging.info(f"Received request with url={url}, reason={reason}, medium={medium}")
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=exc.errors())

    # return a boilerplate response
    return {"message": "boilerplate response", "reason": reason, "medium": medium, "url": url}


def create_app() -> FastAPI:
    """Create FastAPI app with the specified config file"""
    app = FastAPI(
        lifespan=create_lifespan(),
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

    # Add root endpoint here
    @app.get("/health")
    @app.get("/status")
    async def root():
        return {
            "message": "Welcome to Podping HivePinger API",
            "version": __version__,
            "status": "OK",
            "server_id": "id",
            "dns_name": socket.getfqdn(),
            "local_machine_name": "local_machine_name",
            "documentation": "/docs",
        }

    app.include_router(main_router, tags=["main"])

    return app


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Podping HivePinger API Server")
    parser.add_argument(
        "--host", type=str, default="0.0.0.0", help="Host to bind to (default: 0.0.0.0)"
    )
    parser.add_argument("--port", type=int, default=1820, help="Port to bind to (default: 1820)")
    parser.add_argument(
        "--workers", type=int, default=1, help="Number of worker processes (default: 1)"
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
        help="Show program version and exit",
    )
    parser.add_argument(
        "--hive_active_key",
        type=str,
        default=None,
        help="Hive active key for signing transactions (optional)",
    )

    args = parser.parse_args()

    # Create the app with the specified config file
    app = create_app()
    uvicorn.run(
        app,
        host=args.host,
        port=args.port,
        workers=args.workers,
        log_config=None,
        log_level="warning",
        access_log=True,
    )
else:
    # Create app with default config for module imports
    app = create_app()
