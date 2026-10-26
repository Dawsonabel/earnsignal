# earnsignal — research notes

Status as of 2026-10-26. Sections 1–3 were written **before any real data was
collected**, so the pre-registered hypotheses in `config/model.yaml` could not
have been tuned to results. Section 5 lists what is verified, what is not, and
what cannot be evaluated yet.

---

## 1. Industry and universe choice

### Criteria (from the brief)
Accessibility of operational data, link between signals and revenue, frequency
of announcements, historical depth for backtesting, and size of measurable
earnings surprises.

| Candidate | Free operational data | Signal → revenue link | Backtestable history | Announcement moves | Main drawback |
|---|---|---|---|---|---|
| **Consumer internet & app platforms** (chosen) | Best: public job-board APIs (verified for 22 of 29 names; 7 more on Workday, opt-in), App Store ids for 29/29, Wikipedia for 29/29, SEC XBRL | Direct: users, bookings and GMV drive revenue | SEC, prices, insiders, Wikipedia since 2015. Jobs and apps are snapshot-only | Large (often ±10–20%), liquid weekly options | The most heavily covered sector by **paid** alt data (Sensor Tower, Similarweb, card panels), so any free edge is likely smallest here |
| Restaurants & consumer retail | Weak: foot traffic and card data are paid; job boards mostly Workday/iCIMS | Direct (traffic → same-store sales) | Product-level Google Trends had the best published evidence, but pytrends is archived and failing | Moderate | Strongest signals are paid or currently inaccessible |
| Semiconductors / hardware supply chain | Taiwan MOPS monthly revenue (free, official, monthly) | Plausible supplier → customer link | Monthly history available | Large | **No peer-reviewed evidence** that Taiwan monthly sales predict US surprises. Untested, not disproven |
| Small caps generally | Thin | Varies | Limited | Large | Literature finds free-data edges are largest in small, low-coverage, high-idiosyncratic-volatility stocks; data per company is sparse |

**Decision.** Start with consumer internet & app platforms, because it is the
only sector where at least three independent free operational signals are
actually reachable today. The caveat is real: this is also where institutional
alt-data competition is fiercest. **Recommended second industry:** semis and
hardware via Taiwan monthly revenue. It is novel, free and timely. It is also
untested, so it needs strict out-of-sample validation.

### Universe
* **Core (29, hand-verified 2026-10-26):** UBER LYFT DASH CART ABNB EXPE BKNG ·
  ETSY EBAY CHWY W FIGS · PINS SNAP RDDT META · MTCH DUOL PTON HIMS NFLX RBLX
  DKNG · HOOD COIN AFRM SOFI LMND TOST. Five peer segments are used for the
  intra-industry signals. Verification covered live job-board endpoints, iTunes
  lookup, final Wikipedia titles and SEC `fiscalYearEnd`.
* **Excluded:**
  * SPOT is a foreign private issuer, so it has no 10-Q or 8-K Item 2.02.
  * ROKU (Fox deal approved by shareholders 2026-10-14) and BMBL (take-private
    at $6.05, announced 2026-10-13) are pending acquisitions.
  * GRPN and U are weak fits.
* **Extended (41 more, phase 5):** additional consumer-internet, e-commerce,
  ad-tech, media and fintech names, added for statistical power (section 4).
  Wikipedia titles are resolved from Wikidata by ticker. They have no job-board
  or app metadata, and they are **not hand-verified**: the collector logs and
  skips any ticker that has changed.

---

## 2. What the literature says (summary; full review with citations in `docs/literature_review.md`)

