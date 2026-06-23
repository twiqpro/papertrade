# Cursor Implementation Brief: Stocks Tab - Market Movers v1

## Summary

Build the **Stocks** tab as a lightweight **Market Movers** page.

This tab should not try to become a deep stock intelligence product yet. It should give users a quick read on which liquid Indian stocks are active today, especially within the NIFTY and F&O stock universe.

The goal is simple:

> Show what is moving today, what is weak today, and where volume is unusual.

Keep all derivatives intelligence, option chain logic, OI, PCR, max pain, GEX, gamma, and F&O interpretation inside the existing **F&O** tab. The **Stocks** tab should remain cash-market movement only.

## Product Positioning

Use this positioning in UI copy:

**Stocks**

**NIFTY and F&O stock movers. Quick read on what is active today.**

Do not call this page "Stock Intelligence" yet. Use the word **Movers**. This keeps expectations honest and avoids overpromising.

## Core Rules

- No charts.
- No option chain.
- No OI.
- No PCR.
- No max pain.
- No gamma/GEX.
- No buy/sell language.
- No deep fundamental analysis.
- No smart money/ownership analysis in v1.
- No complicated scoring engine.
- Keep this page fast, scannable, and simple.

Allowed language:

- `Strong move`
- `Weak move`
- `High volume`
- `Sector leader`
- `Sector drag`
- `Active`
- `Muted`
- `Watch`

Avoid language:

- `Buy`
- `Sell`
- `Entry`
- `Exit`
- `Target`
- `Stop loss`
- `Trade now`
- `Guaranteed`

## Page Structure

The page should have five main sections:

1. **NIFTY Gainers**
2. **NIFTY Losers**
3. **F&O Gainers**
4. **F&O Losers**
5. **Volume Shockers**

Optional section if data is already available:

6. **Sector Movers**

Do not delay v1 for Sector Movers if the current data model does not already support sector-level grouping.

## Top Summary Strip

Add a compact summary strip at the top of the page.

Fields:

- `Market mood`
- `Strongest mover`
- `Weakest mover`
- `Most active`
- `Last updated`

Example:

```text
Market mood: Mixed
Strongest mover: BEL +3.2%
Weakest mover: Tata Motors -2.1%
Most active: HDFC Bank
Updated 1 min ago
```

### Market Mood Logic

Use a simple first-pass rule:

- `Positive`: more gainers than losers by at least 20%
- `Negative`: more losers than gainers by at least 20%
- `Mixed`: otherwise

If market breadth data is unavailable, use only the visible NIFTY mover list.

## Section Details

### 1. NIFTY Gainers

Show top gaining NIFTY stocks for the current session.

Sort:

- Highest percentage change first.

Rows:

- Stock symbol
- Company name, if available
- Last traded price
- Percentage change
- Absolute point change
- Volume, if available
- Sector, if available
- Label

Labels:

- `Strong move` if gain is high and volume is above average
- `Active` if gain is meaningful but volume confirmation is unavailable
- `Sector leader` if it is also among top stocks in its sector

### 2. NIFTY Losers

Show top falling NIFTY stocks for the current session.

Sort:

- Lowest percentage change first.

Rows:

- Stock symbol
- Company name, if available
- Last traded price
- Percentage change
- Absolute point change
- Volume, if available
- Sector, if available
- Label

Labels:

- `Weak move` if fall is large and volume is above average
- `Active` if fall is meaningful but volume confirmation is unavailable
- `Sector drag` if it is among the weakest stocks in its sector

### 3. F&O Gainers

Show top gaining stocks from the F&O stock universe.

Important:

- This is only a universe filter.
- Do not show F&O analytics.
- Do not show OI, PCR, IV, option volume, or max pain.

Sort:

- Highest percentage change first.

Rows:

- Stock symbol
- Company name, if available
- Last traded price
- Percentage change
- Absolute point change
- Volume, if available
- Sector, if available
- Label

### 4. F&O Losers

Show top falling stocks from the F&O stock universe.

Important:

- This is only a universe filter.
- Do not show F&O analytics.

Sort:

