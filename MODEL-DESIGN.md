# Model design — Index Options Accumulation Research

> Application status: the local app now runs a frozen, explainable `INDEX-PRESSURE-1.0` index-direction baseline and a chronological historical replay. The score is not probability-calibrated. This baseline is a research classifier, not a verified trading edge or an option strategy.

## 1. Product shape mirrored from the existing project

The new project should preserve the existing project’s working pattern while changing the research unit and evidence model:

| Existing project concept | Index-options counterpart |
| --- | --- |
| Stock symbol | Index / exchange pair |
| Daily cash and delivery history | Daily index OHLC plus index derivatives history |
| Stock accumulation and distribution | Bullish and bearish positioning pressure, reported separately |
| Futures/options as confirmation | Index futures and option-chain evidence are first-class inputs |
| Stock details | Index history plus expiry/strike panel |
| My holdings | Optional manually entered index-option lots, keyed by full contract identity |
| Frozen historical rules | Frozen index-session rules, replayed through chronological periods |
| Learning observations | Immutable dated index-session predictions and matured outcomes |
| Data centre | NSE and BSE imports, contract-master checks, coverage and freshness |

The new engine must be the single source for current analysis and historical replay, as `flow_engine.py` is in the existing project. The new learner should retain dated baseline decisions, append revised outcomes when data is corrected, and require post-freeze forward evidence before any candidate filter can become active. The current project’s existing database and holdings must never be reused.

A later executable version can follow a structure such as:

```text
nse-index-options-accumulation-research/
  app.py                    local dashboard and API
  index_data.py             NSE/BSE imports, normalization, coverage
  index_options_engine.py   shared current + historical calculations
  index_service.py          analysis, reports, validation
  index_learning.py         dated observations and forward evaluation
  static/                   dashboard UI
  data/                     separate database and source-file archive
  README.md                 operating guide and model rules
```

## 2. What “accumulation” can mean here

Public end-of-day option-chain records show prices, volume, open interest (OI), and usually OI change. They do not identify who initiated a trade, whether a position belongs to an institution, or whether a new long or short is the informed side. Every contract has both a long and a short counterparty. Therefore the model should call its output **positioning pressure** or **participation**, not proven money inflow or institutional accumulation.

For a single option contract, premium and OI changes can be shown as a four-quadrant descriptive hint:

- premium up, OI up: possible long build-up;
- premium down, OI up: possible short build-up;
- premium up, OI down: possible short covering;
- premium down, OI down: possible long unwinding.

These labels are hypotheses, not proof. Spot movement, implied volatility, time decay, and changing liquidity all affect the premium. The engine should prefer IV- and delta-aware features when the source data supports them, and lower confidence when it does not.

## 3. Observation and contract identity

### Research observation

One **index-session** is one model observation, for example `NSE / NIFTY / YYYY-MM-DD`. The model produces one dated index state from all eligible inputs available by that close. Different expiries and strikes contribute evidence to that state; they do not create extra independent model outcomes.

### Option contract key

A contract row is uniquely identified by:

`exchange + underlying + expiry + strike + option_type (CE/PE) + trading_date`

Its terms include lot size, tick size, contract style, and source contract-master validity. Do not join contracts by symbol and strike alone. Do not compare OI across different expiries as if it were one continuous position.

### Normalize strikes

Store the absolute strike exactly as published, then derive:

- distance to spot: `(strike / index_close) - 1`;
- distance to ATM, using the valid strike grid for that expiry;
- time to expiry in calendar and trading days;
- delta bucket only when option price quality supports stable implied-volatility and Greeks calculations.

Use moneyness/delta buckets such as near-ATM, moderately OTM, and farther OTM for aggregate views. The exact boundaries are a versioned model setting to be selected after inspecting chain coverage and liquidity. Do not hard-code one absolute strike across dates or indices.

## 4. Daily workflow

