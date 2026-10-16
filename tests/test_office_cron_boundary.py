"""Office-accessible canonical cron tool: isolated stores, real script execution."""
import json

import pytest


@pytest.mark.parametrize('exit_code', [0, 7])
def test_canonical_cron_lifecycle_is_profile_scoped(tmp_path, monkeypatch, exit_code):
    from voicebridge import qubit_runtime
    qubit_runtime.ensure_path()
    from qubit_constants import set_qubit_home_override, reset_qubit_home_override
    from tools.cronjob_tools import cronjob
    from cron.jobs import get_job
    import tools.cronjob_tools as cron_tools
    # No parent-session background delivery in this isolated synchronous probe.
    monkeypatch.setattr(cron_tools, '_try_dispatch_background_run', lambda *a, **k: None)

    home = tmp_path / 'office-profile'
    (home / 'scripts').mkdir(parents=True)
    script = home / 'scripts' / 'probe.py'
    script.write_text(f'print("office-cron-proof")\nraise SystemExit({exit_code})\n')
    token = set_qubit_home_override(str(home))
    try:
        created = json.loads(cronjob(action='create', name='office-proof',
                                    schedule='every 1h', script=script.name,
                                    no_agent=True, deliver='local', repeat=3,
                                    enabled_toolsets=[]))
        assert created['success'], created
        jid = created['job_id']
        assert get_job(jid)['enabled_toolsets'] == []
        assert json.loads(cronjob(action='list'))['count'] == 1
        updated = json.loads(cronjob(action='update', job_id=jid, name='updated-proof'))
        assert updated['success'], updated
        assert get_job(jid)['name'] == 'updated-proof'
        other = set_qubit_home_override(str(tmp_path / 'other-profile'))
        try:
            assert json.loads(cronjob(action='list'))['count'] == 0
            assert not json.loads(cronjob(action='remove', job_id=jid))['success']
        finally:
            reset_qubit_home_override(other)
        result = json.loads(cronjob(action='run', job_id=jid))
        assert result['job']['execution_success'] is (exit_code == 0), result
        persisted = get_job(jid)
        assert persisted['last_run_at']
        assert persisted['last_status'] == ('ok' if exit_code == 0 else 'error')
        if exit_code:
            assert persisted['last_error']
        assert json.loads(cronjob(action='remove', job_id=jid))['success']
        assert get_job(jid) is None
        assert json.loads(cronjob(action='list'))['count'] == 0
    finally:
        reset_qubit_home_override(token)
