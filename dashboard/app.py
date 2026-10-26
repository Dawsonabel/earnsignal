"""earnsignal dashboard. Reads only from the SQLite database; never shows invented data.

    streamlit run dashboard/app.py
"""
from __future__ import annotations

import json
import math
import os
import sys
import tempfile
from pathlib import Path

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from earnsignal import config, db  # noqa: E402
from earnsignal.model import scoring  # noqa: E402

st.set_page_config(page_title="earnsignal", layout="wide")


# ---------------------------------------------------- hosted (remote) mode
# On Streamlit Community Cloud the database is pulled from the GitHub release
# that the daily Actions job writes. Configure in the app's Secrets:
#   GITHUB_REPO = "owner/repo"
#   GITHUB_TOKEN = "<fine-grained token with read-only Contents access to that repo>"
def _secret(name: str):
    try:
        if name in st.secrets:
            return st.secrets[name]
    except Exception:  # no secrets file locally
        pass
    return os.environ.get(f"EARNSIGNAL_{name}")


@st.cache_resource(ttl=900, show_spinner="Fetching the latest data snapshot...")
def _remote_snapshot(repo: str, token: str) -> tuple[str | None, str | None]:
    from earnsignal import remote

    info = remote.asset_info(repo, token)
    if info is None:
        return None, None
    stamp = info["updated_at"].replace(":", "").replace("-", "")
    dest = Path(tempfile.gettempdir()) / f"earnsignal_{stamp}.db"
    if not dest.exists():
        remote.download(dest=dest, repo=repo, token=token)
    return str(dest), info["updated_at"]


SNAPSHOT_TIME = None
_repo, _token = _secret("GITHUB_REPO"), _secret("GITHUB_TOKEN")
if _repo and _token:
    try:
        _path, SNAPSHOT_TIME = _remote_snapshot(_repo, _token)
        if _path:
            os.environ["EARNSIGNAL_DB"] = _path
        else:
            st.warning("No data snapshot has been published yet. Run the GitHub workflow once (mode: full).")
    except Exception as e:  # show the problem instead of a blank app
        st.error(f"Could not fetch the data snapshot from {_repo}: {e}")


@st.cache_resource
def _con(path: str):
    con = db.connect(path, check_same_thread=False)
    db.init(con)
    return con


def q(sql: str, params=()) -> pd.DataFrame:
    return db.df(_con(str(config.db_path())), sql, params)


def jl(s):
    try:
        return json.loads(s) if isinstance(s, str) else (s or {})
    except json.JSONDecodeError:
        return {}


def fmt_pct(x, d=1):
    return "—" if x is None or (isinstance(x, float) and math.isnan(x)) else f"{x * 100:.{d}f}%"


def empty_state(msg: str):
    st.info(msg)
    st.code("python -m earnsignal.pipeline init\npython -m earnsignal.pipeline collect\n"
            "python -m earnsignal.pipeline backtest\npython -m earnsignal.pipeline score", language="bash")


def latest_backtest():
    r = q("SELECT run_id, created_at, summary FROM backtest_runs ORDER BY created_at DESC LIMIT 1")
    if r.empty:
        return None, {}
    return r.iloc[0]["run_id"], jl(r.iloc[0]["summary"])


