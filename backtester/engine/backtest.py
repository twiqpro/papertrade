"""Bar-by-bar backtest loop and decision logger."""

from __future__ import annotations

import traceback
from datetime import datetime
from typing import Any

import pandas as pd

from .context import Context, Position
from .snapshot import MarketSnapshot


def _fmt_ts(ts: datetime) -> str:
    if hasattr(ts, "isoformat"):
        return ts.isoformat(sep=" ", timespec="seconds")
    return str(ts)


def _duration(entry: datetime, exit: datetime) -> str:
    delta = exit - entry
    total_sec = int(delta.total_seconds())
    hours, rem = divmod(total_sec, 3600)
    minutes, seconds = divmod(rem, 60)
    if hours:
        return f"{hours}h {minutes}m"
    if minutes:
        return f"{minutes}m {seconds}s"
    return f"{seconds}s"


def _pnl_points(direction: str, entry: float, exit_price: float, qty: int) -> float:
    raw = exit_price - entry
    if direction.upper() == "SELL":
        raw = -raw
    return round(raw * qty, 2)


def run_backtest(strategy: Any, df: pd.DataFrame) -> dict:
    """Run strategy bar-by-bar; return decisions, trades, summary."""
    ctx = Context()
    decisions: list[dict] = []
    cum_pnl = 0.0
    error: dict | None = None

    setup = getattr(strategy, "setup", None)
    if setup is not None:
        try:
            setup(ctx)
        except Exception as exc:
            return {
                "decisions": [],
                "trades": [],
                "summary": _empty_summary(),
                "error": {
                    "message": f"setup() failed: {exc}",
                    "traceback": traceback.format_exc(),
                },
            }

    if df.empty:
        return {
            "decisions": [],
            "trades": [],
            "summary": _empty_summary(),
            "error": {"message": "No data to backtest"},
        }

    grouped = df.groupby("timestamp", sort=True)
    row_num = 0

    for ts, bar_df in grouped:
        snapshot = MarketSnapshot(ts, bar_df)
        ctx.bind_snapshot(snapshot)

        try:
            strategy.on_bar(snapshot, ctx)
        except Exception as exc:
            row_num += 1
            decisions.append(
                _blank_row(row_num, ts, snapshot.spot, cum_pnl, decision="ERROR", reason=str(exc))
            )
            error = {
                "message": str(exc),
                "traceback": traceback.format_exc(),
                "bar": _fmt_ts(ts),
            }
            break

        action = ctx.consume_pending()
        row_num += 1
        row = _blank_row(row_num, ts, snapshot.spot, cum_pnl)

        decision = action["decision"]
        row["decision"] = decision
        row["reason"] = action.get("reason", "")
        if action.get("notes"):
            row["notes"] = action["notes"]
        if action.get("implicit_hold"):
            row["implicit_hold"] = True

        if decision == "ENTER":
            side = action["side"]
            strike = action["strike"]
            direction = action["direction"]
            qty = action["qty"]
            opt = snapshot.option(strike, side)
            fill = action.get("entry_price")
            if fill is None:
                fill = opt.close if opt.close is not None else 0.0
            else:
                fill = float(fill)
            ctx.position = Position(
                side=side,
                strike=strike,
                direction=direction,
                entry_price=fill,
                entry_time=ts,
                qty=qty,
            )
            row.update(
                {
                    "side": side,
                    "direction": direction,
                    "strike": strike,
                    "fill_price": fill,
                    "qty": qty,
                }
            )

        elif decision == "EXIT" and ctx.position is not None:
            pos = ctx.position
            opt = snapshot.option(pos.strike, pos.side)
            fill = opt.close if opt.close is not None else 0.0
            pnl = _pnl_points(pos.direction, pos.entry_price, fill, pos.qty)
            cum_pnl = round(cum_pnl + pnl, 2)
            row.update(
                {
                    "side": pos.side,
                    "direction": pos.direction,
                    "strike": pos.strike,
                    "fill_price": fill,
                    "qty": pos.qty,
                    "pnl": pnl,
                    "cum_pnl": cum_pnl,
                }
            )
            ctx.position = None

        elif decision == "HOLD" and ctx.position is not None:
            pos = ctx.position
            row.update(
                {
                    "side": pos.side,
                    "direction": pos.direction,
                    "strike": pos.strike,
                    "qty": pos.qty,
                }
            )

        row["cum_pnl"] = cum_pnl
        decisions.append(row)

    trades = _build_trades(decisions)
    summary = _build_summary(decisions, trades)

    result: dict = {"decisions": decisions, "trades": trades, "summary": summary}
    if error:
        result["error"] = error
    return result


def _blank_row(
    num: int,
    ts: datetime,
    spot: float,
    cum_pnl: float,
    decision: str = "HOLD",
    reason: str = "",
) -> dict:
    spot_val = ""
    if spot is not None:
        try:
            s = float(spot)
            spot_val = round(s, 2) if s == s else ""  # NaN check
        except (TypeError, ValueError):
            spot_val = ""
    return {
        "#": num,
        "timestamp": _fmt_ts(ts),
        "decision": decision,
        "side": "",
        "direction": "",
        "strike": "",
        "spot": spot_val,
        "fill_price": "",
        "qty": "",
        "reason": reason,
        "pnl": "",
        "cum_pnl": cum_pnl,
        "notes": "",
    }


def _empty_summary() -> dict:
    return {
        "total_trades": 0,
        "win_rate": 0.0,
        "total_pnl": 0.0,
        "max_drawdown": 0.0,
        "skip_count": 0,
        "bar_count": 0,
    }


def _build_trades(decisions: list[dict]) -> list[dict]:
    trades: list[dict] = []
    open_entry: dict | None = None

    for row in decisions:
        if row["decision"] == "ENTER":
            open_entry = row
        elif row["decision"] == "EXIT" and open_entry is not None:
            entry_ts = open_entry["timestamp"]
            exit_ts = row["timestamp"]
            trades.append(
                {
                    "entry_time": entry_ts,
                    "exit_time": exit_ts,
                    "side": row.get("side") or open_entry.get("side"),
                    "strike": row.get("strike") or open_entry.get("strike"),
                    "direction": row.get("direction") or open_entry.get("direction"),
                    "entry": open_entry.get("fill_price"),
                    "exit": row.get("fill_price"),
                    "qty": open_entry.get("qty") or row.get("qty"),
                    "pnl": row.get("pnl"),
                    "hold_duration": _duration(
                        datetime.fromisoformat(str(entry_ts)),
                        datetime.fromisoformat(str(exit_ts)),
                    ),
                }
            )
            open_entry = None

    return trades


def _build_summary(decisions: list[dict], trades: list[dict]) -> dict:
    skip_count = sum(1 for d in decisions if d.get("decision") == "SKIP")
    pnls = [float(t["pnl"]) for t in trades if t.get("pnl") not in ("", None)]
    wins = sum(1 for p in pnls if p > 0)
    total_trades = len(trades)
    win_rate = round(100.0 * wins / total_trades, 1) if total_trades else 0.0
    total_pnl = round(sum(pnls), 2) if pnls else 0.0

    peak = 0.0
    max_dd = 0.0
    for d in decisions:
        c = d.get("cum_pnl")
        if c == "" or c is None:
            continue
        c = float(c)
        peak = max(peak, c)
        max_dd = max(max_dd, peak - c)

    return {
        "total_trades": total_trades,
        "win_rate": win_rate,
        "total_pnl": total_pnl,
        "max_drawdown": round(max_dd, 2),
        "skip_count": skip_count,
        "bar_count": len(decisions),
    }
