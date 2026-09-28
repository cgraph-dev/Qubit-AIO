"""Contract: the dashboard toolset catalog must serve toolsets, not broker actions.

Regression for `h_toolsets` sourcing its names from the broker action inventory
(`local.*` capabilities) and rendering them as placeholder "Qubit broker
capability" rows, which hid the real 29-toolset catalog from the dashboard.
"""

from pathlib import Path

from voicebridge import qubit_compat as QC


class _Cfg:
    def __init__(self, state_dir: Path) -> None:
        self._state = state_dir

    def state_dir(self) -> Path:
        return self._state


def test_h_toolsets_serves_toolset_catalog_not_broker_actions(tmp_path):
    code, rows = QC.h_toolsets(_Cfg(tmp_path), {}, {}, {})
    assert code == 200
    names = {row["name"] for row in rows}
    assert {"web", "terminal", "kanban", "connections"} <= names
    assert not any(name.startswith("local.") for name in names)


def test_h_toolsets_rows_carry_real_metadata_and_member_tools(tmp_path):
    _code, rows = QC.h_toolsets(_Cfg(tmp_path), {}, {}, {})
    by_name = {row["name"]: row for row in rows}
    kanban = by_name["kanban"]
    assert kanban["label"] == "📌 Kanban"
    assert "kanban_create" in kanban["tools"]
    connections = by_name["connections"]
    assert connections["label"] == "🔌 Connections"
    assert connections["tools"] == ["manage_connections"]


def test_catalog_resolves_qubit_home_and_never_external_hermes(tmp_path, monkeypatch):
    """The catalog read path must resolve a Qubit-owned home, never ~/.hermes."""
    import os

    monkeypatch.delenv("QUBIT_HOME", raising=False)
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))

    from voicebridge import skill_catalog

    skill_catalog.catalog_entries({})
    home = os.environ.get("QUBIT_HOME", "")
    assert home, "the catalog must resolve an explicit QUBIT_HOME"
    assert not home.rstrip("/").endswith("/.hermes")
    assert str(tmp_path) in home



def test_catalog_home_never_falls_back_when_home_creation_fails(tmp_path, monkeypatch):
    """Fail closed: if the Qubit home cannot be created, the catalog must still
    publish a Qubit-owned QUBIT_HOME -- never the external ~/.hermes default."""
    import os
    from pathlib import Path

    monkeypatch.delenv("QUBIT_HOME", raising=False)
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))

    real_mkdir = Path.mkdir

    def _boom(self, *args, **kwargs):
        if "qubit-voicebridge" in str(self):
            raise OSError("read-only state dir")
        return real_mkdir(self, *args, **kwargs)

    monkeypatch.setattr(Path, "mkdir", _boom)

    from voicebridge import skill_catalog

    skill_catalog.catalog_entries({})
    home = os.environ.get("QUBIT_HOME", "")
    assert home, "the catalog must publish a QUBIT_HOME even when mkdir fails"
    assert not home.rstrip("/").endswith("/.hermes")
    assert str(tmp_path) in home
