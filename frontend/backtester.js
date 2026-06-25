let currentMapping = {};
let selectedRunId = null;
let activeJobId = null;
let activeJobTargetId = null;
let wizardStep = 1;
let dataSource = null;
let dataInventory = { ready_days: [] };
let selectedReadyDays = new Set();

const WIZARD_STEPS = 4;

function setWizardStep(step) {
  wizardStep = step;
  for (let i = 1; i <= WIZARD_STEPS; i++) {
    const panel = byId(`wizard-panel-${i}`);
    const bar = byId(`bar-step-${i}`);
    const label = document.querySelector(`[data-wizard-step="${i}"] p`);
    if (panel) panel.classList.toggle("tw-hidden", i !== step);
    if (bar) {
      bar.classList.toggle("tw-bg-slate-900", i <= step);
      bar.classList.toggle("tw-bg-slate-200", i > step);
    }
    if (label) {
      label.classList.toggle("tw-text-slate-900", i <= step);
      label.classList.toggle("tw-text-slate-400", i > step);
    }
  }
  const backBtn = byId("wizardBack");
  const nextBtn = byId("wizardNext");
  if (backBtn) backBtn.classList.toggle("tw-invisible", step === 1);
  if (nextBtn) {
    nextBtn.textContent = step === WIZARD_STEPS ? "Go to Strategy Lab →" : "Next →";
    if (step === 1) {
      nextBtn.disabled = !dataSource;
    } else if (step !== WIZARD_STEPS) {
      nextBtn.disabled = false;
    }
  }
  if (step === 2) {
    byId("load-dhan")?.classList.toggle("tw-hidden", dataSource !== "dhan");
    byId("load-csv")?.classList.toggle("tw-hidden", dataSource !== "csv");
    byId("load-yahoo")?.classList.toggle("tw-hidden", dataSource !== "yahoo");
    refreshDhanJsonHints().catch(() => {});
  }
  if (step === 3) {
    byId("load-vix-dhan")?.classList.toggle("tw-hidden", dataSource !== "dhan");
    byId("load-vix-yahoo")?.classList.toggle("tw-hidden", dataSource !== "yahoo");
    byId("load-vix-csv")?.classList.toggle("tw-hidden", dataSource === "dhan" || dataSource === "yahoo");
  }
  if (step === WIZARD_STEPS) {
    updateWizardNextState();
    loadDataInventory().catch(() => {});
  }
}

function selectSource(source) {
  dataSource = source;
  document.querySelectorAll(".source-card").forEach((card) => {
    const active = card.dataset.source === source;
    card.classList.toggle("tw-border-slate-900", active);
    card.classList.toggle("tw-ring-2", active);
    card.classList.toggle("tw-ring-slate-900", active);
    card.classList.toggle("tw-border-slate-200", !active);
  });
  byId("wizardNext").disabled = false;
}

function syncVerifyDatesForSource() {
  const day = getDownloadDate("spotDownloadDate");
  if (byId("covFrom")) byId("covFrom").value = day;
  if (byId("covTo")) byId("covTo").value = day;
  if (byId("runFrom")) byId("runFrom").value = day;
  if (byId("runTo")) byId("runTo").value = day;
}

function getDownloadDate(inputId) {
  return byId(inputId)?.value || new Date().toISOString().slice(0, 10);
}

function initDownloadDates() {
  const today = new Date().toISOString().slice(0, 10);
  for (const id of ["spotDownloadDate", "optionsDownloadDate", "optionsDownloadDateDhan"]) {
    if (byId(id)) byId(id).value = today;
  }
}

function syncDownloadDatesFromSpot() {
  const day = getDownloadDate("spotDownloadDate");
  if (byId("optionsDownloadDate")) byId("optionsDownloadDate").value = day;
  if (byId("covFrom")) byId("covFrom").value = day;
  if (byId("covTo")) byId("covTo").value = day;
  if (byId("runFrom")) byId("runFrom").value = day;
  if (byId("runTo")) byId("runTo").value = day;
}

function formatDayCount(summary) {
  if (!summary?.count) return "0 days in range";
  if (summary.first && summary.last && summary.first !== summary.last) {
    return `${summary.count} days (${summary.first} → ${summary.last})`;
  }
  return `${summary.count} day${summary.count === 1 ? "" : "s"} (${summary.first || "—"})`;
}

function setCheckItem(id, ok, summary) {
  const badge = byId(`check-${id}`);
  const countEl = byId(`check-${id}-count`);
  const statusEl = byId(`status-${id}`);
  if (badge) {
    badge.textContent = ok ? "✓" : "—";
    badge.className = ok
      ? "tw-flex tw-h-6 tw-w-6 tw-items-center tw-justify-center tw-rounded-full tw-bg-emerald-100 tw-text-emerald-700 tw-text-xs tw-font-bold"
      : "tw-flex tw-h-6 tw-w-6 tw-items-center tw-justify-center tw-rounded-full tw-bg-slate-100 tw-text-slate-400 tw-text-xs";
  }
  if (countEl) countEl.textContent = formatDayCount(summary);
  if (statusEl) {
    statusEl.textContent = ok ? formatDayCount(summary) : "Not loaded";
    statusEl.className = ok
      ? "tw-text-xs tw-font-medium tw-text-emerald-600"
      : "tw-text-xs tw-font-medium tw-text-slate-400";
  }
}

async function refreshChecklist() {
  const from = byId("covFrom")?.value || "2020-01-01";
  const to = byId("covTo")?.value || new Date().toISOString().slice(0, 10);
  const rangeEl = byId("covRangeNote");
  if (rangeEl) {
    rangeEl.textContent =
      selectedReadyDays.size > 0
        ? `Backtest period from ${selectedReadyDays.size} selected day${selectedReadyDays.size === 1 ? "" : "s"}: ${from} → ${to}`
        : `Backtest period: ${from} → ${to}`;
  }
  try {
    const coverage = await api(`/api/data/coverage?from_date=${from}&to_date=${to}`);
    const summary = coverage.summary || {};
    setCheckItem("nifty", (summary.nifty?.count || 0) > 0, summary.nifty || { count: 0 });
    setCheckItem("options", (summary.options?.count || 0) > 0, summary.options || { count: 0 });
    setCheckItem("vix", (summary.vix?.count || 0) > 0, summary.vix || { count: 0 });

    const overlapEl = byId("covOverlapNote");
    if (overlapEl) {
      const ready = summary.backtest_ready_days || 0;
      if (ready > 0) {
        overlapEl.textContent = `Days with both NIFTY + options (backtestable): ${ready}${
          summary.backtest_ready_first && summary.backtest_ready_last
            ? ` (${summary.backtest_ready_first} → ${summary.backtest_ready_last})`
            : ""
        }`;
        overlapEl.className = "tw-text-sm tw-text-emerald-700 tw-mb-4";
      } else {
        overlapEl.textContent =
          "No days in this range have both NIFTY and options — select loaded days above or download missing data.";
        overlapEl.className = "tw-text-sm tw-text-amber-700 tw-mb-4";
      }
    }

    const optionsCount = summary.options?.count || 0;
    const optionsStatus = optionsCount > 0 ? formatDayCount(summary.options) : "Not loaded";
    const optionsClass = optionsCount > 0
      ? "tw-text-xs tw-font-medium tw-text-emerald-600"
      : "tw-text-xs tw-font-medium tw-text-slate-400";
    byId("status-options-yahoo") && (byId("status-options-yahoo").textContent = optionsStatus);
    byId("status-options-yahoo") && (byId("status-options-yahoo").className = optionsClass);
  } catch {
    /* ignore */
  }
  updateWizardNextState();
}

