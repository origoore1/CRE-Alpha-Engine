# Stock Alpha Engine (SAE) — v3
## Project Intelligence File: Fundamental Stock Screener, Analyzer & Recommender

> **Philosophy:** A sector-aware, ROIC-centered, cash-flow-driven, forward-looking stock quality engine.
> Inspired by Warren Buffett. Updated for modern markets.
> **Goal:** Cherry-pick the highest-quality companies globally by combining forensic financial analysis with competitive market positioning and valuation expectations.

---

## What This Product Does

Three things, in order:

1. **Screen** — scan a broad universe (S&P 500, DAX, Stoxx 600, or custom ticker list) and drop weak companies fast
2. **Analyze** — deep-dive each passing company: financial truth + market position + valuation expectations
3. **Classify** — score and rank companies with analytical labels, not investment advice

---

## Core Design Principle: The Tripartite Benchmark

**Every single ratio is evaluated through three lenses simultaneously — not one fixed threshold.**

| Lens | What it means | Example |
|------|--------------|---------|
| **Absolute Floor** | A safety baseline — disqualify below this regardless of sector | Net Debt/EBITDA never > 4.0x |
| **Sector-Relative Percentile** | Is this company top quartile vs its peers in the same GICS sector? | Gross margin top 25% for retail sector |
| **5-Year Trend Direction** | Is the metric improving, stable, or deteriorating? | Operating margin expanding = good |

This replaces the broken approach of fixed absolute targets like "gross margin > 40%," which is correct for SaaS but impossible for a grocery chain, insurer, or bank.

---

## The Real Question This Engine Answers

Not: "Is revenue growing?"

But: **"Can this company keep reinvesting capital at returns above its cost of capital — and is the current stock price realistic about that?"**

---

## Classification Labels (analytical, not advisory)

| Composite Score | Label |
|-----------------|-------|
| 80–100 | ✦ Elite Compounder |
| 65–79 | Attractive Quality |
| 50–64 | Neutral — Monitor |
| 35–49 | Fails Quality Screen |
| < 35 or Red Flag | Disqualified |

These are quality classifications, not buy/sell recommendations.

---

## Module 1: Financial Truth (65% of Composite Score)

The forensic layer that tells the real story of a company's health, growth quality, and capital efficiency.

### Sub-area 1A — ROIC / Capital Efficiency (20% of Module 1)

**ROIC is the center of the system.** Growth is valuable only when it generates returns above the cost of capital.

| Ratio | Formula | What it tells you |
|-------|---------|------------------|
| ROIC | NOPAT / Invested Capital | How well the company turns capital into profit |
| ROIC vs WACC Spread | ROIC − WACC | Positive = value creation; negative = value destruction |
| Incremental ROIC | Change in NOPAT / Change in Invested Capital | Return on *newly* deployed capital — the real growth engine |
| Reinvestment Rate | (Capex − Depreciation + ΔWorking Capital) / NOPAT | What fraction of profit is reinvested back into the business |
| Sustainable Growth Rate | ROIC × Reinvestment Rate | How fast can this company grow without taking on debt or diluting? |
| ROIC Trend | 3yr and 5yr trajectory | Is capital efficiency improving or deteriorating? |
| ROIC vs Sector Median | Company ROIC vs peers | Is management above average at capital allocation? |
| ROE (adjusted) | Net Income / Avg Equity — but checked for leverage distortion | Quality return on shareholder capital |

Benchmark: ROIC > WACC (typically 10%), above sector median, stable or improving trend.

### Sub-area 1B — Growth Quality (15% of Module 1)

| Ratio | Formula | Notes |
|-------|---------|-------|
| Revenue CAGR (3yr + 5yr) | Compound annual growth | Look for consistency, not one explosive year |
| Gross Profit CAGR (3yr + 5yr) | Compound growth of gross profit | Better than revenue alone — shows *profitable* growth |
| EPS CAGR (3yr) | Compound earnings per share growth | Adjusted for share dilution |
| Operating Leverage | Operating income growth / Revenue growth | Shows whether scale improves profitability |
| Company growth vs Industry CAGR | Company CAGR minus industry CAGR | Positive = gaining market share |

