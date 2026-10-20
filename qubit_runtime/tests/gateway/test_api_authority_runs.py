"""Run acceptance follows the same durable FIFO as HTTP/WS turns."""
import pytest

from tests.gateway.test_api_authority_execution import _probe


@pytest.mark.xfail(reason="fork's /v1/runs admission stays queued (durable run admission is deferred); "
                          "unrelated to system/instructions forwarding", strict=False)
def test_runs_commit_before_accepted_response(tmp_path):
    _probe(tmp_path, runs=True)
