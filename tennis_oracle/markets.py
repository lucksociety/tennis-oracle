"""Polymarket price comparison (POST-FREEZE ONLY).

Never imported by the model (predict / ratings / simulate / schedule); tests/test_markets.py enforces it.
Prices are fetched from Polymarket's public Gamma API after forecasts are frozen, stored as separate
timestamped snapshots under market_snapshots/, and compared with the frozen distributions in
docs/data/slate-<date>.json. Mirrors docs/markets.js exactly (parity test).

BETS ARE PAUSED (2026-11-16). On 308 settled matches with a pre-match price the market's log loss was
0.571 against the model's 0.623 (95% CI of the gap 0.027 to 0.079), and where the two disagreed by 10+
points the model's side won 27%, which is what the market said. Prices, edges and the would-qualify flag
are still recorded for every match so the comparison keeps accumulating; no outcome is recommended until
research/market_benchmark.py shows the model at least matching the market.
"""
from __future__ import annotations

import json
import math
import re
import time
import unicodedata
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
DOCS_DATA = ROOT / "docs" / "data"
SNAPS = ROOT / "market_snapshots"
GAMMA = "https://gamma-api.polymarket.com"
TENNIS_TAG = "864"
TOURS = ("atp",)                       # Gamma seriesSlug; doubles, ITF and WTA are not modelled
DEFAULT_FEE = {"rate": 0.05, "exponent": 1.0}   # Polymarket sports taker fee when a market exposes no schedule
TYPE_MAP = {"moneyline": "moneyline", "tennis_set_totals": "tennis_set_totals", "tennis_match_totals": "tennis_match_totals",
            "tennis_set_handicap": "handicap", "tennis_game_handicap": "tennis_game_handicap"}
HANDICAP_Q = re.compile(r"^.*?:\s*(.+?)\s*\(([+-]\d+(?:\.\d+)?)\)\s+vs\.?\s+(.+?)\s*\(([+-]\d+(?:\.\d+)?)\)\s*$")
BETS_PAUSED = True
PAUSE_VERDICT = "NO BET (bets paused: the model trails the market price on settled matches)"


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
    matches=[]
    for ev in events:
        if ev.get("subcategory") and m.get("tour") and ev["subcategory"] != m["tour"]: continue
        ps = event_players(ev)
        if not ps: continue
        s0, s1 = side(ps[0], A, B), side(ps[1], A, B)
        if not s0 or not s1 or s0 == s1: continue
        if not t0 or not ev.get('beginAt'):continue
        begin=float(ev['beginAt'])
        if datetime.fromtimestamp(begin,timezone.utc).astimezone(ZoneInfo('America/New_York')).date()!=datetime.fromtimestamp(t0,timezone.utc).astimezone(ZoneInfo('America/New_York')).date():continue
        matches.append(ev)
    unique={ev["eventId"]:ev for ev in matches}
    return next(iter(unique.values())) if len(unique)==1 else None


def parse_label(lbl):
    s = str(lbl or "").strip()
    r = re.match(r"^(over|under)\s+([0-9.]+)$", s, re.I)
    if r: return {"ou": r.group(1).lower(), "line": float(r.group(2))}
    r = re.match(r"^(.*\S)\s+([+-][0-9.]+)$", s)
    if r: return {"name": r.group(1), "line": float(r.group(2))}
    return {"name": s, "line": None}


def cost(p, fee=None):
    """Ask plus the Polymarket taker fee per contract: rate * (p * (1 - p)) ** exponent."""
    f = fee if fee and fee.get("rate") is not None else DEFAULT_FEE
    return p + float(f["rate"]) * (p * (1 - p)) ** float(f.get("exponent", 1))


def _list(v):
    if isinstance(v, str):
        try: v = json.loads(v)
        except ValueError: return []
    return v if isinstance(v, list) else []


def _fnum(x):
    try:
        x = float(x)
    except (TypeError, ValueError):
        return None
    return x if math.isfinite(x) else None


