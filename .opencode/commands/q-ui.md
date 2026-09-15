---
description: Verify a Qubit dashboard area in a real browser
agent: qubit-ui-verifier
subagent: true
---

Verify `$ARGUMENTS` with the Playwright MCP server against the running Qubit dashboard.

Exercise navigation and every visible control. Check backend requests, state changes,
loading, empty, error, responsive, accessibility, and branding states. A page load
alone is not evidence of a working feature. Do not edit files; report exact failures
with route, control, request, and console evidence.
