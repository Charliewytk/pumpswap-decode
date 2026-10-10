"""The buy vs buy_exact_quote_in field-meaning trap, on synthetic events and on mainnet fixtures."""
import base64

import pytest
from helpers import encode_fields

from pumpswap_decode.constants import PUMPSWAP_PROGRAM, event_discriminator
from pumpswap_decode.events import PUMPSWAP_BUY_EVENT, decode_event
from pumpswap_decode.semantics import (BASE_OUT_MODE, QUOTE_IN_MODE, UnknownBuyVariant, identity_residuals,
                                       normalize_buy)

CP, LP, PROTO, CREATOR = 1_000_000, 2_000, 500, 800
FEES = LP + PROTO + CREATOR


def buy_event(ix_name, quote_amount_in, user_quote_amount_in):
    vals = {"ix_name": ix_name, "quote_amount_in": quote_amount_in, "user_quote_amount_in": user_quote_amount_in,
            "quote_amount_in_with_lp_fee": CP + LP, "lp_fee": LP, "protocol_fee": PROTO, "coin_creator_fee": CREATOR}
    return decode_event(PUMPSWAP_PROGRAM, event_discriminator("BuyEvent") + encode_fields(PUMPSWAP_BUY_EVENT, vals))


@pytest.mark.parametrize("ix", ["buy", "buy_v2"])
def test_exact_base_out_buys(ix):
    legs = normalize_buy(buy_event(ix, CP, CP + FEES))
    assert (legs.mode, legs.cp, legs.user, legs.vault) == (BASE_OUT_MODE, CP, CP + FEES, CP + LP)


@pytest.mark.parametrize("ix", ["buy_exact_quote_in", "buy_exact_quote_in_v2"])
def test_exact_quote_in_buys_swap_the_fields(ix):
    legs = normalize_buy(buy_event(ix, CP + FEES, CP))
    assert (legs.mode, legs.cp, legs.user, legs.vault) == (QUOTE_IN_MODE, CP, CP + FEES, CP + LP)


def test_unknown_variant_is_refused_in_strict_mode_and_inferred_otherwise():
    ev = buy_event("buy_v9", CP + FEES, CP)
    with pytest.raises(UnknownBuyVariant):
        normalize_buy(ev)
    legs = normalize_buy(ev, strict=False)
    assert legs.inferred and legs.mode == QUOTE_IN_MODE and legs.cp == CP


def test_multi_hop_legs_are_always_inferred():
    legs = normalize_buy(buy_event("multi_hop_swap", CP, CP + FEES))
    assert legs.inferred and legs.mode == BASE_OUT_MODE


def _amm_buy_events(golden):
    for fx in golden.values():
        for step in fx["record"]["steps"]:
            if step["kind"] == "event" and step["program"] == PUMPSWAP_PROGRAM:
                ev = decode_event(PUMPSWAP_PROGRAM, base64.b64decode(step["data"]))
                if ev is not None and ev.name == "BuyEvent":
                    yield fx["name"], ev


def test_mainnet_identities_hold_for_every_buy(golden):
    seen = set()
    for name, ev in _amm_buy_events(golden):
        legs = normalize_buy(ev)
        assert identity_residuals(ev, legs) == {"user_minus_cp_minus_fees": 0, "vault_field_minus_cp_minus_lp": 0}, name
        seen.add(ev["ix_name"])
    assert {"buy", "buy_exact_quote_in", "buy_v2"} <= seen


def test_naive_reading_is_wrong_on_mainnet(golden):
    """Reading `quote_amount_in` as "what the user paid" drops every fee on exact-base-out buys."""
    wrong = 0
    for name, ev in _amm_buy_events(golden):
        legs = normalize_buy(ev)
        fees = ev["lp_fee"] + ev["protocol_fee"] + ev["coin_creator_fee"]
        if legs.mode == BASE_OUT_MODE and fees:
            assert ev["quote_amount_in"] == legs.user - fees
            wrong += 1
        if legs.mode == QUOTE_IN_MODE:
            assert ev["quote_amount_in"] == legs.user
    assert wrong >= 2
