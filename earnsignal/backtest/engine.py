"""Event-level backtest with chronological, strictly out-of-sample evaluation.

Unit of observation: one earnings announcement. For each, features are
computed as of the entry close (see features.panel) and compared with:
  car_ann        market-adjusted return over the announcement window
  car_drift20    market-adjusted return over the next 20 trading days
  sue            EPS surprise vs a seasonal random walk (SEC data; full history)
  rev_surprise   change in YoY revenue growth (SEC data; full history)
  beat_consensus EPS above Yahoo consensus (limited history)

Train = first 60% of events by date; test = the rest. Signs are
pre-registered; data-driven weights are fit on train only.
"""
from __future__ import annotations

import json
import math
import uuid
from datetime import datetime, timezone

import numpy as np
import pandas as pd
from scipy import stats

from .. import db
from ..features.panel import FEATURES, OUTCOMES, Data, FeatureBuilder, historical_events
from ..model import scoring

TRAIN_FRAC = 0.6
COSTS_BPS = [0, 10, 25, 50]


# --------------------------------------------------------------------- panel
def build_panel(con, persist_features: bool = True) -> pd.DataFrame:
    data = Data(con)
    fb = FeatureBuilder(data)
    rows, feat_rows = [], []
    now = db.now_utc()
    for ev in historical_events(data):
        feats, det, meta = fb.compute(ev)
        if meta.get("error") or meta.get("pre") is None:
            continue
        out = fb.outcomes(ev)
        rows.append(
            {
                "ticker": ev.ticker, "announce_date": ev.announce_date, "timing": ev.timing,
                "period_end": ev.period_end, "segment": data.segment(ev.ticker),
                "cutoff": meta["info_cutoff"], "pre": meta["pre"], "post": meta["post"],
                **{f: feats.get(f, np.nan) for f in FEATURES},
                **{o: (np.nan if out.get(o) is None else out.get(o)) for o in OUTCOMES},
                "details": json.dumps(det, default=str),
                "source_url": ev.url,
            }
        )
        for f, v in feats.items():
            feat_rows.append({"ticker": ev.ticker, "announce_date": ev.announce_date, "as_of_date": meta["info_cutoff"],
                              "feature": f, "value": v, "detail": json.dumps(det.get(f, {}), default=str), "computed_at": now})
    if persist_features and feat_rows:
        db.upsert(con, "features", feat_rows)
        con.commit()
    return pd.DataFrame(rows)


# --------------------------------------------------------------- statistics
def spearman(x: pd.Series, y: pd.Series) -> tuple[float, float, int]:
    m = x.notna() & y.notna()
    n = int(m.sum())
    if n < 10 or x[m].nunique() < 2 or y[m].nunique() < 2:
        return np.nan, np.nan, n
    r, p = stats.spearmanr(x[m], y[m])
    return float(r), float(p), n


def quarterly_ic(df: pd.DataFrame, x: str, y: str, min_n: int = 5) -> dict:
    """Mean of per-calendar-quarter cross-sectional rank ICs (Fama-MacBeth style)."""
    q = pd.PeriodIndex(pd.to_datetime(df["announce_date"]), freq="Q")
    ics = []
    for _, g in df.groupby(q):
        m = g[x].notna() & g[y].notna()
        if m.sum() >= min_n and g.loc[m, x].nunique() > 1 and g.loc[m, y].nunique() > 1:
            ics.append(stats.spearmanr(g.loc[m, x], g.loc[m, y])[0])
    ics = np.array([i for i in ics if not np.isnan(i)])
    if len(ics) < 3:
        return {"mean_ic": np.nan, "t": np.nan, "n_quarters": int(len(ics))}
    t = ics.mean() / (ics.std(ddof=1) / math.sqrt(len(ics))) if ics.std(ddof=1) > 0 else np.nan
    return {"mean_ic": float(ics.mean()), "t": float(t), "n_quarters": int(len(ics))}


def bh_adjust(p: list[float]) -> list[float]:
    p = np.array([np.nan if v is None else v for v in p], dtype=float)
    ok = ~np.isnan(p)
    q = np.full_like(p, np.nan)
    if ok.sum() == 0:
        return q.tolist()
    pv = p[ok]
    order = np.argsort(pv)
    ranked = pv[order] * len(pv) / (np.arange(len(pv)) + 1)
    ranked = np.minimum.accumulate(ranked[::-1])[::-1]
    out = np.empty_like(pv)
    out[order] = np.minimum(ranked, 1)
    q[ok] = out
    return q.tolist()