# ------------------------------------------------------------- rankings
def page_rankings():
    st.title("Earnings opportunity rankings")
    run = q("SELECT run_id, MAX(computed_at) AS t FROM scores GROUP BY run_id ORDER BY t DESC LIMIT 1")
    if run.empty:
        empty_state("No rankings yet. Collect data, run a backtest, then score upcoming announcements.")
        return
    run_id, ts = run.iloc[0]["run_id"], run.iloc[0]["t"]
    sc = q("SELECT s.*, c.name, c.segment FROM scores s LEFT JOIN companies c USING(ticker) WHERE run_id=?", (run_id,))
    _, bt = latest_backtest()
    cal = bt.get("calibration", {})
    st.caption(f"Run {run_id} · computed {ts} UTC · {len(sc)} upcoming announcements")
    st.warning(
        "**How to read this.** The opportunity score is a percentile rank of a composite of pre-registered signals "
        "against past announcements — not a probability. Weights are equal and **not yet validated**. "
        + ("A beat probability is shown because its calibration passed out-of-sample validation."
           if cal.get("approved") else f"No beat probability is shown: {cal.get('reason', 'calibration has not passed validation')}.")
    )
    c1, c2, c3 = st.columns(3)
    seg = c1.multiselect("Segment", sorted(sc["segment"].dropna().unique()))
    dirs = c2.multiselect("Direction", ["bullish", "neutral", "bearish", "insufficient data"])
    min_cov = c3.slider("Minimum signal coverage", 0.0, 1.0, 0.0, 0.05)
    if seg:
        sc = sc[sc["segment"].isin(seg)]
    if dirs:
        sc = sc[sc["direction"].isin(dirs)]
    sc = sc[sc["coverage"].fillna(0) >= min_cov]

    rows = []
    for _, r in sc.iterrows():
        ex = jl(r["explanation"])
        top = (ex.get("bullish", []) + ex.get("bearish", []))
        top = sorted(top, key=lambda c: -abs(c["signed"]))[:3]
        fr = ex.get("freshness", {})
        alt_dates = [v for k, v in fr.items() if k in ("wikipedia", "jobs", "appstore") and v]
        rows.append({
            "Ticker": r["ticker"], "Company": r["name"], "Segment": r["segment"],
            "Earnings date": r["announce_date"], "Time": r["timing"],
            "Score": r["opportunity_score"], "Direction": r["direction"],
            "Top signals": "; ".join(f"{c['feature']} {'▲' if c['signed'] > 0 else '▼'}{abs(c['signed']):.1f}" for c in top) or "—",
            "Signals": f"{int(r['n_signals'])} ({r['coverage'] * 100:.0f}%)",
            "Confidence": r["confidence"],
            "P(beat)": "—" if pd.isna(r["prob_beat"]) else f"{r['prob_beat'] * 100:.0f}%",
            "Latest alt data": max(alt_dates) if alt_dates else "—",
            "Prices through": fr.get("prices") or "—",
        })
    df = pd.DataFrame(rows).sort_values("Score", ascending=False, na_position="last")
    st.dataframe(
        df, hide_index=True, use_container_width=True,
        column_config={"Score": st.column_config.ProgressColumn("Score", min_value=0, max_value=100, format="%.0f"),
                       "Confidence": st.column_config.NumberColumn(format="%.0f")},
    )
    st.caption("▲/▼ = signed z-score contribution (positive supports a better-than-expected announcement). "
               "Earnings dates marked by the schedule source (Yahoo) can move; confirm before relying on them.")


