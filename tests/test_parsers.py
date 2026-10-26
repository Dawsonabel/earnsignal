"""Parser tests on small hand-written fixtures shaped like the real API responses."""
import io
import zipfile
from datetime import date

import pandas as pd
import pytest

from earnsignal.collectors import appstore, insider, jobs, options, sec, wikipedia


def test_parse_ticker_map():
    js = {"0": {"cik_str": 1562088, "ticker": "duol", "title": "Duolingo, Inc."}}
    assert sec.parse_ticker_map(js) == {"DUOL": {"cik": 1562088, "title": "Duolingo, Inc."}}


def test_parse_submission_block_keeps_relevant_forms():
    block = {
        "accessionNumber": ["0001-24-000001", "0001-24-000002", "0001-24-000003"],
        "filingDate": ["2024-08-01", "2024-08-02", "2024-08-05"],
        "reportDate": ["2024-08-01", "", "2024-06-30"],
        "acceptanceDateTime": ["2024-08-01T16:05:12.000Z", "2024-08-02T10:00:00.000Z", "2024-08-05T08:00:00.000Z"],
        "form": ["8-K", "SC 13G", "10-Q"],
        "items": ["2.02,9.01", "", ""],
        "primaryDocument": ["ex991.htm", "x.htm", "q.htm"],
    }
    rows = sec.parse_submission_block(block, "DUOL", 1562088, "now")
    assert [r["form"] for r in rows] == ["8-K", "10-Q"]
    assert rows[0]["items"] == "2.02,9.01"
    assert rows[0]["url"].endswith("/1562088/000124000001/ex991.htm")


def test_classify_timing_converts_utc_to_new_york():
    # verified example: AAPL 10-K accepted 06:01:26 EDT appears as 10:01:26Z
    ts, timing = sec.classify_timing("2025-10-31T10:01:26.000Z")
    assert ts.startswith("2025-10-31T06:01:26") and timing == "BMO"
    assert sec.classify_timing("2024-08-01T20:05:12.000Z")[1] == "AMC"   # 16:05 EDT
    assert sec.classify_timing("2024-08-01T16:00:00.000Z")[1] == "DURING"  # 12:00 EDT
    assert sec.classify_timing("2026-01-30T21:30:00.000Z")[1] == "AMC"   # 16:30 EST
    # after-hours release late in the evening ET lands on the same ET date even if UTC rolled over
    ts, timing = sec.classify_timing("2024-11-06T01:10:00.000Z")
    assert ts.startswith("2024-11-05T20:10") and timing == "AMC"
    assert sec.classify_timing(None)[1] == "UNKNOWN"


def test_match_period():
    ends = [date(2024, 3, 31), date(2024, 6, 30)]
    assert sec.match_period(date(2024, 8, 1), ends, 12) == date(2024, 6, 30)
    # falls back to the fiscal calendar when XBRL periods are not known yet
    assert sec.match_period(date(2024, 11, 5), ends, 12) == date(2024, 9, 30)
    assert sec.match_period(date(2025, 3, 5), [], 1) == date(2025, 1, 31)  # January fiscal year end


def _fact(start, end, val, filed, form="10-Q", accn="a"):
    return {"start": start, "end": end, "val": val, "accn": accn, "fy": 2023, "fp": "Q1", "form": form, "filed": filed}


