"""Transaction-level decoding: find PumpSwap / pump.fun instructions and events, pair them, normalize the swaps.

Two steps, so tests can run on small stored records:

1. `trim_transaction(tx)` takes a `getTransaction` result (encoding "json" or "jsonParsed",
   maxSupportedTransactionVersion 1) and keeps only what decoding needs: the ordered PumpSwap / pump.fun / pump
   fees instructions (raw data plus pool and mint accounts), their emit_cpi event payloads, and the token balances
   of the pools involved. Signer, fee payer and user accounts are not kept outside the raw event bytes.
2. `decode_trimmed(record)` turns that record into normalized swaps, boost buybacks and fee sweeps.

`decode_transaction(tx)` does both.

Fee settlement (v2 trades, multi_hop_swap)
------------------------------------------
`buy_v2`, `buy_exact_quote_in_v2`, `sell_v2` and `multi_hop_swap` charge the same fees as v1 but keep the protocol
fee (minus its buyback part, which is still paid in the trade) and the creator fee inside the pool's quote vault.
The event still reports the amounts charged; its `protocol_fee_recipient` is the zero key. The program subtracts the
kept amount from `virtual_quote_reserves`, and a later `sweep_protocol_fee` / `sweep_creator_fee` pays it out
(`SweepPoolFeeEvent`) and adds it back. So per trade we report:

* `fees_kept` = protocol_fee - buyback_fee + coin_creator_fee (0 for v1 trades),
* `quote_vault_delta` = what the vault really gained or lost, kept fees included,
* `virtual_quote_reserves_after` = V - fees_kept.

Fee income belongs to the trade; sweeps are payouts and are listed separately in `fee_sweeps`. Do not count both.
pump.fun v3 curve trades work the same way on the bonding curve (`fee_recipient` is the zero key).

Boost buybacks
--------------
`boost_buy_and_burn` is a protocol instruction. It spends the pool's boost vault on a fee-free buy and burns the
tokens; it emits an ordinary-looking BuyEvent (ix_name "buy_exact_quote_in", all fees 0) followed by a
BoostBuyAndBurnEvent for the same pool. That BuyEvent is not organic demand. We mark exactly that BuyEvent (same
pool, preceding the BoostBuyAndBurnEvent, zero fees) with `is_boost_buyback=True`, not the whole transaction, so an
organic swap in the same transaction keeps its own label.

`buyback_fee` in Buy/Sell/TradeEvents is different: it is the part of the protocol fee routed to buybacks
(`buyback_fee_basis_points` is a share of the protocol fee, e.g. 5000 = half). It is inside `protocol_fee`, not an
extra charge.
"""
from __future__ import annotations

import base64
from typing import Any

from ._b58 import b58decode
from .constants import DEFAULT_PUBKEY, EMIT_CPI_TAG, IX_NAMES, PUMP_FEES_PROGRAM, PUMPFUN_PROGRAM, PUMPSWAP_PROGRAM
from .events import Event, decode_event
from .instructions import DISCRIMINATORS, decode_instruction
from .pricing import effective_quote
from .semantics import normalize_buy, normalize_sell

PROGRAMS = (PUMPSWAP_PROGRAM, PUMPFUN_PROGRAM, PUMP_FEES_PROGRAM)
AMM_POOL_IX = {"buy", "buy_v2", "buy_exact_quote_in", "buy_exact_quote_in_v2", "sell", "sell_v2",
               "boost_buy_and_burn"}
AMM_V2_IX = {"buy_v2", "buy_exact_quote_in_v2", "sell_v2", "multi_hop_swap"}
AMM_ACCOUNT_SLOTS = {"pool": 0, "base_mint": 3, "quote_mint": 4}
MULTI_HOP_FIXED_ACCOUNTS = 16
MULTI_HOP_ACCOUNTS_PER_HOP = 5
SWEEP_BUCKETS = {0: "protocol", 1: "creator"}


# --------------------------------------------------------------------------------------------------------------------
# 1. trim
# --------------------------------------------------------------------------------------------------------------------
def _account_keys(tx: dict) -> list[str]:
    msg = tx["transaction"]["message"]
    keys = [k["pubkey"] if isinstance(k, dict) else k for k in msg["accountKeys"]]
    loaded = (tx.get("meta") or {}).get("loadedAddresses") or {}
    if not any(isinstance(k, dict) for k in msg["accountKeys"]):  # jsonParsed already includes loaded keys
        keys += list(loaded.get("writable") or []) + list(loaded.get("readonly") or [])
    return keys


