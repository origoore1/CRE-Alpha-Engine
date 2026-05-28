"""
bank_submission.py  v1.0
========================
Generates a professional PDF bank/IC submission package from a completed
rent_roll_parser.py deal analysis.

Sections
--------
  Cover     -- Verdict badge, headline KPIs, deal identity
  1.        -- Transaction Summary (price, equity, loan, acquisition costs)
  2.        -- Key Underwriting Metrics (12 metrics with traffic-light cards)
  3.        -- Rent Roll Schedule (full tenant table + vacant units)
  4.        -- NOI Bridge / Cost Breakdown
  5.        -- Risk Assessment & Mitigants (mapped from QA audit flags)

Dependencies
------------
  fpdf (1.7.x)  rent_roll_parser  numpy_financial  pandas

Usage
-----
  python bank_submission.py <deal_excel_file>
  python bank_submission.py Scenario_1_PASS_Regensburg.xlsx
  python bank_submission.py Scenario_3_DECLINE_Duisburg.xlsx --exit-cap 0.070

  Or call generate_bank_submission() from app.py for Streamlit integration.
"""

import sys
from datetime import date
from pathlib import Path
from typing import Optional

import numpy_financial as npf
import pandas as pd
from fpdf import FPDF

import rent_roll_parser as rrp
from constants import (
    CHECK_LTV, CHECK_DSCR, CHECK_ICR, CHECK_WAULT, CHECK_VACANCY,
    CHECK_ANCHOR, CHECK_EXPIRY_3YR, CHECK_GIY, CHECK_NOI, CHECK_EQUITY,
    CHECK_DY, CHECK_COST_SHEET,
)


# =============================================================================
# COLOUR PALETTE
# =============================================================================

_NAVY    = (30,  58,  95)
_WHITE   = (255, 255, 255)
_LGRAY   = (248, 249, 251)
_MGRAY   = (220, 224, 230)
_DGRAY   = (100, 110, 125)
_BLACK   = (17,  24,  39)

_GREEN   = (4,   120, 87)
_AMBER   = (180, 83,  9)
_RED     = (185, 28,  28)
_LGREEN  = (209, 250, 229)
_LAMBER  = (254, 243, 199)
_LRED    = (254, 226, 226)


# =============================================================================
# TEXT / FORMAT HELPERS
# =============================================================================

def _safe(text) -> str:
    """Latin-1 safe string for fpdf 1.7.x built-in fonts."""
    return (
        str(text)
        .replace("\u20ac", "EUR")   # euro sign
        .replace("\u2014", " - ")   # em dash
        .replace("\u2013", "-")     # en dash
        .replace("\u2019", "'")     # right single quote
        .replace("\u2018", "'")     # left single quote
        .replace("\u201c", '"')     # left double quote
        .replace("\u201d", '"')     # right double quote
        .encode("latin-1", errors="replace")
        .decode("latin-1")
    )


def _eur(value, suffix: str = "") -> str:
    if value is None:
        return "--"
    return f"EUR {float(value):,.0f}{suffix}"


def _pct(value, decimals: int = 1) -> str:
    if value is None:
        return "--"
    return f"{float(value) * 100:.{decimals}f}%"


def _x(value, decimals: int = 2) -> str:
    if value is None:
        return "--"
    return f"{float(value):.{decimals}f}x"


# =============================================================================
# RISK / MITIGANT LIBRARY
# =============================================================================