# ---------------------------------------------------------- company page
def page_company():
    st.title("Company detail")
    comps = q("SELECT * FROM companies ORDER BY ticker")
    if comps.empty:
        empty_state("No companies in the database yet.")
        return
    t = st.selectbox("Company", comps["ticker"], format_func=lambda x: f"{x} — {comps.set_index('ticker').loc[x, 'name']}")
    c = comps.set_index("ticker").loc[t]
    st.caption(f"Segment: {c['segment']} · CIK: {c['cik'] or '—'} · Fiscal year ends month {c['fiscal_year_end']}")

    sc = q("SELECT * FROM scores WHERE ticker=? ORDER BY computed_at DESC LIMIT 1", (t,))
    if not sc.empty:
        r = sc.iloc[0]
        ex = jl(r["explanation"])
        a, b, cc, d = st.columns(4)
        a.metric("Next earnings", r["announce_date"], r["timing"])
        b.metric("Opportunity score", "—" if pd.isna(r["opportunity_score"]) else f"{r['opportunity_score']:.0f}", r["direction"])
        cc.metric("Signals available", f"{int(r['n_signals'])}", f"coverage {r['coverage'] * 100:.0f}%")
        d.metric("P(beat consensus)", "—" if pd.isna(r["prob_beat"]) else f"{r['prob_beat'] * 100:.0f}%")
        st.caption(r["prob_basis"])
        st.subheader("Why it ranks where it does")
        left, right = st.columns(2)
        for col, key, title in ((left, "bullish", "Bullish signals"), (right, "bearish", "Bearish signals")):
            col.markdown(f"**{title}**")
            items = ex.get(key, [])
            if not items:
                col.write("None.")
            for it in items:
                with col.expander(f"{it['feature']}  ({it['signed']:+.2f})"):
                    st.write(it["text"])
                    st.caption(f"{it['kind']} signal · history: {it['history']}")
                    st.json(it.get("evidence", {}), expanded=False)
        if ex.get("missing"):
            st.caption("Not available for this event: " + ", ".join(ex["missing"]))
        st.caption(f"Model: {ex.get('weights')}. Information cutoff {ex.get('meta', {}).get('info_cutoff')}; "
                   f"prices as of {ex.get('meta', {}).get('price_as_of')}.")
    else:
        st.info("No current score for this company (no upcoming earnings date in the window, or scoring has not run).")

    st.subheader("Earnings history")
    ev = q("SELECT announce_date, timing, period_end, url FROM earnings_events WHERE ticker=? AND source='sec_8k_202' ORDER BY announce_date DESC", (t,))
    con_ = q("SELECT announce_date, eps_estimate, eps_reported, surprise_pct FROM consensus WHERE ticker=?", (t,))
    rid, _ = latest_backtest()
    be = q("SELECT announce_date, data FROM backtest_events WHERE run_id=? AND ticker=?", (rid, t)) if rid else pd.DataFrame()
    outc = {}
    for _, r in be.iterrows():
        d = jl(r["data"])
        outc[r["announce_date"]] = d
    if ev.empty:
        st.write("No SEC earnings releases (8-K Item 2.02) collected yet.")
    else:
        rows = []
        for _, e in ev.iterrows():
            o = outc.get(e["announce_date"], {})
            cr = con_[con_["announce_date"].map(lambda x: abs((pd.Timestamp(x) - pd.Timestamp(e["announce_date"])).days) <= 4)]
            rows.append({
                "Announced": e["announce_date"], "Time": e["timing"], "Quarter end": e["period_end"],
                "EPS est.": cr.iloc[0]["eps_estimate"] if len(cr) else None,
                "EPS actual": cr.iloc[0]["eps_reported"] if len(cr) else None,
                "Surprise %": cr.iloc[0]["surprise_pct"] if len(cr) else None,
                "SUE": o.get("sue"), "Revenue surprise": o.get("rev_surprise"),
                "Reaction vs SPY": fmt_pct(o.get("car_ann")), "8-K": e["url"],
            })
        st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True,
                     column_config={"8-K": st.column_config.LinkColumn("8-K", display_text="filing")})

    st.subheader("Signal history")
    tabs = st.tabs(["Fundamentals", "Wikipedia", "Job postings", "App Store", "Insiders", "Price", "Stored features"])
    with tabs[0]:
        f = q("SELECT metric, period_end, value, filed_date, duration_days, derived FROM fundamentals WHERE ticker=? AND duration_days BETWEEN 80 AND 100", (t,))
        if f.empty:
            st.write("No XBRL data yet.")
        else:
            f = f.sort_values("filed_date").drop_duplicates(["metric", "period_end"], keep="first")
            for m in ("revenue", "eps_diluted"):
                g = f[f["metric"] == m].sort_values("period_end")
                if not g.empty:
                    st.plotly_chart(px.bar(g, x="period_end", y="value", title=f"{m} (first-reported, quarterly)"), use_container_width=True)
            st.caption("Source: SEC XBRL companyfacts. First-filed values only (no restatement look-ahead). Q4 values not reported directly are derived in the model, not shown here.")
    with tabs[1]:
        w = q("SELECT series, obs_date, value FROM observations WHERE source='wikipedia' AND ticker=?", (t,))
        if w.empty:
            st.write("No Wikipedia data yet.")
        else:
            w["obs_date"] = pd.to_datetime(w["obs_date"])
            wk = w.groupby([pd.Grouper(key="obs_date", freq="W"), "series"])["value"].sum().reset_index()
            st.plotly_chart(px.line(wk, x="obs_date", y="value", color="series", title="Weekly pageviews (agent=user)"), use_container_width=True)
            st.caption("Source: Wikimedia REST pageviews API.")
    with tabs[2]:
        j = q("SELECT series, obs_date, value FROM observations WHERE source='jobs' AND ticker=?", (t,))
        if j.empty:
            st.write("No job-posting snapshots yet (this signal is collected prospectively)." if c["ats_provider"] else "No public ATS board configured for this company.")
        else:
            st.plotly_chart(px.line(j[j["series"].str.startswith(("total", "cat:"))], x="obs_date", y="value", color="series",
                                    markers=True, title="Open postings by category"), use_container_width=True)
            jp = q("SELECT title, department, category, location, first_seen, last_seen, url FROM job_postings WHERE ticker=? ORDER BY first_seen DESC LIMIT 200", (t,))
            st.dataframe(jp, hide_index=True, use_container_width=True, column_config={"url": st.column_config.LinkColumn("url", display_text="posting")})
    with tabs[3]:
        a = q("SELECT series, obs_date, value, meta FROM observations WHERE source='appstore' AND ticker=?", (t,))
        if a.empty:
            st.write("No App Store snapshots yet." if c["ios_app_id"] else "No iOS app configured.")
        else:
            for s_ in sorted(a["series"].unique()):
                g = a[a["series"] == s_]
                st.plotly_chart(px.line(g, x="obs_date", y="value", markers=True, title=s_), use_container_width=True)
    with tabs[4]:
        ins = q("SELECT filing_date, trans_date, owner_name, owner_title, trans_code, acq_disp, shares, price, value, accession FROM insider_transactions WHERE ticker=? AND trans_code IN ('P','S') ORDER BY filing_date DESC LIMIT 300", (t,))
        if ins.empty:
            st.write("No open-market insider trades collected.")
        else:
            st.dataframe(ins, hide_index=True, use_container_width=True)
            st.caption("P = open-market purchase, S = open-market sale. Source: SEC Form 4.")
    with tabs[5]:
        p = q("SELECT date, adj_close FROM prices WHERE ticker=? ORDER BY date", (t,))
        spy = q("SELECT date, adj_close FROM prices WHERE ticker='SPY' ORDER BY date")
        if p.empty:
            st.write("No prices yet.")
        else:
            m = p.merge(spy, on="date", suffixes=("", "_spy"), how="left")
            base = m.iloc[0]
            fig = go.Figure()
            fig.add_trace(go.Scatter(x=m["date"], y=m["adj_close"] / base["adj_close"], name=t))
            if m["adj_close_spy"].notna().any():
                fig.add_trace(go.Scatter(x=m["date"], y=m["adj_close_spy"] / m["adj_close_spy"].dropna().iloc[0], name="SPY"))
            for d in ev["announce_date"].tolist()[:40]:
                fig.add_vline(x=d, line_width=1, line_dash="dot", opacity=0.3)
            fig.update_layout(title="Total return (dotted lines = earnings releases)", height=420)
            st.plotly_chart(fig, use_container_width=True)
    with tabs[6]:
        fe = q("SELECT announce_date, as_of_date, feature, value FROM features WHERE ticker=? ORDER BY announce_date", (t,))
        if fe.empty:
            st.write("No stored features yet (run a backtest).")
        else:
            pv = fe.pivot_table(index="announce_date", columns="feature", values="value")
            st.dataframe(pv.sort_index(ascending=False), use_container_width=True)
            pick = st.selectbox("Plot feature", pv.columns)
            st.plotly_chart(px.line(pv[pick].dropna().reset_index(), x="announce_date", y=pick, markers=True), use_container_width=True)

    st.subheader("Related companies")
    peers = comps[(comps["segment"] == c["segment"]) & (comps["ticker"] != t)][["ticker", "name"]]
    st.write("Same segment (used for peer early-reporter signals): " + (", ".join(peers["ticker"]) or "none"))
    st.caption("Supplier/customer links are not modelled yet: consumer-internet firms rarely disclose major customers, and free supply-chain data is thin.")