def quantile_spread(df: pd.DataFrame, score: str, y: str, q: int = 5) -> dict:
    m = df[score].notna() & df[y].notna()
    d = df[m]
    if len(d) < 5 * q:
        return {"n": int(len(d))}
    bins = pd.qcut(d[score].rank(method="first"), q, labels=False)
    means = d.groupby(bins)[y].mean()
    return {"n": int(len(d)), "top": float(means.iloc[-1]), "bottom": float(means.iloc[0]),
            "spread": float(means.iloc[-1] - means.iloc[0]), "by_bucket": [float(v) for v in means.values]}


def long_short_by_quarter(df: pd.DataFrame, score: str, y: str = "car_ann", frac: float = 1 / 3) -> dict:
    """Each calendar quarter: mean y of top-third minus bottom-third events."""
    q = pd.PeriodIndex(pd.to_datetime(df["announce_date"]), freq="Q")
    rows = []
    for per, g in df.groupby(q):
        g = g[g[score].notna() & g[y].notna()]
        if len(g) < 6:
            continue
        k = max(1, int(len(g) * frac))
        g = g.sort_values(score)
        rows.append({"quarter": str(per), "long": g[y].iloc[-k:].mean(), "short": g[y].iloc[:k].mean(), "n": len(g)})
    if len(rows) < 3:
        return {"n_quarters": len(rows)}
    r = pd.DataFrame(rows)
    r["ls"] = r["long"] - r["short"]
    res = {"n_quarters": len(r), "series": r.to_dict("records")}
    for c in COSTS_BPS:
        net = r["ls"] - 4 * c / 1e4  # two legs, round trip each
        sd = net.std(ddof=1)
        res[f"mean_ls_net_{c}bps"] = float(net.mean())
        res[f"t_net_{c}bps"] = float(net.mean() / (sd / math.sqrt(len(net)))) if sd > 0 else np.nan
        res[f"sharpe_ann_net_{c}bps"] = float(net.mean() / sd * 2) if sd > 0 else np.nan  # 4 quarters/yr -> sqrt(4)=2
    return res


def precision_at_top(df: pd.DataFrame, score: str, y: str = "beat_consensus", top: float = 0.2, n_perm: int = 2000, seed: int = 0) -> dict:
    d = df[df[score].notna() & df[y].notna()]
    if len(d) < 25:
        return {"n": int(len(d))}
    k = max(1, int(len(d) * top))
    picks = d.sort_values(score).iloc[-k:]
    prec = float(picks[y].mean())
    base = float(d[y].mean())
    rng = np.random.default_rng(seed)
    yv = d[y].values
    perm = np.array([rng.choice(yv, size=k, replace=False).mean() for _ in range(n_perm)])
    p = float((np.sum(perm >= prec) + 1) / (n_perm + 1))
    fp = float(1 - prec)
    return {"n": int(len(d)), "k": k, "precision": prec, "base_rate": base, "lift": prec - base,
            "perm_p": p, "false_positive_rate_in_picks": fp}


# ------------------------------------------------------------ calibration
def calibrate(train: pd.DataFrame, test: pd.DataFrame, score: str = "composite", y: str = "beat_consensus",
              min_test: int = 100, n_boot: int = 1000, seed: int = 0) -> dict:
    from sklearn.linear_model import LogisticRegression

    tr = train[train[score].notna() & train[y].notna()]
    te = test[test[score].notna() & test[y].notna()]
    res = {"n_train": int(len(tr)), "n_test": int(len(te)), "approved": False}
    if len(tr) < 50 or len(te) < 30 or tr[y].nunique() < 2:
        res["reason"] = "not enough events with consensus outcomes"
        return res
    lr = LogisticRegression(C=1.0)
    lr.fit(tr[[score]].values, tr[y].values.astype(int))
    p = lr.predict_proba(te[[score]].values)[:, 1]
    base = float(tr[y].mean())
    yv = te[y].values.astype(float)
    brier_model = np.mean((p - yv) ** 2)
    brier_base = np.mean((base - yv) ** 2)
    rng = np.random.default_rng(seed)
    imp = []
    for _ in range(n_boot):
        i = rng.integers(0, len(yv), len(yv))
        imp.append(np.mean((base - yv[i]) ** 2) - np.mean((p[i] - yv[i]) ** 2))
    lo, hi = np.percentile(imp, [5, 95])
    res.update({
        "intercept": float(lr.intercept_[0]), "coef": float(lr.coef_[0][0]), "train_base_rate": base,
        "brier_model": float(brier_model), "brier_base_rate": float(brier_base),
        "brier_improvement": float(brier_base - brier_model), "improvement_ci90": [float(lo), float(hi)],
    })
    res["approved"] = bool(len(te) >= min_test and lo > 0)
    if not res["approved"]:
        res["reason"] = (f"test set too small ({len(te)} < {min_test})" if len(te) < min_test
                         else "out-of-sample Brier improvement not reliably above zero")
    return res


