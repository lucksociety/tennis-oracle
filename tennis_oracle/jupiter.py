"""Jupiter prediction-market comparison (POST-FREEZE ONLY).

Never imported by the model (predict / ratings / simulate / schedule); tests/test_markets.py enforces it.
Prices are fetched after forecasts are frozen, stored as separate timestamped snapshots under
market_snapshots/, and compared with the frozen distributions in docs/data/slate-<date>.json.
Mirrors docs/markets.js exactly (parity test).
"""
from __future__ import annotations

import json
import math
import os
import re
import time
import unicodedata
from datetime import datetime, timezone
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
DOCS_DATA = ROOT / "docs" / "data"
SNAPS = ROOT / "market_snapshots"
API = "https://api.jup.ag/prediction/v1"
FEE_RATE = 0.07
LISTS = [("atp", "upcoming"), ("atp", "live"), ("wta", "upcoming"), ("wta", "live")]


def norm(s):
    s = unicodedata.normalize("NFKD", str(s or ""))
    s = "".join(c for c in s if not unicodedata.combining(c)).lower()
    s = re.sub(r"[^a-z\s-]", " ", s)
    return [t for t in re.split(r"[\s-]+", s) if t]


def same_player(a, b):
    ta, tb = norm(a), norm(b)
    if not ta or not tb: return False
    sb = set(tb); shared = [t for t in ta if t in sb]
    if len(shared) >= 2: return True
    if len(shared) == 1 and len(shared[0]) >= 3:
        if len(ta) == 1 or len(tb) == 1: return True
        return ta[0][0] == tb[0][0]
    return False


def event_players(ev):
    t = (ev.get("metadata") or {}).get("title") or ""
    body = t[t.index(":") + 1:] if ":" in t else t
    parts = re.split(r"\s+vs\.?\s+", body, flags=re.I)
    return [p.strip() for p in parts] if len(parts) == 2 else None


def side(name, A, B):
    a, b = same_player(name, A), same_player(name, B)
    return "A" if a and not b else "B" if b and not a else None


