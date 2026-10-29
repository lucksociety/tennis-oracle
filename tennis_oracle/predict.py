"""Forecast construction: composite rating -> point-level serve probabilities ->
exact analytic distribution + Monte Carlo simulation -> full TENNIS ORACLE report dict."""
from __future__ import annotations

import math
from datetime import datetime, timezone

import numpy as np
import pandas as pd

from . import MODEL_VERSION, __version__
from .ratings import RatingEngine, elo_prob, logit, sigmoid
from .scoring import mixture_distribution, mixture_win_prob, DIFF_OFF
from .simulate import simulate_match, summarize

FINAL_RULES = {"tb7": "7-point tiebreak at 6-6", "tb10": "10-point tiebreak at 6-6", "adv": "advantage (no tiebreak)"}


def fair_odds(p: float) -> dict:
    p = min(max(p, 1e-6), 1 - 1e-6)
    dec = 1 / p
    amer = -100 * p / (1 - p) if p > 0.5 else 100 * (1 - p) / p
    return {"decimal": round(dec, 2), "american": f"{'+' if amer > 0 else ''}{round(amer):d}"}


def composite_diff(comps: dict, params: dict, context: float = 0.0) -> float:
    w = params["weights"]
    d = sum(w[k] * comps[k] for k in ("surface_elo", "serve_return", "overall_elo", "form"))
    cap = params.get("context_max_elo", 100.0)
    return params["composite_scale"] * d + max(-cap, min(cap, context))


def solve_split(pa_sr: float, pb_sr: float, target: float, sigma: float, rho: float, fast=False) -> float:
    """Find logit shift delta so that Bo3 mixture win prob of
    (sigmoid(logit pa_sr + delta), sigmoid(logit pb_sr - delta)) equals the composite target."""
    la, lb = logit(pa_sr), logit(pb_sr)
    lo, hi = -3.0, 3.0
    for _ in range(40 if not fast else 30):
        mid = (lo + hi) / 2
        p = mixture_win_prob(sigmoid(la + mid), sigmoid(lb - mid), 3, "tb7", sigma, rho, 3)
        if p < target:
            lo = mid
        else:
            hi = mid
        if hi - lo < 1e-5:
            break
    return (lo + hi) / 2


def data_quality(pa: dict, pb: dict, asof: pd.Timestamp, data_cutoff: pd.Timestamp, surface: str) -> tuple[str, list]:
    notes = []
    score = 2  # 2 HIGH, 1 MEDIUM, 0 LOW
    for p in (pa, pb):
        st = p["stats_52w"].get("matches_with_stats", 0)
        n52 = sum(p["last52w"])
        ns = p["matches_surface"].get(surface, 0)
        if p["matches_total"] < 10 or st < 3:
            score = min(score, 0); notes.append(f"{p['name']}: sparse record ({p['matches_total']} tour matches, {st} with serve stats in 52w)")
        elif st < 15 or ns < 15 or n52 < 15:
            score = min(score, 1); notes.append(f"{p['name']}: limited recent/surface sample ({n52} matches 52w, {ns} career on {surface}, {st} with stats)")
        if p["last_stats_match"]:
            gap = (asof - pd.Timestamp(p["last_stats_match"])).days
            if gap > 60:
                score = min(score, 1); notes.append(f"{p['name']}: latest point-level stats are {gap} days old ({p['last_stats_match']})")
        if p["last_match"] and (asof - pd.Timestamp(p["last_match"])).days > 120:
            score = min(score, 1); notes.append(f"{p['name']}: no recorded match since {p['last_match']} (layoff/injury status unverified)")
    if (asof - data_cutoff).days > 45:
        score = min(score, 1); notes.append(f"Point-level dataset cutoff {data_cutoff.date()} is {(asof - data_cutoff).days} days before forecast date")
    return ["LOW", "MEDIUM", "HIGH"][score], notes


def thresholds(bo: int):
    if bo == 3:
        return [x + 0.5 for x in range(17, 26)], [1.5, 2.5, 3.5, 4.5, 5.5]
    return [x + 0.5 for x in range(29, 44, 1)], [2.5, 3.5, 4.5, 5.5, 6.5, 7.5]


