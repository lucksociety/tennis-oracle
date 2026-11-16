"""Polymarket Gamma -> internal events, the bet pause and the market benchmark (shapes copied from live Gamma, 2026-11-16)."""
import json
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import pytest
from tennis_oracle import markets as M

ROOT = Path(__file__).resolve().parent.parent
T = "ATP World Tour Finals, Ivan Lendl Group: Daniil Medvedev vs Flavio Cobolli"
A, B = "Daniil Medvedev", "Flavio Cobolli"
FEE = {"exponent": 1, "rate": 0.05, "takerOnly": True, "rebateRate": 0.15}


def gm(mid, typ, question, outcomes, line=None, bid=None, ask=None, **kw):
    return {"id": mid, "sportsMarketType": typ, "question": question, "outcomes": json.dumps(outcomes), "clobTokenIds": json.dumps([f"{mid}a", f"{mid}b"]),
            "line": line, "bestBid": bid, "bestAsk": ask, "active": True, "closed": False, "acceptingOrders": True, "feesEnabled": True, "feeSchedule": FEE, **kw}


def gamma_event(**kw):
    return {"id": "9001", "slug": "atp-medvede-cobolli-2026-11-16", "title": T, "seriesSlug": "atp", "startTime": "2026-11-16T19:30:00Z", "markets": [
        gm(1, "moneyline", T, [A, B], None, .76, .77),
        gm(2, "tennis_first_set_winner", f"Set 1 Winner: {A} vs {B}", [A, B], None, .69, .70),
        gm(3, "tennis_set_winner", f"Set 2 Winner: {A} vs {B}", [A, B], 2, .68, .72),
        gm(4, "tennis_set_totals", f"{A} vs. {B}: Total Sets O/U 2.5", ["Over 2.5", "Under 2.5"], 2.5, .38, .39),
        gm(5, "tennis_match_totals", "Medvedev vs. Cobolli: Match O/U 21.5", ["Over", "Under"], 21.5, .56, .58),
        gm(6, "tennis_set_handicap", f"Set Handicap: {A} (-1.5) vs {B} (+1.5)", [A, B], -1.5, .51, .52),
        gm(7, "tennis_game_handicap", f"Game Spread: {A} (-4.5) vs {B} (+4.5)", [A, B], -4.5, .39, .41, feeSchedule={"rate": 0.03, "exponent": 1}),
        gm(8, "tennis_match_totals", "Medvedev vs. Cobolli: Match O/U 22.5", ["Over", "Under"], 22.5, None, None),
        gm(9, "tennis_match_totals", "Medvedev vs. Cobolli: Match O/U 23.5", ["Over", "Under"], 23.5, .42, .43, acceptingOrders=False),
        gm(10, "tennis_game_handicap", f"Game Spread: {B} (-4.5) vs {A} (+4.5)", [A, B], -4.5, .1, .2),      # names do not match the outcomes
    ], **kw}


def slate_match(**kw):
    dist = {"win_prob": {"A": 0.70, "B": 0.30}, "set_score_probs": {"2-0": 0.45, "2-1": 0.25, "1-2": 0.15, "0-2": 0.15},
            "games_pmf": {"20": 0.5, "24": 0.5}, "games_diff_pmf": {"5": 0.6, "-3": 0.4}}
    return {"tour": "atp", "espn_id": "x", "start_utc": "2026-11-16T19:30:00Z", "status": "STATUS_SCHEDULED", "best_of": 3, "players": [{"name": A}, {"name": B}],
            "forecast": {"final_score": {"A_sets": 2, "B_sets": 0, "sets": ["6-3", "6-4"]}, "recommendation_quality": {"eligible": True}, "n_sims": 100000,
                         "frozen_at_utc": "2026-11-16T10:00:00+00:00", "dist": dist}, **kw}


AT = "2026-11-16T12:00:00Z"