def _program_of(ix: dict, keys: list[str]) -> str | None:
    if "programId" in ix:
        return ix["programId"]
    i = ix.get("programIdIndex")
    return keys[i] if i is not None and i < len(keys) else None


def _ix_accounts(ix: dict, keys: list[str]) -> list[str]:
    acc = ix.get("accounts") or []
    return [a if isinstance(a, str) else (keys[a] if a < len(keys) else "") for a in acc]


def _ix_name(program: str, data: bytes) -> str:
    return DISCRIMINATORS.get(program, {}).get(data[:8]) or IX_NAMES.get(program, {}).get(data[:8]) or data[:8].hex()


def _step(ix: dict, keys: list[str]) -> dict | None:
    program = _program_of(ix, keys)
    if program not in PROGRAMS or "data" not in ix:
        return None
    try:
        data = b58decode(ix["data"])
    except ValueError:
        return None
    if data[:8] == EMIT_CPI_TAG:
        return {"kind": "event", "program": program, "data": base64.b64encode(data[8:]).decode()}
    name = _ix_name(program, data)
    step: dict[str, Any] = {"kind": "ix", "program": program, "name": name,
                            "data": base64.b64encode(data).decode()}
    if program == PUMPSWAP_PROGRAM and name in AMM_POOL_IX:
        accts = _ix_accounts(ix, keys)
        if len(accts) > max(AMM_ACCOUNT_SLOTS.values()):
            step["accounts"] = {k: accts[i] for k, i in AMM_ACCOUNT_SLOTS.items()}
    elif program == PUMPSWAP_PROGRAM and name == "multi_hop_swap":
        accts = _ix_accounts(ix, keys)[MULTI_HOP_FIXED_ACCOUNTS:]
        hops = []
        for i in range(0, len(accts) - MULTI_HOP_ACCOUNTS_PER_HOP + 1, MULTI_HOP_ACCOUNTS_PER_HOP):
            hops.append({"base_mint": accts[i], "quote_mint": accts[i + 1], "pool_or_curve": accts[i + 2]})
        step["accounts"] = {"hops": hops}
    return step


def trim_transaction(tx: dict) -> dict:
    meta = tx.get("meta") or {}
    keys = _account_keys(tx)
    inner = {ii["index"]: ii["instructions"] for ii in meta.get("innerInstructions") or []}
    steps: list[dict] = []
    for i, ix in enumerate(tx["transaction"]["message"]["instructions"]):
        for candidate in [ix] + list(inner.get(i, [])):
            s = _step(candidate, keys)
            if s:
                steps.append(s)
    pools = set()
    for s in steps:
        if s["kind"] == "event" and s["program"] == PUMPSWAP_PROGRAM:
            ev = decode_event(PUMPSWAP_PROGRAM, base64.b64decode(s["data"]))
            if ev is not None and ev.fields.get("pool"):
                pools.add(ev.fields["pool"])
    balances: dict[tuple[str, str], dict] = {}
    for when in ("pre", "post"):
        for b in meta.get(f"{when}TokenBalances") or []:
            if b.get("owner") not in pools:
                continue
            row = balances.setdefault((b["owner"], b["mint"]),
                                      {"owner": b["owner"], "mint": b["mint"], "pre": 0, "post": 0})
            row[when] += int(b["uiTokenAmount"]["amount"])
    sig = (tx.get("transaction") or {}).get("signatures") or [None]
    return {
        "signature": sig[0],
        "slot": tx.get("slot"),
        "block_time": tx.get("blockTime"),
        "failed": meta.get("err") is not None,
        "steps": steps,
        "pool_balances": sorted(balances.values(), key=lambda r: (r["owner"], r["mint"])),
    }


