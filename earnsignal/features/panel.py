"""Point-in-time feature and outcome computation.

Leakage rules (enforced here and checked by tests/test_leakage.py):
  * An event's features are computed "as of" the close of its entry date
    (pre_date = last trading day before the release reaches the market).
  * Prices may be used up to and including pre_date's close.
  * Every other datum must be dated strictly before pre_date
    (filed_date / obs_date / filing_date < as_of).
  * Earlier quarters' fundamentals count as known only once their earnings
    release (8-K) or 10-Q/10-K filing date has passed.
  * Peer reactions count only if the peer's reaction window closed before as_of.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date, timedelta

import numpy as np
import pandas as pd

from .. import db
from ..collectors.sec import match_period, quarterly_series

FEATURES = [
    "prior_sue", "prior_consensus_surprise", "rev_accel_prev", "momentum_60d",
    "wiki_yoy_rel", "wiki_accel", "peer_early_reaction", "universe_early_reaction",
    "peer_early_surprise", "insider_buyers_90d", "insider_opp_buyers_90d", "insider_sell_abnormal",
    "jobs_growth_28d", "jobs_gtm_share_chg", "app_rating_growth_28d",
]
OUTCOMES = ["sue", "rev_surprise", "beat_consensus", "surprise_pct", "car_ann", "ret_ann", "car_drift20"]

MIN_PEERS = 3


def _nan(x) -> bool:
    return x is None or (isinstance(x, float) and math.isnan(x))


@dataclass
class Event:
    ticker: str
    announce_date: str
    timing: str
    period_end: str | None
    source: str
    url: str | None = None


class Data:
    """Everything needed for feature computation, loaded once into memory."""

    def __init__(self, con, benchmark: str = "SPY"):
        self.benchmark = benchmark
        self.companies = db.df(con, "SELECT * FROM companies").set_index("ticker")
        self.events = db.df(con, "SELECT * FROM earnings_events")
        fund = db.df(con, "SELECT ticker, metric, tag, period_start, period_end, duration_days, filed_date, value FROM fundamentals")
        px = db.df(con, "SELECT ticker, date, close, adj_close FROM prices")
        self.adj = px.pivot(index="date", columns="ticker", values="adj_close").sort_index() if not px.empty else pd.DataFrame()
        self.close = px.pivot(index="date", columns="ticker", values="close").sort_index() if not px.empty else pd.DataFrame()
        if not self.adj.empty:
            self.adj.index = pd.to_datetime(self.adj.index)
            self.close.index = pd.to_datetime(self.close.index)
        ref = self.adj[benchmark].dropna() if benchmark in self.adj.columns else (self.adj.dropna(how="all").iloc[:, 0] if not self.adj.empty else pd.Series(dtype=float))
        self.cal = pd.DatetimeIndex(ref.index)
        self.consensus = db.df(con, "SELECT * FROM consensus")
        self.insider = db.df(con, "SELECT ticker, filing_date, owner_name, trans_code, acq_disp, value FROM insider_transactions")
        self.insider_cov = (
            (self.insider["filing_date"].min(), self.insider["filing_date"].max()) if not self.insider.empty else (None, None)
        )
        obs = db.df(con, "SELECT source, ticker, series, obs_date, value, source_url, observed_at FROM observations")
        self.obs = obs
        self._wiki = _build_wiki_static(obs)
        self.fund = {t: g for t, g in fund.groupby("ticker")} if not fund.empty else {}
        ev = self.events[self.events["source"] == "sec_8k_202"]
        self.pe_to_ann = {t: dict(zip(g["period_end"], g["announce_date"])) for t, g in ev.groupby("ticker")} if not ev.empty else {}
        # full-history quarterly tables: used for OUTCOMES and calendar structure only
        self._q = {}
        for t in self.companies.index:
            f = self.fund.get(t, fund.iloc[0:0])
            self._q[t] = {m: attach_known_dates(quarterly_series(f, m), self.pe_to_ann.get(t, {})) for m in ("revenue", "eps_diluted")}

    def quarters_pit(self, t: str, metric: str, cutoff: str) -> pd.DataFrame:
        """Quarterly table built ONLY from facts filed strictly before `cutoff`
        (tag choice, Q4 derivation and values all point-in-time)."""
        f = self.fund.get(t)
        if f is None:
            return pd.DataFrame(columns=["period_start", "period_end", "value", "filed_date", "tag", "derived", "known_date"])
        prev = (date.fromisoformat(cutoff) - timedelta(days=1)).isoformat()
        return attach_known_dates(quarterly_series(f, metric, as_of=prev), self.pe_to_ann.get(t, {}))

    # ------------------------------------------------------------ utilities
    def segment(self, t: str) -> str:
        return self.companies.loc[t, "segment"] if t in self.companies.index else "other"

    def fye(self, t: str) -> int:
        v = self.companies.loc[t, "fiscal_year_end"] if t in self.companies.index else 12
        return int(v) if not _nan(v) and v else 12

    def window(self, announce_date: str, timing: str) -> tuple[pd.Timestamp | None, pd.Timestamp | None]:
        """(pre_date, post_date): entry close and the first close after the release."""
        if len(self.cal) == 0:
            return None, None
        a = pd.Timestamp(announce_date)
        if timing == "AMC":
            pre = self.cal[self.cal <= a]
        else:
            pre = self.cal[self.cal < a]
        if len(pre) == 0:
            return None, None
        pre_d = pre[-1]
        post = self.cal[self.cal > pre_d]
        return pre_d, (post[0] if len(post) else None)

    def shift_td(self, d: pd.Timestamp, n: int) -> pd.Timestamp | None:
        i = self.cal.searchsorted(d)
        j = i + n
        return self.cal[j] if 0 <= j < len(self.cal) else None

    def ret(self, t: str, d0, d1) -> float | None:
        if t not in self.adj.columns or d0 is None or d1 is None:
            return None
        s = self.adj[t]
        try:
            a, b = s.loc[d0], s.loc[d1]
        except KeyError:
            return None
        if _nan(a) or _nan(b) or a <= 0:
            return None
        return float(b / a - 1)

    def excess(self, t: str, d0, d1) -> float | None:
        r, m = self.ret(t, d0, d1), self.ret(self.benchmark, d0, d1)
        return None if r is None or m is None else r - m

    # -------------------------------------------------------- fundamentals
    def quarters(self, t: str, metric: str) -> pd.DataFrame:
        return self._q.get(t, {}).get(metric, pd.DataFrame())


def attach_known_dates(q: pd.DataFrame, pe_to_ann: dict) -> pd.DataFrame:
    """known_date = min(10-Q/10-K filed date, matching earnings-release date)."""
    q = q.copy()
    if q.empty:
        q["known_date"] = pd.Series(dtype=str)
        return q
    ann = q["period_end"].map(lambda p: _near_lookup(pe_to_ann, p, 7))
    q["known_date"] = [min(f, a) if isinstance(a, str) else f for f, a in zip(q["filed_date"], ann)]
    return q


def _near_lookup(mapping: dict, period_end: str, tol: int):
    if period_end in mapping:
        return mapping[period_end]
    p = date.fromisoformat(period_end)
    for k, v in mapping.items():
        if k and abs((date.fromisoformat(k) - p).days) <= tol:
            return v
    return None


# ------------------------------------------------------------ insiders
def classify_insider(trades: pd.DataFrame, owner: str, on_date: str) -> str:
    """Cohen-Malloy-Pomorski: look at the owner's open-market trades (P/S) in
    the three calendar years before `on_date`'s year. Routine = traded in the
    same calendar month in each of those years. Opportunistic = traded in each
    of those years, not routinely. Otherwise unclassified."""
    d = date.fromisoformat(on_date)
    t = trades[(trades["owner_name"] == owner) & trades["trans_code"].isin(["P", "S"]) & (trades["filing_date"] < on_date)]
    if t.empty:
        return "unclassified"
    dt = pd.to_datetime(t["filing_date"])
    years = [d.year - k for k in (1, 2, 3)]
    if not all((dt.dt.year == y).any() for y in years):
        return "unclassified"
    routine = all(((dt.dt.year == y) & (dt.dt.month == d.month)).any() for y in years)
    return "routine" if routine else "opportunistic"


# ----------------------------------------------------------- seasonal stats
def seasonal_table(q: pd.DataFrame) -> pd.DataFrame:
    """Add YoY growth, seasonal difference and SUE to a quarterly table."""
    q = q.sort_values("period_end").reset_index(drop=True).copy()
    if q.empty:
        for c in ("yoy", "sdiff", "sue"):
            q[c] = []
        return q
    ends = [date.fromisoformat(x) for x in q["period_end"]]
    lag4 = []
    for i, e in enumerate(ends):
        target = e - timedelta(days=364)
        j = next((k for k in range(i - 1, -1, -1) if abs((ends[k] - target).days) <= 20), None)
        lag4.append(j)
    vals = q["value"].astype(float).tolist()
    yoy, sdiff = [], []
    for i, j in enumerate(lag4):
        if j is None:
            yoy.append(np.nan)
            sdiff.append(np.nan)
        else:
            sdiff.append(vals[i] - vals[j])
            yoy.append(vals[i] / vals[j] - 1 if vals[j] and vals[j] > 0 else np.nan)
    q["yoy"], q["sdiff"] = yoy, sdiff
    sue = []
    for i in range(len(q)):
        hist = pd.Series(sdiff[max(0, i - 8):i]).dropna()
        sd = hist.std(ddof=1) if len(hist) >= 4 else np.nan
        sue.append(sdiff[i] / sd if (not _nan(sdiff[i]) and sd and not _nan(sd) and sd > 0) else np.nan)
    q["sue"] = sue
    return q


def _row_for_period(q: pd.DataFrame, period_end: str, tol: int = 10):
    if q.empty or period_end is None:
        return None
    p = date.fromisoformat(period_end)
    for i, r in q.iterrows():
        if abs((date.fromisoformat(r["period_end"]) - p).days) <= tol:
            return i
    return None


# ------------------------------------------------------------ wikipedia
def _build_wiki_static(obs: pd.DataFrame) -> dict[str, pd.Series]:
    w = obs[obs["source"] == "wikipedia"]
    out = {}
    for t, g in w.groupby("ticker"):
        s = g.groupby("obs_date")["value"].sum()
        s.index = pd.to_datetime(s.index)
        out[t] = s.sort_index()
    return out



def wiki_window_logratio(series: pd.Series, start: pd.Timestamp, end: pd.Timestamp, min_cov: float = 0.9) -> float | None:
    """log(sum views in [start,end] / sum views in the same window 364 days earlier)."""
    if series is None or series.empty:
        return None
    days = (end - start).days + 1
    cur = series.loc[start:end]
    ly = series.loc[start - pd.Timedelta(days=364): end - pd.Timedelta(days=364)]
    if len(cur) < min_cov * days or len(ly) < min_cov * days:
        return None
    a, b = cur.mean(), ly.mean()  # means are robust to a few missing days
    if not a or not b or a <= 0 or b <= 0:
        return None
    return float(math.log(a / b))


# ------------------------------------------------------------- the panel
class FeatureBuilder:
    def __init__(self, data: Data):
        self.d = data
        self._seasonal = {(t, m): seasonal_table(data.quarters(t, m)) for t in data.companies.index for m in ("revenue", "eps_diluted")}
        self._event_reactions = self._reaction_table()

    # quarter window for a fiscal period end
    def quarter_window(self, t: str, period_end: str) -> tuple[pd.Timestamp, pd.Timestamp]:
        pe = pd.Timestamp(period_end)
        q = self.d.quarters(t, "revenue")
        prev = None
        if not q.empty:
            earlier = [x for x in q["period_end"] if 70 <= (pe - pd.Timestamp(x)).days <= 110]
            if earlier:
                prev = pd.Timestamp(max(earlier))
        if prev is None:
            prev = pe - pd.DateOffset(months=3)
        return prev + pd.Timedelta(days=1), pe

    def _reaction_table(self) -> pd.DataFrame:
        """Reaction windows and market-adjusted returns for all reported events."""
        ev = self.d.events[self.d.events["source"] == "sec_8k_202"]
        rows = []
        for _, e in ev.iterrows():
            pre, post = self.d.window(e["announce_date"], e["timing"])
            rows.append(
                {
                    "ticker": e["ticker"], "announce_date": e["announce_date"], "period_end": e["period_end"],
                    "timing": e["timing"], "pre": pre, "post": post,
                    "car": self.d.excess(e["ticker"], pre, post), "ret": self.d.ret(e["ticker"], pre, post),
                    "segment": self.d.segment(e["ticker"]),
                }
            )
        return pd.DataFrame(rows)

    def _consensus_for(self, t: str, announce_date: str, tol: int = 4):
        c = self.d.consensus
        if c.empty:
            return None
        c = c[c["ticker"] == t]
        if c.empty:
            return None
        a = date.fromisoformat(announce_date)
        diffs = c["announce_date"].map(lambda x: abs((date.fromisoformat(x) - a).days))
        if diffs.min() > tol:
            return None
        return c.loc[diffs.idxmin()]

    # ---------------------------------------------------------------- core
    def compute(self, ev: Event, live_today: date | None = None) -> tuple[dict, dict, dict]:
        """Return (features, details, meta) for one event.

        Historical events: information cutoff = the entry date (pre_date);
        non-price data must be dated strictly before it, prices up to its close.
        Live/upcoming events (live_today given): everything observed up to and
        including today, prices up to the latest close.
        """
        d = self.d
        t = ev.ticker
        pre, post = d.window(ev.announce_date, ev.timing)
        if live_today is not None:
            as_of = pd.Timestamp(live_today) + pd.Timedelta(days=1)  # strict cutoff => includes today
            past = d.cal[d.cal <= pd.Timestamp(live_today)]
            px_asof = past[-1] if len(past) else None
        else:
            as_of, px_asof = pre, pre
        if as_of is None or px_asof is None:
            return {}, {}, {"error": "no price calendar"}
        as_of_s = as_of.date().isoformat()
        period_end = ev.period_end if isinstance(ev.period_end, str) and ev.period_end else None
        if not period_end:
            ends = [date.fromisoformat(x) for x in d.quarters(t, "revenue")["period_end"]] if not d.quarters(t, "revenue").empty else []
            p = match_period(date.fromisoformat(ev.announce_date), ends, d.fye(t), max_gap=100)
            period_end = p.isoformat() if p else None
        feats: dict[str, float] = {}
        det: dict[str, dict] = {}

        # ---- fundamentals known before as_of (rebuilt from facts filed before the cutoff)
        for metric in ("eps_diluted", "revenue"):
            s = seasonal_table(d.quarters_pit(t, metric, as_of_s))
            known = s[(s["known_date"] < as_of_s)] if not s.empty else s
            if period_end is not None and not known.empty:
                known = known[known["period_end"] < period_end]
            if metric == "eps_diluted" and not known.empty and not _nan(known.iloc[-1]["sue"]):
                r = known.iloc[-1]
                feats["prior_sue"] = float(np.clip(r["sue"], -10, 10))
                det["prior_sue"] = {"quarter": r["period_end"], "known_since": r["known_date"], "source": "SEC XBRL companyfacts"}
            if metric == "revenue" and len(known) >= 2:
                g1, g2 = known.iloc[-1]["yoy"], known.iloc[-2]["yoy"]
                if not _nan(g1) and not _nan(g2):
                    feats["rev_accel_prev"] = float(g1 - g2)
                    det["rev_accel_prev"] = {"quarters": [known.iloc[-2]["period_end"], known.iloc[-1]["period_end"]],
                                             "yoy": [float(g2), float(g1)], "source": "SEC XBRL companyfacts"}

        # ---- last consensus surprise
        c = d.consensus
        if not c.empty:
            ct = c[(c["ticker"] == t) & (c["announce_date"] < as_of_s)]
            if period_end:
                ct = ct[ct["announce_date"] > (date.fromisoformat(period_end) - timedelta(days=150)).isoformat()]
            ct = ct.sort_values("announce_date")
            if not ct.empty and not _nan(ct.iloc[-1]["surprise_pct"]):
                r = ct.iloc[-1]
                feats["prior_consensus_surprise"] = float(np.clip(r["surprise_pct"], -100, 100))
                det["prior_consensus_surprise"] = {"announce_date": r["announce_date"], "estimate": r["eps_estimate"],
                                                   "reported": r["eps_reported"], "source": r["source_url"]}

        # ---- momentum
        start = d.shift_td(px_asof, -63)
        m = d.excess(t, start, px_asof)
        if m is not None:
            feats["momentum_60d"] = m
            det["momentum_60d"] = {"from": str(start.date()), "to": str(px_asof.date()), "benchmark": d.benchmark}

        # ---- wikipedia
        if period_end:
            w0, w1 = self.quarter_window(t, period_end)
            w1 = min(w1, as_of - pd.Timedelta(days=1))
            pw0, pw1 = w0 - pd.DateOffset(months=3), w0 - pd.Timedelta(days=1)
            cur = self._wiki_rel(t, w0, w1)
            prev = self._wiki_rel(t, pw0, pw1)
            if cur is not None:
                feats["wiki_yoy_rel"] = cur["rel"]
                det["wiki_yoy_rel"] = {"window": [str(w0.date()), str(w1.date())], "own_logratio": cur["own"],
                                       "peer_median": cur["peer_median"], "n_peers": cur["n_peers"],
                                       "source": "Wikimedia pageviews API (agent=user)"}
                if prev is not None:
                    feats["wiki_accel"] = cur["rel"] - prev["rel"]
                    det["wiki_accel"] = {"prev_window": [str(pw0.date()), str(pw1.date())], "prev_rel": prev["rel"]}

        # ---- peer early reporters (same fiscal quarter, reaction closed before as_of)
        rt = self._event_reactions
        if period_end and not rt.empty:
            pe = pd.Timestamp(period_end)
            mask = (
                (rt["ticker"] != t)
                & rt["period_end"].notna()
                & (rt["post"].notna())
                & (rt["post"] < as_of)
                & rt["car"].notna()
            )
            cand = rt[mask].copy()
            if not cand.empty:
                cand = cand[(pd.to_datetime(cand["period_end"]) - pe).abs() <= pd.Timedelta(days=20)]
                cand = cand[pd.to_datetime(cand["announce_date"]) > pe]
            if not cand.empty:
                seg = cand[cand["segment"] == d.segment(t)]
                if len(seg):
                    feats["peer_early_reaction"] = float(seg["car"].mean())
                    det["peer_early_reaction"] = {"peers": seg[["ticker", "announce_date", "car"]].round(4).to_dict("records"), "source": "prices + SEC 8-K Item 2.02 dates"}
                    sur = []
                    for _, p in seg.iterrows():
                        cr = self._consensus_for(p["ticker"], p["announce_date"])
                        if cr is not None and not _nan(cr["surprise_pct"]):
                            sur.append(float(np.clip(cr["surprise_pct"], -100, 100)))
                    if sur:
                        feats["peer_early_surprise"] = float(np.mean(sur))
                        det["peer_early_surprise"] = {"n": len(sur), "source": "Yahoo EPS estimate vs reported"}
                if len(cand) >= 2:
                    feats["universe_early_reaction"] = float(cand["car"].mean())
                    det["universe_early_reaction"] = {"n": int(len(cand)), "tickers": sorted(cand["ticker"].unique().tolist())}

        # ---- insiders
        lo, hi = d.insider_cov
        if lo and hi:
            a90 = (as_of - pd.Timedelta(days=90)).date().isoformat()
            if a90 >= lo and as_of_s <= (date.fromisoformat(hi) + timedelta(days=10)).isoformat():
                ins = d.insider[(d.insider["ticker"] == t) & (d.insider["filing_date"] < as_of_s)]
                win = ins[ins["filing_date"] >= a90]
                buys = win[(win["trans_code"] == "P") & (win["acq_disp"] == "A")]
                feats["insider_buyers_90d"] = float(buys["owner_name"].nunique())
                det["insider_buyers_90d"] = {"window": [a90, as_of_s], "buyers": sorted(buys["owner_name"].dropna().unique().tolist())[:10],
                                             "value": float(buys["value"].fillna(0).sum()), "source": "SEC Form 4"}
                a3y = f"{as_of.year - 3}-01-01"
                if a3y >= lo:  # need three full prior calendar years to classify insiders
                    opp = [n for n in buys["owner_name"].dropna().unique()
                           if classify_insider(ins, n, buys[buys["owner_name"] == n]["filing_date"].min()) == "opportunistic"]
                    feats["insider_opp_buyers_90d"] = float(len(opp))
                    det["insider_opp_buyers_90d"] = {"buyers": sorted(opp)[:10], "method": "Cohen, Malloy & Pomorski (2012)"}
                a2y = (as_of - pd.Timedelta(days=90 + 730)).date().isoformat()
                if a2y >= lo:
                    sales = ins[(ins["trans_code"] == "S")]
                    cur_s = float(sales[sales["filing_date"] >= a90]["value"].fillna(0).sum())
                    hist = sales[(sales["filing_date"] >= a2y) & (sales["filing_date"] < a90)]
                    avg = float(hist["value"].fillna(0).sum()) / 8.0
                    feats["insider_sell_abnormal"] = float(math.log((1 + cur_s) / (1 + avg)))
                    det["insider_sell_abnormal"] = {"sales_90d": cur_s, "avg_90d_prior_2y": avg}

        # ---- prospective snapshots
        for name, src, series, fn in (
            ("jobs_growth_28d", "jobs", "total", "logratio"),
            ("app_rating_growth_28d", "appstore", "rating_count", "logratio"),
        ):
            v = self._snapshot_rel(t, src, series, as_of)
            if v is not None:
                feats[name] = v["rel"]
                det[name] = v
        g = self._gtm_share_change(t, as_of)
        if g is not None:
            feats["jobs_gtm_share_chg"] = g["chg"]
            det["jobs_gtm_share_chg"] = g

        meta = {"info_cutoff": as_of_s, "price_as_of": str(px_asof.date()), "pre": str(pre.date()) if pre is not None else None,
                "post": str(post.date()) if post is not None else None, "period_end": period_end}
        return feats, det, meta

    def _wiki_rel(self, t: str, w0, w1):
        if w1 <= w0:
            return None
        own = wiki_window_logratio(self.d._wiki.get(t), w0, w1)
        if own is None:
            return None
        peers = [wiki_window_logratio(s, w0, w1) for k, s in self.d._wiki.items() if k != t]
        peers = [p for p in peers if p is not None]
        if len(peers) < MIN_PEERS:
            return None
        med = float(np.median(peers))
        return {"own": own, "peer_median": med, "n_peers": len(peers), "rel": own - med}

    def _snapshot_logratio(self, t: str, src: str, series: str, as_of: pd.Timestamp):
        o = self.d.obs
        s = o[(o["source"] == src) & (o["ticker"] == t) & (o["series"] == series) & (o["obs_date"] < as_of.date().isoformat())]
        if len(s) < 2:
            return None
        s = s.sort_values("obs_date")
        t1 = pd.Timestamp(s.iloc[-1]["obs_date"])
        if (as_of - t1).days > 7:
            return None
        s0 = s[(pd.to_datetime(s["obs_date"]) <= t1 - pd.Timedelta(days=21)) & (pd.to_datetime(s["obs_date"]) >= t1 - pd.Timedelta(days=35))]
        if s0.empty:
            return None
        r0 = s0.iloc[(pd.to_datetime(s0["obs_date"]) - (t1 - pd.Timedelta(days=28))).abs().argsort().iloc[0]]
        v0, v1 = float(r0["value"]), float(s.iloc[-1]["value"])
        if v0 <= 0 or v1 <= 0 or (src == "appstore" and v1 < v0):  # rating reset -> unusable
            return None
        return {"t0": r0["obs_date"], "t1": s.iloc[-1]["obs_date"], "v0": v0, "v1": v1, "own": math.log(v1 / v0),
                "source": s.iloc[-1]["source_url"], "observed_at": s.iloc[-1]["observed_at"]}

    def _snapshot_rel(self, t, src, series, as_of):
        own = self._snapshot_logratio(t, src, series, as_of)
        if own is None:
            return None
        peers = []
        for k in self.d.companies.index:
            if k == t:
                continue
            p = self._snapshot_logratio(k, src, series, as_of)
            if p is not None:
                peers.append(p["own"])
        if len(peers) < MIN_PEERS:
            return None
        own["peer_median"] = float(np.median(peers))
        own["n_peers"] = len(peers)
        own["rel"] = own["own"] - own["peer_median"]
        return own

    def _gtm_share_change(self, t, as_of):
        o = self.d.obs
        j = o[(o["source"] == "jobs") & (o["ticker"] == t) & (o["obs_date"] < as_of.date().isoformat())]
        tot = j[j["series"] == "total"].set_index("obs_date")["value"]
        if len(tot) < 2:
            return None
        gtm = j[j["series"].isin(["cat:sales_gtm", "cat:ops_support"])].groupby("obs_date")["value"].sum()
        dates = sorted(tot.index)
        t1 = dates[-1]
        cands = [x for x in dates if 21 <= (pd.Timestamp(t1) - pd.Timestamp(x)).days <= 35]
        if not cands or (as_of - pd.Timestamp(t1)).days > 7:
            return None
        t0 = min(cands, key=lambda x: abs((pd.Timestamp(t1) - pd.Timestamp(x)).days - 28))
        if tot[t0] <= 0 or tot[t1] <= 0:
            return None
        s0, s1 = gtm.get(t0, 0) / tot[t0], gtm.get(t1, 0) / tot[t1]
        return {"t0": t0, "t1": t1, "share0": s0, "share1": s1, "chg": s1 - s0}

    # ------------------------------------------------------------ outcomes
    def outcomes(self, ev: Event) -> dict:
        d = self.d
        t = ev.ticker
        out: dict[str, float | None] = {}
        pre, post = d.window(ev.announce_date, ev.timing)
        out["car_ann"] = d.excess(t, pre, post)
        out["ret_ann"] = d.ret(t, pre, post)
        end = d.shift_td(post, 20) if post is not None else None
        out["car_drift20"] = d.excess(t, post, end)
        pe = ev.period_end if isinstance(ev.period_end, str) and ev.period_end else None
        eps = self._seasonal[(t, "eps_diluted")]
        i = _row_for_period(eps, pe)
        out["sue"] = float(np.clip(eps.loc[i, "sue"], -10, 10)) if i is not None and not _nan(eps.loc[i, "sue"]) else None
        rev = self._seasonal[(t, "revenue")]
        i = _row_for_period(rev, pe)
        if i is not None and i >= 1 and not _nan(rev.loc[i, "yoy"]) and not _nan(rev.loc[i - 1, "yoy"]):
            out["rev_surprise"] = float(rev.loc[i, "yoy"] - rev.loc[i - 1, "yoy"])
        else:
            out["rev_surprise"] = None
        c = self._consensus_for(t, ev.announce_date)
        if c is not None and not _nan(c["eps_estimate"]) and not _nan(c["eps_reported"]):
            out["beat_consensus"] = 1.0 if c["eps_reported"] > c["eps_estimate"] else 0.0
            out["surprise_pct"] = None if _nan(c["surprise_pct"]) else float(np.clip(c["surprise_pct"], -100, 100))
        else:
            out["beat_consensus"], out["surprise_pct"] = None, None
        return out


def historical_events(data: Data) -> list[Event]:
    ev = data.events[data.events["source"] == "sec_8k_202"].sort_values("announce_date")
    return [Event(r["ticker"], r["announce_date"], r["timing"], r["period_end"], r["source"], r["url"]) for _, r in ev.iterrows()]


def upcoming_events(data: Data, today: date, window_days: int) -> list[Event]:
    ev = data.events[(data.events["status"] == "scheduled")]
    ev = ev[(ev["announce_date"] >= today.isoformat()) & (ev["announce_date"] <= (today + timedelta(days=window_days)).isoformat())]
    reported = data.events[data.events["source"] == "sec_8k_202"]
    out = []
    for _, r in ev.sort_values("announce_date").drop_duplicates("ticker").iterrows():
        # skip if an actual 8-K release already exists within 5 days of the scheduled date
        rr = reported[(reported["ticker"] == r["ticker"])]
        if not rr.empty and (rr["announce_date"].map(lambda x: abs((date.fromisoformat(x) - date.fromisoformat(r["announce_date"])).days)) <= 5).any():
            continue
        out.append(Event(r["ticker"], r["announce_date"], r["timing"] or "UNKNOWN", r["period_end"], r["source"], r["url"]))
    return out
