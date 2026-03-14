"""Send a constructed Podping to the Hive blockchain.

Extracted from the inline try/except block in ``api.py`` so that the
background loop stays focused on scheduling and the sending logic can be
tested independently.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Callable, Dict, List, NamedTuple

from hivepinger.hive_actions import CustomJsonSendError, send_custom_json
from hivepinger.podping_queue import PodpingQueue
from models.podping import HiveOperationId, HiveTrxID, Podping, StartupPodping


class StartupResult(NamedTuple):
    """Outcome of :func:`send_startup_podping`."""

    success: bool
    fail_reason: str


async def send_startup_podping(
    *,
    hive_account_name: str,
    hive_posting_key: str,
    hive_client: Any,
    no_broadcast: bool,
    podping_prefix: str,
    session_id: int,
    version: str,
) -> StartupResult:
    """Build and broadcast the startup podping.

    Returns a :class:`StartupResult` indicating success/failure.  The caller
    should set ``app.state.fail_*`` accordingly.
    """
    import uuid

    try:
        uuid_str = str(uuid.uuid4())
        rpc_url = hive_client.rpc.url if hive_client and hive_client.rpc else "N/A"
        if not rpc_url:
            rpc_url = "N/A"
            logging.warning("Hive client RPC URL is not available; using 'N/A' in logs")
        startup_podping = StartupPodping(
            server_account=hive_account_name,
            message="Podping HivePinger startup complete",
            uuid=uuid_str,
            hive=rpc_url,
            sessionId=session_id,
            v=version,
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
        trx_id = HiveTrxID(trx=startup_trx)
        rpc_url = hive_client.rpc.url if hive_client and hive_client.rpc else "N/A"
        logging.info(
            f"Sent startup podping with uuid={uuid_str} {trx_id.link} {rpc_url=} {no_broadcast=}"
        )
        return StartupResult(success=True, fail_reason="")

    except CustomJsonSendError as exc:
        logging.error(f"Startup podping failed: {exc}")
        return StartupResult(success=False, fail_reason=f"Startup podping error: {exc}")

    except Exception as exc:
        logging.exception("Unexpected error sending startup podping")
        return StartupResult(
            success=False,
            fail_reason=f"Startup podping unexpected error: {exc}",
        )


class WriteResult(NamedTuple):
    """Outcome of a :func:`send_podping_to_hive` attempt."""

    success: bool
    """True when the transaction was accepted by Hive."""
    should_renew_client: bool
    """True when the caller should recreate the Hive client."""
    fail_reason: str
    """Non-empty on failure — human-readable error description."""


async def send_podping_to_hive(
    *,
    podping_obj: Podping,
    json_id: str,
    hive_account_name: str,
    hive_client: Any,
    hive_posting_key: str,
    queue: PodpingQueue,
    batch_items: List[Dict[str, Any]],
    batch_count: int = 0,
    log_func: Callable[..., None] = logging.debug,
) -> WriteResult:
    """Send *podping_obj* to Hive, mark items sent, and handle errors.

    This wraps the entire try/except block that was previously inline in
    ``api.py``.  On success the batch items are marked sent and removed from
    pending.  On failure the appropriate error is logged and the result tells
    the caller what recovery action (if any) is needed.
    """
    try:
        trx = await send_custom_json(
            json_data=podping_obj.model_dump(),
            send_account=hive_account_name,
            hive_client=hive_client,
            keys=[hive_posting_key],
            id=json_id,
        )
        trx_id = HiveTrxID(trx=trx)
        rpc_url = hive_client.rpc.url if hive_client and hive_client.rpc else "N/A"
        logging.info(
            f"PODPING sent json_id={json_id} count={len(batch_items)} {trx_id.link} {rpc_url=}"
        )
        for item in batch_items:
            await queue.mark_sent(item["url"], item["medium"], item["reason"], str(trx_id))
            log_func(f"{trx_id} {item['url']} marked sent")
        # successfully sent — remove from pending
        ids_to_remove = [item["id"] for item in batch_items]
        await queue.remove_pending(ids_to_remove)

        return WriteResult(success=True, should_renew_client=False, fail_reason="")

    except CustomJsonSendError as ex:
        logging.error(f"Failed to send podping batch op={json_id} count={batch_count}: {ex}")
        if "RC exhaustion" in str(ex):
            await asyncio.sleep(60)
        return WriteResult(
            success=False,
            should_renew_client=False,
            fail_reason=f"CustomJsonSendError: {ex}",
        )

    except Exception:
        logging.exception("Error sending podping batch — renewing client and continuing")
        return WriteResult(
            success=False,
            should_renew_client=True,
            fail_reason="Unexpected error — client renewal requested",
        )
