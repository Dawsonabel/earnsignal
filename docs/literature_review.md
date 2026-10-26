# Literature Review: Alternative-Data Signals for Earnings Surprises

*Prepared 2026-10-26. Effect sizes are as reported in the cited source. When only an abstract or a secondary summary could be read, the text says so. "[n/r]" means a number was not retrievable.*

## Summary table

| # | Signal | Predicts revenue/EPS surprise? | Predicts announcement / post-announcement return? | Decay / crowding | Free data? |
|---|---|---|---|---|---|
| 1 | Google search (product SVI) | Revenue: strong. EPS: weak | 3-day EA return: modest (~27 bp per 1 SD) | Single 2004-08 sample, unpublished; ticker-level ASVI results mixed | Yes (Google Trends, noisy) |
| 2 | Wikipedia page views | No direct evidence | Weak; attention proxy only | Little rigorous work | Yes (clean API) |
| 3 | Job postings | Predicts future revenue/earnings *growth*; surprise evidence thin | Market prices it within ~2 days | Data widely sold since ~2015 | Partial (scraping) |
| 4 | App downloads | Yes, mainly for apps with no visible monetization | ~1%/month L/S, concentrated around EAs | Disappears when firms disclose app metrics | Ranks only (free); downloads paid |
| 5 | Consumer reviews / web traffic | Yes (Amazon ratings, Similarweb) | 55-80 bp/month L/S | Strongest in small, high-idiosyncratic-vol stocks | Partial (scraping, ToS issues) |
| 6 | Insider open-market buys | Predicts EA returns and news (opportunistic buys only) | 82 bp/mo VW, 180 bp/mo EW (opportunistic) | Survived SOX; still followed widely | Yes (EDGAR Form 4) |
| 7 | Peer / early-reporter surprises, customer links | Yes (forecast-error spillover) | Mixed: underreaction (Ramnath) vs overreaction (Thomas-Zhang). Customer momentum decayed | Customer momentum ~halved after 2004 | Mostly yes |
| 8 | SUE autocorrelation / PEAD | SUE persistence still exists but is falling | Drift largely gone in non-microcaps since ~2006 | Strong decay | Yes, if consensus is available |
| 9 | Satellite / card / scanner (paid) | Yes, strongly | 3-5% EA-window spreads historically | Paid data raises price informativeness | No |
| 10 | Taiwan / Korea monthly data | Within Taiwan: yes. Cross-border to US firms: **no peer-reviewed evidence found** | Untested for US names | Unknown | Yes (MOPS, KCS) |

---

## A. Evidence by signal family