| Signal family | Predicts surprise? | Predicts announcement return? | Decay | Free here? | Our pre-registered sign |
|---|---|---|---|---|---|
| Opportunistic insider purchases (Cohen, Malloy & Pomorski 2012) | Yes | Yes (coef 0.30, t≈1.96 on next EA return) | Survived SOX | **Yes** (Form 4) | **+** (`insider_opp_buyers_90d`) |
| Peer early-reporter surprise (Ramnath 2002) | Yes | Underreaction (late reporters' returns predictable) | — | Yes | **+** (`peer_early_surprise`) |
| Peer early-reporter *price* reaction (Thomas & Zhang 2008) | — | **Overreaction**: late reporters reverse at own EA (~1%/qtr contrarian) | — | Yes | **−** (`peer_early_reaction`) |
| SUE autocorrelation / PEAD (Bernard & Thomas) | Yes, weakening | Drift ~gone in non-microcaps after 2005 | Strong | Yes | + (baseline) |
| Product-level Google search (Da, Engelberg & Gao) | Revenue: strong. EPS: weak | ~27 bp per 1 SD, 3-day | Single 2004–08 sample | **No** (pytrends broken; official API is alpha-only) | not implemented |
| Wikipedia page views | **No evidence found** | Attention proxy only | — | Yes (clean API) | + (novel, low prior) |
| Job postings (Gutierrez et al. 2020) | Predicts future growth | Prices absorb within ~2 days | Commercialized since ~2015 | Snapshot only | + (prospective) |
| App downloads (Chen, Liu & Wu 2024) | Yes, only for apps with no visible monetization | ~1%/month, around EAs | Vanishes when firms disclose app metrics | Downloads paid; rating counts free (unvalidated proxy) | + (prospective) |
| Consumer reviews, web traffic (Huang 2018; Armstrong et al. 2025) | Yes | 55–80 bp/month | Strongest in small caps | Mostly paid / scraping limits | not implemented |
| Buying options into earnings (Dubinsky et al. 2019; de Silva, Smith & So 2025) | — | **Negative on average**: straddles ≈ −8%/event; retail buyers −5% to −14%; ~10% effective spreads | — | — | see section 3 |

Two cross-cutting findings shape the design:

* **Published effects shrink.** They lose about 26% out of sample and **about
  58% after publication** (McLean & Pontiff 2016). The average anomaly nets
  about 8 bp/month after costs (Chen & Velikov 2023). Plan for published effect
  sizes to be halved or worse.
* **Paid alt data already prices near-term revenue for covered consumer
  names.** Satellite, card and scanner data showed 3–5% announcement-window
  spreads historically, and that information is now in prices.

---

## 3. Options strategy: prior expectations

The brief's eventual use case is buying short-dated calls before promising
announcements. The evidence is unfavourable:

* Implied event volatility exceeds realized on average.
* Holding options *through* the announcement loses money on average, clearly so
  for retail traders, who also pay ~10–13% quoted spreads on short-dated calls.
* The documented positive straddle return (Gao, Xing & Zhang 2018) comes from
  **selling before** the announcement, at mid-quotes, mostly in illiquid names.

For a directional call to profit, the signal has to beat both the implied move
and the spread. With the ICs that free data can realistically deliver, that is
unlikely.

Fixed $2,500 bets on a $10,000 account risk 25% of equity per trade. Even with
a positive edge, the platform's Monte Carlo shows high probabilities of ruin at
that size (see the dashboard's Backtest page). Free historical option quotes do
not exist:
* Massive (formerly Polygon) free tier: end-of-day bars only, no bid/ask or IV.
* Alpha Vantage: 25 requests/day, and whether options are free is contested.

So the platform:
1. labels its historical options analysis as a **hypothetical scenario** with
   modelled prices, and
2. snapshots real chains (Yahoo, with Cboe delayed quotes as fallback) before
   every upcoming announcement, building a genuine point-in-time options
   dataset going forward.

This is a research tool. It does not place trades and is not investment advice.

---

## 4. Statistical power — why the universe was extended

For a rank IC with independent events, t ≈ IC·√N:

| True IC | Events for t = 1.96 | Events for 80% power |
|---|---|---|
| 0.10 | 384 | 785 |
| 0.05 | ~1,540 | ~3,140 |
| 0.03 | ~4,270 | ~8,720 |

Most core names IPO'd in 2019–2021, so 29 companies give only a few hundred
usable announcements, and the test split is 40% of that. At that size, only a
true IC of roughly 0.10 or more is detectable, about double the realistic
alt-data effect. Events cluster by season, so the effective N is smaller still.
The highest-value improvement after the first working pipeline was therefore
**more events**, not more signals. The 41-name extension adds older companies
(AMZN, GOOGL, PYPL, TRIP, YELP and others) with longer SEC and price histories.
Prospective tracking accumulates events at about 70 names × 4 per year.

---

## 5. Data sources evaluated

| Source | What | Reliability | Access | Point-in-time history | Terms | Cost | Status |
|---|---|---|---|---|---|---|---|
| SEC EDGAR submissions | Filing index; 8-K Item 2.02 = earnings-release timestamp | Official | JSON API, ≤10 req/s, UA with contact required | Full; `acceptanceDateTime` is **true UTC** (verified) | Public | $0 | ✅ |
| SEC XBRL companyfacts | Revenue, diluted EPS, net income | Official; tags vary by company | JSON API | Every filed version kept, with `filed` date. First-filed value used, so restatements never leak back | Public | $0 | ✅ |
| SEC insider datasets + Form 4 XML | Open-market buys/sells | Official | Quarterly zips (path moved in 2026Q2; both handled) + raw XML for recent filings | Since 2006, by filing date | Public | $0 | ✅ |
| Yahoo Finance via yfinance 1.7 | Prices, EPS estimate vs actual, upcoming dates, option chains | Unofficial; rate-limited; breaks periodically | Python library | Prices full history. Consensus ≤100 quarters, **not point-in-time snapshots** (estimates as Yahoo shows them today). Options current only | Yahoo terms: personal use | $0 | ✅ |
| Wikimedia pageviews | Daily article views, agent=user | Official | REST API, UA required | Daily since 2015-07-01 | Open | $0 | ✅ |
| Greenhouse / Lever / Ashby / SmartRecruiters / Eightfold | Open job postings by department | Documented public endpoints | JSON | **None**: snapshot only, tracked prospectively | Public listings | $0 | ✅ |
| Workday career sites | Postings (EXPE, BKNG, ETSY, EBAY, SNAP, DKNG, CHWY) | Undocumented endpoint | POST JSON | None | Unclear: **off by default**, set `enable_workday: true` to opt in | $0 | ⚙️ opt-in |
| Apple iTunes lookup + RSS charts | Rating counts, top-free/top-paid ranks (top-grossing removed) | Official | JSON | None | Apple marketing tools | $0 | ✅ |
| Wikidata SPARQL | Ticker → Wikipedia title for the extended universe | Community | SPARQL | n/a | CC0 | $0 | ✅ |
| Cboe delayed quotes | Option chain with bid/ask/IV/OI | Official, 15-min delayed | JSON | Current only | Cboe terms | $0 | ✅ fallback |
| Google Trends | Product search volume | pytrends archived 2025-04, fails with 429s; official API alpha-only | — | Would be backtestable (YoY ratios are scale-invariant) | Google terms | $0 | ❌ apply for alpha access |
| Stooq | Price fallback | API key required since ~2026-03 | CSV | Full | — | $0 | ⚙️ optional key |
| Massive (Polygon) free | Option EOD bars | Official | 5 calls/min | 2 years, no bid/ask or IV | — | $0 / $29+ | ❌ insufficient |
| Alpha Vantage HISTORICAL_OPTIONS | Historical chains with IV | Official | 25 req/day | 2008+ | — | free status contested | ❌ needs signup |
| DoltHub `post-no-preference/options` | Community EOD option chains since ~2021 | Volunteer | Dolt SQL | Unverified | — | $0 | 🔎 next step |
| ImportYeti / customs manifests | Import volumes | Scraping restrictions | — | — | ToS limits | — | ❌ |
| Nasdaq earnings calendar | Dates + timing | Needs browser headers | JSON | — | — | $0 | ❌ (Yahoo used) |
| Taiwan MOPS | Monthly supplier revenue | Official | Web | Monthly history | Public | $0 | 🔎 second-industry candidate |

---

## 6. Known biases and limitations

1. **Survivorship.** The universe is companies listed in 2026, and free prices
   for delisted stocks are unavailable. Results are optimistic.
2. **Present-day universe selection.** Membership and segment labels use
   today's knowledge. Mitigation: features need at least 4–8 quarters of each
   firm's own public history, and only firms with SEC filings at the time
   contribute events.
3. **Consensus is not point-in-time.** Yahoo shows today's view of past
   estimates and is scraped HTML, so beat/miss labels are noisy. The SEC-based
   SUE and revenue-surprise outcomes are fully point-in-time and are reported
   alongside.
4. **Announcement timing** comes from the 8-K acceptance time. The 8-K usually
   follows the press release within minutes but can lag. The reaction-window
   rule is robust to most lags (see `features/panel.py`).
5. **Wikipedia traffic** fell broadly in 2025. Signals are peer-relative to
   remove market-wide shifts.
6. **Multiple testing.** 13 backtestable signals × 5 outcomes. Benjamini–
   Hochberg q-values are reported on the main outcomes, and signs are
   pre-registered.
7. **No transaction costs on hypothetical options beyond assumptions** (8%
   spread, $0.65/contract). Stock long/short results are shown at 0/10/25/50 bps
   per side.

---

## 7. Verified vs unverified (end of build session)

* **Verified in this session:**
  * Parsers on fixtures shaped like the real responses.
  * The look-ahead test: scrambling every datum after an event's cutoff leaves
    its features unchanged.
  * The backtest recovers a deliberately planted signal in synthetic data and
    finds nothing in a random benchmark.
  * Dashboard pages render.
  * API formats checked live by research agents.
* **Not yet verified:** live collection end-to-end. The build environment
  could not reach the data sources, and the linked Mac was offline. The first
  real run is the user's `make collect`.
* **Cannot be evaluated yet:** job postings, app ratings and live option
  quotes. These are prospective-only and need several quarters of accumulated
  announcements.
