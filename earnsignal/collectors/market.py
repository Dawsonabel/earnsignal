"""Prices, consensus EPS history, and upcoming earnings dates.

Source: Yahoo Finance via the open-source `yfinance` library (free, unofficial;
Yahoo's terms limit use to personal research). yfinance 1.7 scrapes Yahoo's
earnings calendar page (max 100 rows per ticker) and is rate-limited, so calls
are throttled. Stooq, the usual free fallback for prices, has required an API
key since ~March 2026; `collect_prices_stooq` works only if `stooq_api_key` is
set in settings.yaml (parameter name unverified).
"""
from __future__ import annotations

import io
import math
from datetime import date, datetime, timedelta

import pandas as pd

from .. import db
from ..http import Client

STOOQ_URL = "https://stooq.com/q/d/l/?s={sym}.us&i=d"
YAHOO_QUOTE_URL = "https://finance.yahoo.com/quote/{t}"


def _f(x):
    try:
        v = float(x)
        return None if math.isnan(v) else v
    except (TypeError, ValueError):
        return None


# --------------------------------------------------------------------- prices
def collect_prices(con, ticker: str, start: str) -> int:
    import yfinance as yf

    last = con.execute("SELECT MAX(date) FROM prices WHERE ticker=?", (ticker,)).fetchone()[0]
    # refetch a short overlap so dividend/split adjustments refresh
    fetch_start = start if not last else (date.fromisoformat(last) - timedelta(days=30)).isoformat()
    hist = yf.Ticker(ticker).history(start=fetch_start, auto_adjust=False, actions=False)
    if hist is None or hist.empty:
        if not last:
            raise RuntimeError(f"Yahoo returned no price history for {ticker}")
        return 0
    rows = rows_from_history(ticker, hist, "yahoo")
    if last and rows:
        # adjustment factor may have changed (new dividend/split): rewrite full history
        stored = con.execute("SELECT adj_close, close FROM prices WHERE ticker=? AND date=?", (ticker, rows[0]["date"])).fetchone()
        if stored and stored[0] and rows[0]["adj_close"] and abs(stored[0] / rows[0]["adj_close"] - 1) > 1e-4:
            hist = yf.Ticker(ticker).history(start=start, auto_adjust=False, actions=False)
            rows = rows_from_history(ticker, hist, "yahoo")
    return db.upsert(con, "prices", rows)


def rows_from_history(ticker: str, hist: pd.DataFrame, source: str) -> list[dict]:
    now = db.now_utc()
    adj_col = "Adj Close" if "Adj Close" in hist.columns else "Close"
    rows = []
    for ts, r in hist.iterrows():
        d = ts.date().isoformat() if hasattr(ts, "date") else str(ts)[:10]
        close = _f(r.get("Close"))
        if close is None:
            continue
        rows.append(
            {
                "ticker": ticker,
                "date": d,
                "open": _f(r.get("Open")),
                "high": _f(r.get("High")),
                "low": _f(r.get("Low")),
                "close": close,
                "adj_close": _f(r.get(adj_col)),
                "volume": _f(r.get("Volume")),
                "source": source,
                "retrieved_at": now,
            }
        )
    return rows


def collect_prices_stooq(con, client: Client, ticker: str, api_key: str) -> int:
    r = client.get(STOOQ_URL.format(sym=ticker.lower().replace(".", "-")) + f"&apikey={api_key}")
    r.raise_for_status()
    if not r.text.startswith("Date"):
        raise RuntimeError(f"Stooq returned no CSV for {ticker}: {r.text[:80]!r}")
    hist = pd.read_csv(io.StringIO(r.text), parse_dates=["Date"]).set_index("Date")
    # Stooq prices are split/dividend adjusted; store them as both close and adj_close
    hist["Adj Close"] = hist["Close"]
    return db.upsert(con, "prices", rows_from_history(ticker, hist, "stooq"))


