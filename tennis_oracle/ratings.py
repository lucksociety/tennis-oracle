"""Chronological rating engine: surface + overall Elo, opponent-adjusted serve/return
ratings with shrinkage, recent form, and descriptive player profiles.

Every quantity is computed strictly from matches *before* the one being predicted
(walk-forward), so the same engine drives both backtests and live forecasts.
"""
from __future__ import annotations

import math
from collections import defaultdict, deque
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from .data import parse_score

SURFACES = ("Hard", "Clay", "Grass")


def logit(p):
    p = min(max(p, 1e-4), 1 - 1e-4)
    return math.log(p / (1 - p))


def sigmoid(x):
    return 1 / (1 + math.exp(-x))


def elo_prob(diff):
    return 1 / (1 + 10 ** (-diff / 400))


@dataclass
class Player:
    pid: str
    name: str
    tour: str
    hand: str = "U"
    elo: float = 1500.0
    n: int = 0
    selo: dict = field(default_factory=lambda: {s: 1500.0 for s in SURFACES})
    sn: dict = field(default_factory=lambda: {s: 0 for s in SURFACES})
    S: float = 0.0          # serve rating (logit) all surfaces
    R: float = 0.0          # return rating
    sr_n: float = 0.0
    dS: dict = field(default_factory=lambda: {s: 0.0 for s in SURFACES})
    dR: dict = field(default_factory=lambda: {s: 0.0 for s in SURFACES})
    dsr_n: dict = field(default_factory=lambda: {s: 0.0 for s in SURFACES})
    form: float = 0.0
    form_w: float = 0.0
    form_n: int = 0
    last_date: pd.Timestamp | None = None
    last_stats_date: pd.Timestamp | None = None
    age: float | None = None
    age_date: pd.Timestamp | None = None
    rank: float | None = None
    rank_hist: list = field(default_factory=list)       # (date, rank)
    surf_wl: dict = field(default_factory=lambda: {s: [0, 0] for s in SURFACES})
    log: deque = field(default_factory=lambda: deque(maxlen=120))   # recent match records


