import { EditorView, basicSetup } from "https://esm.sh/codemirror@6.0.1";
import { python } from "https://esm.sh/@codemirror/lang-python@6.1.3";

const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => document.querySelectorAll(sel);

let editorView = null;
let lastDecisions = [];
let lastTrades = [];
let selectedSpotDates = new Set();
let selectedOptionsDates = new Set();
let lastInventory = { spot: [], options: [] };

const STORAGE_KEY = "backtester_selected_dates";

const BASE = (
  document.querySelector('meta[name="backtester-base"]')?.content ||
  window.BACKTESTER_BASE ||
  ""
).replace(/\/$/, "");

function apiUrl(path) {
  return `${BASE}${path.startsWith("/") ? path : `/${path}`}`;
}

function getCode() {
  return editorView ? editorView.state.doc.toString() : "";
}

function setCode(text) {
  if (!editorView) return;
  editorView.dispatch({
    changes: { from: 0, to: editorView.state.doc.length, insert: text },
  });
}

function initEditor(initialCode) {
  editorView = new EditorView({
    doc: initialCode,
    extensions: [basicSetup, python(), EditorView.lineWrapping],
    parent: $("#editor"),
  });
}

function formParams() {
  return {
    symbol: "NIFTY",
    start: $("#start-date").value,
    end: $("#end-date").value,
    interval: $("#interval").value,
    strikes_around_atm: Number($("#strikes").value) || 10,
    dates: getRunnableDates(),
  };
}

function datesInRange(start, end) {
  const out = [];
  const s = new Date(start + "T12:00:00");
  const e = new Date(end + "T12:00:00");
  for (let d = new Date(s); d <= e; d.setDate(d.getDate() + 1)) {
    const dow = d.getDay();
    if (dow !== 0 && dow !== 6) {
      out.push(d.toISOString().slice(0, 10));
    }
  }
  return out;
}

function getRunnableDates() {
  const range = new Set(datesInRange($("#start-date").value, $("#end-date").value));
  return [...selectedSpotDates]
    .filter((d) => range.has(d) && selectedOptionsDates.has(d))
    .sort();
}

/** Dates that have both spot and options cached for the current interval/strikes. */
function dualCachedDates(data, interval, strikes, rangeSet) {
  const spotDates = new Set(
    (data.spot || [])
      .filter((it) => it.interval === interval && (!rangeSet || rangeSet.has(it.date)))
      .map((it) => it.date)
  );
  return (data.options || [])
    .filter(
      (it) =>
        it.interval === interval &&
        Number(it.strikes_around_atm) === strikes &&
        spotDates.has(it.date) &&
        (!rangeSet || rangeSet.has(it.date))
    )
    .map((it) => it.date)
    .sort();
}

function persistSelection() {
  localStorage.setItem(
    STORAGE_KEY,
    JSON.stringify({
      spot: [...selectedSpotDates],
      options: [...selectedOptionsDates],
    })
  );
}

function restoreSelection() {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return;
    const data = JSON.parse(raw);
    selectedSpotDates = new Set(data.spot || []);
    selectedOptionsDates = new Set(data.options || []);
  } catch {
    /* ignore */
  }
}

function updateSelectionSummary() {
  const el = $("#selection-summary");
  const btn = $("#run-btn");
  const runnable = getRunnableDates();
  const interval = $("#interval").value;
  const strikes = $("#strikes").value;
  if (!runnable.length) {
    el.textContent =
      "No runnable days — check Start/End dates match your cache, then select spot + options dates (both required).";
    el.className = "selection-summary muted";
    if (btn) {
      btn.disabled = true;
      btn.title = "Select at least one day with both spot and options cached in range";
    }
    return;
  }
  el.textContent = `Ready: ${runnable.length} day(s) · ${interval} · ±${strikes} strikes (${runnable.join(", ")})`;
  el.className = "selection-summary ready";
  if (btn) {
    btn.disabled = false;
    btn.removeAttribute("title");
  }
}

