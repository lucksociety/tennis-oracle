# Model and Discord review — 2026-10-29

Explicitly dated CLI prediction/slate builds now exclude results on/after that date. New forecasts retain full-precision win probabilities for winner verification. Legacy rounded 50/50 forecasts retain the declared side while final-score legality, probability mass, reproduction, and exact analytic cross-checks remain enforced.

This fixes the failure in Actions run 40899403895 (2026-10-29): Kamilla Rakhimova vs Maya Joint rounded to 0.5000 and falsely failed three winner-consistency checks, stopping publication. The daily workflow also now schedules, verifies, and prices the same requested date rather than verifying/pricing today's slate for a different date input.

`/tennis` now dispatches `discord-tennis.yml` by default: update results -> forecast/freeze -> verify -> post-freeze price comparison -> local report -> encrypted-relay interaction response. `RUN_MODE=snapshot` explicitly retains published-only reporting. HTTP delivery errors no longer silently count as success.

Validation: 35 Python tests, signed Worker -> correct workflow -> relay round-trip, Worker build, and local response build pass. All 74 committed forecasts on Oct 28-30 reproduce and verify (37/33/4). No frozen file was modified. This fixes operational/data correctness; a predictive accuracy increase is not established. Point-stat staleness and historical tournament-week date approximations still limit further accuracy analysis.

A read-only PR workflow now runs the model/command regressions. No dashboard presentation files were changed.

## Deployment prerequisites

Merge the workflow to main before deploying the Worker. Configure Worker secrets GITHUB_TOKEN (Actions:write for this repository) and RELAY_KEY; put the same RELAY_KEY in GitHub Actions secrets. Keep the existing Discord application ID/public key and interaction URL. Deploy from `worker/` using the existing Wrangler flow. Never put tokens in command inputs or source. The dedicated workflow saves response and forecast provenance artifacts for 14 days; it does not publish dashboard files or commit its new freezes. Its run artifacts must be retained separately for long-term grading. Queue delay plus runtime must fit Discord's 15-minute interaction lifetime; deployment must confirm this with a real command.