_RISK_MAP = {
    CHECK_LTV: {
        "risk":     ("Loan-to-value exceeds the 65% lender threshold, reducing the equity buffer "
                     "against an asset value decline and increasing refinancing risk."),
        "mitigant": ("Increase equity contribution to bring LTV at or below 65%. Alternatively, "
                     "negotiate a 1-2% price reduction or explore a share deal structure to reduce "
                     "acquisition costs and improve effective LTV."),
    },
    CHECK_DSCR: {
        "risk":     ("Debt service coverage below 1.30x leaves limited headroom before a covenant "
                     "breach. A 15% rental reduction would impair the borrower's ability to service debt."),
        "mitigant": ("Stress-test NOI with a 10% rent reduction and document the floor DSCR. Seek an "
                     "interest-only period to defer amortisation while occupancy stabilises. "
                     "Consider a cash sweep mechanism once DSCR exceeds 1.50x."),
    },
    CHECK_ICR: {
        "risk":     ("Interest cover ratio below 1.75x exposes the loan to covenant breach risk at "
                     "refinancing if market rates increase."),
        "mitigant": ("Model a 200bps rate shock at the refinancing date. Explore an interest rate "
                     "cap or collar for the loan term to limit rate exposure."),
    },
    CHECK_WAULT: {
        "risk":     ("Weighted average unexpired lease term below 4 years. Income security is limited "
                     "within the loan term and refinancing may be difficult without lease renewals."),
        "mitigant": ("Require signed lease renewals or heads-of-terms on key tenants as a condition "
                     "to drawdown. Negotiate a vendor indemnity for void risk on leases expiring "
                     "within 18 months of closing."),
    },
    CHECK_VACANCY: {
        "risk":     ("Above-average vacancy creates ongoing void cost exposure and increases re-letting "
                     "execution risk. Extended voids could impair debt service coverage."),
        "mitigant": ("Build a void cost reserve into debt sizing at drawdown. Obtain a letting agent's "
                     "opinion on achievable rent and realistic re-letting timeline. Stress-test NOI "
                     "assuming a 24-month void on the largest vacant unit."),
    },
    CHECK_ANCHOR: {
        "risk":     ("Single anchor tenant exceeds 55% of gross rental income. Non-renewal or "
                     "departure of the anchor would be catastrophic for debt service coverage."),
        "mitigant": ("Obtain a full covenant report on the anchor tenant (D&B or equivalent). "
                     "Confirm lease renewal probability with a specialist letting agent. "
                     "Model a vacancy scenario: NOI and DSCR assuming the anchor unit is vacant."),
    },
    CHECK_EXPIRY_3YR: {
        "risk":     ("More than 30% of rental income expires within 3 years, creating material "
                     "re-letting risk within the loan term. Failure to renew could breach covenants."),
        "mitigant": ("Require renewal heads-of-terms from key expiring tenants before closing. "
                     "Negotiate a cash retention at drawdown equivalent to 12 months rent of "
                     "all leases expiring within 24 months, released on renewal confirmation."),
    },
    CHECK_GIY: {
        "risk":     ("Gross initial yield below the 5.5% market benchmark for German retail in 2026 "
                     "suggests potential overpricing relative to comparable transactions."),
        "mitigant": ("Present comparable transaction evidence justifying the yield. If covenant "
                     "quality and WAULT support a premium, document the rationale in the IC memo. "
                     "Model downside exit at 6.5-7.0% cap rate to stress-test returns."),
    },
    CHECK_NOI: {
        "risk":     ("Net operating income is zero or negative. The asset cannot service any debt "
                     "from current operating cash flows."),
        "mitigant": ("The deal is not financeable on current terms. A fundamental restructuring of "
                     "price, equity, or cost base is required before IC submission."),
    },
    CHECK_EQUITY: {
        "risk":     ("No equity contribution recorded. The deal cannot be structured without equity."),
        "mitigant": ("Verify equity and financing terms. This field is required for all debt analysis."),
    },
    CHECK_DY: {
        "risk":     ("Debt yield below 7.5% indicates the lender receives insufficient income "
                     "coverage on an unlevered basis, creating vulnerability on enforcement."),
        "mitigant": ("Reduce loan quantum or improve NOI to achieve a debt yield above 7.5%. "
                     "Document any precedent transactions at comparable debt yield levels."),
    },
    CHECK_COST_SHEET: {
        "risk":     ("NOI was estimated using a proxy margin, not an itemised operating cost "
                     "schedule. Actual costs may differ materially."),
        "mitigant": ("Request full property management accounts from the vendor covering the last "
                     "3 years. Verify service charge budgets and historical void periods before IC."),
    },
}

_DEFAULT_RM = {
    "risk":     "Flag identified during underwriting. Requires further review and documentation.",
    "mitigant": "Consult with asset management and legal counsel to assess materiality before IC.",
}


# =============================================================================
# IRR CALCULATOR
# =============================================================================

def _compute_levered_irr(
    noi: float,
    loan: float,
    equity: float,
    interest_rate: float,
    amort_rate: float,
    hold_years: int = 10,
    exit_cap_rate: float = 0.065,
    io_years: int = 0,
) -> Optional[float]:
    """
    Simplified levered IRR over hold_years.
    Year 0: -equity.  Years 1-N: NOI - debt_service.
    Year N: add exit proceeds (NOI / exit_cap_rate) net of remaining loan.
    """
    if equity <= 0 or noi <= 0:
        return None
    try:
        cash_flows = [-equity]
        balance = float(loan)
        for yr in range(1, hold_years + 1):
            interest  = balance * interest_rate
            principal = 0.0 if yr <= io_years else balance * amort_rate
            debt_svc  = interest + principal
            cf = noi - debt_svc
            if yr == hold_years:
                exit_value   = noi / exit_cap_rate
                net_proceeds = exit_value - (balance - principal)
                cf += net_proceeds
            cash_flows.append(cf)
            balance -= principal
        result = npf.irr(cash_flows)
        return float(result) if result == result else None  # NaN guard
    except Exception:
        return None


# =============================================================================
# PDF CLASS
# =============================================================================

