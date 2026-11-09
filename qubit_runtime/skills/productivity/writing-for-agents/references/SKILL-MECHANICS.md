# Skill mechanics

The skill-specific branch of `writing-for-agents`: what changes when the document is a skill (frontmatter, the invocation choice, and router skills). Everything else about writing it is the universal reference in the parent skill.

## Invocation

Two choices, trading the two loads:

- A **model-invoked** skill keeps a `description`, so the agent can fire it autonomously, and other skills can reach it. You can still invoke it by name: model-invocation always _includes_ user reach; a description only ever adds agent discovery, never removes the human's. The description is the skill's top-level context pointer, forced to stay loaded at all times: permanent context load in exchange for discoverability. A model-invoked skill whose content is all reference is also one home for shared reference: another skill can invoke it, so reference needed by several skills lives in one place. Mechanics: write a model-facing description carrying the trigger branches (the pointer-writing rules in the parent skill apply in full).
- A **user-invoked** skill strips the description from the agent's reach: only the human typing its name can invoke it, and no other skill can. Zero context load, but it spends cognitive load: you are the index that must remember it exists. Some agent formats mark this with a `disable-model-invocation` frontmatter key. **Qubit has no such key** — every skill in Qubit's index is model-reachable, so a "user-invoked" skill must be written with a description that says plainly it is invoked by name, and its behaviour must be safe to load on an explicit ask.

Pick model-invocation only when the agent must reach the skill on its own, or another skill must. If it only ever fires by hand, keep its description narrow rather than broad.

Shared reference that two explicitly-invoked skills both need can live in neither: with no reachable descriptions, neither can fire the other. Push it to a plain file outside the skill system: external reference any skill can point at.

## Splitting by invocation

The invocation cut of splitting (the sequence cut lives in the parent skill): split off a model-invoked skill when you have a distinct leading word that should trigger it on its own (a trigger word you actually use in your prompts), or another skill must reach it. You pay context load for the new always-loaded description, so that independent reach has to be worth it.

## Router skills

When skills multiply past what you can remember, that piled-up cognitive load is cured by a **router skill**: one skill that names the others and when to reach for each, so the human has one skill to remember instead of many. In Qubit a router carries a description like any other skill, so it must stay short and route on situation, not on topic keywords — every sibling has its own triggers and the router must not compete with them.

## Qubit frontmatter, in brief

Qubit's validator requires `---` at byte 0, a closing `\n---\n`, and both `name` and
`description`. The repo standard adds `version`, `author`, `license`, `platforms`, and
`metadata.qubit.{tags, category, related_skills}`, plus a description under 60
characters starting with the trigger. `qubit-agent-skill-authoring` is the
authoritative source for those; this file only explains *why* the description is the
expensive field.
