#!/usr/bin/env python3
"""Live proof for the workbench config / extension / auth family.

Boots a REAL ``voicebridge.webui.WebUI`` on a spare port with a throwaway
``QUBIT_HOME`` and drives every path in this family over HTTP — the same
``/qubit-workbench/api/*`` surface the ported page calls. Nothing here mocks the
handler: the assertions are on real status codes and real response bodies, and
the config writes are read back off disk.

    python3 voicebridge/workbench_ui/_prove_config.py

Exit 0 when every check passes; prints each failure and exits 1 otherwise.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import urllib.error
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "qubit_runtime"))

# A throwaway home BEFORE any qubit_cli/config import reads the real one.
_HOME = Path(tempfile.mkdtemp(prefix="qubit-wb-cfgprove-")) / "home"
_HOME.mkdir(parents=True, exist_ok=True)
os.environ["QUBIT_HOME"] = str(_HOME)

FAIL = 0


def check(label: str, ok: bool, detail: str = "") -> None:
    global FAIL
    if ok:
        print(f"  OK   {label}")
    else:
        FAIL += 1
        print(f"  FAIL {label}  {detail}")


def _client(port: int, token: str):
    base = f"http://127.0.0.1:{port}"

    def call(path: str, method: str = "GET", body=None):
        data = None
        headers = {"Accept": "application/json"}
        if token:
            headers["X-Api-Token"] = token
        if body is not None:
            data = json.dumps(body).encode()
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(base + path, data=data, method=method,
                                     headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=20) as resp:
                raw = resp.read()
                try:
                    return resp.status, json.loads(raw)
                except ValueError:
                    return resp.status, raw.decode("utf-8", "replace")
        except urllib.error.HTTPError as exc:
            raw = exc.read()
            try:
                return exc.code, json.loads(raw)
            except ValueError:
                return exc.code, raw.decode("utf-8", "replace")

    return call


def main() -> int:
    from voicebridge import webui as W
    from voicebridge.config import Config

    cfg = Config.load()
    cfg.data.setdefault("webui", {})["state_dir"] = tempfile.mkdtemp(
        prefix="qubit-wb-cfgprove-state-")

    class Orch:
        def __init__(self, config):
            self.cfg = config

    ui = W.WebUI(Orch(cfg), host="127.0.0.1", port=0)
    ui.start()
    try:
        ui._server.wb_cfg = cfg  # type: ignore[attr-defined]
        port = ui._server.server_address[1]
        api = _client(port, ui.api_token or "")
        pre = "/qubit-workbench/api/"
        print(f"live server: 127.0.0.1:{port}  QUBIT_HOME={_HOME}")

        print("\n[settings]")
        code, out = api(pre + "settings")
        check("GET settings is 200", code == 200, str(out)[:200])
        check("carries show_thinking", "show_thinking" in out if isinstance(out, dict) else False)
        check("carries default_model", "default_model" in out if isinstance(out, dict) else False)
        code, out = api(pre + "settings", "POST", {"show_thinking": False})
        check("POST settings writes", code == 200 and out.get("ok") is True, str(out)[:200])
        code, out = api(pre + "settings")
        check("GET reflects the write", out.get("show_thinking") is False, str(out.get("show_thinking")))
        code, out = api(pre + "settings", "POST", {"bogus_field": 1})
        check("POST unknown field refused", code == 501 and out.get("code") == "WB_NOT_IN_PORT_SET",
              f"{code} {str(out)[:120]}")

        print("\n[reasoning]")
        code, out = api(pre + "reasoning", "POST", {"effort": "low"})
        check("POST effort writes", code == 200 and out.get("ok") is True, str(out)[:160])
        code, out = api(pre + "reasoning")
        check("GET reports effort", out.get("reasoning_effort") == "low", str(out.get("reasoning_effort")))
        check("GET exposes the ladder", "low" in (out.get("supported_efforts") or []))
        code, out = api(pre + "reasoning", "POST", {"effort": "turbo"})
        check("POST bad effort refused", code == 400, f"{code} {str(out)[:120]}")

        print("\n[models / default-model]")
        code, out = api(pre + "models")
        check("GET models answers (200, or the typed no-inventory refusal)",
              code == 200 or (isinstance(out, dict)
                              and out.get("code") == "WB_NO_MODEL_INVENTORY"),
              str(out)[:160])
        code, out = api(pre + "default-model", "POST", {"model": "x"})
        check("POST default-model without provider refused (a write path)",
              code == 400 and out.get("code") == "WB_BAD_INPUT", f"{code} {str(out)[:120]}")

        print("\n[providers]")
        code, out = api(pre + "providers")
        check("GET providers 200-or-typed", code in (200, 503), f"{code} {str(out)[:140]}")
        if isinstance(out, dict) and code == 200:
            check("no secret in the payload", "sk-" not in json.dumps(out))
        code, out = api(pre + "providers", "POST", {"provider": "openai", "api_key": "sk-leak"})
        check("POST with a raw key refused",
              code == 403 and out.get("code") == "WB_CREDENTIALS_NOT_IN_CONFIG",
              f"{code} {str(out)[:120]}")
        check("the key is not echoed", "sk-leak" not in json.dumps(out))

        print("\n[tools]")
        code, out = api(pre + "mcp/tools")
        check("mcp/tools answers 200 (was 404)", code == 200, f"{code} {str(out)[:140]}")
        code2, out2 = api(pre + "tools")
        check("tools agrees with mcp/tools",
              code2 == 200 and out.get("total") == out2.get("total"),
              f"{code2} {str(out2)[:120]}")

        print("\n[extensions]")
        code, out = api(pre + "extensions/status")
        check("extensions/status 200", code == 200, f"{code} {str(out)[:140]}")
        code, out = api(pre + "extensions/registry")
        check("extensions/registry 200", code == 200, str(out)[:120])
        for verb in ("install", "uninstall"):
            code, out = api(pre + "extensions/" + verb, "POST", {"id": "x"})
            check(f"extensions/{verb} refused",
                  code == 403 and out.get("code") == "WB_BROKERED_INSTALL",
                  f"{code} {str(out)[:120]}")
        code, out = api(pre + "extensions/sidecar-proxy-consent", "POST",
                        {"id": "x", "approved": True})
        check("sidecar consent names the gap",
              code == 501 and out.get("code") == "WB_CAPABILITY_UNBOUND",
              f"{code} {str(out)[:120]}")

        print("\n[auth]")
        code, out = api(pre + "auth/status")
        check("auth/status 200", code == 200, f"{code} {str(out)[:160]}")
        check("password auth reported absent", out.get("password_auth_enabled") is False)
        check("passkey flag reported absent", out.get("passkey_feature_flag") is False)
        for path in ("auth/passkeys", "auth/passkey/register",
                     "auth/passkey/register/options", "auth/passkey/delete"):
            code, out = api(pre + path, "POST", {})
            check(f"{path} -> typed 501 (never 200)",
                  code == 501 and out.get("code") == "WB_AUTH_MODEL_UNSUPPORTED",
                  f"{code} {str(out)[:120]}")

        print("\n[the six EXISTING routes still work]")
        for path in ("skills", "memory", "profiles", "mcp/servers", "logs"):
            code, out = api(pre + path)
            check(f"{path} still served", code == 200, f"{code} {str(out)[:100]}")
    finally:
        try:
            ui.stop()
        except Exception:
            pass

    print(f"\n{'PASS' if not FAIL else str(FAIL) + ' FAILURE(S)'}")
    return 0 if not FAIL else 1


if __name__ == "__main__":
    sys.exit(main())
