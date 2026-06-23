# Twiq NIFTY Options Paper Trader

Offline-first v1 for a NIFTY ATM options paper-trading dashboard. Everything runs on this laptop through localhost.

## What Is Built

- Static local frontend.
- FastAPI local backend.
- Paper-trading dashboard API.
- Configurable strategy settings.
- Demo market simulator.
- Dhan-first broker adapter placeholder.
- Safe default: paper trading only.

Current v1 strategy defaults:

```text
Trading window: 09:30-11:30 IST
Target: Rs 2
Stop loss: Rs 5
Lot size: 65
EMA: 9 / 15
EMA minimum gap: 3 points
Timeframe: 5m
Max trades/day: 5
Fill model: LTP + Rs 0.50 slippage per side
Broker: Dhan
Mode: Paper trading
```

## One Command Local Start

```bash
bash run-local.sh
```

Open:

```text
http://127.0.0.1:4174
```

This keeps both the frontend and backend on your laptop.

## Local Backend Only

```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

Health check:

```text
http://127.0.0.1:8000/health
```

Dashboard API:

```text
http://127.0.0.1:8000/api/dashboard
```

## Local Frontend

```bash
cd frontend
python3 -m http.server 4174
```

Open:

```text
http://127.0.0.1:4174
```

`frontend/config.js` controls the local backend URL:

```js
window.TWIQ_API_BASE_URL = "http://127.0.0.1:8000";
```

## Offline Rule

Do not deploy this app while the strategy is still being tested.

Keep these local:

- Dhan credentials.
- Trade logs.
- Tick data.
- Backtest reports.
- Paper-trading results.

When Dhan is connected, store credentials in `backend/.env` and do not share that file.

## Next Engineering Steps

1. Replace demo market state with Dhan market quote.
2. Add Dhan live market feed.
3. Add instrument and option-chain resolver.
4. Store ticks, signals, and trades in SQLite/Postgres.
5. Add backtest runner using historical and expired options data.
6. Add local password protection if you want another safety layer on the laptop.
