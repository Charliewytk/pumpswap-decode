import pytest

from pumpswap_decode import pricing as P


def test_effective_quote_signed():
    assert P.effective_quote(1_000, -400) == 600
    assert P.effective_quote(1_000, 17_000) == 18_000
    with pytest.raises(ValueError):
        P.effective_quote(1_000, -1_001)


def test_negative_v_changes_the_price_and_clamping_is_wrong():
    B, vault, v = 10**15, 50 * 10**9, -3 * 10**9
    signed = P.amm_sell_gross(B, vault, v, 10**12)
    clamped = P.amm_sell_gross(B, vault, 0, 10**12)
    assert signed < clamped
    assert P.amm_buy_exact_base_out(B, vault, v, 10**12) < P.amm_buy_exact_base_out(B, vault, 0, 10**12)


def test_rounding_directions():
    assert P.ceil_div(7, 2) == 4 and P.ceil_div(8, 2) == 4
    assert P.fee_amount(10_001, 25) == 26              # ceil
    assert P.amm_buy_exact_quote_in(1000, 1000, 0, 1) == 0
    assert P.amm_net_quote_for_budget(10_030, 30) == 10_000


def test_curve_round_trip_reserves():
    vs, vt = P.curve_pre_reserves(31_000, 900_000, 1_000, 30_000, is_buy=True)
    assert (vs, vt) == (30_000, 930_000)
    vs, vt = P.curve_pre_reserves(30_000, 930_000, 1_000, 30_000, is_buy=False)
    assert (vs, vt) == (31_000, 900_000)
