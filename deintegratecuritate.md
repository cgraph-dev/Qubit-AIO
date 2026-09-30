# Qubit Brain — Secure Personal Data and Credential Design

## Purpose

Qubit Brain is the private, per-user information layer that helps Qubit fill
approved forms, use approved accounts, remember preferences, and complete
ordinary workflows.

It is not a plain folder of passwords. It is an encrypted profile and vault
controlled by Qubit permissions.

## Multi-user and multi-account design

Qubit Brain must support multiple people, organizations, identities, email
addresses, phone numbers, and accounts without mixing their data.

The hierarchy is:

```text
Qubit Brain
├── owner profile
├── organizations
│   ├── company profile
│   ├── employees
│   │   ├── employee profile
│   │   └── employee account vault
│   └── shared non-secret records
├── personal profiles
├── account identities
└── isolated secret vaults
```

Each person or organization is a separate security principal. Each employee
may have multiple identities and accounts, for example:

- work name and personal name;
- work and personal email addresses;
- company and personal phone numbers;
- separate accounts on the same website;
- different billing, shipping, or office addresses;
- different provider API keys;
- different browser profiles or approved origins.

The user must be able to choose the exact identity before filling a form. A
company profile must never silently use a personal profile, and one employee
must never receive another employee’s secrets.

Company administration should manage memberships, roles, approved services,
and shared non-secret data—not read employee passwords, private email, tokens,
recovery codes, or personal profile fields unless separately authorized.

Employee access must support invitation, removal, suspension, rotation of
shared credentials, and immediate grant revocation. Removing an employee must
not delete the company’s audit records or another employee’s data.

## Data layout

The logical layout is:

```text
Qubit Brain
├── principals    owner, organizations, employees, and memberships
├── profiles      one profile per principal and identity
├── accounts      approved sites, account aliases, and scopes
├── vaults        isolated encrypted vault per principal or approved team
├── permissions   site, account, identity, action, and expiry grants
├── preferences   voice, model, and workflow preferences per principal
└── audit         redacted activity history with principal attribution
```

The logical layout does not require these values to be separate plaintext
files. Sensitive values should use the operating-system keyring or an
approved password manager.

## First-run onboarding

Every installation should offer a simple Qubit Brain setup wizard. It must
not force the user to provide personal data that is not needed.

The wizard should ask what the user wants to configure:

- personal profile;
- company or organization profile;
- employee or team membership;
- names and email addresses;
- phone numbers and addresses;
- approved accounts and origins;
- provider/API-key references;
- voice preferences and accessibility settings.

Each form must clearly mark fields as **required**, **optional**, or
**skippable**. The user can skip any optional section and complete it later
from the dashboard or CLI. Qubit must explain why a required field is needed
before requesting it.

The wizard must provide a preview before saving, allow correction, and never
place values in URLs, shell arguments, logs, screenshots, or analytics. Setup
must work offline for local profile data and must report unavailable provider,
audio, browser, or account steps without pretending they succeeded.

The setup flow must be available through the CLI, dashboard, and voice path,
using one backend-authoritative operations registry.

## Moving Qubit Brain to another PC

Qubit Brain must support safe portability between computers. Portability is an
encrypted export/import operation, not copying the project directory or home
folder.

The export wizard must let the user choose:

- personal profiles;
- company and employee profiles;
- selected accounts and origins;
- preferences;
- permissions and grants;
- reports and non-secret history;
- provider references without secret values;
- encrypted vault secrets, only when explicitly selected.

The export must:

- be encrypted before leaving the source PC;
- require explicit human confirmation;
- use a passphrase or approved hardware/keyring key;
- include schema version, source identity, creation time, and integrity hash;
- exclude raw secrets by default;
- exclude cookies, browser profiles, session databases, raw audio, and private
  page text by default;
- redact diagnostics and logs;
- never expose secrets in the filename, terminal, URL, or report.

The import wizard must:

- show a manifest and preview before changing anything;
- validate the archive, schema, signature/integrity, and destination;
- allow selective import by principal, identity, account, and data class;
- refuse path traversal, unsafe symlinks, corrupt archives, newer schemas, and
  conflicting identities unless the user resolves them explicitly;
- merge without silently overwriting existing data;
- preserve the destination’s existing audit history;
- rebuild machine-specific paths, service settings, audio devices, and browser
  attachments rather than importing them blindly;
- require the user to reconnect providers and re-pair live browser/accounts;
- verify the result and provide rollback.

Secrets imported to a new PC must be written only to the destination keyring
or encrypted vault. They must never be placed in the repository, an ordinary
export directory, or a model prompt. API keys, browser sessions, CAPTCHA,
2FA, SMS, email, and recovery challenges require separate operator action on
the destination machine.

Portability must be tested for fresh import, repeated import, partial import,
wrong passphrase, corrupt archive, newer schema, conflicting profiles,
read-only destination, disk-full failure, rollback, and uninstall. The
original PC and its data must remain unchanged if export or import fails.

## Secret rules

- Passwords, API keys, tokens, cookies, and recovery codes are encrypted at
  rest.
- Secrets are never committed to Git or stored in ordinary project files.
- Secrets are never placed in model prompts, voice transcripts, logs, reports,
  screenshots, receipts, crash dumps, or diagnostics.
- The model receives a typed secret reference, not the raw secret.
- A secret is released only to the approved target, origin, account, and
  action.
- Secret values are never printed in terminal output or dashboard responses.
- Export, backup, and migration exclude secrets by default.
- Corrupt, unknown, newer, or tampered vault state fails closed.