function getDataChoice() {
  return byId("dataChoiceDownload")?.checked ? "download" : "existing";
}

function setDataChoice(mode) {
  const useExisting = mode === "existing";
  if (byId("dataChoiceExisting")) byId("dataChoiceExisting").checked = useExisting;
  if (byId("dataChoiceDownload")) byId("dataChoiceDownload").checked = !useExisting;
  byId("existingDataPanel")?.classList.toggle("tw-hidden", !useExisting);
  byId("downloadNewPanel")?.classList.toggle("tw-hidden", useExisting);
  updateWizardNextState();
}

function updateWizardNextState() {
  const nextBtn = byId("wizardNext");
  if (!nextBtn || wizardStep !== WIZARD_STEPS) return;
  if (getDataChoice() === "existing") {
    nextBtn.disabled = selectedReadyDays.size === 0;
  } else {
    nextBtn.disabled = false;
  }
}

function applySelectedDaysToRange() {
  const sorted = [...selectedReadyDays].sort();
  const summaryEl = byId("selectedDaysSummary");
  if (!sorted.length) {
    if (summaryEl) summaryEl.textContent = "No days selected.";
    updateWizardNextState();
    return;
  }
  const from = sorted[0];
  const to = sorted[sorted.length - 1];
  if (byId("covFrom")) byId("covFrom").value = from;
  if (byId("covTo")) byId("covTo").value = to;
  if (byId("runFrom")) byId("runFrom").value = from;
  if (byId("runTo")) byId("runTo").value = to;
  if (summaryEl) {
    summaryEl.textContent =
      sorted.length === 1
        ? `Selected: ${from} (NIFTY + options ready)`
        : `Selected: ${sorted.length} days — ${from} → ${to}`;
  }
  updateWizardNextState();
}

function renderReadyDaysList() {
  const listEl = byId("readyDaysList");
  if (!listEl) return;
  const ready = dataInventory.ready_days || [];
  listEl.innerHTML = "";
  if (!ready.length) {
    listEl.innerHTML =
      '<p class="tw-text-sm tw-text-slate-500 tw-col-span-2">No backtest-ready days yet. Download NIFTY + options for at least one trading day (step 2).</p>';
    return;
  }
  for (const day of ready) {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.dataset.date = day.date;
    const selected = selectedReadyDays.has(day.date);
    btn.className = selected
      ? "ready-day-chip tw-text-left tw-p-3 tw-rounded-lg tw-border-2 tw-border-slate-900 tw-bg-slate-50 tw-transition-colors"
      : "ready-day-chip tw-text-left tw-p-3 tw-rounded-lg tw-border tw-border-slate-200 tw-bg-white hover:tw-border-slate-400 tw-transition-colors";
    const vixNote = day.has_vix ? " · VIX ✓" : " · no VIX";
    btn.innerHTML = `
      <p class="tw-font-medium tw-text-slate-900">${day.date}</p>
      <p class="tw-text-xs tw-text-slate-500 tw-mt-0.5">NIFTY ${day.nifty_bars.toLocaleString()} bars · Options ${day.option_bars.toLocaleString()} bars${vixNote}</p>`;
    btn.addEventListener("click", () => {
      if (selectedReadyDays.has(day.date)) selectedReadyDays.delete(day.date);
      else selectedReadyDays.add(day.date);
      renderReadyDaysList();
      applySelectedDaysToRange();
      refreshChecklist().catch(() => {});
    });
    listEl.appendChild(btn);
  }
}

async function loadDataInventory() {
  try {
    dataInventory = await api("/api/data/inventory");
    const ready = dataInventory.ready_days || [];
    const banner = byId("existingDataBanner");
    const bannerTitle = byId("existingDataBannerTitle");
    const bannerDetail = byId("existingDataBannerDetail");

    if (ready.length > 0) {
      banner?.classList.remove("tw-hidden");
      if (bannerTitle) {
        bannerTitle.textContent = `${ready.length} backtest-ready day${ready.length === 1 ? "" : "s"} available on disk`;
      }
      if (bannerDetail) {
        bannerDetail.textContent = `Data for ${ready[0].date} (latest) is ready to use. Select days below, or choose “Download new data” to fetch another date.`;
      }
      setDataChoice("existing");
      const prefer = getDownloadDate("spotDownloadDate");
      if (ready.some((d) => d.date === prefer)) {
        selectedReadyDays = new Set([prefer]);
      } else if (selectedReadyDays.size === 0 || ![...selectedReadyDays].some((d) => ready.some((r) => r.date === d))) {
        selectedReadyDays = new Set([ready[0].date]);
      }
    } else {
      banner?.classList.add("tw-hidden");
      setDataChoice("download");
      selectedReadyDays = new Set();
      syncVerifyDatesForSource();
    }
    renderReadyDaysList();
    applySelectedDaysToRange();
    await refreshChecklist();
  } catch {
    syncVerifyDatesForSource();
    await refreshChecklist().catch(() => {});
  }
}

