"""Data loading: Jeff Sackmann's public ATP/WTA match files + ESPN public results.

Only performance data (results, scores, serve/return counts, rankings) is read.
Any column that looks like betting / odds / market data is rejected outright.
"""
from __future__ import annotations

import json
import os
import re
import time
import unicodedata
import urllib.request
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / "data" / "raw"
ESPN_DIR = ROOT / "data" / "espn"
# Jeff Sackmann's tennis_atp / tennis_wta repositories (CC BY-NC-SA 4.0). The upstream
# repos are not currently reachable on GitHub, so we read the archival mirror of the
# same files (snapshot of upstream commits from June 2026).
SACKMANN_BASE = "https://raw.githubusercontent.com/Aneeshers/tennis-sackmann-archive/main/{tour}/{tour}_matches_{year}.csv"
FIRST_YEAR = 2012

FORBIDDEN = re.compile(r"(odds|^b365|^ps[hwl]$|^max[wl]$|^avg[wl]$|^ex[wl]$|^lb[wl]$|^sj[wl]$|bet|moneyline|spread|implied|market)", re.I)

COLS = ["tourney_id", "tourney_name", "surface", "tourney_level", "tourney_date", "match_num",
        "winner_id", "winner_name", "winner_hand", "winner_age", "winner_rank",
        "loser_id", "loser_name", "loser_hand", "loser_age", "loser_rank",
        "score", "best_of", "round", "minutes",
        "w_ace", "w_df", "w_svpt", "w_1stIn", "w_1stWon", "w_2ndWon", "w_SvGms", "w_bpSaved", "w_bpFaced",
        "l_ace", "l_df", "l_svpt", "l_1stIn", "l_1stWon", "l_2ndWon", "l_SvGms", "l_bpSaved", "l_bpFaced"]

ROUND_ORDER = {"Q1": 0, "Q2": 1, "Q3": 2, "R128": 3, "R64": 4, "R32": 5, "R16": 6, "QF": 7, "SF": 8, "BR": 8.5, "F": 9, "RR": 5.5, "ER": 3}


def assert_no_market_columns(df: pd.DataFrame) -> None:
    bad = [c for c in df.columns if FORBIDDEN.search(str(c))]
    if bad:
        raise ValueError(f"Refusing market/odds columns: {bad}")


def download_sackmann(tours=("atp", "wta"), years=None, force=False) -> None:
    RAW.mkdir(parents=True, exist_ok=True)
    years = years or range(FIRST_YEAR, date.today().year + 1)
    for t in tours:
        for y in years:
            f = RAW / f"{t}_matches_{y}.csv"
            if f.exists() and not force and y < date.today().year:
                continue
            try:
                temporary=f.with_suffix('.download')
                urllib.request.urlretrieve(SACKMANN_BASE.format(tour=t, year=y), temporary)
                candidate=pd.read_csv(temporary,low_memory=False)
                assert_no_market_columns(candidate)
                if candidate.empty or not {'winner_id','loser_id','tourney_date'}.issubset(candidate.columns):
                    raise ValueError('Invalid point-statistics download; keeping last good data')
                os.replace(temporary,f)
            except Exception as e:  # missing future year etc.
                f.with_suffix('.download').unlink(missing_ok=True)
                print(f"[data] {t} {y}: {e}")


def norm_name(s: str) -> str:
    s = unicodedata.normalize("NFKD", str(s)).encode("ascii", "ignore").decode()
    s = re.sub(r"[^a-z ]", " ", s.lower().replace("-", " "))
    return " ".join(s.split())


SET_RE = re.compile(r"^(\d+)-(\d+)(?:\((\d+)(?:-(\d+))?\))?$")


def parse_score(score: str):
    """Parse '6-3 3-6 7-6(4)' -> ([(6,3),(3,6),(7,6)], n_tiebreaks, complete).

    complete=False for RET/W/O/DEF/abandoned."""
    if not isinstance(score, str):
        return [], 0, False
    s = score.strip()
    complete = not re.search(r"RET|W/O|DEF|Def|Walkover|ABD|unfinished|ABN|In Progress", s, re.I)
    sets, tbs = [], 0
    for tok in s.split():
        m = SET_RE.match(tok.strip("[]"))
        if not m:
            continue
        a, b = int(m.group(1)), int(m.group(2))
        sets.append((a, b))
        if (a, b) in ((7, 6), (6, 7)) or m.group(3) is not None:
            tbs += 1
    return sets, tbs, complete


