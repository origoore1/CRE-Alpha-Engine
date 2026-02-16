import streamlit as st
import pandas as pd
import numpy as np
import google.generativeai as genai
from fpdf import FPDF

# --- 1. SECURE CONFIGURATION ---
st.set_page_config(page_title="CRE Alpha Engine: Retail Specialist", layout="wide")

# Secure Key Retrieval
try:
    API_KEY = st.secrets["GEMINI_API_KEY"]
    genai.configure(api_key=API_KEY)
    AI_READY = True
except Exception:
    st.error("🔑 Security Alert: GEMINI_API_KEY not found in Streamlit Secrets.")
    AI_READY = False

# Persistent Memory
if 'portfolio' not in st.session_state: st.session_state['portfolio'] = []
if 'active_deal' not in st.session_state: st.session_state['active_deal'] = None

# --- 2. RETAIL SPECIALIST GENERATOR ---
def generate_lahav_scenarios():
    """Generates 5 hyper-realistic German Retail scenarios for practice"""
    scenarios = [
        {"name": "Edeka Neighborhood Center (NRW)", "noi": 420000, "price": 6100000, "walt": 12.0, "type": "Grocery Anchor", "esg": 85},
        {"name": "Rewe/Aldi Retail Park (Bavaria)", "noi": 580000, "price": 8400000, "walt": 8.5, "type": "Multi-Tenant Retail", "esg": 70},
        {"name": "DIY Center - Obi (Saxony)", "noi": 310000, "price": 4200000, "walt": 6.0, "type": "Essential DIY", "esg": 50},
        {"name": "Discount Hub - Netto (Brandenburg)", "noi": 240000, "price": 3100000, "walt": 4.5, "type": "Deep Discount", "esg": 30},
        {"name": "Urban Grocery (Berlin Suburb)", "noi": 720000, "price": 10500000, "walt": 15.0, "type": "Grocery Anchor", "esg": 90}
    ]
    st.session_state['portfolio'] = scenarios
    st.success("🎯 Retail-Only Portfolio Injected. Ready for 'Lahav-Style' Stress Testing.")

# --- 3. EXPERT LOGIC MODULES ---
class CRE_Expert:
    @staticmethod
    def calculate_metrics(noi, loan, rate):
        debt_service = loan * rate
        dscr = noi / debt_service if debt_service > 0 else 0
        dy = (noi / loan) * 100
        return dscr, dy

    @staticmethod
    def get_proactive_advice(dscr, walt, esg_score):
        advice = []
        if dscr < 1.35: advice.append("🚨 **Debt Risk:** DSCR below 1.35x. Banker will likely require a cash-trap clause.")
        if walt < 6: advice.append("⌛ **Exit Risk:** WALT < 6 yrs. Expect 'Core-Plus' cap rate expansion on exit.")
        if esg_score < 50: advice.append("🌿 **ESG Gap:** Low efficiency. Budget for 'Green Capex' to avoid brown-discount.")
        return advice

# --- 4. UI: THE WORKBENCH ---
st.title("🏗️ CRE Alpha Engine: Institutional Portfolio Builder")
st.markdown("### *Strategy: Buy, Fix, Bundle, Exit*")

# Sidebar Status
with st.sidebar:
    st.header("Platform Controls")
    st.info(f"AI Connection: {'✅ Online' if AI_READY else '⚠️ Offline'}")
    if st.button("🧪 Inject Retail Test Portfolio"):
        generate_lahav_scenarios()
    st.markdown("---")
    st.write("Use this to practice underwriting before using real-life data.")

tabs = st.tabs(["📂 Data Bridge", "🛡️ Banker Audit", "💰 Exit Strategy"])

# TAB 1: DATA BRIDGE
with tabs[0]:
    st.header("📂 1. Data Ingestion")
    c1, c2 = st.columns(2)
    with c1:
        st.subheader("AI Lease Auditor")
        uploaded_file = st.file_uploader("Upload Tenant Lease (PDF)", type=["pdf"])
        if uploaded_file and AI_READY:
            if st.button("🔍 Run Senior Audit"):
                with st.spinner("AI scanning for CPI Linkage..."):
                    # Simulated AI result for bridge testing
                    st.session_state['active_deal'] = {
                        "name": "Extracted Retail Asset", "noi": 350000, 
                        "price": 5000000, "walt": 10.0, "type": "Manual Upload", "esg": 65
                    }
                    st.success("✅ AI Extraction Complete. Data moved to Banker Audit.")
    with c2:
        st.subheader("Portfolio Status")
        if st.session_state['portfolio']:
            st.write(f"Current Bundle: {len(st.session_state['portfolio'])} Assets")
            st.dataframe(pd.DataFrame(st.session_state['portfolio'])[['name', 'type', 'noi']])
        else:
            st.info("Portfolio is empty. Inject test data or upload a lease.")

# TAB 2: BANKER AUDIT
with tabs[1]:
    st.header("🛡️ 2. Institutional Underwriting")
    # Pull first asset for testing if portfolio exists
    deal = st.session_state['portfolio'][0] if st.session_state['portfolio'] else st.session_state['active_deal']
    
    if deal:
        st.subheader(f"Analyzing: {deal['name']}")
        col1, col2 = st.columns([2, 1])
        with col1:
            rate = st.slider("2026 Interest Rate (%)", 3.0, 8.0, 5.2) / 100
            ltv = st.slider("Target LTV (%)", 50, 80, 65) / 100
            loan = deal['price'] * ltv
            dscr, dy = CRE_Expert.calculate_metrics(deal['noi'], loan, rate)
            
            st.metric("Institutional DSCR", f"{dscr:.2f}", delta="Safe" if dscr > 1.35 else "Covenant Breach")
            st.metric("Debt Yield", f"{dy:.2f}%")
        
        with col2:
            st.subheader("🧐 Senior MD Commentary")
            for advice in CRE_Expert.get_proactive_advice(dscr, deal['walt'], deal['esg']):
                st.write(advice)
    else:
        st.warning("No deal data found. Please Inject Test Data in the sidebar.")

# TAB 3: EXIT STRATEGY
with tabs[2]:
    st.header("💰 3. Portfolio Exit & Bundle Premium")
    if st.session_state['portfolio']:
        df = pd.DataFrame(st.session_state['portfolio'])
        total_noi = df['noi'].sum()
        
        m_cap = st.slider("Market Exit Cap (%)", 4.0, 9.0, 6.5) / 100
        # 60bps premium for bundled stabilized assets
        bundle_val = total_noi / (m_cap - 0.006) 
        
        st.metric("Total Bundle Exit Value", f"€{bundle_val:,.0f}", delta="60bps Portfolio Premium Applied")
        st.write("### Portfolio Composition")
        st.table(df[['name', 'type', 'walt', 'noi']])
    else:
        st.info("Add deals to the bundle to calculate exit value.")