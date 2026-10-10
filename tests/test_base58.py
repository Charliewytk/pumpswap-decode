import os

import pytest

from pumpswap_decode._b58 import b58decode, b58encode
from pumpswap_decode.constants import PUMPSWAP_PROGRAM, WSOL_MINT


def test_roundtrip_random():
    for n in (0, 1, 31, 32, 64):
        for _ in range(20):
            b = os.urandom(n)
            assert b58decode(b58encode(b)) == b


def test_leading_zeros_and_known_keys():
    assert b58encode(b"\0" * 32) == "1" * 32
    assert len(b58decode(PUMPSWAP_PROGRAM)) == 32
    assert b58encode(b58decode(WSOL_MINT)) == WSOL_MINT


def test_rejects_invalid():
    with pytest.raises(ValueError):
        b58decode("0OIl")
