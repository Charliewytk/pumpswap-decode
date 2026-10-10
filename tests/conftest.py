import json
import pathlib

import pytest

HERE = pathlib.Path(__file__).resolve().parent
FIXTURES = HERE / "fixtures"
GOLDEN = sorted((FIXTURES / "golden").glob("*.json"))


def load_golden():
    return [json.loads(p.read_text()) for p in GOLDEN]


@pytest.fixture(scope="session")
def fee_config():
    from pumpswap_decode.fees import decode_fee_config_b64
    return decode_fee_config_b64(json.loads((FIXTURES / "fee_config_mainnet.json").read_text())["data_b64"])


@pytest.fixture(scope="session")
def golden():
    return {fx["name"]: fx for fx in load_golden()}
