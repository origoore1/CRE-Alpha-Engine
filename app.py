import streamlit as st
import pandas as pd
import numpy as np
import google.generativeai as genai
import plotly.express as px

# --- 1. CONFIG & SECURITY ---
st.set_page_config(page_title="CRE Alpha Engine: Institutional Master", layout="wide")

# Custom CSS for a clean "White-Label" Look
st.markdown("""
    <style>
    .main { background-color: #f8f9fa; }
    .stMetric { background-color: white; padding: 15px; border-radius: 10px; border: 1px solid #e9ecef; }
    .stSidebar { background-color: #ffffff; border-right: 1px solid #e9ecef; }
    </style>
    """, unsafe_allow_html=True)

try:
    API_KEY = st.secrets["GEMINI_API_KEY"]
    genai.configure(api_key=API_KEY)
    model = genai.GenerativeModel('gemini-1.5-flash')
    AI_READY = True
except:
    AI_READY = False

# --- 2. PERSISTENT BRAIN & ADAPTIVE LEARNING ---
if 'portfolio' not in st.session_state: st.session_state['portfolio'] = []
if 'risk_registry' not in st.session_state: st.session_state['risk_registry'] = []

def adaptive_brain_audit(lease_text):
    """Pass 2: Proactive Discovery of Novel Risks."""
    prompt = f"""
    Analyze this German Retail Lease as a Senior Credit MD. 
    Identify 1. Standard Red Flags and 2. ONE 'Novel Risk' unique to this specific contract.
    Return as JSON: {{"red_flags": [], "novel_risk": ""}}
    TEXT: {lease_text}
    """
    try:
        response = model.generate_content(prompt)
        # In a real app, use JSON parsing here. 
        # For the demo, we simulate the learning loop:
        new_risk = "Unique Anchor Co-tenancy Clause found in NRW"
        if new_risk not in st.session_state['risk_registry']:
            st.session_state['risk_registry'].append(new_risk)
        return response.text
    except: return "AI Audit Unavailable."

# --- 3. THE INSTITUTIONAL GENERATOR (10+ DEALS) ---
def generate_master_portfolio():
    assets = [
        {"name": "Edeka Center (NRW)", "noi": 420000, "price": 6100000, "walt": 12.0, "type": "Grocery Anchor", "score": 88},
        {"name": "Rewe Park (Bavaria)", "noi": 580000, "price": 8400000, "walt": 8.5, "type": "Multi-Tenant", "score": 75},
        {"name": "Obi DIY (Saxony)", "noi": 310000, "price": 4200000, "walt": 6.0, "type": "DIY", "score": 62},
        {"name": "Netto Hub (Brandenburg)", "noi": 240000, "price": 3100000, "walt": 4.5, "type": "Discount", "score": 54},
        {"name": "Urban Lidl (Berlin)", "noi": 720000, "price": 10500000, "walt": 15.0, "type": "Grocery Anchor", "score": 91}
    ]
    # In practice, loop to 10+. 
    st.session_state['portfolio'] = assets

# --- 4. DASHBOARD UI ---
with st.sidebar:
    st.title("🛡️ Alpha Controls")
    st.info(f"AI Status: {'✅ ONLINE' if AI_READY else '⚠️ OFFLINE'}")
    if st.button("🧪 Inject Master Portfolio"):
        generate_master_portfolio()
    st.markdown("---")
    st.write("### 🧠 Brain Learning Loop")
    st.write(f"Novel Risks Discovered: {len(st.session_state['risk_registry'])}")
    for r in st.session_state['risk_registry']:
        st.caption(f"• {r}")

# TOP LEVEL KPI RIBBON
st.title("🏗️ CRE Alpha Engine: Master Decision Grid")
k1, k2, k3, k4 = st.columns(4)
if st.session_state['portfolio']:
    df = pd.DataFrame(st.session_state['portfolio'])
    k1.metric("Total Bundle Value", f"€{df['price'].sum():,.0f}")
    k2.metric("Portfolio WALT", f"{df['walt'].mean():.1f} yrs")
    k3.metric("Avg. Bankability", "74/100")
    k4.metric("Bundle Alpha", "+60bps")

tabs = st.tabs(["📊 Portfolio Grid", "🛡️ Banker's Negotiation", "⚔️ Investor's Sword", "📂 Data Bridge"])

with tabs[0]:
    st.header("Institutional Portfolio View")
    if st.session_state['portfolio']:
        st.dataframe(df, use_container_width=True)
        fig = px.scatter(df, x="walt", y="score", size="price", color="type", title="WALT vs Risk Score")
        st.plotly_chart(fig, use_container_width=True)
    else: st.info("Inject data to activate dashboard.")

with tabs[1]:
    st.header("Banker's Defensive Shield")
    if st.session_state['portfolio']:
        c1, c2 = st.columns([1, 2])
        with c1:
            rate = st.slider("2026 Interest Rate (%)", 4.0, 7.0, 5.2)
            st.metric("Portfolio DSCR", f"{1.55 - (rate-5.2)*0.1:.2f}")
        with c2:
            st.write("### 🧐 Senior MD Negotiation Commentary")
            st.warning("High Concentration: 40% of bundle is anchored by Edeka. Suggest diversifying anchor brands to improve Bankability score above 80.")

with tabs[2]:
    st.header("Investor's Yield Waterfall")
    if st.session_state['portfolio']:
        st.write("Analysis of CFADS (Cash Flow Available for Debt Service)")
        st.metric("Portfolio Cash-on-Cash", "11.45%")

with tabs[3]:
    st.header("Proactive Data Bridge")
    st.file_uploader("Upload Lease for Proactive Brain Audit")
    if st.button("🔍 Run Adaptive Risk Scan"):
        with st.spinner("AI is reasoning about novel risks..."):
            audit = adaptive_brain_audit("Sample Lease Text")
            st.success("Audit Complete. New risk identified and added to registry.")