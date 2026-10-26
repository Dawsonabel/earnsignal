# Data-collector API spec (verified 2026-10-26)

Legend:
- **[LIVE]**: fetched the real endpoint on 2026-10-26 and saw the fields.
- **[DOC]**: taken from official docs.
- **[SRC]**: read in library source code (PyPI wheel or GitHub clone).
- **[3P]**: third-party article or wrapper.
- **[UNVERIFIED]**: from memory, or the sources disagree. Check it on the first real run.

The shell here could not reach these hosts. Live checks went through WebFetch, which summarizes pages, so "verbatim" examples below are rebuilt from field-by-field output.

---

## 1. SEC EDGAR

### 1(d) Fair-access rules (applies to every sec.gov / data.sec.gov call) [DOC]
- Max rate: **10 requests/second** ("Current max request rate: 10 requests/second"), counted across all of your machines. edgartools uses 9/s by default (`EDGAR_RATE_LIMIT_PER_SEC`) [SRC].
- **User-Agent is required**, in the form `Sample Company Name AdminContact@<sample company domain>.com`, e.g. `User-Agent: Deserved Data Collector dawson@example.com`. Requests without a declared UA get blocked (403).
- The sample headers also include `Accept-Encoding: gzip, deflate` and `Host: www.sec.gov` (or `data.sec.gov`). The page shows them but does not say they are mandatory. Send gzip anyway.
- No API key. data.sec.gov does not support CORS (irrelevant for Python).
- Bulk alternatives are rebuilt nightly around 3:00 a.m. ET: `submissions.zip` and `companyfacts.zip` [DOC]. Path `https://www.sec.gov/Archives/edgar/daily-index/bulkdata/submissions.zip` (and `.../companyfacts.zip`) is **[UNVERIFIED]**.

### 1(a) `https://www.sec.gov/files/company_tickers.json` [LIVE]
One JSON object keyed by string indices `"0"` … `"N"` (about 1,056 entries today). It is not a list.
```json
{"0": {"cik_str": 1045810, "ticker": "NVDA", "title": "NVIDIA CORP"},
 "1": {"cik_str": 320193,  "ticker": "AAPL", "title": "Apple Inc."},
 "2": {"cik_str": 1652044, "ticker": "GOOGL","title": "Alphabet Inc."}}
```
- `cik_str` is an **int** despite its name. Zero-pad it with `f"{cik:010d}"` for the data.sec.gov URLs.
- Iterate with `for v in d.values()`.

### 1(b) Submissions: `https://data.sec.gov/submissions/CIK##########.json` (10-digit, zero-padded) [LIVE]
Top-level keys:
`cik, entityType, sic, sicDescription, ownerOrg, insiderTransactionForOwnerExists, insiderTransactionForIssuerExists, name, tickers, exchanges, ein, lei, description, website, investorWebsite, category, fiscalYearEnd, stateOfIncorporation, stateOfIncorporationDescription, addresses, phone, flags, formerNames, filings`

`filings` has two keys: `recent` and `files`.

`filings.recent` is **columnar**: parallel arrays, where index i is one filing. Key order:
`accessionNumber, filingDate, reportDate, acceptanceDateTime, act, form, fileNumber, filmNumber, items, core_type, size, isXBRL, isInlineXBRL, isXBRLNumeric, primaryDocument, primaryDocDescription`
(`isXBRLNumeric` appears in addition to the commonly documented fields.)

Example, index 0..1 of CIK0002029976 [LIVE]:
```json
"accessionNumber": ["0001213900-26-112288", "0001213900-26-112287"],
"filingDate":      ["2026-10-26", "2026-10-26"],
"reportDate":      ["", "2026-10-23"],
"acceptanceDateTime": ["2026-10-26T12:17:23.000Z", "2026-10-26T12:15:34.000Z"],
"act": ["34", "34"], "form": ["DEFA14A", "8-K"],
"items": ["", "3.02"],
"core_type": [...], "size": [..int..], "isXBRL": [0|1], "isInlineXBRL": [0|1], "isXBRLNumeric": [0|1],
"primaryDocument": ["ea0309570-defa14a_kfgrow2.htm", "ea0309570-8k_kfgrow2.htm"],
"primaryDocDescription": ["DEFINITIVE ADDITIONAL MATERIALS", "CURRENT REPORT"]
```
Field notes:
- `accessionNumber`: string with dashes. Archive path: `https://www.sec.gov/Archives/edgar/data/{cik_int}/{accession_no_dashes}/{primaryDocument}`.
- `filingDate`, `reportDate`: `YYYY-MM-DD`. A missing `reportDate` is the **empty string `""`**, not null.
- `acceptanceDateTime`: `YYYY-MM-DDTHH:MM:SS.000Z`, and it is **true UTC**. I cross-checked against the EDGAR index pages, which show ET:
  - AAPL 10-K 0000320193-25-000079: index page "Accepted 2025-10-31 06:01:26" (EDT); JSON `2025-10-31T10:01:26.000Z`.
  - 0000320193-26-000006: index page "2026-01-30 06:01:32" (EST); JSON `2026-01-30T11:01:32.000Z`.
  - Parse with `datetime.fromisoformat(s.replace('Z','+00:00'))` (edgartools does the same), then `.astimezone(ZoneInfo('America/New_York'))`.
