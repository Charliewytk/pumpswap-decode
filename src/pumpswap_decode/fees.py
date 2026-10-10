"""PumpSwap fee schedule: the FeeConfig account and tier selection.

PumpSwap charges three fees per swap (LP, protocol, coin creator), in basis points. For pools created by pump.fun
migrations ("pump pools") the rates come from a market-cap tier table in the FeeConfig account owned by the pump fees
program. Mainnet has 25 SOL tiers: the per-swap total falls from 125 bps (below 420 SOL market cap) to 30 bps (at or above
98,240 SOL), i.e. 250 to 60 bps for a buy plus a sell. USDC-quoted pools use a parallel 25-tier table in USDC units.

Selection rules (mirroring the public pump-swap-sdk `computeFeesBps`):

* not a pump pool                        -> flat fees (mainnet: lp 25, protocol 5, creator 0)
* quote mint SOL / WSOL / native-2022    -> SOL tiers, by market cap in lamports
* quote mint USDC                        -> stable tiers, by market cap in USDC base units
* any other quote mint                   -> exotic fees (flat fees when the exotic set is all zero)
* pool with a configured creator fee     -> that creator fee replaces the tier's creator part
* coin_creator == default pubkey         -> creator fee 0

market cap = effective_quote_reserves * base_supply // base_reserves, where effective quote = vault + signed V.

Boost buy-and-burn swaps pay no fee at all (see tx.py); they are not tier lookups.
"""
from __future__ import annotations

import base64
from dataclasses import dataclass, field

from .constants import DEFAULT_PUBKEY, SOL_LIKE_QUOTE_MINTS, STABLE_QUOTE_MINTS

BPS = 10_000
MAYHEM_TOTAL_SUPPLY = 1_000_000_000_000_000  # mayhem-mode pools price market cap on a fixed supply


@dataclass(frozen=True)
class Fees:
    lp: int
    protocol: int
    creator: int

    @property
    def total(self) -> int:
        return self.lp + self.protocol + self.creator

    def is_zero(self) -> bool:
        return self.total == 0

    def as_tuple(self) -> tuple[int, int, int]:
        return (self.lp, self.protocol, self.creator)


@dataclass
class FeeConfig:
    flat: Fees
    tiers: list[tuple[int, Fees]]                        # (market cap threshold, fees), ascending
    stable_tiers: list[tuple[int, Fees]] = field(default_factory=list)
    exotic: Fees = Fees(0, 0, 0)


def _u(d: bytes, o: int, n: int) -> int:
    if o + n > len(d):
        raise ValueError("FeeConfig account too short")
    return int.from_bytes(d[o:o + n], "little")


def decode_fee_config(data: bytes) -> FeeConfig:
    """FeeConfig account: 8 disc | u8 bump | 32 admin | flat Fees | vec<(u128, Fees)> | vec<(u128, Fees)> | exotic."""
    o = 8 + 1 + 32

    def fees(o: int) -> tuple[Fees, int]:
        return Fees(_u(data, o, 8), _u(data, o + 8, 8), _u(data, o + 16, 8)), o + 24

    def tiers(o: int) -> tuple[list[tuple[int, Fees]], int]:
        n, o = _u(data, o, 4), o + 4
        out = []
        for _ in range(n):
            threshold = _u(data, o, 16)
            f, o = fees(o + 16)
            out.append((threshold, f))
        return out, o

    flat, o = fees(o)
    sol, o = tiers(o)
    stable, o = tiers(o) if o + 4 <= len(data) else ([], o)
    exotic = fees(o)[0] if o + 24 <= len(data) else Fees(0, 0, 0)
    return FeeConfig(flat, sol, stable, exotic)


def decode_fee_config_b64(text: str) -> FeeConfig:
    return decode_fee_config(base64.b64decode(text))


def market_cap(effective_quote: int, base_supply: int, base_reserves: int, is_mayhem: bool = False) -> int:
    if base_reserves <= 0:
        raise ValueError("base reserves must be positive")
    supply = MAYHEM_TOTAL_SUPPLY if is_mayhem else base_supply
    return effective_quote * supply // base_reserves


def tier_for(tiers: list[tuple[int, Fees]], mcap: int) -> Fees:
    """Highest tier whose threshold is <= mcap; below the first threshold, the first tier."""
    if not tiers:
        raise ValueError("empty tier table")
    chosen = tiers[0][1]
    for threshold, f in tiers:
        if mcap >= threshold:
            chosen = f
        else:
            break
    return chosen


def expected_fees(fc: FeeConfig, *, quote_mint: str, is_pump_pool: bool, effective_quote: int, base_supply: int,
                  base_reserves: int, is_mayhem: bool = False, pool_creator_fee_bps: int | None = None,
                  coin_creator: str | None = None) -> Fees:
    """Fees (bps) the program should charge for a swap against this pre-trade pool state."""
    if not is_pump_pool:
        f = fc.flat
    else:
        if quote_mint in SOL_LIKE_QUOTE_MINTS:
            f = tier_for(fc.tiers, market_cap(effective_quote, base_supply, base_reserves, is_mayhem))
        elif quote_mint in STABLE_QUOTE_MINTS:
            f = tier_for(fc.stable_tiers or fc.tiers, market_cap(effective_quote, base_supply, base_reserves, is_mayhem))
        else:
            f = fc.flat if fc.exotic.is_zero() else fc.exotic
    if pool_creator_fee_bps:
        f = Fees(f.lp, f.protocol, pool_creator_fee_bps)
    if coin_creator == DEFAULT_PUBKEY:
        f = Fees(f.lp, f.protocol, 0)
    return f
