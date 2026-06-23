# NIFTY Options Paper Trader UI

This is the first local dashboard shell for the NIFTY ATM options paper trading system.

Open it at:

```text
http://127.0.0.1:4173
```

Or open `index.html` directly in a browser.

## What v1 Shows

- Capital budget input.
- Daily risk, target, stop loss, EMA gap, and max trade controls.
- 1m, 3m, and 5m timeframe selector.
- Paper session pause/resume.
- Current market state placeholder.
- ATM CE and PE placeholder prices.
- Signal and skipped-trade log.
- Paper trade log.
- Open strategy decisions.

## Questions To Lock Down

1. Should EMA be calculated on 1 minute, 3 minute, or 5 minute candles?
2. Should the stop loss stay fixed at Rs 10, or should it become volatility/time based later?
3. Should the strategy stop after 2 consecutive losses?
4. Should ATM strike selection use NIFTY spot or NIFTY futures?
5. Should expiry roll to next weekly expiry on expiry day?
6. How many total trades are allowed per day?
7. Should the paper fill model use LTP, bid/ask, or LTP plus fixed slippage?

## Next Build Step

Replace placeholder data in `app.js` with a small local API:

- `GET /api/state`
- `GET /api/signals`
- `GET /api/trades`
- `POST /api/settings`
- `POST /api/session/pause`
- `POST /api/session/resume`

After that, connect the API to the strategy engine and Dhan paper-feed layer.

## Broker Preference

Use Dhan as the primary broker/data provider:

- Load credentials from local environment variables.
- Keep the UI in paper-trading mode by default.
- Keep broker logic behind an adapter so Zerodha or another broker can be added later without rewriting the strategy or UI.
