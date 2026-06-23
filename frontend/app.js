const API_BASE_URL = (window.TWIQ_API_BASE_URL || "http://127.0.0.1:8000").replace(/\/$/, "");
const API_KEY = window.TWIQ_API_KEY || "";

const formatInr = new Intl.NumberFormat("en-IN", {
  style: "currency",
  currency: "INR",
  maximumFractionDigits: 0
});

const formatNumber = new Intl.NumberFormat("en-IN", {
  maximumFractionDigits: 2
});

const fallbackPayload = {
  settings: {
    capital_budget: 100000,
    daily_risk: 100000,
    target_rupees: 2,
    stop_loss_rupees: 5,
    ema_gap_min_points: 3,
    max_trades_per_day: 9999,
    max_consecutive_losses: 2,
    timeframe: "5m",
    fill_slippage_rupees: 0
  },
  state: {
    session_mode: "running",
    market_clock: "09:43:12 IST",
    trade_window_open: true,
    nifty_spot: 23486.25,
    ema_9: 23481.8,
    ema_15: 23474.4,
    ema_gap: 7.4,
    trade_allowed: true,
    preferred_side: "CE",
    atm_strike: 23500,
    atm_ce_ltp: 113.3,
    atm_pe_ltp: 96.75,
    open_position: "NIFTY 23500 CE paper",
    broker: "dhan",
    data_mode: "demo"
  },
  summary: {
    total_trades: 2,
    winning_trades: 1,
    losing_trades: 1,
    win_rate: 50,
    gross_pnl: -500,
    max_drawdown: 550,
    affordable_lots: 8,
    lot_size: 50
  },
  signals: [
    {
      time: "09:31",
      signal: "EMA trend check",
      side: "CE",
      ema_gap: 2.1,
      status: "Skipped",
      reason: "EMA gap below threshold"
    },
    {
      time: "09:38",
      signal: "ATM entry",
      side: "CE",
      ema_gap: 6.4,
      status: "Taken",
      reason: "EMA direction and gap confirmed"
    }
  ],
  trades: [
    {
      entry_time: "09:38",
      exit_time: "09:40",
      contract: "NIFTY 23500 CE",
      quantity: 50,
      entry_price: 108.2,
      exit_price: 110.2,
      result: "Target",
      pnl: 50
    },
    {
      entry_time: "09:57",
      exit_time: "10:02",
      contract: "NIFTY 23500 PE",
      quantity: 50,
      entry_price: 101.8,
      exit_price: 91.8,
      result: "Stop",
      pnl: -550
    }
  ]
};

let latestPayload = fallbackPayload;
let backendOnline = false;
let settingsFormDirty = false;
let historyMode = false;

function apiHeaders(extra = {}) {
  const headers = { "Content-Type": "application/json", ...extra };
  if (API_KEY) headers["X-API-Key"] = API_KEY;
  return headers;
}

async function api(path, options = {}) {
  const response = await fetch(`${API_BASE_URL}${path}`, {
    headers: apiHeaders(options.headers || {}),
    ...options
  });
  if (!response.ok) {
    throw new Error(`API ${response.status}`);
  }
  return response.json();
}

function byId(id) {
  return document.getElementById(id);
}

function todayIsoDate() {
  return new Date().toISOString().slice(0, 10);
}

function readSettingsFromForm() {
  return {
    capital_budget: Number(byId("capitalInput").value || 0),
    daily_risk: Number(byId("dailyRiskInput").value || 0),
    target_rupees: Number(byId("targetInput").value || 0),
    stop_loss_rupees: Number(byId("stopInput").value || 0),
    ema_gap_min_points: Number(byId("emaGapInput").value || 0),
    max_trades_per_day: Number(byId("maxTradesInput").value || 1),
    max_consecutive_losses: latestPayload.settings.max_consecutive_losses || 2,
    timeframe: document.querySelector('input[name="timeframe"]:checked')?.value || "1m",
    trade_start: latestPayload.settings.trade_start || "09:30",
    trade_end: latestPayload.settings.trade_end || "11:30",
    fill_slippage_rupees: latestPayload.settings.fill_slippage_rupees ?? 0,
    atm_source: latestPayload.settings.atm_source || "spot",
    expiry_rule: latestPayload.settings.expiry_rule || "current_weekly"
  };
}

