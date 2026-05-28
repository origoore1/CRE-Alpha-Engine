"""
decision_dashboard.py  —  GRU Platform: One-Page Decision Dashboard
====================================================================
Renders a single Streamlit page with the most important deal information
for a rapid investment decision.  Import and call render_decision_dashboard()
from within a Streamlit tab.

Usage (inside app 7.py)
-----------------------
    from decision_dashboard import render_decision_dashboard

    with tab_dash:
        render_decision_dashboard(
            metrics=metrics,
            deal=deal,
            verdict=verdict,
            qa=qa,
            base_irr=base_irr,
            rr=rr,
        )
"""

from __future__ import annotations

import math
from typing import Any

import streamlit as st

from deal_supervisor import audit_deal


# ---------------------------------------------------------------------------
# Traffic-light helpers
# ---------------------------------------------------------------------------

def _tl_giy(v: float | None) -> str:
    if v is None:
        return "grey"
    if v > 0.065:
        return "green"
    if v >= 0.050:
        return "amber"
    return "red"


def _tl_niy(v: float | None) -> str:
    if v is None:
        return "grey"
    if v > 0.055:
        return "green"
    if v >= 0.040:
        return "amber"
    return "red"


def _tl_dscr(v: float | None) -> str:
    if v is None:
        return "grey"
    if v > 1.35:
        return "green"
    if v >= 1.20:
        return "amber"
    return "red"


def _tl_ltv(v: float | None) -> str:
    if v is None:
        return "grey"
    if v < 0.60:
        return "green"
    if v <= 0.70:
        return "amber"
    return "red"


def _tl_wault(v: float | None) -> str:
    if v is None:
        return "grey"
    if v > 7.0:
        return "green"
    if v >= 3.0:
        return "amber"
    return "red"


def _tl_irr(v: float | None) -> str:
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return "grey"
    if v > 0.10:
        return "green"
    if v >= 0.07:
        return "amber"
    return "red"


def _tl_vacancy(v: float | None) -> str:
    if v is None:
        return "grey"
    if v < 0.05:
        return "green"
    if v <= 0.15:
        return "amber"
    return "red"


def _tl_income_cliff(score: float | None) -> str:
    if score is None:
        return "grey"
    if score > 70:
        return "green"
    if score >= 40:
        return "amber"
    return "red"


def _tl_bid_vs_ask(status: str) -> str:
    if status == "AT_OR_BELOW":
        return "green"
    if status == "ABOVE":
        return "red"
    return "amber"


# ---------------------------------------------------------------------------
# Colour palette
# ---------------------------------------------------------------------------
_COLOURS = {
    "green": {"bg": "#d1fae5", "text": "#065f46", "border": "#6ee7b7"},
    "amber": {"bg": "#fef3c7", "text": "#92400e", "border": "#fcd34d"},
    "red":   {"bg": "#fee2e2", "text": "#991b1b", "border": "#fca5a5"},
    "grey":  {"bg": "#f3f4f6", "text": "#6b7280", "border": "#d1d5db"},
}

_VERDICT_COLOURS = {
    "PASS":        {"bg": "#d1fae5", "text": "#065f46"},
    "INVESTIGATE": {"bg": "#fef3c7", "text": "#92400e"},
    "DECLINE":     {"bg": "#fee2e2", "text": "#991b1b"},
}


# ---------------------------------------------------------------------------
# Income Cliff Score  (0–100)
# ---------------------------------------------------------------------------

def _compute_income_cliff_score(metrics: dict, rr: Any) -> float:
    """
    Heuristic income stability score (0–100).

    Component          Weight   Signal
    ─────────────────  ──────   ──────────────────────────────────────
    WAULT score          40 %   0–10yr mapped to 0–100
    Low vacancy          25 %   0–25% vacancy mapped to 100–0
    3-yr expiry safety   25 %   0–60% expiry risk mapped to 100–0
    Low void (binary)    10 %   full marks if vacancy < 5 %
    """
    wault     = metrics.get("wault") or 0.0
    vacancy   = metrics.get("vacancy_rate") or 0.0
    expiry3yr = metrics.get("expiry_risk_3yr_pct") or 0.0

    # WAULT: 0yr = 0, 10yr = 100 (capped)
    wault_score = min(wault / 10.0, 1.0) * 100.0

    # Vacancy: 0% → 100 pts,  25%+ → 0 pts
    vacancy_score = max(0.0, (1.0 - vacancy / 0.25)) * 100.0

    # 3-yr expiry: 0% expiry → 100, 60%+ → 0
    expiry_score = max(0.0, (1.0 - expiry3yr / 0.60)) * 100.0

    # Binary low-void bonus
    void_bonus = 100.0 if vacancy < 0.05 else 0.0

    score = (
        0.40 * wault_score
        + 0.25 * vacancy_score
        + 0.25 * expiry_score
        + 0.10 * void_bonus
    )
    return round(score, 1)


