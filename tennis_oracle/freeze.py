"""Pre-match forecast freezing. Files are created exclusively and never edited.
A changed input before play creates a new version file with a recorded reason."""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FORECASTS = ROOT / "forecasts"


def slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")


def input_hash(f: dict) -> str:
    keys = {k: f.get(k) for k in ("surface", "best_of", "final_rule", "indoor", "context_inputs")}
    keys["model"] = {k: f["model"].get(k) for k in ("version", "components_elo", "point_probs", "seed", "n_sims")}
    keys["players"] = [f["players"]["A"]["id"], f["players"]["B"]["id"]]
    return hashlib.sha256(json.dumps(keys, sort_keys=True, default=str).encode()).hexdigest()[:16]


def freeze(f: dict, match_date: str, match_key: str | None = None, reason: str = "initial pre-match forecast",
           extra: dict | None = None) -> Path | None:
    """Write forecasts/<date>/<match>/v<N>.json. Returns None if an identical-input
    forecast already exists (no duplicate version)."""
    key = match_key or slug(f"{f['players']['A']['name']}-vs-{f['players']['B']['name']}")
    d = FORECASTS / match_date / key
    d.mkdir(parents=True, exist_ok=True)
    h = input_hash(f)
    existing = sorted(d.glob("v*.json"), key=lambda p: int(p.stem[1:]))
    for p in existing:
        if json.loads(p.read_text()).get("freeze", {}).get("input_hash") == h:
            return None
    n = len(existing) + 1
    rec = dict(f)
    rec["freeze"] = {"frozen_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"), "version": n,
                     "input_hash": h, "reason": reason if n == 1 else (reason if reason != "initial pre-match forecast" else "inputs changed before play (ratings/format/context update)"),
                     "pre_match": True, **(extra or {})}
    p = d / f"v{n}.json"
    with open(p, "x") as fh:           # exclusive create: never overwrite
        json.dump(rec, fh, indent=1, default=str)
    return p


def latest(match_date: str, match_key: str) -> dict | None:
    d = FORECASTS / match_date / match_key
    files = sorted(d.glob("v*.json"), key=lambda p: int(p.stem[1:])) if d.exists() else []
    return json.loads(files[-1].read_text()) if files else None
