from models.podping import CURRENT_PODPING_VERSION, HiveOperationId, Medium, Podping, Reason


def test_hive_operation_id_string_and_eq():
    op1 = HiveOperationId(podping="podping", medium=Medium.PODCAST, reason=Reason.UPDATE)
    op2 = HiveOperationId(podping="podping", medium=Medium.PODCAST, reason=Reason.UPDATE)
    assert str(op1) == "podping_podcast_update"
    assert op1 == op2
    assert hash(op1) == hash(op2)


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
