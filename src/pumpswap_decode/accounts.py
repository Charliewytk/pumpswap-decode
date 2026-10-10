"""PumpSwap `Pool` account decoding.

Fields were appended over time, so old pools are shorter: missing trailing fields read as 0 / False, as the public
docs specify. `virtual_quote_reserves` is an i128 and may be negative (since 30 Sep 2026). `protocol_fees` and
`creator_fees` are the fees v2 trades and multi_hop_swap keep in the quote vault until a sweep; they are already
subtracted from `virtual_quote_reserves`, so

    effective_quote_reserves = pool_quote_vault_amount + virtual_quote_reserves
    real_quote_reserves      = pool_quote_vault_amount - protocol_fees - creator_fees
"""
from __future__ import annotations

import hashlib

from ._b58 import b58encode

POOL_DISCRIMINATOR = hashlib.sha256(b"account:Pool").digest()[:8]

POOL_LAYOUT = [
    ("pool_bump", "u8", 1), ("index", "u16", 2), ("creator", "pubkey", 32), ("base_mint", "pubkey", 32),
    ("quote_mint", "pubkey", 32), ("lp_mint", "pubkey", 32), ("pool_base_token_account", "pubkey", 32),
    ("pool_quote_token_account", "pubkey", 32), ("lp_supply", "u64", 8), ("coin_creator", "pubkey", 32),
    ("is_mayhem_mode", "bool", 1), ("is_cashback_coin", "bool", 1), ("virtual_quote_reserves", "i128", 16),
    ("creator_fee_bps", "u64", 8), ("can_edit_creator_fee", "bool", 1), ("is_holder_reward", "bool", 1),
    ("protocol_fees", "u64", 8), ("creator_fees", "u64", 8),
]


def decode_pool(data: bytes) -> dict:
    if data[:8] != POOL_DISCRIMINATOR:
        raise ValueError("not a PumpSwap Pool account")
    out: dict = {}
    o = 8
    for name, ty, n in POOL_LAYOUT:
        chunk = data[o:o + n]
        present = len(chunk) == n
        if ty == "pubkey":
            out[name] = b58encode(chunk) if present else None
        elif ty == "bool":
            out[name] = bool(chunk[0]) if present else False
        else:
            out[name] = int.from_bytes(chunk, "little", signed=(ty == "i128")) if present else 0
        o += n
    out["layout_bytes"] = o
    out["account_bytes"] = len(data)
    return out


def pool_effective_quote(pool: dict, quote_vault_amount: int) -> int:
    y = int(quote_vault_amount) + int(pool["virtual_quote_reserves"])
    if y < 0:
        raise ValueError("effective quote reserves negative: inconsistent pool and vault snapshot")
    return y


def pool_real_quote(pool: dict, quote_vault_amount: int) -> int:
    return int(quote_vault_amount) - int(pool["protocol_fees"]) - int(pool["creator_fees"])
