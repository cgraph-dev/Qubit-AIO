---
title: "Subscription Research — Use the user's own Perplexity Pro / Gemini Web accounts for web research through local OmniRoute."
sidebar_label: "Subscription Research"
description: "Use the user's own Perplexity Pro / Gemini Web accounts for web research through local OmniRoute."
---

{/* This page is auto-generated from the skill's SKILL.md by website/scripts/generate-skill-docs.py. Edit the source SKILL.md, not this page. */}

# Subscription Research

Use the user's own Perplexity Pro / Gemini Web accounts for web research through local OmniRoute.

## Skill metadata

| | |
|---|---|
| Source | Bundled (installed by default) |
| Path | `skills/research/subscription-research` |
| Version | `1.0.0` |
| Author | Qubit Agent |
| License | MIT |
| Platforms | linux, macos, windows |
| Tags | `Research`, `Perplexity`, `Gemini`, `OmniRoute`, `Search` |
| Related skills | [`grounded-citations`](../../bundled/research/research-grounded-citations.md), [`competitor-news-monitor`](../../bundled/research/research-competitor-news-monitor.md) |

## Reference: full SKILL.md

:::info
The following is the complete skill definition that Qubit loads when this skill is triggered. This is what the agent sees as instructions when the skill is active.
:::

# Subscription Research

Use this when the user asks for current web research and has a Perplexity Pro
or Gemini/Google AI subscription connected to local OmniRoute. It uses their
account-backed search, not a paid search API.

## Tool

Call `subscription_research`:

| Argument | Value |
|---|---|
| `query` | the research question |
| `engine` | `perplexity`, `gemini`, or `auto` (default) |

`auto` prefers a connected Gemini Web model, then falls back to Perplexity.
A missing provider returns a typed error; do not silently substitute another
search provider.

## Procedure

1. Call `subscription_research` with the exact user question.
2. Read `answer` and `sources` from the JSON result.
3. If `sources` is empty, say the provider returned no citations; do not invent
   URLs.
4. If the tool returns `provider_not_connected`, tell the user which provider
   to connect in OmniRoute (Providers → Perplexity Web / Gemini Web). Do not
   fall back to Google Search scraping.
5. For extra page detail, use `web_extract` on the returned source URLs, or
   Qubit's browser tools with the real profile. Do not use Google Search
   AI Mode scraping; it is bot-checked.

## Limits

- The answer is only as current as the connected provider's own web index.
- Perplexity/Gemini subscriptions have their own usage quotas; not unlimited.
- This tool does not read browser cookies and does not store credentials.
- Do not claim `UNLIMITED`, `VERIFIED`, or a paid-API substitute.
