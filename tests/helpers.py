"""Borsh encoder used by the tests to build synthetic events and instructions from the decoder's own layouts."""
from pumpswap_decode._b58 import b58decode
from pumpswap_decode.events import _STRUCTS

_INT = {"u8": (1, False), "u16": (2, False), "u32": (4, False), "u64": (8, False), "i64": (8, True),
        "u128": (16, False), "i128": (16, True)}
ZERO_KEY = "11111111111111111111111111111111"


def enc(ty, v) -> bytes:
    if isinstance(ty, tuple) and ty[0] == "vec":
        return len(v).to_bytes(4, "little") + b"".join(enc(ty[1], x) for x in v)
    if ty in _STRUCTS:
        return b"".join(enc(t, v[n]) for n, t in _STRUCTS[ty])
    if ty in _INT:
        n, s = _INT[ty]
        return int(v).to_bytes(n, "little", signed=s)
    if ty == "bool":
        return b"\x01" if v else b"\x00"
    if ty == "pubkey":
        return b58decode(v).rjust(32, b"\0")
    if ty in ("string", "bytes"):
        raw = v.encode() if ty == "string" else bytes.fromhex(v)
        return len(raw).to_bytes(4, "little") + raw
    raise ValueError(ty)


def default(ty):
    if isinstance(ty, tuple):
        return []
    return {"bool": False, "pubkey": ZERO_KEY, "string": "", "bytes": ""}.get(ty, 0)


def encode_fields(layout, values: dict) -> bytes:
    return b"".join(enc(t, values.get(n, default(t))) for n, t in layout)