def forecast(engine: RatingEngine, a_id: str, b_id: str, surface: str, best_of: int = 3, final_rule: str = "tb7",
             tour: str = "atp", asof=None, indoor: bool = False, context: dict | None = None, meta: dict | None = None,
             n_sims: int | None = None, seed: int | None = None, data_cutoff=None) -> dict:
    """Full pre-match forecast for A vs B. All reported probabilities come from one
    Monte Carlo sample; the exact analytic mixture is reported alongside as a cross-check."""
    P = engine.p
    context = context or {}
    meta = meta or {}
    asof = pd.Timestamp(asof or pd.Timestamp.now().normalize())
    data_cutoff = pd.Timestamp(data_cutoff or engine.cutoff)
    A_, B_ = engine.players[a_id], engine.players[b_id]
    comps, pa_sr, pb_sr = engine.components(A_, B_, tour, surface, asof)
    if indoor and P.get("indoor_logit_adj", 0):
        pa_sr = sigmoid(logit(pa_sr) + P["indoor_logit_adj"]); pb_sr = sigmoid(logit(pb_sr) + P["indoor_logit_adj"])
    # manual, verified context only (fatigue / injury / conditions) in Elo points; default 0
    ctx = float(context.get("a_elo_adj", 0.0)) - float(context.get("b_elo_adj", 0.0))
    D = composite_diff(comps, P, ctx)
    target3 = elo_prob(D)
    sig, rho = P["variation"]["sigma"], P["variation"]["rho"]
    delta = solve_split(pa_sr, pb_sr, target3, sig, rho)
    pa = sigmoid(logit(pa_sr) + delta); pb = sigmoid(logit(pb_sr) - delta)
    n = n_sims or P["mc"]["n_sims"]; sd = P["mc"]["seed"] if seed is None else seed
    sim = summarize(simulate_match(pa, pb, best_of, final_rule, n, sd, sig, rho))
    ana = mixture_distribution(pa, pb, best_of, final_rule, sig, rho, P.get("gh_nodes", 5))
    an_games = float((np.arange(len(ana.games)) * ana.games).sum())
    cross = {
        "p_a": [round(sim["p_a"], 4), round(ana.p_win, 4)],
        "p_tb": [round(sim["p_tb"], 4), round(ana.p_tb, 4)],
        "p_decider": [round(sim["p_decider"], 4), round(ana.p_decider, 4)],
        "games_mean": [round(sim["games_mean"], 3), round(an_games, 3)],
        "exp_breaks": [round(sim["exp_breaks"], 3), round(ana.exp_breaks, 3)],
        "set_scores": {k: [round(v, 4), round(float(ana.set_scores.get(tuple(map(int, k.split("-"))), 0)), 4)] for k, v in sim["set_scores"].items()},
        "mc_se_p_a": round(math.sqrt(sim["p_a"] * (1 - sim["p_a"]) / n), 4),
    }
    cross["max_abs_diff_probs"] = round(max(abs(x[0] - x[1]) for x in [cross["p_a"], cross["p_tb"], cross["p_decider"]] + list(cross["set_scores"].values())), 4)
    asof_prof = asof
    prof_a = engine.profile(A_, surface, asof_prof); prof_b = engine.profile(B_, surface, asof_prof)
    dq, dq_notes = data_quality(prof_a, prof_b, asof, data_cutoff, surface)
    if context.get("injury_uncertain"):
        dq = "LOW" if dq != "HIGH" else "MEDIUM"; dq_notes.append("Manual flag: injury/fitness uncertain")
    out = build_report(A_.name, B_.name, best_of, final_rule, sim, pa, pb)
    # matchup drivers
    w = P["weights"]
    contrib = {k: P["composite_scale"] * w[k] * comps[k] for k in comps}
    out.update({
        "match": f"{A_.name} vs {B_.name}",
        "players": {"A": {"id": a_id, **prof_a}, "B": {"id": b_id, **prof_b}},
        "tour": tour, "surface": surface, "indoor": indoor, "best_of": best_of,
        "final_set_rule": FINAL_RULES[final_rule], "final_rule": final_rule,
        "tournament": meta.get("tournament", ""), "round": meta.get("round", ""),
        "format": f"Best of {best_of}, deciding set: {FINAL_RULES[final_rule]}",
        "data_cutoff": str(data_cutoff.date()), "forecast_date": str(asof.date()),
        "data_quality": dq, "data_quality_notes": dq_notes,
        "model": {"version": P["version"], "package": __version__, "composite_elo_diff": round(D, 1),
                  "components_elo": {k: round(v, 1) for k, v in comps.items()},
                  "weighted_contributions": {k: round(v, 1) for k, v in contrib.items()},
                  "context_elo": ctx, "target_bo3_win_prob": round(target3, 4),
                  "serve_return_implied_spw": {"A": round(pa_sr, 4), "B": round(pb_sr, 4)},
                  "point_probs": {"A_serve": round(pa, 4), "B_serve": round(pb, 4)},
                  "point_probs_exact": {"A_serve": float(pa), "B_serve": float(pb)},
                  "hold_probs": {"A": round(float(_hold(pa)), 4), "B": round(float(_hold(pb)), 4)},
                  "variation": {"sigma": sig, "rho": rho}, "n_sims": n, "seed": sd},
        "cross_check": cross,
        "drivers": drivers(A_.name, B_.name, comps, contrib, prof_a, prof_b, pa, pb),
        "context_inputs": context,
    })
    out["best_of"] = best_of
    out["strongest"] = strongest(out, dq)
    return out