class _PDF(FPDF):
    """FPDF subclass with IC document styling helpers."""

    def __init__(self, deal_name: str, date_str: str):
        super().__init__("P", "mm", "A4")
        self.deal_name = _safe(deal_name)
        self.date_str  = date_str
        self.set_auto_page_break(auto=True, margin=20)
        self.set_margins(18, 12, 18)

    # ── Built-in page header / footer ─────────────────────────────────────────

    def header(self):
        if self.page_no() == 1:
            return
        self.set_fill_color(*_NAVY)
        self.rect(0, 0, 210, 9, "F")
        self.set_font("Helvetica", "B", 6.5)
        self.set_text_color(*_WHITE)
        self.set_xy(10, 2)
        title = self.deal_name[:55]
        self.cell(100, 5, title, 0, 0, "L")
        self.set_xy(110, 2)
        self.cell(90, 5, "CONFIDENTIAL  |  FOR BANK USE ONLY", 0, 0, "R")
        self.set_text_color(*_BLACK)

    def footer(self):
        if self.page_no() == 1:
            return
        self.set_y(-13)
        self.set_draw_color(*_MGRAY)
        self.set_line_width(0.3)
        self.line(18, self.get_y(), 192, self.get_y())
        self.set_font("Helvetica", "", 6.5)
        self.set_text_color(*_DGRAY)
        self.set_x(18)
        self.cell(0, 6,
                  f"Page {self.page_no() - 1}  |  {self.date_str}  |  "
                  "German Retail Underwriter  |  IC Grade Analysis",
                  0, 0, "C")
        self.set_text_color(*_BLACK)

    # ── Typography helpers ─────────────────────────────────────────────────────

    def section_title(self, text: str):
        self.ln(5)
        self.set_fill_color(*_NAVY)
        self.set_text_color(*_WHITE)
        self.set_font("Helvetica", "B", 8.5)
        self.set_x(18)
        self.cell(174, 7, f"  {_safe(text).upper()}", 0, 1, "L", fill=True)
        self.set_text_color(*_BLACK)
        self.ln(2)

    def kv_row(self, label: str, value: str, shade: bool = False):
        fill = shade
        bg = _LGRAY if shade else _WHITE
        self.set_fill_color(*bg)
        self.set_font("Helvetica", "", 8)
        self.set_text_color(*_DGRAY)
        self.set_x(18)
        self.cell(78, 5.5, _safe(label), 0, 0, "L", fill=fill)
        self.set_font("Helvetica", "B", 8)
        self.set_text_color(*_BLACK)
        self.cell(96, 5.5, _safe(str(value)), 0, 1, "L", fill=fill)

    def metric_card(
        self,
        x: float, y: float, w: float, h: float,
        label: str, value: str,
        status: str, severity: str, benchmark: str,
    ):
        """Traffic-light metric card."""
        if status == "PASS":
            bg, border = _LGREEN, _GREEN
        elif severity == "RED":
            bg, border = _LRED, _RED
        else:
            bg, border = _LAMBER, _AMBER

        # Background fill
        self.set_fill_color(*bg)
        self.rect(x, y, w, h, "F")
        # Coloured left accent bar
        self.set_fill_color(*border)
        self.rect(x, y, 2, h, "F")
        # Thin outer border
        self.set_draw_color(*border)
        self.set_line_width(0.4)
        self.rect(x, y, w, h, "D")
        self.set_line_width(0.2)

        # Label
        self.set_xy(x + 4, y + 2)
        self.set_font("Helvetica", "", 6)
        self.set_text_color(*_DGRAY)
        self.cell(w - 6, 4, _safe(label), 0, 0, "L")

        # Value
        self.set_xy(x + 4, y + 6)
        self.set_font("Helvetica", "B", 10.5)
        self.set_text_color(*_BLACK)
        self.cell(w - 6, 7, _safe(value), 0, 0, "L")

        # Benchmark
        self.set_xy(x + 4, y + 13)
        self.set_font("Helvetica", "", 5)
        self.set_text_color(*_DGRAY)
        self.cell(w - 6, 3.5, _safe(f"Target: {benchmark}"), 0, 0, "L")

        self.set_text_color(*_BLACK)
        self.set_draw_color(0, 0, 0)

    def risk_block(self, item, bg_color, accent_color):
        """One risk/mitigant block for a single QA flag."""
        info = _RISK_MAP.get(item.check, _DEFAULT_RM)

        self.set_fill_color(*bg_color)
        self.set_draw_color(*accent_color)
        self.set_line_width(0.4)

        # Header row
        self.set_x(18)
        self.set_font("Helvetica", "B", 7.5)
        self.set_text_color(*_BLACK)
        self.cell(118, 6, _safe(f"  {item.check}"), "LTR", 0, "L", fill=True)
        self.set_text_color(*accent_color)
        self.set_font("Helvetica", "B", 7)
        self.cell(56, 6,
                  _safe(f"Actual: {item.actual}   Bench: {item.benchmark}"),
                  "TR", 1, "R", fill=True)
        self.set_text_color(*_BLACK)

        # Risk row
        self.set_x(18)
        self.set_font("Helvetica", "I", 6.5)
        self.set_text_color(*_DGRAY)
        self.cell(20, 5, "  Risk:", "LB", 0, "L", fill=True)
        self.set_font("Helvetica", "", 7)
        self.set_text_color(*_BLACK)
        self.multi_cell(154, 5, _safe(info["risk"]), "RB", "L", fill=True)

        # Mitigant row
        self.set_x(18)
        self.set_font("Helvetica", "I", 6.5)
        self.set_text_color(*_DGRAY)
        self.cell(22, 5, "  Mitigant:", "LB", 0, "L", fill=True)
        self.set_font("Helvetica", "", 7)
        self.set_text_color(*_BLACK)
        self.multi_cell(152, 5, _safe(info["mitigant"]), "RB", "L", fill=True)

        self.set_line_width(0.2)
        self.set_draw_color(0, 0, 0)
        self.ln(2)


# =============================================================================
# SECTION BUILDERS
# =============================================================================