TIINGO_URL = "https://api.tiingo.com/tiingo/daily/{t}/prices?startDate={start}&token={key}"


def collect_prices_tiingo(con, client: Client, ticker: str, start: str, api_key: str) -> int:
    """Optional fallback (free Tiingo account; rate-limited). Uses adjClose for returns."""
    r = client.get(TIINGO_URL.format(t=ticker.lower().replace(".", "-"), start=start, key=api_key))
    r.raise_for_status()
    now = db.now_utc()
    rows = []
    for x in r.json():
        if _f(x.get("close")) is None:
            continue
        rows.append({"ticker": ticker, "date": str(x["date"])[:10], "open": _f(x.get("open")), "high": _f(x.get("high")),
                     "low": _f(x.get("low")), "close": _f(x.get("close")), "adj_close": _f(x.get("adjClose")),
                     "volume": _f(x.get("volume")), "source": "tiingo", "retrieved_at": now})
    if not rows:
        raise RuntimeError(f"Tiingo returned no prices for {ticker}")
    return db.upsert(con, "prices", rows)


# ------------------------------------------------- consensus + upcoming dates
def collect_earnings_dates(con, ticker: str, limit: int = 100) -> tuple[int, int]:
    """Store Yahoo's EPS estimate/actual history and upcoming scheduled dates.

    Returns (n_consensus_rows, n_upcoming_events).
    """
    import yfinance as yf

    t = yf.Ticker(ticker)
    ed = t.get_earnings_dates(limit=limit)
    now = db.now_utc()
    cons, upcoming = [], []
    today = date.today()
    url = YAHOO_QUOTE_URL.format(t=ticker)
    if ed is not None and not ed.empty:
        for ts, r in ed.iterrows():
            ts = pd.Timestamp(ts)
            if ts.tzinfo is None:
                ts = ts.tz_localize("America/New_York")
            ts_ny = ts.tz_convert("America/New_York")
            d = ts_ny.date()
            est = _f(r.get("EPS Estimate"))
            rep = _f(r.get("Reported EPS"))
            sur = _f(r.get("Surprise(%)"))
            if rep is not None:
                cons.append(
                    {
                        "ticker": ticker,
                        "announce_ts": ts_ny.isoformat(),
                        "announce_date": d.isoformat(),
                        "eps_estimate": est,
                        "eps_reported": rep,
                        "surprise_pct": sur,
                        "source": "yahoo",
                        "source_url": url,
                        "retrieved_at": now,
                    }
                )
            elif d >= today:
                hm = ts_ny.hour * 60 + ts_ny.minute
                timing = "UNKNOWN" if hm == 0 else ("BMO" if hm < 570 else ("AMC" if hm >= 960 else "DURING"))
                upcoming.append(
                    {
                        "ticker": ticker,
                        "announce_date": d.isoformat(),
                        "announce_ts": ts_ny.isoformat(),
                        "timing": timing,
                        "period_end": None,
                        "source": "yahoo",
                        "accession": None,
                        "url": url,
                        "status": "scheduled",
                        "retrieved_at": now,
                    }
                )
    # Ticker.calendar is a second opinion on the next date
    if not upcoming:
        try:
            cal = t.calendar or {}
            dates = cal.get("Earnings Date") if isinstance(cal, dict) else None
            for d in dates or []:
                d = pd.Timestamp(d).date()
                if d >= today:
                    upcoming.append(
                        {
                            "ticker": ticker, "announce_date": d.isoformat(), "announce_ts": None,
                            "timing": "UNKNOWN", "period_end": None, "source": "yahoo",
                            "accession": None, "url": url, "status": "scheduled", "retrieved_at": now,
                        }
                    )
                    break
        except Exception:
            pass
    con.execute("DELETE FROM earnings_events WHERE ticker=? AND source='yahoo' AND status='scheduled'", (ticker,))
    return db.upsert(con, "consensus", cons), db.upsert(con, "earnings_events", upcoming)