function applySettings(settings) {
  byId("capitalInput").value = settings.capital_budget;
  byId("dailyRiskInput").value = settings.daily_risk;
  byId("targetInput").value = settings.target_rupees;
  byId("stopInput").value = settings.stop_loss_rupees;
  byId("emaGapInput").value = settings.ema_gap_min_points;
  byId("maxTradesInput").value = settings.max_trades_per_day;
  const timeframe = document.querySelector(`input[name="timeframe"][value="${settings.timeframe}"]`);
  if (timeframe) timeframe.checked = true;
}

function renderSignals(signals, { reverse = true, limit = 25 } = {}) {
  const visible = reverse ? signals.slice(-limit).reverse() : signals.slice(0, limit);
  byId("signalsBody").innerHTML = visible
    .map((row) => {
      const statusClass = row.status === "Taken" ? "good" : "warn";
      return `
        <tr>
          <td>${row.time}</td>
          <td>${row.signal}</td>
          <td>${row.side || "-"}</td>
          <td>${Number(row.ema_gap).toFixed(1)} pts</td>
          <td><span class="pill ${statusClass}">${row.status}</span></td>
          <td>${row.reason}</td>
        </tr>
      `;
    })
    .join("");
}

function formatOpenExitCell(trade) {
  const mtm =
    trade.exit_price != null ? `Rs ${Number(trade.exit_price).toFixed(2)} mtm` : "—";
  const levels = [];
  if (trade.target_price != null) levels.push(`TP ${Number(trade.target_price).toFixed(2)}`);
  if (trade.stop_price != null) levels.push(`SL ${Number(trade.stop_price).toFixed(2)}`);
  if (trade.trail_stop_price != null) {
    levels.push(`trail ${Number(trade.trail_stop_price).toFixed(2)}`);
  } else if (trade.trail_armed === false && trade.target_price != null) {
    const wideTarget = trade.target_price - trade.entry_price > 2.5;
    if (wideTarget) levels.push("trail pending");
  }
  if (!levels.length) return mtm;
  return `${mtm}<br><span class="muted">${levels.join(" · ")}</span>`;
}

function tradeResultClass(trade) {
  if (trade.result === "Open") return "warn";
  if (trade.result === "Target") return "good";
  if (trade.result === "Trail") return trade.pnl >= 0 ? "good" : "warn";
  return trade.pnl >= 0 ? "good" : "warn";
}

function renderTrades(trades) {
  if (!trades.length) {
    byId("tradesBody").innerHTML =
      '<tr><td colspan="7" class="muted">No paper trades yet — closed trades and open positions appear here.</td></tr>';
    return;
  }
  byId("tradesBody").innerHTML = trades
    .map((trade) => {
      const isOpen = trade.result === "Open";
      const resultClass = tradeResultClass(trade);
      const pnlClass = trade.pnl >= 0 ? "positive" : "negative";
      const exitCell = isOpen
        ? formatOpenExitCell(trade)
        : trade.exit_price == null
          ? "—"
          : `Rs ${Number(trade.exit_price).toFixed(2)}`;
      const pnlText = isOpen
        ? `${trade.pnl >= 0 ? "+" : ""}${formatInr.format(trade.pnl)}`
        : formatInr.format(trade.pnl);
      return `
        <tr>
          <td>${trade.entry_time}</td>
          <td>${trade.contract}</td>
          <td>${trade.quantity}</td>
          <td>Rs ${Number(trade.entry_price).toFixed(2)}</td>
          <td>${exitCell}</td>
          <td><span class="pill ${resultClass}">${trade.result}</span></td>
          <td class="${pnlClass}">${pnlText}</td>
        </tr>
      `;
    })
    .join("");
}

