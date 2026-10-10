"""Transaction-level behaviour on synthetic transactions (both RPC encodings, pairing rules, sweeps)."""
from helpers import encode_fields

from pumpswap_decode._b58 import b58encode
from pumpswap_decode.constants import (EMIT_CPI_TAG, PUMPSWAP_PROGRAM, WSOL_MINT, event_discriminator,
                                       instruction_discriminator)
from pumpswap_decode.events import (PUMPSWAP_BOOST_BUY_AND_BURN_EVENT, PUMPSWAP_BUY_EVENT,
                                    PUMPSWAP_SWEEP_POOL_FEE_EVENT)
from pumpswap_decode.tx import decode_transaction, decode_trimmed, organic_swaps, trim_transaction

POOL = "Poo1111111111111111111111111111111111111111"
MINT = "Mint111111111111111111111111111111111111111"
USER = "User111111111111111111111111111111111111111"
GC = "Gc11111111111111111111111111111111111111111"
ROUTER = "Router1111111111111111111111111111111111111"


def ev_bytes(name, layout, vals):
    return b58encode(EMIT_CPI_TAG + event_discriminator(name) + encode_fields(layout, vals))


def buy_vals(**kw):
    v = {"pool": POOL, "user": USER, "ix_name": "buy_exact_quote_in", "base_amount_out": 10,
         "pool_base_token_reserves": 10**12, "pool_quote_token_reserves": 10**9, "virtual_quote_reserves": -5}
    v.update(kw)
    return v


def make_tx(steps, parsed=False, err=None):
    keys = [USER, PUMPSWAP_PROGRAM, POOL, GC, MINT, WSOL_MINT, ROUTER]
    idx = {k: i for i, k in enumerate(keys)}

    def ix(program, data_b58, accounts=()):
        if parsed:
            return {"programId": program, "data": data_b58, "accounts": list(accounts)}
        return {"programIdIndex": idx[program], "data": data_b58, "accounts": [idx[a] for a in accounts]}

    top = [{"programId": ROUTER, "data": "1", "accounts": []} if parsed
           else {"programIdIndex": idx[ROUTER], "data": "1", "accounts": []}]   # an aggregator / router call
    inner = []
    for kind, payload in steps:
        if kind == "top":
            top.append(ix(PUMPSWAP_PROGRAM, *payload))
        else:
            inner.append(ix(PUMPSWAP_PROGRAM, payload))
    account_keys = [{"pubkey": k} for k in keys] if parsed else keys
    return {"slot": 1, "blockTime": 2, "transaction": {"signatures": ["sig"], "message": {
        "accountKeys": account_keys, "instructions": top}},
        "meta": {"err": err, "innerInstructions": [{"index": len(top) - 1, "instructions": inner}],
                 "preTokenBalances": [], "postTokenBalances": [], "loadedAddresses": {"writable": [], "readonly": []}}}


AMM_ACCTS = (POOL, USER, GC, MINT, WSOL_MINT)


def test_both_encodings_give_the_same_result():
    data = b58encode(instruction_discriminator("buy_exact_quote_in") + (1000).to_bytes(8, "little") + bytes(8))
    steps = [("top", (data, AMM_ACCTS)), ("inner", ev_bytes("BuyEvent", PUMPSWAP_BUY_EVENT, buy_vals(
        quote_amount_in=100, user_quote_amount_in=90, quote_amount_in_with_lp_fee=95, lp_fee=5, protocol_fee=5)))]
    a = decode_transaction(make_tx(steps))
    b = decode_transaction(make_tx(steps, parsed=True))
    assert a == b
    s = a["swaps"][0]
    assert (s["quote_cp"], s["quote_user"], s["quote_mint"], s["base_mint"]) == (90, 100, WSOL_MINT, MINT)
    assert s["virtual_quote_reserves"] == -5 and a["instructions"][0]["args"]["spendable_quote_in"] == 1000


def test_trim_drops_signers():
    rec = trim_transaction(make_tx([("top", (b58encode(instruction_discriminator("sell") + bytes(16)), AMM_ACCTS))]))
    assert USER not in str(rec["steps"])


def test_boost_flag_is_per_pool_not_per_tx():
    other = "Xther11111111111111111111111111111111111111"
    steps = [
        ("inner", ev_bytes("BuyEvent", PUMPSWAP_BUY_EVENT, buy_vals(pool=other, quote_amount_in=110,
                                                                    user_quote_amount_in=100, lp_fee=10))),
        ("inner", ev_bytes("BuyEvent", PUMPSWAP_BUY_EVENT, buy_vals(quote_amount_in=50, user_quote_amount_in=50))),
        ("inner", ev_bytes("BoostBuyAndBurnEvent", PUMPSWAP_BOOST_BUY_AND_BURN_EVENT,
                           {"pool": POOL, "mint": MINT, "quote_amount_in_used": 50, "base_amount_burned": 10})),
    ]
    d = decode_transaction(make_tx(steps))
    assert [s["is_boost_buyback"] for s in d["swaps"]] == [False, True]
    assert [s["pool"] for s in organic_swaps(d)] == [other]
    assert d["boost_buybacks"][0]["matched_buy_event"]


def test_sweeps_are_payouts_not_swaps():
    steps = [("inner", ev_bytes("SweepPoolFeeEvent", PUMPSWAP_SWEEP_POOL_FEE_EVENT,
                                {"pool": POOL, "base_mint": MINT, "quote_mint": WSOL_MINT, "recipient": GC,
                                 "payer": USER, "amount": 12_345, "bucket": 1}))]
    d = decode_transaction(make_tx(steps))
    assert d["swaps"] == [] and d["fee_sweeps"][0]["bucket"] == "creator" and d["fee_sweeps"][0]["amount"] == 12_345
    assert "payer" not in d["fee_sweeps"][0]


def test_failed_transaction_has_no_swaps():
    steps = [("inner", ev_bytes("BuyEvent", PUMPSWAP_BUY_EVENT, buy_vals(quote_amount_in=1, user_quote_amount_in=1)))]
    assert decode_trimmed(trim_transaction(make_tx(steps, err={"InstructionError": [0, "x"]})))["swaps"] == []