Benchmark: All CAGRs assessed relative to sector growth rate and 5yr own trend.

### Sub-area 1C — Profitability & Margins (15% of Module 1)

| Ratio | Formula | Notes |
|-------|---------|-------|
| Gross Margin + trend | Gross Profit / Revenue | Pricing power — evaluated vs sector percentile + trend |
| Operating Margin + trend | Operating Income / Revenue | Core business efficiency — vs sector + trend |
| Net Margin + trend | Net Income / Revenue | Bottom-line quality — vs sector + trend |

No fixed absolute targets. Each is: top quartile in sector = full score; below median = penalty; trend direction adds or subtracts.

### Sub-area 1D — Cash Quality & Earnings Integrity (15% of Module 1)

This is the forensic layer. Earnings can be legally inflated; cash cannot.

| Ratio | Formula | What it detects |
|-------|---------|----------------|
| OCF / Net Income | Operating Cash Flow / Net Income | Must consistently exceed 1.0. Below 0.8 signals weak or fake earnings. |
| Accrual Ratio | (Net Income − Operating Cash Flow) / Total Assets | High accruals = low-quality earnings, risk of future revision |
| Free Cash Flow Margin | FCF / Revenue | Is growth generating real cash? |
| FCF Conversion | FCF / Net Income | Close to 1.0 = clean profit. Far below = accounting distortion. |
| Receivables Growth vs Revenue Growth | ΔReceivables / ΔRevenue | Revenue growing faster than collections = deteriorating business quality |

### Sub-area 1E — Balance Sheet & Debt Risk (15% of Module 1)

| Ratio | Formula | Absolute Floor | Sector Context |
|-------|---------|---------------|---------------|
| Net Debt / EBITDA | Net Debt / EBITDA | Hard cap: 4.0x | Cyclicals and utilities have different norms |
| Interest Coverage | EBIT / Interest Expense | Hard floor: 3x | Lower = stress under rate rises |
| Debt / Equity | Total Debt / Equity | No fixed target | vs sector median |
| Current Ratio | Current Assets / Current Liabilities | Only relevant for non-financial sectors | Not applied to banks/insurers |
| Capex / Revenue | Capital Expenditure / Revenue | — | High = capital-intensive, inflation-exposed |

### Sub-area 1F — Capital Allocation & Shareholder Dilution (10% of Module 1)

| Ratio | Formula | Why it matters |
|-------|---------|---------------|
| Share Count CAGR (3–5yr) | Annual change in diluted share count | Dilution destroys per-share value even if absolute profits grow |
| SBC / Revenue | Stock-Based Compensation / Revenue | Tech companies pay employees in stock — this is a real cost missed in GAAP FCF |
| SBC / FCF | SBC / Free Cash Flow | If SBC > 15% of FCF, reported FCF is substantially overstated |
| Buyback Quality | Share reduction + earnings accretion | Are buybacks done at good prices, or just offsetting SBC? |
| Dividend sustainability | FCF payout ratio | Can dividends be maintained through a downturn? |

### Sub-area 1G — Accounting Red Flags (10% of Module 1)

Automatic score penalties for any of these:
- Accrual Ratio above 5% of assets for 3+ consecutive years
- Revenue growing while OCF/Net Income ratio is falling
- Receivables growing faster than revenue (2yr trend)
- Gross margin collapsing while revenue grows
- Net income growing while FCF declining (3yr trend)
- Share count growing > 3% per year (dilution)
- SBC > 20% of FCF

### Module 1 Scoring Summary

| Sub-area | Weight |
|----------|--------|
| ROIC / Capital Efficiency | 20% |
| Growth Quality | 15% |
| Profitability & Margins | 15% |
| Cash Quality & Earnings Integrity | 15% |
| Balance Sheet & Debt Risk | 15% |
| Capital Allocation & Dilution | 10% |
| Accounting Red Flags | 10% |