def test_parse_companyfacts_and_point_in_time_quarters():
    js = {"facts": {"us-gaap": {"Revenues": {"units": {"USD": [
        _fact("2023-01-01", "2023-03-31", 100, "2023-05-01", accn="q1"),
        _fact("2023-01-01", "2023-03-31", 130, "2024-05-01", accn="q1-restated"),  # later comparative, restated
        _fact("2023-04-01", "2023-06-30", 110, "2023-08-01", accn="q2"),
        _fact("2023-01-01", "2023-06-30", 210, "2023-08-01", accn="q2-ytd"),  # 6-month YTD: ignored
        _fact("2023-07-01", "2023-09-30", 120, "2023-11-01", accn="q3"),
        _fact("2023-01-01", "2023-12-31", 470, "2024-02-15", form="10-K", accn="fy"),
    ]}}}}}
    rows = sec.parse_companyfacts(js, "X", 1, "now")
    facts = pd.DataFrame(rows)
    q = sec.quarterly_series(facts, "revenue")
    assert q["value"].tolist() == [100, 110, 120, 140]  # first-filed Q1; Q4 = 470 - 330
    assert q["derived"].tolist() == [0, 0, 0, 1]
    assert q.iloc[-1]["filed_date"] == "2024-02-15"
    # as of mid-2023 only Q1 is known
    q_pit = sec.quarterly_series(facts, "revenue", as_of="2023-06-01")
    assert q_pit["value"].tolist() == [100]


def test_wikipedia_parse():
    js = {"items": [{"article": "Duolingo", "timestamp": "2025010100", "views": 1234}]}
    rows = wikipedia.parse(js, "DUOL", "Duolingo", "u")
    assert rows[0]["obs_date"] == "2025-01-01" and rows[0]["value"] == 1234.0


def test_job_categories():
    assert jobs.categorize("Sales", "Account Executive") == "sales_gtm"
    assert jobs.categorize(None, "Senior Software Engineer, Payments") == "engineering"
    assert jobs.categorize("Community Operations", "Specialist") == "ops_support"
    assert jobs.categorize(None, "Tax Manager") == "g_and_a"


class FakeClient:
    def __init__(self, payloads):
        self.payloads = payloads

    def get_json(self, url):
        for k, v in self.payloads.items():
            if k in url:
                return v
        raise KeyError(url)

    def post_json(self, url, payload):
        return self.get_json(url)


def test_job_fetchers_shapes():
    c = FakeClient({
        "greenhouse": {"jobs": [{"id": 1, "title": "SWE", "updated_at": "2025-01-01", "location": {"name": "NYC"},
                                  "absolute_url": "u", "departments": [{"name": "Engineering"}]}]},
        "lever": [{"id": "abc", "text": "AE", "createdAt": 1735689600000, "categories": {"team": "Sales", "location": "SF"}, "hostedUrl": "u"}],
        "ashby": {"jobs": [{"id": "x", "title": "Designer", "department": "Design", "location": "Remote", "publishedAt": "2025", "isListed": True, "jobUrl": "u"},
                           {"id": "y", "title": "Hidden", "isListed": False}]},
    })
    assert jobs.fetch_greenhouse(c, "t")[0]["department"] == "Engineering"
    assert jobs.fetch_lever(c, "t")[0]["src_created_at"].startswith("2025-01-01")
    assert [j["job_id"] for j in jobs.fetch_ashby(c, "t")] == ["x"]


def test_job_snapshot_new_and_closed(tmp_path):
    from earnsignal import db
    con = db.connect(tmp_path / "j.db"); db.init(con)
    jobs.snapshot(con, "X", "greenhouse", [{"job_id": "1", "title": "SWE"}, {"job_id": "2", "title": "AE"}], today="2025-01-01")
    jobs.snapshot(con, "X", "greenhouse", [{"job_id": "2", "title": "AE"}, {"job_id": "3", "title": "SWE"}], today="2025-01-08")
    obs = {r["series"]: r["value"] for r in con.execute("SELECT series, value FROM observations WHERE obs_date='2025-01-08'")}
    assert obs["total"] == 2 and obs["new_since_prev"] == 1 and obs["closed_since_prev"] == 1


def _zip(files: dict) -> bytes:
    b = io.BytesIO()
    with zipfile.ZipFile(b, "w") as z:
        for name, text in files.items():
            z.writestr(name, text)
    return b.getvalue()


