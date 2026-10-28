import math
import numpy as np
import pandas as pd
import pytest

from tennis_oracle import scoring as S
from tennis_oracle.simulate import simulate_match, summarize
from tennis_oracle.data import parse_score, assert_no_market_columns, _strip_market
from tennis_oracle.predict import fair_odds


def test_hold_prob_known_values():
    assert S.hold_prob(0.5) == pytest.approx(0.5)
    # classic closed form: p=0.6 -> 0.7357
    assert S.hold_prob(0.6) == pytest.approx(0.735729, abs=1e-5)
    assert S.hold_prob(0.0) == pytest.approx(0.0, abs=1e-12)
    assert S.hold_prob(1.0) == pytest.approx(1.0)
    ps = np.linspace(0.05, 0.95, 19)
    assert all(np.diff([S.hold_prob(p) for p in ps]) > 0)


@pytest.mark.parametrize("p", [0.55, 0.64, 0.72])
@pytest.mark.parametrize("target", [7, 10])
def test_tiebreak_symmetric_players_is_half(p, target):
    assert S.tiebreak_prob(p, p, first=S.A, target=target) == pytest.approx(0.5, abs=1e-9)
    assert S.tiebreak_prob(p, p, first=S.B, target=target) == pytest.approx(0.5, abs=1e-9)


def test_tiebreak_rotation_complement():
    # P(A wins TB | A serves first) + P(B wins TB | B serves first, roles swapped) symmetry
    a = S.tiebreak_prob(0.68, 0.62, first=S.A)
    b = S.tiebreak_prob(0.62, 0.68, first=S.B)
    assert a == pytest.approx(1 - b, abs=1e-9)
    assert a > 0.5


@pytest.mark.parametrize("rule", ["tb7", "tb10", "adv"])
def test_set_distribution_sums_to_one(rule):
    for first in (S.A, S.B):
        d = S.set_distribution(0.66, 0.6, first, rule)
        assert sum(d.scores.values()) == pytest.approx(1.0, abs=1e-6)
        for (a, b) in d.scores:
            assert max(a, b) >= 6
            if rule != "adv":
                assert max(a, b) <= 7
            else:
                assert abs(a - b) >= 2


@pytest.mark.parametrize("bo,fr", [(3, "tb7"), (3, "tb10"), (5, "tb10"), (5, "adv")])
def test_match_distribution_consistent(bo, fr):
    m = S.match_distribution(0.67, 0.62, bo, fr)
    assert sum(m.set_scores.values()) == pytest.approx(1.0, abs=1e-6)
    assert m.games.sum() == pytest.approx(1.0, abs=1e-6)
    assert m.diff.sum() == pytest.approx(1.0, abs=1e-6)
    need = bo // 2 + 1
    pw = sum(v for (a, b), v in m.set_scores.items() if a == need)
    assert pw == pytest.approx(m.p_win, abs=1e-9)
    assert m.p_win == pytest.approx(S.match_win_prob(0.67, 0.62, bo, fr), abs=1e-6)


def test_symmetric_players_half():
    for bo in (3, 5):
        assert S.match_distribution(0.63, 0.63, bo, "tb7").p_win == pytest.approx(0.5, abs=1e-9)


def test_best_of_five_amplifies_favourite():
    p3 = S.match_win_prob(0.66, 0.62, 3, "tb7")
    p5 = S.match_win_prob(0.66, 0.62, 5, "tb10")
    assert 0.5 < p3 < p5


def test_tb10_vs_tb7_changes_only_deciding_set():
    a = S.match_distribution(0.66, 0.62, 3, "tb7")
    b = S.match_distribution(0.66, 0.62, 3, "tb10")
    assert a.set_scores[(2, 0)] == pytest.approx(b.set_scores[(2, 0)], abs=1e-12)
    assert a.p_win != pytest.approx(b.p_win, abs=1e-6)
    assert b.p_win > a.p_win  # longer TB favours the stronger server


def test_mc_matches_analytic():
    pa, pb = 0.665, 0.625
    sim = summarize(simulate_match(pa, pb, 3, "tb7", n=100_000, seed=7))
    m = S.match_distribution(pa, pb, 3, "tb7")
    se = math.sqrt(m.p_win * (1 - m.p_win) / 100_000)
    assert abs(sim["p_a"] - m.p_win) < 4 * se
    assert abs(sim["p_tb"] - m.p_tb) < 0.01
    mean_an = float((np.arange(len(m.games)) * m.games).sum())
    assert abs(sim["games_mean"] - mean_an) < 0.1


def test_mc_with_variation_matches_mixture():
    pa, pb, sig, rho = 0.66, 0.62, 0.25, -0.05
    sim = summarize(simulate_match(pa, pb, 5, "tb10", n=100_000, seed=11, sigma=sig, rho=rho))
    m = S.mixture_distribution(pa, pb, 5, "tb10", sig, rho, 5)
    assert abs(sim["p_a"] - m.p_win) < 0.006


def test_mc_seed_reproducible():
    a = summarize(simulate_match(0.65, 0.6, 3, "tb7", n=20_000, seed=3))
    b = summarize(simulate_match(0.65, 0.6, 3, "tb7", n=20_000, seed=3))
    assert a["p_a"] == b["p_a"] and a["games_mean"] == b["games_mean"]


def test_parse_score():
    sets, tbs, ok = parse_score("6-3 3-6 7-6(4)")
    assert sets == [(6, 3), (3, 6), (7, 6)] and tbs == 1 and ok
    sets, tbs, ok = parse_score("6-4 2-1 RET")
    assert not ok and sets[0] == (6, 4)
    assert parse_score("W/O")[2] is False
    assert parse_score(None) == ([], 0, False)
    sets, _, _ = parse_score("6-4 4-6 7-6(10-8)")
    assert sets[-1] == (7, 6)


def test_fair_odds():
    o = fair_odds(0.5)
    assert o["decimal"] == pytest.approx(2.0)
    o = fair_odds(0.8)
    assert o["decimal"] == pytest.approx(1.25) and str(o["american"]).startswith("-400")
    o = fair_odds(0.25)
    assert o["decimal"] == pytest.approx(4.0) and str(o["american"]).lstrip("+").startswith("300")


def test_no_market_columns_guard():
    assert_no_market_columns(pd.DataFrame(columns=["winner_name", "best_of", "w_ace"]))
    for bad in ["B365W", "PSW", "AvgL", "MaxW", "odds_home", "implied_prob"]:
        with pytest.raises(ValueError):
            assert_no_market_columns(pd.DataFrame(columns=["winner_name", bad]))


def test_strip_market_payload():
    payload = {"events": [{"id": 1, "odds": [{"x": 1}], "competitions": [{"predictor": {"a": 1}, "status": "ok"}]}]}
    out = _strip_market(payload)
    s = str(out)
    assert "odds" not in s and "predictor" not in s and "status" in s
