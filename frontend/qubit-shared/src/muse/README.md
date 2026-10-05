# Qubit shared surface layer (`@qubit/shared/muse`)

Framework-neutral logic + design tokens + reusable components, ported from the
pinned upstream mobile surface (`apps/mobile/src/`) ahead of the planned Qubit
mobile app. Exactly one source of geometry, so the React web console and a
future React Native app cannot drift.

## Layers

| File | Kind | Notes |
| --- | --- | --- |
| `conversation-queue.ts` | pure logic | `ConversationQueue` — behaviour-identical port. |
| `conversation-run.ts` | pure logic | `runConversationTurn` turn-running shape. |
| `date-time.ts` | pure logic | tz-aware `localDateTime` / `zonedInstant` / `isCompleteInstant`. |
| `date-labels.ts` | pure logic | `dateLabel` / `timeLabel` / `relativeDate`. |
| `browser-address.ts` | pure logic | `browserAddress` / `browserSite`. |
| `tokens.ts` | tokens | **Geometry source of truth** (radii, sizes, spacing, typography) + the `SurfaceColors` contract. |
| `web-colors.ts` | web | Maps `SurfaceColors` onto Qubit's dark-theme CSS vars. |
| `components.web.tsx` | web components | Button, IconButton, Card, Chip, Field, Empty, ErrorNotice, Sheet, CheckRow, SectionHeading, LinkRow. |
| `styles.rn-contract.ts` | RN contract | Same geometry as RN style objects for the future mobile app. |
| `mascot.ts` | placeholder | Empty mascot slot — no upstream asset ported. |

## Colours vs geometry (owner ruling)

The upstream surface ships a **light** palette. We keep the **Qubit dark
theme**. So `tokens.ts` owns the upstream *geometry* (radii 23 / 24 / 19 / 20 /
26, icon box 42 and 55, button min-height 42, input min-height 45, sheet
max-width 790 / wide 1050, paddings 20 / 16 / 17, font sizes and
letter-spacings), and colours come from `SurfaceColors`:

- **web** → `webSurfaceColors`, which returns `var(--background-base)`,
  `var(--color-card)`, `var(--color-foreground)`, `var(--color-primary)`, etc.
  — the tokens the dashboard `ThemeProvider` already emits. No component
  hardcodes an upstream light hex.
- **RN (later)** → the mobile app resolves the same `SurfaceColors` shape from
  its Qubit skin over JSON-RPC and feeds `rnStyles(colors)`.

## Tests

`conversation-queue.test.ts` + `pure-helpers.test.ts` run under the web app's
Vitest (`cd frontend/qubit-web && npx vitest run ../qubit-shared/src/muse`).