## Profiles and identities

Non-secret data such as name, address, language, timezone, phone number, email
address, company role, and form preferences may be stored in an encrypted
profile. Each field
must have:

- an owner;
- a purpose;
- a source and trust level;
- an explicit retention rule;
- a forget/delete operation;
- an export operation that redacts secrets.

Profiles must have stable identifiers and explicit labels such as `Personal`,
`Acme — Finance`, or `Employee — Maria`. Labels must be shown in previews and
receipts so the user can see which identity was used.

Never merge profiles merely because names or email addresses look similar.
Merging, copying, or sharing a profile requires an explicit human action and a
reviewable preview.

Web pages, models, tools, or external providers cannot silently overwrite
trusted user profile data or grant themselves access to it.

## Account and site binding

Every account record must be bound to an approved origin and account label.
Permissions must specify:

- site or origin;
- principal, organization, and employee where applicable;
- account;
- selected identity/profile;
- capability, such as read, navigate, fill, draft, or send;
- allowed fields or data classes;
- expiry;
- issuing human principal;
- revocation state.

Lookalike domains, cross-origin reuse, wildcard accounts, unsafe redirects,
and target switching must fail closed.

The same origin may have multiple account records. Qubit must show the
selected account and identity before accessing or filling it. Credentials for
one account must never be tried against another account.

## Form assistance

Qubit may use approved profile fields to fill an approved form after checking
the target origin and field purpose.

The normal flow is:

```text
User request
  → identify site and account
  → inspect bounded form fields
  → show or speak a preview
  → fill only permitted fields
  → report what was filled
```

Before filling, Qubit must identify the selected principal, account, identity,
and profile. For company workflows, it must distinguish employee data from
company data and show the distinction in the preview.

Filling a form is separate from submitting it. Sending, publishing,
purchasing, account changes, deletion, and other high-impact actions require a
fresh exact confirmation unless the owner created a narrow, expiring rule for
that exact workflow.

Qubit must never invent identity details, consent, legal answers, financial
answers, medical answers, or verification responses.

## Authentication handoffs

Qubit may assist with an approved login flow without exposing secrets to the
model. The user must personally handle or explicitly control:

- CAPTCHA;
- password-manager unlock;
- 2FA and authenticator prompts;
- SMS, email, or WhatsApp codes;
- recovery challenges;
- identity verification;
- payment confirmation.

Qubit must not bypass these controls, guess codes, defeat CAPTCHA, or create
accounts using undisclosed identities.

## API keys and providers

Provider keys use references such as `env:` or an OS-keyring reference. Public
status may show only:

- provider name;
- configured or missing state;
- redacted key reference;
- permission scope;
- last validation time.

Provider requests use minimum-necessary data and an explicit allowlist. Private
profile data, source code, browser text, credentials, and secrets stay local
unless the user explicitly authorizes a particular provider request.

## Qubit Brain permissions

The Brain must use the existing Qubit broker, grants, audit, redaction,
resource limits, and emergency stop. It must not create a second permission
system or hidden listener.

Routine read and reversible form-fill actions may use a narrow session grant.
High-impact actions require a fresh confirmation containing the exact target,
account, fields, and action hash.

All grants expire and can be revoked immediately. Emergency stop prevents
secret release and action execution before a child process or browser action
starts.

Permissions are evaluated in this order:

```text
owner/organization → employee → identity → account → origin → capability → expiry
```

No broader permission may silently authorize a narrower-looking but different
account or identity. Shared company grants must list exactly which employees,
origins, accounts, fields, and actions they cover.

## Storage and recovery

- Use the OS keyring or a vetted encrypted store for secrets.
- Use restrictive file permissions where encrypted state is stored.
- Use atomic writes, locking, fsync, schema versions, and corruption checks.
- Never overwrite corrupt or newer state automatically.
- Keep backups encrypted and secret-aware.
- Support forget, export, restore, and rollback without losing unrelated user
  data.
- Do not copy browser profiles, cookies, session databases, or private files
  into the Qubit repository.

## Audit and diagnostics

Audit records may include action type, target, account alias, grant, result,
timestamp, and typed error. They must not include secret values, full form
contents, raw page text, cookies, tokens, or unredacted screenshots.

Diagnostics must be bounded, redacted, and safe to share. A diagnostic bundle
must disclose what was removed and must never claim that a secret was absent
merely because it was redacted.

## Required implementation tests

Before calling Qubit Brain complete, test:

- encrypted storage and restrictive permissions;
- multiple owners, organizations, employees, identities, emails, phones, and
  accounts;
- cross-employee and cross-organization isolation;
- same-origin multiple-account selection;
- company membership removal and immediate grant revocation;
- secret references and zero raw-secret leakage;
- profile field provenance, correction, forget, and export;
- origin/account binding and lookalike rejection;
- grant creation, expiry, revocation, and replay rejection;
- form preview versus submit separation;
- exact confirmation and changed-field rejection;
- CAPTCHA/2FA/recovery manual handoff;
- corrupt, newer, partial, and concurrent vault state;
- backup, restore, rollback, and uninstall preservation;
- CLI, dashboard, and voice parity;
- prompt injection and malicious page-field resistance;
- emergency stop and fail-closed behavior;
- clean-install and secret/artifact scans.

No implementation is complete if it stores plaintext credentials, lets model or
page content grant authority, bypasses the broker, or exposes raw secrets in
logs, prompts, reports, screenshots, or diagnostics.
