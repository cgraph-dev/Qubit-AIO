"""Packet 0B tests: env overrides, config doctor, frontend resolution, and the
config contract that keeps portable examples machine-agnostic."""
from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
DOCTOR = REPO / "scripts/config_doctor.py"


def _run_doctor(*args: str, env_extra: dict[str, str] | None = None):
    env = dict(os.environ)
    env.update(env_extra or {})
    return subprocess.run([sys.executable, str(DOCTOR), *args],
                          capture_output=True, text=True, env=env, timeout=60)


# ---------------------------------------------------------------- env overrides

def test_double_underscore_reaches_underscored_key(monkeypatch):
    from voicebridge.config import Config
    monkeypatch.setenv("VOICE_BRIDGE__INTENT__LOCAL_LLM__MODEL", "probe-model")
    cfg = Config.load()
    assert cfg.get("intent", "local_llm", "model") == "probe-model"


def test_legacy_single_underscore_still_works(monkeypatch):
    from voicebridge.config import Config
    monkeypatch.setenv("VOICE_BRIDGE_WEBUI_TOKEN", "legacytok")
    cfg = Config.load()
    assert cfg.get("webui", "token") == "legacytok"


def test_env_overrides_beat_file_and_defaults(monkeypatch, tmp_path):
    from voicebridge.config import Config
    y = tmp_path / "voice.yaml"
    y.write_text("intent:\n  local_llm:\n    model: file-model\n")
    monkeypatch.setenv("VOICE_BRIDGE_CONFIG", str(y))
    cfg = Config.load(str(y))
    assert cfg.get("intent", "local_llm", "model") == "file-model"
    monkeypatch.setenv("VOICE_BRIDGE__INTENT__LOCAL_LLM__MODEL", "env-model")
    cfg2 = Config.load(str(y))
    assert cfg2.get("intent", "local_llm", "model") == "env-model"


# ---------------------------------------------------------------- doctor

SAFE_CFG = ("mic:\n  channels: 1\n  gain: 3.0\n"
            "webui:\n  host: 127.0.0.1\n  token: \"\"\n")


def test_static_ok_on_safe_config(tmp_path):
    y = tmp_path / "voice.yaml"
    y.write_text(SAFE_CFG)
    r = _run_doctor("--static", "--config", str(y))
    assert r.returncode == 0, r.stdout + r.stderr
    assert "config.loaded_path" in r.stdout
    assert not __import__("re").search(r"\[\s*FAIL\]", r.stdout)


def test_static_fails_on_invalid_audio(tmp_path, monkeypatch):
    y = tmp_path / "voice.yaml"
    y.write_text("mic:\n  channels: 0\n  gain: 3.0\n")
    r = _run_doctor("--static", "--config", str(y))
    assert r.returncode == 2
    assert re.search(r"\[\s*FAIL\]", r.stdout)
    assert "audio.mic_channels" in r.stdout


def test_static_fails_on_open_nonloopback_token_combo(tmp_path):
    y = tmp_path / "voice.yaml"
    y.write_text("webui:\n  host: 0.0.0.0\n  token: \"\"\n")
    r = _run_doctor("--static", "--config", str(y))
    assert r.returncode == 2
    assert "security.token_combo" in r.stdout
    assert re.search(r"\[\s*FAIL\] security", r.stdout)


def test_never_prints_secret_values(tmp_path):
    secret = "SUPERSECRET-token-value-9f3a"
    y = tmp_path / "voice.yaml"
    y.write_text(f'webui:\n  token: "{secret}"\n')
    for mode in ("--static", "--runtime"):
        r = _run_doctor(mode, "--config", str(y))
        assert secret not in r.stdout and secret not in r.stderr
    assert "<set>" in _run_doctor("--static", "--config", str(y)).stdout


# ---------------------------------------------------------------- frontend

