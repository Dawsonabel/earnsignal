"""Open job postings from public applicant-tracking-system (ATS) APIs.

Greenhouse, Lever, Ashby and SmartRecruiters publish documented, public,
unauthenticated job-board endpoints. No history is available from these APIs,
so this signal is tracked prospectively: each run is a dated snapshot.
"""
from __future__ import annotations

import re
from datetime import date, datetime, timezone

from .. import db
from ..http import Client

GREENHOUSE = "https://boards-api.greenhouse.io/v1/boards/{token}/jobs?content=true"
LEVER = "https://api.lever.co/v0/postings/{token}?mode=json"
ASHBY = "https://api.ashbyhq.com/posting-api/job-board/{token}?includeCompensation=false"
SMARTRECRUITERS = "https://api.smartrecruiters.com/v1/companies/{token}/postings?limit=100&offset={offset}"
WORKDAY = "https://{tenant}.{dc}.myworkdayjobs.com/wday/cxs/{tenant}/{site}/jobs"

CATEGORIES = [
    ("sales_gtm", r"\b(sales|account (executive|manager)|business development|bdr|sdr|partnership|marketing|growth|brand|merchant|advertis|revenue|go[- ]to[- ]market|gtm|commercial)\b"),
    ("ops_support", r"\b(operations|ops\b|support|customer (service|success|experience)|community|fulfil|logistics|trust (&|and) safety|onboarding|courier|driver|warehouse|supply)\b"),
    ("engineering", r"\b(engineer|engineering|developer|software|sre|devops|infrastructure|security|platform|mobile|ios|android|frontend|backend|machine learning|ml\b|data scien|data eng|analytics eng)\b"),
    ("product_design", r"\b(product manag|product design|designer|ux|ui\b|research(er)?|product)\b"),
    ("g_and_a", r"\b(finance|accounting|legal|counsel|people|recruit|talent|hr\b|human resources|tax|treasury|compliance|policy|workplace|facilities|procurement)\b"),
]


def categorize(department: str | None, title: str | None) -> str:
    text = f"{department or ''} | {title or ''}".lower()
    # department name is the stronger signal; test it first
    for name, pat in CATEGORIES:
        if department and re.search(pat, department.lower()):
            return name
    for name, pat in CATEGORIES:
        if re.search(pat, text):
            return name
    return "other"


def _ms_to_iso(ms):
    try:
        return datetime.fromtimestamp(int(ms) / 1000, tz=timezone.utc).isoformat()
    except (TypeError, ValueError):
        return None


def fetch_greenhouse(client: Client, token: str) -> list[dict]:
    js = client.get_json(GREENHOUSE.format(token=token))
    out = []
    for j in js.get("jobs", []):
        deps = j.get("departments") or []
        out.append(
            {
                "job_id": str(j["id"]),
                "title": j.get("title"),
                "department": deps[0]["name"] if deps else None,
                "location": (j.get("location") or {}).get("name"),
                "src_created_at": j.get("first_published") or j.get("updated_at"),
                "url": j.get("absolute_url"),
            }
        )
    return out


def fetch_lever(client: Client, token: str) -> list[dict]:
    js = client.get_json(LEVER.format(token=token))
    out = []
    for j in js:
        cat = j.get("categories") or {}
        out.append(
            {
                "job_id": str(j["id"]),
                "title": j.get("text"),
                "department": cat.get("department") or cat.get("team"),
                "location": cat.get("location"),
                "src_created_at": _ms_to_iso(j.get("createdAt")),
                "url": j.get("hostedUrl"),
            }
        )
    return out


def fetch_ashby(client: Client, token: str) -> list[dict]:
    js = client.get_json(ASHBY.format(token=token))
    out = []
    for j in js.get("jobs", []):
        if j.get("isListed") is False:
            continue
        out.append(
            {
                "job_id": str(j["id"]),
                "title": j.get("title"),
                "department": j.get("department") or j.get("team"),
                "location": j.get("location"),
                "src_created_at": j.get("publishedAt"),
                "url": j.get("jobUrl"),
            }
        )
    return out


def fetch_smartrecruiters(client: Client, token: str) -> list[dict]:
    out, offset = [], 0
    while True:
        js = client.get_json(SMARTRECRUITERS.format(token=token, offset=offset))
        content = js.get("content", [])
        for j in content:
            loc = j.get("location") or {}
            out.append(
                {
                    "job_id": str(j["id"]),
                    "title": j.get("name"),
                    "department": (j.get("department") or {}).get("label") or (j.get("function") or {}).get("label"),
                    "location": ", ".join(x for x in [loc.get("city"), loc.get("country")] if x),
                    "src_created_at": j.get("releasedDate"),
                    "url": f"https://jobs.smartrecruiters.com/{token}/{j['id']}",
                }
            )
        offset += len(content)
        if not content or offset >= js.get("totalFound", 0) or offset > 5000:
            break
    return out


