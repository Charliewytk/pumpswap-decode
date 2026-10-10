"""Golden set: decoded mainnet transactions (public signatures), checked two ways.

1. The decoder reproduces the stored expected output (regression).
2. Independent invariants that do not trust the decoder's own output:
   * PumpSwap amounts recomputed from the event's pre-trade reserves with SIGNED virtual reserves, integer-exact;
   * fee components = ceil(amount * bps / 10_000) and the user / vault identities;
   * the pool's real quote-vault balance change (from the transaction's token balances) equals the decoded
     `quote_vault_delta`, including fees kept in the pool by v2 trades;
   * fee rates recomputed from the mainnet FeeConfig snapshot and the pool's market cap;
   * pump.fun curve amounts recomputed from the event's reserves.
"""
import base64

import pytest
from conftest import load_golden

from pumpswap_decode import pricing as P
from pumpswap_decode.constants import PUMPSWAP_PROGRAM
from pumpswap_decode.events import decode_event
from pumpswap_decode.fees import expected_fees
from pumpswap_decode.tx import decode_trimmed

FIXTURES = load_golden()
IDS = [fx["name"] for fx in FIXTURES]


def swaps_of(fx):
    return decode_trimmed(fx["record"])["swaps"]


def amm(fx):
    return [s for s in swaps_of(fx) if s["venue"] == "pumpswap"]


def curve(fx):
    return [s for s in swaps_of(fx) if s["venue"] == "pumpfun"]


def _subset(expected, actual, path=""):
    if isinstance(expected, dict):
        for k, v in expected.items():
            assert k in actual, f"{path}.{k} missing"
            _subset(v, actual[k], f"{path}.{k}")
    elif isinstance(expected, list):
        assert len(expected) == len(actual), f"{path} length"
        for i, (e, a) in enumerate(zip(expected, actual)):
            _subset(e, a, f"{path}[{i}]")
    else:
        assert expected == actual, f"{path}: {expected!r} != {actual!r}"


@pytest.mark.parametrize("fx", FIXTURES, ids=IDS)
def test_decoder_reproduces_expected(fx):
    _subset(fx["expected"], decode_trimmed(fx["record"]))


@pytest.mark.parametrize("fx", FIXTURES, ids=IDS)
def test_amm_pricing_recomputed_with_signed_virtual_reserves(fx):
    for s in amm(fx):
        B, Q, V = s["pool_base_reserves"], s["pool_quote_reserves"], s["virtual_quote_reserves"]
        if s["side"] == "sell":
            assert P.amm_sell_gross(B, Q, V, s["base_amount"]) == s["quote_cp"]
        elif s["quote_mode"] == "exact_base_out":
            assert P.amm_buy_exact_base_out(B, Q, V, s["base_amount"]) == s["quote_cp"]
        else:
            assert P.amm_buy_exact_quote_in(B, Q, V, s["quote_cp"]) == s["base_amount"]


@pytest.mark.parametrize("fx", FIXTURES, ids=IDS)
def test_amm_fee_identities(fx):
    for s in amm(fx):
        for part in ("lp", "protocol", "coin_creator"):
            assert s[f"{part}_fee"] == P.fee_amount(s["quote_cp"], s[f"{part}_fee_bps"]), part
        fees = s["lp_fee"] + s["protocol_fee"] + s["coin_creator_fee"]
        if s["side"] == "buy":
            assert s["quote_user"] == s["quote_cp"] + fees
        else:
            assert s["quote_user"] == s["quote_cp"] - fees
        assert s["buyback_fee"] <= s["protocol_fee"]                       # a split of the protocol fee
        if s["buyback_fee_bps"]:
            assert s["buyback_fee"] == s["protocol_fee"] * s["buyback_fee_bps"] // 10_000
        if s["fee_settlement"] == "kept_in_pool":
            assert s["fees_kept"] == s["protocol_fee"] - s["buyback_fee"] + s["coin_creator_fee"]
            assert s["virtual_quote_reserves_after"] == s["virtual_quote_reserves"] - s["fees_kept"]
        else:
            assert s["fees_kept"] == 0


