"""Settings, onboarding and updates: what Qubit HAS authority for, and what it refuses.

WHY THIS MODULE EXISTS
---------------------------------------------------------------------------
The ported client's settings / onboarding / updates panels call 29 paths that
``handlers_config`` and the original ``updates_check`` stub did not answer. They
fall into three kinds, and each one gets a different answer:

PORTED — a real Qubit authority answers, in the shape the call site reads:
  * ``GET  /api/model/auxiliary``  — the vendored ``_AUX_TASK_SLOTS`` over the
    live runtime config's ``auxiliary`` section (the same read the dashboard's
    Models page does).
  * ``POST /api/onboarding/probe`` — a bounded probe of an OpenAI-compatible
    ``{base_url}/models`` endpoint, using ONLY the key in the request (never a
    stored one). Qubit already makes exactly this call when it resolves a
    self-hosted provider, so this is not a new network lane.
  * ``POST /api/onboarding/setup`` — base_url + model, through the canonical
    ``providers.endpoints.select_model`` resolver. A key in the body is refused.
  * ``POST /api/onboarding/complete`` — validates readiness through the same
    preflight ``onboarding_status`` reads, then pin the vendor-AGNOSTIC "setup
    acknowledged" marker on the model entry. It never asserts a capability.
  * ``POST /api/onboarding/oauth/{start,poll,cancel}`` — adapters over the ONE
    device-code flow authority (``qubit_cli.web_routers.oauth``), reached
    in-process. Qubit had the capability; only the wire shape differed.
  * ``GET  /api/provider/cost-history`` — Qubit's OWN recorded spend
    (``sessions.estimated_cost_usd``/``actual_cost_usd``), per day.
  * ``GET  /api/system/health`` — host CPU/memory/disk from the existing
    ``h_system_stats`` probe, reshaped to the three metrics the panel paints.
  * ``GET  /api/gateway/status`` — ``gateway_supervisor.status`` (the
    messaging-gateway daemon), never "the web UI is up".
  * ``GET  /api/git-info`` — the confined repo status behind a session's
    workspace (``git_family``'s confinement, not a second one).
  * ``POST /api/updates/check`` — ``update_manager.check``: Qubit's REAL
    update path (its own release source), not the upstream product's feed.

REFUSED — the concept exists upstream but Qubit has no authority for it. Each
refusal names the real counterpart:
  * ``POST /api/providers/self-hosted``, ``POST /api/providers/delete`` (again
    here for this family) and any ``api_key`` in a setup body — Qubit stores key
    REFERENCES (``env:``/``file:``) and the secret itself in the keys store. It
    never writes a raw key to config.yaml and never hands one back.
  * ``POST /api/models/refresh`` — refreshing a provider's model list means
    asking that provider with its stored credential. Qubit's catalog comes from
    ``/api/models`` (configured inventory), and a live probe would have to move
    a secret out of the keys store into this listener. Not done.
  * ``GET  /api/provider/quota`` — no quota/credential-pool state is recorded
    anywhere in Qubit. Answering ``status: unavailable`` would be a real answer;
    answering usage numbers would be fabrication. It says exactly that.
  * ``POST /api/health/restart`` — restarting the messaging gateway is
    ``qubit gateway restart``, a confirm-gated operator action with its own
    capability grant. A browser POST skipping the gate is worse than a pointer.
  * ``/api/updates/{apply,force,clear_lock,summary}`` — Qubit's updater has a
    DIFFERENT authority surface (``POST /api/qubit/update``, e-stop + clean-tree
    + lock + snapshot). Those are not this listener's verbs, and the upstream
    ones would erase the safety envelope. Named, not faked.

THE ONE RULE THIS MODULE IS BUILT AROUND
---------------------------------------------------------------------------
Nothing here writes, echoes or stores a provider credential. Every refusal that
mentions one says WHY in ``detail`` so the panel can show a reason rather than a
blank failure.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from ..workbench_shim import UPSTREAM_PIN

#: How long a probe may spend on the provider's own ``/models`` endpoint. The
#: ported client debounces this at 400 ms (``_scheduleOnboardingProbe``), so a
#: timeout long enough to be useful still has to be short enough to answer
#: inside the next keystroke's patience.
PROBE_TIMEOUT_S = 5.0

#: Upper bound on probe models returned to the dropdown. The client renders
#: every one; a rogue endpoint answering thousands would freeze the panel.
PROBE_MAX_MODELS = 500


# ----------------------------------------------------------------- helpers
def _refuse(code: str, detail: str, *, endpoint: str,
            counterpart: str, status: int = 0) -> Tuple[int, Dict[str, Any]]:
    """The shared refusal shape, with a counterpart that points somewhere real."""
    return status or _status_for(code), {
        "ok": False,
        "state": "UNAVAILABLE",
        "code": code,
        "endpoint": endpoint,
        "detail": detail,
        "qubit_counterpart": counterpart,
        "owner": "qubit-policy",
        "rollback": "read-only; nothing to roll back",
        "upstream": UPSTREAM_PIN,
    }


def _status_for(code: str) -> int:
    import sys

    routes = sys.modules.get("voicebridge.workbench_ui_routes")
    table = getattr(routes, "_STATUS", None) if routes is not None else None
    if isinstance(table, dict) and code in table:
        return int(table[code])
    return {
        "WB_BAD_INPUT": 400,
        "WB_CREDENTIALS_NOT_IN_CONFIG": 403,
        "WB_READ_ONLY_SURFACE": 403,
        "WB_BROKERED_WRITE": 403,
        "WB_CAPABILITY_UNBOUND": 501,
        "WB_BACKEND_UNAVAILABLE": 503,
        "WB_NOT_FOUND": 404,
    # The onboarding OAuth flow is served by the vendored route, which runs under
    # Qubit's no-unrestricted-network scope: the device-code request is DENIED
    # there. That refusal is a 403 with the terminal command that does the same
    # login outside the listener — the guard is never bypassed to make it work.
    "WB_EGRESS_BLOCKED": 403,
    }.get(code, 502)


def _body(b: Any) -> Dict[str, Any]:
    return b if isinstance(b, dict) else {}


def _runtime_config(cfg: Any) -> Dict[str, Any]:
    """The live runtime config the vendored routes read (vendored path bound)."""
    from .. import qubit_compat as HC

    HC._vendored_runtime(cfg)
    from qubit_cli.config import load_config  # type: ignore

    data = load_config()
    return data if isinstance(data, dict) else {}


def _version() -> str:
    try:
        import voicebridge as _vb

        return str(getattr(_vb, "__version__", "") or "unknown")
    except Exception:  # noqa: BLE001
        return "unknown"


# ------------------------------------------------------------------ auxiliary
def model_auxiliary(cfg: Any, q: Dict[str, Any], b: Dict[str, Any],
                    p: Dict[str, Any]) -> Tuple[int, Any]:
    """``GET /api/model/auxiliary`` — the auxiliary-task pins, and nothing secret.

    The panel calls this on open (``panels.js:13727``) and the session rail calls
    it before a manual title regeneration (``sessions.js:727``). It reads
    ``{main: {provider, model}, tasks: [{task, provider, model, base_url, …}]}``
    and renders a provider select and a model select per task — no credential
    field is populated from this reply, and none is included: ``api_key_set`` is
    a boolean the plugin rows carry, so a row can say "a key is available"
    without saying which.
    """
    try:
        import inspect

        from .. import qubit_compat as HC

        code, out = HC.dispatch(cfg, "GET", "/api/model/auxiliary", query={},
                                body={})
        if inspect.isawaitable(out):
            out = {"state": "UNAVAILABLE", "detail":
                   "the auxiliary read answered asynchronously"}
            code = 503
    except Exception as exc:  # noqa: BLE001
        return _refuse("WB_BACKEND_UNAVAILABLE",
                       f"the auxiliary config raised {type(exc).__name__}: {exc}",
                       endpoint="model/auxiliary",
                       counterpart="Qubit Dashboard → Settings → Models")
    if code != 200 or not isinstance(out, dict):
        return _refuse("WB_BACKEND_UNAVAILABLE",
                       f"/api/model/auxiliary answered {code}: {out}",
                       endpoint="model/auxiliary",
                       counterpart="Qubit Dashboard → Settings → Models")
    tasks = out.get("tasks")
    main = out.get("main")
    return 200, {
        "ok": True,
        "main": main if isinstance(main, dict) else {},
        "tasks": [t for t in (tasks if isinstance(tasks, list) else [])
                  if isinstance(t, dict)],
        "source": "/api/model/auxiliary",
        "upstream": UPSTREAM_PIN,
    }


# ------------------------------------------------------------------- providers
def providers_self_hosted(cfg: Any, q: Dict[str, Any], b: Dict[str, Any],
                          p: Dict[str, Any]) -> Tuple[int, Any]:
    """``POST /api/providers/self-hosted`` — refused, and for a sharp reason.

    The panel posts ``{provider, base_url, model[, api_key]}``
    (``panels.js:12935``). Qubit's Keyless design: config.yaml carries an
    ``env:``/``file:`` REFERENCE, the secret lives in the keys store. Accepting
    this body would either persist a raw key into config.yaml or silently drop
    the key the user typed while reporting "configured" — both worse than a
    pointer. It is refused with the SAME code as the plain ``POST /api/providers``
    refusal so a client branch that inspects ``code`` sees one answer.
    """
    body = _body(b)
    had_key = bool(str(body.get("api_key") or "").strip())
    return _refuse(
        "WB_CREDENTIALS_NOT_IN_CONFIG",
        ("Qubit never stores a provider API key in config.yaml: keys live in the "
         "keys store and config carries only an env:/file: reference. "
         + ("This request carried an api_key, so nothing was written. "
            if had_key else "")
         + "Self-hosted endpoints are configured with base_url + model only, "
           "then the key is added through the keys store."),
        endpoint="providers/self-hosted",
        counterpart="Qubit Dashboard → Settings → Models → Add provider, or "
                    "`qubit config set providers.<name>.base_url …` + `qubit keys set`",
        status=403)


# -------------------------------------------------------- cost / quota / model
def provider_cost_history(cfg: Any, q: Dict[str, Any], b: Dict[str, Any],
                          p: Dict[str, Any]) -> Tuple[int, Any]:
    """``GET /api/provider/cost-history?provider=&days=`` — Qubit's own spend.

    The panel wants ``{snapshots: [{date, delta}], monthly_budget}`` for a 7-day
    bar chart + monthly pace (``panels.js:12314`` / ``12329``). The spend half is
    real and already recorded: ``sessions.estimated_cost_usd`` and
    ``actual_cost_usd`` are columns the dashboard's Analytics page sums. So the
    chart is answerable without inventing anything.

    ``delta`` is the day's spend as Qubit measured it (actual where recorded,
    estimated otherwise) — NOT a difference of two snapshots, because Qubit keeps
    per-session cost, not a per-provider running total, and differencing would
    produce a NEGATIVE day whenever a session is deleted.

    ``monthly_budget`` is null: Qubit has no budget field to read. The panel
    hides the budget controls when it is null, which is the honest rendering.
    """
    days = 7
    try:
        days = max(1, min(int(q.get("days") or 7), 90))
    except (TypeError, ValueError):
        days = 7
    want = str(q.get("provider") or "").strip().lower()
    try:
        from .. import qubit_compat as HC

        HC._vendored_runtime(cfg)
        from qubit_cli.config import get_config_path  # type: ignore
        import sqlite3

        home = Path(get_config_path()).parent
        db_path = home / "sessions.db"
        if not db_path.exists():
            return 200, {"ok": True, "provider": want or None, "snapshots": [],
                         "monthly_budget": None, "currency": "USD",
                         "detail": "no session database yet: nothing has been "
                                   "spent on this install",
                         "upstream": UPSTREAM_PIN}
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=2.0)
        try:
            rows = conn.execute(
                "SELECT date(started_at, 'unixepoch', 'localtime') AS day, "
                "       COALESCE(SUM(actual_cost_usd), 0) AS actual, "
                "       COALESCE(SUM(estimated_cost_usd), 0) AS estimated "
                "FROM sessions "
                "WHERE started_at > ? "
                "GROUP BY day ORDER BY day",
                (time.time() - days * 86400,)).fetchall()
        finally:
            conn.close()
    except Exception as exc:  # noqa: BLE001
        return _refuse("WB_BACKEND_UNAVAILABLE",
                       f"the session cost store raised {type(exc).__name__}: {exc}",
                       endpoint="provider/cost-history",
                       counterpart="Qubit Dashboard → Analytics")
    snaps: List[Dict[str, Any]] = []
    for day, actual, estimated in rows:
        # Actual outranks estimated for the same day (one is a bill, the other
        # an estimate); falling back keeps a day that only has estimates.
        spend = float(actual or 0) or float(estimated or 0)
        snaps.append({"date": str(day), "delta": round(spend, 6),
                      "actual": round(float(actual or 0), 6),
                      "estimated": round(float(estimated or 0), 6)})
    return 200, {
        "ok": True,
        "provider": want or None,
        "snapshots": snaps,
        "monthly_budget": None,
        "currency": "USD",
        "window_days": days,
        # The panel's "Not enough data yet" branch keys on a non-null delta, so
        # an empty window must produce no snapshot rows rather than zeroed ones.
        "detail": ("Qubit tracks cost PER SESSION (sessions table), not a "
                   "per-provider running total, and has no budget setting; the "
                   "chart shows Qubit's own recorded spend per local day."),
        "source": "sessions.db:estimated_cost_usd/actual_cost_usd",
        "upstream": UPSTREAM_PIN,
    }


def provider_quota(cfg: Any, q: Dict[str, Any], b: Dict[str, Any],
                   p: Dict[str, Any]) -> Tuple[int, Any]:
    """``GET /api/provider/quota`` — refused: Qubit records no quota state.

    The composer chip (``ui.js:4612``) and the providers panel
    (``panels.js:12009``) read ``status`` + ``quota{limit,usage,limit_remaining}``
    + ``account_limits{windows[]}``. Those come from the provider's own usage API
    called with the user's stored credential. Qubit has no credential-pool state
    and no quota cache, so there is nothing to read — and a number invented here
    would be a lie about somebody's bill.

    The refusal is deliberately ``WB_CAPABILITY_UNBOUND`` (501) rather than a
    200 with ``status: "unavailable"``: the latter is the answer for "the
    provider refused us", which would wrongly claim a credential exists.
    """
    return _refuse(
        "WB_CAPABILITY_UNBOUND",
        "Qubit records no provider quota or credential-pool state: the quota "
        "figures upstream shows come from a provider usage API called with a "
        "stored credential, and this listener neither holds one nor caches an "
        "answer. Recorded SPEND is available at GET /api/provider/cost-history; "
        "per-provider balances live with the provider.",
        endpoint="provider/quota",
        counterpart="Qubit Dashboard → Analytics (recorded spend), or the "
                    "provider's own usage page")


def models_refresh(cfg: Any, q: Dict[str, Any], b: Dict[str, Any],
                   p: Dict[str, Any]) -> Tuple[int, Any]:
    """``POST /api/models/refresh {provider}`` — refused, and it names why.

    The panel's per-provider refresh button (``panels.js:12983``) expects the
    server to re-ask the provider for its live model list. Doing that requires
    the provider's credential, which lives in the keys store and is deliberately
    not loaded into this process by the workbench. Rather than move a secret into
    this listener to satisfy a dropdown, the refusal points at the two real
    paths: the configured inventory (``/api/models``, already served) and the
    dashboard's own provider page.
    """
    prov = str(_body(b).get("provider") or q.get("provider") or "").strip()
    return _refuse(
        "WB_CREDENTIALS_NOT_IN_CONFIG",
        (f"refreshing {prov or 'a provider'}'s model catalog means calling that "
         "provider with its stored credential. The workbench does not load "
         "provider secrets, so it cannot and will not make that call. The "
         "configured inventory is already served by GET /api/models; a live "
         "re-read runs in the dashboard, which owns the credential."),
        endpoint="models/refresh",
        counterpart="Qubit Dashboard → Settings → Models (refresh there), or "
                    "`qubit models list` in the terminal",
        status=403)


# ----------------------------------------------------------------- onboarding
def onboarding_probe(cfg: Any, q: Dict[str, Any], b: Dict[str, Any],
                     p: Dict[str, Any]) -> Tuple[int, Any]:
    """``POST /api/onboarding/probe {provider, base_url, api_key?}``.

    The wizard tests a self-hosted endpoint before letting the user continue
    (``onboarding.js:40``): it POSTs the base URL and, if the user typed one, a
    key that has NOT been stored yet, and reads ``{ok, models: [{id, label}]}``.

    Two hard rules, both load-bearing:

    1. The key used is the one IN THE REQUEST, held in memory for the duration
       of one HTTP call. No stored credential is ever read, no key is written,
       echoed back, or logged. A client that omits ``api_key`` still probes —
       many self-hosted servers need none.
    2. Only ``{base_url}/models`` is called, over a bounded timeout, and the
       reply is reduced to ``id``/``label`` — the provider's own response body is
       never returned, so nothing that endpoint prints can leak back out.

    A failure is the answer the wizard renders ("unreachable" + a detail), not a
    refusal code: the client's own error branch keys on ``res.ok === false``.
    """
    body = _body(b)
    base_url = str(body.get("base_url") or "").strip()
    api_key = str(body.get("api_key") or "").strip()
    provider = str(body.get("provider") or "").strip()
    if not base_url:
        return _refuse("WB_BAD_INPUT", "probe needs a base_url",
                       endpoint="onboarding/probe",
                       counterpart="Qubit Dashboard → Settings → Models",
                       status=400)
    url = base_url.rstrip("/") + "/models"
    headers = {"Accept": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    try:
        import urllib.error
        import urllib.request

        req = urllib.request.Request(url, headers=headers, method="GET")
        with urllib.request.urlopen(req, timeout=PROBE_TIMEOUT_S) as resp:
            raw = resp.read(2 * 1024 * 1024)
        payload = json.loads(raw.decode("utf-8", "replace"))
    except urllib.error.HTTPError as exc:
        return 200, {"ok": False, "provider": provider or None, "models": [],
                     "error": f"HTTP {exc.code}",
                     "detail": "the endpoint answered but rejected the probe "
                               "(check the base URL and, if it needs one, the key)",
                     "upstream": UPSTREAM_PIN}
    except Exception as exc:  # noqa: BLE001 - unreachable is the wizard's answer
        return 200, {"ok": False, "provider": provider or None, "models": [],
                     "error": "unreachable",
                     "detail": f"{type(exc).__name__}: {str(exc)[:200]}",
                     "upstream": UPSTREAM_PIN}
    rows: List[Dict[str, str]] = []
    data = payload.get("data") if isinstance(payload, dict) else None
    for item in (data if isinstance(data, list) else []):
        mid = ""
        if isinstance(item, dict):
            mid = str(item.get("id") or item.get("name") or "").strip()
        elif isinstance(item, str):
            mid = item.strip()
        if mid:
            rows.append({"id": mid, "label": mid})
        if len(rows) >= PROBE_MAX_MODELS:
            break
    if not rows:
        return 200, {"ok": False, "provider": provider or None, "models": [],
                     "error": "no_models",
                     "detail": "the endpoint answered but listed no models at "
                               "/models",
                     "upstream": UPSTREAM_PIN}
    return 200, {"ok": True, "provider": provider or None, "models": rows,
                 "count": len(rows), "base_url": base_url,
                 "upstream": UPSTREAM_PIN}


def onboarding_setup(cfg: Any, q: Dict[str, Any], b: Dict[str, Any],
                     p: Dict[str, Any]) -> Tuple[int, Any]:
    """``POST /api/onboarding/setup {provider, model, base_url?, api_key?}``.

    Writes what Qubit can honestly write and refuses what it cannot:

    * ``base_url`` + ``model`` + ``provider`` go through the canonical
      ``providers.endpoints.select_model`` resolver — the SAME find-or-create and
      activate path the SPA's model picker and ``POST /api/default-model`` use.
      There is no second writer.
    * ``api_key`` in the body is a typed REFUSAL, not a silent drop: the wizard's
      key field exists, and pretending to accept it would tell the user their
      credential was saved when it was discarded. The refusal is returned with
      the model write NOT performed, so the caller learns immediately.

    The reply is the full ``onboarding_status`` record — the wizard assigns it to
    ``ONBOARDING.status`` and re-renders from it (``onboarding.js:502``).
    """
    body = _body(b)
    if str(body.get("api_key") or "").strip():
        return _refuse(
            "WB_CREDENTIALS_NOT_IN_CONFIG",
            "setup does not store an API key: Qubit keeps secrets in the keys "
            "store and config.yaml carries only an env:/file: reference. The "
            "provider/model choice was NOT written either, so nothing is half "
            "configured — save the key first, then re-run setup without it.",
            endpoint="onboarding/setup",
            counterpart="Qubit Dashboard → Settings → Models (or `qubit keys set`)",
            status=403)
    provider = str(body.get("provider") or "").strip()
    model = str(body.get("model") or "").strip()
    base_url = str(body.get("base_url") or "").strip()
    if provider and not model:
        return _refuse("WB_BAD_INPUT",
                       "setup needs both provider and model, or neither",
                       endpoint="onboarding/setup",
                       counterpart="Qubit Dashboard → Settings → Models",
                       status=400)
    if model:
        # ``select_model`` takes the bare model id; a caller that hands over the
        # composer's ``provider/model`` label would otherwise write a doubled id.
        bare = model.split("/", 1)[1] if "/" in model and provider and \
            model.startswith(provider + "/") else model
        try:
            from voicebridge.providers import endpoints as EP

            applied = EP.select_model(cfg, provider, bare, base_url=base_url)
        except Exception as exc:  # noqa: BLE001
            return _refuse("WB_BACKEND_UNAVAILABLE",
                           f"select_model raised {type(exc).__name__}: {exc}",
                           endpoint="onboarding/setup",
                           counterpart="Qubit Dashboard → Settings → Models")
        if isinstance(applied, dict) and applied.get("ok") is False:
            # The resolver's own refusal (unknown provider, confirm-required for
            # an expensive pick) is passed through — it knows the reason.
            return _status_for(str(applied.get("code") or "")), {
                **applied, "endpoint": "onboarding/setup"}
    from . import handlers_extra as HE

    code, status = HE.onboarding_status(cfg, q, {}, p)
    return code, status


#: The one key ``onboarding/complete`` pins. Vendor-agnostic on purpose: it
#: records that a human finished setup on this install, which is a real fact, and
#: says nothing about any other product's capability surface.
SETUP_ACK_KEY = "setup_acknowledged_at"


def _setup_marker_path(cfg: Any) -> Path:
    """Where the acknowledgement lives — beside the runtime config, not in it.

    A marker is not configuration: putting it in config.yaml would make every
    ``atomic_config_write`` rewrite the file to record "someone clicked Finish".
    """
    from .. import qubit_compat as HC

    HC._vendored_runtime(cfg)
    from qubit_cli.config import get_config_path  # type: ignore

    return Path(get_config_path()).parent / "workbench_setup.json"


def onboarding_complete(cfg: Any, q: Dict[str, Any], b: Dict[str, Any],
                        p: Dict[str, Any]) -> Tuple[int, Any]:
    """``POST /api/onboarding/complete`` — acknowledge setup, or say why not.

    ``onboarding.js:530`` and ``:548`` POST ``{}`` and then read ``completed``
    off the reply to hide the overlay. The upstream handler marks a
    product-specific "onboarding complete" flag in its settings; Qubit has no
    such flag, and inventing one that claims a capability Qubit lacks would be
    the fabrication this port exists to avoid.

    So it does the honest half: readiness is re-checked through the SAME
    preflight ``onboarding_status`` uses, and if a turn is genuinely possible the
    acknowledgement is stamped. If it is not, the POST is refused with the
    readiness detail and NOTHING is written — the wizard stays up, which is the
    true state of the box.
    """
    from . import handlers_extra as HE

    ready, why = HE._agent_ready(cfg)
    if not ready:
        return _refuse("WB_BACKEND_UNAVAILABLE",
                       f"setup is not complete: {why}. Nothing was written.",
                       endpoint="onboarding/complete",
                       counterpart="Qubit Dashboard → Settings → Models",
                       status=409)
    path = _setup_marker_path(cfg)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps({SETUP_ACK_KEY: time.time(),
                                   "detail": why}, indent=2),
                       encoding="utf-8")
        os.replace(tmp, path)
    except Exception as exc:  # noqa: BLE001
        return _refuse("WB_BACKEND_UNAVAILABLE",
                       f"could not record the acknowledgement: "
                       f"{type(exc).__name__}: {exc}",
                       endpoint="onboarding/complete",
                       counterpart="Qubit Dashboard → Settings")
    from . import handlers_extra as HE

    code, status = HE.onboarding_status(cfg, q, {}, p)
    if isinstance(status, dict):
        status = {**status, "acknowledged": True,
                  "acknowledged_at": time.time()}
    return code, status


# ---------------------------------------------------------------- onboarding oauth
def _oauth_call(method: str, path: str, *, body: Any = None,
                query: Optional[Dict[str, Any]] = None
                ) -> Tuple[int, Any]:
    """Run one request through the ONE in-process OAuth route authority.

    ``qubit_cli.web_routers.oauth`` owns the device-code sessions, the poller
    threads and the credential write. This is a transport hop into that app —
    not a second flow implementation — which is why every state change still
    happens in exactly one place.
    """
    from .. import qubit_asgi as HA

    # ``qubit_asgi.call`` takes BYTES: a dict content raises in httpx before the
    # request is ever built. The body here is empty for every flow verb (the
    # provider and session id ride the path), so this stays a plain hop.
    code, _headers, payload = HA.call(
        method, path,
        query={k: v for k, v in (query or {}).items() if v not in (None, "")},
        headers={"accept": "application/json"},
        body=b"" if body is None else json.dumps(body).encode("utf-8"))
    try:
        data = json.loads(bytes(payload).decode("utf-8"))
    except Exception:  # noqa: BLE001
        data = {"ok": False, "detail": "the oauth route returned no JSON"}
    return int(code), data


def onboarding_oauth_start(cfg: Any, q: Dict[str, Any], b: Dict[str, Any],
                           p: Dict[str, Any]) -> Tuple[int, Any]:
    """``POST /api/onboarding/oauth/start {provider}`` → the device-code offer.

    The wizard reads ``{flow_id, user_code, verification_uri,
    poll_interval_seconds}`` (``onboarding.js:694``/``:805``); Qubit's route
    answers ``{session_id, user_code, verification_url, poll_interval,
    expires_in}``. This maps the second onto the first — a pure rename, so the
    two surfaces agree on which flow is running and the CANCEL call can name the
    same session.

    THE EGRESS GUARD IS PART OF THE ANSWER. The device-code exchange calls the
    provider from inside the vendored app, which runs under Qubit's
    no-unrestricted-network scope: the request is DENIED (``EGRESS_DENIED``) and
    the route answers 500. That is the security model working, not a fault to
    route around — moving the call out of that scope to "make login work" would
    be a bypass. So the denial is mapped to an explicit ``egress_blocked``
    refusal naming the terminal command that performs the same login outside the
    listener.
    """
    provider = str(_body(b).get("provider") or "").strip()
    if not provider:
        return _refuse("WB_BAD_INPUT", "oauth start needs a provider",
                       endpoint="onboarding/oauth/start",
                       counterpart="Qubit Dashboard → Settings → Models",
                       status=400)
    code, data = _oauth_call("POST", f"/api/providers/oauth/{provider}/start")
    if code >= 400:
        return _oauth_start_failure(provider, code, data)
    if not isinstance(data, dict):
        return 502, {"ok": False, "detail": str(data)}
    sid = str(data.get("session_id") or "")
    return 200, {
        "ok": True,
        "flow_id": sid,
        "session_id": sid,
        "provider": provider,
        "user_code": data.get("user_code"),
        "verification_uri": data.get("verification_url"),
        "verification_url": data.get("verification_url"),
        "poll_interval_seconds": data.get("poll_interval") or 3,
        "expires_in": data.get("expires_in"),
        "upstream": UPSTREAM_PIN,
    }


def _oauth_start_failure(provider: str, code: int,
                         data: Any) -> Tuple[int, Any]:
    """Map a device-code start failure onto the client's own error branches.

    Three cases, three answers — none of them a 200:

    * the provider is not an OAuth-capable one (route 400): a bad request, passed
      through with the route's own detail;
    * the route refused the outbound call (``EGRESS_DENIED`` in the detail):
      ``WB_EGRESS_BLOCKED`` naming the terminal command that runs the same login;
    * anything else: the route's status and body, unchanged.
    """
    detail = str((data or {}).get("detail") if isinstance(data, dict) else data)
    if "EGRESS_DENIED" in detail:
        return 403, {
            "ok": False,
            "state": "UNAVAILABLE",
            "code": "WB_EGRESS_BLOCKED",
            "endpoint": "onboarding/oauth/start",
            "provider": provider,
            "detail": (
                "the in-process runtime may not make unrestricted outbound "
                "provider calls (Qubit's no-unrestricted-network scope denied "
                "the device-code request), so this listener cannot complete the "
                "login. That guard is the security model — it is not bypassed "
                f"here. Run the same login outside the listener: `qubit auth "
                f"{provider}`."),
            "qubit_counterpart": f"`qubit auth {provider}` in a terminal, or the "
                                 "Qubit Dashboard → Settings → Accounts card",
            "owner": "qubit-policy",
            "rollback": "read-only; nothing to roll back",
            "upstream": UPSTREAM_PIN,
        }
    return code, (data if isinstance(data, dict) else
                  {"ok": False, "detail": detail})


def onboarding_oauth_poll(cfg: Any, q: Dict[str, Any], b: Dict[str, Any],
                          p: Dict[str, Any]) -> Tuple[int, Any]:
    """``GET /api/onboarding/oauth/poll?flow_id=`` → one status reading.

    The wizard polls every 3 s and reads ``status`` ∈ pending|success|expired|
    cancelled|error (``onboarding.js:657``). Qubit's poll route needs the
    provider in the path, and this adapter recovers it from the session itself:
    the start reply told the client which provider it asked for, and the session
    records it, so the lookup is a read of state that already exists.

    An unknown/expired flow answers the wizard's own ``error`` branch with the
    route's 404 detail — never ``pending`` forever.
    """
    fid = str(q.get("flow_id") or _body(b).get("flow_id") or "").strip()
    provider = str(q.get("provider") or _body(b).get("provider") or "").strip()
    if not fid:
        return _refuse("WB_BAD_INPUT", "poll needs a flow_id",
                       endpoint="onboarding/oauth/poll",
                       counterpart="Qubit Dashboard → Settings → Models",
                       status=400)
    if not provider:
        # The session's own provider, read off the authority (no guessing).
        provider = _flow_provider(fid)
    if not provider:
        return 200, {"ok": False, "status": "error",
                     "error": "unknown flow", "flow_id": fid,
                     "detail": "no live login flow carries this id",
                     "upstream": UPSTREAM_PIN}
    code, data = _oauth_call(
        "GET", f"/api/providers/oauth/{provider}/poll/{fid}")
    if code == 404:
        return 200, {"ok": False, "status": "expired", "flow_id": fid,
                     "error": "expired",
                     "detail": str((data or {}).get("detail") or
                                   "session not found or expired"),
                     "upstream": UPSTREAM_PIN}
    if code >= 400 or not isinstance(data, dict):
        return code or 502, (data if isinstance(data, dict) else
                             {"ok": False, "detail": str(data)})
    state = str(data.get("status") or "").lower()
    if state in ("success", "completed", "done"):
        mapped = "success"
    elif state in ("cancelled", "canceled"):
        mapped = "cancelled"
    elif state in ("expired", "not_found"):
        mapped = "expired"
    elif state in ("error", "failed"):
        mapped = "error"
    else:
        mapped = "pending"
    return 200, {
        "ok": mapped == "success",
        "status": mapped,
        "flow_id": fid,
        "provider": provider,
        "error": (None if mapped in ("pending", "success")
                  else str(data.get("error_message") or mapped)),
        "detail": data.get("error_message"),
        "expires_at": data.get("expires_at"),
        "account_email": data.get("account_email"),
        "model": data.get("model"),
        "upstream": UPSTREAM_PIN,
    }


def _flow_provider(flow_id: str) -> str:
    """The provider a live device-code session belongs to, or ``""``."""
    try:
        from .. import qubit_compat as HC

        HC._vendored_runtime(None)
        from qubit_cli.web_routers import oauth as OS  # type: ignore

        lock = getattr(OS, "_oauth_sessions_lock", None)
        sessions = getattr(OS, "_oauth_sessions", None)
        if sessions is None:
            return ""
        if lock is not None:
            with lock:
                sess = sessions.get(flow_id)
                return str((sess or {}).get("provider") or "")
        sess = sessions.get(flow_id)
        return str((sess or {}).get("provider") or "")
    except Exception:  # noqa: BLE001
        return ""


def onboarding_oauth_cancel(cfg: Any, q: Dict[str, Any], b: Dict[str, Any],
                            p: Dict[str, Any]) -> Tuple[int, Any]:
    """``POST /api/onboarding/oauth/cancel {flow_id, provider?}``.

    Qubit's cancel marks the session ``cancelled`` BEFORE popping it, so a
    poller thread holding the dict stops instead of completing a login the user
    believes was aborted. That behaviour is exactly what this call must reach, so
    it is delegated whole. An unknown flow is reported as already gone, which is
    what the user asked for.
    """
    body = _body(b)
    fid = str(body.get("flow_id") or q.get("flow_id") or "").strip()
    provider = str(body.get("provider") or q.get("provider") or "").strip() \
        or _flow_provider(fid)
    if not fid:
        return _refuse("WB_BAD_INPUT", "cancel needs a flow_id",
                       endpoint="onboarding/oauth/cancel",
                       counterpart="Qubit Dashboard → Settings → Models",
                       status=400)
    if not provider:
        return 200, {"ok": True, "cancelled": False, "flow_id": fid,
                     "detail": "no live flow carries this id; nothing to cancel",
                     "upstream": UPSTREAM_PIN}
    code, data = _oauth_call(
        "DELETE", f"/api/providers/oauth/sessions/{fid}", query={})
    if code == 404:
        return 200, {"ok": True, "cancelled": False, "flow_id": fid,
                     "detail": "the flow had already ended",
                     "upstream": UPSTREAM_PIN}
    return code, (data if isinstance(data, dict) else
                  {"ok": code < 400, "flow_id": fid})


# ---------------------------------------------------------------- health / system
def system_health(cfg: Any, q: Dict[str, Any], b: Dict[str, Any],
                  p: Dict[str, Any]) -> Tuple[int, Any]:
    """``GET /api/system/health`` — host CPU / memory / disk for the panel.

    The insights panel paints three rows from ``{cpu, memory, disk}`` percent
    (``ui.js:14727``); each metric is an object or null. Qubit already probes the
    host in ``qubit_compat.h_system_stats`` (psutil when present, stdlib identity
    otherwise), so this reshapes that ONE measurement rather than adding a second
    sampler. A missing metric is ``null`` — the panel then renders 0 % and marks
    the panel, which is honest for a host without psutil.
    """
    try:
        import inspect

        from .. import qubit_compat as HC

        code, out = HC.dispatch(cfg, "GET", "/api/system/stats", query={},
                                body={})
        if inspect.isawaitable(out):
            out = None
            code = 503
    except Exception as exc:  # noqa: BLE001
        return _refuse("WB_BACKEND_UNAVAILABLE",
                       f"the host probe raised {type(exc).__name__}: {exc}",
                       endpoint="system/health",
                       counterpart="Qubit Dashboard → System")
    if code != 200 or not isinstance(out, dict):
        return 200, {"ok": False, "available": False,
                     "detail": "the host probe answered nothing",
                     "upstream": UPSTREAM_PIN}
    cpu = None
    if out.get("cpu_percent") is not None:
        cpu = {"percent": float(out["cpu_percent"]),
               "count": out.get("cpu_count")}
    memory = out.get("memory") if isinstance(out.get("memory"), dict) else None
    if memory is not None:
        memory = {"percent": memory.get("percent"),
                  "total": memory.get("total"), "used": memory.get("used"),
                  "available": memory.get("available")}
    disk = out.get("disk") if isinstance(out.get("disk"), dict) else None
    if disk is not None:
        disk = {"percent": disk.get("percent"), "total": disk.get("total"),
                "used": disk.get("used"), "free": disk.get("free")}
    missing = [n for n, v in (("cpu", cpu), ("memory", memory),
                              ("disk", disk)) if v is None]
    return 200, {
        "ok": True,
        "available": True,
        "status": "partial" if missing else "live",
        "cpu": cpu, "memory": memory, "disk": disk,
        "missing": missing,
        "psutil": bool(out.get("psutil")),
        "host": out.get("hostname"),
        "uptime_seconds": out.get("uptime_seconds"),
        "detail": ("host probes from /api/system/stats"
                   + (f"; unavailable here: {', '.join(missing)}"
                      if missing else "")),
        "upstream": UPSTREAM_PIN,
    }


def health_restart_bridge(cfg: Any, q: Dict[str, Any], b: Dict[str, Any],
                          p: Dict[str, Any]) -> Tuple[int, Any]:
    """``POST /api/health/restart`` — refused: a confirm-gated operator action.

    The banner's "Restart gateway" button (``ui.js:14363``) posts this and
    expects the daemon to come back. In Qubit that is
    ``qubit gateway restart``: ``gateway_supervisor.control`` journals a
    confirmation, checks the e-stop, requires the ``gateway.control`` grant and
    records an audit entry. A browser POST that restarted the messaging daemon
    without passing that gate would be a capability bypass — the exact thing
    this port refuses to build. The status the banner needs is still served
    (``GET /api/gateway/status``), so the button is the only thing that declines.
    """
    return _refuse(
        "WB_BROKERED_WRITE",
        "restarting the messaging gateway is a confirm-gated operator action: "
        "gateway_supervisor.control enforces the e-stop, the `gateway.control` "
        "capability grant and an audit record. This listener will not restart "
        "the daemon behind that gate. Status is still live at GET /api/gateway/status.",
        endpoint="health/restart",
        counterpart="`qubit gateway restart` (asks for confirmation), or the "
                    "Qubit Dashboard → System card",
        status=403)


# --------------------------------------------------------------------- gateway
def gateway_status(cfg: Any, q: Dict[str, Any], b: Dict[str, Any],
                   p: Dict[str, Any]) -> Tuple[int, Any]:
    """``GET /api/gateway/status`` — the messaging daemon's real state.

    Two call sites read it: the Cron panel's "scheduled jobs need a gateway"
    notice (``panels.js:1034``) and the Gateway card
    (``panels.js:14634``). They read ``{configured, running, platforms[{name,
    label}], last_active, session_count, lifecycle{managed, reason}}``.

    Every one of those is a fact ``gateway_supervisor.status`` already measures
    (systemd unit state + a bounded control-socket query). What is NOT
    answerable is ``lifecycle.managed``: that is upstream's "this WebUI owns the
    gateway CLI" proof. Qubit's own rule is the opposite (Owner Decision #16: the
    gateway is an operator daemon), so ``managed`` is honestly ``false`` with a
    reason — the card then shows its read-only note instead of offering
    start/stop buttons that would have to bypass the confirm gate.

    ``session_count`` is absent when the daemon publishes none: a zero would read
    as "no traffic", which is a different claim from "not measured".
    """
    try:
        from .. import gateway_supervisor as GS

        st = GS.status(cfg)
    except Exception as exc:  # noqa: BLE001
        return _refuse("WB_BACKEND_UNAVAILABLE",
                       f"the gateway supervisor raised {type(exc).__name__}: {exc}",
                       endpoint="gateway/status",
                       counterpart="`qubit gateway status`")
    if not isinstance(st, dict):
        return _refuse("WB_BACKEND_UNAVAILABLE",
                       f"gateway status returned {type(st).__name__}",
                       endpoint="gateway/status",
                       counterpart="`qubit gateway status`")
    daemon = st.get("daemon") if isinstance(st.get("daemon"), dict) else {}
    rows: List[Dict[str, Any]] = []
    raw = daemon.get("platforms_map") if isinstance(daemon, dict) else None
    if isinstance(raw, dict):
        for name in sorted(raw):
            info = raw[name] if isinstance(raw[name], dict) else {}
            rows.append({"name": str(name),
                         "label": str(name).replace("_", " ").title(),
                         "state": str(info.get("state") or "unknown")})
    running = bool(st.get("running") or st.get("daemon_live"))
    # "configured" is true when a unit exists OR the daemon answered: a box with
    # no unit installed is genuinely unconfigured, and the notice should say so.
    configured = bool(st.get("service_unit")) and (
        running or str(st.get("active_state") or "") not in ("", "unknown")
        or bool(daemon))
    out: Dict[str, Any] = {
        "ok": True,
        "configured": configured,
        "running": running,
        "platforms": rows,
        "platforms_available": bool(rows),
        "last_active": daemon.get("updated_at") or st.get("started_at"),
        "lifecycle": {
            "managed": False,
            "reason": "operator_daemon",
            "detail": ("Qubit's messaging gateway is an operator-owned daemon "
                       "(Owner Decision #16): it is started and restarted with "
                       "`qubit gateway start|restart`, not from the web UI."),
        },
        "state": st.get("state"),
        "health": st.get("health"),
        "healthy": st.get("healthy"),
        "pid": st.get("pid"),
        "host": st.get("host"),
        "port": st.get("port"),
        "detail": st.get("detail") or "",
        "source": "voicebridge.gateway_supervisor.status",
        "upstream": UPSTREAM_PIN,
    }
    count = daemon.get("session_count") if isinstance(daemon, dict) else None
    if isinstance(count, (int, float)):
        out["session_count"] = int(count)
    if not rows:
        out["detail"] = (out["detail"] or "") + (
            " The daemon has published no per-platform snapshot, so none is "
            "shown." if running else "")
    return 200, out


# --------------------------------------------------------------------- git-info
def git_info(cfg: Any, q: Dict[str, Any], b: Dict[str, Any],
             p: Dict[str, Any]) -> Tuple[int, Any]:
    """``GET /api/git-info?session_id=`` — the workspace badge's repo facts.

    The badge (``workspace.js:1140``) reads ``data.git.{is_git, branch, dirty,
    ahead, behind}`` and hides itself when the directory is not a repository.
    The repo it asks about is the one the SESSION is working in, which is exactly
    what ``git_family.status`` measures — through the workbench's existing
    confinement (registered roots only) rather than a second path check.

    WHERE THE WORKSPACE COMES FROM, in order: the session's own recorded
    workspace, then the turn's working directory, then the panel's current
    directory (``?path=``). Qubit's session row does not always carry a
    ``workspace`` key, so a panel that knows its directory passes it rather than
    the badge silently hiding on a repo that IS a repo.

    A session with no resolvable workspace, or a workspace outside the
    registered roots, answers ``{git: {is_git: false}}``: the badge's own
    "hide" branch, which is a true statement about what this surface may look at
    (the confinement refused; it did not find no repository).
    """
    sid = str(q.get("session_id") or _body(b).get("session_id") or "").strip()
    explicit = str(q.get("path") or _body(b).get("path") or "").strip()
    not_git = {"ok": True, "git": {"is_git": False},
               "upstream": UPSTREAM_PIN}
    workspace = explicit
    if not workspace and sid:
        try:
            from . import handlers_extra as HE

            store = HE._store(cfg)
            meta = store.meta_get(sid)
        except Exception:  # noqa: BLE001 - a badge must never raise into the page
            meta = {}
        if str((meta or {}).get("state")) != "OK":
            return 200, not_git
        fields = (meta.get("meta") or {}) if isinstance(meta, dict) else {}
        session = (meta.get("session") or {}) if isinstance(meta, dict) else {}
        for key in ("workspace", "cwd"):
            value = fields.get(key) if isinstance(fields, dict) else None
            value = value or session.get(key) if isinstance(session, dict) else value
            if value:
                workspace = str(value).strip()
                break
    if not workspace:
        return 200, {**not_git,
                     "detail": "neither the session nor the request names a "
                               "workspace to read a repository from"}
    try:
        from . import git_family as GF

        code, st = GF.status(cfg, {"path": workspace}, {}, p)
    except Exception:  # noqa: BLE001 - confinement refusal IS "not a repo here"
        return 200, not_git
    if int(code) != 200 or not isinstance(st, dict):
        return 200, {**not_git,
                     "detail": "the workspace is outside the registered roots, "
                               "so no repository is reported"}
    # ``git_family.status`` answers a NON-repo workspace as
    # ``{state: "GIT_NOT_A_REPO"}`` with a 200; that is the badge's "this is not
    # a repository" answer, not a repository with no branch.
    if str(st.get("state") or "") == "GIT_NOT_A_REPO":
        return 200, {**not_git,
                     "detail": st.get("detail") or "the workspace is not a git "
                                                   "work tree"}
    branch = st.get("branch")
    ahead, behind = int(st.get("ahead") or 0), int(st.get("behind") or 0)
    return 200, {
        "ok": True,
        "git": {
            "is_git": True,
            "branch": str(branch) if branch else None,
            "detached": bool(st.get("detached")),
            "dirty": int(st.get("changed") or 0),
            "ahead": ahead,
            "behind": behind,
            "staged": int(st.get("staged") or 0),
            "unstaged": int(st.get("unstaged") or 0),
            "untracked": int(st.get("untracked") or 0),
            "head": st.get("head"),
            "root": st.get("root"),
        },
        "workspace": workspace,
        "upstream": UPSTREAM_PIN,
    }


# --------------------------------------------------------------------- updates
def updates_check_q(cfg: Any, q: Dict[str, Any], b: Dict[str, Any],
                    p: Dict[str, Any]) -> Tuple[int, Any]:
    """``POST /api/updates/check`` — Qubit's OWN update source, mapped.

    The client reads ``{disabled, webui: {behind, channel, error}, agent: {...}}``
    (``panels.js:13278``). The check it drives is the real one —
    ``update_manager.check``, the same call the SPA System page and
    ``h_qubit_update_check`` make — so this reports Qubit's configured release
    source, not the upstream product's feed.

    The mapping is honest in two places:

    * ``webui`` is Qubit itself (the checkout this listener runs from);
      ``agent`` is the SAME install, reported for the client's shape rather than
      fabricated as a second product — ``detail`` says so.
    * ``behind`` is ``None`` when the check could not determine it (no remote,
      no git, a refused source). The client's ``behind > 0`` test is false for
      null, so it shows "no update" — and the ``error`` field carries the real
      reason instead of a fabricated "up to date".
    """
    force = bool(_body(b).get("force"))
    channel = str(_body(b).get("channel") or q.get("channel") or "").strip()
    try:
        from .. import update_manager as UM

        kwargs: Dict[str, Any] = {}
        if channel:
            kwargs["channel"] = channel
        rep = UM.check(cfg, **kwargs) if not force else UM.check(cfg, **kwargs)
    except Exception as exc:  # noqa: BLE001
        return _refuse("WB_BACKEND_UNAVAILABLE",
                       f"the update check raised {type(exc).__name__}: {exc}",
                       endpoint="updates/check",
                       counterpart="Qubit Dashboard → System → Update Qubit")
    if not isinstance(rep, dict):
        return _refuse("WB_BACKEND_UNAVAILABLE",
                       "update_manager.check returned no report",
                       endpoint="updates/check",
                       counterpart="Qubit Dashboard → System → Update Qubit")
    state = str(rep.get("state") or "")
    behind = rep.get("behind")
    error = ""
    if state not in ("OK", ""):
        error = str(rep.get("message") or rep.get("detail") or state)
    target = {
        "behind": int(behind) if isinstance(behind, int) else None,
        "channel": channel or rep.get("channel") or rep.get("branch"),
        "current": rep.get("current_version"),
        "latest": rep.get("remote_revision"),
        "revision": rep.get("current_revision"),
        "install_method": rep.get("install_method"),
        "error": error or None,
        "state": state or "OK",
    }
    return 200, {
        "ok": True,
        "disabled": False,
        "update_available": bool(rep.get("update_available")),
        "webui": dict(target),
        "agent": dict(target, detail="the agent runs from the same Qubit "
                                     "checkout, so it reports the same target"),
        "current": rep.get("current_version"),
        "can_apply": bool(rep.get("can_apply")),
        "update_command": rep.get("update_command"),
        "message": rep.get("message"),
        "detail": ("this is Qubit's own update source "
                   "(voicebridge.update_manager.check); the upstream product's "
                   "release feed is not consulted"),
        "qubit_counterpart": "POST /api/qubit/update (confirm-gated apply)",
        "upstream": UPSTREAM_PIN,
    }


def updates_apply_bridge(cfg: Any, q: Dict[str, Any], b: Dict[str, Any],
                         p: Dict[str, Any]) -> Tuple[int, Any]:
    """``POST /api/updates/{apply,force,clear_lock,summary}`` — refused, named.

    These are four different verbs on upstream's updater and NONE of them has a
    Qubit counterpart at this path:

    * ``apply``/``force`` run the update. Qubit's is ``POST /api/qubit/update``
      behind ``confirm: true``, an e-stop check, a clean-worktree check, the
      update lock and a snapshot first. Answering ``ok: true`` here without that
      envelope would claim a change that never happened; answering it by
      running the pull would bypass every guard.
    * ``clear_lock`` deletes an updater lockfile. Qubit's lock is
      ``update_lock`` — held by a live process, not a file to unlink.
    * ``summary`` asks an LLM to summarise somebody else's release notes. There
      are no such notes, so there is nothing to summarise.

    The version check — the one read the panel can genuinely do — IS served by
    ``POST /api/updates/check``.
    """
    action = str((p or {}).get("action") or "").strip().lower() or "apply"
    return _refuse(
        "WB_CAPABILITY_UNBOUND",
        (f"`{action}` is upstream's updater verb and has no authority here. "
         "Qubit updates through `POST /api/qubit/update` (confirm-gated: e-stop, "
         "clean worktree, update lock, snapshot, then `git pull --ff-only`), and "
         "its lock is held by a live process rather than a file to delete. The "
         "version check itself is served by POST /api/updates/check."),
        endpoint=f"updates/{action}",
        counterpart="Qubit Dashboard → System → Update Qubit, or `qubit update`",
        status=501)


HANDLERS = {
    "model_auxiliary": model_auxiliary,
    "providers_self_hosted": providers_self_hosted,
    "provider_cost_history": provider_cost_history,
    "provider_quota": provider_quota,
    "models_refresh": models_refresh,
    "onboarding_probe": onboarding_probe,
    "onboarding_setup": onboarding_setup,
    "onboarding_complete": onboarding_complete,
    "onboarding_oauth_start": onboarding_oauth_start,
    "onboarding_oauth_poll": onboarding_oauth_poll,
    "onboarding_oauth_cancel": onboarding_oauth_cancel,
    "system_health": system_health,
    "health_restart_bridge": health_restart_bridge,
    "gateway_status": gateway_status,
    "git_info": git_info,
    "updates_check_q": updates_check_q,
    "updates_apply_bridge": updates_apply_bridge,
}
