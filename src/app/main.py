import logging
import uuid

from fastapi import FastAPI, HTTPException, Query
from fastapi.concurrency import asynccontextmanager
from pydantic import ValidationError

from models.podping import Mediums, Reasons

session_id = uuid.uuid4().int & (1 << 64) - 1


def create_lifespan(config_filename: str):
    """Factory function to create lifespan with the correct config filename"""

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        logging.basicConfig(level=logging.INFO)
        logging.info("Application startup: initializing resources")
        logging.info(f"Session ID: {session_id}")
        yield
        logging.info("Application shutdown: cleaning up resources")

    return lifespan

app = FastAPI()

@app.get("/")
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
