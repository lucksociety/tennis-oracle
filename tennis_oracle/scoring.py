"""Exact (analytic) tennis scoring model.

All functions take point-level serve probabilities:
    pa = P(player A wins a point on A's serve)
    pb = P(player B wins a point on B's serve)
and compute exact game / tiebreak / set / match distributions with the correct
serving order (games alternate across sets; tiebreak rotation A, BB, AA, ...).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache
from math import comb

import numpy as np

A, B = 0, 1
MAX_GAMES = 140          # total-games axis length (covers advantage sets)
DIFF_OFF = 70            # game-difference axis offset
ADV_CAP = 20             # advantage final set truncated at 20+ games each side (tail mass is negligible)


# ---------------------------------------------------------------- games
def hold_prob(p: float) -> float:
    """P(server holds) given P(server wins a point) = p. Exact closed form with deuce."""
    q = 1.0 - p
    if p <= 0.0:
        return 0.0
    if p >= 1.0:
        return 1.0
    pre = p**4 * (1 + 4 * q + 10 * q * q)
    deuce = 20 * p**3 * q**3
    return pre + deuce * (p * p) / (1 - 2 * p * q)


def expected_break_points(p: float) -> float:
    """Expected number of break points faced by the server in one service game."""
    q = 1.0 - p
    e = sum(comb(i + 3, i) * p**i * q**3 for i in range(3))  # 0-40, 15-40, 30-40 visits
    p_deuce = 20 * p**3 * q**3
    e += p_deuce * q / (1 - 2 * p * q)                         # visits to advantage receiver
    return e


# ---------------------------------------------------------------- tiebreak
def tb_point_server(k: int, first: int) -> int:
    """Server of the k-th (0-based) tiebreak point when `first` serves point 0."""
    if k == 0:
        return first
    return first if ((k - 1) // 2) % 2 == 1 else 1 - first


def tiebreak_prob(pa: float, pb: float, first: int = A, target: int = 7) -> float:
    """P(A wins a tiebreak to `target` (win by 2)) with `first` serving first."""
    # dp over point scores up to (target-1, target-1)
    n = target
    dp = np.zeros((n + 1, n + 1))
    dp[0, 0] = 1.0
    win = 0.0
    for tot in range(0, 2 * n - 2):
        for i in range(max(0, tot - (n - 1)), min(tot, n - 1) + 1):
            j = tot - i
            if j > n - 1 or dp[i, j] == 0.0:
                continue
            s = tb_point_server(tot, first)
            pw = pa if s == A else 1 - pb
            pr = dp[i, j]
            if i + 1 == n:
                win += pr * pw
            else:
                dp[i + 1, j] += pr * pw
            if j + 1 < n:
                dp[i, j + 1] += pr * (1 - pw)
    # tie at (n-1, n-1): every following pair contains one serve each
    tie = dp[n - 1, n - 1]
    x = pa * (1 - pb)
    y = (1 - pa) * pb
    win += tie * (x / (x + y) if (x + y) > 0 else 0.5)
    return win


# ---------------------------------------------------------------- sets
@dataclass
class SetDist:
    scores: dict            # (ga, gb) -> prob
    exp_breaks: float       # expected breaks of serve in the set
    exp_bp: float           # expected break points (opportunities) in the set
    p_tb: float             # P(set reaches a tiebreak)


def set_distribution(pa: float, pb: float, first: int, rule: str = "tb7") -> SetDist:
    """Exact distribution of the set game score.

    rule: 'tb7'  tiebreak to 7 at 6-6 (standard)
          'tb10' tiebreak to 10 at 6-6 (Grand Slam deciding set since 2022)
          'adv'  advantage set, no tiebreak
    """
    ha, hb = hold_prob(pa), hold_prob(pb)
    bpa, bpb = expected_break_points(pa), expected_break_points(pb)
    dp = {(0, 0): 1.0}
    scores: dict = {}
    eb = ebp = 0.0
    p_tb = 0.0
    cap = ADV_CAP if rule == "adv" else 6
    frontier = [(0, 0)]
    # iterate by total games played
    for tot in range(0, 2 * cap + 2):
        nxt = {}
        for (i, j), pr in dp.items():
            if i + j != tot:
                continue
            # terminal checks
            if (i >= 6 or j >= 6) and abs(i - j) >= 2:
                scores[(i, j)] = scores.get((i, j), 0.0) + pr
                continue
            if i == 6 and j == 6 and rule in ("tb7", "tb10"):
                p_tb += pr
                tw = tiebreak_prob(pa, pb, first, 7 if rule == "tb7" else 10)  # 12 games played -> set's first server serves first TB point
                scores[(7, 6)] = scores.get((7, 6), 0.0) + pr * tw
                scores[(6, 7)] = scores.get((6, 7), 0.0) + pr * (1 - tw)
                continue
            if rule == "adv" and i == cap and j == cap:
                # truncate: resolve with the closed form of a two-game cycle
                x = ha * (1 - hb)
                y = (1 - ha) * hb
                w = x / (x + y) if x + y > 0 else 0.5
                scores[(cap + 2, cap)] = scores.get((cap + 2, cap), 0.0) + pr * w
                scores[(cap, cap + 2)] = scores.get((cap, cap + 2), 0.0) + pr * (1 - w)
                continue
            srv = first if (i + j) % 2 == 0 else 1 - first
            h = ha if srv == A else hb
            eb += pr * (1 - h)
            ebp += pr * (bpa if srv == A else bpb)
            if srv == A:
                nxt[(i + 1, j)] = nxt.get((i + 1, j), 0.0) + pr * h
                nxt[(i, j + 1)] = nxt.get((i, j + 1), 0.0) + pr * (1 - h)
            else:
                nxt[(i, j + 1)] = nxt.get((i, j + 1), 0.0) + pr * h
                nxt[(i + 1, j)] = nxt.get((i + 1, j), 0.0) + pr * (1 - h)
        for k, v in nxt.items():
            dp[k] = dp.get(k, 0.0) + v
    return SetDist(scores, eb, ebp, p_tb)


@lru_cache(maxsize=200_000)
def _set_cached(pa: float, pb: float, first: int, rule: str) -> SetDist:
    return set_distribution(pa, pb, first, rule)


def set_win_prob(pa: float, pb: float, first: int, rule: str = "tb7") -> float:
    return sum(v for (i, j), v in _set_cached(pa, pb, first, rule).scores.items() if i > j)


# ---------------------------------------------------------------- match
@dataclass
class MatchDist:
    best_of: int
    p_win: float                         # P(A wins)
    set_scores: dict                     # (sa, sb) -> prob
    games: np.ndarray                    # P(total games = g)
    diff: np.ndarray                     # P(games_A - games_B = d), index d + DIFF_OFF
    p_tb: float                          # P(at least one tiebreak)
    p_a_set: float                       # P(A wins at least one set)
    p_b_set: float
    p_decider: float                     # P(maximum number of sets)
    exp_breaks: float
    exp_bp: float
    exp_breaks_by: tuple = (0.0, 0.0)    # breaks achieved by A, by B (approx split)
    representative: dict = field(default_factory=dict)  # (sa,sb) -> most likely set-score sequence


def match_distribution(pa: float, pb: float, best_of: int = 3, final_rule: str = "tb7",
                       first: int | None = None, rule: str = "tb7") -> MatchDist:
    """Exact match distribution. If `first` is None the coin toss is 50/50."""
    if first is None:
        d0 = match_distribution(pa, pb, best_of, final_rule, A, rule)
        d1 = match_distribution(pa, pb, best_of, final_rule, B, rule)
        return _mix([d0, d1], [0.5, 0.5])
    need = best_of // 2 + 1
    G, D = MAX_GAMES, 2 * DIFF_OFF + 1
    # state: (sa, sb, server, tb_flag) -> (prob array over [games, diff])
    states = {(0, 0, first, 0): _delta(G, D)}
    set_scores: dict = {}
    games = np.zeros(G)
    diff = np.zeros(D)
    p_tb = 0.0
    eb = ebp = 0.0
    p_a_set = p_b_set = 0.0
    p_dec = 0.0
    # viterbi for representative sequences: (sa,sb,srv) -> (logp, seq)
    vit = {(0, 0, first): (0.0, ())}
    rep_all: dict = {}
    for _ in range(2 * need - 1):
        new_states: dict = {}
        new_vit: dict = {}
        for (sa, sb, srv, tbf), arr in states.items():
            mass = arr.sum()
            if mass == 0:
                continue
            decider = (sa == need - 1 and sb == need - 1)
            r = final_rule if decider else rule
            if decider:
                p_dec += mass
            sd = _set_cached(pa, pb, srv, r)
            eb += mass * sd.exp_breaks
            ebp += mass * sd.exp_bp
            for (ga, gb), pr in sd.scores.items():
                if pr < 1e-15:
                    continue
                nsa, nsb = sa + (ga > gb), sb + (gb > ga)
                nsrv = srv if (ga + gb) % 2 == 0 else 1 - srv
                ntb = 1 if (tbf or (max(ga, gb) == 7 and min(ga, gb) == 6) or (r != "adv" and ga + gb == 13)) else 0
                shifted = _shift(arr, ga + gb, ga - gb) * pr
                key = (nsa, nsb, nsrv, ntb)
                if key in new_states:
                    new_states[key] += shifted
                else:
                    new_states[key] = shifted
        for (sa, sb, srv), (lp, seq) in vit.items():
            decider = (sa == need - 1 and sb == need - 1)
            r = final_rule if decider else rule
            sd = _set_cached(pa, pb, srv, r)
            for (ga, gb), pr in sd.scores.items():
                if pr <= 0:
                    continue
                nsa, nsb = sa + (ga > gb), sb + (gb > ga)
                nsrv = srv if (ga + gb) % 2 == 0 else 1 - srv
                cand = (lp + np.log(pr), seq + ((ga, gb),))
                k = (nsa, nsb, nsrv)
                if nsa == need or nsb == need:
                    kk = (nsa, nsb)
                    if kk not in new_vit or cand[0] > new_vit[kk][0]:
                        new_vit[kk] = cand
                elif k not in new_vit or cand[0] > new_vit[k][0]:
                    new_vit[k] = cand
        # split terminal / continuing
        states = {}
        for (sa, sb, srv, tbf), arr in new_states.items():
            if sa == need or sb == need:
                m = arr.sum()
                set_scores[(sa, sb)] = set_scores.get((sa, sb), 0.0) + m
                games += arr.sum(axis=1)
                diff += arr.sum(axis=0)
                if tbf:
                    p_tb += m
                if sa >= 1:
                    p_a_set += m
                if sb >= 1:
                    p_b_set += m
            else:
                states[(sa, sb, srv, tbf)] = arr
        vit_next = {}
        for k, v in new_vit.items():
            if len(k) == 2:
                vit_next[("T",) + k] = v
            else:
                vit_next[k] = v
        # store terminals in representative dict
        reps = {k[1:]: v for k, v in vit_next.items() if k[0] == "T"}
        vit = {k: v for k, v in vit_next.items() if k[0] != "T"}
        for k, v in reps.items():
            if k not in rep_all or v[0] > rep_all[k][0]:
                rep_all[k] = v
    p_win = sum(v for (sa, sb), v in set_scores.items() if sa > sb)
    return MatchDist(best_of, p_win, set_scores, games, diff, p_tb, p_a_set, p_b_set, p_dec,
                     eb, ebp, representative={k: (float(np.exp(v[0])), list(v[1])) for k, v in rep_all.items()})


def match_win_prob(pa: float, pb: float, best_of: int = 3, final_rule: str = "tb7", rule: str = "tb7") -> float:
    """Fast P(A wins match) (coin toss 50/50), no game distribution."""
    out = 0.0
    need = best_of // 2 + 1
    for first in (A, B):
        st = {(0, 0, first): 1.0}
        win = 0.0
        for _ in range(2 * need - 1):
            ns: dict = {}
            for (sa, sb, srv), pr in st.items():
                r = final_rule if (sa == need - 1 and sb == need - 1) else rule
                sd = _set_cached(pa, pb, srv, r)
                for (ga, gb), q in sd.scores.items():
                    nsa, nsb = sa + (ga > gb), sb + (gb > ga)
                    nsrv = srv if (ga + gb) % 2 == 0 else 1 - srv
                    if nsa == need:
                        win += pr * q
                    elif nsb == need:
                        pass
                    else:
                        ns[(nsa, nsb, nsrv)] = ns.get((nsa, nsb, nsrv), 0.0) + pr * q
            st = ns
        out += 0.5 * win
    return out


def _delta(G, D):
    a = np.zeros((G, D))
    a[0, DIFF_OFF] = 1.0
    return a


def _shift(arr, dg, dd):
    out = np.zeros_like(arr)
    G, D = arr.shape
    if dd >= 0:
        out[dg:, dd:] = arr[: G - dg, : D - dd]
    else:
        out[dg:, : D + dd] = arr[: G - dg, -dd:]
    return out


def _mix(dists, weights):
    w = np.asarray(weights, float)
    w = w / w.sum()
    ss: dict = {}
    for d, wi in zip(dists, w):
        for k, v in d.set_scores.items():
            ss[k] = ss.get(k, 0.0) + wi * v
    rep: dict = {}
    for d, wi in zip(dists, w):
        for k, (p, seq) in d.representative.items():
            if k not in rep or p * wi > rep[k][0]:
                rep[k] = (p * wi, seq)
    return MatchDist(
        dists[0].best_of,
        float(sum(wi * d.p_win for d, wi in zip(dists, w))),
        ss,
        sum(wi * d.games for d, wi in zip(dists, w)),
        sum(wi * d.diff for d, wi in zip(dists, w)),
        float(sum(wi * d.p_tb for d, wi in zip(dists, w))),
        float(sum(wi * d.p_a_set for d, wi in zip(dists, w))),
        float(sum(wi * d.p_b_set for d, wi in zip(dists, w))),
        float(sum(wi * d.p_decider for d, wi in zip(dists, w))),
        float(sum(wi * d.exp_breaks for d, wi in zip(dists, w))),
        float(sum(wi * d.exp_bp for d, wi in zip(dists, w))),
        representative=rep,
    )


# ---------------------------------------------------------------- performance variation mixture
def _logit(p):
    return np.log(p / (1 - p))


def _sigmoid(x):
    return 1 / (1 + np.exp(-x))


def gh_nodes(sigma: float, rho: float, n: int = 5):
    """Gauss-Hermite nodes for correlated bivariate normal shocks in logit space."""
    x, w = np.polynomial.hermite_e.hermegauss(n)   # probabilists' Hermite: weight exp(-x^2/2)
    w = w / w.sum()
    nodes = []
    for i in range(n):
        for j in range(n):
            z1 = x[i]
            z2 = rho * x[i] + np.sqrt(max(0.0, 1 - rho * rho)) * x[j]
            nodes.append((sigma * z1, sigma * z2, w[i] * w[j]))
    return nodes


def mixture_distribution(pa, pb, best_of=3, final_rule="tb7", sigma=0.0, rho=0.0, n=5, rule="tb7"):
    """Exact match distribution integrated over per-match performance shocks
    (logit(pa)+e1, logit(pb)+e2), (e1, e2) ~ BVN(0, sigma^2, rho) via Gauss-Hermite."""
    if sigma <= 0:
        return match_distribution(round(pa, 6), round(pb, 6), best_of, final_rule, rule=rule)
    dists, ws = [], []
    la, lb = _logit(pa), _logit(pb)
    for e1, e2, w in gh_nodes(sigma, rho, n):
        qa = float(round(_sigmoid(la + e1), 6))
        qb = float(round(_sigmoid(lb + e2), 6))
        dists.append(match_distribution(qa, qb, best_of, final_rule, rule=rule))
        ws.append(w)
    return _mix(dists, ws)


def mixture_win_prob(pa, pb, best_of=3, final_rule="tb7", sigma=0.0, rho=0.0, n=3):
    if sigma <= 0:
        return match_win_prob(round(pa, 6), round(pb, 6), best_of, final_rule)
    la, lb = _logit(pa), _logit(pb)
    return float(sum(w * match_win_prob(float(round(_sigmoid(la + e1), 6)), float(round(_sigmoid(lb + e2), 6)), best_of, final_rule)
                     for e1, e2, w in gh_nodes(sigma, rho, n)))