def _cover_page(pdf: _PDF, deal: dict, metrics: dict, verdict: dict):
    pdf.add_page()

    # ── Full-width navy header bar ────────────────────────────────────────────
    pdf.set_fill_color(*_NAVY)
    pdf.rect(0, 0, 210, 52, "F")

    # Superscript institution label
    pdf.set_xy(18, 10)
    pdf.set_font("Helvetica", "", 7)
    pdf.set_text_color(160, 185, 210)
    pdf.cell(0, 5, "INSTITUTIONAL CREDIT SUBMISSION  |  GERMAN RETAIL CRE", 0, 0, "L")

    # Deal name
    deal_name = _safe(deal.get("property_name") or "Unnamed Deal")
    pdf.set_xy(18, 18)
    font_size = 18 if len(deal_name) <= 38 else 14 if len(deal_name) <= 52 else 11
    pdf.set_font("Helvetica", "B", font_size)
    pdf.set_text_color(*_WHITE)
    pdf.cell(140, 12, deal_name, 0, 0, "L")

    # Location / type subtitle
    pdf.set_xy(18, 34)
    pdf.set_font("Helvetica", "", 8.5)
    pdf.set_text_color(160, 185, 210)
    pdf.cell(0, 6,
             _safe(f"{deal.get('city','--')}   |   {deal.get('asset_type','--')}"),
             0, 0, "L")

    # ── Verdict badge (top-right) ─────────────────────────────────────────────
    rec = verdict.get("recommendation", "PASS")
    badge_color = _GREEN if rec == "PASS" else _RED if rec == "DECLINE" else _AMBER
    pdf.set_fill_color(*badge_color)
    pdf.rect(152, 14, 40, 26, "F")
    pdf.set_xy(152, 17)
    pdf.set_font("Helvetica", "B", 6.5)
    pdf.set_text_color(*_WHITE)
    pdf.cell(40, 5, "IC RECOMMENDATION", 0, 1, "C")
    pdf.set_xy(152, 22)
    pdf.set_font("Helvetica", "B", 16)
    pdf.cell(40, 12, rec, 0, 0, "C")

    pdf.set_text_color(*_BLACK)

    # ── Headline KPI strip ────────────────────────────────────────────────────
    y_strip = 56
    pdf.set_fill_color(*_LGRAY)
    pdf.rect(0, y_strip, 210, 26, "F")

    kpis = [
        ("Asking Price",  _eur(metrics.get("asking_price"))),
        ("Equity",        _eur(metrics.get("equity"))),
        ("Loan Amount",   _eur(metrics.get("loan_amount"))),
        ("GIY",           _pct(metrics.get("giy"))),
        ("WAULT",         f"{metrics.get('wault', 0):.1f} yrs"),
        ("Vacancy",       _pct(metrics.get("vacancy_rate"))),
    ]
    col_w = 174 / len(kpis)
    for i, (lbl, val) in enumerate(kpis):
        x = 18 + i * col_w
        pdf.set_xy(x, y_strip + 4)
        pdf.set_font("Helvetica", "", 6)
        pdf.set_text_color(*_DGRAY)
        pdf.cell(col_w, 4, _safe(lbl), 0, 0, "C")
        pdf.set_xy(x, y_strip + 9)
        pdf.set_font("Helvetica", "B", 9)
        pdf.set_text_color(*_BLACK)
        pdf.cell(col_w, 8, _safe(val), 0, 0, "C")

    # Dividers between KPIs
    pdf.set_draw_color(*_MGRAY)
    pdf.set_line_width(0.3)
    for i in range(1, len(kpis)):
        x = 18 + i * col_w
        pdf.line(x, y_strip + 5, x, y_strip + 20)
    pdf.set_line_width(0.2)

    # ── Rationale block ───────────────────────────────────────────────────────
    pdf.set_xy(18, 90)
    pdf.set_font("Helvetica", "B", 8.5)
    pdf.set_text_color(*_NAVY)
    pdf.cell(0, 6, "IC RECOMMENDATION RATIONALE", 0, 1, "L")
    pdf.set_draw_color(*_NAVY)
    pdf.set_line_width(0.5)
    pdf.line(18, pdf.get_y(), 192, pdf.get_y())
    pdf.set_line_width(0.2)
    pdf.ln(2)

    pdf.set_x(18)
    pdf.set_font("Helvetica", "", 8.5)
    pdf.set_text_color(*_BLACK)
    pdf.multi_cell(174, 5.5, _safe(verdict.get("rationale", "")), 0, "L")
    pdf.ln(2)

    for reason in verdict.get("top_reasons", [])[:5]:
        pdf.set_x(18)
        bullet = "  (!)" if "CAUTION" in reason else "  (+)"
        pdf.set_font("Helvetica", "", 8)
        pdf.multi_cell(174, 5, _safe(f"{bullet}  {reason}"), 0, "L")

    # ── Critical flags ────────────────────────────────────────────────────────
    if verdict.get("red_flags"):
        pdf.ln(3)
        pdf.set_fill_color(*_LRED)
        pdf.set_draw_color(*_RED)
        pdf.set_line_width(0.5)
        pdf.set_x(18)
        pdf.set_font("Helvetica", "B", 7.5)
        pdf.set_text_color(*_RED)
        pdf.cell(174, 6, "  CRITICAL FLAGS", "LTRB", 1, "L", fill=True)
        pdf.set_font("Helvetica", "", 7.5)
        for flag in verdict["red_flags"]:
            pdf.set_x(18)
            pdf.set_fill_color(*_LRED)
            pdf.multi_cell(174, 5, _safe(f"  {flag}"), "LRB", "L", fill=True)
        pdf.set_text_color(*_BLACK)
        pdf.set_line_width(0.2)

    # ── Confidentiality footer ────────────────────────────────────────────────
    pdf.set_xy(18, 268)
    pdf.set_draw_color(*_MGRAY)
    pdf.set_line_width(0.3)
    pdf.line(18, 268, 192, 268)
    pdf.set_xy(18, 271)
    pdf.set_font("Helvetica", "", 6.5)
    pdf.set_text_color(*_DGRAY)
    pdf.cell(87, 5, f"Analysis date: {date.today().strftime('%d %B %Y')}", 0, 0, "L")
    pdf.cell(87, 5, "CONFIDENTIAL - NOT FOR EXTERNAL DISTRIBUTION", 0, 0, "R")
    pdf.set_text_color(*_BLACK)
    pdf.set_line_width(0.2)