# --------------------------------------------------------------------------------------------------------------------
# 2. decode
# --------------------------------------------------------------------------------------------------------------------
def _amm_swap(ev: Event, ix: dict | None, strict: bool) -> dict:
    buy = ev.name == "BuyEvent"
    legs = normalize_buy(ev, strict=strict) if buy else normalize_sell(ev)
    v = ev["virtual_quote_reserves"] or 0
    accts = (ix or {}).get("accounts") or {}
    hop = next((h for h in accts.get("hops", []) if h["pool_or_curve"] == ev["pool"]), None)
    if hop:
        accts = hop
    kept_in_pool = ev.get("protocol_fee_recipient") == DEFAULT_PUBKEY
    kept = (ev["protocol_fee"] - ev.get("buyback_fee", 0) + ev["coin_creator_fee"]) if kept_in_pool else 0
    outer = (ix or {}).get("name")
    return {
        "venue": "pumpswap",
        "side": "buy" if buy else "sell",
        "ix_name": ev.get("ix_name") if buy else (outer or "sell"),
        "outer_ix": outer,
        "pool": ev["pool"],
        "base_mint": accts.get("base_mint"),
        "quote_mint": accts.get("quote_mint"),
        "trader": ev["user"],
        "timestamp": ev["timestamp"],
        "base_amount": ev["base_amount_out"] if buy else ev["base_amount_in"],
        "quote_mode": legs.mode,
        "quote_cp": legs.cp,
        "quote_user": legs.user,
        "quote_vault_delta": legs.vault + kept,
        "lp_fee": ev["lp_fee"],
        "protocol_fee": ev["protocol_fee"],
        "coin_creator_fee": ev["coin_creator_fee"],
        "lp_fee_bps": ev["lp_fee_basis_points"],
        "protocol_fee_bps": ev["protocol_fee_basis_points"],
        "coin_creator_fee_bps": ev["coin_creator_fee_basis_points"],
        "total_fee_bps": ev["lp_fee_basis_points"] + ev["protocol_fee_basis_points"] + ev["coin_creator_fee_basis_points"],
        "buyback_fee_bps": ev.get("buyback_fee_basis_points", 0),
        "buyback_fee": ev.get("buyback_fee", 0),
        "cashback": ev.get("cashback", 0),
        "fee_settlement": "no_fee" if legs.fees == 0 else ("kept_in_pool" if kept_in_pool else "paid_in_trade"),
        "fees_kept": kept,
        "creator_fee_unclaimed": ev.get("creator_fee_unclaimed"),
        "pool_base_reserves": ev["pool_base_token_reserves"],
        "pool_quote_reserves": ev["pool_quote_token_reserves"],
        "virtual_quote_reserves": v,
        "virtual_quote_reserves_after": v - kept,
        "effective_quote_reserves": effective_quote(ev["pool_quote_token_reserves"], v),
        "base_supply": ev.get("base_supply", 0),
        "can_boost": ev.get("can_boost", False),
        "is_boost_buyback": False,
        "trailing_bytes": ev.trailing_bytes,
        "mode_inferred": legs.inferred,
    }


def _curve_swap(ev: Event) -> dict:
    """pump.fun TradeEvent. Curves can be paired with SOL or with another mint (USDC, a pump coin, ...). The quote-mint
    fields (`quote_amount`, `virtual_quote_reserves`, `real_quote_reserves`) carry the trade for every curve; the
    legacy `sol_*` fields equal them on SOL curves and are 0 on other curves. Older events without the quote fields
    fall back to the `sol_*` fields."""
    buy = bool(ev["is_buy"])
    quote = ev.get("quote_amount") if ev.fields.get("quote_amount") is not None else ev["sol_amount"]
    v_quote = ev.fields.get("virtual_quote_reserves")
    r_quote = ev.fields.get("real_quote_reserves")
    fees = ev["fee"] + ev["creator_fee"]
    kept_on_curve = ev.get("fee_recipient") == DEFAULT_PUBKEY
    return {
        "venue": "pumpfun",
        "side": "buy" if buy else "sell",
        "ix_name": ev.get("ix_name"),
        "mint": ev["mint"],
        "quote_mint": ev.get("quote_mint"),
        "trader": ev["user"],
        "timestamp": ev["timestamp"],
        "base_amount": ev["token_amount"],
        # the quote into / out of the curve, for every buy variant (no AMM-style field switch on the curve)
        "quote_cp": quote,
        "quote_user": quote + fees if buy else quote - fees,
        "protocol_fee": ev["fee"],
        "creator_fee": ev["creator_fee"],
        "protocol_fee_bps": ev["fee_basis_points"],
        "creator_fee_bps": ev["creator_fee_basis_points"],
        "total_fee_bps": ev["fee_basis_points"] + ev["creator_fee_basis_points"],
        "buyback_fee_bps": ev.get("buyback_fee_basis_points", 0),
        "buyback_fee": ev.get("buyback_fee", 0),
        "cashback": ev.get("cashback", 0),
        "fee_settlement": "no_fee" if fees == 0 else ("kept_on_curve" if kept_on_curve else "paid_in_trade"),
        "fees_kept": (ev["fee"] - ev.get("buyback_fee", 0) + ev["creator_fee"]) if kept_on_curve else 0,
        "virtual_quote_reserves_after": v_quote if v_quote is not None else ev["virtual_sol_reserves"],
        "virtual_token_reserves_after": ev["virtual_token_reserves"],
        "real_quote_reserves_after": r_quote if r_quote is not None else ev["real_sol_reserves"],
        "real_token_reserves_after": ev["real_token_reserves"],
        "mayhem_mode": ev.get("mayhem_mode", False),
        "trailing_bytes": ev.trailing_bytes,
    }