function renderDashboard(payload, { syncSettings = false } = {}) {
  latestPayload = payload;
  const { settings, state, summary, signals, trades } = payload;
  if (syncSettings && !settingsFormDirty) {
    applySettings(settings);
  }

  byId("capitalMetric").textContent = formatInr.format(settings.capital_budget);
  byId("lotMetric").textContent =
    summary.affordable_lots === 1 ? "1 lot affordable" : `${summary.affordable_lots} lots affordable`;
  byId("pnlMetric").textContent = `${summary.gross_pnl >= 0 ? "+" : ""}${formatInr.format(summary.gross_pnl)}`;
  byId("pnlMetric").className = summary.gross_pnl >= 0 ? "positive" : "negative";
  const pnlSubtitle = byId("pnlMetric").nextElementSibling;
  if (pnlSubtitle) {
    pnlSubtitle.textContent = state.open_position
      ? "Includes open position mark-to-market"
      : "LTP fills (no slippage)";
  }
  byId("winRateMetric").textContent = `${summary.win_rate.toFixed(0)}%`;
  byId("winRateMetric").nextElementSibling.textContent = `${summary.total_trades} trades today${state.trades_today != null ? ` · budget Rs ${Math.round(state.remaining_daily_budget || 0)}` : ""}`;
  if (state.session_halted && byId("haltBanner")) {
    byId("haltBanner").textContent = state.halt_reason || "Session halted";
    byId("haltBanner").style.display = "block";
  } else if (byId("haltBanner")) {
    byId("haltBanner").style.display = "none";
  }
  byId("emaGapMetric").textContent = `${state.ema_gap.toFixed(1)} pts`;
  byId("emaStateMetric").textContent = state.trade_allowed ? "Trade allowed" : "No trade";

  byId("marketClock").textContent = state.market_clock;
  byId("tradeWindow").textContent = "Trading window open (no time restriction)";
  byId("dataMode").textContent = backendOnline
    ? `${state.broker.toUpperCase()} ${state.data_mode} · ${state.feed_status || "demo"}${state.option_expiry ? ` · expiry ${state.option_expiry}` : ""}`
    : "Backend offline, demo fallback";
  byId("sessionLabel").textContent = state.session_mode === "running" ? "Paper running" : "Paper paused";
  byId("toggleSession").textContent = state.session_mode === "running" ? "Pause" : "Resume";
  document.querySelector(".status-dot").style.background =
    state.session_mode === "running" ? "var(--good)" : "var(--warn)";

  byId("spotValue").textContent = formatNumber.format(state.nifty_spot);
  byId("emaValue").textContent = `${formatNumber.format(state.ema_9)} / ${formatNumber.format(state.ema_15)}`;
  if (byId("vwapValue")) {
    byId("vwapValue").textContent = state.vwap != null ? formatNumber.format(state.vwap) : "—";
  }
  if (byId("oiValue")) {
    byId("oiValue").textContent =
      state.call_wall != null && state.put_wall != null && state.pin_strike != null
        ? `C ${formatNumber.format(state.call_wall)} / P ${formatNumber.format(state.put_wall)} / pin ${formatNumber.format(state.pin_strike)}`
        : "—";
  }
  if (byId("regimeValue")) {
    const pcr =
      state.pcr != null
        ? `PCR ${state.pcr.toFixed(2)} (P/C ${state.pcr < 1 ? "call-heavy" : "put-heavy"})`
        : "";
    const flip = state.gamma_flip != null ? `flip ${formatNumber.format(state.gamma_flip)}` : "";
    byId("regimeValue").textContent = [state.gamma_regime, pcr, flip].filter(Boolean).join(" · ") || "—";
  }
  byId("sideValue").textContent = state.preferred_side ? `ATM ${state.preferred_side}` : "No trade";
  byId("positionValue").textContent = state.open_position || "No open paper position";
  document.querySelector(".state-panel .tag").textContent = `ATM ${state.atm_strike}`;
  const expirySuffix = state.option_expiry ? ` · ${state.option_expiry}` : "";
  byId("ceLabel").textContent = `NIFTY ${state.atm_strike} CE${expirySuffix}`;
  byId("peLabel").textContent = `NIFTY ${state.atm_strike} PE${expirySuffix}`;
  byId("cePrice").textContent = `Rs ${state.atm_ce_ltp.toFixed(2)}`;
  byId("pePrice").textContent = `Rs ${state.atm_pe_ltp.toFixed(2)}`;
  byId("fillModel").textContent =
    settings.fill_slippage_rupees > 0
      ? `LTP + Rs ${settings.fill_slippage_rupees.toFixed(2)} slippage`
      : "LTP fill (no slippage)";
  byId("lastUpdated").textContent = backendOnline
    ? state.feed_message || "Synced now"
    : "Demo fallback";

  renderSignals(signals);
  renderTrades(trades);
}