### 1. Google search volume
- **Da, Engelberg & Gao, "In Search of Fundamentals"** (working paper, 2011 draft; 865 firms, product-level SVI for each firm's most-advertised Nielsen brand, 2004-2008):
  - **Revenue surprises:** strong. The SVI-change coefficient is 0.825 (t = 9.86); a 1 SD rise ≈ +0.23 SD of standardized unexpected revenue. The coefficient shrinks to 0.116 (t = 2.43) after controlling for the lagged revenue surprise, so much of the signal overlaps with revenue momentum.
  - **Earnings surprises:** weak. SUE is insignificant with special items and only 10%-significant without them.
  - **3-day announcement return:** 1 SD ≈ +27 bp (t ≈ 2.6). The effect is concentrated in growth firms, single-brand firms and firms with volatile accruals.
  - **Post-announcement drift:** the 10%-significant result vanishes once the announcement return is controlled for, so the information is priced by the event.
  - The paper does not appear to have been published in a journal, which is a caveat on its own.
- **Da, Engelberg & Gao (2011, JF), "In Search of Attention"** (Russell 3000, 2004-2008, ticker SVI):
  - Abnormal SVI (ASVI) measures *retail attention*: it predicts +18.7 bp (week 1) and +14.9 bp (week 2) per 1 SD, then reverses (−28.9 bp, weeks 5-52, t = 1.69).
  - This is a price-pressure effect, not a fundamentals effect.
- **Drake, Roulstone & Thornock (2012, JAR):** search rises about two weeks before an earnings announcement (EA). More pre-EA search means prices move earlier and the EA reaction is *smaller*.
- **Later work:** Bijl et al. (2016, IRFA; S&P 500, 2008-2013) find high search predicts slightly *negative* returns, unprofitable after costs. Lawrence et al. (2016) find Google Trends less informative than Yahoo search. A 2017 BI thesis (2004-2016) contradicts parts of DEG (2011) for large caps.
- **Data quality:** Google Trends returns a fresh random sample after each daily cache reset, so repeated pulls differ ([arXiv 2504.07032](https://arxiv.org/pdf/2504.07032)).
- **Verdict:** product SVI is a credible free *revenue-surprise* signal for consumer brands, but it rests on one 2004-2008 sample. Ticker ASVI is an attention and reversal effect.

### 2. Wikipedia page views
- Moat et al. (2013, *Scientific Reports*; DJIA pages, 2007-2012): "up to" 141% from index timing. Best-case, no costs, likely data-mined.
- Pyun (2024, *Economics Letters*): long-short portfolios on *changes* in page views earn significant alphas. Magnitudes [n/r].
- Focke, Ruenzi & Ungeheuer (2020, RFS) use Wikipedia-based attention measures. Advertising raises attention but has **no significant effect on short-term returns**.
- An Aalto thesis (577 European firms, 8,702 EAs, 2008-2012) finds page views jump 20-30% on EA day and raise the earnings-response coefficient.
- **I found no study showing that page views predict revenue or EPS surprises.**
- **Verdict:** a clean, free *attention* covariate, useful for conditioning: high attention means less drift and more preemption. Weak as a standalone directional signal.

### 3. Job postings / hiring
- **Gutierrez, Lourie, Nekrasov & Shevlin (2020, *Management Science*)** (LinkUp; 2,255 firms, 2007-2016):
  - Changes in postings predict future revenue, earnings, SG&A and headcount growth, more strongly for growth hiring than for replacement hiring.
  - Prices react within **two days** of posting changes, mostly in 2012-2016, after the data became commercially available.
  - Coefficients [n/r]. I found no published result that postings predict *analyst forecast errors* or announcement returns.
- Belo, Lin & Bazdresch (2014, JPE): high hiring rates predict *lower* returns (10.4%/yr EW low-minus-high). A risk effect, not a surprise effect.
- Indriawan et al. (2023 working paper, Burning Glass): posting-based labor competition predicts lower returns.
- **Verdict:** postings carry fundamental information, but the market appears to absorb it quickly. The case for a pre-EA *surprise* signal is unproven, and the data has been widely sold since about 2015.

### 4. Mobile apps
- **Chen, Liu & Wu (2024 working paper; Sensor Tower, 831 firms, 2012-2023):**
  - Lagged download growth predicts next-quarter earnings and SUE.
  - Long-short on abnormal downloads earns **99 bp/month EW and 93 bp/month VW**, with returns clustered around subsequent EAs.
  - SUE predictability exists **only for apps without visible monetization** (subscriptions, in-app purchases, ads).
  - It **disappears for firms that disclose app metrics** in SEC filings.
- Yuan & Chen (ICIS 2019; Apptopia, 326 firms, 2015-2017): small positive next-month return effects. A short, preliminary paper that does not test surprises.
- **Verdict:** real evidence, but download estimates are paid data. Free App Store and Google Play rank charts are only a coarse proxy, and no study validates ranks.

### 5. Consumer reviews and web traffic
- **Huang (2018, JFE)** (14.5M Amazon reviews, 346 firms, 2004-2015):
  - Abnormal ratings predict revenue and earnings surprises.
  - Long-short returns are **55.7-73 bp/month**, with no reversal.
  - Strongest in small and high-idiosyncratic-volatility stocks, where arbitrage is costly.
- Green, Huang, Wen & Zhou (2019, JFE) (Glassdoor employee reviews, 2008-2016, large firms): rating changes predict one-quarter-ahead earnings surprises; **0.84%/month alpha**.
- Armstrong, Konchitchki & Zhang (2025, TAR) (Similarweb web traffic, about 1,000 large firms): traffic growth predicts revenue beats and misses and abnormal returns, strongest in retail-held firms. Magnitudes [n/r].
- **Verdict:** among the better-supported families. Free scraping is feasible for some sources (app-store reviews, some e-commerce), but sample coverage and terms of service are the constraints.

### 6. Insider open-market purchases
- **Lakonishok & Lee (2001, RFS)** and **Jeng, Metrick & Zeckhauser (2003, REStat)**, 1975-1996: information is in *purchases* (more than 50 bp/month, about half within one month), not sales, and concentrated in small firms.
- **Cohen, Malloy & Pomorski (2012, JF; 1986-2007):**
  - "Opportunistic" trades (not repeated in the same calendar month over the prior three years) earn **82 bp/month VW (t = 2.15) and 180 bp/month EW (t = 6.07)**.
  - Routine trades earn about zero.
  - Opportunistic buys predict *announcement returns at future EAs* (coefficient 0.30, t = 1.96), plus news events, analyst revisions and management forecasts.
- **Ali & Hirshleifer (2017, JFE; 1989-2014):** insiders whose past trades in the **[−21, −2] window before quarterly EAs** were profitable earn **>100 bp/month** on later trades.
- **Post-SOX:** two-day post-filing returns to purchases *rose* (0.32% before SOX vs 1.13% after; Hossain 2010). This is a short-window measure that is not fully capturable.
- **Caveat:** most firms impose blackout windows around EAs (Bettis, Coles & Lemmon 2000, JFE), so literal "pre-earnings" purchases are rare. The usable signal is open-window opportunistic purchases predicting the *next* EA.
- **Verdict:** the strongest documented free signal. It is widely followed, so expect a smaller effect than in 1986-2007, but no paper I found shows it has died.

### 7. Intra-industry information transfer and economic links
- **Foster (1981, JAE):** early announcers' earnings move peers' prices.
- **Ramnath (2002, JAR):** the first announcer's forecast error predicts later announcers' forecast errors. Analysts and investors *underreact*, so later announcers' returns are predictable.
- **Thomas & Zhang (2008, JAR; 1973-2005, 245,742 firm-quarters):** the opposite *price* result.
  - Late announcers *overreact* to peers' news and reverse at their own EA.
  - Long the smallest and short the largest peer-induced reactions, held [−1, +1] around the firm's own EA: about **1% per quarter**.
  - **Implication:** the peer signal is reliable for predicting the *fundamental* surprise (Ramnath), but the *price* already overshoots, so a contrarian EA-window trade is the documented edge.
- **Cohen & Frazzini (2008, JF):** customer-return momentum earns **>150 bp/month**.
  - A 2023 replication ([arXiv 2301.11394](https://arxiv.org/abs/2301.11394v1)): **1.52%/month EW in 1978-2004 vs 0.62% in 2005-2018**; VW 1.30% → 0.62% (t = 1.54, insignificant). Clear decay.
  - Madsen (2017, JAR): concentrated *before the supplier's EA*. Burt & Hrdlicka (2021, JFQA): largely shared momentum.

### 8. PEAD and SUE autocorrelation (baseline)
- **Bernard & Thomas (1989, 1990):**
  - Seasonal-difference SUE autocorrelations are about **0.34, 0.19, 0.06, −0.24** at lags 1-4.
  - Drift was about 8-9% per quarter (about 35% annualized, pre-cost, 1974-1986).
  - 25-30% of the drift accrued in the 3-day windows of *subsequent* EAs.
- **Decay evidence:**
  - Chordia et al. (2009, FAJ): drift is 0.04%/month in liquid vs 2.43% in illiquid stocks; costs eat 70-100%.
  - Martineau (2022, CFR): non-microcap drift is insignificant after 2005; since 2011 prices adjust on EA day.
  - Kettell, McInnis & Zhao (2022 WP): the hedge return fell from about 5% to 3% or less, largely because **SUE persistence fell**.
  - Subrahmanyam (2025 WP; 2001-2024): drift t = 2.18 with all stocks, **t = 1.43 ex-microcaps**.
- **Verdict:** SUE autocorrelation is still a useful *prior for the next surprise*, though weaker than in the 1980s. As a return strategy in liquid names it is essentially dead.

### 9. Paid alternative data (context)
- **Katona, Painter, Patatoukas & Zeng (2025, JFQA; satellite parking lots, 44 retailers, 2011-2017):**
  - 3-day EA buy-minus-sell spread of **4.76%**, with the short side about twice the long side.
  - Informed short sellers profited while retail investors bought ahead of bad news.
  - Liquidity worsened; price discovery did not improve measurably for about three years.
- **Revenue and surprise prediction from paid data:**
  - Froot et al. (2017, JFE; device-based sales): 3.4% excess announcement returns.
  - Dichev & Qian (2022, JAE; Nielsen scanner data): 14-19%/yr.
  - Gupta, Leung & Roscovan (2022, JPM; card data): about 16%/yr net.
- **Zhu (2019, RFS):** alt data raises price informativeness. Dessaint, Foucault & Frésard (2024, JF) find it shifts analysts toward short-horizon accuracy.
- **Implication:** funds already price near-term revenue from paid data in covered names. A free-data edge is likeliest in uncovered small caps and non-consumer firms.

### 10. Taiwan and Korea monthly data
- **Within Taiwan:** monthly sales disclosures preempt earnings news and weaken drift (Lu & Xin 2024, TIJA). Record monthly revenue shows overreaction then one-month drift (Lai et al. 2025, SSRN).
- **Cross-border:** customer-country returns predict producer-country returns (Albuquerque, Ramadorai & Watugala 2015, JFE; 43 countries). Customer industries lead supplier industries (Shahrur et al. 2010, FAJ). Both run *opposite* to "Taiwan supplier sales → US customer surprise".
- **I found no peer-reviewed test showing that Taiwanese or Korean monthly data predict US firms' earnings surprises.** Sell-side and press use is common (for example, monthly sales of Apple's Taiwanese suppliers) but anecdotal.
- **Verdict:** a free, official, timely signal (MOPS around the 10th of each month; Korean 10- and 20-day exports) that is *untested* rather than *disproven*. This is a genuine research opportunity, but every claim needs out-of-sample validation.

---

## B. Buying options ahead of earnings

- **Implied volatility runs up into EAs and collapses after them.**
- **Dubinsky, Johannes, Kaeck & Seeger (2019, RFS):** anticipated EA uncertainty is large, time-varying and predictive (correlation with realized about 0.85), but implied jump volatility *exceeds* realized on average. Their 2017 slides (50 most active names) show announcement-day straddles averaging **about −8% per event**.
- **Gao, Xing & Zhang (2018, JFQA; 1996-2013):**
  - Straddles bought 3 days before and closed on the EA day earn **+2.6% to +3.3%** (EW, t ≈ 14). This captures the IV run-up.
  - Holding *through* the reaction ([0, +1]) earns −0.33% (EW, insignificant) and **−1.37% (open-interest-weighted, t = −3.07)**.
  - Gains concentrate in illiquid, wide-spread, low-coverage names. Returns use **mid-quotes**, so executable returns are lower.
- **Chung & Louis (2017, JEF):** pre-EA straddles are positive after quiet periods. **Barth & So (2014, TAR):** bellwether EAs carry a priced volatility premium, so their straddles are expensive.
- **Milian (2023, JRFM):** weekly straddles are mispriced conditional on *historical vs implied move*. This is the only conditional edge documented.
- **De Silva, Smith & So (2025, *Review of Finance*; 2010-2021, 32,791 EAs):**
  - Retail option buyers lose **5-9% on average and 10-14% around high-expected-volatility EAs**.
  - Losses come from overpaying relative to realized volatility, effective spreads of about 9-10%, and holding about two weeks after volatility collapses.
- **Bryzgalova, Pavlova & Sikorskaya (2023, JF; Nov 2019 – Jun 2021):**
  - Retail favors cheap, short-dated (weekly) calls with quoted spreads of about 12-13%.
  - Losses are about $1.14B before costs, plus about $4.1B in spread costs and $0.8B in commissions.
- **Bottom line:**
  - Buying options *through* the announcement is, on average, a losing or zero-edge trade for liquid names, and clearly negative for retail.
  - The documented positive return comes from *exiting before* the event, in illiquid names, at mid-quotes.
  - A directional surprise signal must beat implied-move pricing **and** spreads of about 10%. The weak ICs above almost certainly do not, so stock or no-trade expression is the safer default.

---

## C. What this implies for a $0-budget platform

**Most promising with free data**
1. **Opportunistic insider purchases** (EDGAR Form 4): strongest literature, survived SOX, and pairs naturally with the next EA.
2. **Peer and early-announcer surprises** for predicting the *fundamental* surprise of late reporters (Ramnath). Be careful on the *price* side: Thomas & Zhang show overreaction.
3. **Product-level Google Trends** for consumer brands' *revenue* surprises. Pull multiple samples to average out Google's sampling noise, and treat 2004-2008 effect sizes as upper bounds.
4. **SUE autocorrelation** as the baseline prior: it is the benchmark every alt signal must beat.
5. **Taiwan MOPS monthly revenue mapped to US customers:** novel and free, but no academic evidence yet. Validate strictly out of sample.

**Likely arbitraged or weak**
- PEAD in non-microcaps.
- Customer momentum (VW insignificant after 2004).
- Ticker-level ASVI (attention with reversal).
- Job postings (priced within days).
- Wikipedia (no fundamentals evidence).
- Anything covered by paid panels (satellite, card, scanner) for large consumer names.
- McLean & Pontiff (2016): predictors lose about 26% out of sample and **about 58% after publication**. Chen & Velikov (2023): the average anomaly nets about **8 bp/month** after spreads and post-publication decay.
- Budget for published effect sizes to be halved or worse.

**Sample sizes**
- For a rank IC with independent events, t ≈ IC × √N. Computed:

  | IC | N for t = 1.96 | N for 80% power at 5% | N for t = 3.0 (Harvey-Liu-Zhu hurdle) | N for 80% power at t = 3 |
  |---|---|---|---|---|
  | 0.10 | 384 | 785 | 900 | ~1,480 |
  | **0.05** | **~1,540** | **~3,140** | **3,600** | **~5,900** |
  | 0.03 | ~4,270 | ~8,720 | 10,000 | ~16,400 |
  | 0.02 | ~9,600 | ~19,600 | 22,500 | ~36,900 |

- Events cluster by season and industry, so the *effective* N is smaller. Test the time series of per-season ICs, or cluster by date. At 500-2,000 covered EAs per quarter, an IC of 0.05 needs roughly **1-3 years** to confirm.
- Pre-register signals, hold out the latest 4-8 quarters, and benchmark against SUE persistence.

---

## References
- Da, Engelberg & Gao, "In Search of Fundamentals" (WP): https://academicweb.nd.edu/~zda/Google_Earnings.pdf
- Da, Engelberg & Gao (2011), "In Search of Attention," JF: https://rady.ucsd.edu/faculty/directory/engelberg/pub/portfolios/GOOGLE.pdf
- Drake, Roulstone & Thornock (2012), JAR: https://ideas.repec.org/a/bla/joares/v50y2012i4p1001-1040.html
- Lawrence et al. (2016), Yahoo Finance search (CXO summary): https://cxoadvisory.com/sentiment-indicators/effects-of-investor-attention-to-returns-around-earnings-announcements
- Bijl et al. (2016), IRFA: https://hedgefundalpha.com/strategies/google-searches-stock-returns/
- BI thesis (2017), robustness of SVI: https://biopen.bi.no/bi-xmlui/handle/11250/2479644
- Google Trends sampling noise: https://arxiv.org/pdf/2504.07032
- Moat et al. (2013), Wikipedia: https://wrap.warwick.ac.uk/54525/ ; https://www.hedgeweek.com/wikipedia-early-warning-sign-stock-market-movements-says-study/
- Pyun (2024), Economics Letters: https://ideas.repec.org/a/eee/ecolet/v241y2024ics0165176524003203.html
- Focke, Ruenzi & Ungeheuer (2020), RFS: https://academicnewsletter.sufe.edu.cn/info/352451 ; https://www.outlookbusiness.com/strategy-4/insight/it-doesnt-add-up-750
- Eloranta (2014), Aalto thesis: https://epub.lib.aalto.fi/en/ethesis/id/13540
- Gutierrez, Lourie, Nekrasov & Shevlin (2020), Mgmt Sci: https://papers.ssrn.com/abstract=3342669 ; https://fen.uchile.cl/uploads/sala_prensa/2aeece551b3c7f4ad61a4d877df0c96c626b11e3.pdf
- Belo, Lin & Bazdresch (2014): https://alphaarchitect.com/2014/02/is-hiring-bad-for-stock-returns/
- Indriawan, Li & Zurbruegg (2023): https://acfr.aut.ac.nz/__data/assets/pdf_file/0019/812125/Labor-Pains-Ivan-Indriawan.pdf
- Chen, Liu & Wu, mobile apps: https://afajof.org/management/viewp.php?n=114548 ; https://www.aeaweb.org/conference/2025/program/paper/tsKtGb3z
- Yuan & Chen (2019), ICIS: https://repository.hku.hk/bitstream/10722/275443/1/Content.pdf
- Huang (2018), JFE: https://ideas.repec.org/a/eee/jfinec/v128y2018i1p164-182.html
- Green et al. (2019), Glassdoor: https://rpc.cfainstitute.org/research/cfa-digest/2020/05/dig-v50-n5-3
- Armstrong, Konchitchki & Zhang (2025), TAR: https://newsroom.haas.berkeley.edu/companies-website-traffic-proves-powerful-predictor-of-financial-performance-and-stock-returns/
- Lakonishok & Lee (2001), RFS: https://ideas.repec.org/a/oup/rfinst/v14y2001i1p79-111.html
- Jeng, Metrick & Zeckhauser: https://www.insidearbitrage.com/academic-research-related-to-insider-trading/
- Cohen, Malloy & Pomorski (2012), JF: https://dash.harvard.edu/bitstream/handle/1/33785679/cohen,malloy,pomorski_decoding-inside-information.pdf ; https://www.nber.org/digest/apr11/decoding-inside-information
- Ali & Hirshleifer (2017), JFE: https://alphaarchitect.com/2016/01/following-opportunistic-insiders-earns-1-alpha/
- Hossain (2010), post-SOX insider returns: https://cxoadvisory.com/investing-expertise/evolving-informativeness-of-insider-trading
- Bettis, Coles & Lemmon (2000), JFE: https://ideas.repec.org/a/eee/jfinec/v57y2000i2p191-220.html
- Foster (1981), JAE: https://ideas.repec.org/r/eee/jaecon/v3y1981i3p201-232.html
- Ramnath (2002), JAR: https://ideas.repec.org/a/bla/joares/v40y2002i5p1351-1376.html
- Thomas & Zhang (2008), JAR: https://ideas.repec.org/a/bla/joares/v46y2008i4p909-940.html ; https://www.cxoadvisory.com/animal-spirits/bet-against-big-sympathy-moves/
- Cohen & Frazzini (2008), JF: https://ideas.repec.org/a/bla/jfinan/v63y2008i4p1977-2011.html
- "Customer Momentum" replication (2023): https://arxiv.org/abs/2301.11394v1
- Madsen (2017), JAR: https://ideas.repec.org/a/bla/joares/v55y2017i3p709-741.html
- Burt & Hrdlicka (2021), JFQA: https://www.cambridge.org/core/journals/journal-of-financial-and-quantitative-analysis/article/where-does-the-predictability-from-sorting-on-returns-of-economically-linked-firms-come-from/6B9F2560E0A27615572309C6F295AFBF
- Bernard & Thomas, PEAD overview: https://en.wikipedia.org/wiki/Post%E2%80%93earnings-announcement_drift
- Chordia et al. (2009), FAJ: https://rpc.cfainstitute.org/research/financial-analysts-journal/2009/liquidity-and-the-post-earnings-announcement-drift
- Martineau (2022), CFR: https://ideas.repec.org/a/now/jnlcfr/104.00000122.html
- Kettell, McInnis & Zhao (2022): https://business.columbia.edu/sites/default/files-efs/imce-uploads/CEASA/Events%20Page/PEAD_Declined_over_time.pdf
- Subrahmanyam (2025) and the PEAD debate: https://anderson-review.ucla.edu/is-post-earnings-announcement-drift-a-thing-again/
- Katona et al. (2025), JFQA: https://www.cambridge.org/core/journals/journal-of-financial-and-quantitative-analysis/article/on-the-capital-market-consequences-of-big-data-evidence-from-outer-space/2F5F99D68D1F8940F61578F198D6C005
- Froot et al. (2017), JFE: https://ideas.repec.org/a/eee/jfinec/v125y2017i1p143-162.html
- Dichev & Qian (2022), JAE: https://ideas.repec.org/a/eee/jaecon/v74y2022i1s0165410122000180.html
- Gupta, Leung & Roscovan (2022), JPM: https://alphaarchitect.com/mining-credit-card-data-for-stock-returns/
- Zhu (2019), RFS: https://ideas.repec.org/a/oup/rfinst/v32y2019i5p2021-2061..html
- Dessaint, Foucault & Frésard (2024), JF: https://www.sfi.ch/en/publications/does-alternative-data-improve-financial-forecasting-the-horizon-effect
- Lu & Xin (2024), TIJA: https://ideas.repec.org/a/wsi/tijaxx/v59y2024i01ns1094406024500021.html
- Lai et al. (2025), record monthly revenue: https://papers.ssrn.com/sol3/papers.cfm?abstract_id=5345252
- Albuquerque, Ramadorai & Watugala (2015), JFE: https://ideas.repec.org/p/ofr/discus/14-04.html
- Shahrur, Becker & Rosenfeld (2010), FAJ: https://rpc.cfainstitute.org/research/financial-analysts-journal/2010/return-predictability-along-the-supply-chain-the-international-evidence
- Dubinsky, Johannes, Kaeck & Seeger (2019), RFS: https://ideas.repec.org/a/oup/rfinst/v32y2019i2p646-687..html ; slides: https://www.stern.nyu.edu/sites/default/files/assets/documents/NYU%20conference%20Johannes%20final.pdf
- Gao, Xing & Zhang (2018), JFQA: https://resolve.cambridge.org/core/journals/journal-of-financial-and-quantitative-analysis/article/anticipating-uncertainty-straddles-around-earnings-announcements/7B34877AD5E06304BA3C55FBA3219FDD ; working paper: https://www.pbcsf.tsinghua.edu.cn/__local/B/7B/EF/9C90E5DE82928B64858EFBA685C_E7B3C45C_4183B.pdf
- Chung & Louis (2017), JEF: https://ideas.repec.org/a/eee/empfin/v40y2017icp220-235.html
- Barth & So (2014), TAR: https://www.gsb.stanford.edu/faculty-research/publications/non-diversifiable-volatility-risk-risk-premiums-earnings
- Milian (2023), JRFM: https://discovery.fiu.edu/display/pub275158
- de Silva, Smith & So (2025), Review of Finance: https://gsbpreserve.stanford.edu/view/63824/losing-is-optional-retail-option-trading-and-expected-announcement-volatility ; https://mitsloan.mit.edu/ideas-made-to-matter/retail-investors-lose-big-options-markets-research-shows
- Bryzgalova, Pavlova & Sikorskaya (2023), JF: https://www.london.edu/faculty-and-research/academic-research/r/retail-trading-in-options-and-the-rise-of-the-big-three-wholesalers ; https://conference.nber.org/conf_papers/f163907/f163907.pdf
- McLean & Pontiff (2016), JF: https://rpc.cfainstitute.org/research/cfa-digest/2016/06/does-academic-research-destroy-stock-return-predictability-digest-summary
- Chen & Velikov (2023), JFQA: https://www.federalreserve.gov/econres/feds/zeroing-in-on-the-expected-returns-of-anomalies.htm
- Harvey, Liu & Zhu (2016), RFS: https://www.nber.org/papers/w20592
