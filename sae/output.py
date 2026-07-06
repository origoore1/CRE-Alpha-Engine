"""
output.py — SAE Phase 1
Formats and prints the scorecard to the console.
"""

from typing import Optional


def _pct(v, decimals=1):
    if v is None:
        return "N/A"
    return f"{v * 100:.{decimals}f}%"


def _x(v, decimals=1):
    if v is None:
        return "N/A"
    return f"{v:.{decimals}f}x"


def _m(v):
    if v is None:
        return "N/A"
    if abs(v) >= 1e12:
        return f"${v/1e12:.1f}T"
    if abs(v) >= 1e9:
        return f"${v/1e9:.1f}B"
    if abs(v) >= 1e6:
        return f"${v/1e6:.1f}M"
    return f"${v:.0f}"


def _score_bar(score, width=20):
    filled = round(score / 100 * width)
    return "X" * filled + "." * (width - filled)


def _classification_icon(label):
    icons = {
        "Elite Compounder":     "***",
        "Attractive Quality":   "** ",
        "Neutral -- Monitor":   "o  ",
        "Fails Quality Screen": "x  ",
        "Disqualified":         "xx ",
    }
    return icons.get(label, "   ")


def _bottom_line(scores, ratios, data):
    label   = scores["classification"]
    upside  = ratios.get("dcf_upside_pct")
    n_flags = len(scores.get("red_flags", []))

    if upside is None:
        dcf_sig = "unknown"
    elif upside > 0.15:
        dcf_sig = "undervalued"
    elif upside > -0.15:
        dcf_sig = "fair"
    else:
        dcf_sig = "overvalued"

    matrix = {
        ("Elite Compounder",     "undervalued"): "STRONG CANDIDATE -- elite business trading below intrinsic value. High-conviction entry.",
        ("Elite Compounder",     "fair"):        "QUALITY AT FAIR VALUE -- exceptional business, reasonable price. Worth owning.",
        ("Elite Compounder",     "overvalued"):  "ELITE BUSINESS, RICH PRICE -- outstanding fundamentals but market has priced in perfection. Wait for a pullback.",
        ("Elite Compounder",     "unknown"):     "ELITE COMPOUNDER -- no DCF signal available. Fundamentals are exceptional.",
        ("Attractive Quality",   "undervalued"): "ATTRACTIVE ENTRY -- solid business trading below intrinsic value.",
        ("Attractive Quality",   "fair"):        "GOOD BUSINESS, FAIR PRICE -- solid fundamentals, no obvious margin of safety.",
        ("Attractive Quality",   "overvalued"):  "GOOD BUSINESS, OVERPRICED -- quality is there but price leaves no margin of safety. Monitor for better entry.",
        ("Attractive Quality",   "unknown"):     "ATTRACTIVE QUALITY -- no DCF signal available.",
        ("Neutral -- Monitor",   "undervalued"): "WATCH -- mediocre fundamentals offset by potential value. High execution risk.",
        ("Neutral -- Monitor",   "fair"):        "NEUTRAL -- average business at fair price. No strong reason to buy or sell.",
        ("Neutral -- Monitor",   "overvalued"):  "AVOID FOR NOW -- average fundamentals with no price support.",
        ("Neutral -- Monitor",   "unknown"):     "NEUTRAL -- monitor for improvement in fundamentals.",
        ("Fails Quality Screen", "undervalued"): "VALUE TRAP RISK -- cheap for a reason. Weak fundamentals dominate.",
        ("Fails Quality Screen", "fair"):        "AVOID -- fails quality screen. Business does not meet minimum standards.",
        ("Fails Quality Screen", "overvalued"):  "AVOID -- weak business and expensive. Double negative.",
        ("Fails Quality Screen", "unknown"):     "AVOID -- fails quality screen.",
        ("Disqualified",         "undervalued"): "AVOID -- disqualified on fundamentals regardless of price.",
        ("Disqualified",         "fair"):        "AVOID -- disqualified on fundamentals.",
        ("Disqualified",         "overvalued"):  "AVOID -- disqualified on fundamentals and overpriced.",
        ("Disqualified",         "unknown"):     "AVOID -- disqualified on fundamentals.",
    }

    verdict = matrix.get((label, dcf_sig), label + " -- " + dcf_sig + " on DCF.")
    if n_flags >= 3:
        verdict += "  WARNING: Multiple red flags -- elevated risk."
    return verdict