def _hold(p):
    from .scoring import hold_prob
    return hold_prob(p)


def build_report(na, nb, bo, fr, sim, pa, pb) -> dict:
    pA = sim["p_a"]; pB = 1 - pA
    ss = dict(sorted(sim["set_scores"].items(), key=lambda kv: -kv[1]))
    top = next(iter(ss))
    rep = sim["representative"].get(top, {"sets": []})
    games_pmf = sim["games_pmf"]; diff_pmf = sim["diff_pmf"]
    tot_keys = np.array(sorted(games_pmf)); tot_p = np.array([games_pmf[k] for k in tot_keys])
    d_keys = np.array(sorted(diff_pmf)); d_p = np.array([diff_pmf[k] for k in d_keys])
    tl, hl = thresholds(bo)
    ou = [{"line": t, "over": round(float(tot_p[tot_keys > t].sum()), 4), "under": round(float(tot_p[tot_keys < t].sum()), 4)} for t in tl]
    hc = []
    for h in hl:
        a_minus = float(d_p[d_keys > h].sum())        # A -h covers: A wins by more than h games
        b_minus = float(d_p[d_keys < -h].sum())
        hc.append({"line": h, f"A -{h}": round(a_minus, 4), f"B +{h}": round(1 - a_minus, 4),
                   f"B -{h}": round(b_minus, 4), f"A +{h}": round(1 - b_minus, 4)})
    # final-score prediction: most likely exact score among the predicted winner's wins (keeps it consistent with the pick)
    a_fav = pA >= 0.5
    fs_key = next(k for k in ss if (int(k.split("-")[0]) > int(k.split("-")[1])) == a_fav)
    fs_rep = sim["representative"].get(fs_key, {"sets": []})
    sa, sb = map(int, fs_key.split("-"))
    return {
        "predicted_winner": na if pA >= 0.5 else nb,
        "win_prob": {"A": round(pA, 4), "B": round(pB, 4)},
        "win_prob_exact": {"A": float(pA), "B": float(pB)},
        "fair_odds": {"A": fair_odds(pA), "B": fair_odds(pB)},
        "set_score_probs": {k: round(v, 4) for k, v in ss.items()},
        "most_likely_set_score": top,
        "representative_sets": rep["sets"], "representative_sets_prob": round(rep.get("p", 0), 4),
        "games": {"mean": round(sim["games_mean"], 2), "median": sim["games_median"], "p10": sim["games_p10"],
                  "p90": sim["games_p90"], "sd": round(sim["games_sd"], 2),
                  # point prediction for total games = median (lower walk-forward MAE than the mean; models/score_study.json)
                  "projected": sim["games_median"], "pmf": {int(k): round(v, 5) for k, v in games_pmf.items()}},
        "games_diff_pmf": {int(k): round(v, 5) for k, v in diff_pmf.items()},
        "over_under": ou, "handicaps": hc,
        "p_tiebreak": round(sim["p_tb"], 4),
        "p_a_wins_set": round(sim["p_a_set"], 4), "p_b_wins_set": round(sim["p_b_set"], 4),
        "p_decider": round(sim["p_decider"], 4),
        "expected_breaks": {"total": round(sim["exp_breaks"], 2), "by_A": round(sim["exp_breaks_a"], 2), "by_B": round(sim["exp_breaks_b"], 2)},
        "expected_break_points": {"A_creates": round(sim["exp_bp_a"], 2), "B_creates": round(sim["exp_bp_b"], 2)},
        "final_score": {"A_sets": sa, "B_sets": sb, "sets": fs_rep["sets"], "prob": round(ss[fs_key], 4)},
    }