async function refreshDashboard({ syncSettings = false } = {}) {
  if (historyMode) return;
  try {
    const payload = await api("/api/dashboard");
    backendOnline = true;
    renderDashboard(payload, { syncSettings });
  } catch (_error) {
    backendOnline = false;
    renderDashboard(fallbackPayload, { syncSettings });
  }
}

async function loadHistory(dateValue) {
  const date = dateValue || byId("historyDate")?.value || todayIsoDate();
  try {
    const [signals, trades] = await Promise.all([
      api(`/api/history/signals?date=${date}&limit=500`),
      api(`/api/history/trades?date=${date}&limit=200`)
    ]);
    backendOnline = true;
    renderSignals(signals, { reverse: false, limit: 500 });
    renderTrades(trades);
    if (byId("historyStatus")) {
      byId("historyStatus").textContent = `History for ${date} · ${signals.length} signals · ${trades.length} trades`;
    }
    if (byId("lastUpdated")) {
      byId("lastUpdated").textContent = `History view · ${date}`;
    }
  } catch (_error) {
    backendOnline = false;
    if (byId("historyStatus")) {
      byId("historyStatus").textContent = "Could not load history — check API URL / key";
    }
  }
}

function setViewMode(mode) {
  historyMode = mode === "history";
  const liveBtn = byId("viewLiveBtn");
  const historyBtn = byId("viewHistoryBtn");
  const historyControls = byId("historyControls");
  if (liveBtn) liveBtn.classList.toggle("active", !historyMode);
  if (historyBtn) historyBtn.classList.toggle("active", historyMode);
  if (historyControls) historyControls.style.display = historyMode ? "flex" : "none";
  if (historyMode) {
    loadHistory();
  } else {
    refreshDashboard();
  }
}

function wireEvents() {
  const strategyForm = byId("strategy");
  strategyForm.addEventListener("input", () => {
    settingsFormDirty = true;
  });
  strategyForm.addEventListener("change", () => {
    settingsFormDirty = true;
  });

  strategyForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    const settings = readSettingsFromForm();
    byId("saveSettings").textContent = "Saving";
    try {
      const saved = await api("/api/settings", {
        method: "POST",
        body: JSON.stringify(settings)
      });
      settingsFormDirty = false;
      applySettings(saved);
      byId("saveSettings").textContent = "Saved";
      await refreshDashboard();
    } catch (_error) {
      fallbackPayload.settings = settings;
      settingsFormDirty = false;
      applySettings(settings);
      byId("saveSettings").textContent = "Save failed";
      renderDashboard(fallbackPayload);
    }
    setTimeout(() => {
      byId("saveSettings").textContent = "Save";
    }, 1200);
  });

  byId("toggleSession").addEventListener("click", async () => {
    const nextMode = latestPayload.state.session_mode === "running" ? "paused" : "running";
    try {
      await api(`/api/session/${nextMode}`, { method: "POST" });
      await refreshDashboard();
    } catch (_error) {
      fallbackPayload.state.session_mode = nextMode;
      renderDashboard(fallbackPayload);
    }
  });

  if (byId("viewLiveBtn")) {
    byId("viewLiveBtn").addEventListener("click", () => setViewMode("live"));
  }
  if (byId("viewHistoryBtn")) {
    byId("viewHistoryBtn").addEventListener("click", () => setViewMode("history"));
  }
  if (byId("historyDate")) {
    byId("historyDate").value = todayIsoDate();
    byId("historyDate").addEventListener("change", () => loadHistory());
  }
  if (byId("loadHistoryBtn")) {
    byId("loadHistoryBtn").addEventListener("click", () => loadHistory());
  }
}

wireEvents();
refreshDashboard({ syncSettings: true });
setInterval(() => {
  if (!historyMode) refreshDashboard();
}, 2500);
