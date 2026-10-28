"""Human-readable reports in the TENNIS ORACLE required output format."""
from __future__ import annotations


def pct(x):
    return f"{100 * x:.1f}%"


def match_report(f: dict) -> str:
    A = f["players"]["A"]["name"]; B = f["players"]["B"]["name"]
    L = []
    L.append(f"MATCH: {A} vs {B}")
    L.append("")
    L.append(f"Tournament: {f.get('tournament') or '(not specified)'}")
    L.append(f"Round: {f.get('round') or '(not specified)'}")
    L.append(f"Surface: {f['surface']}{' (indoor)' if f.get('indoor') else ''}")
    L.append(f"Format: {f['format']}")
    L.append(f"Data cutoff: {f['data_cutoff']} (point-level stats); results through {f.get('results_cutoff', f['data_cutoff'])}")
    L.append(f"Data quality: {f['data_quality']}" + (f"  [{'; '.join(f['data_quality_notes'])}]" if f["data_quality_notes"] else ""))
    L.append(f"Model: {f['model']['version']} | {f['model']['n_sims']:,} Monte Carlo sims, seed {f['model']['seed']} | composite Elo diff {f['model']['composite_elo_diff']:+.0f}")
    L.append("")
    L.append("1. Match winner prediction")
    L.append(f"Predicted winner: {f['predicted_winner']}")
    for k, n in (("A", A), ("B", B)):
        o = f["fair_odds"][k]
        L.append(f"- {n} win probability: {pct(f['win_prob'][k])}  (fair odds {o['decimal']:.2f} / {o['american']})")
    L.append("")
    L.append("2. Exact set-score probabilities")
    for k, v in f["set_score_probs"].items():
        a, b = k.split("-")
        L.append(f"- {A} {a}-{b} {B}: {pct(v)}  (fair {1 / max(v, 1e-9):.2f})")
    L.append(f"Most likely exact outcome: {A} {f['most_likely_set_score']} {B}")
    L.append("")
    L.append("3. Projected individual set scores")
    L.append(f"Representative sequence for {f['most_likely_set_score']}: {', '.join(f['representative_sets'])} "
             f"(the single most frequent sequence, {pct(f['representative_sets_prob'])} of sims - representative, not definitive)")
    L.append("")
    g = f["games"]
    L.append("4. Expected total games")
    L.append(f"- Mean: {g['mean']:.1f}  Median: {g['median']:.0f}  10th-90th pct: {g['p10']:.0f}-{g['p90']:.0f}  SD: {g['sd']:.1f}")
    L.append("")
    L.append("5. Game-total probabilities (from the simulated CDF)")
    for o in f["over_under"]:
        L.append(f"- O/U {o['line']}: Over {pct(o['over'])} | Under {pct(o['under'])}")
    L.append("")
    L.append("6. Game-handicap probabilities")
    for h in f["handicaps"]:
        x = h["line"]
        L.append(f"- {A} -{x}: {pct(h[f'A -{x}'])} | {B} +{x}: {pct(h[f'B +{x}'])} || {B} -{x}: {pct(h[f'B -{x}'])} | {A} +{x}: {pct(h[f'A +{x}'])}")
    L.append("")
    L.append("7. Tiebreak and set probabilities")
    L.append(f"- At least one tiebreak: {pct(f['p_tiebreak'])}")
    L.append(f"- {A} wins a set: {pct(f['p_a_wins_set'])} | {B} wins a set: {pct(f['p_b_wins_set'])}")
    L.append(f"- Deciding set: {pct(f['p_decider'])}")
    eb = f["expected_breaks"]; bp = f["expected_break_points"]
    L.append(f"- Expected breaks of serve: {eb['total']:.2f} ({A} {eb['by_A']:.2f}, {B} {eb['by_B']:.2f}); break points created: {A} {bp['A_creates']:.1f}, {B} {bp['B_creates']:.1f}")
    m = f["model"]
    L.append(f"- Modelled serve-point win: {A} {pct(m['point_probs']['A_serve'])}, {B} {pct(m['point_probs']['B_serve'])}; hold: {pct(m['hold_probs']['A'])} / {pct(m['hold_probs']['B'])}")
    L.append("")
    L.append("8. Matchup assessment")
    for d in f["drivers"]:
        if d["type"] == "statistical":
            L.append(f"- [statistical] {d['driver']}: favours {d['favours']} ({d['elo_points']:+.0f} Elo pts weighted; raw {d['raw_diff']:+.0f})")
        elif d["type"] == "descriptive" and d.get("A") is not None:
            L.append(f"- [descriptive] {d['driver']}: {A} {pct(d['A'])} vs {B} {pct(d['B'])}")
        elif d["type"] == "qualitative":
            L.append(f"- [qualitative] {d['driver']}: {d['note']}")
    L.append("")
    s = f["strongest"]
    L.append("9. Strongest prediction")
    L.append(f"Highest-confidence outcome: {s['selection']}")
    L.append(f"Modeled probability: {pct(s['probability'])}")
    L.append(f"Fair mathematical odds: {s['fair_odds']['decimal']:.2f} / {s['fair_odds']['american']}  (model price, not a sportsbook price; not a proven +EV bet)")
    L.append(f"Confidence: {s['confidence']}")
    L.append(f"Main risk: {s['main_risk']}")
    L.append("")
    fs = f["final_score"]
    L.append("10. Final score prediction")
    L.append(f"{A.upper()} {fs['A_sets']} - {B.upper()} {fs['B_sets']}")
    L.append(f"Projected set scores: {', '.join(fs['sets'])}")
    cc = f["cross_check"]
    L.append("")
    L.append(f"Cross-check (MC vs exact analytic mixture): P(A) {cc['p_a'][0]:.4f} vs {cc['p_a'][1]:.4f}; "
             f"E[games] {cc['games_mean'][0]:.2f} vs {cc['games_mean'][1]:.2f}; max |diff| {cc['max_abs_diff_probs']:.4f} (MC s.e. {cc['mc_se_p_a']:.4f})")
    return "\n".join(L)


def slate_report(forecasts: list) -> str:
    L = ["| Match | Predicted Winner | Win % | Exact Sets | Expected Games | Strongest Outcome |", "|---|---|---|---|---|---|"]
    for f in forecasts:
        A = f["players"]["A"]["name"]; B = f["players"]["B"]["name"]
        w = f["predicted_winner"]; wp = max(f["win_prob"].values())
        L.append(f"| {A} vs {B} | {w} | {pct(wp)} | {f['most_likely_set_score']} | {f['games']['mean']:.1f} | {f['strongest']['selection']} ({pct(f['strongest']['probability'])}, {f['strongest']['confidence']}) |")
    L.append("")
    L.append("Strongest predictions ranked (probability x data reliability):")
    rel = {"HIGH": 1.0, "MEDIUM": 0.9, "LOW": 0.75}
    ranked = sorted(forecasts, key=lambda f: -f["strongest"]["probability"] * rel[f["data_quality"]])
    for i, f in enumerate(ranked, 1):
        s = f["strongest"]
        L.append(f"{i}. {s['selection']} - {pct(s['probability'])} [{s['confidence']}, data {f['data_quality']}] ({f['match']})")
    L.append("")
    L.append("Final set-score predictions:")
    for f in forecasts:
        A = f["players"]["A"]["name"]; B = f["players"]["B"]["name"]; fs = f["final_score"]
        L.append(f"{A} {fs['A_sets']}-{fs['B_sets']} {B} ({', '.join(fs['sets'])})")
    return "\n".join(L)