1. **Import and quality-check.** Load daily index bars, futures, option contracts, and the exchange’s dated contract master. Retain source filename, retrieval/import time, row counts, and date coverage. Reject malformed or duplicate contract identities. Mark missing chain coverage explicitly.
2. **Normalize market data.** Convert NSE/BSE source formats into common fields without collapsing exchange identity. Validate positive prices, plausible OHLC ranges, expiry after trade date for open contracts, and OI/volume units. Use the lot size valid for that contract date.
3. **Build index context.** Calculate trend, recent returns, range/ATR, and realized volatility from the selected index itself. The data set contains no constituent stocks, stock symbols, constituent weights, or breadth calculations.
4. **Build derivatives evidence.** Compare the same expiry/strike/side over adjacent sessions. Separate expiry buckets and time-to-expiry. Calculate changes in OI, traded volume, premium, and—when reliable—implied volatility, skew, term structure, and delta-weighted OI. Keep futures price/OI, basis, and rollover as separate evidence.
5. **Aggregate the strike map.** Summarize CE and PE evidence by moneyness and expiry. Show OI concentration and changes at strikes as descriptive positioning landmarks. Normalize volumes and OI by each index’s own history and lot sizes so the larger nominal contract does not automatically dominate.
6. **Score separate evidence groups.** Produce bullish and bearish pressure separately. Each group is bounded so correlated measurements (for example, premium change and delta-adjusted premium change) cannot count as several independent votes. Keep the score components visible.
7. **Set a state and confidence.** The state comes from persistent index structure plus derivatives evidence, with a separate confidence/data-coverage label. Contradictory, thin, stale, or near-expiry evidence becomes mixed/low-confidence rather than being forced into a direction.
8. **Record before outcomes are known.** Save the date, model version, input revision, evidence snapshot, state, and any review flags as an immutable baseline prediction. Analysis reruns must not rewrite the original prediction.
9. **Measure completed outcomes.** As new dates arrive, calculate fixed-horizon index outcomes and any explicitly defined contract-strategy outcomes. Keep forward, late-recorded, and retrospective observations separate.

## 5. Evidence groups and outputs

The first version should keep the model readable and use a small number of distinct evidence groups:

1. **Index structure:** trend, momentum persistence, range break/hold, volatility regime, and distance from a reference invalidation level.
2. **Cross-index context:** compare returns and volatility among the seven selected index series only. Do not add company-level constituents or unrequested index universes.
3. **Futures participation:** same-contract OI change paired with futures price change; basis and expiry roll shown separately. Futures confirmation is not proof of net buying.
4. **Option positioning:** CE/PE OI and OI change by moneyness and expiry, option turnover, premium/volume persistence, and strike concentration changes. Put/call ratios remain descriptive, never stand-alone direction rules.
5. **Volatility and skew:** ATM IV, put/call skew, and term structure where a reliable forward, rate, and option mark are available. When not calculable, report missing coverage rather than filling it with an estimate disguised as observed data.
6. **Persistence and data quality:** consecutive sessions of evidence, same-contract coverage, stale inputs, missing expiries, sparse strikes, and near-expiry conditions.

The dashboard can show states such as **Bullish positioning watch**, **Bearish positioning watch**, **Building**, **Fading**, **Mixed**, **Reversal risk**, and **Insufficient data**. A state is a research summary, not a probability of profit.

The output should have two layers:

- **Index view:** directional/participation state, evidence, benchmark context, invalidation reference, and 5/10/20-session index outcome diagnostics.
- **Strike map:** which expiries and relative strikes carry the change in OI, volume, premium, and volatility. This explains the index view; it is not a guaranteed support/resistance map or an automatic trade selection.

## 6. Do not collapse the signal into one “hot strike”

A fixed strike can move from OTM to ATM or ITM as the index moves; its premium also decays as expiry approaches. The useful model question is: **which relative strikes in which expiry showed persistent, unusual positioning, and did the underlying index confirm?**

Keep near, next, and farther expiry evidence separate. Require the contract master to confirm which expiries actually exist for each exchange and date. Do not assume that all seven indices have the same weekly cycle. A roll into a new expiry is a new contract series, not an OI increase in the old series.

Potential “call wall” and “put wall” displays should be named **OI concentration zones** and show their changes, date, and expiry. OI alone does not prove a level will hold or break.

## 7. Entry review and risk

The model’s first job is to classify the underlying index state. It should not infer a long-option or short-option strategy from that state alone. Buying options and writing options have different payoff, time-decay, gap, margin, and execution risks.

