# Imported skill sources

These directories contain third-party agent instructions and reference data.
They are not imported into the `voicebridge` runtime and are not executed
automatically. Review a skill before using it on a task.

The snapshots below were downloaded at the pinned commits shown. Local changes
to the snapshots: none.

| Local path | Upstream source | Commit | License evidence |
| --- | --- | --- | --- |
| `anthropic-frontend-design/` | `anthropics/skills`, `skills/frontend-design` | `41bbe19d1a1a7eaab5e7bb9050a417e5c6cffc8f` | Apache-2.0; retained `LICENSE.txt` |
| `vercel-react-best-practices/` | `vercel-labs/agent-skills`, `skills/react-best-practices` | `063bee94c3f4df8453406c830b0a7df0f2860278` | MIT in the skill metadata |
| `vercel-composition-patterns/` | `vercel-labs/agent-skills`, `skills/composition-patterns` | `063bee94c3f4df8453406c830b0a7df0f2860278` | MIT in the skill metadata |
| `vercel-react-native-skills/` | `vercel-labs/agent-skills`, `skills/react-native-skills` | `063bee94c3f4df8453406c830b0a7df0f2860278` | MIT in the skill metadata |
| `ui-ux-pro-max/` | `nextlevelbuilder/ui-ux-pro-max-skill`, `.claude/skills/ui-ux-pro-max` | `4aad0584d92131626b16d4ff4d77f0455385013c` | MIT repository license |
| `bencium-controlled-ux-designer/` | `bencium/bencium-marketplace`, `bencium-controlled-ux-designer/skills/bencium-controlled-ux-designer` | `8b152ec07240cf671340b96cea6bb3442f697205` | MIT repository license |
| `accesslint/` | `AccessLint/skills`, `plugins/accesslint/skills/{accessibility-audit,accessibility-diff,accessibility-fix,accessibility-inspect,accessibility-scan}` | `2e9d7336678302d0bc08848e92542560295b34d3` | MIT declared by `plugins/accesslint/.claude-plugin/plugin.json` |

## Deliberately not imported

- Vercel Web Design Guidelines (`vercel-labs/agent-skills`,
  `skills/web-design-guidelines`) is not copied: the pinned repository has no
  declared license for that skill and reports no recognized repository license.
  It needs a license review before redistribution.
- “Kyosaki” is not copied because no exact repository or skill URL was
  provided and the GitHub repository search found no unambiguous match. Supply
  the exact link for a separate provenance and license review.

License links and upstream identities are recorded here for the school-project
snapshot. If these files are redistributed, retain the upstream notices and
follow each upstream license independently of the project `LICENSE`.
