"""HYPOTHETICAL scenario analysis of buying calls into earnings.

THIS IS NOT A BACKTEST. Free historical option quotes do not exist, so option
prices here are MODELLED with Black-Scholes from assumptions:
  * implied event move = k x the stock's trailing mean absolute announcement
    move (k is a scenario parameter: k > 1 means options price in more than
    the stock has historically moved);
  * pre-event diffusion vol = trailing 60-day realized vol;
  * after the release, implied vol "crushes" back to that diffusion vol;
  * entry at ask, exit at bid (relative spread h), $0.65/contract commission,
    whole contracts only.
Underlying moves, however, are the REAL historical announcement moves.

Once the options collector has recorded real pre-earnings quotes
(implied_moves table), `observed_implied_vs_realized` compares actual implied
moves with what happened.
"""
from __future__ import annotations

import math
from datetime import date

import numpy as np
import pandas as pd
from scipy.stats import norm

from .. import db
from ..features.panel import Data

COMMISSION = 0.65
LABEL = "HYPOTHETICAL SCENARIO - modelled option prices, not observed quotes"


def bs_call(S: float, K: float, T: float, sigma: float, r: float = 0.04) -> float:
    if T <= 1e-6 or sigma <= 0:
        return max(S - K, 0.0)
    d1 = (math.log(S / K) + (r + 0.5 * sigma**2) * T) / (sigma * math.sqrt(T))
    d2 = d1 - sigma * math.sqrt(T)
    return S * norm.cdf(d1) - K * math.exp(-r * T) * norm.cdf(d2)


def event_trades(con, events: pd.DataFrame, k: float = 1.0, spread: float = 0.08, moneyness: str = "atm",
                 days_to_expiry: int = 3, history: pd.DataFrame | None = None, data: Data | None = None) -> pd.DataFrame:
    """events: rows with ticker, announce_date, pre, post, ret_ann (and optional composite).
    history: all events (for each stock's trailing announcement moves; defaults to `events`).
    Returns one modelled trade per event with per-dollar return."""
    d = data or Data(con)
    history = events if history is None else history
    out = []
    for _, e in events.iterrows():
        t = e["ticker"]
        if t not in d.adj.columns or pd.isna(e.get("pre")) or pd.isna(e.get("post")):
            continue
        pre, post = pd.Timestamp(e["pre"]), pd.Timestamp(e["post"])
        adj = d.adj[t].dropna()
        hist = adj.loc[:pre]
        if len(hist) < 80:
            continue
        lr = np.log(hist).diff().dropna().iloc[-60:]
        sigma_base = float(lr.std() * math.sqrt(252))
        # trailing absolute announcement moves, known before this event
        prior = history[(history["ticker"] == t) & (pd.to_datetime(history["post"]) < pre)].sort_values("announce_date").tail(8)
        moves = prior["ret_ann"].abs().dropna()
        if len(moves) < 4 or sigma_base <= 0:
            continue
        implied_move = k * float(moves.mean())
        S0 = float(d.close[t].loc[pre]) if t in d.close.columns else float(adj.loc[pre])
        S1 = S0 * float(adj.loc[post] / adj.loc[pre])
        T = days_to_expiry / 252
        jump_sd = implied_move * math.sqrt(math.pi / 2)  # E|N(0,s)| = s*sqrt(2/pi)
        sigma_entry = math.sqrt(sigma_base**2 + jump_sd**2 / T)
        K = S0 if moneyness == "atm" else S0 * (1 + implied_move)
        mid0 = bs_call(S0, K, T, sigma_entry)
        T1 = max(T - 1 / 252, 0)
        mid1 = bs_call(S1, K, T1, sigma_base)
        ask0 = mid0 * (1 + spread / 2)
        bid1 = max(mid1 * (1 - spread / 2), 0)
        out.append({
            "ticker": t, "announce_date": e["announce_date"], "pre": e["pre"], "S0": S0, "S1": S1,
            "stock_move": S1 / S0 - 1, "implied_move_assumed": implied_move, "sigma_base": sigma_base,
            "strike": K, "premium_ask": ask0, "exit_bid": bid1,
            "ret_per_dollar": (bid1 - ask0) / ask0 if ask0 > 0 else np.nan,
            "score": e.get("composite", np.nan),
        })
    return pd.DataFrame(out)