**Threshold to continue to Module 2:** Module 1 score ≥ 60 / 100

---

## Module 2: Market Position & Expectations (35% of Composite Score)

How does the company sit in its competitive market, and is the current stock price realistic?

### Sub-area 2A — Competitive Moat Strength (25% of Module 2)

Based on Morningstar's five sources of economic moat:

| Moat Source | Definition | Proxy Indicator |
|-------------|-----------|----------------|
| Switching Costs | Customer locked in — cost of leaving exceeds benefit | Revenue retention / contract length / churn |
| Network Effects | Product becomes more valuable as user base grows | User growth vs revenue growth |
| Intangible Assets | Brands, patents, licenses, regulatory approvals | Gross margin premium vs peers; R&D/Revenue |
| Cost Advantage | Structurally cheaper to produce than rivals | Operating cost/revenue vs sector |
| Efficient Scale | Dominant position in a niche too small for competitors | Market share in defined segment |

Moat Score: 0 = no moat, 1 = narrow moat, 2 = wide moat — with decay/strengthen direction.

### Sub-area 2B — Company vs Peers (25% of Module 2)

| Metric | What it reveals |
|--------|----------------|
| Gross Margin vs Sector Median | Pricing power edge |
| Operating Margin vs Sector Median | Operational efficiency edge |
| ROIC vs Sector Median | Capital allocation superiority |
| Revenue Growth vs Top 3 Competitors | Market share gaining or losing |
| Market Share Trend (3yr) | Hard directional signal |

### Sub-area 2C — Industry Growth & Relative Performance (20% of Module 2)

| Metric | Target |
|--------|--------|
| Industry Revenue CAGR (3yr + 5yr) | > 5% = growing market. Shrinking market limits even great companies. |
| Company CAGR minus Industry CAGR | Positive = outperforming / gaining share |
| Cyclicality Classification | Defensive, cyclical, or secular growth |
| TAM penetration | Large underpenetrated market = more runway |

### Sub-area 2D — Reverse DCF / Valuation Expectations (15% of Module 2)

This is the smartest valuation tool. Instead of labeling a stock "cheap" or "expensive," the engine calculates what the current price *implies* about the future — then asks: is that realistic?

**How it works:**
1. Take current stock price → back-calculate the revenue CAGR and operating margin the market is pricing in over 10 years
2. Compare those implied numbers to the company's actual 5-year track record
3. If the market demands 25% revenue growth but the company has averaged 12% — the stock is pricing in a perfection scenario

**Additional valuation metrics:**
| Metric | Why |
|--------|-----|
| FCF Yield | Market Cap / FCF — cleaner than P/E for mature companies |
| EV / FCF | Accounts for debt load, better than Price/FCF |
| EV / EBITDA vs sector median | Relative, not absolute |
| Forward PEG | P/E on forward EPS / forward growth rate — better than trailing |
| Rule of 40 | Revenue growth % + FCF margin % (SaaS/tech quality check) |

### Sub-area 2E — Modern Risk Factors (15% of Module 2)

| Risk Factor | What we assess |
|------------|---------------|
| AI Exposure | Tailwind (company uses AI for productivity/product) or disruption risk |
| Regulation Risk | Finance, healthcare, energy, big tech — rate it High/Medium/Low |
| Geopolitical Risk | China supply chain exposure, sanctions, defense/energy dependency |
| Customer Concentration | Top 3 customers as % of revenue. > 30% = fragility |
| Supplier Concentration | Critical single-supplier dependency |
| Analyst Estimate Revisions | Upward revisions = improving fundamental momentum |
| Insider Ownership & Buying | High ownership = alignment. Recent buying = confidence. |
| Management Capital Allocation | ROIC trend vs investment decisions — M&A discipline, buyback timing |

### Module 2 Scoring Summary

| Sub-area | Weight |
|----------|--------|
| Competitive Moat Strength | 25% |
| Company vs Peers | 25% |
| Industry Growth & Relative Performance | 20% |
| Reverse DCF / Valuation Expectations | 15% |
| Modern Risk Factors | 15% |

