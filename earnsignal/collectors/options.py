"""Option-chain snapshots ahead of earnings (yfinance; free, current quotes only).

Historical point-in-time option quotes are not available for free, so this
collector builds our own record: for each upcoming announcement it stores
near-the-money quotes for the first expiry after the announcement and the
market-implied move (ATM straddle mid / underlying). Over time this becomes a
real dataset for testing the options strategy.
"""
from __future__ import annotations

import math
import re
from datetime import date, datetime, timezone

import pandas as pd

from .. import db


def _num(x):
    try:
        v = float(x)
        return None if math.isnan(v) else v
    except (TypeError, ValueError):
        return None


def first_expiry_after(expiries: list[str], reaction_date: str) -> str | None:
    after = sorted(e for e in expiries if e >= reaction_date)
    return after[0] if after else None


def implied_move(calls: pd.DataFrame, puts: pd.DataFrame, spot: float) -> dict | None:
    """ATM straddle from bid/ask mids. Returns None if quotes are unusable."""
    if calls.empty or puts.empty or not spot:
        return None
    strikes = sorted(set(calls["strike"]).intersection(set(puts["strike"])))
    if not strikes:
        return None
    k = min(strikes, key=lambda s: abs(s - spot))
    c = calls[calls["strike"] == k].iloc[0]
    p = puts[puts["strike"] == k].iloc[0]
    cb, ca, pb, pa = (_num(c.get("bid")), _num(c.get("ask")), _num(p.get("bid")), _num(p.get("ask")))
    if None in (cb, ca, pb, pa) or ca <= 0 or pa <= 0:
        return None
    mid = (cb + ca) / 2 + (pb + pa) / 2
    spread = (ca - cb) + (pa - pb)
    iv = [_num(c.get("impliedVolatility")), _num(p.get("impliedVolatility"))]
    iv = [v for v in iv if v]
    return {
        "strike": k,
        "straddle_mid": mid,
        "implied_move": mid / spot,
        "atm_iv": sum(iv) / len(iv) if iv else None,
        "spread_pct": spread / mid if mid else None,
    }


CBOE_URL = "https://cdn-api.cboe.com/api/global/delayed_quotes/options/{sym}.json"
_OCC = re.compile(r"^(?P<root>[A-Z.]+)(?P<yy>\d{2})(?P<mm>\d{2})(?P<dd>\d{2})(?P<cp>[CP])(?P<strike>\d{8})$")


def parse_cboe(js: dict) -> pd.DataFrame:
    """Cboe delayed-quote JSON (free, ~15-min delayed, current snapshot) -> tidy chain."""
    rows = []
    for o in (js.get("data") or {}).get("options", []):
        m = _OCC.match(str(o.get("option", "")))
        if not m:
            continue
        rows.append({
            "expiry": f"20{m['yy']}-{m['mm']}-{m['dd']}", "type": "call" if m["cp"] == "C" else "put",
            "strike": int(m["strike"]) / 1000, "bid": _num(o.get("bid")), "ask": _num(o.get("ask")),
            "lastPrice": _num(o.get("last_trade_price")), "impliedVolatility": _num(o.get("iv")),
            "openInterest": _num(o.get("open_interest")), "volume": _num(o.get("volume")),
        })
    return pd.DataFrame(rows)


def _chain_yahoo(ticker: str, reaction_date: str):
    import yfinance as yf

    t = yf.Ticker(ticker)
    exp = first_expiry_after(list(t.options or []), reaction_date)
    if not exp:
        raise RuntimeError(f"No listed expiry on/after {reaction_date}")
    chain = t.option_chain(exp)
    hist = t.history(period="5d")
    spot = float(hist["Close"].iloc[-1]) if not hist.empty else None
    return exp, chain.calls, chain.puts, spot, "yahoo"


def _chain_cboe(client, ticker: str, reaction_date: str, spot: float | None):
    js = client.get_json(CBOE_URL.format(sym=ticker.upper()))
    df = parse_cboe(js)
    if df.empty:
        raise RuntimeError("Cboe returned no options")
    exp = first_expiry_after(sorted(df["expiry"].unique()), reaction_date)
    if not exp:
        raise RuntimeError(f"No Cboe expiry on/after {reaction_date}")
    d = df[df["expiry"] == exp]
    spot = spot or _num((js.get("data") or {}).get("current_price"))
    return exp, d[d["type"] == "call"], d[d["type"] == "put"], spot, "cboe"


def collect(con, ticker: str, announce_date: str, reaction_date: str, band: float = 0.25, client=None) -> int:
    try:
        exp, calls, puts, spot, src = _chain_yahoo(ticker, reaction_date)
    except Exception:
        if client is None:
            raise
        last = con.execute("SELECT close FROM prices WHERE ticker=? ORDER BY date DESC LIMIT 1", (ticker,)).fetchone()
        exp, calls, puts, spot, src = _chain_cboe(client, ticker, reaction_date, last[0] if last else None)
    if not spot:
        raise RuntimeError("No underlying price")

    class _C:  # keep the downstream code shape
        pass

    chain = _C()
    chain.calls, chain.puts = calls, puts
    snap_ts = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    rows = []
    for typ, frame in (("call", chain.calls), ("put", chain.puts)):
        near = frame[(frame["strike"] >= spot * (1 - band)) & (frame["strike"] <= spot * (1 + band))]
        for _, r in near.iterrows():
            rows.append(
                {
                    "ticker": ticker, "snapshot_ts": snap_ts, "expiry": exp, "strike": float(r["strike"]),
                    "option_type": typ, "bid": _num(r.get("bid")), "ask": _num(r.get("ask")),
                    "last": _num(r.get("lastPrice")), "iv": _num(r.get("impliedVolatility")),
                    "open_interest": _num(r.get("openInterest")), "volume": _num(r.get("volume")),
                    "underlying": spot, "source": src,
                }
            )
    db.upsert(con, "option_quotes", rows)
    im = implied_move(chain.calls, chain.puts, spot)
    if im:
        db.upsert(
            con,
            "implied_moves",
            [
                {
                    "ticker": ticker, "snapshot_date": date.today().isoformat(), "announce_date": announce_date,
                    "expiry": exp, "underlying": spot, "straddle_mid": im["straddle_mid"],
                    "implied_move": im["implied_move"], "atm_iv": im["atm_iv"], "spread_pct": im["spread_pct"],
                    "source": src,
                }
            ],
        )
    return len(rows)
