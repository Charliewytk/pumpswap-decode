import json

import pytest
from conftest import FIXTURES

from pumpswap_decode.constants import DEFAULT_PUBKEY, USDC_MINT, WSOL_MINT
from pumpswap_decode.fees import Fees, decode_fee_config_b64, expected_fees, market_cap, tier_for

SOL = 1_000_000_000


def test_mainnet_fee_config_has_25_tiers_from_125_to_30_bps(fee_config):
    totals = [f.total for _, f in fee_config.tiers]
    assert len(fee_config.tiers) == 25 and len(fee_config.stable_tiers) == 25
    assert totals[0] == 125 and totals[-1] == 30
    assert totals == sorted(totals, reverse=True)                    # never rises with market cap
    assert [t for t, _ in fee_config.tiers] == sorted(t for t, _ in fee_config.tiers)
    assert 2 * totals[0] == 250 and 2 * totals[-1] == 60             # a buy plus a sell
    assert fee_config.flat == Fees(25, 5, 0)


def test_tier_boundaries(fee_config):
    t = fee_config.tiers
    assert tier_for(t, 0) == t[0][1]
    second = t[1][0]
    assert tier_for(t, second - 1) == t[0][1] and tier_for(t, second) == t[1][1]
    assert tier_for(t, 10**30) == t[-1][1]
    assert tier_for(t, 420 * SOL).total == 120 and tier_for(t, 420 * SOL - 1).total == 125


def test_market_cap_uses_signed_effective_reserves():
    supply = 10**15
    assert market_cap(100 * SOL, supply, 10**14) == 1000 * SOL
    assert market_cap(100 * SOL - 5 * SOL, supply, 10**14) < market_cap(100 * SOL, supply, 10**14)


def test_selection_rules(fee_config):
    kw = dict(effective_quote=100 * SOL, base_supply=10**15, base_reserves=10**14)
    assert expected_fees(fee_config, quote_mint=WSOL_MINT, is_pump_pool=False, **kw) == fee_config.flat
    sol = expected_fees(fee_config, quote_mint=WSOL_MINT, is_pump_pool=True, **kw)
    assert sol == tier_for(fee_config.tiers, 1000 * SOL)
    usdc = expected_fees(fee_config, quote_mint=USDC_MINT, is_pump_pool=True, **kw)
    assert usdc == tier_for(fee_config.stable_tiers, 1000 * SOL)
    exotic = expected_fees(fee_config, quote_mint="OtherMint1111111111111111111111111111111111", is_pump_pool=True, **kw)
    assert exotic == fee_config.exotic
    custom = expected_fees(fee_config, quote_mint=WSOL_MINT, is_pump_pool=True, pool_creator_fee_bps=100, **kw)
    assert custom.creator == 100 and custom.lp == sol.lp
    assert expected_fees(fee_config, quote_mint=WSOL_MINT, is_pump_pool=True, coin_creator=DEFAULT_PUBKEY,
                         **kw).creator == 0


def test_fixture_metadata_is_consistent():
    meta = json.loads((FIXTURES / "fee_config_mainnet.json").read_text())
    assert meta["slot"] > 0 and decode_fee_config_b64(meta["data_b64"]).tiers


def test_truncated_account_is_rejected():
    with pytest.raises(ValueError):
        decode_fee_config_b64("AAAA")