- Lowest percentage change first.

Rows:

- Stock symbol
- Company name, if available
- Last traded price
- Percentage change
- Absolute point change
- Volume, if available
- Sector, if available
- Label

### 5. Volume Shockers

Show stocks where volume is unusually high versus recent average.

Preferred logic:

```text
volumeRatio = currentVolume / averageVolume20d
```

Sort:

- Highest `volumeRatio` first.

Include stocks where:

```text
volumeRatio >= 2.0
```

Rows:

- Stock symbol
- Company name, if available
- Last traded price
- Percentage change
- Current volume
- Volume ratio
- Sector, if available
- Label

Labels:

- `High volume`
- `Strong high-volume move` if price change is positive and volume ratio is high
- `Weak high-volume move` if price change is negative and volume ratio is high

If 20-day average volume is not available yet, use the best available average volume field. If no average exists, hide this section and show a small empty state:

```text
Volume shockers need average volume data.
```

### 6. Sector Movers Optional

Only add this if sector mapping and sector returns are already available.

Show:

- Strongest sector
- Weakest sector
- Top stock inside strongest sector
- Weakest stock inside weakest sector

Do not build a complex sector analytics engine for v1.

## Row Component

Create or reuse a compact stock row component.

Recommended fields:

```ts
type StockMoverRow = {
  symbol: string;
  name?: string;
  lastPrice: number;
  change: number;
  changePercent: number;
  volume?: number;
  averageVolume20d?: number;
  sector?: string;
  label?: StockMoverLabel;
};

type StockMoverLabel =
  | "Strong move"
  | "Weak move"
  | "High volume"
  | "Strong high-volume move"
  | "Weak high-volume move"
  | "Sector leader"
  | "Sector drag"
  | "Active"
  | "Muted";
```

Visual behavior:

- Positive percentage should use green.
- Negative percentage should use red.
- Neutral/missing values should use muted gray.
- Labels should be small pills, not large buttons.
- Keep rows dense and readable.
- Avoid wrapping stock symbols.
- Company names can truncate with ellipsis.

## Formatting Utilities

Use shared formatters instead of rendering raw numbers.

Required helpers:

```ts
formatPrice(value: number | null | undefined): string
formatChange(value: number | null | undefined): string
formatPercent(value: number | null | undefined): string
formatVolume(value: number | null | undefined): string
formatVolumeRatio(value: number | null | undefined): string
formatUpdatedAt(value: Date | string | number | null | undefined): string
```

Formatting rules:

- Price: max 2 decimals.
- Change: max 2 decimals.
- Percent: max 2 decimals.
- Volume:
  - `1,250` for small values
  - `1.2L` for lakhs
  - `1.4Cr` for crores
- Volume ratio:
  - `2.1x`
  - `3.5x`
- Never show `NaN`, `undefined`, `null`, or floating-point artifacts.

Examples:

```text
23.456789 -> 23.46
2.199999999 -> 2.20
150000 -> 1.5L
32000000 -> 3.2Cr
2.083333 -> 2.1x
```

## Data Handling

Use existing market data sources if already present.

Minimum required data:

- Symbol
- Last traded price
- Percentage change
- Absolute change
- Universe membership:
  - NIFTY stock
  - F&O stock

Optional but recommended:

- Volume
- 20-day average volume
- Sector
- Company name
- Last updated timestamp

If NIFTY universe and F&O universe are not already available, add static config lists for v1.

Suggested files:

```text
src/config/niftyStocks.ts
src/config/fnoStocks.ts
```

Keep static lists easy to update.

## Empty States

Each section should have a graceful empty state.

Examples:

```text
No NIFTY gainers available yet.
No F&O losers available yet.
Volume shockers need average volume data.
Market data delayed. Showing latest available snapshot.
```

Do not show broken cards, empty tables, or raw loading errors.

## Stale Data Handling

If data has not refreshed recently, show a subtle stale indicator.

Suggested rule:

```text
If lastUpdated is older than 5 minutes during market hours, show "Data delayed".
```

Display:

```text
Data delayed - last updated 8 min ago
```

