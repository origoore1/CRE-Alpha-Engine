"""
test_ratios.py — SAE Phase 1
Unit tests for ratio_calculator and scorer using synthetic data.
"""

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
from ratio_calculator import calculate, _cagr, _div, _trend
from scorer import score


# ── Helpers ───────────────────────────────────────────────────────────────────

def make_data(**overrides):
    """Build a minimal synthetic data dict resembling a healthy company."""
    base = {
        "ticker":       "TEST",
        "name":         "Test Corp",
        "sector":       "Technology",
        "industry":     "Software",
        "currency":     "USD",
        "years":        [2020, 2021, 2022, 2023],
        "market_cap":   50_000_000_000,
        "current_price": 100.0,
        "beta":          1.1,
        "shares_outstanding": 500_000_000,
        # Income statement (4 years, oldest first)
        "revenue":       [8e9, 10e9, 12e9, 14e9],
        "gross_profit":  [5e9,  6.5e9, 7.8e9, 9.2e9],
        "operating_inc": [2e9,  2.8e9, 3.5e9, 4.2e9],
        "net_income":    [1.6e9, 2.2e9, 2.8e9, 3.4e9],
        "ebit":          [2e9,  2.8e9, 3.5e9, 4.2e9],
        "ebitda":        [2.5e9, 3.4e9, 4.2e9, 5.0e9],
        "interest_exp":  [100e6, 110e6, 90e6, 80e6],
        "income_tax":    [400e6, 560e6, 700e6, 850e6],
        "pretax_inc":    [2.0e9, 2.76e9, 3.5e9, 4.25e9],
        "rd_expense":    [800e6, 1e9, 1.2e9, 1.4e9],
        "sga_expense":   [500e6, 600e6, 700e6, 780e6],
        "diluted_shares":[510e6, 505e6, 502e6, 500e6],
        "eps_series":    [3.14, 4.36, 5.58, 6.80],
        "da":            [500e6, 600e6, 700e6, 800e6],
        # Balance sheet
        "total_assets":  [20e9, 22e9, 24e9, 26e9],
        "total_liab":    [8e9,  9e9,  10e9, 11e9],
        "equity":        [12e9, 13e9, 14e9, 15e9],
        "total_debt":    [2e9,  1.8e9, 1.5e9, 1.2e9],
        "cash":          [3e9,  4e9,  5e9,  6e9],
        "receivables":   [1e9,  1.1e9, 1.2e9, 1.3e9],
        "inventory":     [200e6, 220e6, 240e6, 260e6],
        "net_ppe":       [2e9,  2.1e9, 2.2e9, 2.3e9],
        "goodwill":      [1e9,  1e9,   1e9,   1e9],
        # Cash flow
        "operating_cf":  [2.2e9, 3.0e9, 3.8e9, 4.5e9],
        "capex":         [400e6, 450e6, 500e6, 550e6],
        "free_cf":       [1.8e9, 2.55e9, 3.3e9, 3.95e9],
        "sbc":           [200e6, 230e6, 260e6, 290e6],
    }
    base.update(overrides)
    return base


# ── Math helper tests ─────────────────────────────────────────────────────────

def test_cagr_basic():
    assert abs(_cagr(100, 200, 3) - 0.2599) < 0.001

def test_cagr_zero_start():
    assert _cagr(0, 100, 3) is None

def test_cagr_negative():
    assert _cagr(-100, 100, 3) is None

def test_div_zero():
    assert _div(10, 0) is None

def test_div_normal():
    assert abs(_div(3, 4) - 0.75) < 0.0001

def test_trend_improving():
    slope = _trend([1.0, 1.2, 1.5, 1.8])
    assert slope is not None and slope > 0

def test_trend_declining():
    slope = _trend([2.0, 1.8, 1.5, 1.1])
    assert slope is not None and slope < 0

def test_trend_too_short():
    assert _trend([1.0, 2.0]) is None


# ── Ratio calculator tests ────────────────────────────────────────────────────

