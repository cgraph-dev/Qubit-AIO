"""Packet W4 CLI-port wave 9 — the ``vault`` family, ported from the read-only
reference 0.21.4 onto the VENDORED 0.21.1 tree (no stubs, no invented crypto):

* parser  — reference ``hermes_cli/subcommands/vault.py:6`` →
  vendored ``qubit_cli/subcommands/vault.py`` (``add|list|rm|sources``);
* handler — reference ``hermes_cli/vault.py:185`` / ``:213`` →
  vendored ``qubit_cli/vault.py``;
* store   — reference ``agent/vault_store.py`` (442 L) →
  vendored ``agent/vault_store.py`` (Fernet-encrypted at rest, metadata-only
  listing);
* backends — reference ``agent/vault_backends/{base,local,onepassword,bitwarden,unlock}.py``
  → the same vendored modules.

Proof is behavioral and hermetic: a TEMP ``QUBIT_HOME`` with DUMMY values only.
The store is exercised directly and through the production
``python -m voicebridge agent vault`` passthrough in a scratch HOME. No test
ever reads ``~/.hermes``, the reference service, or a real credential.
"""
from __future__ import annotations

import argparse
import getpass
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
RUNTIME_ROOT = REPO_ROOT / "qubit_runtime"

DUMMY_LABEL = "Dummy GitHub account"
DUMMY_ORIGIN = "https://github.com"
DUMMY_IDENTIFIER = "dummy-9u@example.com"
DUMMY_PASSWORD = "DUMMY_SECRET_PASSWORD_9u"
DUMMY_OTP_SEED = "JBSWY3DPEHPK3PXP"


# ---------------------------------------------------------------------------
# Sandbox helpers (mirror tests/test_cli_ports_wave{6,7,8}.py)
# ---------------------------------------------------------------------------
def _sandbox_env(home: Path, qubit_home: Path) -> dict:
    for d in (home, qubit_home):
        d.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ)
    env["HOME"] = str(home)
    for var in ("XDG_CONFIG_HOME", "XDG_STATE_HOME", "XDG_DATA_HOME"):
        target = home / var.lower()
        target.mkdir(parents=True, exist_ok=True)
        env[var] = str(target)
    env["QUBIT_HOME"] = str(qubit_home)
    env["COLUMNS"] = "220"  # keep the rich metadata table on one line per row
    env.pop("PYTHONPATH", None)
    return env


def _run_agent(tmp_path, argv, *, home=None, qubit_home=None, timeout=180):
    """Run the production ``qubit agent <argv>`` passthrough in a scratch HOME."""
    home = home or (tmp_path / "home")
    qubit_home = qubit_home or (home / "qh")
    return subprocess.run(
        [sys.executable, "-m", "voicebridge", "agent", *argv],
        cwd=str(REPO_ROOT), env=_sandbox_env(home, qubit_home),
        capture_output=True, text=True, timeout=timeout)


@pytest.fixture(autouse=True)
def _vendored_import(tmp_path, monkeypatch):
    """Put the vendored fork on sys.path and redirect QUBIT_HOME to a temp dir."""
    monkeypatch.setenv("QUBIT_HOME", str(tmp_path / "qh-default"))
    if str(RUNTIME_ROOT) not in sys.path:
        sys.path.insert(0, str(RUNTIME_ROOT))
    yield


def _add_dummy_login(qh: Path):
    from agent.vault_store import VaultStore

    store = VaultStore(base_dir=Path(qh) / "vault")
    meta = store.add_item(
        kind="login",
        label=DUMMY_LABEL,
        origin=DUMMY_ORIGIN,
        secret={
            "identifier_type": "email",
            "identifier": DUMMY_IDENTIFIER,
            "password": DUMMY_PASSWORD,
            "otp_secret": DUMMY_OTP_SEED,
        },
    )
    return store, meta


# ---------------------------------------------------------------------------
# 1. Parser surface through the production passthrough (rc 0, real choices).
# ---------------------------------------------------------------------------
def test_vault_family_and_subcommands_help_are_rc0(tmp_path):
    r = _run_agent(tmp_path, ["vault", "--help"])
    assert r.returncode == 0, (r.returncode, r.stderr[-600:])
    for sub in ("add", "list", "rm", "sources"):
        assert sub in r.stdout, r.stdout[:600]
    for sub in ("add", "list", "rm", "sources"):
        r_sub = _run_agent(tmp_path, ["vault", sub, "--help"])
        assert r_sub.returncode == 0, (sub, r_sub.returncode, r_sub.stderr[-400:])
        combined = r_sub.stdout + r_sub.stderr
        assert "invalid choice" not in combined, (sub, combined[-400:])
        assert f"usage: qubit vault {sub}" in r_sub.stdout, (sub, r_sub.stdout[:300])


def test_vault_family_is_a_real_fork_parser_family():
    import argparse as _ap

    sys.path.insert(0, str(RUNTIME_ROOT))
    import qubit_cli.main as m  # noqa: E402

    _parser, subparsers = m._build_cli_parser()
    assert "vault" in subparsers.choices
    vault = subparsers.choices["vault"]
    subs = next(a for a in vault._actions if isinstance(a, _ap._SubParsersAction))
    assert set(subs.choices) == {"add", "list", "rm", "sources"}


