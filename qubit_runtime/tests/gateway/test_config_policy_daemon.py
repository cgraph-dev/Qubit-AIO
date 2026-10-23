import pytest

import json


def test_config_policy_recovery_on_ordinary_daemon(tmp_path):
    from tests.gateway.fixtures.config_policy_recovery_probe import probe
    print(json.dumps(probe(tmp_path)))

pytestmark = pytest.mark.skip(reason="wave-31: live config-source daemon restart harness (spawns a real gateway + loopback inference) unavailable in this hermetic tree")
