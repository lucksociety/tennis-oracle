# TENNIS ORACLE V1.0

An independent, statistics-only tennis prediction and simulation engine for ATP and WTA singles, with a static in-browser dashboard.

**Live dashboard:** https://lucksociety.github.io/tennis-oracle/

> **No betting odds, market prices, consensus picks, tipster, media or other people's predictions are used anywhere**, whether as inputs, for calibration, or for validation. The data loaders refuse odds-like columns (`assert_no_market_columns`), and any odds-like field in the schedule feed is stripped (`_strip_market`). The "fair odds" shown are just `1/p` from this model. They are not sportsbook prices and do not show that a bet has value.

## What it does

For any matchup (surface, indoor flag, best-of-3/5, deciding-set rule) it reports:

1. Match winner, win probabilities and fair decimal/American odds
2. Exact set-score probabilities
3. A representative set-score sequence (the most frequent simulated path, not a certainty)
4. Total games: mean, median, 10th–90th percentile and SD
5. Over/under probabilities for game totals, read from the simulated CDF
6. ± game-handicap probabilities
7. Tiebreak probability, chance of each player winning a set, deciding-set probability, expected breaks and break points, modelled serve-point and hold rates
8. Matchup drivers (statistical, descriptive or qualitative, each labelled)
9. Strongest prediction, with its probability, fair odds, confidence (High/Medium/Low) and main risk
10. Final score prediction (sets plus projected set scores)

It also rates data quality as **HIGH / MEDIUM / LOW** and gives the reasons.

The multi-match slate output has a summary table, rankings and a vertical score list.

## Dashboard (GitHub Pages, `docs/`)

- **Daily Slate.** Pick a day (default: today, America/New_York) and press **▶ Simulate**.
  - Every match on the day's schedule runs through the newest model in the browser: 100,000 Monte Carlo sims per match, fixed seed.
  - Each match shows the predicted winner, win %, final score prediction (sets + projected set scores), most likely set score, expected games, tiebreak %, strongest prediction and data quality. Alongside it are the server-side frozen pre-match forecast and, once a match is over, the actual result with a hit/miss mark.
  - Also shown: the multi-match summary table, rankings and a copyable vertical score list.
  - **Manual matchups** (fallback/additions) go in as one line each: `Player A, Player B, surface, best-of, tour[, tb10]`.
  - Every browser run is frozen as an append-only version in `localStorage` and can be downloaded as JSON, with timestamp, model version, seed and data cutoff.
  - URL parameters: `?date=YYYY-MM-DD&tour=atp|wta&run=1`.
- **Single Match.** The full 10-section report, a total-games distribution chart, set-score bars, O/U and handicap tables, 52-week stat comparison, and an exact-Markov cross-check of the Monte Carlo.
- **Backtest & Calibration.** The real walk-forward results below, a reliability diagram, tables by tour/surface, and the live audit of frozen forecasts.

The browser engine (`docs/engine.js`) is a line-for-line port of the Python scoring model. A pytest checks that the two agree to 1e-9. It reads the model version and all parameters from `docs/data/ratings.json`, which the pipeline re-exports on every run, so the dashboard always uses the newest promoted model.

## Daily schedule: where it comes from

`.github/workflows/daily.yml` runs every 4 hours, and can also be started by hand with `workflow_dispatch` and an optional date. Each run:

1. Runs the tests.
2. `oracle update-data` pulls newly completed results from **ESPN's public tennis scoreboard API** (`site.api.espn.com/apis/site/v2/sports/tennis/{atp,wta}/scoreboard?dates=YYYYMMDD`).
   - This feed carries fixtures, statuses and scores only. It has no odds, and any odds/predictor-like keys are removed defensively anyway.
3. `oracle schedule --days 2`:
   - fetches today's and tomorrow's singles order of play (America/New_York dates);
   - rebuilds the ratings and re-exports `docs/data/ratings.json`;
   - freezes a pre-match forecast for every not-yet-started match in `forecasts/YYYY-MM-DD/<espn-id>-<a>-vs-<b>/vN.json`;
   - writes `docs/data/slate-YYYY-MM-DD.json`.
