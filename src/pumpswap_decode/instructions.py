"""Instruction discriminators and argument decoding for PumpSwap, pump.fun and the pump fees program.

Argument layouts are hand-written from the public IDLs at constants.IDL_REFERENCE_COMMIT; tests/test_idl_pins.py
checks them against the pinned IDL field lists in tools/idl_pins.json. Trailing optional arguments (for example
`track_volume`) were added to some instructions over time and older clients still omit them, so decoding is
prefix-tolerant: missing tail arguments come back as None.

Which of these have golden fixtures (decoded from real mainnet transactions) is listed in the README; the rest are
decoded from the IDL layout only.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .constants import PUMP_FEES_PROGRAM, PUMPFUN_PROGRAM, PUMPSWAP_PROGRAM, instruction_discriminator
from .events import decode_fields

# OptionBool / OptionU64 in these IDLs are single-field structs (a plain bool / u64), not Borsh Option<T>.
INSTRUCTION_ARGS: dict[str, dict[str, list]] = {
    PUMPSWAP_PROGRAM: {
        "buy": [("base_amount_out", "u64"), ("max_quote_amount_in", "u64"), ("track_volume", "bool")],
        "buy_exact_quote_in": [("spendable_quote_in", "u64"), ("min_base_amount_out", "u64"), ("track_volume", "bool")],
        "sell": [("base_amount_in", "u64"), ("min_quote_amount_out", "u64")],
        "buy_v2": [("base_amount_out", "u64"), ("max_quote_amount_in", "u64")],
        "buy_exact_quote_in_v2": [("spendable_quote_in", "u64"), ("min_base_amount_out", "u64")],
        "sell_v2": [("base_amount_in", "u64"), ("min_quote_amount_out", "u64")],
        "multi_hop_swap": [("amount_in", "u64"), ("min_amount_out", "u64")],
        "boost_buy_and_burn": [("quote_amount_in", "u64"), ("min_base_amount_burned", "u64")],
        "init_boost": [],
        "sweep_protocol_fee": [],
        "sweep_creator_fee": [],
        "deposit": [("lp_token_amount_out", "u64"), ("max_base_amount_in", "u64"), ("max_quote_amount_in", "u64")],
        "withdraw": [("lp_token_amount_in", "u64"), ("min_base_amount_out", "u64"), ("min_quote_amount_out", "u64")],
        "create_pool": [("index", "u16"), ("base_amount_in", "u64"), ("quote_amount_in", "u64"),
                        ("coin_creator", "pubkey"), ("is_mayhem_mode", "bool"), ("is_cashback_coin", "bool"),
                        ("creator_fee_bps", "u64"), ("can_edit_creator_fee", "bool"), ("is_holder_reward", "bool")],
    },
    PUMPFUN_PROGRAM: {
        "buy": [("amount", "u64"), ("max_sol_cost", "u64"), ("track_volume", "bool"), ("partial_fill", "bool")],
        "buy_exact_sol_in": [("spendable_sol_in", "u64"), ("min_tokens_out", "u64"), ("track_volume", "bool"),
                             ("partial_fill", "bool")],
        "sell": [("amount", "u64"), ("min_sol_output", "u64")],
        "buy_v2": [("amount", "u64"), ("max_sol_cost", "u64"), ("partial_fill", "bool")],
        "buy_v3": [("amount", "u64"), ("max_sol_cost", "u64"), ("partial_fill", "bool")],
        "buy_exact_quote_in_v2": [("spendable_quote_in", "u64"), ("min_tokens_out", "u64"), ("partial_fill", "bool")],
        "buy_exact_quote_in_v3": [("spendable_quote_in", "u64"), ("min_tokens_out", "u64"), ("partial_fill", "bool")],
        "sell_v2": [("amount", "u64"), ("min_sol_output", "u64")],
        "sell_v3": [("amount", "u64"), ("min_sol_output", "u64")],
        "multi_hop_curve_swap": [("amount_in", "u64"), ("hop_fees", "bytes")],
        "sweep_protocol_fee": [],
        "sweep_creator_fee": [],
    },
    PUMP_FEES_PROGRAM: {
        "upsert_fee_tiers": [("fee_tiers", ("vec", "fee_tier")), ("offset", "u8")],
        "upsert_stable_fee_tiers": [("fee_tiers", ("vec", "fee_tier")), ("offset", "u8")],
        "sweep_buyback": [("index", "u8")],
    },
}

DISCRIMINATORS: dict[str, dict[bytes, str]] = {
    program: {instruction_discriminator(name): name for name in names}
    for program, names in INSTRUCTION_ARGS.items()
}


@dataclass
class Instruction:
    program: str
    name: str
    args: dict[str, Any] = field(default_factory=dict)
    prefix: bool = False
    trailing_bytes: int = 0


def decode_instruction(program: str, data: bytes) -> Instruction | None:
    """Decode instruction data (discriminator + Borsh args). None for an unknown program or discriminator."""
    name = DISCRIMINATORS.get(program, {}).get(bytes(data[:8]))
    if name is None:
        return None
    layout = INSTRUCTION_ARGS[program][name]
    if not layout:
        return Instruction(program, name, {}, False, len(data) - 8)
    try:
        args, prefix, trailing = decode_fields(layout, data[8:])
    except ValueError:
        return Instruction(program, name, {k: None for k, _ in layout}, True, 0)
    return Instruction(program, name, args, prefix, trailing)
