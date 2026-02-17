import streamlit as st
import pandas as pd
import numpy as np
import google.generativeai as genai
import plotly.express as px

# --- 1. CONFIG & INSTITUTIONAL THEME ---
st.set_page_config(page_title="CRE Alpha Engine: Full Stack", layout="wide")

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

# --- 2. THE MASTER DATA GENERATOR (FULL PARAMETERS) ---
def generate_institutional_portfolio():
    # Incorporating parameters from your toolkit: NOI, Capex, Opex, EPC
    assets = [
        {
            "name": "Edeka Center (NRW)", 
            "price": 6100000, 
            "noi": 420000, 
            "opex": 45000, 
            "capex": 15000,
            "interest_rate": 0.0525,
            "amortization": 0.015,
            "ltv": 0.65,
            "walt": 12.0,
            "epc": 2, # EPC Grade A-G
            "type": "Grocery Anchor"
        },
        {
            "name": "Rewe Retail Park (Bavaria)", 
            "price": 8400000, 
            "noi": 580000, 
            "opex": 62000, 
            "capex": 35000,
            "interest_rate": 0.0525,
            "amortization": 0.015,
            "ltv": 0.60,
            "walt": 8.5,
            "epc": 4,
            "type": "Multi-Tenant"
        }
    ]
    st.session_state['portfolio'] = assets

# --- 3. UI LAYOUT ---
st.title("🏗️ CRE Alpha Engine: Institutional Workstation")

with st.sidebar:
    st.header("Platform Controls")
    if st.button("🧪 Inject Full-Stack Portfolio"):
        generate_institutional_portfolio()
    st.markdown("---")
    st.info(f"AI System: {'✅ Active' if AI_READY else '⚠️ Offline'}")

tabs = st.tabs(["📂 Portfolio View", "🛡️ Banker (Credit)", "⚔️ Investor (Equity)", "📂 Red-Flag Audit"])

# TAB 1: PORTFOLIO GRID
with tabs[0]:
    if st.session_state['portfolio']:
        df = pd.DataFrame(st.session_state['portfolio'])
        st.write("### Portfolio Composition")
        st.dataframe(df[['name', 'type', 'noi', 'price', 'walt']])
        
        # High-level metrics based on toolkit
        c1, c2, c3 = st.columns(3)
        c1.metric("Total Bundle Price", f"€{df['price'].sum():,.0f}")
        c2.metric("Total Portfolio NOI", f"€{df['noi'].sum():,.0f}")
        c3.metric("Weighted WALT", f"{df['walt'].mean():.1f} yrs")
    else: st.info("Inject data to begin.")

# TAB 2: BANKER LENS (CREDIT DECISION)
with tabs[1]:
    st.header("🛡️ Banker's Credit Framework")
    if st.session_state['portfolio']:
        deal = st.session_state['portfolio'][0]
        st.subheader(f"Analyzing: {deal['name']}")
        
        # Banker-specific sliders from toolkit
        col_l, col_r = st.columns(2)
        with col_l:
            rate = st.slider("Interest Rate (%)", 3.0, 7.5, deal['interest_rate']*100) / 100
            amort = st.slider("Amortization (%)", 0.0, 3.0, deal['amortization']*100) / 100
            ltv = st.slider("LTV (%)", 50, 80, int(deal['ltv']*100)) / 100
            
        loan = deal['price'] * ltv
        debt_service = loan * (rate + amort)
        dscr = deal['noi'] / debt_service
        debt_yield = deal['noi'] / loan
        
        with col_r:
            st.metric("DSCR (Target > 1.20x)", f"{dscr:.2f}", delta="Safe" if dscr > 1.20 else "Risk")
            st.metric("Debt Yield (Target > 7%)", f"{debt_yield*100:.2f}%")
            st.metric("Annual Debt Service", f"€{debt_service:,.0f}")

# TAB 3: INVESTOR LENS (FFO/AFFO WATERFALL)
with tabs[2]:
    st.header("⚔️ Investor's Yield Sword")
    if st.session_state['portfolio']:
        deal = st.session_state['portfolio'][0]
        equity = deal['price'] * (1 - deal['ltv'])
        interest_exp = (deal['price'] * deal['ltv']) * deal['interest_rate']
        
        # FFO Calculation
        ffo = deal['noi'] - interest_exp
        affo = ffo - deal['capex'] # Subtracting recurring capex
        coc = affo / equity
        
        col_w1, col_w2 = st.columns(2)
        with col_l:
            st.write("### FFO Waterfall")
            st.write(f"**Gross NOI:** €{deal['noi']:,}")
            st.write(f"**- Interest Expense:** (€{interest_exp:,.0f})")
            st.write(f"**= FFO (Funds from Ops):** €{ffo:,.0f}")
            st.write(f"**- Recurring Capex:** (€{deal['capex']:,})")
            st.write(f"**= AFFO (Cash to Equity):** €{affo:,.0f}")
        
        with col_r:
            st.write("### Yield Metrics")
            st.metric("Cash-on-Cash (AFFO Yield)", f"{coc*100:.2f}%")
            st.metric("EPC/ESG Risk Grade", deal['epc'])
    else: st.warning("Inject data first.")

# TAB 4: RED-FLAG AUDIT
with tabs[3]:
    st.header("🚨 Institutional Red-Flag Audit")
    if st.session_state['portfolio']:
        deal = st.session_state['portfolio'][0]
        # Red Flag Logic from your Toolkit
        flags = []
        if deal['noi'] / (deal['price'] * deal['ltv'] * deal['interest_rate']) < 1.20:
            flags.append("🚨 DSCR below Hard-Stop (1.20x)")
        if deal['ltv'] > 0.65:
            flags.append("🚨 LTV above Bank Threshold (65%)")
        if deal['epc'] > 5:
            flags.append("🚨 Environmental/EPC Risk too high")
        
        if flags:
            for f in flags: st.error(f)
        else:
            st.success("✅ Deal cleared all primary Red-Flag checks.")
