import base64
import json

import pytest
from conftest import FIXTURES

from pumpswap_decode.accounts import decode_pool, pool_effective_quote, pool_real_quote

POOLS = json.loads((FIXTURES / "pool_accounts_mainnet.json").read_text())


def test_pool_accounts_decode():
    for entry in POOLS["accounts"]:
        p = decode_pool(base64.b64decode(entry["data_b64"]))
        assert p["base_mint"] and p["quote_mint"] and p["layout_bytes"] <= p["account_bytes"]
        assert p["virtual_quote_reserves"] == entry["expect"]["virtual_quote_reserves"]
        assert p["creator_fee_bps"] == entry["expect"]["creator_fee_bps"]


def test_negative_virtual_reserves_pool_is_present():
    assert any(e["expect"]["virtual_quote_reserves"] < 0 for e in POOLS["accounts"])


def test_old_short_pool_reads_missing_tail_as_zero():
    full = base64.b64decode(POOLS["accounts"][0]["data_b64"])
    short = full[:8 + 1 + 2 + 6 * 32 + 8 + 32 + 2]                   # before virtual_quote_reserves existed
    p = decode_pool(short)
    assert p["virtual_quote_reserves"] == 0 and p["protocol_fees"] == 0 and p["base_mint"]


def test_reserve_identities():
    pool = {"virtual_quote_reserves": -30, "protocol_fees": 20, "creator_fees": 10}
    assert pool_effective_quote(pool, 110) == 80 and pool_real_quote(pool, 110) == 80


def test_wrong_discriminator():
    with pytest.raises(ValueError):
        decode_pool(b"\x00" * 300)
