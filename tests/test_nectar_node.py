import pytest
from nectarapi.node import CallRetriesReached, Nodes


def test_nodes_sleep_and_check_retries_logs_first_and_last_retry(caplog):
    caplog.set_level("WARNING", logger="nectarapi.node")

    nodes = Nodes(["https://example.com"], num_retries=3, num_retries_call=3)
    node = nodes.node

    # first retry should log the first-retry message
    node.error_cnt_call = 1
    nodes.sleep_and_check_retries(
        errorMsg="temporary failure",
        sleep=False,
        call_retry=True,
        showMsg=True,
    )
    assert "First or last retry Node:" in caplog.text
    assert "Retry RPC Call on node:" in caplog.text

    caplog.clear()

    # intermediate retry should not log the first/last retry message
    node.error_cnt_call = 2
    nodes.sleep_and_check_retries(
        errorMsg="temporary failure",
        sleep=False,
        call_retry=True,
        showMsg=True,
    )
    assert "First or last retry Node:" not in caplog.text

    caplog.clear()

    # last retry should log the last-retry message
    node.error_cnt_call = 3
    nodes.sleep_and_check_retries(
        errorMsg="temporary failure",
        sleep=False,
        call_retry=True,
        showMsg=True,
    )
    assert "First or last retry Node:" in caplog.text
    assert "Retry RPC Call on node:" in caplog.text

    caplog.clear()

    # once the retry limit is exceeded, the exception should be raised
    node.error_cnt_call = 4
    with pytest.raises(CallRetriesReached):
        nodes.sleep_and_check_retries(
            errorMsg="temporary failure",
            sleep=False,
            call_retry=True,
            showMsg=True,
        )