function initWizard() {
  const today = new Date();
  const sixMonthsAgo = new Date(today);
  sixMonthsAgo.setMonth(sixMonthsAgo.getMonth() - 6);
  if (byId("covTo")) byId("covTo").value = today.toISOString().slice(0, 10);
  if (byId("covFrom")) byId("covFrom").value = sixMonthsAgo.toISOString().slice(0, 10);
  if (byId("runFrom")) byId("runFrom").value = sixMonthsAgo.toISOString().slice(0, 10);
  if (byId("runTo")) byId("runTo").value = today.toISOString().slice(0, 10);
  initDownloadDates();

  byId("spotDownloadDate")?.addEventListener("change", () => {
    syncDownloadDatesFromSpot();
    refreshDhanJsonHints().catch(() => {});
  });
  byId("optionsDownloadDate")?.addEventListener("change", () => refreshDhanJsonHint("dhanJsonDiskHintYahoo", "optionsDownloadDate").catch(() => {}));
  byId("optionsDownloadDateDhan")?.addEventListener("change", () => refreshDhanJsonHint("dhanJsonDiskHintDhan", "optionsDownloadDateDhan").catch(() => {}));

  byId("importDhanJsonDiskYahoo")?.addEventListener("click", () =>
    importDhanJsonFromDisk("optionsDownloadDate", "importResultYahoo").catch((e) => alert(e.message))
  );
  byId("importDhanJsonDiskDhan")?.addEventListener("click", () =>
    importDhanJsonFromDisk("optionsDownloadDateDhan", "importResultDhanJson").catch((e) => alert(e.message))
  );
  byId("importJsonFolderBtnYahoo")?.addEventListener("click", () =>
    importDhanJsonFolder("jsonFolderOptionsYahoo", "importResultYahoo", "optionsDownloadDate").catch((e) => alert(e.message))
  );
  byId("importJsonFolderBtn")?.addEventListener("click", () =>
    importDhanJsonFolder("jsonFolderOptions", "importResult").catch((e) => alert(e.message))
  );
  byId("jsonFolderOptionsYahoo")?.addEventListener("change", () => updateJsonFolderCount("jsonFolderOptionsYahoo", "jsonFolderCountYahoo"));
  byId("jsonFolderOptions")?.addEventListener("change", () => updateJsonFolderCount("jsonFolderOptions", "jsonFolderCount"));

  document.querySelectorAll('input[name="dataChoice"]').forEach((radio) => {
    radio.addEventListener("change", () => setDataChoice(getDataChoice()));
  });
  byId("selectAllReadyDays")?.addEventListener("click", () => {
    selectedReadyDays = new Set((dataInventory.ready_days || []).map((d) => d.date));
    renderReadyDaysList();
    applySelectedDaysToRange();
    refreshChecklist().catch(() => {});
  });
  byId("selectLatestReadyDay")?.addEventListener("click", () => {
    const latest = dataInventory.ready_days?.[0]?.date;
    if (!latest) return;
    selectedReadyDays = new Set([latest]);
    renderReadyDaysList();
    applySelectedDaysToRange();
    refreshChecklist().catch(() => {});
  });
  byId("clearReadyDays")?.addEventListener("click", () => {
    selectedReadyDays = new Set();
    renderReadyDaysList();
    applySelectedDaysToRange();
    refreshChecklist().catch(() => {});
  });
  byId("goToDownloadStep")?.addEventListener("click", () => {
    const day =
      selectedReadyDays.size > 0
        ? [...selectedReadyDays].sort()[0]
        : getDownloadDate("spotDownloadDate");
    if (byId("spotDownloadDate")) byId("spotDownloadDate").value = day;
    syncDownloadDatesFromSpot();
    setWizardStep(2);
  });

  document.querySelectorAll(".source-card").forEach((card) => {
    card.addEventListener("click", () => selectSource(card.dataset.source));
  });
  byId("wizardBack")?.addEventListener("click", () => {
    if (wizardStep > 1) setWizardStep(wizardStep - 1);
  });
  byId("wizardNext")?.addEventListener("click", () => {
    if (wizardStep < WIZARD_STEPS) setWizardStep(wizardStep + 1);
    else {
      location.hash = "strategy-lab";
      document.querySelector('a[href="#strategy-lab"]')?.click();
      window.scrollTo({ top: 0, behavior: "smooth" });
    }
  });
  setWizardStep(1);
}

let strategyStep = 1;
const STRATEGY_STEPS = 3;

function selectMode(mode) {
  byId("replayMode").value = mode;
  document.querySelectorAll(".mode-card").forEach((card) => {
    const active = card.dataset.mode === mode;
    card.classList.toggle("tw-border-slate-900", active);
    card.classList.toggle("tw-ring-2", active);
    card.classList.toggle("tw-ring-slate-900", active);
    card.classList.toggle("tw-border-slate-200", !active);
  });
  byId("fullContextFilters")?.classList.toggle("tw-hidden", mode !== "full_context");
}

function updateRunSummary() {
  const el = byId("runSummary");
  if (!el) return;
  const mode = byId("replayMode").value === "full_context" ? "Full Context" : "Core";
  el.innerHTML = `
    <div class="tw-flex tw-justify-between"><span class="tw-text-slate-500">Period</span><span class="tw-font-medium">${byId("runFrom").value} → ${byId("runTo").value}</span></div>
    <div class="tw-flex tw-justify-between"><span class="tw-text-slate-500">Mode</span><span class="tw-font-medium">${mode}</span></div>
    <div class="tw-flex tw-justify-between"><span class="tw-text-slate-500">Timeframe</span><span class="tw-font-medium">${byId("timeframe").value}</span></div>
    <div class="tw-flex tw-justify-between"><span class="tw-text-slate-500">Target / Stop</span><span class="tw-font-medium">₹${byId("targetRupees").value} / ₹${byId("stopRupees").value}</span></div>
    <div class="tw-flex tw-justify-between"><span class="tw-text-slate-500">Costs</span><span class="tw-font-medium">${byId("costPreset").selectedOptions[0].text}</span></div>`;
}

function setStrategyStep(step) {
  strategyStep = step;
  for (let i = 1; i <= STRATEGY_STEPS; i++) {
    byId(`strategy-panel-${i}`)?.classList.toggle("tw-hidden", i !== step);
    const bar = byId(`sbar-${i}`);
    const label = document.querySelector(`[data-strategy-step="${i}"] p`);
    if (bar) {
      bar.classList.toggle("tw-bg-slate-900", i <= step);
      bar.classList.toggle("tw-bg-slate-200", i > step);
    }
    if (label) {
      label.classList.toggle("tw-text-slate-900", i <= step);
      label.classList.toggle("tw-text-slate-400", i > step);
    }
  }
  byId("strategyBack")?.classList.toggle("tw-invisible", step === 1);
  const nextBtn = byId("strategyNext");
  if (nextBtn) {
    nextBtn.textContent = step === STRATEGY_STEPS ? "Run on this step ↓" : "Next →";
    nextBtn.classList.toggle("tw-hidden", step === STRATEGY_STEPS);
  }
  if (step === STRATEGY_STEPS) updateRunSummary();
}

function initStrategyWizard() {
  document.querySelectorAll(".mode-card").forEach((card) => {
    card.addEventListener("click", () => selectMode(card.dataset.mode));
  });
  selectMode(byId("replayMode").value || "full_context");
  byId("strategyBack")?.addEventListener("click", () => {
    if (strategyStep > 1) setStrategyStep(strategyStep - 1);
  });
  byId("strategyNext")?.addEventListener("click", () => {
    if (strategyStep < STRATEGY_STEPS) setStrategyStep(strategyStep + 1);
  });
  setStrategyStep(1);
}

function setStatus(elId, text) {
  const el = byId(elId);
  if (el) el.textContent = text;
}

async function loadDataStatus() {
  const el = byId("dhanCredentialStatus");
  try {
    const status = await api("/api/data/status");
    if (el) {
      el.textContent = status.dhan_configured
        ? "✓ Credentials found — ready to download"
        : "✗ Add DHAN_CLIENT_ID and DHAN_ACCESS_TOKEN to backend/.env";
      el.className = status.dhan_configured
        ? "tw-mt-3 tw-text-xs tw-font-medium tw-text-emerald-600"
        : "tw-mt-3 tw-text-xs tw-font-medium tw-text-red-600";
    }
    const hint = byId("todayOptionsCredentialHint");
    if (hint) {
      hint.textContent = status.dhan_configured
        ? "✓ Dhan credentials found — option download ready"
        : "✗ Add DHAN_CLIENT_ID and DHAN_ACCESS_TOKEN to backend/.env";
      hint.className = status.dhan_configured
        ? "tw-mt-2 tw-text-xs tw-font-medium tw-text-emerald-600"
        : "tw-mt-2 tw-text-xs tw-font-medium tw-text-red-600";
    }
    await checkBackendCapabilities();
    const storage = status.storage;
    const note = byId("storagePathsNote");
    if (note && storage) {
      note.textContent = `Database: ${storage.duckdb || "backend/data/twiq_backtest.duckdb"} · Raw CSV: ${storage.raw_yahoo}, ${storage.raw_dhan}`;
    }
  } catch {
    if (el) {
      el.textContent = "Cannot reach backend — check config.js points to http://127.0.0.1:8000";
      el.className = "tw-mt-3 tw-text-xs tw-font-medium tw-text-red-600";
    }
    setBackendStaleBanner(true, "Backend not reachable at " + (window.TWIQ_API_BASE_URL || "http://127.0.0.1:8000"));
  }
}