# -------------------------------------------------------------------- run
def run(con, train_frac: float = TRAIN_FRAC) -> dict:
    cfg = scoring.model_config()
    feat_cfg = cfg["features"]
    panel = build_panel(con)
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:6]
    summary: dict = {"run_id": run_id, "n_events": int(len(panel))}
    if len(panel) < 40:
        summary["status"] = "insufficient data"
        summary["message"] = f"Only {len(panel)} historical events with prices; need >= 40. Run collectors first."
        _store(con, run_id, cfg, summary, panel)
        return summary

    panel = panel.sort_values("cutoff").reset_index(drop=True)
    backtestable = [f for f in FEATURES if feat_cfg.get(f, {}).get("history") == "backtest"]
    alt = [f for f in backtestable if feat_cfg[f]["kind"] == "alt"]
    base = [f for f in backtestable if feat_cfg[f]["kind"] == "baseline"]

    z = scoring.standardize(panel, FEATURES)
    panel = pd.concat([panel, z], axis=1)
    for name, fl in (("composite", backtestable), ("composite_alt", alt), ("composite_baseline", base)):
        c = scoring.composite(panel, fl)
        panel[name] = c["composite"]
        if name == "composite":
            panel["n_signals"] = c["n_signals"]
    for f in FEATURES:  # signed single-signal scores, for benchmarks
        panel[f"s_{f}"] = scoring.signs().get(f, 1) * panel[f"z_{f}"]

    split_i = int(len(panel) * train_frac)
    split_date = panel.loc[split_i, "cutoff"]
    panel["split"] = np.where(panel["cutoff"] < split_date, "train", "test")
    train, test = panel[panel["split"] == "train"], panel[panel["split"] == "test"]
    summary.update({"split_date": split_date, "n_train": int(len(train)), "n_test": int(len(test)),
                    "date_range": [panel["announce_date"].min(), panel["announce_date"].max()]})

    # ---- data coverage
    summary["coverage"] = {f: {"n": int(panel[f].notna().sum()), "share": float(panel[f].notna().mean())} for f in FEATURES}
    summary["outcome_coverage"] = {o: int(panel[o].notna().sum()) for o in OUTCOMES}

    # ---- per-signal evaluation
    sig = []
    for f in FEATURES:
        s = f"s_{f}"
        row = {"feature": f, "kind": feat_cfg.get(f, {}).get("kind"), "history": feat_cfg.get(f, {}).get("history"),
               "sign": feat_cfg.get(f, {}).get("sign"), "n": int(panel[f].notna().sum())}
        for o in ("car_ann", "sue", "rev_surprise", "beat_consensus", "car_drift20"):
            for nm, part in (("train", train), ("test", test)):
                r, p, n = spearman(part[s], part[o])
                row[f"{nm}_ic_{o}"], row[f"{nm}_p_{o}"], row[f"{nm}_n_{o}"] = r, p, n
        qi = quarterly_ic(test, s, "car_ann")
        row["test_qic_car_ann"], row["test_qic_t"] = qi["mean_ic"], qi["t"]
        sig.append(row)
    for o in ("car_ann", "sue", "beat_consensus"):
        q = bh_adjust([r[f"test_p_{o}"] for r in sig])
        for r, v in zip(sig, q):
            r[f"test_q_{o}"] = v
    summary["signals"] = sig

    # ---- data-driven weights from TRAIN only
    w = {}
    for f in backtestable:
        qi = quarterly_ic(train, f"s_{f}", "car_ann")
        if not math.isnan(qi["mean_ic"]) and qi["t"] >= 2.0:  # must agree with the pre-registered sign
            w[f] = qi["mean_ic"]
    summary["train_weights"] = w
    if w:
        c = scoring.composite(panel, list(w), weights=w)
        panel["composite_trained"] = c["composite"]
    else:
        panel["composite_trained"] = np.nan

    # ---- model + benchmarks on TEST
    rng = np.random.default_rng(42)
    test = panel[panel["split"] == "test"].copy()
    test["random"] = rng.normal(size=len(test))
    models = {
        "composite (all, equal-weight)": "composite",
        "composite (alt-data only)": "composite_alt",
        "composite (baseline only)": "composite_baseline",
        "composite (train-fitted weights)": "composite_trained",
        "benchmark: price momentum": "s_momentum_60d",
        "benchmark: prior SUE": "s_prior_sue",
        "benchmark: random": "random",
    }
    evals = {}
    for name, col in models.items():
        e = {}
        for o in ("car_ann", "sue", "rev_surprise", "beat_consensus", "car_drift20"):
            r, p, n = spearman(test[col], test[o])
            e[f"ic_{o}"], e[f"p_{o}"], e[f"n_{o}"] = r, p, n
        e["precision_top20_beat"] = precision_at_top(test, col, "beat_consensus")
        e["quintiles_car_ann"] = quantile_spread(test, col, "car_ann")
        e["long_short_car_ann"] = {k: v for k, v in long_short_by_quarter(test, col).items() if k != "series"}
        evals[name] = e
    summary["test_eval"] = evals
    summary["base_rate_beat_test"] = float(test["beat_consensus"].mean()) if test["beat_consensus"].notna().any() else None
    summary["long_short_series"] = long_short_by_quarter(test, "composite").get("series", [])

    # ---- environments
    env = {}
    for key, grp in (("by_year", pd.to_datetime(test["announce_date"]).dt.year), ("by_segment", test["segment"])):
        env[key] = {}
        for g, part in test.groupby(grp):
            r, p, n = spearman(part["composite"], part["car_ann"])
            env[key][str(g)] = {"ic_car_ann": r, "p": p, "n": n}
    summary["environments"] = env

    # ---- calibration (probability of beating consensus)
    cal = calibrate(panel[panel["split"] == "train"], test, "composite",
                    min_test=cfg.get("calibration", {}).get("min_test_events", 100))
    summary["calibration"] = cal
    db.upsert(con, "model_registry", [{
        "name": "beat_probability", "created_at": db.now_utc(), "params": json.dumps(cal),
        "validation": json.dumps({"run_id": run_id}), "approved": int(cal.get("approved", False)),
    }])

    summary["status"] = "ok"
    _store(con, run_id, cfg, summary, panel)
    _log_findings(con, summary)
    return summary


