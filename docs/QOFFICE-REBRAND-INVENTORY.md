# Qubit Office rebrand inventory — every `habblaud` reference

**Status:** generated inventory. This is a measurement, not a completion
claim and not a `VERIFIED` verdict. Regenerate it; do not hand-edit counts.

```bash
python3 scripts/qoffice_rebrand_inventory.py
```

Counts are case-insensitive occurrences of the literal token `habblaud`
(covers `Habblaud`, `habblaud`, `HABBLAUD_*`) in tracked/unignored files at the
time this doc was generated. The inventory doc and its generator are
excluded so their own prose does not inflate the numbers.

## Summary

| Category | Files | Occurrences |
|---|---:|---:|
| user-visible | 45 | 545 |
| public API | 3 | 269 |
| private code | 14 | 78 |
| tests | 39 | 326 |
| assets/bundle | 9 | 1313 |
| legal/provenance | 3 | 35 |
| generated | 3 | 163 |
| **Total** | **116** | **2729** |

Non-asset, non-test references (the first-wave rebrand scope): **1090**.

### How to read the categories

- **user-visible** — product docs, the Qubit Office shell, plugin
  manifest, document/PWA metadata. This is the lane's primary surface.
- **public API** — `voicebridge/qubit_compat.py` and `voicebridge/webui.py`
  route names, handler symbols and schema ids under
  `/api/office/habblaud/*`. Deliberately unchanged in this lane.
- **private code** — internal comments, tooling, build scripts, package
  metadata. No user reads these; renaming is deferred.
- **tests** — assertions and fixtures that pin the compat surface.
- **assets/bundle** — the vendored client's hashed JS/CSS chunks, art and
  webfonts. Renaming bundle files or the vendored directory is a later
  phase with alias/redirect support.
- **legal/provenance** — MIT notice, `PROVENANCE.json`, upstream commit,
  `THIRD_PARTY_NOTICES.md`. The upstream name **must stay** here.
- **generated** — built artifacts (`qubit-office.game.js`, dist trees) and
  `docs/generated/` records.

## Per-file inventory