def print_scorecard(data, ratios, scores):
    sym   = data["ticker"]
    name  = data["name"]
    sec   = data["sector"]
    ind   = data["industry"]
    price = data["current_price"]
    mc    = data["market_cap"]
    m1    = scores["module1_score"]
    label = scores["classification"]
    flags = scores["red_flags"]

    W = 62

    print()
    print("=" * W)
    print(f"  {name}  [{sym}]")
    print(f"  {sec} > {ind}")
    print(f"  Price: ${price:.2f}   Market cap: {_m(mc)}")
    print("-" * W)

    icon = _classification_icon(label)
    print(f"  MODULE 1 SCORE:  {m1:.0f} / 100   {_score_bar(m1)}")
    print(f"  CLASSIFICATION:  {icon}  {label}")
    print("-" * W)

    subs = [
        ("Capital Efficiency (ROIC)", "score_1a_capital_efficiency", 20),
        ("Growth Quality",             "score_1b_growth_quality",     15),
        ("Profitability & Margins",    "score_1c_profitability",      15),
        ("Cash & Earnings Quality",    "score_1d_cash_quality",       15),
        ("Balance Sheet",              "score_1e_balance_sheet",      15),
        ("Capital Allocation",         "score_1f_capital_allocation", 10),
        ("Accounting / Red Flags",     "score_1g_red_flags",          10),
    ]
    print("  SUB-SCORES:")
    for lbl, key, wt in subs:
        s = scores.get(key, 0)
        print(f"  {lbl:<28}  {s:>3}/100  wt={wt}%")
    print("-" * W)

    print("  KEY RATIOS:")
    rows = [
        ("ROIC",               _pct(ratios.get("roic"))),
        ("WACC",               _pct(ratios.get("wacc"))),
        ("ROIC - WACC spread", _pct(ratios.get("roic_minus_wacc"))),
        ("ROE",                _pct(ratios.get("roe"))),
        ("Revenue CAGR (3yr)", _pct(ratios.get("revenue_cagr_3yr"))),
        ("Revenue CAGR (5yr)", _pct(ratios.get("revenue_cagr_5yr"))),
        ("EPS CAGR (3yr)",     _pct(ratios.get("eps_cagr_3yr"))),
        ("Gross margin",       _pct(ratios.get("gross_margin"))),
        ("Operating margin",   _pct(ratios.get("operating_margin"))),
        ("Net margin",         _pct(ratios.get("net_margin"))),
        ("FCF margin",         _pct(ratios.get("fcf_margin"))),
        ("FCF conversion",     _pct(ratios.get("fcf_conversion"))),
        ("OCF / Net income",   f"{ratios['ocf_to_net_income']:.2f}" if ratios.get("ocf_to_net_income") else "N/A"),
        ("Net debt / EBITDA",  _x(ratios.get("net_debt_to_ebitda"))),
        ("Interest coverage",  _x(ratios.get("interest_coverage"))),
        ("Share count CAGR",   _pct(ratios.get("share_count_cagr"))),
        ("SBC / Revenue",      _pct(ratios.get("sbc_to_revenue"))),
        ("FCF yield",          _pct(ratios.get("fcf_yield"))),
        ("EV / FCF",           _x(ratios.get("ev_to_fcf"))),
    ]
    for lbl, val in rows:
        print(f"  {lbl:<26}  {val:>10}")

    print("-" * W)

    dcf_iv = ratios.get("dcf_intrinsic_value")
    dcf_up = ratios.get("dcf_upside_pct")
    dcf_g  = ratios.get("dcf_growth_assumed")
    print("  VALUATION:")
    print(f"  Current price:              ${price:.2f}")
    if dcf_iv:
        direction = "Upside" if (dcf_up or 0) > 0 else "Downside"
        print(f"  DCF intrinsic value:        ${dcf_iv:.2f}")
        print(f"  vs Current price:           {_pct(dcf_up)}  {direction}")
        print(f"  FCF growth assumed (DCF):   {_pct(dcf_g)}")

    print("-" * W)

    if flags:
        print("  RED FLAGS:")
        for f in flags:
            print(f"  - {f}")
    else:
        print("  No red flags triggered")

    if dcf_iv and dcf_up is not None:
        gap   = dcf_iv - price
        shares = data.get("shares_outstanding", 0)
        total  = gap * shares
        desc   = "unrealised upside" if gap > 0 else "overvaluation"
        print("-" * W)
        print("  VALUE GAP:")
        print(f"  Intrinsic value (DCF):      ${dcf_iv:.2f} / share")
        print(f"  Current price:              ${price:.2f} / share")
        print(f"  Gap per share:              {gap:+.2f}  ({dcf_up:+.1%})")
        if shares:
            print(f"  Total {desc:<19} {_m(abs(total))}")

    verdict = _bottom_line(scores, ratios, data)
    print("-" * W)
    print("  BOTTOM LINE:")
    print(f"  {verdict}")
    print("=" * W)
    print()
