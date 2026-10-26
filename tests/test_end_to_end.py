"""End-to-end on SYNTHETIC data: backtest finds the planted signal, scoring
produces rankings, options analysis stays labelled hypothetical, and every
dashboard page renders without errors."""
import json
import os
from datetime import date

import pytest

from earnsignal import db, pipeline
from tests import synthetic

APP = __import__("pathlib").Path(__file__).resolve().parent.parent / "dashboard" / "app.py"
PAGES = ["Rankings", "Company detail", "Backtest", "Research log", "Data health"]


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    p = tmp_path_factory.mktemp("e2e") / "e2e.db"
    info = synthetic.build(p)
    os.environ["EARNSIGNAL_DB"] = str(p)
    with db.session(p) as con:
        db.log(con, "ops", "synthetic test database", "SYNTHETIC", "test")
        summary = pipeline.cmd_backtest(con)
        scores = pipeline.cmd_score(con, today=info["today"])
    yield p, summary, scores
    os.environ.pop("EARNSIGNAL_DB", None)


def test_backtest_recovers_planted_signal_and_not_noise(built):
    _, s, _ = built
    assert s["status"] == "ok"
    sig = {r["feature"]: r for r in s["signals"]}
    assert sig["wiki_yoy_rel"]["test_ic_car_ann"] > 0.15 and sig["wiki_yoy_rel"]["test_p_car_ann"] < 0.01
    # prospective signals have no history and must not be "evaluated"
    assert sig["jobs_growth_28d"]["n"] == 0
    assert s["n_train"] > s["n_test"] > 100
    assert s["test_eval"]["benchmark: random"]["p_car_ann"] > 0.01


def test_options_analysis_is_labelled_hypothetical(built):
    _, s, _ = built
    opt = s["options_scenario"]
    assert "HYPOTHETICAL" in opt["label"]
    assert opt["scenarios"], "expected modelled scenarios"
    for sc in opt["scenarios"]:
        assert 0 <= sc["mc_fixed_2500"]["p_ruin"] <= 1


def test_scores_are_percentiles_with_explanations(built):
    _, _, scores = built
    assert len(scores) == 12
    assert scores["opportunity_score"].between(0, 100).all()
    ex = json.loads(scores.iloc[0]["explanation"])
    assert {"bullish", "bearish", "missing", "freshness", "meta"} <= set(ex)


def test_probability_withheld_without_approved_calibration(tmp_path):
    p = tmp_path / "noprob.db"
    info = synthetic.build(p, signal_beta=0.0, seed=3)
    with db.session(p) as con:
        pipeline.cmd_backtest(con)
        con.execute("UPDATE model_registry SET approved=0, params=? WHERE name='beat_probability'", (json.dumps({"reason": "test: not approved"}),))
        con.commit()
        sc = pipeline.cmd_score(con, today=info["today"])
    assert sc["prob_beat"].isna().all()
    assert sc["prob_basis"].str.startswith("not supportable").all()


def test_dashboard_pages_render(built):
    from streamlit.testing.v1 import AppTest

    at = AppTest.from_file(str(APP), default_timeout=120)
    at.run()
    assert not at.exception, at.exception
    for page in PAGES:
        at.sidebar.radio[0].set_value(page).run()
        assert not at.exception, (page, at.exception)


def test_dashboard_empty_database(tmp_path):
    from streamlit.testing.v1 import AppTest

    os.environ["EARNSIGNAL_DB"] = str(tmp_path / "empty.db")
    try:
        at = AppTest.from_file(str(APP), default_timeout=60)
        at.run()
        for page in PAGES:
            at.sidebar.radio[0].set_value(page).run()
            assert not at.exception, (page, at.exception)
    finally:
        os.environ.pop("EARNSIGNAL_DB", None)
