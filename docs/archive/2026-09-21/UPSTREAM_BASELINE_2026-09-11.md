# Upstream baseline — pinned revisions (owner directive 2026-09-11)

Read-only discovery. No upstream repository was modified. Sources are cloned
read-only under `/tmp/opencode/pinned/`; revision resolution used
`git ls-remote`/`rev-parse` only.

## Method

- `git -C /tmp/opencode/pinned/<repo> remote -v` → canonical URLs.
- `git ls-remote --tags --refs origin` → all stable tag refs.
- `git ls-remote origin 'refs/tags/<tag>^{}'` → peeled commit hash.
- `git describe --tags` on the checked-out pin → current revision identity.
- Compare the checked-out pin against the highest version-sorted stable tag.

## Hermes (Qubit runtime foundation)

| Field | Value |
|---|---|
| Repository | https://github.com/NousResearch/hermes-agent |
| License | MIT License, Copyright (c) 2025 Nous Research |
| Latest stable tag | `v2026.9.7` |
| Latest stable commit | `2237be355906fbe6065ce1815711eee52b2d646e` |
| Vendored pin | `v2026.9.7` / `2237be35` (up to date) |
| Local fork root | `qubit_hermes/` |
| Provenance manifest | `docs/QUBIT_HERMES_VENDOR_MANIFEST.json` (12173 files, 0 patched) |

The vendored pin **is** the latest stable Hermes release as of the discovery
date; no re-vendor is pending.

## OpenClaw (capability source, behavioral adaptation only)

| Field | Value |
|---|---|
| Repository | https://github.com/openclaw/openclaw |
| Latest stable tag | `v2026.9.4` |
| Latest stable commit | `3a9d69db306cd7f081e06254cb89c4bcc14a7107` |
| Intermediate stable | `v2026.9.3` / `1391f7cd2d40ab5bbcf2f5f831d3a64f520e72d7` |
| Current audited pin | `v2026.9.2` / `3928bad9badfcb6c7d140530435e806fb8092190` |
| Delta | pin is **2 stable releases behind** (v2026.9.2 → v2026.9.4) |
| Reuse rule | behavioral adaptation only; no OpenClaw source copied (see `voicebridge/upstream/ATTRIBUTION.md`) |

### Pending work from this delta (NOT done)

The OpenClaw capability inventory in `docs/AUDIT_OPENCLAW_2026-09-11.md` was
taken at `v2026.9.2`. A re-audit at `v2026.9.4` (new commands, pages, APIs,
tools, plugins, workflows, providers, protocols) has **not** been performed;
until it is, OpenClaw coverage claims are anchored to `v2026.9.2`.

## Honest status

- Hermes: latest stable, vendored, manifest-verified (0 local patches).
- OpenClaw: audited pin is stale by two stable releases; re-audit outstanding.
- Complete Hermes capability parity, complete OpenClaw integration, final
  rebrand, and clean-install packaging of the fork are **not** complete.
