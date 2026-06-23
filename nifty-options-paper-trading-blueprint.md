# NIFTY 50 Options Paper Trading Blueprint

## Goal

Build a paper trading system for a short-window NIFTY 50 ATM options strategy.

The first version should not place live orders. It should:

- Observe live NIFTY and ATM option prices.
- Generate entries only between 09:30 and 11:30 IST.
- Simulate buy and exit decisions.
- Store every signal, simulated order, tick/candle snapshot, and result.
- Produce backtest and forward-test reports.

This is a high-risk intraday options strategy. The system should be treated as a measurement tool first, not an auto-trading bot. Only move to real orders after enough clean forward-test data.

## Strategy v1

### Trading Window

- Market open reference: 09:15 IST.
- Active trading window: 09:30 to 11:30 IST.
- No fresh entries after 11:30.
- Force exit all open paper positions by a configured cutoff, for example 11:30 or 11:45.

### Instrument Universe

- Underlying: NIFTY 50 index.
- Option segment: NFO.
- Expiry: nearest weekly expiry by default.
- Strike: ATM strike nearest to current NIFTY spot or futures reference.
- Side: either ATM CE or ATM PE, depending on directional signal.

### Core Entry Idea

The trade is a quick scalp:

- Buy selected ATM option.
- Target: +2 rupees from entry price.
- Stop loss: -10 rupees from entry price.
- Time stop: exit if target/stop is not hit within a configured number of minutes.

Important: a 2 rupee target can be smaller than spread + slippage during fast markets. The paper engine must model bid/ask or conservative slippage, otherwise results will look better than real execution.

## Signal Rules

### EMA Filter

Use 9 EMA and 15 EMA on the NIFTY underlying candle series.

Do not trade when the EMAs are too close.

Suggested measurable rule:

```text
ema_gap = abs(ema_9 - ema_15)
min_gap = max(3 points, 0.03% of NIFTY price)

trade_allowed = ema_gap >= min_gap
```

This threshold should be configurable and tested.

### Direction

Initial directional rule:

```text
if ema_9 > ema_15 and ema_gap is wide enough:
    prefer ATM CE

if ema_9 < ema_15 and ema_gap is wide enough:
    prefer ATM PE
```

Optional confirmation filters for later:

- Candle close above/below both EMAs.
- Current candle body not too small.
- Option LTP has acceptable spread and volume.
- Avoid trade immediately after a large candle.
- Avoid first 5 to 10 minutes after 09:15 to reduce opening noise.

## Required Missing Decisions

Before coding the strategy engine, define these values:

- Candle timeframe for EMA: 1 minute, 3 minute, or 5 minute.
- EMA source: close price, typical price, or live tick-updated candle close.
- ATM reference: NIFTY spot, NIFTY futures, or option chain nearest strike.
- Expiry selection: current weekly expiry, next weekly expiry on expiry day, or configurable.
- Stop loss: fixed rupees, percentage, or time-based only.
- Re-entry rule: allow multiple trades per day or one trade per direction?
- Daily risk limit: max trades, max loss, and max consecutive losses.
- Paper fill model: LTP, bid/ask, or LTP plus slippage.

## Recommended Architecture

### 1. Broker and Data Layer

Responsibilities:

- Authenticate with the selected broker API.
- Download or load instruments for index and options contracts.
- Resolve NIFTY option symbols for expiry and ATM strike.
- Stream live ticks using the broker's live market feed.
- Fetch historical candles for backtesting.
- Store raw market data needed to replay decisions.

Use a broker adapter interface so Dhan can be the primary integration while Zerodha remains optional.

Suggested adapter methods:

```python
class BrokerDataAdapter:
    def authenticate(self): ...
    def get_instruments(self): ...
    def get_ltp(self, instruments): ...
    def get_historical_candles(self, instrument, from_time, to_time, timeframe): ...
    def subscribe_ticks(self, instruments, on_tick): ...
    def get_option_chain(self, underlying, expiry): ...
```

For v1, implement `DhanAdapter` first.

## Dhan API Integration

Dhan should be the first broker/data integration if your subscription is active.

Useful Dhan API areas:

- Authentication using `client_id` and `access_token`.
- Instrument list for resolving NIFTY and option contracts.
- Market quote for LTP and snapshot data.
- Live market feed for forward paper trading.
- Historical data for backtesting.
- Expired options data for older option backtests.
- Option chain for expiry/strike discovery.

Suggested local config:

```text
DHAN_CLIENT_ID=...
DHAN_ACCESS_TOKEN=...
BROKER=dhan
PAPER_TRADING_ONLY=true
```

Do not put access tokens directly in source files. Keep them in a local `.env` file that is never committed.

### 2. Candle Builder

Responsibilities:

- Convert ticks into 1-minute candles.
- Maintain rolling candles for NIFTY and selected options.
- Calculate EMA 9 and EMA 15.
- Mark candle completion times cleanly.