def _transaction_section(pdf: _PDF, deal: dict, metrics: dict):
    pdf.section_title("1.  Transaction Summary")
    shade = False
    rows = [
        ("Property Name",          deal.get("property_name") or "--"),
        ("Location",               deal.get("city") or "--"),
        ("Asset Type",             deal.get("asset_type") or "--"),
        ("Gross Leasable Area",    f"{metrics.get('gla_total', 0):,.0f} m\xb2"),
        ("Asking Price",           _eur(metrics.get("asking_price"))),
        ("Equity Contributed",     _eur(metrics.get("equity"))),
        ("Loan Amount",            _eur(metrics.get("loan_amount"))),
        ("Loan-to-Value (LTV)",    _pct(metrics.get("ltv"))),
        ("Total Acquisition Cost", _eur(metrics.get("total_acq_cost"))),
        ("GrESt Rate",             _pct(deal.get("grest_rate") or 0, 1)),
        ("Notar / Broker Fees",    _pct((deal.get("notar_rate") or 0) +
                                        (deal.get("broker_rate") or 0), 1)),
        ("Interest Rate",          _pct(deal.get("interest_rate") or 0, 2)),
        ("Amortisation Rate",      _pct(deal.get("amort_rate") or 0, 2)),
        ("Active Leases",          str(metrics.get("n_active_leases", "--"))),
        ("Vacant Units",           str(metrics.get("n_vacant", "--"))),
    ]
    for lbl, val in rows:
        pdf.kv_row(lbl, val, shade)
        shade = not shade
    pdf.ln(2)


def _metrics_section(pdf: _PDF, metrics: dict, qa: list, irr: Optional[float]):
    pdf.section_title("2.  Key Underwriting Metrics")

    qa_idx = {r.check: r for r in qa}

    def _st(check, fallback_status="PASS", fallback_sev="INFO"):
        r = qa_idx.get(check)
        return (r.status, r.severity) if r else (fallback_status, fallback_sev)

    cards = [
        # (label, value_str, qa_check_name, benchmark_display)
        ("LTV",              _pct(metrics.get("ltv")),
         CHECK_LTV,          "<= 65%"),
        ("DSCR",             _x(metrics.get("dscr")),
         CHECK_DSCR,         ">= 1.30x"),
        ("ICR",              _x(metrics.get("icr")),
         CHECK_ICR,          ">= 1.75x"),
        ("GIY",              _pct(metrics.get("giy")),
         CHECK_GIY,          ">= 5.5%"),
        ("NIY",              _pct(metrics.get("niy")),
         "_none_",           "NOI / Price"),
        ("WAULT",            f"{metrics.get('wault', 0):.1f} yrs",
         CHECK_WAULT,        ">= 4 yrs"),
        ("Vacancy Rate",     _pct(metrics.get("vacancy_rate")),
         CHECK_VACANCY,      "<= 10%"),
        ("Anchor Conc.",     _pct(metrics.get("anchor_pct")),
         CHECK_ANCHOR,       "<= 55%"),
        ("3yr Expiry Risk",  _pct(metrics.get("expiry_risk_3yr_pct")),
         CHECK_EXPIRY_3YR,   "<= 30%"),
        ("Debt Yield",       _pct(metrics.get("debt_yield")),
         CHECK_DY,           ">= 7.5%"),
        ("IRR (10yr est.)",  _pct(irr) if irr else "N/A",
         "_none_",           "> 7% target"),
        ("AfA (non-cash)",   _eur(metrics.get("afa_annual")),
         "_none_",           "§7 EStG 2%/yr"),
    ]

    card_w   = 42
    card_h   = 19
    gap      = 3
    per_row  = 4
    x0, y0  = 18.0, float(pdf.get_y())

    for i, (lbl, val, check, bench) in enumerate(cards):
        col = i % per_row
        row = i // per_row
        x = x0 + col * (card_w + gap)
        y = y0 + row * (card_h + gap)

        if check == "_none_":
            status, severity = "PASS", "INFO"
        else:
            status, severity = _st(check)

        pdf.metric_card(x, y, card_w, card_h, lbl, val, status, severity, bench)

    rows_used = (len(cards) + per_row - 1) // per_row
    pdf.set_y(y0 + rows_used * (card_h + gap) + 4)