def test_vault_forward_route_is_classified():
    """The voicebridge forward layer classifies the new family (no raw passthrough)."""
    sys.path.insert(0, str(REPO_ROOT))
    from voicebridge import cli_qubit_forward as HF  # noqa: E402

    assert HF.route(["vault"]) is not None
    assert HF.route(["vault"]).kind == "forward"
    assert HF.route(["vault", "list"]).kind == "forward"
    assert HF.route(["vault", "sources"]).kind == "forward"
    assert HF.route(["vault", "add"]).kind == "needs-confirm"
    assert HF.route(["vault", "rm"]).kind == "needs-confirm"
    # An unlisted subcommand is a typed refusal, never an unrestricted bypass.
    assert HF.route(["vault", "__nope__"]).kind == "refused"


# ---------------------------------------------------------------------------
# 2. Store: add → metadata-only list; secret never appears in metadata.
# ---------------------------------------------------------------------------
def test_add_then_list_is_metadata_only_and_encrypted_at_rest(tmp_path):
    from agent.vault_store import VaultStore

    store, meta = _add_dummy_login(tmp_path)
    assert meta.id.startswith("vault_")
    assert meta.kind == "login"
    assert meta.label == DUMMY_LABEL
    assert meta.identifier == DUMMY_IDENTIFIER
    assert meta.identifier_type == "email"
    assert meta.has_otp is True

    metas = VaultStore(base_dir=tmp_path / "vault").list_items()
    assert len(metas) == 1
    dumped = repr([m.to_dict() for m in metas])
    # Metadata carries the login identifier (visible by design) but NEVER the password.
    assert DUMMY_IDENTIFIER in dumped
    assert DUMMY_PASSWORD not in dumped
    assert "password" not in dumped
    assert DUMMY_OTP_SEED not in dumped

    # Encrypted at rest: the plaintext secret never appears in the vault file.
    blob = (tmp_path / "vault" / "vault.json.enc").read_bytes()
    assert DUMMY_PASSWORD.encode() not in blob
    assert DUMMY_OTP_SEED.encode() not in blob
    # ...but the store can resolve it server-side.
    assert store.resolve_secret(meta.id)["password"] == DUMMY_PASSWORD


# ---------------------------------------------------------------------------
# 3. CLI list → metadata only; CLI rm removes; secret never printed.
# ---------------------------------------------------------------------------
def test_cli_list_shows_metadata_only_then_rm(tmp_path):
    qh = tmp_path / "home" / "qh"
    _add_dummy_login(qh)

    r_list = _run_agent(tmp_path, ["vault", "list"], qubit_home=qh)
    assert r_list.returncode == 0, (r_list.returncode, r_list.stderr[-600:])
    out = r_list.stdout + r_list.stderr
    assert DUMMY_LABEL in out, out[:800]
    assert DUMMY_IDENTIFIER in out, out[:800]
    assert DUMMY_PASSWORD not in out, "secret value leaked into `vault list` output"
    assert DUMMY_OTP_SEED not in out, "otp seed leaked into `vault list` output"

    from agent.vault_store import VaultStore

    item_id = VaultStore(base_dir=qh / "vault").list_items()[0].id

    r_rm = _run_agent(tmp_path, ["vault", "rm", item_id], qubit_home=qh)
    assert r_rm.returncode == 0, (r_rm.returncode, r_rm.stderr[-400:])
    assert "Removed" in (r_rm.stdout + r_rm.stderr)
    assert not VaultStore(base_dir=qh / "vault").list_items()

    r_empty = _run_agent(tmp_path, ["vault", "list"], qubit_home=qh)
    assert r_empty.returncode == 0
    assert "Vault is empty" in (r_empty.stdout + r_empty.stderr)


# ---------------------------------------------------------------------------
# 4. Interactive `vault add` wizard (dummy prompts) never prints the secret.
# ---------------------------------------------------------------------------
def test_cli_add_wizard_stores_secret_without_printing_it(tmp_path, monkeypatch):
    qh = tmp_path / "qh"
    monkeypatch.setenv("QUBIT_HOME", str(qh))
    import qubit_cli.vault as V  # noqa: E402

    answers = iter([DUMMY_LABEL, DUMMY_ORIGIN, "email", DUMMY_IDENTIFIER])
    monkeypatch.setattr("builtins.input", lambda *a, **k: next(answers))
    secrets = iter([DUMMY_PASSWORD, ""])
    monkeypatch.setattr(getpass, "getpass", lambda *a, **k: next(secrets))

    printed: list = []
    monkeypatch.setattr(V, "_console", lambda: type(
        "C", (), {"print": staticmethod(lambda *a, **k: printed.append(" ".join(str(x) for x in a)))}
    )())

    V._cmd_add(argparse.Namespace(kind="login"))

    combined = "\n".join(printed)
    assert DUMMY_PASSWORD not in combined, "password leaked to the console"
    assert "Stored." in combined

    from agent.vault_store import VaultStore

    metas = VaultStore(base_dir=qh / "vault").list_items()
    assert len(metas) == 1
    assert metas[0].label == DUMMY_LABEL
    assert metas[0].identifier == DUMMY_IDENTIFIER


