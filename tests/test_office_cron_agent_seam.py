"""Real cron prompt/scope/construction, controlled model seam; no provider calls."""
from types import SimpleNamespace

import pytest


@pytest.mark.parametrize('agent_fails', [False, True])
def test_agent_cron_context_and_permissions(tmp_path, monkeypatch, agent_fails):
    from voicebridge import qubit_runtime
    qubit_runtime.ensure_path()
    from qubit_constants import set_qubit_home_override, reset_qubit_home_override
    from cron import scheduler as sched
    from gateway.session_context import get_session_env
    import run_agent

    home = tmp_path / 'isolated-office-cron'
    home.mkdir()
    (home / 'config.yaml').write_text('{}\n')
    workdir = home / 'project'
    workdir.mkdir()
    captured = {}
    cfg = {'agent': {'disabled_toolsets': ['browser']}}
    setup = sched._CronAgentSetup(model='controlled-test-model', runtime={})
    monkeypatch.setattr(sched, '_load_cron_job_config',
                        lambda *a: SimpleNamespace(cfg=cfg, model=setup.model))
    monkeypatch.setattr(sched, '_resolve_cron_agent_setup', lambda *a: setup)

    class ControlledAgent:
        def __init__(self, **kwargs):
            captured.update(kwargs)
        def run_conversation(self, prompt, **kwargs):
            captured['prompt'] = prompt
            captured['cron_context'] = get_session_env('HERMES_CRON_SESSION')
            from qubit_constants import get_qubit_home
            captured['home'] = get_qubit_home()
            if agent_fails:
                raise RuntimeError('isolated-office-provider-failure')
            return {'final_response': 'Controlled context observed.', 'messages': []}
        def close(self):
            captured['closed'] = True

    monkeypatch.setattr(run_agent, 'AIAgent', ControlledAgent)
    # `run_job` delegates agent jobs to run_canonical_job, which reaches the gateway
    # owner over RPC (connect_gateway -> ensure_gateway_runtime, which SPAWNS a real
    # `qubit_cli.main gateway run` daemon that outlives the test and is never reaped,
    # so its Popen finalises inside whatever test the GC runs next). This test owns
    # cron prompt/scope/agent construction, not gateway admission: run the job in
    # process by giving it a bound execution, and stamp the authority seam so no
    # daemon is ever started.
    import cron.scheduler_authority as authority
    from gateway.session_cron import _execution, bind_execution

    def _in_process(job, **kwargs):
        return sched.run_job(
            job, extra_prompt=kwargs.get('extra_prompt'),
            execution_id='test-execution')

    monkeypatch.setattr(authority, 'run_canonical_job', _in_process)
    monkeypatch.setattr(sched, 'run_canonical_job', _in_process, raising=False)
    exec_token = bind_execution(
        'test-authority', 'cron_test_session', 'office-agent-context',
        'test-execution')
    token = set_qubit_home_override(str(home))
    try:
        job = {'id': 'office-agent-context', 'name': 'isolated seam',
               'prompt': 'Summarize the supplied context.', 'deliver': 'local',
               'enabled_toolsets': [], 'workdir': str(workdir)}
        result = sched.run_job(job, extra_prompt='unique-office-run-context')
        assert result[0] is (not agent_fails), result
        if agent_fails:
            assert 'isolated-office-provider-failure' in (result[3] or '')
        assert captured['model'] == 'controlled-test-model'
        assert 'unique-office-run-context' in captured['prompt']
        assert captured['platform'] == 'cron'
        assert captured['cron_context'] == '1'
        assert captured['home'] == home
        assert captured['enabled_toolsets'] == []
        assert set(captured['disabled_toolsets']) >= {'cronjob', 'browser', 'messaging', 'clarify'}
        assert captured['skip_context_files'] is False
        assert captured['closed']
        assert 'unique-office-run-context' not in job['prompt']
        assert get_session_env('HERMES_SESSION_PLATFORM') != 'cron'
    finally:
        _execution.reset(exec_token)
        reset_qubit_home_override(token)