def test_frontend_resolution_prefers_current_config_dir(tmp_path):
    # A configured external checkout is honored only when it is CURRENT: it
    # must carry a VERSION marker equal to the repository frontend's (Packet 8E
    # stale-fallback contract).
    from voicebridge.webui import resolve_frontend_dir, frontend_status
    custom = tmp_path / "theme"
    custom.mkdir(parents=True)
    (custom / "index.html").write_text("<html></html>")
    (custom / "VERSION").write_text((REPO / "frontend" / "VERSION").read_text())
    got, serves = resolve_frontend_dir(str(custom))
    assert serves and Path(got) == custom
    _p, _s, stale, _v, active = frontend_status(str(custom))
    assert active == "external" and stale is False


def test_frontend_resolution_stale_external_falls_back_to_repo(tmp_path):
    # An unversioned/stale external checkout is NOT served; the repository
    # frontend is used (safe fallback) and reported as stale (Packet 8E).
    from voicebridge.webui import resolve_frontend_dir, frontend_status
    custom = tmp_path / "stale_theme"
    custom.mkdir(parents=True)
    (custom / "index.html").write_text("<html></html>")   # no VERSION marker
    got, serves = resolve_frontend_dir(str(custom))
    assert serves and Path(got) == REPO / "frontend"
    _p, _s, stale, _v, active = frontend_status(str(custom))
    assert active == "repo" and stale is True


def test_frontend_resolution_falls_back_to_repo(tmp_path):
    from voicebridge.webui import resolve_frontend_dir
    got, serves = resolve_frontend_dir(str(tmp_path / "missing"))
    repo_fe = REPO / "frontend"
    assert Path(got) == repo_fe and serves is (repo_fe / "index.html").exists()


def test_no_hardcoded_home_in_touched_sources():
    for rel in ("voicebridge/webui.py", "voicebridge/config.py",
                "scripts/config_doctor.py"):
        text = (REPO / rel).read_text(errors="replace")
        assert "/home/trick" not in text, f"{rel} hardcodes a user home"


# ------------------------------------------------------- portable config contract

def _yaml(rel: str) -> str:
    return (REPO / rel).read_text(errors="replace")


MACHINE_TOKENS = ("Scarlett", "HyperX", "AT2020")


def test_portable_examples_are_machine_agnostic():
    for rel in ("config/voice.example.yaml", "config/voice.local.yaml"):
        text = _yaml(rel)
        for tok in MACHINE_TOKENS:
            assert tok.lower() not in text.lower(), f"{rel} claims {tok}"
        assert "/home/" not in text, f"{rel} embeds an absolute home path"


def test_examples_pin_current_model_and_safe_audio():
    import yaml
    ex = yaml.safe_load(_yaml("config/voice.example.yaml"))
    loc = yaml.safe_load(_yaml("config/voice.local.yaml"))
    for cfg, name in ((ex, "example"), (loc, "local")):
        assert cfg["intent"]["local_llm"]["model"] == "qubit-brain4", name
        assert int(cfg["mic"]["channels"]) == 1, name
        assert float(cfg["mic"]["gain"]) <= 4.0, name


def test_example_output_pins_empty_and_web_speech_off():
    import yaml
    ex = yaml.safe_load(_yaml("config/voice.example.yaml"))
    assert ex["audio"]["output_device"] in ("", None)
    assert ex["audio"]["output_device_name"] in ("", None)
    assert ex["webui"]["speak_web_locally"] is False


# ------------------------------------------------- absence-of-services robustness

def test_static_runs_without_any_user_environment(tmp_path, monkeypatch):
    """Static doctor stays functional with no user config, no installed unit,
    and (by construction of --static) no Ollama/audio/gateway/Chromium."""
    fake_home = tmp_path / "home"
    (fake_home / ".config").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(fake_home))
    monkeypatch.delenv("VOICE_BRIDGE_CONFIG", raising=False)
    r = _run_doctor("--static")
    assert r.returncode == 0, r.stdout + r.stderr
    def state_for(name: str) -> str:
        for line in r.stdout.splitlines():
            if line.endswith(f"] {name}") or f"] {name} " in line:
                return line.split("]")[0].strip("[ ")
        return ""
    assert state_for("config.loaded_path") == "DEGRADED"
    assert state_for("systemd.installed") == "MISSING"