def test_gamma_event_adapts_to_every_modelled_outcome():
    ev = M.adapt_gamma_event(gamma_event())
    assert ev["eventId"] == "POLY-9001" and ev["subcategory"] == "atp" and ev["isLive"] is False
    assert ev["beginAt"] == str(int(datetime(2026, 11, 16, 19, 30, tzinfo=timezone.utc).timestamp()))
    by = {mk["title"]: mk for mk in ev["markets"]}
    px = lambda k: (by[k]["pricing"]["buyYesPriceUsd"] / 1e6, by[k]["pricing"]["sellYesPriceUsd"] / 1e6)
    assert px(A) == (.77, .76) and px(B) == pytest.approx((.24, .23))            # outcome 1 is the complement of outcome 0's book
    assert px("Over 2.5") == (.39, .38) and px("Under 2.5") == pytest.approx((.62, .61))
    assert px("Over 21.5") == (.58, .56) and by["Over 21.5"]["sportsMarketType"] == "tennis_match_totals"
    assert by[f"{A} -1.5"]["sportsMarketType"] == "handicap" and px(f"{B} +1.5") == pytest.approx((.49, .48))
    assert by[f"{A} -4.5"]["sportsMarketType"] == "tennis_game_handicap" and by[f"{A} -4.5"]["fee"] == {"rate": .03, "exponent": 1.0}
    assert by[A]["fee"] == {"rate": .05, "exponent": 1.0} and by[A]["tokenId"] == "1a" and by[B]["marketId"] == "POLY-1-1"
    assert by["Over 22.5"]["pricing"]["buyYesPriceUsd"] == 0 and by["Over 23.5"]["status"] == "closed"
    # set winners are not modelled; a handicap whose names disagree with its outcomes is never guessed
    assert len(ev["markets"]) == 2 * 7
    assert M.match_event(slate_match(), [ev])["eventId"] == "POLY-9001"


@pytest.mark.parametrize("change", [{"seriesSlug": "atp-doubles"}, {"seriesSlug": "wta"}, {"seriesSlug": "itf"}, {"startTime": None}, {"title": "ATP Finals Winner"}, {"id": None}])
def test_only_dated_atp_singles_matches_are_events(change):
    assert M.adapt_gamma_event(gamma_event(**change)) is None


def test_bets_are_paused_but_everything_is_still_priced():
    ev = M.adapt_gamma_event(gamma_event())
    live = M.evaluate(slate_match(), ev, AT, bets_paused=False)
    assert live["status"] == "bet" and live["best"]["would_qualify"] and live["best"]["eligible"]
    paused = M.evaluate(slate_match(), ev, AT)                                    # module default
    assert M.BETS_PAUSED is True and paused["status"] == "paused" and paused["best"] is None and paused["n_eligible"] == 0
    assert paused["verdict"] == M.PAUSE_VERDICT and not any(o.get("eligible") for o in paused["outcomes"])
    strip = lambda r: [{k: v for k, v in o.items() if k != "eligible"} for o in r["outcomes"]]
    assert strip(paused) == strip(live) and any(o.get("would_qualify") for o in paused["outcomes"])


def test_market_benchmark_is_recorded_only_before_the_match():
    ev = M.adapt_gamma_event(gamma_event())
    r = M.evaluate(slate_match(), ev, AT)
    assert r["model_p_a"] == .70 and r["market_p_a"] == M.r4(.765 / (.765 + .235))
    assert M.evaluate(slate_match(), ev, "2026-11-16T19:31:00Z")["market_p_a"] is None            # after the start
    assert M.evaluate(slate_match(), {**ev, "isLive": True}, AT)["market_p_a"] is None
    assert M.evaluate(slate_match(status="STATUS_IN_PROGRESS"), ev, AT)["market_p_a"] is None
    wide = gamma_event(); wide["markets"][0]["bestBid"] = .60
    assert M.evaluate(slate_match(), M.adapt_gamma_event(wide), AT)["market_p_a"] is None           # 17c wide: not a price
    m = slate_match(); m["forecast"]["recommendation_quality"] = {"eligible": False}
    r = M.evaluate(m, ev, AT)
    assert r["status"] == "input_quality" and r["market_p_a"] is not None                            # graded even when never bettable


