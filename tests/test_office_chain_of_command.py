"""C30 + D34/D35 — the chain of command is real, or the org cannot rate itself.

Contract proofs for `docs/game-office/95-CONTRACT.md` C30 and the structural
rules in `docs/game-office/11-ORG-CHARTER.md`:

* every seat except the CEO names a supervisor;
* the CEO reports to the owner and therefore to nobody;
* the org has exactly one CEO;
* a seat cannot supervise itself, and no cycle may exist;
* span limit 7 — a manager carrying an eighth report is refused;
* the chain from any seat terminates at the CEO, inside the hop budget;
* a business has one owning portfolio director, and a declared kill criteria.

Hermetic: tmp XDG state only; no network, no live services.
"""
from __future__ import annotations

import pytest


def _cfg(tmp_path, monkeypatch):
    from voicebridge import config as C

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "c"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "s"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "d"))
    p = tmp_path / "voice.yaml"
    p.write_text("logging:\n  jsonl: false\n")
    return C.Config.load(p)


def _mgr(cfg, seat_type="manager", **kw):
    """Hire with the given seat type, filling the CEO supervisor by default."""
    from voicebridge.office import org as O
    ceo = O.tree(cfg)["employees"]
    ceo_id = next(e["id"] for e in ceo if e["seat_type"] == "ceo")
    kw.setdefault("supervisor_id", ceo_id)
    kw.setdefault("confirmed", True)
    return O.hire(cfg, kw.pop("slug"), seat_type=seat_type, **kw)


# ------------------------------------------------------- the supervisor edge
def test_a_seat_without_a_supervisor_is_refused(tmp_path, monkeypatch):
    from voicebridge.office import org as O, store
    from voicebridge.office.errors import OfficeError

    cfg = _cfg(tmp_path, monkeypatch)
    store.init(cfg)
    O.hire(cfg, "ceo", seat_type="ceo", confirmed=True)

    with pytest.raises(OfficeError) as err:
        O.hire(cfg, "orphan", seat_type="worker", confirmed=True)
    assert err.value.code == "OFFICE_CHAIN"
    assert "supervisor" in err.value.detail.lower()


def test_the_ceo_may_not_have_an_agent_supervisor(tmp_path, monkeypatch):
    from voicebridge.office import org as O, store
    from voicebridge.office.errors import OfficeError

    cfg = _cfg(tmp_path, monkeypatch)
    store.init(cfg)
    boss = O.hire(cfg, "ceo", seat_type="ceo", confirmed=True)
    with pytest.raises(OfficeError) as err:
        O.hire(cfg, "ceo2", seat_type="ceo", supervisor_id=boss["id"],
               confirmed=True)
    assert err.value.code == "OFFICE_BAD_INPUT"


def test_the_org_has_exactly_one_ceo(tmp_path, monkeypatch):
    from voicebridge.office import org as O, store
    from voicebridge.office.errors import OfficeError

    cfg = _cfg(tmp_path, monkeypatch)
    store.init(cfg)
    O.hire(cfg, "ceo", seat_type="ceo", confirmed=True)
    with pytest.raises(OfficeError) as err:
        O.hire(cfg, "ceo-b", seat_type="ceo", confirmed=True)
    assert err.value.code == "OFFICE_EXISTS"
    assert "one" in err.value.detail.lower()


# ------------------------------------------------------------- no cycles
def test_a_seat_cannot_supervise_itself(tmp_path, monkeypatch):
    from voicebridge.office import org as O, store
    from voicebridge.office.errors import OfficeError

    cfg = _cfg(tmp_path, monkeypatch)
    store.init(cfg)
    O.hire(cfg, "ceo", seat_type="ceo", confirmed=True)
    mgr = _mgr(cfg, slug="mgr")

    with pytest.raises(OfficeError) as err:
        O.set_supervisor(cfg, mgr["id"], mgr["id"], confirmed=True)
    assert err.value.code == "OFFICE_CHAIN"


def test_a_reporting_cycle_is_refused(tmp_path, monkeypatch):
    """Re-pointing a supervisor into its own subtree is a cycle."""
    from voicebridge.office import org as O, store
    from voicebridge.office.errors import OfficeError

    cfg = _cfg(tmp_path, monkeypatch)
    store.init(cfg)
    O.hire(cfg, "ceo", seat_type="ceo", confirmed=True)
    a = _mgr(cfg, slug="mgr-a")
    b = _mgr(cfg, slug="mgr-b", supervisor_id=a["id"])

    with pytest.raises(OfficeError) as err:
        O.set_supervisor(cfg, a["id"], b["id"], confirmed=True)
    assert err.value.code == "OFFICE_CHAIN"
    assert "cycle" in err.value.detail.lower()


def test_a_seat_cannot_be_left_without_a_supervisor(tmp_path, monkeypatch):
    from voicebridge.office import org as O, store
    from voicebridge.office.errors import OfficeError

    cfg = _cfg(tmp_path, monkeypatch)
    store.init(cfg)
    O.hire(cfg, "ceo", seat_type="ceo", confirmed=True)
    mgr = _mgr(cfg, slug="mgr")

    with pytest.raises(OfficeError) as err:
        O.set_supervisor(cfg, mgr["id"], None, confirmed=True)
    assert err.value.code == "OFFICE_CHAIN"


