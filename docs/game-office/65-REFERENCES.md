# 65 — Reference Synthesis

Each source was read for structure, not for code. Licenses differ and matter (`80-RISKS.md`).

## What each one contributed

| Source | What it is | What we take | What we discard |
|---|---|---|---|
| **starnet** (MIT code, proprietary brand) | Local-first harness; pixel station where the layout *is* runtime state | The core law ("the interface must never assert state it cannot prove"); *a room is a team, a hallway is a handoff lane, an object is a capability grant*; Task Brief (one question, options) for ambiguity; OUTBOX for deliverables; Night Shift with a leash; real ledgers on disk | Its station metaphor and its 3D-ish iso station art; its own harness (we already have a better one) |
| **pepeclaw** (MIT code, brand reserved) | Office viewer; **the live renderer is 2D isometric Phaser** (`src/phaser/`), with a Three.js wrapper beside it | **The whole iso foundation, reused under MIT** (D11): diamond room polygons + walls, L-shaped walkways, drag-pan/wheel-zoom camera with click-select and double-click-follow, minimap, per-room agent slot offsets, and procedural agent sprites (legs/torso/arms/head from Graphics, plus desk/typing-dots/speech-bubble overlays). Also the floors-as-rooms framing | Its own layout editor implementation (D12 now puts an editor *in scope*, but ours is staged-gated and capability-aware — see `75-EDITOR.md`, which borrows starnet's BUILD-mode shape instead); demo-data fallback (D14 forbids it); its OpenClaw gateway bridge; its 8 themed room names |
| **pixel-agents** (MIT) | Pixel office for coding agents; layout editor, areas, speech bubbles | **Areas**: paint named regions on the floor and map folders/teams to them — this is the room↔team binding; speech-bubble = waiting-for-input; sub-agents as separate characters; "offices you open like save files" | The VS Code extension form; its Claude-Code hook provider (our runtime is not Claude Code) |
| **Agent-Quest** | Fantasy village dashboard; each agent is a hero, each building is a tool | **The single best idea in any of them**: the building an agent walks to *is* what it is doing (Read→Library, Edit→Forge, Bash→Arena). Our version: the room's `kind` comes from the tool being used. Plus the integrated tile editor and CC0 asset bundling practice | Fantasy theme; session-log scraping as the data source |
| **aphae** (all rights reserved) | Godot office sim; personalities, needs, relationships, confessional, drama director | The **director** pattern: a separate component that decides what is interesting and surfaces it (we use it for the CEO's weekly review, not for drama); relationship web (useful for inter-agent collaboration history); memory-with-emotional-metadata (we use a plain score instead) | Needs/drama/romance systems; the reality-show framing; **its code** (license) |
| **ai-town** (MIT) | Generative-agents starter; Convex backend, PixiJS front, vector memory | The **architecture split**: simulation engine + shared global state + vector search as three clean layers. Also: the "world pauses when nothing is watching" idea — our version is *no events, no animation* | Convex (a cloud dependency; ours is local-first); its chat/socialize focus over work |
| **agent-monitor** (MIT) | Dashboard for OpenClaw: cards, activity feed, **boss chat**, autowork ticker | **Boss chat** (broadcast + per-agent direct chat) and **autowork** on a ticker with per-session policy | Its OpenClaw gateway coupling (we have our own ops broker) |

## The synthesis — what none of them is

Every reference is either **a viewer** (renders agents, doesn't run a company) or **a harness**
(runs agents, doesn't model an organization). None has:

- a **real P&L** where an agent's survival depends on revenue it actually earned;
- **floors as departments** with a charter, a manager, and kill criteria;
- a **ratchet** that turns measured outcomes into permanent agent improvement;
- **capability grants per seat type** as the org's permission model;
- an **append-only ledger** as the company's memory of itself.

Our synthesis is those five things. The visuals are borrowed taste; the economics are new.

## The one-line pitch

**Every other project draws agents working. This one runs a company and draws it running.**
