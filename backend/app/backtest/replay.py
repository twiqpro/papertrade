from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from ..models import StrategySettings
from ..signal_engine import CandleBar, OptionQuote
from ..strategy import LOT_SIZE, MARKET_CLOSE, parse_hhmm
from ..strategy_module import get_strategy_v1
from ..strategy_module.base import AccountState, Decision
from ..strategy_module.v1_nifty_atm import hash_settings
from .candles import aggregate_from_timestamps
from .context_builder import build_context_from_chain_rows
from .costs import trade_pnl
from .db import get_connection
from .exits import evaluate_bar_exit
from .jobs import get_job, update_job
from .reference import is_trading_day
from .repository import list_equity, save_equity_point, save_signal, save_trade
from .sync import completed_candles_before, filter_atm_window, select_chain_snapshot, select_vix
from .validators import validate_day

IST = ZoneInfo("Asia/Kolkata")


@dataclass
class PendingEntry:
    decision: Decision
    signal_time: datetime
    context: object


def _load_day_nifty(conn, trading_date: date) -> list[tuple[datetime, CandleBar]]:
    rows = conn.execute(
        """
        SELECT timestamp_ist, open, high, low, close, volume
        FROM underlying_bars WHERE symbol='NIFTY' AND CAST(timestamp_ist AS DATE)=?
        ORDER BY timestamp_ist
        """,
        [trading_date],
    ).fetchall()
    result = []
    for row in rows:
        ts = row[0]
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=IST)
        result.append((ts, CandleBar(row[1], row[2], row[3], row[4], row[5] or 0)))
    return result


def _load_chain_snapshots(conn, trading_date: date) -> list[tuple[datetime, list[dict]]]:
    rows = conn.execute(
        """
        SELECT timestamp_ist, expiry_date, strike, option_side, open_interest, ltp, bid, ask,
               implied_volatility, delta, gamma, open, high, low, close
        FROM option_bars WHERE CAST(timestamp_ist AS DATE)=?
        ORDER BY timestamp_ist, strike
        """,
        [trading_date],
    ).fetchall()
    by_ts: dict[datetime, list[dict]] = {}
    for row in rows:
        ts = row[0].replace(tzinfo=IST) if row[0].tzinfo is None else row[0]
        by_ts.setdefault(ts, []).append(
            {
                "expiry": str(row[1]),
                "strike": int(row[2]),
                "side": row[3],
                "oi": int(row[4] or 0),
                "ltp": float(row[5] or 0),
                "bid": float(row[6] or 0),
                "ask": float(row[7] or 0),
                "iv": float(row[8] or 0),
                "delta": float(row[9] or 0),
                "gamma": float(row[10] or 0),
                "open": float(row[11] or 0),
                "high": float(row[12] or 0),
                "low": float(row[13] or 0),
                "close": float(row[14] or 0),
            }
        )
    return sorted(by_ts.items())


def _contract_bar(rows: list[dict], strike: int, side: str) -> dict | None:
    for row in rows:
        if row["strike"] == strike and row["side"] == side:
            return row
    return None


def _decision_to_dict(decision: Decision) -> dict:
    return {k: (v.isoformat() if isinstance(v, datetime) else v) for k, v in decision.__dict__.items()}