def _rent_roll_section(pdf: _PDF, rr: pd.DataFrame, metrics: dict):
    pdf.section_title("3.  Rent Roll Schedule")

    active = rr[~rr["is_vacant"]].copy()
    vacant = rr[rr["is_vacant"]].copy()
    gross  = metrics.get("gross_passing_rent", 1) or 1

    # Column widths (total = 174mm)
    cw = [9, 45, 26, 15, 15, 26, 19, 11, 8]
    hd = ["Unit", "Tenant", "Sector",
          "Area m\xb2", "EUR/m\xb2/mo",
          "Annual Rent", "Expiry", "Rem.", "A"]

    # Table header
    pdf.set_fill_color(*_NAVY)
    pdf.set_text_color(*_WHITE)
    pdf.set_font("Helvetica", "B", 6.5)
    pdf.set_x(18)
    for w, h in zip(cw, hd):
        pdf.cell(w, 6, _safe(h), 0, 0, "C", fill=True)
    pdf.ln()
    pdf.set_text_color(*_BLACK)

    # Active tenants
    shade = False
    for _, row in active.iterrows():
        bg = _LGRAY if shade else _WHITE
        pdf.set_fill_color(*bg)

        pct   = row["annual_rent"] / gross if gross else 0
        exp   = row.get("lease_expiry")
        exp_s = pd.Timestamp(exp).strftime("%b %Y") if pd.notna(exp) else "--"
        rem   = float(row.get("unexpired_yrs", 0) or 0)
        rem_s = f"{rem:.1f}yr" if rem > 0 else "EXPIRED"
        anc_s = "(*)" if row.get("is_anchor") else ""

        vals = [
            str(row.get("unit_ref", ""))[:6],
            str(row.get("tenant", ""))[:30],
            str(row.get("sector", ""))[:20],
            f"{row.get('area_sqm', 0):,.0f}",
            f"{row.get('rent_per_sqm_mo', 0):.2f}",
            f"EUR {row.get('annual_rent', 0):,.0f} ({pct:.0%})",
            exp_s,
            rem_s,
            anc_s,
        ]
        pdf.set_font("Helvetica", "", 6.5)
        pdf.set_x(18)
        for w, v in zip(cw, vals):
            pdf.cell(w, 5.5, _safe(v), 0, 0, "C", fill=True)
        pdf.ln()
        shade = not shade

    # Vacant rows
    if len(vacant) > 0:
        for _, row in vacant.iterrows():
            pdf.set_fill_color(255, 238, 238)
            vals = [
                str(row.get("unit_ref", ""))[:6],
                "VACANT",
                "--",
                f"{row.get('area_sqm', 0):,.0f}",
                "--", "--", "--", "--", "",
            ]
            pdf.set_font("Helvetica", "I", 6.5)
            pdf.set_text_color(*_DGRAY)
            pdf.set_x(18)
            for w, v in zip(cw, vals):
                pdf.cell(w, 5.5, _safe(v), 0, 0, "C", fill=True)
            pdf.ln()
        pdf.set_text_color(*_BLACK)

    # Totals row
    pdf.set_fill_color(*_NAVY)
    pdf.set_text_color(*_WHITE)
    pdf.set_font("Helvetica", "B", 6.5)
    totals = [
        "",
        f"{metrics.get('n_active_leases', 0)} tenants",
        "TOTAL / WTD",
        f"{metrics.get('total_leased_area', 0):,.0f}",
        f"{metrics.get('wtd_rent_per_sqm_mo', 0):.2f}",
        f"EUR {metrics.get('gross_passing_rent', 0):,.0f}",
        f"WAULT {metrics.get('wault', 0):.1f}yr",
        "", "(*) = Anchor",
    ]
    pdf.set_x(18)
    for w, v in zip(cw, totals):
        pdf.cell(w, 6, _safe(v), 0, 0, "C", fill=True)
    pdf.ln()
    pdf.set_text_color(*_BLACK)

    # Vacancy footnote
    if metrics.get("total_vacant_area", 0) > 0:
        pdf.ln(1)
        pdf.set_font("Helvetica", "I", 6.5)
        pdf.set_text_color(*_DGRAY)
        pdf.set_x(18)
        pdf.cell(0, 5,
                 _safe(f"Vacant area: {metrics['total_vacant_area']:,.0f} m\xb2  |  "
                        f"Vacancy: {metrics.get('vacancy_rate', 0):.1%}  |  "
                        f"Total GLA: {metrics.get('gla_total', 0):,.0f} m\xb2"),
                 0, 1, "L")
        pdf.set_text_color(*_BLACK)
    pdf.ln(2)


