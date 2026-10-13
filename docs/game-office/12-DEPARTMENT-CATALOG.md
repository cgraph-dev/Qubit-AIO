# 12 — Department Catalog

The org is a **corporation**, not a startup. Dozens of departments, each a whole business with its
own team, running at once. This file is the library the CEO founds from.

Every department is a **template**: a floor plan, a seat roster, a tool set, a unit economics model,
and — this is the part that matters — **its own risk model**. An Etsy shop and a forex desk cannot
share a risk model, so the type is explicit in the schema (`office_department.division_kind`).

---

## The five division kinds

| Kind | What it does | Money model | Risk |
|---|---|---|---|
| **product** | makes and sells a physical or digital thing | margin per unit × volume | inventory/capex, platform fees |
| **content** | writes, licenses, publishes | royalties, licenses, subscriptions | no direct capex, reputation risk |
| **service** | sells human-shaped work to clients | hourly/fixed per engagement | delivery risk, client concentration |
| **trading** | allocates capital into markets | P&L on positions | **capital at risk — hard caps required** |
| **intelligence** | scrapes, researches, analyses — serves the other floors | internal; cost center that makes others earn | terms-of-service, rate limits |

`intelligence` is structurally different and worth stating: it **does not sell to customers**. It
sells to the other departments. Its "revenue" is the margin its work creates elsewhere — measured,
never invented (`45-ECONOMY.md` attribution).

---

## Departments

### product

**1. Print-on-demand** — Etsy / Redbubble / Merch by Amazon / Gelato
| | |
|---|---|
| Rooms | `design_studio`, `storefront`, `fulfillment` |
| Seats | design workers, listing writer, store manager |
| Tools | image generation, template manipulation, storefront API |
| Unit economics | ~$18 price, ~$7 cost, ~$11 margin; no inventory |
| Risk | platform ToS (trademark in designs), account suspension |
| Test path | Etsy **pattern/sandbox** mode; Redbubble has no test mode → stage the listing draft |

**2. Stocks & dropship** — eBay / Shopify / Amazon FBA
| | |
|---|---|
| Rooms | `sourcing`, `storefront`, `fulfillment` |
| Seats | sourcer, listing writer, ops |
| Tools | supplier APIs, price comparison, marketplace API |
| Unit economics | thinner margin (~15–25%), needs volume |
| Risk | inventory capex, late delivery, returns |
| Test path | eBay **sandbox**; supplier test accounts |

### content

**3. Ebooks & guides** — Amazon KDP / Gumroad / own store
| | |
|---|---|
| Rooms | `writing_room`, `editing`, `cover_studio`, `publishing` |
| Seats | writer, editor, cover artist, publisher |
| Tools | long-form writing, image gen, EPUB tooling, KDP upload |
| Unit economics | ~$9.99 price, ~$0.30 delivery → ~65% royalty |
| Risk | plagiarism, low-quality flooding, KDP account strikes |
| Test path | KDP **draft-only** (never publish), Gumroad test product |

**4. Courses** — Udemy / Teachable / own platform
| | |
|---|---|
| Rooms | `studio`, `scripting`, `editing`, `publishing` |
| Seats | curriculum designer, presenter/writer, editor, publisher |
| Tools | video generation/editing, TTS, course platform API |
| Unit economics | $49–$199; platform takes 30–63% |
| Risk | high production cost, slow to first sale, refund rates |
| Test path | platform **draft** + internal review only |

**5. Writing & copywriting as a product** — Substack / Medium / direct
| | |
|---|---|
| Rooms | `writing_room`, `editing`, `publishing` |
| Seats | writers, editor |
| Unit economics | subscriptions, sponsorships — slow, compounding |
| Risk | audience building is slow; platform dependence |
| Test path | free tier post to a private/self-owned publication |

### service

**6. Software agency** — Upwork / Fiverr / direct clients
| | |
|---|---|
| Rooms | `war_room`, `lab`, `client_room` |
| Seats | bidder, developers, reviewer, account manager |
| Tools | full dev toolchain, git, deploy |
| Unit economics | $30–$150/hr equivalent; margin = rate − model cost |
| Risk | client concentration, scope creep, missed deadlines |
| Test path | real platform is fine — bids cost nothing until accepted |

**7. Freelance design & copy** — Fiverr / Upwork / Contra
| | |
|---|---|
| Rooms | `studio`, `client_room` |
| Seats | designer, copywriter, account manager |
| Unit economics | $50–$500/gig |
| Risk | platform ToS on AI-delivered work — **must state AI use where required** |
| Test path | real gigs, low-value first |

**8. Data & research services** — bespoke reports, audits
| | |
|---|---|
| Rooms | `research`, `analyst`, `client_room` |
| Seats | researchers, analyst, writer |
| Tools | Perplexity browser research, scraping, document generation |
| Unit economics | $200–$2,000/report; high margin, low capex |
| Risk | accuracy liability — a wrong report is a refund and a reputation hit |
| Test path | real, but first three delivered free to build reputation |

### trading

**9. Forex desk**
| | |
|---|---|
| Rooms | `trading_floor`, `risk_office`, `vault` |
| Seats | strategist, risk manager, trader |
| Tools | broker API, market data, backtest harness |
| Unit economics | P&L on positions |
| **Risk model** | **per-trade cap, daily loss limit, max drawdown, hard stop** — all owner-set, none agent-editable |
| Test path | **paper account first.** A funded account opens only after N weeks of documented paper P&L |

