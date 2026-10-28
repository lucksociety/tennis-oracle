"""Pre-computed exact-model grids over (pa, pb) for fast vectorised backtesting.

Each grid cell is the *exact* analytic match distribution; values between cells are
bilinearly interpolated (max interpolation error is reported by tests)."""
from __future__ import annotations

from functools import lru_cache
from multiprocessing import Pool
from pathlib import Path

import numpy as np

from .scoring import match_distribution

ROOT = Path(__file__).resolve().parent.parent
GRID_DIR = ROOT / "data" / "grids"
LO, HI, STEP = 0.30, 0.95, 0.005
AX = np.round(np.arange(LO, HI + 1e-9, STEP), 4)


def _cell(args):
    pa, pb, bo, fr = args
    d = match_distribution(float(pa), float(pb), bo, fr)
    need = bo // 2 + 1
    keys = sorted(d.set_scores)
    return (d.p_win, d.p_tb, d.p_decider, d.exp_breaks, d.games[:100], d.diff[70 - 40:70 + 41],
            [d.set_scores[k] for k in keys], keys)


def build(bo: int, fr: str, procs: int = 8) -> dict:
    jobs = [(a, b, bo, fr) for a in AX for b in AX]
    with Pool(procs) as pool:
        res = pool.map(_cell, jobs, chunksize=32)
    n = len(AX)
    g = {
        "p_win": np.array([r[0] for r in res]).reshape(n, n),
        "p_tb": np.array([r[1] for r in res]).reshape(n, n),
        "p_decider": np.array([r[2] for r in res]).reshape(n, n),
        "exp_breaks": np.array([r[3] for r in res]).reshape(n, n),
        "games": np.array([r[4] for r in res]).reshape(n, n, -1),
        "diff": np.array([r[5] for r in res]).reshape(n, n, -1),
        "sets": np.array([r[6] for r in res]).reshape(n, n, -1),
        "set_keys": np.array(res[0][7]),
    }
    return g


@lru_cache(maxsize=8)
def get_grid(bo: int = 3, fr: str = "tb7") -> dict:
    GRID_DIR.mkdir(parents=True, exist_ok=True)
    f = GRID_DIR / f"grid_bo{bo}_{fr}.npz"
    if f.exists():
        z = np.load(f)
        return {k: z[k] for k in z.files}
    g = build(bo, fr)
    np.savez_compressed(f, **g)
    return g


def interp(field: np.ndarray, pa, pb):
    """Bilinear interpolation of a grid field (n, n, ...) at arrays pa, pb."""
    pa = np.clip(np.asarray(pa, float), LO, HI - 1e-9)
    pb = np.clip(np.asarray(pb, float), LO, HI - 1e-9)
    fa = (pa - LO) / STEP; fb = (pb - LO) / STEP
    i = np.floor(fa).astype(int); j = np.floor(fb).astype(int)
    i = np.clip(i, 0, len(AX) - 2); j = np.clip(j, 0, len(AX) - 2)
    ta = fa - i; tb = fb - j
    extra = (Ellipsis,) if field.ndim == 2 else (Ellipsis, None)
    ta_ = ta[extra] if field.ndim > 2 else ta
    tb_ = tb[extra] if field.ndim > 2 else tb
    if field.ndim > 2:
        ta_ = ta.reshape(ta.shape + (1,) * (field.ndim - 2)); tb_ = tb.reshape(tb.shape + (1,) * (field.ndim - 2))
    return ((1 - ta_) * (1 - tb_) * field[i, j] + ta_ * (1 - tb_) * field[i + 1, j]
            + (1 - ta_) * tb_ * field[i, j + 1] + ta_ * tb_ * field[i + 1, j + 1])


def win_prob_fast(pa, pb, bo=3, fr="tb7"):
    return interp(get_grid(bo, fr)["p_win"], pa, pb)


def gh(sigma, rho, n=3):
    from .scoring import gh_nodes
    return np.array(gh_nodes(sigma, rho, n))


def mixture_field(field_name, pa, pb, bo, fr, sigma, rho, n=3):
    """Expectation of a grid field under per-match performance shocks (vectorised)."""
    g = get_grid(bo, fr)
    la = np.log(pa / (1 - pa)); lb = np.log(pb / (1 - pb))
    out = 0
    for e1, e2, w in gh(sigma, rho, n):
        qa = 1 / (1 + np.exp(-(la + e1))); qb = 1 / (1 + np.exp(-(lb + e2)))
        out = out + w * interp(g[field_name], qa, qb)
    return out
