"""Vectorised point-by-point Monte Carlo match simulator.

Simulates every point with the correct serve order (games alternate across sets,
tiebreak rotation A, BB, AA, ...), deuce, tiebreaks, Bo3/Bo5 and deciding-set rules.
Per-match performance variation: logit(p) + e, (e_A, e_B) ~ BVN(0, sigma^2, rho),
drawn once per simulated match (a "day form" shock).
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass
class SimResult:
    n: int
    seed: int
    best_of: int
    a_won: np.ndarray        # bool[n]
    sets_a: np.ndarray
    sets_b: np.ndarray
    games_a: np.ndarray      # total games won by A
    games_b: np.ndarray
    set_scores: np.ndarray   # int[n, best_of, 2] (-1 = not played)
    tiebreaks: np.ndarray    # int[n] number of tiebreaks
    breaks_a: np.ndarray     # breaks of serve achieved by A (A broke B)
    breaks_b: np.ndarray
    bp_a: np.ndarray         # break points created by A
    bp_b: np.ndarray
    points: np.ndarray


def simulate_match(pa: float, pb: float, best_of: int = 3, final_rule: str = "tb7", n: int = 100_000,
                   seed: int = 20261027, sigma: float = 0.0, rho: float = 0.0, rule: str = "tb7") -> SimResult:
    rng = np.random.default_rng(seed)
    need = best_of // 2 + 1
    la, lb = np.log(pa / (1 - pa)), np.log(pb / (1 - pb))
    if sigma > 0:
        z1 = rng.standard_normal(n)
        z2 = rho * z1 + np.sqrt(max(0.0, 1 - rho * rho)) * rng.standard_normal(n)
        PA = 1 / (1 + np.exp(-(la + sigma * z1)))
        PB = 1 / (1 + np.exp(-(lb + sigma * z2)))
    else:
        PA = np.full(n, pa)
        PB = np.full(n, pb)
    first = rng.integers(0, 2, n)            # coin toss: who serves first
    sa = np.zeros(n, np.int16); sb = np.zeros(n, np.int16)
    ga = np.zeros(n, np.int16); gb = np.zeros(n, np.int16)
    pta = np.zeros(n, np.int16); ptb = np.zeros(n, np.int16)
    server = first.astype(np.int8).copy()    # server of current game (or of first TB point)
    in_tb = np.zeros(n, bool)
    set_idx = np.zeros(n, np.int16)
    done = np.zeros(n, bool)
    scores = np.full((n, best_of, 2), -1, np.int16)
    tbs = np.zeros(n, np.int16)
    brk = np.zeros((n, 2), np.int16)
    bps = np.zeros((n, 2), np.int16)
    tot_pts = np.zeros(n, np.int32)
    tga = np.zeros(n, np.int16); tgb = np.zeros(n, np.int16)

    rules = [rule] * best_of
    rules[best_of - 1] = final_rule
    tb_target_by_set = np.array([7 if r == "tb7" else 10 for r in rules])
    has_tb_by_set = np.array([r != "adv" for r in rules])

    act = np.arange(n)
    it = 0
    while act.size:
        it += 1
        if it > 5000:
            raise RuntimeError("simulation did not terminate")
        i = act
        tb = in_tb[i]
        k = pta[i] + ptb[i]
        # tiebreak server rotation
        tbsrv = np.where((k == 0) | (((k - 1) // 2) % 2 == 1), server[i], 1 - server[i])
        srv = np.where(tb, tbsrv, server[i])
        pwin_a = np.where(srv == 0, PA[i], 1 - PB[i])
        # break point bookkeeping (before the point, regular games only)
        rec_pts = np.where(srv == 0, ptb[i], pta[i])
        srv_pts = np.where(srv == 0, pta[i], ptb[i])
        is_bp = (~tb) & (rec_pts >= 3) & (rec_pts > srv_pts)
        bps[i[is_bp & (srv == 1)], 0] += 1
        bps[i[is_bp & (srv == 0)], 1] += 1
        a_pt = rng.random(i.size) < pwin_a
        pta[i] += a_pt
        ptb[i] += ~a_pt
        tot_pts[i] += 1
        # game / tiebreak end
        pa_, pb_ = pta[i], ptb[i]
        target = np.where(tb, tb_target_by_set[set_idx[i]], 4)
        a_g = (pa_ >= target) & (pa_ - pb_ >= 2)
        b_g = (pb_ >= target) & (pb_ - pa_ >= 2)
        g_end = a_g | b_g
        if g_end.any():
            j = i[g_end]
            aw = a_g[g_end]
            wastb = tb[g_end]
            s_ = srv[g_end]
            # breaks (regular games only)
            bA = (~wastb) & aw & (s_ == 1)
            bB = (~wastb) & (~aw) & (s_ == 0)
            brk[j[bA], 0] += 1
            brk[j[bB], 1] += 1
            ga[j] += aw; gb[j] += ~aw
            tga[j] += aw; tgb[j] += ~aw
            pta[j] = 0; ptb[j] = 0
            tbs[j[wastb]] += 1
            # next game's server: alternate. After TB, the player who received first serves next.
            server[j] = 1 - server[j]
            in_tb[j] = False
            g_a, g_b = ga[j], gb[j]
            sidx = set_idx[j]
            hastb = has_tb_by_set[sidx]
            set_a = ((g_a >= 6) & (g_a - g_b >= 2)) | (hastb & (g_a == 7) & (g_b == 6))
            set_b = ((g_b >= 6) & (g_b - g_a >= 2)) | (hastb & (g_b == 7) & (g_a == 6))
            start_tb = hastb & (g_a == 6) & (g_b == 6)
            in_tb[j[start_tb]] = True
            s_end = set_a | set_b
            if s_end.any():
                m = j[s_end]
                scores[m, set_idx[m], 0] = ga[m]
                scores[m, set_idx[m], 1] = gb[m]
                sa[m] += set_a[s_end]; sb[m] += set_b[s_end]
                ga[m] = 0; gb[m] = 0
                set_idx[m] += 1
                fin = (sa[m] == need) | (sb[m] == need)
                done[m[fin]] = True
        act = act[~done[act]]
    return SimResult(n, seed, best_of, sa > sb, sa, sb, tga, tgb, scores, tbs,
                     brk[:, 0], brk[:, 1], bps[:, 0], bps[:, 1], tot_pts)


def summarize(sim: SimResult) -> dict:
    """Turn raw simulations into distribution summaries (all from one coherent sample)."""
    n = sim.n
    need = sim.best_of // 2 + 1
    tot = sim.games_a.astype(int) + sim.games_b
    diff = sim.games_a.astype(int) - sim.games_b
    out = {
        "n_sims": n, "seed": sim.seed,
        "p_a": float(sim.a_won.mean()),
        "set_scores": {},
        "games_pmf": {},
        "diff_pmf": {},
        "p_tb": float((sim.tiebreaks > 0).mean()),
        "p_a_set": float((sim.sets_a >= 1).mean()),
        "p_b_set": float((sim.sets_b >= 1).mean()),
        "p_decider": float(((sim.sets_a + sim.sets_b) == sim.best_of).mean()),
        "exp_breaks": float((sim.breaks_a + sim.breaks_b).mean()),
        "exp_breaks_a": float(sim.breaks_a.mean()),
        "exp_breaks_b": float(sim.breaks_b.mean()),
        "exp_bp_a": float(sim.bp_a.mean()),
        "exp_bp_b": float(sim.bp_b.mean()),
        "games_mean": float(tot.mean()),
        "games_median": float(np.median(tot)),
        "games_p10": float(np.percentile(tot, 10)),
        "games_p90": float(np.percentile(tot, 90)),
        "games_sd": float(tot.std()),
        "diff_mean": float(diff.mean()),
    }
    for a in range(need + 1):
        for b in range(need + 1):
            if (a == need) ^ (b == need):
                out["set_scores"][f"{a}-{b}"] = float(((sim.sets_a == a) & (sim.sets_b == b)).mean())
    v, c = np.unique(tot, return_counts=True)
    out["games_pmf"] = {int(x): float(y / n) for x, y in zip(v, c)}
    v, c = np.unique(diff, return_counts=True)
    out["diff_pmf"] = {int(x): float(y / n) for x, y in zip(v, c)}
    # most common exact set-score sequence for each match result
    seqs = {}
    sc = sim.set_scores
    flat = [tuple(map(tuple, row[row[:, 0] >= 0])) for row in sc[: min(n, 100_000)]]
    from collections import Counter
    cnt = Counter(flat)
    for seq, c in cnt.most_common():
        a = sum(1 for x, y in seq if x > y); b = len(seq) - a
        key = f"{a}-{b}"
        if key not in seqs:
            seqs[key] = {"sets": [f"{x}-{y}" for x, y in seq], "p": c / len(flat)}
    out["representative"] = seqs
    return out