# ---------------------------------------------------------------------------
# 5. Wrong / unset key → typed VaultError (never a crash, never a secret).
# ---------------------------------------------------------------------------
def test_wrong_key_is_a_typed_error(tmp_path):
    from cryptography.fernet import Fernet

    qh = tmp_path / "qh"
    _add_dummy_login(qh)
    # Replace the key with a different valid Fernet key → decryption must fail typed.
    (qh / "vault" / "vault.key").write_bytes(Fernet.generate_key())

    from agent.vault_store import VaultError, VaultStore

    with pytest.raises(VaultError) as excinfo:
        VaultStore(base_dir=qh / "vault").list_items()
    assert "decrypted" in str(excinfo.value) or "key" in str(excinfo.value)
    assert DUMMY_PASSWORD not in str(excinfo.value)

    r = _run_agent(tmp_path, ["vault", "list"], qubit_home=qh)
    combined = r.stdout + r.stderr
    assert "Traceback" not in combined, combined[-600:]
    assert "Error:" in combined, combined[-600:]
    assert DUMMY_PASSWORD not in combined


def test_corrupt_vault_file_is_a_typed_error(tmp_path):
    from cryptography.fernet import Fernet

    from agent.vault_store import VaultError, VaultStore

    base = tmp_path / "vault"
    base.mkdir(parents=True, exist_ok=True)
    # A key exists but the blob is not a Fernet token → typed corruption error.
    (base / "vault.key").write_bytes(Fernet.generate_key())
    (base / "vault.json.enc").write_bytes(b"not-a-fernet-token")
    with pytest.raises(VaultError):
        VaultStore(base_dir=base).list_items()


# ---------------------------------------------------------------------------
# 6. `vault sources` probes optional managers; absent → typed unavailable.
# ---------------------------------------------------------------------------
def test_sources_absent_managers_is_typed_not_a_crash(tmp_path):
    r = _run_agent(tmp_path, ["vault", "sources"])
    assert r.returncode == 0, (r.returncode, r.stderr[-600:])
    combined = r.stdout + r.stderr
    assert "Traceback" not in combined, combined[-600:]
    assert "1Password" in combined and "Bitwarden" in combined, combined[:800]
    # Neither manager CLI is installed in this environment: each is reported as
    # unavailable/turned-off, never a crash or a bare refusal.
    assert ("not installed" in combined) or ("turned off" in combined), combined[:800]


def test_sources_unknown_manager_is_reported_not_crashing(tmp_path):
    r = _run_agent(tmp_path, ["vault", "sources", "--disable", "__nope__"])
    assert r.returncode == 0, (r.returncode, r.stderr[-400:])
    combined = r.stdout + r.stderr
    assert "Unknown password manager" in combined, combined[-400:]
    assert "Traceback" not in combined


# ---------------------------------------------------------------------------
# 7. Validation is typed (no partial writes).
# ---------------------------------------------------------------------------
def test_add_validation_is_typed(tmp_path):
    from agent.vault_store import VaultError, VaultStore

    store = VaultStore(base_dir=tmp_path / "vault")
    with pytest.raises(VaultError):
        store.add_item(kind="bogus", label="x", secret={})
    with pytest.raises(VaultError):
        store.add_item(kind="login", label="", origin=DUMMY_ORIGIN,
                       secret={"identifier_type": "email", "identifier": "a@b.c", "password": "p"})
    with pytest.raises(VaultError):
        store.add_item(kind="login", label="x", origin="", 
                       secret={"identifier_type": "email", "identifier": "a@b.c", "password": "p"})
    with pytest.raises(VaultError):
        store.add_item(kind="login", label="x", origin=DUMMY_ORIGIN,
                       secret={"identifier_type": "nope", "identifier": "a@b.c", "password": "p"})
    with pytest.raises(VaultError):
        store.add_item(kind="payment", label="x", secret={"card_number": "4111"})
    # Nothing was written by the failed validations.
    assert store.list_items() == []


# ---------------------------------------------------------------------------
# 8. Defensive secret scrub (surfaced exceptions never carry a value).
# ---------------------------------------------------------------------------
def test_scrub_secret_from_text_removes_values_and_password_echoes():
    from agent.vault_store import scrub_secret_from_text

    secret = {"password": DUMMY_PASSWORD, "cvc": "123"}
    scrubbed = scrub_secret_from_text(
        f"failed with {DUMMY_PASSWORD} and cvc=123", secret)
    assert DUMMY_PASSWORD not in scrubbed
    assert "[REDACTED]" in scrubbed


def test_normalize_origin_strips_default_ports():
    from agent.vault_store import VaultError, normalize_origin

    assert normalize_origin("https://Example.com:443/path") == "https://example.com"
    assert normalize_origin("http://example.com/login") == "http://example.com"
    assert normalize_origin("https://example.com:8443") == "https://example.com:8443"
    with pytest.raises(VaultError):
        normalize_origin("example.com")


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))