def simulate_account(trades: pd.DataFrame, start: float = 10_000, sizing: str = "fixed", bet: float = 2_500,
                     fraction: float = 0.05, ruin_level: float = 500) -> dict:
    eq = start
    curve = []
    for _, tr in trades.sort_values("announce_date").iterrows():
        if eq < ruin_level:
            break
        stake = min(bet, eq) if sizing == "fixed" else eq * fraction
        per_contract = tr["premium_ask"] * 100 + COMMISSION
        n = int(stake // per_contract) if per_contract > 0 else 0
        if n == 0:
            curve.append({"date": tr["announce_date"], "equity": eq, "pnl": 0.0})
            continue
        pnl = n * 100 * (tr["exit_bid"] - tr["premium_ask"]) - 2 * n * COMMISSION
        eq += pnl
        curve.append({"date": tr["announce_date"], "equity": eq, "pnl": pnl})
    c = pd.DataFrame(curve)
    if c.empty:
        return {"n_trades": 0}
    peak = c["equity"].cummax()
    dd = float(((c["equity"] - peak) / peak).min())
    return {"n_trades": int(len(c)), "final_equity": float(c["equity"].iloc[-1]), "max_drawdown": dd,
            "ruined": bool(c["equity"].iloc[-1] < ruin_level), "win_rate": float((c["pnl"] > 0).mean()),
            "curve": c.to_dict("records")}


def monte_carlo_ruin(rets: np.ndarray, n_trades: int = 100, n_sims: int = 5000, start: float = 10_000,
                     sizing: str = "fixed", bet: float = 2_500, fraction: float = 0.05, ruin_level: float = 500,
                     seed: int = 0) -> dict:
    """Bootstrap per-dollar trade returns into many possible trade sequences."""
    rets = rets[~np.isnan(rets)]
    if len(rets) < 20:
        return {"n_source_trades": int(len(rets))}
    rng = np.random.default_rng(seed)
    finals, ruined, mdds = [], 0, []
    for _ in range(n_sims):
        eq, peak, mdd = start, start, 0.0
        for r in rng.choice(rets, size=n_trades, replace=True):
            if eq < ruin_level:
                break
            stake = min(bet, eq) if sizing == "fixed" else eq * fraction
            eq += stake * r
            peak = max(peak, eq)
            mdd = min(mdd, (eq - peak) / peak)
        finals.append(eq)
        mdds.append(mdd)
        ruined += eq < ruin_level
    finals = np.array(finals)
    return {"n_source_trades": int(len(rets)), "p_ruin": ruined / n_sims, "median_final": float(np.median(finals)),
            "p10_final": float(np.percentile(finals, 10)), "p90_final": float(np.percentile(finals, 90)),
            "median_max_drawdown": float(np.median(mdds)), "mean_ret_per_dollar": float(rets.mean())}


def run(con, panel: pd.DataFrame) -> dict:
    """panel: all events of the latest backtest (with split + composite). Trades are
    taken only on test-split events so the score used for selection is out-of-sample."""
    res = {"label": LABEL, "scenarios": []}
    d = Data(con)
    test = panel[panel["split"] == "test"] if "split" in panel.columns else panel
    for k in (0.9, 1.0, 1.2):
        for money in ("atm", "otm"):
            trades = event_trades(con, test, k=k, moneyness=money, history=panel, data=d)
            if trades.empty:
                continue
            top = trades[trades["score"] >= trades["score"].quantile(0.8)] if trades["score"].notna().sum() > 20 else trades.iloc[0:0]
            for subset_name, sub in (("all events", trades), ("top-20% composite (out-of-sample)", top)):
                if len(sub) < 10:
                    continue
                rets = sub["ret_per_dollar"].values.astype(float)
                res["scenarios"].append({
                    "k_implied_vs_hist": k, "moneyness": money, "subset": subset_name, "n": int(len(sub)),
                    "mean_ret_per_dollar": float(np.nanmean(rets)), "median_ret_per_dollar": float(np.nanmedian(rets)),
                    "win_rate": float(np.nanmean(rets > 0)),
                    "fixed_2500": {k2: v for k2, v in simulate_account(sub, sizing="fixed").items() if k2 != "curve"},
                    "fractional_5pct": {k2: v for k2, v in simulate_account(sub, sizing="fraction", fraction=0.05).items() if k2 != "curve"},
                    "mc_fixed_2500": monte_carlo_ruin(rets, sizing="fixed"),
                    "mc_fractional_5pct": monte_carlo_ruin(rets, sizing="fraction", fraction=0.05),
                    "mc_fractional_2pct": monte_carlo_ruin(rets, sizing="fraction", fraction=0.02),
                })
    res["observed"] = observed_implied_vs_realized(con)
    return res


def observed_implied_vs_realized(con) -> dict:
    """Uses REAL collected option quotes (prospective). Empty until data accrues."""
    im = db.df(con, "SELECT * FROM implied_moves")
    if im.empty:
        return {"n": 0, "note": "No option snapshots collected yet."}
    d = Data(con)
    ev = d.events[d.events["source"] == "sec_8k_202"]
    rows = []
    last = im.sort_values("snapshot_date").groupby(["ticker", "announce_date"]).tail(1)
    for _, r in last.iterrows():
        e = ev[(ev["ticker"] == r["ticker"]) & (ev["announce_date"].map(lambda x: abs((date.fromisoformat(x) - date.fromisoformat(r["announce_date"])).days) <= 3))]
        if e.empty:
            continue
        pre, post = d.window(e.iloc[0]["announce_date"], e.iloc[0]["timing"])
        realized = d.ret(r["ticker"], pre, post)
        if realized is None:
            continue
        rows.append({"ticker": r["ticker"], "announce_date": r["announce_date"], "implied_move": r["implied_move"],
                     "realized_abs_move": abs(realized), "spread_pct": r["spread_pct"]})
    if not rows:
        return {"n": 0, "note": "Option snapshots exist but no matching reported events yet."}
    x = pd.DataFrame(rows)
    return {"n": int(len(x)), "mean_implied": float(x["implied_move"].mean()),
            "mean_realized_abs": float(x["realized_abs_move"].mean()),
            "share_realized_exceeds_implied": float((x["realized_abs_move"] > x["implied_move"]).mean()),
            "rows": x.to_dict("records")}
