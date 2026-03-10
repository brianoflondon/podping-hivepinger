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
