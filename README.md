# earnsignal

An alternative-data research platform. It ranks upcoming earnings announcements
by operational signals that are observable before the release, records every
ranking, and grades each one after the company reports.

Universe: 29 hand-verified US consumer-internet and app companies, plus 41
extended names for backtest power. Budget: $0. All data is free and public.

> Research tool only. It never places trades, and nothing it shows is
> investment advice. Read `RESEARCH.md` before trusting any number: most
> published alt-data effects are small, have decayed, and are hard to detect at
> this sample size.

## Quick start (macOS / Linux, Python 3.10+)

```bash
make setup                 # creates .venv, installs requirements, creates .env
# edit .env:  EARNSIGNAL_CONTACT="Your Name you@example.com"   (SEC + Wikimedia require it)
make init                  # database + universe + research-log seed
make collect               # first run: ~20–40 min (SEC history, insider zips, Wikipedia since 2015)
make backtest              # point-in-time panel, out-of-sample tests, options scenarios
make score                 # rank announcements in the next 45 days
make dashboard             # http://localhost:8501
make schedule              # daily 05:30 job via launchd (collect + grade + score; backtest on Saturdays)
make test                  # tests on synthetic fixtures (no network)
```

## Run it free in the cloud (GitHub Actions + Streamlit Community Cloud)

A scheduled GitHub Actions job collects data, grades past rankings and re-ranks Mon–Sat before the
US open. It stores the SQLite database as an asset on a GitHub Release (`data-latest`), because the
file is too big for git. A Streamlit Community Cloud app reads the latest snapshot. Cost: $0 —
a private repo gets 2,000 free Actions minutes a month, and a daily run uses roughly 10–20.

1. **Repo.** Create an empty **private** repo (e.g. `earnsignal`) and push this folder to it.
2. **Secret.** In the repo, go to Settings → Secrets and variables → Actions → *New repository secret*.
   Add `EARNSIGNAL_CONTACT` = `Your Name you@example.com`. SEC and Wikimedia require a contact.
   Optionally add `TIINGO_API_KEY`, a free tiingo.com key used as a price fallback if Yahoo blocks GitHub's servers.
3. **First load.** Go to Actions → *earnsignal daily* → *Run workflow* → mode **full**. It takes about 30–90 min.
   After that it runs by itself (cron in `.github/workflows/daily.yml`).
4. **Dashboard.** Go to share.streamlit.io → sign in with GitHub → *Create app*:
   - Repo: your repo. Branch: `main`. Main file: `dashboard/app.py`.
   - Advanced settings: Python 3.12. Secrets:
     ```toml
     GITHUB_REPO = "your-user/earnsignal"
     GITHUB_TOKEN = "github_pat_..."   # fine-grained token: only this repo, Contents = Read-only
     ```
   Apps from private repos are private by default; invite viewers with *Share*. The free tier allows one private app.
   Idle apps go to sleep and take a few seconds to wake.

Failures email you by default (GitHub notifications), and each run's summary is on its Actions page.
To roll back a bad snapshot, open the `data-latest` release and rename `earnsignal-prev.db.gz` to `earnsignal.db.gz`.

## What it does

| Layer | Where | Notes |
|---|---|---|
| Collectors | `earnsignal/collectors/` | **SEC EDGAR** (8-K Item 2.02 release times, XBRL revenue/EPS, Form 4 insiders); **Yahoo via yfinance** (prices, EPS consensus, upcoming dates, option chains); **Wikimedia** pageviews; **job boards** (Greenhouse/Lever/Ashby/SmartRecruiters/Eightfold, Workday opt-in); **App Store** ratings and ranks; **Cboe** delayed option quotes (fallback) |
| Storage | `data/earnsignal.db` (SQLite) | Every observation keeps its source URL and fetch time. All XBRL versions are kept, and history uses first-filed values |
| Features | `earnsignal/features/panel.py` | 15 signals, computed **as of** each event's entry close. Non-price data must be dated before the cutoff, which a scramble-the-future test enforces |
| Model | `earnsignal/model/scoring.py`, `config/model.yaml` | Pre-registered signs taken from the literature. Robust z vs each signal's own past, equal-weight composite, and a percentile opportunity score. A probability is shown only after out-of-sample calibration passes |
| Backtest | `earnsignal/backtest/engine.py` | Chronological 60/40 split; rank IC vs announcement return, SUE, revenue surprise and consensus beat; precision@top-20% vs base rate with permutation test; long/short by quarter net of 0–50 bps; Benjamini–Hochberg; benchmarks (random, momentum, prior SUE) |
| Options | `earnsignal/backtest/options_scenario.py` | **Hypothetical** (modelled prices on real moves): ATM/OTM calls, IV crush, spreads; $10k account with fixed $2,500 vs 5%/2% sizing; Monte Carlo ruin. Real chains are snapshotted going forward |
| Dashboard | `dashboard/app.py` | Rankings · Company detail (evidence and source links) · Backtest (including live track record) · Research log · Data health |

## Project layout

```
config/        settings.yaml, universe.yaml (core), universe_extended.yaml, model.yaml (signals + hypotheses), research_seed.yaml
earnsignal/    collectors/, features/, model/, backtest/, pipeline.py (CLI), db.py, http.py
dashboard/     Streamlit app
docs/          literature review, verified API spec, universe verification record
scripts/       run_daily.sh, install_schedule.sh
tests/         parsers, look-ahead leakage, end-to-end + dashboard render (synthetic data only)
RESEARCH.md    industry choice, evidence, data-source evaluation, biases, verified vs unverified
RUNBOOK.md     operations, scheduling, troubleshooting, next steps
```
