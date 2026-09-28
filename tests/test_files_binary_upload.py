"""Brokered, confined binary file upload (audit 2026-09-23 /files binary).

Ports the reference managed-files binary upload path
(``hermes_cli/web_routers/files.py:493-565`` -> ``_resolve_managed_path`` with
the ``_MANAGED_FILE_MAX_BYTES`` cap at ``hermes_cli/web_server.py:821``) onto
Qubit's single confined-write path:

* ``file_patch.write_bytes`` requires explicit confirmation, is confined to an
  approved project root (no absolute escape / ``..`` / symlink), persists the
  exact bytes (NUL + non-UTF-8 included) and is reversible from its backup;
* the ``local.files_upload`` capability is registered MUTATING and needs a
  durable broker confirmation — a client boolean is never authority;
* the multipart body parser hands the binary payload to the handler contract
  as ``content_binary`` (raw) and ``content_base64`` (JSON-scalar form) while a
  UTF-8 upload keeps its text path.
"""
from __future__ import annotations

import base64
import json

import pytest

from voicebridge import file_patch as FP

pytestmark = pytest.mark.unit


def _cfg(tmp_path, monkeypatch):
    from voicebridge import config as C

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "c"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "s"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "d"))
    p = tmp_path / "voice.yaml"
    p.write_text("logging:\n  jsonl: false\n")
    return C.Config.load(p)


def _project(cfg, folder):
    from voicebridge import projects as PJ

    created = PJ.create(cfg, "Work", [str(folder)], requested_by="t",
                        confirmed=True, use=True)
    assert created.get("state") == "OK", created
    return created


def _confinement_ok():
    return FP.confinement_available()[0]


BLOB = b"\x00\x01\x02PNG\xff\xfe\x00binary\x80\x81payload\x00"


# ------------------------------------------------------------- confirmation
def test_binary_write_requires_confirmation(tmp_path, monkeypatch):
    if not _confinement_ok():
        pytest.skip("bubblewrap confinement unavailable")
    cfg = _cfg(tmp_path, monkeypatch)
    root = tmp_path / "proj"
    root.mkdir()
    _project(cfg, root)
    target = root / "blob.bin"

    with pytest.raises(FP.FilePatchError) as exc:
        FP.write_bytes(cfg, str(target), BLOB, requested_by="t",
                       confirmed=False)
    assert exc.value.code == FP.CONFIRM_CODE
    assert not target.exists(), "unconfirmed upload must write nothing"

    receipt = FP.write_bytes(cfg, str(target), BLOB, requested_by="t",
                             confirmed=True)
    assert receipt["state"] == "OK", receipt
    assert receipt["code"] == "FILES_UPLOADED"
    assert target.read_bytes() == BLOB


# ------------------------------------------------------- registry -> root bridge
def test_registered_project_folder_is_approved_root(tmp_path, monkeypatch):
    """A folder added through the canonical project registry is exactly the
    approved root the confined upload path resolves against.

    Owner Decision #2 (2026-09-23) approved a real repo folder as the
    binary-upload project root. This pins the bridge the live proof exercises:
    ``projects.create``/``add_folder`` -> ``files.approved_roots`` ->
    ``file_patch.resolve_target`` accepts an in-root file and refuses an
    out-of-root one.
    """
    from voicebridge import files as FS
    from voicebridge import projects as PJ

    cfg = _cfg(tmp_path, monkeypatch)
    root = tmp_path / "approved-repo"
    root.mkdir()
    other = tmp_path / "not-approved"
    other.mkdir()
    PJ.create(cfg, "Approved", [str(root)], requested_by="t",
              confirmed=True, use=True)

    approved = FS.approved_roots(cfg)
    assert root.resolve() in approved, approved

    inside = root / "probe.bin"
    assert FP.resolve_target(cfg, str(inside)) == inside.resolve()

    with pytest.raises(FP.FilePatchError) as exc:
        FP.resolve_target(cfg, str(other / "probe.bin"))
    assert exc.value.code == FP.OUTSIDE_ROOT_CODE


# --------------------------------------------------------------- confinement
def test_binary_write_rejects_traversal(tmp_path, monkeypatch):
    if not _confinement_ok():
        pytest.skip("bubblewrap confinement unavailable")
    cfg = _cfg(tmp_path, monkeypatch)
    root = tmp_path / "proj"
    root.mkdir()
    _project(cfg, root)
    outside = tmp_path / "outside.bin"
    outside.write_bytes(b"untouched")

    with pytest.raises(FP.FilePatchError) as exc:
        FP.write_bytes(cfg, str(root / ".." / "outside.bin"), BLOB,
                       requested_by="t", confirmed=True)
    assert exc.value.code == FP.OUTSIDE_ROOT_CODE
    assert outside.read_bytes() == b"untouched"


