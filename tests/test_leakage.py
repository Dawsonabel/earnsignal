"""Look-ahead tests: scrambling every datum dated at/after an event's cutoff must
not change that event's features. Runs on SYNTHETIC data (tests/synthetic.py)."""
import math
import shutil

import numpy as np
import pandas as pd
import pytest

from earnsignal import db
from earnsignal.features.panel import Data, FeatureBuilder, historical_events
from earnsignal.model import scoring
from tests import synthetic


@pytest.fixture(scope="module")
def syn_db(tmp_path_factory):
    p = tmp_path_factory.mktemp("syn") / "syn.db"
    synthetic.build(p)
    return p


def _scramble_future(path, cutoff: str, pre: str, keep_event: tuple, seed: int = 1):
    rng = np.random.default_rng(seed)
    con = db.connect(path)
    f = lambda: float(rng.uniform(0.5, 1.5))  # noqa: E731
    for (t, d) in con.execute("SELECT ticker, date FROM prices WHERE date > ?", (pre,)).fetchall():
        k = f()
        con.execute("UPDATE prices SET adj_close=adj_close*?, close=close*? WHERE ticker=? AND date=?", (k, k, t, d))
    con.execute("UPDATE observations SET value = value * ? WHERE obs_date >= ?", (f(), cutoff))
    for row in con.execute("SELECT rowid FROM fundamentals WHERE filed_date >= ?", (cutoff,)).fetchall():
        con.execute("UPDATE fundamentals SET value = value * ? WHERE rowid=?", (f(), row[0]))
    con.execute("UPDATE consensus SET eps_reported = eps_reported + 5, surprise_pct = -surprise_pct WHERE announce_date >= ?", (cutoff,))
    con.execute("UPDATE insider_transactions SET trans_code='P', acq_disp='A', value=value*3 WHERE filing_date >= ?", (cutoff,))
    con.execute("DELETE FROM earnings_events WHERE announce_date > ? AND NOT (ticker=? AND announce_date=?)", (cutoff, *keep_event))
    con.commit()
    con.close()


def test_features_do_not_see_the_future(syn_db, tmp_path):
    con = db.connect(syn_db)
    data = Data(con)
    fb = FeatureBuilder(data)
    events = historical_events(data)
    con.close()
    picks = [events[i] for i in np.linspace(len(events) * 0.35, len(events) * 0.9, 6).astype(int)]
    checked = 0
    for ev in picks:
        base, _, meta = fb.compute(ev)
        if not base:
            continue
        copy = tmp_path / f"copy_{checked}.db"
        shutil.copy(syn_db, copy)
        for ext in ("-wal", "-shm"):
            src = syn_db.parent / (syn_db.name + ext)
            if src.exists():
                shutil.copy(src, tmp_path / (copy.name + ext))
        _scramble_future(copy, meta["info_cutoff"], meta["pre"], (ev.ticker, ev.announce_date))
        c2 = db.connect(copy)
        fb2 = FeatureBuilder(Data(c2))
        again, _, _ = fb2.compute(ev)
        c2.close()
        assert set(base) == set(again), (ev, set(base) ^ set(again))
        for k in base:
            assert math.isclose(base[k], again[k], rel_tol=1e-9, abs_tol=1e-12), (ev.ticker, ev.announce_date, k, base[k], again[k])
        checked += 1
    assert checked >= 4


def test_standardize_uses_only_earlier_rows():
    rng = np.random.default_rng(0)
    df = pd.DataFrame({"cutoff": pd.date_range("2020-01-01", periods=80).strftime("%Y-%m-%d"), "x": rng.normal(size=80)})
    z1 = scoring.standardize(df, ["x"])["z_x"]
    df2 = df.copy()
    df2.loc[60:, "x"] = 1e6  # change the future
    z2 = scoring.standardize(df2, ["x"])["z_x"]
    assert z1.iloc[:60].equals(z2.iloc[:60])  # earlier rows never see later rows
    assert z1.iloc[:20].isna().all()  # not enough history yet


def test_percentile_score_is_point_in_time():
    ref = pd.Series(np.arange(100, dtype=float))
    cut = pd.Series(pd.date_range("2020-01-01", periods=100).strftime("%Y-%m-%d"))
    s = scoring.percentile_score(pd.Series([50.0]), pd.Series(["2020-03-01"]), ref, cut)
    # only the first 60 reference rows (Jan 1 - Feb 29) are earlier than 2020-03-01
    assert s.iloc[0] == pytest.approx(100 * 50.5 / 60, rel=1e-6) or s.iloc[0] == pytest.approx(100 * 50 / 60)
