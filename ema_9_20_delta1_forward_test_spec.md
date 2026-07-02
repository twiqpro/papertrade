# EMA 9/20 Delta-1 Forward Test Spec

This document defines the live-forward-test version of the current EMA 9/20 option scalper.
It is meant to be handed to Cursor for implementation in the existing Twiq strategy engine.

## Goal

Keep the same trend-filter logic, but change execution from an ATM resting-limit scalp to a delta-1 entry model.
The strategy should enter immediately when the signal is valid, use the delta-1 option contract, and manage the trade with a fixed 3-point profit target and 10-point stop loss.

This is for forward testing only.
Do not change the system into a real-order deployment flow.

## Keep These Parts The Same

- Use NIFTY spot 1-minute bars.
- Use EMA 9 and EMA 20.
- Use the EMA gap as the directional filter.
- Keep the session window and end-of-day square-off behavior.
- Keep the cooldown between trades.
- Keep the same compounded equity sizing logic.
- Keep the same max quantity protection.
- Keep the same single-position-at-a-time behavior.

## Change The Entry Logic

### Current behavior

The current variant waits for a resting buy limit on the fixed ATM strike and only enters if price pulls back to that limit.

### New behavior

When the EMA condition is valid:

- do not place a resting limit order
- do not wait for a pullback
- enter immediately on the signal bar
- use the delta-1 option contract instead of the ATM contract

The entry should be a market-like forward-test entry, or the closest supported immediate execution method in the engine.

## Delta-1 Contract Selection

Instead of selecting the ATM strike, the strategy should select the option contract that is closest to delta 1 for the current NIFTY spot and expiry.

Implementation expectation:

- determine the active expiry the same way the live strategy already does
- inspect available option contracts for the chosen side, CE or PE
- select the strike whose delta is nearest to 1.0
- if exact delta-1 is not available, choose the nearest available strike with the highest delta that still matches the side

If the data feed does not provide live delta directly, Cursor should derive it from the available option-chain fields or use the best available proxy already present in the engine.

Important:

- do not use ATM strike selection for this variant
- do not wait for a price retracement to a limit level
- keep the selected contract fixed for the life of the trade

## Signal Rules

The signal condition should remain based on the EMA relationship.

Use the same direction logic:

- if EMA fast is above EMA slow by enough margin, take CE
- if EMA fast is below EMA slow by enough margin, take PE

The user asked for the EMA difference threshold to be checked before entry.
For implementation, preserve the current gap filter concept and set the trigger threshold to the requested level if the engine uses a points-based gap parameter.

## Exit Rules

Replace the existing target and stop with:

- profit target: 3 points
- stop loss: 10 points

Exit handling should remain bar-based in the forward-test engine unless the engine already supports intrabar or tick-level fills.

If the selected contract is entered at price `entry_price`:

- target = `entry_price + 3`
- stop = `entry_price - 10`

For PE trades, the engine should keep the same direction-aware logic already used by the strategy framework.

## Order Handling

The new strategy should not arm a pending order.

It should:

- evaluate the signal
- select the delta-1 contract
- enter immediately
- then manage only the open trade

Remove or bypass all logic related to:

- resting limit orders
- pending order TTL
- pullback fill checks
- limit cancellation due to stale quotes

## Capital And Sizing

Keep the existing equity-compounding and quantity sizing approach.

That means:

- start from the configured trading capital
- size quantity from current equity and entry premium
- preserve max quantity protection
- update equity after each exit using realized P&L

## Session Rules

Keep the same session hygiene:

- no trading before market open
- no new entries after the cutoff time
- square off before market close

If a position is open near square-off, exit it using the same end-of-day handling already used by the engine.

## Suggested Implementation Changes In Code

Cursor should likely update the existing strategy file by:

1. Removing the `_pending_limit`, `_pending_side`, `_pending_strike`, and `_pending_age` entry workflow.
2. Replacing ATM strike selection with a delta-1 strike selector.
3. Replacing the limit entry path with an immediate `enter` path.
4. Updating target and stop values to 3 and 10.
5. Keeping the existing equity sizing and exit accounting logic.
6. Keeping the cooldown, cutoff, and square-off guards intact.

## Validation Checklist

After implementation, verify that:

- the strategy no longer logs or uses resting limit orders
- the selected strike is the delta-1 contract, not ATM
- a valid EMA signal creates an immediate entry
- target is 3 points
- stop loss is 10 points
- cooldown and cutoff behavior still work
- equity compounding still updates after exits

## Notes

This variant will behave very differently from the ATM limit version:

- the fill rate should be higher because there is no pullback condition
- the trade will generally be more expensive because delta-1 contracts are deeper ITM
- P&L will be more linear and closer to underlying movement
- quantity will likely drop because premium is larger

The purpose of this file is to define the new live-forward-test behavior clearly before code changes are made.
