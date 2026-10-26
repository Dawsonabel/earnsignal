"""Command-line entry point.

    python -m earnsignal.pipeline init
    python -m earnsignal.pipeline collect [--only sec,prices,...] [--tickers DUOL,UBER]
    python -m earnsignal.pipeline backtest
    python -m earnsignal.pipeline score
    python -m earnsignal.pipeline daily          # collect + score (+ weekly backtest)
    python -m earnsignal.pipeline status
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
import time
import uuid
from datetime import date, datetime, timedelta, timezone

import numpy as np
import pandas as pd
import yaml

from . import config, db
from .collectors import appstore, insider, jobs, market, options, sec, wikipedia
from .features.panel import FEATURES, Data, Event, FeatureBuilder, upcoming_events
from .http import Client
from .model import scoring

SOURCES = ["sec", "prices", "earnings_dates", "wikipedia", "jobs", "appstore", "insider", "options"]


def _say(msg: str) -> None:
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)


# ------------------------------------------------------------------- init
def cmd_init(con) -> None:
    db.init(con)
    sync_universe(con)
    seed = config.CONFIG_DIR / "research_seed.yaml"
    already = con.execute("SELECT COUNT(*) FROM research_log WHERE ref='seed'").fetchone()[0]
    if seed.exists() and not already:
        for e in yaml.safe_load(seed.read_text()) or []:
            db.log(con, e["category"], e["title"], e.get("body", ""), "seed")
    _say(f"Database ready at {config.db_path()} with {len(config.universe())} companies.")


def sync_universe(con) -> None:
    rows = []
    existing = {r["ticker"]: r for r in con.execute("SELECT ticker, cik, fiscal_year_end, wikipedia FROM companies")}
    for c in config.universe():
        old = existing.get(c.ticker)
        wiki = c.wikipedia
        if not wiki and old and old["wikipedia"]:  # keep titles resolved from Wikidata
            wiki = json.loads(old["wikipedia"])
        rows.append({
            "ticker": c.ticker, "name": c.name, "cik": old["cik"] if old else None, "segment": c.segment,
            "fiscal_year_end": old["fiscal_year_end"] if old and old["fiscal_year_end"] else c.fiscal_year_end,
            "ats_provider": c.ats_provider, "ats_token": c.ats_token,
            "wikipedia": json.dumps(wiki), "ios_app_id": c.ios_app_id, "notes": c.notes, "tier": c.tier,
            "updated_at": db.now_utc(),
        })
    db.upsert(con, "companies", rows)
    keep = {c.ticker for c in config.universe()}
    for t in set(existing) - keep:
        con.execute("DELETE FROM companies WHERE ticker=?", (t,))
    con.commit()


# ---------------------------------------------------------------- collect
def cmd_collect(con, only: list[str] | None = None, tickers: list[str] | None = None, daily: bool = False) -> None:
    s = config.settings()
    only = only or SOURCES
    comps = [c for c in config.universe() if not tickers or c.ticker in tickers]
    needs_contact = {"sec", "wikipedia", "insider"} & set(only)
    client = Client(contact=config.require_contact() if needs_contact else s.get("contact", ""))
    sync_universe(con)

    if "sec" in only or "insider" in only:
        try:
            tmap = sec.load_ticker_map(client)
        except Exception as e:
            _say(f"Could not load the SEC ticker map ({e}); skipping SEC and insider collection.")
            db.log(con, "ops", "SEC ticker map unavailable", str(e)[:500], "collect")
            only = [x for x in only if x not in ("sec", "insider")]
            tmap = {}
        for c in comps:
            info = tmap.get(c.ticker)
            if info:
                con.execute("UPDATE companies SET cik=? WHERE ticker=?", (info["cik"], c.ticker))
            else:
                db.log(con, "data", f"{c.ticker}: no CIK in SEC ticker map", "Ticker may have changed or been delisted.", "collect")
        con.commit()
    cik = {r["ticker"]: r["cik"] for r in con.execute("SELECT ticker, cik FROM companies")}

    if "sec" in only:
        _say("SEC: filings index, XBRL facts, earnings release dates")
        for c in comps:
            if not cik.get(c.ticker):
                continue
            with db.CollectorRun(con, "sec_submissions", c.ticker) as run:
                run.n_rows = sec.collect_submissions(con, client, c.ticker, int(cik[c.ticker]), s["history_start"])
            with db.CollectorRun(con, "sec_companyfacts", c.ticker) as run:
                run.n_rows = sec.collect_companyfacts(con, client, c.ticker, int(cik[c.ticker]))
            with db.CollectorRun(con, "earnings_events", c.ticker) as run:
                run.n_rows = sec.build_earnings_events(con, c.ticker, s["sec_acceptance_tz"])
            con.commit()

    if "prices" in only:
        _say("Prices (Yahoo via yfinance; Stooq fallback)")
        for t in [c.ticker for c in comps] + config.benchmarks():
            with db.CollectorRun(con, "prices", t) as run:
                try:
                    run.n_rows = market.collect_prices(con, t, s["history_start"])
                except Exception as e:
                    tiingo = os.environ.get("TIINGO_API_KEY") or s.get("tiingo_api_key")
                    if tiingo:
                        run.message = f"yahoo failed ({str(e)[:120]}); used tiingo"
                        run.n_rows = market.collect_prices_tiingo(con, client, t, s["history_start"], tiingo)
                    elif s.get("stooq_api_key"):
                        run.message = f"yahoo failed ({str(e)[:120]}); used stooq"
                        run.n_rows = market.collect_prices_stooq(con, client, t, s["stooq_api_key"])
                    else:
                        raise
            con.commit()
            time.sleep(0.5)  # Yahoo rate-limits aggressively

    if "earnings_dates" in only:
        _say("Earnings dates + consensus (Yahoo)")
        for c in comps:
            with db.CollectorRun(con, "earnings_dates", c.ticker) as run:
                n_c, n_u = market.collect_earnings_dates(con, c.ticker)
                run.n_rows, run.message = n_c + n_u, f"{n_c} consensus rows, {n_u} upcoming"
            con.commit()
            time.sleep(1.0)

    if "wikipedia" in only:
        _say("Wikipedia pageviews")
        titles = {r["ticker"]: json.loads(r["wikipedia"] or "[]") for r in con.execute("SELECT ticker, wikipedia FROM companies")}
        for c in comps:
            if not titles.get(c.ticker):
                tried = con.execute("SELECT 1 FROM collector_runs WHERE collector='wikidata_resolve' AND ticker=? AND status='ok'", (c.ticker,)).fetchone()
                if tried:
                    continue
                with db.CollectorRun(con, "wikidata_resolve", c.ticker) as run:
                    title = wikipedia.resolve_title(client, c.ticker)
                    run.message = title or "no unique Wikidata match"
                    if title:
                        titles[c.ticker] = [title]
                        con.execute("UPDATE companies SET wikipedia=? WHERE ticker=?", (json.dumps([title]), c.ticker))
                        run.n_rows = 1
                con.commit()
            for art in titles.get(c.ticker, []):
                with db.CollectorRun(con, "wikipedia", c.ticker) as run:
                    run.n_rows = wikipedia.collect(con, client, c.ticker, art, s["history_start"])
                    run.message = art
            con.commit()

    if "jobs" in only:
        _say("Job postings (public ATS APIs)")
        for c in comps:
            if not c.ats_provider or not c.ats_token or c.ats_provider == "custom":
                continue
            if c.ats_provider == "workday" and not s.get("enable_workday"):
                continue
            with db.CollectorRun(con, "jobs", c.ticker) as run:
                run.n_rows = jobs.collect(con, client, c.ticker, c.ats_provider, c.ats_token)
                run.message = f"{c.ats_provider}:{c.ats_token}"
            con.commit()

    if "appstore" in only:
        _say("App Store ratings + chart ranks")
        ids = {}
        for c in comps:
            if c.ios_app_id:
                ids[c.ios_app_id] = c.ticker
                with db.CollectorRun(con, "appstore", c.ticker) as run:
                    run.n_rows = appstore.collect_lookup(con, client, c.ticker, c.ios_app_id)
        with db.CollectorRun(con, "appstore_charts", None) as run:
            run.n_rows = appstore.collect_charts(con, client, ids)
        con.commit()

    if "insider" in only:
        _say("Insider transactions (SEC Form 4)")
        cik_to_t = {int(v): k for k, v in cik.items() if v}
        # only quarters not yet loaded are downloaded, so this is cheap after the first run
        insider.collect_datasets(con, client, cik_to_t, int(s["insider_start_year"]))
        for c in comps:
            with db.CollectorRun(con, "insider_form4_recent", c.ticker) as run:
                run.n_rows = insider.collect_recent_form4(con, client, c.ticker)
            con.commit()

    if "options" in only:
        _say("Option chains for announcements in the next 14 days")
        data = Data(con)
        today = date.today()
        for ev in upcoming_events(data, today, 14):
            if tickers and ev.ticker not in tickers:
                continue
            a = date.fromisoformat(ev.announce_date)
            reaction = a if ev.timing != "AMC" else a + timedelta(days=1)
            with db.CollectorRun(con, "options", ev.ticker) as run:
                run.n_rows = options.collect(con, ev.ticker, ev.announce_date, reaction.isoformat(), client=client)
            con.commit()
    _say("Collection finished.")


# ------------------------------------------------------------------ score
def latest_backtest(con) -> tuple[str | None, pd.DataFrame, dict]:
    r = con.execute("SELECT run_id, summary FROM backtest_runs ORDER BY created_at DESC LIMIT 1").fetchone()
    if not r:
        return None, pd.DataFrame(), {}
    ev = db.df(con, "SELECT data FROM backtest_events WHERE run_id=?", (r["run_id"],))
    panel = pd.DataFrame([json.loads(x) for x in ev["data"]]) if not ev.empty else pd.DataFrame()
    return r["run_id"], panel, json.loads(r["summary"])


def _applicable(c: config.Company, wiki_titles: list | None = None) -> list[str]:
    cfg = scoring.model_config()["features"]
    fl = [f for f in FEATURES if cfg[f]["history"] == "backtest"]
    if not (c.wikipedia or wiki_titles):
        fl = [f for f in fl if not f.startswith("wiki")]
    if c.ats_provider and c.ats_provider != "custom":
        fl += ["jobs_growth_28d", "jobs_gtm_share_chg"]
    if c.ios_app_id:
        fl += ["app_rating_growth_28d"]
    return fl


def _describe(f: str, raw: float, zv: float, sign: int) -> str:
    cfg = scoring.model_config()["features"][f]
    direction = "supports a beat" if sign * zv > 0 else "points to a miss"
    return f"{f} = {raw:.4g} (z {zv:+.2f}) {direction}. {cfg['description']}"


def cmd_score(con, today: date | None = None) -> pd.DataFrame:
    today = today or date.today()
    s = config.settings()
    cfg = scoring.model_config()
    sg = scoring.signs()
    run_bt, ref, summary = latest_backtest(con)
    if ref.empty:
        _say("No backtest found; building the historical reference panel first.")
        from .backtest import engine
        engine.run(con)
        run_bt, ref, summary = latest_backtest(con)
    if ref.empty or "cutoff" not in ref.columns:
        _say("No historical events to compare against yet; run `collect` (SEC + prices) and `backtest` first.")
        return pd.DataFrame()
    data = Data(con)
    fb = FeatureBuilder(data)
    ups = upcoming_events(data, today, int(s["upcoming_window_days"]))
    if not ups:
        _say("No upcoming earnings dates in the window. Run `collect --only earnings_dates`.")
        return pd.DataFrame()
    comps = {c.ticker: c for c in config.universe()}
    rows, dets = [], {}
    for ev in ups:
        feats, det, meta = fb.compute(ev, live_today=today)
        rows.append({"ticker": ev.ticker, "announce_date": ev.announce_date, "timing": ev.timing,
                     "announce_source": ev.source, "cutoff": meta.get("info_cutoff"), "period_end": meta.get("period_end"),
                     **{f: feats.get(f, np.nan) for f in FEATURES}})
        dets[ev.ticker] = (det, meta)
    live = pd.DataFrame(rows)
    for f in FEATURES:
        if f not in ref.columns:
            ref[f] = np.nan
    z = scoring.standardize(live, FEATURES, ref=ref)
    live = pd.concat([live, z], axis=1)

    # prospective features have no history yet: standardize them cross-sectionally
    # among today's upcoming events and flag them as unvalidated
    prospective = [f for f in FEATURES if cfg["features"][f]["history"] == "prospective"]
    for f in prospective:
        col = live[f].astype(float)
        if col.notna().sum() >= 5:
            med = col.median()
            mad = (col - med).abs().median() * 1.4826 or col.std()
            live[f"z_{f}"] = ((col - med) / mad).clip(-3, 3) if mad else np.nan

    weights = None
    weights_basis = "equal weights (pre-registered signs; not yet validated)"
    comp_all = scoring.composite(live, FEATURES, weights)
    live = pd.concat([live, comp_all], axis=1)
    ref_comp = ref["composite"] if "composite" in ref.columns else pd.Series(dtype=float)
    live["opportunity_score"] = scoring.percentile_score(live["composite"], live["cutoff"], ref_comp.astype(float), ref["cutoff"])

    reg = con.execute("SELECT params, approved FROM model_registry WHERE name='beat_probability'").fetchone()
    cal = json.loads(reg["params"]) if reg else {}
    approved = bool(reg and reg["approved"])

    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:6]
    out = []
    for _, r in live.iterrows():
        t = r["ticker"]
        det, meta = dets[t]
        appl = _applicable(comps[t], json.loads(data.companies.loc[t, "wikipedia"] or "[]") if t in data.companies.index else None) if t in comps else FEATURES
        contrib = []
        for f in FEATURES:
            zv = r.get(f"z_{f}")
            if zv is None or (isinstance(zv, float) and math.isnan(zv)):
                continue
            contrib.append({"feature": f, "raw": float(r[f]), "z": float(zv), "signed": float(sg[f] * zv),
                            "kind": cfg["features"][f]["kind"], "history": cfg["features"][f]["history"],
                            "text": _describe(f, float(r[f]), float(zv), sg[f]), "evidence": det.get(f, {})})
        contrib.sort(key=lambda x: -abs(x["signed"]))
        missing = [f for f in appl if f not in {c["feature"] for c in contrib}]
        conf, conf_label = scoring.confidence(int(r["n_signals"]), len(appl), int(r["n_pos"]), int(r["n_neg"]),
                                              r["composite"], validated=False)
        prob, basis = None, "not supportable: " + (cal.get("reason") or "no approved calibration")
        if approved and not math.isnan(r["composite"]):
            prob = 1 / (1 + math.exp(-(cal["intercept"] + cal["coef"] * r["composite"])))
            basis = f"logistic calibration approved in backtest (OOS Brier improvement {cal['brier_improvement']:.4f})"
        expl = {
            "bullish": [c for c in contrib if c["signed"] > 0.25][:6],
            "bearish": [c for c in contrib if c["signed"] < -0.25][:6],
            "neutral": [c for c in contrib if abs(c["signed"]) <= 0.25],
            "missing": missing, "weights": weights_basis, "meta": meta, "backtest_run": run_bt,
            "freshness": _freshness(con, t),
        }
        out.append({
            "run_id": run_id, "ticker": t, "as_of_date": today.isoformat(), "announce_date": r["announce_date"],
            "announce_source": r["announce_source"], "timing": r["timing"],
            "opportunity_score": None if math.isnan(r["opportunity_score"]) else round(float(r["opportunity_score"]), 1),
            "direction": scoring.label(r["opportunity_score"], cfg.get("bands", {})),
            "composite": None if math.isnan(r["composite"]) else float(r["composite"]),
            "n_signals": int(r["n_signals"]), "coverage": round(int(r["n_signals"]) / max(len(appl), 1), 3),
            "confidence": conf, "prob_beat": prob, "prob_basis": basis,
            "explanation": json.dumps(expl, default=str), "computed_at": db.now_utc(),
        })
    db.upsert(con, "scores", out)
    con.commit()
    res = pd.DataFrame(out).sort_values("opportunity_score", ascending=False, na_position="last")
    _say(f"Scored {len(res)} upcoming announcements (run {run_id}).")
    print(res[["ticker", "announce_date", "timing", "opportunity_score", "direction", "n_signals", "confidence"]].to_string(index=False))
    return res


def update_track_record(con) -> int:
    """For every scored event that has now been reported, store the last score
    issued BEFORE the announcement next to the realized outcomes."""
    sc = db.df(con, "SELECT * FROM scores")
    if sc.empty:
        return 0
    data = Data(con)
    fb = FeatureBuilder(data)
    rep = data.events[data.events["source"] == "sec_8k_202"]
    rows = []
    for (t, ad), g in sc.groupby(["ticker", "announce_date"]):
        g = g[g["as_of_date"] < ad].sort_values("computed_at")
        if g.empty:
            continue
        r = rep[(rep["ticker"] == t) & rep["announce_date"].map(lambda x: abs((date.fromisoformat(x) - date.fromisoformat(ad)).days) <= 5)]
        if r.empty:
            continue
        e = r.iloc[0]
        out = fb.outcomes(Event(t, e["announce_date"], e["timing"], e["period_end"], e["source"], e["url"]))
        if out.get("car_ann") is None:
            continue
        last = g.iloc[-1]
        rows.append({"ticker": t, "announce_date": e["announce_date"], "scored_on": last["as_of_date"],
                     "score_run_id": last["run_id"], "opportunity_score": last["opportunity_score"],
                     "composite": last["composite"], "direction": last["direction"], "n_signals": last["n_signals"],
                     "car_ann": out.get("car_ann"), "ret_ann": out.get("ret_ann"), "sue": out.get("sue"),
                     "beat_consensus": out.get("beat_consensus"), "surprise_pct": out.get("surprise_pct"),
                     "evaluated_at": db.now_utc()})
    n = db.upsert(con, "track_record", rows)
    con.commit()
    return n


def _freshness(con, t: str) -> dict:
    out = {}
    for src in ("wikipedia", "jobs", "appstore"):
        r = con.execute("SELECT MAX(obs_date) FROM observations WHERE ticker=? AND source=?", (t, src)).fetchone()[0]
        out[src] = r
    out["prices"] = con.execute("SELECT MAX(date) FROM prices WHERE ticker=?", (t,)).fetchone()[0]
    out["sec_filings"] = con.execute("SELECT MAX(filing_date) FROM filings WHERE ticker=?", (t,)).fetchone()[0]
    out["insider"] = con.execute("SELECT MAX(filing_date) FROM insider_transactions WHERE ticker=?", (t,)).fetchone()[0]
    return out


# --------------------------------------------------------------- backtest
def cmd_backtest(con) -> dict:
    from .backtest import engine, options_scenario

    _say("Building point-in-time event panel and running the backtest...")
    summary = engine.run(con)
    if summary.get("status") != "ok":
        _say(summary.get("message", "Backtest did not run."))
        return summary
    _, panel, _ = latest_backtest(con)
    _say("Running hypothetical options scenarios (modelled prices)...")
    opt = options_scenario.run(con, panel)
    summary["options_scenario"] = opt
    con.execute("UPDATE backtest_runs SET summary=? WHERE run_id=?", (json.dumps(summary, default=engine._jsonable), summary["run_id"]))
    con.commit()
    _print_summary(summary)
    return summary


def _print_summary(s: dict) -> None:
    print(f"\nBacktest {s['run_id']}: {s['n_events']} events, train {s['n_train']} / test {s['n_test']} (split {s['split_date']})")
    print("\nOut-of-sample rank IC vs market-adjusted announcement return:")
    for r in s["signals"]:
        if r["history"] == "backtest":
            print(f"  {r['feature']:<26} IC {r['test_ic_car_ann']:+.3f}  p {r['test_p_car_ann']:.3f}  n {r['test_n_car_ann']}")
    print("\nModels on the test set:")
    for k, e in s["test_eval"].items():
        pt = e["precision_top20_beat"]
        prec = f"{pt['precision']:.2f} vs base {pt['base_rate']:.2f}" if "precision" in pt else "n/a"
        print(f"  {k:<36} IC(car) {e['ic_car_ann']:+.3f}  IC(sue) {e['ic_sue']:+.3f}  top20% beat precision {prec}")
    print(f"\nBeat-probability calibration approved: {s['calibration'].get('approved')} ({s['calibration'].get('reason', '')})")


# ----------------------------------------------------------------- status
def cmd_status(con) -> None:
    print(f"DB: {config.db_path()}")
    for tbl in ("companies", "filings", "fundamentals", "earnings_events", "consensus", "prices", "observations",
                "job_postings", "insider_transactions", "option_quotes", "implied_moves", "scores", "backtest_runs", "research_log"):
        n = con.execute(f"SELECT COUNT(*) FROM {tbl}").fetchone()[0]
        print(f"  {tbl:<22} {n:>10,}")
    bad = db.df(con, "SELECT collector, ticker, finished_at, message FROM collector_runs WHERE status='error' ORDER BY id DESC LIMIT 15")
    if not bad.empty:
        print("\nRecent collector errors:")
        print(bad.to_string(index=False))


def cmd_daily(con) -> None:
    cmd_collect(con, only=[s for s in SOURCES], daily=True)
    if date.today().weekday() == 5 or not con.execute("SELECT 1 FROM backtest_runs WHERE summary LIKE '%\"status\": \"ok\"%' LIMIT 1").fetchone():
        cmd_backtest(con)
    n = update_track_record(con)
    _say(f"Track record: {n} scored announcements evaluated.")
    cmd_score(con)


def main(argv=None) -> None:
    p = argparse.ArgumentParser(prog="earnsignal")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("init")
    c = sub.add_parser("collect")
    c.add_argument("--only", help=f"comma list of {','.join(SOURCES)}")
    c.add_argument("--tickers")
    sub.add_parser("backtest")
    sub.add_parser("score")
    sub.add_parser("daily")
    sub.add_parser("status")
    a = p.parse_args(argv)
    with db.session() as con:
        if a.cmd == "init":
            cmd_init(con)
        elif a.cmd == "collect":
            cmd_collect(con, only=a.only.split(",") if a.only else None,
                        tickers=[t.strip().upper() for t in a.tickers.split(",")] if a.tickers else None)
        elif a.cmd == "backtest":
            cmd_backtest(con)
        elif a.cmd == "score":
            update_track_record(con)
            cmd_score(con)
        elif a.cmd == "daily":
            cmd_daily(con)
        elif a.cmd == "status":
            cmd_status(con)


if __name__ == "__main__":
    main()