def _run_helper(home, repo=REPO, force=""):
    env = dict(os.environ, HOME=str(home))
    if force:
        env["INSTALL_FORCE_UNIT"] = force
    return subprocess.run(["sh", str(REPO / "scripts/install_systemd_unit.sh"), str(repo)],
                          capture_output=True, text=True, env=env, timeout=30)


TUNED = "# tuned machine override\nRestart=always\nMemoryMax=2G\n"


def _fresh_home(tmp_path):
    home = tmp_path / "home"
    (home / ".config/systemd/user").mkdir(parents=True)
    return home


def test_migration_fresh_install_writes_unit(tmp_path):
    home = _fresh_home(tmp_path)
    r = _run_helper(home)
    assert r.returncode == 0 and "installed atomically" in r.stdout
    unit = home / ".config/systemd/user/qubit-voice-agent.service"
    assert "__PROJECT_DIR__" not in unit.read_text()


def test_migration_idempotent_identical_unit(tmp_path):
    home = _fresh_home(tmp_path)
    assert _run_helper(home).returncode == 0
    before = (home / ".config/systemd/user/qubit-voice-agent.service").read_text()
    r = _run_helper(home)
    assert r.returncode == 0 and "idempotent" in r.stdout
    after = (home / ".config/systemd/user/qubit-voice-agent.service").read_text()
    assert before == after


def test_migration_preserves_divergent_unit(tmp_path):
    home = _fresh_home(tmp_path)
    unit = home / ".config/systemd/user/qubit-voice-agent.service"
    unit.write_text(TUNED)
    r = _run_helper(home)
    assert r.returncode == 0 and "PRESERVED" in r.stdout
    assert unit.read_text() == TUNED                                   # untouched
    backups = list(home.glob(".config/systemd/user/*.bak.*"))
    assert len(backups) == 1          # snapshot taken even in preserve mode
    assert backups[0].read_text() == TUNED                             # faithful copy
    assert (home / ".config/systemd/user/"
                  "qubit-voice-agent.service.template-new").exists()


def test_migration_force_overwrite_and_rollback_drill(tmp_path):
    """Reproducible rollback: forced overwrite creates a timestamped backup and
    restoring the newest backup returns the exact prior tuned unit."""
    import glob as _glob
    home = _fresh_home(tmp_path)
    unit = home / ".config/systemd/user/qubit-voice-agent.service"
    unit.write_text(TUNED)
    r = _run_helper(home, force="1")
    assert r.returncode == 0 and "FORCED" in r.stdout
    assert unit.read_text() != TUNED                       # replaced
    backups = sorted(_glob.glob(str(unit) + ".bak.*"))
    assert len(backups) == 1                               # exactly one backup
    # --- documented rollback drill ---
    restore = subprocess.run(["cp", "-p", backups[0], str(unit)], check=True)
    assert restore.returncode == 0
    assert unit.read_text() == TUNED                       # prior state recovered


def test_clean_import_contract_positive_and_negative(tmp_path):
    checker = REPO / "scripts/check_clean_imports.py"
    ok = subprocess.run([sys.executable, str(checker)],
                        capture_output=True, text=True, timeout=60)
    assert ok.returncode == 0, ok.stdout + ok.stderr
    rogue = tmp_path / "rogue_probe.py"
    rogue.write_text("import pydantic\n")   # installed in dev venv but UNDECLARED
    bad = subprocess.run([sys.executable, str(checker), "--negative", str(rogue)],
                         capture_output=True, text=True, timeout=60)
    assert bad.returncode == 0 and "blocked:" in bad.stdout
