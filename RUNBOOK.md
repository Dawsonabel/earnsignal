# Runbook

## Commands

All `make` targets load `.env` first. The underlying CLI is
`python -m earnsignal.pipeline <cmd>`.

| Command | What it does | Typical time |
|---|---|---|
| `make init` | Creates tables, syncs `config/universe*.yaml`, seeds the research log | seconds |
| `make collect` | Collects everything. First run downloads SEC history, ~35 quarterly insider zips (~10 MB each) and Wikipedia since 2015 | 20–40 min first time, ~5–10 min after |
| `python -m earnsignal.pipeline collect --only sec,prices --tickers DUOL,UBER` | Partial collection. Sources: `sec, prices, earnings_dates, wikipedia, jobs, appstore, insider, options` | — |
| `make backtest` | Builds the point-in-time panel, runs out-of-sample tests and the options scenarios, writes results to the research log | 2–15 min |
| `make score` | Grades past rankings against outcomes, then ranks announcements in the next 45 days | ~1 min |
| `make daily` | Incremental collect, track-record update and score; full backtest on Saturdays | ~10 min |
| `make status` | Row counts and recent collector errors | seconds |
| `make dashboard` | Streamlit at http://localhost:8501 | — |

## Scheduling

* **macOS (recommended):** `make schedule` installs
  `~/Library/LaunchAgents/com.earnsignal.daily.plist`, which runs
  `scripts/run_daily.sh` at 05:30 local (08:30 ET, before the open). If the Mac
  is asleep at that time, launchd runs the job on wake. Logs are in `logs/`.
  Remove it with `make unschedule`.
* **Linux cron:** `30 5 * * * /path/to/earnsignal/scripts/run_daily.sh`.
* **Always-on (free):** GitHub Actions + Streamlit Community Cloud. Setup is
  in README → "Run it free in the cloud". The workflow
  (`.github/workflows/daily.yml`) restores the database from the `data-latest`
  release, runs `pipeline daily` (or `full` / `backtest` when started by hand),
  and saves it back. It keeps the previous snapshot as `earnsignal-prev.db.gz`.
  `python -m earnsignal.remote download|upload` does the same from any machine
  (set `GITHUB_REPOSITORY` and `GITHUB_TOKEN`), so the Mac and the cloud job can
  share one database. Don't run both at the same time.

Prospective signals (job postings, app ratings, option quotes) only accumulate
on days the job runs. Gaps of more than a week disable the 28-day change
features until two snapshots ~28 days apart exist again.

## Configuration

* `config/settings.yaml`
  * `contact`: or set `EARNSIGNAL_CONTACT` in `.env`; required by SEC and Wikimedia.
  * Per-host rate limits. SEC's ceiling is 10/s; the default is 5.
  * `history_start` and `insider_start_year`.
  * `upcoming_window_days`.
  * `universe_files`: remove `universe_extended.yaml` to run only the 29 core names.
  * `enable_workday`: off by default. The endpoint is undocumented, so check
    the career sites' terms before enabling it. It adds job data for EXPE,
    BKNG, ETSY, EBAY, SNAP, DKNG and CHWY.
  * `stooq_api_key`: optional price fallback.
* `config/universe.yaml`: add companies with ticker, segment, job-board
  provider and token, Wikipedia titles and iOS app id. Run `make init` after
  editing.
* `config/model.yaml`: signal definitions and **pre-registered** signs. To
  test a new idea, add a feature with its sign *before* running the backtest,
  and log the hypothesis.

## How rankings should be read

* **Opportunity score (0–100):** the percentile of the composite among past
  announcements. A score of 90 means stronger aggregate signals than 90% of
  history. It is not a probability.
* **P(beat):** shown only when the backtest's logistic calibration beats the
  base rate out of sample (Brier improvement 90% CI > 0, ≥100 test events).
  Otherwise the reason it is withheld is shown.
* **Confidence:** signal coverage × agreement, halved while weights are
  unvalidated.
* **Track record:** every ranking is stored. After the company reports, the
  last pre-announcement ranking is graded against the reaction, SUE and
  consensus outcome (Backtest page → Prospective track record). This
  forward-only record is the honest test, because it cannot be curve-fit.

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| `SEC EDGAR and Wikimedia require a User-Agent` | Set `EARNSIGNAL_CONTACT` in `.env` |
| `YFRateLimitError` or many Yahoo errors | Yahoo throttles; rerun later or `collect --only prices --tickers ...` in smaller batches |
| A job board returns 0 postings | The company changed providers or tokens. Check its careers page network tab and update `config/universe.yaml` |
| `no CIK in SEC ticker map` (research log) | Ticker changed or delisted; update or remove it from the universe |
| Wikipedia `article not found` | The title was renamed; fix it in `universe.yaml` (core) or let Wikidata re-resolve (extended) |
| Insider dataset 404 | The quarter is not published yet; it is retried on the next full collect |
| Backtest says `insufficient data` | Collect SEC + prices first; it needs ≥40 events with prices |
| Actions run fails at "Check the SEC contact secret" | Add the `EARNSIGNAL_CONTACT` repository secret |
| Prices or consensus fail only on GitHub Actions | Yahoo throttles cloud IPs; add `TIINGO_API_KEY` (prices), or run `make daily` on the Mac and `python -m earnsignal.remote upload` |
| Dashboard: "Could not fetch the data snapshot" | Check the Streamlit secrets: `GITHUB_REPO` spelling, token not expired, token has Contents: Read on the repo |

## Next steps (ranked by expected value)

1. **Accumulate prospective data.** Keep the daily job running. Job-postings
   and app signals become testable after ~2 earnings seasons.
2. **Real historical option quotes.** Evaluate DoltHub
   `post-no-preference/options` (free, community, since ~2021) and Alpha
   Vantage `HISTORICAL_OPTIONS` (25 req/day). Either would turn the options
   scenario into a real backtest.
3. **Second industry: semis/hardware** with Taiwan MOPS monthly revenue
   mapped to US customers. It is novel and free, but no published evidence
   supports it yet.
4. **Google Trends** if alpha API access is granted (product-level search has
   the best published revenue-surprise evidence among free signals).
5. **Broaden to smaller caps.** The literature finds free-data edges are
   largest in low-coverage names.