4. `oracle audit` scores settled frozen forecasts and writes `forecasts/audit.json` and `docs/data/audit.json`.
5. Commits `docs/data`, `forecasts` and `data/espn`.

ESPN does not publish the court surface. It is inferred from the Grand Slam name, then from the tournament's or city's history in the Sackmann data. Otherwise it falls back to Hard and is marked `assumed`.

Format rules:
- Best-of-5 applies to ATP main-draw Grand Slam matches.
- Grand Slam deciding sets use a 10-point tiebreak; all other deciding sets use the standard 7-point tiebreak.

## Model

**Data**
- Jeff Sackmann's `tennis_atp` / `tennis_wta` match files, 2012–2026 (CC BY-NC-SA 4.0).
- The upstream GitHub repositories now return 404, so the files are read from an archival mirror of the same CSVs: `Aneeshers/tennis-sackmann-archive`, a June 2026 snapshot.
- That data runs to Roland Garros 2026, which gives a point-level stats cutoff of 2026-06-08.
- Completed match scores after that date come from the ESPN scoreboard and update Elo and form only. As of 2026-10-27 that is 1,677 ATP and 2,975 WTA matches.

**Ratings** (`tennis_oracle/ratings.py`)
- **Elo:** overall and surface-specific (Hard/Clay/Grass), FiveThirtyEight-style, with K = 250/(n+5)^0.4 and a 1.1× multiplier at Slams.
- **Opponent-adjusted serve/return ratings** in logit space, where logit(serve-point win) = tour/surface baseline + Serve(server) − Return(returner).
  - Updated online with a 1/(n+prior) learning rate, which shrinks small samples toward the baseline.
  - Per-surface offsets.
  - Decay toward 0 when no stats have arrived for more than 60 days.
- **Recent form:** an exponentially weighted residual of results against Elo expectation (half-life 8 matches), shrunk by n/(n+6).

**Composite rating**

D = scale × Σ wᵢ·componentᵢ + verified manual context (capped at ±100 Elo).

| Weights | Surface Elo | Serve/Return | Overall Elo | Form | Context | Scale |
|---|---|---|---|---|---|---|
| v1.0 (spec) | 0.35 | 0.25 | 0.20 | 0.15 | 0.05 | 0.929 |
| **v1.1 (current)** | 0.1645 | 0.4696 | 0.318 | −0.002 | 0.05 | 0.743 |

v1.1's weights were refit by logistic regression on 2014–2023. They were promoted only because they beat v1.0 on the held-out 2024 validation season. The active version is stored in `models/CURRENT`.

**Point model** (`scoring.py`, `simulate.py`)
- Each player's serve-point probability keeps their serve/return style. One logit shift is solved so that the match-win probability equals the composite forecast.
- An exact Markov chain covers games, tiebreaks (7- or 10-point, with the correct A/B/B/A serve rotation), sets (tiebreak or advantage) and Bo3/Bo5 matches.
- A vectorised point-by-point Monte Carlo runs 100,000 simulations with a fixed seed of 20261027, which is recorded in every forecast.
- **Per-match variation:** a bivariate-normal logit shock on both players' serve levels, with σ = 0.251 and ρ = −0.055.
  - These values were estimated from data, as the residual variance of serve-point rates beyond what binomial noise explains.
  - They widen game-total and set-score distributions realistically.
- Every Monte Carlo result is cross-checked against an exact Gauss–Hermite mixture of the Markov model, and the difference is reported.

**Freezing & audit** (`freeze.py`, `audit.py`)
- Pre-match forecasts are written with exclusive-create and are never edited.
- If an input changes before play (new results, format, context), a new `vN.json` is written with its reason. Identical inputs are deduplicated by hash.
- After the match, the audit reports Brier score, log loss, accuracy, games MAE, exact-set accuracy and tiebreak Brier, broken down by surface, tour and data quality, plus calibration buckets.

## Backtest (real, walk-forward, no look-ahead)

`oracle backtest` (scripts/run_backtest.py):
- Ratings update chronologically, match by match, from 2012. Each prediction uses only matches completed before it.
- Splits:
  - train 2014–2023 (46,430 matches)
  - validation 2024 (5,289)
  - **test 2025 – 2026-10-27 (10,806, ATP+WTA)**, which includes the 3,603 ESPN-sourced matches from June to October 2026