def adapt_gamma_event(e):
    """Gamma event -> the internal event shape (one market record per buyable outcome)."""
    tour = e.get("seriesSlug")
    try:
        begin = str(int(datetime.fromisoformat(str(e["startTime"]).replace("Z", "+00:00")).timestamp()))
    except (KeyError, ValueError, TypeError):
        return None
    if tour not in TOURS or not e.get("id") or " vs" not in str(e.get("title") or ""):
        return None
    markets = []
    for gm in e.get("markets") or []:
        t = TYPE_MAP.get(gm.get("sportsMarketType"))
        outs, toks = _list(gm.get("outcomes")), _list(gm.get("clobTokenIds"))
        if not t or len(outs) != 2 or len(toks) != 2: continue
        line = _fnum(gm.get("line"))
        if t == "moneyline": labels = [str(o) for o in outs]
        elif t == "tennis_set_totals" or t == "tennis_match_totals":
            if line is None or [str(o).split()[0].lower() for o in outs] != ["over", "under"]: continue
            labels = [f"Over {_fmt(line)}", f"Under {_fmt(line)}"]
        else:
            q = HANDICAP_Q.match(str(gm.get("question") or ""))
            if not q or [q.group(1), q.group(3)] != [str(o) for o in outs] or float(q.group(2)) != -float(q.group(4)): continue
            labels = [f"{outs[0]} {q.group(2)}", f"{outs[1]} {q.group(4)}"]
        bid, ask = _fnum(gm.get("bestBid")), _fnum(gm.get("bestAsk"))
        # Binary CLOB books mirror each other: buying outcome 1 at its ask is selling outcome 0 at its bid.
        quotes = [(ask, bid), (None if bid is None else 1 - bid, None if ask is None else 1 - ask)]
        sched = gm.get("feeSchedule") or {}
        fee = {"rate": 0.0, "exponent": 1.0} if gm.get("feesEnabled") is False else \
              {"rate": float(sched["rate"]), "exponent": float(sched.get("exponent", 1))} if "rate" in sched else None
        open_ = not gm.get("closed") and gm.get("active") is not False and gm.get("acceptingOrders") is not False
        for i in range(2):
            a, b = quotes[i]
            usd = lambda x: int(round(x * 1e6)) if x is not None and 0 < x < 1 else 0
            markets.append({"marketId": f"POLY-{gm.get('id')}-{i}", "sportsMarketType": t, "title": labels[i], "sportsLine": line,
                            "status": "open" if open_ else "closed", "marketOptions": [{"label": labels[i]}],
                            "pricing": {"buyYesPriceUsd": usd(a), "sellYesPriceUsd": usd(b), "buyNoPriceUsd": 0, "sellNoPriceUsd": 0},
                            "fee": fee, "tokenId": str(toks[i])})
    return {"eventId": f"POLY-{e['id']}", "subcategory": tour, "slug": e.get("slug"), "metadata": {"title": e.get("title")},
            "beginAt": begin, "isLive": bool(e.get("live") or e.get("ended")), "markets": markets}


def market_win_prob(ev, A, B):
    """No-vig pre-match moneyline for the slate's player A: the benchmark the model is graded against."""
    mids = {}
    for mk in (ev or {}).get("markets") or []:
        if mk.get("sportsMarketType") != "moneyline" or mk.get("status") != "open": continue
        pr = mk.get("pricing") or {}
        a, b = float(pr.get("buyYesPriceUsd") or 0) / 1e6, float(pr.get("sellYesPriceUsd") or 0) / 1e6
        sd = side(mk.get("title"), A, B)
        if not sd or not (0 < b <= a < 1) or a - b > 0.06 + 1e-9: return None
        mids[sd] = (a + b) / 2
    if set(mids) != {"A", "B"}: return None
    return r4(mids["A"] / (mids["A"] + mids["B"]))


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