async function checkBackendCapabilities() {
  try {
    const response = await fetch(`${API_BASE_URL}/openapi.json`);
    if (!response.ok) throw new Error("openapi unavailable");
    const spec = await response.json();
    const paths = Object.keys(spec.paths || {});
    const hasYahoo = paths.includes("/api/data/yahoo/sync");
    const hasTodayOptions = paths.includes("/api/data/dhan/today-options");
    setBackendStaleBanner(!(hasYahoo && hasTodayOptions));
  } catch {
    setBackendStaleBanner(true, "Cannot verify backend version");
  }
}

function setBackendStaleBanner(stale, message) {
  const bannerId = "backendStaleBanner";
  let banner = byId(bannerId);
  if (!stale) {
    banner?.remove();
    return;
  }
  const text =
    message ||
    "Backend is running old code — restart it (Ctrl+C run-local.sh, then bash run-local.sh) so Yahoo + today-options downloads work.";
  if (!banner) {
    banner = document.createElement("div");
    banner.id = bannerId;
    banner.className =
      "tw-mx-auto tw-max-w-4xl tw-mb-4 tw-rounded-lg tw-border tw-border-red-300 tw-bg-red-50 tw-px-4 tw-py-3 tw-text-sm tw-text-red-800";
    const wizard = byId("data-wizard");
    wizard?.parentElement?.insertBefore(banner, wizard);
  }
  banner.textContent = "⚠ " + text;
}

async function cancelActiveJob() {
  if (!activeJobId) return;
  await api(`/api/data/jobs/${activeJobId}/cancel`, { method: "POST" });
  await pollJob(activeJobId, activeJobTargetId);
}

function formatStatus(data) {
  if (typeof data === "string") return data;
  if (data.rows_imported != null) {
    return `Imported ${data.rows_imported} rows (batch ${data.batch_id?.slice(0, 8) || "—"}).`;
  }
  if (data.nifty_rows != null) {
    return [
      data.error ? `✗ ${data.error}` : "✓ Download complete",
      data.trading_date ? `Date: ${data.trading_date}` : "",
      data.source ? `Source: ${data.source}` : "",
      `NIFTY: ${data.nifty_rows} bars`,
      `VIX: ${data.vix_rows} bars`,
      data.date_from && data.date_to ? `Range: ${data.date_from} → ${data.date_to}` : "",
      data.note || "",
    ].filter(Boolean).join("\n");
  }
  if (data.valid != null) {
    const lines = [
      data.valid ? "✓ Columns look good — ready to import." : `✗ Missing columns: ${(data.missing_required || []).join(", ")}`,
      data.suggested_mapping?.format === "wide" ? "Detected wide format (ce_* / pe_* columns)." : "",
      `File: ${data.filename || "upload.csv"}`,
    ];
    return lines.filter(Boolean).join("\n");
  }
  return JSON.stringify(data, null, 2);
}

function formatJobStatus(job) {
  const progress = job.progress || {};
  const lines = [`Status: ${job.status}`, `Type: ${job.job_type}`];
  if (job.status === "failed" && job.error_message) {
    lines.unshift(`✗ FAILED: ${job.error_message}`);
  } else if (job.status === "completed") {
    lines.unshift("✓ Completed successfully");
    if (progress.trading_date) lines.push(`Trading date: ${progress.trading_date}`);
  } else if (job.status === "running") {
    lines.unshift(`⏳ Running… (phase: ${progress.phase || "starting"})`);
  }
  if (progress.imported != null) lines.push(`Rows imported so far: ${progress.imported}`);
  if (progress.expiry_date) lines.push(`Expiry: ${progress.expiry_date}`);
  if (progress.completed_requests != null && progress.total_requests != null) {
    lines.push(`Requests: ${progress.completed_requests}/${progress.total_requests}`);
  }
  if (progress.cursor) lines.push(`Current date: ${progress.cursor}`);
  if (progress.run_id) lines.push(`Run ID: ${progress.run_id}`);
  if (progress.summary) lines.push(`Net P&L: ${progress.summary.net_pnl ?? "—"}`);
  return lines.join("\n");
}

function formatCoverage(coverage, quality) {
  const summary = coverage.summary || {};
  const lines = [
    `Range checked: ${coverage.date_from} → ${coverage.date_to}`,
    `NIFTY: ${formatDayCount(summary.nifty || { count: 0 })}`,
    `Options: ${formatDayCount(summary.options || { count: 0 })}`,
    `VIX: ${formatDayCount(summary.vix || { count: 0 })}`,
    `Backtestable (NIFTY + options overlap): ${summary.backtest_ready_days || 0} day(s)`,
  ];
  if (quality?.days) {
    const valid = quality.days.filter((d) => d.status === "valid").length;
    const warned = quality.days.filter((d) => d.status === "valid_with_warnings").length;
    const excluded = quality.days.filter((d) => d.status === "excluded").length;
    lines.push(`Quality: ${valid} valid, ${warned} with warnings, ${excluded} excluded`);
  }
  return lines.join("\n");
}

async function previewCsv(fileInputId = "csvFileOptions", resultId = "importResult") {
  const file = byId(fileInputId)?.files[0];
  if (!file) return alert("Choose an options CSV file first");
  byId("datasetType").value = "option_bars";
  const form = new FormData();
  form.append("file", file);
  form.append("dataset_type", "option_bars");
  const response = await fetch(`${API_BASE_URL}/api/data/csv/preview`, {
    method: "POST",
    headers: API_KEY ? { "X-API-Key": API_KEY } : {},
    body: form,
  });
  const data = await response.json();
  currentMapping = data.suggested_mapping || {};
  const wizard = byId("mappingWizard");
  wizard.innerHTML =
    `<p class="tw-text-sm tw-mb-2 ${data.valid ? "tw-text-emerald-600" : "tw-text-red-600"}">${data.valid ? "✓ Columns look good" : "Missing: " + (data.missing_required || []).join(", ")}</p>` +
    (data.suggested_mapping?.format === "wide" ? '<p class="tw-text-sm tw-text-slate-500">Wide CE/PE format detected</p>' : "") +
    Object.entries(currentMapping)
      .filter(([k]) => k !== "format")
      .map(([field, column]) => `<div class="tw-mb-2"><label class="tw-text-xs tw-text-slate-500">${field} <input data-map-field="${field}" value="${column}" class="tw-ml-1 tw-px-2 tw-py-1 tw-rounded tw-border tw-border-slate-300 tw-text-sm" /></label></div>`)
      .join("");
  wizard.querySelectorAll("[data-map-field]").forEach((input) => {
    input.addEventListener("change", () => {
      currentMapping[input.dataset.mapField] = input.value;
    });
  });
  byId(resultId).textContent = formatStatus(data);
}

