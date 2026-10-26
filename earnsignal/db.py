"""SQLite storage. Every observation keeps its source URL and the time it was fetched."""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

import pandas as pd

from . import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS companies (
    ticker TEXT PRIMARY KEY,
    name TEXT, cik INTEGER, segment TEXT, fiscal_year_end INTEGER,
    ats_provider TEXT, ats_token TEXT, wikipedia TEXT, ios_app_id INTEGER,
    notes TEXT, tier TEXT, updated_at TEXT
);

-- Raw SEC filings index (8-K, 10-Q, 10-K, 4 ...)
CREATE TABLE IF NOT EXISTS filings (
    accession TEXT PRIMARY KEY,
    ticker TEXT, cik INTEGER, form TEXT, filing_date TEXT,
    acceptance_datetime TEXT, report_date TEXT, items TEXT,
    primary_doc TEXT, url TEXT, retrieved_at TEXT
);
CREATE INDEX IF NOT EXISTS ix_filings_ticker ON filings(ticker, form, filing_date);

-- XBRL facts. Every reported version is kept; point-in-time queries pick the
-- first-filed value so later restatements never leak into history.
CREATE TABLE IF NOT EXISTS fundamentals (
    ticker TEXT, metric TEXT, tag TEXT,
    period_start TEXT, period_end TEXT, duration_days INTEGER,
    fiscal_year INTEGER, fiscal_period TEXT, form TEXT, accession TEXT,
    filed_date TEXT, value REAL, derived INTEGER DEFAULT 0,
    source_url TEXT, retrieved_at TEXT,
    PRIMARY KEY (ticker, metric, period_start, period_end, accession, tag)
);

-- Earnings announcements. source = 'sec_8k_202' (actual, from 8-K Item 2.02)
-- or 'yahoo' (scheduled/estimated date, upcoming only).
CREATE TABLE IF NOT EXISTS earnings_events (
    ticker TEXT, announce_date TEXT, announce_ts TEXT, timing TEXT,
    period_end TEXT, source TEXT, accession TEXT, url TEXT,
    status TEXT, retrieved_at TEXT,
    PRIMARY KEY (ticker, announce_date, source)
);

-- Consensus EPS estimate vs reported (Yahoo earnings_dates)
CREATE TABLE IF NOT EXISTS consensus (
    ticker TEXT, announce_ts TEXT, announce_date TEXT,
    eps_estimate REAL, eps_reported REAL, surprise_pct REAL,
    source TEXT, source_url TEXT, retrieved_at TEXT,
    PRIMARY KEY (ticker, announce_date, source)
);

CREATE TABLE IF NOT EXISTS prices (
    ticker TEXT, date TEXT, open REAL, high REAL, low REAL, close REAL,
    adj_close REAL, volume REAL, source TEXT, retrieved_at TEXT,
    PRIMARY KEY (ticker, date)
);

-- Generic alternative-data time series.
--   wikipedia  pageviews:<Article>      daily user pageviews
--   jobs       total | cat:<category>   open postings at snapshot
--   appstore   rating_count | avg_rating | rank:<chart>
CREATE TABLE IF NOT EXISTS observations (
    source TEXT, ticker TEXT, series TEXT, obs_date TEXT, value REAL,
    meta TEXT, source_url TEXT, observed_at TEXT,
    PRIMARY KEY (source, ticker, series, obs_date)
);
CREATE INDEX IF NOT EXISTS ix_obs_ticker ON observations(ticker, source, series, obs_date);

CREATE TABLE IF NOT EXISTS job_postings (
    ticker TEXT, provider TEXT, job_id TEXT, title TEXT, department TEXT,
    category TEXT, location TEXT, src_created_at TEXT, url TEXT,
    first_seen TEXT, last_seen TEXT,
    PRIMARY KEY (ticker, provider, job_id)
);

CREATE TABLE IF NOT EXISTS insider_transactions (
    accession TEXT, seq TEXT, ticker TEXT, issuer_cik INTEGER,
    filing_date TEXT, trans_date TEXT, owner_name TEXT, owner_relationship TEXT,
    owner_title TEXT, trans_code TEXT, acq_disp TEXT, shares REAL, price REAL,
    value REAL, security_title TEXT, source TEXT, retrieved_at TEXT,
    PRIMARY KEY (accession, seq)
);
CREATE INDEX IF NOT EXISTS ix_insider_ticker ON insider_transactions(ticker, filing_date);

CREATE TABLE IF NOT EXISTS option_quotes (
    ticker TEXT, snapshot_ts TEXT, expiry TEXT, strike REAL, option_type TEXT,
    bid REAL, ask REAL, last REAL, iv REAL, open_interest REAL, volume REAL,
    underlying REAL, source TEXT,
    PRIMARY KEY (ticker, snapshot_ts, expiry, strike, option_type)
);

CREATE TABLE IF NOT EXISTS implied_moves (
    ticker TEXT, snapshot_date TEXT, announce_date TEXT, expiry TEXT,
    underlying REAL, straddle_mid REAL, implied_move REAL, atm_iv REAL,
    spread_pct REAL, source TEXT,
    PRIMARY KEY (ticker, snapshot_date, expiry)
);