---

## Composite Score & Classification

```
COMPOSITE SCORE = (Module 1 × 0.65) + (Module 2 × 0.35)
```

| Score | Classification | Meaning |
|-------|---------------|---------|
| 80–100 | ✦ Elite Compounder | Exceptional quality, durable moat, realistic valuation |
| 65–79 | Attractive Quality | Strong fundamentals, good market position |
| 50–64 | Neutral — Monitor | Mixed signals; watch for trend improvement |
| 35–49 | Fails Quality Screen | Significant weaknesses in fundamentals or market position |
| < 35 or Red Flag | Disqualified | Red flag triggered; excluded from consideration |

Each output includes: top 3 strengths, top 2 risks, and a plain-language summary such as:
*"This company earns high returns on invested capital and is gaining market share, but the current price implies 22% revenue growth for 10 years — its 5-year average has been 14%. Premium is too high."*

---

## Definitive Ratio Set (v1 Core Build)

### 25 Financial Ratios (Module 1)
1. Revenue CAGR — 3yr
2. Revenue CAGR — 5yr
3. Gross Profit CAGR — 3yr
4. EPS CAGR — 3yr
5. Operating Leverage (OpIncome growth / Revenue growth)
6. Company CAGR vs Industry CAGR
7. Gross Margin + 5yr trend
8. Operating Margin + 5yr trend
9. Net Margin + 5yr trend
10. OCF / Net Income
11. Accrual Ratio
12. Free Cash Flow Margin
13. FCF Conversion
14. Receivables Growth vs Revenue Growth
15. ROIC
16. ROIC vs WACC (spread)
17. Incremental ROIC
18. Reinvestment Rate
19. Sustainable Growth Rate (ROIC × Reinvestment Rate)
20. Net Debt / EBITDA
21. Interest Coverage
22. Capex / Revenue
23. Share Count CAGR
24. SBC / Revenue
25. SBC / FCF

### Valuation Ratios (Module 2D)
26. FCF Yield
27. EV / FCF
28. EV / EBITDA vs sector median
29. Forward PEG
30. Reverse DCF Implied Revenue Growth Rate
31. Rule of 40 (tech/SaaS only)

### 15 Market Parameters (Module 2)
32. Industry Revenue CAGR
33. Company CAGR minus Industry CAGR
34. Gross Margin vs Peer Median
35. Operating Margin vs Peer Median
36. ROIC vs Peer Median
37. Market Share Trend
38. Moat Source Classification
39. Customer Concentration
40. Geographic Concentration
41. Regulation Risk (High/Med/Low)
42. AI Exposure (Tailwind/Neutral/Disruption)
43. Analyst Estimate Revisions (direction)
44. Insider Ownership %
45. Management Capital-Allocation Score
46. SWOT Summary (qualitative layer)

---

## Screening Funnel (Broad Market Mode)

**Phase 1 — Fast Filter** (drops ~70% of stocks instantly)

Pass/fail only — no scoring yet:
- Revenue CAGR (3yr) > 3% (sector-adjusted)
- Gross margin above sector bottom quartile
- Net Debt / EBITDA < 4.0x (hard cap)
- FCF positive (at least 2 of last 3 years)
- OCF / Net Income > 0.7 (basic earnings quality gate)
- No active red flags

**Phase 2 — Score Module 1**
Full financial analysis. Keep only scores ≥ 60.

**Phase 3 — Score Module 2**
Market and expectations analysis. Keep scores ≥ 55.

**Phase 4 — Rank & Output**
Sort by composite score. Output top 10–25 companies with full scorecard and classification label.

---

## Data Infrastructure

| API | Role | Plan Required | Cost Estimate |
|-----|------|--------------|---------------|
| **Financial Modeling Prep (FMP)** | Primary — US + global fundamentals | **Premium** (not Starter) for 10yr+ history | ~$49–79/month |
| **EODHD** | Backup — European coverage, 150k tickers | Basic/All-World plan | ~$19–79/month |