function filterCacheItems(items, kind) {
  const interval = $("#interval").value;
  const strikes = Number($("#strikes").value) || 10;
  return items.filter((it) => {
    if (it.interval !== interval) return false;
    if (kind === "options" && it.strikes_around_atm != null && Number(it.strikes_around_atm) !== strikes) {
      return false;
    }
    return true;
  });
}

function refreshCacheLists() {
  renderCacheList("#spot-cache", lastInventory.spot || [], "spot", selectedSpotDates);
  renderCacheList("#options-cache", lastInventory.options || [], "options", selectedOptionsDates);
  updateSelectionSummary();
}

function selectAllInColumn(kind) {
  const items = kind === "spot" ? lastInventory.spot : lastInventory.options;
  const set = kind === "spot" ? selectedSpotDates : selectedOptionsDates;
  for (const it of filterCacheItems(items || [], kind)) {
    set.add(it.date);
  }
  persistSelection();
  refreshCacheLists();
}

function clearColumn(kind) {
  const items = kind === "spot" ? lastInventory.spot : lastInventory.options;
  const set = kind === "spot" ? selectedSpotDates : selectedOptionsDates;
  for (const it of filterCacheItems(items || [], kind)) {
    set.delete(it.date);
  }
  persistSelection();
  refreshCacheLists();
}

function selectAllRunnableInRange() {
  const interval = $("#interval").value;
  const strikes = Number($("#strikes").value) || 10;
  const range = new Set(datesInRange($("#start-date").value, $("#end-date").value));
  const dual = dualCachedDates(lastInventory, interval, strikes, range);
  for (const d of dual) {
    selectedSpotDates.add(d);
    selectedOptionsDates.add(d);
  }
  persistSelection();
  refreshCacheLists();
}

function setupCacheSelectionControls() {
  $("#select-all-runnable")?.addEventListener("click", selectAllRunnableInRange);
  $$("[data-select-all]").forEach((btn) => {
    btn.addEventListener("click", () => selectAllInColumn(btn.dataset.selectAll));
  });
  $$("[data-clear]").forEach((btn) => {
    btn.addEventListener("click", () => clearColumn(btn.dataset.clear));
  });
}

function renderCacheList(containerId, items, kind, selectedSet) {
  const list = $(containerId);
  const filtered = filterCacheItems(items, kind);

  if (!filtered.length) {
    const interval = $("#interval").value;
    const strikes = Number($("#strikes").value) || 10;
    list.innerHTML = `<li class="muted">No ${kind} cached for ${interval}${kind === "options" ? ` ±${strikes}` : ""}. Download or upload.</li>`;
    return;
  }

  list.innerHTML = filtered
    .map((it) => {
      const date = it.date;
      const checked = selectedSet.has(date) ? "checked" : "";
      const sel = selectedSet.has(date) ? "selected" : "";
      const src = it.source || (kind === "spot" ? "yahoo" : "dhan");
      const fb = it.source === "synthetic" ? ' <span class="muted">(demo — re-download)</span>' : "";
      const cloud = it.remote_only ? ' <span class="muted">(cloud)</span>' : it.storage === "supabase" ? ' <span class="muted">(synced)</span>' : "";
      const rows = it.rows ?? "?";
      return `<li class="${sel}">
        <label>
          <input type="checkbox" data-kind="${kind}" data-date="${date}" ${checked} />
          <span>${date}</span>
          <span class="cache-meta">${src}${fb}${cloud} · ${rows} rows</span>
        </label>
      </li>`;
    })
    .join("");

  list.querySelectorAll('input[type="checkbox"]').forEach((cb) => {
    cb.addEventListener("change", () => {
      const date = cb.dataset.date;
      const set = cb.dataset.kind === "spot" ? selectedSpotDates : selectedOptionsDates;
      if (cb.checked) set.add(date);
      else set.delete(date);
      cb.closest("li").classList.toggle("selected", cb.checked);
      persistSelection();
      updateSelectionSummary();
    });
  });
}

