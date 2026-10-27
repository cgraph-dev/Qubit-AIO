# D16 — vendored website rebrand (2026-10-27)

**Status:** docs/site-only rebrand applied on branch `feat/p8-d16`. Not `VERIFIED`.
No build, install, service restart, secret touch, or push.

- Worktree: `/tmp/opencode/wt-p8-d16`
- Branch: `feat/p8-d16`
- Base: `ddc4056ab` (`fix(cli): make agent roles/run and connectors status reachable from 'qubit'`)
- Scope: `qubit_runtime/website/**` plus this report only. Nothing outside the website tree changed.
- Owner approval: P8 **D16 = Rebrand** (this task). `docs/PHASE8-SIGNOFF-PACK.md` had said
  “do not rename in place”; that was the pre-approval recommendation. This pass follows the
  task's owner approval and changes only vendored site/docs source, not runtime code.

## Method

Three scripted passes over tracked files, then a manual review of every remaining `Hermes` /
`hermes` class:

1. **Masked substitution.** URLs, `HERMES_*` names, `${HERMES_*}` references, `/root/.hermes`,
   `.hermes.*` identifiers, `hermes-agent*` identifiers, `hermes://` links, `@hermes:` IDs,
   `r/hermes*` URLs, and the `Hermes 2/3/4` model family were masked before replacement, so a
   blanket regex never touched them.
2. **Product-name pass.** `Hermes Agent` → `Qubit`, possessive `Hermes'` → `Qubit's`,
   standalone product `Hermes` → `Qubit`, `~/.hermes` → `~/.qubit`.
3. **CLI/path pass.** Bare `hermes <subcommand>` in inline code and fenced blocks → `qubit
   <subcommand>`; legacy home paths in fence titles and Windows paths normalized; headings and
   internal anchors updated to the new slugs.

The model-name trap was handled explicitly: `Hermes 2`, `Hermes 3`, `Hermes 4`, `Hermes-4-70B`,
`Hermes 2/3`, the phrase “the lab behind Hermes, Nomos, and Psyche”, and the `Hermes, Teknium`
vocabulary hints were preserved. A first pass also rewrote `X-Hermes-*` headers and the Algolia
index name; both are external identifiers and were reverted (see Deviations).

## Counts

Commands used (run from the worktree; `HEAD` is the base tree):

```bash
git grep -l -i 'hermes' HEAD -- qubit_runtime/website | wc -l
git grep -o -i 'hermes' HEAD -- qubit_runtime/website | wc -l
git grep -c -i 'hermes' HEAD -- qubit_runtime/website | sed 's/.*://' | awk '{s+=$1} END{print s}'
git grep -l -i 'hermes' -- qubit_runtime/website | wc -l
git grep -o -i 'hermes' -- qubit_runtime/website | wc -l
git grep -c -i 'hermes' -- qubit_runtime/website | sed 's/.*://' | awk '{s+=$1} END{print s}'
```

| Measure | Before | After |
|---|---:|---:|
| Files containing any `hermes` (case-insensitive) | 477 | 302 |
| `hermes` occurrences (case-insensitive) | 14,643 | 3,850 |
| Matching lines | 12,318 | 3,311 |
| `Hermes Agent` occurrences | 521 | **64** |
| Capital `Hermes` occurrences | 5,042 | **527** |
| Lowercase `hermes` occurrences | 8,501 | **2,224** |
| `hermes-agent` occurrences | 802 | **783** |
| `~/.hermes` occurrences | 1,558 | **3** |
| `HERMES_` occurrences | 1,082 | **1,082** |
| `Nous Research` occurrences | 62 | **62** |
| `NousResearch` occurrences | 386 | **386** |

Almost all remaining occurrences are the deliberate exceptions below. `Hermes Agent` is now 62
community-quote occurrences in `userStories.json` + 2 upstream prompt exemplars. `~/.hermes` is 3
occurrences, all in `userStories.json`. `HERMES_`, `Nous Research`, and `NousResearch` are
unchanged.

## Files changed

**469 files**, 9,522 insertions / 9,522 deletions (line-for-line substitutions).