def _iso(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()


def match_event(m, events):
    A, B = m["players"][0]["name"], m["players"][1]["name"]
    t0 = _iso(m["start_utc"]) if m.get("start_utc") else None
    for ev in events:
        if ev.get("subcategory") and m.get("tour") and ev["subcategory"] != m["tour"]: continue
        ps = event_players(ev)
        if not ps: continue
        s0, s1 = side(ps[0], A, B), side(ps[1], A, B)
        if not s0 or not s1 or s0 == s1: continue
        if t0 and ev.get("beginAt") and abs(float(ev["beginAt"]) - t0) > 36 * 3600: continue
        return ev
    return None


def parse_label(lbl):
    s = str(lbl or "").strip()
    r = re.match(r"^(over|under)\s+([0-9.]+)$", s, re.I)
    if r: return {"ou": r.group(1).lower(), "line": float(r.group(2))}
    r = re.match(r"^(.*\S)\s+([+-][0-9.]+)$", s)
    if r: return {"name": r.group(1), "line": float(r.group(2))}
    return {"name": s, "line": None}


def cost(p):
    return p + FEE_RATE * p * (1 - p)


def r4(x):
    return math.floor(x * 1e4 + 0.5) / 1e4  # JS Math.round semantics


def _isint(x):
    return float(x).is_integer()


def model_prob(mk, lab, dist, A, B, bo):
    t = mk.get("sportsMarketType")
    ss, gp, dp = dist["set_score_probs"], dist["games_pmf"], dist["games_diff_pmf"]
    sets_of = lambda k: tuple(int(x) for x in k.split("-"))
    if t == "moneyline":
        sd = side(lab.get("name"), A, B)
        if not sd: return {"skip": "unmatched player"}
        return {"p": dist["win_prob"][sd], "desc": f"{lab['name']} to win"}
    if t in ("tennis_set_totals", "tennis_match_totals"):
        if "ou" not in lab: return {"skip": "unparsed label"}
        L = lab["line"]
        if _isint(L): return {"skip": "integer line (push) not modelled"}
        sets = t == "tennis_set_totals"
        over = sum(v for k, v in ss.items() if sum(sets_of(k)) > L) if sets else sum(v for k, v in gp.items() if int(k) > L)
        return {"p": over if lab["ou"] == "over" else 1 - over, "desc": f"{'Over' if lab['ou'] == 'over' else 'Under'} {_fmt(L)} {'sets' if sets else 'games'}"}
    if t in ("handicap", "tennis_game_handicap"):
        sd = side(lab.get("name"), A, B)
        if not sd or lab.get("line") is None: return {"skip": "unparsed label"}
        L = lab["line"]
        if _isint(L): return {"skip": "integer line (push) not modelled"}
        sg = 1 if sd == "A" else -1; sets = t == "handicap"
        p = sum(v for k, v in ss.items() if sg * (sets_of(k)[0] - sets_of(k)[1]) + L > 0) if sets else \
            sum(v for k, v in dp.items() if sg * int(k) + L > 0)
        return {"p": p, "desc": f"{lab['name']} {'+' if L > 0 else ''}{_fmt(L)} {'sets' if sets else 'games'}"}
    return {"skip": f"market type {t} not modelled"}


def _fmt(x):
    return str(int(x)) if float(x).is_integer() else str(x)


def evaluate(m, ev, snapshot_at_utc=None):
    f = m.get("forecast")
    base = {"espn_id": m["espn_id"], "event_id": ev["eventId"] if ev else None,
            "event_title": (ev.get("metadata") or {}).get("title") if ev else None}
    if not ev: return {**base, "status": "no_market", "best": None, "verdict": "NO BET (no market)", "outcomes": []}
    if not f or not f.get("dist"): return {**base, "status": "no_forecast", "best": None, "verdict": "NO BET (no frozen forecast)", "outcomes": []}
    snap = _iso(snapshot_at_utc) if snapshot_at_utc else time.time()
    if f.get("frozen_at_utc") and _iso(f["frozen_at_utc"]) > snap:
        return {**base, "status": "invalid_order", "best": None, "verdict": "NO BET (prices older than forecast)", "outcomes": []}
    A, B, bo = m["players"][0]["name"], m["players"][1]["name"], m.get("best_of") or 3
    outs = []
    for mk in ev.get("markets") or []:
        label = ((mk.get("marketOptions") or [{}])[0] or {}).get("label") or mk.get("title")
        lab = parse_label(label)
        price = float((mk.get("pricing") or {}).get("buyYesPriceUsd") or 0) / 1e6
        mp = model_prob(mk, lab, f["dist"], A, B, bo)
        o = {"market_id": mk.get("marketId"), "type": mk.get("sportsMarketType"), "label": label, "status": mk.get("status")}
        if "skip" in mp: o["skip"] = mp["skip"]; outs.append(o); continue
        o["selection"] = mp["desc"]; o["model_p"] = r4(mp["p"])
        if mk.get("status") and mk["status"] != "open": o["skip"] = f"market {mk['status']}"; outs.append(o); continue
        if not (0 < price < 1): o["skip"] = "no ask price (no liquidity)"; outs.append(o); continue
        if price >= 0.99: o["skip"] = "ask at 0.99 placeholder (no real liquidity)"; outs.append(o); continue
        c = cost(price)
        o.update(price=r4(price), implied=r4(c), edge=r4(mp["p"] - c), ev_per_1=r4(mp["p"] / c - 1),
                 eligible=bool(mp["p"] > 0.5 and mp["p"] - c > 0))
        outs.append(o)
    priced = [o for o in outs if o.get("implied") is not None]
    elig = sorted([o for o in priced if o["eligible"]], key=lambda o: (-o["ev_per_1"], -o["model_p"]))
    live = m.get("status") and m["status"] != "STATUS_SCHEDULED"
    best = elig[0] if elig else None
    if live:
        best = None; status = "not_prematch"
        verdict = "NO BET (match finished)" if m.get("completed") else "NO BET (match in progress - pre-match model not valid in-play)"
    elif not priced:
        status, verdict = "no_prices", "NO BET (no priced outcomes)"
    elif not best:
        status = "no_bet"
        any_edge = any(o["edge"] > 0 for o in priced); any_fav = any(o["model_p"] > 0.5 for o in priced)
        verdict = "NO BET (" + ("no positive edge" if not any_edge else "no outcome over 50%" if not any_fav else "no outcome with both >50% and positive edge") + ")"
    else:
        status = "bet"
        ev1 = best["ev_per_1"] * 100
        verdict = f"{best['selection']} @ {best['price']:.2f} (model {best['model_p'] * 100:.1f}%, edge {best['edge'] * 100:.1f} pts, EV {'+' if ev1 >= 0 else ''}{ev1:.1f}%)"
    return {**base, "status": status, "best": best, "verdict": verdict, "outcomes": outs, "n_priced": len(priced), "n_eligible": len(elig)}


def fetch_events(gap=2.2, api_key=None):
    api_key = api_key or os.environ.get("JUPITER_API_KEY")
    out, errors, urls = [], [], []
    for i, (sub, filt) in enumerate(LISTS):
        url = f"{API}/events?category=sports&subcategory={sub}&filter={filt}&includeMarkets=true&includeAllMarkets=true&start=0&end=100"
        urls.append(url)
        try:
            r = requests.get(url, headers={"accept": "application/json", **({"x-api-key": api_key} if api_key else {})}, timeout=30)
            if r.status_code != 200: errors.append(f"{sub}/{filt}: HTTP {r.status_code} {r.text[:120]}")
            else: out.extend(r.json().get("data") or [])
        except Exception as e:  # noqa: BLE001
            errors.append(f"{sub}/{filt}: {e}")
        if i < len(LISTS) - 1: time.sleep(gap)
    seen, evs = set(), []
    for e in out:
        if e["eventId"] not in seen: seen.add(e["eventId"]); evs.append(e)
    return {"events": evs, "errors": errors, "urls": urls,
            "fetched_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")}


