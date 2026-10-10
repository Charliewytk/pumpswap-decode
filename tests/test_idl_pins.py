"""Offline check: the hand-written layouts match the pinned public IDLs (tools/idl_pins.json).

tools/idl_diff.py compares the pins with the latest published IDLs (network); this test makes sure the code and the
pins agree, so a re-pin without a layout update fails here.
"""
import json
import pathlib

from pumpswap_decode import constants as C
from pumpswap_decode.accounts import POOL_DISCRIMINATOR, POOL_LAYOUT
from pumpswap_decode.events import LAYOUTS, _STRUCTS
from pumpswap_decode.instructions import INSTRUCTION_ARGS

PINS = json.loads((pathlib.Path(__file__).resolve().parents[1] / "tools" / "idl_pins.json").read_text())["idls"]
PROGRAM_IDL = {C.PUMPSWAP_PROGRAM: "pump_amm", C.PUMPFUN_PROGRAM: "pump", C.PUMP_FEES_PROGRAM: "pump_fees"}
STRUCT_NAMES = {"shareholder": "Shareholder", "fee_tier": "FeeTier"}


def tok(ty):
    if isinstance(ty, tuple):
        return [ty[0], STRUCT_NAMES[ty[1]]]
    return ty


def test_pins_match_constants_and_commit():
    assert PINS["pump_amm"]["address"] == C.PUMPSWAP_PROGRAM
    assert PINS["pump"]["address"] == C.PUMPFUN_PROGRAM
    assert PINS["pump_fees"]["address"] == C.PUMP_FEES_PROGRAM
    root = json.loads((pathlib.Path(__file__).resolve().parents[1] / "tools" / "idl_pins.json").read_text())
    assert root["ref"] == C.IDL_REFERENCE_COMMIT


def test_instruction_names_and_discriminators():
    for program, names in C.IX_NAMES.items():
        pinned = PINS[PROGRAM_IDL[program]]["instructions"]
        assert sorted(names.values()) == sorted(pinned)
        for disc, name in names.items():
            assert disc.hex() == pinned[name], name


def test_instruction_argument_layouts():
    for program, table in INSTRUCTION_ARGS.items():
        pinned = PINS[PROGRAM_IDL[program]]["instruction_args"]
        assert set(table) == set(pinned)
        for name, layout in table.items():
            assert [[n, tok(t)] for n, t in layout] == pinned[name], name


def test_event_discriminators_and_layouts():
    for program, table in LAYOUTS.items():
        pinned = PINS[PROGRAM_IDL[program]]
        for disc, (name, layout) in table.items():
            assert pinned["events"][name] == disc.hex(), name
            if layout is not None:
                assert [[n, tok(t)] for n, t in layout] == pinned["types"][name], name


def test_struct_and_account_layouts():
    assert [[n, t] for n, t in _STRUCTS["shareholder"]] == PINS["pump"]["types"]["Shareholder"]
    assert [[n, t] for n, t in _STRUCTS["fee_tier"]] == PINS["pump_fees"]["types"]["FeeTier"]
    assert [[n, t] for n, t, _ in POOL_LAYOUT] == PINS["pump_amm"]["types"]["Pool"]
    assert POOL_DISCRIMINATOR.hex() == PINS["pump_amm"]["accounts"]["Pool"]


def test_fee_config_layout_matches_decoder_assumption():
    fields = PINS["pump_fees"]["types"]["FeeConfig"]
    assert [f[1] for f in fields] == ["u8", "pubkey", "u64", "u64", "u64", ["vec", "FeeTier"], ["vec", "FeeTier"],
                                      "u64", "u64", "u64"]
