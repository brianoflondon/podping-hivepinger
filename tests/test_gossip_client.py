"""Tests for Cap'n Proto message building and the GossipClient."""

from hivepinger.gossip_client import GossipClient, build_podping_write_message
from models.podping import Medium, Reason


class TestBuildPodpingWriteMessage:
    def test_round_trip(self):
        """Build a PlexoMessage and verify the inner PodpingWrite decodes."""
        from pathlib import Path

        import capnp

        schema_dir = str(Path(__file__).resolve().parent.parent / "src" / "schemas")
        capnp.remove_import_hook()
        plexo_mod = capnp.load(str(Path(schema_dir) / "plexo_message.capnp"))
        pw_mod = capnp.load(str(Path(schema_dir) / "podping_write.capnp"))

        msg_bytes = build_podping_write_message(
            iri="https://example.com/feed.xml",
            medium=Medium.PODCAST,
            reason=Reason.LIVE,
        )
        assert isinstance(msg_bytes, bytes)
        assert len(msg_bytes) > 0

        with plexo_mod.PlexoMessage.from_bytes(msg_bytes) as plexo:
            assert plexo.typeName == "org.podcastindex.podping.hivewriter.PodpingWrite.capnp"
            payload = bytes(plexo.payload)

        with pw_mod.PodpingWrite.from_bytes(payload) as pw:
            assert pw.iri == "https://example.com/feed.xml"
            assert pw.medium == "podcast"
            assert pw.reason == "live"

    def test_new_iri_reason_maps_to_update(self):
        """NEW_IRI is not in the capnp schema, should map to 'update'."""
        from pathlib import Path

        import capnp

        schema_dir = str(Path(__file__).resolve().parent.parent / "src" / "schemas")
        capnp.remove_import_hook()
        plexo_mod = capnp.load(str(Path(schema_dir) / "plexo_message.capnp"))
        pw_mod = capnp.load(str(Path(schema_dir) / "podping_write.capnp"))

        msg_bytes = build_podping_write_message(
            iri="https://new.example.com/feed",
            medium=Medium.MUSIC,
            reason=Reason.NEW_IRI,
        )
        with plexo_mod.PlexoMessage.from_bytes(msg_bytes) as plexo:
            payload = bytes(plexo.payload)
        with pw_mod.PodpingWrite.from_bytes(payload) as pw:
            assert pw.reason == "update"
            assert pw.medium == "music"

    def test_all_mediums_serialize(self):
        """Every Medium member should produce a valid message."""
        for m in Medium:
            msg = build_podping_write_message(
                iri="https://test.com", medium=m, reason=Reason.UPDATE
            )
            assert isinstance(msg, bytes)
            assert len(msg) > 0


class TestGossipClient:
    def test_not_connected_by_default(self):
        client = GossipClient()
        assert not client.is_connected

    def test_send_returns_zero_when_not_connected(self):
        client = GossipClient()
        result = client.send_podping_writes(["https://example.com"], Medium.PODCAST, Reason.UPDATE)
        assert result == 0

    def test_drain_returns_zero_when_not_connected(self):
        client = GossipClient()
        assert client.drain_replies() == 0

    def test_close_is_safe_when_not_connected(self):
        client = GossipClient()
        client.close()  # should not raise
        assert not client.is_connected