def snapshot_record(fetched):
    """Price snapshot: market prices only, no model numbers."""
    evs = []
    for e in fetched["events"]:
        evs.append({"eventId": e["eventId"], "subcategory": e.get("subcategory"), "metadata": {"title": (e.get("metadata") or {}).get("title")},
                    "beginAt": e.get("beginAt"), "isLive": e.get("isLive"),
                    "markets": [{"marketId": mk.get("marketId"), "sportsMarketType": mk.get("sportsMarketType"), "title": mk.get("title"),
                                 "sportsLine": mk.get("sportsLine"), "status": mk.get("status"),
                                 "marketOptions": [{"label": ((mk.get("marketOptions") or [{}])[0] or {}).get("label")}],
                                 "pricing": {k: (mk.get("pricing") or {}).get(k) for k in ("buyYesPriceUsd", "sellYesPriceUsd", "buyNoPriceUsd", "sellNoPriceUsd")}}
                                for mk in e.get("markets") or []]})
    return {"source": "Jupiter Prediction API (api.jup.ag/prediction/v1, markets provided by Polymarket)", "fetched_at_utc": fetched["fetched_at_utc"],
            "urls": fetched["urls"], "errors": fetched["errors"], "n_events": len(evs), "events": evs}


def run(day: str, fetched=None, write=True):
    slate = json.loads((DOCS_DATA / f"slate-{day}.json").read_text())
    fetched = fetched or fetch_events()
    snap = snapshot_record(fetched)
    at = snap["fetched_at_utc"]
    rows = [evaluate(m, match_event(m, snap["events"]), snapshot_at_utc=at) for m in slate["matches"]]
    out = {"date": day, "snapshot_at_utc": at, "snapshot_path": None, "source": snap["source"], "errors": snap["errors"],
           "rule": "best bet = highest EV per $1 among outcomes with model prob > 50% AND edge > 0 (edge = model prob - ask incl. 0.07*p*(1-p) fee); otherwise NO BET",
           "note": "Prices are compared with forecasts frozen BEFORE the snapshot; they are never model inputs.",
           "matches": rows}
    if write and snap["n_events"]:
        d = SNAPS / day; d.mkdir(parents=True, exist_ok=True)
        p = d / (at.replace(":", "").replace("-", "") + ".json")
        p.write_text(json.dumps(snap, indent=0))
        out["snapshot_path"] = str(p.relative_to(ROOT))
    if write and (snap["n_events"] or not (DOCS_DATA / f"jupiter-{day}.json").exists()):
        (DOCS_DATA / f"jupiter-{day}.json").write_text(json.dumps(out, indent=1))
    return out
