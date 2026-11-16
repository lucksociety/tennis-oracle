#!/usr/bin/env python3
"""Model vs market on settled matches: the gate for un-pausing tennis bets.

    python research/market_benchmark.py

For every settled match it takes the last forecast frozen before the start (learning/forecasts.jsonl), the last
pre-match price snapshot with a two-sided moneyline no wider than 6c (market_snapshots/, Jupiter-era and
Polymarket-era files share one shape), and the result (learning/results.json). It prints log loss for both,
the paired difference with a bootstrap interval, and what happens where they disagree.

Read-only research. Nothing here is imported by the model, and no price is ever a model input.
Un-pause (tennis_oracle/markets.py BETS_PAUSED, docs/markets.js BETS_PAUSED) only when the interval for
"model - market" log loss is at or below zero on a sample collected AFTER the model change being judged.
"""
import datetime as dt
import glob
import json
import math
import random
import statistics as st
import unicodedata
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
norm = lambda s: "".join(c for c in unicodedata.normalize("NFKD", str(s).lower()) if c.isalnum())
ll = lambda p, y: -math.log(max(1e-9, p if y else 1 - p))


def rows():
    forecasts = [json.loads(x) for x in (ROOT / "learning/forecasts.jsonl").read_text().splitlines() if x.strip()]
    results = json.loads((ROOT / "learning/results.json").read_text())
    last = {}
    for f in sorted(forecasts, key=lambda x: x["frozen_at"]):
        if f["frozen_at"] < f["start"]: last[f["event_id"]] = f
    market = {}
    for path in sorted(set(glob.glob(str(ROOT / "market_snapshots/*/*.json")))):
        try: snap = json.loads(Path(path).read_text())
        except ValueError: continue
        at = snap["fetched_at_utc"]
        for e in snap["events"]:
            if e.get("isLive"): continue
            begin = dt.datetime.fromtimestamp(int(e["beginAt"]), dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            if at >= begin: continue
            ml = [m for m in e["markets"] if m.get("sportsMarketType") == "moneyline" and m.get("status") == "open"]
            if len(ml) != 2: continue
            q = {}
            for m in ml:
                a, b = (m["pricing"].get("buyYesPriceUsd") or 0) / 1e6, (m["pricing"].get("sellYesPriceUsd") or 0) / 1e6
                if not (0 < b <= a < 1) or a - b > 0.06 + 1e-9: q = None; break
                q[norm(m["title"])] = ((a + b) / 2, a)
            if not q or len(q) != 2: continue
            key = frozenset(q)
            if key not in market or market[key][0] < at: market[key] = (at, q, begin)
    out = []
    for eid, f in last.items():
        r = results.get(eid)
        if not r or r.get("label") not in f["labels"]: continue
        a, b = (norm(x) for x in f["labels"])
        m = market.get(frozenset((a, b)))
        if not m or abs((dt.datetime.fromisoformat(m[2].replace("Z", "+00:00")) - dt.datetime.fromisoformat(f["start"])).total_seconds()) > 2 * 86400: continue
        pa, pb = m[1][a][0], m[1][b][0]
        out.append({"model": f["probabilities"][0], "market": pa / (pa + pb), "won": int(r["label"] == f["labels"][0]),
                    "ask_a": m[1][a][1], "ask_b": m[1][b][1], "start": f["start"], "version": f["model"]})
    return out


def report(rs, seed=3):
    if not rs: return {"n": 0}
    d = [ll(r["model"], r["won"]) - ll(r["market"], r["won"]) for r in rs]
    rng = random.Random(seed); boot = sorted(st.mean(rng.choice(d) for _ in d) for _ in range(4000))
    out = {"n": len(rs), "from": min(r["start"] for r in rs)[:10], "to": max(r["start"] for r in rs)[:10],
           "model_log_loss": round(st.mean(ll(r["model"], r["won"]) for r in rs), 4), "market_log_loss": round(st.mean(ll(r["market"], r["won"]) for r in rs), 4),
           "model_accuracy": round(st.mean((r["model"] > .5) == bool(r["won"]) for r in rs), 3), "market_accuracy": round(st.mean((r["market"] > .5) == bool(r["won"]) for r in rs), 3),
           "model_minus_market_log_loss": round(st.mean(d), 4), "ci95": [round(boot[100], 4), round(boot[3900], 4)], "disagreements": {}}
    for th in (.05, .10, .15):
        big = [r for r in rs if abs(r["model"] - r["market"]) > th]
        if not big: continue
        wins = [(r["model"] > r["market"]) == bool(r["won"]) for r in big]
        out["disagreements"][f">{th:.2f}"] = {"n": len(big), "model_side_won": round(st.mean(wins), 3),
            "model_said": round(st.mean(r["model"] if r["model"] > r["market"] else 1 - r["model"] for r in big), 3),
            "market_said": round(st.mean(r["market"] if r["model"] > r["market"] else 1 - r["market"] for r in big), 3),
            "roi_at_ask_before_fees": round(st.mean((1 / (r["ask_a"] if r["model"] > r["market"] else r["ask_b"]) - 1) if w else -1 for r, w in zip(big, wins)), 3)}
    out["gate"] = "PASS: model at least matches the market" if out["ci95"][1] <= 0 else "FAIL: keep bets paused"
    return out


if __name__ == "__main__":
    print(json.dumps(report(rows()), indent=1))