def score_supports(mk, lab, f, A, B, bo=3):
    fs = f.get('final_score') or {}; a,b = fs.get('A_sets'),fs.get('B_sets')
    if any(isinstance(x,bool) or not isinstance(x,int) or x<0 for x in (a,b)) or max(a,b)!=bo//2+1 or a==b: return False
    t=mk.get('sportsMarketType'); sd=side(lab.get('name'),A,B); line=lab.get('line')
    if t=='moneyline': return a>b if sd=='A' else b>a if sd=='B' else False
    if not isinstance(line,(int,float)) or not math.isfinite(line): return False
    if t=='handicap': return (a-b if sd=='A' else b-a)+line>0 if sd else False
    if t=='tennis_set_totals': return a+b>line if lab.get('ou')=='over' else a+b<line if lab.get('ou')=='under' else False
    sets=fs.get('sets') or []; parsed=[]
    for text in sets:
        m=re.fullmatch(r'(\d+)-(\d+)(?:\(\d+\))?',str(text))
        if not m:return False
        x,y=map(int,m.groups());parsed.append((x,y))
    if len(parsed)!=a+b or sum(x>y for x,y in parsed)!=a or sum(y>x for x,y in parsed)!=b:return False
    ga,gb=sum(x for x,y in parsed),sum(y for x,y in parsed)
    if t=='tennis_game_handicap':return (ga-gb if sd=='A' else gb-ga)+line>0 if sd else False
    if t=='tennis_match_totals':
        totals=[ga+gb]
        if f.get('games_projected') is not None:totals.append(f['games_projected'])
        return all(v>line for v in totals) if lab.get('ou')=='over' else all(v<line for v in totals) if lab.get('ou')=='under' else False
    return False


