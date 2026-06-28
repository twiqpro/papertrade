/** Shared sidebar for paper trading + options backtester portals. */

const SIDEBAR_ICONS = {
  overview: `<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="3" width="7" height="7" rx="1"></rect><rect x="14" y="3" width="7" height="7" rx="1"></rect><rect x="3" y="14" width="7" height="7" rx="1"></rect><rect x="14" y="14" width="7" height="7" rx="1"></rect></svg>`,
  strategy: `<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round"><line x1="4" y1="21" x2="4" y2="14"></line><line x1="4" y1="10" x2="4" y2="3"></line><line x1="12" y1="21" x2="12" y2="12"></line><line x1="12" y1="8" x2="12" y2="3"></line><line x1="20" y1="21" x2="20" y2="16"></line><line x1="20" y1="12" x2="20" y2="3"></line><line x1="1" y1="14" x2="7" y2="14"></line><line x1="9" y1="8" x2="15" y2="8"></line><line x1="17" y1="16" x2="23" y2="16"></line></svg>`,
  trades: `<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round"><polyline points="22 7 13.5 15.5 8.5 10.5 2 17"></polyline><polyline points="16 7 22 7 22 13"></polyline></svg>`,
  signals: `<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round"><line x1="8" y1="6" x2="21" y2="6"></line><line x1="8" y1="12" x2="21" y2="12"></line><line x1="8" y1="18" x2="21" y2="18"></line><line x1="3" y1="6" x2="3.01" y2="6"></line><line x1="3" y1="12" x2="3.01" y2="12"></line><line x1="3" y1="18" x2="3.01" y2="18"></line></svg>`,
  backtester: `<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round"><polyline points="22 12 18 12 15 21 9 3 6 12 2 12"></polyline></svg>`,
};

function paperBase() {
  const raw = window.TWIQ_PAPER_BASE;
  if (raw === undefined || raw === null) return "";
  return String(raw).replace(/\/$/, "");
}

function backtesterHref() {
  const base = window.TWIQ_BACKTESTER_BASE ?? "/options-backtest";
  return base.endsWith("/") ? base : `${base}/`;
}

function navItems() {
  const paper = paperBase();
  return [
    { id: "overview", href: `${paper}/index.html#overview`, label: "Overview", icon: SIDEBAR_ICONS.overview },
    { id: "strategy", href: `${paper}/index.html#strategy`, label: "Strategy", icon: SIDEBAR_ICONS.strategy },
    { id: "trades", href: `${paper}/index.html#trades`, label: "Trades", icon: SIDEBAR_ICONS.trades },
    { id: "signals", href: `${paper}/index.html#signals`, label: "Signals", icon: SIDEBAR_ICONS.signals },
    { id: "backtester", href: backtesterHref(), label: "Backtester", icon: SIDEBAR_ICONS.backtester },
  ];
}

function renderSidebarHtml(active, showSession) {
  const links = navItems()
    .map(
      (item) => `
      <a href="${item.href}" class="${item.id === active ? "active" : ""}" title="${item.label}">
        <span class="nav-icon" aria-hidden="true">${item.icon}</span>
        <span class="nav-label">${item.label}</span>
      </a>`
    )
    .join("");

  const session = showSession
    ? `
        <section class="session-panel">
          <div class="session-state">
            <span class="status-dot"></span>
            <span id="sessionLabel">Paper running</span>
          </div>
          <button id="toggleSession" class="button primary session-btn" type="button">Pause</button>
        </section>`
    : "";

  return `
    <aside class="sidebar" id="sidebar">
      <div class="sidebar-header">
        <div class="brand">
          <div class="brand-mark">N</div>
          <div class="brand-copy">
            <h1>NIFTY Options</h1>
            <p>Paper trading</p>
          </div>
        </div>
        <button
          id="sidebarToggle"
          class="sidebar-toggle"
          type="button"
          aria-label="Collapse sidebar"
          aria-expanded="true"
          title="Collapse sidebar"
        >‹</button>
      </div>
      <nav class="nav" aria-label="Primary">${links}</nav>
      ${session}
    </aside>`;
}

function wireSidebarCollapse() {
  const shell = document.getElementById("appShell");
  const toggle = document.getElementById("sidebarToggle");
  if (!shell || !toggle) return;

  const applyCollapsed = (collapsed) => {
    shell.classList.toggle("is-sidebar-collapsed", collapsed);
    toggle.setAttribute("aria-expanded", String(!collapsed));
    toggle.setAttribute("aria-label", collapsed ? "Expand sidebar" : "Collapse sidebar");
    toggle.title = collapsed ? "Expand sidebar" : "Collapse sidebar";
    toggle.textContent = collapsed ? "›" : "‹";
  };

  applyCollapsed(localStorage.getItem("twiq-sidebar-collapsed") === "1");
  toggle.addEventListener("click", () => {
    const collapsed = !shell.classList.contains("is-sidebar-collapsed");
    applyCollapsed(collapsed);
    localStorage.setItem("twiq-sidebar-collapsed", collapsed ? "1" : "0");
  });
}

/**
 * @param {{ active?: string, showSession?: boolean }} options
 */
function initSidebar(options = {}) {
  const { active = "overview", showSession = false } = options;
  const root = document.getElementById("sidebar-root");
  if (!root) return;

  root.outerHTML = renderSidebarHtml(active, showSession);
  wireSidebarCollapse();
}

window.initSidebar = initSidebar;
