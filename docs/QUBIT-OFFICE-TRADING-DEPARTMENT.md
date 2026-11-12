# Qubit Office — Trading Department

**Status:** department design. Not financial advice, not a promise of returns, not `VERIFIED`.
Live trading is not implemented. Full department structure exists from the start; live execution is
a safety-gated mode, not a reduced scope.

## 1. Scope

Asset classes:
- **Crypto spot** — BTC/ETH/major pairs, exchange APIs.
- **Meme coins** — DEX/CEX tokens with extreme-risk policy.
- **Forex** — major/minor pairs through broker APIs.
- **Research lane** — YouTube/strategy claims converted into testable hypotheses.
- **Later/optional** — futures, options, indices, commodities, prediction markets; each needs a
  separate asset-class approval and risk policy.

The department exists as real records, rooms, agents, sessions, strategies, risks, orders,
positions and accounting from the start. Live execution requires owner approval, credentials and
mandatory risk gates.

## 2. Department layout

- **Trading floor** — desks, market wall, execution desks, paper/live status.
- **Strategy lab** — research, backtests, notebooks, experiment board.
- **Risk room** — exposure, drawdown, limits, alerts, kill switch.
- **Data room** — market data feeds, historical data, provider health.
- **Treasury/accounting** — portfolio ledger, cost basis, fees, funding, PnL, tax lots.
- **Compliance/archive** — orders, approvals, receipts, incidents, audit.
- **War Room link** — strategy debate, evidence, owner decision.
- **Incident room** — exchange outage, runaway loss, stale data, breached limit.

## 3. Roles / agents

- Chief Investment Officer (strategy allocation, owner interface)
- Market Research Analyst
- Quant/Backtest Engineer
- Crypto Analyst
- Meme-Coin Risk Analyst
- Forex Analyst
- Execution Trader
- Risk Manager (independent; cannot be overridden by execution)
- Portfolio Accountant
- Compliance/Policy Officer
- Data Engineer
- Incident Responder
- Independent Verifier/Auditor

The Risk Manager and Auditor are separate agents with separate context and authority. An execution
agent cannot approve its own order or edit its risk limits.

## 4. Systems

1. **Market data service** — official exchange/broker feeds, websocket/REST, timestamps, health,
   stale-data detection, provider failover.
2. **Strategy registry** — hypothesis, universe, timeframe, entry/exit, fees/slippage assumptions,
   risk, version, author, evidence, status.
3. **Backtester** — historical data, fees/slippage/funding, no look-ahead, walk-forward/out-of-
   sample, multiple regimes, survivorship/liquidity caveats.
4. **Paper broker** — simulated fills, slippage, latency, rejection, partial fills, balances.
5. **OMS/EMS** — order state machine, idempotency, retries, partial fills, cancels, reconciliation.
6. **Risk engine** — pre-trade and post-trade limits, drawdown, exposure, concentration, leverage,
   liquidity, correlation, volatility, kill switch.
7. **Portfolio ledger** — double-entry, positions, cost basis, realized/unrealized PnL, fees,
   funding, tax lots, reconciliation.
8. **Approval service** — live order/exchange/credential/limit changes require owner approval.
9. **Audit/evidence** — every order, fill, cancel, rejection, data snapshot, strategy version,
   policy decision, approval and receipt.
10. **Kill switch** — cancel open orders, disable trading, revoke exchange keys, freeze strategies.

## 5. Integrations

### Crypto CEX
Binance, Kraken, Coinbase, Bybit, OKX (official APIs; verify jurisdiction/terms). Keys:
- trade-only, **withdrawal disabled**
- IP allowlist where supported
- subaccount per strategy/department
- short-lived/rotated credentials via vault
- no key ever in prompts, memory, logs or workspace

### DEX / meme coins
Jupiter, Uniswap, 1inch or equivalent aggregator; read-only chain data first.
Meme-coin gates:
- liquidity floor
- holder concentration
- mint/freeze authority
- honeypot/transfer tax/rug checks
- contract verification
- slippage/price-impact cap
- tiny position cap
- no automatic sniping
- manual/owner approval for new tokens

### Forex
MetaTrader 5 / cTrader / OANDA / IG or equivalent official API. Validate leverage, spread,
swap/funding, session hours, news events, broker execution model and jurisdiction.

### Data
Exchange/broker feeds, reference rates, news/economic calendar, on-chain data. No scraping where
terms forbid it. Provider health and stale-data alerts are mandatory.

## 6. Risk policy (mandatory)

Pre-trade:
- asset allowlist/denylist
- max order notional / % portfolio
- max position, concentration and correlation
- leverage cap
- liquidity/slippage/price-impact cap
- meme-coin specific gate
- spread/fee check
- stale-data check
- duplicate/idempotency check
- market-hours/session check