def fetch_workday(client: Client, token: str) -> list[dict]:
    tenant, dc, site = token.split("|")
    url = WORKDAY.format(tenant=tenant, dc=dc, site=site)
    out, offset = [], 0
    while True:
        js = client.post_json(url, {"appliedFacets": {}, "limit": 20, "offset": offset, "searchText": ""})
        posts = js.get("jobPostings", [])
        for j in posts:
            out.append(
                {
                    "job_id": j.get("externalPath") or j.get("title"),
                    "title": j.get("title"),
                    "department": None,
                    "location": j.get("locationsText"),
                    "src_created_at": j.get("postedOn"),
                    "url": f"https://{tenant}.{dc}.myworkdayjobs.com/{site}{j.get('externalPath', '')}",
                }
            )
        offset += len(posts)
        if not posts or offset >= js.get("total", 0) or offset > 3000:
            break
    return out


def fetch_eightfold(client: Client, token: str) -> list[dict]:
    """Eightfold-hosted career sites (e.g. Netflix). token = host|domain."""
    host, domain = token.split("|")
    out, start = [], 0
    while True:
        js = client.get_json(f"https://{host}/api/apply/v2/jobs?domain={domain}&num=50&start={start}")
        pos = js.get("positions", [])
        for j in pos:
            ts = j.get("t_create")
            out.append(
                {
                    "job_id": str(j.get("id")),
                    "title": j.get("name"),
                    "department": j.get("department"),
                    "location": j.get("location"),
                    "src_created_at": _ms_to_iso(ts * 1000) if isinstance(ts, (int, float)) else None,
                    "url": j.get("canonicalPositionUrl"),
                }
            )
        start += len(pos)
        if not pos or start >= js.get("count", 0) or start > 5000:
            break
    return out


FETCHERS = {
    "greenhouse": fetch_greenhouse,
    "lever": fetch_lever,
    "ashby": fetch_ashby,
    "smartrecruiters": fetch_smartrecruiters,
    "workday": fetch_workday,
    "eightfold": fetch_eightfold,
}

SOURCE_URLS = {
    "greenhouse": "https://boards-api.greenhouse.io/v1/boards/{token}/jobs",
    "lever": "https://api.lever.co/v0/postings/{token}",
    "ashby": "https://api.ashbyhq.com/posting-api/job-board/{token}",
    "smartrecruiters": "https://api.smartrecruiters.com/v1/companies/{token}/postings",
    "workday": "workday:{token}",
    "eightfold": "eightfold:{token}",
}


def snapshot(con, ticker: str, provider: str, postings: list[dict], today: str | None = None) -> int:
    """Record a dated snapshot. Returns number of open postings."""
    today = today or date.today().isoformat()
    prev = con.execute(
        "SELECT MAX(last_seen) FROM job_postings WHERE ticker=? AND provider=? AND last_seen < ?",
        (ticker, provider, today),
    ).fetchone()[0]
    existing = {
        r["job_id"]: r["first_seen"]
        for r in con.execute("SELECT job_id, first_seen FROM job_postings WHERE ticker=? AND provider=?", (ticker, provider))
    }
    rows = []
    for p in postings:
        rows.append(
            {
                "ticker": ticker,
                "provider": provider,
                "job_id": p["job_id"],
                "title": p.get("title"),
                "department": p.get("department"),
                "category": categorize(p.get("department"), p.get("title")),
                "location": p.get("location"),
                "src_created_at": p.get("src_created_at"),
                "url": p.get("url"),
                "first_seen": existing.get(p["job_id"], today),
                "last_seen": today,
            }
        )
    db.upsert(con, "job_postings", rows)

    url = SOURCE_URLS[provider].format(token="?")
    now = db.now_utc()
    counts: dict[str, float] = {"total": len(rows)}
    for r in rows:
        counts[f"cat:{r['category']}"] = counts.get(f"cat:{r['category']}", 0) + 1
    if prev:  # new/closed only make sense relative to an earlier snapshot
        counts["new_since_prev"] = sum(1 for r in rows if r["first_seen"] == today)
        counts["closed_since_prev"] = con.execute(
            "SELECT COUNT(*) FROM job_postings WHERE ticker=? AND provider=? AND last_seen=?",
            (ticker, provider, prev),
        ).fetchone()[0]
    obs = [
        {
            "source": "jobs",
            "ticker": ticker,
            "series": k,
            "obs_date": today,
            "value": float(v),
            "meta": db.dumps({"provider": provider, "prev_snapshot": prev}),
            "source_url": url,
            "observed_at": now,
        }
        for k, v in counts.items()
    ]
    db.upsert(con, "observations", obs)
    return len(rows)


def collect(con, client: Client, ticker: str, provider: str, token: str) -> int:
    fetch = FETCHERS[provider]
    postings = fetch(client, token)
    if not postings:
        raise RuntimeError(f"{provider} board '{token}' returned 0 postings (wrong token or board moved?)")
    n = snapshot(con, ticker, provider, postings)
    con.execute(
        "UPDATE observations SET source_url=? WHERE source='jobs' AND ticker=? AND obs_date=?",
        (SOURCE_URLS[provider].format(token=token), ticker, date.today().isoformat()),
    )
    return n