def evaluate(m, ev, snapshot_at_utc=None, bets_paused=None):
    paused = BETS_PAUSED if bets_paused is None else bool(bets_paused)
    f = m.get("forecast")
    base = {"espn_id": m["espn_id"], "event_id": ev["eventId"] if ev else None,
            "event_title": (ev.get("metadata") or {}).get("title") if ev else None}
    if m.get("tour") != "atp": return {**base,"status":"excluded","best":None,"verdict":"Excluded by men-only policy","outcomes":[]}
    if not ev: return {**base, "status": "no_market", "best": None, "verdict": "NO BET (no market)", "outcomes": []}
    if not f or not f.get("dist"): return {**base, "status": "no_forecast", "best": None, "verdict": "NO BET (no frozen forecast)", "outcomes": []}
    snap = _iso(snapshot_at_utc) if snapshot_at_utc else time.time()
    if f.get("frozen_at_utc") and _iso(f["frozen_at_utc"]) > snap:
        return {**base, "status": "invalid_order", "best": None, "verdict": "NO BET (prices older than forecast)", "outcomes": []}
    base["model_p_a"] = r4(f["dist"]["win_prob"]["A"]) if isinstance((f["dist"].get("win_prob") or {}).get("A"), (int, float)) else None
    try:
        prematch = bool(m.get("start_utc")) and snap < _iso(m["start_utc"]) and not ev.get("isLive") and m.get("status") in (None, "STATUS_SCHEDULED")
    except (ValueError, TypeError):
        prematch = False
    base["market_p_a"] = market_win_prob(ev, m["players"][0]["name"], m["players"][1]["name"]) if prematch else None
    if (f.get("recommendation_quality") or {}).get("eligible") is not True:
        return {**base, "status": "input_quality", "best": None, "verdict": "NO BET (current performance inputs not verified)", "outcomes": []}
    A, B, bo = m["players"][0]["name"], m["players"][1]["name"], m.get("best_of") or 3
    outs = []
    for mk in ev.get("markets") or []:
        label = ((mk.get("marketOptions") or [{}])[0] or {}).get("label") or mk.get("title")
        lab = parse_label(label)
        price = float((mk.get("pricing") or {}).get("buyYesPriceUsd") or 0) / 1e6
        mp = model_prob(mk, lab, f["dist"], A, B, bo)
        o = {"market_id": mk.get("marketId"), "type": mk.get("sportsMarketType"), "label": label, "status": mk.get("status")}
        if "skip" in mp: o["skip"] = mp["skip"]; outs.append(o); continue
        if not math.isfinite(mp["p"]) or not 0 <= mp["p"] <= 1:
            o["skip"] = "invalid model probability"; outs.append(o); continue
        o["selection"] = mp["desc"]; o["model_p"] = r4(mp["p"])
        n = f.get("n_sims")
        lower = mp["p"] - 1.96 * math.sqrt(mp["p"] * (1 - mp["p"]) / n) if isinstance(n, int) and n >= 1000 else -1
        o["mc_lower_95"] = r4(lower)
        o["score_consistent"] = score_supports(mk, lab, f, A, B, bo)
        if mk.get("status") and mk["status"] != "open": o["skip"] = f"market {mk['status']}"; outs.append(o); continue
        if not (0 < price < 1): o["skip"] = "no ask price (no liquidity)"; outs.append(o); continue
        if price >= 0.99: o["skip"] = "ask at 0.99 placeholder (no real liquidity)"; outs.append(o); continue
        c = cost(price, mk.get("fee"))
        qualifies = bool(o["score_consistent"] and lower > 0.5 and lower > cost(min(.999,price+.01), mk.get("fee")) and o["model_p"] > 0.5 and r4(mp["p"] / c - 1) > 0)
        o.update(price=r4(price), implied=r4(c), edge=r4(mp["p"] - c), ev_per_1=r4(mp["p"] / c - 1),
                 would_qualify=qualifies, eligible=qualifies and not paused)
        outs.append(o)
    priced = [o for o in outs if o.get("implied") is not None]
    elig = sorted([o for o in priced if o["eligible"]], key=lambda o: (-o["ev_per_1"], -o["model_p"]))
    try:
        start = _iso(m.get("start_utc") or "")
    except (ValueError, TypeError):
        start = 0
    live = (m.get("status") and m["status"] != "STATUS_SCHEDULED") or ev.get("isLive") or not start or snap >= start
    best = elig[0] if elig else None
    if live:
        best = None; status = "not_prematch"
        verdict = "NO BET (match finished)" if m.get("completed") else "NO BET (match in progress - pre-match model not valid in-play)"
    elif not priced:
        status, verdict = "no_prices", "NO BET (no priced outcomes)"
    elif paused:
        status, verdict = "paused", PAUSE_VERDICT
    elif not best:
        status = "no_bet"
        any_edge = any(o["edge"] > 0 for o in priced); any_fav = any(o["model_p"] > 0.5 for o in priced)
        verdict = "NO BET (" + ("no positive edge" if not any_edge else "no outcome over 50%" if not any_fav else "no outcome with both >50% and positive edge") + ")"
    else:
        status = "bet"
        ev1 = best["ev_per_1"] * 100
        verdict = f"{best['selection']} @ {best['price']:.2f} (model {best['model_p'] * 100:.1f}%, edge {best['edge'] * 100:.1f} pts, EV {'+' if ev1 >= 0 else ''}{ev1:.1f}%)"
    return {**base, "status": status, "best": best, "verdict": verdict, "outcomes": outs, "n_priced": len(priced), "n_eligible": len(elig)}


def _gamma(params, retries=4):
    for i in range(retries):
        try:
            r = requests.get(f"{GAMMA}/events", params=params, headers={"accept": "application/json", "User-Agent": "tennis-oracle/1.1 (post-freeze prices)"}, timeout=30)
            if r.status_code == 429 or r.status_code >= 500: raise requests.HTTPError(f"HTTP {r.status_code}", response=r)
            r.raise_for_status(); data = r.json()
            if not isinstance(data, list): raise requests.RequestException("Gamma events page is not a list")
            return data
        except requests.RequestException as exc:
            status = getattr(getattr(exc, "response", None), "status_code", None)
            if i + 1 == retries or (status is not None and status != 429 and status < 500): raise
            time.sleep(min(10, 2 ** i))


