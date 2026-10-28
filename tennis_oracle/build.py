"""Build rating engines from all available independent data."""
from __future__ import annotations

import json
from datetime import timedelta
from difflib import get_close_matches

import pandas as pd

from .config import load_params
from .data import ESPN_DIR, espn_results_frame, load_sackmann, norm_name
from .ratings import RatingEngine, run_history


def sackmann_end(df: pd.DataFrame) -> pd.Timestamp:
    """Effective last day covered by the point-level data (tourney_date is the week start)."""
    last = df["date"].max()
    lvl = df.loc[df["date"] == last, "tourney_level"]
    return last + timedelta(days=14 if (lvl == "G").any() else 7)


def combined_frame(tour: str, use_espn: bool = True):
    df = load_sackmann(tour)
    end = sackmann_end(df)
    info = {"sackmann_last_tourney_date": str(df["date"].max().date()), "point_stats_cutoff": str(end.date()),
            "sackmann_matches": int(len(df)), "espn_matches": 0, "results_cutoff": str(end.date())}
    if use_espn and (ESPN_DIR / "results.json").exists():
        recs = list(json.loads((ESPN_DIR / "results.json").read_text()).values())
        recs = [r for r in recs if r["tour"] == tour]
        name_index = {}
        for nm, pid in zip(pd.concat([df["winner_name"], df["loser_name"]]), pd.concat([df["winner_id"], df["loser_id"]])):
            name_index[(tour, norm_name(nm))] = pid
        # ESPN sometimes orders Chinese names family-first; index both orders
        for (t, nm), pid in list(name_index.items()):
            parts = nm.split()
            if len(parts) == 2:
                name_index.setdefault((t, f"{parts[1]} {parts[0]}"), pid)
        ex = espn_results_frame(recs, df, name_index, end)
        if not ex.empty:
            ex = ex[ex["date"] > end]
            info["espn_matches"] = int(len(ex))
            info["results_cutoff"] = str(ex["date"].max().date())
            df = pd.concat([df, ex.drop(columns=["start"])], ignore_index=True)
    return df, info


def build_engine(tour: str, params: dict | None = None, use_espn: bool = True, on_match=None):
    params = params or load_params()
    df, info = combined_frame(tour, use_espn)
    eng = RatingEngine(params)
    run_history(eng, df, on_match)
    eng.info = info
    eng.point_cutoff = pd.Timestamp(info["point_stats_cutoff"])
    return eng


def find_player(engine: RatingEngine, name: str) -> str:
    q = norm_name(name)
    idx = {}
    for pid, p in engine.players.items():
        k = norm_name(p.name)
        if k not in idx or (p.last_date is not None and (idx[k][1] is None or p.last_date > idx[k][1])):
            idx[k] = (pid, p.last_date)
    if q in idx:
        return idx[q][0]
    # surname / partial match, prefer most recently active
    cands = [(k, v) for k, v in idx.items() if q in k or all(t in k.split() for t in q.split())]
    if cands:
        cands.sort(key=lambda kv: kv[1][1] or pd.Timestamp(0), reverse=True)
        return cands[0][1][0]
    m = get_close_matches(q, list(idx), n=1, cutoff=0.8)
    if m:
        return idx[m[0]][0]
    raise KeyError(f"Player not found in data: {name!r}")
