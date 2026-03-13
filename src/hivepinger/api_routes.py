import logging
from typing import Any, List

from fastapi import FastAPI, HTTPException, Query, Request, Response
from pydantic import HttpUrl, ValidationError

from hivepinger import __version__
from hivepinger.podping_queue import PodpingQueue
from models.podping import Medium, Reason


def register_routes(
    app: FastAPI,
    session_id: int,
    podping_prefix: str,
    no_broadcast: bool,
    verbose: bool,
) -> None:
    """Attach the health and root endpoints to *app*.

    The helper mirrors the layout formerly in ``api.py``.  Parameters are
    passed through from :func:`create_fast_api_app` so that the inner route
    handlers can access them without relying on closure tricks or globals.
    """

    @app.get("/health", tags=["Health"])
    @app.get("/status", tags=["Health"])
    async def health(list_iris: bool = False) -> dict[str, Any]:
        """
        Performs a health check of the Podping HivePinger API.
        Args:
            list_iris (bool, optional): If True, includes a list of pending IRIs in the response. Defaults to False.
        Returns:
            dict[str, Any]: A dictionary containing API health information, including version, status, session ID,
            podping prefix, broadcast status, documentation link, pending queue length, and optionally pending IRIs.
        Raises:
            HTTPException: If the queue is inaccessible or if the application is in a fail state.
        """

        health: dict[str, Any] = {
            "message": "Welcome to Podping HivePinger API",
            "version": __version__,
            "status": "OK",
            "session_id": session_id,
            "podping_prefix": podping_prefix,
            "no_broadcast": no_broadcast,
            "documentation": "/docs",
        }

        queue: PodpingQueue  # type: ignore
        pending_iris: List[str] = []
        try:
            queue = app.state.queue  # type: ignore
            pending_count = await queue.count_pending()
            if list_iris:
                pending_items, _ = await queue.peek_batch()
                pending_iris = [item["url"] for item in pending_items]
        except Exception as exc:  # pragma: no cover - mirrors previous behaviour
            import logging

            logging.error(f"Health check failed: unable to access queue: {exc}")
            pending_count = 0
            pending_iris = []
            raise HTTPException(status_code=503, detail="Queue inaccessible")

        health["pending_queue_length"] = pending_count
        health["pending_iris"] = pending_iris

        if getattr(app.state, "fail_state", False):
            import logging

            logging.error("Health check: fail_state is True, returning unhealthy status")
            raise HTTPException(
                status_code=503,
                detail={"error": app.state.fail_reason, "health": health},
            )

        if pending_count > 0:
            import logging

            logging.info(f"Health check: {pending_count} pending items in queue")
        return health

    @app.get("/check", tags=["Health"], response_model=None)
    async def check(
        url: HttpUrl = Query(..., description="URL to lookup"),
    ) -> dict[str, Any]:
        """
        Return the pending or sent status and transaction IDs for a given URL.
        This endpoint allows clients to check whether a specific URL is currently
        pending in the queue or has already been sent in a transaction.
        The response includes both the pending status and a list of
        transaction IDs where the URL was previously sent.

        History is only stored for 24 hours, so if a URL was sent more than
        24 hours ago it will not appear in the results.

        The response includes:
        - `pending`: whether the URL is currently still queued for sending
        - `sent`: list of transaction IDs where the URL was previously sent
        """
        try:
            queue: PodpingQueue = app.state.queue  # type: ignore
            sent_ids = await queue.find_trx_for_url(str(url))
            pending_details = await queue.pending_details_for_url(str(url))
            pending = bool(pending_details)
            return {"pending": pending, "pending_details": pending_details, "sent": sent_ids}
        except Exception as exc:  # pragma: no cover - defensive
            logging.error(f"Check endpoint failed: unable to access queue: {exc}")
            raise HTTPException(status_code=503, detail="Queue inaccessible")

    @app.get("/podping/", tags=["Podping"], response_model=None)
    @app.get("/", tags=["Podping"], response_model=None)
    async def root(
        request: Request,
        url: HttpUrl = Query(..., description="URL to podping"),
        reason: Reason = Query(Reason.UPDATE, description="Reason string"),
        medium: Medium = Query(Medium.PODCAST, description="Medium string"),
        detailed_response: bool = Query(
            False, description="Whether to include detailed response information"
        ),
    ) -> Any:
        """
        Handles incoming podping requests by enqueuing the provided URL and associated metadata for background processing.

        Args:
            request (Request): The FastAPI request object.
            url (HttpUrl): The URL to podping, provided as a query parameter.
            reason (Reason): The reason for the podping, defaults to Reason.UPDATE.
            medium (Medium): The medium for the podping, defaults to Medium.PODCAST.
            detailed_response (bool): Whether to include detailed response information, defaults to False.
                When False, the response mimics the `podping.cloud` API. When True includes additional fields for debugging and transparency.

        Returns:
            dict[str, Any] | Response: A dictionary containing the result message, reason, medium, and URL, or a plain text response.

        Raises:
            HTTPException: If input validation fails (status code 422) or if the service is in a fail state (status code 503).
        request: Request,

        """
        try:
            import logging

            logging.debug(f"Received {reason} {medium} {url}")
        except ValidationError as exc:  # pragma: no cover
            raise HTTPException(status_code=422, detail=exc.errors())

        # enqueue for background processing
        queue: PodpingQueue = request.app.state.queue
        row_id = await queue.enqueue(url, medium.value, reason.value)

        import logging

        log_func = logging.info if verbose else logging.debug
        message = "enqueued" if row_id > 0 else "duplicate"
        log_func(
            f"{message:>10} podping: reason={reason} medium={medium} url={url} row_id={row_id}"
        )

        if app.state.fail_state:
            logging.error(
                f"Request received but service is in fail_state: {app.state.fail_reason}"
            )
            raise HTTPException(status_code=503, detail={"error": app.state.fail_reason})
        if detailed_response:
            return {"message": message, "reason": reason, "medium": medium, "url": url}
        return Response(content="Success!", media_type="text/plain")
