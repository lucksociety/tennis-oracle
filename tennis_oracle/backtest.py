"""Walk-forward backtest. Every prediction uses only matches completed before it
(ratings are updated after the prediction is recorded). Parameters (variation sigma/rho,
composite scale, optional weight refit) are fitted on TRAIN years only, selected on
VALIDATION, and reported on untouched TEST years."""
from __future__ import annotations

import json
import math
from copy import deepcopy

import numpy as np
import pandas as pd

from .build import build_engine
from .config import load_params
from .data import parse_score
from .grid import mixture_field, get_grid
from .ratings import elo_prob

COMP = ["surface_elo", "serve_return", "overall_elo", "form"]


def collect(tour: str, params: dict, start="2014-01-01"):
    rows = []
    rng = np.random.default_rng(7)
    start = pd.Timestamp(start)

    def on_match(eng, r):
        if r["date"] < start:
            return
        sets, tbs, complete = parse_score(r.get("score"))
        if not complete or not sets:
            return
        surf = r["surface"] if r["surface"] in ("Hard", "Clay", "Grass") else "Hard"
        w = eng.players.get(r["winner_id"]); l = eng.players.get(r["loser_id"])
        if w is None or l is None or w.n < 5 or l.n < 5:   # need some history (both sides)
            return
        flip = rng.random() < 0.5
        A, B = (l, w) if flip else (w, l)
        comps, pa, pb = eng.components(A, B, r["tour"], surf, r["date"])
        gw = sum(a for a, b in sets); gl = sum(b for a, b in sets)
        sw = sum(1 for a, b in sets if a > b); sl = len(sets) - sw
        y = 0 if flip else 1
        bo = int(r.get("best_of") or 3)
        fr = "tb10" if (r.get("tourney_level") == "G" and r["date"].year >= 2022) else "tb7"
        rows.append({
            "date": r["date"], "tour": r["tour"], "surface": surf, "level": r.get("tourney_level"), "bo": bo, "fr": fr,
            "source": r.get("source", "sackmann"), "y": y, **{f"c_{k}": comps[k] for k in COMP},
            "pa_sr": pa, "pb_sr": pb, "games": gw + gl, "diff": (gw - gl) if y else (gl - gw),
            "tb": int(tbs > 0), "sets_a": sw if y else sl, "sets_b": sl if y else sw,
            "p_surface_elo": elo_prob(A.selo[surf] - B.selo[surf]),
            "p_blend_elo": elo_prob(0.5 * (A.selo[surf] - B.selo[surf]) + 0.5 * (A.elo - B.elo)),
            "nA": A.n, "nB": B.n,
        })

    eng = build_engine(tour, params, use_espn=True, on_match=on_match)
    return pd.DataFrame(rows), eng


def estimate_variation(eng, years):
    """Excess (beyond-binomial) per-match variance of logit serve-point residuals, and the
    correlation between the two players' residuals within a match."""
    lg = pd.DataFrame(eng.resid_log, columns=["tour", "surface", "resid", "n", "p", "date", "pid"])
    lg["pair"] = np.arange(len(lg)) // 2
    lg = lg[lg["date"].dt.year.isin(years)]
    binom = 1.0 / (lg["n"] * lg["p"] * (1 - lg["p"]))
    excess = max(1e-6, float(lg["resid"].var() - binom.mean()))
    piv = lg.groupby("pair")["resid"].agg(list)
    piv = piv[piv.map(len) == 2]
    a = np.array([x[0] for x in piv]); b = np.array([x[1] for x in piv])
    cov = np.cov(a, b)[0, 1]
    rho = float(np.clip(cov / excess, -0.9, 0.9))
    return float(math.sqrt(excess)), rho


def logistic_fit(X, y, l2=1e-6, iters=60):
    """Newton-Raphson logistic regression without intercept (symmetric orientation)."""
    beta = np.zeros(X.shape[1])
    for _ in range(iters):
        z = X @ beta
        p = 1 / (1 + np.exp(-z))
        g = X.T @ (y - p) - l2 * beta
        H = (X * (p * (1 - p))[:, None]).T @ X + l2 * np.eye(X.shape[1])
        step = np.linalg.solve(H, g)
        beta += step
        if np.abs(step).max() < 1e-9:
            break
    return beta