async function importCsvForType(datasetType, fileInputId, resultId) {
  const file = byId(fileInputId)?.files[0];
  if (!file) return alert("Choose a CSV file first");
  byId("datasetType").value = datasetType;
  const mapping = datasetType === "option_bars" ? readMappingFromWizard() : {};
  const form = new FormData();
  form.append("dataset_type", datasetType);
  form.append("file", file);
  form.append("mapping", JSON.stringify(mapping));
  const response = await fetch(`${API_BASE_URL}/api/data/csv/import`, {
    method: "POST",
    headers: API_KEY ? { "X-API-Key": API_KEY } : {},
    body: form,
  });
  const data = await response.json();
  byId(resultId).textContent = formatStatus(data);
  await refreshChecklist();
}

function readMappingFromWizard() {
  const mapping = { ...currentMapping };
  byId("mappingWizard")?.querySelectorAll("[data-map-field]").forEach((input) => {
    mapping[input.dataset.mapField] = input.value;
  });
  return mapping;
}

async function importCsv(fileInputId = "csvFileOptions", resultId = "importResult") {
  const input = byId(fileInputId);
  const files = input?.files;
  if (!files?.length) return alert("Choose one or more option CSV files");

  if (files.length === 1) {
    await importCsvForType("option_bars", fileInputId, resultId);
    return;
  }

  const form = new FormData();
  form.append("dataset_type", "option_bars");
  for (const file of files) {
    form.append("files", file);
  }
  byId(resultId).textContent = `Importing ${files.length} files…`;
  const response = await fetch(`${API_BASE_URL}/api/data/csv/import-bulk`, {
    method: "POST",
    headers: API_KEY ? { "X-API-Key": API_KEY } : {},
    body: form,
  });
  const data = await response.json();
  if (!response.ok) throw new Error(data.detail || "Bulk import failed");
  byId(resultId).textContent = [
    `✓ Imported ${data.files_imported} of ${data.files_received} files`,
    `${data.rows_imported} total rows`,
    data.error_count ? `⚠ ${data.error_count} files failed` : "",
    data.errors?.length ? data.errors.map((e) => `${e.filename}: ${e.error}`).join("\n") : "",
  ].filter(Boolean).join("\n");
  await refreshChecklist();
}

function formatDhanJsonImportResult(data) {
  return [
    `✓ Imported ${data.files_imported} JSON file${data.files_imported === 1 ? "" : "s"}`,
    `${data.rows_imported} rows → DuckDB`,
    data.options_files ? `${data.options_files} option file(s)` : "",
    data.nifty_files ? `${data.nifty_files} NIFTY file(s)` : "",
    data.vix_files ? `${data.vix_files} VIX file(s)` : "",
    data.error_count ? `⚠ ${data.error_count} file(s) failed` : "",
    data.errors?.length ? data.errors.map((e) => `${e.filename}: ${e.error}`).join("\n") : "",
  ]
    .filter(Boolean)
    .join("\n");
}

async function refreshDhanJsonHint(hintId, dateInputId) {
  const el = byId(hintId);
  if (!el) return;
  const day = getDownloadDate(dateInputId);
  try {
    const inv = await api("/api/data/dhan/json-inventory");
    const match = (inv.dates || []).find((d) => d.date === day);
    if (match?.option_files) {
      el.textContent = `${match.option_files} option JSON file${match.option_files === 1 ? "" : "s"} on disk for ${day} (${inv.raw_path}). Click Import to load into DuckDB — no CSV conversion needed.`;
      el.className = "tw-text-xs tw-text-emerald-700 tw-mb-3";
    } else {
      el.textContent = `No option JSON for ${day} in backend/data/raw/dhan/. Download via Dhan first, or upload a JSON folder below.`;
      el.className = "tw-text-xs tw-text-slate-600 tw-mb-3";
    }
  } catch {
    el.textContent = "Dhan JSON backups live in backend/data/raw/dhan/";
    el.className = "tw-text-xs tw-text-slate-600 tw-mb-3";
  }
}

async function refreshDhanJsonHints() {
  await Promise.all([
    refreshDhanJsonHint("dhanJsonDiskHintYahoo", "optionsDownloadDate"),
    refreshDhanJsonHint("dhanJsonDiskHintDhan", "optionsDownloadDateDhan"),
  ]);
}

async function importDhanJsonFromDisk(dateInputId, resultId) {
  const tradingDate = getDownloadDate(dateInputId);
  const resultEl = byId(resultId);
  if (resultEl) resultEl.textContent = `Importing JSON for ${tradingDate}…`;
  const data = await api("/api/data/dhan/import-json", {
    method: "POST",
    body: JSON.stringify({ trading_date: tradingDate }),
  });
  if (resultEl) resultEl.textContent = formatDhanJsonImportResult(data);
  await refreshChecklist();
  await refreshDhanJsonHints();
  if (wizardStep === WIZARD_STEPS) await loadDataInventory().catch(() => {});
}

function updateJsonFolderCount(inputId, countId) {
  const input = byId(inputId);
  const el = byId(countId);
  if (!input || !el) return;
  const jsonFiles = [...(input.files || [])].filter((f) => f.name.toLowerCase().endsWith(".json"));
  el.textContent =
    jsonFiles.length === 0
      ? "No folder selected"
      : `${jsonFiles.length} JSON file${jsonFiles.length === 1 ? "" : "s"} in folder`;
}

async function importDhanJsonFolder(inputId, resultId, dateInputId = null) {
  const input = byId(inputId);
  const jsonFiles = [...(input?.files || [])].filter((f) => f.name.toLowerCase().endsWith(".json"));
  if (!jsonFiles.length) return alert("Choose a folder containing Dhan .json files");

  const form = new FormData();
  for (const file of jsonFiles) {
    form.append("files", file, file.webkitRelativePath || file.name);
  }
  const tradingDate = dateInputId ? getDownloadDate(dateInputId) : null;
  if (tradingDate) form.append("trading_date", tradingDate);

  const resultEl = byId(resultId);
  if (resultEl) resultEl.textContent = `Importing ${jsonFiles.length} JSON files…`;
  const response = await fetch(`${API_BASE_URL}/api/data/dhan/import-json-bulk`, {
    method: "POST",
    headers: API_KEY ? { "X-API-Key": API_KEY } : {},
    body: form,
  });
  const data = await response.json();
  if (!response.ok) throw new Error(data.detail || "JSON import failed");
  if (resultEl) resultEl.textContent = formatDhanJsonImportResult(data);
  await refreshChecklist();
  await refreshDhanJsonHints();
  if (wizardStep === WIZARD_STEPS) await loadDataInventory().catch(() => {});
}