def replay_day(
    trading_date: date,
    settings: StrategySettings,
    run_id: str,
    equity_state: dict,
) -> dict:
    quality = validate_day(trading_date)
    if quality["status"] == "excluded":
        return {"date": str(trading_date), "skipped": True, "reason": quality["warnings"], "pnl": 0.0}

    strategy = get_strategy_v1()
    from ..signal_engine import reset_entry_bar_tracking

    reset_entry_bar_tracking()
    conn = get_connection()
    signals_count = 0
    trades_count = 0
    day_pnl = 0.0

    try:
        nifty_1m = _load_day_nifty(conn, trading_date)
        if not nifty_1m:
            return {"date": str(trading_date), "skipped": True, "reason": ["No NIFTY data"], "pnl": 0.0}

        tf_minutes = int(settings.timeframe.replace("m", ""))
        strategy_candles = aggregate_from_timestamps(nifty_1m, tf_minutes)
        strategy_close_times = {ts for ts, _ in strategy_candles}
        chain_snaps = _load_chain_snapshots(conn, trading_date)
        chain_by_ts = {ts: rows for ts, rows in chain_snaps}
        vix_rows = [
            (r[0].replace(tzinfo=IST) if r[0].tzinfo is None else r[0], float(r[1]))
            for r in conn.execute(
                "SELECT timestamp_ist, close FROM vix_bars WHERE CAST(timestamp_ist AS DATE)=? ORDER BY timestamp_ist",
                [trading_date],
            ).fetchall()
        ]

        trade_start = parse_hhmm(settings.trade_start)

        open_position = None
        pending_entry: PendingEntry | None = None
        account = AccountState(remaining_daily_budget=settings.daily_risk)
        cumulative = equity_state.get("cumulative_pnl", 0.0)
        peak_equity = equity_state.get("peak_equity", settings.capital_budget)

        for ts, nifty_bar in nifty_1m:
            if ts.time() < trade_start:
                continue
            if ts.time() > MARKET_CLOSE and open_position is None and pending_entry is None:
                break

            chain_ts, chain_rows, stale_reason = select_chain_snapshot(
                chain_snaps, ts, settings.chain_staleness_seconds
            )
            minute_rows = chain_by_ts.get(ts, chain_rows if chain_ts == ts else [])

            if open_position is not None:
                ob = _contract_bar(minute_rows, open_position.strike, open_position.side)
                if ob is None:
                    trade_payload = {
                        "id": str(uuid.uuid4()),
                        "status": "incomplete",
                        "reason": "Contract left ATM±10 data window",
                        "side": open_position.side,
                        "strike": open_position.strike,
                        "entry_time": open_position.entry_time.isoformat(),
                    }
                    save_trade(run_id, trading_date, trade_payload)
                    trades_count += 1
                    open_position = None
                    account.has_open_position = False
                else:
                    quote = OptionQuote(ob["ltp"], ob["bid"], ob["ask"], ob["oi"], ob["iv"], ob["delta"])
                    ctx = build_context_from_chain_rows(
                        nifty_bar.close,
                        open_position.expiry,
                        filter_atm_window(minute_rows, nifty_bar.close, settings.option_chain_window),
                        completed_candles_before(strategy_candles, ts),
                        select_vix(vix_rows, ts)[1],
                        settings.option_chain_window,
                    )
                    exit_decision = evaluate_bar_exit(
                        strategy,
                        ts,
                        open_position,
                        ob["open"],
                        ob["high"],
                        ob["low"],
                        ob["close"],
                        quote,
                        ctx,
                        settings,
                    )
                    if exit_decision is None and ts.time() >= MARKET_CLOSE:
                        exit_decision = evaluate_bar_exit(
                            strategy,
                            ts.replace(hour=MARKET_CLOSE.hour, minute=MARKET_CLOSE.minute, second=0),
                            open_position,
                            ob["high"],
                            ob["low"],
                            ob["close"],
                            quote,
                            ctx,
                            settings,
                        )
                    if exit_decision:
                        gross, net = trade_pnl(
                            open_position.entry_price,
                            exit_decision.exit_price,
                            open_position.quantity,
                            open_position.lots,
                            settings,
                        )
                        day_pnl += net
                        cumulative += net
                        if net < 0:
                            account.consecutive_losses += 1
                        else:
                            account.consecutive_losses = 0
                        if account.consecutive_losses >= settings.max_consecutive_losses:
                            account.halted = True
                        if not settings.use_full_capital:
                            account.remaining_daily_budget = max(0.0, account.remaining_daily_budget + net)
                            if account.remaining_daily_budget <= 0:
                                account.halted = True
                        peak_equity = max(peak_equity, settings.capital_budget + cumulative)
                        drawdown = (settings.capital_budget + cumulative) - peak_equity
                        save_equity_point(run_id, exit_decision.exit_time, settings.capital_budget + cumulative, drawdown)
                        save_trade(
                            run_id,
                            trading_date,
                            {
                                "id": str(uuid.uuid4()),
                                "side": open_position.side,
                                "strike": open_position.strike,
                                "entry_time": open_position.entry_time.isoformat(),
                                "entry_price": round(open_position.entry_price, 2),
                                "execution_time": open_position.entry_time.isoformat(),
                                "signal_time": (
                                    open_position.signal_time.isoformat()
                                    if open_position.signal_time
                                    else open_position.entry_time.isoformat()
                                ),
                                "exit_time": exit_decision.exit_time.isoformat(),
                                "exit_price": round(exit_decision.exit_price, 2),
                                "result": exit_decision.result,
                                "pnl": net,
                                "gross_pnl": gross,
                                "lots": open_position.lots,
                            },
                        )
                        trades_count += 1
                        account.trades_today += 1
                        open_position = None
                        account.has_open_position = False
                        if exit_decision.result in ("Stop", "Trail") and settings.cooldown_enabled:
                            account.has_open_position = False

            if pending_entry is not None and open_position is None and ts > pending_entry.signal_time:
                ob = _contract_bar(minute_rows, pending_entry.decision.strike, pending_entry.decision.side or "CE")
                if ob is not None:
                    exec_quote = OptionQuote(
                        ob["open"] or ob["ltp"],
                        ob["bid"],
                        ob["ask"],
                        ob["oi"],
                        ob["iv"],
                        ob["delta"],
                    )
                    decision = pending_entry.decision
                    decision.timestamp = ts
                    open_position = strategy.create_position(decision, exec_quote, settings, pending_entry.context)
                    open_position.entry_time = ts
                    open_position.signal_time = pending_entry.signal_time
                    account.has_open_position = True
                    pending_entry = None

            if ts in strategy_close_times and open_position is None and pending_entry is None:
                completed_bars = completed_candles_before(strategy_candles, ts)
                if not completed_bars:
                    continue
                bar = next(bar for close_ts, bar in strategy_candles if close_ts == ts)
                spot = bar.close
                if chain_ts is None:
                    continue
                expiry = chain_rows[0]["expiry"] if chain_rows else trading_date.isoformat()
                vix_ts, vix = select_vix(vix_rows, ts)
                ctx = build_context_from_chain_rows(
                    spot,
                    expiry,
                    filter_atm_window(chain_rows, spot, settings.option_chain_window),
                    completed_bars,
                    vix,
                    settings.option_chain_window,
                )
                if stale_reason:
                    decision = strategy.evaluate_entry(ts, ctx, account, settings)
                    payload = _decision_to_dict(decision)
                    payload["status"] = "Skipped"
                    payload["reason"] = stale_reason
                    save_signal(run_id, trading_date, payload)
                    signals_count += 1
                    continue

                decision = strategy.evaluate_entry(ts, ctx, account, settings)
                payload = _decision_to_dict(decision)
                payload["signal_time"] = ts.isoformat()
                payload["chain_timestamp"] = chain_ts.isoformat() if chain_ts else None
                payload["vix_timestamp"] = vix_ts.isoformat() if vix_ts else None

                if decision.status == "Taken" and decision.side is not None:
                    if account.halted:
                        payload["status"] = "Skipped"
                        payload["reason"] = "Entry blocked: Daily halt active"
                    elif account.trades_today >= settings.max_trades_per_day:
                        payload["status"] = "Skipped"
                        payload["reason"] = (
                            f"Entry blocked: Max trades per day ({settings.max_trades_per_day}) reached"
                        )
                    elif not settings.use_full_capital:
                        stop_risk = settings.stop_loss_rupees * LOT_SIZE
                        if account.remaining_daily_budget < stop_risk:
                            payload["status"] = "Skipped"
                            payload["reason"] = "Entry blocked: Daily risk budget exhausted"

                save_signal(run_id, trading_date, payload)
                signals_count += 1

                if payload["status"] == "Taken" and decision.side is not None:
                    pending_entry = PendingEntry(decision=decision, signal_time=ts, context=ctx)

        equity_state["cumulative_pnl"] = cumulative
        equity_state["peak_equity"] = peak_equity
    finally:
        conn.close()

    return {"date": str(trading_date), "signals": signals_count, "trades": trades_count, "pnl": day_pnl}