For a later strategy-specific review, define the strategy before testing it:

- direction and position type (long premium, defined-risk spread, or another stated rule);
- expiry/DTE selection rule;
- strike or delta selection rule;
- next-session entry price convention;
- maximum holding period and expiry handling;
- stop/target or exit rule;
- realistic exchange, brokerage, tax, spread, and slippage assumptions.

Until such a template is declared, display eligible contracts and their liquidity/coverage as research candidates only. Do not present a specific contract as a buy/sell instruction. Keep the underlying index’s reference risk separate from option premium risk; an index stop does not translate linearly into a premium stop.

## 8. Historical validation and learning

Use the same frozen engine for current analysis and historical replay. Split history chronologically into development, validation, and later holdout periods. Do not randomly split option rows or use contract rows from the same index-day across train and test.

For index direction, evaluate predeclared 5/10/20-session outcomes from a clearly available entry point. For contract strategies, replay only the contract that was selectable from the chain and contract master on the signal date; do not replace it later with whichever strike performed best. Respect expiry, gaps, missing quotes, no-trade sessions, and transaction costs. Report exclusions and source coverage.

Report metrics by index, by strategy template, and by time period. Use calendar/month blocks to show uncertainty and account for cross-index correlation. Treat repeated signals in the same index as overlapping unless spaced by a declared horizon. Do not count each strike as a separate trade. A pooled seven-index result must not hide a weak index or claim seven independent confirmations.

The existing learner’s **baseline → candidate → shadow → forward-qualified** pattern is suitable, but its numeric trade-count gates should not be copied blindly. With only seven related underlyings, the effective sample can be small even when the chain contains thousands of contracts. First collect real coverage and prospective observations; only then set minimum counts. No probability should be displayed unless it is separately calibrated and shown to be reliable out of sample.

## 9. Data layout and dashboard sections

Suggested independent tables (there is no stock-symbol or constituent table):

- `index_bars(exchange, underlying, day, open, high, low, close, source)`
- `derivative_contracts(exchange, underlying, day, expiry, strike, option_type, open, high, low, close, volume, oi, oi_change, source)`
- `contract_terms(exchange, underlying, expiry, strike, option_type, lot_size, tick_size, valid_from, valid_to, source)`

- `data_coverage`, `source_files`, `index_signals`, `validation_runs`
- `learning_observations`, `learning_outcomes`, `learning_models`, `learning_runs`
- optional private `positions` and `decisions`, stored separately from market-only exports

## 10. Implemented baseline: INDEX-PRESSURE-1.0

This section records the current executable rules in `index_options_engine.py`. It distinguishes implemented rules from the richer candidate features discussed above. Do not imply that IV, Greeks, breadth, institutional identity, online learning, or option-strategy profitability are available in this version.

### Evidence scores

The score groups are capped separately. Their score points are heuristic units, not percentages or probabilities.

| Group | Maximum absolute contribution | Current inputs |
| --- | ---: | --- |
| Index structure | 35 | Five-session and one-session returns normalized by recent average true range, close location within the day, close versus SMA20, and a close outside the prior 20-session high/low |
| Futures positioning | 25 | Nearest traded unexpired contract's same-expiry price/OI quadrant (up/up +14, down/up -14, up/down +7, down/down -7), plus a matched-expiry basket's five-session quadrant (±8 for OI building, ±4 for OI falling) |
| Options positioning | 20 | Nearest eligible expiry from 2–45 calendar days, strikes within ±8% of spot, and same strike/expiry/side contracts matched to the prior cash session; premium and OI quadrants are proximity-weighted |
| Cross-index context | 8 | Same-session up/down direction of the other six requested indices only; score is normalized by all six possible peers, so missing indices reduce the contribution |

Index structure detail: the five-session return contributes up to ±14 points using `ATR% × sqrt(5)`, the one-session return up to ±7 using `ATR%`, close location up to ±3, SMA20 contributes ±4, and a close outside the previous 20-session boundary contributes ±7. ATR is the mean of up to 14 true-range observations when at least five are present. The score is capped at ±35.