@pytest.mark.parametrize("fx", FIXTURES, ids=IDS)
def test_amm_vault_delta_matches_token_balances(fx):
    swaps = amm(fx)
    per_pool = {}
    for s in swaps:
        per_pool.setdefault(s["pool"], []).append(s)
    bal = {(b["owner"], b["mint"]): b["post"] - b["pre"] for b in fx["record"]["pool_balances"]}
    checked = 0
    for pool, ss in per_pool.items():
        if len(ss) != 1 or not ss[0]["quote_mint"]:
            continue
        assert bal[(pool, ss[0]["quote_mint"])] == ss[0]["quote_vault_delta"]
        base = ss[0]["base_amount"] if ss[0]["side"] == "sell" else -ss[0]["base_amount"]
        if (pool, ss[0]["base_mint"]) in bal and not ss[0]["is_boost_buyback"]:
            assert bal[(pool, ss[0]["base_mint"])] == base
        checked += 1
    assert checked or not swaps or len(swaps) > len(per_pool)


@pytest.mark.parametrize("fx", FIXTURES, ids=IDS)
def test_fee_rates_match_fee_config_snapshot(fx, fee_config):
    ctx = fx.get("context", {})
    for s in amm(fx):
        if s["is_boost_buyback"]:
            assert s["total_fee_bps"] == 0
            continue
        if s["outer_ix"] == "multi_hop_swap":
            continue                                    # fees are charged once per route, not per hop
        f = expected_fees(fee_config, quote_mint=s["quote_mint"], is_pump_pool=s["base_supply"] > 0,
                          effective_quote=s["effective_quote_reserves"], base_supply=s["base_supply"],
                          base_reserves=s["pool_base_reserves"],
                          pool_creator_fee_bps=ctx.get("pool_creator_fee_bps", {}).get(s["pool"]))
        assert (s["lp_fee_bps"], s["protocol_fee_bps"], s["coin_creator_fee_bps"]) == f.as_tuple()


@pytest.mark.parametrize("fx", FIXTURES, ids=IDS)
def test_pumpfun_curve_math(fx):
    for s in curve(fx):
        if s["mayhem_mode"] and s["total_fee_bps"] == 0:
            pytest.skip("zero-fee mayhem trades are not modelled")
        vs, vt = P.curve_pre_reserves(s["virtual_quote_reserves_after"], s["virtual_token_reserves_after"],
                                      s["quote_cp"], s["base_amount"], s["side"] == "buy")
        if s["side"] == "sell":
            assert P.curve_sell_sol_out(vs, vt, s["base_amount"]) == s["quote_cp"]
        elif s["ix_name"] == "buy":
            assert P.curve_buy_exact_tokens_cost(vs, vt, s["base_amount"]) == s["quote_cp"]
        elif s["ix_name"] != "multi_hop_swap":
            assert P.curve_buy_tokens_for_sol(vs, vt, s["quote_cp"]) == s["base_amount"]
        assert s["protocol_fee"] == P.fee_amount(s["quote_cp"], s["protocol_fee_bps"])
        assert s["creator_fee"] == P.fee_amount(s["quote_cp"], s["creator_fee_bps"])


@pytest.mark.parametrize("fx", FIXTURES, ids=IDS)
def test_slippage_limits_respected(fx):
    """The instruction arguments bound the decoded amounts (a cross-check on the field mapping)."""
    decoded = decode_trimmed(fx["record"])
    by_name = {}
    for ix in decoded["instructions"]:
        by_name.setdefault(ix["name"], []).append(ix["args"] or {})
    for s in amm(fx):
        args = (by_name.get(s["outer_ix"]) or [{}])[0]
        if s["outer_ix"] in ("buy", "buy_v2"):
            assert args["base_amount_out"] == s["base_amount"] and s["quote_user"] <= args["max_quote_amount_in"]
        elif s["outer_ix"] in ("buy_exact_quote_in", "buy_exact_quote_in_v2"):
            assert s["quote_user"] <= args["spendable_quote_in"] and s["base_amount"] >= args["min_base_amount_out"]
        elif s["outer_ix"] in ("sell", "sell_v2"):
            assert args["base_amount_in"] == s["base_amount"] and s["quote_user"] >= args["min_quote_amount_out"]