- `items`: comma-separated with **no spaces**, e.g. `"7.01,8.01,9.01"` or `"1.01,2.03,3.02,9.01"`. It is `""` for forms other than 8-K. Test for earnings with `'2.02' in items.split(',')`.
- `form`: e.g. `"8-K"`, `"8-K/A"`, `"4"`, `"10-Q"`.
- `recent` holds at least one year of filings or the latest 1,000, whichever is more [DOC].

`filings.files` (older filings) is a list of objects: `{"name": "CIK0000320193-submissions-001.json", "filingCount": 1236, "filingFrom": "1994-01-26", "filingTo": "2015-05-27"}` [3P + SRC]. edgartools fetches them as `https://data.sec.gov/submissions/` + `name` [SRC].
- **Paginated files have the columnar arrays at the TOP LEVEL**, with no `filings.recent` wrapper [LIVE: `CIK0000320193-submissions-001.json` starts with `accessionNumber, filingDate, reportDate, ...`].
- `files` is `[]` for small filers [LIVE].

### 1(c) Company facts: `https://data.sec.gov/api/xbrl/companyfacts/CIK##########.json` [LIVE]
- Top level: `cik, entityName, facts`.
- `facts` is keyed by taxonomy (`dei`, `us-gaap`, sometimes `ifrs-full`, `srt`), then by concept (`{label, description, units}`), then by unit (`USD`, `shares`, `USD/shares`, `pure`, …), then a list of fact objects.

Per-fact keys: `start` (only for duration facts), `end`, `val`, `accn`, `fy`, `fp`, `form`, `filed`, and `frame` (**optional**).
```json
{"start":"2025-12-28","end":"2026-03-28","val":111184000000,"accn":"0000320193-26-000013","fy":2026,"fp":"Q2","form":"10-Q","filed":"2026-05-01","frame":"CY2026Q1"}
{"start":"2025-09-28","end":"2026-06-27","val":364357000000,"accn":"0000320193-26-000020","fy":2026,"fp":"Q3","form":"10-Q","filed":"2026-07-31"}
{"end":"2009-06-27","val":895816758,"accn":"0001193125-09-153165","fy":2009,"fp":"Q3","form":"10-Q","filed":"2009-07-22","frame":"CY2009Q2I"}
```
Gotchas [LIVE]:
- `fy` and `fp` belong to the **filing** the fact came from, not to the fact's own period. A prior-year comparative in a 10-Q carries that 10-Q's fy/fp, so use `start`/`end` for the period.
- `fy` and `fp` can be **null** (seen on an 8-K).
- The same `end`/`val` repeats across several filings. `frame` is set on only one (the "best fit") fact per calendar period.
- Frame formats: `CY2019` (annual), `CY2019Q1` (quarterly duration), `CY2019Q1I` (instant).
- YTD facts (9-month etc.) have no `frame`.

Related endpoints [DOC]:
- `https://data.sec.gov/api/xbrl/companyconcept/CIK##########/us-gaap/AccountsPayableCurrent.json` returns `cik, taxonomy, tag, label, description, entityName, units` [LIVE].
- `https://data.sec.gov/api/xbrl/frames/us-gaap/AccountsPayableCurrent/USD/CY2019Q1I.json`.

### 1(e) Insider Transactions Data Sets (Forms 3/4/5, DERA quarterly zips) [DOC + LIVE listing page]
**The URL path changed in 2026.**
- 2026 Q2 and later: `https://www.sec.gov/files/datastandardsinnovation/data/insider-transactions-data-sets/{YYYY}q{N}_form345.zip` (2026q2, 2026q3 listed).
- 2006 Q1 through 2026 Q1: `https://www.sec.gov/files/structureddata/data/insider-transactions-data-sets/{YYYY}q{N}_form345.zip`.
- A robust collector should try the new path and fall back to the old one on 404. The page doesn't say whether old URLs will be moved.
- Cadence: quarterly. Rows are assigned to a quarter by **filing date**. Filings made after 5:30 pm ET on the last business day of a quarter go into the next quarter's zip. Size is about 8–13 MB per zip. The page was last updated 2026-09-30.
- Readme: `https://www.sec.gov/files/insider_transactions_readme.pdf`.

Format [DOC]: tab-delimited, UTF-8, 8 tables:
- `SUBMISSION`, `REPORTINGOWNER`, `NONDERIV_TRANS`, `NONDERIV_HOLDING`, `DERIV_TRANS`, `DERIV_HOLDING`, `FOOTNOTES`, `OWNER_SIGNATURE`.
- A W3C tabular-data metadata JSON comes with each zip. Its file name is **[UNVERIFIED]**.
- File names inside the zip are `SUBMISSION.tsv`, `NONDERIV_TRANS.tsv`, `REPORTINGOWNER.tsv`, … (a 2026 third-party loader reads `SUBMISSION.tsv` / `NONDERIV_TRANS.tsv`) [3P]. List the zip contents at runtime anyway.

