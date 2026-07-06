# GRU – German Retail Underwriter
## CLAUDE.md — Project Intelligence File
> Inspired by Boris Cherny (creator of Claude Code) workflow principles.
> Update this file whenever Claude makes a mistake or learns something new about this project.

---

## 📁 Project Location
- **Local path:** `C:\Users\iritg\retail_underwriter\`
- **GitHub:** `origoore1/CRE-Alpha-Engine`
- **Runs on:** `localhost:8506` (Streamlit)
- **Main app file:** `app 7.py`

---

## ⚙️ How Claude Should Work on This Project (Boris Rules)

1. **Always plan before coding.** Propose a step-by-step plan and wait for Ori's approval before writing any code.
2. **Make surgical edits only.** Change only what is needed. Do not refactor untouched code.
3. **Run pytest automatically** after every code change. Report pass/fail clearly.
4. **Short explanations unless asked.** Ori is non-technical — plain language always.
5. **Never rename or move files without explicit instruction.** File naming is critical to how the app loads modules.
6. **Always check which file is being imported.** The app reads `rent_roll_parser 7.py` not `rent_roll_parser.py` — silent import mismatches have caused bugs before.

---

## 🏗️ Project Architecture

### Key Files
| File | Purpose |
|------|---------|
| `app 7.py` | Main Streamlit app entry point |
| `rent_roll_parser 7.py` | Parses rent roll Excel inputs |
| `dd_scanner 7.py` | PDF lease due diligence scanner (pdfplumber) — the plain `dd_scanner.py` was stale and is archived |
| `practice_deals.py` | Practice deal generator (8 deals, expanding to 30) |
| `audit_agent.py` | Detects import mismatches, stale files, hardcoded values |
| `bank_submission.py` | (GRU Lender Pack) German credit memo PDF generator |
| `term_sheet_parser.py` | (GRU Lender Pack) Extracts bank term sheet data |
| `term_sheet_comparator.py` | (GRU Lender Pack) Compares 5+ bank term sheets |

### Excel Input Convention
- Data is read from **B-column labeled cells** in specific Excel sheets
- Vacant units must have **zero or null values** — never omit them
- `data_only=True` returning `None` on formula cells is **expected behavior**
- Rent Roll tenant rows: **4–103**
- Annual rent computed as: `area × monthly_rent × 12`

### Practice Deal File Naming
```
Practice_NN_DIFFICULTY_VERDICT_Description.xlsx
Example: Practice_01_BEGINNER_APPROVE_REWE_Anchor_Bonn.xlsx
```

---

## 🏦 Domain Knowledge (German Retail CRE)

### Key Metrics Claude Must Know
| Term | Meaning |
|------|---------|
| DSCR | Debt Service Coverage Ratio — must be ≥ 1.25x to approve |
| ICR | Interest Coverage Ratio |
| LTV | Loan-to-Value — typically max 70–75% for retail |
| WAULT | Weighted Average Unexpired Lease Term |
| GIY | Gross Initial Yield |
| NIY | Net Initial Yield |
| Debt Yield | NOI ÷ Loan Amount |
| AfA | German depreciation allowance |
| GrESt | Real estate transfer tax (varies by state, 3.5–6.5%) |
| Erbpacht | Ground lease (leasehold) — risk flag |

### Verdict Logic
- **APPROVE:** DSCR ≥ 1.25x, LTV ≤ 75%, WAULT ≥ 5 years, anchor tenant stable
- **DECLINE triggers:** Short WAULT, overleveraged structure, anchor departure risk, high vacancy, Erbpacht terms, insufficient DSCR

### Typical German Retail Tenants
REWE, Edeka, ALDI, Lidl, Kaufland, OBI, Toom, Action, dm, Rossmann, NKD, ATU, Decathlon, Woolworth

### Yield Benchmarks (JLL/CBRE Germany)
- Prime Fachmarktzentrum: 5.0–5.5% NIY
- Secondary retail: 6.5–8.5% NIY

---

## 📊 Practice Deals Plan
- **Total:** 30 deals (expanding from 8)
- **Distribution:** ~20 APPROVE / ~10 DECLINE
- **Difficulty split:** BEGINNER / INTERMEDIATE / ADVANCED
- **Cities:** Varied German cities/states — realistic locations
- **Data sources:** BNP Paribas RE Germany, CBRE Germany, JLL Germany, Savills Germany, gif e.V./IPD

---

## 🏦 GRU Lender Pack (Product Extension)
Two-module add-on that takes GRU from underwriting verdict → bank-ready submission:
- **Module 1:** `bank_submission.py` — German credit memo PDF + cover letter with ESG section
- **Module 2:** `term_sheet_parser.py` + `term_sheet_comparator.py` — normalizes and compares term sheets across 5+ banks, outputs 4 key comparison metrics
- **Pricing model:** €3,500 single deal / €6,500 five-bank package / €1,500/quarter covenant monitoring

---

## 🐛 Known Issues & Lessons Learned
> Add to this section whenever a bug is found and fixed.

- **[FIXED]** `app 7.py` was silently importing stale `rent_roll_parser.py` instead of `rent_roll_parser 7.py` — caused WAULT to compute incorrectly. Always verify import paths.
- **[FIXED]** WAULT now correctly shows 0.9 years for Kerken Retail Center / Woolworth deal (anchor with ~2 months to break option).
- **[FIXED]** Vacant units with missing values (instead of zero) caused calculation errors — all vacant rows must have explicit 0.
- **[RESOLVED 2026-07-06]** The old "deals 5 and 7 mismatch" note was stale — both match their intended verdicts. The real drift was deal 12 (see next item).
- **[FIXED 2026-07-06]** Practice deals decayed over real time: lease dates are literals, so WAULT shrank every month and verdicts silently flipped (deal 12 drifted from its designed INVESTIGATE to DECLINE). Fix: `generate_practice_deal()` now shifts lease dates by (today − design_date), so every deal keeps its designed WAULT and verdict forever. Deals may carry their own `"design_date"`; the default is `DESIGN_DATE_DEFAULT` in `practice_deals7.py`. When authoring NEW deals, set `design_date` to the authoring date.
- **[NOTE 2026-07-06]** NIY convention decided: `niy` = NOI ÷ asking price (GRU original); `niy_on_tac` = NOI ÷ total acquisition cost incl. GrESt (German market convention — use THIS when comparing against JLL/CBRE benchmarks).
- **[CLEANUP 2026-07-06]** 17 dead/duplicate files (עותק copies, stale parser/scanner versions, empty launch.py) moved to `archive\`. Live modules are only those listed in the Architecture table. Never import anything from `archive\`.
- **[FIXED 2026-07-06]** Regression suite failed on all 12 deals from time drift: WAULT is computed from `date.today()` but baselines are static, so the suite went red ~4 days after baselines were generated (±0.01yr tolerance ≈ 3.65 days). Fix: `tests/test_regression.py` now freezes the engine clock to the baseline date (2026-05-28). If baselines are ever regenerated, update `BASELINE_AS_OF` in the test to the regeneration date.

---

## ✅ After Every Session
- [ ] Run `pytest` and confirm all tests pass
- [ ] Note any new bugs or lessons in the **Known Issues** section above
- [ ] Confirm no files were renamed or moved unintentionally
- [ ] Push changes to GitHub if session included stable new features
