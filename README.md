# pumpswap-decode

A small, dependency-free Python decoder for **PumpSwap** (the `pump_amm` program) and **pump.fun bonding-curve**
swaps on Solana. Give it a transaction from `getTransaction` and it returns normalized swaps: side, base amount,
quote amount actually paid or received, every fee, the pool reserves the trade priced on, and how the fees were
settled. It is read-only tooling for public on-chain data. It never builds, signs or sends a transaction.

It exists because these programs changed in ways that quietly break decoders that look correct. Each trap below has
tests; most are covered by real mainnet transactions (see "Covered by fixtures" for exactly which).

```
pip install -e .            # Python 3.10+, no runtime dependencies
pip install pytest && pytest
```

```python
from pumpswap_decode import decode_transaction

tx = ...  # a getTransaction result: encoding "json" or "jsonParsed", maxSupportedTransactionVersion 1
for swap in decode_transaction(tx)["swaps"]:
    print(swap["venue"], swap["side"], swap["base_amount"], swap["quote_user"], swap["total_fee_bps"])
```

## What it decodes

| Program | Decoded |
| --- | --- |
| PumpSwap `pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA` | `BuyEvent`, `SellEvent`, `BoostBuyAndBurnEvent`, `InitBoostEvent`, `SweepPoolFeeEvent`; arguments of `buy`, `buy_v2`, `buy_exact_quote_in`, `buy_exact_quote_in_v2`, `sell`, `sell_v2`, `multi_hop_swap`, `boost_buy_and_burn`, `init_boost`, `sweep_protocol_fee`, `sweep_creator_fee`, `deposit`, `withdraw`, `create_pool`; the `Pool` account |
| pump.fun `6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P` | `TradeEvent`, `SweepBondingCurveFeeEvent`; arguments of `buy`, `buy_v2`, `buy_v3`, `buy_exact_sol_in`, `buy_exact_quote_in_v2`, `buy_exact_quote_in_v3`, `sell`, `sell_v2`, `sell_v3`, `multi_hop_curve_swap`, the sweeps |
| pump fees `pfeeUxB6jkeY1Hxd7CsFCAjcbHA9rWtchMGdZ6VojVZ` | the PumpSwap `FeeConfig` account (fee tiers); `UpsertFeeTiersEvent`, `UpsertStableFeeTiersEvent`, `SweepBuybackEvent`; arguments of `upsert_fee_tiers`, `upsert_stable_fee_tiers`, `sweep_buyback` |