For options, a matched contract is ignored when lot size changed, prior OI/premium is unavailable, absolute OI change is below 1%, or absolute premium change is below 0.25%. The proximity factor declines toward the ±8% band edge. OI decreases receive lower weight than OI increases. Positive premium change is bullish for calls and bearish for puts; negative premium change reverses those signs. This summarizes a weak premium/OI quadrant hypothesis; it does not identify the party initiating a trade. Largest included PE OI below spot and CE OI above spot are displayed as landmarks, not guaranteed support or resistance.

### Classification and confidence

- Fewer than 20 index sessions: **INSUFFICIENT DATA**.
- **RISE:** net score at least +25, positive index-structure score, and at least one positive futures, options, or cross-index score.
- **FALL:** net score at most −25, negative index-structure score, and at least one negative futures, options, or cross-index score.
- **RANGE:** at least 20 sessions, recent five-session volatility below 0.85× its comparison median, absolute net score below 15, absolute structure score below 10, and the close inside the prior 20-session high/low.
- Otherwise: **NO EDGE**. Conflicting or missing data is not forced into a directional call.
- Confidence defaults to **LOW**. The directional **MODERATE — UNCALIBRATED** label additionally requires at least 20 sessions, at least three aligned evidence groups, absolute net score of at least 40, and at least three aligned peer indices. Range uses a stricter volatility-compression condition. There is no HIGH label and no probability output.

Every saved record contains the signal close date, model version, as-of input revision, component scores, features, reasons, and warnings. The unique key allows a corrected/imported input revision to create a new record without overwriting an earlier one. When a later index bar is imported and analysis runs, the first following session's outcome is appended separately.

### Historical replay and outcomes

`Evaluate selected index` walks the selected index's daily bars in date order. Each forecast sees only rows dated through its signal close. It then observes the next imported cash-index session and applies thresholds fixed from the signal-date ATR: **RISE/FALL** if the close-to-close move is at least `max(0.10%, 0.20 × prior ATR%)` in either direction; otherwise **RANGE** if the next session's high-low range is no more than `1.10 × prior ATR%`; otherwise the observed label is **VOLATILE**. A volatile actual outcome counts as a miss for any RISE/FALL/RANGE forecast.

Replay rows are divided chronologically into earlier 60%, next 20%, latest 20% holdout, and all-history views. The dashboard reports prediction coverage, classification accuracy among RISE/FALL/RANGE calls, directional hit rate (including calls followed by range or volatile outcomes as misses), and the four-class majority baseline. Fewer than 30 directional calls are explicitly marked too small to interpret. These metrics test only the index label; they do not test option selection, entry timing, execution, transaction costs, or P&L. The rules are frozen for this baseline and are not tuned from the displayed holdout.

The current local database previously contained only a short history for five NSE indices and no BSE index OHLC. Until adequate independent history is imported for an index, this version will return insufficient data or an uninformative replay. It must not be described as identifying big-player activity or as a high-probability trading system.

Dashboard sections should parallel the existing application: **Market research**, **Index history**, **My positions** (optional manual lots), **Data centre**, **Historical results**, **Learning & performance**, and **Model & evidence**. The contract detail key must include exchange, underlying, expiry, strike, and CE/PE. Holdings also need quantity, premium, lot size, entry date, and contract expiry; they must not be merged across different contracts.

The database and source archive belong only to this new project’s `data/` directory. Keep raw files for audit. Exclude database files, imported data, personal positions, and secrets from source control.

## 10. Data-access gate before implementation

The existing project already has an NSE-focused downloader. This project adds BSE index data, date-valid index-derivative contract masters, and options history, while keeping the market universe index-only. The first build should support validated manual imports for both exchanges. Automatic downloads should be added only where official access, terms, format stability, and adequate historical coverage are confirmed. BSE’s official market-data portal describes its index and derivatives feeds as data products with access plans, so do not assume a complete free BSE history is available.

Before selecting final thresholds or promising a historical backtest, audit sample NSE and BSE files for dates, fields, chain completeness, expiry identifiers, lot-size changes, and matching index closes. If historical IV or quote spreads are missing, mark those features unavailable and narrow the claims.
