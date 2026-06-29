# CURSOR_BACKTESTER_BUILD_SPEC.md

## Cursor Task

Extend the existing Twiq project with a local NIFTY options backtesting portal parallel to the paper-trading portal.

The backtester must synchronize historical NIFTY spot candles, ATM ±10 option-chain information, OI, PCR, IV, gamma, VIX, and option prices by timestamp. It must reuse the same strategy engine as forward testing, prevent look-ahead bias, support Dhan API downloads and mapped CSV imports, allow settings changes, save strategy versions, rerun experiments, compare results, and visualize the complete decision process.

Do not place real orders. Everything must remain paper-trading and backtesting only.

## Existing Application

Preserve the existing architecture and visual language:

- FastAPI backend under `backend/`
- Vanilla HTML/CSS/JavaScript frontend under `frontend/`
- Existing Dhan adapter, signal engine, OI analysis, and paper broker
- Existing local startup workflow
- Existing paper-trading portal must continue working

Add a `Backtester` navigation item and a separate backtesting page rather than replacing the paper portal.

Before implementation:

1. Inspect the current strategy, signal, market-data, OI, paper-broker, settings, and frontend files.
2. Identify disabled logic and contradictory defaults.
3. Refactor shared behavior before implementing replay.
4. Do not duplicate the live strategy in a second backtester-only implementation.

## Canonical Strategy Defaults

Use one settings model shared by forward testing and backtesting.

Initial defaults:

```yaml
underlying: NIFTY
option_type: ATM CE or ATM PE
strike_step: 50
option_chain_window: ATM-10 through ATM+10
timeframe: 5m
trade_start: "09:30"
trade_end: "11:30"
force_exit_time: "11:30"
ema_fast: 9
ema_slow: 15
ema_gap_min_points: 3
target_rupees: 2
stop_loss_rupees: 10
time_stop_candles: 2
capital_budget: 100000
lot_size: effective historical lot size
max_consecutive_losses: 2
max_trades_per_day: configurable
reversal_enabled: false
pcr_filter_enabled: true
```

Resolve current contradictions:

- Replace the active ₹5 default with ₹10.
- Restore the real trade-window check.
- Explicitly mark trailing, cooldown, reversal, dynamic exits, VIX, PCR, gamma, spread, and expiry-day rules as enabled or disabled.
- Remove hard-coded `True` trade-window states.
- Store every toggle in each backtest configuration.
- Do not claim disabled code is part of the tested strategy.

## Strategy Layers

The shared strategy must expose each decision layer independently.

### Layer 0: Direction

Use completed NIFTY candles only.

- CE candidate: EMA 9 above EMA 15 and EMA 9 rising.
- PE candidate: EMA 9 below EMA 15 and EMA 9 falling.
- Otherwise skip unless reversal mode is enabled.

### Layer A: Trend Quality

- EMA gap must meet the configured minimum.
- EMA slope must agree with the trade direction.
- Signal candle body ratio must meet the minimum.
- CE requires a bullish signal candle.
- PE requires a bearish signal candle.

### Layer B: VWAP

- CE requires NIFTY above VWAP.
- PE requires NIFTY below VWAP.
- NIFTY index candles do not contain meaningful traded volume.
- Prefer NIFTY futures candles for true VWAP when available.
- If futures volume is unavailable, label the fallback explicitly as TWAP.
- Never silently call TWAP “VWAP.”

### Layer C: OI And PCR

Calculate from the same ATM ±10 strike window in both live and historical modes.

- Call wall: highest CE OI at or above spot.
- Put wall: highest PE OI at or below spot.
- Pin strike: highest combined CE plus PE OI.
- PCR: total PE OI divided by total CE OI inside ATM ±10.
- Headroom and recent wall-break rules must use only data available at the decision timestamp.
- Record OI values and wall locations in every signal record.

### Layer D: Pin And Gamma

- Use source gamma when supplied by CSV.
- Otherwise calculate Black-Scholes gamma only when IV, spot, strike, verified expiry time, and required assumptions are available.
- Store risk-free rate, dividend yield, and calculation version in the run configuration.
- Calculate gamma exposure using gamma multiplied by OI and lot size.
- Derive gamma-flip context consistently across ATM ±10.
- If gamma cannot be calculated reliably, mark it unavailable and disable gamma-dependent rules for that run.
- Never silently replace missing gamma with invented values.
- A pin-strike fallback may be displayed separately but must not be labelled true gamma flip.

