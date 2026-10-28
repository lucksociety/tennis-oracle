"""Command line interface: `oracle ...`"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pandas as pd


def _engines(tours):
    from .build import build_engine
    from .config import load_params
    p = load_params()
    return {t: build_engine(t, p) for t in tours}


def cmd_predict(a):
    from .build import find_player
    from .freeze import freeze
    from .report import match_report
    eng = _engines([a.tour])[a.tour]
    ia, ib = find_player(eng, a.player_a), find_player(eng, a.player_b)
    ctx = {"a_elo_adj": a.a_adj, "b_elo_adj": a.b_adj, "injury_uncertain": a.injury_uncertain}
    if a.notes:
        ctx["notes"] = a.notes
    f = _forecast(eng, ia, ib, a, ctx)
    if a.freeze:
        p = freeze(f, a.date or str(date.today()))
        f["frozen_path"] = str(p) if p else "identical forecast already frozen"
    print(json.dumps(f, indent=1, default=str) if a.json else match_report(f))
    if a.freeze:
        print(f"\nFrozen: {f['frozen_path']}", file=sys.stderr)


def _forecast(eng, ia, ib, a, ctx):
    from .predict import forecast
    fr = a.final_rule or ("tb10" if a.slam else "tb7")
    f = forecast(eng, ia, ib, a.surface.capitalize(), a.bo, fr, a.tour, asof=a.date, indoor=a.indoor, context=ctx,
                 meta={"tournament": a.tournament, "round": a.round}, n_sims=a.sims, seed=a.seed, data_cutoff=eng.point_cutoff)
    f["results_cutoff"] = eng.info["results_cutoff"]
    return f


def cmd_slate(a):
    """Slate file: one match per line: Player A, Player B[, surface, bo, tour]"""
    from .build import find_player
    from .freeze import freeze
    from .report import match_report, slate_report
    lines = [l.strip() for l in open(a.file) if l.strip() and not l.startswith("#")]
    if lines and lines[0].split(",")[0].strip().lower() in ("a", "player_a", "player a", "player1"):
        lines = lines[1:]                      # optional header row
    tours = sorted(set((l.split(",")[4].strip() if len(l.split(",")) > 4 else a.tour) for l in lines))
    engs = _engines(tours)
    fs = []
    for l in lines:
        parts = [x.strip() for x in l.split(",")]
        ns = argparse.Namespace(**vars(a))
        if len(parts) > 2: ns.surface = parts[2]
        if len(parts) > 3: ns.bo = int(parts[3])
        if len(parts) > 4: ns.tour = parts[4]
        eng = engs[ns.tour]
        f = _forecast(eng, find_player(eng, parts[0]), find_player(eng, parts[1]), ns, {})
        if a.freeze:
            freeze(f, a.date or str(date.today()))
        fs.append(f)
        if a.full:
            print(match_report(f)); print("\n" + "=" * 80 + "\n")
    print(slate_report(fs))


def cmd_schedule(a):
    from .export import export_ratings
    from .schedule import build_slate
    from .config import load_params
    engs = _engines(["atp", "wta"])
    if not a.no_export:
        export_ratings(engs, load_params())
    tz = ZoneInfo("America/New_York")
    d0 = date.fromisoformat(a.date) if a.date else datetime.now(tz).date()
    for k in range(a.days):
        d = d0 + timedelta(days=k)
        s = build_slate(d, engs, n_sims=a.sims)
        nf = sum(1 for m in s["matches"] if m["forecast"])
        print(f"{d}: {len(s['matches'])} singles matches, {nf} with frozen forecasts")


def cmd_export(a):
    from .config import load_params
    from .export import export_ratings
    print(export_ratings(_engines(["atp", "wta"]), load_params()))


def cmd_update(a):
    from .build import sackmann_end
    from .data import download_sackmann, load_sackmann, update_espn_results
    download_sackmann(force=a.force)
    end = min(sackmann_end(load_sackmann(t)) for t in ("atp", "wta"))
    since = end.date() - timedelta(days=3) if a.full else max(end.date(), date.today() - timedelta(days=a.days))
    recs = update_espn_results(since, date.today() + timedelta(days=1), step_days=2 if a.full else 1)
    print(f"ESPN results cached: {len(recs)} (fetched from {since})")


def cmd_backtest(a):
    import runpy, pathlib
    runpy.run_path(str(pathlib.Path(__file__).resolve().parent.parent / "scripts" / "run_backtest.py"), run_name="__main__")


def cmd_audit(a):
    from .audit import audit
    r = audit()
    from datetime import datetime, timezone
    from .freeze import FORECASTS
    from .export import DOCS_DATA
    summ = {"n": r["n"], **r.get("overall", {}), **{k: r[k] for k in ("games_mae", "exact_sets_accuracy", "tiebreak_brier") if k in r}}
    doc = {"generated_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"), "summary": summ,
           **{k: v for k, v in r.items() if k not in ("rows",)}, "rows": r.get("rows", [])}
    FORECASTS.mkdir(parents=True, exist_ok=True)
    (FORECASTS / "audit.json").write_text(json.dumps(doc, indent=1, default=str))
    DOCS_DATA.mkdir(parents=True, exist_ok=True)
    (DOCS_DATA / "audit.json").write_text(json.dumps(doc, indent=1, default=str))
    if not a.json:
        r = {k: v for k, v in r.items() if k != "rows"}
    print(json.dumps(r, indent=1, default=str))


def main(argv=None):
    ap = argparse.ArgumentParser(prog="oracle", description="TENNIS ORACLE V1.0 - independent tennis prediction (no market data)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    def common(p):
        p.add_argument("--surface", default="hard", choices=["hard", "clay", "grass", "Hard", "Clay", "Grass"])
        p.add_argument("--indoor", action="store_true")
        p.add_argument("--bo", type=int, default=3, choices=[3, 5])
        p.add_argument("--tour", default="atp", choices=["atp", "wta"])
        p.add_argument("--final-rule", choices=["tb7", "tb10", "adv"], default=None)
        p.add_argument("--slam", action="store_true", help="Grand Slam: deciding set 10-point tiebreak")
        p.add_argument("--tournament", default=""); p.add_argument("--round", default="")
        p.add_argument("--date", default=None, help="forecast date (YYYY-MM-DD)")
        p.add_argument("--sims", type=int, default=None); p.add_argument("--seed", type=int, default=None)
        p.add_argument("--freeze", action="store_true", help="save as frozen pre-match forecast")
        p.add_argument("--json", action="store_true")

    p = sub.add_parser("predict"); p.add_argument("player_a"); p.add_argument("player_b"); common(p)
    p.add_argument("--a-adj", type=float, default=0.0, help="verified context adjustment for A (Elo pts, e.g. fatigue -25)")
    p.add_argument("--b-adj", type=float, default=0.0)
    p.add_argument("--injury-uncertain", action="store_true"); p.add_argument("--notes", default="")
    p.set_defaults(func=cmd_predict)
    p = sub.add_parser("slate"); p.add_argument("file"); common(p); p.add_argument("--full", action="store_true"); p.set_defaults(func=cmd_slate)
    p = sub.add_parser("schedule", help="fetch day's order of play (ESPN), freeze forecasts, write docs/data slate")
    p.add_argument("--date", default=None); p.add_argument("--days", type=int, default=1); p.add_argument("--sims", type=int, default=100_000)
    p.add_argument("--no-export", action="store_true"); p.set_defaults(func=cmd_schedule)
    p = sub.add_parser("export"); p.set_defaults(func=cmd_export)
    p = sub.add_parser("update-data"); p.add_argument("--force", action="store_true"); p.add_argument("--full", action="store_true")
    p.add_argument("--days", type=int, default=10); p.set_defaults(func=cmd_update)
    p = sub.add_parser("backtest"); p.set_defaults(func=cmd_backtest)
    p = sub.add_parser("audit"); p.add_argument("--json", action="store_true"); p.set_defaults(func=cmd_audit)
    a = ap.parse_args(argv)
    a.func(a)


if __name__ == "__main__":
    main()
