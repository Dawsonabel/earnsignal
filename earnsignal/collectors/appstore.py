"""Apple App Store: rating counts (iTunes Lookup API) and top-chart ranks (Apple
Marketing Tools RSS). Both are free and public but current-snapshot only, so
the signal is built prospectively from daily snapshots.

Rating-count growth is a rough proxy for active-user/download growth. Caveat:
developers may reset the rating summary on a new release, so negative jumps
are flagged rather than treated as demand collapses.
"""
from __future__ import annotations

from datetime import date

from .. import db
from ..http import Client

LOOKUP = "https://itunes.apple.com/lookup?id={app_id}&country=us&entity=software"
CHART = "https://rss.marketingtools.apple.com/api/v2/us/apps/{chart}/{n}/apps.json"
CHARTS = ["top-free", "top-paid"]


def collect_lookup(con, client: Client, ticker: str, app_id: int) -> int:
    url = LOOKUP.format(app_id=app_id)
    js = client.get_json(url)
    res = js.get("results") or []
    if not res:
        raise RuntimeError(f"App id {app_id} not found in the US store")
    a = res[0]
    today = date.today().isoformat()
    now = db.now_utc()
    meta = db.dumps({"app_id": app_id, "name": a.get("trackName"), "version": a.get("version"),
                     "current_version_release": a.get("currentVersionReleaseDate")})
    rows = []
    for series, key in [("rating_count", "userRatingCount"), ("avg_rating", "averageUserRating"),
                        ("rating_count_current_version", "userRatingCountForCurrentVersion")]:
        if a.get(key) is not None:
            rows.append({"source": "appstore", "ticker": ticker, "series": series, "obs_date": today,
                         "value": float(a[key]), "meta": meta, "source_url": url, "observed_at": now})
    return db.upsert(con, "observations", rows)


def collect_charts(con, client: Client, app_ids: dict[int, str], n: int = 100) -> int:
    """Look up each tracked app's rank in the US top charts. Apps outside the
    chart are recorded as missing (no row), never as a guessed rank."""
    today = date.today().isoformat()
    now = db.now_utc()
    rows = []
    for chart in CHARTS:
        url = CHART.format(chart=chart, n=n)
        r = client.get(url)
        if r.status_code != 200:
            continue
        results = r.json().get("feed", {}).get("results", [])
        for rank, app in enumerate(results, start=1):
            try:
                aid = int(app.get("id"))
            except (TypeError, ValueError):
                continue
            if aid in app_ids:
                rows.append({"source": "appstore", "ticker": app_ids[aid], "series": f"rank:{chart}",
                             "obs_date": today, "value": float(rank),
                             "meta": db.dumps({"app_id": aid, "chart_size": len(results)}),
                             "source_url": url, "observed_at": now})
    return db.upsert(con, "observations", rows)
