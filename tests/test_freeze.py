import json
import pytest
from tennis_oracle import freeze as F


def _f(surface="Hard"):
    return {"players": {"A": {"name": "Player One", "id": "t:1"}, "B": {"name": "Player Two", "id": "t:2"}}, "surface": surface, "best_of": 3,
            "final_rule": "tb7", "indoor": False, "context_inputs": {}, "win_prob": {"A": 0.6, "B": 0.4},
            "model": {"version": "test"}}


def test_freeze_never_overwrites(tmp_path, monkeypatch):
    monkeypatch.setattr(F, "FORECASTS", tmp_path)
    p1 = F.freeze(_f(), "2030-01-01", "m1")
    assert p1 and p1.name == "v1.json"
    before = p1.read_text()
    assert F.freeze(_f(), "2030-01-01", "m1") is None          # identical inputs: no new version
    p2 = F.freeze(_f("Clay"), "2030-01-01", "m1")              # changed input: new version
    assert p2.name == "v2.json"
    assert p1.read_text() == before                              # v1 untouched
    rec = json.loads(p2.read_text())
    assert rec["freeze"]["version"] == 2 and rec["freeze"]["pre_match"] is True
    with pytest.raises(FileExistsError):
        open(p1, "x")