def test_revenue_cagr_positive():
    d = make_data()
    r = calculate(d)
    assert r.get("revenue_cagr_3yr") is not None
    assert r["revenue_cagr_3yr"] > 0.10   # 10-14B over 3yr ≈ 20%

def test_gross_margin_positive():
    d = make_data()
    r = calculate(d)
    gm = r.get("gross_margin")
    assert gm is not None and 0.5 < gm < 0.8

def test_operating_margin_positive():
    d = make_data()
    r = calculate(d)
    om = r.get("operating_margin")
    assert om is not None and om > 0.25

def test_fcf_conversion_healthy():
    d = make_data()
    r = calculate(d)
    fc = r.get("fcf_conversion")
    assert fc is not None and fc > 0.8

def test_roic_positive():
    d = make_data()
    r = calculate(d)
    assert r.get("roic") is not None
    assert r["roic"] > 0.10

def test_roic_above_wacc():
    d = make_data()
    r = calculate(d)
    assert r.get("roic_minus_wacc") is not None
    assert r["roic_minus_wacc"] > 0

def test_net_debt_to_ebitda():
    d = make_data()
    r = calculate(d)
    nd = r.get("net_debt_to_ebitda")
    assert nd is not None
    # Net debt = 1.2B - 6B = -4.8B → negative (net cash)
    assert nd < 0

def test_share_count_cagr_declining():
    d = make_data()
    r = calculate(d)
    sc = r.get("share_count_cagr")
    assert sc is not None and sc < 0   # declining shares = buybacks

def test_dcf_intrinsic_value_computed():
    d = make_data()
    r = calculate(d)
    assert r.get("dcf_intrinsic_value") is not None
    assert r["dcf_intrinsic_value"] > 0


# ── Scorer tests ──────────────────────────────────────────────────────────────

def test_healthy_company_scores_above_65():
    d = make_data()
    r = calculate(d)
    s = score(r, d)
    assert s["module1_score"] >= 65, f"Expected ≥65, got {s['module1_score']}"

def test_classification_label_present():
    d = make_data()
    r = calculate(d)
    s = score(r, d)
    assert s["classification"] in [
        "Elite Compounder", "Attractive Quality",
        "Neutral — Monitor", "Fails Quality Screen", "Disqualified"
    ]

def test_red_flags_is_list():
    d = make_data()
    r = calculate(d)
    s = score(r, d)
    assert isinstance(s["red_flags"], list)

def test_negative_fcf_triggers_flag():
    d = make_data(free_cf=[-500e6, -200e6, -100e6, -50e6])
    r = calculate(d)
    s = score(r, d)
    assert any("FCF" in f or "cash" in f.lower() for f in s["red_flags"])

def test_high_dilution_triggers_flag():
    d = make_data(
        diluted_shares=[400e6, 430e6, 460e6, 500e6]  # growing ~8%/yr
    )
    r = calculate(d)
    s = score(r, d)
    assert any("dilut" in f.lower() for f in s["red_flags"])

def test_score_capped_at_49_with_3_flags():
    """Three or more red flags cap score at 49."""
    d = make_data(
        free_cf=[-1e9, -800e6, -600e6, -400e6],          # negative FCF → flag
        diluted_shares=[400e6, 450e6, 510e6, 580e6],      # heavy dilution → flag
        operating_cf=[100e6, 80e6, 50e6, 20e6],           # OCF collapsing → flag
        net_income=[1.6e9, 2.2e9, 2.8e9, 3.4e9],
    )
    r = calculate(d)
    s = score(r, d)
    if len(s["red_flags"]) >= 3:
        assert s["module1_score"] <= 49

def test_all_sub_scores_present():
    d = make_data()
    r = calculate(d)
    s = score(r, d)
    for key in ["score_1a_capital_efficiency", "score_1b_growth_quality",
                "score_1c_profitability", "score_1d_cash_quality",
                "score_1e_balance_sheet", "score_1f_capital_allocation",
                "score_1g_red_flags"]:
        assert key in s, f"Missing {key}"
        assert 0 <= s[key] <= 100, f"{key} out of range: {s[key]}"