def oracle_probs(df: pd.DataFrame, params: dict) -> pd.DataFrame:
    """Vectorised version of predict.forecast using exact-model grids."""
    w = params["weights"]; s = params["composite_scale"]
    D = s * sum(w[k] * df[f"c_{k}"].values for k in COMP)
    target = 1 / (1 + 10 ** (-D / 400))
    sig, rho = params["variation"]["sigma"], params["variation"]["rho"]
    la = np.log(df.pa_sr.values / (1 - df.pa_sr.values)); lb = np.log(df.pb_sr.values / (1 - df.pb_sr.values))
    lo = np.full(len(df), -3.0); hi = np.full(len(df), 3.0)
    for _ in range(30):
        mid = (lo + hi) / 2
        pa = 1 / (1 + np.exp(-(la + mid))); pb = 1 / (1 + np.exp(-(lb - mid)))
        p = mixture_field("p_win", pa, pb, 3, "tb7", sig, rho, 3)
        lo = np.where(p < target, mid, lo); hi = np.where(p < target, hi, mid)
    mid = (lo + hi) / 2
    pa = 1 / (1 + np.exp(-(la + mid))); pb = 1 / (1 + np.exp(-(lb - mid)))
    out = pd.DataFrame(index=df.index)
    out["p"] = np.nan; out["e_games"] = np.nan; out["p_tb"] = np.nan; out["top_set"] = ""; out["p_set_actual"] = np.nan
    for (bo, fr), idx in df.groupby(["bo", "fr"]).groups.items():
        ii = df.index.get_indexer(idx)
        g = get_grid(bo, fr)
        out.loc[idx, "p"] = mixture_field("p_win", pa[ii], pb[ii], bo, fr, sig, rho, 3)
        gm = mixture_field("games", pa[ii], pb[ii], bo, fr, sig, rho, 3)
        out.loc[idx, "e_games"] = (gm * np.arange(gm.shape[1])).sum(1) / gm.sum(1)
        out.loc[idx, "p_tb"] = mixture_field("p_tb", pa[ii], pb[ii], bo, fr, sig, rho, 3)
        st = mixture_field("sets", pa[ii], pb[ii], bo, fr, sig, rho, 3)
        keys = [f"{a}-{b}" for a, b in g["set_keys"]]
        out.loc[idx, "top_set"] = [keys[k] for k in st.argmax(1)]
        act = (df.loc[idx, "sets_a"].astype(str) + "-" + df.loc[idx, "sets_b"].astype(str)).values
        kidx = np.array([keys.index(a) if a in keys else -1 for a in act])
        out.loc[idx, "p_set_actual"] = np.where(kidx >= 0, st[np.arange(len(ii)), np.clip(kidx, 0, None)], np.nan)
    return out


def metrics(y, p):
    p = np.clip(np.asarray(p, float), 1e-6, 1 - 1e-6); y = np.asarray(y, float)
    return {"n": int(len(y)), "brier": float(np.mean((p - y) ** 2)),
            "log_loss": float(-np.mean(y * np.log(p) + (1 - y) * np.log(1 - p))),
            "accuracy": float(np.mean((p > 0.5) == (y == 1)))}


def calibration(y, p, bins=(0.5, 0.6, 0.7, 0.8, 0.9, 1.0)):
    """Reliability by favourite-probability bucket (orientation: the model's favourite)."""
    p = np.asarray(p); y = np.asarray(y)
    fav = np.where(p >= 0.5, p, 1 - p); won = np.where(p >= 0.5, y, 1 - y)
    out = []
    lo = 0.5
    for hi in bins[1:]:
        m = (fav >= lo) & (fav < hi if hi < 1 else fav <= hi)
        if m.sum():
            out.append({"bucket": f"{lo:.0%}-{hi:.0%}", "n": int(m.sum()), "mean_pred": round(float(fav[m].mean()), 4), "observed": round(float(won[m].mean()), 4)})
        lo = hi
    return out