**Why 10+ years matters:** We need to see how a company performed through at least one full credit cycle (2008–09 or 2020 COVID). A 5-year window misses downturns and gives a misleading picture of balance sheet resilience.

**Before committing to any plan:** Run data quality tests on 3 companies:
- Apple (AAPL) — US tech, well-documented
- SAP (SAP.DE) — European tech, tests EU coverage
- Novo Nordisk (NOVO-B.CO) — European pharma, tests sector + non-USD currency handling

---

## Tech Stack

| Layer | Choice | Notes |
|-------|--------|-------|
| Language | Python | Consistent with GRU project |
| Data | FMP API + EODHD backup | Verified after data test |
| Calculation engine | pandas | Ratio calculations, rolling trends |
| Scoring logic | Python classes | Modular — one class per sub-area |
| Visualization | plotly | Interactive charts (ROIC trend, margin evolution) |
| UI | Streamlit | Consistent with GRU — decide after Phase 1 scripts validated |

---

## Build Roadmap

### Phase 0 — Data Pipeline Validation (start here, before any scoring)
- [ ] Set up FMP API connection
- [ ] Pull income statement, balance sheet, cash flow for AAPL, SAP.DE, NOVO-B.CO
- [ ] Verify: 10yr history available? All 25 ratios computable? Non-USD currencies handled?
- [ ] Decision gate: confirm FMP plan or switch to EODHD for EU names

### Phase 1 — Module 1 Core Engine (scripts only, no UI)
- [ ] Build ratio calculator (all 25 ratios)
- [ ] Build Tripartite Benchmark logic (absolute floor + sector percentile + 5yr trend)
- [ ] Build Module 1 scoring model
- [ ] Build red-flag detector
- [ ] Test on 3 companies across different sectors (tech, auto, pharma)

### Phase 2 — Module 2
- [ ] Build peer comparison engine (pull sector medians from FMP)
- [ ] Build moat scoring logic (5-source classification)
- [ ] Build Reverse DCF calculator (implied growth at current price)
- [ ] Add modern risk flag assessment
- [ ] Test Module 2 on same 3 companies

### Phase 3 — Screener
- [ ] Build Phase 1–4 fast-filter funnel
- [ ] Test on S&P 500 full list
- [ ] Output ranked CSV with scores

### Phase 4 — UI & Recommender
- [ ] Build Streamlit app
- [ ] Scorecard dashboard per company
- [ ] Charts: ROIC trend, margin trends, score breakdown
- [ ] Plain-language classification output with top strengths + risks

---

## Open Questions (Resolve in Phase 0)
- [ ] Confirm FMP Premium covers SAP.DE, NOVO-B.CO with 10yr fundamentals
- [ ] Decide: handle non-USD currencies by normalization or report in local currency?
- [ ] Define GICS sector peer groups — FMP provides this?
- [ ] Where does WACC come from? Compute it (needs beta + risk-free rate) or use FMP's pre-calculated value?

---

## Sources
- Henry Chien (Buttonwood) — "The Only Fundamental Analysis Video You Will Ever Need" (YouTube, 2024)
- Warren Buffett / Berkshire Hathaway annual letters
- Morningstar Economic Moat Framework (5 sources)
- Damodaran — Fundamental Determinants of Growth (ROIC × Reinvestment Rate)
- Morgan Stanley — ROIC and the Investment Process
- SEC Beginner's Guide to Financial Statements
- Financial Modeling Prep API documentation
- EODHD Financial APIs

---

---

## MCP Connectors (Plugins Available)

These connectors can be installed in Cowork to give Claude direct API access — no Python API keys needed for prototyping:

| Connector | What it adds | Priority |
|-----------|-------------|----------|
| **FMP** (Financial Modeling Prep) | Full financial statements, ratios, sector data, 70k+ tickers | #1 — install first |
| **S&P Global** | Premium datasets: capitalizations, company summaries, business relationships | #2 — institutional-grade data |
| **Quartr** | Earnings call transcripts, company documents, events | #3 — management analysis + news |
| **LunarCrush** | Real-time social media sentiment for stocks | #4 — whispers/sentiment layer |

