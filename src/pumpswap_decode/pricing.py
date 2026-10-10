"""Exact integer swap math for PumpSwap pools and pump.fun bonding curves.

PumpSwap prices on EFFECTIVE quote reserves:

    effective_quote = pool_quote_vault_amount + virtual_quote_reserves

`virtual_quote_reserves` (V) is an i128. Boost pools start with V > 0 (virtual SOL added at migration). Since
30 Sep 2026 V can also be NEGATIVE, which puts the effective reserves below the vault balance. Reading V as unsigned,
or clamping it at 0, misprices every swap on those pools. The program guarantees vault + V is never negative.

All functions reproduce the program's integer rounding; the golden tests check them against mainnet events.
"""
from __future__ import annotations

BPS = 10_000


def ceil_div(a: int, b: int) -> int:
    if b <= 0:
        raise ZeroDivisionError("ceil_div by non-positive divisor")
    return -(-a // b)


def fee_amount(amount: int, bps: int) -> int:
    """Each fee component is ceil(amount * bps / 10_000)."""
    return ceil_div(amount * bps, BPS)


def effective_quote(vault: int, virtual: int) -> int:
    y = int(vault) + int(virtual)
    if y < 0:
        raise ValueError(f"effective quote reserves negative (vault={vault}, V={virtual})")
    return y


# --- PumpSwap -------------------------------------------------------------------------------------------------------
def amm_buy_exact_base_out(base_reserves: int, quote_vault: int, virtual: int, base_out: int) -> int:
    """Constant-product quote needed for `base_out` (before fees): ceil(y * b / (B - b))."""
    y = effective_quote(quote_vault, virtual)
    if not 0 < base_out < base_reserves:
        raise ValueError("base_out must be between 0 and the base reserves")
    return ceil_div(y * base_out, base_reserves - base_out)


def amm_buy_exact_quote_in(base_reserves: int, quote_vault: int, virtual: int, cp_quote: int) -> int:
    """Base out for a constant-product quote input. The program swaps `cp_quote - 1` (it keeps 1 unit back)."""
    y = effective_quote(quote_vault, virtual)
    inp = cp_quote - 1
    if inp <= 0:
        return 0
    return base_reserves * inp // (y + inp)


def amm_net_quote_for_budget(budget: int, total_fee_bps: int) -> int:
    """Constant-product quote an exact-quote-in budget buys after fees: floor(budget * 1e4 / (1e4 + bps))."""
    return budget * BPS // (BPS + total_fee_bps)


def amm_sell_gross(base_reserves: int, quote_vault: int, virtual: int, base_in: int) -> int:
    """Gross quote out for selling `base_in` (fees are then taken from this): floor(y * b / (B + b))."""
    y = effective_quote(quote_vault, virtual)
    return y * base_in // (base_reserves + base_in)


# --- pump.fun bonding curve -----------------------------------------------------------------------------------------
# TradeEvent reports reserves AFTER the trade; pre-trade reserves are recovered from the amounts.
def curve_pre_reserves(virtual_sol_after: int, virtual_token_after: int, sol_amount: int, token_amount: int,
                       is_buy: bool) -> tuple[int, int]:
    if is_buy:
        return virtual_sol_after - sol_amount, virtual_token_after + token_amount
    return virtual_sol_after + sol_amount, virtual_token_after - token_amount


def curve_buy_exact_tokens_cost(v_sol: int, v_token: int, token_out: int) -> int:
    """SOL into the curve for exactly `token_out` (ix `buy`): ceil(v_sol * t / (v_token - t))."""
    return ceil_div(v_sol * token_out, v_token - token_out)


def curve_buy_tokens_for_sol(v_sol: int, v_token: int, sol_in: int) -> int:
    """Tokens out for an exact SOL amount into the curve (ix `buy_exact_sol_in` / `buy_exact_quote_in*`). As on the
    AMM, the program prices `sol_in - 1`."""
    inp = sol_in - 1
    if inp <= 0:
        return 0
    return v_token * inp // (v_sol + inp)


def curve_sell_sol_out(v_sol: int, v_token: int, token_in: int) -> int:
    """Gross SOL out of the curve for `token_in`: floor(v_sol * t / (v_token + t))."""
    return v_sol * token_in // (v_token + token_in)