| Area | Files |
|---|---:|
| `docs/user-guide/**` | 335 |
| `docs/developer-guide/**` | 55 |
| `docs/guides/**` | 35 |
| `docs/reference/**` | 14 |
| `docs/getting-started/**` | 8 |
| `src/**` (React components/pages, CSS) | 7 |
| `scripts/**` (site generators, prose/output strings only) | 4 |
| `docs/integrations/**` | 4 |
| `static/**` (SVG text, OAuth client display name) | 3 |
| `sidebars.ts`, `docusaurus.config.ts` | 2 |
| `docs/user-stories.mdx`, `docs/index.mdx` | 2 |

## What was replaced

- `Hermes Agent` → `Qubit` (titles, descriptions, body prose, UI copy, nav/sidebar labels,
  footer/alt text, OAuth `client_name`).
- Standalone product `Hermes` → `Qubit` (e.g. “Hermes ships with…”, “Hermes Desktop”,
  “Hermes Cloud”, “Hermes gateway”).
- Possessives: `Hermes'` / `Hermes’` → `Qubit's` / `Qubit’s` (including `Qubit'ss` repair).
- `~/.hermes` → `~/.qubit`; `$HOME/.hermes` → `$HOME/.qubit`; `%LOCALAPPDATA%\hermes` and the
  literal Windows `AppData\Local\hermes` paths → `qubit` (same class as `~/.hermes`).
- Bare CLI tokens in code/examples: `hermes run`, `hermes config`, `hermes gateway`,
  `hermes update`, `hermes setup`, `hermes -p …`, `hermes --tui`, etc. → `qubit …`.
- Generated site strings: `qubit skills install …`, `qubit plugins install …`; sidebar labels;
  Docusaurus title/navbar/logo alt.
- Internal heading anchors that changed (e.g. `#hermes-home-and-profile-isolation` →
  `#qubit-home-and-profile-isolation`, `#hermes-serve` → `#qubit-serve`,
  `#connecting-hermes-desktop-to-a-remote-backend` →
  `#connecting-qubit-desktop-to-a-remote-backend`).

## Preserved exceptions (with examples)

1. **Community evidence / third-party content — `src/data/userStories.json` (unchanged).**
   62 `Hermes Agent`, 438 capital `Hermes`, 3 `~/.hermes`, and every Reddit/GitHub URL remain.
   Rewriting user quotes would fabricate testimonials; the URLs are provenance. Example:
   `"quote": "...Hermes handles the bulk of the actual implementation..."`,
   `"url": "https://www.reddit.com/r/hermesagent/..."`.
2. **Hermes model family (17 occurrences).** `Hermes 2`, `Hermes 3`, `Hermes 4`, `Hermes 2/3`,
   `Hermes-4-70B`, `Hermes-4-405B`, `Hermes-4.3-36B`, `Hermes 0.18`. Example:
   `run-hermes-with-nous-portal.md: "Don't pick Hermes-4 for agent work"` and
   `nous-portal.md: "A note on Hermes 4"`.
3. **Nous Research attribution / upstream prompt exemplars (62 `Nous Research`, 386
   `NousResearch`).** The two literal default-prompt lines were left intact as attribution:
   - `prompt-assembly.md:66: You are Hermes, an AI assistant created by Nous Research.`
   - `prompt-assembly.md:201: You are Hermes Agent, built by Nous Research...`
   (`personality.md` quotes the same fallback.) The phrase “the lab behind Hermes, Nomos, and
   Psyche” and the `Hermes, Teknium` transcription hints are preserved as model-vocabulary
   provenance. `NousResearch/hermes-agent` GitHub links and `hermes-agent.nousresearch.com` URLs
   are untouched throughout.
4. **Environment / API / container identifiers.**
   `HERMES_*` (1,082), `${HERMES_SKILL_DIR}`, `${HERMES_SESSION_ID}`, `/root/.hermes` (2),
   `X-Hermes-Session-Id` / `-Key` / `-Token` (24), `X-Hermes-Event` / `-Delivery` /
   `-Signature-256`, the `AI_AGENT=hermes-agent` value, `.hermes.md` / `.hermes-package.json`
   / `./.hermes/` project-config names, `hermes://` deep links (20), and the Algolia index name
   `hermes docs` (external service identifier) are preserved. Examples:
   `HERMES_HOME`, `HERMES_KANBAN_TASK`, `AI_AGENT` “Set to `hermes-agent`”, `X-Hermes-Session-Key`.
