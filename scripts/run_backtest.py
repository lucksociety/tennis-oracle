import json, sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from tennis_oracle.backtest import run
from tennis_oracle.config import save_params

t = time.time()
res, p10, chosen = run()
res["runtime_sec"] = round(time.time() - t, 1)
root = Path(__file__).resolve().parent.parent
(root / "backtests").mkdir(exist_ok=True)
(root / "backtests" / "backtest_results.json").write_text(json.dumps(res, indent=1, default=str))
save_params(p10, make_current=(chosen["version"] == "v1.0"))
if chosen["version"] != "v1.0":
    save_params(chosen, make_current=True)
print(json.dumps({k: res[k] for k in ("data", "variation_fit", "promoted", "counts")}, indent=1, default=str))
for part in ("validation", "test"):
    for nm, r in res[part].items():
        print(part, nm, json.dumps({k: r[k] for k in ("winner", "surface_elo_baseline", "blend_elo_baseline", "games_mae", "games_mae_naive_mean", "tiebreak_brier", "tiebreak_base_rate_brier", "exact_set_score_accuracy")}))
print(json.dumps(res["test"]["v1.0"]["calibration"]))
print(json.dumps(res["test"]["v1.0"]["by_source"]))

# compact copy for the dashboard (no per-match rows)
dash = {k: res[k] for k in ("data", "variation_fit", "promoted", "counts", "validation", "test", "runtime_sec")}
dash["params"] = {"v1.0": res["params_v1.0"], "v1.1-candidate": res["params_v1.1_candidate"]}
(root / "docs" / "data").mkdir(parents=True, exist_ok=True)
(root / "docs" / "data" / "backtest.json").write_text(json.dumps(dash, indent=1, default=str))