def run_backtest(settings: StrategySettings, date_from: str, date_to: str, job_id: str | None = None) -> str:
    run_id = str(uuid.uuid4())
    strategy = get_strategy_v1()
    s_hash = hash_settings(settings)
    conn = get_connection()
    try:
        conn.execute(
            """
            INSERT INTO backtest_runs (id, strategy_id, strategy_version, strategy_hash, settings, date_from, date_to,
                replay_mode, status, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'running', ?)
            """,
            [
                run_id,
                strategy.strategy_id,
                strategy.strategy_version,
                s_hash,
                settings.model_dump_json(),
                date_from,
                date_to,
                settings.replay_mode,
                datetime.utcnow(),
            ],
        )
    finally:
        conn.close()

    if job_id:
        update_job(job_id, "running", {"run_id": run_id})

    start = date.fromisoformat(date_from)
    end = date.fromisoformat(date_to)
    total_pnl = 0.0
    total_trades = 0
    equity_state = {"cumulative_pnl": 0.0, "peak_equity": settings.capital_budget}
    current = start
    while current <= end:
        if job_id:
            job = get_job(job_id)
            if job and job["status"] == "cancelled":
                conn = get_connection()
                try:
                    conn.execute(
                        "UPDATE backtest_runs SET status='cancelled', completed_at=? WHERE id=?",
                        [datetime.utcnow(), run_id],
                    )
                finally:
                    conn.close()
                update_job(job_id, "cancelled", {"run_id": run_id})
                return run_id
        if is_trading_day(current):
            day_result = replay_day(current, settings, run_id, equity_state)
            total_pnl += day_result.get("pnl", 0)
            total_trades += day_result.get("trades", 0)
        current += timedelta(days=1)

    summary = {
        "net_pnl": total_pnl,
        "gross_pnl": total_pnl,
        "total_trades": total_trades,
        "strategy_hash": s_hash,
        "final_equity": settings.capital_budget + equity_state["cumulative_pnl"],
        "max_drawdown": min((p["drawdown"] for p in list_equity(run_id)), default=0.0),
    }
    conn = get_connection()
    try:
        conn.execute(
            "UPDATE backtest_runs SET status='completed', summary=?, completed_at=? WHERE id=?",
            [json.dumps(summary), datetime.utcnow(), run_id],
        )
    finally:
        conn.close()

    if job_id:
        update_job(job_id, "completed", {"run_id": run_id, "summary": summary})
    return run_id


