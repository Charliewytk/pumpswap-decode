"""Borsh decoding of the PumpSwap and pump.fun Anchor events that matter for swaps.

Layouts are hand-written from the public IDLs (see constants.IDL_REFERENCE_COMMIT) instead of loading the IDL JSON.
The reader is prefix-tolerant, because both programs append fields to their events over time:

* fields decode in order until the bytes run out; any missing tail field is None and `prefix` is True;
* bytes left over after the last known field are counted in `trailing_bytes` (a newer layout than this file knows).

So an old event still decodes, and a new field shows up as trailing bytes instead of silently shifting other fields.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ._b58 import b58encode
from .constants import EMIT_CPI_TAG, PUMP_FEES_PROGRAM, PUMPFUN_PROGRAM, PUMPSWAP_PROGRAM, event_discriminator

_INT = {"u8": (1, False), "u16": (2, False), "u32": (4, False), "u64": (8, False), "i64": (8, True),
        "u128": (16, False), "i128": (16, True)}

# --------------------------------------------------------------------------------------------------------------------
# Layouts (field name, borsh type). Only swap-relevant events get full layouts.
# --------------------------------------------------------------------------------------------------------------------
PUMPSWAP_BUY_EVENT = [
    ("timestamp", "i64"), ("base_amount_out", "u64"), ("max_quote_amount_in", "u64"),
    ("user_base_token_reserves", "u64"), ("user_quote_token_reserves", "u64"),
    ("pool_base_token_reserves", "u64"), ("pool_quote_token_reserves", "u64"),
    ("quote_amount_in", "u64"), ("lp_fee_basis_points", "u64"), ("lp_fee", "u64"),
    ("protocol_fee_basis_points", "u64"), ("protocol_fee", "u64"), ("quote_amount_in_with_lp_fee", "u64"),
    ("user_quote_amount_in", "u64"), ("pool", "pubkey"), ("user", "pubkey"), ("user_base_token_account", "pubkey"),
    ("user_quote_token_account", "pubkey"), ("protocol_fee_recipient", "pubkey"),
    ("protocol_fee_recipient_token_account", "pubkey"), ("coin_creator", "pubkey"),
    ("coin_creator_fee_basis_points", "u64"), ("coin_creator_fee", "u64"), ("track_volume", "bool"),
    ("total_unclaimed_tokens", "u64"), ("total_claimed_tokens", "u64"), ("current_sol_volume", "u64"),
    ("last_update_timestamp", "i64"), ("min_base_amount_out", "u64"), ("ix_name", "string"),
    ("cashback_fee_basis_points", "u64"), ("cashback", "u64"), ("buyback_fee_basis_points", "u64"),
    ("buyback_fee", "u64"), ("virtual_quote_reserves", "i128"), ("can_boost", "bool"), ("base_supply", "u64"),
    ("holder_rewards_bps", "u64"), ("holder_rewards", "u64"), ("creator_fee_unclaimed", "u64"),
]

PUMPSWAP_SELL_EVENT = [
    ("timestamp", "i64"), ("base_amount_in", "u64"), ("min_quote_amount_out", "u64"),
    ("user_base_token_reserves", "u64"), ("user_quote_token_reserves", "u64"),
    ("pool_base_token_reserves", "u64"), ("pool_quote_token_reserves", "u64"),
    ("quote_amount_out", "u64"), ("lp_fee_basis_points", "u64"), ("lp_fee", "u64"),
    ("protocol_fee_basis_points", "u64"), ("protocol_fee", "u64"), ("quote_amount_out_without_lp_fee", "u64"),
    ("user_quote_amount_out", "u64"), ("pool", "pubkey"), ("user", "pubkey"), ("user_base_token_account", "pubkey"),
    ("user_quote_token_account", "pubkey"), ("protocol_fee_recipient", "pubkey"),
    ("protocol_fee_recipient_token_account", "pubkey"), ("coin_creator", "pubkey"),
    ("coin_creator_fee_basis_points", "u64"), ("coin_creator_fee", "u64"), ("cashback_fee_basis_points", "u64"),
    ("cashback", "u64"), ("buyback_fee_basis_points", "u64"), ("buyback_fee", "u64"),
    ("virtual_quote_reserves", "i128"), ("can_boost", "bool"), ("base_supply", "u64"),
    ("holder_rewards_bps", "u64"), ("holder_rewards", "u64"), ("creator_fee_unclaimed", "u64"),
]

PUMPSWAP_BOOST_BUY_AND_BURN_EVENT = [
    ("timestamp", "i64"), ("mint", "pubkey"), ("bonding_curve", "pubkey"), ("pool", "pubkey"),
    ("authority", "pubkey"), ("quote_amount_in_requested", "u64"), ("quote_amount_in_used", "u64"),
    ("base_amount_burned", "u64"), ("virtual_quote_reserves", "i128"), ("real_quote_reserves_after", "u64"),
    ("base_reserves_after", "u64"), ("boost_vault_remaining", "u64"),
]

PUMPSWAP_INIT_BOOST_EVENT = [
    ("timestamp", "i64"), ("mint", "pubkey"), ("bonding_curve", "pubkey"), ("pool", "pubkey"),
    ("virtual_quote_reserves", "i128"), ("real_quote_reserves_after", "u64"),
]

PUMPFUN_TRADE_EVENT = [
    ("mint", "pubkey"), ("sol_amount", "u64"), ("token_amount", "u64"), ("is_buy", "bool"), ("user", "pubkey"),
    ("timestamp", "i64"), ("virtual_sol_reserves", "u64"), ("virtual_token_reserves", "u64"),
    ("real_sol_reserves", "u64"), ("real_token_reserves", "u64"), ("fee_recipient", "pubkey"),
    ("fee_basis_points", "u64"), ("fee", "u64"), ("creator", "pubkey"), ("creator_fee_basis_points", "u64"),
    ("creator_fee", "u64"), ("track_volume", "bool"), ("total_unclaimed_tokens", "u64"),
    ("total_claimed_tokens", "u64"), ("current_sol_volume", "u64"), ("last_update_timestamp", "i64"),
    ("ix_name", "string"), ("mayhem_mode", "bool"), ("cashback_fee_basis_points", "u64"), ("cashback", "u64"),
    ("buyback_fee_basis_points", "u64"), ("buyback_fee", "u64"), ("shareholders", ("vec", "shareholder")),
    ("quote_mint", "pubkey"), ("quote_amount", "u64"), ("virtual_quote_reserves", "u64"),
    ("real_quote_reserves", "u64"), ("holder_rewards_bps", "u64"), ("holder_rewards", "u64"),
    ("creator_fee_unclaimed", "u64"),
]

# Fees kept in the pool by v2 trades / multi_hop_swap are paid out later by a sweep (bucket 0 protocol, 1 creator).
PUMPSWAP_SWEEP_POOL_FEE_EVENT = [
    ("timestamp", "i64"), ("pool", "pubkey"), ("base_mint", "pubkey"), ("quote_mint", "pubkey"),
    ("recipient", "pubkey"), ("payer", "pubkey"), ("amount", "u64"), ("bucket", "u8"),
]

PUMPFUN_SWEEP_BONDING_CURVE_FEE_EVENT = [
    ("timestamp", "i64"), ("mint", "pubkey"), ("bonding_curve", "pubkey"), ("quote_mint", "pubkey"),
    ("recipient", "pubkey"), ("amount", "u64"), ("bucket", "u8"),
]

FEES_SWEEP_BUYBACK_EVENT = [
    ("index", "u8"), ("sol_amount", "u64"), ("token_amount", "u64"), ("destination", "pubkey"),
    ("buyback_vault", "pubkey"), ("mint", "pubkey"),
]

FEES_UPSERT_FEE_TIERS_EVENT = [
    ("timestamp", "i64"), ("admin", "pubkey"), ("fee_config", "pubkey"), ("fee_tiers", ("vec", "fee_tier")),
    ("offset", "u8"),
]

FEES_UPSERT_STABLE_FEE_TIERS_EVENT = [
    ("timestamp", "i64"), ("admin", "pubkey"), ("fee_config", "pubkey"), ("stable_fee_tiers", ("vec", "fee_tier")),
    ("offset", "u8"),
]

_STRUCTS = {
    "shareholder": [("address", "pubkey"), ("share_bps", "u16")],
    "fee_tier": [("market_cap_lamports_threshold", "u128"), ("lp_fee_bps", "u64"), ("protocol_fee_bps", "u64"),
                 ("creator_fee_bps", "u64")],
}

LAYOUTS: dict[str, dict[bytes, tuple[str, list | None]]] = {
    PUMPSWAP_PROGRAM: {
        event_discriminator("BuyEvent"): ("BuyEvent", PUMPSWAP_BUY_EVENT),
        event_discriminator("SellEvent"): ("SellEvent", PUMPSWAP_SELL_EVENT),
        event_discriminator("BoostBuyAndBurnEvent"): ("BoostBuyAndBurnEvent", PUMPSWAP_BOOST_BUY_AND_BURN_EVENT),
        event_discriminator("InitBoostEvent"): ("InitBoostEvent", PUMPSWAP_INIT_BOOST_EVENT),
        event_discriminator("SweepPoolFeeEvent"): ("SweepPoolFeeEvent", PUMPSWAP_SWEEP_POOL_FEE_EVENT),
    },
    PUMPFUN_PROGRAM: {
        event_discriminator("TradeEvent"): ("TradeEvent", PUMPFUN_TRADE_EVENT),
        event_discriminator("SweepBondingCurveFeeEvent"): ("SweepBondingCurveFeeEvent",
                                                           PUMPFUN_SWEEP_BONDING_CURVE_FEE_EVENT),
    },
    PUMP_FEES_PROGRAM: {
        event_discriminator("SweepBuybackEvent"): ("SweepBuybackEvent", FEES_SWEEP_BUYBACK_EVENT),
        event_discriminator("UpsertFeeTiersEvent"): ("UpsertFeeTiersEvent", FEES_UPSERT_FEE_TIERS_EVENT),
        event_discriminator("UpsertStableFeeTiersEvent"): ("UpsertStableFeeTiersEvent",
                                                           FEES_UPSERT_STABLE_FEE_TIERS_EVENT),
    },
}
# Recognised by name only (not needed for swap decoding).
for _name in ("CreatePoolEvent", "DepositEvent", "WithdrawEvent", "ExtendAccountEvent", "CollectCoinCreatorFeeEvent",
              "ClaimCashbackEvent", "InitUserVolumeAccumulatorEvent", "CloseUserVolumeAccumulatorEvent",
              "SyncUserVolumeAccumulatorEvent", "AdminCtoPoolEvent", "UpdateFeeConfigEvent"):
    LAYOUTS[PUMPSWAP_PROGRAM][event_discriminator(_name)] = (_name, None)
for _name in ("CreateEvent", "CompleteEvent", "CompletePumpAmmMigrationEvent", "SetCreatorEvent",
              "CollectCreatorFeeEvent", "PostCompleteBuyEvent", "ExtendAccountEvent"):
    LAYOUTS[PUMPFUN_PROGRAM][event_discriminator(_name)] = (_name, None)


class _Short(Exception):
    pass


@dataclass
class Event:
    program: str
    name: str
    fields: dict[str, Any] = field(default_factory=dict)
    prefix: bool = False          # some tail fields were missing (older layout); they are None
    trailing_bytes: int = 0       # bytes after the last known field (newer layout)

    def __getitem__(self, key: str) -> Any:
        return self.fields[key]

    def get(self, key: str, default: Any = None) -> Any:
        v = self.fields.get(key, default)
        return default if v is None else v


def _read(ty: Any, b: bytes, o: int) -> tuple[Any, int]:
    if isinstance(ty, tuple) and ty[0] == "vec":
        if o + 4 > len(b):
            raise _Short
        n = int.from_bytes(b[o:o + 4], "little")
        o += 4
        out = []
        for _ in range(n):
            v, o = _read(ty[1], b, o)
            out.append(v)
        return out, o
    if ty in _STRUCTS:
        d = {}
        for name, t in _STRUCTS[ty]:
            d[name], o = _read(t, b, o)
        return d, o
    if ty in _INT:
        n, signed = _INT[ty]
        if o + n > len(b):
            raise _Short
        return int.from_bytes(b[o:o + n], "little", signed=signed), o + n
    if ty == "bool":
        if o >= len(b):
            raise _Short
        return b[o] != 0, o + 1
    if ty == "pubkey":
        if o + 32 > len(b):
            raise _Short
        return b58encode(b[o:o + 32]), o + 32
    if ty == "bytes":
        if o + 4 > len(b):
            raise _Short
        n = int.from_bytes(b[o:o + 4], "little")
        if o + 4 + n > len(b):
            raise _Short
        return b[o + 4:o + 4 + n].hex(), o + 4 + n
    if ty == "string":
        if o + 4 > len(b):
            raise _Short
        n = int.from_bytes(b[o:o + 4], "little")
        if o + 4 + n > len(b):
            raise _Short
        return b[o + 4:o + 4 + n].decode("utf-8", "replace"), o + 4 + n
    raise ValueError(f"unknown borsh type {ty!r}")


def decode_fields(layout: list, body: bytes) -> tuple[dict[str, Any], bool, int]:
    """Decode `body` (event bytes after the 8-byte discriminator). Returns (fields, prefix, trailing_bytes)."""
    out: dict[str, Any] = {}
    o = 0
    for i, (name, ty) in enumerate(layout):
        try:
            out[name], o = _read(ty, body, o)
        except _Short:
            if i == 0:
                raise ValueError("event body too short for its first field") from None
            for rest, _ in layout[i:]:
                out[rest] = None
            return out, True, 0
    return out, False, len(body) - o


def decode_event(program: str, data: bytes) -> Event | None:
    """Decode one event. `data` is either the emit_cpi instruction data (tag + discriminator + body) or a bare event
    (discriminator + body). Returns None for an unknown program or discriminator."""
    if data[:8] == EMIT_CPI_TAG:
        data = data[8:]
    entry = LAYOUTS.get(program, {}).get(bytes(data[:8]))
    if entry is None:
        return None
    name, layout = entry
    if layout is None:
        return Event(program, name)
    fields, prefix, trailing = decode_fields(layout, data[8:])
    return Event(program, name, fields, prefix, trailing)
