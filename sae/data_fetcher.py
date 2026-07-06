"""
data_fetcher.py — SAE Phase 1
Pulls all raw financial data from yfinance for a given ticker.
Returns a clean, structured dictionary ready for ratio_calculator.py.
"""

import yfinance as yf
import pandas as pd
import numpy as np


class DataFetchError(Exception):
    pass


# ── Helpers ────────────────────────────────────────────────────────────────────

def _row(df: pd.DataFrame, *candidates) -> list:
    """
    Try each candidate row name in order; return oldest-to-newest float list.
    Returns empty list if nothing found.
    """
    for name in candidates:
        if name in df.index:
            series = df.loc[name].dropna()
            if series.empty:
                continue
            # yfinance returns newest-first; reverse to oldest-first
            vals = series.sort_index().values.astype(float)
            return [v for v in vals if not np.isnan(v)]
    return []


def _safe_float(d: dict, *keys, default=None):
    for k in keys:
        v = d.get(k)
        if v is not None and not (isinstance(v, float) and np.isnan(v)):
            try:
                return float(v)
            except (TypeError, ValueError):
                pass
    return default


# ── Main fetch ─────────────────────────────────────────────────────────────────

def fetch(ticker_symbol: str) -> dict:
    """
    Fetch and return all raw financials needed for SAE analysis.
    Raises DataFetchError if data cannot be retrieved.
    """
    sym = ticker_symbol.upper().strip()
    tk = yf.Ticker(sym)

    # ── Company info ───────────────────────────────────────────────────────────
    try:
        info = tk.info or {}
    except Exception:
        info = {}

    name = info.get("shortName") or info.get("longName") or sym
    sector = info.get("sector", "Unknown")
    industry = info.get("industry", "Unknown")
    currency = info.get("currency", "USD")
    market_cap = _safe_float(info, "marketCap", default=0)
    current_price = _safe_float(info, "currentPrice", "regularMarketPrice", default=0)
    beta = _safe_float(info, "beta", default=1.0) or 1.0
    shares = _safe_float(info, "sharesOutstanding", default=0)

    # ── Financial statements ───────────────────────────────────────────────────
    try:
        inc = tk.financials       # income statement, annual
        bal = tk.balance_sheet    # balance sheet, annual
        cf  = tk.cashflow         # cash flow, annual
    except Exception as e:
        raise DataFetchError(f"Cannot fetch statements for {sym}: {e}")

    if inc is None or inc.empty:
        raise DataFetchError(f"Empty income statement for {sym}")
    if bal is None or bal.empty:
        raise DataFetchError(f"Empty balance sheet for {sym}")
    if cf is None or cf.empty:
        raise DataFetchError(f"Empty cash flow for {sym}")

    # Years list (oldest first)
    years = sorted([c.year for c in inc.columns])

    # ── Income statement ───────────────────────────────────────────────────────
    revenue        = _row(inc, "Total Revenue")
    gross_profit   = _row(inc, "Gross Profit")
    operating_inc  = _row(inc, "Operating Income", "EBIT")
    net_income     = _row(inc, "Net Income")
    ebit           = _row(inc, "EBIT", "Operating Income")
    interest_exp   = _row(inc, "Interest Expense")
    income_tax     = _row(inc, "Tax Provision")
    pretax_inc     = _row(inc, "Pretax Income")
    rd_expense     = _row(inc, "Research And Development")
    sga_expense    = _row(inc, "Selling General And Administration",
                              "Selling And Marketing Expense")
    diluted_shares = _row(inc, "Diluted Average Shares")
    da_income      = _row(inc, "Reconciled Depreciation",
                              "Depreciation And Amortization")

    # ── Balance sheet ──────────────────────────────────────────────────────────
    total_assets   = _row(bal, "Total Assets")
    total_liab     = _row(bal, "Total Liabilities Net Minority Interest",
                              "Total Liabilities")
    equity         = _row(bal, "Stockholders Equity",
                              "Total Stockholders Equity")
    total_debt     = _row(bal, "Total Debt")
    if not total_debt:
        lt_debt    = _row(bal, "Long Term Debt")
        curr_debt  = _row(bal, "Current Debt", "Short Long Term Debt")
        if lt_debt and curr_debt:
            n = min(len(lt_debt), len(curr_debt))
            total_debt = [lt_debt[i] + curr_debt[i] for i in range(n)]
        else:
            total_debt = lt_debt or curr_debt or []

    cash           = _row(bal, "Cash And Cash Equivalents",
                              "Cash Cash Equivalents And Short Term Investments")
    receivables    = _row(bal, "Accounts Receivable", "Net Receivables")
    inventory      = _row(bal, "Inventory")
    net_ppe        = _row(bal, "Net PPE")
    goodwill       = _row(bal, "Goodwill")

    # ── Cash flow ──────────────────────────────────────────────────────────────
    operating_cf   = _row(cf, "Operating Cash Flow")
    capex_raw      = _row(cf, "Capital Expenditure")
    capex          = [abs(v) for v in capex_raw]   # yfinance stores as negative
    free_cf        = _row(cf, "Free Cash Flow")
    if not free_cf and operating_cf and capex:
        n = min(len(operating_cf), len(capex))
        free_cf = [operating_cf[i] - capex[i] for i in range(n)]
    sbc            = _row(cf, "Stock Based Compensation")
    da_cf          = _row(cf, "Depreciation Amortization Depletion",
                              "Depreciation And Amortization")

    # Use cash-flow D&A if income statement version is missing
    da = da_income if da_income else da_cf

    # EBITDA = EBIT + D&A
    ebitda = []
    if ebit and da:
        n = min(len(ebit), len(da))
        ebitda = [ebit[i] + da[i] for i in range(n)]

    # Interest expense: yfinance sometimes returns negative; use absolute value
    interest_exp = [abs(v) for v in interest_exp]

    # ── EPS series ─────────────────────────────────────────────────────────────
    eps_series = []
    if net_income and diluted_shares:
        n = min(len(net_income), len(diluted_shares))
        for i in range(n):
            if diluted_shares[i] > 0:
                eps_series.append(net_income[i] / diluted_shares[i])
    if not eps_series:
        trailing_eps = _safe_float(info, "trailingEps")
        if trailing_eps:
            eps_series = [trailing_eps]

    # ── Validate minimum data ──────────────────────────────────────────────────
    if not revenue or len(revenue) < 2:
        raise DataFetchError(f"Insufficient revenue history for {sym} (need ≥2 years)")

    return {
        # Identity
        "ticker":         sym,
        "name":           name,
        "sector":         sector,
        "industry":       industry,
        "currency":       currency,
        "years":          years,
        # Market data
        "market_cap":     market_cap,
        "current_price":  current_price,
        "beta":           beta,
        "shares_outstanding": shares,
        # Income statement
        "revenue":        revenue,
        "gross_profit":   gross_profit,
        "operating_inc":  operating_inc,
        "net_income":     net_income,
        "ebit":           ebit,
        "ebitda":         ebitda,
        "interest_exp":   interest_exp,
        "income_tax":     income_tax,
        "pretax_inc":     pretax_inc,
        "rd_expense":     rd_expense,
        "sga_expense":    sga_expense,
        "diluted_shares": diluted_shares,
        "eps_series":     eps_series,
        "da":             da,
        # Balance sheet
        "total_assets":   total_assets,
        "total_liab":     total_liab,
        "equity":         equity,
        "total_debt":     total_debt,
        "cash":           cash,
        "receivables":    receivables,
        "inventory":      inventory,
        "net_ppe":        net_ppe,
        "goodwill":       goodwill,
        # Cash flow
        "operating_cf":   operating_cf,
        "capex":          capex,
        "free_cf":        free_cf,
        "sbc":            sbc,
    }
