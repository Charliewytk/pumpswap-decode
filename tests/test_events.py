from helpers import ZERO_KEY, encode_fields

from pumpswap_decode.constants import EMIT_CPI_TAG, PUMPFUN_PROGRAM, PUMPSWAP_PROGRAM, event_discriminator
from pumpswap_decode.events import PUMPFUN_TRADE_EVENT, PUMPSWAP_BUY_EVENT, PUMPSWAP_SELL_EVENT, decode_event

POOL = "11111111111111111111111111111112"


def _buy(**kw):
    vals = {"timestamp": 1, "base_amount_out": 10, "pool": POOL, "user": ZERO_KEY, "ix_name": "buy",
            "virtual_quote_reserves": -123_456_789, "creator_fee_unclaimed": 7}
    vals.update(kw)
    return event_discriminator("BuyEvent") + encode_fields(PUMPSWAP_BUY_EVENT, vals)


def test_buy_event_roundtrip_signed_virtual_reserves():
    ev = decode_event(PUMPSWAP_PROGRAM, EMIT_CPI_TAG + _buy())
    assert ev.name == "BuyEvent" and not ev.prefix and ev.trailing_bytes == 0
    assert ev["virtual_quote_reserves"] == -123_456_789
    assert ev["pool"] == POOL and ev["ix_name"] == "buy" and ev["creator_fee_unclaimed"] == 7


def test_i128_read_as_unsigned_would_be_absurd():
    raw = _buy()
    ev = decode_event(PUMPSWAP_PROGRAM, raw)
    idx = [n for n, _ in PUMPSWAP_BUY_EVENT].index("virtual_quote_reserves")
    off = len(encode_fields(PUMPSWAP_BUY_EVENT[:idx], {"ix_name": "buy", "pool": POOL, "user": ZERO_KEY}))
    unsigned = int.from_bytes(raw[8 + off:8 + off + 16], "little", signed=False)
    assert unsigned > 2**127 and ev["virtual_quote_reserves"] == -123_456_789


def test_older_shorter_layout_decodes_as_prefix():
    raw = _buy()
    short = raw[:-(8 + 8 + 8 + 8)]          # drop the four newest trailing u64 fields
    ev = decode_event(PUMPSWAP_PROGRAM, short)
    assert ev.prefix and ev["virtual_quote_reserves"] == -123_456_789
    assert ev.fields["creator_fee_unclaimed"] is None and ev.fields["base_supply"] is None


def test_newer_longer_layout_reports_trailing_bytes():
    ev = decode_event(PUMPSWAP_PROGRAM, _buy() + b"\x01" * 8)
    assert ev.trailing_bytes == 8 and not ev.prefix


def test_unknown_discriminator_and_program():
    assert decode_event(PUMPSWAP_PROGRAM, b"\x00" * 40) is None
    assert decode_event("Unknown11111111111111111111111111111111111", _buy()) is None


def test_sell_and_trade_events():
    sell = event_discriminator("SellEvent") + encode_fields(PUMPSWAP_SELL_EVENT, {"base_amount_in": 5, "pool": POOL})
    assert decode_event(PUMPSWAP_PROGRAM, sell)["base_amount_in"] == 5
    trade = event_discriminator("TradeEvent") + encode_fields(PUMPFUN_TRADE_EVENT, {
        "is_buy": True, "sol_amount": 9, "ix_name": "buy_exact_sol_in",
        "shareholders": [{"address": POOL, "share_bps": 10_000}]})
    ev = decode_event(PUMPFUN_PROGRAM, trade)
    assert ev["is_buy"] and ev["sol_amount"] == 9 and ev["shareholders"][0]["share_bps"] == 10_000


def test_name_only_events():
    ev = decode_event(PUMPSWAP_PROGRAM, event_discriminator("WithdrawEvent") + b"\x00" * 16)
    assert ev.name == "WithdrawEvent" and ev.fields == {}


def test_sweep_and_fee_program_events():
    from pumpswap_decode.constants import PUMP_FEES_PROGRAM
    from pumpswap_decode.events import (FEES_SWEEP_BUYBACK_EVENT, FEES_UPSERT_FEE_TIERS_EVENT,
                                        PUMPFUN_SWEEP_BONDING_CURVE_FEE_EVENT, PUMPSWAP_SWEEP_POOL_FEE_EVENT)
    cases = [
        (PUMPSWAP_PROGRAM, "SweepPoolFeeEvent", PUMPSWAP_SWEEP_POOL_FEE_EVENT, {"amount": 5, "bucket": 0}),
        (PUMPFUN_PROGRAM, "SweepBondingCurveFeeEvent", PUMPFUN_SWEEP_BONDING_CURVE_FEE_EVENT, {"amount": 6, "bucket": 1}),
        (PUMP_FEES_PROGRAM, "SweepBuybackEvent", FEES_SWEEP_BUYBACK_EVENT, {"index": 3, "sol_amount": 7}),
        (PUMP_FEES_PROGRAM, "UpsertFeeTiersEvent", FEES_UPSERT_FEE_TIERS_EVENT, {"offset": 2, "fee_tiers": [
            {"market_cap_lamports_threshold": 1, "lp_fee_bps": 2, "protocol_fee_bps": 3, "creator_fee_bps": 4}]}),
    ]
    for program, name, layout, vals in cases:
        ev = decode_event(program, event_discriminator(name) + encode_fields(layout, vals))
        assert ev.name == name and not ev.prefix and ev.trailing_bytes == 0
        for k, v in vals.items():
            assert ev[k] == v


def test_curve_sweep_is_a_payout_in_tx_decoding():
    from pumpswap_decode.events import PUMPFUN_SWEEP_BONDING_CURVE_FEE_EVENT
    from pumpswap_decode.tx import decode_trimmed
    import base64
    data = event_discriminator("SweepBondingCurveFeeEvent") + encode_fields(
        PUMPFUN_SWEEP_BONDING_CURVE_FEE_EVENT, {"amount": 99, "bucket": 0, "mint": POOL})
    rec = {"steps": [{"kind": "event", "program": PUMPFUN_PROGRAM, "data": base64.b64encode(data).decode()}]}
    sweep = decode_trimmed(rec)["fee_sweeps"][0]
    assert sweep["venue"] == "pumpfun" and sweep["bucket"] == "protocol" and sweep["amount"] == 99