Install these via the Connect buttons shown above before Phase 0 begins.

---

## Sector-Specific Analysis Templates

The system auto-detects GICS sector and loads a sector template ON TOP of the universal ratio core. Different sectors have structurally different financial profiles — applying universal thresholds across all of them gives wrong results.

### Universal Core (applies to all sectors)
ROIC, Revenue CAGR, FCF Conversion, OCF/Net Income, Accrual Ratio, Net Debt/EBITDA, Share Count CAGR — evaluated via Tripartite Benchmark.

### Banks & Financial Institutions
Replace/add: Net Interest Margin (NIM), Non-Performing Loan ratio (NPL%), Tier 1 Capital Ratio, Loan-to-Deposit Ratio, Net Charge-off Rate, Return on Assets (ROA), Efficiency Ratio (costs/revenue). Remove: Gross margin, Capex/Revenue (not applicable).

### SaaS / Subscription Software
Add: Annual Recurring Revenue (ARR) growth, Net Revenue Retention (NRR > 110% = excellent), Gross Revenue Retention (churn floor), Customer Acquisition Cost (CAC), LTV/CAC ratio, Payback period, Rule of 40 (revenue growth % + FCF margin %), Magic Number (sales efficiency). Weight FCF conversion and dilution (SBC) more heavily.

### Semiconductors
Add: Book-to-bill ratio (> 1.0 = demand exceeding supply), R&D/Revenue (innovation intensity), Design win pipeline, Gross margin structure (fabless vs IDM), Inventory cycles, Capex intensity (fabs are extremely capital heavy). Flag cyclicality — semi cycles are brutal.

### Healthcare & Pharma
Add: Pipeline stage value (Phase 1/2/3 drugs as revenue probability-weighted), Patent cliff exposure (% of revenue from patents expiring < 5yr), R&D success rate, Revenue concentration by drug (top drug > 40% of revenue = risk), Regulatory approval timeline, Generic competition risk.

### Energy & Commodities
Add: Reserve replacement ratio, Production cost per barrel/unit vs commodity price, Realized price vs benchmark, Hedging coverage, Maintenance capex vs growth capex distinction, Reserve life index. Treat debt differently — energy companies are cyclical; covenant analysis matters more than absolute leverage.

### Retail & Consumer
Add: Same-store sales growth (SSS), Inventory turnover, Gross margin by category, E-commerce penetration %, Foot traffic trend, Working capital cycle. Customer loyalty / repeat purchase rate where available.

### Industrials & Engineering
Add: Order backlog, Book-to-bill ratio, Backlog-to-revenue coverage, Utilization rate, Capex cycle position. Operating leverage is especially important — margins expand/contract sharply with volume.

---

## Module 3: News, Sentiment & Market Intelligence

> A great company can be temporarily mispriced by sentiment. This module tracks what the market *feels* about a company — and whether that feeling is informed or noise.

### 3A: Earnings Whispers & Analyst Signals
| Signal | Source | What it tells you |
|--------|--------|------------------|
| Earnings Whisper Number | EarningsWhispers.com | Unofficial expected EPS the market is actually pricing — beats/misses vs whisper matter more than vs consensus |
| Analyst Estimate Revisions | FMP / S&P Global | Consistent upward revisions = improving fundamentals; downward = deterioration |
| Consensus vs Whisper gap | Delta calculation | Large gap = potential surprise (up or down) |
| Analyst rating changes | FMP | Upgrades/downgrades from major banks |

### 3B: Social & News Sentiment
| Signal | Source | Weight |
|--------|--------|--------|
| Social sentiment score | LunarCrush MCP | News/social tone — contrarian signal (extreme negative often = buy opportunity) |
| News volume trend | LunarCrush | Unusual spike = event risk or catalyst |
| Earnings call tone | Quartr MCP | Management language: confident vs hedged vs defensive |
| Management guidance vs actuals | Quartr | Has management historically beaten or missed their own forecasts? |

