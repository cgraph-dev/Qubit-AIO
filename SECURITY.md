# Security Policy

## Supported Versions

Security fixes are applied to the current `main` branch and the latest tagged release.

## Reporting a Vulnerability

Please do not open a public issue for a security vulnerability.

Use GitHub's **private vulnerability reporting** on this repository:
`Security` → `Advisories` → `Report a vulnerability`.

Include:

- affected version or commit
- reproduction steps
- impact and any known mitigations
- whether credentials or personal data may be exposed

Do not include live secrets, tokens, or personal data in the report. We will acknowledge
the report, investigate, and coordinate disclosure after a fix is available.

## Scope

This repository runs local agents with access to files, terminals, browsers, and optional
network providers. Reports involving privilege boundaries, secret handling, command
execution, authentication bypass, or data exfiltration are in scope.
