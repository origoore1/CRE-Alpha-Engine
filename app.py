import streamlit as st
import pandas as pd
import numpy as np
import google.generativeai as genai

# --- 1. CONFIG & SECURITY ---
st.set_page_config(page_title="CRE Alpha Engine: Full Stack", layout="wide")

try:
    API_KEY = st.secrets["GEMINI_API_KEY"]
    genai.configure(api_key=API_KEY)
    AI_READY = True
except:
    AI_READY = False

if 'portfolio' not in st.session_state: st.session_state['portfolio'] = []

# --- 2. THE RETAIL SPECIALIST GENERATOR ---
def generate_full_stack_scenarios():
    scenarios = [
        {"name": "Edeka Anchor (NRW)", "noi": 420000, "price": 6100000, "walt": 12.0, "type": "Grocery Anchor", "esg": 85},
        {"name": "Rewe Park (Bavaria)", "noi": 580000, "price": 8400000, "walt": 8.5, "type": "Multi-Tenant", "esg": 65},
        {"name": "Obi DIY Hub (Saxony)", "noi": 310000, "price": 4200000, "walt": 6.0, "type": "Essential Retail", "esg": 45},
        {"name": "Netto Hub (Brandenburg)", "noi": 240000, "price": 3100000, "walt": 4.5, "type": "Discount", "esg": 25},
        {"name": "Urban Grocery (Berlin)", "noi": 720000, "price": 10500000, "walt": 15.0, "type": "Grocery Anchor", "esg": 92}
    ]
    st.session_state['portfolio'] = scenarios

# --- 3. UI LAYOUT ---
st.title("🏗️ CRE Alpha Engine: Institutional Master")
st.markdown("### *Strategy: Buy, Fix, Bundle, Exit*")

with st.sidebar:
    st.header("Platform Controls")
    st.info(f"AI Connection: {'✅ Online' if AI_READY else '⚠️ Offline'}")
    if st.button("🧪 Inject Full Retail Portfolio"):
        generate_full_stack_scenarios()
    st.markdown("---")

tabs = st.tabs(["📂 Data Bridge", "🛡️ Banker Audit", "⚔️ Investor Yield", "💰 Exit Strategy", "🌿 ESG Audit"])

# TAB 1: DATA
with tabs[0]:
    st.header("📂 1. Data Ingestion")
    st.write("Current Portfolio Bundle:")
    if st.session_state['portfolio']:
        st.dataframe(pd.DataFrame(st.session_state['portfolio'])[['name', 'type', 'noi', 'walt']])
    else: st.info("Inject data to begin.")

# TAB 2: BANKER
with tabs[1]:
    st.header("🛡️ 2. Banker's Defensive Shield")
    if st.session_state['portfolio']:
        deal = st.session_state['portfolio'][0]
        st.subheader(f"Underwriting: {deal['name']}")
        rate = st.slider("2026 Interest Rate (%)", 3.0, 8.0, 5.2) / 100
        ltv = st.slider("Target LTV (%)", 50, 80, 65) / 100
        loan = deal['price'] * ltv
        dscr = deal['noi'] / (loan * rate)
        st.metric("DSCR", f"{dscr:.2f}", delta="Safe" if dscr > 1.35 else "Covenant Risk")
    else: st.warning("Inject data first.")

# TAB 3: INVESTOR (RE-ADDED)
with tabs[2]:
    st.header("⚔️ 3. Investor's Yield Sword")
    if st.session_state['portfolio']:
        deal = st.session_state['portfolio'][0]
        equity = deal['price'] * (1 - 0.65) # Simulating 65% LTV
        coc = (deal['noi'] - (deal['price'] * 0.65 * 0.052)) / equity
        st.metric("Cash-on-Cash Yield", f"{coc*100:.2f}%")
        st.write("**Strategy:** This grocery-anchored asset offers a high defensive yield with CPI-linked upside.")
    else: st.warning("No data to calculate return.")

# TAB 4: EXIT
with tabs[3]:
    st.header("💰 4. Portfolio Exit Strategy")
    if st.session_state['portfolio']:
        df = pd.DataFrame(st.session_state['portfolio'])
        m_cap = st.slider("Market Exit Cap (%)", 4.0, 8.0, 6.0) / 100
        bundle_val = df['noi'].sum() / (m_cap - 0.006) # 60bps bundle premium
        st.metric("Total Bundle Exit Value", f"€{bundle_val:,.0f}")
    else: st.info("Add deals to calculate exit.")

# TAB 5: ESG (RE-ADDED)
with tabs[4]:
    st.header("🌿 5. ESG Performance Scorecard")
    if st.session_state['portfolio']:
        df = pd.DataFrame(st.session_state['portfolio'])
        for _, asset in df.iterrows():
            st.write(f"**{asset['name']}**")
            st.progress(asset['esg'] / 100)
            if asset['esg'] < 50: st.error("🚨 Brown Discount Risk: Modernization required for exit.")
    else: st.warning("Inject data to see ESG scoring.")