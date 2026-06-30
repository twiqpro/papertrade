const POLL_MS = 2500;

const DEMO_SETTINGS = {
  capital_budget: 100000,
};

const DEMO_TRADES = [
  {
    id: "demo-1",
    entry_time: "09:38",
    exit_time: null,
    contract: "NIFTY 23500 CE",
    quantity: 325,
    entry_price: 113.3,
    exit_price: 117.05,
    result: "Open",
    pnl: 1218,
  },
  {
    id: "demo-2",
    entry_time: "09:52",
    exit_time: "10:04",
    contract: "NIFTY 23450 PE",
    quantity: 260,
    entry_price: 98.75,
    exit_price: 100.25,
    result: "Target",
    pnl: 390,
  },
  {
    id: "demo-3",
    entry_time: "10:18",
    exit_time: "10:26",
    contract: "NIFTY 23550 CE",
    quantity: 195,
    entry_price: 87.4,
    exit_price: 84.1,
    result: "Stop",
    pnl: -644,
  },
  {
    id: "demo-4",
    entry_time: "10:41",
    exit_time: "11:02",
    contract: "NIFTY 23500 PE",
    quantity: 325,
    entry_price: 102.2,
    exit_price: 104.85,
    result: "Trail",
    pnl: 861,
  },
  {
    id: "demo-5",
    entry_time: "11:15",
    exit_time: "11:28",
    contract: "NIFTY 23450 CE",
    quantity: 260,
    entry_price: 119.5,
    exit_price: 118.9,
    result: "Time Exit",
    pnl: -156,
  },
  {
    id: "demo-6",
    entry_time: "11:34",
    exit_time: "11:47",
    contract: "NIFTY 23500 CE",
    quantity: 325,
    entry_price: 108.6,
    exit_price: 110.1,
    result: "Target",
    pnl: 488,
  },
];

function parseParams() {
  const params = new URLSearchParams(window.location.search);
  const isLocal =
    window.location.hostname === "127.0.0.1" || window.location.hostname === "localhost";
  const demo =
    params.get("demo") === "1" || (isLocal && params.get("live") !== "1");
  return {
    limit: Math.max(1, Number(params.get("limit")) || 12),
    chromeless: params.get("chrome") === "0",
    demo,
  };
}

const { limit, chromeless, demo } = parseParams();

if (chromeless) {
  document.body.classList.add("chromeless");
}

function ordinalDay(day) {
  if (day >= 11 && day <= 13) return `${day}th`;
  const last = day % 10;
  if (last === 1) return `${day}st`;
  if (last === 2) return `${day}nd`;
  if (last === 3) return `${day}rd`;
  return `${day}th`;
}

function formatTodayLabel() {
  const now = new Date();
  const month = now.toLocaleDateString("en-IN", { month: "long" });
  return `${ordinalDay(now.getDate())} ${month}`;
}

function formatCapital(value) {
  return formatInr.format(value ?? 100000);
}

function renderCardTop(settings) {
  const today = formatTodayLabel();
  const capital = settings?.capital_budget ?? 100000;

  const topDate = document.getElementById("topDate");
  const startingCapital = document.getElementById("startingCapital");

  if (topDate) topDate.textContent = today;
  if (startingCapital) startingCapital.textContent = formatCapital(capital);
}

function contractTitle(contract) {
  const match = contract.match(/NIFTY\s+(\d+)\s+(CE|PE)/i);
  if (match) return `${match[1]} ${match[2]}`;
  return contract;
}

function formatPnlValue(trade) {
  if (trade.result === "Open") {
    const sign = trade.pnl >= 0 ? "+" : "";
    return `${sign}${formatInr.format(trade.pnl)}`;
  }
  return formatInr.format(trade.pnl);
}

function formatMeta(trade) {
  const isOpen = trade.result === "Open";
  const status = isOpen ? "Open" : "Closed";
  const statusClass = isOpen ? "watchlist__status--open" : "watchlist__status--closed";

  if (isOpen) {
    return `Entered ${trade.entry_time} · <span class="${statusClass}">${status}</span>`;
  }

  const exited = trade.exit_time ? ` · Exited ${trade.exit_time}` : "";
  return `Entered ${trade.entry_time}${exited} · <span class="${statusClass}">${status}</span>`;
}

function renderTradesLog(trades) {
  const list = document.getElementById("tradesLogBody");
  const countEl = document.getElementById("watchlistCount");
  if (!list) return;

  const visible = (trades || []).slice(-limit).reverse();

  if (countEl) {
    countEl.textContent =
      visible.length === 0 ? "ATM trades" : `ATM trades (${visible.length})`;
  }

  if (!visible.length) {
    list.innerHTML =
      '<li class="watchlist__empty">No paper trades yet — entries and exits appear here during the session.</li>';
    return;
  }

  list.innerHTML = visible
    .map((trade) => {
      const openClass = trade.result === "Open" ? " is-open" : "";
      const valueClass = trade.pnl >= 0 ? "pnl--pos" : "pnl--neg";
      return `
        <li class="watchlist__row${openClass}">
          <div class="watchlist__left">
            <span class="watchlist__name">${contractTitle(trade.contract)}</span>
            <span class="watchlist__meta">${formatMeta(trade)}</span>
          </div>
          <div class="watchlist__right">
            <span class="watchlist__value ${valueClass}">${formatPnlValue(trade)}</span>
          </div>
        </li>
      `;
    })
    .join("");
}

function renderDashboard(payload) {
  const { trades = [], settings = {} } = payload;
  renderCardTop(settings);
  renderTradesLog(trades);
}

function showDemoPreview() {
  renderDashboard({
    trades: DEMO_TRADES,
    settings: DEMO_SETTINGS,
  });
}

async function refreshTrades() {
  if (demo) return;

  try {
    const payload = await api("/api/dashboard");
    renderDashboard(payload);
  } catch (_err) {
    renderCardTop({ capital_budget: 100000 });
    const list = document.getElementById("tradesLogBody");
    const countEl = document.getElementById("watchlistCount");
    if (countEl) countEl.textContent = "ATM trades";
    if (list) {
      list.innerHTML =
        '<li class="watchlist__empty">Cannot reach paper-trading backend. Start it with <code>run-local.sh</code>.</li>';
    }
  }
}

if (demo) {
  showDemoPreview();
} else {
  void refreshTrades();
  window.setInterval(refreshTrades, POLL_MS);
}
