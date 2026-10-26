"""SEC EDGAR: ticker->CIK map, filing index (8-K Item 2.02 earnings releases,
10-Q/10-K), and XBRL company facts (revenue, EPS, net income).

All endpoints are free and official. SEC fair-access policy: descriptive
User-Agent with contact details and no more than 10 requests/second.
"""
from __future__ import annotations

import calendar
import json
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

from .. import config, db
from ..http import Client

TICKER_MAP_URL = "https://www.sec.gov/files/company_tickers.json"
SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik:010d}.json"
SUBMISSIONS_PAGE_URL = "https://data.sec.gov/submissions/{name}"
COMPANYFACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json"
ARCHIVE_URL = "https://www.sec.gov/Archives/edgar/data/{cik}/{acc}/{doc}"

KEEP_FORMS = {"8-K", "8-K/A", "10-Q", "10-K", "10-Q/A", "10-K/A", "6-K", "20-F", "4", "4/A"}
PERIODIC_FORMS = {"10-Q", "10-K", "10-Q/A", "10-K/A"}

# metric -> (candidate us-gaap tags in priority order, unit)
METRICS = {
    "revenue": (
        [
            "RevenueFromContractWithCustomerExcludingAssessedTax",
            "Revenues",
            "RevenueFromContractWithCustomerIncludingAssessedTax",
            "SalesRevenueNet",
            "RevenuesNetOfInterestExpense",
        ],
        "USD",
    ),
    "eps_diluted": (["EarningsPerShareDiluted", "EarningsPerShareBasicAndDiluted", "EarningsPerShareBasic"], "USD/shares"),
    "net_income": (["NetIncomeLoss", "ProfitLoss"], "USD"),
    "operating_income": (["OperatingIncomeLoss"], "USD"),
}


# ----------------------------------------------------------------- ticker map
def load_ticker_map(client: Client, max_age_hours: int = 24) -> dict[str, dict]:
    path = config.cache_dir() / "company_tickers.json"
    if not path.exists() or (time.time() - path.stat().st_mtime) > max_age_hours * 3600:
        js = client.get_json(TICKER_MAP_URL)
        path.write_text(json.dumps(js))
    js = json.loads(path.read_text())
    return parse_ticker_map(js)


def parse_ticker_map(js: dict) -> dict[str, dict]:
    out = {}
    for row in js.values():
        out[str(row["ticker"]).upper()] = {"cik": int(row["cik_str"]), "title": row.get("title")}
    return out


# ---------------------------------------------------------------- submissions
def fiscal_year_end_month(mmdd: str | None) -> int | None:
    """SEC fiscalYearEnd 'MMDD' -> month. 52/53-week years that end in the first
    week of a month (e.g. '0201') belong to the previous month."""
    if not mmdd or len(mmdd) != 4 or not mmdd.isdigit():
        return None
    m, d = int(mmdd[:2]), int(mmdd[2:])
    if d <= 7:
        m = 12 if m == 1 else m - 1
    return m

def parse_submission_block(block: dict, ticker: str, cik: int, retrieved_at: str) -> list[dict]:
    """`block` is the columnar dict found at filings.recent or in an older page file."""
    n = len(block.get("accessionNumber", []))
    rows = []
    for i in range(n):
        form = block["form"][i]
        if form not in KEEP_FORMS:
            continue
        acc = block["accessionNumber"][i]
        doc = (block.get("primaryDocument") or [""] * n)[i]
        rows.append(
            {
                "accession": acc,
                "ticker": ticker,
                "cik": cik,
                "form": form,
                "filing_date": block["filingDate"][i],
                "acceptance_datetime": (block.get("acceptanceDateTime") or [None] * n)[i],
                "report_date": (block.get("reportDate") or [None] * n)[i] or None,
                "items": (block.get("items") or [""] * n)[i] or "",
                "primary_doc": doc,
                "url": ARCHIVE_URL.format(cik=cik, acc=acc.replace("-", ""), doc=doc),
                "retrieved_at": retrieved_at,
            }
        )
    return rows


def collect_submissions(con, client: Client, ticker: str, cik: int, history_start: str) -> int:
    js = client.get_json(SUBMISSIONS_URL.format(cik=cik))
    now = db.now_utc()
    rows = parse_submission_block(js["filings"]["recent"], ticker, cik, now)
    for f in js["filings"].get("files", []):
        if f.get("filingTo", "9999") < history_start:
            continue
        page = client.get_json(SUBMISSIONS_PAGE_URL.format(name=f["name"]))
        rows += parse_submission_block(page, ticker, cik, now)
    fye = fiscal_year_end_month(js.get("fiscalYearEnd"))
    if fye:
        con.execute("UPDATE companies SET fiscal_year_end=? WHERE ticker=?", (fye, ticker))
    return db.upsert(con, "filings", rows)


