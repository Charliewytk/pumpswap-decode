"""Program IDs, discriminators and well-known mints. All values are public on-chain constants."""
from __future__ import annotations

import hashlib

PUMPSWAP_PROGRAM = "pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA"  # pump_amm ("PumpSwap")
PUMPFUN_PROGRAM = "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P"  # pump (bonding curve)
PUMP_FEES_PROGRAM = "pfeeUxB6jkeY1Hxd7CsFCAjcbHA9rWtchMGdZ6VojVZ"  # owns the PumpSwap FeeConfig account

WSOL_MINT = "So11111111111111111111111111111111111111112"
NATIVE_MINT_2022 = "9pan9bMn5HatX4EJdBwg9VgCa7Uz5HL8N1m5D3NdXejP"
DEFAULT_PUBKEY = "11111111111111111111111111111111"
USDC_MINT = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"

SOL_LIKE_QUOTE_MINTS = frozenset({DEFAULT_PUBKEY, WSOL_MINT, NATIVE_MINT_2022})
STABLE_QUOTE_MINTS = frozenset({USDC_MINT})

# Anchor `emit_cpi!` wraps every event in a self-invoked instruction whose data starts with this 8-byte tag
# (the little-endian bytes of sha256("anchor:event")[:8] read as a u64).
EMIT_CPI_TAG = bytes([228, 69, 165, 46, 81, 203, 154, 29])

# Layouts were checked against pump-fun/pump-public-docs, idl/pump_amm.json and idl/pump.json at this commit.
IDL_REFERENCE_COMMIT = "2293f9a66c654e9fe82dc5e8f4618538f24bb35f"


def instruction_discriminator(name: str) -> bytes:
    """Anchor instruction discriminator: sha256("global:<name>")[:8]."""
    return hashlib.sha256(f"global:{name}".encode()).digest()[:8]


def event_discriminator(name: str) -> bytes:
    """Anchor event discriminator: sha256("event:<Name>")[:8]."""
    return hashlib.sha256(f"event:{name}".encode()).digest()[:8]


# Every instruction name in the pinned IDLs (names only; argument layouts for the swap-relevant ones are in
# instructions.py). tests/test_idl_pins.py checks these lists against tools/idl_pins.json.
PUMPSWAP_INSTRUCTIONS = (
    "admin_cto_pool", "admin_update_token_incentives", "boost_buy_and_burn", "buy", "buy_exact_quote_in",
    "buy_exact_quote_in_v2", "buy_v2", "claim_cashback", "claim_token_incentives",
    "close_user_volume_accumulator", "collect_coin_creator_fee", "create_config", "create_pool", "deposit",
    "disable", "extend_account", "init_boost", "init_user_volume_accumulator", "migrate_pool_coin_creator",
    "multi_hop_swap", "sell", "sell_v2", "set_boost_authority", "set_coin_creator", "set_reserved_fee_recipients",
    "sweep_creator_fee", "sweep_protocol_fee", "sync_user_volume_accumulator", "toggle_boost",
    "toggle_cashback_enabled", "toggle_mayhem_mode", "transfer_creator_fees_to_pump",
    "transfer_creator_fees_to_pump_v2", "update_admin", "update_buyback_config", "update_creator_fee_config",
    "update_fee_config", "withdraw",
)
PUMPFUN_INSTRUCTIONS = (
    "add_quote_control_mint", "add_quote_mint", "admin_cto", "admin_set_idl_authority",
    "admin_update_token_incentives", "buy", "buy_exact_quote_in_v2", "buy_exact_quote_in_v3", "buy_exact_sol_in",
    "buy_v2", "buy_v3", "claim_cashback", "claim_cashback_v2", "claim_token_incentives",
    "close_user_volume_accumulator", "collect_creator_fee", "collect_creator_fee_v2", "create", "create_v2",
    "distribute_creator_fees", "distribute_creator_fees_v2", "distribute_fee_to_holders", "extend_account",
    "get_minimum_distributable_fee", "init_user_volume_accumulator", "initialize", "initialize_quote_control",
    "migrate", "migrate_bonding_curve_creator", "migrate_v2", "multi_hop_curve_swap", "remove_quote_control_mint",
    "remove_quote_mint", "sell", "sell_v2", "sell_v3", "set_creator", "set_max_curve_depth",
    "set_mayhem_virtual_params", "set_metaplex_creator", "set_params", "set_quote_control_admin",
    "set_quote_control_mint_reserves", "set_quote_control_reserves_admin", "set_reserved_fee_recipients",
    "set_virtual_quote_reserves", "sweep_creator_fee", "sweep_protocol_fee", "sync_user_volume_accumulator",
    "toggle_cashback_enabled", "toggle_create_v2", "toggle_mayhem_mode", "update_buyback_config",
    "update_creator_fee_config", "update_global_authority", "update_holder_reward_config",
)
PUMP_FEES_INSTRUCTIONS = (
    "admin_cto_sharing_config", "claim_social_fee_pda", "claim_social_fee_pda_v2", "crank_donation_fee_pda",
    "create_donation_fee_pda", "create_fee_sharing_config", "create_social_fee_pda", "extend_fee_config",
    "get_fees", "get_fees_with_quote_mint", "initialize_buyback", "initialize_fee_config",
    "initialize_fee_program_global", "revoke_fee_sharing_authority", "set_authority", "set_claim_rate_limit",
    "set_disable_flags", "set_exotic_flat_fees", "set_social_claim_authority", "sweep_buyback",
    "transfer_fee_sharing_authority", "update_admin", "update_buyback_authority",
    "update_buyback_claim_rate_limit", "update_fee_config", "update_fee_shares", "update_fee_shares_v2",
    "update_stable_fee_config", "upsert_fee_tiers", "upsert_stable_fee_tiers",
)

IX_NAMES = {
    PUMPSWAP_PROGRAM: {instruction_discriminator(n): n for n in PUMPSWAP_INSTRUCTIONS},
    PUMPFUN_PROGRAM: {instruction_discriminator(n): n for n in PUMPFUN_INSTRUCTIONS},
    PUMP_FEES_PROGRAM: {instruction_discriminator(n): n for n in PUMP_FEES_INSTRUCTIONS},
}
