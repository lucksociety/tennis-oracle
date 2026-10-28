"""Model parameters. Every version is stored in models/params_<version>.json and never edited."""
from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MODELS = ROOT / "models"

DEFAULT_PARAMS = {
    "version": "v1.0",
    "description": "Starting weights from the TENNIS ORACLE V1.0 spec; scale/variation fitted chronologically on pre-test data.",
    # composite rating weights (starting weights, not validated optima)
    "weights": {"surface_elo": 0.35, "serve_return": 0.25, "overall_elo": 0.20, "form": 0.15, "context": 0.05},
    "composite_scale": 1.0,          # multiplies the weighted Elo-point difference (fitted on training years)
    "context_max_elo": 100.0,        # cap on manual verified context adjustments (Elo points)
    # Elo (FiveThirtyEight-style decaying K)
    "elo": {"init": 1500.0, "k_num": 250.0, "k_off": 5.0, "k_pow": 0.4, "slam_mult": 1.1},
    # serve/return ratings (logit space, opponent-adjusted, shrunk toward tour/surface baseline)
    "sr": {"prior_n": 12.0, "eta_min": 0.04, "surface_prior_n": 15.0, "eta_min_surface": 0.05,
           "baseline_eta": 0.002, "decay_days": 365.0},
    # recent form: EW mean of (result - Elo expectation)
    "form": {"halflife_matches": 8.0, "shrink_n": 6.0},
    # serve/return-implied Elo cap
    "sr_elo_cap": 600.0,
    # per-match performance variation in logit(serve-point) space (estimated from data)
    "variation": {"sigma": 0.25, "rho": 0.0},
    "indoor_logit_adj": 0.0,         # no measurable basis in the data set -> 0 (configurable)
    "mc": {"n_sims": 100000, "seed": 20261027},
    "gh_nodes": 5,
}


def load_params(version: str | None = None) -> dict:
    MODELS.mkdir(exist_ok=True)
    if version is None:
        cur = MODELS / "CURRENT"
        version = cur.read_text().strip() if cur.exists() else DEFAULT_PARAMS["version"]
    f = MODELS / f"params_{version}.json"
    if f.exists():
        return json.loads(f.read_text())
    return deepcopy(DEFAULT_PARAMS)


def save_params(params: dict, make_current: bool = True) -> Path:
    MODELS.mkdir(exist_ok=True)
    f = MODELS / f"params_{params['version']}.json"
    f.write_text(json.dumps(params, indent=2))
    if make_current:
        (MODELS / "CURRENT").write_text(params["version"])
    return f