### Layer E: Liquidity And Volatility

- Apply bid/ask spread filtering only when historical bid and ask exist.
- If spread is unavailable, disable the spread gate and apply configured conservative slippage.
- Apply India VIX filtering only when timestamp-aligned VIX data exists.
- Missing VIX must be disclosed in the run’s data-quality report.

## Strategy Module Interface

Create versioned strategy modules for deeper logic changes. Common values remain editable in the portal.

Required conceptual interface:

```python
class Strategy:
    strategy_id: str
    strategy_version: str

    def required_features(self) -> set[str]:
        ...

    def evaluate_entry(
        self,
        timestamp,
        market_context,
        account_state,
        settings,
    ) -> Decision:
        ...

    def create_position(
        self,
        decision,
        execution_quote,
        settings,
    ) -> Position:
        ...

    def evaluate_exit(
        self,
        timestamp,
        position,
        option_bar,
        market_context,
        settings,
    ) -> ExitDecision | None:
        ...
```

Every `Decision` must include:

```text
timestamp
strategy_id
strategy_version
status
side
expiry
strike
signal_layer
reason
NIFTY OHLC
EMA 9
EMA 15
EMA gap
VWAP or TWAP
ATR
session high and low
market regime
CE OI
PE OI
call wall
put wall
pin strike
PCR
IV
gamma
gamma flip
VIX
ATM CE price
ATM PE price
data timestamps used
data-quality flags
```

Calculate a strategy hash from the strategy source plus normalized settings. Save it with every run.

Do not add arbitrary Python execution inside the browser. Cursor can modify versioned strategy files; the portal controls settings and selects strategy versions.

## Local Historical Dataset

Use DuckDB for normalized local data and compressed JSON for immutable raw API responses.

Suggested structure:

```text
data/
  raw/
    dhan/
    csv-imports/
  twiq_backtest.duckdb
  manifests/
  exports/
```

Keep `data/` out of Git.

### Underlying Bars

```text
timestamp_ist
symbol
timeframe
open
high
low
close
volume
source
import_batch_id
```

### Option Bars And Chain Rows

```text
timestamp_ist
underlying
expiry_date
strike
option_side
relative_strike
open
high
low
close
ltp
volume
open_interest
implied_volatility
bid
ask
delta
gamma
source
import_batch_id
```

The unique contract identity is:

```text
expiry_date + strike + option_side
```

Never identify an option only by strike.

### VIX Bars

```text
timestamp_ist
open
high
low
close
source
import_batch_id
```

### Reference Data

Store:

- Trading calendar
- Weekly expiry calendar
- Expiry-day holiday adjustments
- Historical lot-size schedule
- Instrument metadata
- Import manifests
- API request parameters
- Dataset versions
- Data coverage and missing intervals

### Backtest Tables

Store:

- Runs
- Run settings
- Data manifests
- Signals
- Trades
- Daily summaries
- Equity points
- Data-quality warnings
- Run comparisons

## Dhan API Import

Add a resumable Dhan historical downloader.

### Required Data

1. NIFTY one-minute historical candles.
2. India VIX one-minute candles when available.
3. NIFTY near-weekly expired option data for:
   - CALL and PUT
   - ATM
   - ATM+1 through ATM+10
   - ATM-1 through ATM-10
4. Required option fields:
   - OHLC
   - IV
   - volume
   - OI
   - absolute strike
   - spot
   - timestamp
5. Expiry and lot-size reference information.

### Pilot Range

```text
From: 2025-12-24
Through: 2026-06-23
```

The end date sent to APIs must account for non-inclusive API semantics.

### Downloader Requirements

- Read credentials only from the local environment.
- Never save credentials in DuckDB, manifests, logs, or exports.
- Chunk ordinary intraday requests into no more than 90 days.
- Chunk rolling expired-option requests into no more than 30 days.
- Respect Dhan rate limits.
- Use retries with exponential backoff for transient failures.
- Stop and report authentication failures.
- Save successful raw responses before normalization.
- Resume incomplete imports without redownloading valid chunks.
- Record request hashes and response checksums.
- Show progress by dataset, date range, side, and relative strike.
- Provide cancel and resume controls.
- Do not delete a previous valid dataset during refresh.

## CSV Mapping Wizard

Support CSV data from MGrid or another source without assuming exact column names.

### Import Flow

