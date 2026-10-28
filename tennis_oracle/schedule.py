"""Daily order of play from ESPN's public scoreboard (fixtures/results only; any odds or
prediction fields are stripped before parsing), turned into frozen pre-match forecasts."""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

from .build import sackmann_end
from .data import espn_competitions, espn_flatten, fetch_espn_day, infer_surface, norm_name, surface_lookup, load_sackmann
from .freeze import freeze, latest, slug
from .predict import forecast

ROOT = Path(__file__).resolve().parent.parent
DOCS_DATA = ROOT / "docs" / "data"
TZ = ZoneInfo("America/New_York")


def name_map(engine):
    m = {}
    for pid, p in engine.players.items():
        k = norm_name(p.name)
        if k not in m or (p.last_date is not None and (engine.players[m[k]].last_date or pd.Timestamp(0)) < p.last_date):
            m[k] = pid
        parts = k.split()
        if len(parts) == 2:
            m.setdefault(f"{parts[1]} {parts[0]}", pid)
    return m


def fetch_day_matches(day: date) -> list:
    seen = {}
    for q in (day - timedelta(days=1), day, day + timedelta(days=1)):
        for league in ("atp", "wta"):
            payload = fetch_espn_day(league, q)
            for tour in ("atp", "wta"):
                for ev, c in espn_competitions(payload, tour):
                    rec = espn_flatten(ev, c, tour)
                    if not rec["start"]:
                        continue
                    local = pd.Timestamp(rec["start"]).tz_convert(TZ).date()
                    if local == day:
                        seen[rec["espn_id"]] = rec
    return sorted(seen.values(), key=lambda r: (r["start"], r["tour"]))


def build_slate(day: date, engines: dict, n_sims: int = 100_000, write=True, do_freeze=True) -> dict:
    lookups = {t: surface_lookup(load_sackmann(t)) for t in engines}
    nmap = {t: name_map(e) for t, e in engines.items()}
    matches = fetch_day_matches(day)
    out = []
    for r in matches:
        tour = r["tour"]; eng = engines[tour]
        surf, ssrc = infer_surface(r["event"], r["venue"], lookups[tour])
        bo = 5 if (tour == "atp" and r["major"] and "Qualifying" not in r["round"]) else 3
        fr = "tb10" if r["major"] else "tb7"
        ids = [nmap[tour].get(norm_name(p["name"])) for p in r["players"]]
        m = {"espn_id": r["espn_id"], "tour": tour, "tournament": r["event"], "venue": r["venue"], "round": r["round"],
             "court": r["court"], "start_utc": r["start"], "status": r["status"], "completed": r["completed"],
             "players": [{"name": p["name"], "id": i} for p, i in zip(r["players"], ids)],
             "surface": surf, "surface_source": ssrc, "indoor": None, "best_of": bo, "final_rule": fr,
             "result": {"winner": r["winner"], "sets": r["sets"]} if r["completed"] else None, "forecast": None}
        key = f"{r['espn_id']}-{slug(r['players'][0]['name'])}-vs-{slug(r['players'][1]['name'])}"
        if all(ids):
            f = latest(str(day), key)
            if f is None and r["status"] == "STATUS_SCHEDULED":
                f = forecast(eng, ids[0], ids[1], surf, bo, fr, tour, asof=pd.Timestamp(day),
                             meta={"tournament": r["event"], "round": r["round"]}, n_sims=n_sims,
                             data_cutoff=eng.point_cutoff)
                f["results_cutoff"] = eng.info["results_cutoff"]
                if do_freeze:
                    freeze(f, str(day), key, extra={"espn_id": r["espn_id"], "scheduled_start_utc": r["start"]})
                    f = latest(str(day), key)
            if f is not None:
                m["forecast"] = {
                    "frozen_version": f.get("freeze", {}).get("version"), "frozen_at_utc": f.get("freeze", {}).get("frozen_at_utc"),
                    "path": f"forecasts/{day}/{key}/v{f.get('freeze', {}).get('version')}.json",
                    "model_version": f["model"]["version"], "predicted_winner": f["predicted_winner"], "win_prob": f["win_prob"],
                    "most_likely_set_score": f["most_likely_set_score"], "final_score": f["final_score"],
                    "games_mean": f["games"]["mean"], "strongest": {k: f["strongest"][k] for k in ("selection", "probability", "confidence")},
                    "data_quality": f["data_quality"]}
        else:
            m["note"] = "player(s) not in rating database (no tour-level history) - no forecast"
        out.append(m)
    slate = {"date": str(day), "timezone": "America/New_York",
             "generated_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
             "source": "ESPN public scoreboard API (site.api.espn.com/apis/site/v2/sports/tennis/{atp,wta}/scoreboard) - fixtures/results only; odds/prediction fields stripped",
             "model_version": next(iter(engines.values())).p["version"], "matches": out}
    if write:
        DOCS_DATA.mkdir(parents=True, exist_ok=True)
        (DOCS_DATA / f"slate-{day}.json").write_text(json.dumps(slate, indent=1, default=str))
        idx_f = DOCS_DATA / "slates.json"
        idx = json.loads(idx_f.read_text()) if idx_f.exists() else {"dates": []}
        if str(day) not in idx["dates"]:
            idx["dates"] = sorted(idx["dates"] + [str(day)])
        idx["updated_utc"] = slate["generated_at_utc"]
        idx_f.write_text(json.dumps(idx, indent=1))
    return slate
