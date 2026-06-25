from __future__ import annotations

import json
import uuid
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from ..strategy import LOT_SIZE
from ..strategy_module.base import AccountState
from ..signal_engine import calc_ema_series
from ..strategy_module.v3_ema_pullback import (
    EmaPullbackStrategy,
    StratConfig,
    _ema20_from_context,
    hash_config,
    on_trade_closed,
    settings_shell,
)
from .costs import trade_pnl
from .db import get_connection
from .exits import evaluate_bar_exit
from .reference import is_trading_day
from .replay import (
    PendingEntry,
    _contract_bar,
    _decision_to_dict,
    _load_chain_snapshots,
    _load_day_nifty,
    get_run,
    list_equity,
    list_run_signals,
    list_run_trades,
)
from .repository import save_equity_point, save_signal, save_trade
from .sync import completed_candles_before, filter_atm_window, select_chain_snapshot, select_vix
from .validators import validate_day
from ..signal_engine import OptionQuote
from .candles import aggregate_from_timestamps
from .context_builder import build_context_from_chain_rows

IST = ZoneInfo("Asia/Kolkata")


def replay_squeeze_day(
    trading_date: date,
    cfg: StratConfig,
    strategy: EmaPullbackStrategy,
    run_id: str,
    equity_state: dict,
) -> dict:
    settings = settings_shell(cfg)
    if hasattr(strategy, "reset_session"):
        strategy.reset_session()
    quality = validate_day(trading_date)
    if quality["status"] == "excluded":
        return {"date": str(trading_date), "skipped": True, "reason": quality["warnings"], "pnl": 0.0}

    conn = get_connection()
    signals_count = 0
    trades_count = 0
    day_pnl = 0.0

    try:
        nifty_1m = _load_day_nifty(conn, trading_date)
        if not nifty_1m:
            return {"date": str(trading_date), "skipped": True, "reason": ["No NIFTY data"], "pnl": 0.0}

        tf_minutes = int(cfg.timeframe.replace("m", ""))
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

        open_position = None
        pending_entry: PendingEntry | None = None
        cumulative = equity_state.get("cumulative_pnl", 0.0)
        peak_equity = equity_state.get("peak_equity", cfg.capital_budget)
        account = AccountState(
            remaining_daily_budget=cfg.capital_budget,
            session_equity=cfg.capital_budget + cumulative,
            bar_index=0,
        )

        for ts, nifty_bar in nifty_1m:
            if ts.time() < cfg.trade_start:
                continue
            if ts.time() > cfg.force_exit and open_position is None and pending_entry is None:
                break

            chain_ts, chain_rows, stale_reason = select_chain_snapshot(
                chain_snaps, ts, cfg.chain_staleness_seconds
            )
            minute_rows = chain_by_ts.get(ts, chain_rows if chain_ts == ts else [])

            if open_position is not None:
                ob = _contract_bar(minute_rows, open_position.strike, open_position.side)
                if ob is None:
                    save_trade(
                        run_id,
                        trading_date,
                        {
                            "id": str(uuid.uuid4()),
                            "status": "incomplete",
                            "reason": "Contract left ATM±10 data window",
                            "side": open_position.side,
                            "strike": open_position.strike,
                            "entry_time": open_position.entry_time.isoformat(),
                        },
                    )
                    trades_count += 1
                    open_position = None
                    account.has_open_position = False
                else:
                    quote = OptionQuote(ob["ltp"], ob["bid"], ob["ask"], ob["oi"], ob["iv"], ob["delta"])
                    completed_for_exit = completed_candles_before(strategy_candles, ts)
                    ctx = build_context_from_chain_rows(
                        nifty_bar.close,
                        open_position.expiry,
                        filter_atm_window(minute_rows, nifty_bar.close, cfg.option_chain_window),
                        completed_for_exit,
                        select_vix(vix_rows, ts)[1],
                        cfg.option_chain_window,
                    )
                    if hasattr(strategy, "feed_index_bar"):
                        strategy.feed_index_bar(
                            open_=nifty_bar.open,
                            high=nifty_bar.high,
                            low=nifty_bar.low,
                            close=nifty_bar.close,
                            ema9=ctx.ema_9,
                            ema20=_ema20_from_context(ctx),
                            atr=ctx.atr_14,
                            bar_index=account.bar_index,
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
                    if exit_decision is None and ts.time() >= cfg.force_exit:
                        exit_decision = evaluate_bar_exit(
                            strategy,
                            ts.replace(hour=cfg.force_exit.hour, minute=cfg.force_exit.minute, second=0),
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
                        on_trade_closed(cfg, account, net, account.bar_index, exit_decision.result)
                        peak_equity = max(peak_equity, cfg.capital_budget + cumulative)
                        drawdown = (cfg.capital_budget + cumulative) - peak_equity
                        save_equity_point(run_id, exit_decision.exit_time, cfg.capital_budget + cumulative, drawdown)
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
                        open_position = None

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
                if ts.time() > cfg.trade_end:
                    account.bar_index += 1
                    continue
                completed_bars = completed_candles_before(strategy_candles, ts)
                account.bar_index += 1
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
                    filter_atm_window(chain_rows, spot, cfg.option_chain_window),
                    completed_bars,
                    vix,
                    cfg.option_chain_window,
                )
                if stale_reason:
                    payload = _decision_to_dict(
                        strategy.evaluate_entry(ts, ctx, account, settings)
                    )
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
                save_signal(run_id, trading_date, payload)
                signals_count += 1

                if decision.status == "Taken" and decision.side is not None:
                    pending_entry = PendingEntry(decision=decision, signal_time=ts, context=ctx)

        equity_state["cumulative_pnl"] = cumulative
        equity_state["peak_equity"] = peak_equity
    finally:
        conn.close()

    return {"date": str(trading_date), "signals": signals_count, "trades": trades_count, "pnl": day_pnl}


def run_squeeze_backtest(cfg: StratConfig, date_from: str, date_to: str) -> str:
    strategy = EmaPullbackStrategy(cfg=cfg)
    settings = settings_shell(cfg)
    s_hash = hash_config(cfg)
    run_id = str(uuid.uuid4())
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
                json.dumps({"ema_pullback_config": _config_json(cfg)}),
                date_from,
                date_to,
                "ema_pullback",
                datetime.utcnow(),
            ],
        )
    finally:
        conn.close()

    start = date.fromisoformat(date_from)
    end = date.fromisoformat(date_to)
    total_pnl = 0.0
    total_trades = 0
    equity_state = {"cumulative_pnl": 0.0, "peak_equity": cfg.capital_budget}
    current = start
    while current <= end:
        if is_trading_day(current):
            day_result = replay_squeeze_day(current, cfg, strategy, run_id, equity_state)
            total_pnl += day_result.get("pnl", 0)
            total_trades += day_result.get("trades", 0)
        current += timedelta(days=1)

    summary = {
        "net_pnl": total_pnl,
        "gross_pnl": total_pnl,
        "total_trades": total_trades,
        "strategy_hash": s_hash,
        "final_equity": cfg.capital_budget + equity_state["cumulative_pnl"],
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
    return run_id


def _config_json(cfg: StratConfig) -> dict:
    from dataclasses import asdict

    raw = asdict(cfg)
    for key in ("trade_start", "trade_end", "force_exit"):
        raw[key] = raw[key].strftime("%H:%M")
    return raw
