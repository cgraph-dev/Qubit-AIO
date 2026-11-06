# Jev (hermes-jev-skills) integration note — contract, provider facts, decision

Status: **integration PROCEEDED as an optional, fail-soft decision client**. The decisive
question is (d): Jev's contract is a bespoke typed-decision API, **not** OpenAI
chat-completions, so it cannot be routed through an OpenAI-compatible relay. It does,
however, reachable through one specific *non-compatible* endpoint on the OpenCode family —
and Qubit already holds that credential. The refusal part of the finding is recorded here so
no future agent "simplifies" this into a chat-completions bridge.

Upstream: `github.com/kerpopule/hermes-jev-skills`, pin `8316358bf60e98a21477582af829ef11c9cf52fb`,
MIT, Copyright (c) 2026 Steve Darlow.

---

## (a) What jevkit is, architecturally

**An HTTP client only. No local model, no weights, no inference of any kind.**

Evidence:

- `jevkit/client.py:23` — `ENDPOINT = "https://api.typesafe.ai/v1/systemone"`, and `ask()`
  is one `POST` of a JSON body (`_http_transport`, `client.py:316`).
- No weight files ship in the tree: `find . -name '*.gguf' -o -name '*.onnx' -o -name
  '*.safetensors' -o -name '*.pt'` returns nothing.
- `ladder.py:11-15` — the escalation ladder "knows nothing about Codex, Claude or any
  particular vendor — the fleet names the rungs and their probes". It is a cooldown file
  plus shell probes, i.e. config, not inference. `ladder.py` performs **no** HTTP to Jev at
  all; it only records which seats are cooling down.
- `decide.py:5-19` — "Code goes first… one yes/no question returns a probability". The
  arithmetic, thresholds, redaction and rules are local; only the probability comes from the
  service.
- `plan.py:1-12` is the one exception: a *separate* small text-model call (default
  `google/gemini-2.5-flash` on OpenRouter) that turns a goal into atomic steps. It is not
  Jev and is out of scope for this integration.

**Consequence for the swap question:** because jevkit is a pure client, the *transport* is
swappable in principle — but only to a host that speaks **Jev's** wire contract. Which hosts
qualify is (d).

## (b) Every distinct API operation and its request/response contract

jevkit performs exactly **one** Jev operation. Everything else (`route.py`, `search.py`,
`triage.py`, `mailbox.py`, `skillpick.py`, `compact.py`, `gate.py`, …) is a caller that builds
questions and interprets answers; they all funnel through `client.ask()`.

### Operation 1 — the decision call (`client.ask`, `client.py:610-729`)

Request — `POST {endpoint}` , `Content-Type: application/json`,
`Accept: application/json`, `Authorization: Bearer <key>` when a key exists:

```json
{
  "state": "<string or object or array, ≤ 60000 chars>",
  "model": "jev-latest" | "jev-1.13-free" | …,
  "questions": {
    "<name>": {"type": "noul",  "instructions": "<text|object|array>",
               "criteria": {"true": "…", "false": "…"}},          // criteria optional
    "<name>": {"type": "choice","instructions": "…",
               "criteria": {"opt": "what it means", …}},          // 2..255 options
    "<name>": {"type": "score", "instructions": "…",
               "criteria": ["level 0", … "level 9"]},             // 2..10 levels
    "<name>": {"type": "box",   "instructions": "…"}              // no criteria; images
  }
}
```

Image-bearing requests additionally send `assets` and the header
`TypeSafe-Danger-Zone-Images: true` (`client.py:671-678`, `images.py:5-19`).

Response — HTTP 200, JSON:

```json
{
  "model": "jev-1.13-free",
  "answers": {
    "<name>": {"type": "noul",   "noul": 0.99},
    "<name>": {"type": "choice", "choice": "billing", "confidence": 1.0,
               "probabilities": {"billing": 1.0, "tech": 0.0}},
    "<name>": {"type": "score",  "score": 2.0, "confidence": 0.9,
               "probabilities": {"0": 0.2, "1": 0.3, …}, "legend": {...}},
    "<name>": {"type": "box",    "box": {"left":…,"top":…,"right":…,"bottom":…},
               "confidence": 0.8}
  },
  "usage": {"input_tokens": 280, "output_tokens": 20}
}
```

The client **refuses** a reply it cannot fully validate — `JevError("malformed")` or
`JevError("invalid_response")`. The invariants enforced (`client.py:510-605`):

- the probability key set must **equal** the offered options (`_distribution`, 523-531);
- probabilities must sum to 1 ± 0.01 (`PROBABILITY_SUM_TOLERANCE`);
- the chosen option must be the argmax of its own distribution (`choice_is_argmax`);
- a score must equal the mean of its own two-decimal distribution ± a rounding band
  (`score_matches_its_distribution`).

