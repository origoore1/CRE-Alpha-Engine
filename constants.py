"""
constants.py  —  GRU Platform: Single Source of Truth
======================================================
All shared constants live here. Every module imports from this file.
To update a threshold, GrESt rate, or anchor list — change it once here.

Sections
--------
1. GrESt Rates          — all 16 Bundesländer, current 2026 rates
2. QA Thresholds        — pass/warn/fail values for every bank covenant check
3. QA Check Labels      — canonical string names used as keys across modules
4. NOI / Cost Model     — void holding rate, fallback margin, AfA parameters
5. Anchor Tenants       — substring match list + full covenant ranking table
"""

# =============================================================================
# 1. GREST RATES
# =============================================================================
# Grunderwerbsteuer (real estate transfer tax) by Bundesland.
# Source: GrEStG § 11, as amended; rates verified for 2026.
# Note: Bayern remains the only state at the original federal rate of 3.5%.

GREST_BY_STATE: dict[str, float] = {
    "Bayern":                 0.035,  # Bavaria — lowest rate; unchanged since 1997
    "Baden-Württemberg":      0.050,
    "Berlin":                 0.060,
    "Brandenburg":            0.065,
    "Bremen":                 0.050,
    "Hamburg":                0.055,
    "Hessen":                 0.060,
    "Mecklenburg-Vorpommern": 0.060,
    "Niedersachsen":          0.050,
    "Nordrhein-Westfalen":    0.065,
    "Rheinland-Pfalz":        0.050,
    "Saarland":               0.065,
    "Sachsen":                0.055,
    "Sachsen-Anhalt":         0.050,
    "Schleswig-Holstein":     0.065,
    "Thüringen":              0.065,
}

GREST_DEFAULT = 0.055  # Fallback when state cannot be auto-detected (Hamburg rate)


# =============================================================================
# 2. QA UNDERWRITING THRESHOLDS
# =============================================================================
# Source: German CRE senior debt market practice (pbb Deutsche Pfandbriefbank,
# Berlin Hyp, Helaba, DZ Hyp, Aareal Bank) as of 2026.
# RED = hard fail (likely decline); YELLOW = soft fail (further DD required).

# ── Loan-to-Value ─────────────────────────────────────────────────────────────
LTV_PASS = 0.65    # ≤ 65% → green
LTV_WARN = 0.70    # ≤ 70% → amber;   > 70% → RED FAIL

# ── Debt Service Coverage Ratio  (DSCR = NOI / annual debt service) ──────────
DSCR_PASS = 1.30   # ≥ 1.30x → green
DSCR_WARN = 1.20   # ≥ 1.20x → amber;  < 1.20x → RED FAIL

# ── Interest Coverage Ratio  (ICR = NOI / annual interest) ───────────────────
ICR_PASS = 1.75    # ≥ 1.75x → green
ICR_WARN = 1.50    # ≥ 1.50x → amber;  < 1.50x → RED FAIL

# ── Weighted Average Unexpired Lease Term  (WAULT, years) ────────────────────
WAULT_PASS = 4.0   # ≥ 4.0 yr → green
WAULT_WARN = 2.5   # ≥ 2.5 yr → amber;  < 2.5 yr → RED FAIL

# ── Vacancy Rate  (vacant GLA / total GLA) ───────────────────────────────────
VACANCY_PASS = 0.10  # ≤ 10% → green
VACANCY_WARN = 0.15  # ≤ 15% → amber;   > 15% → YELLOW FAIL

# ── Anchor Concentration  (anchor rent / total passing rent) ─────────────────
ANCHOR_PASS = 0.55   # ≤ 55% → green
ANCHOR_WARN = 0.65   # ≤ 65% → amber;   > 65% → YELLOW FAIL

# ── 3-Year Lease Expiry Risk  (rent expiring ≤ 3 yrs / total rent) ───────────
EXPIRY_3YR_PASS = 0.30  # ≤ 30% → green
EXPIRY_3YR_WARN = 0.45  # ≤ 45% → amber;  > 45% → YELLOW FAIL

# ── Gross Initial Yield  (GIY = gross rent / asking price) ───────────────────
GIY_PASS = 0.055   # ≥ 5.5% → green  (DE retail 2026 benchmark)
GIY_WARN = 0.045   # ≥ 4.5% → amber;  < 4.5% → YELLOW FAIL

# ── Debt Yield  (DY = NOI / loan) ────────────────────────────────────────────
DY_PASS = 0.075    # ≥ 7.5% → green
DY_WARN = 0.060    # ≥ 6.0% → amber;  < 6.0% → YELLOW FAIL

# ── Deal Repair Engine ────────────────────────────────────────────────────────
# Target LTV used when recommending an equity injection to fix an overleveraged
# deal. Intentionally set 2pp below LTV_PASS to give headroom after the fix.
LTV_REPAIR_TARGET = 0.63


