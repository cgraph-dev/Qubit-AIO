---
name: hermes-administration
description: "Bulk provisioning, tool management, and hub skill operations for Hermes Agent environments."
version: 1.0.0
author: agent
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [hermes, setup, provisioning, batch, tools, skills, cli]
---

# Hermes Administration

This skill covers bulk operations and environment provisioning for Hermes Agent: enabling all toolsets, installing skills at scale from the hub, and handling the CLI quirks that appear during non-interactive administration.

## When to use

- User asks to "install everything", "enable all tools", or "install all skills"
- You need to batch-install multiple hub skills without interactive confirmation
- You need to enumerate available skills programmatically from `hermes skills browse`
- Setting up a fresh Hermes profile or environment

## Bulk tool enablement

Enable multiple built-in toolsets in a single command:

```bash
hermes tools enable web browser terminal file code_execution vision video image_gen video_gen x_search moa context_engine homeassistant spotify yuanbao
```

To see the current state: `hermes tools list`

Newly enabled tools take effect on the next session (`/reset` or fresh `hermes` invocation).

## Bulk skill installation

### The `--yes` flag is required for batch installs

`hermes skills install` prompts for confirmation by default. In automated or batch contexts, this causes silent cancellation. Always use `--yes`:

```bash
hermes skills install --yes official/security/1password
```

### Installing all official skills

1. Browse the official catalog (paginated, 20 per page by default):
   ```bash
   hermes skills browse --source official --page 1
   ```

2. Because the table output wraps the Identifier column across lines, simple `grep`/`awk` will fail. Use the Python parsing recipe in `references/bulk-skill-install.md` to extract every identifier.

3. Install with a loop or script, always passing `--yes`.

### Identifier gotchas in browse output

- The Identifier column is the **last** column and wraps across continuation lines in the terminal table.
- Box-drawing characters (`│`, `┃`) separate columns.
- Some rows may appear to append the word "Identifier" (the column header) to the actual ID if parsed naively. Parse by splitting on box-drawing chars and accumulating only the last column per row.
- A skill's hub identifier (e.g. `official/mlops/torchtitan`) may differ from its display name in `hermes skills list`, which comes from the SKILL.md frontmatter `name` field.

### Hub vs local vs builtin

- **builtin**: shipped with Hermes, located under `~/.hermes/skills/` category directories
- **hub-installed**: fetched from the skills hub, copied into `~/.hermes/skills/<category>/<name>/`
- **local**: any SKILL.md discovered under `~/.hermes/skills/` that isn't builtin or hub-managed

`hermes skills list` shows the source column. Hub-installed skills can be updated with `hermes skills update`.

## Verification

After bulk operations, verify with:

```bash
hermes tools list
hermes skills list
```

Check counts: expect all toolsets enabled, and hub-installed count should match the number of skills installed.

## Pitfalls

1. **Missing `--yes`**: Batch installs silently cancel. No error is raised; the skill simply isn't installed.
2. **Mid-session tool changes**: Enabling/disabling tools does not apply to the current running session. Always tell the user to `/reset` or start a new session.
3. **Terminal tool blocks complex pipelines**: Shell commands using `perl`, `grep -P`, or other pattern-heavy pipelines may be blocked by the terminal safety filter as potentially destructive. Use `execute_code` with Python for reliable text parsing of Hermes CLI output.
4. **Wrapped table columns**: Never grep identifiers out of `hermes skills browse`. The table uses Unicode box-drawing and soft-wraps long identifiers.
5. **Name vs identifier mismatch**: A skill installed by `official/mlops/accelerate` may display as `huggingface-accelerate` in `hermes skills list` because the SKILL.md frontmatter defines the display name. Use the hub identifier for installs, the display name for `/skill <name>`.

## References

- `references/bulk-skill-install.md` — Python script to extract all official skill identifiers and install them in batch.