def test_binary_write_rejects_absolute_escape(tmp_path, monkeypatch):
    if not _confinement_ok():
        pytest.skip("bubblewrap confinement unavailable")
    cfg = _cfg(tmp_path, monkeypatch)
    root = tmp_path / "proj"
    root.mkdir()
    _project(cfg, root)
    outside = tmp_path / "elsewhere" / "evil.bin"
    outside.parent.mkdir()

    with pytest.raises(FP.FilePatchError) as exc:
        FP.write_bytes(cfg, str(outside), BLOB, requested_by="t",
                       confirmed=True)
    assert exc.value.code == FP.OUTSIDE_ROOT_CODE
    assert not outside.exists()


def test_binary_write_rejects_symlink_target(tmp_path, monkeypatch):
    if not _confinement_ok():
        pytest.skip("bubblewrap confinement unavailable")
    cfg = _cfg(tmp_path, monkeypatch)
    root = tmp_path / "proj"
    root.mkdir()
    _project(cfg, root)
    outside = tmp_path / "outside.bin"
    outside.write_bytes(b"untouched")
    link = root / "link.bin"
    try:
        link.symlink_to(outside)
    except OSError:
        pytest.skip("symlinks unavailable")

    with pytest.raises(FP.FilePatchError) as exc:
        FP.write_bytes(cfg, str(link), BLOB, requested_by="t",
                       confirmed=True)
    assert exc.value.code == FP.OUTSIDE_ROOT_CODE
    assert outside.read_bytes() == b"untouched"


# ---------------------------------------------------------------- persistence
def test_binary_write_persists_exact_bytes_and_reverses(tmp_path, monkeypatch):
    if not _confinement_ok():
        pytest.skip("bubblewrap confinement unavailable")
    cfg = _cfg(tmp_path, monkeypatch)
    root = tmp_path / "proj"
    root.mkdir()
    _project(cfg, root)
    target = root / "blob.bin"
    target.write_bytes(b"prior-bytes")

    receipt = FP.write_bytes(cfg, str(target), BLOB, requested_by="t",
                             confirmed=True)
    assert receipt["state"] == "OK", receipt
    assert target.read_bytes() == BLOB
    assert receipt["bytes_before"] == len(b"prior-bytes")
    assert receipt["bytes_after"] == len(BLOB)
    assert receipt["backup"], receipt

    undone = FP.rollback(cfg, str(target), backup=receipt["backup"],
                         requested_by="t", confirmed=True)
    assert undone["state"] == "OK", undone
    assert target.read_bytes() == b"prior-bytes"


def test_binary_write_no_overwrite_is_conflict(tmp_path, monkeypatch):
    if not _confinement_ok():
        pytest.skip("bubblewrap confinement unavailable")
    cfg = _cfg(tmp_path, monkeypatch)
    root = tmp_path / "proj"
    root.mkdir()
    _project(cfg, root)
    target = root / "blob.bin"
    target.write_bytes(b"existing")

    with pytest.raises(FP.FilePatchError) as exc:
        FP.write_bytes(cfg, str(target), BLOB, requested_by="t",
                       confirmed=True, overwrite=False)
    assert exc.value.code == FP.CONFLICT_CODE
    assert target.read_bytes() == b"existing"


def test_binary_write_creates_empty_file(tmp_path, monkeypatch):
    if not _confinement_ok():
        pytest.skip("bubblewrap confinement unavailable")
    cfg = _cfg(tmp_path, monkeypatch)
    root = tmp_path / "proj"
    root.mkdir()
    _project(cfg, root)
    target = root / "empty.bin"

    receipt = FP.write_bytes(cfg, str(target), b"", requested_by="t",
                             confirmed=True)
    assert receipt["state"] == "OK", receipt
    assert target.is_file() and target.read_bytes() == b""


def test_binary_write_enforces_size_limit(tmp_path, monkeypatch):
    if not _confinement_ok():
        pytest.skip("bubblewrap confinement unavailable")
    cfg = _cfg(tmp_path, monkeypatch)
    root = tmp_path / "proj"
    root.mkdir()
    _project(cfg, root)
    target = root / "big.bin"
    monkeypatch.setattr(FP, "MAX_UPLOAD_BYTES", 8)

    with pytest.raises(FP.FilePatchError) as exc:
        FP.write_bytes(cfg, str(target), b"x" * 9, requested_by="t",
                       confirmed=True)
    assert exc.value.code == FP.TOO_LARGE_CODE
    assert not target.exists()


def test_binary_write_estop_refuses(tmp_path, monkeypatch):
    if not _confinement_ok():
        pytest.skip("bubblewrap confinement unavailable")
    cfg = _cfg(tmp_path, monkeypatch)
    root = tmp_path / "proj"
    root.mkdir()
    _project(cfg, root)
    target = root / "blob.bin"
    from voicebridge.power import power_control_store

    power_control_store(cfg).set(emergency_stop=True, global_disable=False,
                                 issued_by="user")
    with pytest.raises(FP.FilePatchError) as exc:
        FP.write_bytes(cfg, str(target), BLOB, requested_by="t",
                       confirmed=True)
    assert exc.value.code == FP.ESTOP_CODE
    assert not target.exists()