**Dates are `DD-MON-YYYY`** (e.g. `22-OCT-2026`). Parse with `pd.to_datetime(s, format='%d-%b-%Y')`. Read every column as `dtype=str` first.

Key columns:
- **SUBMISSION**:
  - `ACCESSION_NUMBER` (PK), `FILING_DATE`, `PERIOD_OF_REPORT`, `DATE_OF_ORIG_SUB`.
  - `NO_SECURITIES_OWNED`, `NOT_SUBJECT_SEC16`, `FORM3_HOLDING_REPORTED`, `FORM4_TRANS_REPORTED`.
  - `DOCUMENT_TYPE` (`3`, `4`, `5`, `3/A`, `4/A`, `5/A`).
  - `ISSUERCIK`, `ISSUERNAME`, `ISSUERTRADINGSYMBOL`, `REMARKS`.
  - `AFF10B5ONE` (10b5-1 checkbox) was added in July 2025 to the 2023–2025 sets. It is **not in the readme**.
  - **[UNVERIFIED]**: the readme calls the form column `DOCUMENT_TYPE`, but a 2026 third-party loader asks for `FORM_TYPE` (it tolerates missing columns). Check the header row.
- **REPORTINGOWNER**:
  - `ACCESSION_NUMBER`, `RPTOWNERCIK`, `RPTOWNERNAME`, `RPTOWNER_RELATIONSHIP` (OFFICER/DIRECTOR/TENPERCENTOWNER/OTHER), `RPTOWNER_TITLE`, `RPTOWNER_TXT`.
  - Address fields, `FILE_NUMBER`.
  - One filing can have several owners.
- **NONDERIV_TRANS**:
  - Keys: `ACCESSION_NUMBER`, `NONDERIV_TRANS_SK` (row surrogate key).
  - `SECURITY_TITLE`, `TRANS_DATE`, `DEEMED_EXECUTION_DATE`, `TRANS_FORM_TYPE`.
  - `TRANS_CODE` (P=open-market purchase, S=open-market sale, A=grant, D=disposition to issuer, F=tax withholding, G=gift, M=option exercise; also C, E, H, I, J, L, O, U, W, X, Z).
  - `EQUITY_SWAP_INVOLVED`, `TRANS_TIMELINESS` (E/L/blank), `TRANS_SHARES`, `TRANS_PRICEPERSHARE`.
  - `TRANS_ACQUIRED_DISP_CD` (`A`/`D`), `SHRS_OWND_FOLWNG_TRANS`, `VALU_OWND_FOLWNG_TRANS`, `DIRECT_INDIRECT_OWNERSHIP`, `NATURE_OF_OWNERSHIP`.
  - Each field also has a `*_FN` footnote-ID column.
- **DERIV_TRANS**:
  - Same columns plus `DERIV_TRANS_SK`, `CONV_EXERCISE_PRICE`, `TRANS_TOTAL_VALUE`.
  - `EXCERCISE_DATE` (sic, misspelled), `EXPIRATION_DATE`.
  - `UNDLYNG_SEC_TITLE`, `UNDLYNG_SEC_SHARES`, `UNDLYNG_SEC_VALUE`.
- Join: NONDERIV_TRANS → SUBMISSION on `ACCESSION_NUMBER`.
- Amendments (`4/A`) aren't resolved for you.

---

## 2. Wikimedia pageviews (per-article) [SRC: wikimedia/analytics-aqs OpenAPI spec + mwviews 0.2.1; DOC: access policy]
I could not fetch live; WebFetch is blocked for wikimedia.org from here.

```
GET https://wikimedia.org/api/rest_v1/metrics/pageviews/per-article/{project}/{access}/{agent}/{article}/{granularity}/{start}/{end}
e.g. .../per-article/en.wikipedia/all-access/user/Albert_Einstein/daily/20260101/20260131
```
- `project`: `en.wikipedia` (a `.org` suffix and a leading `www.` are stripped); `commons.wikimedia`.
- `access`: `all-access | desktop | mobile-app | mobile-web`.
- `agent`: `all-agents | user | spider | automated`. `automated` was added 2020-04-29. Use `user` for human traffic.
- `article`: spaces become `_`, then URL-encode with `quote(title, safe='')` (e.g. `Are_You_the_One%3F`, `AC%2FDC`).
- `granularity`: `daily | monthly`. Hourly is not offered per article.
- `start`/`end`: `YYYYMMDD`. `YYYYMMDDHH` is also accepted. The range is inclusive.

