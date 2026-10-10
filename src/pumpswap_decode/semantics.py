"""What the PumpSwap BuyEvent quote fields actually mean, per instruction.

The trap
--------
BuyEvent has three quote amounts: `quote_amount_in`, `user_quote_amount_in` and `quote_amount_in_with_lp_fee`. Their
meaning depends on which instruction produced the event (the event's `ix_name`):

===========================  ============================  ============================
field                        ix `buy` / `buy_v2`           ix `buy_exact_quote_in`
===========================  ============================  ============================
quote_amount_in              constant-product quote (CP)   total the user paid
user_quote_amount_in         total the user paid           constant-product quote (CP)
quote_amount_in_with_lp_fee  CP + lp_fee                   CP + lp_fee
===========================  ============================  ============================

In both cases: user total = CP + lp_fee + protocol_fee + coin_creator_fee, and the pool's quote vault receives
CP + lp_fee. Code that reads `quote_amount_in` as "what the user paid" is right for exact-quote-in buys and wrong
for exact-base-out buys (it misses all the fees), and the error is silent because both numbers look plausible.

Sell events have no such switch: quote_amount_out is the gross CP output, user_quote_amount_out = gross - all fees,
quote_amount_out_without_lp_fee = gross - lp_fee (what leaves the vault).

The v2 instructions (`buy_v2`, `buy_exact_quote_in_v2`) use the same field meanings as their v1 versions.

`normalize_buy` maps fields by `ix_name` and refuses unknown names unless `strict=False`, in which case the mode is
inferred from the fee identity (only possible when fees are non-zero). `multi_hop_swap` legs are always inferred.
"""
from __future__ import annotations

from dataclasses import dataclass

from .events import Event

BASE_OUT_MODE = "exact_base_out"     # user asked for an exact base amount, quote is the result
QUOTE_IN_MODE = "exact_quote_in"     # user spent an exact quote amount, base is the result

# Field mappings checked against mainnet events (fee identities hold exactly; see tests/fixtures/golden).
BUY_MODES = {
    "buy": BASE_OUT_MODE,
    "buy_v2": BASE_OUT_MODE,
    "buy_exact_quote_in": QUOTE_IN_MODE,
    "buy_exact_quote_in_v2": QUOTE_IN_MODE,
}
# Buy legs of `multi_hop_swap` (ix_name "multi_hop_swap") only charge the fees of that hop, and the route is exact-in.
# Their mapping is inferred from the fee identity, never assumed.
INFERRED_BUY_NAMES = {"multi_hop_swap"}


class UnknownBuyVariant(ValueError):
    """A BuyEvent whose ix_name has no verified field mapping."""


@dataclass(frozen=True)
class QuoteLegs:
    """Normalized quote amounts of one swap, in quote-mint base units (lamports for SOL pools)."""
    mode: str
    cp: int            # constant-product amount (buy: quote into the curve before fees; sell: gross out)
    user: int          # buy: total the user paid; sell: what the user received
    vault: int         # signed change of the pool's quote vault from the swap (+ in, - out)
    fees: int          # lp + protocol + coin creator
    inferred: bool = False


def _fees(ev: Event) -> int:
    return ev.get("lp_fee", 0) + ev.get("protocol_fee", 0) + ev.get("coin_creator_fee", 0)


def infer_buy_mode(ev: Event) -> str | None:
    """Mode implied by the fee identity, or None when it is ambiguous (zero fees) or neither identity holds."""
    fees = _fees(ev)
    q, u = ev["quote_amount_in"], ev["user_quote_amount_in"]
    if fees == 0:
        return None
    if u == q + fees:
        return BASE_OUT_MODE
    if q == u + fees:
        return QUOTE_IN_MODE
    return None


def normalize_buy(ev: Event, strict: bool = True) -> QuoteLegs:
    if ev.name != "BuyEvent":
        raise ValueError(f"not a BuyEvent: {ev.name}")
    name = ev.get("ix_name")
    mode = BUY_MODES.get(name)
    inferred = False
    if mode is None:
        if strict and name not in INFERRED_BUY_NAMES:
            raise UnknownBuyVariant(f"no verified field mapping for BuyEvent ix_name={name!r}")
        mode = infer_buy_mode(ev)
        inferred = True
        if mode is None:
            if _fees(ev) == 0 and ev["quote_amount_in"] == ev["user_quote_amount_in"]:
                mode = QUOTE_IN_MODE  # zero fees: both readings give the same numbers
            else:
                raise UnknownBuyVariant(f"cannot infer field mapping for ix_name={name!r}")
    if mode == BASE_OUT_MODE:
        cp, user = ev["quote_amount_in"], ev["user_quote_amount_in"]
    else:
        cp, user = ev["user_quote_amount_in"], ev["quote_amount_in"]
    return QuoteLegs(mode, cp, user, cp + ev["lp_fee"], _fees(ev), inferred)


def normalize_sell(ev: Event) -> QuoteLegs:
    if ev.name != "SellEvent":
        raise ValueError(f"not a SellEvent: {ev.name}")
    gross = ev["quote_amount_out"]
    return QuoteLegs("exact_base_in", gross, ev["user_quote_amount_out"], -ev["quote_amount_out_without_lp_fee"],
                     _fees(ev))


def identity_residuals(ev: Event, legs: QuoteLegs) -> dict[str, int]:
    """Residuals of the accounting identities (all zero for a correctly mapped event)."""
    if ev.name == "BuyEvent":
        return {"user_minus_cp_minus_fees": legs.user - legs.cp - legs.fees,
                "vault_field_minus_cp_minus_lp": ev["quote_amount_in_with_lp_fee"] - legs.cp - ev["lp_fee"]}
    return {"user_minus_gross_plus_fees": legs.user - (legs.cp - legs.fees),
            "vault_field_minus_gross_plus_lp": ev["quote_amount_out_without_lp_fee"] - (legs.cp - ev["lp_fee"])}
