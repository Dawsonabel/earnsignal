"""Insider transactions (SEC Forms 4). Two free sources:

1. SEC "Insider Transactions Data Sets": quarterly zips of every Form 3/4/5,
   structured as TSV files. Used for history (point-in-time by FILING_DATE).
2. Raw Form 4 XML for filings newer than the latest quarterly dataset.

Signal of interest: open-market purchases (transaction code P), which the
literature finds informative; sales (S) are mostly routine/10b5-1 and noisier.
"""
from __future__ import annotations

import csv
import io
import xml.etree.ElementTree as ET
import zipfile
from datetime import date, datetime

import pandas as pd

from .. import config, db
from ..http import Client

# SEC moved the datasets in 2026: 2026Q2+ live under datastandardsinnovation/.
DATASET_URLS = [
    "https://www.sec.gov/files/datastandardsinnovation/data/insider-transactions-data-sets/{y}q{q}_form345.zip",
    "https://www.sec.gov/files/structureddata/data/insider-transactions-data-sets/{y}q{q}_form345.zip",
]
ARCHIVE_DIR = "https://www.sec.gov/Archives/edgar/data/{cik}/{acc}/"


def parse_sec_date(s) -> str | None:
    if s is None or (isinstance(s, float) and pd.isna(s)) or str(s).strip() == "":
        return None
    s = str(s).strip()
    for fmt in ("%d-%b-%Y", "%Y-%m-%d", "%m/%d/%Y", "%Y%m%d"):
        try:
            return datetime.strptime(s.title() if "%b" in fmt else s, fmt).date().isoformat()
        except ValueError:
            continue
    return None


def _read_tsv(z: zipfile.ZipFile, name: str, usecols: list[str]) -> pd.DataFrame:
    member = next((n for n in z.namelist() if n.upper().endswith(name.upper())), None)
    if member is None:
        return pd.DataFrame(columns=usecols)
    with z.open(member) as fh:
        df = pd.read_csv(
            io.TextIOWrapper(fh, encoding="utf-8", errors="replace"),
            sep="\t", dtype=str, quoting=csv.QUOTE_NONE, on_bad_lines="skip",
            usecols=lambda c: c in usecols,
        )
    for c in usecols:
        if c not in df.columns:
            df[c] = None
    return df


def parse_dataset(zbytes: bytes, cik_to_ticker: dict[int, str]) -> list[dict]:
    z = zipfile.ZipFile(io.BytesIO(zbytes))
    sub = _read_tsv(z, "SUBMISSION.tsv", ["ACCESSION_NUMBER", "FILING_DATE", "DOCUMENT_TYPE", "FORM_TYPE", "ISSUERCIK", "AFF10B5ONE"])
    form = sub["DOCUMENT_TYPE"].where(sub["DOCUMENT_TYPE"].notna(), sub["FORM_TYPE"])  # readme vs observed column name
    sub = sub[form.isin(["4", "4/A"])]
    sub = sub[pd.to_numeric(sub["ISSUERCIK"], errors="coerce").isin(list(cik_to_ticker))]
    if sub.empty:
        return []
    accs = set(sub["ACCESSION_NUMBER"])
    owners = _read_tsv(z, "REPORTINGOWNER.tsv", ["ACCESSION_NUMBER", "RPTOWNERNAME", "RPTOWNER_RELATIONSHIP", "RPTOWNER_TITLE"])
    owners = owners[owners["ACCESSION_NUMBER"].isin(accs)].drop_duplicates("ACCESSION_NUMBER")
    tr = _read_tsv(
        z, "NONDERIV_TRANS.tsv",
        ["ACCESSION_NUMBER", "NONDERIV_TRANS_SK", "SECURITY_TITLE", "TRANS_DATE", "TRANS_CODE",
         "TRANS_SHARES", "TRANS_PRICEPERSHARE", "TRANS_ACQUIRED_DISP_CD"],
    )
    tr = tr[tr["ACCESSION_NUMBER"].isin(accs)]
    m = tr.merge(sub, on="ACCESSION_NUMBER", how="left").merge(owners, on="ACCESSION_NUMBER", how="left")
    now = db.now_utc()
    rows = []
    for _, r in m.iterrows():
        cik = int(float(r["ISSUERCIK"]))
        shares = pd.to_numeric(r["TRANS_SHARES"], errors="coerce")
        price = pd.to_numeric(r["TRANS_PRICEPERSHARE"], errors="coerce")
        rows.append(
            {
                "accession": r["ACCESSION_NUMBER"],
                "seq": str(r["NONDERIV_TRANS_SK"]),
                "ticker": cik_to_ticker[cik],
                "issuer_cik": cik,
                "filing_date": parse_sec_date(r["FILING_DATE"]),
                "trans_date": parse_sec_date(r["TRANS_DATE"]),
                "owner_name": r.get("RPTOWNERNAME"),
                "owner_relationship": r.get("RPTOWNER_RELATIONSHIP"),
                "owner_title": r.get("RPTOWNER_TITLE"),
                "trans_code": r["TRANS_CODE"],
                "acq_disp": r["TRANS_ACQUIRED_DISP_CD"],
                "shares": None if pd.isna(shares) else float(shares),
                "price": None if pd.isna(price) else float(price),
                "value": None if (pd.isna(shares) or pd.isna(price)) else float(shares * price),
                "security_title": r.get("SECURITY_TITLE"),
                "source": "sec_form345_dataset",
                "retrieved_at": now,
            }
        )
    return rows


