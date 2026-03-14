"""Optional ZMQ client for forwarding podpings to the gossip-writer.

The gossip-writer (https://github.com/Podcastindex-org/podping.alpha) listens
on a ZMQ PAIR socket and expects Cap'n Proto ``PlexoMessage``-wrapped
``PodpingWrite`` messages.  Each message carries a single IRI; the
gossip-writer batches them internally every ~3 seconds grouped by
(medium, reason) before broadcasting a signed ``GossipNotification``.

This module is designed to be a no-op when either ``pycapnp`` or ``pyzmq`` is
not installed, or when the gossip-writer feature is not enabled via env vars.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from models.podping import Medium, Reason

from typing import List

# Schema directory is relative to this file: ../schemas/
_SCHEMA_DIR = str(Path(__file__).resolve().parent.parent / "schemas")

# Lazy-loaded module-level references (populated by _ensure_capnp_loaded)
_capnp = None
_plexo_mod = None
_pw_mod = None
_capnp_loaded = False

PLEXO_TYPE_NAME = "org.podcastindex.podping.hivewriter.PodpingWrite.capnp"


def _ensure_capnp_loaded() -> bool:
    """Load capnp schemas on first use.  Returns True if available."""
    global _capnp, _plexo_mod, _pw_mod, _capnp_loaded
    if _capnp_loaded:
        return _capnp is not None
    _capnp_loaded = True
    try:
        import capnp

        capnp.remove_import_hook()
        _capnp = capnp
        _plexo_mod = capnp.load(str(Path(_SCHEMA_DIR) / "plexo_message.capnp"))
        _pw_mod = capnp.load(str(Path(_SCHEMA_DIR) / "podping_write.capnp"))
        return True
    except Exception:
        logging.warning("pycapnp not available — gossip-writer integration disabled")
        return False


def build_podping_write_message(iri: str, medium: Medium, reason: Reason) -> bytes:
    """Build a PlexoMessage-wrapped PodpingWrite in standard capnp binary.

    Returns the complete serialised bytes ready to send over ZMQ.
    """
    if not _ensure_capnp_loaded():
        raise RuntimeError("pycapnp is not available")

    # Inner PodpingWrite
    pw = _pw_mod.PodpingWrite.new_message()
    pw.medium = medium.value
    # NEW_IRI is a hivepinger extension not in the capnp schema; map to "update"
    pw.reason = reason.value if reason.value in ("update", "live", "liveEnd") else "update"
    pw.iri = iri
    pw_bytes = pw.to_bytes()

    # Outer PlexoMessage wrapper
    plexo = _plexo_mod.PlexoMessage.new_message()
    plexo.typeName = PLEXO_TYPE_NAME
    plexo.payload = pw_bytes
    return plexo.to_bytes()


class GossipClient:
    """Manages an optional ZMQ PAIR connection to the gossip-writer."""

    def __init__(self) -> None:
        self._socket = None
        self._context = None
        self._monitor = None
        self._connected = False

    def connect(self, addr: str = "tcp://127.0.0.1:9998") -> bool:
        """Connect to the gossip-writer ZMQ PAIR socket.

        Returns True on success, False if pyzmq is unavailable or connect fails.
        """
        if not _ensure_capnp_loaded():
            logging.warning("Cannot connect gossip client: pycapnp unavailable")
            return False

        try:
            import zmq
        except ImportError:
            logging.warning("Cannot connect gossip client: pyzmq not installed")
            return False

        try:
            self._context = zmq.Context()
            self._socket = self._context.socket(zmq.PAIR)
            self._socket.setsockopt(zmq.SNDTIMEO, 50)  # 50ms fire-and-forget
            self._socket.setsockopt(zmq.RCVTIMEO, 0)
            self._socket.setsockopt(zmq.LINGER, 0)
            self._socket.setsockopt(zmq.SNDHWM, 10000)

            # Set up a socket monitor to detect the actual TCP connection
            monitor_addr = "inproc://gossip-monitor"
            self._socket.monitor(monitor_addr, zmq.EVENT_CONNECTED | zmq.EVENT_CONNECT_DELAYED)
            self._monitor = self._context.socket(zmq.PAIR)
            self._monitor.connect(monitor_addr)

            self._socket.connect(addr)
            self._connected = True
            logging.info(f"Gossip client socket created, connecting to {addr}")

            # Wait up to 3 seconds for the TCP connection to be established
            if self._verify_connection(timeout_ms=3000):
                logging.info(f"Gossip client TCP connection verified to {addr}")
            else:
                logging.warning(
                    f"Gossip client could not verify TCP connection to {addr} "
                    "(gossip-writer may not be running yet — will retry on send)"
                )

            # Clean up monitor
            self._socket.monitor(None)
            self._monitor.close()
            self._monitor = None

            return True
        except Exception:
            logging.exception("Failed to connect gossip client")
            self._connected = False
            return False

    def _verify_connection(self, timeout_ms: int = 3000) -> bool:
        """Poll the socket monitor for a CONNECTED event."""
        import zmq

        if self._monitor is None:
            return False
        if self._monitor.poll(timeout_ms):
            try:
                msg = zmq.utils.monitor.recv_monitor_message(self._monitor)
                return msg["event"] == zmq.EVENT_CONNECTED
            except Exception:
                return False
        return False

    @property
    def is_connected(self) -> bool:
        return self._connected

    def send_podping_writes(self, iris: List[str], medium: Medium, reason: Reason) -> int:
        """Send a batch of PodpingWrite messages to the gossip-writer.

        One capnp message is sent per IRI (matching the gossip-writer's
        expected input format — it batches them internally).

        Returns the number of IRIs successfully sent.
        Failures are logged but never raised — gossip is best-effort.
        """
        if not self._connected or self._socket is None:
            return 0

        sent = 0
        try:
            import zmq

            for iri in iris:
                try:
                    msg_bytes = build_podping_write_message(iri, medium, reason)
                    self._socket.send(msg_bytes, zmq.DONTWAIT)
                    logging.debug(f"Gossip: sent PodpingWrite for {iri}")
                    sent += 1
                except Exception as exc:
                    logging.warning(f"Gossip: failed to send PodpingWrite for {iri}: {exc}")
        except Exception as exc:
            logging.warning(f"Gossip: error during batch send: {exc}")
        return sent

    def drain_replies(self) -> int:
        """Read and discard any pending reply messages from the gossip-writer.

        Returns the number of messages drained.
        """
        if not self._connected or self._socket is None:
            return 0

        count = 0
        try:
            import zmq

            while True:
                try:
                    self._socket.recv(zmq.NOBLOCK)
                    count += 1
                except zmq.Again:
                    break
        except Exception:
            pass
        return count

    def close(self) -> None:
        """Disconnect and clean up resources."""
        if self._socket is not None:
            try:
                self._socket.close()
            except Exception:
                pass
            self._socket = None
        if self._context is not None:
            try:
                self._context.term()
            except Exception:
                pass
            self._context = None
        self._connected = False
        logging.info("Gossip client closed")