def strongest(r: dict, dq: str) -> dict:
    na, nb = r["players"]["A"]["name"], r["players"]["B"]["name"]
    c = []
    c.append((f"{na} to win", r["win_prob"]["A"], "winner"))
    c.append((f"{nb} to win", r["win_prob"]["B"], "winner"))
    c.append((f"{na} to win at least one set", r["p_a_wins_set"], "set"))
    c.append((f"{nb} to win at least one set", r["p_b_wins_set"], "set"))
    for k, v in r["set_score_probs"].items():
        c.append((f"Exact sets {na} {k.split('-')[0]}-{k.split('-')[1]} {nb}", v, "exact"))
    core_totals = {18.5, 20.5, 22.5, 24.5} if r["best_of"] == 3 else {33.5, 36.5, 38.5, 40.5}
    for o in r["over_under"]:
        if o["line"] not in core_totals:
            continue
        c.append((f"Over {o['line']} games", o["over"], "total")); c.append((f"Under {o['line']} games", o["under"], "total"))
    for h in r["handicaps"]:
        if h["line"] not in (2.5, 4.5):
            continue
        a_fav = r["win_prob"]["A"] >= 0.5
        # informative handicaps only: favourite giving games, underdog receiving games
        for k in ((f"A -{h['line']}", f"B +{h['line']}") if a_fav else (f"B -{h['line']}", f"A +{h['line']}")):
            who = na if k.startswith("A") else nb
            c.append((f"{who} {k[2:]} games", h[k], "handicap"))
    c.append(("At least one tiebreak", r["p_tiebreak"], "tb")); c.append(("No tiebreak", 1 - r["p_tiebreak"], "tb"))
    c.append(("Deciding set played", r["p_decider"], "decider")); c.append(("No deciding set", 1 - r["p_decider"], "decider"))
    # strongest = highest probability outcome that is still informative (prob <= 0.92 excludes near-certain filler lines)
    elig = [x for x in c if x[1] <= 0.92] or c
    sel = max(elig, key=lambda x: x[1])
    p = sel[1]
    conf = "High" if (p >= 0.75 and dq == "HIGH") else ("Medium" if (p >= 0.62 and dq in ("HIGH", "MEDIUM")) else "Low")
    risks = {
        "winner": f"Upset through serve-dominant variance: P(at least one tiebreak) = {r['p_tiebreak']:.0%}; underdog wins {min(r['win_prob'].values()):.0%} of simulations.",
        "set": "Straight-sets result for the opponent; driven by tiebreak/hold variance.",
        "total": f"Total-games distribution is wide (sd {r['games']['sd']} games; p10-p90 {r['games']['p10']:.0f}-{r['games']['p90']:.0f}); a single tiebreak or deciding set swings it.",
        "handicap": "Game margin is highly sensitive to a deciding set and to break-point conversion luck.",
        "tb": "Tiebreak occurrence hinges on hold rates; small serve-model misspecification moves it materially.",
        "decider": "Deciding-set probability depends on the closeness estimate; rating error widens it.",
        "exact": "Exact set scores are low-probability outcomes by nature.",
    }
    risk = risks[sel[2]]
    if dq != "HIGH":
        risk += f" Data quality {dq}: " + "; ".join(r.get("data_quality_notes", [])[:2])
    return {"selection": sel[0], "probability": round(p, 4), "fair_odds": fair_odds(p), "confidence": conf, "main_risk": risk,
            "candidates": sorted([{"selection": x[0], "probability": round(x[1], 4), "fair_decimal": fair_odds(x[1])["decimal"]} for x in c],
                                 key=lambda d: -d["probability"])[:15]}


def drivers(na, nb, comps, contrib, prof_a, prof_b, pa, pb) -> list:
    labels = {"surface_elo": "surface-specific Elo", "overall_elo": "all-surface Elo", "serve_return": "opponent-adjusted serve/return quality", "form": "recent opponent-adjusted form (Elo residual)"}
    order = sorted(contrib.items(), key=lambda kv: -abs(kv[1]))
    out = []
    for k, v in order[:3]:
        fav = na if v > 0 else nb
        out.append({"driver": labels[k], "favours": fav, "elo_points": round(v, 1), "raw_diff": round(comps[k], 1), "type": "statistical"})
    sa, sb = prof_a["stats_52w"], prof_b["stats_52w"]
    if sa and sb:
        out.append({"driver": "second-serve points won (52w)", "A": sa.get("second_won"), "B": sb.get("second_won"), "type": "descriptive"})
        out.append({"driver": "return points won (52w)", "A": sa.get("ret_pts_won"), "B": sb.get("ret_pts_won"), "type": "descriptive"})
    out.append({"driver": "modelled service-point win probability", "A": round(pa, 4), "B": round(pb, 4), "type": "model"})
    out.append({"driver": "tactical/handedness/fatigue/weather", "note": "not modelled unless entered as verified manual context (no fabricated inputs)", "type": "qualitative"})
    return out
