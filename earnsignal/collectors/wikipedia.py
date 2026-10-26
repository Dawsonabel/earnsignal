"""Wikipedia daily pageviews (Wikimedia REST API, free, history from 2015-07-01).

Used as an attention/consumer-interest proxy for each company and its flagship
product. agent=user excludes self-identified bots and spiders.
"""
from __future__ import annotations

from datetime import date, timedelta
from urllib.parse import quote

from .. import db
from ..http import Client

URL = (
    "https://wikimedia.org/api/rest_v1/metrics/pageviews/per-article/"
    "en.wikipedia/all-access/user/{article}/daily/{start}/{end}"
)
EARLIEST = date(2015, 7, 1)


def parse(js: dict, ticker: str, article: str, url: str) -> list[dict]:
    now = db.now_utc()
    rows = []
    for it in js.get("items", []):
        ts = it["timestamp"]  # YYYYMMDD00
        d = f"{ts[0:4]}-{ts[4:6]}-{ts[6:8]}"
        rows.append(
            {
                "source": "wikipedia",
                "ticker": ticker,
                "series": f"pageviews:{article}",
                "obs_date": d,
                "value": float(it["views"]),
                "meta": None,
                "source_url": url,
                "observed_at": now,
            }
        )
    return rows


def collect(con, client: Client, ticker: str, article: str, history_start: str) -> int:
    series = f"pageviews:{article}"
    last = con.execute(
        "SELECT MAX(obs_date) FROM observations WHERE source='wikipedia' AND ticker=? AND series=?",
        (ticker, series),
    ).fetchone()[0]
    start = max(EARLIEST, date.fromisoformat(history_start))
    if last:
        start = date.fromisoformat(last) + timedelta(days=1)
    end = date.today() - timedelta(days=1)
    if start > end:
        return 0
    url = URL.format(article=quote(article, safe=""), start=start.strftime("%Y%m%d"), end=end.strftime("%Y%m%d"))
    r = client.get(url)
    if r.status_code == 404:  # no views in range or bad title
        raise RuntimeError(f"Wikipedia article not found or no data: {article}")
    r.raise_for_status()
    return db.upsert(con, "observations", parse(r.json(), ticker, article, url))


# ------------------------------------------------- title resolution (Wikidata)
WIKIDATA_SPARQL = "https://query.wikidata.org/sparql"
_RESOLVE_QUERY = """
SELECT DISTINCT ?item ?article WHERE {
  VALUES ?exch { wd:Q82059 wd:Q13677 }
  ?item p:P414 ?st . ?st ps:P414 ?exch ; pq:P249 ?sym .
  FILTER(UCASE(STR(?sym)) = "%s")
  ?article schema:about ?item ; schema:isPartOf <https://en.wikipedia.org/> .
} LIMIT 5
"""


def resolve_title(client: Client, ticker: str) -> str | None:
    """English Wikipedia article for a NASDAQ/NYSE ticker via Wikidata (P414 + P249).
    Returns None when there is no unique match, rather than guessing."""
    from urllib.parse import unquote

    r = client.get(WIKIDATA_SPARQL, params={"query": _RESOLVE_QUERY % ticker.upper(), "format": "json"},
                   headers={"Accept": "application/sparql-results+json"})
    if r.status_code != 200:
        return None
    rows = r.json().get("results", {}).get("bindings", [])
    arts = sorted({b["article"]["value"] for b in rows})
    if len(arts) != 1:
        return None
    return unquote(arts[0].rsplit("/wiki/", 1)[-1])
