"""Build a golden fixture from a public mainnet transaction signature.

    SOLANA_RPC_URL=<your RPC endpoint> python tools/make_fixture.py --name buy_v_pos --note "..." <signature>
    python tools/make_fixture.py --name buy_v_pos --tx-json saved_getTransaction_result.json

The fixture stores the trimmed record (see pumpswap_decode.tx.trim_transaction) and the decoder's output with
trader addresses removed. Review the decoded output by hand before committing it: the golden tests also check
every fixture against independent invariants (pricing recomputed from reserves, fee identities, vault balance
deltas, fee tiers), so a wrong decode does not pass just because it was frozen.

`--fee-config` fetches the current PumpSwap FeeConfig account into tests/fixtures/fee_config_mainnet.json.
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import pathlib
import sys
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from pumpswap_decode.tx import decode_trimmed, trim_transaction  # noqa: E402

GOLDEN = ROOT / "tests" / "fixtures" / "golden"
FEE_CONFIG_ACCOUNT = "5PHirr8joyTMp9JMm6nW7hNDVyEYdkzDqazxPD7RaTjx"  # PDA ["fee_config", pump_amm] of the fees program
PRIVATE_KEYS = {"trader"}


def rpc(method: str, params: list):
    url = os.environ.get("SOLANA_RPC_URL")
    if not url:
        sys.exit("set SOLANA_RPC_URL to an RPC endpoint you are allowed to use")
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode()
    req = urllib.request.Request(url, body, {"content-type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        j = json.load(r)
    if "error" in j:
        raise RuntimeError(j["error"])
    return j["result"]


def get_transaction(sig: str) -> dict:
    tx = rpc("getTransaction", [sig, {"encoding": "json", "maxSupportedTransactionVersion": 1,
                                      "commitment": "confirmed"}])
    if tx is None:
        raise RuntimeError(f"transaction not found: {sig}")
    return tx


def public_view(decoded: dict) -> dict:
    out = dict(decoded)
    out["swaps"] = [{k: v for k, v in s.items() if k not in PRIVATE_KEYS} for s in decoded["swaps"]]
    return out


def build(tx: dict, name: str, note: str, context: dict | None = None) -> dict:
    record = trim_transaction(tx)
    return {"name": name, "note": note, "signature": record["signature"], "slot": record["slot"],
            "block_time": record["block_time"], "context": context or {}, "record": record,
            "expected": public_view(decode_trimmed(record, strict=False))}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("signature", nargs="?")
    ap.add_argument("--tx-json", help="a saved getTransaction result instead of fetching")
    ap.add_argument("--name")
    ap.add_argument("--note", default="")
    ap.add_argument("--context", default="{}", help="JSON with facts the record cannot carry (see tests)")
    ap.add_argument("--fee-config", action="store_true")
    ap.add_argument("--refresh", action="store_true",
                    help="rebuild `expected` of every stored fixture from its stored record (after a decoder change; "
                         "review the git diff)")
    a = ap.parse_args()
    if a.fee_config:
        res = rpc("getAccountInfo", [FEE_CONFIG_ACCOUNT, {"encoding": "base64"}])
        out = {"account": FEE_CONFIG_ACCOUNT, "slot": res["context"]["slot"], "data_b64": res["value"]["data"][0]}
        base64.b64decode(out["data_b64"])
        (ROOT / "tests" / "fixtures" / "fee_config_mainnet.json").write_text(json.dumps(out, indent=1) + "\n")
        return
    if a.refresh:
        for path in sorted(GOLDEN.glob("*.json")):
            fx = json.loads(path.read_text())
            fx["expected"] = public_view(decode_trimmed(fx["record"], strict=False))
            path.write_text(json.dumps(fx, indent=1) + "\n")
        return
    if not a.name or not (a.signature or a.tx_json):
        ap.error("need --name and a signature or --tx-json")
    tx = json.loads(pathlib.Path(a.tx_json).read_text()) if a.tx_json else get_transaction(a.signature)
    fx = build(tx, a.name, a.note, json.loads(a.context))
    GOLDEN.mkdir(parents=True, exist_ok=True)
    path = GOLDEN / f"{a.name}.json"
    path.write_text(json.dumps(fx, indent=1, sort_keys=False) + "\n")
    print(path.relative_to(ROOT), len(fx["expected"]["swaps"]), "swaps")


if __name__ == "__main__":
    main()