# ------------------------------------------------------------- company facts
def parse_companyfacts(js: dict, ticker: str, cik: int, retrieved_at: str) -> list[dict]:
    gaap = js.get("facts", {}).get("us-gaap", {})
    url = COMPANYFACTS_URL.format(cik=cik)
    rows = []
    for metric, (tags, unit) in METRICS.items():
        for tag in tags:
            units = gaap.get(tag, {}).get("units", {})
            for fact in units.get(unit, []):
                if fact.get("form") not in PERIODIC_FORMS or "start" not in fact:
                    continue
                start, end = fact["start"], fact["end"]
                dur = (date.fromisoformat(end) - date.fromisoformat(start)).days
                rows.append(
                    {
                        "ticker": ticker,
                        "metric": metric,
                        "tag": tag,
                        "period_start": start,
                        "period_end": end,
                        "duration_days": dur,
                        "fiscal_year": fact.get("fy"),
                        "fiscal_period": fact.get("fp"),
                        "form": fact.get("form"),
                        "accession": fact.get("accn"),
                        "filed_date": fact.get("filed"),
                        "value": float(fact["val"]),
                        "derived": 0,
                        "source_url": url,
                        "retrieved_at": retrieved_at,
                    }
                )
    return rows


def collect_companyfacts(con, client: Client, ticker: str, cik: int) -> int:
    r = client.get(COMPANYFACTS_URL.format(cik=cik))
    if r.status_code == 404:
        return 0
    r.raise_for_status()
    rows = parse_companyfacts(r.json(), ticker, cik, db.now_utc())
    con.execute("DELETE FROM fundamentals WHERE ticker=? AND derived=0", (ticker,))
    return db.upsert(con, "fundamentals", rows)


# --------------------------------------------------- point-in-time quarterlies
def _is_quarter(d: int) -> bool:
    return 80 <= d <= 100


def _is_annual(d: int) -> bool:
    return 350 <= d <= 380


def quarterly_series(facts: pd.DataFrame, metric: str, as_of: str | None = None) -> pd.DataFrame:
    """Point-in-time quarterly values for one ticker/metric.

    facts: rows from the fundamentals table for one ticker.
    For each fiscal quarter, the EARLIEST-filed value is used (what the market
    first saw), restricted to filings on or before `as_of`. Q4 is derived as
    annual minus Q1..Q3 when it is not reported directly; derived rows carry
    the 10-K filing date and derived=1.
    Returns columns: period_start, period_end, value, filed_date, tag, derived.
    """
    f = facts[facts["metric"] == metric].copy()
    if as_of is not None:
        f = f[f["filed_date"] <= as_of]
    if f.empty:
        return pd.DataFrame(columns=["period_start", "period_end", "value", "filed_date", "tag", "derived"])
    tags = METRICS[metric][0]
    q = f[f["duration_days"].map(_is_quarter)]
    # pick the tag with the best quarterly coverage for consistency; fill gaps with others
    if not q.empty:
        cover = q.groupby("tag")["period_end"].nunique()
        order = sorted(cover.index, key=lambda t: (-cover[t], tags.index(t) if t in tags else 99))
    else:
        order = [t for t in tags if t in set(f["tag"])]
    rank = {t: i for i, t in enumerate(order + [t for t in tags if t not in order])}
    cols = ["period_start", "period_end", "value", "filed_date", "tag"]

    def first_filed(g: pd.DataFrame) -> pd.DataFrame:
        g = g.assign(_r=g["tag"].map(rank).fillna(99))
        g = g.sort_values(["period_end", "_r", "filed_date"])
        return g.drop_duplicates("period_end", keep="first")[cols].reset_index(drop=True)

    if not q.empty:
        known = first_filed(q)
        known["derived"] = 0
    else:
        known = pd.DataFrame(columns=cols + ["derived"])

    a = f[f["duration_days"].map(_is_annual)]
    derived = []
    if not a.empty:
        aa = first_filed(a)
        known_ends = set(known["period_end"])
        for _, row in aa.iterrows():
            if row["period_end"] in known_ends:
                continue
            inside = known[(known["period_start"] >= row["period_start"]) & (known["period_end"] < row["period_end"])]
            if len(inside) != 3:
                continue
            q3_end = inside["period_end"].max()
            q4_start = (date.fromisoformat(q3_end) + timedelta(days=1)).isoformat()
            derived.append(
                {
                    "period_start": q4_start,
                    "period_end": row["period_end"],
                    "value": float(row["value"]) - float(inside["value"].sum()),
                    "filed_date": max(row["filed_date"], inside["filed_date"].max()),
                    "tag": row["tag"],
                    "derived": 1,
                }
            )
    res = pd.concat([known, pd.DataFrame(derived)], ignore_index=True) if derived else known
    return res.sort_values("period_end").reset_index(drop=True)