-- Point-in-time feature values for an event, computed as of as_of_date.
CREATE TABLE IF NOT EXISTS features (
    ticker TEXT, announce_date TEXT, as_of_date TEXT, feature TEXT,
    value REAL, detail TEXT, computed_at TEXT,
    PRIMARY KEY (ticker, announce_date, as_of_date, feature)
);

-- Rankings produced for upcoming events (the prospective track record).
CREATE TABLE IF NOT EXISTS scores (
    run_id TEXT, ticker TEXT, as_of_date TEXT, announce_date TEXT,
    announce_source TEXT, timing TEXT, opportunity_score REAL,
    direction TEXT, composite REAL, n_signals INTEGER, coverage REAL,
    confidence REAL, prob_beat REAL, prob_basis TEXT, explanation TEXT,
    computed_at TEXT,
    PRIMARY KEY (run_id, ticker)
);

-- Prospective track record: the last pre-announcement score vs what happened.
CREATE TABLE IF NOT EXISTS track_record (
    ticker TEXT, announce_date TEXT, scored_on TEXT, score_run_id TEXT,
    opportunity_score REAL, composite REAL, direction TEXT, n_signals INTEGER,
    car_ann REAL, ret_ann REAL, sue REAL, beat_consensus REAL, surprise_pct REAL,
    evaluated_at TEXT,
    PRIMARY KEY (ticker, announce_date)
);

CREATE TABLE IF NOT EXISTS backtest_runs (
    run_id TEXT PRIMARY KEY, created_at TEXT, config TEXT, summary TEXT
);
CREATE TABLE IF NOT EXISTS backtest_events (
    run_id TEXT, ticker TEXT, announce_date TEXT, split TEXT, data TEXT,
    PRIMARY KEY (run_id, ticker, announce_date)
);

CREATE TABLE IF NOT EXISTS model_registry (
    name TEXT PRIMARY KEY, created_at TEXT, params TEXT, validation TEXT,
    approved INTEGER
);

CREATE TABLE IF NOT EXISTS research_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, category TEXT,
    title TEXT, body TEXT, ref TEXT
);

CREATE TABLE IF NOT EXISTS collector_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT, collector TEXT, ticker TEXT,
    started_at TEXT, finished_at TEXT, status TEXT, n_rows INTEGER, message TEXT
);
"""


def now_utc() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def connect(path: Path | str | None = None, check_same_thread: bool = True) -> sqlite3.Connection:
    p = Path(path) if path else config.db_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(p, timeout=60, check_same_thread=check_same_thread)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA foreign_keys=ON")
    return con


def init(con: sqlite3.Connection) -> None:
    con.executescript(SCHEMA)
    con.commit()


@contextmanager
def session(path: Path | str | None = None):
    con = connect(path)
    init(con)
    try:
        yield con
        con.commit()
    finally:
        con.close()


def upsert(con: sqlite3.Connection, table: str, rows: Iterable[dict]) -> int:
    rows = list(rows)
    if not rows:
        return 0
    cols = list(rows[0].keys())
    sql = (
        f"INSERT OR REPLACE INTO {table} ({','.join(cols)}) "
        f"VALUES ({','.join('?' for _ in cols)})"
    )
    con.executemany(sql, [tuple(r.get(c) for c in cols) for r in rows])
    return len(rows)


def insert_ignore(con: sqlite3.Connection, table: str, rows: Iterable[dict]) -> int:
    rows = list(rows)
    if not rows:
        return 0
    cols = list(rows[0].keys())
    sql = (
        f"INSERT OR IGNORE INTO {table} ({','.join(cols)}) "
        f"VALUES ({','.join('?' for _ in cols)})"
    )
    cur = con.executemany(sql, [tuple(r.get(c) for c in cols) for r in rows])
    return cur.rowcount


def df(con: sqlite3.Connection, sql: str, params: tuple | list = ()) -> pd.DataFrame:
    return pd.read_sql_query(sql, con, params=params)


def log(con: sqlite3.Connection, category: str, title: str, body: str = "", ref: str = "") -> None:
    """Append to the research log. Categories: data, signal, model,
    failed_hypothesis, improvement, ops."""
    con.execute(
        "INSERT INTO research_log (ts, category, title, body, ref) VALUES (?,?,?,?,?)",
        (now_utc(), category, title, body, ref),
    )
    con.commit()


class CollectorRun:
    """Context manager that records a collector run for the Data Health page."""

    def __init__(self, con: sqlite3.Connection, collector: str, ticker: str | None = None):
        self.con, self.collector, self.ticker = con, collector, ticker
        self.n_rows = 0
        self.message = ""

    def __enter__(self):
        self.started = now_utc()
        return self

    def __exit__(self, exc_type, exc, tb):
        status = "ok" if exc is None else "error"
        msg = self.message if exc is None else f"{exc_type.__name__}: {exc}"[:500]
        self.con.execute(
            "INSERT INTO collector_runs (collector, ticker, started_at, finished_at, status, n_rows, message)"
            " VALUES (?,?,?,?,?,?,?)",
            (self.collector, self.ticker, self.started, now_utc(), status, self.n_rows, msg),
        )
        self.con.commit()
        return exc_type is not None and issubclass(exc_type, Exception)  # swallow & record


def dumps(o) -> str:
    return json.dumps(o, default=str, sort_keys=True)