def _cost_section(pdf: _PDF, metrics: dict, costs: dict):
    pdf.section_title("4.  NOI Bridge / Cost Breakdown")

    gross      = metrics.get("gross_passing_rent", 0)
    noi        = metrics.get("noi_proxy", 0)
    annual_ds  = metrics.get("annual_debt_service", 0)
    noi_source = metrics.get("noi_source", "fallback_80pct")

    source_label = {
        "cost_sheet":                "Itemised Cost Sheet",
        "slider_with_void_estimate": "Sidebar Estimate + Void Adjustment",
        "fallback_80pct":            "80% Proxy (Cost Sheet not populated)",
    }.get(noi_source, noi_source)

    pdf.set_font("Helvetica", "I", 7)
    pdf.set_text_color(*_DGRAY)
    pdf.set_x(18)
    pdf.cell(0, 5, f"NOI source: {_safe(source_label)}", 0, 1, "L")
    pdf.set_text_color(*_BLACK)
    pdf.ln(1)

    col_l, col_r = 118, 56

    def _row(label, amount, negative=False, bold=False, bg=None, txt_color=None):
        fill = bg is not None
        if fill:
            pdf.set_fill_color(*bg)
        else:
            pdf.set_fill_color(*_WHITE)
        pdf.set_font("Helvetica", "B" if bold else "", 8 if bold else 7.5)
        pdf.set_x(18)
        pdf.cell(col_l, 5.5, _safe(label), 0, 0, "L", fill=fill)
        if amount is None:
            pdf.cell(col_r, 5.5, "--", 0, 1, "R", fill=fill)
            return
        sign   = "-" if negative else " "
        color  = txt_color or (_RED if negative else _GREEN)
        pdf.set_text_color(*color if not bold else _BLACK)
        pdf.cell(col_r, 5.5, f"{sign}EUR {abs(float(amount)):>12,.0f}", 0, 1, "R", fill=fill)
        pdf.set_text_color(*_BLACK)

    # Gross rent
    _row("Gross Passing Rent", gross, bold=True, bg=_LGRAY, txt_color=_BLACK)
    pdf.ln(1)

    # Cost lines
    cost_labels = {
        "property_mgmt_fee":   "  Property Management Fee",
        "asset_mgmt_fee":      "  Asset Management Fee",
        "building_insurance":  "  Building Insurance",
        "capex_reserve":       "  CapEx Reserve",
        "ground_rent":         "  Ground Rent (Erbpacht)",
        "grundsteuer":         "  Grundsteuer (Property Tax)",
        "void_service_charge": "  Void Service Charge",
        "void_insurance":      "  Void Insurance",
        "letting_costs":       "  Letting / Leasing Commissions",
        "other_costs":         "  Other Non-Recoverables",
    }
    cost_breakdown = (costs or {}).get("costs", {})
    shade = False
    for k, lbl in cost_labels.items():
        v = cost_breakdown.get(k, 0)
        if v and v > 0:
            _row(lbl, v, negative=True, bg=_LGRAY if shade else None)
            shade = not shade

    # Fallback proxy if cost sheet unavailable
    if not cost_breakdown:
        proxy = gross * 0.20
        _row("  Operating Costs (20% proxy - itemise before IC)", proxy, negative=True,
             bg=_LAMBER)
        void_c = metrics.get("void_costs", 0)
        if void_c > 0:
            _row("  Void Holding Costs (estimate)", void_c, negative=True, bg=_LAMBER)

    pdf.ln(1)

    # NOI bar
    noi_pct = metrics.get("noi_margin", 0)
    pdf.set_fill_color(*_NAVY)
    pdf.set_text_color(*_WHITE)
    pdf.set_font("Helvetica", "B", 9)
    pdf.set_x(18)
    pdf.cell(col_l, 7, "  NET OPERATING INCOME (NOI)", 0, 0, "L", fill=True)
    pdf.cell(col_r, 7,
             _safe(f" EUR {noi:>12,.0f}  ({noi_pct:.1%} margin)"),
             0, 1, "R", fill=True)
    pdf.set_text_color(*_BLACK)

    # Debt service and levered CF
    if annual_ds:
        pdf.ln(2)
        _row("  Annual Debt Service (interest + amortisation)", annual_ds, negative=True,
             bg=_LGRAY)
        lcf = noi - annual_ds
        color = _GREEN if lcf >= 0 else _RED
        _row("  Levered Cash Flow (pre-tax)", lcf, bold=True,
             bg=_LGRAY, txt_color=color)

    # AfA footnote
    afa = metrics.get("afa_annual")
    if afa:
        pdf.ln(2)
        pdf.set_font("Helvetica", "I", 6.5)
        pdf.set_text_color(*_DGRAY)
        pdf.set_x(18)
        bld_val = metrics.get("afa_building_value", 0)
        pdf.multi_cell(
            174, 4.5,
            _safe(f"AfA (non-cash, §7 Abs.4 EStG): EUR {afa:,.0f}/yr  "
                  f"on building value EUR {bld_val:,.0f} (75% of price, 2% linear depreciation)"),
            0, "L",
        )
        pdf.set_text_color(*_BLACK)
    pdf.ln(3)


def _risk_section(pdf: _PDF, qa: list, metrics: dict):
    pdf.section_title("5.  Risk Assessment & Mitigants")

    red_items  = [r for r in qa if r.status == "FAIL" and r.severity == "RED"]
    yel_items  = [r for r in qa if r.status in ("FAIL", "WARN") and r.severity == "YELLOW"]
    pass_items = [r for r in qa if r.status == "PASS"]

    # QA summary line
    pdf.set_x(18)
    pdf.set_font("Helvetica", "", 8.5)
    pdf.cell(0, 6,
             f"QA Result:   {len(red_items)} critical (RED)   "
             f"{len(yel_items)} caution (YELLOW)   "
             f"{len(pass_items)} passed",
             0, 1, "L")
    pdf.ln(1)

    # ── Critical (RED) ────────────────────────────────────────────────────────
    if red_items:
        pdf.set_fill_color(*_RED)
        pdf.set_text_color(*_WHITE)
        pdf.set_font("Helvetica", "B", 7.5)
        pdf.set_x(18)
        pdf.cell(174, 6,
                 "  CRITICAL RISKS  --  MUST RESOLVE BEFORE IC APPROVAL",
                 0, 1, "L", fill=True)
        pdf.set_text_color(*_BLACK)
        pdf.ln(1)
        for item in red_items:
            pdf.risk_block(item, _LRED, _RED)

    # ── Caution (YELLOW) ──────────────────────────────────────────────────────
    if yel_items:
        pdf.set_fill_color(*_AMBER)
        pdf.set_text_color(*_WHITE)
        pdf.set_font("Helvetica", "B", 7.5)
        pdf.set_x(18)
        pdf.cell(174, 6,
                 "  CAUTION FLAGS  --  FURTHER DILIGENCE REQUIRED",
                 0, 1, "L", fill=True)
        pdf.set_text_color(*_BLACK)
        pdf.ln(1)
        for item in yel_items:
            pdf.risk_block(item, _LAMBER, _AMBER)

    # ── Passing checks ────────────────────────────────────────────────────────
    if pass_items:
        pdf.ln(2)
        pdf.set_fill_color(*_GREEN)
        pdf.set_text_color(*_WHITE)
        pdf.set_font("Helvetica", "B", 7.5)
        pdf.set_x(18)
        pdf.cell(174, 6,
                 "  METRICS PASSING IC BENCHMARKS",
                 0, 1, "L", fill=True)
        pdf.set_text_color(*_BLACK)
        pdf.ln(1)
        shade = False
        for item in pass_items:
            bg = _LGRAY if shade else _WHITE
            pdf.set_fill_color(*bg)
            pdf.set_x(18)
            pdf.set_font("Helvetica", "", 7.5)
            pdf.set_text_color(*_BLACK)
            pdf.cell(95, 5.5, _safe(f"  {item.check}"), 0, 0, "L", fill=True)
            pdf.set_font("Helvetica", "B", 7.5)
            pdf.set_text_color(*_GREEN)
            pdf.cell(35, 5.5, _safe(item.actual), 0, 0, "L", fill=True)
            pdf.set_text_color(*_DGRAY)
            pdf.set_font("Helvetica", "", 6.5)
            pdf.cell(44, 5.5, _safe(f"Benchmark: {item.benchmark}"), 0, 1, "R", fill=True)
            pdf.set_text_color(*_BLACK)
            shade = not shade