1. Upload one or more CSV files.
2. Select dataset type:
   - NIFTY candles
   - NIFTY futures candles
   - Option bars
   - Option-chain snapshots
   - India VIX
   - Expiry calendar
   - Lot-size schedule
3. Detect delimiter, encoding, timestamp format, and headers.
4. Preview sample rows.
5. Map source columns to canonical fields.
6. Choose timezone and whether timestamps represent candle open or close.
7. Select long or wide option format.
8. Validate before importing.
9. Save the mapping profile for later files.
10. Create an import batch and coverage report.

Support:

- Long option rows with a `side` column.
- Wide chain rows with `ce_*` and `pe_*` columns.
- Separate CE and PE files.
- Separate files by date or expiry.
- Timestamp formats with and without timezone.
- Optional bid, ask, IV, delta, and gamma columns.

Required option fields for price-only tests:

```text
timestamp
expiry
strike
side
open
high
low
close
```

Additional required fields for full-context tests:

```text
open_interest
spot or synchronized NIFTY data
```

Gamma mode additionally requires either:

```text
gamma
```

or:

```text
implied_volatility + verified expiry timestamp
```

Reject files that cannot identify expiry, strike, side, and timestamp.

## Data Validation

Run validation before enabling a dataset.

Check:

- Duplicate timestamps
- Duplicate contract rows
- Out-of-order rows
- Invalid OHLC relationships
- Negative prices, volume, OI, IV, or gamma
- Missing expiry
- Missing side
- Invalid strike increments
- Timezone ambiguity
- Bars outside Indian market hours
- Missing trading days
- Missing decision-window candles
- Missing ATM rows
- Missing ATM ±10 coverage
- Stale option-chain information
- Contract discontinuities
- Incorrect lot sizes
- NIFTY and option timestamp mismatch
- Expiry contamination
- Suspicious spot jumps
- Option prices below tick size

Every day receives one status:

```text
valid
valid_with_warnings
excluded
```

Excluded days must never silently enter aggregate results.

## Timestamp Synchronization

This is a non-negotiable part of the implementation.

For a decision timestamp `T`:

1. Use only NIFTY strategy candles whose close timestamp is `<= T`.
2. Calculate indicators only from those completed candles.
3. Use the newest option-chain state whose timestamp is `<= T`.
4. Reject it if its age exceeds the configured staleness limit.
5. Require the option chain to use the intended weekly expiry.
6. Determine ATM from NIFTY spot at `T`.
7. Restrict chain calculations to ATM ±10.
8. Evaluate the strategy.
9. If a trade is accepted, enter using the first executable option bar after `T`.
10. Lock expiry, strike, and side for the entire trade.
11. Continue following that exact contract even when ATM changes later.

Default maximum chain staleness:

```text
75 seconds
```

Store the exact NIFTY timestamp, chain timestamp, VIX timestamp, and execution timestamp with every decision.

## Replay Rules

### Candle Preparation

- Store raw data at one-minute resolution.
- Aggregate to 1m, 3m, or 5m strategy candles.
- Align bars to Indian exchange time.
- Use 09:15–09:30 as indicator warm-up.
- Do not permit entries before 09:30.
- Evaluate only completed candles.
- Do not carry indicators across trading days unless explicitly configured.

### Entry

- Signal occurs at a completed strategy-candle close.
- Entry occurs on the first option bar after that close.
- Default fill is next option bar open plus entry slippage.
- Do not enter using a quote that helped create the signal.
- Save signal time and execution time separately.

### Position Tracking

Track:

```text
expiry
strike
side
quantity
lots
entry time
entry price
target
stop
time stop
peak price
trailing state
strategy version
settings snapshot
```

When using rolling ATM-relative Dhan data, locate the stored absolute strike at every future timestamp by matching it against the available rolling-strike rows. Do not keep reading the `ATM` series after ATM moves.

If the fixed contract leaves ATM ±10:

- Mark the trade as incomplete.
- Exclude it from trusted aggregate results.
- Display the reason.
- Do not substitute a different strike.

### Exits

Use one-minute option OHLC.

- Target touched: option high reaches target.
- Stop touched: option low reaches stop.
- Both touched in one minute: assume stop first.
- Time exit: use the first available option close at or after the deadline.
- Forced exit: close at 11:30.
- Missing exit data: mark trade incomplete, not profitable or losing.
- Apply exit slippage conservatively.

### Costs

Make these configurable:

```text
entry_slippage_rupees
exit_slippage_rupees
brokerage_per_lot_round_trip
STT
exchange charges
GST
SEBI charges
stamp duty
```

Initial comparison presets:

- Ideal: zero slippage, brokerage only
- Base: ₹0.50 entry and ₹0.50 exit slippage
- Stress: ₹1.00 entry and ₹1.00 exit slippage

Show gross and net results separately.

## Replay Modes

### Core Mode

Use:

- NIFTY EMA
- EMA slope
- EMA gap
- Candle body and direction
- VWAP/TWAP
- ATR and regime
- ATM selection
- Time window
- Risk sizing
- Target, stop, time exit, forced exit

### Full Context Mode

Use everything in Core Mode plus:

- CE and PE OI
- Call wall
- Put wall
- Pin strike
- PCR
- IV
- Gamma and gamma flip when valid
- India VIX when valid
- Bid/ask spread when available
- Expiry-day policy

The run must list which required features were available and which filters were disabled because data was missing.

## Backend API

Implement local endpoints equivalent to:

```text
POST   /api/data/dhan/sync
POST   /api/data/csv/preview
POST   /api/data/csv/import
GET    /api/data/imports
GET    /api/data/coverage
GET    /api/data/quality
POST   /api/backtests/runs
GET    /api/backtests/runs
GET    /api/backtests/runs/{run_id}
POST   /api/backtests/runs/{run_id}/cancel
GET    /api/backtests/runs/{run_id}/signals
GET    /api/backtests/runs/{run_id}/trades
GET    /api/backtests/runs/{run_id}/equity
GET    /api/backtests/runs/{run_id}/replay
POST   /api/backtests/compare
GET    /api/backtests/runs/{run_id}/export
GET    /api/strategies
GET    /api/strategies/{strategy_id}/settings-schema
```

Long-running imports and backtests must use a persisted local job queue.

Job states:

```text
queued
running
completed
failed
cancelled
interrupted
```

On application restart, unfinished jobs become `interrupted` and can be restarted safely.

## Backtesting Portal

Create four main views.

### 1. Data Manager

Show:

- Dhan credential status without displaying credentials
- Dataset coverage calendar
- Dhan sync controls
- CSV upload and mapping wizard
- Import progress
- Missing dates and intervals
- ATM ±10 coverage
- VIX, IV, gamma, bid/ask availability
- Dataset size and version
- Validation warnings
- Valid, warning, and excluded trading days

### 2. Strategy Lab

Controls:

- Strategy version
- Date range
- Core or Full Context mode
- Timeframe
- Trade window
- EMA periods and gap
- Candle-body threshold
- Target and stop
- Time stop
- Capital and risk settings
- Maximum trades
- Consecutive-loss halt
- OI wall parameters
- PCR thresholds
- Pin band
- Gamma settings
- VIX limit
- Expiry policy
- Trailing and dynamic-exit toggles
- Slippage and transaction-cost preset
- Save configuration
- Run backtest

### 3. Results

Summary metrics:

- Net P&L
- Gross P&L
- Return on capital
- Total trades
- Winning and losing trades
- Win rate
- Profit factor
- Expectancy
- Average win
- Average loss
- Risk/reward achieved
- Maximum drawdown
- Recovery factor
- Consecutive wins and losses
- Exposure time
- Trading days
- Profitable days
- Incomplete trades
- Excluded days
- Data-quality score

Breakdowns:

- By day
- By week
- By month
- By CE/PE
- By entry time
- By expiry day/non-expiry day
- By market regime
- By gamma regime
- By exit reason
- By strategy layer
- By EMA gap bucket
- By PCR bucket
- By VIX bucket

### 4. Historical Replay

Create a synchronized visual replay for any selected day or trade.

Display:

- NIFTY candlestick chart
- EMA 9 and EMA 15
- VWAP or TWAP
- Entry and skipped-signal markers
- Call wall, put wall, and pin levels
- ATM strike changes
- Selected option candlestick chart
- Entry, target, stop, time exit, and actual exit
- CE and PE OI by strike
- PCR timeline
- IV timeline
- Gamma/gamma-exposure timeline
- VIX timeline
- Market regime timeline
- Decision log with layer and reason
- Timestamp slider that updates all charts together

At every replay timestamp, display exactly which data rows were used.

## Charts

Bundle chart dependencies locally so the portal works without internet access.

Required charts:

- NIFTY candlestick with EMA/VWAP overlays
- Selected option candlestick
- CE versus PE OI horizontal strike chart
- OI wall and pin history
- PCR line
- IV line
- Gamma exposure by strike
- VIX line
- Equity curve
- Drawdown curve
- Daily P&L calendar
- Trade return distribution
- Cumulative gross versus net P&L
- Core versus Full Context comparison
- Multi-run comparison chart

Charts must resize correctly on desktop and mobile.

## Run Comparison

Allow selecting multiple saved runs.

Compare:

- Settings differences
- Strategy hashes
- Dataset versions
- Available/missing features
- Net P&L
- Drawdown
- Win rate
- Profit factor
- Expectancy
- Trade count
- Exit mix
- Daily overlap
- Trades added or removed by each filter
- Signals where Core and Full Context disagree

Do not implement automatic parameter optimization in the first release.

## Exports

Allow export of:

- Run configuration JSON
- Data-quality report
- Trade CSV
- Signal CSV
- Daily-results CSV
- Equity CSV
- Comparison CSV
- Human-readable HTML report

Exports must contain strategy version, settings hash, dataset version, and assumptions.

## Tests

### Unit Tests

Test:

- EMA calculations
- Candle aggregation
- ATM rounding
- ATM ±10 filtering
- OI walls
- PCR
- Pin strike
- Black-Scholes gamma
- Gamma exposure
- Expiry lookup
- Historical lot size
- VWAP versus TWAP labeling
- Signal layers
- Position sizing
- Slippage and charges
- Time stops
- Forced exits
- Consecutive-loss halt
- Contract tracking after ATM changes

### Look-Ahead Tests

Prove that:

- Unfinished candles cannot influence a signal.
- Future option snapshots cannot influence OI or entry decisions.
- Entry cannot occur at the signal-generating quote.
- Future expiry information cannot leak into earlier decisions.
- Later option highs/lows cannot influence entry.
- Strategy indicators reset correctly each day.

### Synchronization Tests

Create fixtures where NIFTY and option timestamps differ.

Verify:

- Latest earlier chain snapshot is selected.
- Future snapshot is never selected.
- Stale snapshot blocks the decision.
- Wrong expiry is rejected.
- Same strike from another expiry is never used.
- Fixed contract remains selected after ATM changes.

### Golden-Day Test

Create one small manually verified trading day containing:

- NIFTY candles
- ATM ±10 chain rows
- One CE trade
- One skipped PE signal
- Target or stop
- OI wall movement
- PCR movement

Record the exact expected decisions and trades. The golden test must remain stable across refactors.

### Shared-Engine Parity Test

Feed identical market contexts into forward and historical adapters. Require identical:

- Entry decisions
- Skip reasons
- Position size
- Target and stop
- Exit decisions

## Acceptance Criteria

The feature is complete only when:

1. The paper portal still works.
2. Dhan data can be downloaded and resumed locally.
3. CSV data can be mapped and imported.
4. Six months of NIFTY and options data can be validated.
5. NIFTY and option data are synchronized without future leakage.
6. ATM ±10 is used consistently in live and historical OI analysis.
7. A fixed option contract is followed after entry.
8. Core and Full Context runs can be saved and compared.
9. Every taken and skipped signal has a reason.
10. Results disclose missing gamma, VIX, spread, or VWAP inputs.
11. Repeating the same run produces the same hash and results.
12. A user can change settings and rerun without editing code.
13. Cursor can add a new versioned strategy module without changing the replay engine.
14. Replay charts show NIFTY, calls, puts, OI, PCR, IV, gamma, VIX, entries, and exits together.
15. No real-order API is introduced or called.

## Implementation Order

1. Canonicalize the existing strategy and defaults.
2. Extract the shared pure strategy interface.
3. Add DuckDB schema and import manifests.
4. Build data-quality validators.
5. Implement Dhan downloader.
6. Implement CSV mapping and import.
7. Implement timestamp synchronization.
8. Implement the deterministic replay engine.
9. Add run persistence and comparison APIs.
10. Build Data Manager and Strategy Lab.
11. Build Results and Historical Replay.
12. Add exports.
13. Add unit, synchronization, look-ahead, golden-day, and parity tests.
14. Run a short one-day validation.
15. Run one week.
16. Run the complete six-month pilot.
17. Compare Core and Full Context results.
18. Document all assumptions and remaining historical-data limitations.