def test_fetch_pages_gamma_and_never_calls_jupiter():
    calls = []

    def get(params):
        calls.append(params)
        return [gamma_event(), gamma_event(id="9002", seriesSlug="atp-doubles")] + [gamma_event(id=str(i), seriesSlug="itf") for i in range(98)] if len(calls) == 1 else [gamma_event(id="9003")]
    now = datetime(2026, 11, 16, 12, tzinfo=timezone.utc)
    f = M.fetch_events(now=now, get=get)
    assert [e["eventId"] for e in f["events"]] == ["POLY-9001", "POLY-9003"] and not f["errors"] and f["fetched_at_utc"] == "2026-11-16T12:00:00Z"
    assert [c["offset"] for c in calls] == [0, 100] and all(c["tag_id"] == "864" and c["closed"] == "false" for c in calls)
    assert calls[0]["start_time_min"].startswith("2026-11-16T00:00") and calls[0]["start_time_max"].startswith("2026-11-19T12:00")
    assert "jup" not in json.dumps(f["urls"]) and "gamma-api.polymarket.com" in f["urls"][0]
    boom = M.fetch_events(now=now, get=lambda p: (_ for _ in ()).throw(RuntimeError("down")))
    assert boom["events"] == [] and "down" in boom["errors"][0]


def test_run_writes_polymarket_file_and_snapshot(tmp_path, monkeypatch):
    monkeypatch.setattr(M, "DOCS_DATA", tmp_path); monkeypatch.setattr(M, "SNAPS", tmp_path / "snaps"); monkeypatch.setattr(M, "ROOT", tmp_path)
    (tmp_path / "slate-2026-11-16.json").write_text(json.dumps({"matches": [slate_match()]}))
    fetched = {"events": [M.adapt_gamma_event(gamma_event())], "errors": [], "urls": ["u"], "fetched_at_utc": AT}
    out = M.run("2026-11-16", fetched=fetched)
    saved = json.loads((tmp_path / "polymarket-2026-11-16.json").read_text())
    assert saved["bets_paused"] is True and saved["rule"].startswith("BETS PAUSED") and saved["matches"][0]["status"] == "paused"
    assert saved["matches"][0]["market_p_a"] == out["matches"][0]["market_p_a"] and not (tmp_path / "jupiter-2026-11-16.json").exists()
    snap = json.loads((tmp_path / out["snapshot_path"]).read_text())
    assert snap["source"].startswith("Polymarket") and snap["events"][0]["markets"][0]["fee"] == {"rate": .05, "exponent": 1.0}


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_js_adapter_and_pause_match_python():
    g, m = gamma_event(), slate_match()
    js = """const M=require(process.argv[1]);const [g,m,at]=JSON.parse(process.argv[2]);const ev=M.adaptGammaEvent(g);
    const none=['atp-doubles','wta'].map(s=>M.adaptGammaEvent(Object.assign({},g,{seriesSlug:s})));
    (async()=>{const f=await M.fetchEvents(async u=>({ok:true,json:async()=>[g]}),{now:at});
    console.log(JSON.stringify({ev,none,paused:M.evaluate(m,ev,{snapshot_at_utc:at}),live:M.evaluate(m,ev,{snapshot_at_utc:at,bets_paused:false}),n:f.events.length,errs:f.errors,cost:M.cost(.25),c3:M.cost(.5,{rate:.03,exponent:1})}))})();"""
    out = json.loads(subprocess.check_output(["node", "-e", js, str(ROOT / "docs" / "markets.js"), json.dumps([g, m, AT])], text=True))
    ev = M.adapt_gamma_event(g)
    assert out["ev"] == json.loads(json.dumps(ev)) and out["none"] == [None, None] and out["n"] == 1 and out["errs"] == []
    keys = ("market_id", "label", "model_p", "price", "implied", "edge", "ev_per_1", "eligible", "would_qualify", "skip", "selection")
    for name, py in (("paused", M.evaluate(m, ev, AT)), ("live", M.evaluate(m, ev, AT, bets_paused=False))):
        js_row = out[name]
        for k in ("status", "verdict", "model_p_a", "market_p_a", "n_priced", "n_eligible"):
            assert js_row[k] == py[k], (name, k)
        assert (js_row["best"] or {}).get("market_id") == (py["best"] or {}).get("market_id")
        assert len(js_row["outcomes"]) == len(py["outcomes"])
        for oa, ob in zip(js_row["outcomes"], py["outcomes"]):
            for k in keys:
                assert oa.get(k) == ob.get(k), (name, k, oa, ob)
    assert out["paused"]["status"] == "paused" and out["live"]["status"] == "bet"
    assert out["cost"] == pytest.approx(M.cost(.25)) and out["c3"] == pytest.approx(.5075)