Every instruction of the three programs is recognised by name. The layouts are hand-written from the public IDLs at
[pump-fun/pump-public-docs](https://github.com/pump-fun/pump-public-docs) commit `2293f9a` (8 Oct 2026). A test
checks them field by field against the pinned IDL snapshot in `tools/idl_pins.json`.

## The traps

### 1. Virtual quote reserves are signed, and negative since 30 Sep 2026

PumpSwap prices on *effective* quote reserves: `pool_quote_vault + virtual_quote_reserves`. `virtual_quote_reserves`
(V) is an `i128`. Boost pools carry V > 0. Since 30 September 2026, V can also be **negative**, because v2 trades
leave their protocol and creator fees in the vault and subtract them from V (trap 4). Reading V as unsigned gives a
number near 2^128. Clamping it at 0 misprices every swap on those pools.

The golden tests recompute every PumpSwap trade from the event's own reserves, using signed V and the program's
integer rounding, and match it exactly. That includes pools with V < 0 and pools where V turns negative mid-sequence.

### 2. `buy` and `buy_exact_quote_in` swap the meaning of two event fields

`BuyEvent` has `quote_amount_in` and `user_quote_amount_in`. Which one is the total the user paid depends on the
instruction (`ix_name` in the event):

| field | `buy`, `buy_v2` | `buy_exact_quote_in`, `buy_exact_quote_in_v2` |
| --- | --- | --- |
| `quote_amount_in` | constant-product quote (CP), before fees | **total the user paid** |
| `user_quote_amount_in` | **total the user paid** | CP |
| `quote_amount_in_with_lp_fee` | CP + LP fee | CP + LP fee |

In both cases, user total = CP + LP + protocol + creator fees. A parser that sums `quote_amount_in` as "SOL spent"
is right for exact-quote-in buys and silently drops every fee on exact-base-out buys. `normalize_buy` maps the
fields by `ix_name` and raises on a name it has not verified. With `strict=False` it infers the mapping from the
fee identity instead. Legs of `multi_hop_swap` are always inferred.

Two smaller details are also reproduced: exact-quote-in buys price `cp - 1`, holding back one unit, and every fee
component is `ceil(amount * bps / 10_000)`.

### 3. The fee schedule: 25 market-cap tiers, 125 → 30 bps per swap

For pump pools (pools created by migration), the rates come from the `FeeConfig` account of the pump fees program.
Mainnet has 25 SOL tiers. The per-swap total falls from **125 bps** below 420 SOL market cap to **30 bps** at or
above 98,240 SOL. That is **250 → 60 bps for a round trip** (a buy plus a sell). The rules, as checked against mainnet:

- **non-pump pools:** flat fees, 25 / 5 / 0 bps (LP / protocol / creator);
- **SOL-quoted pump pools:** SOL tiers, by market cap = effective quote × base supply / base reserves;
- **USDC-quoted pools:** the parallel 25-tier stable table, in USDC units;
- **other quote mints:** the "exotic" fee set;
- **per-pool creator fee:** a configured creator fee replaces the tier's creator part;
- **boost buybacks:** they pay nothing (trap 5).

The golden test recomputes the fee rates of every single-hop PumpSwap fixture swap from a FeeConfig snapshot taken
the same day and matches all of them. Multi-hop legs and pump.fun curve fee rates are not checked against the tiers.

### 4. v2 trades keep fees in the pool, and sweeps pay them out later

`buy_v2`, `buy_exact_quote_in_v2`, `sell_v2` and `multi_hop_swap` (PumpSwap docs commit `8cda1fa`, 7 Oct 2026)
charge the same fees as v1. However, they keep the protocol fee and the creator fee in the pool's quote vault
instead of paying them out:

- **the buyback part** of the protocol fee is still paid out in the trade;
- **the event** reports the amounts charged, with `protocol_fee_recipient` set to the zero key;
- **V** goes down by the kept amount;
- **a later `sweep_protocol_fee` / `sweep_creator_fee`** emits `SweepPoolFeeEvent`, pays the fees out and adds the
  amount back to V.

Per trade, the decoder reports:

- `fee_settlement`: one of `paid_in_trade`, `kept_in_pool`, `kept_on_curve` or `no_fee`;
- `fees_kept`: protocol − buyback + creator;
- `quote_vault_delta`: the vault's real change, kept fees included;
- `virtual_quote_reserves_after`: V − kept.

Sweeps are listed separately in `fee_sweeps` as payouts. Count fee income at the trade or at the sweep, never both.
The golden tests check the vault delta against the transaction's own token balances. They also check that a
`sell_v2`'s V-after equals the V the same pool's next trade priced on. pump.fun's v3 curve trades work the same way
on the bonding curve.

### 5. Boost buybacks look like buys

`boost_buy_and_burn` is a protocol instruction. It spends a boosted pool's boost vault on a fee-free buy, then burns
the tokens. It emits an ordinary `BuyEvent` (`ix_name` `buy_exact_quote_in`, all fees zero) followed by a
`BoostBuyAndBurnEvent` for the same pool. That buy is not demand. The decoder flags exactly that `BuyEvent`
(`is_boost_buyback=True`), matched by pool. It does not flag the whole transaction, so an organic trade in the same
transaction keeps its own label. `organic_swaps()` drops these buys.

Do not confuse this with `buyback_fee` in trade events. That field is the share of the protocol fee routed to
buybacks (`buyback_fee_basis_points` = 5000 means half of it). It is part of `protocol_fee`, not an extra charge.

### 6. pump.fun curves are not always SOL-quoted

`TradeEvent.sol_amount` is 0 on curves paired with USDC or another coin. The trade is in `quote_amount`,
`virtual_quote_reserves` and `real_quote_reserves`, which equal the `sol_*` fields on SOL curves. The decoder uses
the quote fields. On the curve, `quote_amount` is always the amount into or out of the curve, for every buy
variant. Exact-in buys price `amount - 1`, and exact-token buys pay `ceil(v_quote * t / (v_token - t))`.

## Golden test set

`tests/fixtures/golden/` holds 22 mainnet transactions, all decoded from public signatures on 10 Oct 2026, about
86 KB in total. Each fixture stores:

- the signature and slot;
- a **trimmed record**: the ordered PumpSwap / pump.fun instructions with only their pool and mint accounts, the
  raw emit_cpi event bytes, and the token balances of the pools involved;
- the decoder's expected output, without trader addresses as fields.

Trader and coin-creator keys are not stored as separate fields. They are still present inside the raw event bytes,
because they are part of the public on-chain event, and anyone who decodes a fixture will see them. The fixture
transactions were chosen from recent public mainnet activity of unrelated traders.

Rebuilding the record from the full transaction is `tools/make_fixture.py`. It needs your own RPC endpoint in
`SOLANA_RPC_URL`.

Matching a frozen output proves little on its own, so every fixture is also checked against independent
invariants, listed in `tests/test_golden.py`:

- pricing recomputed from reserves;
- fee identities;
- vault balance deltas;
- fee tiers recomputed from the FeeConfig snapshot;
- curve math;
- instruction slippage limits.

Covered by fixtures:

| Venue | Covered |
| --- | --- |
| PumpSwap | `buy`, `buy_exact_quote_in`, `buy_v2`, `sell`, `sell_v2`, `boost_buy_and_burn` (×3), `multi_hop_swap` (pool leg); V > 0, V = 0 and V < 0; SOL and USDC pools; pump and non-pump pools |
| pump.fun | `buy`, `buy_exact_sol_in`, `buy_exact_quote_in_v2`, `sell_v3`, the curve leg of a multi-hop route, one mayhem-mode coin |
| Accounts | `Pool` (V < 0, boosted with kept fees, plain), `FeeConfig` |

**Decoded from the IDL only (no golden fixture yet):**

- `buy_exact_quote_in_v2` on PumpSwap (no golden fixture yet);
- `SweepPoolFeeEvent` / `SweepBondingCurveFeeEvent` (synthetic tests only);
- all pump fees program instructions and events (`upsert_fee_tiers`, `upsert_stable_fee_tiers`, `sweep_buyback`);
- pump.fun `buy_v2`, `buy_v3`, `buy_exact_quote_in_v3`, `sell`, `sell_v2` arguments;
- `deposit`, `withdraw`, `create_pool`, `init_boost`;
- non-SOL, non-USDC ("exotic") quote pools and per-pool creator fees (unit tests only).

**Known gaps:**

- zero-fee trades on mayhem-mode curves do not reproduce from the event reserves and are not modelled;
- multi-hop legs only charge the fees of their hop, so they are excluded from the tier check;
- the "pump pool" test in the fee check uses `base_supply > 0` from the event;
- cashback coins have no fixture, and the fee identity ignores the `cashback` amount;
- the per-pool creator fee is applied without checking the global `creatorFeeConfigurable` switch.

## Program upgrades

`tools/idl_diff.py` compares the pinned IDL snapshot with the latest published IDLs:

```
python tools/idl_diff.py                 # vs pump-public-docs@main
python tools/idl_diff.py --ref <commit>
python tools/idl_diff.py --write --ref <commit>   # re-pin after updating the layouts
```

It exits 1 when anything tracked changed:

- an IDL file hash;
- any instruction, event or account discriminator added, removed or changed;
- the fields of a type or instruction this decoder hand-codes.

When a struct gained trailing fields, it says so. Run it on a schedule as an upgrade alarm. The decoder itself is
also tolerant of upgrades:

- an older, shorter event decodes with the missing tail fields set to `None`;
- a newer, longer event reports `trailing_bytes` instead of shifting fields.

## Comparison

These facts were checked on 10 Oct 2026.

- [Carbon](https://github.com/sevenlabs-hq/carbon) (Rust) has a `pump-swap-decoder`. Its last commit touching it is
  from 4 Sep 2026. At that commit it has no `buy_v2` / `sell_v2` / `buy_exact_quote_in_v2`, `multi_hop_swap` or
  `boost_buy_and_burn` instructions. Its `Pool`, `BuyEvent` and `SellEvent` have no `virtual_quote_reserves` field,
  signed or otherwise.
- [sol-parser-sdk-python](https://github.com/0xfnzero/sol-parser-sdk-python) is the closest Python project we found.
  Its coverage of the cases above was not audited here.

## Layout

```
src/pumpswap_decode/   events.py  instructions.py  accounts.py  semantics.py  fees.py  pricing.py  tx.py
tests/                 unit tests + golden tests; fixtures/ (golden/, FeeConfig and Pool account snapshots)
tools/                 make_fixture.py, idl_diff.py, idl_pins.json
```

## License

Apache-2.0. See `LICENSE`.
