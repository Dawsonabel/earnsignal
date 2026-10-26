"""Interpretable scoring.

1. Each feature is standardized against its OWN PAST distribution (only events
   whose information cutoff precedes the event being scored): robust z =
   (x - median) / (1.4826 * MAD), clipped to [-3, 3].
2. composite = weighted mean of sign * z over the features available, where
   sign is the pre-registered direction from config/model.yaml.
3. opportunity score (0-100) = percentile of the composite within the
   historical composite distribution. It is a RANK, not a probability.
4. A probability of beating consensus is reported only when a calibration
   model has passed out-of-sample validation (see backtest.engine).
"""
from __future__ import annotations

import math
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

from .. import config

MIN_HISTORY = 20


def model_config() -> dict:
    with open(config.CONFIG_DIR / "model.yaml") as f:
        return yaml.safe_load(f)


def signs() -> dict[str, int]:
    return {k: int(v["sign"]) for k, v in model_config()["features"].items()}


def robust_z(x: float, hist: np.ndarray) -> float | None:
    hist = hist[~np.isnan(hist)]
    if x is None or (isinstance(x, float) and math.isnan(x)) or len(hist) < MIN_HISTORY:
        return None
    med = np.median(hist)
    mad = np.median(np.abs(hist - med)) * 1.4826
    if mad <= 0:
        sd = hist.std()
        if sd <= 0:
            return None
        mad = sd
    return float(np.clip((x - med) / mad, -3, 3))


def standardize(panel: pd.DataFrame, features: list[str], ref: pd.DataFrame | None = None) -> pd.DataFrame:
    """Point-in-time z-scores. `panel` rows need columns `cutoff` + features.
    `ref` (default: panel itself) supplies the history; a row only sees ref
    rows with cutoff strictly earlier than its own."""
    ref = panel if ref is None else ref
    ref = ref.sort_values("cutoff")
    ref_cut = ref["cutoff"].values
    out = pd.DataFrame(index=panel.index)
    for f in features:
        if f not in panel.columns:
            out[f"z_{f}"] = np.nan
            continue
        vals = ref[f].astype(float).values if f in ref.columns else np.full(len(ref), np.nan)
        zs = []
        for cut, x in zip(panel["cutoff"].values, panel[f].astype(float).values):
            n = np.searchsorted(ref_cut, cut, side="left")
            zs.append(robust_z(x, vals[:n]))
        out[f"z_{f}"] = pd.array(zs, dtype="Float64").astype(float)
    return out


def composite(z: pd.DataFrame, features: list[str], weights: dict[str, float] | None = None) -> pd.DataFrame:
    sg = signs()
    w = weights or {f: 1.0 for f in features}
    num = pd.Series(0.0, index=z.index)
    den = pd.Series(0.0, index=z.index)
    n = pd.Series(0, index=z.index)
    agree_pos = pd.Series(0, index=z.index)
    agree_neg = pd.Series(0, index=z.index)
    for f in features:
        col = z.get(f"z_{f}")
        if col is None or w.get(f, 0) == 0:
            continue
        v = sg.get(f, 1) * col
        m = v.notna()
        num[m] += w[f] * v[m]
        den[m] += abs(w[f])
        n[m] += 1
        agree_pos[m & (v > 0.25)] += 1
        agree_neg[m & (v < -0.25)] += 1
    comp = num / den.replace(0, np.nan)
    return pd.DataFrame({"composite": comp, "n_signals": n, "n_pos": agree_pos, "n_neg": agree_neg})


def percentile_score(values: pd.Series, cutoffs: pd.Series, ref_values: pd.Series, ref_cutoffs: pd.Series) -> pd.Series:
    """Percentile of each composite among strictly-earlier reference composites."""
    order = np.argsort(ref_cutoffs.values)
    rc, rv = ref_cutoffs.values[order], ref_values.values[order]
    out = []
    for v, c in zip(values.values, cutoffs.values):
        if v is None or (isinstance(v, float) and math.isnan(v)):
            out.append(np.nan)
            continue
        n = np.searchsorted(rc, c, side="left")
        hist = rv[:n]
        hist = hist[~np.isnan(hist)]
        if len(hist) < MIN_HISTORY:
            out.append(np.nan)
            continue
        out.append(100.0 * (np.sum(hist < v) + 0.5 * np.sum(hist == v)) / len(hist))
    return pd.Series(out, index=values.index)


def label(score: float, bands: dict) -> str:
    if score is None or (isinstance(score, float) and math.isnan(score)):
        return "insufficient data"
    if score >= bands.get("bullish", 70):
        return "bullish"
    if score <= bands.get("bearish", 30):
        return "bearish"
    return "neutral"


def confidence(n_signals: int, n_applicable: int, n_pos: int, n_neg: int, comp: float, validated: bool) -> tuple[float, str]:
    """0-100 confidence = coverage x agreement x validation discount. Transparent, not a probability."""
    if n_signals == 0 or comp is None or (isinstance(comp, float) and math.isnan(comp)):
        return 0.0, "none"
    coverage = n_signals / max(n_applicable, 1)
    agree = (n_pos if comp > 0 else n_neg) / n_signals
    c = 100 * coverage * (0.4 + 0.6 * agree) * (1.0 if validated else 0.5)
    lab = "high" if c >= 60 else ("medium" if c >= 35 else "low")
    return round(c, 1), lab
