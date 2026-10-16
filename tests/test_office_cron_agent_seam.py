"""Real cron prompt/scope/construction, controlled model seam; no provider calls."""
from types import SimpleNamespace


def test_agent_cron_context_and_permissions(tmp_path, monkeypatch):
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
            return {'final_response': 'Controlled context observed.', 'messages': []}
        def close(self):
            captured['closed'] = True

    monkeypatch.setattr(run_agent, 'AIAgent', ControlledAgent)
    token = set_qubit_home_override(str(home))
    try:
        job = {'id': 'office-agent-context', 'name': 'isolated seam',
               'prompt': 'Summarize the supplied context.', 'deliver': 'local',
               'enabled_toolsets': [], 'workdir': str(workdir)}
        result = sched.run_job(job, extra_prompt='unique-office-run-context')
        assert result[0], result
        assert 'unique-office-run-context' in captured['prompt']
        assert captured['platform'] == 'cron'
        assert captured['cron_context'] == '1'
        assert captured['enabled_toolsets'] == []
        assert set(captured['disabled_toolsets']) >= {'cronjob', 'browser', 'messaging', 'clarify'}
        assert captured['skip_context_files'] is False
        assert captured['closed']
        assert 'unique-office-run-context' not in job['prompt']
        assert get_session_env('HERMES_SESSION_PLATFORM') != 'cron'
    finally:
        reset_qubit_home_override(token)
