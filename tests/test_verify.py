"""The simulator audit must pass genuine frozen forecasts and catch tampered ones."""
import copy
import json
from pathlib import Path

from tennis_oracle.verify import check

ROOT = Path(__file__).resolve().parent.parent


def _one():
    p = sorted((ROOT / "forecasts").glob("*/*/v1.json"))[0]
    return json.loads(p.read_text())


def test_verify_passes_and_catches_tampering():
    f = _one()
    assert check(f, resim=False)["ok"]
    bad = copy.deepcopy(f); bad["model"]["n_sims"] = 5000
    assert not check(bad, resim=False)["ok"]
    bad = copy.deepcopy(f); bad["win_prob"]["A"] = round(bad["win_prob"]["A"] + 0.05, 4)
    assert not check(bad, resim=False)["ok"]
    bad = copy.deepcopy(f); bad["games"]["mean"] += 1.0
    assert not check(bad, resim=False)["ok"]
