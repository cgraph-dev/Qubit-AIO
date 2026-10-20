"""The compression tip is readable on a writer's own connection, as its callers require."""

from qubit_state import SessionDB
from qubit_state_sessions_model_config import update_conversation_model_config_value


def _chain(db):
    db.create_session('root', source='cli')
    db.end_session('root', 'compression')
    db.create_session('mid', source='cli', parent_session_id='root')
    db.end_session('mid', 'compression')
    db.create_session('tip', source='cli', parent_session_id='mid')


def test_compression_tip_reads_on_the_callers_connection(tmp_path):
    with SessionDB(tmp_path / 'state.db') as db:
        _chain(db)
        with db._read_ctx() as conn:
            assert db._compression_tip_on(conn, 'root') == 'tip'
            assert db._compression_tip_on(conn, 'tip') == 'tip'


def test_conversation_model_config_writes_at_the_tip_on_its_write_connection(tmp_path):
    with SessionDB(tmp_path / 'state.db') as db:
        _chain(db)
        assert update_conversation_model_config_value(db, 'root', 'voice_chat', lambda _: 'on') == 'tip'
        with db._read_ctx() as conn:
            row = conn.execute('SELECT model_config FROM sessions WHERE id = ?', ('tip',)).fetchone()
            root = conn.execute('SELECT model_config FROM sessions WHERE id = ?', ('root',)).fetchone()
        assert row[0] is not None and '"voice_chat": "on"' in row[0]
        assert root[0] is None or 'voice_chat' not in root[0]