function updateOptionsFileCount(inputId = "csvFileOptions", countId = "optionsFileCount") {
  const input = byId(inputId);
  const el = byId(countId);
  if (!input || !el) return;
  const n = input.files?.length || 0;
  el.textContent = n === 0 ? "No files selected" : `${n} file${n === 1 ? "" : "s"} selected`;
}

async function startTodayOptions(statusId = "todayOptionsStatus", dateInputId = "optionsDownloadDate") {
  const statusEl = byId(statusId);
  const tradingDate = getDownloadDate(dateInputId);
  try {
    if (statusEl) statusEl.textContent = `Starting options download for ${tradingDate}…`;
    const data = await api("/api/data/dhan/today-options", {
      method: "POST",
      body: JSON.stringify({ trading_date: tradingDate }),
    });
    if (statusEl) statusEl.textContent = `Download started (job ${data.job_id?.slice(0, 8) || "—"})…`;
    if (data.job_id) pollJob(data.job_id, statusId);
  } catch (error) {
    if (statusEl) {
      statusEl.textContent = error.message.includes("Not Found")
        ? "✗ Backend is outdated — restart run-local.sh so /api/data/dhan/today-options exists."
        : `✗ Failed: ${error.message}`;
    }
    throw error;
  }
}

async function startDhanSync() {
  const data = await api("/api/data/dhan/sync", { method: "POST" });
  byId("dhanStatus").textContent = `Download started (job ${data.job_id?.slice(0, 8) || "—"})…`;
  if (data.job_id) pollJob(data.job_id, "dhanStatus");
}

async function startYahooSync() {
  const statusEl = byId("yahooStatus");
  const tradingDate = getDownloadDate("spotDownloadDate");
  syncDownloadDatesFromSpot();
  try {
    if (statusEl) statusEl.textContent = `Downloading NIFTY + VIX for ${tradingDate}…`;
    const data = await api("/api/data/yahoo/sync", {
      method: "POST",
      body: JSON.stringify({ trading_date: tradingDate }),
    });
    if (statusEl) statusEl.textContent = formatStatus(data);
    await refreshChecklist();
    if (wizardStep === WIZARD_STEPS) await loadDataInventory().catch(() => {});
  } catch (error) {
    if (statusEl) {
      statusEl.textContent = error.message.includes("Not Found")
        ? "✗ Backend is outdated — restart the backend (run-local.sh) so /api/data/yahoo/sync exists."
        : `✗ Failed: ${error.message}`;
    }
    throw error;
  }
}

async function pollJob(jobId, targetId) {
  activeJobId = jobId;
  activeJobTargetId = targetId;
  const cancelBtn =
    targetId === "dhanStatus"
      ? byId("cancelDhanJobBtn")
      : targetId === "todayOptionsStatus" || targetId === "todayOptionsStatusDhan"
        ? byId("cancelTodayOptionsBtn")
        : byId("cancelBacktestJobBtn");
  const job = await api(`/api/data/jobs/${jobId}`);
  byId(targetId).textContent = formatJobStatus(job);
  if (job.status === "queued" || job.status === "running") {
    if (cancelBtn) cancelBtn.classList.remove("tw-hidden");
    setTimeout(() => pollJob(jobId, targetId), 2000);
  } else {
    if (cancelBtn) cancelBtn.classList.add("tw-hidden");
    activeJobId = null;
    if (targetId === "runStatus") refreshRuns();
    if (targetId === "dhanStatus" || targetId === "todayOptionsStatus" || targetId === "todayOptionsStatusDhan") {
      refreshChecklist().catch(() => {});
      refreshDhanJsonHints().catch(() => {});
      if (wizardStep === WIZARD_STEPS) loadDataInventory().catch(() => {});
    }
  }
}

async function loadCoverage() {
  const from = byId("covFrom").value;
  const to = byId("covTo").value;
  const coverage = await api(`/api/data/coverage?from_date=${from}&to_date=${to}`);
  const quality = await api(`/api/data/quality?from_date=${from}&to_date=${to}`);
  byId("coverageResult").textContent = formatCoverage(coverage, quality);
  await refreshChecklist();
}

function readSettings() {
  const capital = Number(byId("capitalBudget").value || 100000);
  return {
    capital_budget: capital,
    daily_risk: capital,
    per_trade_risk_cap: capital,
    use_full_capital: true,
    target_rupees: Number(byId("targetRupees").value || 2),
    stop_loss_rupees: Number(byId("stopRupees").value || 10),
    ema_gap_min_points: Number(byId("emaGap").value || 3),
    min_candle_body_ratio: Number(byId("candleBodyRatio").value || 0.5),
    max_trades_per_day: Number(byId("maxTradesPerDay").value || 5),
    max_consecutive_losses: Number(byId("maxConsecutiveLosses").value || 2),
    timeframe: byId("timeframe").value,
    trade_start: byId("tradeStart").value.slice(0, 5),
    time_stop_candles: Number(byId("timeStopCandles").value || 2),
    reentry_cooldown_candles: 1,
    fill_slippage_rupees: 0.5,
    exit_slippage_rupees: 0.5,
    replay_mode: byId("replayMode").value,
    reversal_enabled: byId("reversalEnabled").checked,
    pcr_filter_enabled: byId("pcrFilterEnabled").checked,
    pcr_ce_block: Number(byId("pcrCeBlock").value || 0.7),
    pcr_pe_block: Number(byId("pcrPeBlock").value || 1.3),
    dynamic_exits_enabled: byId("dynamicExitsEnabled").checked,
    trail_enabled: byId("trailEnabled").checked,
    cooldown_enabled: byId("cooldownEnabled").checked,
    spread_filter_enabled: byId("spreadFilterEnabled").checked,
    vix_filter_enabled: byId("vixFilterEnabled").checked,
    max_india_vix: Number(byId("maxIndiaVix").value || 22),
    option_chain_window: 10,
    chain_staleness_seconds: 75,
  };
}

async function runBacktest(event) {
  event.preventDefault();
  const payload = {
    settings: readSettings(),
    from_date: byId("runFrom").value,
    to_date: byId("runTo").value,
    cost_preset: byId("costPreset").value,
  };
  const data = await api("/api/backtests/runs", {
    method: "POST",
    body: JSON.stringify(payload),
  });
  byId("runStatus").textContent = `Backtest queued (job ${data.job_id?.slice(0, 8) || "—"})…`;
  if (data.job_id) pollJob(data.job_id, "runStatus");
  setTimeout(refreshRuns, 3000);
  setTimeout(() => {
    location.hash = "results";
    window.scrollTo({ top: 0, behavior: "smooth" });
  }, 1500);
}

function statusBadge(status) {
  const colors = {
    completed: "tw-bg-emerald-100 tw-text-emerald-700",
    running: "tw-bg-blue-100 tw-text-blue-700",
    failed: "tw-bg-red-100 tw-text-red-700",
    cancelled: "tw-bg-slate-100 tw-text-slate-600",
  };
  const cls = colors[status] || "tw-bg-slate-100 tw-text-slate-600";
  return `<span class="tw-inline-block tw-px-2 tw-py-0.5 tw-rounded tw-text-xs tw-font-medium ${cls}">${status}</span>`;
}

function pnlClass(pnl) {
  return pnl >= 0 ? "tw-text-emerald-600 tw-font-semibold" : "tw-text-red-600 tw-font-semibold";
}