def _store(con, run_id, cfg, summary, panel):
    con.execute("INSERT OR REPLACE INTO backtest_runs VALUES (?,?,?,?)",
                (run_id, db.now_utc(), json.dumps(cfg, default=str), json.dumps(summary, default=_jsonable)))
    if len(panel):
        keep = ["ticker", "announce_date", "split"] if "split" in panel.columns else ["ticker", "announce_date"]
        rows = []
        for _, r in panel.iterrows():
            data = {k: (None if (isinstance(v, float) and math.isnan(v)) else v) for k, v in r.items() if k not in ("details",)}
            rows.append({"run_id": run_id, "ticker": r["ticker"], "announce_date": r["announce_date"],
                         "split": r.get("split", "all"), "data": json.dumps(data, default=_jsonable)})
        db.upsert(con, "backtest_events", rows)
    con.commit()


def _jsonable(o):
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, (np.floating,)):
        return None if np.isnan(o) else float(o)
    if isinstance(o, (pd.Timestamp,)):
        return o.isoformat()
    return str(o)


def _log_findings(con, s: dict) -> None:
    rid = s["run_id"]
    db.log(con, "model", f"Backtest {rid}: {s['n_events']} events ({s['n_train']} train / {s['n_test']} test)",
           f"Split date {s['split_date']}. Date range {s['date_range']}.", rid)
    for r in s["signals"]:
        if r["history"] != "backtest":
            continue
        ic, p, n = r["test_ic_car_ann"], r["test_p_car_ann"], r["test_n_car_ann"]
        q = r.get("test_q_car_ann")
        if n < 30 or ic is None or (isinstance(ic, float) and math.isnan(ic)):
            db.log(con, "signal", f"{r['feature']}: not enough test events to evaluate (n={n})", "", rid)
        elif q is not None and not math.isnan(q) and q < 0.10 and ic > 0:
            db.log(con, "signal", f"{r['feature']}: predicted announcement returns out-of-sample",
                   f"Test rank IC {ic:.3f} (p={p:.3f}, BH q={q:.3f}, n={n}). Needs prospective confirmation.", rid)
        else:
            db.log(con, "failed_hypothesis", f"{r['feature']}: no reliable out-of-sample link to announcement returns",
                   f"Test rank IC {ic:.3f} (p={p:.3f}, BH q={q if q is None else round(q, 3)}, n={n}). Pre-registered sign {r['sign']:+d}.", rid)
    cal = s["calibration"]
    if not cal.get("approved"):
        db.log(con, "model", "Beat probability NOT published", cal.get("reason", ""), rid)
    else:
        db.log(con, "model", "Beat probability calibration approved",
               f"Brier improvement {cal['brier_improvement']:.4f}, 90% CI {cal['improvement_ci90']}", rid)
