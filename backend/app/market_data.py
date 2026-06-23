from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import date, datetime
from typing import Optional
from zoneinfo import ZoneInfo

from .dhan_client import DhanAdapter, DhanApiError, NIFTY_SECURITY_ID, NIFTY_SEGMENT
from .models import StrategySettings
from .oi_analysis import classify_regime, gamma_context, regime_display_label
from .signal_engine import INDIA_VIX_SECURITY_ID, MarketContext, build_market_context
from .strategy import DemoMarket, nearest_nifty_strike


IST = ZoneInfo("Asia/Kolkata")
CHAIN_CACHE_SECONDS = 3.5
CANDLE_CACHE_SECONDS = 60.0
EXPIRY_CACHE_SECONDS = 3600.0


@dataclass
class LiveMarketSnapshot:
    market: DemoMarket
    context: Optional[MarketContext]
    atm_strike: int
    expiry: str
    feed_status: str
    feed_message: Optional[str] = None


def timeframe_to_interval(timeframe: str) -> str:
    return timeframe.replace("m", "")


def pick_expiry(expiries: list[str], settings: StrategySettings, today: date) -> str:
    today_str = today.isoformat()
    future = [value for value in expiries if value >= today_str]
    if not future:
        return expiries[-1]
    if settings.expiry_rule == "next_weekly_on_expiry" and future[0] == today_str and len(future) > 1:
        return future[1]
    return future[0]


def dashboard_regime_label(context: MarketContext, settings: StrategySettings) -> str:
    ema_gap = abs(context.ema_9 - context.ema_15)
    regime = classify_regime(
        ema_gap,
        context.session_high,
        context.session_low,
        context.atr_14,
        settings.strong_trend_gap,
        settings.gamma_range_atr_ratio,
    )
    gamma = gamma_context(context.spot, context.gamma_flip)
    return regime_display_label(regime, gamma)


class DhanMarketFeed:
    def __init__(self, adapter: DhanAdapter | None = None) -> None:
        self.adapter = adapter or DhanAdapter()
        self._expiry_cache: tuple[float, list[str]] = (0.0, [])
        self._chain_cache: tuple[float, str, dict] = (0.0, "", {})
        self._candle_cache: tuple[float, str, str, dict] = (0.0, "", "", {})
        self.last_good: Optional[LiveMarketSnapshot] = None

    def _cached_expiries(self) -> list[str]:
        now = time.monotonic()
        cached_at, expiries = self._expiry_cache
        if expiries and now - cached_at < EXPIRY_CACHE_SECONDS:
            return expiries
        expiries = self.adapter.get_expiry_list()
        self._expiry_cache = (now, expiries)
        return expiries

    def _cached_option_chain(self, expiry: str) -> dict:
        now = time.monotonic()
        cached_at, cached_expiry, chain = self._chain_cache
        if chain and cached_expiry == expiry and now - cached_at < CHAIN_CACHE_SECONDS:
            return chain
        chain = self.adapter.get_option_chain(expiry)
        self._chain_cache = (now, expiry, chain)
        return chain

    def _cached_candles(self, day: str, interval: str) -> dict:
        now = time.monotonic()
        cached_at, cached_day, cached_interval, candles = self._candle_cache
        if candles and cached_day == day and cached_interval == interval and now - cached_at < CANDLE_CACHE_SECONDS:
            return candles
        candles = self.adapter.get_intraday_candles(from_date=day, to_date=day, interval=interval)
        self._candle_cache = (now, day, interval, candles)
        return candles

    def _fetch_india_vix(self) -> Optional[float]:
        try:
            data = self.adapter.get_ltp({"IDX_I": [INDIA_VIX_SECURITY_ID]})
            segment = data.get("IDX_I") or {}
            quote = segment.get(str(INDIA_VIX_SECURITY_ID)) or segment.get(INDIA_VIX_SECURITY_ID)
            if quote:
                value = float(quote.get("last_price") or 0)
                # Ignore bad mappings (e.g. wrong security id returning index levels ~10k+).
                if 5.0 <= value <= 80.0:
                    return value
        except DhanApiError:
            return None
        return None

    def get_snapshot(self, settings: StrategySettings) -> LiveMarketSnapshot:
        now = datetime.now(IST)
        today = now.date().isoformat()
        candle_interval = timeframe_to_interval(settings.timeframe)
        try:
            expiries = self._cached_expiries()
            expiry = pick_expiry(expiries, settings, now.date())
            chain = self._cached_option_chain(expiry)
            candles_raw = self._cached_candles(today, candle_interval)
            india_vix = self._fetch_india_vix()
            context = build_market_context(chain, candles_raw, expiry, india_vix)
            if context.spot <= 0:
                raise DhanApiError("Dhan option chain returned no underlying price")
            if not context.candles:
                raise DhanApiError("No intraday candles available for strategy filters")

            regime = dashboard_regime_label(context, settings)
            snapshot = LiveMarketSnapshot(
                market=DemoMarket(
                    nifty_spot=context.spot,
                    ema_9=context.ema_9,
                    ema_15=context.ema_15,
                    atm_ce_ltp=context.atm_ce.ltp,
                    atm_pe_ltp=context.atm_pe.ltp,
                ),
                context=context,
                atm_strike=context.atm_strike,
                expiry=expiry,
                feed_status="live",
                feed_message=f"5m NIFTY · expiry {expiry} · {regime}",
            )
            self.last_good = snapshot
            return snapshot
        except DhanApiError as error:
            if self.last_good is not None:
                return LiveMarketSnapshot(
                    market=self.last_good.market,
                    context=self.last_good.context,
                    atm_strike=self.last_good.atm_strike,
                    expiry=self.last_good.expiry,
                    feed_status="stale",
                    feed_message=str(error),
                )
            raise


def get_dhan_feed() -> DhanMarketFeed:
    return DhanMarketFeed()
