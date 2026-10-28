"""Post-match audit of frozen pre-match forecasts against actual results."""
from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path

import numpy as np

from .data import ESPN_DIR, norm_name
from .freeze import FORECASTS


def _result_index():
    f = ESPN_DIR / "results.json"
    idx = {}
    if f.exists():
        for r in json.loads(f.read_text()).values():
            idx[str(r["espn_id"])] = r
            names = frozenset(norm_name(p["name"]) for p in r["players"])
            idx[(names, r["start"][:10])] = r
    return idx


def load_forecasts(first_version_only=True):
    """The audited forecast is the latest version frozen before the start (all versions are pre-match)."""
    out = []
    for d in sorted(FORECASTS.glob("*/*")):
        files = sorted(d.glob("v*.json"), key=lambda p: int(p.stem[1:]))
        if files:
            out.append(json.loads(files[-1].read_text()))
    return out


def audit(manual_results: dict | None = None) -> dict:
    idx = _result_index()
    rows = []
    for f in load_forecasts():
        A = f["players"]["A"]["name"]; B = f["players"]["B"]["name"]
        r = None
        eid = f.get("freeze", {}).get("espn_id")
        if eid and str(eid) in idx:
            r = idx[str(eid)]
        if r is None or r.get("winner") is None or r.get("status") != "STATUS_FINAL":
            continue
        names = [norm_name(p["name"]) for p in r["players"]]
        a_idx = 0 if names[0] == norm_name(A) else 1
        a_won = int(r["winner"] == a_idx)
        sets = [(s[a_idx], s[1 - a_idx]) for s in r["sets"]]
        sa = sum(1 for x, y in sets if x > y); sb = len(sets) - sa
        games = sum(x + y for x, y in sets); diff = sum(x - y for x, y in sets)
        tb = int(any(sorted(s) == [6, 7] for s in sets))
        p = f["win_prob"]["A"]
        ss = f"{sa}-{sb}"
        hc = {}
        for h in f["handicaps"]:
            x = h["line"]
            hc[f"A -{x}"] = (h[f"A -{x}"], int(diff > x))
        rows.append({"match": f["match"], "date": f["forecast_date"], "tour": f["tour"], "surface": f["surface"],
                     "level": f.get("tournament", ""), "p_a": p, "a_won": a_won,
                     "pred_winner": f["predicted_winner"], "actual_winner": A if a_won else B,
                     "pred_sets": f["most_likely_set_score"], "actual_sets": ss, "p_actual_sets": f["set_score_probs"].get(ss, 0.0),
                     "exp_games": f["games"]["mean"], "actual_games": games, "p_tb": f["p_tiebreak"], "tb": tb,
                     "handicaps": hc, "data_quality": f["data_quality"], "version": f["model"]["version"]})
    return summarize(rows)


def summarize(rows):
    if not rows:
        return {"n": 0, "rows": []}
    p = np.clip(np.array([r["p_a"] for r in rows]), 1e-6, 1 - 1e-6); y = np.array([r["a_won"] for r in rows])

    def m(sel):
        pp, yy = p[sel], y[sel]
        return {"n": int(sel.sum()), "brier": float(np.mean((pp - yy) ** 2)), "log_loss": float(-np.mean(yy * np.log(pp) + (1 - yy) * np.log(1 - pp))),
                "accuracy": float(np.mean((pp > 0.5) == (yy == 1)))}

    allm = np.ones(len(rows), bool)
    out = {"n": len(rows), "overall": m(allm),
           "games_mae": float(np.mean([abs(r["exp_games"] - r["actual_games"]) for r in rows])),
           "exact_sets_accuracy": float(np.mean([r["pred_sets"] == r["actual_sets"] for r in rows])),
           "tiebreak_brier": float(np.mean([(r["p_tb"] - r["tb"]) ** 2 for r in rows])),
           "by_surface": {}, "by_tour": {}, "by_quality": {}, "calibration": [], "rows": rows}
    for key, name in (("surface", "by_surface"), ("tour", "by_tour"), ("data_quality", "by_quality")):
        for v in sorted(set(r[key] for r in rows)):
            out[name][v] = m(np.array([r[key] == v for r in rows]))
    fav = np.where(p >= 0.5, p, 1 - p); won = np.where(p >= 0.5, y, 1 - y)
    for lo, hi in ((0.5, 0.6), (0.6, 0.7), (0.7, 0.8), (0.8, 0.9), (0.9, 1.01)):
        s = (fav >= lo) & (fav < hi)
        if s.sum():
            out["calibration"].append({"bucket": f"{lo:.0%}-{min(hi, 1):.0%}", "n": int(s.sum()), "mean_pred": float(fav[s].mean()), "observed": float(won[s].mean())})
    return out
