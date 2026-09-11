# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Users

The primary user is the local operator using a voice assistant from a desktop
browser or a phone-sized browser window. They need to speak or type tasks,
understand whether the bridge is ready, review activity and reports, manage
permissions, and pair a browser safely.

## Product Purpose

Qubit Voice Dashboard is a local voice-assistant control surface. It connects
the operator to the bridge's existing speech, response, activity, report,
permission, and pairing workflows. Success means the operator can understand
the current state and complete the next safe action without hunting across
separate pages.

## Positioning

The product is a local-first voice workspace: speech, status, approvals,
reports, and configuration are visible through one authenticated bridge rather
than a cloud-only assistant shell.

## Operating Context

The dashboard is served by the existing Python web UI on the local bridge. It
must work on a desktop and narrow mobile viewport, retain authenticated API
requests, and remain useful when the bridge, microphone, or pairing is
unavailable.

## Capabilities and Constraints

- Preserve the existing routes and API contracts for Talk, Activity, Reports,
  Approvals, Settings, and token setup.
- Preserve the existing voice/audio providers, model configuration, service,
  permissions, and Packet 8H qualification behavior.
- Use static HTML/CSS/JavaScript already served by the repository frontend.
- Do not invent production metrics, claims, or new backend behavior.

## Brand Commitments

The product name is Qubit Voice Agent / Voice Dashboard. The requested visual
constraint is a black-first interface with clear, professional controls and
plain-language status and recovery copy.

## Evidence on Hand

Existing dashboard routes, API handlers, and local-first audio/voice workflows
are the source of truth. No additional marketing proof, customer claims, or
illustrative production metrics were supplied.

## Product Principles

- Make the next safe action obvious.
- Keep live state visible without overwhelming the operator.
- Preserve local control and explicit authentication.
- Prefer one consistent workflow over scattered controls.

## Accessibility & Inclusion

The interface must support keyboard focus, readable contrast, reduced motion,
touch-sized controls, and useful typed errors on narrow screens.