- Completed matches only; retirements and walkovers are excluded.

**Test set, match winner**

| Model | Log loss | Brier | Accuracy |
|---|---|---|---|
| Oracle v1.0 (spec weights) | 0.6234 | 0.2175 | 64.2% |
| **Oracle v1.1 (current)** | **0.6227** | **0.2172** | **64.9%** |
| Plain surface-Elo baseline | 0.6456 | 0.2264 | 63.0% |
| 538-style blended Elo | 0.6308 | 0.2202 | 63.7% |

**Validation 2024, log loss:** v1.0 0.6136, v1.1 0.6111, surface Elo 0.6380.

**Score-model checks (test, v1.1)**
- Total-games MAE is 5.21, against 5.24 for always predicting the mean. That is an honest but small edge, since game totals are noisy.
- Tiebreak Brier is 0.2060, against 0.2190 for the base rate.
- The most likely exact set score was right 42.9% of the time.

**Calibration (test, v1.1, favourite's probability)**

| Predicted | n | Mean predicted | Observed |
|---|---|---|---|
| 50–60% | 4,272 | 54.8% | 55.6% |
| 60–70% | 3,185 | 64.7% | 63.7% |
| 70–80% | 2,016 | 74.6% | 73.3% |
| 80–90% | 1,149 | 84.8% | 83.3% |
| 90–100% | 184 | 91.9% | 93.5% |

**By segment (test, v1.1, log loss: Oracle vs surface Elo)**

| Segment | n | Oracle | Surface Elo |
|---|---|---|---|
| ATP Hard | 3,073 | 0.621 | 0.644 |
| ATP Clay | 1,489 | 0.629 | 0.652 |
| ATP Grass | 577 | 0.617 | 0.649 |
| WTA Hard | 3,844 | 0.625 | 0.640 |
| WTA Clay | 1,146 | 0.609 | 0.634 |
| WTA Grass | 677 | 0.633 | 0.690 |

The ESPN segment, which has no fresh serve stats, scores 0.636 against 0.657. The Sackmann segment scores 0.616.

Backtest speed-up: the backtest interpolates the exact model on a precomputed (p_serve_A, p_serve_B) grid with step 0.005. Live forecasts use the exact model plus Monte Carlo.

## CLI

```bash
pip install -e .
oracle update-data                 # download Sackmann CSVs + ESPN results since the cutoff
oracle predict "Sinner" "Alcaraz" --surface hard --indoor --bo 3 --tour atp
oracle predict "Swiatek" "Sabalenka" --surface clay --bo 3 --tour wta --a-adj -20 --json
oracle schedule --days 2           # today's+tomorrow's fixtures -> frozen forecasts + docs/data slates
oracle slate matches.csv           # your own slate (a,b,surface,bo,tour[,final_rule])
oracle export                      # docs/data/ratings.json for the dashboard
oracle backtest                    # walk-forward backtest -> backtests/backtest_results.json
oracle audit                       # score frozen forecasts against results
python -m pytest -q tests          # scoring-logic tests (incl. JS/Python parity when node is installed)
```

### Sample run

Run on 2026-10-27 for a hypothetical indoor match on 2026-10-28. Point-level data cutoff 2026-06-08; results through 2026-10-27.

`oracle predict "Sinner" "Alcaraz" --surface hard --indoor --bo 3 --tour atp --date 2026-10-28`:

- **Win probabilities:** Sinner 66.5% (fair 1.50 / −199), Alcaraz 33.5% (2.99 / +199)
- **Set scores:** 2-0 44.5%, 2-1 22.0%, 0-2 17.8%, 1-2 15.7%
- **Games:** mean 24.2, median 23, p10–p90 17–33
- **Other probabilities:** at least one tiebreak 44.9%, deciding set 37.7%
- **Final score prediction:** Sinner 2-0, 6-4 6-4
- **Strongest prediction:** Sinner to win at least one set, 82.2% (Medium)
- **Data quality:** MEDIUM. Both players' latest point-level stats are more than 5 months old.

## Limitations (read these)

- **Stale serve statistics.** Point-level stats end at 2026-06-08 because the Sackmann data is frozen there. Results after that come from ESPN and are scores only, so they update Elo and form but not serve/return ratings. Data quality is therefore capped at MEDIUM for current matches.
- **ESPN coverage.** The scoreboard covers tour-level events, including qualifying. It does not cover Challengers or ITFs, so some players have thin histories and some can't be rated at all; those are skipped and listed.
- **Inferred surface.** The surface for scheduled matches is inferred, not published, and is marked `assumed` when it falls back to Hard.
- **Indoor/outdoor** is not in the feed. The indoor adjustment is 0 until it can be estimated.
- **Not modelled automatically:** fatigue, injuries, weather, altitude, balls and court speed. Enter verified facts as context adjustments (`--a-adj/--b-adj`, max ±100 Elo) or with the injury-uncertain flag. The model never fabricates these inputs.
- Set-score sequences are representative paths, not certainties.
- This is a statistical model for analysis. It is not betting advice, and its fair odds say nothing about value against any market.

## Licence

Code: MIT. Data: Jeff Sackmann's tennis_atp / tennis_wta, CC BY-NC-SA 4.0, non-commercial. Derived files in `docs/data` and `forecasts/` inherit that licence.

## Simulator verification

`oracle verify --date YYYY-MM-DD [--days N] [--json]` audits every frozen forecast: n_sims ≥ 100,000 and a recorded
seed; a re-run of `simulate_match` with the recorded serve-point probabilities, format, σ/ρ, n and seed reproduces the
frozen numbers (exactly for new forecasts, which store full-precision probabilities; within MC error for older ones that
stored 4-dp values); MC vs exact Markov-mixture cross-check within 4 MC standard errors; set-score, games and
handicap distributions internally consistent; projected final score legal and equal to the winner's modal set score.
Runs in the daily Action; results in `docs/data/verify-<date>.json`.

Score study (`scripts/score_study.py`, walk-forward train 2014–23 / validate 2024 / test 2025–26): the serve-variation σ
multiplier 0.9 beat 1.0 on validation by a noise-level margin (score log-loss 4.3587 vs 4.3605) and not meaningfully on test,
so σ stays at 1.0. The games median beats the mean as a point prediction (MAE 4.99 vs 5.14 validation, 5.06 vs 5.21 test),
so "projected games" = median.

## Jupiter best bets (post-freeze only)

`oracle markets --date D` fetches Jupiter Prediction API tennis events (api.jup.ag/prediction/v1, Polymarket-provided markets)
AFTER forecasts are frozen, saves a timestamped price snapshot in `market_snapshots/<date>/`, and writes
`docs/data/jupiter-<date>.json`. For each outcome: implied = ask + fee 0.07·p·(1−p) (Jupiter fee schedule), edge = model − implied,
EV per $1 = model / implied − 1. Best bet = highest-EV outcome with model probability > 50% AND edge > 0; otherwise NO BET
("no market" when Jupiter lists none; started matches are never bet because the pre-match model is not an in-play model).
Prices are never inputs: `tests/test_markets.py` asserts no model/forecast module or `engine.js` imports or reads price data.
Note: Jupiter geo-restricts US (and South Korea) IPs; the Action step is allowed to fail.

## Discord `/tennis`

Cloudflare Worker in `worker/` (`node build.mjs` bundles `docs/engine.js` + `docs/markets.js`). `POST /interactions` verifies the
Ed25519 signature, defers, then posts the NY-date slate (winner, win %, final score, projected games, Jupiter best bet or NO BET,
plus top-probability and top-EV leaderboards) in < 2000-char messages. Optional `date` option uses autocomplete (Today / Tomorrow / Yesterday / published slate dates; typed today, MM-DD, YYYY-MM-DD also work); always all ATP+WTA. `GET /api/bets?date=` returns live evaluations (CORS).
Live: https://tennis-oracle.dreiss324.workers.dev (Interactions Endpoint: /interactions). Register the command with `DISCORD_BOT_TOKEN=... python scripts/register_commands.py`; deploy with `worker/deploy.sh`.
The bot token is NOT needed by the Worker (follow-ups use the interaction token).
