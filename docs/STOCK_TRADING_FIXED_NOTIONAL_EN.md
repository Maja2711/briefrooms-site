# Stock Trading v2 — fixed 5K position notional

## Status

This policy is a mandatory contract for new Stock Trading v2 positions.

- GPW: **PLN 5,000 per company**.
- US: **USD 5,000 per company**.
- The rule applies to every new position opened by production Stock Trading v2.
- It is independent of the price of one share.

## Objective

Position notional must be comparable across companies. A high or low single-share price must not cause one trade to represent only a few currency units while another represents tens of thousands.

Sizing is therefore defined as fixed gross notional:

```text
GPW target_position_notional = 5000 PLN
US  target_position_notional = 5000 USD
```

With all three slots filled, maximum nominal gross exposure is therefore PLN 15,000 on GPW and USD 15,000 in the US market.

## Quantity

BriefRooms currently operates as a paper-trading / research system. To preserve exactly comparable notional even for very expensive shares, quantity is calculated as:

```text
quantity = 5000 / entry_price
```

and may be fractional.

Example:

```text
US entry = 421.82 USD
quantity ~= 11.8534
entry_notional ~= 5,000 USD
```

If a future broker or execution venue does not support fractional shares, the execution layer must explicitly define rounding or rejection. It must not silently change this architectural rule.

## Position fields

Every new policy-compliant position records at least:

- `sizing_policy_version = FIXED_NOTIONAL_V1`,
- `position_currency`,
- `target_position_notional = 5000`,
- `quantity`,
- `entry_notional`,
- `initial_risk_cash`.

A closed position additionally records:

- `exit_notional`,
- `pnl_amount`.

Cash P&L is calculated as:

```text
pnl_amount = (exit_price - entry_price) * quantity
```

not as the price move of one share.

## Authority

The source of truth is:

- `data/investments/stock_trading_policy.json`.

Production fails closed unless it confirms:

- GPW = PLN 5,000,
- US = USD 5,000,
- `fractional_quantity_allowed = true`,
- `sizing_policy_version = FIXED_NOTIONAL_V1`.

Main implementation:

- `scripts/stock_trading_portfolio.py`,
- `.github/workflows/stock-trading-v2-production.yml`.

## Relationship to risk policy

Fixed notional does **not replace** SL/TP or the risk policy.

Two positions may have the same 5K nominal exposure but different percentage risk to stop. `maximum_risk_percent`, reward/risk and all existing gates remain active.

## History / NO RETROACTIVE

Stock Trading history is compared on the same 5K notional basis, including trades closed before `FIXED_NOTIONAL_V1` became the live sizing rule.

This is an **analytical normalization**, not a rewrite of historical execution.

For every closed trade:

```text
history_normalized_quantity = 5000 / entry_price
history_normalized_pnl_amount =
    (exit_price - entry_price) * history_normalized_quantity
```

GPW uses PLN 5,000 and US uses USD 5,000. Fractional quantity is allowed, so an expensive share above 5,000 PLN/USD receives a fractional analytical quantity.

Derived history fields are explicit:

- `history_normalization_version = FIXED_NOTIONAL_HISTORY_V1`,
- `history_normalization_basis = ANALYTICAL_FIXED_5000_NOTIONAL`,
- `history_target_position_notional`,
- `history_normalized_quantity`,
- `history_normalized_entry_notional`,
- `history_normalized_exit_notional`,
- `history_normalized_pnl_amount`.

NO RETROACTIVE continues to protect prices, timestamps and actual decision/execution history. Normalization must not change `entry`, `exit_price`, `opened_at`, `closed_at` or claim that the derived quantity was the historical broker fill.

## Public UI PL/EN

Both Stock Trading language versions display:

- position value,
- quantity / shares,
- P&L calculated from the full exposure.

New positions show approximately PLN 5,000 or USD 5,000 entry notional. Legacy positions without historically frozen quantity are explicitly labelled as legacy instead of receiving an invented notional.