For backtests, this same module should consume historical candle data so live and backtest logic stay consistent.

### 3. Strategy Engine

Responsibilities:

- Read latest candles and current time.
- Check trading window.
- Check EMA gap filter.
- Select CE or PE.
- Emit a signal object.

Example signal:

```json
{
  "timestamp": "2026-06-22T09:43:00+05:30",
  "underlying": "NIFTY 50",
  "side": "CE",
  "strike": 23500,
  "expiry": "2026-06-25",
  "reason": "ema_9_above_ema_15_gap_ok",
  "ema_9": 23482.4,
  "ema_15": 23475.8,
  "ema_gap": 6.6
}
```

### 4. Paper Broker

Responsibilities:

- Simulate order placement.
- Simulate fills.
- Track open positions.
- Apply target, stop loss, time stop, and forced exit.
- Record realized P&L after brokerage/slippage assumptions.

For options, include:

- Lot size.
- Entry price.
- Exit price.
- Quantity.
- Simulated slippage.
- Charges estimate if you want realistic net P&L.

### 5. Risk Manager

Responsibilities:

- Block trades outside time window.
- Enforce max trades per day.
- Enforce max daily loss.
- Enforce max open positions.
- Prevent duplicate entries in the same candle.
- Stop trading after repeated failed paper trades.

### 6. Storage

Start with SQLite because it is simple, inspectable, and enough for v1.

Suggested tables:

- `ticks`
- `candles`
- `signals`
- `paper_orders`
- `paper_trades`
- `positions`
- `daily_summary`
- `strategy_config`

### 7. Reports

Minimum report fields:

- Total trades.
- Win rate.
- Average win.
- Average loss.
- Net P&L.
- Max drawdown.
- Trades by time bucket.
- CE vs PE performance.
- Slippage-adjusted P&L.
- Day-wise P&L.
- Reason for every skipped signal.

## Backtest Flow

1. Download historical NIFTY candles.
2. Download historical ATM option candles for each tested day.
3. Reconstruct which strike was ATM at each decision time.
4. Run the same strategy engine used in live paper trading.
5. Simulate fills with conservative slippage.
6. Generate a day-wise and trade-wise report.

Key warning: option backtests are tricky because the ATM symbol changes as NIFTY moves. The backtester must resolve the correct option contract at each timestamp instead of testing a single static symbol all day.

## Forward-Test Flow

1. Load Dhan credentials from local environment.
2. Load instrument master.
3. Start NIFTY tick stream around 09:15.
4. Begin signal evaluation at 09:30.
5. On valid signal, create paper order.
6. Track option LTP until target, stop, or time exit.
7. Save all decisions, including skipped trades.
8. Generate report after 11:30 or market close.

## Suggested Project Structure

```text
twiq-trader/
  app/
    config.py
    main.py
    broker/
      dhan_client.py
      broker_adapter.py
      paper_broker.py
    data/
      instruments.py
      candle_builder.py
      storage.py
    strategy/
      nifty_atm_scalper.py
      indicators.py
      risk.py
    backtest/
      runner.py
      option_resolver.py
      report.py
    dashboard/
      streamlit_app.py
  data/
    market.db
  reports/
  .env.example
  requirements.txt
```

## Build Phases

### Phase 1: Offline Backtest Skeleton

- Implement config.
- Implement EMA calculation.
- Implement strategy decision logic.
- Use CSV or historical candles as input.
- Generate trade list and summary report.

### Phase 2: Dhan Data Integration

- Add Dhan authentication and client setup.
- Fetch or load instruments.
- Resolve NIFTY ATM option.
- Fetch historical candles.
- Store raw data locally.

### Phase 3: Live Paper Trading

- Add Dhan live market feed.
- Build live candles.
- Run strategy from 09:30 to 11:30.
- Simulate paper orders.
- Store all events.

### Phase 4: Dashboard

- Show current state.
- Show active paper position.
- Show latest signal and reason.
- Show skipped-trade reasons.
- Show daily P&L.
- Show backtest report.

### Phase 5: Real Execution Gate

Only after forward testing:

- Add real order adapter.
- Keep paper and real broker behind the same interface.
- Add kill switch.
- Add max-loss hard stop.
- Add manual approval mode before full automation.

## Safe Defaults for v1

```text
candle_timeframe = 1 minute
trade_start = 09:30
trade_end = 11:30
target_rupees = 2
stop_loss_rupees = 10
time_stop_minutes = 3
max_trades_per_day = 5
max_consecutive_losses = 2
max_daily_loss_rupees = configurable
slippage_rupees_per_side = 0.5
ema_gap_min_points = 3
```

## First Implementation Target

The best first milestone is:

> Given historical NIFTY and option candles for one day, produce a trade-by-trade report for this exact strategy.

Once that works, connect Zerodha live data and run the same engine in paper mode.
