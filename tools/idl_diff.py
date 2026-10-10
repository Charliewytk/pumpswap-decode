"""Detect PumpSwap / pump.fun / pump fees program upgrades by diffing the public IDLs against a pinned snapshot.

    python tools/idl_diff.py                      # compare tools/idl_pins.json with pump-public-docs @ main
    python tools/idl_diff.py --ref <commit>       # ... with a specific commit
    python tools/idl_diff.py --idl-dir <dir>      # ... with local copies (pump_amm.json, pump.json, pump_fees.json)
    python tools/idl_diff.py --write --ref <commit>   # re-pin (after updating the decoder layouts)

Exit code 0 when nothing tracked changed, 1 when anything changed (file hash, any instruction / event / account
discriminator added, removed or changed, or the fields of a type or instruction this decoder hand-codes). That makes
it usable as a scheduled CI job: a failure means "a program upgrade landed in the public IDL; re-check the layouts".

The IDLs are fetched from the public GitHub repository pump-fun/pump-public-docs; nothing else is contacted.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import sys
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parents[1]
PINS = ROOT / "tools" / "idl_pins.json"
REPO = "pump-fun/pump-public-docs"
RAW = "https://raw.githubusercontent.com/{repo}/{ref}/idl/{name}.json"
IDLS = ("pump_amm", "pump", "pump_fees")

# Types whose layouts the decoder hand-codes (events.py, accounts.py, fees.py).
TRACKED_TYPES = {
    "pump_amm": ["BuyEvent", "SellEvent", "BoostBuyAndBurnEvent", "InitBoostEvent", "SweepPoolFeeEvent", "Pool"],
    "pump": ["TradeEvent", "Shareholder", "SweepBondingCurveFeeEvent"],
    "pump_fees": ["FeeConfig", "FeeTier", "Fees", "SweepBuybackEvent", "UpsertFeeTiersEvent",
                  "UpsertStableFeeTiersEvent"],
}
SCALAR_WRAPPERS = {"OptionBool": "bool", "OptionU64": "u64"}  # single-field structs in these IDLs


def norm_type(ty, types: dict):
    """IDL type -> compact token: primitives as-is, OptionBool/OptionU64 -> bool/u64, vec<T> -> ["vec", T]."""
    if isinstance(ty, str):
        return ty
    if "defined" in ty:
        name = ty["defined"]["name"] if isinstance(ty["defined"], dict) else ty["defined"]
        return SCALAR_WRAPPERS.get(name, name)
    if "vec" in ty:
        return ["vec", norm_type(ty["vec"], types)]
    if "option" in ty:
        return ["option", norm_type(ty["option"], types)]
    if "array" in ty:
        return ["array", norm_type(ty["array"][0], types), ty["array"][1]]
    return json.dumps(ty, sort_keys=True)


def flatten_fields(type_name: str, types: dict, prefix: str = "") -> list[list]:
    """Struct fields as [name, token] pairs, nested non-wrapper structs inlined."""
    t = types[type_name]["type"]
    out: list[list] = []
    for f in t.get("fields", []):
        if not isinstance(f, dict):
            continue
        tok = norm_type(f["type"], types)
        if isinstance(tok, str) and tok in types and types[tok]["type"].get("kind") == "struct" \
                and tok not in SCALAR_WRAPPERS:
            out.extend(flatten_fields(tok, types, prefix))
        else:
            out.append([prefix + f["name"], tok])
    return out


def summarize(raw_bytes: bytes, idl_name: str, tracked_ix: dict[str, list[str]] | None = None) -> dict:
    idl = json.loads(raw_bytes)
    types = {t["name"]: t for t in idl.get("types", [])}
    disc = lambda items: {x["name"]: bytes(x["discriminator"]).hex() for x in items}  # noqa: E731
    ix_args = {}
    for ix in idl["instructions"]:
        if tracked_ix is None or ix["name"] in tracked_ix.get(idl_name, []):
            ix_args[ix["name"]] = [[a["name"], norm_type(a["type"], types)] for a in ix["args"]]
    return {
        "sha256": hashlib.sha256(raw_bytes).hexdigest(),
        "address": idl.get("address"),
        "instructions": disc(idl["instructions"]),
        "events": disc(idl.get("events", [])),
        "accounts": disc(idl.get("accounts", [])),
        "types": {n: flatten_fields(n, types) for n in TRACKED_TYPES[idl_name] if n in types},
        "instruction_args": ix_args,
    }


def tracked_instructions() -> dict[str, list[str]]:
    sys.path.insert(0, str(ROOT / "src"))
    from pumpswap_decode.constants import PUMP_FEES_PROGRAM, PUMPFUN_PROGRAM, PUMPSWAP_PROGRAM
    from pumpswap_decode.instructions import INSTRUCTION_ARGS
    by_prog = {PUMPSWAP_PROGRAM: "pump_amm", PUMPFUN_PROGRAM: "pump", PUMP_FEES_PROGRAM: "pump_fees"}
    return {by_prog[p]: sorted(names) for p, names in INSTRUCTION_ARGS.items()}


def load_idls(ref: str | None, idl_dir: str | None) -> dict[str, bytes]:
    out = {}
    for name in IDLS:
        if idl_dir:
            out[name] = (pathlib.Path(idl_dir) / f"{name}.json").read_bytes()
        else:
            with urllib.request.urlopen(RAW.format(repo=REPO, ref=ref, name=name), timeout=30) as r:
                out[name] = r.read()
    return out


def diff(pinned: dict, current: dict) -> list[str]:
    changes = []
    for name in IDLS:
        p, c = pinned["idls"][name], current[name]
        if p["sha256"] != c["sha256"]:
            changes.append(f"{name}: file sha256 {p['sha256'][:12]} -> {c['sha256'][:12]}")
        if p["address"] != c["address"]:
            changes.append(f"{name}: program address {p['address']} -> {c['address']}")
        for section in ("instructions", "events", "accounts"):
            a, b = p[section], c[section]
            for k in sorted(set(a) | set(b)):
                if k not in b:
                    changes.append(f"{name}: {section[:-1]} removed: {k}")
                elif k not in a:
                    changes.append(f"{name}: {section[:-1]} added: {k}")
                elif a[k] != b[k]:
                    changes.append(f"{name}: {section[:-1]} discriminator changed: {k} {a[k]} -> {b[k]}")
        for section in ("types", "instruction_args"):
            for k, fields in p[section].items():
                now = c[section].get(k)
                if now is None:
                    changes.append(f"{name}: tracked {section} entry missing: {k}")
                elif now != fields:
                    old_names = [f[0] for f in fields]
                    new_names = [f[0] for f in now]
                    if new_names[:len(old_names)] == old_names and now[:len(fields)] == fields:
                        changes.append(f"{name}: {k} gained trailing fields {new_names[len(old_names):]} "
                                       "(prefix-compatible; add them to the layout)")
                    else:
                        changes.append(f"{name}: {k} layout changed (NOT prefix-compatible)")
    return changes


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ref", default="main")
    ap.add_argument("--idl-dir")
    ap.add_argument("--write", action="store_true")
    a = ap.parse_args()
    raw = load_idls(a.ref, a.idl_dir)
    tracked = tracked_instructions()
    current = {n: summarize(raw[n], n, tracked) for n in IDLS}
    if a.write:
        PINS.write_text(json.dumps({"repo": REPO, "ref": a.ref, "idls": current}, indent=1, sort_keys=True) + "\n")
        print(f"pinned {REPO}@{a.ref} -> {PINS.relative_to(ROOT)}")
        return 0
    pinned = json.loads(PINS.read_text())
    changes = diff(pinned, current)
    src = a.idl_dir or f"{REPO}@{a.ref}"
    if not changes:
        print(f"no change: {src} matches pinned {pinned['ref'][:12]}")
        return 0
    print(f"{len(changes)} change(s) vs pinned {pinned['ref'][:12]} ({src}):")
    for c in changes:
        print("  -", c)
    return 1


if __name__ == "__main__":
    sys.exit(main())
