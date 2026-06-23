const API_BASE_URL = (window.TWIQ_API_BASE_URL || "http://127.0.0.1:8000").replace(/\/$/, "");

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
    capital_budget: 50000,
    daily_risk: 2500,
    target_rupees: 2,
    stop_loss_rupees: 10,
    ema_gap_min_points: 3,
    max_trades_per_day: 5,
    max_consecutive_losses: 2,
    timeframe: "1m",
    fill_slippage_rupees: 0.5
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

function byId(id) {
  return document.getElementById(id);
}

async function api(path, options = {}) {
  const response = await fetch(`${API_BASE_URL}${path}`, {
    headers: {
      "Content-Type": "application/json",
      ...(options.headers || {})
    },
    ...options
  });
  if (!response.ok) {
    throw new Error(`API ${response.status}`);
  }
  return response.json();
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
    fill_slippage_rupees: latestPayload.settings.fill_slippage_rupees || 0.5,
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

function renderSignals(signals) {
  byId("signalsBody").innerHTML = signals
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

function renderTrades(trades) {
  byId("tradesBody").innerHTML = trades
    .map((trade) => {
      const resultClass = trade.pnl >= 0 ? "good" : "warn";
      const pnlClass = trade.pnl >= 0 ? "positive" : "negative";
      const exitPrice = trade.exit_price == null ? "-" : `Rs ${Number(trade.exit_price).toFixed(2)}`;
      return `
        <tr>
          <td>${trade.entry_time}</td>
          <td>${trade.contract}</td>
          <td>${trade.quantity}</td>
          <td>Rs ${Number(trade.entry_price).toFixed(2)}</td>
          <td>${exitPrice}</td>
          <td><span class="pill ${resultClass}">${trade.result}</span></td>
          <td class="${pnlClass}">${formatInr.format(trade.pnl)}</td>
        </tr>
      `;
    })
    .join("");
}

function renderDashboard(payload) {
  latestPayload = payload;
  const { settings, state, summary, signals, trades } = payload;
  applySettings(settings);

  byId("capitalMetric").textContent = formatInr.format(settings.capital_budget);
  byId("lotMetric").textContent =
    summary.affordable_lots === 1 ? "1 lot affordable" : `${summary.affordable_lots} lots affordable`;
  byId("pnlMetric").textContent = `${summary.gross_pnl >= 0 ? "+" : ""}${formatInr.format(summary.gross_pnl)}`;
  byId("pnlMetric").className = summary.gross_pnl >= 0 ? "positive" : "negative";
  byId("winRateMetric").textContent = `${summary.win_rate.toFixed(0)}%`;
  byId("winRateMetric").nextElementSibling.textContent = `${summary.total_trades} trades today`;
  byId("emaGapMetric").textContent = `${state.ema_gap.toFixed(1)} pts`;
  byId("emaStateMetric").textContent = state.trade_allowed ? "Trade allowed" : "No trade";

  byId("marketClock").textContent = state.market_clock;
  byId("tradeWindow").textContent = state.trade_window_open ? "Trading window open" : "Trading window closed";
  byId("dataMode").textContent = backendOnline
    ? `${state.broker.toUpperCase()} ${state.data_mode} mode`
    : "Backend offline, demo fallback";
  byId("sessionLabel").textContent = state.session_mode === "running" ? "Paper running" : "Paper paused";
  byId("toggleSession").textContent = state.session_mode === "running" ? "Pause" : "Resume";
  document.querySelector(".status-dot").style.background =
    state.session_mode === "running" ? "var(--good)" : "var(--warn)";

  byId("spotValue").textContent = formatNumber.format(state.nifty_spot);
  byId("emaValue").textContent = `${formatNumber.format(state.ema_9)} / ${formatNumber.format(state.ema_15)}`;
  byId("sideValue").textContent = state.preferred_side ? `ATM ${state.preferred_side}` : "No trade";
  byId("positionValue").textContent = state.open_position || "No open paper position";
  document.querySelector(".state-panel .tag").textContent = `ATM ${state.atm_strike}`;
  byId("cePrice").textContent = `Rs ${state.atm_ce_ltp.toFixed(2)}`;
  byId("pePrice").textContent = `Rs ${state.atm_pe_ltp.toFixed(2)}`;
  byId("fillModel").textContent = `LTP + Rs ${settings.fill_slippage_rupees.toFixed(2)} slippage`;
  byId("lastUpdated").textContent = backendOnline ? "Synced now" : "Demo fallback";

  renderSignals(signals);
  renderTrades(trades);
}

async function refreshDashboard() {
  try {
    const payload = await api("/api/dashboard");
    backendOnline = true;
    renderDashboard(payload);
  } catch (_error) {
    backendOnline = false;
    renderDashboard(fallbackPayload);
  }
}

function wireEvents() {
  byId("strategy").addEventListener("submit", async (event) => {
    event.preventDefault();
    const settings = readSettingsFromForm();
    byId("saveSettings").textContent = "Saving";
    try {
      await api("/api/settings", {
        method: "POST",
        body: JSON.stringify(settings)
      });
      byId("saveSettings").textContent = "Saved";
      await refreshDashboard();
    } catch (_error) {
      fallbackPayload.settings = settings;
      byId("saveSettings").textContent = "Saved locally";
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
}

wireEvents();
refreshDashboard();
setInterval(refreshDashboard, 2500);
