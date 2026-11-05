"""Hermetic tests for ``scripts/release_fresh_install_gate.py``.

No test builds the real wheel, performs the real network install, or touches a
live service. A tiny fixture repository plus a fake installer prove the gate's
pass/fail/degraded decisions.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "scripts" / "release_fresh_install_gate.py"


def _load():
    spec = importlib.util.spec_from_file_location("release_fresh_install_gate", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _write(path: Path, text: str, mode: int | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    if mode is not None:
        path.chmod(mode)


def _fixture_webui(immutable: bool = True, shell_cached: bool = False) -> str:
    cache = (
        '            cache_control = "public, max-age=31536000, immutable"\n'
        if immutable else
        '            cache_control = "public, max-age=60"\n'
    )
    shell_arg = ", cache_control='public, max-age=60'" if shell_cached else ""
    return (
        "def _gzip_cache_get(key):\n"
        "    return None\n"
        "def _gzip_cache_put(key, body):\n"
        "    return None\n"
        "class WebUIHandler:\n"
        "    def _send(self, code, body, ctype='text/plain', extra_headers=None,\n"
        "              cache_control='no-store'):\n"
        "        return None\n"
        "    def _gzip_if_wanted(self, body, path):\n"
        "        _gzip_cache_get((body, 0, 0))\n"
        "        return body, False\n"
        "    def _serve_qubit_static(self, sub):\n"
        "        rel = sub\n"
        "        if rel.startswith('assets/'):\n"
        "%s"
        "        self._file(rel, cache_control=cache_control)\n"
        "    def _serve_qubit_index(self):\n"
        "        self._send(200, b'shell', 'text/html; charset=utf-8'%s)\n"
        % (cache, shell_arg)
    )


def _fixture_repo(
    tmp_path: Path,
    *,
    dashboard: bool = True,
    tui: bool = True,
    provider: str = "ollama",
    base_url: str = "http://127.0.0.1:11434",
    install_seeds_config: bool = True,
    write_config: bool = True,
    immutable_assets: bool = True,
    shell_cached: bool = False,
) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _write(repo / "scripts" / "bootstrap.sh", "#!/usr/bin/env bash\nexit 0\n")
    _write(repo / "scripts" / "bootstrap.ps1", "# bootstrap\n")
    installer = "#!/usr/bin/env bash\n"
    if install_seeds_config:
        installer += "# seeds root config.yaml from cli-config.yaml.example\n"
    installer += "exit 0\n"
    _write(repo / "scripts" / "install.sh", installer, 0o755)
    _write(repo / "scripts" / "install.ps1", installer)
    _write(
        repo / "qubit_runtime" / "cli-config.yaml.example",
        "model:\n  provider: %s\n  default: qubit-brain\n  base_url: %s\n"
        % (provider, base_url),
    )
    _write(
        repo / "voicebridge" / "webui.py",
        _fixture_webui(immutable=immutable_assets, shell_cached=shell_cached),
    )
    if dashboard:
        _write(repo / "frontend" / "qubit-dist" / "index.html", "<html></html>\n")
    if tui:
        _write(repo / "qubit_runtime" / "ui-tui" / "dist" / "entry.js", "// tui\n")
    _write(
        repo / "qubit_runtime" / "qubit_cli" / "main_tui_launch.py",
        "def _find_bundled_tui(*args, **kwargs):\n"
        "    return None\n"
        "def _tui_node_bin(name):\n"
        "    return '/nonexistent/node'\n"
        "def _ensure_tui_workspace(tui_dir):\n"
        "    raise AssertionError('source workspace required')\n"
        "def _tui_need_rebuild(tui_dir):\n"
        "    raise AssertionError('source rebuild required')\n"
        "def _make_tui_argv(tui_dir, tui_dev):\n"
        "    return ['/nonexistent/node', '--expose-gc', str(tui_dir / 'dist' / 'entry.js')], tui_dir\n",
    )
    # A fake installer used only by the fresh-install probe: it honours
    # --prefix and writes a root config.yaml like the shipped installer must.
    fake_installer = (
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        "prefix=''\n"
        "while [ \"$#\" -gt 0 ]; do\n"
        "  case \"$1\" in\n"
        "    --prefix) prefix=\"$2\"; shift 2 ;;\n"
        "    *) shift ;;\n"
        "  esac\n"
        "done\n"
    )
    if install_seeds_config:
        fake_installer += "# seeds root config.yaml from cli-config.yaml.example\n"
    if write_config:
        fake_installer += (
            "mkdir -p \"$prefix\"\n"
            "printf 'model:\\n  provider: %s\\n  default: qubit-brain\\n  base_url: %s\\n' "
            "> \"$prefix/config.yaml\"\n" % (provider, base_url)
        )
    fake_installer += "exit 0\n"
    _write(repo / "scripts" / "install.sh", fake_installer, 0o755)
    return repo


def test_static_contract_passes_but_fresh_install_is_degraded(tmp_path: Path):
    module = _load()
    repo = _fixture_repo(tmp_path)
    report = module.run_gate(repo=repo)
    by_name = {check.name: check for check in report.checks}
    assert by_name["dashboard_assets"].state == "OK"
    assert by_name["tui_dist_entry"].state == "OK"
    assert by_name["packaged_tui_no_source_build"].state == "OK"
    assert by_name["config_default_ollama"].state == "OK"
    assert by_name["installer_install_sh_seeds_config"].state == "OK"
    assert by_name["fresh_install"].state == "DEGRADED"
    assert report.verdict == "DEGRADED"


def test_fresh_install_must_create_root_config(tmp_path: Path):
    module = _load()
    repo = _fixture_repo(tmp_path, write_config=False)
    report = module.run_gate(repo=repo, fresh_install=True,
                             require_fresh_install=True)
    by_name = {check.name: check for check in report.checks}
    assert by_name["fresh_install_root_config"].state == "FAIL"
    assert report.verdict == "FAIL"


def test_fresh_install_requires_ollama_provider(tmp_path: Path):
    module = _load()
    repo = _fixture_repo(tmp_path, provider="auto",
                         base_url="https://openrouter.ai/api/v1")
    report = module.run_gate(repo=repo, fresh_install=True,
                             require_fresh_install=True)
    by_name = {check.name: check for check in report.checks}
    assert by_name["config_default_ollama"].state == "FAIL"
    assert by_name["fresh_install_ollama_provider"].state == "FAIL"
    assert report.verdict == "FAIL"


def test_fresh_install_passes_with_root_ollama_config(tmp_path: Path):
    module = _load()
    repo = _fixture_repo(tmp_path)
    report = module.run_gate(repo=repo, fresh_install=True,
                             require_fresh_install=True)
    by_name = {check.name: check for check in report.checks}
    assert by_name["fresh_install_root_config"].state == "OK"
    assert by_name["fresh_install_ollama_provider"].state == "OK"
    assert report.verdict == "PASS"


def test_missing_dashboard_and_tui_fail(tmp_path: Path):
    module = _load()
    repo = _fixture_repo(tmp_path, dashboard=False, tui=False)
    report = module.run_gate(repo=repo, require_dashboard=True)
    by_name = {check.name: check for check in report.checks}
    assert by_name["dashboard_assets"].state == "FAIL"
    assert by_name["tui_dist_entry"].state == "FAIL"
    assert by_name["packaged_tui_no_source_build"].state == "FAIL"
    assert report.verdict == "FAIL"


def test_installer_must_seed_config(tmp_path: Path):
    module = _load()
    repo = _fixture_repo(tmp_path, install_seeds_config=False, write_config=False)
    report = module.run_gate(repo=repo)
    by_name = {check.name: check for check in report.checks}
    assert by_name["installer_install_sh_seeds_config"].state == "FAIL"
    assert report.verdict == "FAIL"


def test_cli_exit_codes_and_json(tmp_path: Path, capsys):
    module = _load()
    repo = _fixture_repo(tmp_path)
    rc = module.main(["--repo", str(repo), "--json"])
    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert rc == module.EXIT_DEGRADED
    assert payload["verdict"] == "DEGRADED"

    rc = module.main(["--repo", str(repo), "--fresh-install",
                      "--require-fresh-install"])
    assert rc == module.EXIT_OK


def test_perf_cache_contract_passes(tmp_path: Path):
    module = _load()
    repo = _fixture_repo(tmp_path)
    report = module.run_gate(repo=repo)
    by_name = {check.name: check for check in report.checks}
    assert by_name["perf_cache_static_contract"].state == "OK"
    assert "immutable" in by_name["perf_cache_static_contract"].detail


def test_perf_cache_contract_requires_immutable_assets(tmp_path: Path):
    module = _load()
    repo = _fixture_repo(tmp_path, immutable_assets=False)
    report = module.run_gate(repo=repo)
    by_name = {check.name: check for check in report.checks}
    assert by_name["perf_cache_static_contract"].state == "FAIL"
    assert "immutable" in by_name["perf_cache_static_contract"].detail
    assert report.verdict == "FAIL"


def test_perf_cache_contract_rejects_cached_token_shell(tmp_path: Path):
    module = _load()
    repo = _fixture_repo(tmp_path, shell_cached=True)
    report = module.run_gate(repo=repo)
    by_name = {check.name: check for check in report.checks}
    assert by_name["perf_cache_static_contract"].state == "FAIL"
    assert "shell" in by_name["perf_cache_static_contract"].detail
    assert report.verdict == "FAIL"


def test_release_dir_requires_platform_assets(tmp_path: Path):
    module = _load()
    repo = _fixture_repo(tmp_path)
    release = tmp_path / "release"
    release.mkdir()
    for name in ("bootstrap.sh", "install.sh", "qubit-release.tar.gz"):
        _write(release / name, "x")
    report = module.run_gate(repo=repo, release_dir=release)
    by_name = {check.name: check for check in report.checks}
    assert by_name["release_assets"].state == "FAIL"
    assert "bootstrap.ps1" in by_name["release_assets"].detail
    assert "install.ps1" in by_name["release_assets"].detail
    assert "qubit-release.zip" in by_name["release_assets"].detail