**10. Prediction markets & betting** — Polymarket / sportsbooks
| | |
|---|---|
| Rooms | `trading_floor`, `risk_office` |
| Seats | analyst, risk manager |
| Tools | market APIs (Polymarket read tooling already exists in this repo) |
| Unit economics | binary outcomes; edge must be demonstrable |
| **Risk model** | hard per-event cap, total exposure cap, no chasing losses. Under D25 the desk may be as aggressive as it likes **inside these caps** — the caps are the only bound |
| Test path | paper/analysis-only (no bet placed) until a documented record exists |

**11. Arbitrage & market-making**
| | |
|---|---|
| Rooms | `trading_floor`, `risk_office` |
| Unit economics | thin, high-frequency, capital-intensive |
| Risk | execution risk, capital lockup, fee drag |
| Test path | simulation harness against live prices |

### intelligence

**12. Web scraping & data collection**
| | |
|---|---|
| Rooms | `crawl_room`, `data_lab`, `vault` |
| Seats | crawl engineer, data engineer |
| Tools | browser automation, HTTP fetch, parsing, storage |
| Unit economics | internal — serves every other department |
| **Risk model** | **this is the highest legal-risk department.** robots.txt respect, rate limits, no auth-wall bypass, no personal data, no scraping where the terms forbid it. **Under D25/D29, item 6 of the hardline floor does the heavy lifting here:** "act against a platform's published terms" is what stops an aggressive scraping desk from getting the org's accounts banned |
| Test path | crawl a permitted site first; record the permitted-use check per source |

**13. Market & competitor intelligence**
| | |
|---|---|
| Rooms | `research`, `analyst` |
| Tools | Perplexity browser research, feeds, market data |
| Unit economics | internal |
| Risk | low if it stays on public sources |

**14. Analytics**
| | |
|---|---|
| Rooms | `data_lab` |
| Tools | SQL, charting, the org's own ledger |
| Unit economics | internal |
| Risk | low |

### meta (org-level, not a business)

**15. R&D / new ventures** — scouts propose new businesses; the CEO founds them
**16. HR / hiring** — recruit, onboard, fire across all floors
**17. Finance / bookkeeping** — owns the ledger views, not the ledger itself
**18. Legal & compliance liaison** — prepares packages for the owner; never acts as the owner

---

## Account creation (the owner's ruling)

Agents may **create accounts**, and may **ask the owner for his information**, with one structural
rule that keeps it honest:

**An outbound account is a capability, and the credentials belong to the org, not the agent.**

| Step | Mechanism |
|---|---|
| Agent decides a platform is needed | creates an `office_account_request` with the platform, why, and expected first-dollar path |
| Owner reviews | staged (this is a money/identity action → posture `supervised` always applies, even under `unattended_full`) |
| Account created | by the agent with owner-supplied info, or by the owner directly |
| Credentials stored | in the **owner's** credential store, referenced by `env:`/`file:` handle — **never in the org DB, never in an agent's workspace** |
| Agent uses it | through the platform tool, which resolves the handle — the agent never sees the raw secret |

**What the owner must supply** (the agent asks, the owner pastes once):

| Item | Used for |
|---|---|
| Legal name / entity | every platform's KYC |
| Address | billing, shipping, tax |
| Tax ID | payout forms (1099/W-8) |
| Bank / payout account | where the money lands |
| Email alias(es) | one per department is recommended, so a suspension is contained |
| Phone for verification | SMS/OTP handoff — the agent asks, the owner supplies |

**Hard limits that survive this ruling:**

- An agent may **create** an account, but **never sign a contract as the owner**, and never accept
  terms that name the owner as a party without a per-instance owner approval.
- An agent may **request** the owner's info; it may never **read** it from wherever else it lives.
  The request is the only channel.
- Credentials never enter an agent's context. A tool resolves them at call time.
- **Test/paper accounts are the default.** A real funded account requires the owner to explicitly
  fund it — the org cannot promote itself from paper to real.

| Platform | Test mode |
|---|---|
| Etsy | pattern/sandbox; listings staged |
| eBay | sandbox |
| Shopify | development store |
| Gumroad / KDP / Udemy | draft-only, never publish |
| Fiverr / Upwork | real (bids are free until accepted) |
| Brokers | paper account |
| Polymarket | read/analysis only |
| Redbubble / Amazon | no test mode → **owner approval before any real listing** |

---

## How a department is founded

```
scout proposes (thesis + citations + unit economics + kill criteria + regulatory exposure)
  └─► CEO reviews, picks an option, recommends
      └─► owner approves capital (this is the one thing that is always the owner's)
          └─► department created: floor assigned, rooms materialized, roster hired
              └─► starts on TEST/paper credentials
                  └─► proves it, then requests funding for real credentials
```

**Why test-first even though the ruling says real money:** the org's whole premise is survival
pressure, and the pressure is only real once it is spending real money. But a brand-new department
on a real account with an unproven thesis is how you lose an account — and a suspended account is
a permanent loss, not a temporary one. Paper first, then fund, keeps the pressure and removes the
irreversible loss.