async function refreshRuns() {
  const runs = await api("/api/backtests/runs");
  byId("runsTableBody").innerHTML = runs
    .map(
      (run) => {
        const pnl = run.summary?.net_pnl || 0;
        const selected = selectedRunId === run.id;
        return `<tr data-run-id="${run.id}" class="tw-cursor-pointer hover:tw-bg-slate-50 ${selected ? "tw-bg-violet-50" : ""}">
        <td class="tw-px-4 tw-py-3"><button type="button" class="tw-font-mono tw-text-xs tw-text-violet-600 hover:tw-underline" data-select-run="${run.id}">${run.id.slice(0, 8)}</button></td>
        <td class="tw-px-4 tw-py-3 tw-text-slate-600">${run.date_from} → ${run.date_to}</td>
        <td class="tw-px-4 tw-py-3 tw-text-slate-600">${run.replay_mode === "full_context" ? "Full" : "Core"}</td>
        <td class="tw-px-4 tw-py-3">${statusBadge(run.status)}</td>
        <td class="tw-px-4 tw-py-3 tw-text-right ${pnlClass(pnl)}">${formatInr.format(pnl)}</td>
        <td class="tw-px-4 tw-py-3 tw-text-right tw-text-slate-600">${run.summary?.total_trades || 0}</td>
      </tr>`;
      }
    )
    .join("");

  document.querySelectorAll("[data-select-run]").forEach((button) => {
    button.addEventListener("click", (e) => {
      e.stopPropagation();
      selectRun(button.dataset.selectRun);
    });
  });
  document.querySelectorAll("#runsTableBody tr").forEach((row) => {
    row.addEventListener("click", () => selectRun(row.dataset.runId));
  });

  if (runs[0]) {
    const r = runs[0];
    const pnl = r.summary?.net_pnl || 0;
    byId("runsSummary").innerHTML = `
      <div class="tw-rounded-xl tw-border tw-border-slate-200 tw-bg-white tw-p-4">
        <p class="tw-text-xs tw-text-slate-500 tw-mb-1">Latest run</p>
        <p class="tw-font-mono tw-text-sm tw-font-semibold">${r.id.slice(0, 8)}</p>
      </div>
      <div class="tw-rounded-xl tw-border tw-border-slate-200 tw-bg-white tw-p-4">
        <p class="tw-text-xs tw-text-slate-500 tw-mb-1">Net P&amp;L</p>
        <p class="tw-text-lg tw-font-semibold ${pnlClass(pnl)}">${formatInr.format(pnl)}</p>
      </div>
      <div class="tw-rounded-xl tw-border tw-border-slate-200 tw-bg-white tw-p-4">
        <p class="tw-text-xs tw-text-slate-500 tw-mb-1">Trades</p>
        <p class="tw-text-lg tw-font-semibold tw-text-slate-900">${r.summary?.total_trades || 0}</p>
      </div>
      <div class="tw-rounded-xl tw-border tw-border-slate-200 tw-bg-white tw-p-4">
        <p class="tw-text-xs tw-text-slate-500 tw-mb-1">Max drawdown</p>
        <p class="tw-text-lg tw-font-semibold tw-text-slate-900">${formatInr.format(r.summary?.max_drawdown || 0)}</p>
      </div>`;
    if (!selectedRunId) selectRun(runs[0].id);
  }
}

async function selectRun(runId) {
  selectedRunId = runId;
  byId("replayRunId").value = runId;
  byId("selectedRunPanel")?.classList.remove("tw-hidden");
  const trades = await api(`/api/backtests/runs/${runId}/trades`);
  byId("tradesTableBody").innerHTML = trades
    .map(
      (t) => {
        const pnl = t.pnl || 0;
        return `<tr class="hover:tw-bg-slate-50">
        <td class="tw-px-4 tw-py-2">${t.side || "—"}</td>
        <td class="tw-px-4 tw-py-2">${t.strike || "—"}</td>
        <td class="tw-px-4 tw-py-2 tw-text-xs tw-text-slate-500">${(t.entry_time || "—").replace("T", " ").slice(0, 16)}</td>
        <td class="tw-px-4 tw-py-2 tw-text-xs tw-text-slate-500">${(t.exit_time || "—").replace("T", " ").slice(0, 16)}</td>
        <td class="tw-px-4 tw-py-2">${t.result || t.status || "—"}</td>
        <td class="tw-px-4 tw-py-2 tw-text-right ${pnlClass(pnl)}">${formatInr.format(pnl)}</td>
      </tr>`;
      }
    )
    .join("") || `<tr><td colspan="6" class="tw-px-4 tw-py-6 tw-text-center tw-text-slate-400">No trades in this run</td></tr>`;
  const equity = await api(`/api/backtests/runs/${runId}/equity`);
  renderEquityChart(byId("equityChart"), equity);
  renderDrawdownChart(byId("drawdownChart"), equity);
  loadReplay().catch(() => {});
}

async function compareRuns() {
  const ids = byId("compareRunIds").value.split(",").map((v) => v.trim()).filter(Boolean);
  if (ids.length < 2) return alert("Enter at least two run IDs");
  const data = await api("/api/backtests/compare", {
    method: "POST",
    body: JSON.stringify({ run_ids: ids }),
  });
  byId("compareResult").textContent = JSON.stringify(data, null, 2);
}

async function exportRun(format) {
  if (!selectedRunId) return alert("Select a run first");
  const data = await api(`/api/backtests/runs/${selectedRunId}/export?format=${format}`);
  if (format === "json") {
    const blob = new Blob([JSON.stringify(data, null, 2)], { type: "application/json" });
    downloadBlob(blob, `backtest-${selectedRunId.slice(0, 8)}.json`);
  } else {
    const blob = new Blob([data.content], { type: format === "html" ? "text/html" : "text/csv" });
    downloadBlob(blob, `backtest-${selectedRunId.slice(0, 8)}.${format}`);
  }
}

function downloadBlob(blob, filename) {
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = filename;
  anchor.click();
  URL.revokeObjectURL(url);
}