async function loadInventory() {
  try {
    const res = await fetch(apiUrl("/api/inventory"));
    const data = await res.json();
    lastInventory = { spot: data.spot || [], options: data.options || [] };
    reconcileSelection(data);
    refreshCacheLists();
  } catch {
    $("#spot-cache").innerHTML = '<li class="muted">Could not load inventory.</li>';
    $("#options-cache").innerHTML = '<li class="muted">Could not load inventory.</li>';
  }
}

function reconcileSelection(data) {
  const interval = $("#interval").value;
  const strikes = Number($("#strikes").value) || 10;
  const allDual = dualCachedDates(data, interval, strikes, null);

  if (!allDual.length) {
    selectedSpotDates.clear();
    selectedOptionsDates.clear();
    persistSelection();
    return;
  }

  let range = new Set(datesInRange($("#start-date").value, $("#end-date").value));
  let inRangeDual = dualCachedDates(data, interval, strikes, range);

  // Default range (Jan 2–3) often misses cached days — snap to cache when no overlap.
  if (!inRangeDual.length) {
    $("#start-date").value = allDual[0];
    $("#end-date").value = allDual[allDual.length - 1];
    range = new Set(datesInRange($("#start-date").value, $("#end-date").value));
    inRangeDual = dualCachedDates(data, interval, strikes, range);
  }

  const validSpot = new Set(
    (data.spot || [])
      .filter((it) => it.interval === interval && range.has(it.date))
      .map((it) => it.date)
  );
  const validOpt = new Set(
    (data.options || [])
      .filter(
        (it) => it.interval === interval && Number(it.strikes_around_atm) === strikes && range.has(it.date)
      )
      .map((it) => it.date)
  );

  selectedSpotDates = new Set([...selectedSpotDates].filter((d) => validSpot.has(d)));
  selectedOptionsDates = new Set([...selectedOptionsDates].filter((d) => validOpt.has(d)));

  const runnable = [...selectedSpotDates].filter((d) => selectedOptionsDates.has(d));
  if (!runnable.length && inRangeDual.length) {
    for (const d of inRangeDual) {
      selectedSpotDates.add(d);
      selectedOptionsDates.add(d);
    }
  }

  persistSelection();
}

async function parseApiResponse(res) {
  const text = await res.text();
  try {
    return { ok: res.ok, data: JSON.parse(text) };
  } catch {
    const msg = text.trim().slice(0, 500) || `Request failed (HTTP ${res.status})`;
    return { ok: res.ok, data: { detail: msg } };
  }
}

async function downloadData(force = false) {
  const status = $("#download-status");
  const btn = $("#download-btn");
  const forceBtn = $("#force-download-btn");
  const p = formParams();
  btn.disabled = true;
  if (forceBtn) forceBtn.disabled = true;
  status.textContent = force ? "Force refreshing from Yahoo/Dhan…" : "Downloading missing days…";
  status.className = "status-text";

  try {
    const res = await fetch(apiUrl("/api/download"), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ...p, dates: undefined, force }),
    });
    const { ok, data } = await parseApiResponse(res);
    if (!ok) throw new Error(data.detail || "Download failed");
    const skipped = (data.spot_skipped || 0) + (data.options_skipped || 0);
    status.textContent = `${data.days} day(s) · ${data.rows} rows · ${skipped} skipped (already cached)`;
    status.className = "status-text ok";
    await loadInventory();
  } catch (err) {
    status.textContent = err.message;
    status.className = "status-text err";
  } finally {
    btn.disabled = false;
    if (forceBtn) forceBtn.disabled = false;
  }
}

async function uploadFile(kind, file, inputEl) {
  const status = $("#download-status");
  const uploadDate = $("#upload-date")?.value;
  const day = uploadDate || $("#start-date").value;
  const form = new FormData();
  form.append("kind", kind);
  if (day) form.append("date", day);
  form.append("interval", $("#interval").value);
  form.append("strikes_around_atm", $("#strikes").value);
  form.append("file", file);

  status.textContent = `Uploading ${kind}…`;
  status.className = "status-text";
  try {
    const res = await fetch(apiUrl("/api/upload"), { method: "POST", body: form });
    const { ok, data } = await parseApiResponse(res);
    if (!ok) throw new Error(data.detail || "Upload failed");
    const targetDay = data.date || day;
    status.textContent = `Uploaded ${kind} for ${targetDay} (${data.rows} rows)${data.storage === "supabase" ? " · synced to cloud" : ""}`;
    status.className = "status-text ok";
    if (targetDay) {
      if (kind === "spot") selectedSpotDates.add(targetDay);
      else selectedOptionsDates.add(targetDay);
      if ($("#upload-date") && !$("#upload-date").value) $("#upload-date").value = targetDay;
    }
    persistSelection();
    await loadInventory();
  } catch (err) {
    status.textContent = err.message;
    status.className = "status-text err";
  } finally {
    if (inputEl) inputEl.value = "";
  }
}

