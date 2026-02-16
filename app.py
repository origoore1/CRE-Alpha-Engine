import streamlit as st
import pandas as pd
import numpy as np

# --- 1. CONFIG & PERSISTENCE ---
st.set_page_config(page_title="CRE Alpha Engine: Institutional Master", layout="wide")
if 'portfolio' not in st.session_state: st.session_state['portfolio'] = []

# --- 2. THE SIMULATED DATA GENERATOR ---
def generate_master_scenarios():
    scenarios = [
        {"name": "Edeka Anchor (NRW)", "noi": 420000, "price": 6100000, "walt": 12.0, "type": "Grocery Anchor", "esg": 85, "capex": 15000},
        {"name": "Rewe Park (Bavaria)", "noi": 580000, "price": 8400000, "walt": 8.5, "type": "Multi-Tenant", "esg": 65, "capex": 45000},
        {"name": "Obi DIY Hub (Saxony)", "noi": 310000, "price": 4200000, "walt": 6.0, "type": "Essential Retail", "esg": 45, "capex": 25000}
    ]
    st.session_state['portfolio'] = scenarios

# --- 3. UI LAYOUT ---
st.title("🏗️ CRE Alpha Engine: Institutional Master")
st.markdown("### *Strategy: Buy, Fix, Bundle, Exit*")

with st.sidebar:
    st.header("Platform Controls")
    if st.button("🧪 Inject Full Retail Portfolio"):
        generate_master_scenarios()

tabs = st.tabs(["📂 Data Bridge", "🛡️ Banker's Shield", "⚔️ Investor's Yield", "💰 Exit Strategy"])

# TAB 1: DATA
with tabs[0]:
    st.header("📂 1. Portfolio Data")
    if st.session_state['portfolio']:
        st.dataframe(pd.DataFrame(st.session_state['portfolio']))
    else: st.info("Inject data in sidebar.")

# TAB 2: BANKER'S DEFENSIVE SHIELD (EXPANDED)
with tabs[1]:
    st.header("🛡️ 2. Banker's Underwriting Lens")
    if st.session_state['portfolio']:
        deal = st.session_state['portfolio'][0]
        st.subheader(f"Asset: {deal['name']}")
        
        c1, c2, c3 = st.columns(3)
        with c1:
            ltv = st.slider("LTV (%)", 50, 80, 65) / 100
            rate = st.slider("Interest Rate (%)", 3.0, 7.0, 5.2) / 100
            indexation = st.slider("CPI Indexation (%)", 0, 100, 100) / 100
        
        loan = deal['price'] * ltv
        debt_service = loan * rate
        dscr = deal['noi'] / debt_service
        debt_yield = (deal['noi'] / loan) * 100
        
        with c2:
            st.metric("DSCR (Target > 1.35x)", f"{dscr:.2f}")
            st.metric("Debt Yield (Target > 9%)", f"{debt_yield:.2f}%")
        
        with c3:
            st.write("**Proactive Banker Risk Flags:**")
            if dscr < 1.35: st.error("🚨 Covenant Breach: DSCR < 1.35x (Non-Recourse Limit)")
            if debt_yield < 9.0: st.warning("⚠️ Low Debt Yield: Bank may reduce leverage.")
        
        # Stress Test Matrix
        st.subheader("📊 Interest Rate Sensitivity Matrix (DSCR)")
        rates = [0.04, 0.05, 0.052, 0.06, 0.07]
        matrix = {f"{r*100:.1f}% Rate": [deal['noi'] / (loan * r)] for r in rates}
        st.table(pd.DataFrame(matrix, index=["DSCR Result"]))
    else: st.warning("Inject data first.")

# TAB 3: INVESTOR'S YIELD SWORD (EXPANDED)
with tabs[2]:
    st.header("⚔️ 3. Investor's Yield & FFO Waterfall")
    if st.session_state['portfolio']:
        deal = st.session_state['portfolio'][0]
        equity = deal['price'] * (1 - 0.65) # 65% LTV assumed
        loan_int = (deal['price'] * 0.65) * 0.052
        
        # FFO Calculation
        ffo = deal['noi'] - loan_int
        affo = ffo - deal['capex'] # Subtract recurring capex
        coc = affo / equity
        
        c1, c2 = st.columns(2)
        with c1:
            st.write("### FFO Waterfall")
            st.write(f"**Annual NOI:** €{deal['noi']:,}")
            st.write(f"**- Interest Expense:** (€{loan_int:,.0f})")
            st.write(f"**= FFO (Funds from Ops):** €{ffo:,.0f}")
            st.write(f"**- Recurring Capex:** (€{deal['capex']:,})")
            st.write(f"**= AFFO (Adjusted FFO):** €{affo:,.0f}")
        
        with c2:
            st.write("### Investor Return Metrics")
            st.metric("Cash-on-Cash (AFFO Yield)", f"{coc*100:.2f}%")
            st.metric("Equity Multiple (Simulated 5yr Exit)", "1.85x")
            st.info("Strategy: Buy, improve efficiency, and bundle for institutional premium exit.")
    else: st.warning("Inject data first.")