| Occurrences | Category | File |
|---:|---|---|
| 404 | assets/bundle | `frontend/qubit-app/plugins/qubit-office/habblaud/bundle/en-2E7tqGEw.js` |
| 392 | assets/bundle | `frontend/qubit-app/plugins/qubit-office/habblaud/bundle/es-BlnHV64J.js` |
| 392 | assets/bundle | `frontend/qubit-app/plugins/qubit-office/habblaud/bundle/fr-Bhd3CCOg.js` |
| 251 | public API | `voicebridge/qubit_compat.py` |
| 156 | user-visible | `docs/game-office/HABBLAUD-REBRAND-INVENTORY.md` |
| 90 | tests | `tests/test_office_habblaud_compat.py` |
| 85 | assets/bundle | `frontend/qubit-app/plugins/qubit-office/habblaud/bundle/app-0Gqa7aIn.js` |
| 78 | generated | `docs/generated/HERMES_SOURCE_COMPARISON.json` |
| 78 | generated | `docs/generated/HERMES_SOURCE_COMPARISON_FINISH_LINE.json` |
| 43 | user-visible | `docs/OFFICE-ACCEPTANCE-AND-RISK.md` |
| 35 | user-visible | `docs/STATUS.md` |
| 31 | user-visible | `docs/FLAKY-TEST-FINDINGS.md` |
| 29 | user-visible | `docs/superpowers/plans/2026-11-09-office-ui-theme-upgrade.md` |
| 27 | user-visible | `docs/game-office/HABBLAUD-IFRAME-ISOLATION.md` |
| 27 | tests | `tests/test_office_framing_policy.py` |
| 26 | private code | `scripts/vendor_habblaud.py` |
| 24 | assets/bundle | `frontend/qubit-app/plugins/qubit-office/habblaud/bundle/app-BW61bUik.css` |
| 23 | legal/provenance | `docs/game-office/HABBLAUD-PROVENANCE.md` |
| 22 | user-visible | `docs/OFFICE-ACCEPTANCE-CLOSEOUT.md` |
| 22 | user-visible | `docs/OFFICE-DOC-RECONCILIATION.md` |
| 20 | user-visible | `docs/QUBIT-OFFICE-MASTER-PLAN.md` |
| 17 | public API | `voicebridge/webui.py` |
| 16 | user-visible | `docs/OFFICE-ACCEPTANCE-VERIFIED-2026-10-16.md` |
| 16 | tests | `tests/test_office_app_auth.py` |
| 14 | tests | `frontend/qubit-app/plugins/qubit-office/game/app/api-bridge.test.ts` |
| 14 | tests | `tests/test_office_classic_gate.py` |
| 13 | user-visible | `docs/OPEN_THREADS.md` |
| 13 | user-visible | `docs/superpowers/plans/2026-11-09-office-habblaud-surfaces.md` |
| 13 | private code | `scripts/verify_habblaud_revendor.py` |
| 12 | tests | `tests/test_office_vendor_habblaud.py` |
| 12 | tests | `tests/test_office_vendor_stream_rewrite.py` |
| 11 | user-visible | `docs/OFFICE-SURFACE-SECURITY-AUDIT.md` |
| 11 | user-visible | `docs/OFFICE-UI-THEMES.md` |
| 10 | user-visible | `docs/BROWSER-PARITY-FINDINGS.md` |
| 10 | user-visible | `docs/DECISIONS.md` |
| 10 | tests | `tests/test_office_assets_vendored.py` |
| 10 | tests | `tests/tools/test_office_vendor_publish_concurrency.py` |
| 9 | tests | `frontend/qubit-app/plugins/qubit-office/game/app/agents-panel.test.ts` |
| 8 | user-visible | `docs/OFFICE-LIVE-VERIFICATION.md` |
| 8 | tests | `tests/test_office_game_shell.py` |
| 8 | tests | `tests/test_office_route_bindings.py` |
| 8 | tests | `tests/test_office_seat_model.py` |
| 7 | legal/provenance | `THIRD_PARTY_NOTICES.md` |
| 7 | user-visible | `docs/VENDOR-ATOMIC-PUBLISH-FIX.md` |
| 7 | assets/bundle | `frontend/qubit-app/plugins/qubit-office/habblaud/index.html` |
| 7 | generated | `frontend/qubit-app/plugins/qubit-office/qubit-office.game.js` |
| 7 | tests | `tests/test_office_vendor_reproducibility.py` |
| 7 | tests | `tests/test_qoffice_brand.py` |
| 7 | private code | `tools/office-acceptance/prove_doubled_url.py` |
| 6 | user-visible | `docs/VENDOR-REPRODUCIBILITY.md` |
| 6 | private code | `scripts/check_qoffice_brand.py` |
| 6 | tests | `tests/_probe_agents_panel_browser.py` |
| 6 | tests | `tests/test_office_stream_auth.py` |
| 5 | user-visible | `docs/FIREFOX-PARITY-FINDINGS.md` |
| 5 | user-visible | `docs/README.md` |
| 5 | legal/provenance | `frontend/qubit-app/plugins/qubit-office/habblaud/PROVENANCE.json` |
| 5 | tests | `tests/test_office_agents_panel.py` |
| 5 | tests | `tests/test_office_deployment_contract.py` |
| 5 | tests | `tests/test_office_full_integration.py` |
| 5 | tests | `tests/test_office_habblaud_server_contract.py` |
| 5 | private code | `voicebridge/office/theme.py` |
| 4 | user-visible | `docs/game-office/frontend-game/60-IMPLEMENTATION-PLAN.md` |
| 4 | user-visible | `frontend/qubit-app/plugins/qubit-office/game/app/api-bridge.ts` |
| 4 | user-visible | `frontend/qubit-app/plugins/qubit-office/game/app/seat-model-panel.ts` |
| 4 | assets/bundle | `frontend/qubit-app/plugins/qubit-office/habblaud/bundle/main-FPel1-E-.js` |
| 4 | private code | `scripts/office_theme_audit.py` |
| 4 | private code | `scripts/render_office_screenshot.py` |
| 4 | tests | `tests/test_dashboard_agent3_plugins.py` |
| 4 | tests | `tests/test_office_plugin_app.py` |
| 4 | tests | `tests/test_packaging_wave3.py` |
| 4 | private code | `tools/office-acceptance/cdp_eval.py` |
| 3 | tests | `frontend/qubit-app/plugins/qubit-office/game/app/OfficeAppShell.test.ts` |
| 3 | tests | `frontend/qubit-app/plugins/qubit-office/game/app/brand.test.ts` |
| 3 | user-visible | `frontend/qubit-app/plugins/qubit-office/game/app/brand.ts` |
| 3 | user-visible | `frontend/qubit-app/plugins/qubit-office/game/app/office-app.ts` |
| 3 | assets/bundle | `frontend/qubit-app/plugins/qubit-office/habblaud/manifest.webmanifest` |
| 3 | tests | `frontend/qubit-web/src/lib/office-theme.test.ts` |
| 3 | tests | `tests/test_dashboard_ui_parity_wave4.py` |
| 3 | tests | `tests/test_office_stream.py` |
| 3 | private code | `voicebridge/office_stream.py` |
| 2 | user-visible | `docs/OFFICE-GROWTH-REPORT.md` |
| 2 | user-visible | `docs/PARITY_PLAN.md` |
| 2 | user-visible | `docs/SHELL-TEST-RECONCILIATION.md` |
| 2 | user-visible | `docs/VENDOR-CONCURRENCY-EVIDENCE.md` |
| 2 | user-visible | `docs/evidence/a9-revendor-reproduction.json` |
| 2 | user-visible | `docs/game-office/frontend-game/README.md` |
| 2 | user-visible | `frontend/qubit-app/plugins/qubit-office/game/app/agents-panel.ts` |
| 2 | tests | `frontend/qubit-app/plugins/qubit-office/game/app/seat-model-panel.test.ts` |
| 2 | user-visible | `frontend/qubit-app/plugins/qubit-office/game/entry.ts` |
| 2 | assets/bundle | `frontend/qubit-app/plugins/qubit-office/habblaud/bundle/daystats-I5nXHlX6.js` |
| 2 | private code | `pyproject.toml` |
| 2 | tests | `tests/_probe_agents_panel_dom.py` |
| 2 | tests | `tests/test_office_theme_serve.py` |
| 2 | tests | `tests/test_office_tower_plugin.py` |
| 2 | tests | `tests/test_office_visual_regression.py` |
| 1 | user-visible | `docs/ATTRIBUTION-OFFICE.md` |
| 1 | user-visible | `docs/OFFICE-DEPLOYMENT-CONTRACT.md` |
| 1 | user-visible | `docs/OFFICE-INVARIANT-AUDIT.md` |
| 1 | user-visible | `docs/OFFICE-LIVE-STATE-VERIFICATION-SESSION2.md` |
| 1 | user-visible | `docs/REBRAND_INVENTORY.md` |
| 1 | user-visible | `docs/TEST-HONESTY-AUDIT.md` |
| 1 | user-visible | `docs/game-office/README.md` |
| 1 | user-visible | `frontend/qubit-app/plugins/qubit-office/game/app/AgentsPanel.tsx` |
| 1 | user-visible | `frontend/qubit-app/plugins/qubit-office/game/app/office-api.ts` |
| 1 | user-visible | `frontend/qubit-app/plugins/qubit-office/game/app/office-menu.ts` |
| 1 | user-visible | `frontend/qubit-app/plugins/qubit-office/game/art/iso.ts` |
| 1 | private code | `scripts/office_theme_build.py` |
| 1 | private code | `scripts/office_theme_extract.py` |
| 1 | public API | `scripts/public_tree_feature_parity.json` |
| 1 | tests | `tests/test_office_pages_security_contract.py` |
| 1 | tests | `tests/test_office_stream_notice_contract.py` |
| 1 | tests | `tests/test_office_theme.py` |
| 1 | tests | `tests/test_qbpv2_reach_deadcode.py` |
| 1 | tests | `tests/test_webui_security_hardening9u.py` |
| 1 | private code | `voicebridge/office/messages.py` |
| 1 | private code | `voicebridge/office/store.py` |

