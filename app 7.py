"""
app.py  --  German Retail Underwriter  |  IC Grade  v1.2
=========================================================
Bug fixes vs v1.1
  FIX 1: Annuity vs linear amortisation -- both loan types now offered
  FIX 2: Void costs explicitly modelled in NOI (not absorbed silently in margin)
  FIX 3: IRR heatmap capped at 25%; cells above cap flagged as unrealistic
  FIX 4: NIY in export uses the SAME noi_margin as screen (no silent inconsistency)

New:
  -- Cost Sheet itemised breakdown shown in Deal Pack
  -- NOI source badge (Cost Sheet / slider / fallback) shown on all tabs
  -- Loan type selector: Annuitaetendarlehen vs Lineare Tilgung
"""

import io
import pathlib
import traceback

import numpy as np
import numpy_financial as npf
import pandas as pd
import streamlit as st

import importlib.util as _ilu
import os as _os

from constants import (
    LTV_PASS, LTV_WARN, LTV_REPAIR_TARGET,
    DSCR_PASS, ICR_PASS, GIY_PASS, DY_PASS,
    VOID_HOLD_RATE,
)
from decision_dashboard import render_decision_dashboard

# dd_scanner 7.py has a space in its filename so it cannot be imported directly.
# Use importlib to load it by file path.
_dd_spec = _ilu.spec_from_file_location(
    "dd_scanner",
    _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "dd_scanner 7.py"),
)
_dd_mod = _ilu.module_from_spec(_dd_spec)
_dd_spec.loader.exec_module(_dd_mod)
extract_lease_terms = _dd_mod.extract_lease_terms
run_dd_scan         = _dd_mod.run_dd_scan
# rent_roll_parser 7.py has a space — load via importlib (same as dd_scanner).
_rrp_spec = _ilu.spec_from_file_location(
    "rent_roll_parser",
    _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "rent_roll_parser 7.py"),
)
rrp = _ilu.module_from_spec(_rrp_spec)
_rrp_spec.loader.exec_module(rrp)
del _rrp_spec
del _ilu, _os, _dd_spec, _dd_mod

st.set_page_config(
    page_title="DE Retail Underwriter | IC Grade",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.markdown("""
<style>
  html, body, [class*="css"] { font-family: 'Segoe UI', sans-serif; }
  [data-testid="stMetric"] {
    background:#f8f9fb; border-radius:8px; padding:12px;
    border:1px solid #e5e7eb;
  }
  [data-testid="stMetricLabel"] {
    font-size:0.72rem; color:#6b7280; text-transform:uppercase; letter-spacing:.06em;
  }
  [data-testid="stMetricValue"] { font-size:1.35rem; font-weight:700; color:#1e3a5f; }
  button[data-baseweb="tab"] { font-weight:600; font-size:0.85rem; }
  .noi-badge-cs   { background:#d1fae5; color:#065f46; padding:3px 10px;
                    border-radius:12px; font-size:0.78rem; font-weight:600; }
  .noi-badge-sl   { background:#fef3c7; color:#92400e; padding:3px 10px;
                    border-radius:12px; font-size:0.78rem; font-weight:600; }
  .noi-badge-fb   { background:#fee2e2; color:#991b1b; padding:3px 10px;
                    border-radius:12px; font-size:0.78rem; font-weight:600; }
  .verdict-pass   { background:#d1fae5; border-left:4px solid #059669;
                    padding:14px 20px; border-radius:6px; }
  .verdict-inv    { background:#fef3c7; border-left:4px solid #d97706;
                    padding:14px 20px; border-radius:6px; }
  .verdict-dec    { background:#fee2e2; border-left:4px solid #dc2626;
                    padding:14px 20px; border-radius:6px; }
  .cost-line      { display:flex; justify-content:space-between;
                    padding:4px 0; font-size:0.88rem; border-bottom:1px solid #f3f4f6; }
  .cost-label     { color:#374151; }
  .cost-value     { color:#dc2626; font-weight:600; }
  .noi-total      { display:flex; justify-content:space-between;
                    padding:8px 0; font-size:0.95rem; font-weight:700;
                    border-top:2px solid #1e3a5f; margin-top:4px; color:#1e3a5f; }
</style>
""", unsafe_allow_html=True)


IRR_CAP = 0.25   # FIX 3: heatmap ceiling -- anything above 25% is flagged


# =============================================================================
# FINANCIAL ENGINE  v1.2
# =============================================================================

def _annuity_payment(loan: float, rate: float, n_years: int) -> float:
    """Fixed annual payment for a standard Annuitaetendarlehen."""
    if rate == 0 or n_years == 0:
        return loan / n_years if n_years else 0.0
    return loan * rate * (1 + rate) ** n_years / ((1 + rate) ** n_years - 1)


def calculate_10y_projection(
    metrics: dict,
    deal: dict,
    io_years: int,
    exit_cap: float,
    indexation: float,
    noi: float,
    loan_type: str = "Linear",
    hold_years: int = 10,        # NEW: variable hold period for multi-IRR
) -> tuple:
    """
    10-year levered cash flow model.

    FIX 1: Annuity loan produces a FIXED annual payment (interest + principal),
           Linear loan produces a declining payment as principal reduces.
    FIX 2: void costs already baked into the noi parameter passed in.
    FIX 3: IRR capped at IRR_CAP; NaN guarded.
    FIX 4: caller passes the same noi that is displayed on screen.
    """
    loan         = float(metrics.get("loan_amount", 0) or 0)
    acq_cost     = float(metrics.get("total_acq_cost", 0) or 0)
    true_equity  = acq_cost - loan
    interest_rate = float(deal.get("interest_rate", 0) or 0)
    amort_rate    = float(deal.get("amort_rate", 0) or 0)
    loan_term     = int(deal.get("loan_term_yrs", 10) or 10)

    if true_equity <= 0 or noi <= 0:
        empty = pd.DataFrame(columns=[
            "Year", "NOI (EUR)", "Interest (EUR)", "Principal (EUR)",
            "Debt Service (EUR)", "Net CF (EUR)", "Cash-on-Cash (%)", "Loan Balance (EUR)"
        ])
        return empty, float("nan")

    # Pre-compute annuity payment (used only in Annuity mode, post-IO)
    annuity_pmt = _annuity_payment(loan, interest_rate, loan_term)

    cash_flows   = [-true_equity]
    loan_balance = loan
    current_noi  = noi
    rows         = []

    for year in range(1, hold_years + 1):
        if year > 1:
            current_noi *= (1 + indexation)

        interest_pmt = loan_balance * interest_rate

        if year <= io_years:
            # Interest-only period
            principal_pmt = 0.0
            debt_svc      = interest_pmt
        else:
            if loan_type == "Annuity":
                # FIX 1: Fixed total payment, interest shrinks, principal grows
                debt_svc      = min(annuity_pmt, loan_balance + interest_pmt)
                principal_pmt = max(debt_svc - interest_pmt, 0.0)
            else:
                # Linear: constant principal, declining total payment
                principal_pmt = loan_balance * amort_rate   # FIX 2 carried from v1.1
                debt_svc      = interest_pmt + principal_pmt

        loan_balance = max(loan_balance - principal_pmt, 0.0)
        net_cf = current_noi - debt_svc
        coc    = net_cf / true_equity

        # FIX: exit value -- no spurious forward year (carried from v1.1)
        if year == hold_years:
            exit_value   = current_noi / exit_cap
            net_proceeds = exit_value - loan_balance
            cash_flows.append(net_cf + net_proceeds)
        else:
            cash_flows.append(net_cf)

        # AfA reduces taxable income but is not a cash outflow
        # After-tax benefit: AfA × marginal tax rate (typically 30-45% for German RE)
        afa_annual = float(metrics.get("afa_annual") or 0)
        rows.append({
            "Year":              year,
            "NOI (EUR)":         round(current_noi, 0),
            "Interest (EUR)":    round(interest_pmt, 0),
            "Principal (EUR)":   round(principal_pmt, 0),
            "Debt Service (EUR)":round(debt_svc, 0),
            "Net CF (EUR)":      round(net_cf, 0),
            "Cash-on-Cash (%)":  coc,
            "AfA Shield (EUR)":  round(afa_annual, 0),
            "Loan Balance (EUR)":round(loan_balance, 0),
        })

    # FIX 3: IRR guard
    try:
        irr = npf.irr(cash_flows)
        if np.isnan(irr) or np.isinf(irr):
            irr = float("nan")
        elif irr > IRR_CAP:
            irr = IRR_CAP          # cap -- heatmap will flag this cell
    except Exception:
        irr = float("nan")

    return pd.DataFrame(rows), irr


def build_irr_heatmap(metrics, deal, io_years, base_exit_cap,
                      base_indexation, noi, loan_type):
    cap_deltas = [-0.010, -0.005, 0.000, +0.005, +0.010]
    idx_deltas = [+0.010, +0.005, 0.000, -0.005, -0.010]

    cap_range = [base_exit_cap + d for d in cap_deltas]
    idx_range = [base_indexation + d for d in idx_deltas]

    matrix, capped_mask = [], []
    for idx in idx_range:
        row, mask_row = [], []
        for cap in cap_range:
            if cap <= 0:
                row.append(float("nan"))
                mask_row.append(False)
                continue
            _, irr = calculate_10y_projection(
                metrics, deal, io_years, cap, idx, noi, loan_type
            )
            # detect if IRR was capped
            raw_irr = irr
            mask_row.append(not np.isnan(irr) and irr >= IRR_CAP)
            row.append(irr)
        matrix.append(row)
        capped_mask.append(mask_row)

    df = pd.DataFrame(
        matrix,
        index  =[f"{r:.1%} growth" for r in idx_range],
        columns=[f"{c:.1%} exit cap" for c in cap_range],
    )
    return df, capped_mask, 2, 2   # base case at centre (row 2, col 2)



# =============================================================================
# MULTI-PERIOD IRR  (3yr / 5yr / 7yr / 10yr hold)
# =============================================================================

def calculate_multi_period_irr(metrics, deal, io_years, exit_cap,
                                indexation, noi, loan_type):
    """
    Calculate IRR for 3, 5, 7 and 10-year hold periods.
    Returns a dict {hold_years: irr_float_or_nan}.
    Reveals whether the deal is a short, medium or long-hold play.
    """
    results = {}
    for yrs in [3, 5, 7, 10]:
        _, irr = calculate_10y_projection(
            metrics, deal, io_years, exit_cap, indexation,
            noi, loan_type, hold_years=yrs
        )
        results[yrs] = irr
    return results


# =============================================================================
# REFINANCING STRESS TEST
# =============================================================================

def calculate_refi_stress(metrics, deal, io_years, noi, loan_type,
                           refi_year=5, cpi_rate=0.02):
    """
    At the refinancing year, stress-test the LTV under cap rate expansion.
    Returns a list of dicts with cap_rate, asset_value, loan_balance, ltv, status.
    A real German bank will ask: if cap rates move 50–150bps at refinancing,
    does the LTV stay below 65%? If not, the borrower must inject equity.
    """
    loan          = float(metrics.get("loan_amount", 0) or 0)
    interest_rate = float(deal.get("interest_rate", 0) or 0)
    amort_rate    = float(deal.get("amort_rate", 0) or 0)
    loan_term     = int(deal.get("loan_term_yrs", 10) or 10)
    indexation    = cpi_rate   # use sidebar CPI value for consistency with CF model

    if loan <= 0 or noi <= 0:
        return [], 0.0, 0.0   # safe 3-tuple for caller unpacking

    annuity_pmt = _annuity_payment(loan, interest_rate, loan_term)
    loan_balance = loan
    current_noi  = noi

    for year in range(1, refi_year + 1):
        if year > 1:
            current_noi *= (1 + indexation)
        interest_pmt = loan_balance * interest_rate
        if year <= io_years:
            principal_pmt = 0.0
        else:
            if loan_type == "Annuity":
                debt_svc      = min(annuity_pmt, loan_balance + interest_pmt)
                principal_pmt = max(debt_svc - interest_pmt, 0.0)
            else:
                principal_pmt = loan_balance * amort_rate
        loan_balance = max(loan_balance - principal_pmt, 0.0)

    rows = []
    base_cap = float(metrics.get("giy", 0.065) or 0.065)
    # FIX 3: Use explicit named shifts so labels are unambiguous
    shift_labels = [
        (0.000, "Base (0bps)"),
        (0.005, "+50bps"),
        (0.010, "+100bps"),
        (0.015, "+150bps"),
        (0.020, "+200bps"),
    ]
    for shift, label in shift_labels:
        cap       = base_cap + shift
        asset_val = current_noi / cap if cap > 0 else 0
        ltv_refi  = loan_balance / asset_val if asset_val > 0 else 1.0
        if ltv_refi <= LTV_PASS:
            status = "PASS"
        elif ltv_refi <= LTV_WARN:
            status = "WARN"
        else:
            status = "FAIL"
        rows.append({
            "Cap Rate Shift":    label,
            "Exit Cap Rate":     f"{cap:.1%}",
            "Asset Value (EUR)": f"{asset_val:,.0f}",
            "Loan Balance (EUR)":f"{loan_balance:,.0f}",
            "LTV at Refi":       f"{ltv_refi:.1%}",
            "Status":            status,
            "_ltv_raw":          ltv_refi,
        })
    return rows, loan_balance, current_noi




# =============================================================================
# DEAL REPAIR ENGINE
# For non-PASS deals: calculates exact changes needed to achieve PASS.
# Outputs: price reduction, equity injection, IO extension, share deal, IRR price.
# =============================================================================

def deal_repair_engine(metrics, deal, qa, verdict):
    """
    Calculate minimum adjustments to turn a DECLINE or INVESTIGATE into a PASS.
    Returns list of repair suggestions, sorted by priority.
    Each fix is actionable: tells you the exact number and who to negotiate with.
    """
    if verdict.get('recommendation') == 'PASS':
        return []

    fixes = []
    asking     = metrics.get('asking_price', 0) or 0
    noi        = metrics.get('noi_proxy', 0) or 0
    loan       = metrics.get('loan_amount', 0) or 0
    equity     = metrics.get('equity', 0) or 0
    gross_rent = metrics.get('gross_passing_rent', 0) or 0
    interest   = float(deal.get('interest_rate') or 0.041)
    amort      = float(deal.get('amort_rate') or 0.020)
    loan_term  = int(deal.get('loan_term_yrs') or 10)
    grest      = float(deal.get('grest_rate') or 0.055)
    notar      = float(deal.get('notar_rate') or 0.015)
    broker     = float(deal.get('broker_rate') or 0.010)
    orig_ltv   = loan / asking if asking > 0 else 0.61
    annual_ds  = loan * (interest + amort) if loan > 0 else 1
    annual_int = loan * interest if loan > 0 else 1

    # ── FIX 1: Price Reduction ───────────────────────────────────────────────
    if asking > 0 and noi > 0 and orig_ltv > 0:
        max_p_dscr = noi / (DSCR_PASS * orig_ltv * (interest + amort))
        max_p_icr  = noi / (ICR_PASS  * orig_ltv * interest)
        max_p_giy  = gross_rent / GIY_PASS if gross_rent > 0 else asking
        max_p_dy   = noi / (DY_PASS   * orig_ltv)
        max_price  = min(max_p_dscr, max_p_icr, max_p_giy, max_p_dy)

        if max_price < asking * 0.999:
            reduction     = asking - max_price
            reduction_pct = reduction / asking
            binding = min(
                [('DSCR', max_p_dscr), ('ICR', max_p_icr),
                 ('GIY', max_p_giy),   ('Debt Yield', max_p_dy)],
                key=lambda x: x[1]
            )[0]
            new_loan = max_price * orig_ltv
            new_ds   = new_loan * (interest + amort)
            fixes.append({
                'lever': 'PRICE REDUCTION', 'category': 'seller', 'icon': '💰', 'priority': 1,
                'current':  f'EUR {asking:,.0f}',
                'required': f'EUR {max_price:,.0f}',
                'delta':    f'-EUR {reduction:,.0f}  ({reduction_pct:.1%} off ask)',
                'binding':  binding,
                'action': (
                    f'Tell the seller: "At EUR {asking:,.0f}, this asset does not clear '
                    f'institutional debt covenants (binding constraint: {binding}). '
                    f'Maximum price for a PASS verdict is EUR {max_price:,.0f} — '
                    f'a {reduction_pct:.1%} reduction. This is not negotiation; '
                    f'it is the math of what a German bank will lend against."'
                ),
                'after': {
                    'DSCR':       f'{noi/new_ds:.2f}x',
                    'GIY':        f'{gross_rent/max_price:.2%}',
                    'Debt Yield': f'{noi/new_loan:.2%}',
                },
            })

    # ── FIX 2: Additional Equity ─────────────────────────────────────────────
    ltv = metrics.get('ltv', 0) or 0
    if ltv > LTV_PASS and asking > 0:
        req_loan    = asking * LTV_REPAIR_TARGET
        extra_eq    = loan - req_loan
        new_eq      = equity + extra_eq
        new_ds_eq   = req_loan * (interest + amort)
        fixes.append({
            'lever': 'ADDITIONAL EQUITY', 'category': 'equity', 'icon': '📥', 'priority': 2,
            'current':  f'EUR {equity:,.0f}  (LTV {ltv:.1%})',
            'required': f'EUR {new_eq:,.0f}  (LTV {LTV_REPAIR_TARGET:.1%})',
            'delta':    f'+EUR {extra_eq:,.0f} equity injection',
            'binding':  'LTV',
            'action': (
                f'Inject EUR {extra_eq:,.0f} additional equity to reduce LTV from '
                f'{ltv:.1%} to {LTV_REPAIR_TARGET:.1%}. Alternatively, negotiate a lower loan amount '
                f'with the bank. This also improves DSCR to {noi/new_ds_eq:.2f}x.'
            ),
            'after': {
                'LTV':        f'{LTV_REPAIR_TARGET:.1%}',
                'New Loan':   f'EUR {req_loan:,.0f}',
                'DSCR':       f'{noi/new_ds_eq:.2f}x',
                'Debt Yield': f'{noi/req_loan:.2%}',
            },
        })

    # ── FIX 4: Share Deal ────────────────────────────────────────────────────
    grest_saving = asking * grest if asking > 0 else 0
    if grest_saving > 30_000:
        share_acq = asking * (1 + notar + broker)
        asset_acq = asking * (1 + grest + notar + broker)
        fixes.append({
            'lever': 'SHARE DEAL STRUCTURE', 'category': 'structuring', 'icon': '📋', 'priority': 4,
            'current':  f'Asset deal total cost: EUR {asset_acq:,.0f}',
            'required': f'Share deal total cost: EUR {share_acq:,.0f}',
            'delta':    f'Saves EUR {grest_saving:,.0f} in GrESt',
            'binding':  'Acquisition Cost',
            'action': (
                f'Structure as Anteilskauf (share purchase <90% threshold) to eliminate '
                f'Grunderwerbsteuer of EUR {grest_saving:,.0f}. '
                f'Reduces true equity required by EUR {grest_saving:,.0f}. '
                f'Requires corporate DD on the holding company. '
                f'Engage a German M&A tax advisor (KPMG, Deloitte, or local Steuerberater).'
            ),
            'after': {
                'GrESt':        'EUR 0 (eliminated)',
                'True Equity':  f'EUR {share_acq - loan:,.0f}',
                'Saving':       f'EUR {grest_saving:,.0f}',
            },
        })

    # ── FIX 5: Target IRR Pricing (7% target) ───────────────────────────────
    if noi > 0 and asking > 0 and orig_ltv > 0:
        def _irr_at_price(price):
            if price <= 0: return -1
            ln  = price * orig_ltv
            eq  = price * (1 + grest + notar + broker) - ln
            if eq <= 0: return -1
            pmt = _annuity_payment(ln, interest, loan_term)
            cfs = [-eq]; lb = ln; cn = noi
            for yr in range(1, 11):
                if yr > 1: cn *= 1.02
                ip = lb * interest
                if yr <= 2: pp, ds = 0, ip
                else: ds = min(pmt, lb+ip); pp = max(ds-ip, 0)
                lb = max(lb-pp, 0)
                net = cn - ds
                if yr == 10: cfs.append(net + cn/0.065 - lb)
                else: cfs.append(net)
            try:
                irr = npf.irr(cfs)
                return irr if not np.isnan(irr) else -1
            except: return -1

        irr_at_ask = _irr_at_price(asking)
        if irr_at_ask < 0.07:
            lo, hi = asking * 0.25, asking
            for _ in range(40):
                mid = (lo + hi) / 2
                if _irr_at_price(mid) < 0.07: hi = mid
                else: lo = mid
            target_p = lo
            if target_p < asking * 0.99:
                disc = (asking - target_p) / asking
                fixes.append({
                    'lever': 'TARGET IRR PRICING', 'category': 'return', 'icon': '📈', 'priority': 5,
                    'current':  f'10yr levered IRR: {irr_at_ask:.2%}  at EUR {asking:,.0f}',
                    'required': f'10yr levered IRR: 7.00%  (institutional target)',
                    'delta':    f'Maximum price: EUR {target_p:,.0f}  ({disc:.1%} below ask)',
                    'binding':  'Levered IRR',
                    'action': (
                        f'Investor return logic: at a 6.5% exit cap and 2% annual '
                        f'rent indexation, the maximum price that delivers 7% levered IRR '
                        f'is EUR {target_p:,.0f}. The seller is asking EUR {asking:,.0f} — '
                        f'that is EUR {asking-target_p:,.0f} too expensive for the return '
                        f'profile. Use this number as your maximum bid.'
                    ),
                    'after': {
                        'Max Price': f'EUR {target_p:,.0f}',
                        '10yr IRR':  '7.00%',
                        'Discount':  f'{disc:.1%} off ask',
                    },
                })

    fixes.sort(key=lambda x: x['priority'])
    return fixes


# =============================================================================
# VALUE CREATION MODULE
# Solar PV | EV Charging | ERV Reversion
# Sources: Fraunhofer ISE (solar), BDEW (EV), CBRE DE (ERV benchmarks) 2024
# =============================================================================

# Regional solar electricity output — kWh per m² of panel per year
# Fraunhofer ISE Photovoltaik report 2024 (NOT irradiation — actual panel output)
SOLAR_YIELD_BY_REGION = {
    'Bayern':               175,
    'Baden-Württemberg':    172,
    'Hessen':               162,
    'Rheinland-Pfalz':      162,
    'Niedersachsen':        155,
    'Hamburg':              150,
    'Berlin':               158,
    'Sachsen':              162,
    'Thüringen':            160,
    'Nordrhein-Westfalen':  152,
    'Sachsen-Anhalt':       160,
    'Brandenburg':          160,
    'Default':              160,
}

def _vc_solar(gla_sqm: float, region: str = 'Default',
              self_consump_rate: float = 0.60,
              self_consump_price: float = 0.30,
              feedin_price: float = 0.082,
              capex_per_m2: float = 130.0,
              roof_ratio: float = 0.75) -> dict:
    """
    Calculate solar PV value creation.
    Returns annual net income, CapEx, payback, and asset value uplift.
    """
    roof_area    = gla_sqm * roof_ratio
    kwh_per_m2   = SOLAR_YIELD_BY_REGION.get(region, SOLAR_YIELD_BY_REGION['Default'])
    total_kwh    = roof_area * kwh_per_m2 * 0.80   # 0.80 = system efficiency factor
    self_kwh     = total_kwh * self_consump_rate
    feedin_kwh   = total_kwh * (1 - self_consump_rate)
    gross_income = self_kwh * self_consump_price + feedin_kwh * feedin_price
    capex        = roof_area * capex_per_m2
    annual_opex  = capex * 0.015                    # 1.5% maintenance + insurance
    net_income   = gross_income - annual_opex
    payback      = capex / gross_income if gross_income > 0 else 99.0
    return {
        'roof_area_m2':   round(roof_area),
        'annual_kwh':     round(total_kwh),
        'gross_income':   round(gross_income),
        'opex':           round(annual_opex),
        'net_income':     round(net_income),
        'capex':          round(capex),
        'payback_yrs':    round(payback, 1),
        'enabled':        True,
    }


def _vc_ev(parking_spaces: int,
           ground_rent_per_charger: float = 2200.0,
           capex_per_charger: float = 3000.0) -> dict:
    """
    Calculate EV charging ground rent income.
    Operator (EnBW / ARAL / Ionity) funds hardware; landlord provides space + grid.
    Returns zero when no parking spaces provided.
    """
    if parking_spaces <= 0:
        return dict(chargers=0, annual_income=0, capex=0,
                    payback_yrs=99.0, enabled=True)
    chargers     = max(2, int(parking_spaces * 1.2 / 10))
    annual_income = chargers * ground_rent_per_charger
    capex         = chargers * capex_per_charger
    payback       = capex / annual_income if annual_income > 0 else 99.0
    return {
        'chargers':       chargers,
        'annual_income':  round(annual_income),
        'capex':          round(capex),
        'payback_yrs':    round(payback, 1),
        'enabled':        True,
    }


def _vc_erv(gross_rent: float, erv_uplift_pct: float = 0.10) -> dict:
    """
    Estimate ERV reversion value.
    Positive uplift = under-rented (upside at expiry).
    Negative uplift = over-rented (risk at expiry — flag this).
    """
    annual_uplift = gross_rent * erv_uplift_pct
    return {
        'current_rent':   round(gross_rent),
        'erv_estimate':   round(gross_rent * (1 + erv_uplift_pct)),
        'annual_uplift':  round(annual_uplift),
        'uplift_pct':     erv_uplift_pct,
        'direction':      'UPSIDE' if erv_uplift_pct >= 0 else 'OVER-RENTED RISK',
        'enabled':        True,
    }


def _vc_adjusted_metrics(base_metrics: dict, solar: dict, ev: dict, erv: dict,
                          exit_cap_rate: float) -> dict:
    """
    Compute all adjusted metrics after value creation.
    Only includes enabled items. ERV reversion is future income — shown
    separately, NOT added to current DSCR (it arrives at lease expiry).
    """
    add_noi  = 0
    add_noi += solar['net_income'] if solar.get('enabled') else 0
    add_noi += ev['annual_income'] if ev.get('enabled')    else 0
    # ERV reversion is FUTURE income — not in current NOI or DSCR
    # Shown as upside/risk flag only

    total_capex  = 0
    total_capex += solar['capex'] if solar.get('enabled') else 0
    total_capex += ev['capex']    if ev.get('enabled')    else 0

    base_noi     = base_metrics.get('noi_proxy', 0) or 0
    adjusted_noi = base_noi + add_noi
    loan         = base_metrics.get('loan_amount', 0) or 0
    asking       = base_metrics.get('asking_price', 0) or 0

    # Adjusted DSCR and ICR
    interest_rate = 0.041  # use same assumption as main model
    amort_rate    = 0.020
    adj_annual_ds = loan * (interest_rate + amort_rate) if loan > 0 else 1
    adj_dscr      = adjusted_noi / adj_annual_ds if adj_annual_ds > 0 else 0
    adj_icr       = adjusted_noi / (loan * interest_rate) if loan > 0 else 0
    adj_niy       = adjusted_noi / asking if asking > 0 else 0
    adj_val       = adjusted_noi / exit_cap_rate if exit_cap_rate > 0 else 0
    val_uplift    = adj_val - asking

    capex_irr = add_noi / total_capex if total_capex > 0 else 0

    return {
        'base_noi':      round(base_noi),
        'additional_noi':round(add_noi),
        'adjusted_noi':  round(adjusted_noi),
        'total_capex':   round(total_capex),
        'adj_dscr':      round(adj_dscr, 2),
        'adj_icr':       round(adj_icr, 2),
        'adj_niy':       round(adj_niy, 4),
        'asset_uplift':  round(val_uplift),
        'capex_irr':     round(capex_irr, 4),
        'erv_upside':    erv.get('annual_uplift', 0) if erv.get('enabled') else 0,
        'erv_direction': erv.get('direction', ''),
    }


# =============================================================================
# GLOSSARY
# =============================================================================

GLOSSARY = [
    ("WAULT",              "Weighted Average Unexpired Lease Term",
     "Income-weighted average remaining duration across all active leases. "
     "The single most scrutinised metric by German institutional buyers. "
     "Lenders typically require WAULT > 4 years for standard senior debt.",
     "{wault:.1f} years"),
    ("DSCR",               "Debt Service Coverage Ratio",
     "NOI divided by total annual debt service (interest + amortisation). "
     "Standard German CRE bank covenant minimum: 1.30x. "
     "Below 1.20x will typically result in a credit decline.",
     "{dscr}"),
    ("ICR",                "Interest Coverage Ratio",
     "NOI divided by the annual interest charge (excluding amortisation). "
     "Tracked separately from DSCR by most German lenders (pbb, Berlin Hyp, Helaba). "
     "Minimum covenant: typically 1.75x.",
     "{icr}"),
    ("LTV",                "Loan-to-Value",
     "Senior loan amount divided by purchase price. "
     "German CRE senior lenders cap at 60-65% for retail. "
     "Above 65% generally requires mezzanine financing.",
     "{ltv:.1%}"),
    ("GIY",                "Gross Initial Yield",
     "Gross passing rent divided by purchase price. "
     "2026 German retail benchmarks: 5.5-9%+ (prime lower, secondary upper end).",
     "{giy}"),
    ("NIY",                "Net Initial Yield",
     "NOI (after all non-recoverables) divided by purchase price. "
     "This is what the investor actually receives after costs.",
     "{niy}"),
    ("NOI",                "Net Operating Income",
     "Gross passing rent minus all non-recoverable operating costs. "
     "Your NOI source: {noi_source}. Margin: {noi_margin:.1%}.",
     "EUR {noi:,.0f} / yr"),
    ("Void Costs",         "Leerstandskosten",
     "Costs the landlord bears on vacant units: service charge, insurance, Grundsteuer. "
     "Often omitted in amateur models -- your void costs: EUR {void_costs:,.0f} / yr. "
     "At EUR 20/m2/yr minimum, a 10% vacancy on a 10,000m2 centre = EUR 20,000 / yr invisible cost.",
     "EUR {void_costs:,.0f} / yr"),
    ("GrESt",              "Grunderwerbsteuer",
     "German real estate transfer tax on the purchase price. "
     "Ranges from 3.5% (Bayern) to 6.5% (NRW, Brandenburg). "
     "A share deal structure can reduce or eliminate this cost.",
     "{grest:.1%} assumed"),
    ("AfA",                "Absetzung fur Abnutzung",
     "German tax depreciation (§7 Abs.4 EStG). Rate depends on build year: "
     "pre-1925 → 2.5% (40-yr life, Nr.2a); 1925-2000 → 2.0% (50-yr life, Nr.2b); "
     "post-2000 → 3.0% (33-yr life, Nr.1). Applied to building value only "
     "(75% of purchase price; land is excluded). Not a cash cost — reduces taxable income.",
     "Rate set by year_built"),
    ("Annuitaetendarlehen","Annuity Loan",
     "Fixed combined payment (interest + principal) throughout the loan term. "
     "As balance reduces, interest shrinks and principal grows -- total stays constant. "
     "Most common German CRE loan structure.",
     "Selected: {loan_type}"),
    ("Lineare Tilgung",    "Linear Amortisation",
     "Fixed principal repayment each period. Total debt service DECLINES over time "
     "as interest reduces. More conservative than annuity in early years, "
     "but produces higher NOI cover in later years.",
     "Selected: {loan_type}"),
    ("WALT",               "Weighted Average Lease Term",
     "Total weighted lease length from original start dates. "
     "Distinct from WAULT which measures only remaining (unexpired) term.",
     "--"),
    ("ERV",                "Estimated Rental Value",
     "Open-market rent a vacant unit could achieve today. "
     "If ERV < passing rent: asset is over-rented (risk at expiry). "
     "If ERV > passing rent: reversion potential (positive for investor).",
     "Enter in Excel"),
    ("Anchor Tenant",      "Ankermieter",
     "The dominant tenant driving footfall. Typical anchors: food (Rewe, Edeka, Aldi), "
     "DIY (OBI, Bauhaus), electronics (MediaMarkt, Saturn). "
     "Concentration above 55% of income creates single-event risk.",
     "{anchor_pct:.1%} of income"),
    ("Mezzanine",          "Mezzanine-Finanzierung",
     "Subordinated debt between senior debt and equity. "
     "Used to bridge when LTV exceeds bank appetite (65%). "
     "Typically priced at 8-12% and structurally junior to senior lender.",
     "--"),
    ("Share Deal",         "Anteilskauf",
     "Acquisition of shares in the property-owning company rather than the asset. "
     "Can reduce or eliminate GrESt if structured below the 90% threshold. "
     "Requires full corporate due diligence.",
     "--"),
    ("Void Period",        "Leerstandszeitraum",
     "Time between a tenant vacating and new tenant paying rent. "
     "German retail typical: 6-12 months (Fachmarkt), 12-24 months (struggling centre).",
     "--"),
    ("CapEx Reserve",      "Instandhaltungsrucklage",
     "Annual provision for future capital expenditure (roof, HVAC, facade, lifts). "
     "IREBS benchmark: EUR 4-8 per m2 GLA per year for retail. "
     "Under-reserving flatters NOI and surprises the buyer in year 5.",
     "--"),
    ("Bruttomietvertrag",  "Gross Lease",
     "The landlord pays all operating costs (insurance, maintenance, Grundsteuer, service charge). "
     "The tenant pays only the headline rent. Common in German retail for smaller units. "
     "Results in a lower NIY but simpler tenant relationship. "
     "Net Recovery Rate = 0%: all costs are landlord-borne.",
     "--"),
    ("Nettomietvertrag",   "Net Lease",
     "The tenant pays a net base rent plus a share of specified operating costs. "
     "More common for anchor tenants (Rewe, Edeka) and Fachmarktzentren. "
     "The exact cost items passed through are negotiated in the lease — "
     "CPI clause, service charge, insurance are typical pass-throughs. "
     "Net Recovery Rate typically 40-70% for well-structured German retail leases.",
     "--"),
    ("Vollständige Kostenumlage", "Triple-Net Lease (NNN)",
     "All operating costs — maintenance, insurance, management, Grundsteuer, CapEx — "
     "are borne by the tenant. Standard in US real estate; rare but not unknown in Germany "
     "for long-dated sale-and-leaseback structures (e.g. Lidl, Aldi own-use assets). "
     "Produces the cleanest NOI: gross rent ≈ net rent. Net Recovery Rate = 100%. "
     "Institutional benchmark for German retail NNN: GIY 5.0-6.5% (2026).",
     "--"),
]


def render_glossary(metrics, deal, y1_dscr, y1_icr, noi, loan_type):
    giy_s  = f"{metrics['giy']:.2%}"  if metrics.get("giy")  else "No price data"
    niy_s  = f"{metrics['niy']:.2%}"  if metrics.get("niy")  else "No price data"
    dscr_s = f"{y1_dscr:.2f}x"        if y1_dscr             else "No debt data"
    icr_s  = f"{y1_icr:.2f}x"         if y1_icr              else "No debt data"

    src_map = {
        "cost_sheet":              "Itemised Cost Sheet",
        "slider_with_void_estimate":"Sidebar slider + void estimate",
        "fallback_80pct":          "80% fallback",
    }
    fmt = dict(
        wault      = metrics.get("wault", 0),
        dscr       = dscr_s,
        icr        = icr_s,
        ltv        = metrics.get("ltv", 0),
        giy        = giy_s,
        niy        = niy_s,
        noi        = noi,
        noi_margin = metrics.get("noi_margin", 0.80),
        noi_source = src_map.get(metrics.get("noi_source",""), "unknown"),
        void_costs = metrics.get("void_costs", 0),
        grest      = deal.get("grest_rate", 0) or 0,
        anchor_pct = metrics.get("anchor_pct", 0),
        loan_type  = loan_type,
    )

    for term, full_name, definition, live_tpl in GLOSSARY:
        try:
            live_val = live_tpl.format(**fmt)
        except Exception:
            live_val = "--"
        try:
            defn = definition.format(**fmt)
        except Exception:
            defn = definition

        st.markdown(
            f"""<div style="border:1px solid #e5e7eb; border-radius:8px;
                           padding:14px 18px; margin-bottom:10px; background:#fafafa;">
              <strong style="color:#1e3a5f; font-size:0.95rem;">{term}</strong>
              <span style="color:#9ca3af; font-size:0.82rem; margin-left:8px;">{full_name}</span>
              <span style="float:right; background:#d1fae5; color:#065f46;
                           padding:2px 10px; border-radius:10px; font-size:0.78rem;
                           font-weight:600;">&#x1f4ca; {live_val}</span>
              <br/><span style="font-size:0.87rem; color:#374151; line-height:1.6;">{defn}</span>
            </div>""",
            unsafe_allow_html=True,
        )


# =============================================================================
# EXPORT
# =============================================================================

def build_export_excel(deal, metrics, qa, verdict, cf_df, base_irr,
                       noi, loan_type) -> bytes:
    """
    FIX 4: NIY in export uses the same noi passed from screen,
    not a recalculated value from a different margin assumption.
    """
    asking = metrics.get("asking_price", 0) or 0
    # FIX 4: consistent NIY
    niy_export = f"{noi / asking:.2%}" if asking > 0 else "--"
    giy_export = f"{metrics['giy']:.2%}" if metrics.get("giy") else "--"

    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        # IC Summary
        src_map = {"cost_sheet":"Itemised Cost Sheet",
                   "slider_with_void_estimate":"Slider + void estimate",
                   "fallback_80pct":"80% fallback"}
        summary = {
            "Metric": [
                "Property", "City / Asset Type",
                "Asking Price (EUR)", "Total Acq. Cost (EUR)",
                "Equity Required (EUR)", "LTV (%)",
                "Gross Passing Rent (EUR/yr)", "Void Costs (EUR/yr)",
                "NOI (EUR/yr)", "NOI Margin (%)", "NOI Source",
                "GIY (%)", "NIY (%)",
                "DSCR", "ICR",
                "WAULT (Yrs)", "Vacancy Rate (%)",
                "Anchor Concentration (%)",
                "Loan Type", "Base Levered IRR (%)",
                "IC Verdict",
            ],
            "Value": [
                deal.get("property_name", "--"),
                f"{deal.get('city','--')}  |  {deal.get('asset_type','--')}",
                f"{metrics['asking_price']:,.0f}",
                f"{metrics['total_acq_cost']:,.0f}",
                f"{metrics['equity']:,.0f}",
                f"{metrics['ltv']:.1%}",
                f"{metrics['gross_passing_rent']:,.0f}",
                f"{metrics['void_costs']:,.0f}",
                f"{noi:,.0f}",
                f"{metrics['noi_margin']:.1%}",
                src_map.get(metrics.get("noi_source",""), "unknown"),
                giy_export,
                niy_export,          # FIX 4
                f"{metrics['dscr']:.2f}x" if metrics.get("dscr") else "--",
                f"{metrics['icr']:.2f}x"  if metrics.get("icr")  else "--",
                f"{metrics['wault']:.2f}",
                f"{metrics['vacancy_rate']:.1%}",
                f"{metrics['anchor_pct']:.1%}",
                loan_type,
                f"{base_irr:.2%}" if not np.isnan(base_irr) else "--",
                verdict["recommendation"],
            ],
        }
        pd.DataFrame(summary).to_excel(writer, sheet_name="IC Summary", index=False)

        # Cost Breakdown (if available)
        if metrics.get("cost_breakdown"):
            label_map = {
                "property_mgmt_fee":   "Property Management Fee",
                "asset_mgmt_fee":      "Asset Management Fee",
                "building_insurance":  "Building Insurance",
                "capex_reserve":       "CapEx / Structural Reserve",
                "ground_rent":         "Ground Rent (Erbpacht)",
                "grundsteuer":         "Grundsteuer (vacant portion)",
                "void_service_charge": "Void Service Charge",
                "void_insurance":      "Void Insurance",
                "letting_costs":       "Letting / Re-leasing Costs",
                "other_costs":         "Other Non-Recoverable Costs",
            }
            cost_rows = [{"Cost Item": label_map.get(k, k), "Annual Cost (EUR)": v}
                         for k, v in metrics["cost_breakdown"].items()]
            cost_rows.append({"Cost Item": "TOTAL NON-RECOVERABLE", "Annual Cost (EUR)": sum(metrics["cost_breakdown"].values())})
            cost_rows.append({"Cost Item": "NET OPERATING INCOME", "Annual Cost (EUR)": noi})
            pd.DataFrame(cost_rows).to_excel(writer, sheet_name="Cost Breakdown", index=False)

        # QA Audit
        qa_data = [{"Check": r.check, "Status": r.status,
                    "Actual": r.actual, "Benchmark": r.benchmark,
                    "Severity": r.severity} for r in qa]
        pd.DataFrame(qa_data).to_excel(writer, sheet_name="QA Audit", index=False)

        # 10-Year Cash Flow
        if not cf_df.empty:
            cf_df.to_excel(writer, sheet_name="10-Year CF", index=False)

        # Verdict
        vd = (
            [{"Item": "Recommendation", "Detail": verdict["recommendation"]},
             {"Item": "Rationale",      "Detail": verdict["rationale"]}]
            + [{"Item": f"Factor {i+1}", "Detail": r}
               for i, r in enumerate(verdict["top_reasons"])]
            + [{"Item": f"Red Flag {i+1}", "Detail": f}
               for i, f in enumerate(verdict["red_flags"])]
        )
        pd.DataFrame(vd).to_excel(writer, sheet_name="IC Verdict", index=False)

    return buf.getvalue()


# =============================================================================
# MAIN APP
# =============================================================================

# =============================================================================
# VALUE CREATION — ADDITIONAL MODULES
# Building Rights, Parking Monetisation, Functional Obsolescence
# =============================================================================

_BR_CITIES = {
    'münchen':('HIGH','Munich metro housing shortage — densification above retail actively supported.'),
    'germering':('HIGH','Munich suburb — extreme housing pressure. Planning receptive to residential addition.'),
    'hamburg':('HIGH','Hamburg housing pressure. Rooftop residential above retail increasingly permitted.'),
    'berlin':('HIGH','Berlin densification policy. Precedents for residential above Fachmarkt established.'),
    'frankfurt':('HIGH','Frankfurt metro demand. Planning authority receptive to mixed-use.'),
    'dreieich':('HIGH','Frankfurt suburb — premium residential market, strong planning support.'),
    'köln':('HIGH','Cologne inner suburbs under strong residential pressure.'),
    'koeln':('HIGH','Cologne inner suburbs under strong residential pressure.'),
    'stuttgart':('HIGH','Stuttgart housing deficit. Underused retail sites prioritised for densification.'),
    'mannheim':('MEDIUM','Mannheim urban densification active. Moderate potential.'),
    'düsseldorf':('MEDIUM','Growing city — retail-to-residential needs strong political engagement.'),
    'dusseldorf':('MEDIUM','Growing city — retail-to-residential needs strong political engagement.'),
    'leipzig':('MEDIUM','Leipzig growing fast — increasing housing pressure. Trend favourable.'),
    'dresden':('MEDIUM','Growth area. Planning permissive but residential market depth uncertain.'),
    'hannover':('MEDIUM','Solid housing market. Moderate potential for suburban retail sites.'),
    'dortmund':('MEDIUM','Dortmund regenerating — selected suburban areas have potential.'),
    'ingolstadt':('HIGH','Strong auto-industry economy. Housing demand high, planning receptive.'),
    'rostock':('LOW','Population declining. Housing supply adequate. Building rights upside limited.'),
    'bochum':('LOW','Declining Ruhr city. No credible residential market above retail.'),
    'gelsenkirchen':('LOW','Shrinking city. Building rights not a realistic value lever.'),
    'magdeburg':('LOW','Structural decline. Residential demand weak.'),
}

def _vc_building_rights(city_str, gla_sqm, year_built, asset_type='Fachmarktzentrum'):
    """Flag building rights (Baurechte) potential based on location and site."""
    city_lower = (city_str or '').lower()
    potential, rationale = 'UNKNOWN', 'City not in database — commission local planning assessment.'
    for city_key, (level, reason) in _BR_CITIES.items():
        if city_key in city_lower:
            potential, rationale = level, reason
            break
    if potential == 'UNKNOWN':
        if any(x in city_lower for x in ['bayern','bavaria']): potential = 'MEDIUM'
        elif any(x in city_lower for x in ['hessen','hamburg']): potential = 'HIGH'
        elif any(x in city_lower for x in ['sachsen','thüringen','mecklenburg']): potential = 'LOW'
        else: potential = 'MEDIUM'
    # Site size modifier
    if gla_sqm and gla_sqm < 1500 and potential == 'HIGH': potential = 'MEDIUM'
    # Estimated residential GFA (conservative: 1.2× GLA on car park / unused land)
    est_gfa  = int((gla_sqm or 0) * 1.2)
    val_low  = est_gfa * 800    # EUR/m² net dev value conservative
    val_high = est_gfa * 2500   # EUR/m² prime location
    age_note = ''
    if year_built and (2026 - year_built) > 15:
        age_note = f' Building is {2026-year_built}yr old — refurbishment cycle creates natural planning window.'
    return {
        'potential':    potential,
        'rationale':    rationale + age_note,
        'est_gfa_m2':   est_gfa,
        'val_low':      val_low,
        'val_high':     val_high,
        'action':       ('Commission local Gutachter: assess Bebauungsplan amendment potential, '
                         'current GFZ/GRZ utilisation, zoning (Baugebiet), and municipal housing plan. '
                         'Timeline: 4–8 weeks. Cost: EUR 3,000–8,000.'),
    }


def _vc_parking_monetisation(parking_spaces, location_type='MEDIUM'):
    """
    Calculate parking revenue beyond EV charging.
    Three streams: dynamic pricing, logistics micro-hub, click-and-collect.
    """
    if not parking_spaces or parking_spaces <= 0:
        return {'total_annual': 0, 'breakdown': {}, 'capex': 0,
                'telecom': 0, 'total_with_telecom': 0, 'payback_yrs': 99}
    mult = {'HIGH': 1.4, 'MEDIUM': 1.0, 'LOW': 0.6}.get(location_type, 1.0)
    # Dynamic pricing: 15% of spaces, 3hrs/day, 180 days/yr, €2/hr
    dyn_sp  = max(0, int(parking_spaces * 0.15))
    dyn_rev = int(dyn_sp * 3 * 180 * 2.0 * mult)
    # Logistics micro-hub
    log_sp  = min(6, max(3, int(parking_spaces * 0.04)))
    log_rev = int(log_sp * 800 * 12 * (1 if mult >= 1.0 else 0.5))
    # Click-and-collect
    cc_sp   = min(8, max(4, int(parking_spaces * 0.05)))
    cc_rev  = int(cc_sp * 500 * 12 * mult)
    # Telecom mast — flat EUR 12k/yr 20-yr lease
    telecom = 12_000
    total   = dyn_rev + log_rev + cc_rev
    capex   = dyn_sp * 3000 + log_sp * 500 + cc_sp * 500
    return {
        'total_annual':       total,
        'capex':              capex,
        'payback_yrs':        round(capex / total, 1) if total > 0 else 99,
        'telecom':            telecom,
        'total_with_telecom': total + telecom,
        'breakdown': {
            'Dynamic Pricing':     {'income': dyn_rev, 'spaces': dyn_sp, 'capex': dyn_sp*3000},
            'Logistics Micro-Hub': {'income': log_rev, 'spaces': log_sp, 'capex': log_sp*500},
            'Click & Collect':     {'income': cc_rev,  'spaces': cc_sp,  'capex': cc_sp*500},
            'Telecom Mast':        {'income': telecom,  'spaces': 0,      'capex': 0},
        },
    }


def _vc_obsolescence(year_built, gla_sqm, asset_type='Fachmarktzentrum'):
    """RICS/gif-based functional obsolescence assessment."""
    if not year_built:
        return {'age': None, 'condition': 'Unknown', 'discount_pct': None,
                'note': 'Year built unknown — obtain Baujahr from seller.',
                'action': 'Request year of construction and Energy Performance Certificate (Energieausweis).',
                'disc_note': 'No obsolescence discount calculated — year built not provided.'}
    age = 2026 - year_built
    if age <= 5:   cond, disc = 'Modern',      0.00
    elif age <= 10: cond, disc = 'Good',        0.02
    elif age <= 15: cond, disc = 'Adequate',    0.05
    elif age <= 20: cond, disc = 'Ageing',      0.08
    elif age <= 30: cond, disc = 'Dated',       0.12
    else:           cond, disc = 'Obsolescent', 0.18
    notes = {
        'Modern':      'No functional obsolescence expected. Minimal CapEx required.',
        'Good':        'Minor cosmetic updates may be needed. No structural discount warranted.',
        'Adequate':    'HVAC and energy systems approaching end-of-life. Verify EPC rating.',
        'Ageing':      'Roof, HVAC, and façade likely requiring CapEx within 5 years. Building survey required.',
        'Dated':       f'{age}yr asset. Energy performance likely EPC Grade D-E. KfW retrofit eligible but substantial CapEx.',
        'Obsolescent': f'{age}yr asset. Structural obsolescence. Consider repositioning/redevelopment in business plan.',
    }
    actions = {
        'Modern':      'No urgent action. Standard 5-yearly condition inspection recommended.',
        'Good':        'No urgent action. Obtain EPC on acquisition.',
        'Adequate':    'Obtain EPC and HVAC service records from seller.',
        'Ageing':      'Commission independent building survey (Gebäudegutachten) before IC submission.',
        'Dated':       'Commission full building survey + EPC. Obtain quotes for KfW retrofit program.',
        'Obsolescent': 'Full structural survey mandatory. Factor EUR 300–600/m² refurbishment CapEx into model.',
    }
    return {
        'age':          age,
        'condition':    cond,
        'discount_pct': disc,
        'note':         notes[cond],
        'action':       actions[cond],
        'disc_note':    (f'Appraisers typically apply {disc:.0%} functional obsolescence discount vs modern stock.'
                         if disc > 0 else 'No obsolescence discount expected.'),
    }


# ==============================================================================
# DEEP INSIGHTS — 7 helper functions
# ==============================================================================

_SECTOR_SCORE: dict = {
    'food': 100, 'grocery': 100, 'supermarket': 100, 'discounter': 95,
    'drug store': 90, 'pharmacy': 88, 'health': 85, 'beauty': 85, 'drugstore': 85,
    'home improvement': 75, 'diy': 75, 'electronics': 60,
    'sports': 58, 'fitness': 55, 'pet': 55,
    'discount fashion': 50, 'fashion': 45, 'clothing': 45,
    'restaurant': 40, 'food & beverage': 40,
    'service': 35, 'office': 30, 'other': 35,
}


def _sector_base_score(sector: str) -> int:
    s = (sector or '').lower().strip()
    for k, v in _SECTOR_SCORE.items():
        if k in s:
            return v
    return 35


def _score_color(score: int) -> str:
    if score >= 75: return '#16a34a'
    if score >= 50: return '#d97706'
    return '#dc2626'


def _di_covenant_scores(rr: pd.DataFrame) -> pd.DataFrame:
    """0-100 covenant score per tenant: sector base + anchor +15 + CPI +10."""
    active = rr[~rr['is_vacant']].copy()
    if active.empty:
        return pd.DataFrame(columns=['Tenant', 'Sector', 'Score', 'Color', 'Annual Rent (EUR)'])
    rows = []
    for _, t in active.iterrows():
        base = _sector_base_score(str(t.get('sector', '')))
        bonus = 0
        if t.get('anchor', False):
            bonus += 15
        ci = t.get('cpi_clause', False)
        if isinstance(ci, str):
            ci = ci.strip().upper() not in ('', 'NO', 'N', 'FALSE', '0')
        if ci:
            bonus += 10
        score = min(100, base + bonus)
        rows.append({
            'Tenant': str(t.get('tenant_name', t.get('tenant', 'Unknown'))),
            'Sector': str(t.get('sector', '—')),
            'Score': score,
            'Color': _score_color(score),
            'Annual Rent (EUR)': float(t.get('annual_rent', 0) or 0),
        })
    return pd.DataFrame(rows)


def _di_income_cliff(rr: pd.DataFrame, annual_debt_service: float) -> dict:
    """Month-by-month rent assuming zero renewals; find when rent < monthly DS."""
    import datetime
    today = datetime.date.today()
    monthly_ds = (annual_debt_service / 12.0) if annual_debt_service > 0 else 0
    active = rr[~rr['is_vacant']].copy()
    expiries = []
    for _, row in active.iterrows():
        try:
            exp = pd.to_datetime(row.get('lease_expiry')).date()
            ar = float(row.get('annual_rent', 0) or 0)
            expiries.append((exp, ar))
        except Exception:
            pass
    total_rent = sum(ar for _, ar in expiries)
    months_data = []
    cliff_month = cliff_rent = None
    for m in range(1, 181):
        probe = today + datetime.timedelta(days=int(m * 30.44))
        remaining = sum(ar for exp, ar in expiries if exp > probe)
        monthly_rem = remaining / 12.0
        months_data.append({'month': m, 'monthly_rent': monthly_rem, 'monthly_ds': monthly_ds})
        if cliff_month is None and monthly_ds > 0 and monthly_rem < monthly_ds:
            cliff_month = m
            cliff_rent = monthly_rem
    return {
        'cliff_month': cliff_month,
        'cliff_rent': cliff_rent,
        'monthly_ds': monthly_ds,
        'rent_now': total_rent / 12.0,
        'data': months_data,
    }


def _di_break_stress(rr: pd.DataFrame, noi: float,
                     loan: float, int_rt: float, amrt_rt: float) -> pd.DataFrame:
    """Post-break DSCR for every tenant with a break option (tenant exits, full void costs)."""
    active = rr[~rr.get('is_vacant', pd.Series(False, index=rr.index))].copy()
    if 'break_option' not in active.columns:
        return pd.DataFrame()
    breaks = active[active['break_option'].notna() & (active['break_option'] != '')].copy()
    if breaks.empty:
        return pd.DataFrame()
    annual_ds = loan * (int_rt + amrt_rt) if loan > 0 else 0
    rows = []
    for _, t in breaks.iterrows():
        area = float(t.get('area_sqm', 0) or 0)
        rent = float(t.get('annual_rent', 0) or 0)
        void_cost = area * VOID_HOLD_RATE
        post_noi = noi - rent - void_cost
        post_dscr = post_noi / annual_ds if annual_ds > 0 else None
        if post_dscr is None:       flag = '—'
        elif post_dscr < 1.20:     flag = 'FAIL'
        elif post_dscr < 1.30:     flag = 'WARN'
        else:                       flag = 'PASS'
        try:
            break_yr = pd.to_datetime(t.get('break_option')).year
        except Exception:
            break_yr = '—'
        rows.append({
            'Tenant': str(t.get('tenant_name', t.get('tenant', '?'))),
            'Break Year': break_yr,
            'Rent at Risk': f"EUR {rent:,.0f}",
            'Void Cost/yr': f"EUR {void_cost:,.0f}",
            'Post-Break NOI': f"EUR {post_noi:,.0f}",
            'Post-Break DSCR': f"{post_dscr:.2f}x" if post_dscr else '—',
            'Status': flag,
        })
    return pd.DataFrame(rows)


def _di_true_yield_gap(metrics: dict, rr: pd.DataFrame) -> dict:
    """Actual GIY vs stabilised GIY (void filled at avg passing rent/m²); gap in bps."""
    asking = metrics.get('asking_price', 0) or 0
    if asking <= 0:
        return {}
    actual_giy = metrics.get('giy', 0) or 0
    active = rr[~rr['is_vacant']]
    vacant = rr[rr['is_vacant']]
    active_area = float(active['area_sqm'].sum() or 0)
    avg_rent_sqm_mo = (active['annual_rent'].sum() / 12.0 / active_area) if active_area > 0 else 0
    vacant_area = float(vacant['area_sqm'].sum() or 0)
    erv_void = vacant_area * avg_rent_sqm_mo * 12.0
    stabilised_rent = metrics.get('gross_passing_rent', 0) + erv_void
    stabilised_giy = stabilised_rent / asking if asking > 0 else 0
    gap_bps = (stabilised_giy - actual_giy) * 10000
    return {
        'actual_giy': actual_giy,
        'stabilised_giy': stabilised_giy,
        'gap_bps': gap_bps,
        'erv_void_income': erv_void,
        'vacant_area': vacant_area,
        'avg_rent_sqm_mo': avg_rent_sqm_mo,
        'asking': asking,
    }


def _di_refi_risk(metrics: dict, noi: float, loan: float, int_rt: float,
                  amrt_rt: float, io_years: int, hold_years: int,
                  loan_type: str, cpi_rate: float) -> dict:
    """Exit value / implied cap at hold_years; flag if inside entry GIY by >100bps."""
    if loan <= 0 or int_rt <= 0:
        return {}
    projected_noi = noi * ((1 + cpi_rate) ** hold_years)
    balance = loan
    for yr in range(1, hold_years + 1):
        interest = balance * int_rt
        if yr <= io_years:
            amort = 0.0
        elif loan_type == 'Annuity':
            total_pmt = loan * (int_rt + amrt_rt)
            amort = total_pmt - interest
        else:
            amort = loan * amrt_rt
        balance = max(0.0, balance - amort)
    exit_val = balance / 0.65 if balance > 0 else 0
    implied_cap = projected_noi / exit_val if exit_val > 0 else None
    entry_giy = metrics.get('giy', 0) or 0
    flag = None
    if implied_cap and entry_giy > 0:
        spread_bps = (entry_giy - implied_cap) * 10000
        if spread_bps > 100:
            flag = (f"Implied exit cap {implied_cap:.2%} is {spread_bps:.0f}bps tighter than "
                    f"entry GIY {entry_giy:.2%} — bullish cap rate compression assumed. "
                    "Verify with current market evidence before IC.")
        elif implied_cap < entry_giy:
            flag = (f"Implied exit cap {implied_cap:.2%} inside entry GIY {entry_giy:.2%} — "
                    "market needs to tighten for refinancing to work at 65% LTV.")
    return {
        'hold_years': hold_years,
        'loan_balance': balance,
        'projected_noi': projected_noi,
        'exit_val_65ltv': exit_val,
        'implied_cap': implied_cap,
        'entry_giy': entry_giy,
        'flag': flag,
    }


def _di_cpi_projection(rr: pd.DataFrame, cpi_rate: float) -> pd.DataFrame:
    """Rent at Years 3/5/7/10 per tenant using per-tenant CPI passthrough rates."""
    active = rr[~rr['is_vacant']].copy()
    if active.empty:
        return pd.DataFrame()
    rows = []
    for _, t in active.iterrows():
        base_rent = float(t.get('annual_rent', 0) or 0)
        passthrough = float(t.get('cpi_passthrough', 0) or 0)
        ci = t.get('cpi_clause', False)
        if isinstance(ci, str):
            ci = ci.strip().upper() not in ('', 'NO', 'N', 'FALSE', '0')
        effective_rate = cpi_rate * passthrough if ci else 0.0
        row = {
            'Tenant': str(t.get('tenant_name', t.get('tenant', '?'))),
            'Indexed': 'Yes' if ci else 'No',
            'Pass-thru': f"{passthrough:.0%}",
            'Base Rent': f"EUR {base_rent:,.0f}",
        }
        for yr in [3, 5, 7, 10]:
            row[f'Yr {yr}'] = f"EUR {base_rent * ((1 + effective_rate) ** yr):,.0f}"
        rows.append(row)
    return pd.DataFrame(rows)


def _di_tenant_concentration(rr: pd.DataFrame) -> dict:
    """
    Compute tenant income concentration metrics from the rent roll.

    Returns a dict with:
      total_rent        float   — sum of all active annual rents
      top1_pct          float   — top-1 tenant share of total rent
      top3_pct          float   — top-3 tenants' combined share
      top5_pct          float   — top-5 tenants' combined share
      top1_name         str     — name of the single largest tenant
      top_tenants       list    — [{'tenant', 'rent', 'pct'}, ...] sorted descending, all active
      sector_breakdown  list    — [{'sector', 'rent', 'pct'}, ...] sorted descending
      expiry_by_year    list    — [{'year', 'rent', 'pct'}, ...] next 10 calendar years
      anchor_pct        float   — anchor rent / total rent
      anchor_rent       float   — total rent from anchor-flagged tenants
    """
    if rr is None or rr.empty:
        return {}

    active = rr[~rr.get('is_vacant', pd.Series(False, index=rr.index))]
    if active.empty:
        return {}

    total_rent = float(active['annual_rent'].sum())
    if total_rent <= 0:
        return {}

    # ── Top-N tenant concentration ────────────────────────────────────────────
    by_tenant = (
        active.groupby('tenant', as_index=False)['annual_rent']
        .sum()
        .sort_values('annual_rent', ascending=False)
        .reset_index(drop=True)
    )
    by_tenant['pct'] = by_tenant['annual_rent'] / total_rent
    top1_rent = float(by_tenant.iloc[0]['annual_rent'])   if len(by_tenant) >= 1 else 0
    top3_rent = float(by_tenant.iloc[:3]['annual_rent'].sum()) if len(by_tenant) >= 1 else 0
    top5_rent = float(by_tenant.iloc[:5]['annual_rent'].sum()) if len(by_tenant) >= 1 else 0

    top_tenants = [
        {
            'tenant': str(r['tenant']),
            'rent':   float(r['annual_rent']),
            'pct':    float(r['pct']),
        }
        for _, r in by_tenant.iterrows()
    ]

    # ── Sector breakdown ──────────────────────────────────────────────────────
    sector_col = 'sector' if 'sector' in active.columns else None
    if sector_col:
        by_sector = (
            active.groupby(sector_col, as_index=False)['annual_rent']
            .sum()
            .sort_values('annual_rent', ascending=False)
            .reset_index(drop=True)
        )
        by_sector['pct'] = by_sector['annual_rent'] / total_rent
        sector_breakdown = [
            {'sector': str(r[sector_col]), 'rent': float(r['annual_rent']), 'pct': float(r['pct'])}
            for _, r in by_sector.iterrows()
        ]
    else:
        sector_breakdown = []

    # ── Lease expiry by calendar year (rent expiring, next 10 yrs) ───────────
    import datetime as _dt
    this_year = _dt.date.today().year
    expiry_years = range(this_year, this_year + 11)   # 10-year window inclusive of current year
    expiry_by_year = []
    if 'lease_expiry' in active.columns:
        for yr in expiry_years:
            mask = active['lease_expiry'].apply(
                lambda d: (d.year == yr) if (hasattr(d, 'year') and pd.notna(d)) else False
            )
            yr_rent = float(active.loc[mask, 'annual_rent'].sum())
            expiry_by_year.append({
                'year': yr,
                'rent': yr_rent,
                'pct':  yr_rent / total_rent,
            })

    # ── Anchor dependency ─────────────────────────────────────────────────────
    anchor_rent = 0.0
    if 'is_anchor' in active.columns:
        anchor_rent = float(active.loc[active['is_anchor'].astype(bool), 'annual_rent'].sum())

    return {
        'total_rent':       total_rent,
        'top1_pct':         top1_rent / total_rent,
        'top3_pct':         top3_rent / total_rent,
        'top5_pct':         top5_rent / total_rent,
        'top1_name':        str(by_tenant.iloc[0]['tenant']) if len(by_tenant) >= 1 else '',
        'top_tenants':      top_tenants,
        'sector_breakdown': sector_breakdown,
        'expiry_by_year':   expiry_by_year,
        'anchor_pct':       anchor_rent / total_rent,
        'anchor_rent':      anchor_rent,
    }


def _is_cpi_indexed(row) -> bool:
    """Return True if a rent roll row has CPI indexation enabled."""
    ci = row.get('cpi_clause', False)
    if isinstance(ci, str):
        return ci.strip().upper() not in ('', 'NO', 'N', 'FALSE', '0')
    return bool(ci)


def _di_swot(metrics: dict, qa: list, rr: pd.DataFrame, deal: dict,
             noi: float, loan: float, int_rt: float, amrt_rt: float,
             io_years: int, loan_type_key: str,
             cpi_indexation: float, y1_debt_svc: float) -> dict:
    """
    Build a SWOT analysis dict from already-computed deal data — no external data needed.

    Returns:
      strengths        list[str]  — max 6, green metrics + strong tenants
      weaknesses       list[str]  — max 6, yellow/red QA flags in plain English
      opportunities    list[str]  — max 6, quantified upside
      threats          list[str]  — max 6, income-cliff / break / rollover / refi risks
      summary          str        — one-sentence dominant theme
      opp_yield_gap_eur float     — ERV void upside EUR (mirrors _di_true_yield_gap)
    """
    from constants import (
        DSCR_PASS, ICR_PASS, LTV_PASS, GIY_PASS, WAULT_PASS,
        CHECK_LTV, CHECK_DSCR, CHECK_ICR, CHECK_WAULT, CHECK_VACANCY,
        CHECK_ANCHOR, CHECK_EXPIRY_3YR, CHECK_GIY, CHECK_NOI,
        CHECK_EQUITY, CHECK_DY,
    )

    strengths     = []
    weaknesses    = []
    opportunities = []
    threats       = []

    gross_rent    = float(metrics.get('gross_passing_rent', 0) or 0)
    wault         = float(metrics.get('wault', 0) or 0)
    anchor_pct    = float(metrics.get('anchor_pct', 0) or 0)
    expiry_risk   = float(metrics.get('expiry_risk_3yr_pct', 0) or 0)
    loan_term_yrs = int(deal.get('loan_term_yrs', 10) or 10)
    active        = rr[~rr.get('is_vacant', pd.Series(False, index=rr.index))] if not rr.empty else rr.iloc[:0]

    # ── STRENGTHS (green metrics, high-covenant anchors, CPI income) ──────────
    dscr = metrics.get('dscr')
    if dscr and dscr >= DSCR_PASS:
        strengths.append(
            f"DSCR {dscr:.2f}x — {dscr - DSCR_PASS:.2f}x above 1.30x bank minimum"
        )

    icr = metrics.get('icr')
    if icr and icr >= ICR_PASS:
        strengths.append(
            f"ICR {icr:.2f}x — {icr - ICR_PASS:.2f}x above 1.75x interest cover covenant"
        )

    ltv = metrics.get('ltv')
    if ltv and ltv <= LTV_PASS:
        strengths.append(
            f"LTV {ltv:.1%} — {(LTV_PASS - ltv)*100:.1f}pp of equity buffer below 65% bank max"
        )

    giy = metrics.get('giy')
    if giy and giy >= GIY_PASS:
        strengths.append(
            f"GIY {giy:.2%} — {(giy - GIY_PASS)*10000:.0f}bps above 5.5% DE retail benchmark"
        )

    if wault >= WAULT_PASS:
        strengths.append(
            f"WAULT {wault:.1f} years — {wault - WAULT_PASS:.1f}yrs above 4-year minimum; strong income visibility"
        )

    # High-covenant anchor tenants (score ≥ 75)
    if not rr.empty:
        _cov = _di_covenant_scores(rr)
        for _, _cr in _cov.iterrows():
            if float(_cr['Score']) >= 75:
                strengths.append(
                    f"{str(_cr['Tenant'])[:28]}: covenant score {int(_cr['Score'])}/100"
                    f" — strong {_cr['Sector']} anchor"
                )

    # CPI-indexed income ≥ 60%
    if gross_rent > 0 and not active.empty:
        idx_rent = sum(
            float(t.get('annual_rent', 0) or 0)
            for _, t in active.iterrows()
            if _is_cpi_indexed(t)
        )
        idx_pct = idx_rent / gross_rent
        if idx_pct >= 0.60:
            strengths.append(
                f"{idx_pct:.0%} of passing rent CPI-indexed — embedded inflation protection on income"
            )

    # ── WEAKNESSES (QA flags + structural risks) ──────────────────────────────
    _QA_EN = {
        CHECK_LTV:
            lambda: f"LTV {metrics.get('ltv', 0):.1%} exceeds 65% — equity injection or price reduction required",
        CHECK_DSCR:
            lambda: f"DSCR {metrics.get('dscr') or 0:.2f}x below 1.30x — income insufficient vs debt service",
        CHECK_ICR:
            lambda: f"ICR {metrics.get('icr') or 0:.2f}x below 1.75x minimum — interest cover is thin",
        CHECK_WAULT:
            lambda: f"WAULT {wault:.1f} years below 4-year minimum — lease rollover risk within loan term",
        CHECK_VACANCY:
            lambda: f"Vacancy {metrics.get('vacancy_rate', 0):.1%} exceeds 10% — void costs eroding NOI",
        CHECK_ANCHOR:
            lambda: f"Anchor concentration {anchor_pct:.1%} exceeds 55% — single-covenant income dependency",
        CHECK_EXPIRY_3YR:
            lambda: f"{expiry_risk:.0%} of rent expires within 3 years — concentrated near-term rollover",
        CHECK_GIY:
            lambda: f"GIY {metrics.get('giy', 0):.2%} below 5.5% benchmark — thin entry yield",
        CHECK_DY:
            lambda: f"Debt yield {metrics.get('debt_yield', 0):.2%} below 7.5% — lender may require lower loan",
        CHECK_NOI:
            lambda: "NOI zero or negative — deal cannot service any debt",
        CHECK_EQUITY:
            lambda: "Equity position zero or negative — capital structure unviable",
    }
    _seen_checks = set()
    for _qi in sorted(qa, key=lambda q: 0 if q.status == 'FAIL' else 1):
        if _qi.status in ('WARN', 'FAIL') and _qi.check in _QA_EN and _qi.check not in _seen_checks:
            weaknesses.append(_QA_EN[_qi.check]())
            _seen_checks.add(_qi.check)

    # Anchor concentration > 50% (softer than QA threshold of 55%)
    if anchor_pct > 0.50 and CHECK_ANCHOR not in _seen_checks:
        weaknesses.append(
            f"Anchor concentration {anchor_pct:.1%} — loss of anchor is income-critical"
        )

    # WAULT 4–5yr (softer than QA minimum; not already caught above)
    if 4.0 <= wault < 5.0 and CHECK_WAULT not in _seen_checks:
        weaknesses.append(
            f"WAULT {wault:.1f} years — below 5-year preferred threshold; active lease management needed"
        )

    # Non-retail tenants (office / industrial / residential)
    if not active.empty and 'sector' in active.columns:
        _NR = {'office', 'residential', 'industrial', 'warehouse', 'storage'}
        for _, _row in active.iterrows():
            if any(s in str(_row.get('sector', '')).lower() for s in _NR):
                _ar = float(_row.get('annual_rent', 0) or 0)
                weaknesses.append(
                    f"{str(_row.get('tenant', '?'))[:28]} ({_row['sector']}): non-retail tenant — "
                    f"EUR {_ar:,.0f}/yr may not suit retail CRE lender mandate"
                )

    # Vacant units with area and void cost
    if not rr.empty and 'is_vacant' in rr.columns:
        for _, _vrow in rr[rr['is_vacant']].iterrows():
            _varea = float(_vrow.get('area_sqm', 0) or 0)
            if _varea > 0:
                weaknesses.append(
                    f"Unit {_vrow.get('unit_ref', '?')}: {_varea:,.0f} m² vacant — "
                    f"EUR {_varea * VOID_HOLD_RATE:,.0f}/yr void holding cost"
                )

    # ── OPPORTUNITIES (quantified upside, EUR-sorted) ─────────────────────────
    opp_yield_gap_eur = 0.0

    # 1. Yield gap void upside
    _yg = _di_true_yield_gap(metrics, rr)
    if _yg and _yg.get('erv_void_income', 0) > 0:
        opp_yield_gap_eur = float(_yg['erv_void_income'])
        opportunities.append(
            f"Void leasing upside: +EUR {opp_yield_gap_eur:,.0f}/yr NOI if "
            f"{_yg['vacant_area']:,.0f} m² re-let at portfolio avg "
            f"EUR {_yg['avg_rent_sqm_mo']:.2f}/m²/mo (stabilised GIY: {_yg['stabilised_giy']:.2%})"
        )

    # 2. Share deal GrESt saving
    _sd = _di_share_deal_calc(metrics, deal)
    if _sd and _sd.get('net_saving', 0) > 10_000:
        _pct_eq = f" ({_sd['pct_of_equity']:.1%} of equity)" if _sd.get('pct_of_equity') else ""
        opportunities.append(
            f"Share deal (Anteilskauf): EUR {_sd['net_saving']:,.0f} GrESt saving{_pct_eq} — "
            f"requires hold > 10 years (§1 GrEStG re-trigger risk)"
        )

    # 3. CPI rent growth to Year 5
    if gross_rent > 0 and not active.empty:
        _yr5_uplift = 0.0
        for _, _t in active.iterrows():
            if _is_cpi_indexed(_t):
                _pt  = float(_t.get('cpi_passthrough', 0.75) or 0.75)
                _eff = cpi_indexation * _pt
                _yr5_uplift += float(_t.get('annual_rent', 0) or 0) * ((1 + _eff) ** 5 - 1)
        if _yr5_uplift > 0:
            opportunities.append(
                f"CPI income growth: +EUR {_yr5_uplift:,.0f}/yr additional rent by Year 5 "
                f"at {cpi_indexation:.1%} CPI across indexed leases"
            )

    # 4. Below-market reversion tenants (rent/m² > 15% below portfolio avg)
    _avg_sqm = float(metrics.get('wtd_rent_per_sqm_mo', 0) or 0)
    if _avg_sqm > 0 and not active.empty and 'rent_per_sqm_mo' in active.columns:
        for _, _t in active.iterrows():
            _rsqm = float(_t.get('rent_per_sqm_mo', 0) or 0)
            if 0 < _rsqm < _avg_sqm * 0.85:
                _disc = (_avg_sqm - _rsqm) / _avg_sqm
                _area = float(_t.get('area_sqm', 0) or 0)
                _rev  = (_avg_sqm - _rsqm) * _area * 12
                opportunities.append(
                    f"{str(_t.get('tenant', '?'))[:25]}: EUR {_rsqm:.2f}/m²/mo is "
                    f"{_disc:.0%} below avg — EUR {_rev:,.0f}/yr reversion potential at lease renewal"
                )

    # ── THREATS (income cliff, break options, expiry, concentration, refi) ────

    # 1. Income cliff within loan term
    if y1_debt_svc > 0 and not rr.empty:
        _cliff = _di_income_cliff(rr, y1_debt_svc)
        _cm = _cliff.get('cliff_month')
        if _cm and _cm < loan_term_yrs * 12:
            threats.append(
                f"Income cliff at Month {_cm} ({_cm / 12:.1f} yrs) — "
                f"rent falls to EUR {_cliff['cliff_rent']:,.0f}/mo vs "
                f"EUR {_cliff['monthly_ds']:,.0f}/mo debt service, within loan term"
            )

    # 2. Break options with post-break DSCR at WARN or FAIL
    if not rr.empty and noi > 0 and loan > 0:
        _brk = _di_break_stress(rr, noi, loan, int_rt, amrt_rt)
        if not _brk.empty:
            for _, _brow in _brk.iterrows():
                if str(_brow.get('Status', '')) in ('WARN', 'FAIL'):
                    threats.append(
                        f"Break option — {str(_brow.get('Tenant', '?'))[:25]}: "
                        f"post-exercise DSCR {_brow.get('Post-Break DSCR', '?')} "
                        f"[{_brow.get('Status', '')}]"
                    )

    # 3. Leases expiring within 3 years > 20% of gross rent
    if expiry_risk > 0.20 and gross_rent > 0:
        threats.append(
            f"{expiry_risk:.0%} of gross rent (EUR {expiry_risk * gross_rent:,.0f}/yr) "
            f"expires within 3 years — concentrated near-term rollover"
        )

    # 4. Single tenant > 40% of income
    if not active.empty and gross_rent > 0 and 'tenant' in active.columns:
        _by_t = active.groupby('tenant')['annual_rent'].sum()
        if len(_by_t) > 0:
            _t1r = float(_by_t.max())
            _t1n = _by_t.idxmax()
            _t1p = _t1r / gross_rent
            if _t1p > 0.40:
                threats.append(
                    f"{str(_t1n)[:28]}: {_t1p:.1%} of gross income — "
                    f"income-critical single-tenant concentration"
                )

    # 5. Refinancing screen: cap rate compression required
    if loan > 0:
        _refi = _di_refi_risk(
            metrics, noi, loan, int_rt, amrt_rt,
            io_years, 5, loan_type_key, cpi_indexation,
        )
        if _refi and _refi.get('flag'):
            threats.append(f"Refinancing screen: {str(_refi['flag'])[:130]}")

    # ── Cap each quadrant at 6 items ──────────────────────────────────────────
    strengths     = strengths[:6]
    weaknesses    = weaknesses[:6]
    opportunities = opportunities[:6]
    threats       = threats[:6]

    # ── One-sentence SWOT summary ─────────────────────────────────────────────
    n_s = len(strengths)
    n_w = len(weaknesses)
    n_t = len(threats)
    n_o = len(opportunities)
    _hard_fails   = [q for q in qa if q.status == 'FAIL' and q.severity == 'RED']
    _yellow_fails = [q for q in qa if q.status == 'FAIL' and q.severity == 'YELLOW']

    if _hard_fails:
        _rf_names = ', '.join(q.check for q in _hard_fails[:2])
        summary = (
            f"Hard fails on {_rf_names} must be resolved before IC submission — "
            f"{n_o} upside opportunit{'y' if n_o == 1 else 'ies'} exist but are secondary to "
            f"addressing covenant breaches."
        )
    elif _yellow_fails:
        _yf_names = ', '.join(q.check for q in _yellow_fails[:2])
        summary = (
            f"Warnings to address: {_yf_names} — "
            f"{n_s} strength{'s' if n_s != 1 else ''} support credit viability; "
            f"no hard covenant breaches but further DD required."
        )
    elif n_t >= 3:
        summary = (
            f"High-risk profile with {n_t} material threats; "
            f"{n_s} strength{'s' if n_s != 1 else ''} provide partial mitigation — "
            f"thorough stress-testing required before IC submission."
        )
    elif n_s >= 4 and n_t <= 1 and n_w <= 2:
        _upside = (
            f" and EUR {opp_yield_gap_eur:,.0f}/yr void upside available"
            if opp_yield_gap_eur > 0 else ""
        )
        summary = (
            f"Fundamentally strong deal: {n_s} green metrics with limited downside{_upside}."
        )
    elif n_w >= 3 or n_t >= 2:
        summary = (
            f"Mixed risk profile — {n_w} weakness{'es' if n_w != 1 else ''} and "
            f"{n_t} threat{'s' if n_t != 1 else ''} require active management; "
            f"{n_s} strength{'s' if n_s != 1 else ''} support INVESTIGATE rather than DECLINE."
        )
    else:
        summary = (
            f"{n_s} strength{'s' if n_s != 1 else ''} support credit viability; "
            f"{n_w} weakness{'es' if n_w != 1 else ''} and {n_t} "
            f"threat{'s' if n_t != 1 else ''} are manageable — "
            f"focus on lease rollover and concentration mitigation."
        )

    return {
        'strengths':          strengths,
        'weaknesses':         weaknesses,
        'opportunities':      opportunities,
        'threats':            threats,
        'summary':            summary,
        'opp_yield_gap_eur':  opp_yield_gap_eur,
    }


def _di_share_deal_calc(metrics: dict, deal: dict) -> dict:
    """Asset deal vs share deal cost comparison (0% GrESt + 0.5% legal premium)."""
    asking = metrics.get('asking_price', 0) or 0
    equity = metrics.get('equity', 0) or 0
    grest_rate = deal.get('grest_rate', 0.055) or 0.055
    grest_asset = asking * grest_rate
    share_legal = asking * 0.005
    notary_asset = asking * 0.015
    notary_share = asking * 0.005
    total_acq_asset = asking + grest_asset + notary_asset
    total_acq_share = asking + share_legal + notary_share
    total_saving = total_acq_asset - total_acq_share
    net_saving = grest_asset - share_legal
    return {
        'asking': asking,
        'grest_rate': grest_rate,
        'grest_asset': grest_asset,
        'share_legal': share_legal,
        'net_saving': net_saving,
        'pct_of_equity': (net_saving / equity) if equity > 0 else None,
        'total_acq_asset': total_acq_asset,
        'total_acq_share': total_acq_share,
        'total_saving': total_saving,
        'notary_asset': notary_asset,
        'notary_share': notary_share,
    }


# ── Scenario Engine helpers ────────────────────────────────────────────────────

def _scen_irr_bisect(flows, lo=-0.999, hi=50.0, tol=1e-7, max_iter=300):
    """Bisection IRR solver. Returns None if no sign change in [lo, hi]."""
    def _npv(r):
        return sum(cf / (1.0 + r) ** t for t, cf in enumerate(flows))
    f_lo = _npv(lo)
    f_hi = _npv(hi)
    if f_lo * f_hi > 0:
        return None                             # no real IRR in range
    for _ in range(max_iter):
        mid = (lo + hi) / 2.0
        f_mid = _npv(mid)
        if abs(f_mid) < tol or (hi - lo) < tol:
            return mid
        if f_lo * f_mid <= 0:
            hi, f_hi = mid, f_mid
        else:
            lo, f_lo = mid, f_mid
    return (lo + hi) / 2.0


def _scen_loan_balances(loan, int_rt, amrt_rt, io_years, loan_type, hold_yrs):
    """
    Return list of dicts {yr, ds, balance_end} for each year of the hold period.
    Handles IO period and both Annuity / Linear amortisation types.
    """
    balance = float(loan)
    schedule = []
    for yr in range(1, hold_yrs + 1):
        interest = balance * int_rt
        if yr <= io_years:
            amort = 0.0
        elif loan_type == 'Annuity':
            ann_pmt = loan * (int_rt + amrt_rt)   # constant payment on original balance
            amort = max(0.0, ann_pmt - interest)
        else:                                       # Linear
            amort = loan * amrt_rt
        amort    = min(amort, balance)
        balance  = max(0.0, balance - amort)
        schedule.append({'yr': yr, 'ds': interest + amort, 'balance': balance})
    return schedule


def _scen_irr(equity, noi_steady, noi_yr1, cpi, loan, int_rt, amrt_rt,
              io_years, loan_type, exit_cap, hold_yrs=10):
    """Compute equity IRR for one scenario. Returns float or None."""
    if equity <= 0 or exit_cap <= 0:
        return None
    sched  = _scen_loan_balances(loan, int_rt, amrt_rt, io_years, loan_type, hold_yrs)
    flows  = [-float(equity)]
    for row in sched:
        yr   = row['yr']
        # Year 1 may use a different NOI (e.g. rent-free period in upside)
        base = noi_yr1 if yr == 1 else noi_steady
        yr_noi     = base * (1.0 + cpi) ** (yr - 1)
        equity_cf  = yr_noi - row['ds']
        if yr == hold_yrs:
            terminal_noi = noi_steady * (1.0 + cpi) ** hold_yrs
            exit_val     = terminal_noi / exit_cap
            equity_cf   += (exit_val - row['balance'])
        flows.append(equity_cf)
    return _scen_irr_bisect(flows)


# Stress severity parameter table
_SCEN_SEVERITY = {
    1: {'label': 'Mild',   'cpi': 0.015, 'cap_widen': 0.0025, 'int_add': 0.000,
        'anchor_dark': False, 'anchor_factor': 0.85},
    2: {'label': 'Base',   'cpi': 0.010, 'cap_widen': 0.0075, 'int_add': 0.005,
        'anchor_dark': True,  'anchor_factor': 0.00},
    3: {'label': 'Severe', 'cpi': 0.005, 'cap_widen': 0.0150, 'int_add': 0.010,
        'anchor_dark': True,  'anchor_factor': 0.00},
}


def _scen_verdict(dscr, icr, ltv):
    """Simple threshold-based verdict for scenario metrics."""
    if (ltv  and ltv  > 0.70) or (dscr and dscr < 1.20) or (icr and icr < 1.50):
        return 'DECLINE'
    if (ltv  and ltv  > 0.65) or (dscr and dscr < 1.30) or (icr and icr < 1.75):
        return 'INVESTIGATE'
    return 'PASS'


def _di_scenario_engine(
    metrics: dict, rr: pd.DataFrame, deal: dict,
    noi: float, loan: float, int_rt: float, amrt_rt: float,
    io_years: int, loan_type_key: str, cpi_indexation: float,
    stress_severity: int = 2,
    hold_yrs: int = 10,
) -> dict:
    """
    Run Base / Stress / Upside scenarios and return comparison metrics.

    All three scenarios derive from the already-computed deal data — no
    external sources required.

    Returns dict with keys:
      base, stress, upside  — each {noi, gross_rent, dscr, icr, ltv, giy, irr, verdict}
      anchor_name, anchor_rent  — the tenant stressed in the stress scenario
      summary   — {base, stress, upside}  plain-English sentence per scenario
      sev_label — stress severity label
    """
    sev        = _SCEN_SEVERITY.get(stress_severity, _SCEN_SEVERITY[2])
    asking     = float(metrics.get('asking_price', 0) or 0)
    equity     = float(metrics.get('equity', 0) or 0)
    gross_rent = float(metrics.get('gross_passing_rent', 0) or 0)
    void_costs = float(metrics.get('void_costs', 0) or 0)
    entry_giy  = float(metrics.get('giy', 0) or 0.055)
    active     = rr[~rr['is_vacant']].copy() if not rr.empty else pd.DataFrame()
    vacant     = rr[rr['is_vacant']].copy()  if not rr.empty else pd.DataFrame()

    # ── Year-1 debt service (matches main engine) ──────────────────────────────
    y1_ds_base = loan * int_rt if io_years >= 1 else loan * (int_rt + amrt_rt)

    # ── BASE CASE ──────────────────────────────────────────────────────────────
    base_dscr = noi / y1_ds_base if y1_ds_base > 0 else None
    base_icr  = noi / (loan * int_rt) if (loan > 0 and int_rt > 0) else None
    base_ltv  = loan / asking if asking > 0 else None
    base_giy  = gross_rent / asking if asking > 0 else None
    base_irr  = _scen_irr(equity, noi, noi, cpi_indexation, loan, int_rt, amrt_rt,
                           io_years, loan_type_key, entry_giy, hold_yrs)
    base_scen = {
        'noi': noi, 'gross_rent': gross_rent,
        'dscr': base_dscr, 'icr': base_icr, 'ltv': base_ltv,
        'giy': base_giy, 'irr': base_irr,
        'verdict': _scen_verdict(base_dscr, base_icr, base_ltv),
    }

    # ── IDENTIFY ANCHOR TO STRESS ──────────────────────────────────────────────
    anchor_row  = None
    anchor_name = '(none)'
    anchor_rent = 0.0
    anchor_area = 0.0
    if not active.empty:
        _anc = (active[active['is_anchor'].astype(bool)]
                if 'is_anchor' in active.columns else pd.DataFrame())
        _pool = _anc if not _anc.empty else active
        anchor_row  = _pool.nlargest(1, 'annual_rent').iloc[0]
        anchor_name = str(anchor_row.get('tenant', '?'))
        anchor_rent = float(anchor_row.get('annual_rent', 0) or 0)
        anchor_area = float(anchor_row.get('area_sqm', 0) or 0)

    # ── STRESS CASE ────────────────────────────────────────────────────────────
    stress_int = int_rt + sev['int_add']
    if sev['anchor_dark']:
        # Anchor leaves: lose rent, gain void holding cost
        stress_noi        = noi - anchor_rent - anchor_area * VOID_HOLD_RATE
        stress_gross_rent = gross_rent - anchor_rent
    else:
        # Anchor renews at reduced rent
        _rent_cut         = anchor_rent * (1.0 - sev['anchor_factor'])
        stress_noi        = noi - _rent_cut
        stress_gross_rent = gross_rent - _rent_cut
    stress_y1_ds  = loan * stress_int if io_years >= 1 else loan * (stress_int + amrt_rt)
    stress_exit   = entry_giy + sev['cap_widen']
    stress_dscr   = stress_noi / stress_y1_ds if stress_y1_ds > 0 else None
    stress_icr    = stress_noi / (loan * stress_int) if (loan > 0 and stress_int > 0) else None
    stress_ltv    = base_ltv    # LTV is a purchase-date metric, unchanged
    stress_giy    = stress_gross_rent / asking if asking > 0 else None
    stress_irr    = _scen_irr(equity, stress_noi, stress_noi, sev['cpi'], loan, stress_int,
                               amrt_rt, io_years, loan_type_key, stress_exit, hold_yrs)
    stress_scen   = {
        'noi': stress_noi, 'gross_rent': stress_gross_rent,
        'dscr': stress_dscr, 'icr': stress_icr, 'ltv': stress_ltv,
        'giy': stress_giy, 'irr': stress_irr,
        'verdict': _scen_verdict(stress_dscr, stress_icr, stress_ltv),
    }

    # ── UPSIDE CASE ────────────────────────────────────────────────────────────
    # Voids let within 12 months with 1-year rent-free incentive:
    #   Year 1: void holding costs eliminated but no new rent received
    #   Year 2+: full new rent received (stabilised position)
    _yg           = _di_true_yield_gap(metrics, rr)
    void_erv      = float(_yg.get('erv_void_income', 0)) if _yg else 0.0
    upside_cpi    = 0.03
    upside_exit   = max(0.001, entry_giy - 0.0025)
    # Steady-state (Y2+): noi + new void rent + previously-deducted void holding costs
    upside_noi_steady = noi + void_erv + void_costs
    # Year 1: rent-free → no new rent yet, but void holding costs no longer paid
    upside_noi_yr1    = noi + void_costs
    upside_gross_rent = gross_rent + void_erv
    upside_y1_ds      = y1_ds_base                    # interest rate unchanged in upside
    upside_dscr       = upside_noi_steady / upside_y1_ds if upside_y1_ds > 0 else None
    upside_icr        = upside_noi_steady / (loan * int_rt) if (loan > 0 and int_rt > 0) else None
    upside_ltv        = base_ltv
    upside_giy        = upside_gross_rent / asking if asking > 0 else None
    upside_irr        = _scen_irr(equity, upside_noi_steady, upside_noi_yr1,
                                   upside_cpi, loan, int_rt, amrt_rt,
                                   io_years, loan_type_key, upside_exit, hold_yrs)
    upside_scen       = {
        'noi': upside_noi_steady, 'gross_rent': upside_gross_rent,
        'dscr': upside_dscr, 'icr': upside_icr, 'ltv': upside_ltv,
        'giy': upside_giy, 'irr': upside_irr,
        'verdict': _scen_verdict(upside_dscr, upside_icr, upside_ltv),
    }

    # ── Plain-English summaries ────────────────────────────────────────────────
    _anc_action = (
        f"{anchor_name} exits (space goes dark)" if sev['anchor_dark']
        else f"{anchor_name} renews at {sev['anchor_factor']:.0%} of current rent"
    )
    _stress_sum = (
        f"Stress ({sev['label']}): {_anc_action}, CPI drops to {sev['cpi']:.1%}, "
        f"exit cap widens {sev['cap_widen']*10000:.0f}bps, interest rate "
        f"+{sev['int_add']*100:.0f}bps — "
        + (f"DSCR falls to {stress_dscr:.2f}x: deal {'remains viable' if stress_dscr and stress_dscr >= 1.20 else 'DECLINE'}."
           if stress_dscr else "DSCR cannot be computed.")
    )
    # Simpler summary strings
    _base_sum = (
        f"Base case: current assumptions as entered — DSCR {base_dscr:.2f}x, "
        f"IRR {base_irr:.1%} (10yr), verdict {base_scen['verdict']}."
        if base_dscr and base_irr else
        f"Base case: DSCR {base_dscr:.2f}x, verdict {base_scen['verdict']}."
        if base_dscr else "Base case: loan or price data not entered."
    )
    _stress_sum = (
        f"Stress ({sev['label']}): {_anc_action}; CPI {sev['cpi']:.1%}, "
        f"cap +{sev['cap_widen']*10000:.0f}bps, rate +{sev['int_add']*100:.0f}bps — "
        f"DSCR {stress_dscr:.2f}x [{stress_scen['verdict']}]."
        if stress_dscr else
        f"Stress ({sev['label']}): {_anc_action} — stress metrics cannot be computed."
    )
    _upside_sum = (
        f"Upside: {vacant['area_sqm'].sum() if not vacant.empty else 0:,.0f} m² void let "
        f"(1-yr rent-free), CPI 3.0%, cap tightens 25bps — "
        f"stabilised DSCR {upside_dscr:.2f}x, IRR {upside_irr:.1%} [{upside_scen['verdict']}]."
        if upside_dscr and upside_irr else
        f"Upside: void fill adds EUR {void_erv:,.0f}/yr at stabilisation "
        f"[DSCR {upside_dscr:.2f}x]."
        if upside_dscr else "Upside: insufficient data for full projection."
    )

    return {
        'base':         base_scen,
        'stress':       stress_scen,
        'upside':       upside_scen,
        'anchor_name':  anchor_name,
        'anchor_rent':  anchor_rent,
        'sev_label':    sev['label'],
        'void_erv':     void_erv,
        'summary': {
            'base':   _base_sum,
            'stress': _stress_sum,
            'upside': _upside_sum,
        },
    }


def main():
    st.markdown("""
    <div style="background:linear-gradient(135deg,#1e3a5f,#2d6a9f);
                padding:22px 30px; border-radius:10px; margin-bottom:22px;">
      <h1 style="color:white;margin:0;font-size:1.55rem;font-weight:700;">
        German Retail Underwriter
      </h1>
      <p style="color:#93c5fd;margin:4px 0 0;font-size:0.8rem;letter-spacing:.08em;">
        INVESTMENT COMMITTEE GRADE  v1.2
      </p>
    </div>
    """, unsafe_allow_html=True)

    # ------------------------------------------------------------------
    # SIDEBAR
    # ------------------------------------------------------------------
    with st.sidebar:
        st.markdown("### Data Import")
        uploaded_file = st.file_uploader(
            "Upload populated Excel template", type=["xlsx"],
            help="Use DE_Retail_Underwriter_Template.xlsx"
        )

        # ── PRACTICE MODE ─────────────────────────────────────────────
        st.divider()
        st.markdown("### 🎓 Practice Mode")
        _pd_dir = pathlib.Path(__file__).parent / "practice_deals"
        _pd_files = sorted(_pd_dir.glob("*.xlsx")) if _pd_dir.exists() else []
        if _pd_files:
            # Build display names: "01 · PASS · Textbook NVZ — Edeka + dm Augsburg"
            def _pd_label(p):
                parts = p.stem.split("_", 3)   # ['Practice','01','BEGINNER','PASS_Title...']
                num     = parts[1] if len(parts) > 1 else "?"
                verdict = parts[3].split("_")[0] if len(parts) > 3 else "?"
                title   = "_".join(parts[3].split("_")[1:]).replace("_", " ") if len(parts) > 3 else p.stem
                return f"{num} · {verdict} · {title[:45]}"

            _pd_labels  = ["— select a deal —"] + [_pd_label(f) for f in _pd_files]
            _pd_sel     = st.selectbox("Load practice deal", _pd_labels, key="_pd_select",
                                       label_visibility="collapsed")

            _col_load, _col_clear = st.columns(2)
            if _col_load.button("▶ Load", key="_pd_load", use_container_width=True):
                if _pd_sel != "— select a deal —":
                    _pd_idx = _pd_labels.index(_pd_sel) - 1
                    with open(_pd_files[_pd_idx], "rb") as _pf:
                        st.session_state["_practice_bytes"] = _pf.read()
                        st.session_state["_practice_label"] = _pd_sel
                    st.rerun()
            if _col_clear.button("✕ Clear", key="_pd_clear", use_container_width=True):
                st.session_state.pop("_practice_bytes", None)
                st.session_state.pop("_practice_label", None)
                st.rerun()

            if st.session_state.get("_practice_label") and not uploaded_file:
                st.success(f"📂 {st.session_state['_practice_label'][:50]}")
        else:
            st.caption("No practice deals found. Run `practice_deals_100.py` first.")

        st.divider()
        st.markdown("### Model Assumptions")

        # Only show slider when Cost Sheet is not populated
        # When Cost Sheet is active it is greyed out and labelled clearly
        _cs_active = False  # evaluated after file upload; placeholder here
        st.caption("NOI MARGIN (only used if Cost Sheet is empty)")
        noi_margin_slider = st.slider(
            "NOI Margin (% of gross rent)", 60, 95, 80,
            help="Fachmarkt: ~90%. Shopping Centre: ~78%. High-street: ~83%."
        ) / 100

        st.caption("MARKET RENT BENCHMARK")
        market_rent_override = st.number_input(
            "Asset-Wide Market Rent (€/m²/mo)",
            min_value=0.0, max_value=50.0, value=0.0, step=0.5,
            help=(
                "Enter the market rent per m²/month for this asset type and location. "
                "Used to calculate reversion risk where per-tenant market rent is not in the Excel. "
                "Leave at 0 if market rent is entered per-tenant in the Rent Roll tab."
            ),
        )
        market_rent_override = market_rent_override if market_rent_override > 0 else None

        st.caption("DEBT STRUCTURE")
        loan_type = st.radio(
            "Loan Amortisation Type",
            ["Annuity (Annuitaetendarlehen)", "Linear (Lineare Tilgung)"],
            index=0,
            help="Annuity = fixed total payment. Linear = fixed principal, declining total.",
        )
        loan_type_key = "Annuity" if "Annuity" in loan_type else "Linear"

        io_years = 0  # IO period removed — always full amortising debt service

        # Practice deal fallback — treat stored bytes as if user uploaded the file
        if uploaded_file is None and st.session_state.get("_practice_bytes"):
            uploaded_file = io.BytesIO(st.session_state["_practice_bytes"])

        # Reset share deal toggle and parser cache whenever file content changes
        if uploaded_file is not None:
            import hashlib as _hashlib
            _file_bytes = uploaded_file.getvalue()
            _curr_fid   = _hashlib.md5(_file_bytes).hexdigest()
            uploaded_file.seek(0)
            if st.session_state.get("_last_uploaded_fid") != _curr_fid:
                st.session_state["_last_uploaded_fid"] = _curr_fid
                st.session_state.pop("share_deal_toggle", None)
                st.cache_data.clear()

        st.caption("DEAL STRUCTURE")
        share_deal = st.toggle(
            "Share Deal (Anteilskauf)",
            key="share_deal_toggle",
            value=False,
            help="Share deal avoids GrESt by acquiring company shares below 90% "
                 "threshold. Reduces acquisition cost but requires full corporate DD."
        )

        st.caption("EXIT & GROWTH")
        base_exit_cap = st.slider(
            "Base Exit Cap Rate (%)", 4.0, 10.0, 6.5, 0.1,
            help="2026 German retail range: 5.5%-9%+"
        ) / 100
        cpi_indexation = st.slider(
            "Annual Rent Indexation (%)", 0.0, 5.0, 2.0, 0.1,
            help="CPI pass-through applied each year."
        ) / 100

        st.divider()
        st.markdown("### AI Updates")
        if st.button("🤖 Check AI Updates", help="Search for new Anthropic/Claude releases and AI real-estate tools relevant to GRU."):
            with st.spinner("Searching for AI updates..."):
                try:
                    import subprocess as _sp
                    _ai_mon_path = str(pathlib.Path(__file__).parent / "ai_monitor.py")
                    _proc = _sp.run(
                        ["python", _ai_mon_path, "--quiet", "--force"],
                        capture_output=True, text=True, timeout=120,
                    )
                    if _proc.returncode != 0 and _proc.stderr:
                        st.session_state["_ai_update_error"] = _proc.stderr[-800:]
                    else:
                        st.session_state["_ai_update_error"] = None
                    st.session_state["_ai_update_ran"] = True
                except Exception as _aie:
                    st.session_state["_ai_update_error"] = str(_aie)
                    st.session_state["_ai_update_ran"] = True

        if st.session_state.get("_ai_update_ran"):
            _err = st.session_state.get("_ai_update_error")
            if _err:
                st.error(f"AI monitor error: {_err[:300]}")
            else:
                try:
                    import importlib.util as _ilu
                    _am_spec = _ilu.spec_from_file_location(
                        "ai_monitor",
                        str(pathlib.Path(__file__).parent / "ai_monitor.py"),
                    )
                    _am_mod = _ilu.module_from_spec(_am_spec)
                    _am_spec.loader.exec_module(_am_mod)
                    _latest_report = _am_mod.read_latest()
                except Exception:
                    _ai_upd_dir = pathlib.Path(__file__).parent / "ai_updates" / "latest.txt"
                    _latest_report = _ai_upd_dir.read_text(encoding="utf-8") if _ai_upd_dir.exists() else ""
                if _latest_report:
                    with st.expander("Latest AI Update Report", expanded=True):
                        st.text(_latest_report[:4000])
                        if len(_latest_report) > 4000:
                            st.caption("(truncated — open ai_updates/latest.txt for full report)")
                else:
                    st.info("No report found. Check ai_updates/ folder.")

        st.divider()
        st.markdown("### 🏢 Deal Scanner")
        st.caption("Search ImmoScout24 & Immowelt for qualifying retail listings.")
        _ds_col1, _ds_col2 = st.columns(2)
        with _ds_col1:
            if st.button("🔍 Scan now", key="_ds_scan_btn", use_container_width=True,
                         help="Scrape listings, filter, score, pre-populate templates."):
                with st.spinner("Scanning listings..."):
                    try:
                        import importlib.util as _ds_ilu
                        _ds_spec = _ds_ilu.spec_from_file_location(
                            "deal_scanner",
                            str(pathlib.Path(__file__).parent / "deal_scanner.py"),
                        )
                        _ds_mod = _ds_ilu.module_from_spec(_ds_spec)
                        _ds_spec.loader.exec_module(_ds_mod)
                        _ds_mod.run(force=True, quiet=True)
                        st.session_state["_ds_ran"]   = True
                        st.session_state["_ds_error"] = None
                        st.toast("Scan complete.", icon="✅")
                    except Exception as _dse:
                        st.session_state["_ds_ran"]   = True
                        st.session_state["_ds_error"] = str(_dse)
        with _ds_col2:
            if st.button("📋 View results", key="_ds_view_btn", use_container_width=True):
                st.session_state["_ds_show"] = not st.session_state.get("_ds_show", False)

        if st.session_state.get("_ds_error"):
            st.error(f"Scanner error: {st.session_state['_ds_error'][:200]}")

        if st.session_state.get("_ds_show", False):
            try:
                import importlib.util as _ds_ilu2
                _ds_spec2 = _ds_ilu2.spec_from_file_location(
                    "deal_scanner",
                    str(pathlib.Path(__file__).parent / "deal_scanner.py"),
                )
                _ds_mod2 = _ds_ilu2.module_from_spec(_ds_spec2)
                _ds_spec2.loader.exec_module(_ds_mod2)
                _ds_data = _ds_mod2.read_latest()

                if not _ds_data:
                    st.info("No scan results yet. Click 'Scan now' first.")
                else:
                    st.markdown(
                        f"**{_ds_data['total_found']}** found · "
                        f"**{_ds_data['total_passed']}** passing filter · "
                        f"scanned {_ds_data.get('timestamp','?')}",
                    )
                    if _ds_data.get("errors"):
                        with st.expander(f"⚠️ {len(_ds_data['errors'])} scan warning(s)"):
                            for _dserr in _ds_data["errors"]:
                                st.caption(_dserr)
                    if _ds_data.get("top5"):
                        st.markdown("**Top qualifying deals:**")
                        import html as _ds_html
                        for _dsd in _ds_data["top5"]:
                            _dsp = (
                                f"EUR {_dsd['asking_price']:,.0f}"
                                if _dsd.get("asking_price") else "n/a"
                            )
                            _dsg = (
                                f"{_dsd['giy_stated']:.1%}"
                                if _dsd.get("giy_stated") else "n/a"
                            )
                            _dskw  = ", ".join(_dsd.get("keywords", [])[:2]) or "—"
                            _dsurl = _dsd.get("url", "")
                            _dsxls = _dsd.get("excel_path")
                            _dstit = _ds_html.escape(str(_dsd.get("title", ""))[:50])
                            _dscit = _ds_html.escape(str(_dsd.get("city", "?")))
                            if _dsxls:
                                _dsxls_fwd = _dsxls.replace("\\", "/")
                                _xls_link  = f'<br>&#128206; <a href="file:///{_dsxls_fwd}" target="_blank">Open Excel</a>'
                            else:
                                _xls_link = ""
                            _url_link = (
                                f'<br>&#128279; <a href="{_dsurl}" target="_blank">Listing</a>'
                                if _dsurl else ""
                            )
                            st.markdown(
                                '<div style="background:#f0f9ff;border-left:3px solid #0284c7;'
                                'padding:7px 9px;border-radius:4px;font-size:0.78em;margin-bottom:5px">'
                                f'<b>[{_dsd["score"]}/100]</b> {_dstit}<br>'
                                f'{_dscit} &middot; {_dsp} &middot; GIY {_dsg}<br>'
                                f'<i>{_dskw}</i>'
                                f'{_xls_link}{_url_link}'
                                '</div>',
                                unsafe_allow_html=True,
                            )
                    else:
                        st.info(
                            "No listings passed the filter. Likely cause: "
                            "ImmoScout24/Immowelt use JavaScript rendering — "
                            "plain HTTP returned no listing data. "
                            "Check deal_pipeline/deals_latest.json for full report."
                        )
            except Exception as _dse2:
                st.error(f"Results display error: {_dse2}")

    if not uploaded_file:
        st.info("Upload your populated `DE_Retail_Underwriter_Template.xlsx` to begin — or load a practice deal from the sidebar.")
        st.markdown("""
        **This tool calculates:**  DSCR · ICR · LTV · GIY · NIY · WAULT ·
        Anchor concentration · 10-year levered IRR · Exit cap sensitivity heatmap ·
        Itemised NOI from Cost Sheet · Pass / Investigate / Decline verdict
        """)

        # ── BATCH PRACTICE RUN ─────────────────────────────────────────
        _pd_dir2 = pathlib.Path(__file__).parent / "practice_deals"
        _pd_files2 = sorted(_pd_dir2.glob("*.xlsx")) if _pd_dir2.exists() else []
        if _pd_files2:
            with st.expander("📊 Run all practice deals — batch summary", expanded=False):
                st.caption(f"{len(_pd_files2)} deals · shows engine verdict and key metrics for each")
                if st.button("▶ Run all now", key="_pd_batch_run"):
                    _batch_rows = []
                    _prog = st.progress(0, text="Running…")
                    for _bi, _bf in enumerate(_pd_files2):
                        _prog.progress((_bi + 1) / len(_pd_files2), text=f"{_bf.stem[:40]}…")
                        try:
                            with open(_bf, "rb") as _bfh:
                                _bdata = io.BytesIO(_bfh.read())
                            _bd    = rrp.load_deal_overview(_bdata)
                            _brr   = rrp.load_rent_roll(_bdata)
                            _bact  = _brr[~_brr["is_vacant"]]
                            _bvac  = _brr[_brr["is_vacant"]]
                            _bcosts = rrp.load_cost_sheet(
                                _bdata,
                                rr_gross_rent  = float(_bact["annual_rent"].sum()),
                                rr_gla_total   = float(_brr["area_sqm"].sum()),
                                rr_vacant_area = float(_bvac["area_sqm"].sum()),
                            )
                            _bm  = rrp.compute_metrics(_bd, _brr, _bcosts)
                            _bqa = rrp.run_qa_audit(_bm, _bd)
                            _bv  = rrp.generate_verdict(_bm, _bqa)
                            _parts = _bf.stem.split("_", 3)
                            _batch_rows.append({
                                "#":       _parts[1] if len(_parts) > 1 else "?",
                                "Difficulty": _parts[2] if len(_parts) > 2 else "?",
                                "Verdict": _bv["recommendation"],
                                "Deal":    (_parts[3].split("_",1)[-1].replace("_"," ")[:40]
                                            if len(_parts) > 3 else _bf.stem[:40]),
                                "DSCR":   round(_bm.get("dscr") or 0, 2),
                                "LTV":    f"{(_bm.get('ltv') or 0):.0%}",
                                "WAULT":  round(_bm.get("wault") or 0, 1),
                                "GIY":    f"{(_bm.get('giy') or 0):.2%}",
                                "NIY":    f"{(_bm.get('niy') or 0):.2%}",
                                "Vac":    f"{(_bm.get('vacancy_rate') or 0):.0%}",
                                "Flags":  len([r for r in _bqa if r.status in ("FAIL","WARN")
                                               and r.severity in ("RED","YELLOW")]),
                            })
                        except Exception as _be:
                            _batch_rows.append({
                                "#": _bf.stem[9:11], "Difficulty": "?",
                                "Verdict": "ERROR", "Deal": str(_be)[:50],
                                "DSCR": 0, "LTV": "?", "WAULT": 0,
                                "GIY": "?", "NIY": "?", "Vac": "?", "Flags": 0,
                            })
                    _prog.empty()
                    _bdf = pd.DataFrame(_batch_rows)

                    def _colour_verdict(val):
                        c = {"PASS": "background-color:#d1fae5;color:#065f46",
                             "INVESTIGATE": "background-color:#fef3c7;color:#92400e",
                             "DECLINE": "background-color:#fee2e2;color:#991b1b",
                             "ERROR": "background-color:#f3f4f6;color:#6b7280"}.get(val, "")
                        return c

                    st.dataframe(
                        _bdf.style.applymap(_colour_verdict, subset=["Verdict"]),
                        use_container_width=True,
                        hide_index=True,
                    )
                    _pass = sum(1 for r in _batch_rows if r["Verdict"] == "PASS")
                    _inv  = sum(1 for r in _batch_rows if r["Verdict"] == "INVESTIGATE")
                    _dec  = sum(1 for r in _batch_rows if r["Verdict"] == "DECLINE")
                    _err  = sum(1 for r in _batch_rows if r["Verdict"] == "ERROR")
                    st.caption(f"✅ {_pass} PASS   ⚠️ {_inv} INVESTIGATE   ❌ {_dec} DECLINE"
                               + (f"   💥 {_err} ERROR" if _err else ""))

        return

    # ------------------------------------------------------------------
    # PARSE
    # ------------------------------------------------------------------
    try:
        deal    = rrp.load_deal_overview(uploaded_file)
        rr      = rrp.load_rent_roll(uploaded_file)
        # Compute rent roll aggregates first so Cost Sheet can resolve
        # cross-sheet formula links without LibreOffice recalc
        _rr_active  = rr[~rr["is_vacant"]]
        _rr_vacant  = rr[rr["is_vacant"]]
        costs   = rrp.load_cost_sheet(
            uploaded_file,
            rr_gross_rent  = float(_rr_active["annual_rent"].sum()),
            rr_gla_total   = float(rr["area_sqm"].sum()),
            rr_vacant_area = float(_rr_vacant["area_sqm"].sum()),
        )
        metrics = rrp.compute_metrics(deal, rr, costs, noi_margin_slider, market_rent_override)
        qa      = rrp.run_qa_audit(metrics, deal)
        verdict = rrp.generate_verdict(metrics, qa)
    except Exception as e:
        st.error(f"Failed to parse Excel file: {e}")
        with st.expander("Technical detail"):
            st.code(traceback.format_exc())
        return

    # FIX 6: Apply share deal adjustment to acquisition cost and equity
    if share_deal and deal.get("acq_cost_share_deal"):
        metrics["total_acq_cost"]  = deal["acq_cost_share_deal"]
        metrics["equity"]          = deal["acq_cost_share_deal"] - (metrics["loan_amount"] or 0)
        _grest_saving = deal.get("grest_saving") or 0
    else:
        metrics["total_acq_cost"]  = deal.get("acq_cost_asset_deal") or metrics["total_acq_cost"]
        _grest_saving = 0

    # Single source of truth for NOI used everywhere
    noi          = metrics["noi_proxy"]
    noi_margin   = metrics["noi_margin"]
    noi_source   = metrics.get("noi_source", "fallback_80pct")

    # FIX 2: Show or hide the NOI slider based on Cost Sheet availability
    # When Cost Sheet is active the slider has no effect — communicate this clearly
    _cs_populated = costs.get("available", False) and costs.get("noi", 0) > 0
    if _cs_populated:
        st.sidebar.caption(
            "NOI MARGIN — disabled: Cost Sheet is active\n"
            f"Current margin: {noi_margin:.0%} (from itemised costs)"
        )
    gross_rent   = metrics["gross_passing_rent"]

    # Recalculate NIY from the SAME noi (FIX 4)
    asking = metrics.get("asking_price", 0) or 0
    metrics["niy"] = noi / asking if asking > 0 else None

    # Recompute debt metrics consistently
    loan     = metrics.get("loan_amount", 0) or 0
    int_rt   = deal.get("interest_rate", 0) or 0
    amrt_rt  = deal.get("amort_rate", 0) or 0
    y1_interest = loan * int_rt
    y1_amort    = loan * amrt_rt
    y1_debt_svc = y1_interest + y1_amort
    y1_dscr     = noi / y1_debt_svc if y1_debt_svc > 0 else None
    y1_icr      = noi / y1_interest  if y1_interest  > 0 else None

    # Base IRR
    cf_df, base_irr = calculate_10y_projection(
        metrics, deal, io_years, base_exit_cap, cpi_indexation, noi, loan_type_key
    )

    # NOI source badge HTML
    badge_class = {"cost_sheet":"noi-badge-cs",
                   "slider_with_void_estimate":"noi-badge-sl"}.get(noi_source,"noi-badge-fb")
    badge_label = {"cost_sheet":"NOI: Itemised Cost Sheet",
                   "slider_with_void_estimate":"NOI: Slider + void estimate",
                   "fallback_80pct":"NOI: 80% fallback"}.get(noi_source, noi_source)
    noi_badge   = f'<span class="{badge_class}">{badge_label}</span>'

    # ------------------------------------------------------------------
    # CLAUDE ADVISOR + SESSION JOURNAL  (sidebar panels, deal-loaded only)
    # ------------------------------------------------------------------
    with st.sidebar:
        # ── Claude Advisor ──────────────────────────────────────────────
        st.divider()
        st.markdown("### 🧠 Claude Advisor")

        # Fingerprint so advice auto-refreshes when a new deal is loaded
        _cadv_fp = (
            f"{deal.get('property_name','')}_"
            f"{metrics.get('asking_price',0):.0f}_"
            f"{noi:.0f}"
        )
        _cadv_fp_changed = st.session_state.get("_cadv_fp") != _cadv_fp
        _cadv_refresh_clicked = st.button(
            "🔄 Refresh advice", key="_cadv_refresh",
            help="Ask Claude for a different set of observations on this deal.",
        )
        _cadv_do_gen = (
            _cadv_fp_changed
            or _cadv_refresh_clicked
            or "_cadv_text" not in st.session_state
        )

        if _cadv_do_gen:
            st.session_state["_cadv_fp"] = _cadv_fp
            with st.spinner("Consulting Claude..."):
                try:
                    import anthropic as _ant
                    import json as _cadv_json

                    # Build a clean, JSON-serialisable metrics payload
                    _cadv_payload = {
                        k: (round(v, 6) if isinstance(v, float) else v)
                        for k, v in metrics.items()
                        if v is not None and isinstance(v, (int, float, str, bool))
                    }
                    _cadv_payload.update({
                        "verdict":       verdict.get("recommendation", ""),
                        "property_name": deal.get("property_name", ""),
                        "city":          deal.get("city", ""),
                        "asset_type":    deal.get("asset_type", ""),
                        "year_built":    deal.get("year_built", ""),
                        "hold_period":   deal.get("hold_period", ""),
                    })

                    _cadv_client = _ant.Anthropic()
                    _cadv_resp   = _cadv_client.messages.create(
                        model="claude-haiku-4-5-20251001",
                        max_tokens=600,
                        system=(
                            "You are an expert German retail CRE underwriter. Analyse "
                            "the deal metrics provided and give exactly 5 short, sharp "
                            "observations — things the analyst should investigate, "
                            "challenge, or be aware of. Each observation maximum 2 "
                            "sentences. Be specific to the numbers, not generic. "
                            "Focus on risks and opportunities the metrics reveal that "
                            "a standard IC checklist would miss."
                        ),
                        messages=[{
                            "role": "user",
                            "content": (
                                "Deal metrics:\n"
                                + _cadv_json.dumps(_cadv_payload, indent=2)
                            ),
                        }],
                    )
                    st.session_state["_cadv_text"] = _cadv_resp.content[0].text
                    st.session_state["_cadv_err"]  = None
                except Exception as _cadv_exc:
                    _cadv_msg = str(_cadv_exc)
                    st.session_state["_cadv_text"] = ""
                    st.session_state["_cadv_err"]  = _cadv_msg

        _cadv_err  = st.session_state.get("_cadv_err")
        _cadv_text = st.session_state.get("_cadv_text", "")

        if _cadv_err:
            if "auth" in _cadv_err.lower() or "api_key" in _cadv_err.lower() or "authentication" in _cadv_err.lower():
                st.warning(
                    "Set the **ANTHROPIC_API_KEY** environment variable "
                    "to enable Claude Advisor.",
                    icon="🔑",
                )
            else:
                st.error(f"Claude Advisor error: {_cadv_err[:200]}")
        elif _cadv_text:
            import html as _html_mod
            _cadv_safe = _html_mod.escape(_cadv_text).replace("\n", "<br>")
            st.markdown(
                f'<div style="background:#e8f4fd;border-left:3px solid #1976d2;'
                f'padding:10px 12px;border-radius:4px;font-size:0.82em;line-height:1.6">'
                f'{_cadv_safe}</div>',
                unsafe_allow_html=True,
            )

        # ── Session Journal ─────────────────────────────────────────────
        st.divider()
        st.markdown("### 📓 Session Journal")

        _sj_note = st.text_input(
            "Session note",
            key="_sj_note_input",
            placeholder="e.g. Discussed with Lahav, awaiting DD",
            label_visibility="collapsed",
        )

        _sj_c1, _sj_c2 = st.columns(2)
        with _sj_c1:
            if st.button("💾 Log session", key="_sj_log_btn", use_container_width=True):
                try:
                    import importlib.util as _sj_ilu
                    _sj_spec = _sj_ilu.spec_from_file_location(
                        "session_journal",
                        str(pathlib.Path(__file__).parent / "session_journal.py"),
                    )
                    _sj_mod = _sj_ilu.module_from_spec(_sj_spec)
                    _sj_spec.loader.exec_module(_sj_mod)

                    # Collect any RI signals stored in session state
                    _sj_ri = {}
                    for _ssk, _ssv in st.session_state.items():
                        if (
                            isinstance(_ssk, str)
                            and _ssk.startswith("ri_state_")
                            and isinstance(_ssv, dict)
                        ):
                            for _rtk, _rtv in _ssv.items():
                                if _rtk.startswith("ri_sig_"):
                                    _sj_ri[_rtk[7:]] = _rtv

                    _sj_path = _sj_mod.log_entry(
                        deal_name=deal.get("property_name", "Unknown"),
                        verdict=verdict.get("recommendation", ""),
                        metrics=metrics,
                        ri_signals=_sj_ri,
                        note=_sj_note,
                    )
                    st.toast(f"Logged to {_sj_path.name}", icon="✅")
                except Exception as _sje:
                    st.error(f"Journal error: {_sje}")

        with _sj_c2:
            if st.button("📖 View journal", key="_sj_view_btn", use_container_width=True):
                st.session_state["_sj_show"] = not st.session_state.get("_sj_show", False)

        if st.session_state.get("_sj_show", False):
            try:
                import importlib.util as _sj_ilu2
                _sj_spec2 = _sj_ilu2.spec_from_file_location(
                    "session_journal",
                    str(pathlib.Path(__file__).parent / "session_journal.py"),
                )
                _sj_mod2 = _sj_ilu2.module_from_spec(_sj_spec2)
                _sj_spec2.loader.exec_module(_sj_mod2)
                _sj_entries = _sj_mod2.read_recent(n=10)
                if not _sj_entries:
                    st.info("No journal entries yet.")
                else:
                    for _sje2 in reversed(_sj_entries):
                        _sj_ts  = str(_sje2.get("timestamp", ""))[:16]
                        _sj_nm  = _sje2.get("deal_name", "?")
                        _sj_vd  = _sje2.get("verdict", "?")
                        _sj_nt  = _sje2.get("note", "")
                        _sj_m   = _sje2.get("metrics", {})
                        _sj_bg  = {
                            "PASS":        "#d1fae5",
                            "INVESTIGATE": "#fef3c7",
                            "DECLINE":     "#fee2e2",
                        }.get(_sj_vd, "#f3f4f6")
                        _sj_dscr = _sje2.get("metrics", {}).get("dscr")
                        _sj_giy  = _sje2.get("metrics", {}).get("giy")
                        _sj_sub  = (
                            f"DSCR {_sj_dscr:.2f}x  |  GIY {_sj_giy:.1%}"
                            if _sj_dscr is not None and _sj_giy is not None
                            else ""
                        )
                        import html as _sj_html
                        st.markdown(
                            f'<div style="background:{_sj_bg};padding:6px 8px;'
                            f'border-radius:4px;font-size:0.78em;margin-bottom:4px">'
                            f'<b>{_sj_ts}</b>  {_sj_html.escape(_sj_nm)}<br>'
                            f'<b>{_sj_vd}</b>'
                            f'{("  " + _sj_sub) if _sj_sub else ""}'
                            f'{("<br><i>" + _sj_html.escape(_sj_nt) + "</i>") if _sj_nt else ""}'
                            f'</div>',
                            unsafe_allow_html=True,
                        )
            except Exception as _sje3:
                st.error(f"Journal read error: {_sje3}")

    # COST RECOVERABILITY — session state (persists while deal is loaded)
    # ------------------------------------------------------------------
    _recov_sk_prefix = f"recov_{(deal.get('property_name') or 'deal').replace(' ', '_')}"
    _cs_cost_items   = costs.get("costs", {}) if costs else {}
    for _rck in _cs_cost_items:
        _rck_key = f"{_recov_sk_prefix}_{_rck}"
        if _rck_key not in st.session_state:
            st.session_state[_rck_key] = False

    # ------------------------------------------------------------------
    # TABS  (v2 \u2014 simplified: Scorecard / Banker View / Investor View / Advanced)
    # ------------------------------------------------------------------
    tab_score, tab_banker, tab_investor, tab_adv = st.tabs([
        "\U0001f4ca Scorecard", "\U0001f3e6 Banker View", "\U0001f4b0 Investor View", "\U0001f527 Advanced",
    ])

    # Sub-tabs live inside Advanced; their with-blocks below add content.
    with tab_adv:
        tab1, tab_bid, tab_vc, tab_port, tab4, tab5, tab_dd, tab_di, tab_dash = st.tabs([
            "Deal Pack", "Bid Price \U0001f3af", "Value Creation \u2728",
            "\U0001f4ca Portfolio", "Glossary", "Export",
            "\U0001f50d DD Scanner", "\U0001f52c Deep Insights", "\U0001f4ca Decision Dashboard",
        ])

    # ------------------------------------------------------------------ SCORECARD
    with tab_score:
        _prop = deal.get("property_name") or "Unnamed Asset"
        st.subheader(f"\U0001f4ca {_prop} — Banker Scorecard")
        st.markdown(noi_badge, unsafe_allow_html=True)
        st.markdown("")

        # ── Verdict banner
        _rec  = verdict.get("recommendation", "INVESTIGATE")
        _vcls = {"PASS": "verdict-pass", "DECLINE": "verdict-dec"}.get(_rec, "verdict-inv")
        _icon = {"PASS": "✅", "DECLINE": "❌"}.get(_rec, "⚠️")
        _summary = verdict.get("rationale") or "Review details in Banker View and Investor View tabs."
        st.markdown(
            f'<div class="{_vcls}"><b>{_icon} {_rec}</b> — {_summary}</div>',
            unsafe_allow_html=True,
        )
        st.markdown("")

        # ── Row 1: 5 key metrics (4 banker + NIY)
        s1, s2, s3, s4, s5 = st.columns(5)

        _ltv    = metrics.get("ltv", 0) or 0
        _ltv_ok = _ltv <= 0.70
        s1.metric("LTV", f"{_ltv:.1%}",
                  delta="✅ ≤70%" if _ltv_ok else "❌ >70%",
                  delta_color="normal" if _ltv_ok else "inverse",
                  help="Loan ÷ Asset Value. Bank max 70–75%. Above 65% → senior debt gets harder.")

        _dscr    = y1_dscr or 0
        _dscr_ok = _dscr >= 1.25
        s2.metric("DSCR", f"{_dscr:.2f}x" if _dscr else "—",
                  delta="✅ ≥1.25x" if _dscr_ok else "❌ <1.25x",
                  delta_color="normal" if _dscr_ok else "inverse",
                  help="NOI ÷ Annual Debt Service. Bank floor: 1.25x. Best-in-class: 1.30x+.")

        _dy    = metrics.get("debt_yield") or 0
        _dy_ok = _dy >= 0.075
        s3.metric("Debt Yield", f"{_dy:.1%}" if _dy else "—",
                  delta="✅ ≥7.5%" if _dy_ok else "❌ <7.5%",
                  delta_color="normal" if _dy_ok else "inverse",
                  help="NOI ÷ Loan Amount. German lenders require ≥7.5–8.0%.")

        _wault    = metrics.get("wault") or 0
        _wault_ok = _wault >= 4.0
        s4.metric("WAULT", f"{_wault:.1f} yrs",
                  delta="✅ ≥4 yrs" if _wault_ok else "❌ <4 yrs",
                  delta_color="normal" if _wault_ok else "inverse",
                  help="Weighted Average Unexpired Lease Term. Bank minimum for senior debt: 4 yrs.")

        _niy    = metrics.get("niy") or 0
        _niy_ok = _niy >= 0.055
        _niy_high = _niy > 0.10  # >10% = market pricing in risk
        if _niy_high:
            _niy_delta = "⚠️ >10% — distress risk"
            _niy_color = "off"      # amber / neutral
        elif _niy_ok:
            _niy_delta = "✅ ≥5.5%"
            _niy_color = "normal"
        else:
            _niy_delta = "❌ <5.5%"
            _niy_color = "inverse"
        s5.metric("NIY", f"{_niy:.2%}" if _niy else "—",
                  delta=_niy_delta,
                  delta_color=_niy_color,
                  help="Net Initial Yield: NOI ÷ Asking Price — what you actually earn after costs. "
                       "Prime German retail: 5.0–5.5%. Secondary: 6.5–8.5%. "
                       "NIY >10% usually means a problem the market has already priced in.")

        # ── Row 2: Rent vs Market + Refi Stress
        st.markdown("")
        r1, r2 = st.columns(2)

        with r1:
            _passing_sqm = metrics.get("wtd_rent_per_sqm_mo") or 0
            _mkt_sqm     = market_rent_override or deal.get("market_rent_per_sqm_mo") or 0
            if _passing_sqm > 0 and _mkt_sqm > 0:
                _rent_diff = (_passing_sqm - _mkt_sqm) / _mkt_sqm
                if _rent_diff > 0.15:
                    st.warning(
                        f"⚠️ **Over-rented:** Passing rent €{_passing_sqm:.2f}/m²/mo is "
                        f"**{_rent_diff:.0%} above** market (€{_mkt_sqm:.2f}). "
                        "Risk of rent drop at expiry — tenants may not renew at current levels."
                    )
                elif _rent_diff < -0.15:
                    st.info(
                        f"📈 **Under-rented:** Passing rent €{_passing_sqm:.2f}/m²/mo is "
                        f"**{abs(_rent_diff):.0%} below** market (€{_mkt_sqm:.2f}). "
                        "Reversion upside at renewal — value-add potential."
                    )
                else:
                    st.success(
                        f"✅ **Rent at market:** €{_passing_sqm:.2f}/m²/mo vs "
                        f"market €{_mkt_sqm:.2f} ({_rent_diff:+.0%}). Stable base."
                    )
            else:
                st.caption("ℹ️ Enter Asset-Wide Market Rent in sidebar to see Rent vs. Market comparison.")

        with r2:
            try:
                _refi_rows, _refi_bal, _refi_noi = calculate_refi_stress(
                    metrics, deal, io_years, noi, loan_type_key
                )
                if _refi_rows:
                    _r100 = next((r for r in _refi_rows if "+100bps" in r["Cap Rate Shift"]), None)
                    if _r100:
                        _ltv_r    = _r100["_ltv_raw"]
                        _ltv_r_ok = _ltv_r <= 0.65
                        st.metric(
                            "LTV at Refi +100bps (Yr 5)",
                            f"{_ltv_r:.1%}",
                            delta="✅ ≤65% safe" if _ltv_r_ok else "❌ >65% — equity top-up needed",
                            delta_color="normal" if _ltv_r_ok else "inverse",
                            help="If cap rates rise 100bps at your refinancing year, does LTV stay below 65%? "
                                 "A bank will require equity injection to cure an LTV breach at maturity."
                        )
            except Exception:
                st.caption("ℹ️ Enter debt structure to unlock refi stress.")

        # ── Pass/Fail summary
        st.markdown("")
        _n_pass  = sum([_ltv_ok, _dscr_ok, _dy_ok, _wault_ok, _niy_ok])
        _n_total = 5
        if _n_pass == _n_total:
            st.success(f"✅ All {_n_total} criteria met — financeable and priced correctly.")
        elif _n_pass >= 3:
            st.warning(f"⚠️ {_n_total - _n_pass} of {_n_total} criteria not met. See 🏦 Banker View for detail.")
        else:
            st.error(f"❌ Only {_n_pass} of {_n_total} criteria met — significant obstacles to financing.")

        # ── Mentor Panel
        st.divider()
        st.markdown("##### 🎓 Expert Observations")
        _mentor_notes = []

        if _dscr > 3.0:
            _mentor_notes.append(
                f"**DSCR of {_dscr:.2f}x is unusually high.** Verify the rent roll is real — "
                "very high coverage ratios can reflect inflated headline rents or under-structured debt. "
                "Also ask: why is the seller selling a deal with this much cash flow?"
            )
        if _niy > 0.10:
            _mentor_notes.append(
                f"**NIY of {_niy:.1%} is well above market.** High yields are a signal, not a gift — "
                "the market has already priced in a risk. Identify exactly what it is: "
                "short WAULT, high vacancy, weak location, or structural issues."
            )
        if _wault < 2.0:
            _mentor_notes.append(
                f"**WAULT of {_wault:.1f} years: this deal cannot be financed today.** "
                "Before spending anything on DD, get signed heads of terms for a lease extension "
                "from the anchor. Without that, you are paying for information you cannot act on."
            )
        elif _wault < 4.0:
            _mentor_notes.append(
                f"**WAULT of {_wault:.1f} years — marginal for standard bank debt.** "
                "pbb and Berlin Hyp typically require 4+ years. "
                "Factor a higher cost of capital (mezzanine or bridge) into your return model."
            )
        _vac = metrics.get("vacancy_rate") or 0
        if _vac > 0.15:
            _mentor_notes.append(
                f"**Vacancy at {_vac:.1%} — model the void costs explicitly.** "
                "Grundsteuer, insurance and service charge on empty units are all landlord-borne. "
                "Also budget 6–18 months re-letting timeline for German retail units."
            )
        _exp3 = metrics.get("expiry_risk_3yr_pct") or 0
        if _exp3 > 0.50:
            _mentor_notes.append(
                f"**{_exp3:.0%} of leases expire within 3 years — build a stress scenario.** "
                "Model what happens if 30% of the rent roll goes vacant at Year 3. "
                "That is your real downside, not just the base case."
            )
        _anch = metrics.get("anchor_pct") or 0
        if _anch > 0.55:
            _mentor_notes.append(
                f"**Anchor concentration at {_anch:.1%} — single-event risk.** "
                "Run a stressed valuation with anchor rent at zero and a 24-month void. "
                "That is your worst-case asset value and the bank will model it."
            )

        if not _mentor_notes:
            st.caption("No significant concerns flagged. Proceed with standard due diligence.")
        else:
            for _note in _mentor_notes:
                st.markdown(f"- {_note}")

        st.divider()
        st.caption(
            "Full covenant analysis → 🏦 Banker View tab  |  "
            "Return metrics → 💰 Investor View tab  |  "
            "Rent roll & deal detail → 🔧 Advanced → Deal Pack"
        )

    # ------------------------------------------------------------------ TAB 1
    with tab1:
        prop = deal.get("property_name") or "Unnamed Asset"
        st.subheader(f"{prop} -- Deal Pack")
        st.markdown(noi_badge, unsafe_allow_html=True)
        st.markdown("")

        k1,k2,k3,k3b,k4,k5,k6 = st.columns(7)
        _asking      = metrics.get("asking_price") or 0
        _total_acq   = metrics.get("total_acq_cost") or 0
        _loan_amt    = metrics.get("loan_amount") or 0
        _ltv_pct     = metrics.get("ltv") or 0
        _equity_pct  = metrics["equity"] / _total_acq if _total_acq > 0 else 0
        _loan_acq_pct = _loan_amt / _total_acq if _total_acq > 0 else 0
        k1.metric("Asking Price",    f"EUR {metrics['asking_price']:,.0f}")
        _deal_type_label = "Total Acq. Cost (Share)" if share_deal else "Total Acq. Cost"
        _saving_delta    = (
            f"Share deal \u2014 saves EUR {_grest_saving:,.0f} vs asset deal"
            if share_deal and _grest_saving else None
        )
        k2.metric(_deal_type_label, f"EUR {_total_acq:,.0f}",
                  delta=_saving_delta, delta_color="normal" if share_deal else "off")
        k3.metric("Equity Required", f"EUR {metrics['equity']:,.0f}",
                  delta=f"{_equity_pct:.1%} of total acq. cost", delta_color="off")
        k3b.metric("Loan Amount",    f"EUR {_loan_amt:,.0f}",
                   delta=f"{_loan_acq_pct:.1%} of total acq. cost \u2014 LTV {_ltv_pct:.1%}", delta_color="off")
        k4.metric("Gross Rent / yr", f"EUR {gross_rent:,.0f}")
        k5.metric("NOI / yr",        f"EUR {noi:,.0f}",
                  delta=f"{noi_margin:.0%} margin")
        k6.metric("WAULT",           f"{metrics['wault']:.1f} yrs")

        st.divider()
        col_left, col_right = st.columns([1.6, 1])

        with col_left:
            st.markdown("#### Rent Roll")
            col_rename = {
                "unit_ref":      "Unit Ref",
                "tenant":        "Tenant Name",
                "sector":        "Trade Sector",
                "area_sqm":      "Area (m2)",
                "annual_rent":   "Annual Rent (EUR)",
                "unexpired_yrs": "Unexpired Term (Yrs)",
                "is_anchor":     "Anchor",
                "cpi_clause":    "CPI Indexed",
                "lease_expiry":  "Lease Expiry",
            }
            disp_cols = [c for c in col_rename if c in rr.columns]
            disp_df   = rr[disp_cols].rename(columns=col_rename).copy()
            # % of Rent column (before Annual Rent is stringified)
            if "Annual Rent (EUR)" in disp_df.columns and gross_rent > 0:
                _pct_col = disp_df["Annual Rent (EUR)"].apply(
                    lambda x: f"{float(x)/gross_rent*100:.1f}%" if pd.notna(x) and float(x) > 0 else "\u2014"
                )
                disp_df.insert(disp_df.columns.get_loc("Annual Rent (EUR)") + 1, "% of Rent", _pct_col)
            # Annual Rent: vacant units show "EUR 0 (void)"
            if "Annual Rent (EUR)" in disp_df.columns:
                _is_vac = rr["is_vacant"].values
                disp_df["Annual Rent (EUR)"] = [
                    "EUR 0 (void)" if _is_vac[i]
                    else (f"EUR {v:,.0f}" if pd.notna(v) else "--")
                    for i, v in enumerate(disp_df["Annual Rent (EUR)"])
                ]
            # Unexpired Term: vacant → "—", active with binding break → "X.Xyr (break: MMM YYYY)"
            if "Unexpired Term (Yrs)" in disp_df.columns:
                _uyr_vals      = rr["unexpired_yrs"].values
                _is_vac        = rr["is_vacant"].values
                _has_bopt      = "break_option" in rr.columns
                _has_expiry    = "lease_expiry" in rr.columns
                _bopt_vals     = rr["break_option"].values if _has_bopt else [pd.NaT] * len(rr)
                _expiry_vals   = rr["lease_expiry"].values if _has_expiry else [pd.NaT] * len(rr)
                _unexpired_fmt = []
                for i in range(len(rr)):
                    if _is_vac[i]:
                        _unexpired_fmt.append("\u2014")
                        continue
                    _uyr = float(_uyr_vals[i]) if pd.notna(_uyr_vals[i]) else 0.0
                    _base = f"{_uyr:.1f}yr"
                    # Annotate if break option is present and was binding (< expiry)
                    if _has_bopt:
                        _bopt = _bopt_vals[i]
                        _exp  = _expiry_vals[i]
                        if pd.notna(_bopt) and pd.notna(_exp) and _bopt < _exp:
                            _base = f"{_uyr:.1f}yr (break: {pd.Timestamp(_bopt).strftime('%b %Y')})"
                    _unexpired_fmt.append(_base)
                disp_df["Unexpired Term (Yrs)"] = _unexpired_fmt
            st.dataframe(disp_df, use_container_width=True, hide_index=True)

            # ── Market Rent vs Contract Rent Analysis ─────────────────────────
            if metrics.get("reversion_data_available") and metrics.get("reversion_rows"):
                st.markdown("#### 📊 Market Rent vs Contract Rent")
                _rev_rows = metrics["reversion_rows"]
                _port_rev = metrics.get("portfolio_reversion_pct", 0)
                _anch_rev = metrics.get("anchor_reversion_pct", 0)
                _anch_flag = metrics.get("anchor_over_rented", False)

                # Summary metrics
                _rc1, _rc2, _rc3 = st.columns(3)
                _port_colour = "#dc2626" if abs(_port_rev) > 0.20 else ("#d97706" if abs(_port_rev) > 0.10 else "#059669")
                _anch_colour = "#dc2626" if _anch_flag else ("#d97706" if _anch_rev > 0.10 else "#059669")
                _rc1.markdown(
                    f'<div style="background:#f8f9fb;border-radius:8px;padding:12px;border:1px solid #e5e7eb;">'
                    f'<div style="font-size:0.72rem;color:#6b7280;text-transform:uppercase;letter-spacing:.06em;">Portfolio Reversion</div>'
                    f'<div style="font-size:1.35rem;font-weight:700;color:{_port_colour};">{_port_rev:+.1%}</div>'
                    f'<div style="font-size:0.72rem;color:#6b7280;">weighted vs market</div>'
                    f'</div>', unsafe_allow_html=True
                )
                _rc2.markdown(
                    f'<div style="background:#f8f9fb;border-radius:8px;padding:12px;border:1px solid #e5e7eb;">'
                    f'<div style="font-size:0.72rem;color:#6b7280;text-transform:uppercase;letter-spacing:.06em;">Anchor Reversion</div>'
                    f'<div style="font-size:1.35rem;font-weight:700;color:{_anch_colour};">{_anch_rev:+.1%}</div>'
                    f'<div style="font-size:0.72rem;color:#6b7280;">{"⚠ over-rented — verdict impacted" if _anch_flag else "within tolerance"}</div>'
                    f'</div>', unsafe_allow_html=True
                )
                _verdict_impact = "NONE"
                if _anch_flag or abs(_port_rev) > 0.20:
                    _verdict_impact = "VERDICT DOWNGRADED"
                elif _anch_rev > 0.10 or abs(_port_rev) > 0.10:
                    _verdict_impact = "CAUTION FLAG"
                _vi_colour = "#dc2626" if _verdict_impact == "VERDICT DOWNGRADED" else ("#d97706" if _verdict_impact == "CAUTION FLAG" else "#059669")
                _rc3.markdown(
                    f'<div style="background:#f8f9fb;border-radius:8px;padding:12px;border:1px solid #e5e7eb;">'
                    f'<div style="font-size:0.72rem;color:#6b7280;text-transform:uppercase;letter-spacing:.06em;">Verdict Impact</div>'
                    f'<div style="font-size:1.10rem;font-weight:700;color:{_vi_colour};">{_verdict_impact}</div>'
                    f'<div style="font-size:0.72rem;color:#6b7280;">reversion risk signal</div>'
                    f'</div>', unsafe_allow_html=True
                )

                st.markdown("")

                # Per-tenant table
                _tbl_rows = []
                for _r in _rev_rows:
                    _rv = _r["reversion_pct"]
                    _flag = ""
                    if _rv > 0.25:   _flag = "🔴 Severely Over-Rented"
                    elif _rv > 0.15: _flag = "🟠 Over-Rented"
                    elif _rv > 0.05: _flag = "🟡 Slightly Over"
                    elif _rv < -0.10: _flag = "🟢 Under-Rented (upside)"
                    else:            _flag = "✔ At Market"
                    _tbl_rows.append({
                        "Tenant":           _r["tenant"],
                        "Anchor":           "★" if _r["is_anchor"] else "",
                        "Contract (€/m²/mo)": f"{_r['contract_rent']:.2f}",
                        "Market (€/m²/mo)":   f"{_r['market_rent']:.2f}",
                        "Variance":           f"{_rv:+.1%}",
                        "Income Share":       f"{_r['income_share']:.1%}",
                        "Signal":             _flag,
                    })
                st.dataframe(pd.DataFrame(_tbl_rows), use_container_width=True, hide_index=True)

        with col_right:
            st.markdown("#### NOI Bridge")
            if costs and costs.get("available") and costs.get("costs"):
                _label_map = {
                    "property_mgmt_fee":   "Property Mgmt",
                    "asset_mgmt_fee":      "Asset Mgmt",
                    "building_insurance":  "Building Insurance",
                    "capex_reserve":       "CapEx Reserve",
                    "ground_rent":         "Ground Rent",
                    "grundsteuer":         "Grundsteuer",
                    "void_service_charge": "Void Svc Charge",
                    "void_insurance":      "Void Insurance",
                    "letting_costs":       "Letting Costs",
                    "other_costs":         "Other",
                }
                # ── Recoverability toggles ─────────────────────────────
                with st.expander("⚙️ Cost Recoverability", expanded=False):
                    st.caption("Mark costs recovered from tenants. Memo only — IC verdict uses full cost-sheet NOI.")
                    for _rck, _rcv in costs["costs"].items():
                        if _rcv != 0:
                            st.checkbox(
                                f"{_label_map.get(_rck, _rck)} — Tenant pays",
                                key=f"{_recov_sk_prefix}_{_rck}",
                            )
                # ── Compute landlord vs tenant split ───────────────────
                _landlord_costs = {
                    k: v for k, v in costs["costs"].items()
                    if v != 0 and not st.session_state.get(f"{_recov_sk_prefix}_{k}", False)
                }
                _tenant_costs = {
                    k: v for k, v in costs["costs"].items()
                    if v != 0 and st.session_state.get(f"{_recov_sk_prefix}_{k}", False)
                }
                _tenant_total  = sum(_tenant_costs.values())
                _noi_bridge    = gross_rent - sum(_landlord_costs.values())
                # ── Gross Passing Rent ─────────────────────────────────
                st.markdown(
                    f'<div class="cost-line">'
                    f'<span class="cost-label">Gross Passing Rent</span>'
                    f'<span style="color:#059669;font-weight:600;">'
                    f'EUR {gross_rent:,.0f}</span></div>',
                    unsafe_allow_html=True,
                )
                # ── Landlord-borne section ─────────────────────────────
                if _landlord_costs:
                    st.markdown(
                        '<div style="font-size:0.77rem;color:#6b7280;margin:4px 0 2px;">'
                        'Landlord-borne costs:</div>',
                        unsafe_allow_html=True,
                    )
                    for k, v in _landlord_costs.items():
                        lbl = _label_map.get(k, k)
                        void_flag = " (void)" if "void" in k else ""
                        st.markdown(
                            f'<div class="cost-line">'
                            f'<span class="cost-label">&nbsp;&nbsp;{lbl}{void_flag}</span>'
                            f'<span class="cost-value">-EUR {v:,.0f}</span></div>',
                            unsafe_allow_html=True,
                        )
                # ── NOI line ───────────────────────────────────────────
                st.markdown(
                    f'<div class="noi-total">'
                    f'<span>NET OPERATING INCOME</span>'
                    f'<span>EUR {_noi_bridge:,.0f}</span></div>',
                    unsafe_allow_html=True,
                )
                # ── Tenant-recoverable memo section ────────────────────
                if _tenant_costs:
                    _all_nonzero = sum(v for v in costs["costs"].values() if v != 0)
                    _recov_rate  = _tenant_total / _all_nonzero if _all_nonzero > 0 else 0
                    st.markdown(
                        '<div style="font-size:0.77rem;color:#6b7280;margin:8px 0 2px;">'
                        'Tenant-recoverable costs (memo \u2014 not deducted from NOI):</div>',
                        unsafe_allow_html=True,
                    )
                    for k, v in _tenant_costs.items():
                        lbl = _label_map.get(k, k)
                        st.markdown(
                            f'<div class="cost-line" style="opacity:0.55;">'
                            f'<span class="cost-label">&nbsp;&nbsp;\u21a9 {lbl}</span>'
                            f'<span class="cost-value">EUR {v:,.0f}</span></div>',
                            unsafe_allow_html=True,
                        )
                    st.caption(f"Net Recovery Rate: {_recov_rate:.1%} of operating costs recovered from tenants.")
                    st.caption(
                        f"\u26a0\ufe0f IC verdict uses full cost-sheet NOI (EUR {noi:,.0f}). "
                        "Recovery selections are memo only and do not affect the QA audit."
                    )
                st.caption(f"Void costs: EUR {metrics['void_costs']:,.0f} / yr "
                           f"({metrics['total_vacant_area']:,.0f} m2 vacant)")
            else:
                st.info(f"Populate the Cost Sheet tab for itemised NOI.\n\n"
                        f"Current: {noi_margin:.0%} margin applied = EUR {noi:,.0f}")

            # AfA rate indicator
            _afa_lbl_dp = metrics.get("afa_rate_label")
            _afa_ann_dp = metrics.get("afa_annual")
            if _afa_lbl_dp:
                _afa_str = f"EUR {_afa_ann_dp:,.0f} / yr" if _afa_ann_dp else "n/a (no asking price)"
                st.caption(f"AfA rate: **{_afa_lbl_dp}** — annual depreciation {_afa_str}")

    # ------------------------------------------------------------------ TAB BID PRICE
    with tab_bid:
        st.subheader("Bid Price Calculator \u2014 What Is Our Number?")
        st.caption(
            "Reverse underwriting: find the maximum price that satisfies your return "
            "requirements. The asking price is irrelevant \u2014 this answers \u201cWhat is our number?\u201d"
        )

        _bid_ltv_ratio = metrics.get("ltv") or 0
        _bid_ds_rate   = (y1_debt_svc / loan) if loan > 0 else (int_rt + amrt_rt)

        if gross_rent <= 0:
            st.warning("Gross Rent is zero \u2014 populate the rent roll to use the Bid Price calculator.")
        else:
            _bid_break_fails = []
            _bl, _br = st.columns([1, 1.5])

            with _bl:
                st.markdown("#### Your Return Requirements")
                st.caption("Set your hurdle rates. Max bid updates instantly.")
                _tgt_dscr    = st.slider("Target DSCR", 1.20, 2.00, 1.40, 0.05,
                                          format="%.2fx", key="bid_dscr")
                _tgt_giy_pct = st.slider("Target GIY", 5.0, 10.0, 6.5, 0.5,
                                          format="%.1f%%", key="bid_giy_pct")
                _tgt_giy     = _tgt_giy_pct / 100.0
                _tgt_irr_pct = st.slider("Target IRR (10yr)", 6.0, 15.0, 8.0, 0.5,
                                          format="%.1f%%", key="bid_irr_pct")
                _tgt_irr     = _tgt_irr_pct / 100.0
                st.divider()
                st.caption(
                    f"**Inherited assumptions:**  \n"
                    f"Rate {int_rt:.2%} \u00b7 Amort {amrt_rt:.2%} "
                    f"\u00b7 LTV {_bid_ltv_ratio:.1%} \u00b7 Exit cap {base_exit_cap:.2%} "
                    f"\u00b7 CPI {cpi_indexation:.1%}"
                )
                # ── Break option scan (numeric values for CP scenario) ──────
                try:
                    if 'break_option' in rr.columns:
                        _bid_active_rr  = rr[rr["tenant"].str.upper() != "VACANT"].copy()
                        _bid_has_break  = _bid_active_rr[
                            _bid_active_rr['break_option'].notna() &
                            (_bid_active_rr['break_option'].astype(str).str.strip() != '')
                        ]
                        _bid_annual_ds  = loan * (int_rt + amrt_rt) if loan > 0 else 0
                        for _, _bt in _bid_has_break.iterrows():
                            _b_area      = float(_bt.get('area_sqm', 0) or 0)
                            _b_rent      = float(_bt.get('annual_rent', 0) or 0)
                            _b_void      = _b_area * VOID_HOLD_RATE
                            _b_post_noi  = noi - _b_rent - _b_void
                            _b_post_dscr = _b_post_noi / _bid_annual_ds if _bid_annual_ds > 0 else None
                            if _b_post_dscr is not None and _b_post_dscr < 1.20:
                                _bid_break_fails.append({
                                    'tenant':    str(_bt.get('tenant_name', _bt.get('tenant', '?'))),
                                    'rent':      _b_rent,
                                    'area':      _b_area,
                                    'void_cost': _b_void,
                                    'post_dscr': _b_post_dscr,
                                })
                except Exception:
                    pass

                if _bid_break_fails:
                    _bf_names_str = ", ".join(r['tenant'] for r in _bid_break_fails)
                    st.markdown(
                        f'<div style="background:#fee2e2;border:1px solid #dc2626;'
                        f'border-radius:8px;padding:10px 14px;margin:12px 0 4px;">'
                        f'<b style="color:#991b1b;">\u26a0\ufe0f Break Option Risk Detected</b><br>'
                        f'<span style="font-size:0.84rem;color:#7f1d1d;">'
                        f'<b>{_bf_names_str}</b> \u2014 DSCR falls below 1.20x if break is exercised. '
                        f'Base bid price overstates what you can safely pay.</span></div>',
                        unsafe_allow_html=True,
                    )
                    st.markdown("**Fix It \u2014 Condition Precedent Scenario**")
                    _new_lease_yrs = st.number_input(
                        "New lease term (years) if CP satisfied",
                        min_value=1, max_value=30, value=10, step=1,
                        key="bid_cp_lease_yrs",
                    )
                    # Recalculate WAULT with break-fail tenants on extended lease
                    try:
                        _today_ts    = pd.Timestamp.today()
                        _bwt_active  = rr[rr["tenant"].str.upper() != "VACANT"].copy()
                        _bf_t_names  = {r['tenant'] for r in _bid_break_fails}
                        _wt_num, _wt_den = 0.0, 0.0
                        for _, _wr in _bwt_active.iterrows():
                            _wr_rent = float(_wr.get('annual_rent', 0) or 0)
                            if _wr_rent <= 0:
                                continue
                            _wr_t = str(_wr.get('tenant_name', _wr.get('tenant', '')))
                            if _wr_t in _bf_t_names:
                                _wr_unexpired = float(_new_lease_yrs)
                            else:
                                try:
                                    _wr_unexpired = max(0.0, (
                                        pd.Timestamp(_wr.get('lease_expiry')) - _today_ts
                                    ).days / 365.25)
                                except Exception:
                                    _wr_unexpired = 0.0
                            _wt_num += _wr_rent * _wr_unexpired
                            _wt_den += _wr_rent
                        _cp_wault = _wt_num / _wt_den if _wt_den > 0 else 0.0
                    except Exception:
                        _cp_wault = metrics.get('wault', 0)
                    st.caption(
                        f"New WAULT with {_new_lease_yrs}yr lease: "
                        f"**{_cp_wault:.1f} yrs** "
                        f"(current: {metrics.get('wault', 0):.1f} yrs)"
                    )
                    st.toggle(
                        "Model as Condition Precedent (CP)",
                        key="bid_cp_on",
                        help=(
                            "ON: bid uses full NOI \u2014 assumes new lease is signed as CP. "
                            "OFF: bid uses post-break NOI \u2014 break risk is live."
                        ),
                    )

            # ── NOI/gross used for bid price (adjusted for break risk) ─────────────
            _bf_total_rent  = sum(r['rent']      for r in _bid_break_fails)
            _bf_total_void  = sum(r['void_cost'] for r in _bid_break_fails)
            _bid_cp_active  = bool(_bid_break_fails) and st.session_state.get("bid_cp_on", False)
            if _bid_break_fails and not st.session_state.get("bid_cp_on", False):
                # CP OFF: price off post-break NOI (break risk is live)
                _bid_noi   = noi        - _bf_total_rent - _bf_total_void
                _bid_gross = gross_rent - _bf_total_rent
            else:
                # No break fails, or CP is ON: price off full NOI
                _bid_noi   = noi
                _bid_gross = gross_rent

            # ── Three max prices ───────────────────────────────────────────────────
            # 1. GIY constraint:  price = gross_rent / target_giy
            _bp_giy = _bid_gross / _tgt_giy if _tgt_giy > 0 else None

            # 2. DSCR constraint (fixed LTV):
            #    NOI = target_dscr * ds_rate * ltv * price  =>  price = NOI / (...)
            if _bid_noi > 0 and _bid_ds_rate > 0 and _bid_ltv_ratio > 0:
                _bp_dscr = _bid_noi / (_tgt_dscr * _bid_ds_rate * _bid_ltv_ratio)
            else:
                _bp_dscr = None

            # 3. IRR constraint:  bisect for price where levered IRR = target
            _bp_irr = None
            if _bid_noi > 0 and _bid_ltv_ratio > 0:
                try:
                    def _irr_at_p(p):
                        if p <= 0:
                            return None
                        return _scen_irr(
                            p * (1.0 - _bid_ltv_ratio), _bid_noi, _bid_noi,
                            cpi_indexation, p * _bid_ltv_ratio,
                            int_rt, amrt_rt, io_years, loan_type_key,
                            base_exit_cap, hold_yrs=10,
                        )

                    _irr_lo_p, _irr_hi_p = 100_000.0, 200_000_000.0
                    _f_lo = _irr_at_p(_irr_lo_p)
                    _f_hi = _irr_at_p(_irr_hi_p)
                    if _f_lo is not None and _f_hi is not None and _f_lo >= _tgt_irr:
                        for _ in range(80):
                            _mid_p = (_irr_lo_p + _irr_hi_p) / 2.0
                            _f_mid = _irr_at_p(_mid_p)
                            if _f_mid is None:
                                break
                            if abs(_f_mid - _tgt_irr) < 1e-6 or (_irr_hi_p - _irr_lo_p) < 1.0:
                                break
                            if _f_mid > _tgt_irr:
                                _irr_lo_p = _mid_p  # can afford to pay more
                            else:
                                _irr_hi_p = _mid_p  # price too high
                        _bp_irr = (_irr_lo_p + _irr_hi_p) / 2.0
                except Exception:
                    _bp_irr = None

            # ── Binding = lowest of the three ─────────────────────────────────────
            _bp_cands = {
                k: v for k, v in {"DSCR": _bp_dscr, "GIY": _bp_giy, "IRR": _bp_irr}.items()
                if v is not None and v > 0
            }
            if _bp_cands:
                _bp_binding  = min(_bp_cands, key=_bp_cands.get)
                _max_bid     = _bp_cands[_bp_binding]
                _max_bid_rnd = int(_max_bid / 50_000) * 50_000
            else:
                _bp_binding = _max_bid = _max_bid_rnd = None

            with _br:
                st.markdown("#### Maximum Bid Price")

                # Three constraint cards
                _bc1, _bc2, _bc3 = st.columns(3)
                _bc1.metric(
                    "Max Price (DSCR)",
                    f"EUR {_bp_dscr:,.0f}" if _bp_dscr else "--",
                    delta=f"DSCR {_tgt_dscr:.2f}x hurdle", delta_color="off",
                    help=(
                        f"Bank will lend against NOI EUR {_bid_noi:,.0f}/yr at your DSCR "
                        f"target of {_tgt_dscr:.2f}x. "
                        "Formula: NOI \u00f7 (DSCR \u00d7 debt_service_rate \u00d7 LTV)"
                    ),
                )
                _bc2.metric(
                    "Max Price (GIY)",
                    f"EUR {_bp_giy:,.0f}" if _bp_giy else "--",
                    delta=f"GIY {_tgt_giy:.1%} hurdle", delta_color="off",
                    help=f"Yield floor: paying more compresses GIY below {_tgt_giy:.1%}. Formula: Gross Rent \u00f7 GIY",
                )
                _bc3.metric(
                    "Max Price (IRR)",
                    f"EUR {_bp_irr:,.0f}" if _bp_irr else "--",
                    delta=f"IRR {_tgt_irr:.1%} hurdle", delta_color="off",
                    help=(
                        f"Return floor: paying more produces a 10yr levered IRR below {_tgt_irr:.1%}. "
                        f"Exit cap {base_exit_cap:.2%}, CPI {cpi_indexation:.1%}. "
                        "Solved via bisection."
                    ),
                )

                # ── CP mode label ──────────────────────────────────────────────────
                if _bid_break_fails:
                    if _bid_cp_active:
                        st.markdown(
                            '<div style="background:#fef3c7;border:1px solid #f59e0b;'
                            'border-radius:6px;padding:7px 12px;margin:8px 0 2px;'
                            'font-size:0.83rem;color:#92400e;">'
                            '<b>\U0001f4cb CP ON</b> \u2014 bid uses full NOI. '
                            'New lease signed as condition precedent.</div>',
                            unsafe_allow_html=True,
                        )
                    else:
                        st.caption(
                            f"\u26a0\ufe0f Bid uses post-break NOI "
                            f"(EUR {_bid_noi:,.0f}/yr) \u2014 break risk live. "
                            f"Toggle CP above to model with new lease."
                        )

                # ── Shared helpers ─────────────────────────────────────────────────
                def _bid_chg(v_new, v_old, scale=100, sfx="pp", fmt="+.1f"):
                    if v_new is None or v_old is None:
                        return "--"
                    return f"{(v_new - v_old) * scale:{fmt}}{sfx}"

                def _metrics_at(price):
                    """Return dict of key metrics computed at an arbitrary price."""
                    _l  = price * _bid_ltv_ratio
                    _ds = _bid_ds_rate * _l
                    return {
                        "giy":  _bid_gross / price,
                        "dscr": _bid_noi / _ds if _ds > 0 else None,
                        "dy":   _bid_noi / _l  if _l  > 0 else None,
                        "irr":  _irr_at_p(price) if _bid_noi > 0 else None,
                    }

                _ask_irr = base_irr if (base_irr is not None and base_irr == base_irr) else None

                # ── Branch: asking OK vs asking too high ───────────────────────────
                if _max_bid is not None:
                    if asking > 0 and asking <= _max_bid:
                        # ── CASE A: asking price is within return requirements ──────
                        _headroom = _max_bid - asking  # EUR room before hitting floor
                        st.markdown(
                            f'<div style="background:#d1fae5;border:2px solid #059669;'
                            f'border-radius:10px;padding:16px 20px;margin:14px 0 8px;">'
                            f'<div style="font-size:0.80rem;color:#065f46;font-weight:700;'
                            f'text-transform:uppercase;letter-spacing:0.06em;margin-bottom:6px;">'
                            f'\u2705 Asking price is within your return requirements</div>'
                            f'<div style="font-size:1.55rem;font-weight:800;color:#065f46;">'
                            f'You can proceed at EUR {asking:,.0f} and exceed all hurdle rates.</div>'
                            f'</div>',
                            unsafe_allow_html=True,
                        )
                        st.markdown(
                            f'<div style="font-size:0.87rem;line-height:2.0;margin-bottom:10px;">'
                            f'<b>Headroom:</b> EUR {_headroom:,.0f} above asking before hitting '
                            f'your {_bp_binding} floor &mdash; room to compete if bidding process escalates.'
                            f'</div>',
                            unsafe_allow_html=True,
                        )
                        # Metrics at asking price vs hurdles
                        st.markdown("##### Metrics at Asking Price vs Your Hurdles")
                        try:
                            _am = _metrics_at(asking)
                            st.dataframe(pd.DataFrame([
                                {
                                    "Metric":       "GIY",
                                    "At Asking":    f"{_am['giy']:.2%}",
                                    "Your Hurdle":  f"{_tgt_giy:.2%}",
                                    "Margin":       _bid_chg(_am["giy"], _tgt_giy, 10000, "bps", "+.0f"),
                                },
                                {
                                    "Metric":       "DSCR",
                                    "At Asking":    f"{_am['dscr']:.2f}x" if _am["dscr"] else "--",
                                    "Your Hurdle":  f"{_tgt_dscr:.2f}x",
                                    "Margin":       _bid_chg(_am["dscr"], _tgt_dscr, 1, "x", "+.2f"),
                                },
                                {
                                    "Metric":       "IRR (10yr)",
                                    "At Asking":    f"{_am['irr']:.1%}" if _am["irr"] else "--",
                                    "Your Hurdle":  f"{_tgt_irr:.1%}",
                                    "Margin":       _bid_chg(_am["irr"], _tgt_irr, 100, "pp", "+.1f"),
                                },
                            ]), hide_index=True, use_container_width=True)
                        except Exception as _cmp_e:
                            st.caption(f"Metrics table: {_cmp_e}")

                    else:
                        # ── CASE B: asking price is too high ──────────────────────
                        _overpay = asking - _max_bid
                        st.markdown(
                            f'<div style="background:#fee2e2;border:2px solid #dc2626;'
                            f'border-radius:10px;padding:16px 20px;margin:14px 0 8px;">'
                            f'<div style="font-size:0.80rem;color:#991b1b;font-weight:700;'
                            f'text-transform:uppercase;letter-spacing:0.06em;margin-bottom:6px;">'
                            f'\u274c Asking price exceeds your return requirements</div>'
                            f'<div style="font-size:1.55rem;font-weight:800;color:#991b1b;">'
                            f'Overpaying by EUR {_overpay:,.0f} at asking price of EUR {asking:,.0f}.</div>'
                            f'</div>',
                            unsafe_allow_html=True,
                        )
                        st.markdown(
                            f'<div style="font-size:0.87rem;line-height:2.0;margin-bottom:10px;">'
                            f'<b>Your maximum bid:</b> EUR {_max_bid:,.0f} '
                            f'(binding constraint: {_bp_binding})<br>'
                            f'<b>Negotiation target:</b> EUR {_max_bid_rnd:,.0f} '
                            f'(rounded to nearest EUR 50,000)'
                            f'</div>',
                            unsafe_allow_html=True,
                        )
                        # Comparison table: asking vs max bid
                        st.markdown("##### What Changes at Your Bid Price")
                        try:
                            _am  = _metrics_at(asking)
                            _mbm = _metrics_at(_max_bid)
                            st.dataframe(pd.DataFrame([
                                {
                                    "Metric":          "GIY",
                                    "At Asking Price": f"{_am['giy']:.2%}",
                                    "At Max Bid":      f"{_mbm['giy']:.2%}",
                                    "Change":          _bid_chg(_mbm["giy"], _am["giy"], 10000, "bps", "+.0f"),
                                },
                                {
                                    "Metric":          "DSCR",
                                    "At Asking Price": f"{_am['dscr']:.2f}x" if _am["dscr"] else "--",
                                    "At Max Bid":      f"{_mbm['dscr']:.2f}x" if _mbm["dscr"] else "--",
                                    "Change":          _bid_chg(_mbm["dscr"], _am["dscr"], 1, "x", "+.2f"),
                                },
                                {
                                    "Metric":          "Debt Yield",
                                    "At Asking Price": f"{_am['dy']:.2%}" if _am["dy"] else "--",
                                    "At Max Bid":      f"{_mbm['dy']:.2%}" if _mbm["dy"] else "--",
                                    "Change":          _bid_chg(_mbm["dy"], _am["dy"], 10000, "bps", "+.0f"),
                                },
                                {
                                    "Metric":          "IRR (10yr)",
                                    "At Asking Price": f"{_am['irr']:.1%}" if _am["irr"] else "--",
                                    "At Max Bid":      f"{_mbm['irr']:.1%}" if _mbm["irr"] else "--",
                                    "Change":          _bid_chg(_mbm["irr"], _am["irr"], 100, "pp", "+.1f"),
                                },
                            ]), hide_index=True, use_container_width=True)
                        except Exception as _cmp_e:
                            st.caption(f"Comparison table: {_cmp_e}")
                else:
                    st.warning(
                        "Could not calculate max bid price. "
                        "Ensure the deal has positive NOI, loan amount, and LTV entered."
                    )

    # ------------------------------------------------------------------ BANKER VIEW
    with tab_banker:
        st.subheader("\U0001f3e6 Banker View — Covenant Audit")
        st.markdown(noi_badge, unsafe_allow_html=True)
        st.markdown("")

        b1,b2,b3,b4,b5,b6 = st.columns(6)
        dscr_lbl = "Yr 1 DSCR (Amort)"
        b1.metric("LTV",
                  f"{metrics['ltv']:.1%}",
                  delta=f"{'✅' if metrics['ltv']<=0.65 else '⚠️'} max 65%  |  limit 70%",
                  delta_color="normal" if metrics["ltv"]<=0.65 else "inverse",
                  help="Bank max: 65%. Above 65% → mezzanine required. Above 70% → most banks decline.")
        with b1:
            _eq_split = 1.0 - (metrics.get("ltv") or 0)
            st.caption(f"Equity: {_eq_split:.1%} | Debt: {metrics.get('ltv', 0):.1%}")
        b2.metric(dscr_lbl,
                  f"{y1_dscr:.2f}x" if y1_dscr else "--",
                  delta=f"{'✅' if y1_dscr and y1_dscr>=1.30 else '⚠️'} min 1.30x  |  floor 1.20x",
                  delta_color="normal" if y1_dscr and y1_dscr>=1.30 else "inverse",
                  help="pbb/Berlin Hyp/Helaba covenant min: 1.30x. Below 1.20x = credit decline. Uses full amortising debt service (interest + principal).")
        b3.metric("Yr 1 ICR",
                  f"{y1_icr:.2f}x" if y1_icr else "--",
                  delta=f"{'✅' if y1_icr and y1_icr>=1.75 else '⚠️'} min 1.75x  |  floor 1.50x",
                  delta_color="normal" if y1_icr and y1_icr>=1.75 else "inverse",
                  help="Interest Coverage Ratio. Pfandbriefbank covenant: min 1.75x. Tracked quarterly by the bank.")
        b4.metric("WAULT",
                  f"{metrics['wault']:.1f} yrs",
                  delta=f"{'✅' if metrics['wault']>=4 else '⚠️'} min 4 yrs  |  floor 2.5 yrs",
                  delta_color="normal" if metrics["wault"]>=4 else "inverse",
                  help="Bank minimum for senior debt: 4 years. Below 2.5 years → typically declined.")
        b5.metric("Anchor",
                  f"{metrics['anchor_pct']:.1%}",
                  delta=f"{'✅' if metrics['anchor_pct']<=0.55 else '⚠️'} warn >55%  |  concern >65%",
                  delta_color="normal" if metrics["anchor_pct"]<=0.55 else "inverse",
                  help="Single tenant income concentration. >55% = single-event risk. >65% = bank requires mitigation plan.")
        _dy  = metrics.get("debt_yield")
        _afa = metrics.get("afa_annual")
        b6.metric("Debt Yield", f"{_dy:.2%}" if _dy else "--",
                  delta="OK >=7.5%" if _dy and _dy>=0.075 else "WARN <7.5%",
                  delta_color="normal" if _dy and _dy>=0.075 else "inverse",
                  help="NOI / Loan Amount. German lenders increasingly require >= 7.5-8.0%")

        # ── Net Recovery Rate ──────────────────────────────────────────────────
        if _cs_cost_items:
            _bl_total_costs = sum(v for v in _cs_cost_items.values() if v != 0)
            _bl_recoverable = sum(
                v for k, v in _cs_cost_items.items()
                if v != 0 and st.session_state.get(f"{_recov_sk_prefix}_{k}", False)
            )
            _bl_recov_rate  = _bl_recoverable / _bl_total_costs if _bl_total_costs > 0 else 0
            st.divider()
            _nr_c1, _nr_c2 = st.columns([1, 2])
            _nr_c1.metric(
                "Net Recovery Rate",
                f"{_bl_recov_rate:.1%}",
                delta="\u2705 >60% institutional standard" if _bl_recov_rate >= 0.60
                      else "\u26a0\ufe0f <60% — below institutional standard",
                delta_color="normal" if _bl_recov_rate >= 0.60 else "inverse",
                help=(
                    "% of total operating costs recovered from tenants. "
                    "Institutional benchmark: >60% for well-structured German retail leases. "
                    "A Nettomietvertrag passes service costs to tenants; "
                    "a Bruttomietvertrag makes all costs landlord-borne. "
                    "Toggle recoverability in Deal Pack \u2192 NOI Bridge \u2192 \u2699\ufe0f Cost Recoverability."
                ),
            )
            _nr_c2.caption(
                f"**Gross lease (Bruttomietvertrag):** all {_bl_total_costs:,.0f} EUR / yr of costs are "
                f"landlord-borne. Recovery rate = 0%.\n\n"
                f"**Net lease (Nettomietvertrag):** tenants pay specified cost items. "
                f"Current selections: EUR {_bl_recoverable:,.0f} / yr recovered "
                f"({_bl_recov_rate:.1%} of total costs). "
                f"Adjust toggles in Deal Pack \u2192 NOI Bridge."
            )

        # AfA (tax depreciation) information block
        if _afa and _afa > 0:
            _build_val  = metrics.get("afa_building_value", 0)
            _afa_lbl    = metrics.get("afa_rate_label", "2.0% standard")
            _afa_r      = metrics.get("afa_rate", 0.02)
            st.info(
                f"**AfA (Absetzung fur Abnutzung):** Annual tax depreciation "
                f"EUR {_afa:,.0f} ({_afa_lbl} of building value EUR {_build_val:,.0f} = "
                f"75% of asking price). Not a cash cost — reduces taxable income. "
                f"At a 30% marginal tax rate, saves approximately "
                f"EUR {_afa * 0.30:,.0f} in tax per year."
            )

        st.divider()
        st.markdown("#### QA Covenant Checklist")
        for r in qa:
            msg = f"**{r.check}**  |  Actual: `{r.actual}`  |  Benchmark: `{r.benchmark}`"
            if r.status == "PASS":
                st.success(f"PASS -- {msg}")
            elif r.status == "WARN":
                st.warning(f"WARN -- {msg}")
            else:
                st.error(f"FAIL -- {msg}")

        st.divider()
        st.markdown("#### Lease Expiry Schedule")
        active2 = rr[~rr["is_vacant"]].copy()
        if "lease_expiry" in active2.columns and not active2.empty:
            active2["Expiry Year"] = pd.to_datetime(active2["lease_expiry"], errors="coerce").dt.year
            exp_df = (active2.groupby("Expiry Year")["annual_rent"]
                      .sum().reset_index()
                      .rename(columns={"annual_rent":"Rent at Risk (EUR)"}))
            exp_df = exp_df[exp_df["Expiry Year"].notna()]
            if not exp_df.empty:
                st.bar_chart(exp_df.set_index("Expiry Year"))

        # ── Refinancing Stress Test ─────────────────────────────────────
        st.divider()
        st.markdown("#### Refinancing Stress Test")
        _refi_year = st.select_slider(
            "Stress-test at loan maturity (years)", options=[3,5,7,10], value=5,
            help="At this year, how does LTV hold up if cap rates expand?")
        try:
            _refi_rows, _refi_bal, _refi_noi = calculate_refi_stress(
                metrics, deal, io_years, noi, loan_type_key,
                refi_year=_refi_year, cpi_rate=cpi_indexation)
            st.caption(
                f"Year {_refi_year}: Loan balance EUR {_refi_bal:,.0f} "
                f"| NOI EUR {_refi_noi:,.0f} "
                f"| Key question: does LTV stay below 65% as cap rates rise?")
            _refi_display = [{k:v for k,v in r.items() if k != "_ltv_raw"}
                             for r in _refi_rows]
            _refi_df = pd.DataFrame(_refi_display)

            def _color_status(val):
                if val == "PASS":  return "background-color:#d1fae5; color:#065f46; font-weight:600"
                if val == "WARN":  return "background-color:#fef3c7; color:#92400e; font-weight:600"
                if val == "FAIL":  return "background-color:#fee2e2; color:#991b1b; font-weight:600"
                return ""
            st.dataframe(
                _refi_df.style.map(_color_status, subset=["Status"]),
                use_container_width=True, hide_index=True)
            if not _refi_rows:
                st.info("Enter transaction data (asking price, loan, interest rate) in Excel to see refinancing stress test.")
            else:
                _fail_count = sum(1 for r in _refi_rows if r["Status"] == "FAIL")
                if _fail_count > 0:
                    st.warning(f"⚠️  LTV breaches 65% in {_fail_count} cap rate scenario(s). "
                               f"Equity injection or price renegotiation required.")
                else:
                    st.success("✅  LTV stays below 65% across all cap rate scenarios. Refinancing risk is low.")
        except Exception as _e:
            st.info(f"Refinancing stress test requires transaction data in Excel. ({_e})")

    # ------------------------------------------------------------------ TAB 3
    with tab_vc:  # ── VALUE CREATION ────────────────────────────────────────────
        st.markdown("#### Value Creation Analysis")
        st.caption(
            "Bonus upside analysis — separate from the IC verdict. "
            "The core deal is assessed on real estate fundamentals only (Investor Lens). "
            "This tab quantifies what additional value you can create AFTER acquisition "
            "through active asset management. Toggle each source on/off."
        )

        # Derive region from city string
        _city_vc = (deal.get('city') or '').lower()
        _region_vc = 'Default'
        for _r in ['Bayern','Baden-Württemberg','Hessen','Rheinland-Pfalz',
                   'Niedersachsen','Hamburg','Berlin','Sachsen','Thüringen',
                   'Nordrhein-Westfalen','Sachsen-Anhalt','Brandenburg']:
            if _r.lower() in _city_vc:
                _region_vc = _r; break

        _gla_vc     = float(metrics.get('gla_total') or 0)
        _parking_vc = int(deal.get('parking') or 0)
        _gross_vc   = float(metrics.get('gross_passing_rent') or 0)

        # Controls
        st.divider()
        _c1, _c2, _c3 = st.columns(3)
        with _c1:
            st.markdown("☀️ **Solar PV**")
            _sol_on  = st.toggle("Include Solar", value=True, key="sol_on")
            _roof_r  = st.slider("Usable roof %", 50, 90, 75, key="roof_r") / 100
            _s_cons  = st.slider("Self-consumption %", 30, 80, 60, key="s_cons") / 100
            _grid_p  = st.slider("Grid price (€/kWh)", 20, 40, 30, key="grid_p") / 100
            _feedin  = st.slider("Feed-in (€/kWh)", 6, 16, 8, key="feedin") / 100
            _sol_cap = st.slider("CapEx (€/m²)", 80, 180, 130, key="sol_cap")
        with _c2:
            st.markdown("🔌 **EV Charging**")
            _ev_on   = st.toggle("Include EV Charging", value=True, key="ev_on")
            _ev_rent = st.slider("Ground rent/charger (€/yr)", 1000, 4000, 2200, 100, key="ev_rent")
            _ev_cap  = st.slider("CapEx/charger (€)", 1000, 6000, 3000, 500, key="ev_cap")
            _est_chargers = max(2, int(_parking_vc * 1.2 / 10))
            st.caption(f"Car park: {_parking_vc} spaces → ~{_est_chargers} chargers")
        with _c3:
            st.markdown("📈 **ERV Reversion**")
            _erv_on  = st.toggle("Include ERV Analysis", value=True, key="erv_on")
            _erv_up  = st.slider("ERV uplift vs passing rent (%)", -20, 30, 10, key="erv_up") / 100
            st.caption("Positive = under-rented (upside). Negative = over-rented (risk).")

        st.divider()

        # Calculations
        _sol = _vc_solar(_gla_vc, _region_vc, _s_cons, _grid_p, _feedin, _sol_cap, _roof_r)
        _sol['enabled'] = _sol_on
        _ev  = _vc_ev(_parking_vc, _ev_rent, _ev_cap)
        _ev['enabled']  = _ev_on
        _erv = _vc_erv(_gross_vc, _erv_up)
        _erv['enabled'] = _erv_on
        _adj = _vc_adjusted_metrics(metrics, _sol, _ev, _erv, base_exit_cap)

        # KPI cards
        st.markdown("#### Base vs Adjusted Metrics")
        _bv1,_bv2,_bv3,_bv4,_bv5 = st.columns(5)
        _bv1.metric("Base NOI", f"EUR {_adj['base_noi']:,.0f}",
                    delta=f"+EUR {_adj['additional_noi']:,.0f}" if _adj['additional_noi'] > 0 else None,
                    delta_color="normal")
        _bv2.metric("Adjusted NOI", f"EUR {_adj['adjusted_noi']:,.0f}",
                    delta=f"{(_adj['adjusted_noi']/_adj['base_noi']-1)*100:.1f}% uplift" if _adj['base_noi'] > 0 else None,
                    delta_color="normal")
        _base_dscr_vc = metrics.get('dscr') or 0
        _bv3.metric("DSCR if VC deployed",
                    f"{_adj['adj_dscr']:.2f}x",
                    delta=f"base: {_base_dscr_vc:.2f}x (core verdict uses base)",
                    delta_color="off",
                    help="Illustrative only — core IC verdict is always based on Day-1 fundamentals")
        _bv4.metric("Asset Value Uplift",
                    f"EUR {_adj['asset_uplift']:+,.0f}",
                    delta=f"at {base_exit_cap:.1%} exit cap",
                    delta_color="normal" if _adj['asset_uplift'] >= 0 else "inverse")
        _bv5.metric("VC CapEx IRR", f"{_adj['capex_irr']:.1%}",
                    delta=f"EUR {_adj['total_capex']:,} total CapEx",
                    delta_color="normal" if _adj['capex_irr'] >= 0.15 else "off")

        # Upside summary — no verdict issued here, core IC verdict is in Investor Lens
        st.divider()
        st.info(
            "ℹ️ **These are bonus upside opportunities — not part of the IC verdict.** "
            "The deal is assessed on its real estate fundamentals in the Investor Lens tab. "
            "Value creation is what you can achieve *after* acquisition if you invest."
        )
        _bv = verdict['recommendation']
        _add = _adj['additional_noi']
        _cap = _adj['total_capex']
        _irr = _adj['capex_irr']
        if _add > 0:
            st.success(
                f"**Upside potential:** EUR {_add:,}/yr additional NOI available "
                f"(solar + EV charging) at EUR {_cap:,} CapEx — "
                f"{_irr:.1%} IRR on the value creation investment. "
                f"Core deal verdict remains **{_bv}** based on real estate fundamentals."
            )

        # Detail tables
        st.divider()
        _d1, _d2 = st.columns(2)
        with _d1:
            st.markdown("**Solar PV Detail**")
            if _sol_on and _gla_vc > 0:
                st.dataframe(pd.DataFrame({
                    "Item":  ["Usable roof","Generation","Gross income","OpEx","Net income/yr","CapEx","Payback"],
                    "Value": [f"{_sol['roof_area_m2']:,} m²",
                              f"{_sol['annual_kwh']:,} kWh",
                              f"EUR {_sol['gross_income']:,}",
                              f"EUR {_sol['opex']:,}",
                              f"EUR {_sol['net_income']:,}",
                              f"EUR {_sol['capex']:,}",
                              f"{_sol['payback_yrs']} yrs"],
                }), hide_index=True, use_container_width=True)
                st.caption(f"Region: {_region_vc} — {SOLAR_YIELD_BY_REGION.get(_region_vc,160)} kWh/m²/yr panel output")
            else:
                st.caption("Solar excluded or no GLA data.")

            st.markdown("**ERV Reversion**")
            if _erv_on and _gross_vc > 0:
                _icon = "📈" if _erv['direction'] == 'UPSIDE' else "⚠️"
                st.metric("Direction", f"{_icon} {_erv['direction']}")
                st.metric("Current passing rent", f"EUR {_erv['current_rent']:,}/yr")
                st.metric("ERV estimate", f"EUR {_erv['erv_estimate']:,}/yr",
                          delta=f"EUR {_erv['annual_uplift']:+,}/yr at expiry",
                          delta_color="normal" if _erv_up >= 0 else "inverse")
                if _erv_up < 0:
                    st.error("Asset appears OVER-RENTED. Rent reduction likely at next expiry.")
                else:
                    st.info("ERV upside captured at lease renewal — not in current NOI/DSCR.")
            else:
                st.caption("ERV analysis excluded.")

        with _d2:
            st.markdown("**EV Charging Detail**")
            if _ev_on and _parking_vc > 0:
                st.dataframe(pd.DataFrame({
                    "Item":  ["Car park spaces","Chargers","Ground rent/yr","CapEx","Payback"],
                    "Value": [f"{_parking_vc}",
                              f"{_ev['chargers']} DC fast chargers",
                              f"EUR {_ev['annual_income']:,}",
                              f"EUR {_ev['capex']:,}",
                              f"{_ev['payback_yrs']} yrs"],
                }), hide_index=True, use_container_width=True)
                st.caption("Operator funds hardware. Landlord: space + grid upgrade only.")
            else:
                st.caption("EV charging excluded or no parking data.")

            st.markdown("**Combined VC Summary**")
            if _adj['total_capex'] > 0:
                _rows = {"Source":[], "Income (EUR/yr)":[], "CapEx (EUR)":[], "Payback (yrs)":[]}
                if _sol_on:
                    _rows["Source"].append("Solar PV")
                    _rows["Income (EUR/yr)"].append(f"{_sol['net_income']:,}")
                    _rows["CapEx (EUR)"].append(f"{_sol['capex']:,}")
                    _rows["Payback (yrs)"].append(_sol['payback_yrs'])
                if _ev_on:
                    _rows["Source"].append("EV Charging")
                    _rows["Income (EUR/yr)"].append(f"{_ev['annual_income']:,}")
                    _rows["CapEx (EUR)"].append(f"{_ev['capex']:,}")
                    _rows["Payback (yrs)"].append(_ev['payback_yrs'])
                _rows["Source"].append("TOTAL")
                _rows["Income (EUR/yr)"].append(f"{_adj['additional_noi']:,}")
                _rows["CapEx (EUR)"].append(f"{_adj['total_capex']:,}")
                _total_pb = round(_adj['total_capex']/_adj['additional_noi'],1) if _adj['additional_noi']>0 else "—"
                _rows["Payback (yrs)"].append(_total_pb)
                st.dataframe(pd.DataFrame(_rows), hide_index=True, use_container_width=True)
                st.metric("Overall VC CapEx IRR", f"{_adj['capex_irr']:.1%}",
                          delta="Return on value creation investment")

    # ------------------------------------------------------------------ INVESTOR VIEW
    with tab_investor:
        st.subheader("\U0001f4b0 Investor View — Investment Committee Verdict")
        st.markdown(noi_badge, unsafe_allow_html=True)
        st.markdown("")

        # ── Three-Verdict Summary Row ──────────────────────────────────────────
        try:
            import datetime as _tdt

            # Badge 1 — Base Verdict
            _bv_rec  = verdict.get("recommendation", "INVESTIGATE")
            _bv_col  = {"PASS": ("#d1fae5","#065f46"), "INVESTIGATE": ("#fef3c7","#92400e"),
                        "DECLINE": ("#fee2e2","#991b1b")}.get(_bv_rec, ("#fef3c7","#92400e"))
            _bv_icon = {"PASS": "✓", "INVESTIGATE": "⚠", "DECLINE": "✗"}.get(_bv_rec, "⚠")

            # Badge 2 — Stress Survival (Severity 2)
            _stress_dscr = None
            if not rr.empty and loan > 0 and y1_debt_svc > 0:
                try:
                    _sc2 = _di_scenario_engine(
                        metrics, rr, deal, noi, loan, int_rt, amrt_rt,
                        io_years, loan_type_key, cpi_indexation,
                        stress_severity=2,
                    )
                    _stress_dscr = _sc2.get('stress', {}).get('dscr')
                except Exception:
                    _stress_dscr = None

            if _stress_dscr is None:
                _ss_label = "N/A"
                _ss_col   = ("#f3f4f6","#374151")
                _ss_icon  = "—"
            elif _stress_dscr >= 1.20:
                _ss_label = "SURVIVES"
                _ss_col   = ("#d1fae5","#065f46")
                _ss_icon  = "✓"
            elif _stress_dscr >= 1.00:
                _ss_label = "MARGINAL"
                _ss_col   = ("#fef3c7","#92400e")
                _ss_icon  = "⚠"
            else:
                _ss_label = "FAILS"
                _ss_col   = ("#fee2e2","#991b1b")
                _ss_icon  = "✗"

            # Badge 3 — RI-Adjusted
            # Reconstruct qualifying tenants using same logic as section 11
            _ri_badge_label = "No signals entered"
            _ri_badge_col   = ("#f3f4f6","#6b7280")
            _ri_badge_icon  = "—"
            _ri_adj_dscr    = None

            if not rr.empty and y1_debt_svc > 0:
                _cutoff_ri = _tdt.date.today() + _tdt.timedelta(days=3*365)
                _active_ri = rr[~rr.get('is_vacant', pd.Series(False, index=rr.index))].copy() if not rr.empty else pd.DataFrame()
                _qual_ri   = []
                for _, _rr_row in _active_ri.iterrows():
                    _has_break = False
                    _exp_near  = False
                    if 'break_option' in _rr_row.index:
                        _bo = _rr_row.get('break_option')
                        if _bo is not None and not (isinstance(_bo, float) and pd.isna(_bo)):
                            try:
                                _bd2 = _bo.date() if hasattr(_bo, 'date') else _bo
                                if isinstance(_bd2, _tdt.date):
                                    _has_break = True
                            except Exception:
                                pass
                    _ex2 = _rr_row.get('lease_expiry')
                    if _ex2 is not None and not (isinstance(_ex2, float) and pd.isna(_ex2)):
                        try:
                            _ed2 = _ex2.date() if hasattr(_ex2, 'date') else _ex2
                            if isinstance(_ed2, _tdt.date) and _ed2 <= _cutoff_ri:
                                _exp_near = True
                        except Exception:
                            pass
                    if _has_break or _exp_near:
                        _qual_ri.append(_rr_row)

                if _qual_ri:
                    _tk_hash = '_'.join(str(_r.get('tenant','')) for _r in _qual_ri)
                    _ri_sk   = f'ri_state_{hash(_tk_hash) & 0xFFFFFF}'
                    _ri_data = st.session_state.get(_ri_sk, {})

                    # Check if any non-AMBER signals have been entered
                    _any_signal = any(
                        _ri_data.get(f'ri_sig_{str(_r.get("tenant","?"))}', 'AMBER') != 'AMBER'
                        for _r in _qual_ri
                    )
                    if _any_signal:
                        _adj_noi_ri = noi
                        for _r in _qual_ri:
                            _tn2  = str(_r.get('tenant', '?'))
                            _sig2 = _ri_data.get(f'ri_sig_{_tn2}', 'AMBER')
                            if _sig2 != 'GREEN':
                                _adj_noi_ri -= float(_r.get('annual_rent', 0) or 0)
                                _adj_noi_ri -= float(_r.get('area_sqm', 0) or 0) * 20.0
                        _ri_adj_dscr = _adj_noi_ri / y1_debt_svc

                        if _ri_adj_dscr >= 1.30:
                            _ri_badge_label = "PASS"
                            _ri_badge_col   = ("#d1fae5","#065f46")
                            _ri_badge_icon  = "✓"
                        elif _ri_adj_dscr >= 1.20:
                            _ri_badge_label = "MARGINAL"
                            _ri_badge_col   = ("#fef3c7","#92400e")
                            _ri_badge_icon  = "⚠"
                        else:
                            _ri_badge_label = "WEAK"
                            _ri_badge_col   = ("#fee2e2","#991b1b")
                            _ri_badge_icon  = "✗"

            # Render the three badges side-by-side
            def _verdict_badge_html(title, icon, label, bg, fg, dscr_val=None):
                _sub = f"DSCR {dscr_val:.2f}x" if dscr_val is not None else ""
                return (
                    f'<div style="background:{bg};color:{fg};border-radius:10px;'
                    f'padding:14px 18px;text-align:center;border:1.5px solid {fg}33;">'
                    f'<div style="font-size:0.72rem;font-weight:600;letter-spacing:0.06em;'
                    f'text-transform:uppercase;opacity:0.75;margin-bottom:4px;">{title}</div>'
                    f'<div style="font-size:1.5rem;font-weight:800;line-height:1;">'
                    f'{icon}&nbsp;{label}</div>'
                    + (f'<div style="font-size:0.78rem;margin-top:6px;opacity:0.85;">{_sub}</div>' if _sub else '')
                    + '</div>'
                )

            _b1_html = _verdict_badge_html("Base Verdict",    _bv_icon, _bv_rec,    _bv_col[0], _bv_col[1])
            _b2_html = _verdict_badge_html("Stress Survival", _ss_icon, _ss_label,  _ss_col[0], _ss_col[1], _stress_dscr)
            _ri_dscr_disp = _ri_adj_dscr if _ri_adj_dscr is not None and _ri_badge_label != "No signals entered" else None
            _b3_html = _verdict_badge_html("RI-Adjusted",     _ri_badge_icon, _ri_badge_label, _ri_badge_col[0], _ri_badge_col[1], _ri_dscr_disp)

            st.markdown(
                f'<div style="display:grid;grid-template-columns:1fr 1fr 1fr;gap:12px;margin-bottom:18px;">'
                f'{_b1_html}{_b2_html}{_b3_html}</div>',
                unsafe_allow_html=True,
            )

            # Synthesis sentence
            _synth_parts = []
            if _bv_rec == "PASS":
                _synth_parts.append("Base case passes all covenants")
            elif _bv_rec == "INVESTIGATE":
                _synth_parts.append("Base case requires further investigation")
            else:
                _synth_parts.append("Base case fails IC criteria")

            if _stress_dscr is not None:
                if _ss_label == "SURVIVES":
                    _synth_parts.append(f"stress-tested DSCR holds at {_stress_dscr:.2f}x")
                elif _ss_label == "MARGINAL":
                    _synth_parts.append(f"stress DSCR is marginal at {_stress_dscr:.2f}x")
                else:
                    _synth_parts.append(f"stress scenario breaks cover ({_stress_dscr:.2f}x)")

            if _ri_adj_dscr is not None and _ri_badge_label != "No signals entered":
                if _ri_badge_label == "PASS":
                    _synth_parts.append(f"relationship intelligence supports renewal ({_ri_adj_dscr:.2f}x RI-adjusted)")
                elif _ri_badge_label == "MARGINAL":
                    _synth_parts.append(f"RI-adjusted cover is marginal at {_ri_adj_dscr:.2f}x")
                else:
                    _synth_parts.append(f"RI signals weaken cover to {_ri_adj_dscr:.2f}x")
            else:
                _synth_parts.append("no relationship intelligence entered")

            _synthesis = "; ".join(_synth_parts) + "."
            st.caption(_synthesis)

        except Exception as _tvd_err:
            st.error(f"Three-verdict display error: {_tvd_err}")

        # ── End Three-Verdict ──────────────────────────────────────────────────

        v_class = {"PASS":"verdict-pass","INVESTIGATE":"verdict-inv","DECLINE":"verdict-dec"}.get(
            verdict["recommendation"],"verdict-inv")
        v_icon  = {"PASS":"✓","INVESTIGATE":"CAUTION","DECLINE":"✗"}.get(
            verdict["recommendation"],"CAUTION")
        st.markdown(
            f'<div class="{v_class}"><strong style="font-size:1.05rem;">'
            f'{v_icon}  --  {verdict["recommendation"]}</strong><br/>'
            f'<span style="font-size:0.9rem;">{verdict["rationale"]}</span></div>',
            unsafe_allow_html=True,
        )
        st.markdown("")

        cf1, cf2 = st.columns(2)
        with cf1:
            st.markdown("**Key Factors**")
            for r in verdict["top_reasons"]: st.markdown(f"- {r}")
        with cf2:
            if verdict["red_flags"]:
                st.markdown("**Red Flags**")
                for f in verdict["red_flags"]: st.markdown(f"- {f}")

        # ── Deal Repair Engine ──────────────────────────────────────────────
        _repairs = deal_repair_engine(metrics, deal, qa, verdict)
        if _repairs:
            st.divider()
            st.markdown("#### 🔧 Deal Repair — How to Make This Work")
            st.caption(
                "Exact adjustments needed to turn this deal into a PASS. "
                "Each fix is actionable — the exact number and who to negotiate with. "
                "Fixes are independent: choose the most practical combination."
            )
            _cat_labels = {
                'seller':'Seller','equity':'Your equity',
                'bank':'Your bank','structuring':'Tax advisor','return':'Seller'
            }
            _rep_df = pd.DataFrame({
                "Fix":           [f'{r["icon"]} {r["lever"]}' for r in _repairs],
                "Binding Issue": [r["binding"]   for r in _repairs],
                "Change Needed": [r["delta"]     for r in _repairs],
                "Who":           [_cat_labels.get(r["category"], r["category"]) for r in _repairs],
            })
            st.dataframe(_rep_df, hide_index=True, use_container_width=True)
            for rx in _repairs:
                with st.expander(f'{rx["icon"]} {rx["lever"]}  —  {rx["delta"]}'):
                    _rc1, _rc2 = st.columns(2)
                    with _rc1:
                        st.markdown(f"**Current:** {rx['current']}")
                        st.markdown(f"**Required:** {rx['required']}")
                    with _rc2:
                        if rx.get('after'):
                            for _mk, _mv in rx['after'].items():
                                st.metric(_mk, _mv)
                    st.markdown("**What to say / do:**")
                    st.info(rx['action'])

        st.divider()

        # ── Multi-Period IRR ─────────────────────────────────────────────
        st.markdown("#### Hold Period Analysis — IRR by Exit Year")
        st.caption(
            "A negative or low short-term IRR means this is a long-hold asset. "
            "The exit drives the return, not the in-period cash flow.")
        try:
            _multi = calculate_multi_period_irr(
                metrics, deal, io_years, base_exit_cap, cpi_indexation,
                noi, loan_type_key)
            _irr_cols = st.columns(4)
            _labels = {3:"3-Year\n(Short Hold)",5:"5-Year\n(Core+)",
                       7:"7-Year\n(Institutional)",10:"10-Year\n(Full Cycle)"}
            for i,(yrs,label) in enumerate(_labels.items()):
                irr_val = _multi.get(yrs)
                irr_str = f"{irr_val:.2%}" if irr_val and not np.isnan(irr_val) else "N/A"
                delta_str = None
                delta_col = "normal"
                if irr_val and not np.isnan(irr_val):
                    if irr_val >= 0.10:   delta_str, delta_col = "Strong", "normal"
                    elif irr_val >= 0.07: delta_str, delta_col = "Target range", "normal"
                    elif irr_val >= 0.05: delta_str, delta_col = "Marginal", "off"
                    else:                 delta_str, delta_col = "Below target", "inverse"
                _irr_cols[i].metric(label, irr_str, delta=delta_str,
                                    delta_color=delta_col)
        except Exception as _e:
            st.info(f"Multi-period IRR requires transaction data. ({_e})")

        st.divider()
        colA, colB = st.columns([1.6, 1])

        with colA:
            st.markdown("#### Levered IRR Sensitivity (Exit Cap x Rent Growth)")
            st.caption(f"Base case = CENTRE cell  |  Capped at {IRR_CAP:.0%}  |  "
                       f"'MAX' = above cap (check assumptions)  |  Loan: {loan_type_key}")

            heatmap_df, capped_mask, base_r, base_c = build_irr_heatmap(
                metrics, deal, io_years, base_exit_cap, cpi_indexation,
                noi, loan_type_key
            )

            def style_heatmap(df):
                def cell_style(val):
                    if isinstance(val, str):
                        return "background:#1e3a5f; color:white; font-weight:700;"
                    return ""

                styled = df.style.background_gradient(cmap="RdYlGn", axis=None)

                # Format: show MAX for capped cells, % for others
                def fmt_cell(v):
                    if np.isnan(v): return "N/A"
                    if v >= IRR_CAP: return f"MAX ({v:.1%})"
                    return f"{v:.1%}"
                styled = styled.format(fmt_cell)

                # Highlight base case
                styled = styled.apply(
                    lambda col: [
                        "background-color:#1e3a5f; color:white; font-weight:700; font-size:0.9rem;"
                        if (col.name == df.columns[base_c] and idx == df.index[base_r]) else ""
                        for idx in col.index
                    ], axis=0
                )
                return styled

            st.dataframe(style_heatmap(heatmap_df), use_container_width=True)
            st.caption(f"Base: {base_exit_cap:.1%} exit cap  |  {cpi_indexation:.1%} indexation")

        with colB:
            st.markdown("#### 10-Year Cash Flow")
            irr_disp = f"{base_irr:.2%}" if not np.isnan(base_irr) else "N/A"
            st.metric("Base Levered IRR", irr_disp)
            true_equity = (metrics.get("total_acq_cost",0) or 0) - (loan or 0)
            st.metric("True Day-1 Equity", f"EUR {true_equity:,.0f}",
                      delta="incl. GrESt & costs")
            st.metric("Loan Type", loan_type_key)

            if not cf_df.empty:
                disp = cf_df[["Year","NOI (EUR)","Debt Service (EUR)",
                               "Net CF (EUR)","Cash-on-Cash (%)"]].copy()
                for c in ["NOI (EUR)","Debt Service (EUR)","Net CF (EUR)"]:
                    disp[c] = disp[c].apply(lambda x: f"EUR {x:,.0f}")
                disp["Cash-on-Cash (%)"] = disp["Cash-on-Cash (%)"].apply(lambda x: f"{x:.1%}")
                st.dataframe(disp, use_container_width=True, hide_index=True)
            else:
                st.info("Populate transaction data in Excel to see cash flows.")

        # ── ADDITIONAL VALUE MODULES ─────────────────────────────────────────
        st.divider()
        st.markdown("### 🏗️ Additional Value Opportunities")
        st.caption("Beyond solar, EV and ERV — three more value levers assessed from deal data.")

        _adv1, _adv2, _adv3 = st.columns(3)

        # ── Building Rights ──────────────────────────────────────────────────
        with _adv1:
            st.markdown("#### 🏘️ Building Rights")
            _br = _vc_building_rights(
                deal.get('city',''),
                metrics.get('gla_total') or deal.get('gla_sqm') or 0,
                deal.get('year_built'),
                deal.get('asset_type',''),
            )
            _br_colours = {'HIGH':'🟢','MEDIUM':'🟡','LOW':'🔴','UNKNOWN':'⚪'}
            st.metric("Potential", f"{_br_colours.get(_br['potential'],'⚪')} {_br['potential']}")
            if _br['est_gfa_m2'] > 0:
                st.metric("Est. Residential GFA", f"{_br['est_gfa_m2']:,} m²")
                st.metric("Est. Value Creation",
                          f"EUR {_br['val_low']:,.0f} – {_br['val_high']:,.0f}",
                          help="Conservative to optimistic net development value. Not in IC verdict.")
            st.caption(_br['rationale'])
            with st.expander("Next Step"):
                st.write(_br['action'])

        # ── Parking Monetisation ─────────────────────────────────────────────
        with _adv2:
            st.markdown("#### 🚗 Parking Monetisation")
            _pk_loc = st.selectbox("Car park location", ['HIGH','MEDIUM','LOW'],
                                   index=1, key='pk_loc',
                                   help='HIGH=urban/city centre. MEDIUM=suburban. LOW=rural.')
            _pk = _vc_parking_monetisation(deal.get('parking',0) or 0, _pk_loc)
            if _pk['total_annual'] > 0:
                st.metric("Additional Income/yr",
                          f"EUR {_pk['total_with_telecom']:,}",
                          delta=f"incl. telecom EUR {_pk['telecom']:,}")
                st.metric("CapEx Required", f"EUR {_pk['capex']:,}")
                st.metric("Payback", f"{_pk['payback_yrs']} yrs")
                _pk_df = pd.DataFrame([
                    {'Stream': k,
                     'Income/yr': f"EUR {v['income']:,}",
                     'Spaces': v['spaces'],
                     'CapEx': f"EUR {v['capex']:,}"}
                    for k,v in _pk['breakdown'].items()
                ])
                st.dataframe(_pk_df, hide_index=True, use_container_width=True)
            else:
                st.info("No parking spaces entered — add parking count to Excel to unlock this analysis.")

        # ── Functional Obsolescence ──────────────────────────────────────────
        with _adv3:
            st.markdown("#### 🏚️ Functional Obsolescence")
            _obs = _vc_obsolescence(
                deal.get('year_built'),
                metrics.get('gla_total') or deal.get('gla_sqm') or 0,
                deal.get('asset_type',''),
            )
            _obs_colours = {'Modern':'🟢','Good':'🟢','Adequate':'🟡',
                            'Ageing':'🟠','Dated':'🔴','Obsolescent':'🔴','Unknown':'⚪'}
            st.metric("Condition", f"{_obs_colours.get(_obs['condition'],'⚪')} {_obs['condition']}")
            if _obs['age']:
                st.metric("Asset Age", f"{_obs['age']} years")
            if _obs['discount_pct'] is not None and _obs['discount_pct'] > 0:
                st.metric("Appraiser Discount", f"{_obs['discount_pct']:.0%}",
                          help="Typical functional obsolescence discount vs modern equivalent stock (RICS/gif methodology).")
            st.caption(_obs['note'])
            st.caption(_obs['disc_note'])
            with st.expander("Action Required"):
                st.write(_obs['action'])

    # ---------------------------------------------------------------- TAB PORT
    with tab_port:  # ── PORTFOLIO ────────────────────────────────────────────
        st.subheader("Portfolio Comparison")
        st.caption(
            "Load up to 8 deals and compare key metrics side-by-side. "
            "Add the currently loaded deal, or upload additional Excel files. "
            "Colour coding matches the individual deal analysis thresholds."
        )

        # ── session state ─────────────────────────────────────────────────────
        if 'portfolio_deals' not in st.session_state:
            st.session_state['portfolio_deals'] = []

        # ── helper: run full pipeline on raw bytes ────────────────────────────
        def _port_run(raw_bytes, fname):
            try:
                _buf = io.BytesIO(raw_bytes)
                _d   = rrp.load_deal_overview(_buf)
                _rr  = rrp.load_rent_roll(_buf)
                _act = _rr[~_rr['is_vacant']]
                _vac = _rr[_rr['is_vacant']]
                _c   = rrp.load_cost_sheet(
                    _buf,
                    rr_gross_rent  = float(_act['annual_rent'].sum()),
                    rr_gla_total   = float(_rr['area_sqm'].sum()),
                    rr_vacant_area = float(_vac['area_sqm'].sum()),
                )
                _m   = rrp.compute_metrics(_d, _rr, _c)
                _qa  = rrp.run_qa_audit(_m, _d)
                _v   = rrp.generate_verdict(_m, _qa)

                _ln  = float(_m.get('loan_amount', 0) or 0)
                _ir  = float(_d.get('interest_rate', 0) or 0)
                _ar  = float(_d.get('amort_rate', 0) or 0)
                _iop = int(_d.get('io_period', 0) or 0)
                _nn  = float(_m.get('noi_proxy', 0) or 0)
                _ask = float(_m.get('asking_price', 0) or 0)
                _ec  = (float(_m.get('giy', 0) or 0.065) or 0.065) + 0.005

                if _ask > 0:
                    _m['niy'] = _nn / _ask

                _, _irr = calculate_10y_projection(_m, _d, _iop, _ec, 0.02, _nn, 'Annuity')

                _sv = 'N/A'
                if not _rr.empty and _ln > 0 and _nn > 0:
                    try:
                        _sc2 = _di_scenario_engine(
                            _m, _rr, _d, _nn, _ln, _ir, _ar,
                            _iop, 'Annuity', 0.02, stress_severity=2,
                        )
                        _sd2 = _sc2.get('stress', {}).get('dscr')
                        if _sd2 is not None:
                            _sv = ('SURVIVES' if _sd2 >= 1.20
                                   else ('MARGINAL' if _sd2 >= 1.00 else 'FAILS'))
                    except Exception:
                        pass

                return {
                    'fname':          fname,
                    'name':           str(_d.get('property_name', fname) or fname),
                    'city':           str(_d.get('city', '—') or '—'),
                    'asset_type':     str(_d.get('asset_type', '—') or '—'),
                    'asking_price':   _ask,
                    'giy':            float(_m.get('giy', 0) or 0),
                    'niy':            float(_m.get('niy', 0) or 0),
                    'dscr':           float(_m.get('dscr', 0) or 0),
                    'icr':            float(_m.get('icr', 0) or 0),
                    'ltv':            float(_m.get('ltv', 0) or 0),
                    'wault':          float(_m.get('wault', 0) or 0),
                    'vacancy':        float(_m.get('vacancy_rate', 0) or 0),
                    'irr_10yr':       (float(_irr) if isinstance(_irr, float) and not np.isnan(_irr) else None),
                    'verdict':        _v['recommendation'],
                    'stress_verdict': _sv,
                }
            except Exception as _pe:
                return {'error': str(_pe), 'fname': fname}

        # ── action bar ────────────────────────────────────────────────────────
        _pa1, _pa2, _pa3 = st.columns([2, 3, 1])

        with _pa1:
            _cur_key = f"__cur__{deal.get('property_name','')}"
            _already = any(e.get('fname') == _cur_key
                           for e in st.session_state['portfolio_deals'])
            if not _already:
                if st.button("➕  Add current deal", key='port_add_cur',
                             help="Add the currently loaded deal to the portfolio"):
                    _cur_sv = 'N/A'
                    if not rr.empty and loan > 0 and noi > 0:
                        try:
                            _c2 = _di_scenario_engine(
                                metrics, rr, deal, noi, loan, int_rt, amrt_rt,
                                io_years, loan_type_key, cpi_indexation,
                                stress_severity=2,
                            )
                            _s2d = _c2.get('stress', {}).get('dscr')
                            if _s2d is not None:
                                _cur_sv = ('SURVIVES' if _s2d >= 1.20
                                           else ('MARGINAL' if _s2d >= 1.00 else 'FAILS'))
                        except Exception:
                            pass
                    _cur_irr = (float(base_irr)
                                if isinstance(base_irr, float) and not np.isnan(base_irr)
                                else None)
                    st.session_state['portfolio_deals'].append({
                        'fname':          _cur_key,
                        'name':           str(deal.get('property_name', 'Current Deal') or 'Current Deal'),
                        'city':           str(deal.get('city', '—') or '—'),
                        'asset_type':     str(deal.get('asset_type', '—') or '—'),
                        'asking_price':   float(metrics.get('asking_price', 0) or 0),
                        'giy':            float(metrics.get('giy', 0) or 0),
                        'niy':            float(metrics.get('niy', 0) or 0),
                        'dscr':           float(metrics.get('dscr', 0) or 0),
                        'icr':            float(metrics.get('icr', 0) or 0),
                        'ltv':            float(metrics.get('ltv', 0) or 0),
                        'wault':          float(metrics.get('wault', 0) or 0),
                        'vacancy':        float(metrics.get('vacancy_rate', 0) or 0),
                        'irr_10yr':       _cur_irr,
                        'verdict':        verdict['recommendation'],
                        'stress_verdict': _cur_sv,
                    })
                    st.rerun()
            else:
                st.caption("✓ Current deal is in portfolio")

        with _pa2:
            if len(st.session_state['portfolio_deals']) < 8:
                _pup = st.file_uploader(
                    "Add deal from file (xlsx)",
                    type=['xlsx'], key='port_uploader',
                    label_visibility='collapsed',
                )
                if _pup is not None:
                    _pfn = _pup.name
                    if not any(e.get('fname') == _pfn
                               for e in st.session_state['portfolio_deals']):
                        with st.spinner(f"Processing {_pfn}…"):
                            _pres = _port_run(_pup.read(), _pfn)
                        if 'error' in _pres:
                            st.error(f"Could not process {_pfn}: {_pres['error']}")
                        else:
                            st.session_state['portfolio_deals'].append(_pres)
                            st.rerun()
                    else:
                        st.caption(f"✓ {_pfn} already loaded")
            else:
                st.info("Portfolio full — max 8 deals. Remove one to add another.")

        with _pa3:
            if st.button("🗑  Clear all", key='port_clear'):
                st.session_state['portfolio_deals'] = []
                if 'port_export_text' in st.session_state:
                    del st.session_state['port_export_text']
                st.rerun()

        _port = st.session_state['portfolio_deals']

        if not _port:
            st.info("No deals in portfolio yet. Use the buttons above to add deals.")
        else:
            # ── sort controls ──────────────────────────────────────────────
            _sort_map = {
                'Deal Name': 'name', 'Asking Price': 'asking_price',
                'GIY': 'giy', 'NIY': 'niy', 'DSCR': 'dscr', 'ICR': 'icr',
                'LTV': 'ltv', 'WAULT': 'wault', 'Vacancy': 'vacancy',
                'IRR (10yr)': 'irr_10yr', 'Verdict': 'verdict',
            }
            _srt1, _srt2 = st.columns([3, 1])
            with _srt1:
                _sort_by = st.selectbox(
                    "Sort", list(_sort_map.keys()), index=2,
                    key='port_sort', label_visibility='collapsed',
                )
            with _srt2:
                _sort_asc = st.toggle("Asc", value=False, key='port_asc')

            _sk = _sort_map[_sort_by]
            _sorted = sorted(
                _port,
                key=lambda x: (x.get(_sk) or 0) if _sk != 'name' else (x.get(_sk) or ''),
                reverse=not _sort_asc,
            )

            # ── colour helpers ─────────────────────────────────────────────
            _G, _A, _R = '#d1fae5','#fef3c7','#fee2e2'
            _Gf,_Af,_Rf = '#065f46','#92400e','#991b1b'
            _NU, _NUf  = '#f9fafb','#374151'

            def _port_color(metric, val):
                if val is None: return _NU, _NUf
                if metric == 'giy':
                    return (_G,_Gf) if val>=0.055 else ((_A,_Af) if val>=0.045 else (_R,_Rf))
                if metric == 'niy':
                    return (_G,_Gf) if val>=0.050 else ((_A,_Af) if val>=0.040 else (_R,_Rf))
                if metric == 'dscr':
                    return (_G,_Gf) if val>=1.30 else ((_A,_Af) if val>=1.20 else (_R,_Rf))
                if metric == 'icr':
                    return (_G,_Gf) if val>=1.75 else ((_A,_Af) if val>=1.50 else (_R,_Rf))
                if metric == 'ltv':
                    return (_G,_Gf) if val<=0.65 else ((_A,_Af) if val<=0.70 else (_R,_Rf))
                if metric == 'wault':
                    return (_G,_Gf) if val>=4.0 else ((_A,_Af) if val>=2.5 else (_R,_Rf))
                if metric == 'vacancy':
                    return (_G,_Gf) if val<=0.10 else ((_A,_Af) if val<=0.15 else (_R,_Rf))
                if metric == 'irr':
                    return (_G,_Gf) if val>=0.08 else ((_A,_Af) if val>=0.05 else (_R,_Rf))
                if metric == 'verdict':
                    return ({
                        'PASS':(_G,_Gf),'INVESTIGATE':(_A,_Af),'DECLINE':(_R,_Rf)
                    }.get(val, (_NU,_NUf)))
                if metric == 'stress':
                    return ({
                        'SURVIVES':(_G,_Gf),'MARGINAL':(_A,_Af),'FAILS':(_R,_Rf)
                    }.get(val, (_NU,_NUf)))
                return _NU, _NUf

            # ── comparison table ───────────────────────────────────────────
            DEAL_COLORS = [
                '#2563eb','#dc2626','#16a34a','#d97706',
                '#7c3aed','#0891b2','#be185d','#4b5563',
            ]
            _TDS = 'padding:7px 10px;border:1px solid #e5e7eb;font-size:0.79rem;'
            _THS = ('padding:8px 10px;background:#1e3a5f;color:#fff;'
                    'font-size:0.75rem;font-weight:600;border:1px solid #172f4c;'
                    'text-align:left;white-space:nowrap;')

            def _th_h(t): return f'<th style="{_THS}">{t}</th>'
            def _td_n(v): return f'<td style="{_TDS}background:{_NU};color:{_NUf};">{v}</td>'
            def _td_m(metric, val, fmt):
                bg, fg = _port_color(metric, val)
                disp   = fmt(val) if val is not None else '—'
                return (f'<td style="{_TDS}background:{bg};color:{fg};'
                        f'font-weight:600;">{disp}</td>')

            _p  = lambda v: f"{v:.1%}"  if v is not None else '—'
            _px = lambda v: f"{v:.2f}x" if v is not None else '—'
            _yr = lambda v: f"{v:.1f}yr" if v is not None else '—'
            _mm = lambda v: (f"EUR {v/1e6:.2f}M" if v >= 1e6 else f"EUR {v:,.0f}") if v else '—'

            _cols = [
                '','Deal','City','Type','Price',
                'GIY','NIY','DSCR','ICR','LTV',
                'WAULT','Vacancy','IRR 10yr',
                'IC Verdict','Stress',
            ]
            _thead = ''.join(_th_h(c) for c in _cols)
            _tbody = []

            for _idx, _pe in enumerate(_sorted):
                _dot = (f'<span style="display:inline-block;width:11px;height:11px;'
                        f'border-radius:50%;background:{DEAL_COLORS[_idx%8]};"></span>')
                _bv, _fv2 = _port_color('verdict', _pe.get('verdict'))
                _bs, _fs  = _port_color('stress',  _pe.get('stress_verdict'))
                _tbody.append(
                    f'<tr>'
                    f'<td style="{_TDS}text-align:center;">{_dot}</td>'
                    f'<td style="{_TDS}font-weight:600;max-width:160px;">{_pe.get("name","?")[:26]}</td>'
                    f'<td style="{_TDS}max-width:120px;">{_pe.get("city","—")[:18]}</td>'
                    f'<td style="{_TDS}color:#6b7280;font-size:0.72rem;">{_pe.get("asset_type","—")[:20]}</td>'
                    f'{_td_n(_mm(_pe.get("asking_price")))}'
                    f'{_td_m("giy",     _pe.get("giy"),     _p)}'
                    f'{_td_m("niy",     _pe.get("niy"),     _p)}'
                    f'{_td_m("dscr",    _pe.get("dscr"),    _px)}'
                    f'{_td_m("icr",     _pe.get("icr"),     _px)}'
                    f'{_td_m("ltv",     _pe.get("ltv"),     _p)}'
                    f'{_td_m("wault",   _pe.get("wault"),   _yr)}'
                    f'{_td_m("vacancy", _pe.get("vacancy"), _p)}'
                    f'{_td_m("irr",     _pe.get("irr_10yr"),_p)}'
                    f'<td style="{_TDS}background:{_bv};color:{_fv2};font-weight:700;">'
                    f'{_pe.get("verdict","—")}</td>'
                    f'<td style="{_TDS}background:{_bs};color:{_fs};font-weight:600;">'
                    f'{_pe.get("stress_verdict","—")}</td>'
                    f'</tr>'
                )

            st.markdown(
                '<div style="overflow-x:auto;margin-bottom:8px;">'
                '<table style="border-collapse:collapse;width:100%;font-family:sans-serif;">'
                f'<thead><tr>{_thead}</tr></thead>'
                f'<tbody>{"".join(_tbody)}</tbody>'
                '</table></div>',
                unsafe_allow_html=True,
            )

            # ── per-deal remove buttons ────────────────────────────────────
            st.markdown("")
            _rm_cols = st.columns(min(len(_sorted), 4))
            for _ri, _pe in enumerate(_sorted):
                with _rm_cols[_ri % 4]:
                    if st.button(
                        f"✕ {_pe.get('name','?')[:18]}",
                        key=f'port_rm_{_pe.get("fname",str(_ri))}',
                        use_container_width=True,
                    ):
                        st.session_state['portfolio_deals'] = [
                            x for x in st.session_state['portfolio_deals']
                            if x.get('fname') != _pe.get('fname')
                        ]
                        st.rerun()

            # ── best deal highlights ───────────────────────────────────────
            st.divider()
            st.markdown("##### Best Deal Highlights")
            _vld = [p for p in _port if p.get('giy', 0) > 0]
            if _vld:
                _b_giy   = max(_vld, key=lambda x: x.get('giy', 0))
                _b_dscr  = max(_vld, key=lambda x: x.get('dscr', 0))
                _b_wault = max(_vld, key=lambda x: x.get('wault', 0))
                _b_ltv   = min(_vld, key=lambda x: x.get('ltv', 1.0))
                _bh1, _bh2, _bh3, _bh4 = st.columns(4)
                _bh1.metric("Highest GIY",    f"{_b_giy['giy']:.1%}",
                             _b_giy['name'][:22])
                _bh2.metric("Strongest DSCR", f"{_b_dscr['dscr']:.2f}x",
                             _b_dscr['name'][:22])
                _bh3.metric("Longest WAULT",  f"{_b_wault['wault']:.1f}yr",
                             _b_wault['name'][:22])
                _bh4.metric("Lowest LTV",     f"{_b_ltv['ltv']:.1%}",
                             _b_ltv['name'][:22])
                _p_cnt  = sum(1 for p in _vld if p.get('verdict')=='PASS')
                _i_cnt  = sum(1 for p in _vld if p.get('verdict')=='INVESTIGATE')
                _d_cnt  = sum(1 for p in _vld if p.get('verdict')=='DECLINE')
                _a_giy  = sum(p['giy'] for p in _vld) / len(_vld)
                _a_dscr = sum(p.get('dscr', 0) for p in _vld) / len(_vld)
                st.caption(
                    f"Portfolio: {len(_vld)} deals  ·  "
                    f"{_p_cnt} PASS  {_i_cnt} INVESTIGATE  {_d_cnt} DECLINE  |  "
                    f"Avg GIY {_a_giy:.1%}  ·  Avg DSCR {_a_dscr:.2f}x"
                )

            # ── radar chart ────────────────────────────────────────────────
            if len(_port) >= 2:
                st.divider()
                st.markdown("##### Deal Quality Radar")
                st.caption(
                    "Each axis 0–100. "
                    "GIY: 0% → 0, 8%+ → 100. "
                    "DSCR: 1.0x → 0, 2.0x+ → 100. "
                    "WAULT: 0yr → 0, 10yr+ → 100. "
                    "LTV (inv): 80%+ → 0, 0% → 100. "
                    "Vacancy (inv): 40%+ → 0, 0% → 100."
                )
                try:
                    import matplotlib
                    matplotlib.use('Agg')
                    import matplotlib.pyplot as _mplt

                    _rlbls  = ['GIY', 'DSCR', 'WAULT', 'LTV\n(inv)', 'Vacancy\n(inv)']
                    _rN     = len(_rlbls)
                    _ra     = [n / float(_rN) * 2 * np.pi for n in range(_rN)]
                    _ra    += _ra[:1]

                    _rfig, _rax = _mplt.subplots(
                        figsize=(6.2, 5.2), subplot_kw=dict(polar=True)
                    )
                    _rfig.patch.set_facecolor('#f9fafb')
                    _rax.set_facecolor('#f9fafb')

                    for _ri2, _rp in enumerate(_port[:8]):
                        _sc = [
                            min(float(_rp.get('giy', 0) or 0) / 0.08, 1.0),
                            min(max((float(_rp.get('dscr', 0) or 0) - 1.0) / 1.0, 0.0), 1.0),
                            min(float(_rp.get('wault', 0) or 0) / 10.0, 1.0),
                            max(1.0 - float(_rp.get('ltv', 1.0) or 1.0) / 0.80, 0.0),
                            max(1.0 - float(_rp.get('vacancy', 1.0) or 1.0) / 0.40, 0.0),
                        ]
                        _sc += _sc[:1]
                        _rc = DEAL_COLORS[_ri2 % 8]
                        _rax.plot(_ra, _sc, color=_rc, linewidth=2,
                                  label=_rp.get('name', '?')[:24])
                        _rax.fill(_ra, _sc, color=_rc, alpha=0.09)

                    _rax.set_xticks(_ra[:-1])
                    _rax.set_xticklabels(_rlbls, size=9, color='#1f2937')
                    _rax.set_ylim(0, 1)
                    _rax.set_yticks([0.25, 0.50, 0.75, 1.0])
                    _rax.set_yticklabels(['25', '50', '75', '100'],
                                          size=7, color='#9ca3af')
                    _rax.grid(color='#e5e7eb', linewidth=0.7)
                    _rax.spines['polar'].set_color('#d1d5db')
                    _rax.legend(
                        loc='upper right',
                        bbox_to_anchor=(1.60, 1.18),
                        fontsize=8.5, framealpha=0.9,
                    )
                    _mplt.tight_layout(pad=1.2)

                    _rc1, _rc2 = st.columns([5, 3])
                    with _rc1:
                        st.pyplot(_rfig, use_container_width=False)
                    with _rc2:
                        st.markdown("**Score guide**")
                        st.markdown(
                            "- **100** = exceeds all benchmarks\n"
                            "- **75** = comfortably passing\n"
                            "- **50** = at the warn threshold\n"
                            "- **25** = approaching hard fail\n"
                            "- **0** = at or below hard fail\n\n"
                            "A larger filled polygon = stronger overall deal. "
                            "Compare shapes to see where each deal "
                            "outperforms or underperforms on individual axes."
                        )
                    _mplt.close(_rfig)
                except Exception as _re:
                    st.warning(f"Radar chart unavailable: {_re}")

            # ── export to Claude ───────────────────────────────────────────
            st.divider()
            st.markdown("##### Export Portfolio to Claude")
            if st.button("Generate Portfolio Export", type='primary',
                         key='port_claude_btn'):
                _pl = []
                _pH  = lambda t: _pl.append(f"\n{'='*68}\n{t}\n{'='*68}")
                _pH2 = lambda t: _pl.append(f"\n--- {t} ---")
                _pKV = lambda k, v: _pl.append(f"  {k:<30}: {v}")
                _pLI = lambda s: _pl.append(f"    • {s}")

                _pl.append("=" * 68)
                _pl.append("  GERMAN RETAIL CRE — PORTFOLIO COMPARISON")
                _pl.append(
                    f"  {len(_port)} deal(s)  |  "
                    f"Generated {pd.Timestamp.today().strftime('%d %B %Y')}"
                )
                _pl.append("=" * 68)

                _pH("PORTFOLIO SUMMARY")
                _pKV("Total Deals", str(len(_port)))
                _pKV("PASS",        str(sum(1 for p in _port if p.get('verdict')=='PASS')))
                _pKV("INVESTIGATE", str(sum(1 for p in _port if p.get('verdict')=='INVESTIGATE')))
                _pKV("DECLINE",     str(sum(1 for p in _port if p.get('verdict')=='DECLINE')))
                _vld2 = [p for p in _port if p.get('giy', 0) > 0]
                if _vld2:
                    _pKV("Avg GIY",   f"{sum(p['giy'] for p in _vld2)/len(_vld2):.1%}")
                    _pKV("Avg DSCR",  f"{sum(p.get('dscr',0) for p in _vld2)/len(_vld2):.2f}x")
                    _pKV("Avg WAULT", f"{sum(p.get('wault',0) for p in _vld2)/len(_vld2):.1f}yr")
                    _pKV("Avg LTV",   f"{sum(p.get('ltv',0) for p in _vld2)/len(_vld2):.1%}")
                    _b2 = max(_vld2, key=lambda x: x.get('giy',0))
                    _pKV("Best GIY deal", f"{_b2['name']} ({_b2['giy']:.1%})")

                for _n2, _pe2 in enumerate(_port, 1):
                    _pH(f"DEAL {_n2} — {_pe2.get('name','?').upper()}")
                    _pKV("City",           _pe2.get('city','—'))
                    _pKV("Asset Type",     _pe2.get('asset_type','—'))
                    _pKV("Asking Price",   f"EUR {_pe2.get('asking_price',0):,.0f}")
                    _pH2("Income & Yield")
                    _pKV("GIY",            f"{_pe2.get('giy',0):.2%}")
                    _pKV("NIY",            f"{_pe2.get('niy',0):.2%}")
                    _pH2("Debt Metrics")
                    _pKV("DSCR (Yr 1)",    f"{_pe2.get('dscr',0):.2f}x")
                    _pKV("ICR (Yr 1)",     f"{_pe2.get('icr',0):.2f}x")
                    _pKV("LTV",            f"{_pe2.get('ltv',0):.1%}")
                    _pH2("Asset Quality")
                    _pKV("WAULT",          f"{_pe2.get('wault',0):.1f}yr")
                    _pKV("Vacancy",        f"{_pe2.get('vacancy',0):.1%}")
                    _pH2("Returns & Verdict")
                    _irr3 = _pe2.get('irr_10yr')
                    _pKV("IRR (10yr)",     f"{_irr3:.1%}" if _irr3 else '—')
                    _pKV("IC Verdict",     _pe2.get('verdict','—'))
                    _pKV("Stress Verdict", _pe2.get('stress_verdict','—'))

                st.session_state['port_export_text'] = '\n'.join(_pl)

            if st.session_state.get('port_export_text'):
                st.markdown(
                    '<div style="background:#fef3c7;border:2px solid #f59e0b;'
                    'border-radius:8px;padding:10px 16px;margin:8px 0;'
                    'font-weight:600;color:#92400e;">'
                    '📋 PASTE INTO CLAUDE — copy the text below</div>',
                    unsafe_allow_html=True,
                )
                st.text_area(
                    "portfolio_export",
                    value=st.session_state['port_export_text'],
                    height=280,
                    key='port_export_ta',
                    label_visibility='collapsed',
                )

    # ------------------------------------------------------------------ TAB 4
    with tab4:
        st.subheader("Terminology Textbook -- 19 Core Terms")
        render_glossary(metrics, deal, y1_dscr, y1_icr, noi, loan_type_key)

    # ------------------------------------------------------------------ TAB 5
    with tab5:
        # ── Document Checklist + Data Completeness Scanner ─────────────────
        st.markdown("#### 📋 Seller Document Checklist")
        st.caption(
            "Standard documents a seller provides for pre-DD underwriting. "
            "Tick what you have received. 🔴 = critical for reliable analysis."
        )
        _doc_categories = {
            "📊 Financial": [
                ("Rent roll (current)",         True,  "Annual rent, area, expiry per tenant"),
                ("Operating cost statement",    True,  "Actual non-recoverable costs last 12 months"),
                ("Service charge reconciliation",False,"What tenants pay vs landlord receives"),
            ],
            "🏢 Property": [
                ("Exposé / Info Memorandum",    True,  "GLA, year built, location, parking"),
                ("Floor plans / GLA survey",    True,  "Verified GLA per unit"),
                ("Energy Performance Cert",     False, "Required for ESG / KfW grant eligibility"),
                ("Building survey",             False, "Hidden CapEx — critical for pricing"),
                ("Grundbuchauszug",             False, "Land registry — confirms Erbpacht, encumbrances"),
            ],
            "📄 Leases": [
                ("All current lease agreements",True,  "Exact rent, expiry, break options, CPI clause"),
                ("Side letters / amendments",   False, "Rent-free periods, capex contributions"),
                ("Tenant correspondence",       False, "Renewal / exit signals"),
            ],
            "📝 Legal": [
                ("Draft purchase contract",     False, "GrESt structure, warranties, conditions"),
                ("Previous DD reports",         False, "Prior buyer due diligence"),
            ],
        }
        _total_docs = _critical_docs = _have_critical = 0
        _checked_state = {}
        for _cat, _docs in _doc_categories.items():
            st.markdown(f"**{_cat}**")
            for _dn, _ic, _why in _docs:
                _total_docs += 1
                if _ic: _critical_docs += 1
                _ca, _cb, _cc = st.columns([0.05, 0.45, 0.50])
                with _ca:
                    _chk = st.checkbox("", key=f"doc_{hash(_dn)}", value=_ic)
                    _checked_state[_dn] = _chk
                    if _ic and _chk: _have_critical += 1
                with _cb:
                    st.markdown(f"🔴 **{_dn}**" if _ic else f"⚪ {_dn}")
                with _cc:
                    st.caption(_why)
        _total_chk   = sum(_checked_state.values())
        _completeness = _total_chk / _total_docs if _total_docs > 0 else 0
        _crit_pct    = _have_critical / _critical_docs if _critical_docs > 0 else 0
        st.divider()
        _sc1, _sc2, _sc3 = st.columns(3)
        _sc1.metric("Documents Received", f"{_total_chk}/{_total_docs}", delta=f"{_completeness:.0%} of full set")
        _sc2.metric("Critical Docs", f"{_have_critical}/{_critical_docs}",
                    delta="✅ Complete" if _crit_pct>=1 else "⚠️ Missing critical docs",
                    delta_color="normal" if _crit_pct>=1 else "inverse")
        _sc3.metric("Analysis Reliability",
                    "High" if _crit_pct>=1 else ("Medium" if _crit_pct>=0.6 else "Low"),
                    delta_color="normal" if _crit_pct>=1 else ("off" if _crit_pct>=0.6 else "inverse"))

        st.divider()
        st.markdown("#### 🔍 Missing Data — What to Request From Seller")
        _missing = []; _warnings = []
        if not deal.get('parking') or deal.get('parking',0)==0:
            _missing.append(("🚗 Parking count", "Needed for EV charging analysis", "Ask: Anzahl Stellplätze"))
        if not deal.get('year_built'):
            _missing.append(("🏗️ Year built", "Affects CapEx reserve benchmark", "Ask: Baujahr"))
        if metrics.get('noi_source') != 'cost_sheet':
            _missing.append(("💰 Itemised costs", "NOI is estimated — ask for actual Betriebskostenabrechnung", "Ask: IST-Kosten last 12 months"))
        if metrics.get('vacancy_rate',0) > 0:
            _warnings.append(f"📭 Vacancy {metrics.get('vacancy_rate',0):.1%} — ask for re-letting history and current negotiations")
        if metrics.get('wault',0) < 5:
            _warnings.append(f"⏰ WAULT {metrics.get('wault',0):.1f}yr — request written renewal intention from anchor tenant")
        if metrics.get('anchor_pct',0) > 0.55:
            _warnings.append(f"⚓ Anchor {metrics.get('anchor_pct',0):.1%} — request last 3 years turnover figures if available")
        if _missing:
            for _f,_r,_a in _missing:
                _ma,_mb,_mc = st.columns([0.25,0.40,0.35])
                _ma.markdown(f"**{_f}**"); _mb.caption(_r); _mc.caption(f"_{_a}_")
        if _warnings:
            for _w in _warnings: st.warning(_w)
        if not _missing and not _warnings:
            st.success("✅ Deal data appears complete. No missing fields detected.")

        st.divider()
        st.subheader("Export -- IC Package")
        slug = (deal.get("property_name") or "deal").replace(" ", "_")

        xl = build_export_excel(deal, metrics, qa, verdict, cf_df,
                                base_irr, noi, loan_type_key)
        st.download_button(
            "Download IC Summary (Excel)", xl,
            f"{slug}_IC_Summary.xlsx",
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )

        if not rr.empty:
            st.download_button(
                "Download Rent Roll (CSV)", rr.to_csv(index=False).encode(),
                f"{slug}_RentRoll.csv", "text/csv"
            )

        # Text memo
        niy_txt = f"{noi/asking:.2%}" if asking > 0 else "--"
        memo = "\n".join([
            "=" * 68,
            "  INVESTMENT COMMITTEE MEMO",
            f"  Property : {deal.get('property_name','--')}",
            f"  Date     : {pd.Timestamp.today().strftime('%d %B %Y')}",
            "=" * 68,
            "",
            f"  Asking Price          : EUR {metrics['asking_price']:,.0f}",
            f"  Total Acq. Cost       : EUR {metrics['total_acq_cost']:,.0f}",
            f"  LTV                   : {metrics['ltv']:.1%}",
            f"  Gross Rent / yr       : EUR {gross_rent:,.0f}",
            f"  Void Costs / yr       : EUR {metrics['void_costs']:,.0f}",
            f"  NOI / yr              : EUR {noi:,.0f}  ({noi_margin:.1%} margin)",
            f"  NOI Source            : {noi_source}",
            f"  GIY                   : {metrics['giy']:.2%}" if metrics.get("giy") else "  GIY                   : --",
            f"  NIY                   : {niy_txt}",   # FIX 4
            f"  DSCR (Yr 1)           : {y1_dscr:.2f}x" if y1_dscr else "  DSCR (Yr 1)           : --",
            f"  ICR  (Yr 1)           : {y1_icr:.2f}x"  if y1_icr  else "  ICR  (Yr 1)           : --",
            f"  WAULT                 : {metrics['wault']:.2f} yrs",
            f"  Vacancy               : {metrics['vacancy_rate']:.1%}",
            f"  Anchor Conc.          : {metrics['anchor_pct']:.1%}",
            f"  Loan Type             : {loan_type_key}",
            f"  Base Levered IRR      : {base_irr:.2%}" if not np.isnan(base_irr) else "  Base Levered IRR      : N/A",
            "",
            "  QA AUDIT",
        ] + [
            f"  [{r.status:4s}]  {r.check:<42} {r.actual}  (benchmark {r.benchmark})"
            for r in qa
        ] + [
            "",
            "=" * 68,
            f"  VERDICT:  {verdict['recommendation']}",
            f"  {verdict['rationale']}",
        ] + (["  RED FLAGS:"] + [f"    {f}" for f in verdict["red_flags"]]
             if verdict["red_flags"] else []) +
        ["  KEY FACTORS:"] + [f"    {r}" for r in verdict["top_reasons"]] +
        ["=" * 68]
        )

        st.download_button(
            "Download IC Memo (Text)", memo.encode(),
            f"{slug}_IC_Memo.txt", "text/plain"
        )

        # ── Export to Claude ──────────────────────────────────────────────────
        st.divider()
        st.markdown("#### Export to Claude")
        st.caption(
            "Generate a complete structured summary of all 11 Deep Insights modules. "
            "Copy and paste into Claude chat to ask follow-up questions about the deal."
        )

        if st.button("Generate Claude Export", type="primary", key="gen_claude_export"):
            import datetime as _cex_dt
            _lines = []
            _H  = lambda t: _lines.append(f"\n{'=' * 68}\n{t}\n{'=' * 68}")
            _H2 = lambda t: _lines.append(f"\n--- {t} ---")
            _KV = lambda k, v: _lines.append(f"  {k:<34}: {v}")
            _LI = lambda s: _lines.append(f"    • {s}")

            # ── Header
            _lines.append("=" * 68)
            _lines.append("  GERMAN RETAIL CRE UNDERWRITER — FULL DEAL ANALYSIS")
            _lines.append(f"  Property : {deal.get('property_name', '--')}")
            _lines.append(f"  Location : {deal.get('city', '--')}")
            _lines.append(f"  Asset    : {deal.get('asset_type', '--')}")
            _lines.append(f"  Date     : {_cex_dt.date.today().strftime('%d %B %Y')}")
            _lines.append("=" * 68)

            # ── Verdict
            _H("VERDICT")
            _KV("Recommendation", verdict['recommendation'])
            _KV("Rationale",      verdict['rationale'])

            # Three-verdict summary in export
            try:
                import datetime as _tdt_ex
                # Stress Survival (Severity 2)
                _ex_stress_dscr = None
                if not rr.empty and loan > 0 and y1_debt_svc > 0:
                    try:
                        _ex_sc2 = _di_scenario_engine(
                            metrics, rr, deal, noi, loan, int_rt, amrt_rt,
                            io_years, loan_type_key, cpi_indexation,
                            stress_severity=2,
                        )
                        _ex_stress_dscr = _ex_sc2.get('stress', {}).get('dscr')
                    except Exception:
                        pass

                if _ex_stress_dscr is None:
                    _ex_ss = "N/A"
                elif _ex_stress_dscr >= 1.20:
                    _ex_ss = f"SURVIVES ({_ex_stress_dscr:.2f}x)"
                elif _ex_stress_dscr >= 1.00:
                    _ex_ss = f"MARGINAL ({_ex_stress_dscr:.2f}x)"
                else:
                    _ex_ss = f"FAILS ({_ex_stress_dscr:.2f}x)"

                # RI-Adjusted
                _ex_ri_label = "No signals entered"
                if not rr.empty and y1_debt_svc > 0:
                    _cutoff_ex = _tdt_ex.date.today() + _tdt_ex.timedelta(days=3*365)
                    _act_ex = rr[~rr.get('is_vacant', pd.Series(False, index=rr.index))].copy() if not rr.empty else pd.DataFrame()
                    _qual_ex = []
                    for _, _rex in _act_ex.iterrows():
                        _hb_ex = False
                        _en_ex = False
                        if 'break_option' in _rex.index:
                            _bo_ex = _rex.get('break_option')
                            if _bo_ex is not None and not (isinstance(_bo_ex, float) and pd.isna(_bo_ex)):
                                try:
                                    _bd_ex = _bo_ex.date() if hasattr(_bo_ex, 'date') else _bo_ex
                                    if isinstance(_bd_ex, _tdt_ex.date): _hb_ex = True
                                except Exception:
                                    pass
                        _lex = _rex.get('lease_expiry')
                        if _lex is not None and not (isinstance(_lex, float) and pd.isna(_lex)):
                            try:
                                _led = _lex.date() if hasattr(_lex, 'date') else _lex
                                if isinstance(_led, _tdt_ex.date) and _led <= _cutoff_ex: _en_ex = True
                            except Exception:
                                pass
                        if _hb_ex or _en_ex:
                            _qual_ex.append(_rex)

                    if _qual_ex:
                        _tk_ex  = '_'.join(str(_r.get('tenant','')) for _r in _qual_ex)
                        _rsk_ex = f'ri_state_{hash(_tk_ex) & 0xFFFFFF}'
                        _rid_ex = st.session_state.get(_rsk_ex, {})
                        _any_sig_ex = any(
                            _rid_ex.get(f'ri_sig_{str(_r.get("tenant","?"))}', 'AMBER') != 'AMBER'
                            for _r in _qual_ex
                        )
                        if _any_sig_ex:
                            _adj_ex = noi
                            for _r in _qual_ex:
                                _tn_ex = str(_r.get('tenant', '?'))
                                _sg_ex = _rid_ex.get(f'ri_sig_{_tn_ex}', 'AMBER')
                                if _sg_ex != 'GREEN':
                                    _adj_ex -= float(_r.get('annual_rent', 0) or 0)
                                    _adj_ex -= float(_r.get('area_sqm', 0) or 0) * 20.0
                            _ri_dscr_ex = _adj_ex / y1_debt_svc
                            if _ri_dscr_ex >= 1.30:
                                _ex_ri_label = f"PASS ({_ri_dscr_ex:.2f}x RI-adjusted)"
                            elif _ri_dscr_ex >= 1.20:
                                _ex_ri_label = f"MARGINAL ({_ri_dscr_ex:.2f}x RI-adjusted)"
                            else:
                                _ex_ri_label = f"WEAK ({_ri_dscr_ex:.2f}x RI-adjusted)"

                _H2("Three-Verdict Summary")
                _KV("Base Verdict",     verdict['recommendation'])
                _KV("Stress Survival",  _ex_ss)
                _KV("RI-Adjusted",      _ex_ri_label)
            except Exception:
                pass
            if verdict.get("red_flags"):
                _H2("Red Flags")
                for _rf in verdict["red_flags"]: _LI(_rf)
            if verdict.get("top_reasons"):
                _H2("Key Factors")
                for _tr in verdict["top_reasons"]: _LI(_tr)

            # ── Module 1 / 2 · Core Metrics (NOI Bridge + financials)
            _H("MODULE 1-2 · NOI BRIDGE & CORE METRICS")
            _KV("Asking Price",         f"EUR {metrics.get('asking_price',0):,.0f}")
            _KV("Loan Amount",          f"EUR {metrics.get('loan_amount',0):,.0f}")
            _KV("Equity",               f"EUR {metrics.get('equity',0):,.0f}")
            _KV("Gross Rent / yr",      f"EUR {metrics.get('gross_passing_rent',0):,.0f}")
            _KV("Void Costs / yr",      f"EUR {metrics.get('void_costs',0):,.0f}")
            _noi_disp = metrics.get('noi_proxy', 0) or 0
            _KV("NOI / yr",             f"EUR {_noi_disp:,.0f}  ({metrics.get('noi_margin',0):.1%} margin)")
            _KV("NOI Source",           metrics.get('noi_source', '--'))
            _KV("LTV",                  f"{metrics.get('ltv',0):.1%}")
            _KV("GIY",                  f"{metrics.get('giy',0):.2%}" if metrics.get('giy') else '--')
            _KV("NIY",                  f"{metrics.get('niy',0):.2%}" if metrics.get('niy') else '--')
            _KV("DSCR (Yr 1)",          f"{y1_dscr:.2f}x" if y1_dscr else '--')
            _KV("ICR  (Yr 1)",          f"{y1_icr:.2f}x"  if y1_icr  else '--')
            _KV("Debt Yield",           f"{metrics.get('debt_yield',0):.2%}" if metrics.get('debt_yield') else '--')
            _KV("WAULT",                f"{metrics.get('wault',0):.1f} yr")
            _KV("Vacancy",              f"{metrics.get('vacancy_rate',0):.1%}")
            _KV("Anchor Concentration", f"{metrics.get('anchor_pct',0):.1%}")
            _KV("3-Year Expiry Risk",   f"{metrics.get('expiry_risk_3yr_pct',0):.1%}")
            _KV("Base Levered IRR",     f"{base_irr:.1%}" if not np.isnan(base_irr) else 'N/A')
            _KV("Loan Type",            loan_type_key)
            _KV("Interest Rate",        f"{int_rt:.2%}")
            _KV("GLA",                  f"{metrics.get('gla_total',0):,.0f} m²")

            # ── Module 3 · QA Audit
            _H("MODULE 3 · QA AUDIT")
            for _qr in qa:
                _icon = "PASS" if _qr.status == "PASS" else ("WARN" if _qr.status == "WARN" else "FAIL")
                _lines.append(f"  [{_icon}]  {_qr.check:<42} {_qr.actual:<14} (benchmark {_qr.benchmark})")

            # ── Module 4 · True Yield Gap
            _H("MODULE 4 · TRUE YIELD GAP")
            try:
                if not rr.empty:
                    _yg = _di_true_yield_gap(metrics, rr)
                    if _yg:
                        _KV("Vacant Area",          f"{_yg.get('vacant_area',0):,.0f} m²")
                        _KV("Portfolio ERV/m²/mo",  f"EUR {_yg.get('avg_rent_sqm_mo',0):.2f}")
                        _KV("Void ERV Upside/yr",   f"EUR {_yg.get('erv_void_income',0):,.0f}")
                        _KV("Stabilised GIY",       f"{_yg.get('stabilised_giy',0):.2%}")
                        _KV("Yield Gap vs Entry",   f"{_yg.get('yield_gap',0)*100:+.0f} bps")
                    else:
                        _lines.append("  No void space — fully let.")
                else:
                    _lines.append("  No rent roll data.")
            except Exception as _e:
                _lines.append(f"  [error: {_e}]")

            # ── Module 5 · Income Cliff
            _H("MODULE 5 · INCOME CLIFF")
            try:
                if not rr.empty and y1_debt_svc > 0:
                    _ic = _di_income_cliff(rr, y1_debt_svc)
                    _cm = _ic.get('cliff_month')
                    if _cm:
                        _KV("Cliff Month",     f"Month {_cm} ({_cm/12:.1f} yrs)")
                        _KV("Rent at Cliff",   f"EUR {_ic.get('cliff_rent',0):,.0f}/mo")
                        _KV("Monthly DS",      f"EUR {_ic.get('monthly_ds',0):,.0f}/mo")
                        _KV("Coverage",        f"{_ic.get('coverage_ratio',0):.2f}x at cliff")
                    else:
                        _lines.append("  No income cliff within projection window.")
                else:
                    _lines.append("  No data / no debt service entered.")
            except Exception as _e:
                _lines.append(f"  [error: {_e}]")

            # ── Module 6 · Break Option Stress
            _H("MODULE 6 · BREAK OPTION STRESS")
            try:
                if not rr.empty and loan > 0:
                    _brk = _di_break_stress(rr, noi, loan, int_rt, amrt_rt)
                    if not _brk.empty:
                        for _, _br in _brk.iterrows():
                            _lines.append(
                                f"  {str(_br.get('Tenant','?'))[:30]:<30} "
                                f"Break: {_br.get('Break Year','?')}  "
                                f"Rent: {_br.get('Rent at Risk','?')}  "
                                f"Post-Break DSCR: {_br.get('Post-Break DSCR','?')}  "
                                f"[{_br.get('Status','?')}]"
                            )
                    else:
                        _lines.append("  No break options in rent roll.")
                else:
                    _lines.append("  No rent roll / no loan data.")
            except Exception as _e:
                _lines.append(f"  [error: {_e}]")

            # ── Module 7 · Refinancing Screen
            _H("MODULE 7 · REFINANCING SCREEN")
            try:
                if loan > 0 and int_rt > 0:
                    _refi = _di_refi_risk(
                        metrics, noi, loan, int_rt, amrt_rt,
                        io_years, 5, loan_type_key, cpi_indexation,
                    )
                    if _refi:
                        _KV("Loan Balance at Yr 5",  f"EUR {_refi.get('loan_balance',0):,.0f}")
                        _KV("Projected NOI at Yr 5",  f"EUR {_refi.get('projected_noi',0):,.0f}")
                        _KV("Exit Value at 65% LTV",  f"EUR {_refi.get('exit_val_65ltv',0):,.0f}")
                        _KV("Implied Cap at Refi",    f"{_refi.get('implied_cap',0):.2%}" if _refi.get('implied_cap') else '--')
                        _KV("Refi Flag",              str(_refi.get('flag','None')))
                    else:
                        _lines.append("  Insufficient data for refi screen.")
                else:
                    _lines.append("  No loan data entered.")
            except Exception as _e:
                _lines.append(f"  [error: {_e}]")

            # ── Module 8 · Share Deal / GrESt  (calculated inline — avoids key mismatch)
            _H("MODULE 8 · SHARE DEAL (GREST)")
            try:
                _sd_asking     = float(metrics.get('asking_price', 0) or 0)
                _sd_equity     = float(metrics.get('equity', 0) or 0)
                _sd_grest_rate = float(deal.get('grest_rate', 0.055) or 0.055)
                _sd_grest_asset     = _sd_asking * _sd_grest_rate
                _sd_share_legal     = _sd_asking * 0.005
                _sd_net_saving      = _sd_grest_asset - _sd_share_legal
                _sd_total_acq_asset = _sd_asking + _sd_grest_asset + (_sd_asking * 0.015)
                _sd_total_acq_share = _sd_asking + _sd_share_legal + (_sd_asking * 0.005)
                if _sd_asking > 0:
                    _KV("GrESt Rate",             f"{_sd_grest_rate:.1%}")
                    _KV("GrESt (Asset Deal)",     f"EUR {_sd_grest_asset:,.0f}")
                    _KV("Share Deal Legal Cost",  f"EUR {_sd_share_legal:,.0f}")
                    _KV("Net Saving",             f"EUR {_sd_net_saving:,.0f}")
                    _KV("Total Acq (Asset Deal)", f"EUR {_sd_total_acq_asset:,.0f}")
                    _KV("Total Acq (Share Deal)", f"EUR {_sd_total_acq_share:,.0f}")
                    _KV("As % of Equity",         f"{_sd_net_saving/_sd_equity:.1%}" if _sd_equity > 0 else '--')
                else:
                    _lines.append("  No asking price entered.")
            except Exception as _e:
                _lines.append(f"  [error: {_e}]")

            # ── Module 8b · CPI Projection (year 10 total)
            _H2("CPI Rent Projection")
            try:
                if not rr.empty:
                    _cpi_df = _di_cpi_projection(rr, cpi_indexation)
                    if not _cpi_df.empty and 'Yr 10' in _cpi_df.columns:
                        _KV("CPI Rate Used", f"{cpi_indexation:.1%}/yr")
                        for _, _cr in _cpi_df.iterrows():
                            _lines.append(
                                f"  {str(_cr.get('Tenant','?'))[:28]:<28} "
                                f"Base: {_cr.get('Base Rent','?')}  "
                                f"Yr 5: {_cr.get('Yr 5','?')}  "
                                f"Yr 10: {_cr.get('Yr 10','?')}  "
                                f"Indexed: {_cr.get('Indexed','?')}"
                            )
            except Exception as _e:
                _lines.append(f"  [error: {_e}]")

            # ── Module 8c · Tenant Concentration
            _H("MODULE 8C · TENANT CONCENTRATION")
            try:
                if not rr.empty:
                    _tc = _di_tenant_concentration(rr)
                    if _tc:
                        _KV("Top-1 Tenant",     f"{_tc.get('top1_name','?')[:30]} ({_tc.get('top1_pct',0):.1%})")
                        _KV("Top-3 Combined",   f"{_tc.get('top3_pct',0):.1%}")
                        _KV("Top-5 Combined",   f"{_tc.get('top5_pct',0):.1%}")
                        _KV("Anchor Rent",      f"EUR {_tc.get('anchor_rent',0):,.0f}/yr ({_tc.get('anchor_pct',0):.1%})")
                        _H2("All Active Tenants")
                        for _t in (_tc.get('top_tenants') or []):
                            _KV(f"  {_t.get('tenant','?')[:30]}", f"EUR {_t.get('rent',0):,.0f}/yr ({_t.get('pct',0):.1%})")
                    else:
                        _lines.append("  No active tenants.")
            except Exception as _e:
                _lines.append(f"  [error: {_e}]")

            # ── Module 9 · SWOT
            _H("MODULE 9 · DEAL SWOT ANALYSIS")
            try:
                if not rr.empty:
                    _swot = _di_swot(
                        metrics, qa, rr, deal, noi, loan, int_rt, amrt_rt,
                        io_years, loan_type_key, cpi_indexation, y1_debt_svc,
                    )
                    for _quad, _qname in [
                        ('strengths','STRENGTHS'), ('weaknesses','WEAKNESSES'),
                        ('opportunities','OPPORTUNITIES'), ('threats','THREATS'),
                    ]:
                        _H2(_qname)
                        _items = _swot.get(_quad, [])
                        if _items:
                            for _si in _items: _LI(_si)
                        else:
                            _lines.append("    None identified.")
                    _lines.append(f"\n  Summary: {_swot.get('summary','')}")
                else:
                    _lines.append("  No rent roll data.")
            except Exception as _e:
                _lines.append(f"  [error: {_e}]")

            # ── Module 10 · Scenario Engine
            _H("MODULE 10 · SCENARIO ENGINE")
            try:
                if not rr.empty and loan > 0:
                    for _sev, _slbl in [(1,"Mild"),(2,"Base Stress"),(3,"Severe")]:
                        _sc = _di_scenario_engine(
                            metrics, rr, deal, noi, loan, int_rt, amrt_rt,
                            io_years, loan_type_key, cpi_indexation,
                            stress_severity=_sev,
                        )
                        _H2(f"Stress Severity {_sev} — {_slbl}")
                        for _k, _label in [('base','Base'), ('stress','Stress'), ('upside','Upside')]:
                            _s = _sc[_k]
                            _dscr_s = f"{_s['dscr']:.2f}x" if _s.get('dscr') else '--'
                            _irr_s  = f"{_s['irr']:.1%}"   if _s.get('irr')  else '--'
                            _lines.append(
                                f"  {_label:<8}: NOI EUR {_s['noi']:>10,.0f}  "
                                f"DSCR {_dscr_s:<8}  IRR {_irr_s:<7}  [{_s.get('verdict','?')}]"
                            )
                        _lines.append(f"  Stress anchor: {_sc.get('anchor_name','?')} "
                                      f"(EUR {_sc.get('anchor_rent',0):,.0f}/yr at risk)")
                else:
                    _lines.append("  No rent roll or loan data.")
            except Exception as _e:
                _lines.append(f"  [error: {_e}]")

            # ── Module 11 · Relationship Intelligence
            _H("MODULE 11 · RELATIONSHIP INTELLIGENCE")
            try:
                import datetime as _ri_cex_dt
                _today_cex = _ri_cex_dt.date.today()
                _cutoff_cex = _ri_cex_dt.date(_today_cex.year + 3, _today_cex.month, _today_cex.day)

                if not rr.empty:
                    _active_cex = rr[~rr.get('is_vacant', pd.Series(False, index=rr.index))]
                    _qual_cex = []
                    for _, _row in _active_cex.iterrows():
                        _has_brk = False
                        _has_exp = False
                        _bo = _row.get('break_option')
                        if _bo is not None and not (isinstance(_bo, float) and pd.isna(_bo)):
                            try:
                                _bo_d = _bo.date() if hasattr(_bo, 'date') else _bo
                                _has_brk = isinstance(_bo_d, _ri_cex_dt.date)
                            except Exception:
                                pass
                        _ex = _row.get('lease_expiry')
                        if _ex is not None and not (isinstance(_ex, float) and pd.isna(_ex)):
                            try:
                                _ex_d = _ex.date() if hasattr(_ex, 'date') else _ex
                                _has_exp = isinstance(_ex_d, _ri_cex_dt.date) and _ex_d <= _cutoff_cex
                            except Exception:
                                pass
                        if _has_brk or _has_exp:
                            _qual_cex.append(_row)

                    if not _qual_cex:
                        _lines.append("  No near-term break or expiry risk — RI not required.")
                    else:
                        # Reconstruct RI state key the same way section 11 does
                        _tk_hash_cex = '_'.join(str(_row.get('tenant','')) for _row in _qual_cex)
                        _ri_sk_cex   = f'ri_state_{hash(_tk_hash_cex) & 0xFFFFFF}'
                        _ri_data_cex = st.session_state.get(_ri_sk_cex, {})

                        _n_green = _n_amber = _n_red = 0
                        for _row in _qual_cex:
                            _tn   = str(_row.get('tenant', '?'))
                            _sig  = _ri_data_cex.get(f'ri_sig_{_tn}',  'AMBER')
                            _note = _ri_data_cex.get(f'ri_note_{_tn}', '')
                            if _sig == 'GREEN':  _n_green += 1
                            elif _sig == 'RED':  _n_red   += 1
                            else:                _n_amber += 1
                            _lines.append(
                                f"  {_tn[:35]:<35} Signal: {_sig}"
                            )
                            if _note.strip():
                                _lines.append(f"    Intelligence: {_note.strip()}")

                        _lines.append(
                            f"\n  Signals: {_n_green} GREEN / {_n_amber} AMBER / {_n_red} RED"
                            f"  (of {len(_qual_cex)} qualifying tenants)"
                        )
                        _lines.append(
                            "  DISCLAIMER: RI signals are analyst input, not verified data. "
                            "Must be disclosed in IC submission."
                        )
                else:
                    _lines.append("  No rent roll data.")
            except Exception as _e:
                _lines.append(f"  [error: {_e}]")

            # ── Bid Price
            _H("BID PRICE \u2014 MAXIMUM BID CALCULATOR")
            try:
                _ex_tgt_dscr    = st.session_state.get("bid_dscr", 1.40)
                _ex_tgt_giy     = st.session_state.get("bid_giy_pct", 6.5) / 100.0
                _ex_tgt_irr     = st.session_state.get("bid_irr_pct", 8.0) / 100.0
                _ex_ltv_ratio   = metrics.get("ltv") or 0
                _ex_ds_rate     = (y1_debt_svc / loan) if loan > 0 else (int_rt + amrt_rt)
                _ex_bp_giy  = gross_rent / _ex_tgt_giy if _ex_tgt_giy > 0 else None
                _ex_bp_dscr = (
                    noi / (_ex_tgt_dscr * _ex_ds_rate * _ex_ltv_ratio)
                    if noi > 0 and _ex_ds_rate > 0 and _ex_ltv_ratio > 0 else None
                )
                _ex_bp_irr = None
                if noi > 0 and _ex_ltv_ratio > 0:
                    def _ex_irr_at_p(p):
                        return _scen_irr(
                            p * (1.0 - _ex_ltv_ratio), noi, noi, cpi_indexation,
                            p * _ex_ltv_ratio, int_rt, amrt_rt, io_years,
                            loan_type_key, base_exit_cap, hold_yrs=10,
                        )
                    _ex_f_lo = _ex_irr_at_p(100_000.0)
                    if _ex_f_lo is not None and _ex_f_lo >= _ex_tgt_irr:
                        _ex_lo_p, _ex_hi_p = 100_000.0, 200_000_000.0
                        for _ in range(60):
                            _ex_mp = (_ex_lo_p + _ex_hi_p) / 2.0
                            _ex_fm = _ex_irr_at_p(_ex_mp)
                            if _ex_fm is None:
                                break
                            if abs(_ex_fm - _ex_tgt_irr) < 1e-5:
                                break
                            if _ex_fm > _ex_tgt_irr:
                                _ex_lo_p = _ex_mp
                            else:
                                _ex_hi_p = _ex_mp
                        _ex_bp_irr = (_ex_lo_p + _ex_hi_p) / 2.0
                _ex_cands = {
                    k: v for k, v in
                    {"DSCR": _ex_bp_dscr, "GIY": _ex_bp_giy, "IRR": _ex_bp_irr}.items()
                    if v is not None and v > 0
                }
                _KV("Target DSCR hurdle", f"{_ex_tgt_dscr:.2f}x")
                _KV("Target GIY hurdle",  f"{_ex_tgt_giy:.1%}")
                _KV("Target IRR hurdle",  f"{_ex_tgt_irr:.1%}")
                _KV("Max Price (DSCR)",   f"EUR {_ex_bp_dscr:,.0f}" if _ex_bp_dscr else "--")
                _KV("Max Price (GIY)",    f"EUR {_ex_bp_giy:,.0f}"  if _ex_bp_giy  else "--")
                _KV("Max Price (IRR)",    f"EUR {_ex_bp_irr:,.0f}"  if _ex_bp_irr  else "--")
                if _ex_cands:
                    _ex_binding   = min(_ex_cands, key=_ex_cands.get)
                    _ex_max_bid   = _ex_cands[_ex_binding]
                    _ex_max_bid_r = int(_ex_max_bid / 50_000) * 50_000
                    _KV("BINDING CONSTRAINT",  _ex_binding)
                    _KV("YOUR MAX BID",        f"EUR {_ex_max_bid:,.0f}")
                    _KV("Negotiation target",  f"EUR {_ex_max_bid_r:,.0f}")
                    if asking > 0:
                        _ex_gap = asking - _ex_max_bid
                        _KV("Asking price",    f"EUR {asking:,.0f}")
                        _KV("Gap to asking",   f"EUR {_ex_gap:+,.0f}  ({_ex_gap / asking:.1%} of asking)")
                else:
                    _lines.append("  Insufficient data for bid price calculation.")
                # CP mode note
                _ex_cp_on       = st.session_state.get("bid_cp_on", False)
                _ex_cp_lease    = st.session_state.get("bid_cp_lease_yrs", 10)
                if _ex_cp_on:
                    _H2("Condition Precedent (CP) — Active")
                    _lines.append(
                        f"  CP ON: bid prices above use full NOI. "
                        f"New lease term modelled: {_ex_cp_lease} years. "
                        f"Deal is conditional on new lease being signed before closing."
                    )
                elif st.session_state.get("bid_cp_on") is not None:
                    # toggle exists in session state but is OFF — break risk was identified
                    _H2("Break Option Risk — CP Not Applied")
                    _lines.append(
                        "  Break option FAIL detected. CP toggle was OFF at export time. "
                        "Bid prices above reflect post-break NOI (break risk is live). "
                        "Consider making new lease a condition precedent."
                    )
            except Exception as _e:
                _lines.append(f"  [error: {_e}]")

            # ── Footer
            _lines.append("\n" + "=" * 68)
            _lines.append("  End of analysis. Paste into Claude and ask your questions.")
            _lines.append("=" * 68)

            _claude_text = "\n".join(_lines)
            st.session_state['claude_export_text'] = _claude_text

        # Display text box if export has been generated
        if 'claude_export_text' in st.session_state and st.session_state['claude_export_text']:
            st.markdown(
                '<div style="background:#fef9c3;border:2px solid #fbbf24;border-radius:8px;'
                'padding:10px 16px;margin:12px 0 4px 0;font-weight:700;font-size:0.88rem;'
                'color:#92400e;letter-spacing:0.05em;">PASTE INTO CLAUDE</div>',
                unsafe_allow_html=True,
            )
            st.text_area(
                label="Copy the full text below and paste into Claude chat",
                value=st.session_state['claude_export_text'],
                height=400,
                key="claude_export_display",
            )
            st.caption(
                "Select all text above (Ctrl+A inside the box, or Ctrl+C after clicking), "
                "then paste into Claude.ai chat. Claude will be able to answer detailed "
                "questions about the deal, suggest structuring options, and compare deals."
            )

        st.divider()
        st.markdown("#### Memo Preview")
        st.code(memo, language=None)


    # ------------------------------------------------------------------ TAB DD
    with tab_dd:  # ── DD LEASE SCANNER ───────────────────────────────────────
        st.markdown("#### 🔍 DD Lease Scanner")
        st.caption(
            "Upload PDF lease agreements to extract commercial terms and compare "
            "against the rent roll. Flags discrepancies before you submit to IC. "
            "Works on text-based PDFs — scanned images require OCR (not supported)."
        )

        if rr is None or len(rr) == 0:
            st.info("Upload a deal Excel file first, then upload lease PDFs here.")
        else:
            _dd_files = st.file_uploader(
                "Upload lease PDFs (one per tenant)",
                type=['pdf'], accept_multiple_files=True,
                key='dd_pdf_upload',
                help="Upload signed lease agreements from the seller. "
                     "The scanner will extract rent, area, dates, CPI and compare to your rent roll."
            )

            if _dd_files:
                _pdf_dict = {f.name: f.read() for f in _dd_files}
                st.markdown(f"**{len(_pdf_dict)} PDF(s) uploaded — running scan...**")

                with st.spinner("Extracting lease terms..."):
                    _scan = run_dd_scan(_pdf_dict, rr)

                # Summary banner
                _s = _scan['summary']
                _sc1, _sc2, _sc3, _sc4 = st.columns(4)
                _sc1.metric("🔴 Critical", _scan['summary'].get('RED', 0),
                            delta="Requires immediate attention" if _scan['summary'].get('RED',0) > 0 else "None",
                            delta_color="inverse" if _scan['summary'].get('RED',0) > 0 else "normal")
                _sc2.metric("🟡 Warning",  _scan['summary'].get('AMBER', 0))
                _sc3.metric("✅ Clean",    _scan['summary'].get('OK', 0))
                _sc4.metric("❓ Unmatched",_scan['summary'].get('UNMATCHED', 0))

                st.divider()

                # Per-file results
                for _fname, _res in _scan['results'].items():
                    _status_icon = {'RED':'🔴','AMBER':'🟡','OK':'✅','UNMATCHED':'❓','ERROR':'❌'}.get(_res['status'],'❓')
                    with st.expander(f"{_status_icon} {_fname}  —  matched to: {_res.get('matched_tenant','Unknown')}"):

                        if _res['status'] == 'ERROR':
                            st.error(f"Could not read PDF: {_res.get('error','Unknown error')}")
                            continue
                        if _res['status'] == 'UNMATCHED':
                            st.warning(_res.get('message','Could not match to rent roll tenant.'))

                        # Extracted terms
                        _ext = _res.get('extracted', {})
                        if _ext:
                            st.markdown("**Extracted from PDF:**")
                            _e1, _e2, _e3 = st.columns(3)
                            _e1.metric("Tenant", str(_ext.get('tenant_name','—'))[:30])
                            _e1.metric("Area", f"{_ext.get('area_sqm','—')} m²" if _ext.get('area_sqm') else "—")
                            _e1.metric("Rent", f"EUR {_ext.get('rent_sqm_mo','—')}/m²/mo" if _ext.get('rent_sqm_mo') else "—")
                            _e2.metric("Lease Expiry", str(_ext.get('lease_expiry','—')))
                            _e2.metric("Break Option", str(_ext.get('break_option','—')))
                            _e2.metric("CPI Clause", _ext.get('cpi_clause','—'))
                            _e3.metric("Security Dep", f"EUR {_ext.get('security_dep',0):,.0f}" if _ext.get('security_dep') else "—")
                            # CPI passthrough — show value + detected format so analyst can verify
                            _cpi_pt      = _ext.get('cpi_passthrough')
                            _cpi_fmt     = _ext.get('cpi_passthrough_format')
                            _cpi_raw     = _ext.get('cpi_passthrough_raw')
                            _cpi_conf    = _ext.get('confidence', {}).get('cpi_passthrough')
                            _fmt_labels  = {
                                'integer%':       'integer %',
                                'decimal':        'decimal',
                                'integer_no_pct': 'no % sign',
                            }
                            if _cpi_pt is not None:
                                _pt_disp = f"{_cpi_pt:.0%} ({_fmt_labels.get(_cpi_fmt, _cpi_fmt or '?')})"
                                _e3.metric(
                                    "CPI Pass-thru", _pt_disp,
                                    delta="⚠️ Verify format" if _cpi_conf == 'AMBER' else "Confirmed %",
                                    delta_color="inverse" if _cpi_conf == 'AMBER' else "normal",
                                    help="Format detected from lease text. AMBER = no explicit % sign — confirm value.",
                                )
                                if _cpi_conf == 'AMBER' and _cpi_raw:
                                    st.caption(f"⚠️ CPI passthrough raw text: \"{_cpi_raw}\" — confirm decimal vs integer format.")
                            else:
                                _e3.metric("CPI Pass-thru", "—")
                            # Confidence — exclude cpi_passthrough since it has its own indicator above
                            _conf = _ext.get('confidence', {})
                            if _conf:
                                _low_conf = [k for k, v in _conf.items()
                                             if v not in ('HIGH', 'CALCULATED') and k != 'cpi_passthrough']
                                if _low_conf:
                                    st.caption(f"⚠️ Lower confidence fields: {', '.join(_low_conf)} — verify manually")

                        # Discrepancies
                        _discs = _res.get('discrepancies', [])
                        _real_discs = [d for d in _discs if d['severity'] != 'OK']
                        if _real_discs:
                            st.markdown("**Discrepancies vs Rent Roll:**")
                            for _d in _real_discs:
                                _icon = '🔴' if _d['severity']=='RED' else '🟡'
                                st.markdown(f"{_icon} **{_d['field']}**: {_d['message']}")
                        else:
                            st.success("✅ All extracted fields match rent roll within tolerance.")

                # ── Lease Abstract ────────────────────────────────────────────────
                st.divider()
                st.markdown("#### 📋 Lease Abstract")
                st.caption(
                    "Structured summary of the 10 commercial terms extracted from each PDF. "
                    "Cell colour: green = matches rent roll · red = mismatch · amber = unverifiable. "
                    "'Manual review required' = field not readable from this PDF format."
                )

                try:
                    import html as _html_esc

                    # ── per-field match helpers ────────────────────────────────
                    def _la_num(pdf_v, rr_v, tol=0.05):
                        if pdf_v is None: return 'AMBER'
                        try:
                            rr_f = float(rr_v or 0)
                            if rr_f == 0: return 'AMBER'
                            return ('GREEN' if abs(float(pdf_v)-rr_f)/rr_f <= tol
                                    else ('AMBER' if abs(float(pdf_v)-rr_f)/rr_f <= tol*2
                                    else 'RED'))
                        except: return 'AMBER'

                    def _la_date(pdf_v, rr_v):
                        if pdf_v is None or rr_v is None: return 'AMBER'
                        try:
                            pv = pdf_v.date() if hasattr(pdf_v,'date') else pdf_v
                            rv = rr_v
                            if hasattr(rv,'date'):   rv = rv.date()
                            if hasattr(rv,'item'):   rv = rv.item().date()   # numpy Timestamp
                            return 'GREEN' if pv == rv else 'RED'
                        except: return 'AMBER'

                    def _la_yn(pdf_v, rr_v):
                        if pdf_v is None or rr_v is None: return 'AMBER'
                        return ('GREEN' if str(pdf_v).upper().strip()==str(rr_v).upper().strip()
                                else 'RED')

                    def _la_fmt(val, fmt):
                        if val is None: return None
                        try:
                            if fmt == 'eur':  return f"EUR {float(val):,.0f}"
                            if fmt == 'sqm':  return f"{float(val):,.0f} m²"
                            if fmt == 'rpsm': return f"{float(val):.2f}"
                            if fmt == 'date':
                                d = val.date() if hasattr(val,'date') else val
                                if hasattr(d,'item'): d = d.item().date()
                                return d.strftime('%d.%m.%Y') if hasattr(d,'strftime') else str(d)
                            if fmt == 'pct':  return f"{float(val):.0%}"
                            return str(val)
                        except: return str(val)

                    _CELL_BG = {'GREEN':'#d1fae5','AMBER':'#fef3c7','RED':'#fee2e2','NEUTRAL':'#f9fafb'}
                    _CELL_FG = {'GREEN':'#065f46','AMBER':'#92400e','RED':'#991b1b','NEUTRAL':'#374151'}
                    _TD      = ('padding:7px 10px;border:1px solid #e5e7eb;'
                                'font-size:0.79rem;vertical-align:top;')

                    def _td(val, status='NEUTRAL'):
                        if val is None:
                            return (f'<td style="{_TD}background:#fef3c7;color:#92400e;'
                                    f'font-style:italic;">Manual review required</td>')
                        return (f'<td style="{_TD}background:{_CELL_BG[status]};'
                                f'color:{_CELL_FG[status]};">'
                                f'{_html_esc.escape(str(val))}</td>')

                    def _td_match(status):
                        ic = {'GREEN':'✓','AMBER':'?','RED':'✗'}.get(status,'?')
                        return (f'<td style="{_TD}background:{_CELL_BG[status]};'
                                f'color:{_CELL_FG[status]};font-weight:800;font-size:1.05rem;'
                                f'text-align:center;">{ic}</td>')

                    _TH = ('padding:8px 10px;background:#1e3a5f;color:#fff;font-size:0.76rem;'
                           'font-weight:600;text-align:left;border:1px solid #172f4c;'
                           'white-space:nowrap;')
                    _COLS = [
                        'Tenant Legal Entity','Unit Ref','Area (m²)','Annual Rent (EUR)',
                        'EUR/m²/mo','Lease Start','Lease Expiry','Break Option',
                        'CPI (Y/N · Pass-thru · Trigger)','Security Deposit (EUR)','Match',
                    ]
                    _thead = ''.join(f'<th style="{_TH}">{c}</th>' for c in _COLS)

                    _tbody = []
                    _la_export_rows = []

                    for _lfn, _lr in _scan['results'].items():
                        if _lr['status'] == 'ERROR':
                            continue
                        _le  = _lr.get('extracted', {})
                        _lrw = _lr.get('matched_row') or {}

                        # 1. Tenant name
                        _tn_pdf = _le.get('tenant_name')
                        _tn_rr  = str(_lrw.get('tenant',''))
                        _tn_m   = ('GREEN'
                                   if _tn_pdf and _tn_rr and (
                                       _tn_pdf.lower() in _tn_rr.lower() or
                                       _tn_rr.lower() in _tn_pdf.lower())
                                   else 'AMBER')

                        # 2. Unit ref (from RR — not in PDF)
                        _ur_rr = str(_lrw.get('unit_ref','—') or '—')

                        # 3. Area
                        _ar_pdf = _le.get('area_sqm')
                        _ar_m   = _la_num(_ar_pdf, _lrw.get('area_sqm'), 0.02)

                        # 4. Annual rent (extract or back-calc)
                        _ann_pdf = _le.get('annual_rent')
                        if _ann_pdf is None and _le.get('rent_sqm_mo') and _le.get('area_sqm'):
                            _ann_pdf = round(_le['rent_sqm_mo'] * _le['area_sqm'] * 12, 0)
                        _ann_m = _la_num(_ann_pdf, _lrw.get('annual_rent'), 0.05)

                        # 5. Rent/m²/mo
                        _rpsm_pdf = _le.get('rent_sqm_mo')
                        _rpsm_m   = _la_num(_rpsm_pdf, _lrw.get('rent_per_sqm_mo'), 0.05)

                        # 6. Lease start
                        _ls_m = _la_date(_le.get('lease_start'), _lrw.get('lease_start'))

                        # 7. Lease expiry
                        _le_m = _la_date(_le.get('lease_expiry'), _lrw.get('lease_expiry'))

                        # 8. Break option
                        _brk_pdf = _le.get('break_option')
                        _brk_rr  = _lrw.get('break_option')
                        _brk_rr_none = (_brk_rr is None or
                                        (isinstance(_brk_rr, float) and pd.isna(_brk_rr)))
                        if _brk_pdf is None and _brk_rr_none: _brk_m = 'GREEN'
                        elif _brk_pdf is None:                  _brk_m = 'AMBER'
                        elif _brk_rr_none:                      _brk_m = 'RED'
                        else:                                   _brk_m = _la_date(_brk_pdf, _brk_rr)

                        # 9. CPI  (Y/N · passthrough · trigger)
                        _cpi_yn_pdf  = _le.get('cpi_clause')
                        _cpi_yn_rr   = str(_lrw.get('cpi_clause','') or '')
                        _cpi_pt_pdf  = _le.get('cpi_passthrough')
                        _cpi_pt_rr   = _lrw.get('cpi_passthrough', 0) or 0
                        _cpi_tg_pdf  = _le.get('cpi_trigger')
                        _cpi_tg_rr   = _lrw.get('cpi_trigger', 0) or 0
                        _cpi_yn_m    = _la_yn(_cpi_yn_pdf, _cpi_yn_rr) if _cpi_yn_rr else 'AMBER'
                        _cpi_pt_m    = _la_num(_cpi_pt_pdf, _cpi_pt_rr, 0.05) if _cpi_pt_rr else 'AMBER'
                        _cpi_m = ('RED' if 'RED' in (_cpi_yn_m,_cpi_pt_m)
                                  else ('GREEN' if _cpi_yn_m == 'GREEN' else 'AMBER'))
                        _cpi_parts = []
                        if _cpi_yn_pdf: _cpi_parts.append(_cpi_yn_pdf)
                        if _cpi_pt_pdf is not None: _cpi_parts.append(f"{_cpi_pt_pdf:.0%} pass-thru")
                        if _cpi_tg_pdf is not None: _cpi_parts.append(f"trig {_cpi_tg_pdf:.0%}")
                        _cpi_disp = ' · '.join(_cpi_parts) or None

                        # 10. Security deposit
                        _sd_pdf = _le.get('security_dep')
                        _sd_rr  = float(_lrw.get('security_deposit', 0) or 0)
                        _sd_m   = (_la_num(_sd_pdf, _sd_rr, 0.10) if _sd_rr > 0
                                   else ('AMBER' if _sd_pdf is None else 'NEUTRAL'))

                        # Overall match
                        _field_ms = [_tn_m,_ar_m,_ann_m,_rpsm_m,_ls_m,_le_m,_brk_m,_cpi_m,_sd_m]
                        if _lr.get('status') == 'RED' or 'RED' in _field_ms:
                            _ov_m = 'RED'
                        elif all(m in ('GREEN','NEUTRAL') for m in _field_ms):
                            _ov_m = 'GREEN'
                        else:
                            _ov_m = 'AMBER'

                        _tbody.append(
                            f'<tr>'
                            f'{_td(_tn_pdf, _tn_m)}'
                            f'<td style="{_TD}background:#f9fafb;color:#374151;">'
                            f'{_html_esc.escape(_ur_rr)}</td>'
                            f'{_td(_la_fmt(_ar_pdf,"sqm"),  _ar_m)}'
                            f'{_td(_la_fmt(_ann_pdf,"eur"), _ann_m)}'
                            f'{_td(_la_fmt(_rpsm_pdf,"rpsm"), _rpsm_m)}'
                            f'{_td(_la_fmt(_le.get("lease_start"),"date"),  _ls_m)}'
                            f'{_td(_la_fmt(_le.get("lease_expiry"),"date"), _le_m)}'
                            f'{_td(_la_fmt(_brk_pdf,"date") if not _brk_rr_none or _brk_pdf else None, _brk_m)}'
                            f'{_td(_cpi_disp, _cpi_m)}'
                            f'{_td(_la_fmt(_sd_pdf,"eur"),  _sd_m)}'
                            f'{_td_match(_ov_m)}'
                            f'</tr>'
                        )

                        _la_export_rows.append({
                            'File':                     _lfn,
                            'Tenant Legal Entity':      _tn_pdf or 'Manual review required',
                            'Unit Ref':                 _ur_rr,
                            'Area (m²)':                _ar_pdf or '',
                            'Annual Rent (EUR)':        _ann_pdf or '',
                            'EUR/m²/mo':                _rpsm_pdf or '',
                            'Lease Start':              _la_fmt(_le.get('lease_start'),'date') or '',
                            'Lease Expiry':             _la_fmt(_le.get('lease_expiry'),'date') or '',
                            'Break Option':             (_la_fmt(_brk_pdf,'date') if _brk_pdf else 'None'),
                            'CPI Y/N':                  _cpi_yn_pdf or '',
                            'CPI Passthrough':          (f"{_cpi_pt_pdf:.0%}" if _cpi_pt_pdf is not None else ''),
                            'CPI Trigger':              (f"{_cpi_tg_pdf:.0%}" if _cpi_tg_pdf is not None else ''),
                            'Security Deposit (EUR)':   _sd_pdf or '',
                            'Overall Match':            _ov_m,
                            'RR Tenant':                _tn_rr,
                            'RR Area (m²)':             float(_lrw.get('area_sqm',0) or 0) or '',
                            'RR Annual Rent (EUR)':     float(_lrw.get('annual_rent',0) or 0) or '',
                            'RR EUR/m²/mo':             float(_lrw.get('rent_per_sqm_mo',0) or 0) or '',
                            'RR Lease Expiry':          (_la_fmt(_lrw.get('lease_expiry'),'date')
                                                         if _lrw.get('lease_expiry') else ''),
                        })

                    if _tbody:
                        st.markdown(
                            '<div style="overflow-x:auto;">'
                            '<table style="border-collapse:collapse;width:100%;'
                            'font-family:sans-serif;">'
                            f'<thead><tr>{_thead}</tr></thead>'
                            f'<tbody>{"".join(_tbody)}</tbody>'
                            '</table></div>',
                            unsafe_allow_html=True,
                        )

                        # Export button
                        if _la_export_rows:
                            st.markdown("")
                            _la_df  = pd.DataFrame(_la_export_rows)
                            _la_buf = io.BytesIO()
                            with pd.ExcelWriter(_la_buf, engine='openpyxl') as _la_wr:
                                _la_df.to_excel(_la_wr, index=False, sheet_name='Lease Abstract')
                                _la_ws = _la_wr.sheets['Lease Abstract']
                                # auto-width columns
                                for _lc in _la_ws.columns:
                                    _lw = max(len(str(_lcel.value or '')) for _lcel in _lc) + 4
                                    _la_ws.column_dimensions[_lc[0].column_letter].width = min(_lw, 45)
                            _la_buf.seek(0)
                            st.download_button(
                                "⬇️  Export Lease Abstract (Excel)",
                                data=_la_buf.getvalue(),
                                file_name=(f"Lease_Abstract_"
                                           f"{str(deal.get('property_name','Deal')).replace(' ','_')}.xlsx"),
                                mime=("application/vnd.openxmlformats-"
                                      "officedocument.spreadsheetml.sheet"),
                                key="download_lease_abstract",
                            )
                    else:
                        st.info("No matched leases available to display in abstract.")

                except Exception as _la_err:
                    st.error(f"Lease Abstract error: {_la_err}")

                # ── end Lease Abstract ────────────────────────────────────────

                st.divider()
                st.caption(
                    "Scanner confidence varies by PDF quality and lease format. "
                    "Always verify RED flags with the original document. "
                    "Fields not extracted (shown as —) require manual review."
                )
            else:
                st.info("Upload PDF lease agreements above to begin scanning.")


    # ------------------------------------------------------------------ TAB DI
    with tab_di:  # ── DEEP INSIGHTS ──────────────────────────────────────────
        st.markdown("#### 🔬 Deep Insights — Advanced Analytics Layer")
        st.caption(
            "Nine advanced analytics modules beyond the standard IC checklist. "
            "For experienced underwriters who want to stress-test income assumptions, "
            "model covenant fragility, and quantify structural risks."
        )

        # ── 1. Covenant Score ─────────────────────────────────────────────────
        st.divider()
        st.markdown(
            "### 1 · Tenant Covenant Scores &nbsp;"
            '<span title="Scores are based on sector weights and lease structure — '
            'not legal entity financials or external credit ratings." '
            'style="font-size:0.68rem;font-weight:500;background:#f3f4f6;color:#6b7280;'
            'border:1px solid #d1d5db;border-radius:4px;padding:2px 7px;'
            'vertical-align:middle;cursor:help;">Heuristic Score</span>',
            unsafe_allow_html=True,
        )
        st.caption(
            "0–100 per tenant. Sector base score + anchor status (+15) + CPI clause (+10). "
            "**Green** ≥ 75 · **Amber** 50–74 · **Red** < 50. "
            "Sector weights: food/grocery 100 · health/beauty 85 · fashion 45–50 · office 30."
        )
        if rr.empty:
            st.info("Upload a deal Excel file to enable covenant scoring.")
        else:
            _cov_df = _di_covenant_scores(rr)
            if _cov_df.empty:
                st.info("No active tenants in rent roll.")
            else:
                for _, _row in _cov_df.iterrows():
                    _sc   = int(_row['Score'])
                    _col  = _row['Color']
                    _name = str(_row['Tenant'])[:30]
                    _sec  = str(_row['Sector'])[:20]
                    _ar   = float(_row['Annual Rent (EUR)'])
                    st.markdown(
                        f'<div style="margin:5px 0;line-height:1.6;">'
                        f'<span style="display:inline-block;width:190px;font-size:0.82rem;">{_name}</span>'
                        f'<span style="display:inline-block;width:130px;font-size:0.78rem;color:#6b7280;">{_sec}</span>'
                        f'<div style="display:inline-block;background:#e5e7eb;width:200px;height:14px;'
                        f'border-radius:4px;vertical-align:middle;">'
                        f'<div style="background:{_col};width:{_sc * 2}px;height:14px;border-radius:4px;"></div></div>'
                        f'<span style="margin-left:10px;font-weight:700;color:{_col};font-size:0.84rem;">{_sc}</span>'
                        f'<span style="margin-left:12px;font-size:0.75rem;color:#9ca3af;">'
                        f'EUR {_ar:,.0f}/yr</span>'
                        f'</div>',
                        unsafe_allow_html=True,
                    )
                _total_ar = _cov_df['Annual Rent (EUR)'].sum()
                if _total_ar > 0:
                    _wt_score = (_cov_df['Score'] * _cov_df['Annual Rent (EUR)']).sum() / _total_ar
                    st.metric("Portfolio Weighted Covenant Score", f"{_wt_score:.0f} / 100")

        # ── 2. Income Cliff ───────────────────────────────────────────────────
        st.divider()
        st.markdown("### 2 · Income Cliff Analysis")
        st.caption(
            "Assumes zero lease renewals. Shows the month when cumulative passing rent "
            "drops below monthly debt service — the 'cliff'. "
            "A cliff inside the loan term requires a lease management plan before IC."
        )
        if rr.empty or y1_debt_svc <= 0:
            st.info("Upload deal data and set financing parameters to enable income cliff analysis.")
        else:
            _cliff = _di_income_cliff(rr, y1_debt_svc)
            _cc1, _cc2, _cc3 = st.columns(3)
            _cc1.metric("Monthly Debt Service", f"EUR {_cliff['monthly_ds']:,.0f}")
            _cc2.metric("Current Monthly Rent", f"EUR {_cliff['rent_now']:,.0f}")
            if _cliff['cliff_month']:
                _cc3.metric(
                    "Income Cliff",
                    f"Month {_cliff['cliff_month']}  ({_cliff['cliff_month']/12:.1f} yrs)",
                    delta=f"Rent drops to EUR {_cliff['cliff_rent']:,.0f}/mo",
                    delta_color="inverse",
                )
                st.warning(
                    f"⚠️ Income cliff at **Month {_cliff['cliff_month']}** "
                    f"({_cliff['cliff_month']/12:.1f} yrs from today) — passing rent falls to "
                    f"EUR {_cliff['cliff_rent']:,.0f}/mo vs debt service of "
                    f"EUR {_cliff['monthly_ds']:,.0f}/mo. "
                    "Lease renewals or re-letting before that date are credit-critical."
                )
            else:
                _cc3.metric("Income Cliff", "None within 15 yrs", delta_color="normal")
                st.success("No income cliff within 15-year horizon under zero-renewal assumption.")
            if _cliff.get('data'):
                _cdf = pd.DataFrame(_cliff['data']).set_index('month')
                _cdf.columns = ['Monthly Rent (EUR)', 'Monthly Debt Service (EUR)']
                st.line_chart(_cdf, height=260)

        # ── 3. Break Option Stress Test ───────────────────────────────────────
        st.divider()
        st.markdown("### 3 · Break Option Stress Test")
        st.caption(
            "For each tenant with a break clause: post-break DSCR assuming the tenant exercises, "
            "space goes dark, and full void holding costs are applied "
            f"(EUR {VOID_HOLD_RATE:.0f}/m²/yr void reserve)."
        )
        if rr.empty:
            st.info("Upload deal data to enable break stress testing.")
        else:
            _brk_df = _di_break_stress(rr, noi, loan, int_rt, amrt_rt)
            if _brk_df.empty:
                st.success("✅ No break options in current rent roll — full income certainty to lease expiry dates.")
            else:
                def _brk_color(v):
                    if v == 'PASS': return 'background-color:#d1fae5;color:#065f46;font-weight:700'
                    if v == 'WARN': return 'background-color:#fef3c7;color:#92400e;font-weight:700'
                    if v == 'FAIL': return 'background-color:#fee2e2;color:#991b1b;font-weight:700'
                    return ''
                st.dataframe(
                    _brk_df.style.map(_brk_color, subset=['Status']),
                    use_container_width=True, hide_index=True,
                )
                _n_fail = (_brk_df['Status'] == 'FAIL').sum()
                _n_warn = (_brk_df['Status'] == 'WARN').sum()
                if _n_fail > 0:
                    st.error(f"🔴 {_n_fail} break option(s) would trigger DSCR < 1.20x — credit covenant breach risk.")
                elif _n_warn > 0:
                    st.warning(f"🟡 {_n_warn} break option(s) push DSCR to amber zone (1.20–1.30x).")
                else:
                    st.success("✅ All break scenarios maintain DSCR above 1.30x.")

        # ── 4. True Yield Gap ─────────────────────────────────────────────────
        st.divider()
        st.markdown("### 4 · True Yield Gap")
        st.caption(
            "Actual GIY vs stabilised GIY (vacant space filled at the portfolio's current "
            "average rent/m²). The gap = repositioning premium baked into the asking price."
        )
        _yg = _di_true_yield_gap(metrics, rr)
        if not _yg:
            st.info("Enter asking price to enable yield gap analysis.")
        else:
            _yc1, _yc2, _yc3, _yc4 = st.columns(4)
            _yc1.metric("Actual GIY", f"{_yg['actual_giy']:.2%}")
            _delta_bps_str = f"+{_yg['gap_bps']:.0f}bps" if _yg['gap_bps'] >= 0 else f"{_yg['gap_bps']:.0f}bps"
            _yc2.metric(
                "Stabilised GIY",
                f"{_yg['stabilised_giy']:.2%}",
                delta=_delta_bps_str,
                delta_color="normal",
                help=(
                    "Mathematical Upper Bound — assumes vacant space lets at the current "
                    "portfolio average rent/sqm. Does not account for unit-specific letting "
                    "risk, capex requirement, or market ERV."
                ),
            )
            _yc3.metric("Yield Gap", f"{_yg['gap_bps']:.0f} bps")
            _yc4.metric(
                "Void ERV Upside",
                f"EUR {_yg['erv_void_income']:,.0f}/yr",
                delta=f"{_yg['vacant_area']:,.0f} m² @ EUR {_yg['avg_rent_sqm_mo']:.2f}/m²/mo",
            )
            if _yg['gap_bps'] > 150:
                st.warning(
                    f"Significant repositioning premium: **{_yg['gap_bps']:.0f}bps**. "
                    f"Seller is pricing {_yg['vacant_area']:,.0f} m² of void being re-let at "
                    f"EUR {_yg['avg_rent_sqm_mo']:.2f}/m²/mo. "
                    "Challenge: what is the realistic re-letting timeline and incentive package cost?"
                )
            elif _yg['gap_bps'] > 0:
                st.info(
                    f"Modest yield gap of **{_yg['gap_bps']:.0f}bps** — "
                    f"{_yg['vacant_area']:,.0f} m² vacant space adds "
                    f"EUR {_yg['erv_void_income']:,.0f}/yr NOI upside when re-let."
                )
            else:
                st.success("No yield gap — asset is fully stabilised at asking price.")

        # ── 5. Refinancing Risk ───────────────────────────────────────────────
        st.divider()
        st.markdown(
            "### 5 · Refinancing Screen &nbsp;"
            '<span title="Screens for cap-rate compression dependency and LTV constraint. '
            'Does not model lender DSCR sizing or debt yield constraints." '
            'style="font-size:0.68rem;font-weight:500;background:#f3f4f6;color:#6b7280;'
            'border:1px solid #d1d5db;border-radius:4px;padding:2px 7px;'
            'vertical-align:middle;cursor:help;">Refinancing Screen</span>',
            unsafe_allow_html=True,
        )
        st.caption(
            "At end of hold period: residual loan balance, projected NOI (CPI-grown), "
            "and exit value required for 65% LTV refinancing. "
            "Screens for cap-rate compression dependency — does not model lender DSCR "
            "sizing or debt yield constraints."
        )
        _rr_hold = st.select_slider(
            "Hold period for refi analysis (years)", options=[3, 5, 7, 10],
            value=5, key='di_hold',
        )
        _refi = _di_refi_risk(
            metrics, noi, loan, int_rt, amrt_rt,
            io_years, _rr_hold, loan_type_key, cpi_indexation,
        )
        if not _refi:
            st.info("Enter loan parameters to enable refinancing risk analysis.")
        else:
            _rfc1, _rfc2, _rfc3, _rfc4 = st.columns(4)
            _rfc1.metric(f"Loan Balance (Yr {_rr_hold})", f"EUR {_refi['loan_balance']:,.0f}")
            _rfc2.metric(f"Projected NOI (Yr {_rr_hold})",
                         f"EUR {_refi['projected_noi']:,.0f}",
                         delta=f"{cpi_indexation:.1%}/yr CPI applied")
            _rfc3.metric("Exit Value (65% LTV)", f"EUR {_refi['exit_val_65ltv']:,.0f}")
            _rfc4.metric(
                "Implied Exit Cap Rate",
                f"{_refi['implied_cap']:.2%}" if _refi['implied_cap'] else "—",
                delta=f"Entry GIY: {_refi['entry_giy']:.2%}",
                delta_color="off",
            )
            if _refi.get('flag'):
                st.warning(_refi['flag'])
            else:
                st.success(
                    "Refinancing cap rate assumption is broadly in line with entry yield — "
                    "no bullish compression required."
                )

        # ── 6. CPI Rent Projection ────────────────────────────────────────────
        st.divider()
        st.markdown("### 6 · CPI Rent Projection")
        st.caption(
            f"Rent at Years 3 / 5 / 7 / 10 at **{cpi_indexation:.1%} CPI**. "
            "Each tenant's effective increase = CPI × their lease passthrough rate. "
            "Non-indexed tenants show flat rent (0% passthrough)."
        )
        if rr.empty:
            st.info("Upload rent roll data to enable CPI projections.")
        else:
            _cpi_df = _di_cpi_projection(rr, cpi_indexation)
            if _cpi_df.empty:
                st.info("No active tenants.")
            else:
                st.dataframe(_cpi_df, use_container_width=True, hide_index=True)
                # Indexed vs non-indexed summary
                _idx_rent = _nidx_rent = 0.0
                for _, _t in rr[~rr['is_vacant']].iterrows():
                    _ci = _t.get('cpi_clause', False)
                    if isinstance(_ci, str):
                        _ci = _ci.strip().upper() not in ('', 'NO', 'N', 'FALSE', '0')
                    _ar = float(_t.get('annual_rent', 0) or 0)
                    if _ci: _idx_rent  += _ar
                    else:   _nidx_rent += _ar
                _tot_r = _idx_rent + _nidx_rent
                _ci1, _ci2, _ci3 = st.columns(3)
                _ci1.metric("CPI-Indexed Rent",
                            f"EUR {_idx_rent:,.0f}",
                            delta=f"{_idx_rent/_tot_r:.0%} of passing rent" if _tot_r > 0 else None)
                _ci2.metric("Non-Indexed Rent",
                            f"EUR {_nidx_rent:,.0f}",
                            delta=f"{_nidx_rent/_tot_r:.0%} of passing rent" if _tot_r > 0 else None,
                            delta_color="off")
                _yr10_total = _idx_rent * ((1 + cpi_indexation) ** 10) + _nidx_rent
                _ci3.metric("Total Rent at Year 10",
                            f"EUR {_yr10_total:,.0f}",
                            delta=f"+EUR {_yr10_total - _tot_r:,.0f} vs today",
                            delta_color="normal")

        # ── 7. Share Deal Calculator ──────────────────────────────────────────
        st.divider()
        st.markdown("### 7 · Share Deal Calculator")
        st.caption(
            "Compare total acquisition costs: asset deal (full GrESt) vs share deal "
            "(0% GrESt + 0.5% legal/structuring premium). "
            "Shows EUR net saving and its value as % of equity deployed."
        )
        _sd = _di_share_deal_calc(metrics, deal)
        if not _sd or _sd['asking'] <= 0:
            st.info("Enter asking price and equity to enable share deal analysis.")
        else:
            _show_sd = st.toggle("Show cost comparison table", value=True, key='di_sd_toggle')
            if _show_sd:
                _sd_tbl = pd.DataFrame({
                    'Cost Component': [
                        'Asking Price',
                        f'GrESt ({_sd["grest_rate"]:.1%})',
                        'Notary + Land Registry (~1.5%)',
                        'Legal / Structuring Premium (0.5%)',
                        'TOTAL Acquisition Cost',
                    ],
                    'Asset Deal': [
                        f"EUR {_sd['asking']:,.0f}",
                        f"EUR {_sd['grest_asset']:,.0f}",
                        f"EUR {_sd['notary_asset']:,.0f}",
                        "—",
                        f"EUR {_sd['total_acq_asset']:,.0f}",
                    ],
                    'Share Deal': [
                        f"EUR {_sd['asking']:,.0f}",
                        "EUR 0  (exempt)",
                        f"EUR {_sd['notary_share']:,.0f}",
                        f"EUR {_sd['share_legal']:,.0f}",
                        f"EUR {_sd['total_acq_share']:,.0f}",
                    ],
                })
                st.dataframe(_sd_tbl, use_container_width=True, hide_index=True)
            _sdc1, _sdc2, _sdc3 = st.columns(3)
            _sdc1.metric("GrESt Saving (gross)", f"EUR {_sd['grest_asset']:,.0f}")
            _sdc2.metric("Net Saving (after premium)", f"EUR {_sd['net_saving']:,.0f}",
                         delta_color="normal" if _sd['net_saving'] > 0 else "inverse")
            if _sd.get('pct_of_equity'):
                _sdc3.metric("Saving as % of Equity", f"{_sd['pct_of_equity']:.1%}",
                             delta="equity freed for CAPEX / reserves")
            st.warning(
                "**Legal note — read before structuring:**\n\n"
                "**1 · Structuring requirement:** GrESt is avoided only when the acquiring entity "
                "holds less than 90% of the SPV post-acquisition (§1 Abs. 3 GrEStG), or uses a "
                "qualifying holding structure that satisfies the 10-year seasoning period.\n\n"
                "**2 · Re-trigger risk (critical for exit planning):** If the shares are sold again "
                "within 10 years of the original acquisition, GrESt can be triggered retroactively "
                "under §1(2a) and §1(2b) GrEStG. A planned exit at Year 3, 5, or 7 may not "
                "achieve the full GrESt saving shown above — the tax authority can assess GrESt "
                "on the original transaction at the point of the subsequent transfer.\n\n"
                "**3 · Saving shown assumes hold > 10 years.** The EUR saving displayed is the "
                "maximum achievable only when the structure is held beyond the full 10-year "
                "seasoning window. Shorter hold periods carry partial or full re-trigger exposure.\n\n"
                "**4 · Tax counsel required.** Full tax and legal advice is mandatory before "
                "proceeding with any share deal structure. Requires corporate DD, W&I insurance, "
                "and lender consent. Not available for direct asset purchases."
            )

        # ── 8. Tenant Concentration Risk ─────────────────────────────────────
        st.divider()
        st.markdown("### 8 · Tenant Concentration Risk")
        st.caption(
            "Income dependency analysis: single-name concentration, sector diversity, "
            "rent expiry profile, and anchor dependency. WAULT alone hides cliff years — "
            "this section maps which EUR of rent expires when."
        )
        if rr.empty:
            st.info("Upload rent roll data to enable concentration analysis.")
        else:
            _tc = _di_tenant_concentration(rr)
            if not _tc:
                st.info("No active tenants in rent roll.")
            else:
                # ── 8a. Top-N income concentration ───────────────────────────
                st.markdown("**Top Tenant Income Concentration**")
                _t1_pct = _tc['top1_pct']
                _t3_pct = _tc['top3_pct']
                _t5_pct = _tc['top5_pct']
                _tnc1, _tnc2, _tnc3 = st.columns(3)
                _tnc1.metric(
                    f"Top-1 ({_tc['top1_name'][:22]})",
                    f"{_t1_pct:.1%}",
                    delta="🔴 Exceeds 50% threshold" if _t1_pct > 0.50 else "Within limit",
                    delta_color="inverse" if _t1_pct > 0.50 else "normal",
                    help="Single-tenant concentration > 50% is a RED flag for most German CRE lenders.",
                )
                _tnc2.metric(
                    "Top-3 Combined",
                    f"{_t3_pct:.1%}",
                    delta="🔴 Exceeds 75% threshold" if _t3_pct > 0.75 else "Within limit",
                    delta_color="inverse" if _t3_pct > 0.75 else "normal",
                    help="Top-3 concentration > 75% signals insufficient tenant diversification.",
                )
                _tnc3.metric(
                    "Top-5 Combined",
                    f"{_t5_pct:.1%}",
                    help="Provides context on mid-tier concentration beyond top-3.",
                )

                # Horizontal bar chart — all active tenants, sorted by rent
                _bar_total = _tc['total_rent']
                _BAR_MAX_W = 300   # px for 100% share
                st.markdown("**Rent Share per Tenant**")
                for _tt in _tc['top_tenants']:
                    _bar_w  = int(_tt['pct'] * _BAR_MAX_W)
                    _bar_c  = '#ef4444' if _tt['pct'] > 0.50 else ('#f59e0b' if _tt['pct'] > 0.25 else '#3b82f6')
                    _name_s = str(_tt['tenant'])[:32]
                    st.markdown(
                        f'<div style="margin:3px 0;line-height:1.5;">'
                        f'<span style="display:inline-block;width:200px;font-size:0.80rem;">{_name_s}</span>'
                        f'<div style="display:inline-block;background:#e5e7eb;width:{_BAR_MAX_W}px;'
                        f'height:13px;border-radius:4px;vertical-align:middle;">'
                        f'<div style="background:{_bar_c};width:{_bar_w}px;height:13px;border-radius:4px;"></div></div>'
                        f'<span style="margin-left:8px;font-size:0.80rem;font-weight:600;">{_tt["pct"]:.1%}</span>'
                        f'<span style="margin-left:8px;font-size:0.75rem;color:#9ca3af;">'
                        f'EUR {_tt["rent"]:,.0f}</span>'
                        f'</div>',
                        unsafe_allow_html=True,
                    )
                if _t1_pct > 0.50:
                    st.error(
                        f"🔴 **Single-tenant concentration: {_t1_pct:.1%}** — "
                        f"{_tc['top1_name']} represents over 50% of income. "
                        "Most German senior lenders require a concentration covenant or "
                        "additional security when a single tenant exceeds 50%."
                    )
                elif _t3_pct > 0.75:
                    st.warning(
                        f"🟡 **Top-3 concentration: {_t3_pct:.1%}** — "
                        "three tenants control more than 75% of income. "
                        "Limited diversification benefit; lease expiries must be staggered."
                    )

                # ── 8b. Sector breakdown ──────────────────────────────────────
                if _tc['sector_breakdown']:
                    st.markdown("**Income by Trade Sector**")
                    _sec_data = {s['sector']: s['rent'] for s in _tc['sector_breakdown']}
                    _sec_df   = pd.DataFrame.from_dict(
                        _sec_data, orient='index', columns=['Annual Rent (EUR)']
                    )
                    st.bar_chart(_sec_df, height=200)
                    # Caption with % breakdown
                    _sec_lines = [f"{s['sector']}: {s['pct']:.1%}" for s in _tc['sector_breakdown']]
                    st.caption("Sector split: " + "  ·  ".join(_sec_lines))

                # ── 8c. Lease expiry by year (rent-weighted) ──────────────────
                if _tc['expiry_by_year']:
                    st.markdown("**Rent Expiry Profile — Next 10 Years**")
                    st.caption(
                        "Shows EUR of annual rent expiring each calendar year. "
                        "Cliff years (tall bars) are re-letting risk concentrations "
                        "that WAULT alone does not reveal."
                    )
                    _exp_df = pd.DataFrame(_tc['expiry_by_year'])
                    _exp_df['Year'] = _exp_df['year'].astype(str)
                    _exp_plot = _exp_df.set_index('Year')[['rent']].rename(
                        columns={'rent': 'Rent Expiring (EUR)'}
                    )
                    st.bar_chart(_exp_plot, height=220)

                    # Flag years where > 25% of total rent expires
                    _cliff_years = [
                        e for e in _tc['expiry_by_year'] if e['pct'] > 0.25 and e['rent'] > 0
                    ]
                    if _cliff_years:
                        _cy_str = ', '.join(
                            f"{e['year']} ({e['pct']:.0%}, EUR {e['rent']:,.0f})"
                            for e in _cliff_years
                        )
                        st.warning(
                            f"🟡 **Expiry cliff year(s): {_cy_str}** — "
                            "more than 25% of total rent expires in a single year. "
                            "Re-letting risk concentrated; stress-test with Section 2 Income Cliff."
                        )
                    # Table view
                    _exp_tbl = pd.DataFrame([
                        {
                            'Year':                 e['year'],
                            'Rent Expiring (EUR)':  f"EUR {e['rent']:,.0f}" if e['rent'] > 0 else '—',
                            '% of Total Rent':      f"{e['pct']:.1%}"       if e['rent'] > 0 else '—',
                        }
                        for e in _tc['expiry_by_year']
                    ])
                    with st.expander("View expiry table"):
                        st.dataframe(_exp_tbl, use_container_width=True, hide_index=True)

                # ── 8d. Anchor dependency ─────────────────────────────────────
                st.markdown("**Anchor Dependency Score**")
                _anc_pct = _tc['anchor_pct']
                _anc_col1, _anc_col2 = st.columns(2)
                _anc_col1.metric(
                    "Anchor Income Share",
                    f"{_anc_pct:.1%}",
                    delta=(
                        "🔴 Below 40% minimum" if _anc_pct < 0.40
                        else ("Within range" if _anc_pct <= 0.65 else "🟡 Anchor concentration risk (>65%)")
                    ),
                    delta_color=(
                        "inverse" if _anc_pct < 0.40 or _anc_pct > 0.65 else "normal"
                    ),
                    help=(
                        "Anchor tenants provide covenant strength and footfall. "
                        "< 40% = limited anchor base; > 65% = single-covenant concentration risk."
                    ),
                )
                _anc_col2.metric(
                    "Anchor Annual Rent",
                    f"EUR {_tc['anchor_rent']:,.0f}",
                    delta=f"of EUR {_tc['total_rent']:,.0f} total",
                    delta_color="off",
                )
                if _anc_pct < 0.40:
                    st.error(
                        f"🔴 **Anchor dependency: {_anc_pct:.1%}** — below the 40% threshold. "
                        "Anchor tenants drive footfall for non-anchor tenants. "
                        "A low anchor share increases void risk across the whole scheme."
                    )
                elif _anc_pct > 0.65:
                    st.warning(
                        f"🟡 **High anchor concentration: {_anc_pct:.1%}** — "
                        "loss of one anchor tenant would severely impair income and footfall. "
                        "Check covenant quality and remaining WAULT of anchor leases specifically."
                    )

        # ── 9. Deal SWOT Analysis ─────────────────────────────────────────────
        st.divider()
        st.markdown("### 9 · Deal SWOT Analysis")
        st.caption(
            "Auto-generated from deal data already calculated — no external data required. "
            "Strengths from green metrics · Weaknesses from QA flags · "
            "Opportunities quantified in EUR · Threats from stress tests. "
            "Max 6 items per quadrant, prioritised by financial impact."
        )
        try:
            if rr.empty:
                st.info("Upload deal data to generate SWOT analysis.")
            else:
                _swot = _di_swot(
                    metrics, qa, rr, deal,
                    noi, loan, int_rt, amrt_rt,
                    io_years, loan_type_key,
                    cpi_indexation, y1_debt_svc,
                )

                # Helper to render one SWOT box
                def _swot_box(col, title, icon, items, bg, border, text_col):
                    _li = ''.join(
                        f'<li style="margin:4px 0;line-height:1.5;">{item}</li>'
                        for item in items
                    ) or '<li style="color:#9ca3af;font-style:italic;">None identified</li>'
                    col.markdown(
                        f'<div style="background:{bg};border:1.5px solid {border};'
                        f'border-radius:8px;padding:14px 16px;min-height:200px;">'
                        f'<div style="font-weight:700;color:{text_col};margin-bottom:8px;'
                        f'font-size:0.88rem;">{icon} {title}</div>'
                        f'<ul style="margin:0;padding-left:16px;color:{text_col};'
                        f'font-size:0.76rem;line-height:1.5;">{_li}</ul></div>',
                        unsafe_allow_html=True,
                    )

                _q1, _q2, _q3, _q4 = st.columns(4)
                _swot_box(_q1, 'STRENGTHS',    '✅', _swot['strengths'],
                          '#d1fae5', '#6ee7b7', '#065f46')
                _swot_box(_q2, 'WEAKNESSES',   '⚠️', _swot['weaknesses'],
                          '#fef3c7', '#fcd34d', '#92400e')
                _swot_box(_q3, 'OPPORTUNITIES','🔵', _swot['opportunities'],
                          '#dbeafe', '#93c5fd', '#1e40af')
                _swot_box(_q4, 'THREATS',      '🔴', _swot['threats'],
                          '#fee2e2', '#fca5a5', '#991b1b')

                # Summary sentence
                st.markdown(
                    f'<div style="background:#f8fafc;border:1px solid #e2e8f0;'
                    f'border-radius:8px;padding:12px 18px;margin-top:14px;">'
                    f'<span style="font-weight:700;color:#374151;font-size:0.84rem;">'
                    f'SWOT Summary: </span>'
                    f'<span style="color:#374151;font-size:0.84rem;">{_swot["summary"]}</span>'
                    f'</div>',
                    unsafe_allow_html=True,
                )
        except Exception as _swot_err:
            st.error(f"Section 9 error: {type(_swot_err).__name__}: {_swot_err}")
            st.exception(_swot_err)

        # ── 10. Scenario Engine ───────────────────────────────────────────────
        st.divider()
        st.markdown("### 10 · Scenario Engine")
        st.caption(
            "Three scenarios run automatically from existing deal data. "
            "Base = current inputs · Stress = anchor exit + rate shock + cap rate widening · "
            "Upside = void fill + higher CPI + cap rate tightening. "
            "IRR computed on 10-year equity cash flows."
        )
        try:
            if rr.empty or loan <= 0:
                st.info("Upload deal data and enter financing parameters to enable scenario analysis.")
            else:
                _sev = st.select_slider(
                    "Stress Severity",
                    options=[1, 2, 3],
                    value=2,
                    format_func=lambda v: {1: "1 — Mild", 2: "2 — Base Stress", 3: "3 — Severe"}[v],
                    key='di_scen_sev',
                    help=(
                        "Mild: anchor renews at 85% rent, cap +25bps. "
                        "Base: anchor exits, cap +75bps, rate +50bps. "
                        "Severe: anchor exits, cap +150bps, rate +100bps, CPI 0.5%."
                    ),
                )
                _scen = _di_scenario_engine(
                    metrics, rr, deal, noi, loan, int_rt, amrt_rt,
                    io_years, loan_type_key, cpi_indexation,
                    stress_severity=_sev,
                )

                # ── Comparison table ──────────────────────────────────────────────
                def _cell_html(val, fmt_fn, green_fn, amber_fn):
                    """Return a table cell (td) string with colour-coded background."""
                    if val is None:
                        return '<td style="padding:6px 10px;text-align:right;color:#9ca3af;">—</td>'
                    text = fmt_fn(val)
                    if green_fn(val):
                        bg = '#d1fae5'; fg = '#065f46'
                    elif amber_fn(val):
                        bg = '#fef3c7'; fg = '#92400e'
                    else:
                        bg = '#fee2e2'; fg = '#991b1b'
                    return (f'<td style="padding:6px 10px;text-align:right;'
                            f'background:{bg};color:{fg};font-weight:600;">{text}</td>')

                def _verdict_cell(v):
                    _vc = {'PASS': ('#d1fae5','#065f46'), 'INVESTIGATE': ('#fef3c7','#92400e'),
                           'DECLINE': ('#fee2e2','#991b1b')}.get(v, ('#f3f4f6','#374151'))
                    return (f'<td style="padding:6px 10px;text-align:center;'
                            f'background:{_vc[0]};color:{_vc[1]};font-weight:700;">{v}</td>')

                def _delta_cell(base_v, stress_v, fmt_fn, positive_is_good=True):
                    if base_v is None or stress_v is None:
                        return '<td style="padding:6px 10px;text-align:right;color:#9ca3af;">—</td>'
                    delta = stress_v - base_v
                    pct   = fmt_fn(delta)
                    if (delta > 0) == positive_is_good:
                        fg = '#065f46'
                    else:
                        fg = '#991b1b'
                    sign = '+' if delta > 0 else ''
                    return (f'<td style="padding:6px 10px;text-align:right;'
                            f'color:{fg};font-weight:600;">{sign}{pct}</td>')

                B = _scen['base']
                S = _scen['stress']
                U = _scen['upside']
                _hdr = (
                    '<thead><tr style="background:#f1f5f9;">'
                    '<th style="padding:8px 10px;text-align:left;font-size:0.8rem;">Metric</th>'
                    f'<th style="padding:8px 10px;text-align:right;font-size:0.8rem;">Base Case</th>'
                    f'<th style="padding:8px 10px;text-align:right;font-size:0.8rem;">'
                    f'Stress ({_scen["sev_label"]})</th>'
                    f'<th style="padding:8px 10px;text-align:right;font-size:0.8rem;">Upside</th>'
                    f'<th style="padding:8px 10px;text-align:right;font-size:0.8rem;">'
                    f'Δ Stress vs Base</th>'
                    '</tr></thead>'
                )
                def _lbl(txt):
                    return f'<td style="padding:6px 10px;font-size:0.8rem;font-weight:500;">{txt}</td>'

                _rows = [
                    # NOI
                    '<tr>' + _lbl('NOI (EUR/yr)') +
                    ''.join(
                        f'<td style="padding:6px 10px;text-align:right;">'
                        f'EUR {sc["noi"]:,.0f}</td>'
                        for sc in (B, S, U)
                    ) +
                    _delta_cell(B['noi'], S['noi'], lambda d: f'EUR {d:,.0f}') +
                    '</tr>',
                    # DSCR
                    '<tr>' + _lbl('DSCR') +
                    _cell_html(B['dscr'], lambda v: f'{v:.2f}x',
                               lambda v: v >= 1.30, lambda v: v >= 1.20) +
                    _cell_html(S['dscr'], lambda v: f'{v:.2f}x',
                               lambda v: v >= 1.30, lambda v: v >= 1.20) +
                    _cell_html(U['dscr'], lambda v: f'{v:.2f}x',
                               lambda v: v >= 1.30, lambda v: v >= 1.20) +
                    _delta_cell(B['dscr'], S['dscr'], lambda d: f'{d:.2f}x') +
                    '</tr>',
                    # ICR
                    '<tr>' + _lbl('ICR') +
                    _cell_html(B['icr'], lambda v: f'{v:.2f}x',
                               lambda v: v >= 1.75, lambda v: v >= 1.50) +
                    _cell_html(S['icr'], lambda v: f'{v:.2f}x',
                               lambda v: v >= 1.75, lambda v: v >= 1.50) +
                    _cell_html(U['icr'], lambda v: f'{v:.2f}x',
                               lambda v: v >= 1.75, lambda v: v >= 1.50) +
                    _delta_cell(B['icr'], S['icr'], lambda d: f'{d:.2f}x') +
                    '</tr>',
                    # LTV
                    '<tr>' + _lbl('LTV') +
                    _cell_html(B['ltv'], lambda v: f'{v:.1%}',
                               lambda v: v <= 0.65, lambda v: v <= 0.70) +
                    _cell_html(S['ltv'], lambda v: f'{v:.1%}',
                               lambda v: v <= 0.65, lambda v: v <= 0.70) +
                    _cell_html(U['ltv'], lambda v: f'{v:.1%}',
                               lambda v: v <= 0.65, lambda v: v <= 0.70) +
                    '<td style="padding:6px 10px;text-align:right;color:#9ca3af;">—</td>' +
                    '</tr>',
                    # GIY
                    '<tr>' + _lbl('GIY') +
                    _cell_html(B['giy'], lambda v: f'{v:.2%}',
                               lambda v: v >= 0.055, lambda v: v >= 0.045) +
                    _cell_html(S['giy'], lambda v: f'{v:.2%}',
                               lambda v: v >= 0.055, lambda v: v >= 0.045) +
                    _cell_html(U['giy'], lambda v: f'{v:.2%}',
                               lambda v: v >= 0.055, lambda v: v >= 0.045) +
                    '<td style="padding:6px 10px;text-align:right;color:#9ca3af;">—</td>' +
                    '</tr>',
                    # IRR
                    '<tr>' + _lbl('IRR (10yr equity)') +
                    _cell_html(B['irr'], lambda v: f'{v:.1%}',
                               lambda v: v >= 0.10, lambda v: v >= 0.07) +
                    _cell_html(S['irr'], lambda v: f'{v:.1%}',
                               lambda v: v >= 0.10, lambda v: v >= 0.07) +
                    _cell_html(U['irr'], lambda v: f'{v:.1%}',
                               lambda v: v >= 0.10, lambda v: v >= 0.07) +
                    _delta_cell(B['irr'], S['irr'], lambda d: f'{d:.1%}') +
                    '</tr>',
                    # Verdict
                    '<tr>' + _lbl('Verdict') +
                    _verdict_cell(B['verdict']) + _verdict_cell(S['verdict']) +
                    _verdict_cell(U['verdict']) +
                    '<td style="padding:6px 10px;text-align:right;color:#9ca3af;">—</td>' +
                    '</tr>',
                ]
                _tbl_html = (
                    '<table style="width:100%;border-collapse:collapse;'
                    'font-size:0.8rem;border:1px solid #e5e7eb;border-radius:6px;">'
                    + _hdr + '<tbody>' + ''.join(_rows) + '</tbody></table>'
                )
                st.markdown(_tbl_html, unsafe_allow_html=True)
                st.caption(
                    f"Stress anchor: {_scen['anchor_name']} "
                    f"(EUR {_scen['anchor_rent']:,.0f}/yr at risk). "
                    f"Upside void ERV: EUR {_scen['void_erv']:,.0f}/yr (steady-state, Year 2+). "
                    "LTV is a purchase-date metric — unchanged across scenarios. "
                    "IRR = 10-year equity IRR; None shown as — when equity or price not entered."
                )

                # ── Plain-English scenario summaries ──────────────────────────────
                st.markdown("")
                _sm = _scen['summary']
                _sc1, _sc2, _sc3 = st.columns(3)
                for _col, _key, _bg, _bd, _fc in [
                    (_sc1, 'base',   '#f0fdf4', '#86efac', '#14532d'),
                    (_sc2, 'stress', '#fffbeb', '#fcd34d', '#78350f'),
                    (_sc3, 'upside', '#eff6ff', '#93c5fd', '#1e3a8a'),
                ]:
                    _col.markdown(
                        f'<div style="background:{_bg};border:1px solid {_bd};'
                        f'border-radius:8px;padding:12px 14px;font-size:0.78rem;'
                        f'color:{_fc};line-height:1.5;">{_sm[_key]}</div>',
                        unsafe_allow_html=True,
                    )
        except Exception as _scen_err:
            st.error(f"Section 10 error: {type(_scen_err).__name__}: {_scen_err}")
            st.exception(_scen_err)

        # ── 11. Relationship Intelligence Layer ──────────────────────────────
        st.divider()
        # Section header with grey pill badge
        st.markdown(
            '### 11 · Relationship Intelligence Layer &nbsp;'
            '<span style="background:#e5e7eb;color:#374151;font-size:0.70rem;'
            'font-weight:600;padding:3px 10px;border-radius:999px;'
            'vertical-align:middle;">Analyst Input — Not Calculated</span>',
            unsafe_allow_html=True,
        )
        st.caption(
            "Input soft information — break option signals, renewal conversations, "
            "market intelligence. The tool recalculates DSCR under your relationship "
            "assumptions vs the model assumption and shows the delta."
        )

        try:
            if rr.empty:
                st.info("Upload rent roll data to enable Relationship Intelligence.")
            else:
                import datetime as _ridt
                _today = _ridt.date.today()
                _cutoff_3yr = _ridt.date(_today.year + 3, _today.month, _today.day)

                # Identify qualifying tenants: has break_option OR expiry within 3yr
                _active_rr = rr[~rr.get('is_vacant', pd.Series(False, index=rr.index))]

                def _ri_has_break(row):
                    bo = row.get('break_option')
                    if bo is None or (isinstance(bo, float) and pd.isna(bo)):
                        return False
                    try:
                        d = bo.date() if hasattr(bo, 'date') else bo
                        return isinstance(d, _ridt.date)
                    except Exception:
                        return False

                def _ri_expiry_near(row):
                    ex = row.get('lease_expiry')
                    if ex is None or (isinstance(ex, float) and pd.isna(ex)):
                        return False
                    try:
                        d = ex.date() if hasattr(ex, 'date') else ex
                        return isinstance(d, _ridt.date) and d <= _cutoff_3yr
                    except Exception:
                        return False

                _qualifying = []
                for _, _row in _active_rr.iterrows():
                    _hb = _ri_has_break(_row)
                    _en = _ri_expiry_near(_row)
                    if _hb or _en:
                        _qualifying.append((_row, _hb, _en))

                if not _qualifying:
                    st.success(
                        "No near-term break or expiry risk — Relationship Intelligence "
                        "not required for this deal."
                    )
                else:
                    # Disclaimer
                    st.warning(
                        "**Analyst Override** — these inputs are not verified market data. "
                        "They represent the analyst's relationship intelligence and must be "
                        "disclosed in any IC submission. GREEN signals reduce modelled risk "
                        "but do not eliminate it — leases must be formally signed before IC."
                    )

                    # Initialise session-state bucket for this section
                    # Reset when a new file is uploaded (handled by the file_uploader key
                    # already resetting rr; we tie our state to the tenant list hash)
                    _tenant_key_hash = '_'.join(
                        str(_row.get('tenant', '')) for _row, _, _ in _qualifying
                    )
                    _ri_state_key = f'ri_state_{hash(_tenant_key_hash) & 0xFFFFFF}'
                    if _ri_state_key not in st.session_state:
                        st.session_state[_ri_state_key] = {}

                    _ri_st = st.session_state[_ri_state_key]

                    _signal_map   = {}   # tenant_name → 'GREEN' | 'AMBER' | 'RED'
                    _note_map     = {}   # tenant_name → str note

                    for _row, _hb, _en in _qualifying:
                        _tname  = str(_row.get('tenant', '?'))
                        _trent  = float(_row.get('annual_rent', 0) or 0)
                        _tarea  = float(_row.get('area_sqm', 0) or 0)

                        # Risk label
                        _risk_parts = []
                        if _hb:
                            try:
                                _bd = _row['break_option']
                                _bd = _bd.date() if hasattr(_bd, 'date') else _bd
                                _risk_parts.append(f"Break option {_bd.strftime('%b %Y')}")
                            except Exception:
                                _risk_parts.append("Break option")
                        if _en:
                            try:
                                _ex = _row['lease_expiry']
                                _ex = _ex.date() if hasattr(_ex, 'date') else _ex
                                _risk_parts.append(f"Expiry {_ex.strftime('%b %Y')}")
                            except Exception:
                                _risk_parts.append("Near expiry")
                        _risk_label = ' · '.join(_risk_parts)

                        st.markdown(
                            f'<div style="background:#f8fafc;border:1px solid #e2e8f0;'
                            f'border-radius:8px;padding:12px 16px;margin-bottom:4px;">'
                            f'<span style="font-weight:700;font-size:0.86rem;">{_tname}</span>'
                            f'&nbsp;&nbsp;<span style="color:#6b7280;font-size:0.80rem;">'
                            f'{_risk_label}</span>'
                            f'&nbsp;&nbsp;<span style="color:#374151;font-size:0.80rem;">'
                            f'EUR {_trent:,.0f}/yr at risk</span>'
                            f'</div>',
                            unsafe_allow_html=True,
                        )

                        _sig_key  = f'ri_sig_{_tname}'
                        _note_key = f'ri_note_{_tname}'

                        # Default to AMBER if not set
                        _current_sig  = _ri_st.get(_sig_key, 'AMBER')
                        _current_note = _ri_st.get(_note_key, '')

                        _col_sig, _col_note = st.columns([1, 2])
                        with _col_sig:
                            _new_sig = st.radio(
                                f"Signal — {_tname[:28]}",
                                options=['GREEN', 'AMBER', 'RED'],
                                index=['GREEN', 'AMBER', 'RED'].index(_current_sig),
                                format_func=lambda v: {
                                    'GREEN': '🟢 GREEN — renewal / extension intent',
                                    'AMBER': '🟡 AMBER — unknown (model assumption)',
                                    'RED':   '🔴 RED — signalled exit / contracting',
                                }[v],
                                key=f'ri_radio_{_tname}',
                                horizontal=False,
                            )
                            _ri_st[_sig_key] = _new_sig
                            _signal_map[_tname] = _new_sig

                        with _col_note:
                            _new_note = st.text_area(
                                f"Intelligence note (optional) — {_tname[:25]}",
                                value=_current_note,
                                height=90,
                                key=f'ri_note_ta_{_tname}',
                                placeholder=(
                                    "e.g. Spoke to JAWOLL property director March 2026 — "
                                    "confirmed expansion plan, 3 new stores planned in region."
                                ),
                            )
                            _ri_st[_note_key] = _new_note
                            _note_map[_tname] = _new_note

                    # ── DSCR Comparison ──────────────────────────────────────
                    st.markdown("#### Model vs Relationship-Adjusted DSCR")

                    if y1_debt_svc > 0 and noi > 0:
                        # MODEL DSCR: all qualifying tenants exit (worst-case stress)
                        _model_noi = noi
                        for _row, _hb, _en in _qualifying:
                            _tname = str(_row.get('tenant', '?'))
                            _trent = float(_row.get('annual_rent', 0) or 0)
                            _tarea = float(_row.get('area_sqm', 0) or 0)
                            _model_noi -= _trent
                            _model_noi -= _tarea * 20.0  # VOID_HOLD_RATE

                        # ADJUSTED DSCR: GREEN = stay, AMBER = exit (model), RED = exit
                        _adj_noi = noi
                        for _row, _hb, _en in _qualifying:
                            _tname = str(_row.get('tenant', '?'))
                            _sig   = _signal_map.get(_tname, 'AMBER')
                            _trent = float(_row.get('annual_rent', 0) or 0)
                            _tarea = float(_row.get('area_sqm', 0) or 0)
                            if _sig == 'GREEN':
                                pass  # tenant stays at current rent — no adjustment
                            else:    # AMBER or RED → exit
                                _adj_noi -= _trent
                                _adj_noi -= _tarea * 20.0  # VOID_HOLD_RATE

                        _model_dscr = _model_noi / y1_debt_svc
                        _adj_dscr   = _adj_noi   / y1_debt_svc
                        _delta_dscr = _adj_dscr  - _model_dscr

                        def _dscr_colour(v):
                            if v >= 1.30:   return '#d1fae5', '#065f46'
                            elif v >= 1.20: return '#fef3c7', '#92400e'
                            else:           return '#fee2e2', '#991b1b'

                        _mc_bg, _mc_fg = _dscr_colour(_model_dscr)
                        _ac_bg, _ac_fg = _dscr_colour(_adj_dscr)

                        _dcol1, _dcol2, _dcol3 = st.columns(3)
                        _dcol1.markdown(
                            f'<div style="background:{_mc_bg};border:1.5px solid {_mc_fg};'
                            f'border-radius:8px;padding:14px 16px;text-align:center;">'
                            f'<div style="font-size:0.75rem;color:{_mc_fg};font-weight:600;'
                            f'margin-bottom:6px;">MODEL DSCR<br>(all at-risk tenants exit)</div>'
                            f'<div style="font-size:1.6rem;font-weight:800;color:{_mc_fg};">'
                            f'{_model_dscr:.2f}x</div>'
                            f'<div style="font-size:0.72rem;color:{_mc_fg};margin-top:4px;">'
                            f'NOI EUR {_model_noi:,.0f}/yr</div>'
                            f'</div>',
                            unsafe_allow_html=True,
                        )
                        _dcol2.markdown(
                            f'<div style="background:{_ac_bg};border:1.5px solid {_ac_fg};'
                            f'border-radius:8px;padding:14px 16px;text-align:center;">'
                            f'<div style="font-size:0.75rem;color:{_ac_fg};font-weight:600;'
                            f'margin-bottom:6px;">RELATIONSHIP-ADJUSTED DSCR<br>'
                            f'(GREEN tenants stay)</div>'
                            f'<div style="font-size:1.6rem;font-weight:800;color:{_ac_fg};">'
                            f'{_adj_dscr:.2f}x</div>'
                            f'<div style="font-size:0.72rem;color:{_ac_fg};margin-top:4px;">'
                            f'NOI EUR {_adj_noi:,.0f}/yr</div>'
                            f'</div>',
                            unsafe_allow_html=True,
                        )
                        _delta_colour = '#065f46' if _delta_dscr >= 0 else '#991b1b'
                        _delta_sign   = '+' if _delta_dscr >= 0 else ''
                        _dcol3.markdown(
                            f'<div style="background:#f8fafc;border:1.5px solid #e2e8f0;'
                            f'border-radius:8px;padding:14px 16px;text-align:center;">'
                            f'<div style="font-size:0.75rem;color:#374151;font-weight:600;'
                            f'margin-bottom:6px;">DELTA<br>(Adj. minus Model)</div>'
                            f'<div style="font-size:1.6rem;font-weight:800;'
                            f'color:{_delta_colour};">'
                            f'{_delta_sign}{_delta_dscr:.2f}x</div>'
                            f'<div style="font-size:0.72rem;color:#6b7280;margin-top:4px;">'
                            f'GREEN signals: {sum(1 for v in _signal_map.values() if v=="GREEN")}'
                            f'</div>'
                            f'</div>',
                            unsafe_allow_html=True,
                        )

                        # ── Relationship-Adjusted Verdict ─────────────────────
                        st.markdown("---")
                        _n_green = sum(1 for v in _signal_map.values() if v == 'GREEN')
                        _n_total = len(_signal_map)

                        if _adj_dscr >= 1.30:
                            _ra_verdict = 'PASS'
                            _ra_bg, _ra_fg = '#d1fae5', '#065f46'
                        elif _adj_dscr >= 1.20:
                            _ra_verdict = 'INVESTIGATE'
                            _ra_bg, _ra_fg = '#fef3c7', '#92400e'
                        else:
                            _ra_verdict = 'DECLINE'
                            _ra_bg, _ra_fg = '#fee2e2', '#991b1b'

                        # Model verdict — use the authoritative verdict already computed,
                        # which includes the WAULT hard-DECLINE override and all other rules.
                        _model_verdict_str = verdict.get("recommendation", "INVESTIGATE")

                        _same = _ra_verdict == _model_verdict_str
                        _delta_txt = (
                            f"Same as model ({_model_verdict_str})"
                            if _same
                            else f"Model: {_model_verdict_str} → Adjusted: {_ra_verdict}"
                        )

                        st.markdown(
                            f'<div style="background:{_ra_bg};border:1.5px solid {_ra_fg};'
                            f'border-radius:8px;padding:14px 18px;margin-top:8px;">'
                            f'<span style="font-weight:700;font-size:0.90rem;color:{_ra_fg};">'
                            f'Relationship-Adjusted Verdict: {_ra_verdict}</span>'
                            f'<span style="color:{_ra_fg};font-size:0.82rem;margin-left:16px;">'
                            f'{_delta_txt}</span>'
                            f'<span style="color:{_ra_fg};font-size:0.82rem;margin-left:16px;">'
                            f'GREEN signals entered: {_n_green} of {_n_total}</span>'
                            f'</div>',
                            unsafe_allow_html=True,
                        )
                    else:
                        st.info(
                            "Enter financing parameters to enable DSCR comparison. "
                            "Radio selectors above are active — DSCR will calculate once loan data is provided."
                        )

        except Exception as _ri_err:
            st.error(f"Section 11 error: {type(_ri_err).__name__}: {_ri_err}")
            st.exception(_ri_err)

    # ------------------------------------------------------------------ DECISION DASHBOARD
    with tab_dash:
        try:
            render_decision_dashboard(
                metrics=metrics,
                deal=deal,
                verdict=verdict,
                qa=qa,
                base_irr=base_irr,
                rr=rr,
            )
        except Exception as _dd_err:
            st.error(f"Decision Dashboard error: {type(_dd_err).__name__}: {_dd_err}")
            st.exception(_dd_err)


if __name__ == "__main__":
    main()
