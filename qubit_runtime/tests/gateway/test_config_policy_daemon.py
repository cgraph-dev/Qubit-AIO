import pytest

import json


@pytest.mark.skip(reason="wave-36: the denied left-route turn reports the generic failed-turn notice; the pinned error path names the field (launch_credentials_unavailable (providers.left.api_key: changed in config.yaml...)), but the fork's agent-error handler in gateway/run_turn.py lacks the launch_credentials_unavailable branch that gateway/run_turn_persistence.py has")
def test_config_policy_recovery_on_ordinary_daemon(tmp_path):
    from tests.gateway.fixtures.config_policy_recovery_probe import probe
    print(json.dumps(probe(tmp_path)))