# ---------------------------------------------------------- backtest page
def page_backtest():
    st.title("Backtest & validation")
    rid, s = latest_backtest()
    if not rid:
        empty_state("No backtest has been run yet.")
        return
    if s.get("status") != "ok":
        st.error(s.get("message", "Backtest did not complete."))
        return
    st.caption(f"Run {rid} · {s['n_events']} announcements {s['date_range'][0]} → {s['date_range'][1]} · "
               f"train {s['n_train']} / test {s['n_test']} (chronological split at {s['split_date']})")
    st.info("All statistics below are **out-of-sample** (test period) unless labelled train. Signs were pre-registered "
            "in config/model.yaml before data collection. Rank IC = Spearman correlation between signal and outcome.")
    n_test = s["n_test"]
    st.caption(f"Power check: with {n_test} test events, the smallest rank IC distinguishable from zero at 5% is about "
               f"{1.96 / math.sqrt(max(n_test, 1)):.3f}. Realistic alt-data ICs are 0.02–0.05.")

    st.subheader("Models vs benchmarks (test period)")
    rows = []
    for k, e in s["test_eval"].items():
        pt = e.get("precision_top20_beat", {})
        ls = e.get("long_short_car_ann", {})
        qs = e.get("quintiles_car_ann", {})
        rows.append({
            "Model": k, "IC vs reaction": e.get("ic_car_ann"), "p": e.get("p_car_ann"), "n": e.get("n_car_ann"),
            "IC vs SUE": e.get("ic_sue"), "IC vs revenue surprise": e.get("ic_rev_surprise"), "IC vs 20d drift": e.get("ic_car_drift20"),
            "Top-20% beat precision": pt.get("precision"), "Base rate": pt.get("base_rate"), "Perm. p": pt.get("perm_p"),
            "Q5−Q1 reaction": qs.get("spread"),
            "L/S per qtr, net 10bps": ls.get("mean_ls_net_10bps"), "t (net 10bps)": ls.get("t_net_10bps"),
        })
    st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True,
                 column_config={c: st.column_config.NumberColumn(format="%.3f") for c in
                                ["IC vs reaction", "p", "IC vs SUE", "IC vs revenue surprise", "IC vs 20d drift",
                                 "Top-20% beat precision", "Base rate", "Perm. p", "Q5−Q1 reaction",
                                 "L/S per qtr, net 10bps", "t (net 10bps)"]})

    st.subheader("Individual signals")
    sig = pd.DataFrame(s["signals"])
    show = sig[["feature", "kind", "history", "sign", "n", "train_ic_car_ann", "test_ic_car_ann", "test_p_car_ann",
                "test_q_car_ann", "test_n_car_ann", "test_ic_sue", "test_ic_rev_surprise", "test_ic_beat_consensus", "test_qic_car_ann", "test_qic_t"]]
    st.dataframe(show, hide_index=True, use_container_width=True,
                 column_config={c: st.column_config.NumberColumn(format="%.3f") for c in show.columns if "ic" in c or "_p_" in c or "_q_" in c})
    st.caption("test_q = Benjamini–Hochberg adjusted p-value across signals. qic = mean of per-quarter cross-sectional ICs and its t-statistic.")

    if s.get("train_weights"):
        st.write("Signals that earned a data-driven weight in the TRAIN period:", s["train_weights"])
    else:
        st.write("No signal passed the train-period bar (per-quarter IC t ≥ 2 in the pre-registered direction), so no data-driven weights exist.")

    ser = pd.DataFrame(s.get("long_short_series", []))
    if not ser.empty:
        ser["cum"] = ser["ls"].cumsum()
        st.plotly_chart(px.bar(ser, x="quarter", y="ls", title="Composite: top-third minus bottom-third reaction, per quarter (gross)"), use_container_width=True)

    st.subheader("By year and segment (test)")
    env = s.get("environments", {})
    c1, c2 = st.columns(2)
    c1.dataframe(pd.DataFrame(env.get("by_year", {})).T, use_container_width=True)
    c2.dataframe(pd.DataFrame(env.get("by_segment", {})).T, use_container_width=True)

    st.subheader("Data coverage")
    cov = pd.DataFrame(s["coverage"]).T
    st.dataframe(cov, use_container_width=True)
    st.write("Outcome coverage:", s.get("outcome_coverage"))

    st.subheader("Beat-probability calibration")
    st.json(s.get("calibration", {}))

    st.subheader("Options strategy")
    opt = s.get("options_scenario")
    if opt:
        st.error(opt.get("label", "HYPOTHETICAL"))
        sc = pd.DataFrame(opt.get("scenarios", []))
        if not sc.empty:
            flat = []
            for _, r in sc.iterrows():
                flat.append({
                    "Implied/hist move k": r["k_implied_vs_hist"], "Strike": r["moneyness"], "Events": r["subset"], "n": r["n"],
                    "Mean return/$": r["mean_ret_per_dollar"], "Win rate": r["win_rate"],
                    "Fixed $2,500: final": r["fixed_2500"].get("final_equity"), "Fixed: max DD": r["fixed_2500"].get("max_drawdown"),
                    "5% sizing: final": r["fractional_5pct"].get("final_equity"),
                    "P(ruin) fixed (MC)": r["mc_fixed_2500"].get("p_ruin"), "P(ruin) 5% (MC)": r["mc_fractional_5pct"].get("p_ruin"),
                })
            st.dataframe(pd.DataFrame(flat), hide_index=True, use_container_width=True)
        st.caption("Real underlying moves; modelled option prices (Black–Scholes, IV crush, 8% spread, $0.65/contract). "
                   "Literature: buying options through announcements loses on average (Dubinsky et al. 2019; de Silva, Smith & So 2025).")
        obs = opt.get("observed", {})
        st.write("Observed implied vs realized moves (real collected quotes):", {k: v for k, v in obs.items() if k != "rows"})

    st.subheader("Prospective track record")
    tr = q("SELECT * FROM track_record ORDER BY announce_date DESC")
    if tr.empty:
        st.write("No scored announcement has reported yet. Every daily score is stored, and each one is graded here after the company reports.")
    else:
        st.dataframe(tr, hide_index=True, use_container_width=True)
        m = tr.dropna(subset=["composite", "car_ann"])
        if len(m) >= 10:
            from scipy.stats import spearmanr
            r, p = spearmanr(m["composite"], m["car_ann"])
            st.metric("Live rank IC (composite vs reaction)", f"{r:+.3f}", f"p={p:.3f}, n={len(m)}")