# --------------------------------------------------------------- span limit
def test_a_manager_may_carry_seven_reports_and_not_eight(tmp_path,
                                                         monkeypatch):
    """D34's span limit, and the refusal names the reason."""
    from voicebridge.office import org as O, store
    from voicebridge.office.errors import OfficeError

    cfg = _cfg(tmp_path, monkeypatch)
    store.init(cfg)
    O.hire(cfg, "ceo", seat_type="ceo", confirmed=True)
    mgr = _mgr(cfg, slug="mgr")

    for i in range(store.MAX_SPAN):
        O.hire(cfg, f"w{i}", seat_type="worker", supervisor_id=mgr["id"],
               confirmed=True)

    with pytest.raises(OfficeError) as err:
        O.hire(cfg, "w-extra", seat_type="worker", supervisor_id=mgr["id"],
               confirmed=True)
    assert err.value.code == "OFFICE_CAPACITY"
    assert str(store.MAX_SPAN) in err.value.detail


# ------------------------------------------------------- the chain terminates
def test_every_chain_terminates_at_the_ceo(tmp_path, monkeypatch):
    from voicebridge.office import org as O, store

    cfg = _cfg(tmp_path, monkeypatch)
    store.init(cfg)
    O.hire(cfg, "ceo", seat_type="ceo", confirmed=True)
    d = _mgr(cfg, seat_type="director_portfolio", slug="dir", portfolio="product")
    m = _mgr(cfg, slug="mgr", supervisor_id=d["id"])
    w = O.hire(cfg, "w0", seat_type="worker", supervisor_id=m["id"],
               confirmed=True)

    for seat_id in (w["id"], m["id"], d["id"]):
        chain = O.chain(cfg, seat_id)
        assert chain["terminates_at_ceo"] is True, chain
    # ground-up ordering, and the CEO is the last hop
    assert O.chain(cfg, w["id"])["depth"] == 4
    assert O.chain(cfg, w["id"])["chain"][-1]["seat_type"] == "ceo"


def test_tree_reports_roots_and_never_over_span(tmp_path, monkeypatch):
    from voicebridge.office import org as O, store

    cfg = _cfg(tmp_path, monkeypatch)
    store.init(cfg)
    O.hire(cfg, "ceo", seat_type="ceo", confirmed=True)
    d = _mgr(cfg, seat_type="director_portfolio", slug="dir", portfolio="product")
    _mgr(cfg, slug="mgr", supervisor_id=d["id"])

    t = O.tree(cfg)
    assert t["seats"] == 3
    assert t["roots"] == ["ceo"]
    assert t["over_span"] == []
    assert t["counts"] == {"ceo": 1, "director_portfolio": 1, "manager": 1}


# --------------------------------------------------- a business needs an owner
def test_a_department_needs_kill_criteria(tmp_path, monkeypatch):
    from voicebridge.office import org as O, store
    from voicebridge.office.errors import OfficeError

    cfg = _cfg(tmp_path, monkeypatch)
    store.init(cfg)
    with pytest.raises(OfficeError) as err:
        O.department_create(cfg, "pod", division_kind="product", confirmed=True)
    assert err.value.code == "OFFICE_BAD_INPUT"
    assert "kill_criteria" in err.value.detail


def test_only_a_portfolio_director_may_own_a_business(tmp_path, monkeypatch):
    """D35: a business has exactly one owning portfolio director."""
    from voicebridge.office import org as O, store
    from voicebridge.office.errors import OfficeError

    cfg = _cfg(tmp_path, monkeypatch)
    store.init(cfg)
    O.hire(cfg, "ceo", seat_type="ceo", confirmed=True)
    mgr = _mgr(cfg, slug="mgr")

    with pytest.raises(OfficeError) as err:
        O.department_create(cfg, "pod", division_kind="product",
                            director_id=mgr["id"], kill_criteria="no sale",
                            confirmed=True)
    assert err.value.code == "OFFICE_BAD_STATE"
    assert "director_portfolio" in err.value.detail


def test_mutations_require_confirm(tmp_path, monkeypatch):
    from voicebridge.office import org as O, store
    from voicebridge.office.errors import OfficeError

    cfg = _cfg(tmp_path, monkeypatch)
    store.init(cfg)
    with pytest.raises(OfficeError) as err:
        O.hire(cfg, "ceo", seat_type="ceo")
    assert err.value.code == "OFFICE_CONFIRM_REQUIRED"


def test_unknown_seat_type_and_floor_number_are_refused(tmp_path, monkeypatch):
    from voicebridge.office import org as O, store
    from voicebridge.office.errors import OfficeError

    cfg = _cfg(tmp_path, monkeypatch)
    store.init(cfg)
    with pytest.raises(OfficeError) as err:
        O.hire(cfg, "x", seat_type="wizard", confirmed=True)
    assert err.value.code == "OFFICE_BAD_INPUT"

    with pytest.raises(OfficeError) as ferr:
        O.floor_create(cfg, 99, confirmed=True)
    assert ferr.value.code == "OFFICE_BAD_INPUT"

    with pytest.raises(OfficeError) as rerr:
        O.floor_create(cfg, 3, role="penthouse", confirmed=True)
    assert rerr.value.code == "OFFICE_BAD_INPUT"