# =============================================================================
# 3. QA CHECK LABEL STRINGS
# =============================================================================
# These string values are the canonical names for each QA check.
# They appear as the `check` field on every QAResult object returned by
# rent_roll_parser.run_qa_audit(), and as keys in bank_submission._RISK_MAP.
# If a label ever needs to change, update it here and nowhere else.

CHECK_LTV        = "LTV <= 65%"
CHECK_DSCR       = "DSCR >= 1.30x"
CHECK_ICR        = "ICR >= 1.75x"
CHECK_WAULT      = "WAULT >= 4 years"
CHECK_VACANCY    = "Vacancy <= 10%"
CHECK_ANCHOR     = "Anchor Concentration <= 55%"
CHECK_EXPIRY_3YR = "3-Year Expiry Risk <= 30%"
CHECK_GIY        = "GIY >= 5.5% (retail DE 2026)"
CHECK_NOI        = "NOI > 0"
CHECK_EQUITY     = "Equity > 0"
CHECK_DY         = "Debt Yield >= 7.5% (NOI / Loan)"
CHECK_COST_SHEET = "NOI - Cost Sheet populated"


# =============================================================================
# 4. NOI / COST MODEL
# =============================================================================

VOID_HOLD_RATE      = 20.0   # EUR/m²/yr — landlord void holding cost (insurance + svc chg)
NOI_FALLBACK_MARGIN = 0.80   # Fallback NOI margin when no itemised Cost Sheet or slider
AFA_BUILDING_PCT    = 0.75   # Building value as fraction of purchase price (land = 25%)

# §7 EStG AfA (Absetzung für Abnutzung) — straight-line depreciation rates by build era.
# Rate is applied to building value only (AFA_BUILDING_PCT × purchase price).
# Source: §7 Abs.4 EStG as applicable to non-residential commercial property.
AFA_YEAR_PRE1925    = 1925   # Cutoff: buildings BEFORE this year → AFA_RATE_PRE1925
AFA_YEAR_MODERN     = 2000   # Cutoff: buildings AFTER  this year → AFA_RATE_MODERN
AFA_RATE_PRE1925    = 0.025  # 2.5% — §7 Abs.4 Nr.2a EStG (pre-1925, 40-yr life)
AFA_RATE_STANDARD   = 0.020  # 2.0% — §7 Abs.4 Nr.2b EStG (1925–2000, 50-yr life)
AFA_RATE_MODERN     = 0.030  # 3.0% — §7 Abs.4 Nr.1  EStG (post-2000, 33-yr life)


# =============================================================================
# 5. ANCHOR TENANTS
# =============================================================================

# ANCHOR_NAMES — lowercase substrings for fuzzy tenant-name matching.
# A tenant is flagged as anchor if any entry appears anywhere in its name
# (case-insensitive). Keep entries specific enough to avoid false positives.
ANCHOR_NAMES: list[str] = [
    "rewe",
    "edeka",
    "kaufland",
    "aldi",
    "lidl",
    "penny",
    "netto",
    "obi",
    "bauhaus",
    "hornbach",
    "mediamarkt",
    "saturn",
    "dm ",          # trailing space avoids matching "dm" inside other words
    "rossmann",
    "müller",
    "globus",
]

# ANCHOR_COVENANT_RANKING — ordered by credit covenant quality.
# Used by underwriters to assess tenant credit, set WAULT expectations,
# and estimate renewal probability for concentration-risk notes.
#
# Fields per entry:
#   name              Display name
#   slug              Substring used in ANCHOR_NAMES for matching
#   tier              1 (strongest) → 5 (most cyclical)
#   sector            Retail sub-sector
#   typical_area_sqm  Tuple (min, max) typical store size in m²
#   typical_wault     String describing typical unexpired term range
#   covenant          Indicative credit quality (not a formal rating)

