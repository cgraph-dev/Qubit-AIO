---
name: visualize
description: "Create inline visuals for code and explanations, and author persistent Qubit dashboard widgets through the canonical show_widget operation."
version: "1.0.0"
kind: bundled-skill
source: qubit-bundled
capabilities: ["show_widget", "dashboard.widget", "kanban.board"]
---

# Visualize

Use the canonical `show_widget` operation when seeing or interacting with a
result helps the user reason about it: code architecture, execution traces,
performance, comparisons, simulations, UI mockups, and session dashboards. Use
prose, a Markdown table, or a Mermaid block when that already explains the
answer. A request to implement an application still needs changes in its
project; a widget can illustrate the design but does not complete the
implementation.

## Choose the surface

- **Code work and one-off explanations:** prefer an inline visual for the
  current turn. Show the actual owners, relationships, measured values, or
  states relevant to the question. Distinguish proposed designs and
  illustrative data from observed behavior. Several diagrams for a code task do
  not by themselves call for a dashboard.
- **Persistent dashboards:** select `pin: true` for an explicit dashboard
  request or for multiple non-code visualizations that belong together. Read
  the current board first and reuse its names and tabs.
- **Native reports:** prefer a plain report (`kind: report`) for dashboard text,
  metrics, tables, simple charts, and links. Reports do not render inline.
- **Custom widgets:** use `kind: html` for interactive HTML/SVG. Reuse an
  explicit `name` with `pin: true` to replace a pinned widget's content. A
  same-name update cannot change its content owner; inspect before converting
  HTML to a report.

Use only tools and presentation targets advertised for the current turn. On a
pinned-only surface, set `pin: true` and omit the inline target. If the tool is
unavailable, explain the limitation instead of inventing a render call.

## Author the content

Send the markup itself in the `widget_code` field. Use an HTML or SVG fragment,
optionally including `<style>`. Qubit supplies the document shell and theme.
Do not send a file path, a Markdown fence, a full HTML document, or another
application's visualization directive. `title` is host metadata: start with
useful content rather than repeating the title or drawing dashboard chrome.

Keep a useful initial state in the markup. Local selection, filtering,
parameter changes, and animation are allowed. Give the root a unique ID and
scope styles and selectors to it. Insert labels and external data with
`textContent` instead of interpolating untrusted strings into markup or
executable code.

Keep source compact. Aggregate large datasets rather than embedding them.
Configured limits are enforced by the canonical store; a widget over the limit
is a typed refusal, not a truncated success.

## Render contract

`show_widget` is read-only. It returns the redacted widget, an explicit
`trust` marker, and the sandbox the caller must use:

- `render.executable` is always `false`;
- `render.sandbox` is `inert-html` for markup widgets and `plain-text`
  otherwise;
- the widget is never rendered, executed, fetched, or mutated by the backend.

Scripts that fetch remote data, open websockets, load arbitrary remote scripts,
or call host APIs are not part of the Qubit widget contract. Qubit does not
assume an external CDN, icon set, or font: choose native SVG/CSS for simple
visuals, and retrieve any data with the agent's own tools and embed the needed
observations. There is no outbound network in the widget path.

## Composition and theme

For code explanations, emphasize the relationship being discussed: aligned
lanes for concurrent work, labeled edges for ownership and data flow, or shared
scales for performance comparisons. Keep source details that support the
conclusion; avoid decorative metrics or diagrams of unrelated modules.

For dashboards, make each widget useful on its own and keep related measures
comparable. Show data freshness, units, loading, empty, and error states where
they affect interpretation. Preserve the last successful data when a refresh
fails.

Fit the actual container, including narrow chat and resized dashboard columns.
Use fluid widths, wrapping controls, and stacked panels; reserve horizontal
scrolling for code, tables, or diagrams that require exact geometry. Size SVGs
from their container and keep labels readable instead of shrinking a desktop
layout. Include axes and units for plots, accessible names, visible control
labels, keyboard operation, and touch targets. Honor reduced-motion
preferences.

## Verify and deliver

Inspect the rendered result with the available browser or device tools: verify
that important controls work and that content fits the intended width and
theme. Fix reported inline-script syntax errors and call the tool again. A
`status: "pinned"` result means the widget is on the session dashboard; focus
its tab rather than navigating to a widget hosting URL.

Report any concrete visual or platform verification gap. Successful hosting
alone proves neither rendering nor data access.

Keep the final prose focused on the useful conclusion or remaining limitation,
without repeating the entire visual.
