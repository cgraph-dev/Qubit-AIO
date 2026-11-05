"""One lineage read serves a cold resume's model history, display history and ancestor prefix."""

from qubit_state import SessionDB


def _seed_lineage(db: SessionDB) -> None:
    db.create_session("root", source="cli", model="m")
    db.append_message("root", role="user", content="ancestor question", timestamp=100.0)
    db.append_message("root", role="assistant", content="ancestor answer", timestamp=101.0)
    db.end_session("root", "compression")
    db.create_session("tip", source="cli", model="m", parent_session_id="root")
    db.append_message("tip", role="user", content="current ask", timestamp=102.0)
    db.append_message("tip", role="assistant", content="current answer", timestamp=103.0)


def test_bundle_matches_the_two_standalone_projections(tmp_path):
    db = SessionDB(db_path=tmp_path / "state.db")
    try:
        _seed_lineage(db)
        model, display, prefix = db.get_resume_bundle("tip")
        pair_model, pair_display = db.get_resume_conversations("tip")
        pair_prefix = db.get_ancestor_display_prefix("tip")
        assert model == pair_model
        assert display == pair_display
        assert prefix == pair_prefix
        assert [m["content"] for m in prefix] == ["ancestor question", "ancestor answer"]
    finally:
        db.close()


def test_bundle_reads_the_lineage_once_not_twice(tmp_path, monkeypatch):
    db = SessionDB(db_path=tmp_path / "state.db")
    try:
        _seed_lineage(db)
        original = db._fetch_conversation_rows
        reads = []

        def counted(*args, **kwargs):
            reads.append(1)
            return original(*args, **kwargs)

        monkeypatch.setattr(db, "_fetch_conversation_rows", counted)
        db.get_resume_conversations("tip")
        db.get_ancestor_display_prefix("tip")
        assert len(reads) == 2, "the standalone pair still takes two lineage reads"

        reads.clear()
        db.get_resume_bundle("tip")
        assert len(reads) == 1, "the bundle must materialize the lineage once"
    finally:
        db.close()


def test_bundle_without_a_compression_lineage_has_no_prefix(tmp_path):
    db = SessionDB(db_path=tmp_path / "state.db")
    try:
        db.create_session("plain", source="cli", model="m")
        db.append_message("plain", role="user", content="hello")
        db.append_message("plain", role="assistant", content="hi")
        model, display, prefix = db.get_resume_bundle("plain")
        assert [m["content"] for m in model] == ["hello", "hi"]
        assert [m["content"] for m in display] == ["hello", "hi"]
        assert prefix == []
    finally:
        db.close()