class RatingEngine:
    def __init__(self, params: dict):
        self.p = params
        self.players: dict[str, Player] = {}
        # tour/surface baseline serve-point win rate (logit), learned online
        self.base = defaultdict(lambda: logit(0.63))
        self.base_init = {("atp", "Hard"): logit(0.64), ("atp", "Clay"): logit(0.61), ("atp", "Grass"): logit(0.66),
                          ("wta", "Hard"): logit(0.565), ("wta", "Clay"): logit(0.545), ("wta", "Grass"): logit(0.575)}
        for k, v in self.base_init.items():
            self.base[k] = v
        self.resid_log = []   # (tour, surface, logit residual, svpt, p_pred) for variation estimation
        self.cutoff = None

    # ------------------------------------------------------------ helpers
    def get(self, pid, name, tour, hand="U"):
        pl = self.players.get(pid)
        if pl is None:
            pl = Player(pid, name, tour, hand if isinstance(hand, str) else "U")
            self.players[pid] = pl
        return pl

    def k(self, n, slam=False):
        e = self.p["elo"]
        return e["k_num"] / ((n + e["k_off"]) ** e["k_pow"]) * (e["slam_mult"] if slam else 1.0)

    def serve_logit(self, server: Player, returner: Player, tour, surface, date=None):
        """Opponent-adjusted logit P(server wins a service point) on `surface`."""
        b = self.base[(tour, surface)]
        return b + self.eff_S(server, surface, date) - self.eff_R(returner, surface, date)

    def _decay(self, pl: Player, date):
        if date is None or pl.last_stats_date is None:
            return 1.0
        days = (date - pl.last_stats_date).days
        return math.exp(-max(0, days - 60) / self.p["sr"]["decay_days"])

    def eff_S(self, pl, surface, date=None):
        return (pl.S + pl.dS[surface]) * self._decay(pl, date)

    def eff_R(self, pl, surface, date=None):
        return (pl.R + pl.dR[surface]) * self._decay(pl, date)

    def form_value(self, pl: Player):
        sh = self.p["form"]["shrink_n"]
        f = pl.form * pl.form_n / (pl.form_n + sh)
        f = max(min(f, 0.45), -0.45)
        return 400 * math.log10((0.5 + f) / (0.5 - f))     # Elo-point equivalent

    def components(self, a: Player, b: Player, tour, surface, date=None):
        """Raw (unweighted) Elo-point differences A - B for each composite component,
        plus point-level serve probabilities implied by serve/return ratings."""
        from .grid import win_prob_fast
        la = self.serve_logit(a, b, tour, surface, date)
        lb = self.serve_logit(b, a, tour, surface, date)
        pa, pb = sigmoid(la), sigmoid(lb)
        p_sr = float(win_prob_fast(pa, pb, 3))
        p_sr = min(max(p_sr, 1e-4), 1 - 1e-4)
        cap = self.p["sr_elo_cap"]
        d_sr = max(-cap, min(cap, 400 * math.log10(p_sr / (1 - p_sr))))
        return {
            "surface_elo": a.selo[surface] - b.selo[surface],
            "overall_elo": a.elo - b.elo,
            "serve_return": d_sr,
            "form": self.form_value(a) - self.form_value(b),
        }, pa, pb

    # ------------------------------------------------------------ update
    def update(self, r):
        """Apply one completed match (row with winner/loser fields)."""
        tour, surf = r["tour"], r["surface"]
        if surf not in SURFACES:
            surf = "Hard"
        date = r["date"]
        w = self.get(r["winner_id"], r["winner_name"], tour, r.get("winner_hand", "U"))
        l = self.get(r["loser_id"], r["loser_name"], tour, r.get("loser_hand", "U"))
        sets, tbs, complete = parse_score(r.get("score"))
        if not complete or not sets:
            return
        slam = r.get("tourney_level") == "G"
        # ---- Elo expectation (pre-update) for form
        exp_w = elo_prob(w.elo - l.elo)
        # ---- overall Elo
        kw, kl = self.k(w.n, slam), self.k(l.n, slam)
        w.elo += kw * (1 - exp_w); l.elo -= kl * (1 - exp_w)
        w.n += 1; l.n += 1
        # ---- surface Elo
        es = elo_prob(w.selo[surf] - l.selo[surf])
        kws, kls = self.k(w.sn[surf], slam), self.k(l.sn[surf], slam)
        w.selo[surf] += kws * (1 - es); l.selo[surf] -= kls * (1 - es)
        w.sn[surf] += 1; l.sn[surf] += 1
        # ---- form (EW residual vs Elo expectation)
        hl = self.p["form"]["halflife_matches"]
        a = 1 - 0.5 ** (1 / hl)
        for pl, res in ((w, 1 - exp_w), (l, -(1 - exp_w))):
            pl.form = (1 - a) * pl.form + a * res
            pl.form_n += 1
        # ---- serve / return
        has_stats = all(pd.notna(r.get(c)) for c in ("w_svpt", "w_1stWon", "w_2ndWon", "l_svpt", "l_1stWon", "l_2ndWon")) \
            and r.get("w_svpt", 0) and r.get("l_svpt", 0) and r["w_svpt"] >= 20 and r["l_svpt"] >= 20
        if has_stats:
            sp = self.p["sr"]
            for srv, ret, pre in ((w, l, "w_"), (l, w, "l_")):
                n = r[pre + "svpt"]
                won = r[pre + "1stWon"] + r[pre + "2ndWon"]
                obs = (won + 0.5) / (n + 1.0)
                pred_l = self.serve_logit(srv, ret, tour, surf, date)
                pred = sigmoid(pred_l)
                resid = logit(obs) - pred_l
                self.resid_log.append((tour, surf, resid, n, pred, date, srv.pid))
                # overall ratings, eta ~ 1/(n_matches + prior) with floor (=> shrinkage to baseline)
                es_ = max(sp["eta_min"], 1.0 / (srv.sr_n + sp["prior_n"]))
                er_ = max(sp["eta_min"], 1.0 / (ret.sr_n + sp["prior_n"]))
                srv.S += es_ * resid
                ret.R -= er_ * resid
                # surface offsets learn the residual left after the overall update
                resid2 = logit(obs) - self.serve_logit(srv, ret, tour, surf, date)
                es2 = max(sp["eta_min_surface"], 1.0 / (srv.dsr_n[surf] + sp["surface_prior_n"]))
                er2 = max(sp["eta_min_surface"], 1.0 / (ret.dsr_n[surf] + sp["surface_prior_n"]))
                srv.dS[surf] += es2 * resid2 * 0.5
                ret.dR[surf] -= er2 * resid2 * 0.5
                # tour/surface baseline drift
                self.base[(tour, surf)] += sp["baseline_eta"] * resid
            for pl in (w, l):
                pl.sr_n += 1
                pl.dsr_n[surf] += 1
                pl.last_stats_date = date
        # ---- descriptive log
        gw = sum(a_ for a_, b_ in sets); gl = sum(b_ for a_, b_ in sets)
        sw = sum(1 for a_, b_ in sets if a_ > b_); sl = len(sets) - sw
        for pl, opp, won, pre, opre in ((w, l, 1, "w_", "l_"), (l, w, 0, "l_", "w_")):
            rec = {"date": date, "opp": opp.name, "opp_elo": opp.elo, "surface": surf, "won": won,
                   "gw": gw if won else gl, "gl": gl if won else gw, "sw": sw if won else sl, "sl": sl if won else sw,
                   "tourney": r.get("tourney_name"), "round": r.get("round"), "src": r.get("source", "sackmann")}
            if has_stats:
                for c in ("svpt", "1stIn", "1stWon", "2ndWon", "ace", "df", "SvGms", "bpSaved", "bpFaced"):
                    rec[c] = float(r.get(pre + c) or 0)
                for c in ("svpt", "1stIn", "1stWon", "2ndWon", "SvGms", "bpSaved", "bpFaced"):
                    rec["o_" + c] = float(r.get(opre + c) or 0)
            pl.log.append(rec)
            pl.surf_wl[surf][0 if won else 1] += 1
            pl.last_date = date
            age = r.get(("winner" if won else "loser") + "_age")
            if pd.notna(age) if age is not None else False:
                pl.age, pl.age_date = float(age), date
            rk = r.get(("winner" if won else "loser") + "_rank")
            if rk is not None and pd.notna(rk):
                pl.rank = float(rk)
                if not pl.rank_hist or pl.rank_hist[-1][1] != rk:
                    pl.rank_hist.append((date, float(rk)))
                    pl.rank_hist = pl.rank_hist[-60:]
        self.cutoff = date if self.cutoff is None or date > self.cutoff else self.cutoff

    # ------------------------------------------------------------ profiles
    def profile(self, pl: Player, surface: str, asof: pd.Timestamp) -> dict:
        log = list(pl.log)
        yr = [m for m in log if (asof - m["date"]).days <= 365]

        def wl(ms):
            return [sum(m["won"] for m in ms), sum(1 - m["won"] for m in ms)]

        st = [m for m in yr if "svpt" in m and m["svpt"] > 0]
        agg = defaultdict(float)
        for m in st:
            for k, v in m.items():
                if isinstance(v, float) and k not in ("opp_elo",):
                    agg[k] += v
        stats = {}
        if agg["svpt"] > 0:
            second = agg["svpt"] - agg["1stIn"]
            stats = {
                "first_in": agg["1stIn"] / agg["svpt"],
                "first_won": agg["1stWon"] / max(agg["1stIn"], 1),
                "second_won": agg["2ndWon"] / max(second, 1),
                "serve_pts_won": (agg["1stWon"] + agg["2ndWon"]) / agg["svpt"],
                "hold": 1 - (agg["bpFaced"] - agg["bpSaved"]) / max(agg["SvGms"], 1),
                "aces_per_svgm": agg["ace"] / max(agg["SvGms"], 1),
                "df_per_svgm": agg["df"] / max(agg["SvGms"], 1),
                "bp_saved": agg["bpSaved"] / max(agg["bpFaced"], 1),
                "ret_pts_won": 1 - (agg["o_1stWon"] + agg["o_2ndWon"]) / max(agg["o_svpt"], 1),
                "ret_1st_won": 1 - agg["o_1stWon"] / max(agg["o_1stIn"], 1),
                "ret_2nd_won": 1 - agg["o_2ndWon"] / max(agg["o_svpt"] - agg["o_1stIn"], 1),
                "ret_games_won": (agg["o_bpFaced"] - agg["o_bpSaved"]) / max(agg["o_SvGms"], 1),
                "bp_conv": (agg["o_bpFaced"] - agg["o_bpSaved"]) / max(agg["o_bpFaced"], 1),
                "bp_chances_per_retgm": agg["o_bpFaced"] / max(agg["o_SvGms"], 1),
                "matches_with_stats": len(st),
            }
        last20 = log[-20:]
        return {
            "name": pl.name, "tour": pl.tour, "hand": pl.hand,
            "rank": pl.rank, "age": (pl.age + (asof - pl.age_date).days / 365.25) if pl.age and pl.age_date is not None else None,
            "elo": round(pl.elo, 1), "surface_elo": {s: round(v, 1) for s, v in pl.selo.items()},
            "matches_total": pl.n, "matches_surface": dict(pl.sn),
            "last5": wl(log[-5:]), "last10": wl(log[-10:]), "last20": wl(last20),
            "last52w": wl(yr), "last52w_surface": wl([m for m in yr if m["surface"] == surface]),
            "career_surface_wl": pl.surf_wl[surface],
            "games_won_lost_52w": [sum(m["gw"] for m in yr), sum(m["gl"] for m in yr)],
            "sets_won_lost_52w": [sum(m["sw"] for m in yr), sum(m["sl"] for m in yr)],
            "avg_opp_elo_last20": round(float(np.mean([m["opp_elo"] for m in last20])), 1) if last20 else None,
            "vs_top_opp_52w": wl([m for m in yr if m["opp_elo"] >= 1900]),
            "serve_rating": round(self.eff_S(pl, surface, asof), 4), "return_rating": round(self.eff_R(pl, surface, asof), 4),
            "form_elo": round(self.form_value(pl), 1),
            "last_match": str(pl.last_date.date()) if pl.last_date is not None else None,
            "last_stats_match": str(pl.last_stats_date.date()) if pl.last_stats_date is not None else None,
            "stats_52w": {k: (round(v, 4) if isinstance(v, float) else v) for k, v in stats.items()},
            "rank_history": [(str(d.date()), r) for d, r in pl.rank_hist[-12:]],
        }


def run_history(engine: RatingEngine, df: pd.DataFrame, on_match=None):
    """Feed matches chronologically. `on_match(engine, row)` is called *before* each update,
    so any prediction it makes only sees prior information."""
    cols = list(df.columns)
    for tup in df.itertuples(index=False, name=None):
        r = dict(zip(cols, tup))
        if on_match is not None:
            on_match(engine, r)
        engine.update(r)
    return engine
