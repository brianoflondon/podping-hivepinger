# Gossip-Writer Integration Plan

## Overview

Two linked changes to podping-hivepinger:

1. **Replace static `Medium`/`Reason` enums** with Cap'n Proto schema-aligned
   enums that carry numeric ordinals matching the canonical
   [podping-schemas](https://github.com/Podcastindex-org/podping-schemas).
2. **Send podpings to the gossip-writer** over ZMQ after a successful Hive
   write, using the Cap'n Proto binary wire format the gossip-writer expects.

Both changes are **optional at runtime** — the service continues to work
without the gossip-writer connected.

## Why not capnpy?

The original `podping-hivewriter` uses `capnpy-agates`, which:

- Requires Python < 3.12 (we target ≥ 3.13).
- Needs the `capnproto` C++ toolchain installed — painful on macOS.
- Has limited maintenance.

Instead we use **`pycapnp`**, which:

- Supports Python 3.13+.
- Reads the same `.capnp` schema files natively.
- Works on macOS without extra system libraries (ships prebuilt wheels).

## Cap'n Proto Schema Files

Vendored from [podping-schemas](https://github.com/Podcastindex-org/podping-schemas)
into `src/schemas/`:

| File | Purpose |
|------|---------|
| `podping_medium.capnp` | `PodpingMedium` enum (mixed=0 … courseL=18) |
| `podping_reason.capnp` | `PodpingReason` enum (update=0, live=1, liveEnd=2) |
| `podping_write.capnp` | `PodpingWrite` struct {medium, reason, iri} |
| `plexo_message.capnp` | `PlexoMessage` wrapper {typeName, payload} |

## Enum Changes (`models/podping.py`)

Each `StrEnum` member gains a `capnp_ordinal` property that returns the
matching Cap'n Proto numeric code, e.g. `Medium.PODCAST.capnp_ordinal == 1`.
The string values remain unchanged so existing JSON serialisation is
unaffected.

## New Modules

### `hivepinger/hive_writer.py`

Extracted from `api.py` lines 428-477.  Contains
`send_podping_to_hive()` — sends a `Podping` to Hive via `send_custom_json`,
handles success bookkeeping (mark sent, remove pending), and returns a
`HiveTrxID` on success.  On failure it raises the original exception so
the caller can decide how to react.

### `hivepinger/gossip_client.py`

Manages an optional ZMQ PAIR socket to the gossip-writer:

- **`GossipClient`** class:
  - `connect(addr)` / `close()` — lifecycle.
  - `send_podping_write(iri, medium, reason)` — builds a
    PlexoMessage-wrapped PodpingWrite in Cap'n Proto binary and sends it
    over ZMQ.
  - Fire-and-forget: failures are logged but never block the main loop.

Configuration via environment variables:

| Variable | Default | Description |
|----------|---------|-------------|
| `GOSSIP_WRITER_ENABLED` | `false` | Enable gossip forwarding |
| `GOSSIP_WRITER_ZMQ` | `tcp://127.0.0.1:9998` | ZMQ address |

## Wire-Up in `api.py`

After constructing a `Podping` and computing `json_id`, the background
loop calls `send_podping_to_hive()`.  On success it then iterates over
each IRI and calls `gossip_client.send_podping_write(iri, medium, reason)`
(if enabled).

```
podping_obj constructed
        │
        ▼
 send_podping_to_hive()  ───► Hive blockchain
        │
        ▼ (on success)
 gossip_client.send_podping_write()  ───► gossip-writer (ZMQ)
        │
        ▼
 mark sent / remove pending
```

## ZMQ Message Format (matches podping.alpha front-end)

Each message is a serialised Cap'n Proto `PlexoMessage`:

```
PlexoMessage {
    typeName: "org.podcastindex.podping.hivewriter.PodpingWrite.capnp"
    payload:  <serialised PodpingWrite bytes>
}
```

Where `PodpingWrite`:

```
PodpingWrite {
    medium: <PodpingMedium enum>
    reason: <PodpingReason enum>
    iri:    "https://example.com/feed.xml"
}
```

One message per IRI (the gossip-writer batches internally every 3 s).

## Dependencies Added

```toml
# pyproject.toml [project.optional-dependencies]
gossip = ["pycapnp>=2.0.0", "pyzmq>=26.0"]
```

Both are also added to the main `dependencies` list as optional imports —
the code gracefully degrades if they are not installed.

## Testing

- `test_models.py` — verify `capnp_ordinal` values.
- `test_gossip_client.py` — unit-test Cap'n Proto message building (no
  live ZMQ needed; mock the socket).
- `test_hive_writer.py` — unit-test the extracted send function.

## Rollout

1. Merge this branch.
2. Deploy with `GOSSIP_WRITER_ENABLED=false` (default) — zero behaviour
   change.
3. Start gossip-writer alongside, set `GOSSIP_WRITER_ENABLED=true` +
   `GOSSIP_WRITER_ZMQ=tcp://127.0.0.1:9998`.