Error codes the caller branches on (`client.py:347-351`, `460`): `auth_failed`,
`credits_exhausted`, `rate_limited`, `overloaded`, `network`, `http_5xx`, `timeout`,
`no_key`, `state_too_large`, `malformed`, `invalid_response`, `images_unsupported`.

### Operation 2 — key verification (`client.verify_key`, `client.py:762-772`)

Not a distinct endpoint: it is one tiny synthetic `ask()` ("The build finished and all tests
passed" / noul) whose *success* is the verification result. No new surface.

### Non-Jev operations elsewhere in jevkit (out of scope)

- `plan.py` — a chat-completions call to a text model. Already OpenAI-shaped, separate key,
  not part of the decision contract.
- `catalog.py` — reads model/price catalogs.
- `ladder.py` — file-backed cooldowns + shell probes.

## (c) Qubit's opencode-go provider facts (verified from code, not assumed)

| Fact | Value | Source |
|---|---|---|
| canonical id | `opencode-go` (aliases `go`, `opencode-go-sub`) | `qubit_cli/providers.py:57`, `:124` |
| overlay | `HermesOverlay(is_aggregator=True, base_url_env_var="OPENCODE_GO_BASE_URL")` | `qubit_cli/providers.py:57` |
| base URL | `https://opencode.ai/zen/go/v1` (+ `OPENCODE_GO_BASE_URL` override) | `qubit_cli/auth.py:234` |
| **key env var** | **`OPENCODE_GO_API_KEY`** | `qubit_cli/auth.py:234`; `qubit_cli/dump.py:239`; `qubit_cli/config_defaults.py:2904`; `qubit_cli/doctor_connectivity.py:84`; `plugins/model-providers/opencode-zen/__init__.py:126` |
| transport | `openai_chat` by default; per-model api_mode (`codex_responses` for `gpt-`/`grok-`/`muse-spark`, `anthropic_messages` for `minimax-`/`qwen`/`union-alpha`) | `qubit_cli/models.py:2639-2670` |

Resolution authority is `get_provider()` (`providers.py:282-313`), order: models.dev row →
`HERMES_OVERLAYS` → plugin `ProviderProfile` (`providers.py:316-338`). For `opencode-go` the
models.dev row exists, so the **models.dev api field wins** over the auth-table literal; the
two agree on host and on the `/zen/go/v1` path (`models.py:2665`, `_OPENCODE_FAMILY_PATHS`).
The `opencode-zen` plugin profile is the in-tree sibling and confirms the env var.

**Sibling that matters for this task.** The OpenCode *family* has two relays, and they are
different products:

- `opencode-go` → `https://opencode.ai/zen/go/v1` — the **paid `$10/mo` subscription relay**
  (`auth.py:233-234`), an OpenAI-compatible chat relay, 30 chat models, **no decision model**.
- `opencode` / `opencode-zen` → `https://opencode.ai/zen/v1` (`auth.py:232`) — the
  pay-as-you-go / free-tier relay, key `OPENCODE_ZEN_API_KEY`, which **also serves the Jev
  decision path**.

That distinction is the whole answer to (d) and (e).

## (d) Is the Jev contract OpenAI-compatible? — THE DECISIVE FINDING

**No. The Jev contract is bespoke and cannot be expressed as an OpenAI chat-completions
call.** Routing it through `opencode-go`'s OpenAI-compatible endpoint is physically
impossible, and opencode-go's model list does not contain any decision model at all.

Four independent pieces of evidence, all from live probes and code reads:

**d1 — the request/response bodies share nothing with chat-completions.** Above: the request
is `{state, model, questions}`, the response is `{answers: {…}, usage, model}`. Chat
completions is `{messages, model, …}` → `{choices:[{message:{content}}], usage}`. There is no
`messages`, no `choices`, no `content`. The reply carries *probability distributions plus
self-consistency invariants* (argmax, mass == 1, score == mean). A chat-completions response
cannot carry those, so `_check_answer` would reject every reply a relay produced.

**d2 — prompting a chat model for JSON is the exact anti-pattern upstream warns about.**
`client.py:26-29`:

> "Contributed as PR #1 by Lorenzo DZ (@Barba2k2), whose version prompted a chat model for
> JSON instead; that returns an LLM's guess with a made-up confidence, which is the one thing
> a decision model exists not to do."

That is a prior contributor's rejected shortcut inside this very repo. Rebuilding it in
Qubit would be re-landing a known-bad design, not absorbing the good one.

**d3 — opencode-go serves no decision model.** `GET https://opencode.ai/zen/go/v1/models`
returns 30 ids and none is Jev:

```
deepseek-v4.1-flash, deepseek-v4-pro, glm-5.2, glm-5.3, glm-5.3-flash, grok-4.7,
gpt-5.6-luna, gpt-6-luna, claude-haiku-5-5, hy3, hy4, hy4-preview, iceman-contributor-free,
kimi-k2.7-code, kimi-k3, kimi-k3.1-pro, longcat-2.0, step-5-preview, beam-free,
qwen3.8-max, qwen3.8-flash, qwen3.7-plus, mimo-v2.6-flash, mimo-v2.6-pro, minimax-m3,
minimax-m3.1-flash, muse-spark-1.2-contributor, muse-spark-1.3-contributor,
gemini-4-argon, crouton-alpha-free
```
`[i for i in ids if 'jev' in i.lower()] == []`. The `opencode-go` provider in Qubit is a chat
relay with zero jеv capability.

**d4 — posting a real Jev body to Go's Jev path is refused.** A valid Jev request to
`https://opencode.ai/zen/go/v1/systemone` answers:

```
HTTP 400 {"type":"error","error":{"type":"MissingSessionID","message":"Request is missing
x-opencode-session and cannot be routed efficiently. …"}}
```
and with a credential-shaped header:

```
HTTP 401 {"error":{"type":"server_error","message":"Upstream request failed: Go requires a
credential"}}   /   "Upstream request failed: Invalid credential"
```
Go answers the path but will not serve a decision there for a Go credential.

**Where it *does* work — and why this is not a new paid signup.** The same exact Jev body to
the **Zen** relay answers in the documented shape, with no credential and `cost: 0`:

```
POST https://opencode.ai/zen/v1/systemone
{"state":"ticket: refund my order","model":"jev-1.13-free",
 "questions":{"a":{"type":"noul","instructions":"This is a refund request"},
              "b":{"type":"choice","instructions":"Which queue",
                   "criteria":{"billing":"money questions","tech":"bugs"}}}}
→ HTTP 200
{"model":"jev-1.13-free",
 "answers":{"a":{"type":"noul","noul":0.98},
            "b":{"type":"choice","choice":"billing","confidence":1,"probabilities":{"billing":1,"tech":0}}},
 "usage":{"input_tokens":320,"output_tokens":47,"cost":0,
          "opencode":{"billing_source":"free","list_cost":0,…}}}
```

That is byte-for-byte the contract `_check_answer` validates — `type`, `probabilities` key
set equal to the offered options, mass 1.0, `choice` == argmax. (`jev-latest` on Zen answers
`Model is unavailable` 400, so the working free id is `jev-1.13-free`.)

Upstream already knows this door: `client.py:37-38` `ZEN_ENDPOINT =
"https://opencode.ai/zen/v1/systemone"`, `ZEN_MODEL = "jev-1.13-free"`, with the comment
"TypeSafe is not accepting new signups, so this is the door a new install can actually open."

**Net finding.** Jev is a bespoke typed-decision HTTP API. It is served by the OpenCode **Zen**
relay's `/v1/systemone` path, free tier included, under Qubit's existing `OPENCODE_ZEN_API_KEY`
— that is the same OpenCode account/subscription family the owner already uses, resolved
through the same registry. It is **not** served by `opencode-go`'s OpenAI-compatible
`/v1/chat/completions`, and no translation layer between the two contracts can exist without
fabricating confidences. So: do **not** bridge to chat-completions; **do** call the Zen
systemone path directly with the Zen key.

## (e) Decision and implementation plan

**Decision: integrate.** Wire jevkit's contract into Qubit as an optional decision client
under `qubit_runtime/plugins/jev/`, resolving the key through
`agent.secret_scope.get_secret` / the providers registry, and calling the real
`/v1/systemone` contract — Zen first (free tier), TypeSafe second (if the owner ever has a
key), and `unavailable` otherwise.

Explicitly **not** done: an "opencode-go chat-completions translation", a fake-confidence
prompt shim, or any stub that pretends to route decisions.

Ordered plan:

1. `PROVENANCE.json` — upstream repo, pin, source path, MIT + Steve Darlow notice, list of
   local changes.
2. `client.py` — a small stdlib transport over the real contract: `{state, model, questions}`
   in, `{answers,…}` out, with `_check_answer`-equivalent invariants (mass 1, key set,
   argmax, score-vs-distribution). Deliberately narrower than upstream: no images/`box`, no
   TypеSafe custom-endpoint/`JEV_PROXY_API_KEY` surface.
3. `providers` resolution — ordered candidates `zen` (`OPENCODE_ZEN_API_KEY` →
   `https://opencode.ai/zen/v1/systemone`, model `jev-1.13-free`) then `typesafe`
   (`TYPESAFE_API_KEY`); key read only via `agent.secret_scope.get_secret`, never
   `os.environ`. No key for any candidate → `unavailable`.
4. `__init__.py` `register(ctx)` — one tool, `jev_decide`, gated by a `check_fn` that reports
   unavailable when no candidate resolves. No hook, no core edit.
5. `plugin.yaml` + `__init__.py` header carry the MIT notice.
6. Prove fail-soft: with the key unset, the tool reports unavailable and the agent's tool set
   is otherwise unchanged; with a key present (or the free Zen path), one real decision call
   returns validated answers.

Optional must be optional: every entry point reports unavailable rather than raising, and no
decision path is reached unless a key resolves.