### Docs: what is product copy and what is provenance

Technical docs legitimately name the upstream component when they
document its vendored files, licence, upgrade procedure or `--check`
gate (for example `docs/OFFICE-UI-THEMES.md` says the theme emits a
Habblaud CSS variable). Those references are kept: replacing them would
make the provenance unreadable, and `docs/BRAND.md` allows naming
upstream provenance. Product-copy docs and every shipped UI surface
name **Qubit Office**; the guard checks the shipped surfaces, not
internal audit/status prose.

## Changed in this lane

| Surface | File | Change |
|---|---|---|
| Brand constants | `frontend/qubit-app/plugins/qubit-office/game/app/brand.ts` | New central module: name, shortName, title, description, logo path, LimeZu credit, MIT/legal label, upstream pin. |
| Iframe document title, aria-label, screen-reader copy | `.../game/app/OfficeAppShell.tsx` | Reads the brand module; credits surface gains the MIT legal label. |
| Capability note | `.../game/app/office-api.ts` | Removed the upstream name from a user-facing note string. |
| Document title + OG/Twitter/Safari labels | `.../habblaud/index.html` | `Qubit Office`; description says "your agents", not "Claude Code agents". |
| PWA manifest | `.../habblaud/manifest.webmanifest` | `name: Qubit Office`, `short_name: Office`. |
| Vendoring transform | `scripts/vendor_habblaud.py` | Brand labels are applied by the documented, idempotent `--retransform`; internal identifiers, paths and bundle filenames untouched. |
| Legal record | `THIRD_PARTY_NOTICES.md` | New Habblaud MIT entry with upstream commit and local changes; copyright holder unchanged. |
| Guard | `scripts/check_qoffice_brand.py`, `tests/test_qoffice_brand.py` | Fails if visible copy regains the upstream name or the MIT/provenance record disappears. |

