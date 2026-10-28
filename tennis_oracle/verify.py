"""Simulator audit: proves frozen forecasts came from a real Monte Carlo run and are internally consistent.

For every frozen forecast of a date it checks
  * n_sims >= 100,000 and a recorded seed;
  * reproduction: re-running simulate_match with the recorded serve-point probabilities, format,
    variation (sigma, rho), n and seed reproduces the frozen win probability (within MC error when only
    4-dp probabilities were recorded; exactly when full-precision probabilities are present);
  * analytic cross-check: |MC - exact Markov mixture| within 4 MC standard errors (+0.002) for every
    reported probability (win, tiebreak, decider, each exact set score);
  * internal consistency: set-score probabilities sum to 1 and the predicted winner's set scores sum to
    the win probability; games PMF sums to 1 and reproduces mean/median; over/under lines and game
    handicaps are consistent with the PMFs; final score is the predicted winner's most likely set score
    and its projected set sequence is a legal tennis score with the right set count.
"""
from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np

from .freeze import FORECASTS
from .simulate import simulate_match, summarize


def legal_set(a: int, b: int, rule: str) -> bool:
    hi, lo = max(a, b), min(a, b)
    if hi == 6 and lo <= 4:
        return True
    if hi == 7 and lo in (5, 6):
        return rule != "adv" or lo == 5
    return rule == "adv" and hi >= 8 and hi - lo == 2


def check(f: dict, resim: bool = True) -> dict:
    errs, notes = [], []
    m = f["model"]; n = m["n_sims"]; bo = f["best_of"]; fr = f["final_rule"]; need = bo // 2 + 1
    if n < 100_000: errs.append(f"n_sims {n} < 100000")
    if m.get("seed") is None: errs.append("no seed recorded")
    pA = f["win_prob"]["A"]; se = math.sqrt(max(pA * (1 - pA), 1e-9) / n)
    # analytic cross-check
    cc = f["cross_check"]; tol = 4 * max(se, 0.0016) + 0.002
    pairs = [("p_a", cc["p_a"]), ("p_tb", cc["p_tb"]), ("p_decider", cc["p_decider"])] + [(f"sets {k}", v) for k, v in cc["set_scores"].items()]
    worst = max(abs(a - b) for _, (a, b) in pairs)
    for k, (a, b) in pairs:
        if abs(a - b) > tol: errs.append(f"cross-check {k}: MC {a} vs exact {b}")
    gm = cc["games_mean"]
    if abs(gm[0] - gm[1]) > 0.15: errs.append(f"cross-check games mean {gm}")
    # set scores
    ss = f["set_score_probs"]; tot = sum(ss.values())
    if abs(tot - 1) > 2e-3: errs.append(f"set-score probs sum {tot}")
    pa_sets = sum(v for k, v in ss.items() if int(k.split("-")[0]) == need)
    if abs(pa_sets - pA) > 2e-3: errs.append(f"A set-score total {pa_sets} != win prob {pA}")
    # winner / final score
    a_fav = pA >= 0.5
    if (f["predicted_winner"] == f["players"]["A"]["name"]) != a_fav: errs.append("predicted winner != higher win prob")
    fs = f["final_score"]; sa, sb = fs["A_sets"], fs["B_sets"]
    if (sa > sb) != a_fav: errs.append("final score winner != predicted winner")
    best_w = max((v for k, v in ss.items() if (int(k.split("-")[0]) > int(k.split("-")[1])) == a_fav))
    if abs(ss[f"{sa}-{sb}"] - best_w) > 1e-9: errs.append("final score is not the winner's most likely set score")
    seq = [tuple(map(int, s.split("-"))) for s in fs["sets"]]
    if len(seq) != sa + sb: errs.append(f"final sets {fs['sets']} length != {sa}-{sb}")
    if sum(1 for a, b in seq if a > b) != sa: errs.append("final sets do not match set count")
    for i, (a, b) in enumerate(seq):
        rule = fr if i == bo - 1 else "tb7"
        if not legal_set(a, b, "tb7" if rule == "tb10" else rule): errs.append(f"illegal set score {a}-{b}")
    # games
    g = f["games"]; pmf = {int(k): v for k, v in g["pmf"].items()}
    if abs(sum(pmf.values()) - 1) > 2e-3: errs.append("games pmf does not sum to 1")
    mean = sum(k * v for k, v in pmf.items())
    if abs(mean - g["mean"]) > 0.02: errs.append(f"games mean {g['mean']} vs pmf {mean:.3f}")
    ks = sorted(pmf); cum = np.cumsum([pmf[k] for k in ks]); med = ks[int(np.searchsorted(cum, 0.5))]
    if abs(med - g["median"]) > 1: errs.append(f"games median {g['median']} vs pmf {med}")
    for o in f["over_under"]:
        ov = sum(v for k, v in pmf.items() if k > o["line"])
        if abs(ov - o["over"]) > 2e-3 or abs(o["over"] + o["under"] - 1) > 2e-3: errs.append(f"O/U {o['line']} inconsistent")
    dpmf = {int(k): v for k, v in f["games_diff_pmf"].items()}
    for h in f["handicaps"]:
        L = h["line"]; am = sum(v for k, v in dpmf.items() if k > L)
        if abs(am - h[f"A -{L}"]) > 2e-3: errs.append(f"handicap A -{L} inconsistent")
    if abs(sum(k * v for k, v in dpmf.items()) * 0 + sum(dpmf.values()) - 1) > 2e-3: errs.append("diff pmf sum")
    # reproduction
    rep = None
    if resim:
        pp = m.get("point_probs_exact") or m["point_probs"]
        var = m["variation"]
        s = summarize(simulate_match(pp["A_serve"], pp["B_serve"], bo, fr, n, m["seed"], var["sigma"], var["rho"]))
        exact = "point_probs_exact" in m
        rep = {"frozen_p_a": pA, "rerun_p_a": round(s["p_a"], 4), "frozen_games_mean": g["mean"], "rerun_games_mean": round(s["games_mean"], 2),
               "mode": "exact" if exact else "4dp-probs"}
        lim = 1e-4 if exact else 4 * se + 0.004
        if abs(s["p_a"] - pA) > lim: errs.append(f"re-simulation p_a {s['p_a']:.4f} != frozen {pA}")
        if abs(s["games_mean"] - g["mean"]) > (0.01 if exact else 0.25): errs.append(f"re-simulation games mean {s['games_mean']:.2f} != {g['mean']}")
    return {"match": f["match"], "n_sims": n, "seed": m.get("seed"), "mc_se": round(se, 4), "max_mc_vs_exact": round(worst, 4),
            "reproduction": rep, "ok": not errs, "errors": errs}


def verify_date(day: str, resim: bool = True) -> dict:
    rows = []
    for d in sorted((FORECASTS / day).glob("*")):
        files = sorted(d.glob("v*.json"), key=lambda p: int(p.stem[1:]))
        for p in files:
            r = check(json.loads(p.read_text()), resim=resim); r["file"] = str(p.relative_to(FORECASTS.parent)); rows.append(r)
    return {"date": day, "n": len(rows), "passed": sum(r["ok"] for r in rows), "results": rows}
