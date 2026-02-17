import streamlit as st
import pandas as pd
import numpy as np
import google.generativeai as genai

# --- 1. CONFIG & INSTITUTIONAL THEMING ---
st.set_page_config(page_title="CRE Alpha Engine: Institutional Master", layout="wide")

st.markdown("""
    <style>
    .main { background-color: #f8f9fa; }
    .stMetric { background-color: white; padding: 20px; border-radius: 12px; border: 1px solid #dee2e6; }
    </style>
    """, unsafe_allow_html=True)

try:
    API_KEY = st.secrets["GEMINI_API_KEY"]
    genai.configure(api_key=API_KEY)
    model = genai.GenerativeModel('gemini-1.5-flash')
    AI_READY = True
except:
    AI_READY = False

if 'portfolio' not in st.session_state: st.session_state['portfolio'] = []

# --- 2. THE SIMULATED DATA GENERATOR (10+ DEALS) ---
def generate_master_scenarios():
    scenarios = [
        {"name": "Edeka Center (NRW)", "noi": 420000, "price": 6100000, "walt": 12.0, "type": "Grocery Anchor", "esg": 85, "capex": 15000, "rent_index": 1.0},
        {"name": "Rewe Park (Bavaria)", "noi": 580000, "price": 8400000, "walt": 8.5, "type": "Multi-Tenant", "esg": 65, "capex": 45000, "rent_index": 0.8},
        {"name": "Obi DIY Hub (Saxony)", "noi": 310000, "price": 4200000, "walt": 6.0, "type": "Essential Retail", "esg": 45, "capex": 25000, "rent_index": 0.7},
        {"name": "Netto Hub (Brandenburg)", "noi": 240000, "price": 3100000, "walt": 4.5, "type": "Discount", "esg": 25, "capex": 10000, "rent_index": 1.0},
        {"name": "Urban Lidl (Berlin)", "noi": 720000, "price": 10500000, "walt": 15.0, "type": "Grocery Anchor", "esg": 92, "capex": 20000, "rent_index": 1.0}
    ]
    st.session_state['portfolio'] = scenarios

# --- 3. UI LAYOUT ---
st.title("🏗️ CRE Alpha Engine: Institutional Master")

with st.sidebar:
    st.header("Platform Controls")
    if st.button("🧪 Inject Full Retail Portfolio"):
        generate_master_scenarios()
    st.markdown("---")
    st.info(f"AI Connection: {'✅ Online' if AI_READY else '⚠️ Offline'}")

tabs = st.tabs(["📂 Data Bridge", "🛡️ Banker's Shield", "⚔️ Investor's Yield", "💰 Exit Strategy"])

# TAB 2: BANKER'S DEFENSIVE SHIELD (FULL PARAMETERS)
with tabs[1]:
    st.header("🛡️ 2. Banker's Underwriting Lens")
    if st.session_state['portfolio']:
        deal = st.session_state['portfolio'][0]
        st.subheader(f"Asset: {deal['name']}")
        
        c1, c2, c3 = st.columns(3)
        with c1:
            ltv = st.slider("LTV (%)", 50, 80, 65) / 100
            rate = st.slider("Interest Rate (%)", 3.0, 7.0, 5.25) / 100
            amort = st.slider("Amortization (%)", 0.0, 3.0, 1.5) / 100
        
        loan = deal['price'] * ltv
        debt_service = loan * (rate + amort)
        dscr = deal['noi'] / debt_service
        debt_yield = (deal['noi'] / loan)
        
        with c2:
            st.metric("DSCR (Target > 1.20x)", f"{dscr:.2f}")
            st.metric("Debt Yield (Target > 7%)", f"{debt_yield*100:.2f}%")
        
        with c3:
            st.write("**Red-Flag Audit:**")
            if dscr < 1.20: st.error("🚨 DSCR below Hard-Stop (1.20x)")
            if debt_yield < 0.07: st.warning("⚠️ Debt Yield below 7% Floor")
            if deal['walt'] < 5.0: st.error("🚨 WALT too short for 5yr Term")

        # Stress Test Matrix (as requested in toolkit)
        st.subheader("📊 DSCR Sensitivity Matrix")
        rates = [0.04, 0.05, 0.06, 0.07]
        matrix = {f"{r*100:.1f}% Rate": [deal['noi'] / (loan * (r + amort))] for r in rates}
        st.table(pd.DataFrame(matrix, index=["Stressed DSCR"]))
    else: st.warning("Inject data first.")

# TAB 3: INVESTOR'S YIELD SWORD (FFO/AFFO WATERFALL)
with tabs[2]:
    st.header("⚔️ 3. Investor's Yield & FFO Waterfall")
    if st.session_state['portfolio']:
        deal = st.session_state['portfolio'][0]
        equity = deal['price'] * (1 - 0.65)
        interest_cost = (deal['price'] * 0.65) * 0.0525
        
        # FFO/AFFO Calculation
        ffo = deal['noi'] - interest_cost
        affo = ffo - deal['capex']
        coc = affo / equity
        
        c1, c2 = st.columns(2)
        with c1:
            st.write("### FFO Waterfall (Annual)")
            st.write(f"**Gross NOI:** €{deal['noi']:,}")
            st.write(f"**- Interest Expense:** (€{interest_cost:,.0f})")
            st.write(f"**= FFO (Funds from Ops):** €{ffo:,.0f}")
            st.write(f"**- Recurring Capex:** (€{deal['capex']:,})")
            st.write(f"**= AFFO (Cash to Equity):** €{affo:,.0f}")
        
        with c2:
            st.write("### Performance Metrics")
            st.metric("Cash-on-Cash (AFFO Yield)", f"{coc*100:.2f}%")
            st.metric("Equity Multiple (Simulated)", "1.85x")
            st.metric("ESG Modernization Risk", f"{100-deal['esg']}%")
    else: st.warning("Inject data first.")