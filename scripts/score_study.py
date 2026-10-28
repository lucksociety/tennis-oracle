"""Walk-forward score-model study (no look-ahead).

Win probability is pinned by the composite rating (the serve split is solved to hit it), so the
per-match variation sigma only shapes the *score* distribution (set scores, games, tiebreaks).
sigma was originally estimated from serve-point residual variance; here we test multipliers of it
and point-prediction rules, selecting on VALIDATION (2024) only and reporting untouched TEST (2025-26).
Usage: python scripts/score_study.py  -> writes models/score_study.json
"""
import json, math, sys, time
from copy import deepcopy
from pathlib import Path
import numpy as np, pandas as pd
ROOT = Path(__file__).resolve().parent.parent; sys.path.insert(0, str(ROOT))
from tennis_oracle.backtest import collect, COMP
from tennis_oracle.config import load_params
from tennis_oracle.grid import mixture_field, get_grid

t0 = time.time()
P = load_params()
frames = [collect(t, P)[0] for t in ("atp", "wta")]
df = pd.concat(frames, ignore_index=True)
yr = df.date.dt.year
parts = {"train": df[yr.between(2014, 2023)], "validation": df[yr == 2024], "test": df[yr >= 2025]}
print("collected", {k: len(v) for k, v in parts.items()}, f"{time.time()-t0:.0f}s", flush=True)

def evaluate(part, sigma, rho, nodes=5):
    w = P["weights"]; s = P["composite_scale"]
    D = s * sum(w[k] * part[f"c_{k}"].values for k in COMP); target = 1 / (1 + 10 ** (-D / 400))
    la = np.log(part.pa_sr.values / (1 - part.pa_sr.values)); lb = np.log(part.pb_sr.values / (1 - part.pb_sr.values))
    lo = np.full(len(part), -3.0); hi = np.full(len(part), 3.0)
    for _ in range(30):
        mid = (lo + hi) / 2
        p = mixture_field("p_win", 1/(1+np.exp(-(la+mid))), 1/(1+np.exp(-(lb-mid))), 3, "tb7", sigma, rho, nodes)
        lo = np.where(p < target, mid, lo); hi = np.where(p < target, hi, mid)
    mid = (lo + hi) / 2; pa = 1/(1+np.exp(-(la+mid))); pb = 1/(1+np.exp(-(lb-mid)))
    R = {"set_ll": [], "games_ll": [], "mae_mean": [], "mae_median": [], "acc_top": [], "acc_wincond": [], "tb_brier": [], "p": [], "y": []}
    for (bo, fr), idx in part.groupby(["bo", "fr"]).groups.items():
        ii = part.index.get_indexer(idx); g = get_grid(bo, fr); sub = part.loc[idx]
        pw = mixture_field("p_win", pa[ii], pb[ii], bo, fr, sigma, rho, nodes)
        st = mixture_field("sets", pa[ii], pb[ii], bo, fr, sigma, rho, nodes); st = st / st.sum(1, keepdims=True)
        gm = mixture_field("games", pa[ii], pb[ii], bo, fr, sigma, rho, nodes); gm = np.clip(gm, 0, None); gm = gm / gm.sum(1, keepdims=True)
        tb = mixture_field("p_tb", pa[ii], pb[ii], bo, fr, sigma, rho, nodes)
        keys = [f"{a}-{b}" for a, b in g["set_keys"]]; aw = np.array([int(k.split("-")[0]) > int(k.split("-")[1]) for k in keys])
        act = (sub.sets_a.astype(str) + "-" + sub.sets_b.astype(str)).values
        k_idx = np.array([keys.index(a) if a in keys else -1 for a in act]); ok = k_idx >= 0
        R["set_ll"] += list(-np.log(np.clip(st[np.arange(len(ii)), np.clip(k_idx, 0, None)], 1e-6, 1))[ok])
        top = st.argmax(1)
        wc = np.where(pw[:, None] >= 0.5, np.where(aw, st, -1), np.where(~aw, st, -1)).argmax(1)
        R["acc_top"] += list((top == k_idx)[ok]); R["acc_wincond"] += list((wc == k_idx)[ok])
        G = sub.games.values.astype(int); inr = G < gm.shape[1]
        R["games_ll"] += list(-np.log(np.clip(gm[np.arange(len(ii)), np.clip(G, 0, gm.shape[1]-1)], 1e-6, 1))[inr])
        mean = (gm * np.arange(gm.shape[1])).sum(1); med = (gm.cumsum(1) >= 0.5).argmax(1)
        R["mae_mean"] += list(np.abs(mean - G)); R["mae_median"] += list(np.abs(med - G))
        R["tb_brier"] += list((tb - sub.tb.values) ** 2); R["p"] += list(pw); R["y"] += list(sub.y.values)
    p = np.clip(np.array(R.pop("p")), 1e-6, 1-1e-6); y = np.array(R.pop("y"))
    out = {k: round(float(np.mean(v)), 5) for k, v in R.items()}
    out["win_log_loss"] = round(float(-np.mean(y*np.log(p)+(1-y)*np.log(1-p))), 5); out["n"] = int(len(y))
    out["score_ll"] = round(out["set_ll"] + out["games_ll"], 5)
    return out

sig0, rho0 = P["variation"]["sigma"], P["variation"]["rho"]
res = {"base_sigma": sig0, "rho": rho0, "validation": {}, "test": {}}
mults = [0.0, 0.6, 0.8, 0.9, 1.0, 1.1, 1.2, 1.4, 1.8, 2.2]
for m in mults:
    res["validation"][str(m)] = evaluate(parts["validation"], sig0 * m, rho0)
    print("val", m, res["validation"][str(m)], f"{time.time()-t0:.0f}s", flush=True)
best = min(mults, key=lambda m: res["validation"][str(m)]["score_ll"])
res["selected_multiplier"] = best; res["selected_sigma"] = round(sig0 * best, 4)
for m in sorted({1.0, best}):
    res["test"][str(m)] = evaluate(parts["test"], sig0 * m, rho0)
    print("test", m, res["test"][str(m)], flush=True)
v = res["validation"][str(best)]
res["games_point_rule"] = "median" if v["mae_median"] < v["mae_mean"] else "mean"
(ROOT / "models" / "score_study.json").write_text(json.dumps(res, indent=1))
print("DONE", best, res["games_point_rule"], f"{time.time()-t0:.0f}s")
