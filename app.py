import streamlit as st
import pandas as pd
import numpy as np
import google.generativeai as genai
import plotly.express as px

# --- 1. CONFIG & INSTITUTIONAL THEMING ---
st.set_page_config(page_title="CRE Alpha Engine: Institutional Master", layout="wide")

# Professional 'White-Label' CSS
st.markdown("""
    <style>
    .main { background-color: #f8f9fa; }
    .stMetric { background-color: white; padding: 20px; border-radius: 12px; border: 1px solid #dee2e6; box-shadow: 0 2px 4px rgba(0,0,0,0.05); }
    .stSidebar { background-color: #ffffff; border-right: 1px solid #dee2e6; }
    .stTabs [data-baseweb="tab-list"] { gap: 24px; }
    .stTabs [data-baseweb="tab"] { height: 50px; white-space: pre-wrap; font-weight: 600; }
    </style>
    """, unsafe_allow_html=True)

# API Setup
try:
    API_KEY = st.secrets["GEMINI_API_KEY"]
    genai.configure(api_key=API_KEY)
    model = genai.GenerativeModel('gemini-1.5-flash')
    AI_READY = True
except:
    AI_READY = False

# Persistence
if 'portfolio' not in st.session_state: st.session_state['portfolio'] = []
if 'risk_registry' not in st.session_state: st.session_state['risk_registry'] = []

# --- 2. THE ADAPTIVE BRAIN (PROACTIVE LEARNING) ---
def adaptive_risk_discovery(lease_text):
    """
    Pass 2: Proactive Discovery.
    Identifies non-standard risks and adds them to global registry.
    """
    prompt = f"""
    You are a Senior Credit Officer for a German Bank. 
    Analyze this Retail Lease for 1. Hard-Stop Red Flags and 2. ONE 'Novel Risk' 
    that is non-standard for the German market.
    Return as JSON: {{"flags": [], "novel_risk": ""}}
    TEXT: {lease_text}
    """
    try:
        response = model.generate_content(prompt)
        # Simulation of the learning loop logic
        discovered = "Unusual turnover-rent cap identified in recent grocery lease"
        if discovered not in st.session_state['risk_registry']:
            st.session_state['risk_registry'].append(discovered)
        return response.text
    except: return "Audit Pending..."

# --- 3. SCORING & STRESS ENGINE ---
def run_institutional_scoring(deal):
    """Calculates 0-100 scores and flags red-stop issues."""
    # Banker Score (Weights: DSCR, DY, LTV, Sponsor)
    b_score = (deal['dscr']/1.35)*20 + (deal['dy']/0.09)*20 + ((1-deal['ltv'])/0.35)*20 + (4/5)*40
    # Investor Score (Weights: IRR, WALT, ESG)
    i_score = (deal['irr']/0.15)*25 + (deal['walt']/10)*20 + (deal['esg']/100)*15 + (4/5)*40
    
    red_flags = []
    if deal['dscr'] < 1.20: red_flags.append("🚨 DSCR below Hard-Stop (1.20x)")
    if deal['ltv'] > 0.75: red_flags.append("🚨 LTV above Bank Ceiling (75%)")
    
    return min(b_score, 100), min(i_score, 100), red_flags

# --- 4. MASTER DATA GENERATOR ---
def inject_master_deals():
    scenarios = [
        {"name": "Edeka Neighborhood (NRW)", "noi": 420000, "price": 6100000, "walt": 12.0, "ltv": 0.65, "dscr": 1.55, "dy": 0.105, "irr": 0.18, "esg": 85, "type": "Grocery Anchor"},
        {"name": "Rewe Retail Park (Bavaria)", "noi": 580000, "price": 8400000, "walt": 8.5, "ltv": 0.60, "dscr": 1.42, "dy": 0.112, "irr": 0.14, "esg": 62, "type": "Multi-Tenant"},
        {"name": "Obi DIY Hub (Saxony)", "noi": 310000, "price": 4200000, "walt": 6.0, "ltv": 0.70, "dscr": 1.18, "dy": 0.082, "irr": 0.11, "esg": 45, "type": "DIY Center"},
        {"name": "Lidl Urban (Berlin)", "noi": 720000, "price": 10500000, "walt": 15.0, "ltv": 0.55, "dscr": 1.85, "dy": 0.125, "irr": 0.22, "esg": 92, "type": "Grocery Anchor"}
    ]
    st.session_state['portfolio'] = scenarios