# =============================================================================
# PUBLIC API
# =============================================================================

def generate_bank_submission(
    filepath,
    output_dir: str = "deals",
    analyst_name: str = "",
    exit_cap_rate: float = 0.065,
) -> str:
    """
    Run the full rent_roll_parser pipeline on filepath, then render the PDF.

    Parameters
    ----------
    filepath      : path (str/Path) or file-like object (BytesIO from Streamlit)
    output_dir    : folder to save the PDF (created if absent)
    analyst_name  : optional analyst name for document metadata
    exit_cap_rate : assumed exit cap rate for the 10yr IRR estimate

    Returns
    -------
    str — absolute path to the generated PDF file
    """
    # ── Parse ──────────────────────────────────────────────────────────────
    if hasattr(filepath, "seek"):
        filepath.seek(0)

    deal    = rrp.load_deal_overview(filepath)
    rr      = rrp.load_rent_roll(filepath)
    _active = rr[~rr["is_vacant"]]
    _vacant = rr[rr["is_vacant"]]
    costs   = rrp.load_cost_sheet(
        filepath,
        rr_gross_rent  = float(_active["annual_rent"].sum()),
        rr_gla_total   = float(rr["area_sqm"].sum()),
        rr_vacant_area = float(_vacant["area_sqm"].sum()),
    )
    metrics = rrp.compute_metrics(deal, rr, costs)
    qa      = rrp.run_qa_audit(metrics, deal)
    verdict = rrp.generate_verdict(metrics, qa)

    # ── IRR estimate ───────────────────────────────────────────────────────
    irr = _compute_levered_irr(
        noi          = float(metrics.get("noi_proxy", 0)),
        loan         = float(metrics.get("loan_amount", 0)),
        equity       = float(metrics.get("equity", 0)),
        interest_rate= float(deal.get("interest_rate") or 0),
        amort_rate   = float(deal.get("amort_rate") or 0),
        hold_years   = 10,
        exit_cap_rate= exit_cap_rate,
        io_years     = 0,
    )

    # ── Build PDF ──────────────────────────────────────────────────────────
    date_str  = date.today().strftime("%d %B %Y")
    deal_name = deal.get("property_name") or "Deal"
    pdf = _PDF(deal_name, date_str)

    # Page 1 — Cover
    _cover_page(pdf, deal, metrics, verdict)

    # Page 2 — Transaction + Metrics
    pdf.add_page()
    _transaction_section(pdf, deal, metrics)
    _metrics_section(pdf, metrics, qa, irr)

    # Page 3 — Rent Roll + NOI Bridge
    pdf.add_page()
    _rent_roll_section(pdf, rr, metrics)
    _cost_section(pdf, metrics, costs)

    # Page 4 — Risk Assessment
    pdf.add_page()
    _risk_section(pdf, qa, metrics)

    # ── Save ───────────────────────────────────────────────────────────────
    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    safe_name = "".join(
        c if c.isalnum() or c in "-_" else "_"
        for c in str(deal_name)
    )[:50]
    date_tag  = date.today().strftime("%Y%m%d")
    out_path  = out_dir / f"BankSubmission_{date_tag}_{safe_name}.pdf"

    pdf.output(str(out_path))
    return str(out_path)


# =============================================================================
# CLI ENTRY POINT
# =============================================================================

def main():
    args = sys.argv[1:]
    if not args or args[0] in ("-h", "--help"):
        print(
            "\nUsage: python bank_submission.py <deal_excel_file> [--exit-cap RATE]\n"
            "\n  <deal_excel_file>   Excel file produced by the Retail Underwriter template\n"
            "  --exit-cap RATE     Assumed exit cap rate for IRR (default 0.065)\n"
            "\nExamples:\n"
            "  python bank_submission.py Scenario_1_PASS_Regensburg.xlsx\n"
            "  python bank_submission.py Scenario_3_DECLINE_Duisburg.xlsx --exit-cap 0.075\n"
        )
        sys.exit(0)

    excel_path = args[0]
    if not Path(excel_path).exists():
        print(f"Error: file not found: {excel_path}")
        sys.exit(1)

    exit_cap = 0.065
    for i, arg in enumerate(args):
        if arg == "--exit-cap" and i + 1 < len(args):
            try:
                exit_cap = float(args[i + 1])
            except ValueError:
                print(f"Warning: invalid exit-cap '{args[i+1]}', using 0.065")

    print(f"\n  Loading deal:   {excel_path}")
    print(f"  Exit cap rate:  {exit_cap:.1%}")

    out = generate_bank_submission(excel_path, exit_cap_rate=exit_cap)

    print(f"  PDF saved:      {out}")
    print()


if __name__ == "__main__":
    main()