# -------------------------------------------------------------------- audit
def test_binary_write_is_audited_without_payload(tmp_path, monkeypatch):
    if not _confinement_ok():
        pytest.skip("bubblewrap confinement unavailable")
    cfg = _cfg(tmp_path, monkeypatch)
    root = tmp_path / "proj"
    root.mkdir()
    _project(cfg, root)
    target = root / "blob.bin"

    captured = []
    import voicebridge.logging_setup as LS

    monkeypatch.setattr(
        LS, "log_event",
        lambda event, **fields: captured.append((event, fields)))
    receipt = FP.write_bytes(cfg, str(target), BLOB, requested_by="t",
                             confirmed=True)
    assert receipt["state"] == "OK", receipt
    events = {e for e, _ in captured}
    assert "file_patch.upload" in events, captured
    blob = json.dumps(captured)
    assert repr(BLOB) not in blob
    assert BLOB.hex() not in blob
    assert receipt["after_sha256"] in blob


# -------------------------------------------------------------------- broker
def test_broker_registers_upload_capability():
    from voicebridge import file_patch as FP2
    from voicebridge.broker import build_host_broker, register_file_patch
    from voicebridge.capabilities import Tier

    broker = build_host_broker()
    names = register_file_patch(broker)
    assert FP2.write_bytes_capability_name() in names
    spec = broker.registry.get(FP2.write_bytes_capability_name())
    assert spec is not None
    assert spec.tier is Tier.MUTATING
    assert spec.needs_confirmation is True
    assert "local" in spec.scopes


def test_broker_upload_requires_durable_confirmation(tmp_path, monkeypatch):
    if not _confinement_ok():
        pytest.skip("bubblewrap confinement unavailable")
    from voicebridge.broker import build_host_broker, register_file_patch
    from voicebridge import file_patch as FP2

    cfg = _cfg(tmp_path, monkeypatch)
    root = tmp_path / "proj"
    root.mkdir()
    _project(cfg, root)
    target = root / "blob.bin"
    broker = build_host_broker(cfg=cfg)
    register_file_patch(broker, cfg=cfg)
    args = {"target_path": str(target),
            "content_base64": base64.b64encode(BLOB).decode(),
            "overwrite": True, "requested_by": "dashboard:user",
            "project_root": ""}

    first = broker.execute(FP2.write_bytes_capability_name(), dict(args),
                           source="model")
    assert not first.ok
    assert first.denied in ("CONFIRMATION_SOURCE_FORBIDDEN",
                            "CONFIRMATION_REQUIRED"), first.denied
    assert not target.exists(), "unconfirmed upload must not write"

    rec = broker.issue_confirmation(FP2.write_bytes_capability_name(),
                                    dict(args), issued_by="user")
    done = broker.execute(FP2.write_bytes_capability_name(), dict(args),
                          source="user", confirmation_id=rec.id)
    assert done.ok, (done.denied, done.error, done.output)
    payload = json.loads(done.output)
    assert payload["ok"] is True
    assert target.read_bytes() == BLOB
    assert payload["receipt"]["code"] == "FILES_UPLOADED"

    # Single-use: a replay fails closed.
    replay = broker.execute(FP2.write_bytes_capability_name(), dict(args),
                            source="user", confirmation_id=rec.id)
    assert not replay.ok and replay.denied == "REPLAYED_CONFIRMATION"


# ------------------------------------------------------------------- parser
def _multipart(fields, filename, payload, content_type):
    boundary = "----qubitboundary9u"
    body = b""
    for name, value in fields.items():
        body += (f"--{boundary}\r\n"
                 f'Content-Disposition: form-data; name="{name}"\r\n\r\n'
                 f"{value}\r\n").encode()
    body += (f"--{boundary}\r\n"
             f'Content-Disposition: form-data; name="file"; '
             f'filename="{filename}"\r\n'
             f"Content-Type: {content_type}\r\n\r\n").encode()
    body += payload + b"\r\n"
    body += f"--{boundary}--\r\n".encode()
    return body, f"multipart/form-data; boundary={boundary}"


def test_multipart_parser_passes_binary_through():
    from voicebridge.webui import parse_multipart_form

    body, ctype = _multipart({"path": "/proj/blob.bin"}, "blob.bin", BLOB,
                             "application/octet-stream")
    parsed = parse_multipart_form(body, ctype)
    assert parsed is not None
    assert parsed["path"] == "/proj/blob.bin"
    assert parsed["content_binary"] == BLOB
    assert base64.b64decode(parsed["content_base64"]) == BLOB
    assert "content" not in parsed, "binary must not be text-decoded"
    assert parsed["_upload"]["filename"] == "blob.bin"
    assert parsed["_upload"]["content_type"] == "application/octet-stream"


def test_multipart_parser_keeps_text_path():
    from voicebridge.webui import parse_multipart_form

    body, ctype = _multipart({"path": "/proj/notes.txt"}, "notes.txt",
                             b"hello\n", "text/plain")
    parsed = parse_multipart_form(body, ctype)
    assert parsed is not None
    assert parsed["content"] == "hello\n"
    assert parsed["content_binary"] == b"hello\n"
