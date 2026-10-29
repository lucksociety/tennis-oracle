import pandas as pd
from tennis_oracle import build
from tennis_oracle.config import load_params


def test_explicit_forecast_date_excludes_same_day_and_future_results(monkeypatch):
    frame = pd.DataFrame({"date": pd.to_datetime(["2026-01-01", "2026-01-02", "2026-01-03"])})
    monkeypatch.setattr(build, "combined_frame", lambda *args: (frame, {
        "results_cutoff": "2026-01-03", "point_stats_cutoff": "2026-01-01"}))
    observed = []
    monkeypatch.setattr(build, "run_history", lambda eng, df, cb: observed.extend(df.date.tolist()))
    engine = build.build_engine("atp", load_params(), asof="2026-01-02")
    assert observed == [pd.Timestamp("2026-01-01")]
    assert engine.info["results_cutoff"] == "2026-01-01"