def test_insider_dataset_parse():
    z = _zip({
        "SUBMISSION.tsv": "ACCESSION_NUMBER\tFILING_DATE\tDOCUMENT_TYPE\tISSUERCIK\n0001\t15-MAR-2024\t4\t1562088\n0002\t16-MAR-2024\t4\t999\n",
        "REPORTINGOWNER.tsv": "ACCESSION_NUMBER\tRPTOWNERNAME\tRPTOWNER_RELATIONSHIP\tRPTOWNER_TITLE\n0001\tDoe Jane\tDirector\t\n",
        "NONDERIV_TRANS.tsv": "ACCESSION_NUMBER\tNONDERIV_TRANS_SK\tSECURITY_TITLE\tTRANS_DATE\tTRANS_CODE\tTRANS_SHARES\tTRANS_PRICEPERSHARE\tTRANS_ACQUIRED_DISP_CD\n"
                              "0001\t11\tClass A\t13-MAR-2024\tP\t100\t20.5\tA\n0002\t12\tCommon\t13-MAR-2024\tS\t5\t1\tD\n",
    })
    rows = insider.parse_dataset(z, {1562088: "DUOL"})
    assert len(rows) == 1
    r = rows[0]
    assert r["ticker"] == "DUOL" and r["filing_date"] == "2024-03-15" and r["trans_code"] == "P" and r["value"] == pytest.approx(2050)


def test_form4_xml_parse():
    xml = """<ownershipDocument><issuer><issuerCik>0001562088</issuerCik></issuer>
    <reportingOwner><reportingOwnerId><rptOwnerName>Doe Jane</rptOwnerName></reportingOwnerId>
    <reportingOwnerRelationship><isDirector>1</isDirector><isOfficer>0</isOfficer></reportingOwnerRelationship></reportingOwner>
    <nonDerivativeTable><nonDerivativeTransaction><securityTitle><value>Class A</value></securityTitle>
    <transactionDate><value>2025-01-02</value></transactionDate><transactionCoding><transactionCode>P</transactionCode></transactionCoding>
    <transactionAmounts><transactionShares><value>10</value></transactionShares><transactionPricePerShare><value>300</value></transactionPricePerShare>
    <transactionAcquiredDisposedCode><value>A</value></transactionAcquiredDisposedCode></transactionAmounts></nonDerivativeTransaction></nonDerivativeTable>
    </ownershipDocument>"""
    rows = insider.parse_form4_xml(xml, "acc", "DUOL", "2025-01-03")
    assert rows[0]["owner_relationship"] == "Director" and rows[0]["value"] == 3000


def test_implied_move():
    calls = pd.DataFrame({"strike": [95, 100, 105], "bid": [6, 3, 1], "ask": [6.4, 3.2, 1.2], "impliedVolatility": [0.8, 0.7, 0.6]})
    puts = pd.DataFrame({"strike": [95, 100, 105], "bid": [1, 3, 6], "ask": [1.2, 3.2, 6.4], "impliedVolatility": [0.8, 0.7, 0.6]})
    im = options.implied_move(calls, puts, 100.4)
    assert im["strike"] == 100 and im["implied_move"] == pytest.approx(6.2 / 100.4)
    assert options.first_expiry_after(["2025-01-03", "2025-01-10"], "2025-01-04") == "2025-01-10"


def test_fiscal_year_end_month():
    assert sec.fiscal_year_end_month("1231") == 12
    assert sec.fiscal_year_end_month("0630") == 6
    assert sec.fiscal_year_end_month("0201") == 1  # Chewy-style 52/53-week year
    assert sec.fiscal_year_end_month("0103") == 12
    assert sec.fiscal_year_end_month(None) is None


def test_parse_cboe_occ_symbols():
    js = {"data": {"options": [
        {"option": "AAPL261030C00245000", "bid": 3.1, "ask": 3.3, "iv": 0.4, "open_interest": 10, "volume": 5},
        {"option": "AAPL261030P00245000", "bid": 2.9, "ask": 3.1, "iv": 0.41},
        {"option": "garbage"},
    ]}}
    df = options.parse_cboe(js)
    assert len(df) == 2
    assert df.iloc[0]["expiry"] == "2026-10-30" and df.iloc[0]["strike"] == 245.0 and df.iloc[1]["type"] == "put"
