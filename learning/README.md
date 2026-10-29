# Daily results feedback

The daily workflow imports market-free forecasts from command artifacts and native frozen records, then grades final results. Forecasts without a valid timestamp strictly before kickoff are excluded. One final pre-game freeze per event and model version is evaluated; repeated commands do not multiply the sample. Original probability records are hash-checked and retained in `forecasts.jsonl`; result corrections update `results.json` without rewriting predictions.

`report.json` tracks log loss, multiclass Brier score (sum over classes), winner accuracy, per-team score MAE, total/margin MAE where available, and missing-result coverage separately by model version. Daily base-data refreshes update the existing model inputs. The evaluation workflow preserves a permanent record before temporary GitHub artifacts expire.

A calibration challenger is considered only after 500 settled predictions on 30 distinct days. Its temperature is chosen on the earliest 60% of days; the next 20% and final untouched 20% each require at least 100 games, lower log loss, no worse Brier score, and a negative upper 95% day-clustered confidence bound. This is a screening test, not proof against repeated-testing bias. A candidate is recorded for review, never silently applied to live score distributions. Small samples retain the existing model.

Actions failures surface in GitHub; reports flag outcomes still missing after seven days. Scheduled execution depends on GitHub and source availability. No system can guarantee maximum accuracy or that every future update improves held-out performance.