function escapeHtml(text) {
  return String(text ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function formatSignalTime(ts) {
  if (!ts) return "—";
  return String(ts).replace("T", " ").slice(0, 16);
}

function signalStatusBadge(status) {
  if (status === "Taken") {
    return '<span class="tw-inline-flex tw-px-2 tw-py-0.5 tw-rounded tw-bg-emerald-100 tw-text-emerald-800 tw-text-xs tw-font-medium">Taken</span>';
  }
  return '<span class="tw-inline-flex tw-px-2 tw-py-0.5 tw-rounded tw-bg-slate-100 tw-text-slate-600 tw-text-xs tw-font-medium">Skipped</span>';
}

function formatOptionPrice(value) {
  if (value == null || value === "") return "—";
  return `₹${Number(value).toFixed(2)}`;
}

function renderReplayTable(signals) {
  const body = byId("replayTableBody");
  if (!body) return;
  if (!signals?.length) {
    body.innerHTML =
      '<tr><td colspan="11" class="tw-px-4 tw-py-6 tw-text-center tw-text-slate-400">No signals in this run</td></tr>';
    return;
  }
  body.innerHTML = signals
    .map(
      (s) => `<tr class="hover:tw-bg-slate-50 tw-align-top">
      <td class="tw-px-3 tw-py-2 tw-text-xs tw-whitespace-nowrap tw-text-slate-600">${formatSignalTime(s.timestamp)}</td>
      <td class="tw-px-3 tw-py-2">${signalStatusBadge(s.status)}</td>
      <td class="tw-px-3 tw-py-2 tw-font-medium tw-text-slate-900">${escapeHtml(s.side || "—")}</td>
      <td class="tw-px-3 tw-py-2">${s.strike ?? "—"}</td>
      <td class="tw-px-3 tw-py-2 tw-text-xs tw-text-slate-600">${escapeHtml(s.signal_layer || "—")}</td>
      <td class="tw-px-3 tw-py-2 tw-text-right tw-text-slate-600">${s.spot != null ? Number(s.spot).toFixed(1) : "—"}</td>
      <td class="tw-px-3 tw-py-2 tw-text-right tw-text-slate-600">${s.ema_gap != null ? Number(s.ema_gap).toFixed(1) : "—"}</td>
      <td class="tw-px-3 tw-py-2 tw-text-right tw-text-slate-600">${s.lots || "—"}</td>
      <td class="tw-px-3 tw-py-2 tw-text-right tw-font-medium tw-text-slate-900">${formatOptionPrice(s.entry_price)}</td>
      <td class="tw-px-3 tw-py-2 tw-text-right tw-font-medium tw-text-slate-900">${formatOptionPrice(s.exit_price)}</td>
      <td class="tw-px-3 tw-py-2 tw-text-xs tw-text-slate-600 tw-max-w-lg">${escapeHtml(s.reason || "—")}</td>
    </tr>`
    )
    .join("");
}

function setReplayView(mode) {
  const isTable = mode === "table";
  byId("replayTableWrap")?.classList.toggle("tw-hidden", !isTable);
  byId("replayLog")?.classList.toggle("tw-hidden", isTable);
  const tableBtn = byId("replayViewTableBtn");
  const jsonBtn = byId("replayViewJsonBtn");
  if (tableBtn) {
    tableBtn.classList.toggle("tw-bg-slate-900", isTable);
    tableBtn.classList.toggle("tw-text-white", isTable);
    tableBtn.classList.toggle("tw-bg-white", !isTable);
    tableBtn.classList.toggle("tw-text-slate-700", !isTable);
  }
  if (jsonBtn) {
    jsonBtn.classList.toggle("tw-bg-slate-900", !isTable);
    jsonBtn.classList.toggle("tw-text-white", !isTable);
    jsonBtn.classList.toggle("tw-bg-white", isTable);
    jsonBtn.classList.toggle("tw-text-slate-700", isTable);
  }
}

async function loadReplay() {
  const runId = byId("replayRunId").value;
  if (!runId) return alert("Enter a run ID or select one from Results");
  const data = await api(`/api/backtests/runs/${runId}/replay`);
  renderReplayTable(data.signals || []);
  byId("replayLog").textContent = JSON.stringify(data, null, 2);
  const taken = (data.signals || []).filter((s) => s.status === "Taken").length;
  const skipped = (data.signals || []).filter((s) => s.status === "Skipped").length;
  const summary = byId("replaySummary");
  if (summary) {
    summary.textContent = `${data.signals?.length || 0} signals · ${taken} taken · ${skipped} skipped · ${data.trades?.length || 0} trades`;
  }
  setReplayView("table");
  renderEquityChart(byId("equityChart"), data.equity || []);
  renderDrawdownChart(byId("drawdownChart"), data.equity || []);
}

byId("previewCsvBtn").addEventListener("click", () => previewCsv().catch((e) => alert(e.message)));
byId("importCsvBtn").addEventListener("click", () => importCsv().catch((e) => alert(e.message)));
byId("previewCsvBtnYahoo")?.addEventListener("click", () => previewCsv("csvFileOptionsYahoo", "importResultYahoo").catch((e) => alert(e.message)));
byId("importCsvBtnYahoo")?.addEventListener("click", () => importCsv("csvFileOptionsYahoo", "importResultYahoo").catch((e) => alert(e.message)));
byId("csvFileOptions")?.addEventListener("change", () => updateOptionsFileCount());
byId("csvFileOptionsYahoo")?.addEventListener("change", () => updateOptionsFileCount("csvFileOptionsYahoo", "optionsFileCountYahoo"));
byId("importNiftyBtn")?.addEventListener("click", () => importCsvForType("nifty_candles", "csvFileNifty", "importResultNifty").catch((e) => alert(e.message)));
byId("importVixBtn")?.addEventListener("click", () => importCsvForType("india_vix", "csvFileVix", "importResultVix").catch((e) => alert(e.message)));
byId("dhanSyncBtn").addEventListener("click", () => startDhanSync().catch((e) => alert(e.message)));
byId("yahooSyncBtn")?.addEventListener("click", () => startYahooSync().catch((e) => alert(e.message)));
byId("todayOptionsBtn")?.addEventListener("click", () => startTodayOptions("todayOptionsStatus", "optionsDownloadDate").catch((e) => alert(e.message)));
byId("todayOptionsBtnDhan")?.addEventListener("click", () => startTodayOptions("todayOptionsStatusDhan", "optionsDownloadDateDhan").catch((e) => alert(e.message)));
byId("cancelTodayOptionsBtn")?.addEventListener("click", () => cancelActiveJob().catch((e) => alert(e.message)));
byId("cancelDhanJobBtn").addEventListener("click", () => cancelActiveJob().catch((e) => alert(e.message)));
byId("cancelBacktestJobBtn").addEventListener("click", () => cancelActiveJob().catch((e) => alert(e.message)));
byId("coverageBtn").addEventListener("click", () => loadCoverage().catch((e) => alert(e.message)));
byId("covFrom")?.addEventListener("change", () => refreshChecklist().catch(() => {}));
byId("covTo")?.addEventListener("change", () => refreshChecklist().catch(() => {}));
byId("backtestForm").addEventListener("submit", (e) => runBacktest(e).catch((err) => alert(err.message)));
byId("refreshRunsBtn").addEventListener("click", () => refreshRuns().catch((e) => alert(e.message)));
byId("loadReplayBtn").addEventListener("click", () => loadReplay().catch((e) => alert(e.message)));
byId("replayViewTableBtn")?.addEventListener("click", () => setReplayView("table"));
byId("replayViewJsonBtn")?.addEventListener("click", () => setReplayView("json"));
byId("compareBtn").addEventListener("click", () => compareRuns().catch((e) => alert(e.message)));
byId("exportJsonBtn").addEventListener("click", () => exportRun("json").catch((e) => alert(e.message)));
byId("exportCsvBtn").addEventListener("click", () => exportRun("csv").catch((e) => alert(e.message)));
byId("exportHtmlBtn").addEventListener("click", () => exportRun("html").catch((e) => alert(e.message)));

refreshRuns().catch(() => {});
loadDataStatus().catch(() => {});
initWizard();
initStrategyWizard();