function decisionClass(decision) {
  const d = (decision || "").toUpperCase();
  if (d === "ENTER") return "decision-enter";
  if (d === "EXIT") return "decision-exit";
  if (d === "SKIP") return "decision-skip";
  if (d === "ERROR") return "decision-error";
  return "decision-hold";
}

function renderSummary(summary) {
  const el = $("#summary-strip");
  if (!summary) {
    el.innerHTML = "";
    return;
  }
  const items = [
    ["Total trades", summary.total_trades],
    ["Win rate", `${summary.win_rate}%`],
    ["Total P&L", summary.total_pnl],
    ["Max drawdown", summary.max_drawdown],
    ["Skips", summary.skip_count],
    ["Bars", summary.bar_count],
  ];
  el.innerHTML = items
    .map(([label, val]) => `<div class="summary-item"><span>${label}</span><strong>${val}</strong></div>`)
    .join("");
}

function renderTable(tbody, rows, columns, rowClassFn) {
  tbody.innerHTML = "";
  if (!rows.length) {
    tbody.innerHTML = `<tr><td colspan="${columns.length}" class="muted">No rows</td></tr>`;
    return;
  }
  for (const row of rows) {
    const tr = document.createElement("tr");
    if (rowClassFn) tr.className = rowClassFn(row);
    for (const col of columns) {
      const td = document.createElement("td");
      const val = row[col];
      td.textContent = val === null || val === undefined ? "" : val;
      tr.appendChild(td);
    }
    tbody.appendChild(tr);
  }
}

function showResults(result) {
  const section = $("#results-section");
  const banner = $("#error-banner");
  section.hidden = false;

  if (result.error) {
    banner.hidden = false;
    banner.textContent = result.error.message + (result.error.traceback ? "\n\n" + result.error.traceback : "");
  } else {
    banner.hidden = true;
    banner.textContent = "";
  }

  lastDecisions = result.decisions || [];
  lastTrades = result.trades || [];

  renderSummary(result.summary);
  renderTable(
    $("#decisions-table tbody"),
    lastDecisions,
    ["#", "timestamp", "decision", "side", "direction", "strike", "spot", "fill_price", "qty", "reason", "pnl", "cum_pnl"],
    (row) => decisionClass(row.decision)
  );
  renderTable(
    $("#trades-table tbody"),
    lastTrades,
    ["entry_time", "exit_time", "side", "strike", "direction", "entry", "exit", "qty", "pnl", "hold_duration"]
  );

  section.scrollIntoView({ behavior: "smooth", block: "start" });
}

async function runStrategy() {
  const btn = $("#run-btn");
  const p = formParams();
  const dates = p.dates;
  if (!dates.length) {
    showResults({
      decisions: [],
      trades: [],
      summary: null,
      error: {
        message:
          "No runnable days. Set Start/End to match your cached dates and check at least one spot + options day above.",
      },
    });
    return;
  }

  btn.disabled = true;
  btn.textContent = "Running…";

  try {
    const res = await fetch(apiUrl("/api/run"), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ...p, code: getCode() }),
    });
    const { ok, data } = await parseApiResponse(res);
    if (!ok) throw new Error(data.detail || data.error?.message || "Run failed");
    showResults(data);
  } catch (err) {
    showResults({
      decisions: [],
      trades: [],
      summary: null,
      error: { message: err.message },
    });
  } finally {
    btn.textContent = "Run Strategy";
    updateSelectionSummary();
  }
}

