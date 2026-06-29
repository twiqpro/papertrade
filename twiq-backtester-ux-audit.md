# Twiq Backtester — UX Audit & Improvement Suggestions

> **Prepared for:** Cursor AI  
> **Date:** 2026-06-25  
> **App URL:** https://papertrade-rho.vercel.app/backtester.html  
> **Scope:** Full single-page backtester UI — navigation, wizards, forms, results, accessibility, and visual consistency.

---

## Table of Contents

1. [Executive Summary](#1-executive-summary)
2. [Information Architecture & Navigation](#2-information-architecture--navigation)
3. [Step 1 — Load Data Wizard](#3-step-1--load-data-wizard)
4. [Step 2 — Run Strategy Wizard](#4-step-2--run-strategy-wizard)
5. [Step 3 — Results Section](#5-step-3--results-section)
6. [Step 4 — Decision Log (Replay Log)](#6-step-4--decision-log-replay-log)
7. [Accessibility (WCAG 2.1 AA)](#7-accessibility-wcag-21-aa)
8. [Visual Design Consistency](#8-visual-design-consistency)
9. [Performance & State Management](#9-performance--state-management)
10. [Quick-Win Priority Matrix](#10-quick-win-priority-matrix)

---

## 1. Executive Summary

Twiq Backtester is a developer-oriented, single-page backtesting tool for NIFTY options strategies. The UI follows a two-wizard pattern (Load Data → Run Strategy) rendered as vertically stacked sections on a single scrollable page, with a persistent left sidebar for high-level navigation. 

The overall aesthetic is clean and minimal (Tailwind-based, monochrome palette with a green accent). However several structural and accessibility issues reduce the quality of the experience for both first-time and returning users. The most impactful problems are:

- **Dual-wizard collision on one page** — two independent wizard flows (Load Data and Run Strategy) are both visible simultaneously, creating confusion about what step the user is currently on.
- **No visual state persistence** — the sidebar always shows "1. Load Data" as active regardless of scroll position or wizard step.
- **Zero ARIA labelling** — not a single `<input>`, `<select>`, `<button>`, or card has an `aria-label` or `for/id` label association; keyboard and screen-reader users cannot use the tool.
- **Disconnected progress indicators** — each wizard has its own stepper bar, but neither stepper is interactive or provides any status feedback (completed, error, locked).
- **Results section is always empty on load** — there is no empty-state illustration or guidance, making the page look broken on first visit.

---

## 2. Information Architecture & Navigation

### 2.1 Sidebar navigation does not reflect scroll position

**Problem:** The left sidebar contains four numbered links (1. Load Data, 2. Run Strategy, 3. Results, 4. Replay Log) that act as anchor links. The active state (green highlight) is hardcoded on "1. Load Data" and never updates as the user scrolls to other sections.

**Fix:**
```js
// Use IntersectionObserver to update the active sidebar link
const sections = document.querySelectorAll('.workspace-pane');
const navLinks = document.querySelectorAll('nav a[href^="#"]');

const observer = new IntersectionObserver(
  (entries) => {
    entries.forEach(entry => {
      if (entry.isIntersecting) {
        navLinks.forEach(link => link.classList.remove('active'));
        const activeLink = document.querySelector(`nav a[href="#${entry.target.id}"]`);
        activeLink?.classList.add('active');
      }
    });
  },
  { threshold: 0.3 }
);
sections.forEach(section => observer.observe(section));
```

### 2.2 Two wizard flows stacked vertically on one page

**Problem:** Both the Load Data wizard (4 steps) and the Run Strategy wizard (3 steps) are rendered as full-width stacked sections on the same scrolling page. This creates ambiguity — does "Next →" in the Load Data wizard advance Load Data steps, or does it navigate to Run Strategy? Each wizard has its own progress bar, but they appear to be completely separate systems with no visual grouping separating them.

**Fix:** Separate the two wizards into clearly labelled "Phase" containers with a divider heading, or better yet, render them as true page-level steps where completing Phase 1 (Load Data) automatically scrolls/animates to Phase 2 (Run Strategy). Add a sticky breadcrumb bar that reads: **Phase 1: Load Data → Phase 2: Run Strategy → Phase 3: Results**.

### 2.3 The "Next →" button at Load Data step 1 is disabled (grey) with no explanation

**Problem:** The "Next →" button in the Load Data section is visually greyed out but there is no tooltip, inline message, or validation feedback explaining what the user needs to do to enable it. Users are left guessing.

**Fix:**
```html
<!-- Add a helper text element that appears when Next is hovered/focused in disabled state -->
<button id="wizardNext1" disabled aria-describedby="next-hint">Next →</button>
<p id="next-hint" class="hint-text">Select a data source above to continue.</p>
```
Also add an `aria-disabled="true"` and remove `disabled` in favour of preventing the click in JS so the button remains focusable and screen-reader accessible.

### 2.4 No breadcrumb or "you are here" context when using sidebar links

**Problem:** Clicking "3. Results" in the sidebar jumps directly to the Results section, but there is no visual signal confirming the user has reached it (no section header scroll-into-view animation, no active state update).

**Fix:** Add a `scroll-margin-top` CSS property to each section to account for the sticky header, and trigger a brief highlight animation on the section heading when it is anchor-navigated to.

---

## 3. Step 1 — Load Data Wizard

### 3.1 Source selection cards have no selected state

**Problem:** The three data source cards (Dhan API, Upload CSV, Yahoo Finance) are `<button>` elements but clicking one does not produce any persistent visual "selected" state. There is no check mark, border change, or background fill indicating which option is currently active.

**Fix:**
```css
/* Add a selected state to source cards */
.source-card[aria-pressed="true"] {
  border: 2px solid var(--accent-strong);
  background: var(--accent-soft);
}
.source-card[aria-pressed="true"]::after {
  content: "✓";
  position: absolute;
  top: 8px;
  right: 12px;
  color: var(--good);
  font-weight: 700;
}
```
Set `aria-pressed="true"` on the selected card and `aria-pressed="false"` on the rest. The cards should also use `role="radio"` within a `role="radiogroup"` since only one can be selected at a time.

### 3.2 Wizard stepper (Source → Load data → Optional → Verify) is not interactive

**Problem:** The four-step progress bar at the top of the Load Data wizard is purely decorative. Steps are not clickable, do not show completion status (checkmarks), and do not indicate which steps are locked vs accessible.

**Fix:** Make each completed step clickable (navigating back), add a checkmark icon to completed steps, use distinct styling for: active (dark, underlined), completed (green check), upcoming (grey), and blocked/locked (grey + lock icon if data is required first).

```html
<!-- Example step markup -->
<div class="wizard-step completed" role="tab" aria-selected="false" tabindex="0">
  <span class="step-icon">✓</span>
  <span class="step-label">Source</span>
</div>
<div class="wizard-step active" role="tab" aria-selected="true" aria-current="step">
  <span class="step-label">Load data</span>
</div>
```

### 3.3 "Download options for date" and "Import JSON for selected date" appear regardless of source selection

**Problem:** Multiple sub-panels (download buttons, import buttons, file pickers) for Dhan, CSV, and Yahoo Finance all render inside the same container without clear conditional visibility. When a user selects "Upload CSV", they still see Dhan-specific buttons like "Download options for date" and "Import JSON for selected date" further down the page.

**Fix:** Strictly show only the sub-panel content relevant to the selected source. Use a JavaScript-driven show/hide pattern tied to the source card selection.

### 3.4 "Stored locally in backend/data/" helper text is easy to miss

**Problem:** This important note (telling the user where data is persisted) is rendered in a very small, muted font floating in the middle of the page footer area, sandwiched between two `Next →` buttons (one greyed, one active). It looks like an afterthought.

**Fix:** Move this into a dedicated info banner or tooltip near the relevant section (e.g., next to the "Start download" button), styled with an ℹ️ icon and a subtle blue-tinted background.

### 3.5 Verify step (step 4) has a "From / To" date range that duplicates Step 2's date range

**Problem:** There are two separate From/To date pickers — one in the Verify step of Load Data (`covFrom`/`covTo`) and one in the Period step of Run Strategy (`runFrom`/`runTo`). These appear to be two separate state variables. If a user changes one, the other does not update, which can result in running a backtest with different dates than the data that was downloaded/verified.

**Fix:** Bind both date pickers to the same state, or auto-populate `runFrom`/`runTo` from the verified coverage range after the user completes the Verify step. Show a clear warning if the Run Strategy dates fall outside the verified data coverage.

---

## 4. Step 2 — Run Strategy Wizard

### 4.1 Strategy mode card (Core vs Full Context) has an invisible select element underneath

**Problem:** The two strategy mode cards (Core, Full Context) appear to be `<button>` elements, but there is also a hidden `<select id="replayMode">` synced underneath them. The selected card (Full Context) has a dark border but the unselected card (Core) has no visual distinction from its default state other than the missing border. On first glance both cards appear the same weight.

**Fix:** Visually differentiate selected vs unselected cards more strongly: give the selected card a filled dark background with white text, and the unselected card a light grey background with a dashed border. Remove the hidden `<select>` and manage state entirely in JavaScript to avoid confusion.

### 4.2 Advanced Settings accordion is collapsed by default with no affordance

**Problem:** The advanced settings section (candle body ratio, max trades/day, PCR filters, VIX filters, etc.) is hidden inside a `<details>` element labelled "Advanced settings" in tiny muted text. Users who need these controls may not notice it exists.

**Fix:** Add a ▼ chevron icon that rotates on open, change the text weight to medium, and add a badge showing how many advanced settings differ from defaults — e.g. "Advanced settings (3 modified)". This gives power users a clear signal that customisation exists without overwhelming beginners.

```html
<summary class="advanced-toggle">
  Advanced settings
  <span class="advanced-badge" id="advancedBadge"><!-- "3 modified" --></span>
  <svg class="chevron" .../>
</summary>
```

### 4.3 "Trading rules" section mixes core parameters with Full Context-only parameters

**Problem:** Parameters such as PCR CE/PE thresholds and Max VIX are shown inside the Advanced Settings block regardless of whether the user selected Core or Full Context mode. In Core mode, these filters are irrelevant and should be greyed out or hidden.

**Fix:** Dynamically show/hide or disable the "FULL CONTEXT FILTERS" sub-section based on the selected strategy mode. Add a label like "Only applies in Full Context mode" to those fields if they must remain visible.

### 4.4 Number inputs have no units or range hints

**Problem:** Fields like "Target (₹)", "Stop loss (₹)", "EMA min gap", "Candle body ratio", and "Capital (₹)" have no `min`, `max`, or `step` attributes, and no hint text explaining valid ranges or what the unit represents.

**Fix:**
```html
<label for="targetRupees">
  Target
  <span class="unit">₹ per lot</span>
</label>
<input 
  id="targetRupees" 
  type="number" 
  min="0.5" 
  max="100" 
  step="0.5" 
  value="2"
  aria-describedby="targetRupees-hint"
/>
<p id="targetRupees-hint" class="field-hint">Typical range: ₹1 – ₹20</p>
```

### 4.5 "Run backtest" button has no loading state

**Problem:** After clicking "Run backtest", there is no visual feedback (spinner, progress bar, disabled state) confirming that the backtest is running. The user cannot tell if their click registered.

**Fix:** Immediately disable the button and replace its label with a spinner + "Running…" text on click. Show a live status message (e.g., "Processing 47 trading days…") and re-enable the button when done. Add a cancel mechanism.

```js
runBtn.addEventListener('click', () => {
  runBtn.disabled = true;
  runBtn.innerHTML = '<span class="spinner"></span> Running…';
  // ... run logic
});
```

### 4.6 Transaction costs select uses an em-dash (—) in option labels

**Problem:** The options read "Base — ₹0.50 each side" using an em-dash. This can be misread in some screen readers and is not consistent with standard UI copy conventions.

**Fix:** Rewrite as "Base (₹0.50/side)" or "Base · ₹0.50 each side" using a middle dot, which is both more readable and more screen-reader friendly.

---

## 5. Step 3 — Results Section

### 5.1 Empty state is a bare table header with no guidance

**Problem:** When no backtest has been run, the Results section shows a table with column headers (RUN, DATES, MODE, STATUS, P&L, TRADES) but no rows and no empty-state message. This looks like a rendering error.

**Fix:** Replace the empty table with an illustrated empty state component:
```html
<div class="empty-state" role="status" aria-live="polite">
  <svg class="empty-icon" .../>  <!-- e.g. a chart with a play button -->
  <h3>No backtest runs yet</h3>
  <p>Configure your strategy in Step 2 and click "Run backtest" to see results here.</p>
  <a href="#strategy-lab" class="btn-primary">Go to Run Strategy →</a>
</div>
```

### 5.2 "Selected run" and equity curve sections are permanently visible even with no data

**Problem:** The "Selected run", "Equity curve", "Drawdown", and trade table headers are all visible on load. With no data, these appear as orphaned section titles with nothing below them.

**Fix:** Conditionally render these sub-sections only after a run is selected. Use `display: none` or a `v-if`-equivalent pattern until data exists.

### 5.3 Compare & export accordion has no description of what "Compare" does

**Problem:** The "Compare & export" accordion contains a text input for "Compare run IDs (comma-separated)" and buttons for JSON/CSV/HTML export. There is no explanation of what the comparison produces (a side-by-side P&L table? a chart overlay?).

**Fix:** Add a one-line description: "Side-by-side comparison of P&L, win rate, and drawdown across selected runs." Rename the "Compare" button to "Compare runs" for clarity.

### 5.4 Export buttons (JSON, CSV, HTML) have no feedback

**Problem:** Clicking JSON, CSV, or HTML export buttons produces no visual feedback — no toast notification, no download progress, no confirmation that a file was created.

**Fix:** Show a brief toast notification ("✓ CSV downloaded: run_20260625.csv") after each export action.

---

## 6. Step 4 — Decision Log (Replay Log)

### 6.1 Run ID must be manually typed — no autocomplete from Results

**Problem:** The Decision Log section requires the user to manually type a Run ID from memory. The user must scroll back up to Results, read the Run ID, then scroll back down and type it.

**Fix:** Replace the text input with a `<select>` dropdown populated from the results list, or add a "View in Decision Log" button directly on each row of the Results table that auto-fills and loads the log.

```html
<label for="replayRunId">Select run</label>
<select id="replayRunId">
  <option value="">— choose a run —</option>
  <!-- populated dynamically from results -->
</select>
```

### 6.2 "Table" and "JSON" toggle buttons have no selected state

**Problem:** The Table/JSON view toggle buttons next to the "Load" button have no visual indication of which view is currently active. Both appear the same.

**Fix:** Apply an `aria-pressed="true"` / active class to the selected view mode button, giving it a dark background or underline to distinguish the active choice.

### 6.3 Decision log table has 11 columns — likely to overflow on smaller screens

**Problem:** The decision log table has 11 columns (Time, Status, Side, Strike, Layer, Spot, EMA gap, Lots, Entry, Exit, Reason). On a 1280px viewport many of these will overflow or wrap awkwardly.

**Fix:** Make the table horizontally scrollable within its container, pin the Time + Status columns, and allow the remaining columns to scroll horizontally. Alternatively collapse some columns into a row-expand pattern for mobile/compact views.

---

## 7. Accessibility (WCAG 2.1 AA)

### 7.1 No `<label for>` associations on any input

**Critical.** An audit of all 40+ form inputs shows that **zero inputs have a properly associated `<label for="id">` or `aria-label` attribute**. The labels use wrapper `<label>` elements without a `for` attribute, so the association only works if the `<input>` is the direct child — but several labels wrap descriptive text alongside the input, breaking the implicit association.

**Fix:** Add explicit `for` attributes on every `<label>` matching the `id` of its `<input>`:
```html
<!-- Before -->
<label>Target (₹) <input id="targetRupees" type="number" /></label>

<!-- After -->
<label for="targetRupees">Target (₹)</label>
<input id="targetRupees" type="number" aria-describedby="targetRupees-hint" />
```

### 7.2 Source selection buttons have no ARIA role or label

**Critical.** The three data source cards are plain `<button>` elements. Their accessible name is computed from their emoji + text children, but the emoji (⚡, 📄, 📈) will be read aloud by screen readers as "High voltage sign", "Page facing up", "Chart increasing". This is confusing.

**Fix:**
```html
<button 
  role="radio" 
  aria-checked="false" 
  aria-label="Dhan API — recommended, auto-downloads NIFTY and options"
  class="source-card"
>
  <!-- visual content -->
</button>
```
Wrap the three buttons in a `<div role="radiogroup" aria-label="Data source">`.

### 7.3 No skip link for keyboard users

**Fix:** Add a skip-to-main-content link as the very first focusable element:
```html
<a href="#data-manager" class="skip-link">Skip to main content</a>
```
```css
.skip-link {
  position: absolute;
  top: -40px;
  left: 0;
  background: var(--accent-strong);
  color: #000;
  padding: 8px 16px;
  z-index: 9999;
  transition: top 0.2s;
}
.skip-link:focus { top: 0; }
```

### 7.4 Colour contrast — "Recommended" badge and muted helper text may fail AA

**Problem:** The "Recommended" badge uses `--accent-text: #166534` on `--accent-soft: #ecfdf3`. This passes AA (~5.2:1). However muted helper text uses `--muted: #666666` on white (`#ffffff`), which is 5.74:1 — borderline. The "Load data", "Optional", "Verify" step labels in the stepper use an even lighter grey (`--muted-light: #999999` on white = 2.85:1), which **fails** WCAG AA (minimum 4.5:1 for normal text).

**Fix:** Darken the inactive stepper label colour to at least `#767676` (4.54:1), or increase the font weight to bold (large text threshold of 3:1 applies at 18px+ bold).

### 7.5 File input buttons are unstyled browser defaults

**Problem:** Multiple `<input type="file">` elements appear as raw browser default file picker buttons. These are styled inconsistently with the rest of the UI.

**Fix:** Visually hide the `<input type="file">` and use a styled `<label>` as the trigger button, maintaining full keyboard accessibility:
```html
<label for="csvFileNifty" class="btn-secondary">
  Choose NIFTY CSV…
</label>
<input id="csvFileNifty" type="file" class="visually-hidden" accept=".csv" />
```

### 7.6 No `aria-live` regions for dynamic feedback

**Problem:** Status messages (e.g., "✓ Credentials found — ready to download", "Not loaded", download progress) are rendered in the DOM but not announced to screen readers.

**Fix:** Wrap all status message containers in `aria-live="polite"` (or `"assertive"` for errors):
```html
<div aria-live="polite" aria-atomic="true" id="download-status"></div>
```

---

## 8. Visual Design Consistency

### 8.1 Two different "Next →" button styles

**Problem:** There are two "Next →" buttons visible on the initial page load — one in the Load Data section (grey/disabled) and one in the Run Strategy section (dark/enabled). Both use the same label but visually different states, which is confusing. There is also a "← Back" button without clear context about what "back" means in a single-page scroll layout.

**Fix:** Reserve "Next →" / "← Back" for within-wizard navigation only. Replace cross-section navigation with named buttons ("Continue to Run Strategy →") so the user always knows where they are going.

### 8.2 Section separators are missing

**Problem:** The four main sections (Load Data, Run Strategy, Results, Decision Log) flow into each other with only a heading change. There is no visual separator (divider line, card container, background colour change) making it hard to perceive where one section ends and another begins — especially when the page is partially scrolled.

**Fix:** Wrap each major section in a card with a `--panel-muted` background, or add a subtle `border-top` + generous top padding between sections. Alternatively add a sticky section header that shows the current section name while scrolling.

### 8.3 Inconsistent button hierarchy

**Problem:** Three different button styles appear but their hierarchy is unclear:
- Dark filled (e.g., "Run backtest", active "Next →")
- White outlined (e.g., "Refresh", "← Back")  
- Plain text buttons (e.g., "Cancel")

The "Cancel" button in the download flow uses the same styling as destructive/secondary actions, but in the strategy form it sits next to "Run backtest" at the same visual weight, making the primary CTA less prominent.

**Fix:** Establish and document a three-tier button system:
- **Primary** (dark fill): one per section, for the main forward action
- **Secondary** (outlined): for reversible/neutral actions  
- **Ghost/Tertiary** (text only): for cancel or low-importance actions

### 8.4 The "B" logo / avatar in the sidebar has no functional purpose

**Problem:** The sidebar header shows a dark square with "B" and "Backtester / Historical replay". This appears decorative but takes up considerable space and may confuse users into thinking it is a clickable avatar or home link.

**Fix:** Either make it a clickable home/reset button (with a `title="Back to home"` tooltip and `href`), or reduce its size and de-emphasise it as pure branding.

### 8.5 Page title in `<title>` tag is generic

**Problem:** The browser tab title is just "Twiq Backtester" regardless of the current step. When a user has multiple tabs open, this makes it impossible to distinguish tabs.

**Fix:** Dynamically update the document title based on the current wizard step:
```js
document.title = `Step 1: Load Data — Twiq Backtester`;
// Update on step change
```

---

## 9. Performance & State Management

### 9.1 No state persistence between page refreshes

**Problem:** All wizard form values (selected source, date range, strategy parameters) are lost on page refresh. For a tool that involves multi-step configuration, this is a significant usability regression — users who accidentally close the tab must reconfigure everything.

**Fix:** Persist wizard state to `localStorage` on every change and restore it on page load:
```js
const STORAGE_KEY = 'twiq_backtest_config';

function saveState() {
  const state = {
    source: selectedSource,
    runFrom: document.getElementById('runFrom').value,
    runTo: document.getElementById('runTo').value,
    mode: document.getElementById('replayMode').value,
    // ... all other fields
  };
  localStorage.setItem(STORAGE_KEY, JSON.stringify(state));
}

function restoreState() {
  const saved = JSON.parse(localStorage.getItem(STORAGE_KEY) || '{}');
  if (saved.runFrom) document.getElementById('runFrom').value = saved.runFrom;
  // ... restore all fields
}
```

### 9.2 No error handling UI visible for failed API calls

**Problem:** The app makes backend API calls (Dhan download, Yahoo Finance fetch) but there is no visible error state UI — no error banner, no retry button, no explanation of why something failed.

**Fix:** Add a global error toast component and ensure every async action has a `.catch()` handler that renders a human-readable error:
```js
function showError(message) {
  const toast = document.createElement('div');
  toast.className = 'error-toast';
  toast.setAttribute('role', 'alert');
  toast.textContent = message;
  document.body.appendChild(toast);
  setTimeout(() => toast.remove(), 5000);
}
```

### 9.3 "Refresh" button in Results has no debounce or loading indicator

**Problem:** The "Refresh" button in the Results section has no debounce, no disabled state during refresh, and no indication that a refresh is in progress. Rapid clicking could trigger multiple concurrent API calls.

**Fix:** Disable the button on click, show a spinner, and re-enable after the refresh completes.

---

## 10. Quick-Win Priority Matrix

| # | Issue | Impact | Effort | Priority |
|---|-------|--------|--------|----------|
| 1 | Add `aria-label` and `for` to all inputs | High (accessibility) | Low | 🔴 Critical |
| 2 | Source card selected state (border + checkmark) | High (usability) | Low | 🔴 Critical |
| 3 | Empty state for Results table | High (first impressions) | Low | 🔴 Critical |
| 4 | Disabled "Next →" explanation text | High (usability) | Low | 🔴 Critical |
| 5 | Sidebar active state via IntersectionObserver | Medium (navigation) | Low | 🟠 High |
| 6 | "Run backtest" loading/spinner state | High (feedback) | Low | 🟠 High |
| 7 | Decision Log: dropdown instead of manual ID input | High (usability) | Medium | 🟠 High |
| 8 | Fix stepper inactive label contrast (#999 → #767676) | Medium (a11y) | Low | 🟠 High |
| 9 | Add `aria-live` to status message containers | Medium (a11y) | Low | 🟠 High |
| 10 | localStorage state persistence | Medium (retention) | Medium | 🟡 Medium |
| 11 | Advanced settings badge (N modified) | Medium (discoverability) | Medium | 🟡 Medium |
| 12 | Conditionally show Full Context filters | Medium (clarity) | Medium | 🟡 Medium |
| 13 | Section visual separators | Medium (layout) | Low | 🟡 Medium |
| 14 | Export buttons toast feedback | Low (feedback) | Low | 🟡 Medium |
| 15 | Dynamic page `<title>` per step | Low (multi-tab UX) | Low | 🟢 Low |
| 16 | Style file inputs with `<label>` pattern | Low (consistency) | Medium | 🟢 Low |
| 17 | Dark mode support | Low (preference) | High | 🟢 Low |

---

*End of UX Audit — Twiq Backtester*