ANCHOR_COVENANT_RANKING: list[dict] = [
    # ── Tier 1: Investment-grade food anchors ─────────────────────────────────
    {
        "name": "REWE Group",
        "slug": "rewe",
        "tier": 1,
        "sector": "Food",
        "typical_area_sqm": (1500, 3500),
        "typical_wault": "10-15 yr",
        "covenant": "AA-equivalent (private cooperative; Schufa AAA)",
        "notes": "Largest food retailer operator in Germany. Renewal rate extremely high.",
    },
    {
        "name": "Edeka Group",
        "slug": "edeka",
        "tier": 1,
        "sector": "Food",
        "typical_area_sqm": (1200, 4000),
        "typical_wault": "10-20 yr",
        "covenant": "AA-equivalent (cooperative structure; very strong balance sheet)",
        "notes": "Independent operator structure; each store owner separately creditworthy.",
    },
    {
        "name": "Kaufland (Schwarz Gruppe)",
        "slug": "kaufland",
        "tier": 1,
        "sector": "Food",
        "typical_area_sqm": (3000, 8000),
        "typical_wault": "15-25 yr",
        "covenant": "AA (Schwarz Gruppe; unlisted; considered one of Europe's strongest retailers)",
        "notes": "Hypermarket format. Very long leases; rarely exits existing stores.",
    },
    {
        "name": "Globus SB-Warenhaus",
        "slug": "globus",
        "tier": 1,
        "sector": "Food",
        "typical_area_sqm": (5000, 15000),
        "typical_wault": "15-25 yr",
        "covenant": "A-equivalent (family-owned; conservative balance sheet)",
        "notes": "Owner-operated hypermarket. Very long leases; strong renewal intention.",
    },
    # ── Tier 2: Discount food anchors ────────────────────────────────────────
    {
        "name": "Aldi (Nord / Süd)",
        "slug": "aldi",
        "tier": 2,
        "sector": "Discount Food",
        "typical_area_sqm": (800, 1500),
        "typical_wault": "10-15 yr",
        "covenant": "AA-equivalent (unlisted; regarded as extremely resilient)",
        "notes": "Compact, standardised stores. Some owned freehold; leased stores show high renewal.",
    },
    {
        "name": "Lidl (Schwarz Gruppe)",
        "slug": "lidl",
        "tier": 2,
        "sector": "Discount Food",
        "typical_area_sqm": (1000, 1800),
        "typical_wault": "10-15 yr",
        "covenant": "AA (Schwarz Gruppe)",
        "notes": "Pan-European discounter. Growing store network; strong renewal probability.",
    },
    {
        "name": "Netto Marken-Discount",
        "slug": "netto",
        "tier": 2,
        "sector": "Discount Food",
        "typical_area_sqm": (600, 1200),
        "typical_wault": "7-12 yr",
        "covenant": "A (Edeka Group subsidiary)",
        "notes": "Edeka-owned discount chain. Covenant supported by parent-group resources.",
    },
    {
        "name": "Penny (REWE Group)",
        "slug": "penny",
        "tier": 2,
        "sector": "Discount Food",
        "typical_area_sqm": (600, 1200),
        "typical_wault": "7-12 yr",
        "covenant": "A+ (REWE Group subsidiary)",
        "notes": "REWE-owned discounter. Covenant equivalent to REWE parent.",
    },
    # ── Tier 3: DIY / large-format specialists ────────────────────────────────
    {
        "name": "OBI",
        "slug": "obi",
        "tier": 3,
        "sector": "DIY",
        "typical_area_sqm": (4000, 12000),
        "typical_wault": "15-20 yr",
        "covenant": "A-equivalent (Tengelmann Group; private)",
        "notes": "Largest DIY chain in Germany. High CapEx stores; sticky leases.",
    },
    {
        "name": "Bauhaus",
        "slug": "bauhaus",
        "tier": 3,
        "sector": "DIY",
        "typical_area_sqm": (6000, 20000),
        "typical_wault": "15-25 yr",
        "covenant": "A-equivalent (private; very conservative management)",
        "notes": "Premium DIY. Very long initial lease terms; rarely exits.",
    },
    {
        "name": "Hornbach",
        "slug": "hornbach",
        "tier": 3,
        "sector": "DIY",
        "typical_area_sqm": (8000, 25000),
        "typical_wault": "15-25 yr",
        "covenant": "BBB+ (listed; SDAX; family-controlled float)",
        "notes": "Largest DIY format. Listed covenant; audited financials available.",
    },
    # ── Tier 4: Health & beauty / drugstores ─────────────────────────────────
    {
        "name": "dm-drogerie markt",
        "slug": "dm ",
        "tier": 4,
        "sector": "Health & Beauty",
        "typical_area_sqm": (400, 900),
        "typical_wault": "7-10 yr",
        "covenant": "A-equivalent (private; Götz Werner family; very strong)",
        "notes": "High-traffic format. Strong renewal record; consistent brand expansion.",
    },
    {
        "name": "Rossmann",
        "slug": "rossmann",
        "tier": 4,
        "sector": "Health & Beauty",
        "typical_area_sqm": (400, 900),
        "typical_wault": "7-10 yr",
        "covenant": "A-equivalent (private; Rossmann family)",
        "notes": "dm competitor. Similar footprint and renewal behaviour.",
    },
    {
        "name": "Müller Drogerie",
        "slug": "müller",
        "tier": 4,
        "sector": "Health & Beauty",
        "typical_area_sqm": (600, 2000),
        "typical_wault": "7-10 yr",
        "covenant": "BBB+ (private; strong but smaller than dm/Rossmann)",
        "notes": "Larger store format than dm/Rossmann; includes clothing and toys.",
    },
    # ── Tier 5: Non-food / electronics (most cyclical) ────────────────────────
    {
        "name": "MediaMarkt / Saturn (Ceconomy)",
        "slug": "mediamarkt",
        "tier": 5,
        "sector": "Consumer Electronics",
        "typical_area_sqm": (2000, 8000),
        "typical_wault": "5-10 yr",
        "covenant": "BBB (Ceconomy AG; listed; MDAX)",
        "notes": "Audited listed covenant. Sector under structural pressure from e-commerce.",
    },
]