def test_boost_buybacks_are_flagged_per_pool(golden):
    boosts = [fx for name, fx in golden.items() if name.startswith("amm_boost_buyback")]
    assert len(boosts) >= 3
    for fx in boosts:
        d = decode_trimmed(fx["record"])
        assert [s["is_boost_buyback"] for s in d["swaps"]] == [True]
        assert d["boost_buybacks"][0]["matched_buy_event"] and d["swaps"][0]["outer_ix"] == "boost_buy_and_burn"
        assert d["boost_buybacks"][0]["pool"] == d["swaps"][0]["pool"]


def test_negative_virtual_reserves_present(golden):
    negs = [s for fx in golden.values() for s in amm(fx) if s["virtual_quote_reserves"] < 0]
    assert len(negs) >= 3


def test_v2_kept_fees_carry_into_the_next_trade(golden):
    """A sell_v2 on a pool with V = 0 keeps its fees; the pool's next trade prices on V = -kept."""
    first = amm(golden["amm_sell_v2_fees_kept_v_turns_negative"])[0]
    later = amm(golden["amm_sell_negative_v_same_pool_later"])[0]
    assert first["pool"] == later["pool"] and first["virtual_quote_reserves"] == 0
    assert first["fee_settlement"] == "kept_in_pool" and first["fees_kept"] > 0
    assert later["virtual_quote_reserves"] == first["virtual_quote_reserves_after"] == -first["fees_kept"]


def test_multi_hop_route(golden):
    swaps = swaps_of(golden["multi_hop_swap"])
    assert {s["venue"] for s in swaps} == {"pumpswap", "pumpfun"}
    assert all(s["ix_name"] == "multi_hop_swap" for s in swaps)
    assert all(s["fee_settlement"] in ("kept_in_pool", "kept_on_curve", "no_fee") for s in swaps)


def test_every_event_layout_is_current(golden):
    """No golden event has unknown trailing bytes or a truncated layout."""
    for fx in golden.values():
        for step in fx["record"]["steps"]:
            if step["kind"] == "event":
                ev = decode_event(step["program"], base64.b64decode(step["data"]))
                if ev is not None and ev.fields:
                    assert ev.trailing_bytes == 0 and not ev.prefix, (fx["name"], ev.name)


def test_records_hold_no_signer_accounts(golden):
    """Trimmed records keep pool/mint accounts only; traders appear solely inside raw event bytes."""
    for fx in golden.values():
        for step in fx["record"]["steps"]:
            assert set(step) <= {"kind", "program", "name", "data", "accounts"}
            accts = step.get("accounts") or {}
            assert set(accts) <= {"pool", "base_mint", "quote_mint", "hops"}
        for s in fx["expected"]["swaps"]:
            assert "trader" not in s


def test_golden_set_covers_the_documented_cases(golden):
    names = set(golden)
    seen_ix = {s["ix_name"] for fx in golden.values() for s in swaps_of(fx)}
    assert {"buy", "buy_exact_quote_in", "buy_v2", "sell", "sell_v2", "multi_hop_swap", "buy_exact_sol_in"} <= seen_ix
    assert any(n.startswith("curve_") for n in names) and len(names) >= 20
    assert any(fx["record"]["steps"] and amm(fx) and amm(fx)[0]["quote_mint"] and
               amm(fx)[0]["quote_mint"].startswith("EPjF") for fx in golden.values())     # a USDC pool
    assert PUMPSWAP_PROGRAM in {st["program"] for fx in golden.values() for st in fx["record"]["steps"]}
