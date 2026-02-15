import streamlit as st
import pandas as pd
import numpy as np

# --- 1. SYSTEM CONFIGURATION ---
st.set_page_config(page_title="CRE Alpha Engine - Germany", layout="wide")

# Persistent memory for your portfolio bundle
if 'portfolio' not in st.session_state:
    st.session_state['portfolio'] = []

# --- 2. THE EXPERT LOGIC ENGINE ---
class GermanCRELogic:
    @staticmethod
    def calculate_costs(price, state):
        # 2026 Institutional Friction (RETT + Notary + Broker)
        rett_map = {"Saxony": 0.055, "NRW": 0.065, "Bavaria": 0.035, "Berlin": 0.06}
        tax_rate = rett_map.get(state, 0.05)
        total_fees_pct = tax_rate + 0.02 + (0.03 * 1.19) 
        return price * (1 + total_fees_pct), price * total_fees_pct

    @staticmethod
    def banker_audit(price, noi, loan, rate):
        debt_service = loan * rate
        dscr = noi / debt_service if debt_service > 0 else 0
        debt_yield = (noi / loan) * 100
        ltv = (loan / price) * 100
        # 2026 Safety Standards: DSCR 1.3+ and Debt Yield 9%+
        approved = dscr >= 1.3 and debt_yield >= 9.0
        return dscr, debt_yield, ltv, approved

# --- 3. THE USER INTERFACE ---
st.title("🏗️ CRE Alpha Engine: Institutional Portfolio Builder")
st.markdown("### *Strategy: Buy, Fix, Bundle, Exit*")

# Correctly defining tabs as a list
tabs = st.tabs(["📂 Data", "🎲 Simulator", "🛡️ Banker", "⚔️ Investor", "💰 Exit", "🌿 ESG"])

# TAB 1: DATA UPLOAD
with tabs[0]:
    st.header("📂 1. Real Data Upload")
    st.file_uploader("Upload German Mietaufstellung", type=["pdf", "xlsx"])
    st.info("Upload your real deal documents here once the simulator tests are complete.")

# TAB 2: SIMULATOR
with tabs[1]:
    st.header("🎲 Deal Simulator")
    if st.button("Generate Randomized Asset"):
        price = np.random.randint(4, 12) * 1000000
        entry_yield = np.random.uniform(0.065, 0.085)
        st.session_state['active_deal'] = {
            "price": price, 
            "noi": price * entry_yield,
            "walt": np.random.uniform(3.0, 5.5),
            "state": np.random.choice(["Saxony", "NRW", "Thuringia"]),
            "esg": np.random.choice(["C", "D", "E"])
        }
    
    if 'active_deal' in st.session_state:
        d = st.session_state['active_deal']
        total_basis, fees = GermanCRELogic.calculate_costs(d['price'], d['state'])
        st.session_state['active_deal']['total_basis'] = total_basis
        c1, c2, c3 = st.columns(3)
        c1.metric("Price", f"€{d['price']:,}")
        c2.metric("Yield", f"{round((d['noi']/d['price'])*100, 2)}%")
        c3.metric("Location", d['state'])

# TAB 3: BANKER
with tabs[2]:
    st.header("🛡️ Banker's Defensive Shield")
    if 'active_deal' in st.session_state:
        d = st.session_state['active_deal']
        ltv_in = st.slider("LTV (%)", 50, 80, 65)
        rate_in = st.slider("Interest Rate (%)", 3.0, 6.0, 4.5) / 100
        loan = d['price'] * (ltv_in / 100)
        dscr, dy, ltv_out, ok = GermanCRELogic.banker_audit(d['price'], d['noi'], loan, rate_in)
        st.metric("DSCR", f"{dscr:.2f}", delta="SAFE" if ok else "RISKY")
        if ok: 
            st.success("APPROVED: Institutional-grade debt profile.")
        else: 
            st.error("REJECTED: High risk for German lenders.")
        st.session_state['active_deal']['loan_amount'] = loan
    else: 
        st.warning("Generate a deal first.")

# TAB 4: INVESTOR
with tabs[3]:
    st.header("⚔️ Investor's Yield Sword")
    if 'active_deal' in st.session_state:
        d = st.session_state['active_deal']
        capex = st.number_input("Betterment Capex (€)", 500000)
        # VERIFIED FIX: Added the rent_uplift slider back correctly
        rent_uplift = st.slider("Rent Uplift (%)", 0, 30, 15) / 100
        new_noi = d['noi'] * (1 + rent_uplift)
        yoc = (new_noi / (d['total_basis'] + capex)) * 100
        st.metric("Stabilized Yield on Cost", f"{yoc:.2f}%")
        if st.button("✅ Add to Portfolio Bundle"):
            final = d.copy()
            final['noi'] = new_noi
            final['total_basis'] += capex
            st.session_state['portfolio'].append(final)
            st.success("Asset added to the 'Exit' bundle!")
    else: 
        st.warning("Finish the Banker analysis first.")

# TAB 5: EXIT
with tabs[4]:
    st.header("💰 Institutional Exit & Portfolio Alpha")
    if st.session_state['portfolio']:
        df = pd.DataFrame(st.session_state['portfolio'])
        t_noi, t_cost = df['noi'].sum(), df['total_basis'].sum()
        m_cap = st.slider("Market Exit Cap (%)", 5.0, 8.5, 7.0) / 100
        prem = st.slider("Bundle Premium (bps)", 0, 120, 60)
        bundle_cap = m_cap - (prem / 10000)
        val = t_noi / bundle_cap
        st.metric("Bundle Portfolio Value", f"€{val:,.0f}", delta=f"€{(val - (t_noi/m_cap)):,.0f} Alpha")
        st.table(df[['state', 'price', 'noi', 'esg']])
    else: 
        st.info("Portfolio is empty. Add assets in Tab 4.")

# TAB 6: ESG
with tabs[5]:
    st.header("🌿 ESG Risk Audit")
    st.write("2026 Reality: No ESG 'B' rating means no Institutional Sale.")
    if st.session_state['portfolio']:
        st.warning("Action Required: Asset upgrades needed to reach institutional 'Green' standards.")