### 3C: Informed Money Signals
| Signal | What it means |
|--------|--------------|
| Short interest (%) | High + rising = informed sellers. Interpret carefully. |
| Institutional ownership changes | 13F filings: are big funds buying or selling? |
| Insider buying/selling net | Net buyers over 12 months = confidence signal |
| Options unusual activity | Large unusual call/put volume = informed money positioning |
| Credit spread on company bonds | Bond market often prices risk before equity market |

Module 3 is a **modifier**, not a primary score. It can nudge a company up or down by ±5 points on the composite, or trigger a "flag for review" even if financials are strong.

---

## Deep Company Health Checks (Additional to Core Ratios)

These answer Ori's question: does the model really see how *robust, stable, and healthy* the company is?

### FCF vs Capex Sufficiency
- FCF / Capex ratio: Does the company generate enough free cash to fund its own growth without borrowing?
- Maintenance capex vs growth capex split: Is capex sustaining the existing business or expanding it?
- FCF after capex trend (3yr): Is this improving or being squeezed?

### Asset Growth Quality
- Asset growth decomposition: Is the balance sheet growing because of retained profits (healthy) or because of new debt and equity issuance (potentially dilutive or risky)?
- Return on Assets (ROA) trend: Are growing assets producing proportionally more income, or just sitting idle?
- Goodwill as % of total assets: High and rising goodwill from acquisitions can mask poor organic performance.

### Debt Allocation Efficiency
- Revenue growth / Debt growth ratio: When the company borrows, does revenue grow proportionally?
- EBITDA growth vs Debt growth: Leverage should decrease as the business grows — if debt grows faster than EBITDA, the company is digging a hole.
- Interest coverage trend: Is debt becoming more or less manageable over time?

### Profit Margin Dynamics
- Gross margin trend (5yr): Stable or expanding = pricing power. Declining = competitive pressure.
- Operating margin trend (5yr): Shows whether scale is producing efficiency gains.
- Net margin trend (5yr): After interest and taxes — the real bottom line.
- Margin vs peers trend: Are margins expanding faster than the sector? That is a quality signal.

---

## Built-In QA Auditor (Pre-Output Check)

No output reaches the user without passing this internal validation layer.

### Layer 1: Data Integrity
- All required ratios present and computed for the requested time period
- No implausible values (e.g. revenue growth > 500% in a single year without a merger — flag for review)
- Currency normalization confirmed (all non-USD values converted consistently)
- Cross-statement consistency: Net income on income statement matches equity changes on balance sheet
- Data freshness: Are we using the most recent annual/quarterly filing? Flag if data is > 6 months stale

### Layer 2: Scoring Consistency
- Internal logic check: A company cannot score > 80 on ROIC but < 20 on cash quality without a flag — these are correlated metrics
- Red flag override: If any red flag is triggered, composite score is capped at 49 (Fails Quality Screen) regardless of other scores
- Sector template confirmation: Correct sector module was applied

### Layer 3: Output Validation
- Plain-language summary consistent with numerical scores
- Top 3 strengths and top 2 risks are supported by actual ratio values
- Reverse DCF implied growth rate is compared to actual 5yr track record — discrepancy > 10% flagged
- Final output label matches composite score band

### Layer 4: Anomaly Flags
These don't block output but appear as warning tags alongside results:
- "DATA GAP: less than 7 years of history available"
- "SECTOR MISMATCH: company recently changed industry classification"
- "CURRENCY RISK: significant non-reporting-currency revenue exposure"
- "RESTATEMENT HISTORY: prior financials were restated"

---

## Management Efficiency Module (Dedicated Section)

Added to Module 2 as sub-area 2F.

