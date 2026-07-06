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
import traceback

import numpy as np
import numpy_financial as npf
import pandas as pd
import streamlit as st

import rent_roll_parser as rrp
from dd_scanner import extract_lease_terms, run_dd_scan

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
        if ltv_refi <= 0.65:
            status = "PASS"
        elif ltv_refi <= 0.70:
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
        max_p_dscr = noi / (1.30 * orig_ltv * (interest + amort))
        max_p_icr  = noi / (1.75 * orig_ltv * interest)
        max_p_giy  = gross_rent / 0.055 if gross_rent > 0 else asking
        max_p_dy   = noi / (0.075 * orig_ltv)
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
    if ltv > 0.65 and asking > 0:
        req_loan    = asking * 0.63
        extra_eq    = loan - req_loan
        new_eq      = equity + extra_eq
        new_ds_eq   = req_loan * (interest + amort)
        fixes.append({
            'lever': 'ADDITIONAL EQUITY', 'category': 'equity', 'icon': '📥', 'priority': 2,
            'current':  f'EUR {equity:,.0f}  (LTV {ltv:.1%})',
            'required': f'EUR {new_eq:,.0f}  (LTV 63.0%)',
            'delta':    f'+EUR {extra_eq:,.0f} equity injection',
            'binding':  'LTV',
            'action': (
                f'Inject EUR {extra_eq:,.0f} additional equity to reduce LTV from '
                f'{ltv:.1%} to 63.0%. Alternatively, negotiate a lower loan amount '
                f'with the bank. This also improves DSCR to {noi/new_ds_eq:.2f}x.'
            ),
            'after': {
                'LTV':        '63.0%',
                'New Loan':   f'EUR {req_loan:,.0f}',
                'DSCR':       f'{noi/new_ds_eq:.2f}x',
                'Debt Yield': f'{noi/req_loan:.2%}',
            },
        })

    # ── FIX 3: Extended IO Period ────────────────────────────────────────────
    dscr = metrics.get('dscr', 0) or 0
    if dscr < 1.30 and loan > 0 and annual_int > 0:
        io_dscr = noi / annual_int
        if io_dscr >= 1.30:
            fixes.append({
                'lever': 'EXTENDED IO PERIOD', 'category': 'bank', 'icon': '🏦', 'priority': 3,
                'current':  f'DSCR {dscr:.2f}x  (2yr IO, then amortising)',
                'required': f'DSCR {io_dscr:.2f}x  achievable on full IO',
                'delta':    f'Request 3–5yr IO instead of standard 2yr',
                'binding':  'DSCR',
                'action': (
                    f'Negotiate with the bank: extend IO period to 3–5 years. '
                    f'DSCR improves from {dscr:.2f}x to {io_dscr:.2f}x during the '
                    f'IO period. Business plan argument: "We will re-let vacant units '
                    f'in years 1–3, increasing NOI before amortisation begins. '
                    f'We need IO headroom to execute the value-add plan."'
                ),
                'after': {
                    'DSCR (IO)':    f'{io_dscr:.2f}x',
                    'Annual DS':    f'EUR {annual_int:,.0f}  (interest only)',
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
     "NOI divided by the interest-only component of debt service. "
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
     "German tax depreciation. Standard: 2% linear per year on building value "
     "(purchase price minus land value). Reduces taxable income materially.",
     "2.0% standard"),
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
                'action': 'Request year of construction and Energy Performance Certificate (Energieausweis).'}
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

        st.caption("DEBT STRUCTURE")
        loan_type = st.radio(
            "Loan Amortisation Type",
            ["Annuity (Annuitaetendarlehen)", "Linear (Lineare Tilgung)"],
            index=0,
            help="Annuity = fixed total payment. Linear = fixed principal, declining total.",
        )
        loan_type_key = "Annuity" if "Annuity" in loan_type else "Linear"

        io_years = st.slider(
            "Interest-Only (IO) Period (years)", 0, 5, 2,
            help="Years before amortisation begins."
        )

        st.caption("DEAL STRUCTURE")
        share_deal = st.toggle(
            "Share Deal (Anteilskauf)",
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

    if not uploaded_file:
        st.info("Upload your populated `DE_Retail_Underwriter_Template.xlsx` to begin.")
        st.markdown("""
        **This tool calculates:**  DSCR · ICR · LTV · GIY · NIY · WAULT ·
        Anchor concentration · 10-year levered IRR · Exit cap sensitivity heatmap ·
        Itemised NOI from Cost Sheet · Pass / Investigate / Decline verdict
        """)
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
        metrics = rrp.compute_metrics(deal, rr, costs, noi_margin_slider)
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
    y1_amort    = 0.0 if io_years >= 1 else loan * amrt_rt
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
    # TABS
    # ------------------------------------------------------------------
    tab1, tab2, tab_vc, tab3, tab4, tab5, tab_dd = st.tabs([
        "Deal Pack", "Banker Lens", "Value Creation ✨", "Investor Lens", "Glossary", "Export", "🔍 DD Scanner"
    ])

    # ------------------------------------------------------------------ TAB 1
    with tab1:
        prop = deal.get("property_name") or "Unnamed Asset"
        st.subheader(f"{prop} -- Deal Pack")
        st.markdown(noi_badge, unsafe_allow_html=True)
        st.markdown("")

        k1,k2,k3,k4,k5,k6 = st.columns(6)
        k1.metric("Asking Price",    f"EUR {metrics['asking_price']:,.0f}")
        _deal_type_label = "Total Acq. Cost (Share)" if share_deal else "Total Acq. Cost"
        _saving_delta    = f"Saves EUR {_grest_saving:,.0f} GrESt" if share_deal and _grest_saving else None
        k2.metric(_deal_type_label, f"EUR {metrics['total_acq_cost']:,.0f}",
                  delta=_saving_delta, delta_color="normal" if share_deal else "off")
        k3.metric("Equity Required", f"EUR {metrics['equity']:,.0f}")
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
            if "Annual Rent (EUR)" in disp_df.columns:
                disp_df["Annual Rent (EUR)"] = disp_df["Annual Rent (EUR)"].apply(
                    lambda x: f"EUR {x:,.0f}" if pd.notna(x) else "--"
                )
            if "Unexpired Term (Yrs)" in disp_df.columns:
                disp_df["Unexpired Term (Yrs)"] = disp_df["Unexpired Term (Yrs)"].apply(
                    lambda x: f"{x:.1f}" if pd.notna(x) and float(x) > 0 else "VACANT"
                )
            st.dataframe(disp_df, use_container_width=True, hide_index=True)

        with col_right:
            st.markdown("#### NOI Bridge")
            if costs and costs.get("available") and costs.get("costs"):
                label_map = {
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
                st.markdown(
                    f'<div class="cost-line">'
                    f'<span class="cost-label">Gross Passing Rent</span>'
                    f'<span style="color:#059669;font-weight:600;">'
                    f'EUR {gross_rent:,.0f}</span></div>',
                    unsafe_allow_html=True,
                )
                for k, v in costs["costs"].items():
                    if v != 0:
                        lbl = label_map.get(k, k)
                        void_flag = "(void)" if "void" in k else ""
                        st.markdown(
                            f'<div class="cost-line">'
                            f'<span class="cost-label">&nbsp;&nbsp;{lbl} {void_flag}</span>'
                            f'<span class="cost-value">-EUR {v:,.0f}</span></div>',
                            unsafe_allow_html=True,
                        )
                st.markdown(
                    f'<div class="noi-total">'
                    f'<span>NET OPERATING INCOME</span>'
                    f'<span>EUR {noi:,.0f}</span></div>',
                    unsafe_allow_html=True,
                )
                st.caption(f"Void costs: EUR {metrics['void_costs']:,.0f} / yr "
                           f"({metrics['total_vacant_area']:,.0f} m2 vacant)")
            else:
                st.info(f"Populate the Cost Sheet tab for itemised NOI.\n\n"
                        f"Current: {noi_margin:.0%} margin applied = EUR {noi:,.0f}")

    # ------------------------------------------------------------------ TAB 2
    with tab2:
        st.subheader("Covenant Audit -- Banker Lens")
        st.markdown(noi_badge, unsafe_allow_html=True)
        st.markdown("")

        b1,b2,b3,b4,b5,b6 = st.columns(6)
        dscr_lbl = f"Yr 1 DSCR ({'IO' if io_years >= 1 else 'Amort'})"
        b1.metric("LTV",
                  f"{metrics['ltv']:.1%}",
                  delta=f"{'✅' if metrics['ltv']<=0.65 else '⚠️'} max 65%  |  limit 70%",
                  delta_color="normal" if metrics["ltv"]<=0.65 else "inverse",
                  help="Bank max: 65%. Above 65% → mezzanine required. Above 70% → most banks decline.")
        b2.metric(dscr_lbl,
                  f"{y1_dscr:.2f}x" if y1_dscr else "--",
                  delta=f"{'✅' if y1_dscr and y1_dscr>=1.30 else '⚠️'} min 1.30x  |  floor 1.20x",
                  delta_color="normal" if y1_dscr and y1_dscr>=1.30 else "inverse",
                  help="pbb/Berlin Hyp/Helaba covenant min: 1.30x. Below 1.20x = credit decline. IO period shows interest-only DSCR.")
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

        # AfA (tax depreciation) information block
        if _afa and _afa > 0:
            _build_val = metrics.get("afa_building_value", 0)
            st.info(
                f"**AfA (Absetzung fur Abnutzung):** Annual tax depreciation "
                f"EUR {_afa:,.0f} (2% of building value EUR {_build_val:,.0f} = "
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

    with tab3:
        st.subheader("Investment Committee Verdict -- Investor Lens")
        st.markdown(noi_badge, unsafe_allow_html=True)
        st.markdown("")

        v_class = {"PASS":"verdict-pass","INVESTIGATE":"verdict-inv","DECLINE":"verdict-dec"}.get(
            verdict["recommendation"],"verdict-inv")
        v_icon  = {"PASS":"OK","INVESTIGATE":"CAUTION","DECLINE":"DECLINE"}.get(
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
            st.caption(f"Base: {base_exit_cap:.1%} exit cap  |  {cpi_indexation:.1%} indexation  |  {io_years}yr IO")

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

        if rent_roll is None or len(rent_roll) == 0:
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
                    _scan = run_dd_scan(_pdf_dict, rent_roll)

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
                            _e3.metric("CPI Pass-thru", f"{_ext.get('cpi_passthrough',0):.0%}" if _ext.get('cpi_passthrough') else "—")
                            # Confidence
                            _conf = _ext.get('confidence',{})
                            if _conf:
                                _low_conf = [k for k,v in _conf.items() if v not in ('HIGH','CALCULATED')]
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

                st.divider()
                st.caption(
                    "Scanner confidence varies by PDF quality and lease format. "
                    "Always verify RED flags with the original document. "
                    "Fields not extracted (shown as —) require manual review."
                )
            else:
                st.info("Upload PDF lease agreements above to begin scanning.")


if __name__ == "__main__":
    main()