def fetch_events(now=None, hours_back=12, days_ahead=3, get=None):
    """Open ATP singles events starting from 12 h ago (in play) to 3 days ahead, with their markets."""
    get = get or _gamma
    now = now or datetime.now(timezone.utc)
    lo, hi = (now - timedelta(hours=hours_back)).isoformat(), (now + timedelta(days=days_ahead)).isoformat()
    out, errors, urls = {}, [], [f"{GAMMA}/events?tag_id={TENNIS_TAG}&closed=false&start_time_min={lo}&start_time_max={hi}"]
    try:
        for page in range(30):
            data = get({"tag_id": TENNIS_TAG, "closed": "false", "limit": 100, "offset": page * 100, "order": "id", "ascending": "true",
                        "start_time_min": lo, "start_time_max": hi})
            for e in data:
                ev = adapt_gamma_event(e)
                if ev: out[ev["eventId"]] = ev
            if len(data) < 100: break
        else:
            errors.append("pagination safety limit reached; event list may be partial")
    except Exception as e:  # noqa: BLE001
        errors.append(f"gamma: {e}")
    return {"events": list(out.values()), "errors": errors, "urls": urls,
            "fetched_at_utc": now.isoformat(timespec="seconds").replace("+00:00", "Z")}


def snapshot_record(fetched):
    """Price snapshot: market prices only, no model numbers."""
    evs = []
    for e in fetched["events"]:
        evs.append({"eventId": e["eventId"], "subcategory": e.get("subcategory"), "metadata": {"title": (e.get("metadata") or {}).get("title")},
                    "beginAt": e.get("beginAt"), "isLive": e.get("isLive"),
                    "markets": [{"marketId": mk.get("marketId"), "sportsMarketType": mk.get("sportsMarketType"), "title": mk.get("title"),
                                 "sportsLine": mk.get("sportsLine"), "status": mk.get("status"),
                                 "marketOptions": [{"label": ((mk.get("marketOptions") or [{}])[0] or {}).get("label")}],
                                 "fee": mk.get("fee"),
                                 "pricing": {k: (mk.get("pricing") or {}).get(k) for k in ("buyYesPriceUsd", "sellYesPriceUsd", "buyNoPriceUsd", "sellNoPriceUsd")}}
                                for mk in e.get("markets") or []]})
    return {"source": "Polymarket Gamma API (gamma-api.polymarket.com; best bid/ask per market)", "fetched_at_utc": fetched["fetched_at_utc"],
            "urls": fetched["urls"], "errors": fetched["errors"], "n_events": len(evs), "events": evs}


def run(day: str, fetched=None, write=True):
    slate = json.loads((DOCS_DATA / f"slate-{day}.json").read_text())
    fetched = fetched or fetch_events()
    snap = snapshot_record(fetched)
    at = snap["fetched_at_utc"]
    rows = [evaluate(m, match_event(m, snap["events"]), snapshot_at_utc=at) for m in slate["matches"]]
    out = {"date": day, "snapshot_at_utc": at, "snapshot_path": None, "source": snap["source"], "errors": snap["errors"],
           "bets_paused": BETS_PAUSED,
           "rule": ("BETS PAUSED: no outcome is recommended while the model trails the market on settled matches. " if BETS_PAUSED else "")
                   + "Candidate rule (would_qualify) = highest EV per $1 among outcomes with model prob > 50% AND edge > 0 "
                     "(edge = model prob - ask incl. the Polymarket taker fee rate*p*(1-p)); otherwise NO BET",
           "note": "Prices are compared with forecasts frozen BEFORE the snapshot; they are never model inputs.",
           "matches": rows}
    if write and snap["n_events"]:
        d = SNAPS / day; d.mkdir(parents=True, exist_ok=True)
        p = d / (at.replace(":", "").replace("-", "") + ".json")
        p.write_text(json.dumps(snap, indent=0))
        out["snapshot_path"] = str(p.relative_to(ROOT))
    if write and (snap["n_events"] or not (DOCS_DATA / f"polymarket-{day}.json").exists()):
        (DOCS_DATA / f"polymarket-{day}.json").write_text(json.dumps(out, indent=1))
    return out