Response:
```json
{"items": [
  {"project": "en.wikipedia", "article": "Albert_Einstein", "granularity": "daily",
   "timestamp": "2026010100", "access": "all-access", "agent": "user", "views": 12345}
]}
```
- `timestamp` is a **string** `YYYYMMDDHH` (`HH` = `00` for daily and monthly). `views` is an int.
- Days with no data are **omitted**, not zero. A range with no data at all returns a 404 problem JSON.
- Data starts **July 2015** ("backfilled to July 2015", pageviews FAQ). The exact first day (2015-07-01) is **[UNVERIFIED]**.
- Lag is about 24 hours or more; yesterday may be missing.

Policy [DOC, access-policy page]:
- "The API requires an HTTP User-Agent header for all requests"; clients without one "may be blocked without notice".
- Preferred format: `<client name>/<version> (<contact information>) <library/framework name>/<version>`, e.g. `MyCollector/0.1 (dawson@example.com) python-requests/2.32`.
- Rate: "There's no fixed limit on requests to the API". Wait for each request to finish before sending the next one, i.e. sequential, no parallel bursts.
- The older "100 req/s" figure seen in some places is **[UNVERIFIED]**.
- Wikimedia is rolling out stricter UA enforcement and gateway rate limits in 2025–26 (Phabricator). Send `Accept-Encoding: gzip` and back off on 429.

---

## 3. ATS job-board APIs (all public, no auth for GET)

### Greenhouse [DOC + LIVE]
- `GET https://boards-api.greenhouse.io/v1/boards/{board_token}/jobs` lists jobs. There is no pagination; one call returns every job.
- Fields per job without `content=true` [LIVE, airbnb]:
  - `id` (int, job post ID), `internal_job_id`, `title`, `updated_at`, `requisition_id`.
  - `location` (`{"name": ...}`), `absolute_url`, `metadata` (list of `{id,name,value,value_type}`).
  - `company_name`, **`first_published`**, `language`, `data_compliance`, `application_deadline`.
  - So `first_published` and `company_name` DO appear in the list response, although the docs show them only for single-job GET.
- **`?content=true` adds `content`, `departments`, `offices`** [DOC + LIVE]:
  - `departments: [{"id":57796,"name":"Sales","child_ids":[],"parent_id":73691}]`
  - `offices: [{"id":153,"name":"Berlin, Germany","location":"Berlin, Germany","child_ids":[],"parent_id":64286}]`
  - `content` is **HTML-entity-escaped** HTML (`&lt;p&gt;`); run `html.unescape()` on it.
  - It also adds `ai_disclaimer`, `include_ai_disclaimer`, `ai_opt_out_request_url` (null).
- Timestamps are ISO-8601 with offset: `"2026-07-31T07:59:51-04:00"`.
- `meta.total` at the top level is documented. I could not confirm it live because the response was truncated **[UNVERIFIED]**.
- Other endpoints: `/v1/boards/{token}/departments` and `/offices` (`render_as=list|tree`), and `/jobs/{id}`.
- No documented GET rate limit. Unknown board returns 404.

### Lever [DOC + LIVE]
- `GET https://api.lever.co/v0/postings/{site}?mode=json`. EU: `https://api.eu.lever.co/v0/postings/{site}`. HTTPS only.
- Response is a **bare JSON array** with no wrapper.
- Pagination: `skip`, `limit`. Filters: `location`, `commitment`, `team`, `department` (case-sensitive, repeatable = OR), `level`, `group=location|commitment|team`.
- Fields [LIVE, leverdemo]:
  - `id` (UUID str), `text` (title).
  - **`createdAt` = epoch milliseconds int** (`1788199939703`). Convert with `pd.to_datetime(x, unit='ms', utc=True)`.
  - **There is no `updatedAt`.** `createdAt` is not in the README field table but is present live.
  - `categories: {"commitment","department","location","team","allLocations":[...]}`. Any key can be missing.
  - `country` (ISO-2 or null), `workplaceType` (`unspecified|on-site|remote|hybrid`).
  - `hostedUrl`, `applyUrl`.
  - `description`, `descriptionPlain`, `descriptionBody`, `descriptionBodyPlain`, `opening`, `openingPlain`.
  - `lists[{text,content}]`, `additional`, `additionalPlain`.
  - `salaryRange{currency,interval,min,max}` (optional).
- Rate limit is documented only for application POST (2/s). Unknown site returns 404.