def load_sackmann(tour: str, years=None) -> pd.DataFrame:
    years = years or range(FIRST_YEAR, date.today().year + 1)
    frames = []
    for y in years:
        f = RAW / f"{tour}_matches_{y}.csv"
        if not f.exists():
            continue
        df = pd.read_csv(f, low_memory=False)
        assert_no_market_columns(df)
        frames.append(df[[c for c in COLS if c in df.columns]])
    df = pd.concat(frames, ignore_index=True)
    df["date"] = pd.to_datetime(df["tourney_date"].astype(str), format="%Y%m%d")
    df["surface"] = df["surface"].fillna("Hard").replace({"Carpet": "Hard"})
    df["round_ord"] = df["round"].map(ROUND_ORDER).fillna(5)
    df["tour"] = tour
    df["source"] = "sackmann"
    df["winner_id"] = tour + ":" + df["winner_id"].astype(str)
    df["loser_id"] = tour + ":" + df["loser_id"].astype(str)
    df = df.sort_values(["date", "tourney_id", "round_ord", "match_num"], kind="mergesort").reset_index(drop=True)
    return df


# ---------------------------------------------------------------- ESPN (public scoreboard, no odds)
ESPN_URL = "https://site.api.espn.com/apis/site/v2/sports/tennis/{league}/scoreboard?dates={d}"
ESPN_FORBIDDEN_KEYS = {"odds", "pickcenter", "predictor", "againstTheSpread", "winprobability", "bets"}


def _strip_market(obj):
    """Recursively drop any market/prediction fields an API might include."""
    if isinstance(obj, dict):
        return {k: _strip_market(v) for k, v in obj.items() if k.lower() not in {x.lower() for x in ESPN_FORBIDDEN_KEYS}}
    if isinstance(obj, list):
        return [_strip_market(v) for v in obj]
    return obj


def fetch_espn_day(league: str, d: date, retries: int = 3, strict: bool = False) -> dict:
    url = ESPN_URL.format(league=league, d=d.strftime("%Y%m%d"))
    for k in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "tennis-oracle/1.0 (research)"})
            with urllib.request.urlopen(req, timeout=30) as r:
                return _strip_market(json.loads(r.read().decode()))
        except Exception as e:
            if k == retries - 1:
                print(f"[espn] {url}: {e}")
                if strict: raise RuntimeError(f"ESPN results unavailable for {league} {d}; keeping cached results") from e
                return {"events": []}
            time.sleep(2)


GROUP = {"atp": "Men's Singles", "wta": "Women's Singles"}


def espn_competitions(payload: dict, tour: str):
    """Yield flattened singles competitions for one tour from a scoreboard payload."""
    for ev in payload.get("events", []):
        for g in ev.get("groupings", []):
            if g.get("grouping", {}).get("displayName") != GROUP[tour]:
                continue
            for c in g.get("competitions", []):
                comps = c.get("competitors", [])
                if len(comps) != 2 or not all(x.get("athlete", {}).get("displayName") for x in comps):
                    continue
                yield ev, c


def espn_flatten(ev, c, tour):
    comps = sorted(c["competitors"], key=lambda x: x.get("order", 0))
    st = c.get("status", {}).get("type", {})
    rec = {
        "espn_id": c["id"], "tour": tour, "event": ev.get("name"), "event_id": ev.get("id"),
        "venue": (ev.get("venue") or {}).get("displayName", ""), "major": bool(ev.get("major")),
        "start": c.get("date"), "status": st.get("name"), "completed": bool(st.get("completed")),
        "round": (c.get("round") or {}).get("displayName", ""),
        "court": (c.get("venue") or {}).get("court", ""),
        "players": [], "winner": None, "sets": [],
    }
    for x in comps:
        rec["players"].append({"name": x["athlete"]["displayName"], "espn_athlete": x.get("id"),
                               "country": (x["athlete"].get("flag") or {}).get("alt", "")})
    if rec["completed"]:
        w = [i for i, x in enumerate(comps) if x.get("winner")]
        rec["winner"] = w[0] if w else None
        ls0 = comps[0].get("linescores", []); ls1 = comps[1].get("linescores", [])
        for a, b in zip(ls0, ls1):
            rec["sets"].append([int(a.get("value", 0)), int(b.get("value", 0))])
    return rec


