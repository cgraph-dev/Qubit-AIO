---
name: subscription-research
description: "Use the user's own Perplexity Pro / Gemini Web accounts for web research through local OmniRoute."
version: 1.0.0
author: Qubit Agent
license: MIT
platforms: [linux, macos, windows]
metadata:
  qubit:
    tags: [Research, Perplexity, Gemini, OmniRoute, Search]
    related_skills: [grounded-citations, competitor-news-monitor]
---

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