# ---------------------------------------------------------- research log
def page_log():
    st.title("Research log")
    lg = q("SELECT ts, category, title, body, ref FROM research_log ORDER BY id DESC")
    if lg.empty:
        st.info("Empty. `pipeline init` seeds it with the initial research decisions.")
        return
    cats = st.multiselect("Category", sorted(lg["category"].unique()))
    if cats:
        lg = lg[lg["category"].isin(cats)]
    for _, r in lg.iterrows():
        with st.expander(f"{r['ts'][:16]} · {r['category']} · {r['title']}"):
            st.markdown(r["body"] or "")
            if r["ref"]:
                st.caption(f"ref: {r['ref']}")


# ------------------------------------------------------------ data health
def page_health():
    st.title("Data health")
    counts = []
    for tbl in ("filings", "fundamentals", "earnings_events", "consensus", "prices", "observations", "job_postings",
                "insider_transactions", "option_quotes", "implied_moves", "features", "scores", "track_record"):
        counts.append({"table": tbl, "rows": int(q(f"SELECT COUNT(*) n FROM {tbl}")["n"].iloc[0])})
    st.dataframe(pd.DataFrame(counts), hide_index=True)
    runs = q("SELECT collector, ticker, MAX(finished_at) AS last_run, status, n_rows, message FROM collector_runs GROUP BY collector, ticker ORDER BY collector, ticker")
    if runs.empty:
        st.info("No collector has run yet.")
        return
    st.subheader("Latest run per collector")
    st.dataframe(runs, hide_index=True, use_container_width=True)
    err = q("SELECT * FROM collector_runs WHERE status='error' ORDER BY id DESC LIMIT 100")
    if not err.empty:
        st.subheader("Recent errors")
        st.dataframe(err, hide_index=True, use_container_width=True)
    obs = q("SELECT source, ticker, MIN(obs_date) AS first, MAX(obs_date) AS last, COUNT(*) AS n FROM observations GROUP BY source, ticker")
    if not obs.empty:
        st.subheader("Alternative-data coverage")
        st.dataframe(obs, hide_index=True, use_container_width=True)


PAGES = {"Rankings": page_rankings, "Company detail": page_company, "Backtest": page_backtest,
         "Research log": page_log, "Data health": page_health}
choice = st.sidebar.radio("Page", list(PAGES))
st.sidebar.caption(f"Data snapshot: {SNAPSHOT_TIME} UTC" if SNAPSHOT_TIME else f"Database: {config.db_path()}")
st.sidebar.caption("Research tool only. Not investment advice.")
PAGES[choice]()
