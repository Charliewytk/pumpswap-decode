from helpers import encode_fields

from pumpswap_decode.constants import PUMP_FEES_PROGRAM, PUMPFUN_PROGRAM, PUMPSWAP_PROGRAM, instruction_discriminator
from pumpswap_decode.instructions import INSTRUCTION_ARGS, decode_instruction


def test_every_layout_roundtrips():
    for program, table in INSTRUCTION_ARGS.items():
        for name, layout in table.items():
            vals = {}
            for i, (arg, ty) in enumerate(layout):
                if ty in ("u8", "u16", "u64"):
                    vals[arg] = i + 1
                elif ty == "bool":
                    vals[arg] = True
            data = instruction_discriminator(name) + encode_fields(layout, vals)
            ix = decode_instruction(program, data)
            assert ix.name == name and not ix.prefix and ix.trailing_bytes == 0, (program, name)
            for arg, v in vals.items():
                assert ix.args[arg] == v


def test_optional_trailing_arg_may_be_missing():
    data = instruction_discriminator("buy") + (5).to_bytes(8, "little") + (9).to_bytes(8, "little")
    ix = decode_instruction(PUMPSWAP_PROGRAM, data)
    assert ix.prefix and ix.args == {"base_amount_out": 5, "max_quote_amount_in": 9, "track_volume": None}


def test_fee_program_upsert_fee_tiers():
    tiers = [{"market_cap_lamports_threshold": 420 * 10**9, "lp_fee_bps": 20, "protocol_fee_bps": 5,
              "creator_fee_bps": 95}]
    layout = INSTRUCTION_ARGS[PUMP_FEES_PROGRAM]["upsert_fee_tiers"]
    ix = decode_instruction(PUMP_FEES_PROGRAM, instruction_discriminator("upsert_fee_tiers") +
                            encode_fields(layout, {"fee_tiers": tiers, "offset": 1}))
    assert ix.args["fee_tiers"] == tiers and ix.args["offset"] == 1


def test_same_name_differs_per_program():
    data = instruction_discriminator("buy") + bytes(16)
    assert set(decode_instruction(PUMPFUN_PROGRAM, data).args) == {"amount", "max_sol_cost", "track_volume",
                                                                  "partial_fill"}
    assert decode_instruction("Unknown11111111111111111111111111111111111", data) is None
