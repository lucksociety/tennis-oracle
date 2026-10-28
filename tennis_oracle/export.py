"""Export current ratings + model parameters for the in-browser dashboard engine."""
from __future__ import annotations

import json
import math
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from .ratings import SURFACES

ROOT = Path(__file__).resolve().parent.parent
DOCS_DATA = ROOT / "docs" / "data"
STAT_KEYS = ["first_in", "first_won", "second_won", "serve_pts_won", "hold", "aces_per_svgm", "df_per_svgm", "bp_saved",
             "ret_pts_won", "ret_1st_won", "ret_2nd_won", "ret_games_won", "bp_conv", "matches_with_stats"]


def export_ratings(engines: dict, params: dict, out: Path | None = None, active_days: int = 730) -> Path:
    DOCS_DATA.mkdir(parents=True, exist_ok=True)
    out = out or DOCS_DATA / "ratings.json"
    players = []
    info = {}
    for tour, eng in engines.items():
        asof = pd.Timestamp(eng.info["results_cutoff"])
        info[tour] = eng.info
        for pid, p in eng.players.items():
            gap = None if p.last_date is None else (asof - p.last_date).days
            if gap is None or gap > active_days or (p.n < 3 and gap > 365):
                continue
            prof = eng.profile(p, "Hard", asof)
            st = prof["stats_52w"]
            players.append({
                "id": pid, "n": p.name, "t": tour, "h": p.hand, "rk": p.rank,
                "age": round(prof["age"], 1) if prof["age"] else None,
                "e": round(p.elo, 1), "se": [round(p.selo[s], 1) for s in SURFACES], "sn": [p.sn[s] for s in SURFACES],
                "S": [round(eng.eff_S(p, s, asof), 4) for s in SURFACES], "R": [round(eng.eff_R(p, s, asof), 4) for s in SURFACES],
                "f": round(eng.form_value(p), 1), "m": p.n, "l52": prof["last52w"], "l10": prof["last10"],
                "lm": prof["last_match"], "ls": prof["last_stats_match"],
                "sw": [p.surf_wl[s] for s in SURFACES],
                "gw": prof["games_won_lost_52w"], "sl": prof["sets_won_lost_52w"],
                "st": {k: st.get(k) for k in STAT_KEYS if k in st},
            })
    base = {}
    for tour, eng in engines.items():
        base[tour] = {s: round(eng.base[(tour, s)], 5) for s in SURFACES}
    doc = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "model_version": params["version"], "params": params, "baseline_logit": base, "surfaces": list(SURFACES),
        "data": info,
        "sources": ["Jeff Sackmann tennis_atp / tennis_wta match data (CC BY-NC-SA 4.0), via archival mirror",
                    "ESPN public scoreboard results (scores only) after the point-level cutoff"],
        "no_market_data": True,
        "players": players,
    }
    out.write_text(json.dumps(doc, separators=(",", ":")))
    return out