| Metric | How measured | What it tells you |
|--------|-------------|------------------|
| Guidance accuracy | Mgmt forecast vs actual result (3yr avg) | Consistently optimistic = red flag. Consistently beating = credibility |
| Capital deployment ROIC | ROIC before and after major investments/M&A | Did their big bets create or destroy value? |
| SBC as % of total compensation | SBC / (SBC + cash comp) | Are executives enriching themselves at shareholders' expense? |
| Net insider buying | Shares bought minus sold by insiders, 12mo | Insiders know the business best |
| CEO tenure | Years in role | Stable, long-tenured management at high-ROIC companies = positive |
| Board quality | Independent directors %, committee composition | Oversight strength |
| Dividend policy consistency | Dividends maintained/grown through downturns | Measures financial conservatism and shareholder commitment |

Source: Quartr MCP (earnings call transcripts for guidance tracking), FMP (insider transactions, institutional ownership).

---

## Additional Forensic Tools

| Tool | Formula | What it catches |
|------|---------|----------------|
| Beneish M-Score | 8-variable model (receivables, margins, asset quality, sales growth, capex, accruals) | Probability of earnings manipulation. Score > -1.78 = possible fraud. |
| Altman Z-Score | 5-variable model (working capital, retained earnings, EBIT, equity/debt, sales/assets) | Bankruptcy probability. Z < 1.81 = distress zone. |
| Piotroski F-Score | 9 binary signals for financial strength | Quick quality pass/fail (0-9 scale; > 7 = strong) |
| DuPont ROE Decomposition | ROE = Net Margin × Asset Turnover × Equity Multiplier | Reveals *why* ROE is high — margin-driven (good) vs leverage-driven (risky) |

These run automatically as part of Module 1 scoring and feed into the accounting red flags sub-area.

---

## Output Design (Simple Frontend)

The UI shows only what matters. No data dump.

### Company Scorecard View
```
[Company Name] [Ticker] [Sector]
─────────────────────────────────────────────
COMPOSITE SCORE: 78 / 100  →  Attractive Quality
─────────────────────────────────────────────
Module 1: Financial Truth      82/100  ████████░░
  ↳ ROIC vs WACC: +4.2%       ████████░░
  ↳ Growth Quality             74/100  ███████░░░
  ↳ Cash Integrity             88/100  █████████░
  ↳ Balance Sheet              71/100  ███████░░░

Module 2: Market Position      68/100  ██████░░░░
  ↳ Moat: Narrow (Switching Costs + Brand)
  ↳ vs Peers: Top 30% on ROIC, Margins
  ↳ Implied Growth (Reverse DCF): 18% — actual 5yr avg: 14%
  ↳ Risk flags: Regulation (Medium), AI (Tailwind)

Module 3: Sentiment            Neutral ▲
─────────────────────────────────────────────
✓ Top strengths: FCF conversion (0.91), ROIC expanding 3yr
⚠ Top risks: Valuation implies growth above historical avg
─────────────────────────────────────────────
```

### Screener Output View
Ranked table — ticker, sector, composite score, classification, top strength, top risk. 15 columns max. Export to CSV.

### Watchlist View
Saved companies with weekly delta: what changed in their scores and why.

---

## Watchlist & Alert System

- **Watchlist:** Save any company. Scores refreshed weekly when new data is available.
- **Alerts triggered by:**
  - ROIC drops below WACC
  - Red flag newly triggered
  - Composite score changes classification band
  - Analyst estimate revisions turn negative (3 consecutive)
  - Insider net selling exceeds threshold
- **Compare mode:** Side-by-side scorecard for any two tickers in the same sector

---

## Audit History
- **v0 (May 2026):** Initial blueprint. Grade B/B+. Fixed thresholds, no forensic layer, light valuation, ROIC underweighted.
- **v2 (May 2026):** Full upgrade. Tripartite Benchmark, ROIC as center, forensic accounting layer, Reverse DCF, modern risk factors, non-advisory labels.
- **v3 (May 2026):** Added sector templates, Module 3 (news/sentiment/whispers), built-in QA auditor, deep health checks (FCF vs Capex, asset quality, debt efficiency), management efficiency module, Beneish/Altman/Piotroski/DuPont forensic tools, simple frontend wireframe, watchlist & alerts. MCP connectors identified: FMP, S&P Global, Quartr, LunarCrush.
