# Options Backtesting Platform

A minimal single-page app: drag-and-drop or paste a Python strategy, click **Run Strategy**, and get a bar-by-bar decision table (ENTER / EXIT / SKIP / HOLD) for Nifty options backtests.

## Quick start

```bash
cd backtester
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
uvicorn app:app --reload --port 8080
```

Open http://localhost:8080

1. Choose dates and interval, set strikes ±ATM, click **Download data**
2. Edit or drop a `.py` strategy file
3. Click **Run Strategy**

## Strategy API

Your file must define `class Strategy`:

```python
class Strategy:
    def setup(self, ctx):
        # optional — set ctx.params here
        ...

    def on_bar(self, snapshot, ctx):
        # Must end with exactly one of:
        #   ctx.enter(side=..., strike=..., direction=..., qty=..., reason=...)
        #   ctx.exit(reason=...)
        #   ctx.skip(reason="...")   # logged with reason
        #   ctx.hold()
        ...
```

### `snapshot` (read-only)

- `snapshot.timestamp`
- `snapshot.spot` — Nifty spot close
- `snapshot.atm_strike` — nearest 50-point strike
- `snapshot.option(strike, opt_type)` → `.open .high .low .close .oi .oi_chg .volume .iv`
- `snapshot.by_offset(n, opt_type)` — n strikes from ATM (negative = below)
- `snapshot.chain` — pandas DataFrame of all strikes this bar

### `ctx` (actions + position)

- `ctx.position` — open position or `None`
- `ctx.enter(side, strike, direction="BUY", qty=1, reason="")` — `side` CE/PE; `strike` int, `"ATM"`, or use in combination with offsets
- `ctx.exit(reason="")`
- `ctx.skip(reason="...")` — **always log why you skipped**
- `ctx.hold()`
- `ctx.params` — dict from `setup()`
- `ctx.log(msg)` — free-form note on the row
- `ctx.atm_offset(n)` — strike n steps from ATM

If `on_bar` returns without calling enter/exit/skip/hold, the engine treats it as `hold()` and flags it.

## Data sources (fixed)

| Data | Source |
|------|--------|
| NIFTY spot OHLC | Yahoo Finance |
| ATM implied volatility | Yahoo Finance |
| Options CE/PE (ATM ±N) | Dhan API |

Interval, date range, and strike range are shared across both sources. Until APIs are wired, download falls back to synthetic demo data.

| Status | Module |
|--------|--------|
| Yahoo spot + IV | `data/providers/yahoo_api.py` — TODO |
| Dhan options | `data/providers/helm_api.py` — TODO |
| Demo fallback | `data/synthetic.py` |

Normalized long schema: `timestamp`, spot OHLC, `strike`, `opt_type` (CE/PE), option OHLC, `oi`, `oi_chg`, `volume`, `iv`.

## Assumptions (v1)

- Qty in lots, **0 slippage**
- P&L: BUY → `exit - entry`; SELL → inverted
- Lot size / margin not modeled yet (TODO)

## Security

This runs **arbitrary Python** from the code box in a subprocess (60s timeout). Intended for **local/personal use only**. Do not expose publicly without proper sandboxing.

## Project layout

```
backtester/
├── app.py              # FastAPI routes
├── frontend/           # Single-page UI
├── engine/             # Backtest loop, snapshot, context, runner
├── data/               # feed.py + providers + parquet cache
└── strategies/         # example_strategy.py
```
