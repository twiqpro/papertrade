from __future__ import annotations

from datetime import datetime
from random import random
from zoneinfo import ZoneInfo

from .config import get_settings
from .dhan_client import DhanAdapter
from .market_data import DhanMarketFeed, get_dhan_feed
from .oi_analysis import classify_regime, gamma_context, regime_display_label
from .models import DashboardPayload, MarketState, SessionMode, StrategySettings, Summary
from .paper_broker import PaperBroker
from .repository import save_signal, save_trade
from .signal_engine import LOT_SIZE, MarketContext, build_demo_context, capital_lots, evaluate_delta1_entry_signal
from .strategy import DemoMarket, nearest_nifty_strike, is_trade_window_open


IST = ZoneInfo("Asia/Kolkata")


def _demo_context(market: DemoMarket, strike: int) -> MarketContext:
    return build_demo_context(market, strike)


class PaperTradingStore:
    def __init__(self) -> None:
        self.settings = StrategySettings(
            target_rupees=3,
            stop_loss_rupees=10,
            reentry_cooldown_candles=4,
        )
        self.session_mode: SessionMode = "running"
        self.market = DemoMarket()
        self.context: MarketContext | None = None
        self.signals = []
        self.paper = PaperBroker()
        self._dhan_feed: DhanMarketFeed | None = None

    def set_settings(self, settings: StrategySettings) -> StrategySettings:
        self.settings = settings
        return self.settings

    def set_session(self, mode: SessionMode) -> SessionMode:
        self.session_mode = mode
        return self.session_mode

    def reset_paper_day(self) -> None:
        from .signal_engine import reset_entry_bar_tracking

        self.paper = PaperBroker()
        self.signals = []
        reset_entry_bar_tracking()

    def _use_dhan(self) -> bool:
        settings = get_settings()
        return bool(settings.dhan_client_id and settings.dhan_access_token)

    def _dhan(self) -> DhanMarketFeed:
        if self._dhan_feed is None:
            self._dhan_feed = get_dhan_feed()
        return self._dhan_feed

    def tick_demo_market(self) -> None:
        if self.session_mode != "running":
            return
        move = (random() - 0.48) * 8
        self.market.nifty_spot += move
        self.market.ema_9 += move * 0.28
        self.market.ema_15 += move * 0.18
        self.market.atm_ce_ltp = max(20, self.market.atm_ce_ltp + move * 0.18 + (random() - 0.5) * 0.7)
        self.market.atm_pe_ltp = max(20, self.market.atm_pe_ltp - move * 0.16 + (random() - 0.5) * 0.7)

    def _load_market(self) -> tuple[DemoMarket, MarketContext | None, str, str, str | None, int, str | None]:
        if not self._use_dhan():
            self.tick_demo_market()
            strike = nearest_nifty_strike(self.market.nifty_spot)
            ctx = _demo_context(self.market, strike)
            self.context = ctx
            return self.market, ctx, "demo", "demo", "Simulated market data", strike, None

        if not DhanAdapter().authenticate():
            self.tick_demo_market()
            strike = nearest_nifty_strike(self.market.nifty_spot)
            ctx = _demo_context(self.market, strike)
            self.context = ctx
            return self.market, ctx, "demo", "demo", "Dhan credentials missing", strike, None

        try:
            snapshot = self._dhan().get_snapshot(self.settings)
            self.market = snapshot.market
            self.context = snapshot.context
            return (
                snapshot.market,
                snapshot.context,
                "dhan",
                snapshot.feed_status,
                snapshot.feed_message,
                snapshot.atm_strike,
                snapshot.expiry,
            )
        except Exception as error:
            self.tick_demo_market()
            strike = nearest_nifty_strike(self.market.nifty_spot)
            ctx = _demo_context(self.market, strike)
            self.context = ctx
            return (
                self.market,
                ctx,
                "demo",
                "demo",
                f"Dhan feed failed, using simulator: {error}",
                strike,
                None,
            )

    def dashboard(self) -> DashboardPayload:
        market, context, data_mode, feed_status, feed_message, atm_strike, option_expiry = self._load_market()
        now = datetime.now(IST)
        self.paper._ensure_day(now, self.settings)

        if context is not None:
            close_meta = None
            if self.paper.open_position is not None:
                pos = self.paper.open_position
                close_meta = {
                    "id": pos.id,
                    "lots": pos.lots,
                    "target_price": pos.target_price,
                    "stop_price": pos.base_stop_price,
                    "trail_stop_price": pos.trail_stop_price,
                    "regime_at_entry": pos.regime_at_entry,
                }
            known_trade_ids = {trade.id for trade in self.paper.trades}
            self.paper.manage_exits(now, self.settings, context)
            can_enter, block_reason = self.paper.can_enter(now, self.settings)
            signal = evaluate_delta1_entry_signal(
                now,
                self.settings,
                context,
                remaining_daily_budget=self.paper.remaining_budget(self.settings),
                has_open_position=self.paper.open_position is not None,
                entry_block_reason=block_reason if not can_enter else None,
            )
            if signal.status == "Taken" and self.session_mode == "running":
                entered = self.paper.try_enter(
                    now,
                    self.settings,
                    context,
                    signal,
                    session_running=True,
                )
                if entered is None and self.paper.open_position is None:
                    can, block_reason = self.paper.can_enter(now, self.settings)
                    if not can:
                        signal = signal.model_copy(
                            update={
                                "status": "Skipped",
                                "reason": f"Entry blocked: {block_reason}",
                            }
                        )
            elif signal.status == "Taken":
                signal = signal.model_copy(
                    update={"status": "Skipped", "reason": "Entry blocked: session paused"}
                )

        trade_window = is_trade_window_open(now, self.settings)

        signal_appended = False
        if (
            not self.signals
            or self.signals[-1].time != signal.time
            or self.signals[-1].status != signal.status
            or self.signals[-1].signal != signal.signal
        ):
            self.signals.append(signal)
            self.signals = self.signals[-25:]
            signal_appended = True

        session_date = now.date()

        if context is not None:
            for trade in self.paper.trades:
                if trade.id in known_trade_ids:
                    continue
                meta = close_meta if close_meta and close_meta["id"] == trade.id else None
                save_trade(
                    trade,
                    session_date,
                    lots=meta["lots"] if meta else None,
                    target_price=meta["target_price"] if meta else None,
                    stop_price=meta["stop_price"] if meta else None,
                    trail_stop_price=meta["trail_stop_price"] if meta else None,
                    regime_at_entry=meta["regime_at_entry"] if meta else None,
                )

        closed_trades = self.paper.trades
        trades = list(closed_trades)
        open_trade = self.paper.open_position_trade(context, self.settings) if context else None
        if open_trade is not None:
            trades.append(open_trade)
        pnl_values = [trade.pnl for trade in closed_trades]
        gross_pnl = sum(pnl_values)
        if open_trade is not None:
            gross_pnl += open_trade.pnl
        winning = len([value for value in pnl_values if value > 0])
        losing = len([value for value in pnl_values if value < 0])
        running = 0.0
        peak = 0.0
        max_drawdown = 0.0
        for value in pnl_values:
            running += value
            peak = max(peak, running)
            max_drawdown = min(max_drawdown, running - peak)

        ema_gap = abs(market.ema_9 - (context.ema_20 if context and context.ema_20 else market.ema_15))
        trade_allowed = signal.status == "Taken" and self.paper.open_position is None
        side = signal.side
        quote_ltp = (
            context.atm_ce.ltp
            if context and side == "CE"
            else context.atm_pe.ltp
            if context and side == "PE"
            else market.atm_ce_ltp
        )
        lots = capital_lots(self.settings, quote_ltp) if context else 0
        regime_label = None
        market_regime = None
        gamma_flip = None
        if context is not None:
            ema_gap_ctx = abs(context.ema_9 - context.ema_15)
            market_regime = classify_regime(
                ema_gap_ctx,
                context.session_high,
                context.session_low,
                context.atr_14,
                self.settings.strong_trend_gap,
                self.settings.gamma_range_atr_ratio,
            )
            gamma_flip = context.gamma_flip
            regime_label = regime_display_label(market_regime, gamma_context(context.spot, context.gamma_flip))

        if signal_appended:
            save_signal(
                signal,
                session_date,
                market_regime=market_regime,
                nifty_spot=market.nifty_spot,
                pcr=context.walls.pcr if context is not None else None,
            )

        day = self.paper.day
        open_label = self.paper.open_position_label()

        return DashboardPayload(
            settings=self.settings,
            state=MarketState(
                timestamp=now,
                session_mode=self.session_mode,
                market_clock=now.strftime("%H:%M:%S IST"),
                trade_window_open=trade_window,
                nifty_spot=market.nifty_spot,
                ema_9=market.ema_9,
                ema_15=market.ema_15,
                ema_gap=ema_gap,
                vwap=context.vwap if context else None,
                vwap_label=context.vwap_label if context else None,
                call_wall=context.walls.call_wall if context else None,
                put_wall=context.walls.put_wall if context else None,
                pin_strike=context.walls.pin_strike if context else None,
                pcr=context.walls.pcr if context else None,
                gamma_regime=regime_label,
                market_regime=market_regime,
                gamma_flip=gamma_flip,
                trade_allowed=trade_allowed and not (day.halted if day else False),
                preferred_side=side,
                atm_strike=atm_strike,
                atm_ce_ltp=market.atm_ce_ltp,
                atm_pe_ltp=market.atm_pe_ltp,
                open_position=open_label,
                open_position_detail=open_label,
                trades_today=day.trades_today if day else 0,
                remaining_daily_budget=round(self.paper.remaining_budget(self.settings), 2),
                session_halted=day.halted if day else False,
                halt_reason=day.halt_reason if day and day.halted else None,
                broker=get_settings().broker,
                data_mode=data_mode,
                feed_status=feed_status,  # type: ignore[arg-type]
                feed_message=feed_message,
                option_expiry=option_expiry,
            ),
            summary=Summary(
                total_trades=len(closed_trades),
                winning_trades=winning,
                losing_trades=losing,
                win_rate=(winning / len(closed_trades) * 100) if closed_trades else 0,
                gross_pnl=gross_pnl,
                max_drawdown=abs(max_drawdown),
                affordable_lots=lots,
                lot_size=LOT_SIZE,
            ),
            signals=self.signals,
            trades=trades,
        )


store = PaperTradingStore()