def run(tours=("atp", "wta"), train=range(2014, 2024), valid=(2024,), test=(2025, 2026), out_path=None, promote=True):
    base = load_params("v1.0") if False else deepcopy(load_params())
    base["version"] = "v1.0"
    frames, engines = [], {}
    for t in tours:
        d, e = collect(t, base)
        frames.append(d); engines[t] = e
    df = pd.concat(frames, ignore_index=True)
    yr = df.date.dt.year
    tr, va, te = df[yr.isin(train)], df[yr.isin(valid)], df[yr.isin(test)]
    # ---- fit variation on training years only (pooled tours, separate estimates reported)
    var = {t: estimate_variation(engines[t], list(train)) for t in tours}
    sig = float(np.mean([v[0] for v in var.values()])); rho = float(np.mean([v[1] for v in var.values()]))
    p10 = deepcopy(base); p10["variation"] = {"sigma": round(sig, 4), "rho": round(rho, 4), "by_tour": {t: [round(a, 4), round(b, 4)] for t, (a, b) in var.items()}}
    # ---- fit composite scale on training years with the spec's starting weights
    w = p10["weights"]
    x = sum(w[k] * tr[f"c_{k}"].values for k in COMP) * math.log(10) / 400
    s = logistic_fit(x[:, None], tr.y.values)[0]
    p10["composite_scale"] = round(float(s), 4)
    p10["fit"] = {"train_years": list(train), "validation_years": list(valid), "test_years": list(test)}
    # ---- candidate v1.1: weights re-estimated on training years (chronological), normalised
    X = np.column_stack([tr[f"c_{k}"].values for k in COMP]) * math.log(10) / 400
    beta = logistic_fit(X, tr.y.values, l2=1.0)
    p11 = deepcopy(p10); p11["version"] = "v1.1-candidate"
    tot = float(np.sum(beta))
    p11["weights"] = {**{k: round(float(b / tot * 0.95), 4) for k, b in zip(COMP, beta)}, "context": 0.05}
    p11["composite_scale"] = round(tot / 0.95, 4)
    p11["description"] = "Weights re-estimated by logistic regression on train years; promoted only if better on validation."
    res = {"data": {t: engines[t].info for t in tours}, "variation_fit": p10["variation"], "params_v1.0": p10, "params_v1.1_candidate": p11}

    def evaluate(part, params, name):
        o = oracle_probs(part, params)
        r = {"winner": metrics(part.y, o.p), "surface_elo_baseline": metrics(part.y, part.p_surface_elo),
             "blend_elo_baseline": metrics(part.y, part.p_blend_elo)}
        r["games_mae"] = float(np.mean(np.abs(o.e_games - part.games)))
        r["tiebreak_brier"] = float(np.mean((o.p_tb - part.tb) ** 2))
        r["tiebreak_base_rate_brier"] = float(np.mean((tr.tb.mean() - part.tb) ** 2))
        act = part.sets_a.astype(str) + "-" + part.sets_b.astype(str)
        r["exact_set_score_accuracy"] = float(np.mean(o.top_set.values == act.values))
        r["exact_set_score_log_loss"] = float(-np.nanmean(np.log(np.clip(o.p_set_actual, 1e-6, 1))))
        mean_games = tr.groupby(["tour", "bo"]).games.mean()
        naive = np.array([mean_games.get((t_, b_), part.games.mean()) for t_, b_ in zip(part.tour, part.bo)])
        r["games_mae_naive_mean"] = float(np.mean(np.abs(naive - part.games)))
        r["calibration"] = calibration(part.y.values, o.p.values)
        r["by_tour_surface"] = {}
        for (t_, s_), g in part.groupby(["tour", "surface"]):
            oo = o.loc[g.index]
            r["by_tour_surface"][f"{t_}/{s_}"] = {"oracle": metrics(g.y, oo.p), "surface_elo": metrics(g.y, g.p_surface_elo),
                                                 "games_mae": float(np.mean(np.abs(oo.e_games - g.games))),
                                                 "calibration": calibration(g.y.values, oo.p.values)}
        r["by_source"] = {src: {"oracle": metrics(g.y, o.loc[g.index].p), "surface_elo": metrics(g.y, g.p_surface_elo)} for src, g in part.groupby("source")}
        return r, o

    res["validation"] = {}
    for nm, pp in (("v1.0", p10), ("v1.1-candidate", p11)):
        res["validation"][nm], _ = evaluate(va, pp, nm)
    better = res["validation"]["v1.1-candidate"]["winner"]["log_loss"] < res["validation"]["v1.0"]["winner"]["log_loss"] - 1e-4
    chosen = p11 if better else p10
    res["promoted"] = "v1.1" if better else "v1.0"
    if better:
        chosen = deepcopy(p11); chosen["version"] = "v1.1"
    res["test"] = {}
    for nm, pp in (("v1.0", p10), ("v1.1-candidate", p11)):
        r, o = evaluate(te, pp, nm)
        res["test"][nm] = r
    # sample of test predictions for the dashboard reliability chart
    o = oracle_probs(te, chosen)
    res["test_points"] = [{"p": round(float(a), 4), "y": int(b), "tour": t_, "surface": s_, "src": src}
                          for a, b, t_, s_, src in zip(o.p, te.y, te.tour, te.surface, te.source)]
    res["counts"] = {"train": int(len(tr)), "validation": int(len(va)), "test": int(len(te))}
    return res, p10, chosen