Portfolio:
- daily loss limit
- max drawdown
- max open positions
- strategy exposure cap
- venue/counterparty exposure
- funding/borrow cost
- margin/available balance
- correlation/cluster exposure

Operational:
- kill switch
- cancel-all
- disable strategy/venue/asset class
- key revocation
- incident escalation
- manual override with audit
- no withdrawal permission
- no unlimited leverage
- no trading during stale/missing data
- no live mode without owner approval

## 7. Strategy research — YouTube and external claims

Treat every YouTube/strategy claim as a **hypothesis**, not truth:
1. Capture source, timestamp, claim and exact rules.
2. Define universe, timeframe, fees, slippage, funding and liquidity assumptions.
3. Backtest with no look-ahead and out-of-sample/walk-forward.
4. Test across regimes and multiple venues.
5. Paper trade with realistic fills/latency.
6. Independent risk review.
7. Owner approval before live.
8. Record performance, drawdown, hit rate, expectancy and failure modes.

Do not copy “signals”, promises, screenshots or claimed returns without evidence. Do not optimize
on the same period used to evaluate.

## 8. Agent workflow and authority

- Every trading agent has a durable session/workspace, scoped memory and capability grants.
- Market data/research agents cannot place orders.
- Execution agents cannot change risk limits, strategy definitions or approvals.
- Risk Manager can veto/stop; independent Auditor can freeze.
- Orders flow only through ToolGateway → Risk Engine → Broker Adapter → Receipt → Ledger.
- Approval is bound to exact order intent (venue, symbol, side, quantity, price/type, max slippage,
  strategy, expiry).
- Kill switch revokes keys/leases and cancels orders; it is not a UI-only button.
- Trading memory scopes: strategy, department, portfolio; no cross-account leakage.
- No agent may withdraw funds or change bank/exchange payout details.

## 9. Portfolio accounting

- Double-entry ledger; no PnL from narrative.
- Positions, lots, cost basis, realized/unrealized, fees, funding, interest, taxes.
- Reconcile exchange/broker balances and fills; unknown outcomes remain unknown until reconciled.
- Separate personal/company books and per-strategy subledgers.
- Reports: exposure, drawdown, PnL attribution, win rate, expectancy, slippage, fee drag.
- Tax lots and export for the owner’s jurisdiction; no tax advice claim.

## 10. UI / office

- Trading floor room with market wall, desk agents, paper/live badge.
- Strategy Lab panel: hypotheses, backtests, experiments, verdicts.
- Risk dashboard: exposure, drawdown, limits, correlation, venue health, alerts.
- Order ticket: preview, fees, slippage, risk checks, approval, receipt.
- Portfolio/ledger panel: positions, PnL, fees, funding, reconciliation.
- Incident panel and kill switch.
- Trading memory graph and decision log.
- Clear “not financial advice / live risk” notice.

## 11. Legal / compliance

- Personal account now; future entity/books separation.
- Exchange/broker KYC, terms, jurisdiction and API restrictions.
- Tax/reporting obligations; owner responsible for advice/tax/legal decisions.
- No guaranteed returns; no solicitation/managed-fund claims.
- No custody of third-party funds without legal structure.
- Record all decisions and approvals for audit.

## 12. Failure modes

- Exchange outage / stale data / websocket gap.
- Duplicate or partial fills; cancel/replace race.
- API key leakage; over-scoped/withdrawal key.
- Strategy overfitting; survivorship/look-ahead bias.
- Meme coin rug/honeypot/liquidity collapse.
- Forex leverage/margin call/gap risk.
- Correlation concentration hidden across strategies.
- Runaway loop/retry storm; duplicate orders.
- PnL/accounting drift; unreconciled balances.
- Agent self-approval or risk-limit editing.
- Owner unavailable during a drawdown/incident.
- Regulatory/tax surprises.

## 13. Mandatory gates

- Paper/testnet mode passes before live.
- Exchange/broker keys trade-only, withdrawal disabled, vaulted, rotated.
- Risk engine pre-trade/post-trade tests and kill switch pass.
- Double-entry ledger and reconciliation tests pass.
- Live order approval payload-bound and expiring.
- OWASP/MCP/WCAG/OTel mandatory checklist applies.
- No live capital until owner signs the trading risk policy and limits.

## 14. Implementation placement

- **Phase 2:** agent registry/sessions, ToolGateway, risk/approval capabilities.
- **Phase 3:** Trading Department floor/rooms/data/agent manifests.
- **Phase 5:** market data, strategy lab, paper broker, portfolio ledger, exchange/broker adapters,
  Etsy-style end-to-end business workflow.
- **Phase 6:** trading floor UI, market wall, Character Studio/room style, memory graph.
- **Phase 8:** load/fault tests, kill switch, security, reconciliation, live approval.

The department and data exist from the start; live execution is enabled only through the gate.