def collect_datasets(con, client: Client, cik_to_ticker: dict[int, str], start_year: int) -> int:
    done = {
        r[0]
        for r in con.execute("SELECT ticker FROM collector_runs WHERE collector='insider_dataset' AND status='ok'")
    }
    today = date.today()
    total = 0
    for y in range(start_year, today.year + 1):
        for q in range(1, 5):
            key = f"{y}q{q}"
            q_end_month = q * 3
            if (y, q_end_month) >= (today.year, today.month):  # quarter not finished
                continue
            if key in done:
                continue
            with db.CollectorRun(con, "insider_dataset", key) as run:
                urls = DATASET_URLS if (y, q) >= (2026, 2) else DATASET_URLS[::-1]
                r = None
                for u in urls:
                    r = client.get(u.format(y=y, q=q))
                    if r.status_code != 404:
                        break
                if r.status_code == 404:
                    raise RuntimeError(f"dataset {key} not published (404 at both known paths)")
                r.raise_for_status()
                run.message = r.url
                rows = parse_dataset(r.content, cik_to_ticker)
                run.n_rows = db.upsert(con, "insider_transactions", rows)
                total += run.n_rows
                con.commit()
    return total


# ---------------------------------------------------------- recent Form 4 XML
def _t(el, path):
    x = el.find(path)
    return x.text.strip() if x is not None and x.text else None


def parse_form4_xml(xml_text: str, accession: str, ticker: str, filing_date: str) -> list[dict]:
    root = ET.fromstring(xml_text)
    cik = _t(root, "issuer/issuerCik")
    owner = root.find("reportingOwner")
    name = _t(owner, "reportingOwnerId/rptOwnerName") if owner is not None else None
    rel = owner.find("reportingOwnerRelationship") if owner is not None else None
    roles = []
    if rel is not None:
        for tag, label in [("isDirector", "Director"), ("isOfficer", "Officer"), ("isTenPercentOwner", "TenPercentOwner"), ("isOther", "Other")]:
            if (_t(rel, tag) or "").lower() in ("1", "true"):
                roles.append(label)
    title = _t(rel, "officerTitle") if rel is not None else None
    now = db.now_utc()
    rows = []
    for i, tx in enumerate(root.findall("nonDerivativeTable/nonDerivativeTransaction")):
        shares = _t(tx, "transactionAmounts/transactionShares/value")
        price = _t(tx, "transactionAmounts/transactionPricePerShare/value")
        try:
            s = float(shares) if shares else None
        except ValueError:
            s = None
        try:
            p = float(price) if price else None
        except ValueError:
            p = None
        rows.append(
            {
                "accession": accession, "seq": f"x{i}", "ticker": ticker,
                "issuer_cik": int(cik) if cik else None, "filing_date": filing_date,
                "trans_date": (_t(tx, "transactionDate/value") or "")[:10] or None,
                "owner_name": name, "owner_relationship": ",".join(roles), "owner_title": title,
                "trans_code": _t(tx, "transactionCoding/transactionCode"),
                "acq_disp": _t(tx, "transactionAmounts/transactionAcquiredDisposedCode/value"),
                "shares": s, "price": p, "value": s * p if (s is not None and p is not None) else None,
                "security_title": _t(tx, "securityTitle/value"),
                "source": "sec_form4_xml", "retrieved_at": now,
            }
        )
    return rows


def collect_recent_form4(con, client: Client, ticker: str, max_filings: int = 200) -> int:
    latest_ds = con.execute(
        "SELECT MAX(filing_date) FROM insider_transactions WHERE ticker=? AND source='sec_form345_dataset'", (ticker,)
    ).fetchone()[0] or "1900-01-01"
    have = {r[0] for r in con.execute("SELECT DISTINCT accession FROM insider_transactions WHERE ticker=?", (ticker,))}
    todo = db.df(
        con,
        "SELECT accession, cik, filing_date, primary_doc FROM filings WHERE ticker=? AND form IN ('4','4/A') AND filing_date > ? ORDER BY filing_date DESC LIMIT ?",
        (ticker, latest_ds, max_filings),
    )
    n = 0
    for _, f in todo.iterrows():
        if f["accession"] in have:
            continue
        doc = (f["primary_doc"] or "").split("/")[-1]  # strip xsl rendering folder -> raw XML
        if not doc.endswith(".xml"):
            continue
        url = ARCHIVE_DIR.format(cik=int(f["cik"]), acc=f["accession"].replace("-", "")) + doc
        r = client.get(url)
        if r.status_code != 200:
            continue
        try:
            rows = parse_form4_xml(r.text, f["accession"], ticker, f["filing_date"])
        except ET.ParseError:
            continue
        n += db.upsert(con, "insider_transactions", rows)
    return n