def decode_trimmed(record: dict, strict: bool = True) -> dict:
    """Normalized swaps, boost buybacks, fee sweeps and instruction arguments of one trimmed transaction record."""
    out: dict[str, Any] = {"signature": record.get("signature"), "slot": record.get("slot"), "instructions": [],
                           "swaps": [], "boost_buybacks": [], "fee_sweeps": [], "other_events": []}
    if record.get("failed"):
        return out
    open_ix: list[dict] = []          # AMM pool instructions not yet matched to a swap event
    amm_swaps_by_pool: dict[str, list[dict]] = {}
    for step in record["steps"]:
        if step["kind"] == "ix":
            decoded = decode_instruction(step["program"], base64.b64decode(step["data"])) if "data" in step else None
            out["instructions"].append({"program": step["program"], "name": step["name"],
                                        "args": decoded.args if decoded else None})
            if step["program"] == PUMPSWAP_PROGRAM and (step["name"] in AMM_POOL_IX or step["name"] == "multi_hop_swap"):
                open_ix.append(step)
            continue
        ev = decode_event(step["program"], base64.b64decode(step["data"]))
        if ev is None:
            out["other_events"].append("unknown")
            continue
        if ev.name in ("BuyEvent", "SellEvent"):
            pool = ev["pool"]
            ix = next((s for s in open_ix if (s.get("accounts") or {}).get("pool") == pool), None)
            if ix is not None:
                open_ix.remove(ix)        # one swap event per single-pool instruction
            else:
                ix = next((s for s in open_ix if any(h["pool_or_curve"] == pool
                                                     for h in (s.get("accounts") or {}).get("hops", []))), None)
            swap = _amm_swap(ev, ix, strict)
            out["swaps"].append(swap)
            amm_swaps_by_pool.setdefault(pool, []).append(swap)
        elif ev.name == "TradeEvent":
            out["swaps"].append(_curve_swap(ev))
        elif ev.name == "BoostBuyAndBurnEvent":
            pool = ev["pool"]
            match = None
            for swap in reversed(amm_swaps_by_pool.get(pool, [])):
                if swap["side"] == "buy" and not swap["is_boost_buyback"] and swap["total_fee_bps"] == 0:
                    match = swap
                    break
            if match is not None:
                match["is_boost_buyback"] = True
            out["boost_buybacks"].append({
                "pool": pool, "mint": ev["mint"], "quote_amount_in_used": ev["quote_amount_in_used"],
                "base_amount_burned": ev["base_amount_burned"],
                "virtual_quote_reserves_after": ev["virtual_quote_reserves"],
                "real_quote_reserves_after": ev["real_quote_reserves_after"],
                "boost_vault_remaining": ev["boost_vault_remaining"], "matched_buy_event": match is not None,
            })
        elif ev.name in ("SweepPoolFeeEvent", "SweepBondingCurveFeeEvent"):
            out["fee_sweeps"].append({
                "venue": "pumpswap" if ev.name == "SweepPoolFeeEvent" else "pumpfun",
                "pool": ev.get("pool"), "bonding_curve": ev.get("bonding_curve"),
                "mint": ev.get("base_mint") or ev.get("mint"), "quote_mint": ev["quote_mint"],
                "bucket": SWEEP_BUCKETS.get(ev["bucket"], ev["bucket"]), "amount": ev["amount"],
                "recipient": ev["recipient"], "timestamp": ev["timestamp"],
            })
        else:
            out["other_events"].append(ev.name)
    return out


def decode_transaction(tx: dict, strict: bool = True) -> dict:
    return decode_trimmed(trim_transaction(tx), strict=strict)


def organic_swaps(decoded: dict) -> list[dict]:
    """Swaps that represent user demand: drops boost buybacks."""
    return [s for s in decoded["swaps"] if not s.get("is_boost_buyback")]