### Ashby [DOC + LIVE]
- `GET https://api.ashbyhq.com/posting-api/job-board/{JOB_BOARD_NAME}?includeCompensation=true`.
- Top level: `jobs` (live). `apiVersion: "1"` is documented but I could not confirm it live (response truncated) **[UNVERIFIED]**.
- `jobs[]` fields [LIVE]:
  - **`id`** (UUID string; present even though the docs don't list it), `title`, `department`, `team`.
  - `employmentType` (`FullTime|PartTime|Intern|Contract|Temporary`).
  - `location` (string), `secondaryLocations[{location, address{postalAddress{...}}}]`.
  - `publishedAt` (`"2024-03-04T14:29:08.532+00:00"`), `isListed` (bool), `isRemote` (true for Remote **and** Hybrid), `workplaceType` (`OnSite|Remote|Hybrid`).
  - `address{postalAddress{addressLocality,addressRegion,addressCountry,postalCode}}`.
  - `jobUrl`, `applyUrl`, `descriptionHtml`, `descriptionPlain`.
  - `shouldDisplayCompensationOnJobPostings`.
  - `compensation{compensationTierSummary, scrapeableCompensationSalarySummary, compensationTiers[], summaryComponents[]}`.
- No pagination. No documented rate limit.

### SmartRecruiters [DOC (official OpenAPI reference); live fetch blocked by robots]
- `GET https://api.smartrecruiters.com/v1/companies/{companyIdentifier}/postings`.
- Auth is **optional**. It is needed only for `destination=INTERNAL|INTERNAL_OR_PUBLIC` (`x-smarttoken` header or OAuth scope `internal_postings_read`).
- Query params:
  - `limit` (**max 100**), `offset`, `q`.
  - `country`, `region`, `city`, `department` (id), `jobAdId`, `language`.
  - `locationType=REMOTE,HYBRID,ONSITE,ANY`, `releasedAfter` (ISO 8601).
  - `customField` (`custom_field.<ID>=<VALUE_ID>`).
- Response: `{"offset", "limit", "totalFound", "content": [...]}`. Paginate until `offset >= totalFound`.
- `content[]` fields:
  - `id`, `uuid`, `name` (title), `refNumber`, `ref` (detail URL), `jobAdId`, `defaultJobAd`.
  - `company{identifier,name}`, `releasedDate` (`"2014-02-19T19:36:42.346Z"`).
  - `location{country,region,city,remote,hybrid,latitude,longitude}` (`country` is lowercase e.g. `"us"` in the doc example).
  - `industry{id,label}`, `department{id,label,description}`, `function{id,label}`, `typeOfEmployment{id,label}`.
  - `experienceLevel{id,name}`, `customField[{fieldId,fieldLabel,valueId,valueLabel}]`.
  - `creator{name}`, `language{code,label,labelNative}`, `visibility`.
- No description in the list. Fetch detail with `GET .../postings/{postingId}`.
- **Unknown company returns 200 with `totalFound: 0`**, not 404 [3P].
- Rate: SmartAPIs allow 10 req/s and 8 concurrent "for most endpoints", with `X-RateLimit-*` headers and 429 [DOC]. Whether this applies to the public postings endpoint is **[UNVERIFIED]**.

---

## 4. Apple

### iTunes Lookup [LIVE]
- `GET https://itunes.apple.com/lookup?id=284882215&country=us`.
- Comma-separated ids work: `?id=284882215,389801252&country=us&entity=software` returned `resultCount: 2`.
- Response: `{"resultCount": N, "results": [ {...} ]}`. Selected keys for an app (`wrapperType`/`kind` = `"software"`):
```json
{"trackId": 284882215, "trackName": "Facebook", "bundleId": "com.facebook.Facebook",
 "averageUserRating": 4.52647, "userRatingCount": 28905415,
 "averageUserRatingForCurrentVersion": 4.52647, "userRatingCountForCurrentVersion": 28905415,
 "version": "584.1", "currentVersionReleaseDate": "2026-10-23T09:58:23Z", "releaseDate": "2019-02-05T08:00:00Z",
 "price": 0.00, "formattedPrice": "Free", "currency": "USD", "genres": ["Social Networking"], "primaryGenreId": 6005,
 "artistId": 284882218, "artistName": "Meta Platforms, Inc.", "sellerName": "...", "minimumOsVersion": "15.1",
 "fileSizeBytes": "529373184", "contentAdvisoryRating": "12+", "releaseNotes": "...", "description": "...", "trackViewUrl": "..."}
```
- `averageUserRating` is a float with long binary noise. Round it.
- `userRatingCount` updates intraday: 28,905,415 then 28,915,446 minutes later.
- **`*ForCurrentVersion` fields equal the all-time values**. They are not version-specific any more, so don't use them for version-level deltas.
- `fileSizeBytes` is a **string**.
- Rate: "approximately 20 calls per minute (subject to change)". That figure is documented for the Search API [DOC]; assume the same for lookup.

### Apple Marketing Tools RSS (top charts) [LIVE]
- Current host: **`https://rss.marketingtools.apple.com`**. The old `rss.applemarketingtools.com` 302-redirects there. Format:
  `https://rss.marketingtools.apple.com/api/v2/{country}/apps/{chart}/{limit}/apps.json`
- Working charts: **`top-free`** and **`top-paid`** (both verified live).
- **`top-grossing` returns a client error (not available in v2).**
- The legacy `https://itunes.apple.com/{cc}/rss/topgrossingapplications/limit=N/json` exists in older tooling. I could not verify it (robots-blocked) **[UNVERIFIED]**.
- `limit`: the generator offers 10/25/50. 100 reportedly works by editing the URL [3P].
- There is no category/genre segment in v2.
- Response:
```json
{"feed": {"title": "Top Paid Apps", "id": "<self url>", "author": {"name": "...", "url": "..."},
  "links": [{"self": "..."}], "copyright": "...", "country": "us", "icon": "...",
  "updated": "Mon, 26 Oct 2026 19:30:51 +0000",
  "results": [{"artistName": "Meta Platforms, Inc.", "id": "6760173601", "name": "Muse from Meta",
               "releaseDate": "2026-09-08", "kind": "apps", "artworkUrl100": "...", "genres": [], "url": "https://apps.apple.com/us/app/.../id6760173601"}]}}
```
- `id` is a **string**.
- Rank is the array position.
- `releaseDate` is the app's release date, not the chart date.
- `genres` came back empty.
- `updated` is in RFC-2822 format.

---

## 5. yfinance (latest PyPI = **1.7.0**, uploaded 2026-08-26; source read from the wheel) [SRC]
- Deps: `curl_cffi>=0.15` (1.5.2 fixed breakage with curl_cffi>=0.16; main branch now pins >=0.17), `pandas` (pandas 3 supported since 1.2.x), `protobuf`, `peewee`, `beautifulsoup4`, `lxml`.
- Since 1.4.0 curl_cffi is optional, with fallback to `requests`.
- Passing a custom session must be a curl_cffi or requests Session, or it raises `YFDataException`.

### `Ticker.history(period='1mo', interval='1d', start=None, end=None, prepost=False, actions=True, auto_adjust=True, back_adjust=False, repair=False, keepna=False, rounding=False, timeout=10, raise_errors=False)`
- **`auto_adjust=True` is the default**, here and in `yf.download` (`download(..., actions=False, auto_adjust=True, multi_level_index=True, ...)`).
- With `auto_adjust=True`, OHLC are scaled by `Adj Close/Close` and `Adj Close` is dropped. Columns: `Open, High, Low, Close, Volume, Dividends, Stock Splits` (+ `Capital Gains` for funds).
- With `auto_adjust=False` you also get `Adj Close`.
- Index name: `Date` (daily) or `Datetime` (intraday). It is **tz-aware, in the exchange tz** (e.g. America/New_York, midnight-stamped for daily).
- `end` is exclusive.
- Intraday is limited to the last 60 days.
- `period` options: `1d,5d,1mo,3mo,6mo,1y,2y,5y,10y,ytd,max`.
- `raise_errors` is deprecated. Use `yf.config.debug.hide_exceptions = False` to get exceptions.

### `Ticker.get_earnings_dates(limit=12, offset=0)`; property `earnings_dates` uses limit=12
- `limit > 100` raises `ValueError`.
- Since summer 2025 it **scrapes HTML**: `https://finance.yahoo.com/calendar/earnings?symbol={T}&offset={o}&size={25|50|100}`. The old visualization API stopped updating ("In Summer 2025, Yahoo stopped updating the data at this endpoint").
- Returns a DataFrame indexed by **`Earnings Date`**. Columns: `EPS Estimate`, `Reported EPS`, `Surprise(%)` (renamed from `Surprise (%)`).
- Only `Symbol` and `Company` are dropped. Any extra column Yahoo adds (e.g. `Event Name`) passes through **[UNVERIFIED]**.
- `-` is parsed as NaN. Future dates have NaN in `Reported EPS`.
- Index tz: the strings `"... at 4 PM EDT"` are parsed with `'%B %d, %Y at %I %p'`, and `EDT/EST` is mapped to `America/New_York`. So the index is **tz-aware America/New_York at hour precision** (e.g. 06:00 or 16:00). That hour is a usable BMO/AMC hint.
- Returns `None` (and logs) if no table is found.
- Results are cached per `limit`.

### `Ticker.calendar` → dict (quoteSummary module `calendarEvents`)
- Keys (only present if Yahoo returns them):
  - `'Dividend Date'`, `'Ex-Dividend Date'` (datetime.date).
  - `'Earnings Date'` (**list** of datetime.date, often 2 = window).
  - `'Earnings High'`, `'Earnings Low'`, `'Earnings Average'`, `'Revenue High'`, `'Revenue Low'`, `'Revenue Average'`.
- Gotcha: dates come from `datetime.fromtimestamp(ts).date()`, which uses the **machine's local tz**. Run in UTC or ET to avoid off-by-one dates.

### Options
- `Ticker.options` returns a tuple of expiry strings `'YYYY-MM-DD'`, from `https://query2.finance.yahoo.com/v7/finance/options/{T}`.
- `Ticker.option_chain(date=None, tz=None)` returns namedtuple `Options(calls, puts, underlying)`.
  - `date` must be one of `options` or it raises `ValueError`.
  - `underlying` is the raw quote dict.
  - Empty result gives all three as `None`.
- `calls`/`puts` columns, in this exact order: `contractSymbol, lastTradeDate, strike, lastPrice, bid, ask, change, percentChange, volume, openInterest, impliedVolatility, inTheMoney, contractSize, currency`.
- `lastTradeDate` is tz-aware **UTC** unless `tz=` is passed.
- This is current snapshot only: **no historical chains**.

### `yf.Calendars(start, end).get_earnings_calendar(market_cap=None, filter_most_active=True, start, end, limit=12≤100, offset=0)`
- This is a free market-wide earnings calendar with timing. It POSTs to `https://query1.finance.yahoo.com/v1/finance/visualization` (entityIdType `sp_earnings`).
- Index `Symbol`. Columns: `Company`, `Marketcap`, `Event Name`, `Event Start Date` (datetime), **`Timing`** (from `startdatetimetype`), `EPS Estimate`, `Reported EPS`, `Surprise(%)`.
- The `Timing` values themselves (expected BMO/AMC/TAS/TNS) are **[UNVERIFIED]**.
- `filter_most_active=True` drops less active names. Set it to False for full coverage.

### Known 2025–2026 breakage
- Yahoo rate limiting raises `YFRateLimitError` (widespread in 2025). There was also an "Invalid Cookie" 401 (#2495, 2025).
- Earnings-dates API endpoint went stale in summer 2025; now HTML scrape.
- `get_news()` returns `[]` (#3004, opened 2026-10-19, `/xhr/ncp` 404).
- pandas 3.1 `Pandas4Warning`s (#3009).
- Brotli CVE in curl_cffi (#3012).
- Cache Yahoo calls and throttle to about 1–2 req/s per IP.

---

## 6. pytrends / Google Trends
- **pytrends is archived**: "archived by the owner on Apr 17, 2025. It is now read-only." Last PyPI release is 4.9.2 (2023-04-13). The README asks for maintainers.
- In 2026 it fails frequently: `TooManyRequestsError` (429) on the first call because of stale cookie/session handling, and `trending_searches()` returns 404 [3P]. Treat it as unreliable or broken.
- **Official Google Trends API (alpha)**, announced July 2025 [DOC]:
  - Access is only for selected alpha testers. Apply via the form linked at `https://developers.google.com/search/apis/trends`; priority goes to a clear use case and willingness to give feedback.
  - The pilot is "quite small" (Mueller, Aug 2025). There was no GA date as of Oct 2026.
  - Data: rolling 5-year window; daily/weekly/monthly/yearly aggregation; country and sub-region.
  - Values are **consistently scaled, not 0–100 normalized**, so increments can be appended.
  - It allows comparing dozens of terms.
  - Quotas are not published.

---

## 7. Stooq
- Historical URL: `https://stooq.com/q/d/l/?s={sym}.us&i=d` with optional `&d1=YYYYMMDD&d2=YYYYMMDD`. Returns CSV `Date,Open,High,Low,Close,Volume` **[UNVERIFIED; robots-blocked from here]**.
- **Since about March 2026 Stooq requires an API key.** Without one it returns an HTML instruction page instead of CSV (pandas-datareader issue #1012).
  - Get a key at `https://stooq.com/q/d/?s=spy.us&get_apikey`.
  - The query-parameter name for the key (likely `apikey=`) and whether a captcha is shown are **[UNVERIFIED]**.
  - Users also report cookie requirements and per-IP download quotas (multi-day IP blocks) [3P, Apr 2026].
- **pandas-datareader 0.11.0 (June 2026) removed the Stooq reader** (along with Yahoo, AlphaVantage, IEX, Tiingo, Quandl, …). Write your own requests code.
- Detect failure by checking that the body starts with `Date,`.

---

## 8. Free historical option quotes: what is actually free
- **Massive (formerly Polygon.io; the brand is now "Massive", massive.com)**, Options Basic $0 [DOC pricing page]:
  - **5 API calls/minute, 2 years history, end-of-day data**, minute aggregates, reference data, corporate actions, technical indicators.
  - **No** trades/quotes (bid/ask), **no** Greeks/IV, **no** snapshot, **no** open interest. "Daily open interest" is listed only from Options Starter, $29/mo.
  - So the free tier gives per-contract OHLCV bars, not historical quote chains.
  - The API host/path after the rename (`api.polygon.io` vs a massive.com host) is **[UNVERIFIED]**.
- **Alpha Vantage `HISTORICAL_OPTIONS`**:
  - Request: `https://www.alphavantage.co/query?function=HISTORICAL_OPTIONS&symbol=IBM&date=YYYY-MM-DD&apikey=KEY`. One full chain per symbol per date. Dates after 2008-01-01 ("15+ years"); one secondary source says 2018-01-01. Includes IV and Greeks.
  - Free tier is **25 requests/day** total (official support page). One 2025 article adds 5/min.
  - **Whether HISTORICAL_OPTIONS works on a free key is contested**: one 2026 article says "premium endpoint: the free tier allows 25 requests a day", another says options need premium **[UNVERIFIED]**. The `demo` key returns only an `Information` message.
  - `REALTIME_OPTIONS` is premium. Cheapest premium: $49.99/mo for 75 req/min.
  - Response fields (`data[]` with `contractID, symbol, expiration, strike, type, last, mark, bid, bid_size, ask, ask_size, volume, open_interest, date, implied_volatility, delta, gamma, theta, vega, rho`) are **[UNVERIFIED]**.
- **Cboe delayed quotes JSON** (free, current snapshot only, about 15-min delayed) [LIVE]:
  - `https://cdn.cboe.com/api/global/delayed_quotes/options/{SYM}.json` 302-redirects to **`https://cdn-api.cboe.com/api/global/delayed_quotes/options/{SYM}.json`**.
  - Response: `{"timestamp": "2026-10-26 19:35:24", "data": {"options": [...] , ...}}`.
  - Per-contract keys: `option` (OCC symbol, e.g. `AAPL261026C00245000`), `bid, bid_size, ask, ask_size, iv, open_interest, volume, delta, gamma, vega, theta, rho, theo, change, open, high, low, tick, last_trade_price, last_trade_time, percent_change, prev_day_close`.
  - Other keys under `data` (underlying price etc.) were not visible **[UNVERIFIED]**.
  - **This is the best free source of bid/ask + IV + OI if you snapshot it yourself daily.** It has no history.
- **DoltHub `post-no-preference/options`**: free public Dolt database (clone or SQL on the web), updated by a volunteer since 2021. Tables, columns and coverage are **[UNVERIFIED]**.
- yfinance options: current snapshot only.
- Cboe DataShop: paid.

---

## 9. Earnings calendars with announcement time

### Nasdaq [SRC: openbb-nasdaq 2.0.0 and finance-calendars 0.0.7; live fetch robots-blocked]
- `GET https://api.nasdaq.com/api/calendar/earnings?date=YYYY-MM-DD`
- Headers: Nasdaq blocks non-browser clients. Send:
  - `User-Agent: <real browser UA>`
  - `Accept: application/json, text/plain, */*`
  - `Accept-Language: en-US,en;q=0.9`
  - `Origin: https://www.nasdaq.com`
  - `Referer: https://www.nasdaq.com/`
  - `Accept-Encoding: gzip` (avoid `br` unless you have brotli installed)
- Envelope: `{"data": {"asOf": "Mon, Oct 26, 2026", "headers": {...}, "rows": [...]}, "message": ..., "status": {"rCode": 200, "bCodeMessage": ..., "developerMessage": ...}}`.
  - Check `status.rCode == 200`.
  - `data.rows` can be `null` or empty on weekends and holidays.
  - Parse `asOf` with `'%a, %b %d, %Y'`.
- Row keys:
  - `symbol`, `name`, **`time`**, `marketCap` (`"$1,234,567"`), `fiscalQuarterEnding` (`"Sep/2026"`, parse with `%b/%Y`), `epsForecast` (`"$1.23"` or `"($0.05)"` for negatives), `noOfEsts`, `lastYearRptDt` (`"%m/%d/%Y"`), `lastYearEPS`.
  - After the report: `eps`, `surprise`.
  - All values are strings, with `"N/A"` for missing.
- `time` values look like `time-pre-market`, `time-after-hours`, `time-not-supplied`. OpenBB strips the `time-` prefix. The exact value set is **[UNVERIFIED]**.

### Yahoo
- `yf.Calendars().get_earnings_calendar(...)` returns a `Timing` column (see §5).
- `Ticker.get_earnings_dates()` gives an hour-precision ET timestamp.

### Alpha Vantage `EARNINGS_CALENDAR`
- `https://www.alphavantage.co/query?function=EARNINGS_CALENDAR&horizon=3month|6month|12month[&symbol=X]&apikey=KEY` returns **CSV** with `symbol,name,reportDate,fiscalDateEnding,estimate,currency` [3P].
- **There is no BMO/AMC column** in that listing. A newer `timeOfTheDay` column is **[UNVERIFIED]**.

### SEC 8-K Item 2.02 as a proxy (verified mechanics)
- Filter submissions `form in {'8-K','8-K/A'}` and `'2.02' in items.split(',')`.
- Take `acceptanceDateTime` (true UTC) and convert to America/New_York:
  - before 09:30 → BMO
  - at or after 16:00 → AMC
  - in between → during market hours or ambiguous
- Verified example: an AAPL filing accepted 06:01 ET shows as `10:01:26Z`.
- EDGAR accepts filings 6:00–22:00 ET on business days.
- **`filingDate` rolls to the next business day for submissions started after 5:30 pm ET** (Forms 3/4/5 have a 10 pm cutoff). Always use `acceptanceDateTime`, not `filingDate`, for event timing.
- Caveats:
  - The 8-K is usually filed minutes after the press release, but it can lag. Item 2.02 allows up to 4 business days.
  - Some companies furnish the release before the call, or file after midnight UTC.
  - Some issuers (and most foreign filers using 6-K) won't have 2.02 at all.
  - It is a good proxy for US filers. Spot-check against Nasdaq's `time` field.