def update_espn_results(since: date, until: date | None = None, step_days: int = 2) -> list:
    """Collect completed singles results (ATP + WTA tour-level, incl. majors) from ESPN's
    public scoreboard between `since` and `until`. Results are cached per competition id."""
    until = until or date.today()
    ESPN_DIR.mkdir(parents=True, exist_ok=True)
    cache_f = ESPN_DIR / "results.json"
    cache = json.loads(cache_f.read_text()) if cache_f.exists() else {}
    d = since
    while d <= until:
        for league in ("atp", "wta"):
            payload = fetch_espn_day(league, d, strict=True)
            for tour in ("atp", "wta"):
                for ev, c in espn_competitions(payload, tour):
                    rec = espn_flatten(ev, c, tour)
                    if rec["completed"]:
                        cache[rec["espn_id"]] = rec
        # Checkpoint each day so a timeout does not discard a long catch-up run.
        temporary=cache_f.with_suffix('.tmp')
        temporary.write_text(json.dumps(cache,indent=0,sort_keys=True))
        os.replace(temporary,cache_f)
        coverage=ESPN_DIR/'coverage.json'
        progress=coverage.with_suffix('.tmp')
        progress.write_text(json.dumps({'through':d.isoformat(),'source':'ESPN ATP/WTA daily scoreboards'}))
        os.replace(progress,coverage)
        d += timedelta(days=step_days)
    return list(cache.values())


MAJOR_SURFACE = {"wimbledon": "Grass", "roland garros": "Clay", "french open": "Clay",
                 "us open": "Hard", "australian open": "Hard"}


def surface_lookup(sackmann: pd.DataFrame):
    """Map tournament / city names to their most recent recorded surface."""
    m = {}
    recent = sackmann.sort_values("date")
    for name, surf in zip(recent["tourney_name"], recent["surface"]):
        m[norm_name(name)] = surf
    return m


def infer_surface(event: str, venue: str, lookup: dict) -> tuple[str, str]:
    e = norm_name(event)
    # Official WTA tournament metadata, checked 2026-10-30. The historical venue
    # lookup can collide with a different event at the same city.
    # https://www.wtatennis.com/tournaments/1145/queretaro-125/2026
    if "queretaro" in e.split():
        return "Clay", "official-wta-1145"
    for k, v in MAJOR_SURFACE.items():
        if k in e:
            return v, "major"
    city = norm_name((venue or "").split(",")[0])
    if city and city in lookup:
        return lookup[city], "venue-history"
    for k, v in lookup.items():
        if len(k) > 4 and (k in e or (city and k == city)):
            return v, "name-history"
    return "Hard", "assumed"


ESPN_ROUND = {"Final": "F", "Semifinal": "SF", "Quarterfinal": "QF", "Round 4": "R16", "Round 3": "R32",
              "Round 2": "R64", "Round 1": "R128", "Round of 16": "R16", "Round of 32": "R32"}


def espn_results_frame(records: list, sackmann: pd.DataFrame, name_index: dict, after: pd.Timestamp) -> pd.DataFrame:
    """Convert cached ESPN results into rows compatible with the Sackmann frame
    (no serve/return statistics: ESPN gives scores only)."""
    lookup = surface_lookup(sackmann)
    rows = []
    for r in records:
        if r.get("winner") is None or r["status"] not in ("STATUS_FINAL",):
            continue
        dt = pd.Timestamp(r["start"]).tz_convert(None) if pd.Timestamp(r["start"]).tzinfo else pd.Timestamp(r["start"])
        if dt <= after:
            continue
        w = r["winner"]; l = 1 - w
        surf, _ = infer_surface(r["event"], r["venue"], lookup)
        sets = [(s[w], s[l]) for s in r["sets"]]
        score = " ".join(f"{a}-{b}" for a, b in sets)
        wn, ln = r["players"][w]["name"], r["players"][l]["name"]
        rows.append({
            "tourney_id": f"espn-{r['event_id']}", "tourney_name": r["event"], "surface": surf,
            "tourney_level": "G" if r["major"] else "A", "date": dt.normalize(), "match_num": 0,
            "winner_id": name_index.get((r["tour"], norm_name(wn)), f"{r['tour']}:espn{r['players'][w]['espn_athlete']}"),
            "winner_name": wn, "loser_id": name_index.get((r["tour"], norm_name(ln)), f"{r['tour']}:espn{r['players'][l]['espn_athlete']}"),
            "loser_name": ln, "score": score,
            "best_of": 5 if (r["major"] and r["tour"] == "atp" and "Qualifying" not in r["round"]) else 3,
            "round": ESPN_ROUND.get(r["round"], "Q1" if "Qualifying" in r["round"] else "R32"),
            "tour": r["tour"], "source": "espn", "start": r["start"],
        })
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    df["round_ord"] = df["round"].map(ROUND_ORDER).fillna(5)
    return df.sort_values(["start"]).reset_index(drop=True)
