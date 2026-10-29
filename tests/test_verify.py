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


def test_rounded_even_probability_uses_declared_legacy_winner():
    f = _one()
    # Keep the genuine favourite and final score; the rounded value alone cannot choose a side.
    f["win_prob"] = {"A": 0.5, "B": 0.5}
    r = check(f, resim=False)
    assert "predicted winner != higher win prob" not in r["errors"]
    assert "final score winner != predicted winner" not in r["errors"]
    assert r["notes"]
    bad = copy.deepcopy(f)
    bad["predicted_winner"] = "not a player"
    assert "predicted winner is not a player" in check(bad, resim=False)["errors"]


def test_full_precision_win_probability_keeps_sub_rounding_favourite():
    f = _one()
    a = f["predicted_winner"] == f["players"]["A"]["name"]
    f["win_prob"] = {"A": 0.5, "B": 0.5}
    f["win_prob_exact"] = {"A": 0.50004 if a else 0.49996, "B": 0.49996 if a else 0.50004}
    r = check(f, resim=False)
    assert "predicted winner != higher win prob" not in r["errors"]
    f["win_prob_exact"]["A"] = 0.49996 if a else 0.50004
    assert "predicted winner != higher win prob" in check(f, resim=False)["errors"]