# ------------------------------------------------------------ earnings events
def classify_timing(acceptance: str | None, tz: str = "America/New_York") -> tuple[str | None, str]:
    """Return (ISO timestamp in exchange time, timing) with timing BMO/DURING/AMC/UNKNOWN.

    SEC's acceptanceDateTime is true UTC (verified against EDGAR index pages,
    which show Eastern time: 06:01:26 EDT appears as 10:01:26.000Z), so it is
    converted to New York time before classifying."""
    if not acceptance:
        return None, "UNKNOWN"
    s = acceptance.strip().split(".")[0].replace("Z", "")
    try:
        ts = datetime.fromisoformat(s).replace(tzinfo=ZoneInfo("UTC")).astimezone(ZoneInfo(tz))
    except ValueError:
        return None, "UNKNOWN"
    hm = ts.hour * 60 + ts.minute
    timing = "BMO" if hm < 9 * 60 + 30 else ("AMC" if hm >= 16 * 60 else "DURING")
    return ts.isoformat(), timing


def _calendar_quarter_ends(fye_month: int, around: date) -> list[date]:
    months = sorted({((fye_month - 1 + 3 * k) % 12) + 1 for k in range(4)})
    ends = []
    for y in (around.year - 1, around.year):
        for m in months:
            ends.append(date(y, m, calendar.monthrange(y, m)[1]))
    return sorted(ends)


def match_period(announce: date, period_ends: list[date], fye_month: int, max_gap: int = 75) -> date | None:
    cands = [p for p in period_ends if 0 < (announce - p).days <= max_gap]
    if cands:
        return max(cands)
    cal = [p for p in _calendar_quarter_ends(fye_month, announce) if 0 < (announce - p).days <= max_gap]
    return max(cal) if cal else None


def build_earnings_events(con, ticker: str, tz: str) -> int:
    """Turn 8-K filings with Item 2.02 into earnings events (one per fiscal quarter)."""
    filings = db.df(
        con,
        "SELECT * FROM filings WHERE ticker=? AND form='8-K' AND items LIKE '%2.02%' ORDER BY filing_date",
        (ticker,),
    )
    filings = filings[filings["items"].map(lambda x: "2.02" in str(x).replace(" ", "").split(","))]
    if filings.empty:
        return 0
    fye = con.execute("SELECT fiscal_year_end FROM companies WHERE ticker=?", (ticker,)).fetchone()
    fye_month = int(fye[0]) if fye and fye[0] else 12
    ends = [
        date.fromisoformat(r[0])
        for r in con.execute(
            "SELECT DISTINCT period_end FROM fundamentals WHERE ticker=? AND metric='revenue' AND duration_days BETWEEN 80 AND 100",
            (ticker,),
        )
    ]
    # derived Q4 ends = annual period ends
    ends += [
        date.fromisoformat(r[0])
        for r in con.execute(
            "SELECT DISTINCT period_end FROM fundamentals WHERE ticker=? AND metric='revenue' AND duration_days BETWEEN 350 AND 380",
            (ticker,),
        )
    ]
    events: dict[date, dict] = {}
    for _, f in filings.iterrows():
        ts, timing = classify_timing(f["acceptance_datetime"], tz)
        local_day = date.fromisoformat(ts[:10]) if ts else date.fromisoformat(f["filing_date"])
        period = match_period(local_day, ends, fye_month)
        if period is None:
            continue
        if period in events and events[period]["announce_date"] <= local_day.isoformat():
            continue  # keep the earliest release for the quarter
        events[period] = {
            "ticker": ticker,
            "announce_date": local_day.isoformat(),
            "announce_ts": ts,
            "timing": timing,
            "period_end": period.isoformat(),
            "source": "sec_8k_202",
            "accession": f["accession"],
            "url": f["url"],
            "status": "reported",
            "retrieved_at": db.now_utc(),
        }
    con.execute("DELETE FROM earnings_events WHERE ticker=? AND source='sec_8k_202'", (ticker,))
    return db.upsert(con, "earnings_events", events.values())
