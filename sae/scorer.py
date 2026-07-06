"""
scorer.py — SAE Phase 1
Scores ratios against the Tripartite Benchmark (Phase 1: absolute floor + trend).
Sector-relative percentile is deferred to Phase 2.
Returns sub-scores, Module 1 composite, classification label, and red flags.
"""

from typing import Optional


# ── Helpers ───────────────────────────────────────────────────────────────────

def _clamp(v: float, lo=0.0, hi=100.0) -> float:
    return max(lo, min(hi, v))


def _score_bands(value: Optional[float], bands: list, default=50) -> int:
    """
    bands: [(threshold, score), ...] sorted ASCENDING.
    Returns the score of the highest band the value reaches.
    """
    if value is None:
        return default
    result = 0
    for threshold, pts in bands:
        if value >= threshold:
            result = pts
    return result


def _trend_adj(trend: Optional[float], weight: int = 12) -> int:
    """Convert a normalised trend slope to a score adjustment."""
    if trend is None:
        return 0
    if trend > 0.05:
        return weight
    if trend > 0.01:
        return weight // 2
    if trend < -0.05:
        return -weight
    if trend < -0.01:
        return -(weight // 2)
    return 0


# ── Sub-area scorers ──────────────────────────────────────────────────────────

def _score_1a_capital_efficiency(r: dict) -> tuple[int, list]:
    """ROIC / Capital Efficiency — 20% of Module 1"""
    flags = []
    roic       = r.get("roic")
    wacc       = r.get("wacc", 0.08)
    roic_trend = r.get("roic_trend")
    rmw        = r.get("roic_minus_wacc")

    base = _score_bands(roic, [
        (-99,  0),
        (0.00, 20),
        (0.08, 40),
        (0.12, 55),   # at or just above typical WACC
        (0.15, 68),
        (0.20, 80),
        (0.25, 90),
        (0.30, 97),
    ])

    # Trend adjustment
    base = int(_clamp(base + _trend_adj(roic_trend, 15)))

    # ROIC vs WACC bonus/penalty
    if rmw is not None:
        if rmw > 0.15:
            base = int(_clamp(base + 5))
        elif rmw < 0:
            base = int(_clamp(base - 10))
            flags.append(f"ROIC ({roic:.1%}) below WACC ({wacc:.1%}) — value destruction")

    return base, flags


def _score_1b_growth(r: dict) -> tuple[int, list]:
    """Growth Quality — 15% of Module 1"""
    flags = []

    def band(v):
        return _score_bands(v, [(-99, 0), (0.0, 20), (0.04, 40),
                                  (0.07, 55), (0.10, 70), (0.15, 83), (0.20, 95)])

    scores = []
    rev_cagr = r.get("revenue_cagr_3yr") or r.get("revenue_cagr_5yr")
    gp_cagr  = r.get("gross_profit_cagr_3yr")
    eps_cagr = r.get("eps_cagr_3yr")

    if rev_cagr is not None:
        scores.append(band(rev_cagr))
    if gp_cagr is not None:
        scores.append(band(gp_cagr))
    if eps_cagr is not None:
        scores.append(band(eps_cagr))

    if not scores:
        return 50, flags

    # Penalty if gross profit growing slower than revenue (margin dilution)
    if rev_cagr and gp_cagr and rev_cagr > 0 and gp_cagr < rev_cagr - 0.03:
        flags.append("Gross profit growing slower than revenue — margin dilution risk")

    return int(sum(scores) / len(scores)), flags


def _score_1c_profitability(r: dict) -> tuple[int, list]:
    """Profitability & Margins — 15% of Module 1.
    NOTE: Phase 1 uses broad thresholds. Phase 2 will add sector-relative percentile."""
    flags = []
    scores = []

    gm  = r.get("gross_margin");      gm_t  = r.get("gross_margin_trend")
    om  = r.get("operating_margin");  om_t  = r.get("operating_margin_trend")
    nm  = r.get("net_margin");        nm_t  = r.get("net_margin_trend")

    if gm is not None:
        s = _score_bands(gm, [(0, 10), (0.10, 30), (0.20, 50),
                               (0.35, 65), (0.50, 80), (0.65, 92)])
        s = int(_clamp(s + _trend_adj(gm_t, 12)))
        scores.append(s)

    if om is not None:
        s = _score_bands(om, [(-99, 0), (0.0, 20), (0.05, 40),
                               (0.10, 58), (0.15, 72), (0.22, 85), (0.30, 95)])
        s = int(_clamp(s + _trend_adj(om_t, 12)))
        scores.append(s)

    if nm is not None:
        s = _score_bands(nm, [(-99, 0), (0.0, 20), (0.04, 42),
                               (0.08, 58), (0.12, 72), (0.18, 85), (0.25, 95)])
        s = int(_clamp(s + _trend_adj(nm_t, 12)))
        scores.append(s)

    if not scores:
        return 50, flags

    # Warn if all margins trending down
    trends = [t for t in [gm_t, om_t, nm_t] if t is not None]
    if trends and all(t < -0.02 for t in trends):
        flags.append("All margins in multi-year decline — competitive pressure or cost inflation")

    return int(sum(scores) / len(scores)), flags


def _score_1d_cash_quality(r: dict) -> tuple[int, list]:
    """Cash Quality & Earnings Integrity — 15% of Module 1"""
    flags = []
    base = 65

    ocf_ni   = r.get("ocf_to_net_income")
    accrual  = r.get("accrual_ratio")
    fcf_m    = r.get("fcf_margin")
    fcf_c    = r.get("fcf_conversion")
    recv_vs  = r.get("recv_vs_rev_growth")

    # OCF / Net Income
    if ocf_ni is not None:
        if ocf_ni > 1.20:
            base += 15
        elif ocf_ni > 0.90:
            base += 5
        elif ocf_ni < 0.70:
            base -= 18
            flags.append(f"OCF/Net Income = {ocf_ni:.2f} — earnings quality weak")
        elif ocf_ni < 0:
            base -= 35
            flags.append("Negative OCF while reporting positive net income — serious quality risk")

    # Accrual ratio
    if accrual is not None:
        if abs(accrual) < 0.02:
            base += 5
        elif abs(accrual) > 0.08:
            base -= 12
            flags.append(f"High accrual ratio ({accrual:.2%}) — possible earnings inflation")

    # FCF margin
    if fcf_m is not None:
        if fcf_m > 0.15:
            base += 10
        elif fcf_m > 0.08:
            base += 5
        elif fcf_m < 0:
            base -= 20
            flags.append(f"Negative FCF margin ({fcf_m:.1%}) — company burning cash")

    # FCF conversion
    if fcf_c is not None:
        if fcf_c > 0.90:
            base += 8
        elif fcf_c < 0.50:
            base -= 18
            flags.append(f"Low FCF conversion ({fcf_c:.1%}) — profit not translating to cash")

    # Receivables vs revenue (leading quality signal)
    if recv_vs is not None and recv_vs > 0.08:
        base -= 8
        flags.append(f"Receivables growing faster than revenue (+{recv_vs:.1%} gap) — collection risk")

    return int(_clamp(base)), flags


def _score_1e_balance_sheet(r: dict) -> tuple[int, list]:
    """Balance Sheet & Debt Risk — 15% of Module 1"""
    flags = []
    base = 65

    nd_ebitda = r.get("net_debt_to_ebitda")
    int_cov   = r.get("interest_coverage")
    d_e       = r.get("debt_to_equity")
    net_debt  = r.get("net_debt", 0)

    # Net debt / EBITDA
    if nd_ebitda is not None:
        if nd_ebitda < 0:
            base += 20   # net cash position
        elif nd_ebitda < 0.5:
            base += 15
        elif nd_ebitda < 1.5:
            base += 8
        elif nd_ebitda < 3.0:
            base -= 5
        elif nd_ebitda > 4.0:
            base -= 25
            flags.append(f"Net debt/EBITDA = {nd_ebitda:.1f}x — leverage exceeds safety floor")
        elif nd_ebitda > 3.0:
            base -= 12

    # Interest coverage
    if int_cov is not None:
        if int_cov > 15:
            base += 10
        elif int_cov > 8:
            base += 5
        elif int_cov < 3.0:
            base -= 15
            flags.append(f"Interest coverage = {int_cov:.1f}x — debt service risk")
        elif int_cov < 1.5:
            base -= 30
            flags.append(f"Interest coverage < 1.5x ({int_cov:.1f}x) — cannot comfortably service debt")

    return int(_clamp(base)), flags


def _score_1f_capital_allocation(r: dict) -> tuple[int, list]:
    """Capital Allocation & Dilution — 10% of Module 1"""
    flags = []
    base = 65

    sh_cagr  = r.get("share_count_cagr")
    sbc_rev  = r.get("sbc_to_revenue")
    sbc_fcf  = r.get("sbc_to_fcf")

    if sh_cagr is not None:
        if sh_cagr < -0.03:
            base += 20   # strong buybacks
        elif sh_cagr < -0.01:
            base += 10
        elif sh_cagr > 0.05:
            base -= 22
            flags.append(f"Share dilution: {sh_cagr:+.1%}/yr — shareholders diluted meaningfully")
        elif sh_cagr > 0.02:
            base -= 10

    if sbc_rev is not None:
        if sbc_rev > 0.10:
            base -= 15
            flags.append(f"SBC = {sbc_rev:.1%} of revenue — real cost significantly above reported FCF")
        elif sbc_rev > 0.06:
            base -= 6

    if sbc_fcf is not None and sbc_fcf > 0.20:
        base -= 10
        flags.append(f"SBC consumes {sbc_fcf:.1%} of FCF — overstates true free cash generation")

    return int(_clamp(base)), flags


def _score_1g_red_flags(all_flags: list) -> int:
    """Accounting Red Flags — 10% of Module 1"""
    score = 100 - (len(all_flags) * 12)
    return int(_clamp(score))


# ── Main scorer ───────────────────────────────────────────────────────────────

def score(r: dict, data: dict = None) -> dict:
    """
    r: ratios dict from ratio_calculator.calculate()
    data: raw data dict (optional, for extra context)
    Returns complete scoring dict.
    """
    results = {}
    all_flags = []

    s1a, f1a = _score_1a_capital_efficiency(r)
    s1b, f1b = _score_1b_growth(r)
    s1c, f1c = _score_1c_profitability(r)
    s1d, f1d = _score_1d_cash_quality(r)
    s1e, f1e = _score_1e_balance_sheet(r)
    s1f, f1f = _score_1f_capital_allocation(r)

    all_flags = f1a + f1b + f1c + f1d + f1e + f1f

    s1g = _score_1g_red_flags(all_flags)

    results["score_1a_capital_efficiency"] = s1a
    results["score_1b_growth_quality"]     = s1b
    results["score_1c_profitability"]      = s1c
    results["score_1d_cash_quality"]       = s1d
    results["score_1e_balance_sheet"]      = s1e
    results["score_1f_capital_allocation"] = s1f
    results["score_1g_red_flags"]          = s1g
    results["red_flags"]                   = all_flags

    # ── Module 1 composite ────────────────────────────────────────────────────
    weights = {
        "score_1a_capital_efficiency": 0.20,
        "score_1b_growth_quality":     0.15,
        "score_1c_profitability":      0.15,
        "score_1d_cash_quality":       0.15,
        "score_1e_balance_sheet":      0.15,
        "score_1f_capital_allocation": 0.10,
        "score_1g_red_flags":          0.10,
    }

    module1 = sum(results[k] * w for k, w in weights.items())
    results["module1_score"] = round(module1, 1)

    # Hard cap at 49 if 3+ red flags
    if len(all_flags) >= 3:
        results["module1_score"] = min(results["module1_score"], 49.0)

    # ── Classification ────────────────────────────────────────────────────────
    m = results["module1_score"]
    if m >= 80:
        label = "Elite Compounder"
    elif m >= 65:
        label = "Attractive Quality"
    elif m >= 50:
        label = "Neutral — Monitor"
    elif m >= 35:
        label = "Fails Quality Screen"
    else:
        label = "Disqualified"

    results["classification"] = label
    return results