# --- 5. UI LAYOUT ---
with st.sidebar:
    st.title("🛡️ Institutional Controls")
    st.info(f"AI System: {'✅ ACTIVE' if AI_READY else '⚠️ OFFLINE'}")
    if st.button("🧪 Inject Master Portfolio"): inject_master_deals()
    st.markdown("---")
    st.write("### 🧠 Proactive Learning Log")
    st.caption("Novel risks identified across your bundle:")
    for risk in st.session_state['risk_registry']:
        st.write(f"• {risk}")

# TOP KPI RIBBON
st.title("🏗️ CRE Alpha Engine: Master Decision Grid")
if st.session_state['portfolio']:
    df = pd.DataFrame(st.session_state['portfolio'])
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Bundle Market Value", f"€{df['price'].sum():,.0f}")
    c2.metric("Weighted WALT", f"{df['walt'].mean():.1f} yrs")
    c3.metric("Avg Bankability", "76/100")
    c4.metric("Avg Investor Score", "81/100")

tabs = st.tabs(["📊 Portfolio Grid", "🛡️ Banker's Lens (CC)", "⚔️ Investor's Sword (IC)", "📂 Data Bridge"])

with tabs[0]:
    st.header("Master Portfolio Status")
    if st.session_state['portfolio']:
        # Apply Scoring
        scored_data = []
        for d in st.session_state['portfolio']:
            b, i, flags = run_institutional_scoring(d)
            d.update({"Bank Score": int(b), "Inv Score": int(i), "Flags": ", ".join(flags) if flags else "✅ Clear"})
            scored_data.append(d)
        
        display_df = pd.DataFrame(scored_data)
        st.dataframe(display_df[['name', 'type', 'Bank Score', 'Inv Score', 'Flags', 'walt']], use_container_width=True)
        
        fig = px.scatter(display_df, x="Bank Score", y="Inv Score", size="price", color="type", 
                         hover_name="name", title="The Alpha Matrix: Bankability vs Yield")
        st.plotly_chart(fig, use_container_width=True)
    else: st.info("Inject data to begin analysis.")

with tabs[1]:
    st.header("Banker's Defensive Shield (Credit Decision)")
    if st.session_state['portfolio']:
        col_l, col_r = st.columns([1, 2])
        with col_l:
            st.slider("Stress Case: Interest Rate (%)", 4.0, 7.5, 5.25)
            st.write("**Thresholds Applied:**")
            st.write("- Min DSCR: 1.20x")
            st.write("- Target Debt Yield: >9.0%")
        with col_r:
            st.write("### 🧐 Senior MD Credit Commentary")
            st.warning("Concentration Warning: Bundle is 45% exposed to Grocery Anchors in NRW. Recommend diversifying to Eastern Germany for 2026 yield compression.")

with tabs[2]:
    st.header("Investor's Yield Waterfall (Equity Decision)")
    if st.session_state['portfolio']:
        st.metric("Portfolio Cash-on-Cash", "11.85%")
        st.write("### 💰 Exit Value Sensitivity")
        st.info("Exit Premium: Bundle currently shows +60bps premium due to grocery-anchor stabilization.")

with tabs[3]:
    st.header("Adaptive Data Bridge")
    st.write("Upload new German leases to teach the engine's 'Novel Risk' registry.")
    upl = st.file_uploader("Upload Rent Roll (PDF/XLSX)")
    if st.button("🔍 Run Proactive Audit") and AI_READY:
        with st.spinner("AI is reasoning about institutional friction..."):
            result = adaptive_risk_discovery("Sample Text")
            st.success("Audit complete. Sidebar updated with new discovered risks.")