5. **Package / binary / path / Nix / unit identifiers (`hermes-agent`, 783).**
   `qubit-agent` is the real Python package; `hermes-agent` remains where it names the upstream
   package, binary, install directory, Nix flake/attribute, systemd unit, Docker image, or API
   model id rather than the product. Examples: `pip install hermes-agent`,
   `services.hermes-agent`, `programs.hermes-agent`, `hermes-agent.nixosModules.default`,
   `systemctl status hermes-agent`, `nousresearch/hermes-agent` (image), `"model":
   "hermes-agent"`, `~/.qubit/hermes-agent/` (install root), `hermes-agent=1` (container marker).
6. **Container user/install tree.** `/opt/hermes`, `/var/lib/hermes`, `/etc/hermes`, the
   `hermes` service user/group, and the `hermes` shim/binary references were preserved (27
   `/opt/hermes`, 13 `/var/lib/hermes`, 2 `/root/.hermes`). Example:
   `sudo -u hermes`, `chown hermes:hermes`, `/opt/hermes/.venv/bin/python`.
7. **License/copyright/attribution text.** Not rewritten; `Nous Research` and upstream links
   remain, as required by `docs/BRAND.md` §1–§3.

## Verification (run and observed)

```bash
cd /tmp/opencode/wt-p8-d16
git diff --check                  # CLEAN
python3 scripts/check_brand_half_rename.py
# brand half-rename check: OK (46 global name(s) consistent)   (exit 0)
```

Also checked:

- `git diff --name-only | grep -v '^qubit_runtime/website/'` → no files outside the website.
- Every changed `*.json` parses (`static/oauth/client-metadata.json`); every changed `*.py`
  compiles under `python3 -m py_compile`.
- Internal `hermes`-named anchors were enumerated and rewritten; a follow-up grep leaves only
  valid identifiers (`#hermes_write_safe_root`, `#a-note-on-hermes-4`, upstream
  `…cli-commands#hermes-egress`, `#hermes_home-survival-contract-…`).
- Final remaining-occurrence classes were reviewed by hand; the lists above are that review.

## Deviations / judgment calls

- **`hermes-agent` was not blanket-replaced.** It is overwhelmingly a package, binary, Nix,
  systemd, Docker, path, or API identifier (and 443 occurrences sit inside URLs/GitHub links).
  Only clear prose product references were rewritten (e.g. “hermes-agent codebase” → “Qubit
  codebase”, “exposes hermes-agent” → “exposes Qubit”). This follows the task's “never inside a
  URL / GitHub path / identifier” rule rather than producing `pip install qubit`-style breakage.
- **`userStories.json` is intentionally untouched** (community quotes and provenance URLs).
- **First-pass over-reach reverted:** `X-Qubit-Session-*` → restored to `X-Hermes-Session-*`
  (HTTP API headers) and Algolia `indexName` → restored to `hermes docs` (external index id).
- **Extra home-path normalization:** `$HOME/.hermes` and the Windows
  `%LOCALAPPDATA%\hermes` / `AppData\Local\hermes` spellings were changed with `~/.hermes`; they
  are the same user-facing path class.
- **Attribution lines were not rewritten.** Some now read “Qubit … built by Nous Research”;
  that is preserved legal/upstream attribution, not a missed product rename. If the owner wants
  Qubit-only identity copy there, that is a separate editorial decision.
- **The two upstream prompt exemplars keep “You are Hermes …, built by Nous Research.”** They
  document the default system prompt and its attribution; changing them would misstate upstream
  behaviour.

## Not verified / limitations

- **No Docusaurus build, `npm ci`, or browser run** (task forbids build/install); TypeScript/TSX
  edits are string/label only, but not compiled here.
- **No service restart and no push.**
- The anchor check used a local slug approximation, not Docusaurus's slugger; all
  `hermes`-derived anchors were enumerated and the known model/API anchors intentionally remain.
- `src/data/userStories.json` still displays upstream brand in quotes by design.
- Remaining `hermes` occurrences are the preserved classes above; the final grep is listed in
  the counts section so the next agent can re-run it. No claim of “zero hermes” is made.