## Deliberately left for the API/rename phase

1. `/api/office/habblaud/*` route aliases and `h_office_habblaud_*` handler
   names (public API category).
2. The vendored directory and hash-named bundle files (`assets/bundle`),
   plus `habblaud:locale` and other internal storage keys.
3. The vendored client's own runtime i18n strings: they are inside the
   hashed bundle and are renamed only with a real re-vendor/alias window.
4. Test assertions and fixtures that pin the compat surface.
5. `docs/game-office/HABBLAUD-*` provenance/isolation records and the
   vendored `LICENSE`/`PROVENANCE.json` (legal record, must stay).

## Legal/provenance that must remain

- `frontend/qubit-app/plugins/qubit-office/habblaud/LICENSE` —
  MIT, Copyright (c) 2026 Márcio Junior.
- `frontend/qubit-app/plugins/qubit-office/habblaud/PROVENANCE.json` —
  upstream `4ccce2537472fe5a8d432f06974494d2e778ff15`, local changes.
- `THIRD_PARTY_NOTICES.md` — Habblaud MIT entry and upstream commit.
- `docs/game-office/HABBLAUD-PROVENANCE.md` — vendoring rationale.

## Verify

```bash
python3 scripts/check_qoffice_brand.py
python3 scripts/vendor_habblaud.py --check
python3 -m pytest -q tests/test_qoffice_brand.py
```
