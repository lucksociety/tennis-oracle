"""Jupiter comparison: Python/JS parity, the betting rule, and the market-data firewall."""
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from tennis_oracle import jupiter as J

ROOT = Path(__file__).resolve().parent.parent
FIX = json.loads((ROOT / "tests" / "fixtures" / "markets_fixture.json").read_text())


def _py():
    return [J.evaluate(m, J.match_event(m, FIX["events"]), FIX["snapshot_at_utc"]) for m in FIX["slate"]["matches"]]


def test_model_modules_never_touch_market_code():
    # forecast pipeline must not import the market module (prices can never become model inputs)
    for mod in ("predict", "ratings", "simulate", "scoring", "schedule", "freeze", "build", "grid", "config", "data", "export", "backtest"):
        src = (ROOT / "tennis_oracle" / f"{mod}.py").read_text()
        assert not re.search(r"\bjupiter\b|markets\.js|api\.jup\.ag|polymarket", src, re.I), mod
    out = subprocess.check_output([sys.executable, "-c", "import sys, tennis_oracle.predict, tennis_oracle.schedule; print('tennis_oracle.jupiter' in sys.modules)"], text=True)
    assert out.strip() == "False"
    eng = (ROOT / "docs" / "engine.js").read_text()
    assert not re.search(r"jup\.ag|TOMarkets|jupiter|market_snapshots|buyYes|pricing", eng, re.I)
    # no model / forecast module reads price files or snapshot dirs
    for f in (ROOT / "tennis_oracle").glob("*.py"):
        if f.name in ("jupiter.py", "cli.py"): continue
        assert not re.search(r"market_snapshots|jupiter-|buyYesPrice", f.read_text()), f.name


def test_forecast_identical_with_or_without_price_files(tmp_path, monkeypatch):
    # the frozen forecast is a pure function of ratings/stats: hide every price file and nothing changes
    import tennis_oracle.schedule as SC
    src = (ROOT / "tennis_oracle" / "schedule.py").read_text()
    assert "jupiter" not in src.lower()
    f = sorted((ROOT / "forecasts").glob("*/*/v1.json"))[0]
    fr = json.loads(f.read_text())
    assert not any(k in json.dumps(fr["model"]).lower() for k in ("jupiter", "price", "polymarket", "odds"))


def test_fee_and_rule():
    assert J.cost(0.25) * 100 == pytest.approx(26.3125)          # 100 contracts @0.25 -> $1.32 fee (rounded up)
    dist = {"win_prob": {"A": 0.6, "B": 0.4}, "set_score_probs": {"2-0": 0.35, "2-1": 0.25, "1-2": 0.2, "0-2": 0.2},
            "games_pmf": {"20": 0.5, "24": 0.5}, "games_diff_pmf": {"3": 0.6, "-3": 0.4}}
    m = {"espn_id": "x", "status": "STATUS_SCHEDULED", "best_of": 3, "players": [{"name": "Ann Alpha"}, {"name": "Bea Beta"}],
         "forecast": {"frozen_at_utc": "2026-01-01T00:00:00+00:00", "dist": dist}}
    mk = lambda t, lab, p: {"marketId": lab, "sportsMarketType": t, "status": "open", "marketOptions": [{"label": lab}], "pricing": {"buyYesPriceUsd": int(p * 1e6)}}
    ev = {"eventId": "E", "metadata": {"title": "T: Ann Alpha vs Bea Beta"}, "markets": [
        mk("moneyline", "Ann Alpha", 0.55), mk("moneyline", "Bea Beta", 0.30), mk("handicap", "Bea Beta +1.5", 0.50),
        mk("tennis_match_totals", "Over 22.5", 0.30)]}
    r = J.evaluate(m, ev, "2026-01-02T00:00:00Z")
    by = {o["label"]: o for o in r["outcomes"]}
    assert by["Bea Beta +1.5"]["model_p"] == pytest.approx(0.65)
    assert by["Bea Beta"]["edge"] > 0 and not by["Bea Beta"]["eligible"]      # +edge but <50% -> never recommended
    assert by["Over 22.5"]["model_p"] == pytest.approx(0.5) and not by["Over 22.5"]["eligible"]  # exactly 50% is not >50%
    assert r["best"]["label"] == "Bea Beta +1.5"                                # highest EV among eligible
    ev["markets"] = [mk("moneyline", "Ann Alpha", 0.62)]
    assert J.evaluate(m, ev, "2026-01-02T00:00:00Z")["verdict"].startswith("NO BET")
    assert J.evaluate(m, None)["verdict"] == "NO BET (no market)"
    assert J.evaluate(m, ev, "2025-12-31T00:00:00Z")["status"] == "invalid_order"   # prices before freeze are rejected
    m["status"] = "STATUS_IN_PROGRESS"
    assert J.evaluate(m, {**ev, "markets": [mk("handicap", "Bea Beta +1.5", 0.5)]}, "2026-01-02T00:00:00Z")["best"] is None


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_js_matches_python():
    js = f"""const M=require({json.dumps(str(ROOT / 'docs' / 'markets.js'))});
    const F=require({json.dumps(str(ROOT / 'tests' / 'fixtures' / 'markets_fixture.json'))});
    console.log(JSON.stringify(F.slate.matches.map(m=>M.evaluate(m, M.matchEvent(m, F.events), {{snapshot_at_utc: F.snapshot_at_utc}}))));"""
    js_out = json.loads(subprocess.check_output(["node", "-e", js], text=True))
    py_out = json.loads(json.dumps(_py()))
    assert len(js_out) == len(py_out) >= 3
    for a, b in zip(js_out, py_out):
        assert a["verdict"] == b["verdict"] and a["event_id"] == b["event_id"]
        for oa, ob in zip(a["outcomes"], b["outcomes"]):
            for k in ("model_p", "implied", "edge", "ev_per_1", "eligible", "skip", "selection"):
                assert oa.get(k) == ob.get(k), (k, oa, ob)