Outside market hours, use:

```text
Market closed - showing last session data
```

Do not make this visually alarming unless data is completely unavailable.

## Visual Design

Match the existing twiQ style:

- Dark premium background.
- Compact panels.
- Pink only as accent, not as constant alarm.
- Green for gainers.
- Red for losers.
- Gray/muted text for secondary fields.
- 8px or smaller border radius unless the existing design system already uses another value.
- No large marketing hero.
- No charts.
- No decorative illustrations.

The page should feel like a fast market desk read, not a landing page.

## Recommended Layout

Desktop:

```text
Top summary strip

Two-column grid:
Left column:
  NIFTY Gainers
  NIFTY Losers

Right column:
  F&O Gainers
  F&O Losers

Full width:
  Volume Shockers
  Sector Movers optional
```

Mobile:

```text
Top summary strip
NIFTY Gainers
NIFTY Losers
F&O Gainers
F&O Losers
Volume Shockers
Sector Movers optional
```

On mobile, rows should remain readable without horizontal scrolling if possible. If table columns become too dense, switch to card-like rows.

## Interaction

Keep v1 interactions minimal:

- Refresh button if the app already supports manual refresh.
- Optional tabs:
  - `NIFTY`
  - `F&O`
  - `Volume`
- Clicking a stock row may open a future stock detail page, but this is optional.

Do not block v1 on:

- Watchlists
- Alerts
- Stock detail pages
- News feeds
- Sector drilldowns
- Smart money analysis

## Acceptance Criteria

- Stocks tab loads without charts.
- Page title is `Stocks`.
- Subtitle says `NIFTY and F&O stock movers. Quick read on what is active today.`
- NIFTY gainers and losers render from the NIFTY universe.
- F&O gainers and losers render from the F&O stock universe.
- F&O sections do not show option-chain or derivatives metrics.
- Volume Shockers render only if volume and average volume data are available.
- Missing data never displays as `NaN`, `undefined`, or `null`.
- Positive moves are green.
- Negative moves are red.
- Page has graceful empty states.
- Stale data indicator appears when data is old.
- UI remains readable on desktop and mobile.

## Test Cases

### Positive mover

Input:

```ts
{
  symbol: "BEL",
  lastPrice: 304.4567,
  change: 9.4321,
  changePercent: 3.20123,
  volume: 12500000,
  averageVolume20d: 5000000
}
```

Expected:

```text
BEL
304.46
+9.43
+3.20%
1.3Cr
2.5x
Strong high-volume move
```

### Negative mover

Input:

```ts
{
  symbol: "TATAMOTORS",
  lastPrice: 912.1,
  change: -18.35,
  changePercent: -1.972,
  volume: 8000000,
  averageVolume20d: 12000000
}
```

Expected:

```text
TATAMOTORS
912.10
-18.35
-1.97%
80.0L
Weak move
```

### Missing average volume

Input:

```ts
{
  symbol: "RELIANCE",
  lastPrice: 2850,
  change: 12,
  changePercent: 0.42,
  volume: 4000000
}
```

Expected:

```text
No volume ratio shown.
No NaN shown.
```

### Empty volume shockers

Input:

```ts
[]
```

Expected:

```text
Volume shockers need average volume data.
```

### Stale data

Input:

```ts
lastUpdated = 8 minutes ago during market hours
```

Expected:

```text
Data delayed - last updated 8 min ago
```

## Implementation Notes

- Keep this implementation separate from the existing F&O tab logic.
- Do not reuse F&O-specific terminology in this tab.
- If existing components are too heavy, create small dedicated components for the Stocks tab.
- Prioritize correctness and readability over visual complexity.
- Ship this as a useful placeholder that does not distract from the main F&O product.

## Future Features Not in v1

Do not implement these now:

- Deep stock detail pages
- Ownership/smart money tracking
- Delivery intelligence
- Promoter pledge/red flag analysis
- News impact explanation
- Watchlist intelligence
- Stock alerts
- Fundamental scoring
- F&O analytics for single stocks
- Charts

These can come later once the F&O product is stronger and the data layer is more mature.
