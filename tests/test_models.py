from models.podping import CURRENT_PODPING_VERSION, HiveOperationId, Medium, Podping, Reason


def test_hive_operation_id_string_and_eq():
    op1 = HiveOperationId(prefix="podping", medium=Medium.PODCAST, reason=Reason.UPDATE)
    op2 = HiveOperationId(prefix="podping", medium=Medium.PODCAST, reason=Reason.UPDATE)
    assert str(op1) == "podping_podcast_update"
    assert op1 == op2
    assert hash(op1) == hash(op2)


def test_hive_operation_id_custom_prefix_and_startup():
    # different prefix should be reflected and startup toggles output
    op = HiveOperationId(prefix="foo", medium=Medium.MUSIC, reason=Reason.LIVE)
    assert str(op) == "foo_music_live"

    startup = HiveOperationId(prefix="foo", startup=True)
    assert str(startup) == "foo_startup"
    assert startup != op


def test_hive_operation_id_underscore_replacement():
    # the __str__ method replaces underscores with hyphens
    class DummyReason:
        def __str__(self):
            return "some_reason_with_underscores"

    op = HiveOperationId(prefix="x", medium=Medium.BLOG, reason=DummyReason())
    assert str(op) == "x_blog_some-reason-with-underscores"


def test_podping_model_basic():
    obj = Podping(
        version=CURRENT_PODPING_VERSION,
        medium=Medium.MUSIC,
        reason=Reason.LIVE,
        iris=["https://a", "https://b"],
        timestampNs=123,
        sessionId=456,
    )
    data = obj.model_dump()
    assert data["medium"] == "music"
    assert data["reason"] == "live"
    assert data["iris"] == ["https://a", "https://b"]


def test_medium_capnp_ordinals():
    """Verify capnp_ordinal matches the canonical podping-schemas ordering."""
    assert Medium.MIXED.capnp_ordinal == 0
    assert Medium.PODCAST.capnp_ordinal == 1
    assert Medium.PODCAST_LIVE.capnp_ordinal == 2
    assert Medium.MUSIC.capnp_ordinal == 3
    assert Medium.VIDEO.capnp_ordinal == 5
    assert Medium.FILM.capnp_ordinal == 7
    assert Medium.AUDIOBOOK.capnp_ordinal == 9
    assert Medium.NEWSLETTER.capnp_ordinal == 11
    assert Medium.BLOG.capnp_ordinal == 13
    assert Medium.PUBLISHER.capnp_ordinal == 15
    assert Medium.COURSE.capnp_ordinal == 17
    assert Medium.COURSE_LIVE.capnp_ordinal == 18


def test_reason_capnp_ordinals():
    """Verify capnp_ordinal matches the canonical podping-schemas ordering."""
    assert Reason.UPDATE.capnp_ordinal == 0
    assert Reason.LIVE.capnp_ordinal == 1
    assert Reason.LIVE_END.capnp_ordinal == 2
    # NEW_IRI is not in the capnp schema; maps to update (0)
    assert Reason.NEW_IRI.capnp_ordinal == 0


def test_all_mediums_have_capnp_ordinal():
    """Every Medium member should have a valid capnp_ordinal."""
    for m in Medium:
        assert isinstance(m.capnp_ordinal, int)
        assert 0 <= m.capnp_ordinal <= 18


def test_all_reasons_have_capnp_ordinal():
    """Every Reason member should have a valid capnp_ordinal."""
    for r in Reason:
        assert isinstance(r.capnp_ordinal, int)
        assert 0 <= r.capnp_ordinal <= 2