# ---------------------------------------------------------------------------
# Bid vs Ask  (compare asking price to NOI-implied value at target GIY 6.5 %)
# ---------------------------------------------------------------------------
_TARGET_GIY = 0.065   # 6.5% target GIY for implied max bid


def _compute_bid_vs_ask(metrics: dict) -> dict:
    asking    = metrics.get("asking_price") or 0
    gross_rent = metrics.get("gross_passing_rent") or 0

    if asking <= 0 or gross_rent <= 0:
        return {"status": "UNKNOWN", "asking": asking, "implied_bid": None, "delta_pct": None}

    implied_bid = gross_rent / _TARGET_GIY

    if asking <= implied_bid:
        status = "AT_OR_BELOW"
    else:
        status = "ABOVE"

    delta_pct = (asking - implied_bid) / implied_bid if implied_bid > 0 else None

    return {
        "status": status,
        "asking": asking,
        "implied_bid": implied_bid,
        "delta_pct": delta_pct,
    }


# ---------------------------------------------------------------------------
# Metric card HTML
# ---------------------------------------------------------------------------

def _metric_card_html(
    label: str,
    value_str: str,
    tl_colour: str,
    subtitle: str = "",
) -> str:
    c = _COLOURS.get(tl_colour, _COLOURS["grey"])
    dot_html = (
        f'<span style="display:inline-block;width:10px;height:10px;'
        f'border-radius:50%;background:{c["border"]};margin-right:6px"></span>'
    )
    sub_html = (
        f'<div style="font-size:0.72em;color:{c["text"]};margin-top:2px;'
        f'opacity:0.75">{subtitle}</div>'
        if subtitle else ""
    )
    return (
        f'<div style="background:{c["bg"]};border:1px solid {c["border"]};'
        f'border-radius:8px;padding:12px 14px;min-height:80px">'
        f'<div style="font-size:0.78em;font-weight:600;color:{c["text"]};'
        f'text-transform:uppercase;letter-spacing:0.04em">{dot_html}{label}</div>'
        f'<div style="font-size:1.45em;font-weight:700;color:{c["text"]};'
        f'margin-top:4px">{value_str}</div>'
        f'{sub_html}'
        f'</div>'
    )


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def render_decision_dashboard(
    metrics: dict[str, Any],
    deal: dict[str, Any],
    verdict: dict[str, Any],
    qa: list,
    base_irr: float | None,
    rr: Any,  # pd.DataFrame
) -> None:
    """
    Render the one-page Decision Dashboard.

    Parameters
    ----------
    metrics   : output of compute_metrics()
    deal      : output of load_deal_overview()
    verdict   : output of generate_verdict()
    qa        : output of run_qa_audit()
    base_irr  : float — 10yr levered IRR (may be NaN)
    rr        : pd.DataFrame — rent roll
    """

    prop_name    = deal.get("property_name") or "Unnamed Asset"
    asking       = metrics.get("asking_price") or 0
    verdict_text = (verdict.get("recommendation") or "INVESTIGATE").upper()

    # ── Build deal_supervisor input dict from live metrics ──────────────────
    anchor_info  = _find_anchor_info(metrics, rr)
    irr_val      = base_irr if (base_irr is not None and not math.isnan(base_irr)) else None

    supervisor_input = {
        "purchase_price":  asking,
        "giy":             metrics.get("giy"),
        "niy":             metrics.get("niy"),
        "wault":           metrics.get("wault"),
        "dscr":            metrics.get("dscr"),
        "ltv":             metrics.get("ltv"),
        "irr":             irr_val,
        "anchor_tenant":   anchor_info["name"],
        "anchor_covenant": anchor_info["covenant"],
        "vacancy_rate":    metrics.get("vacancy_rate"),
        "rent_roll":       _rr_to_list(rr),
        "verdict":         verdict_text,
    }
    sup = audit_deal(supervisor_input)

    # ── Derived values ───────────────────────────────────────────────────────
    income_cliff_score = _compute_income_cliff_score(metrics, rr)
    bid_vs_ask         = _compute_bid_vs_ask(metrics)
    dscr_val           = metrics.get("dscr")

    # ════════════════════════════════════════════════════════════════════════
    # TOP SECTION — Verdict Card
    # ════════════════════════════════════════════════════════════════════════
    vc = _VERDICT_COLOURS.get(verdict_text, _VERDICT_COLOURS["INVESTIGATE"])
    verdict_icon = {"PASS": "✓", "INVESTIGATE": "⚠", "DECLINE": "✗"}.get(verdict_text, "⚠")

    st.markdown(
        f"""
        <div style="background:{vc['bg']};border-radius:12px;padding:20px 28px;
                    text-align:center;margin-bottom:16px">
          <div style="font-size:3em;font-weight:800;color:{vc['text']};
                      letter-spacing:0.06em">{verdict_icon}  {verdict_text}</div>
          <div style="font-size:1.1em;font-weight:600;color:{vc['text']};
                      margin-top:4px">{prop_name}</div>
          <div style="font-size:0.9em;color:{vc['text']};opacity:0.8;margin-top:2px">
            Asking Price: EUR {asking:,.0f}
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    # Readiness score progress bar
    score = sup["readiness_score"]
    score_colour = "#10b981" if score >= 80 else ("#f59e0b" if score >= 60 else "#ef4444")
    st.markdown(
        f"""
        <div style="margin-bottom:20px">
          <div style="display:flex;justify-content:space-between;
                      font-size:0.8em;font-weight:600;color:#374151;margin-bottom:4px">
            <span>Supervisor Readiness Score</span>
            <span style="color:{score_colour}">{score:.0f}%</span>
          </div>
          <div style="background:#e5e7eb;border-radius:6px;height:10px">
            <div style="background:{score_colour};width:{score:.0f}%;
                        height:10px;border-radius:6px;
                        transition:width 0.4s ease"></div>
          </div>
          <div style="font-size:0.75em;color:#6b7280;margin-top:4px;font-style:italic">
            {sup['mentor_note']}
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    # ════════════════════════════════════════════════════════════════════════
    # MIDDLE SECTION — 3 × 3 Metrics Grid
    # ════════════════════════════════════════════════════════════════════════
    st.markdown("#### Key Metrics")

    giy_val      = metrics.get("giy")
    niy_val      = metrics.get("niy")
    ltv_val      = metrics.get("ltv")
    wault_val    = metrics.get("wault")
    vacancy_val  = metrics.get("vacancy_rate")

    # Format helpers
    def _pct(v, decimals=1):
        return f"{v:.{decimals}%}" if v is not None else "N/A"

    def _x(v, decimals=2):
        return f"{v:.{decimals}f}x" if v is not None else "N/A"

    def _yr(v):
        return f"{v:.1f} yr" if v is not None else "N/A"

    # Bid vs Ask display
    bva_status = bid_vs_ask["status"]
    bva_implied = bid_vs_ask.get("implied_bid")
    if bva_implied is not None:
        bva_delta = bid_vs_ask.get("delta_pct") or 0
        if bva_status == "AT_OR_BELOW":
            bva_label = f"OK ({bva_delta*100:+.1f}% vs target)"
        else:
            bva_label = f"PREMIUM (+{bva_delta*100:.1f}%)"
        bva_subtitle = f"Implied bid @ 6.5% GIY: EUR {bva_implied:,.0f}"
    else:
        bva_label    = "N/A"
        bva_subtitle = "Insufficient data"

    irr_display = _pct(irr_val, 1) if irr_val is not None else "N/A"

    row1_cols = st.columns(3)
    row2_cols = st.columns(3)
    row3_cols = st.columns(3)

    cards = [
        # Row 1
        ("GIY",      _pct(giy_val),    _tl_giy(giy_val),      "Gross Initial Yield"),
        ("NIY",      _pct(niy_val),    _tl_niy(niy_val),      "Net Initial Yield"),
        ("DSCR",     _x(dscr_val),     _tl_dscr(dscr_val),    "Debt Service Coverage"),
        # Row 2
        ("LTV",      _pct(ltv_val),    _tl_ltv(ltv_val),      "Loan-to-Value"),
        ("WAULT",    _yr(wault_val),   _tl_wault(wault_val),  "Wtd Avg Unexpired Lease"),
        ("IRR",      irr_display,      _tl_irr(irr_val),      "10yr Levered IRR"),
        # Row 3
        ("Vacancy",  _pct(vacancy_val),_tl_vacancy(vacancy_val), "Vacancy Rate"),
        ("Income Cliff", f"{income_cliff_score:.0f} / 100", _tl_income_cliff(income_cliff_score),
         "Income stability score"),
        ("Bid vs Ask", bva_label,      _tl_bid_vs_ask(bva_status), bva_subtitle),
    ]

    for i, (label, val_str, tl, subtitle) in enumerate(cards):
        row_idx = i // 3
        col_idx = i % 3
        col_group = [row1_cols, row2_cols, row3_cols][row_idx]
        with col_group[col_idx]:
            st.markdown(
                _metric_card_html(label, val_str, tl, subtitle),
                unsafe_allow_html=True,
            )

    st.markdown("<div style='margin-top:8px'></div>", unsafe_allow_html=True)

    # ════════════════════════════════════════════════════════════════════════
    # BOTTOM SECTION — Supervisor Alerts
    # ════════════════════════════════════════════════════════════════════════
    st.markdown("#### Supervisor Alerts")

    alerts = sup["alerts"]
    criticals = [a for a in alerts if a.severity == "CRITICAL"]
    warnings  = [a for a in alerts if a.severity == "WARNING"]

    if not alerts:
        st.markdown(
            '<div style="background:#d1fae5;border-radius:6px;padding:10px 14px;'
            'color:#065f46;font-size:0.85em">✓ No alerts — deal data is complete and '
            'metrics are internally consistent.</div>',
            unsafe_allow_html=True,
        )
    else:
        for alert in criticals:
            st.markdown(
                f'<div style="background:#fee2e2;border-left:4px solid #ef4444;'
                f'border-radius:4px;padding:8px 12px;margin-bottom:6px;'
                f'font-size:0.82em;color:#991b1b">'
                f'<b>CRITICAL — {alert.category}:</b> {alert.message}</div>',
                unsafe_allow_html=True,
            )
        for alert in warnings:
            st.markdown(
                f'<div style="background:#fef3c7;border-left:4px solid #f59e0b;'
                f'border-radius:4px;padding:8px 12px;margin-bottom:6px;'
                f'font-size:0.82em;color:#92400e">'
                f'<b>WARNING — {alert.category}:</b> {alert.message}</div>',
                unsafe_allow_html=True,
            )

    # Mentor note
    st.markdown(
        f'<div style="background:#f3f4f6;border-radius:6px;padding:10px 14px;'
        f'margin-top:8px;font-size:0.85em;color:#374151">'
        f'<b>Mentor note:</b> {sup["mentor_note"]}</div>',
        unsafe_allow_html=True,
    )


# ---------------------------------------------------------------------------
# Internal utilities
# ---------------------------------------------------------------------------

def _rr_to_list(rr: Any) -> list:
    """Convert a rent-roll DataFrame to a plain list (just needs to be non-empty)."""
    try:
        if rr is None:
            return []
        if hasattr(rr, "empty"):
            return [] if rr.empty else rr.to_dict("records")
        if isinstance(rr, list):
            return rr
    except Exception:
        pass
    return []


def _find_anchor_info(metrics: dict, rr: Any) -> dict:
    """
    Try to extract anchor tenant name and covenant strength from the rent roll.
    Falls back to generic values if the information is not available.
    """
    default = {"name": "Unknown", "covenant": "medium"}

    try:
        if rr is None or (hasattr(rr, "empty") and rr.empty):
            return default

        # Look for the tenant with the highest annual rent that is flagged as anchor
        if "anchor" in rr.columns and "tenant" in rr.columns:
            anchors = rr[rr["anchor"].astype(str).str.upper() == "Y"]
            if not anchors.empty:
                rent_col = "annual_rent" if "annual_rent" in anchors.columns else None
                if rent_col:
                    top = anchors.loc[anchors[rent_col].idxmax()]
                else:
                    top = anchors.iloc[0]
                name = str(top.get("tenant", "Unknown"))
                # Derive covenant from constants ANCHOR_COVENANT_RANKING
                covenant = _covenant_from_name(name)
                return {"name": name, "covenant": covenant}
    except Exception:
        pass

    return default


def _covenant_from_name(tenant_name: str) -> str:
    """Map a tenant name to a covenant strength string using ANCHOR_COVENANT_RANKING."""
    try:
        from constants import ANCHOR_COVENANT_RANKING
        name_lower = tenant_name.lower()
        for entry in ANCHOR_COVENANT_RANKING:
            slug = entry.get("slug", "")
            if slug and slug.strip() in name_lower:
                tier = entry.get("tier", 3)
                if tier <= 2:
                    return "strong"
                if tier <= 4:
                    return "medium"
                return "weak"
    except Exception:
        pass
    return "medium"