def get_run(run_id: str) -> dict | None:
    conn = get_connection(read_only=True)
    try:
        row = conn.execute(
            "SELECT id, strategy_id, strategy_version, strategy_hash, settings, date_from, date_to, replay_mode, status, summary, created_at, completed_at FROM backtest_runs WHERE id=?",
            [run_id],
        ).fetchone()
        if not row:
            return None
        return {
            "id": row[0],
            "strategy_id": row[1],
            "strategy_version": row[2],
            "strategy_hash": row[3],
            "settings": json.loads(row[4]),
            "date_from": str(row[5]),
            "date_to": str(row[6]),
            "replay_mode": row[7],
            "status": row[8],
            "summary": json.loads(row[9] or "{}"),
            "created_at": str(row[10]),
            "completed_at": str(row[11]) if row[11] else None,
        }
    finally:
        conn.close()


def list_runs() -> list[dict]:
    conn = get_connection(read_only=True)
    try:
        rows = conn.execute(
            "SELECT id, strategy_id, strategy_version, strategy_hash, date_from, date_to, replay_mode, status, summary, created_at FROM backtest_runs ORDER BY created_at DESC LIMIT 50"
        ).fetchall()
        return [
            {
                "id": r[0],
                "strategy_id": r[1],
                "strategy_version": r[2],
                "strategy_hash": r[3],
                "date_from": str(r[4]),
                "date_to": str(r[5]),
                "replay_mode": r[6],
                "status": r[7],
                "summary": json.loads(r[8] or "{}"),
                "created_at": str(r[9]),
            }
            for r in rows
        ]
    finally:
        conn.close()


def list_run_signals(run_id: str) -> list[dict]:
    conn = get_connection(read_only=True)
    try:
        rows = conn.execute("SELECT payload FROM backtest_signals WHERE run_id=? ORDER BY trading_date", [run_id]).fetchall()
        signals = [json.loads(r[0]) for r in rows]
        signals.sort(key=lambda s: s.get("timestamp") or s.get("signal_time") or "")
        return signals
    finally:
        conn.close()


def list_run_trades(run_id: str) -> list[dict]:
    conn = get_connection(read_only=True)
    try:
        rows = conn.execute("SELECT payload FROM backtest_trades WHERE run_id=? ORDER BY trading_date", [run_id]).fetchall()
        return [json.loads(r[0]) for r in rows]
    finally:
        conn.close()


def attach_trade_prices_to_signals(signals: list[dict], trades: list[dict]) -> list[dict]:
    """Merge entry/exit prices from completed trades onto matching Taken signals."""
    trade_by_signal: dict[str, dict] = {}
    for trade in trades:
        key = trade.get("signal_time") or trade.get("entry_time")
        if key:
            trade_by_signal[key] = trade

    enriched: list[dict] = []
    for signal in signals:
        row = dict(signal)
        if signal.get("status") != "Taken":
            enriched.append(row)
            continue
        key = signal.get("signal_time") or signal.get("timestamp")
        trade = trade_by_signal.get(key) if key else None
        if trade is None:
            for candidate in trades:
                if candidate.get("side") != signal.get("side") or candidate.get("strike") != signal.get("strike"):
                    continue
                signal_ts = str(key or "")[:16]
                trade_ts = str(candidate.get("signal_time") or candidate.get("entry_time") or "")[:16]
                if signal_ts and signal_ts == trade_ts:
                    trade = candidate
                    break
        if trade:
            row["entry_price"] = trade.get("entry_price")
            row["exit_price"] = trade.get("exit_price")
            row["trade_result"] = trade.get("result")
            row["trade_pnl"] = trade.get("pnl")
        enriched.append(row)
    return enriched
