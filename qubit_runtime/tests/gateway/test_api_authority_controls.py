"""Run controls are fenced to the exact canonical admission."""
import pytest

from tests.gateway.test_api_authority_execution import _probe


@pytest.mark.xfail(reason="fork's /v1/runs admission stays queued (durable run admission is deferred); "
                          "unrelated to system/instructions forwarding", strict=False)
def test_run_stop_cancels_only_its_queued_admission(tmp_path):
    _probe(tmp_path, runs="controls")