function exportCsv(rows, columns, filename) {
  if (!rows.length) return;
  const escape = (v) => {
    const s = String(v ?? "");
    return s.includes(",") || s.includes('"') || s.includes("\n") ? `"${s.replace(/"/g, '""')}"` : s;
  };
  const lines = [columns.join(",")];
  for (const row of rows) {
    lines.push(columns.map((c) => escape(row[c])).join(","));
  }
  const blob = new Blob([lines.join("\n")], { type: "text/csv" });
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = filename;
  a.click();
  URL.revokeObjectURL(a.href);
}

function setupDropZone() {
  const zone = $("#drop-zone");
  const input = $("#file-input");

  zone.addEventListener("click", () => input.click());
  zone.addEventListener("keydown", (e) => {
    if (e.key === "Enter" || e.key === " ") {
      e.preventDefault();
      input.click();
    }
  });

  input.addEventListener("change", () => {
    const file = input.files?.[0];
    if (file) readStrategyFile(file);
    input.value = "";
  });

  zone.addEventListener("dragover", (e) => {
    e.preventDefault();
    zone.classList.add("dragover");
  });
  zone.addEventListener("dragleave", () => zone.classList.remove("dragover"));
  zone.addEventListener("drop", (e) => {
    e.preventDefault();
    zone.classList.remove("dragover");
    const file = e.dataTransfer?.files?.[0];
    if (file) readStrategyFile(file);
  });
}

function readStrategyFile(file) {
  if (!file.name.endsWith(".py")) {
    alert("Please drop a .py file");
    return;
  }
  const reader = new FileReader();
  reader.onload = () => setCode(reader.result);
  reader.readAsText(file);
}

function setupUploads() {
  $("#upload-spot").addEventListener("change", (e) => {
    const file = e.target.files?.[0];
    if (file) uploadFile("spot", file, e.target);
  });
  $("#upload-options").addEventListener("change", (e) => {
    const file = e.target.files?.[0];
    if (file) uploadFile("options", file, e.target);
  });
}

async function loadExample() {
  try {
    const res = await fetch(apiUrl("/api/example"));
    const data = await res.json();
    return data.code || "";
  } catch {
    return 'class Strategy:\n    def on_bar(self, snapshot, ctx):\n        return ctx.skip(reason="example not loaded")\n';
  }
}

async function loadCloudStatus() {
  const note = $("#cloud-cache-note");
  if (!note) return;
  try {
    const res = await fetch(apiUrl("/api/cache/cloud"));
    const { ok, data } = await parseApiResponse(res);
    if (!ok || !data.enabled) {
      note.hidden = true;
      return;
    }
    note.hidden = false;
    note.textContent = `Cloud cache active (${data.bucket}) — uploads sync to Supabase; production pulls data on demand.`;
  } catch {
    note.hidden = true;
  }
}

async function init() {
  restoreSelection();
  updateSelectionSummary();
  const example = await loadExample();
  initEditor(example);
  setupDropZone();
  setupUploads();
  setupCacheSelectionControls();

  $("#download-btn").addEventListener("click", () => downloadData(false));
  const forceBtn = $("#force-download-btn");
  if (forceBtn) forceBtn.addEventListener("click", () => downloadData(true));
  $("#run-btn").addEventListener("click", runStrategy);

  for (const id of ["interval", "strikes", "start-date", "end-date"]) {
    $(`#${id}`).addEventListener("change", () => {
      loadInventory();
      updateSelectionSummary();
    });
  }

  $("#export-decisions").addEventListener("click", () =>
    exportCsv(
      lastDecisions,
      ["#", "timestamp", "decision", "side", "direction", "strike", "spot", "fill_price", "qty", "reason", "pnl", "cum_pnl"],
      "decisions.csv"
    )
  );
  $("#export-trades").addEventListener("click", () =>
    exportCsv(
      lastTrades,
      ["entry_time", "exit_time", "side", "strike", "direction", "entry", "exit", "qty", "pnl", "hold_duration"],
      "trades.csv"
    )
  );

  await loadInventory();
  await loadCloudStatus();
}

init();
