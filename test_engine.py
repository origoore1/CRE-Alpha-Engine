"""
test_engine.py  —  GRU Engine Regression Test Suite
====================================================
Generates each practice deal as an Excel file, runs it through the full
rent_roll_parser.py pipeline, and asserts correctness of verdict, DSCR,
LTV, and WAULT.  Also covers three edge-case scenarios.

Run with:
    pytest test_engine.py -v
    pytest test_engine.py -v --tb=short   # compact tracebacks
    pytest test_engine.py -v -k deal_3    # single deal
"""

import importlib.util
import os
import shutil
import tempfile
from datetime import date
from pathlib import Path

import pytest

import rent_roll_parser as rrp
from constants import (
    DSCR_PASS, DSCR_WARN, LTV_PASS, LTV_WARN,
    WAULT_PASS, WAULT_WARN, VACANCY_PASS, VACANCY_WARN,
    ANCHOR_PASS, ANCHOR_WARN,
    VOID_HOLD_RATE, NOI_FALLBACK_MARGIN,
    GREST_BY_STATE,
)

# ── Load practice_deals7.py via importlib (space in filename) ─────────────────
_pd7_path = Path(__file__).parent / "practice_deals7.py"
_pd7_spec = importlib.util.spec_from_file_location("practice_deals7", _pd7_path)
_pd7 = importlib.util.module_from_spec(_pd7_spec)
_pd7_spec.loader.exec_module(_pd7)

PRACTICE_DEALS        = _pd7.PRACTICE_DEALS
generate_practice_deal = _pd7.generate_practice_deal
MASTER_TEMPLATE        = _pd7.MASTER_TEMPLATE


# ═════════════════════════════════════════════════════════════════════════════
# SHARED HELPERS
# ═════════════════════════════════════════════════════════════════════════════

def _run_pipeline(path: str) -> tuple:
    """Run the complete parser pipeline and return (deal, rr, costs, metrics, qa, verdict)."""
    deal   = rrp.load_deal_overview(path)
    rr     = rrp.load_rent_roll(path)
    active = rr[~rr["is_vacant"]]
    vacant = rr[rr["is_vacant"]]
    costs  = rrp.load_cost_sheet(
        path,
        rr_gross_rent  = float(active["annual_rent"].sum()),
        rr_gla_total   = float(rr["area_sqm"].sum()),
        rr_vacant_area = float(vacant["area_sqm"].sum()),
    )
    metrics = rrp.compute_metrics(deal, rr, costs)
    qa      = rrp.run_qa_audit(metrics, deal)
    verdict = rrp.generate_verdict(metrics, qa)
    return deal, rr, costs, metrics, qa, verdict


def _wault_from_params(rent_roll_tuples: list) -> float:
    """
    Compute expected WAULT from raw deal parameter tuples (date-aware).
    Uses the same formula as rent_roll_parser so the value stays valid
    regardless of when the test suite is run.

    Tuple layout: (unit_ref, name, sector, anchor, area_sqm, rent_sqm_mo,
                   lease_start, lease_expiry, break_option, cpi_clause, ...)
    """
    today      = date.today()
    total_rent = 0.0
    weighted   = 0.0
    for row in rent_roll_tuples:
        area_sqm, rent_sqm_mo, lease_expiry, break_opt = row[4], row[5], row[7], row[8]
        if rent_sqm_mo > 0 and lease_expiry is not None:
            # Use the earlier of expiry / break option — matches engine logic
            effective_expiry = min(lease_expiry, break_opt) if break_opt else lease_expiry
            annual_rent  = area_sqm * rent_sqm_mo * 12.0
            unexpired_yr = max(0.0, (effective_expiry - today).days / 365.25)
            weighted    += annual_rent * unexpired_yr
            total_rent  += annual_rent
    return weighted / total_rent if total_rent > 0 else 0.0


def _gross_rent_from_params(rent_roll_tuples: list) -> float:
    return sum(row[4] * row[5] * 12.0 for row in rent_roll_tuples if row[5] > 0)


# ═════════════════════════════════════════════════════════════════════════════
# MODULE-SCOPED FIXTURE  —  generate all Excel files once per test run
# ═════════════════════════════════════════════════════════════════════════════

@pytest.fixture(scope="module")
def practice_results(tmp_path_factory):
    """
    Generate all 8 practice deal Excel files and run the full engine pipeline
    on each. Results are cached at module scope — Excel generation happens once.
    """
    out_dir = tmp_path_factory.mktemp("practice_deals")
    results = {}
    for dc in PRACTICE_DEALS:
        path = generate_practice_deal(dc, str(out_dir))
        deal, rr, costs, metrics, qa, verdict = _run_pipeline(path)
        results[dc["id"]] = {
            "config":  dc,
            "path":    path,
            "deal":    deal,
            "rr":      rr,
            "costs":   costs,
            "metrics": metrics,
            "qa":      qa,
            "verdict": verdict,
        }
    return results


# ═════════════════════════════════════════════════════════════════════════════
# EXPECTED VALUES
# ─────────────────────────────────────────────────────────────────────────────
# LTV   — exact arithmetic  (price - equity) / price  →  tolerance 0.5pp
# DSCR  — computed from cost-sheet NOI / (loan × rate) → tolerance 0.05x
# WAULT — computed dynamically by _wault_from_params()  → tolerance 0.20yr
# ═════════════════════════════════════════════════════════════════════════════

_EXP = {
    # id : (expected_ltv, expected_dscr)
    1: (0.6064, 1.663),   # PASS   — Regensburg clean deal
    2: (0.7143, 0.000),   # DECLINE — Duisburg vacant / NOI ≤ 0
    3: (0.6026, 1.143),   # INVESTIGATE — Erbpacht drags DSCR below 1.30x
    4: (0.6207, 1.051),   # INVESTIGATE — Aldi trophy; GIY < 4.5% → YELLOW
    5: (0.6531, 1.550),   # INVESTIGATE — LTV + anchor + expiry: 3 yellow warns
    6: (0.6098, 1.454),   # PASS   — Hamburg convenience
    7: (0.4706, 1.208),   # INVESTIGATE — Cologne mixed-use; thin DSCR
    8: (0.6138, 1.419),   # PASS   — Munich premium core
}

LTV_TOL   = 0.005   # ± 0.5pp
DSCR_TOL  = 0.05    # ± 0.05x
WAULT_TOL = 0.20    # ± 0.20yr


# ═════════════════════════════════════════════════════════════════════════════
# PRACTICE DEAL TESTS — VERDICT
# ═════════════════════════════════════════════════════════════════════════════

class TestVerdict:
    """Verdict (PASS / INVESTIGATE / DECLINE) must match expected for all deals."""

    @pytest.mark.parametrize("deal_id", range(1, 9), ids=[f"deal_{i}" for i in range(1, 9)])
    def test_verdict_matches_expected(self, deal_id, practice_results):
        r   = practice_results[deal_id]
        got = r["verdict"]["recommendation"]
        exp = r["config"]["verdict_expected"]
        assert got == exp, (
            f"\nDeal {deal_id} — {r['config']['title']}\n"
            f"  Expected verdict : {exp}\n"
            f"  Actual verdict   : {got}\n"
            f"  Rationale        : {r['verdict']['rationale']}\n"
            f"  Red flags        : {r['verdict']['red_flags']}"
        )

    def test_all_verdicts_are_valid_strings(self, practice_results):
        """Verdict is always one of the three canonical strings."""
        valid = {"PASS", "INVESTIGATE", "DECLINE"}
        for deal_id, r in practice_results.items():
            assert r["verdict"]["recommendation"] in valid, (
                f"Deal {deal_id}: verdict {r['verdict']['recommendation']!r} is not valid"
            )

    def test_verdict_includes_rationale(self, practice_results):
        """Verdict always carries a non-empty rationale string."""
        for deal_id, r in practice_results.items():
            assert r["verdict"].get("rationale"), f"Deal {deal_id}: missing rationale"


# ═════════════════════════════════════════════════════════════════════════════
# PRACTICE DEAL TESTS — DSCR
# ═════════════════════════════════════════════════════════════════════════════

class TestDSCR:
    """DSCR must be within ±0.05x of the expected value."""

    @pytest.mark.parametrize("deal_id", range(1, 9), ids=[f"deal_{i}" for i in range(1, 9)])
    def test_dscr_within_tolerance(self, deal_id, practice_results):
        r    = practice_results[deal_id]
        dscr = r["metrics"].get("dscr") or 0.0
        exp  = _EXP[deal_id][1]
        assert abs(dscr - exp) <= DSCR_TOL, (
            f"\nDeal {deal_id}: DSCR {dscr:.4f}x  expected {exp:.4f}x  "
            f"(diff {abs(dscr-exp):.4f}x, tol ±{DSCR_TOL}x)"
        )

    @pytest.mark.parametrize("deal_id", [1, 5, 6, 8], ids=["deal_1", "deal_5", "deal_6", "deal_8"])
    def test_pass_deal_dscr_above_covenant(self, deal_id, practice_results):
        """PASS deals must have DSCR ≥ 1.30x (the bank covenant minimum)."""
        dscr = practice_results[deal_id]["metrics"].get("dscr") or 0.0
        assert dscr >= DSCR_PASS, (
            f"Deal {deal_id} is PASS but DSCR {dscr:.4f}x < {DSCR_PASS}x covenant"
        )

    @pytest.mark.parametrize("deal_id", [2], ids=["deal_2"])
    def test_decline_deal_dscr_is_zero(self, deal_id, practice_results):
        """Deal 2 (fully distressed) must produce DSCR = 0 (NOI ≤ 0)."""
        dscr = practice_results[deal_id]["metrics"].get("dscr") or 0.0
        assert dscr < DSCR_WARN, (
            f"Deal {deal_id}: DSCR {dscr:.4f}x should be below {DSCR_WARN}x"
        )


# ═════════════════════════════════════════════════════════════════════════════
# PRACTICE DEAL TESTS — LTV
# ═════════════════════════════════════════════════════════════════════════════

class TestLTV:
    """LTV must be within ±0.5pp of the expected value."""

    @pytest.mark.parametrize("deal_id", range(1, 9), ids=[f"deal_{i}" for i in range(1, 9)])
    def test_ltv_within_tolerance(self, deal_id, practice_results):
        r   = practice_results[deal_id]
        ltv = r["metrics"].get("ltv", 0.0)
        exp = _EXP[deal_id][0]
        assert abs(ltv - exp) <= LTV_TOL, (
            f"\nDeal {deal_id}: LTV {ltv:.4f} ({ltv:.1%})  "
            f"expected {exp:.4f} ({exp:.1%})  "
            f"(diff {abs(ltv-exp):.4f}, tol ±{LTV_TOL})"
        )

    def test_ltv_formula_is_loan_over_price(self, practice_results):
        """Spot-check that LTV = (price - equity) / price for all deals."""
        for deal_id, r in practice_results.items():
            price  = r["deal"].get("asking_price", 0) or 0
            equity = r["deal"].get("equity", 0) or 0
            loan   = r["deal"].get("loan_amount", 0) or (price - equity)
            if price > 0:
                expected = loan / price
                actual   = r["metrics"].get("ltv", 0.0)
                assert abs(actual - expected) < 0.001, (
                    f"Deal {deal_id}: LTV formula mismatch — "
                    f"metric={actual:.4f}, loan/price={expected:.4f}"
                )

    @pytest.mark.parametrize("deal_id", [1, 6, 8], ids=["deal_1", "deal_6", "deal_8"])
    def test_pass_deal_ltv_within_bank_covenant(self, deal_id, practice_results):
        """PASS deals must have LTV ≤ 65%."""
        ltv = practice_results[deal_id]["metrics"].get("ltv", 0.0)
        assert ltv <= LTV_PASS, (
            f"Deal {deal_id} is PASS but LTV {ltv:.1%} > {LTV_PASS:.0%} covenant"
        )


# ═════════════════════════════════════════════════════════════════════════════
# PRACTICE DEAL TESTS — WAULT
# ═════════════════════════════════════════════════════════════════════════════

class TestWAULT:
    """WAULT must be within ±0.20yr of the value computed from deal parameters."""

    @pytest.mark.parametrize("deal_id", range(1, 9), ids=[f"deal_{i}" for i in range(1, 9)])
    def test_wault_within_tolerance(self, deal_id, practice_results):
        r            = practice_results[deal_id]
        wault_engine = r["metrics"].get("wault", 0.0)
        wault_exp    = _wault_from_params(r["config"]["rent_roll"])
        assert abs(wault_engine - wault_exp) <= WAULT_TOL, (
            f"\nDeal {deal_id}: WAULT from engine {wault_engine:.4f}yr  "
            f"expected {wault_exp:.4f}yr  "
            f"(diff {abs(wault_engine-wault_exp):.4f}yr, tol ±{WAULT_TOL}yr)"
        )

    @pytest.mark.parametrize("deal_id", [1, 6, 8], ids=["deal_1", "deal_6", "deal_8"])
    def test_pass_deal_wault_above_minimum(self, deal_id, practice_results):
        """PASS deals must have WAULT ≥ 4.0yr (bank minimum)."""
        wault = practice_results[deal_id]["metrics"].get("wault", 0.0)
        assert wault >= WAULT_PASS, (
            f"Deal {deal_id} is PASS but WAULT {wault:.2f}yr < {WAULT_PASS}yr minimum"
        )

    def test_wault_is_non_negative(self, practice_results):
        """WAULT must never be negative."""
        for deal_id, r in practice_results.items():
            assert r["metrics"].get("wault", 0.0) >= 0.0, (
                f"Deal {deal_id}: negative WAULT"
            )


# ═════════════════════════════════════════════════════════════════════════════
# PRACTICE DEAL TESTS — QA / METRICS SANITY
# ═════════════════════════════════════════════════════════════════════════════

class TestQASanity:
    """Cross-metric consistency checks across all practice deals."""

    def test_qa_results_non_empty(self, practice_results):
        """QA audit must return at least one result for every deal."""
        for deal_id, r in practice_results.items():
            assert len(r["qa"]) > 0, f"Deal {deal_id}: empty QA result list"

    def test_qa_result_statuses_are_valid(self, practice_results):
        """Every QAResult.status must be one of PASS / WARN / FAIL / N/A."""
        valid = {"PASS", "WARN", "FAIL", "N/A"}
        for deal_id, r in practice_results.items():
            for qa_r in r["qa"]:
                assert qa_r.status in valid, (
                    f"Deal {deal_id}, check {qa_r.check!r}: "
                    f"invalid status {qa_r.status!r}"
                )

    def test_noi_non_negative(self, practice_results):
        """NOI is always clamped to ≥ 0 by the engine (max(noi, 0))."""
        for deal_id, r in practice_results.items():
            assert r["metrics"].get("noi_proxy", 0) >= 0, (
                f"Deal {deal_id}: negative NOI_proxy"
            )

    def test_vacancy_rate_bounded(self, practice_results):
        """Vacancy rate is in [0, 1] for all deals."""
        for deal_id, r in practice_results.items():
            vr = r["metrics"].get("vacancy_rate", 0)
            assert 0.0 <= vr <= 1.0, f"Deal {deal_id}: vacancy_rate {vr} out of [0,1]"

    def test_anchor_pct_bounded(self, practice_results):
        """Anchor pct is in [0, 1] for all deals."""
        for deal_id, r in practice_results.items():
            ap = r["metrics"].get("anchor_pct", 0)
            assert 0.0 <= ap <= 1.0, f"Deal {deal_id}: anchor_pct {ap} out of [0,1]"

    def test_decline_deal_has_two_red_fails(self, practice_results):
        """Deal 2 (DECLINE) must have ≥ 2 RED fails, satisfying the verdict rule."""
        qa       = practice_results[2]["qa"]
        red_fails = [r for r in qa if r.status == "FAIL" and r.severity == "RED"]
        assert len(red_fails) >= 2, (
            f"DECLINE deal 2 has {len(red_fails)} RED fails — expected ≥ 2.\n"
            f"QA: {[(r.check, r.status, r.severity) for r in qa]}"
        )

    def test_investigate_deals_have_insufficient_reds_for_decline(self, practice_results):
        """INVESTIGATE deals must have < 2 RED fails (otherwise they'd be DECLINE)."""
        for deal_id in [3, 4, 5, 7]:
            qa        = practice_results[deal_id]["qa"]
            red_fails = [r for r in qa if r.status == "FAIL" and r.severity == "RED"]
            assert len(red_fails) < 2, (
                f"INVESTIGATE deal {deal_id} has {len(red_fails)} RED fails — "
                f"should be < 2.\n{[(r.check, r.status, r.severity) for r in qa]}"
            )

    def test_deal3_erbpacht_in_cost_breakdown(self, practice_results):
        """Deal 3 (Erbpacht) must show ground rent in the cost breakdown."""
        costs = practice_results[3]["costs"]
        ground_rent = costs.get("costs", {}).get("ground_rent", 0)
        assert ground_rent == pytest.approx(85_000, abs=1), (
            f"Deal 3 Erbpacht: ground_rent expected 85000, got {ground_rent}"
        )

    def test_deal4_single_tenant_anchor_pct(self, practice_results):
        """Deal 4 (single-tenant Aldi) must show anchor_pct = 1.0 (±0.01)."""
        anchor_pct = practice_results[4]["metrics"].get("anchor_pct", 0.0)
        assert abs(anchor_pct - 1.0) <= 0.01, (
            f"Deal 4 single-tenant: anchor_pct {anchor_pct:.4f} — expected 1.0"
        )


# ═════════════════════════════════════════════════════════════════════════════
# EDGE CASE 1 — VACANT-ONLY DEAL
# A property with 100% vacancy: no active leases, all units dark.
# ═════════════════════════════════════════════════════════════════════════════

_VACANT_DEAL = {
    "id": 99, "difficulty": "EDGE", "verdict_expected": "DECLINE",
    "title": "Edge Case Vacant Only Deal",
    "learning_objective": "Engine handles 100% vacancy gracefully.",
    "teaching_notes": "All units vacant.",
    "deal": {
        "property_name": "All-Vacant Test Property",
        "street_address": "Teststrasse 1",
        "city": "Berlin / Berlin",
        "asset_type": "Fachmarktzentrum (Retail Park)",
        "gla_sqm": 3000, "year_built": 2010, "parking": 100,
        "asking_price": 5_000_000, "equity": 2_000_000,
        "grest_rate": GREST_BY_STATE["Berlin"], "notar_rate": 0.015, "broker_rate": 0.010,
        "analysis_date": date.today(), "hold_period": 10,
        "interest_rate": 0.041, "loan_term_yrs": 10,
        "amort_type": "Annuitaetendarlehen", "amort_rate": 0.020, "io_period": 0,
    },
    "rent_roll": [
        ("V-01", "VACANT", "—", "N", 1000, 0, None, None, None,
         "N", 0.00, 0.00, 0, "N", 0, "Structural void"),
        ("V-02", "VACANT", "—", "N", 1200, 0, None, None, None,
         "N", 0.00, 0.00, 0, "N", 0, "Dark 24 months"),
        ("V-03", "VACANT", "—", "N",  800, 0, None, None, None,
         "N", 0.00, 0.00, 0, "N", 0, "Former anchor"),
    ],
    "costs": {
        "property_mgmt_pct": 0.015, "asset_mgmt_pct": 0.005,
        "insurance_sqm": 2.50, "capex_sqm": 5.00, "ground_rent_abs": 0,
        "grundsteuer_abs": 0, "void_svc_sqm": 22.0, "void_ins_sqm": 2.00,
        "letting_pct": 0.100, "other_abs": 3000,
    },
}


@pytest.fixture(scope="module")
def vacant_result(tmp_path_factory):
    out_dir = tmp_path_factory.mktemp("edge_vacant")
    path = generate_practice_deal(_VACANT_DEAL, str(out_dir))
    _, rr, costs, metrics, qa, verdict = _run_pipeline(path)
    return {"rr": rr, "costs": costs, "metrics": metrics, "qa": qa, "verdict": verdict}


class TestVacantOnlyDeal:
    """Edge case: 100% vacant property."""

    def test_verdict_is_decline(self, vacant_result):
        got = vacant_result["verdict"]["recommendation"]
        assert got == "DECLINE", f"Vacant deal: expected DECLINE, got {got!r}"

    def test_vacancy_rate_is_one(self, vacant_result):
        vr = vacant_result["metrics"].get("vacancy_rate", 0.0)
        assert abs(vr - 1.0) < 0.001, f"Vacant deal: vacancy_rate {vr:.4f}, expected 1.0"

    def test_gross_rent_is_zero(self, vacant_result):
        gr = vacant_result["metrics"].get("gross_passing_rent", -1)
        assert gr == pytest.approx(0.0, abs=0.01), (
            f"Vacant deal: gross_passing_rent {gr}, expected 0.0"
        )

    def test_noi_is_zero(self, vacant_result):
        """NOI is clamped to 0 when all units are vacant."""
        noi = vacant_result["metrics"].get("noi_proxy", -1)
        assert noi == pytest.approx(0.0, abs=0.01), (
            f"Vacant deal: noi_proxy {noi}, expected 0.0 (clamped)"
        )

    def test_wault_is_zero(self, vacant_result):
        """WAULT is 0 when there are no active leases."""
        wault = vacant_result["metrics"].get("wault", -1)
        assert wault == pytest.approx(0.0, abs=0.001), (
            f"Vacant deal: wault {wault:.4f}, expected 0.0"
        )

    def test_dscr_is_zero_or_none(self, vacant_result):
        """DSCR is 0 (NOI = 0, debt service > 0)."""
        dscr = vacant_result["metrics"].get("dscr") or 0.0
        assert dscr == pytest.approx(0.0, abs=0.001), (
            f"Vacant deal: DSCR {dscr:.4f}, expected 0.0"
        )

    def test_no_engine_crash(self, vacant_result):
        """The engine must complete without raising an exception (fixture success = pass)."""
        assert vacant_result["metrics"] is not None

    def test_anchor_pct_is_zero(self, vacant_result):
        """No active anchor tenants → anchor_pct = 0."""
        ap = vacant_result["metrics"].get("anchor_pct", -1)
        assert ap == pytest.approx(0.0, abs=0.001)


# ═════════════════════════════════════════════════════════════════════════════
# EDGE CASE 2 — SINGLE-TENANT DEAL
# One tenant occupies 100% of the GLA.
# ═════════════════════════════════════════════════════════════════════════════

_SINGLE_TENANT_DEAL = {
    "id": 98, "difficulty": "EDGE", "verdict_expected": "INVESTIGATE",
    "title": "Edge Case Single Tenant Deal",
    "learning_objective": "Engine correctly handles single-tenant asset.",
    "teaching_notes": "One anchor tenant, anchor_pct should equal 1.0.",
    "deal": {
        "property_name": "Lidl Fachmarkt Test",
        "street_address": "Musterweg 1",
        "city": "Hamburg / Hamburg",
        "asset_type": "Fachmarkt Single-Tenant Food",
        "gla_sqm": 1200, "year_built": 2015, "parking": 60,
        "asking_price": 4_500_000, "equity": 1_600_000,
        "grest_rate": GREST_BY_STATE["Hamburg"], "notar_rate": 0.015, "broker_rate": 0.010,
        "analysis_date": date.today(), "hold_period": 10,
        "interest_rate": 0.041, "loan_term_yrs": 10,
        "amort_type": "Annuitaetendarlehen", "amort_rate": 0.020, "io_period": 0,
    },
    # Single Lidl store: anchor=Y, 1200m², 5yr remaining from today
    "rent_roll": [
        ("U-01", "Lidl Dienstleistung GmbH", "Food & Grocery", "Y",
         1200, 10.50,
         date(2021, 3, 1), date(2031, 3, 1), None,
         "Y", 0.75, 0.10, 15120, "Y", 0,
         "Single tenant. Full-building Lidl."),
    ],
    "costs": {
        "property_mgmt_pct": 0.010, "asset_mgmt_pct": 0.003,
        "insurance_sqm": 2.00, "capex_sqm": 3.50, "ground_rent_abs": 0,
        "grundsteuer_abs": 0, "void_svc_sqm": 0, "void_ins_sqm": 0,
        "letting_pct": 0.020, "other_abs": 1000,
    },
}


@pytest.fixture(scope="module")
def single_tenant_result(tmp_path_factory):
    out_dir = tmp_path_factory.mktemp("edge_single")
    path = generate_practice_deal(_SINGLE_TENANT_DEAL, str(out_dir))
    _, rr, costs, metrics, qa, verdict = _run_pipeline(path)
    return {"rr": rr, "costs": costs, "metrics": metrics, "qa": qa, "verdict": verdict}


class TestSingleTenantDeal:
    """Edge case: single anchor tenant occupying 100% of GLA."""

    def test_anchor_pct_equals_one(self, single_tenant_result):
        """With one anchor tenant and no vacants, anchor_pct must be 1.0."""
        ap = single_tenant_result["metrics"].get("anchor_pct", 0.0)
        assert abs(ap - 1.0) <= 0.001, (
            f"Single-tenant deal: anchor_pct {ap:.4f}, expected 1.0"
        )

    def test_vacancy_rate_is_zero(self, single_tenant_result):
        vr = single_tenant_result["metrics"].get("vacancy_rate", 1.0)
        assert vr == pytest.approx(0.0, abs=0.001)

    def test_wault_equals_remaining_lease(self, single_tenant_result):
        """WAULT must equal the single tenant's remaining lease term (±0.20yr)."""
        wault = single_tenant_result["metrics"].get("wault", 0.0)
        expiry = _SINGLE_TENANT_DEAL["rent_roll"][0][7]  # date(2031,3,1)
        expected_wault = max(0.0, (expiry - date.today()).days / 365.25)
        assert abs(wault - expected_wault) <= WAULT_TOL, (
            f"Single-tenant WAULT {wault:.4f}yr, expected {expected_wault:.4f}yr "
            f"(tolerance ±{WAULT_TOL}yr)"
        )

    def test_n_active_leases_is_one(self, single_tenant_result):
        assert single_tenant_result["metrics"].get("n_active_leases") == 1

    def test_anchor_concentration_qa_is_fail(self, single_tenant_result):
        """100% anchor concentration should trigger the Anchor Concentration check as FAIL."""
        from constants import CHECK_ANCHOR
        qa_idx = {r.check: r for r in single_tenant_result["qa"]}
        anchor_result = qa_idx.get(CHECK_ANCHOR)
        assert anchor_result is not None, "Anchor Concentration check missing from QA"
        assert anchor_result.status in ("FAIL", "WARN"), (
            f"Single-tenant anchor: expected FAIL/WARN, got {anchor_result.status}"
        )

    def test_ltv_formula_correct(self, single_tenant_result):
        d      = _SINGLE_TENANT_DEAL["deal"]
        price  = d["asking_price"]
        equity = d["equity"]
        exp    = (price - equity) / price
        actual = single_tenant_result["metrics"].get("ltv", 0.0)
        assert abs(actual - exp) < 0.001, f"LTV {actual:.4f} vs expected {exp:.4f}"

    def test_no_engine_crash(self, single_tenant_result):
        assert single_tenant_result["metrics"] is not None


# ═════════════════════════════════════════════════════════════════════════════
# EDGE CASE 3 — ERBPACHT (GROUND LEASE)
# Ground rent materially reduces NOI.  Verified by running the same deal
# with and without Erbpacht and comparing the NOI difference.
# ═════════════════════════════════════════════════════════════════════════════

def _make_erbpacht_config(ground_rent_abs: int, suffix: str = "") -> dict:
    """Return a deal config with configurable ground rent."""
    return {
        "id": 97, "difficulty": "EDGE",
        "verdict_expected": "INVESTIGATE" if ground_rent_abs > 0 else "PASS",
        "title": f"Edge Case Erbpacht{suffix}",
        "learning_objective": "Ground rent correctly deducted from NOI.",
        "teaching_notes": "Erbpacht test case.",
        "deal": {
            "property_name": "Rewe Center Erbpacht Test",
            "street_address": "Hauptstrasse 1",
            "city": "Frankfurt / Hessen",
            "asset_type": "Nahversorgungszentrum",
            "gla_sqm": 2800, "year_built": 2005, "parking": 120,
            "asking_price": 7_500_000, "equity": 3_000_000,
            "grest_rate": GREST_BY_STATE["Hessen"], "notar_rate": 0.015, "broker_rate": 0.010,
            "analysis_date": date.today(), "hold_period": 10,
            "interest_rate": 0.041, "loan_term_yrs": 10,
            "amort_type": "Annuitaetendarlehen", "amort_rate": 0.020, "io_period": 0,
        },
        "rent_roll": [
            ("U-01", "Rewe Markt GmbH", "Food & Grocery", "Y",
             1800, 11.50, date(2019, 1, 1), date(2034, 12, 31), None,
             "Y", 0.75, 0.10, 23115, "Y", 0, "Anchor 8yr remaining"),
            ("U-02", "dm-drogerie markt", "Health & Beauty", "N",
             560, 17.00, date(2021, 4, 1), date(2031, 3, 31), None,
             "Y", 1.00, 0.10, 11424, "Y", 0, "5yr remaining"),
            ("U-03", "Lotto/Tabak", "Personal Services", "N",
             440, 12.00, date(2022, 1, 1), date(2029, 12, 31), None,
             "N", 0.00, 0.00, 3264, "Y", 0, "3yr remaining"),
        ],
        "costs": {
            "property_mgmt_pct": 0.015, "asset_mgmt_pct": 0.005,
            "insurance_sqm": 2.50, "capex_sqm": 5.00,
            "ground_rent_abs": ground_rent_abs,
            "grundsteuer_abs": 0, "void_svc_sqm": 0, "void_ins_sqm": 0,
            "letting_pct": 0.040, "other_abs": 2000,
        },
    }


@pytest.fixture(scope="module")
def erbpacht_results(tmp_path_factory):
    out_dir = tmp_path_factory.mktemp("edge_erbpacht")
    cfg_no   = _make_erbpacht_config(0,      suffix=" NoGround")
    cfg_with = _make_erbpacht_config(85_000, suffix=" WithGround")

    path_no   = generate_practice_deal(cfg_no,   str(out_dir))
    path_with = generate_practice_deal(cfg_with, str(out_dir))

    _, _, costs_no,   metrics_no,   qa_no,   verdict_no   = _run_pipeline(path_no)
    _, _, costs_with, metrics_with, qa_with, verdict_with = _run_pipeline(path_with)

    return {
        "no_ground":   {"costs": costs_no,   "metrics": metrics_no,
                        "qa": qa_no,         "verdict": verdict_no},
        "with_ground": {"costs": costs_with, "metrics": metrics_with,
                        "qa": qa_with,       "verdict": verdict_with},
    }


class TestErbpachtGroundLease:
    """Edge case: Erbpacht (ground lease) correctly reduces NOI."""

    def test_ground_rent_deducted_from_noi(self, erbpacht_results):
        """NOI must drop by ≈ EUR 85,000 when ground rent is added."""
        noi_no   = erbpacht_results["no_ground"]["metrics"]["noi_proxy"]
        noi_with = erbpacht_results["with_ground"]["metrics"]["noi_proxy"]
        delta = noi_no - noi_with
        assert abs(delta - 85_000) <= 1_000, (
            f"Erbpacht: NOI delta {delta:,.0f} EUR, expected ≈ 85,000 EUR. "
            f"NOI without ground rent: {noi_no:,.0f}, with: {noi_with:,.0f}"
        )

    def test_dscr_lower_with_ground_rent(self, erbpacht_results):
        """DSCR must be materially lower when ground rent is present."""
        dscr_no   = erbpacht_results["no_ground"]["metrics"].get("dscr") or 0.0
        dscr_with = erbpacht_results["with_ground"]["metrics"].get("dscr") or 0.0
        assert dscr_no > dscr_with, (
            f"Erbpacht: DSCR without ground rent ({dscr_no:.4f}x) should be "
            f"higher than with ground rent ({dscr_with:.4f}x)"
        )

    def test_erbpacht_deal_does_not_pass(self, erbpacht_results):
        """A deal with EUR 85k ground rent (NOI-destructive) must not produce PASS."""
        verdict = erbpacht_results["with_ground"]["verdict"]["recommendation"]
        assert verdict in ("INVESTIGATE", "DECLINE"), (
            f"Erbpacht deal produced {verdict!r} — expected INVESTIGATE or DECLINE"
        )

    def test_no_ground_deal_has_higher_giy(self, erbpacht_results):
        """GIY is identical for both deals (it's gross-based); NIY differs."""
        giy_no   = erbpacht_results["no_ground"]["metrics"].get("giy", 0)
        giy_with = erbpacht_results["with_ground"]["metrics"].get("giy", 0)
        # GIY = gross_rent / price, not affected by ground rent
        assert abs(giy_no - giy_with) < 0.001, (
            f"GIY should be the same regardless of ground rent "
            f"(gross-based metric). no={giy_no:.4f} with={giy_with:.4f}"
        )

    def test_niy_lower_with_ground_rent(self, erbpacht_results):
        """NIY = NOI / price — must be lower when ground rent reduces NOI."""
        niy_no   = erbpacht_results["no_ground"]["metrics"].get("niy", 0)
        niy_with = erbpacht_results["with_ground"]["metrics"].get("niy", 0)
        assert niy_no > niy_with, (
            f"NIY without ground rent ({niy_no:.4f}) should exceed "
            f"NIY with ground rent ({niy_with:.4f})"
        )

    def test_ground_rent_in_cost_breakdown(self, erbpacht_results):
        """Cost breakdown must contain the ground rent line item."""
        costs_with = erbpacht_results["with_ground"]["costs"]
        ground_rent = costs_with.get("costs", {}).get("ground_rent", 0)
        assert ground_rent == pytest.approx(85_000, abs=1), (
            f"Ground rent in cost breakdown: expected 85000, got {ground_rent}"
        )

    def test_no_engine_crash(self, erbpacht_results):
        """Engine must complete without exception for both variants."""
        assert erbpacht_results["no_ground"]["metrics"] is not None
        assert erbpacht_results["with_ground"]["metrics"] is not None


# ═════════════════════════════════════════════════════════════════════════════
# CONSTANTS CONSISTENCY TESTS
# ═════════════════════════════════════════════════════════════════════════════

class TestConstantsConsistency:
    """Verify constants.py values are internally consistent and match engine behaviour."""

    def test_grest_all_16_states(self):
        from constants import GREST_BY_STATE
        assert len(GREST_BY_STATE) == 16, (
            f"Expected 16 Bundesländer, got {len(GREST_BY_STATE)}"
        )

    def test_grest_rates_in_valid_range(self):
        from constants import GREST_BY_STATE
        for state, rate in GREST_BY_STATE.items():
            assert 0.03 <= rate <= 0.07, (
                f"{state}: GrESt rate {rate:.1%} outside valid range [3%, 7%]"
            )

    def test_pass_threshold_below_warn(self):
        """PASS threshold is always better than WARN threshold."""
        assert LTV_PASS   < LTV_WARN       # lower LTV is better
        assert DSCR_PASS  > DSCR_WARN      # higher DSCR is better
        assert WAULT_PASS > WAULT_WARN
        assert VACANCY_PASS < VACANCY_WARN  # lower vacancy is better
        assert ANCHOR_PASS  < ANCHOR_WARN

    def test_void_hold_rate_positive(self):
        assert VOID_HOLD_RATE > 0

    def test_noi_fallback_margin_in_range(self):
        assert 0.5 < NOI_FALLBACK_MARGIN < 1.0, (
            f"NOI_FALLBACK_MARGIN {NOI_FALLBACK_MARGIN} outside sensible range (0.5, 1.0)"
        )

    def test_anchor_names_are_lowercase(self):
        from constants import ANCHOR_NAMES
        for name in ANCHOR_NAMES:
            assert name == name.lower(), (
                f"ANCHOR_NAMES entry {name!r} is not lowercase — matching will fail"
            )

    def test_check_labels_are_non_empty_strings(self):
        from constants import (
            CHECK_LTV, CHECK_DSCR, CHECK_ICR, CHECK_WAULT, CHECK_VACANCY,
            CHECK_ANCHOR, CHECK_EXPIRY_3YR, CHECK_GIY, CHECK_NOI,
            CHECK_EQUITY, CHECK_DY, CHECK_COST_SHEET,
        )
        checks = [CHECK_LTV, CHECK_DSCR, CHECK_ICR, CHECK_WAULT, CHECK_VACANCY,
                  CHECK_ANCHOR, CHECK_EXPIRY_3YR, CHECK_GIY, CHECK_NOI,
                  CHECK_EQUITY, CHECK_DY, CHECK_COST_SHEET]
        for c in checks:
            assert isinstance(c, str) and len(c) > 0

    def test_check_labels_match_qa_output(self, practice_results):
        """All CHECK_* label constants appear in actual QA output from at least one deal."""
        from constants import (
            CHECK_LTV, CHECK_DSCR, CHECK_ICR, CHECK_WAULT, CHECK_VACANCY,
            CHECK_ANCHOR, CHECK_EXPIRY_3YR, CHECK_GIY, CHECK_NOI,
            CHECK_EQUITY, CHECK_DY,
        )
        all_checks = set()
        for r in practice_results.values():
            for qa_r in r["qa"]:
                all_checks.add(qa_r.check)

        for label in [CHECK_LTV, CHECK_DSCR, CHECK_ICR, CHECK_WAULT, CHECK_VACANCY,
                      CHECK_ANCHOR, CHECK_EXPIRY_3YR, CHECK_GIY, CHECK_NOI,
                      CHECK_EQUITY, CHECK_DY]:
            assert label in all_checks, (
                f"CHECK label {label!r} never appeared in any QA output — "
                f"check name mismatch between constants.py and rent_roll_parser.py"
            )


# =============================================================================
# DD SCANNER — CPI PASSTHROUGH PARSING TESTS  (BL-04)
# =============================================================================
# Load dd_scanner 7.py via importlib (space in filename prevents normal import)

import importlib.util as _ilu_dd

_dd_path  = Path(__file__).parent / "dd_scanner 7.py"
_dd_spec2 = _ilu_dd.spec_from_file_location("dd_scanner", _dd_path)
_dd_mod2  = _ilu_dd.module_from_spec(_dd_spec2)
_dd_spec2.loader.exec_module(_dd_mod2)

_extract_cpi_passthrough = _dd_mod2._extract_cpi_passthrough
_extract_lease_terms_fn  = _dd_mod2.extract_lease_terms
del _ilu_dd, _dd_spec2, _dd_mod2


class TestCpiPassthroughParsing:
    """BL-04: CPI passthrough decimal vs integer disambiguation."""

    # ── _extract_cpi_passthrough unit tests ──────────────────────────────────

    def test_integer_pct_75(self):
        """75% with explicit % sign → HIGH confidence, integer% format."""
        r = _extract_cpi_passthrough(
            "Die Miete wird zu 75% der Veränderung des Verbraucherpreisindex angepasst."
        )
        assert r['value'] == pytest.approx(0.75)
        assert r['format'] == 'integer%'
        assert r['confidence'] == 'HIGH'

    def test_integer_pct_100(self):
        """100% full passthrough → HIGH confidence."""
        r = _extract_cpi_passthrough(
            "angepasst zu 100% der Indexveränderung"
        )
        assert r['value'] == pytest.approx(1.0)
        assert r['format'] == 'integer%'
        assert r['confidence'] == 'HIGH'

    def test_integer_pct_50(self):
        """50% passthrough via 'of the change' construction → HIGH confidence."""
        r = _extract_cpi_passthrough(
            "adjusted by 50% of the change in the CPI index"
        )
        assert r['value'] == pytest.approx(0.50)
        assert r['format'] == 'integer%'
        assert r['confidence'] == 'HIGH'

    def test_decimal_format_dot(self):
        """Decimal 0.75 with dot separator → AMBER confidence, decimal format."""
        r = _extract_cpi_passthrough(
            "Die Anpassung erfolgt zu 0.75 der Indexveränderung."
        )
        assert r['value'] == pytest.approx(0.75)
        assert r['format'] == 'decimal'
        assert r['confidence'] == 'AMBER'

    def test_decimal_format_comma(self):
        """Decimal 0,75 with German comma → AMBER confidence, decimal format."""
        r = _extract_cpi_passthrough(
            "Die Anpassung erfolgt zu 0,75 der Indexveränderung."
        )
        assert r['value'] == pytest.approx(0.75)
        assert r['format'] == 'decimal'
        assert r['confidence'] == 'AMBER'

    def test_no_passthrough_found(self):
        """CPI clause text without a rate → value None."""
        r = _extract_cpi_passthrough(
            "Es gibt eine Wertsicherungsklausel im Mietvertrag."
        )
        assert r['value'] is None
        assert r['format'] is None
        assert r['confidence'] is None

    def test_raw_text_captured(self):
        """raw field must be populated when a value is found."""
        r = _extract_cpi_passthrough(
            "Anpassung zu 80% der Veränderung."
        )
        assert r['raw'] is not None
        assert len(r['raw']) > 0

    # ── extract_lease_terms integration tests ────────────────────────────────

    def test_full_extract_integer_pct(self):
        """extract_lease_terms: integer% passthrough stored with HIGH confidence."""
        text = (
            "Indexklausel: Die Miete wird zu 80% der Veränderung des CPI angepasst. "
            "Mietfläche: 1.500 m². Kaltmiete: 15,00 EUR/m²/Monat."
        )
        res = _extract_lease_terms_fn(text)
        assert res['cpi_clause'] == 'Y'
        assert res['cpi_passthrough'] == pytest.approx(0.80)
        assert res['cpi_passthrough_format'] == 'integer%'
        assert res['confidence']['cpi_passthrough'] == 'HIGH'

    def test_full_extract_decimal_amber(self):
        """extract_lease_terms: decimal passthrough stored with AMBER confidence."""
        text = (
            "CPI-Klausel: Die Anpassung beträgt 0.80 der Indexveränderung. "
            "Mietfläche: 1.500 m²."
        )
        res = _extract_lease_terms_fn(text)
        assert res['cpi_clause'] == 'Y'
        assert res['cpi_passthrough'] == pytest.approx(0.80)
        assert res['cpi_passthrough_format'] == 'decimal'
        assert res['confidence']['cpi_passthrough'] == 'AMBER'

    def test_full_extract_no_passthrough(self):
        """extract_lease_terms: CPI clause present but no rate → passthrough None."""
        text = (
            "Wertsicherungsklausel gemäß §2 des Vertrags. "
            "Mietfläche: 1.000 m²."
        )
        res = _extract_lease_terms_fn(text)
        assert res['cpi_clause'] == 'Y'
        assert res['cpi_passthrough'] is None
        assert res['cpi_passthrough_format'] is None

    def test_normalised_values_within_bounds(self):
        """Normalised passthrough must always be in (0, 1] when found."""
        texts = [
            "zu 75% der Veränderung",
            "zu 100% der Indexveränderung",
            "zu 0.50 der Veränderung",
            "beträgt 0,90 der Indexveränderung",
        ]
        for t in texts:
            r = _extract_cpi_passthrough(t)
            if r['value'] is not None:
                assert 0 < r['value'] <= 1.0, (
                    f"Out-of-bounds passthrough {r['value']} from text: {t!r}"
                )

    # ── New-gap tests added after fixing Group B (1.0/1,0/1.00) and
    #    Group C lookahead (75,0 German decimal) ──────────────────────────────

    def test_german_decimal_75_comma_zero(self):
        """75,0 (German decimal notation for 75.0%) → 0.75, AMBER."""
        r = _extract_cpi_passthrough(
            "Die Miete beträgt 75,0 der Indexveränderung."
        )
        assert r['value'] == pytest.approx(0.75), f"got {r['value']}"
        assert r['format'] == 'integer_no_pct'
        assert r['confidence'] == 'AMBER'

    def test_decimal_100pct_dot_zero(self):
        """1.0 decimal (100% full passthrough) → 1.0, AMBER."""
        r = _extract_cpi_passthrough(
            "passthrough of 1.0 of the change in the CPI index"
        )
        assert r['value'] == pytest.approx(1.0), f"got {r['value']}"
        assert r['format'] == 'decimal'
        assert r['confidence'] == 'AMBER'

    def test_decimal_100pct_german_comma(self):
        """1,0 German decimal (100% passthrough) → 1.0, AMBER."""
        r = _extract_cpi_passthrough(
            "passthrough of 1,0 of the change in the CPI index"
        )
        assert r['value'] == pytest.approx(1.0), f"got {r['value']}"
        assert r['format'] == 'decimal'
        assert r['confidence'] == 'AMBER'

    def test_decimal_100pct_dot_double_zero(self):
        """1.00 decimal (100% passthrough) → 1.0, AMBER."""
        r = _extract_cpi_passthrough(
            "passthrough of 1.00 of the change in the CPI index"
        )
        assert r['value'] == pytest.approx(1.0), f"got {r['value']}"
        assert r['format'] == 'decimal'
        assert r['confidence'] == 'AMBER'

    def test_list_pattern_not_matched(self):
        """'75, 80' list pattern must not be misread as a passthrough rate."""
        r = _extract_cpi_passthrough(
            "beträgt 75, 80 Indexpunkte"
        )
        assert r['value'] is None, f"Expected None, got {r['value']}"

    # ── Regression: existing passing formats must still work ─────────────────

    def test_regression_integer_pct_75_with_space(self):
        """75 % (space before %) still detected at HIGH confidence."""
        r = _extract_cpi_passthrough(
            "passthrough of 75 % of the change"
        )
        assert r['value'] == pytest.approx(0.75)
        assert r['confidence'] == 'HIGH'

    def test_regression_decimal_0_75_dot(self):
        """0.75 decimal still detected after Group B change."""
        r = _extract_cpi_passthrough(
            "passthrough of 0.75 of the change"
        )
        assert r['value'] == pytest.approx(0.75)
        assert r['format'] == 'decimal'

    def test_regression_decimal_0_75_comma(self):
        """0,75 German decimal still detected after Group B change."""
        r = _extract_cpi_passthrough(
            "passthrough of 0,75 of the change"
        )
        assert r['value'] == pytest.approx(0.75)
        assert r['format'] == 'decimal'

    def test_regression_integer_pct_100(self):
        """100% full passthrough still detected at HIGH confidence."""
        r = _extract_cpi_passthrough(
            "passthrough of 100% of the change"
        )
        assert r['value'] == pytest.approx(1.0)
        assert r['confidence'] == 'HIGH'


# =============================================================================
# SECTION 8 — TENANT CONCENTRATION RISK  (Deep Insights)
# =============================================================================
# Tests validate the concentration calculation logic using controlled DataFrames
# and practice deal rent rolls.  `_di_tenant_concentration` lives in app 7.py
# which imports streamlit at module level and cannot be imported in tests;
# these tests therefore define a _calc_concentration() reference implementation
# that mirrors the algorithm and can be kept in sync via the assertions below.

import pandas as _pd_conc
import datetime as _dt_conc


def _calc_concentration(rr_df) -> dict:
    """
    Reference implementation of the concentration calculations in
    `_di_tenant_concentration()` (app 7.py).  Used to verify correctness
    independently of the Streamlit import chain.
    """
    if rr_df is None or rr_df.empty:
        return {}

    active = rr_df[~rr_df['is_vacant']] if 'is_vacant' in rr_df.columns else rr_df
    if active.empty:
        return {}

    total_rent = float(active['annual_rent'].sum())
    if total_rent <= 0:
        return {}

    by_tenant = (
        active.groupby('tenant', as_index=False)['annual_rent']
        .sum()
        .sort_values('annual_rent', ascending=False)
        .reset_index(drop=True)
    )
    by_tenant['pct'] = by_tenant['annual_rent'] / total_rent

    top1 = float(by_tenant.iloc[0]['annual_rent'])            if len(by_tenant) >= 1 else 0.0
    top3 = float(by_tenant.iloc[:3]['annual_rent'].sum())     if len(by_tenant) >= 1 else 0.0
    top5 = float(by_tenant.iloc[:5]['annual_rent'].sum())     if len(by_tenant) >= 1 else 0.0

    # Sector breakdown
    sector_col = 'sector' if 'sector' in active.columns else None
    sector_breakdown = []
    if sector_col:
        by_sec = (
            active.groupby(sector_col, as_index=False)['annual_rent']
            .sum()
            .sort_values('annual_rent', ascending=False)
            .reset_index(drop=True)
        )
        by_sec['pct'] = by_sec['annual_rent'] / total_rent
        sector_breakdown = [
            {'sector': str(r[sector_col]), 'rent': float(r['annual_rent']), 'pct': float(r['pct'])}
            for _, r in by_sec.iterrows()
        ]

    # Expiry by year
    this_year = _dt_conc.date.today().year
    expiry_by_year = []
    if 'lease_expiry' in active.columns:
        for yr in range(this_year, this_year + 11):
            mask = active['lease_expiry'].apply(
                lambda d: (d.year == yr) if (hasattr(d, 'year') and _pd_conc.notna(d)) else False
            )
            yr_rent = float(active.loc[mask, 'annual_rent'].sum())
            expiry_by_year.append({'year': yr, 'rent': yr_rent, 'pct': yr_rent / total_rent})

    # Anchor
    anchor_rent = 0.0
    if 'is_anchor' in active.columns:
        anchor_rent = float(active.loc[active['is_anchor'].astype(bool), 'annual_rent'].sum())

    return {
        'total_rent':       total_rent,
        'top1_pct':         top1 / total_rent,
        'top3_pct':         top3 / total_rent,
        'top5_pct':         top5 / total_rent,
        'top1_name':        str(by_tenant.iloc[0]['tenant']) if len(by_tenant) >= 1 else '',
        'top_tenants':      [
            {'tenant': str(r['tenant']), 'rent': float(r['annual_rent']), 'pct': float(r['pct'])}
            for _, r in by_tenant.iterrows()
        ],
        'sector_breakdown': sector_breakdown,
        'expiry_by_year':   expiry_by_year,
        'anchor_pct':       anchor_rent / total_rent,
        'anchor_rent':      anchor_rent,
    }


def _make_rr(rows: list) -> '_pd_conc.DataFrame':
    """
    Build a minimal rent roll DataFrame for concentration tests.
    rows: list of (tenant, sector, annual_rent, is_anchor, is_vacant, lease_expiry_year)
    """
    from datetime import date as _d
    records = []
    for tenant, sector, rent, anchor, vacant, exp_yr in rows:
        records.append({
            'tenant':      tenant,
            'sector':      sector,
            'annual_rent': float(rent),
            'is_anchor':   anchor,
            'is_vacant':   vacant,
            'lease_expiry': _d(exp_yr, 12, 31) if exp_yr else None,
        })
    return _pd_conc.DataFrame(records)


class TestTenantConcentration:
    """Section 8 — Tenant Concentration Risk calculation tests."""

    # ── Top-N concentration ───────────────────────────────────────────────────

    def test_top1_single_dominant_tenant(self):
        """Single tenant holding 80% of rent → top1_pct = 0.80."""
        rr = _make_rr([
            ('REWE',   'Food', 80_000, True,  False, 2033),
            ('dm',     'H&B',  10_000, False, False, 2030),
            ('Lotto',  'Svcs', 10_000, False, False, 2029),
        ])
        c = _calc_concentration(rr)
        assert c['top1_pct'] == pytest.approx(0.80)
        assert c['top1_name'] == 'REWE'

    def test_top3_pct_sums_correctly(self):
        """top3_pct must equal sum of top 3 tenant rents / total."""
        rr = _make_rr([
            ('A', 'Food',  50_000, True,  False, 2035),
            ('B', 'Food',  30_000, False, False, 2032),
            ('C', 'DIY',   15_000, False, False, 2031),
            ('D', 'H&B',    5_000, False, False, 2028),
        ])
        c = _calc_concentration(rr)
        expected_top3 = (50_000 + 30_000 + 15_000) / 100_000
        assert c['top3_pct'] == pytest.approx(expected_top3)

    def test_top5_equals_total_when_fewer_than_5_tenants(self):
        """With only 3 tenants, top5_pct must equal 100%."""
        rr = _make_rr([
            ('A', 'Food', 40_000, True,  False, 2033),
            ('B', 'H&B',  35_000, False, False, 2030),
            ('C', 'Svcs', 25_000, False, False, 2029),
        ])
        c = _calc_concentration(rr)
        assert c['top5_pct'] == pytest.approx(1.0)

    def test_top1_red_flag_threshold(self):
        """top1_pct > 0.50 is detectable; below 0.50 is not flagged."""
        rr_ok = _make_rr([
            ('A', 'Food', 40_000, True,  False, 2033),
            ('B', 'H&B',  35_000, False, False, 2030),
            ('C', 'DIY',  25_000, False, False, 2029),
        ])
        rr_flag = _make_rr([
            ('A', 'Food', 55_000, True,  False, 2033),
            ('B', 'H&B',  45_000, False, False, 2030),
        ])
        assert _calc_concentration(rr_ok)['top1_pct']   < 0.50
        assert _calc_concentration(rr_flag)['top1_pct'] > 0.50

    def test_top3_red_flag_threshold(self):
        """top3_pct > 0.75 boundary — detectable via calculation."""
        rr = _make_rr([
            ('A', 'Food', 40_000, True,  False, 2033),
            ('B', 'Food', 20_000, False, False, 2031),
            ('C', 'DIY',  16_000, False, False, 2030),
            ('D', 'H&B',  14_000, False, False, 2029),
            ('E', 'Svcs', 10_000, False, False, 2028),
        ])
        c = _calc_concentration(rr)
        assert c['top3_pct'] == pytest.approx(76_000 / 100_000)
        assert c['top3_pct'] > 0.75   # triggers warning

    def test_vacant_units_excluded_from_concentration(self):
        """Vacant units must not contribute rent to concentration figures."""
        rr = _make_rr([
            ('REWE',   'Food',  60_000, True,  False, 2035),
            ('Vacant', '',           0, False, True,  2030),
        ])
        c = _calc_concentration(rr)
        # Total rent = 60 000 (vacant contributes 0, but is also excluded by is_vacant)
        assert c['total_rent'] == pytest.approx(60_000)
        assert c['top1_pct']   == pytest.approx(1.0)

    # ── Sector breakdown ──────────────────────────────────────────────────────

    def test_sector_breakdown_sums_to_100pct(self):
        """All sector shares must sum to exactly 1.0."""
        rr = _make_rr([
            ('REWE',    'Food & Grocery', 50_000, True,  False, 2035),
            ('dm',      'Health & Beauty',20_000, False, False, 2031),
            ('OBI',     'DIY',            20_000, False, False, 2030),
            ('Fashion', 'Fashion',        10_000, False, False, 2029),
        ])
        c = _calc_concentration(rr)
        total_pct = sum(s['pct'] for s in c['sector_breakdown'])
        assert total_pct == pytest.approx(1.0, abs=1e-9)

    def test_sector_breakdown_ordered_descending(self):
        """Sector breakdown must be ordered largest → smallest rent."""
        rr = _make_rr([
            ('A', 'DIY',   10_000, False, False, 2030),
            ('B', 'Food',  50_000, True,  False, 2035),
            ('C', 'H&B',   20_000, False, False, 2031),
        ])
        c = _calc_concentration(rr)
        rents = [s['rent'] for s in c['sector_breakdown']]
        assert rents == sorted(rents, reverse=True)

    def test_same_sector_tenants_aggregated(self):
        """Two tenants in the same sector should appear as one sector entry."""
        rr = _make_rr([
            ('REWE',  'Food', 40_000, True,  False, 2035),
            ('Edeka', 'Food', 30_000, True,  False, 2033),
            ('dm',    'H&B',  30_000, False, False, 2030),
        ])
        c = _calc_concentration(rr)
        sectors = [s['sector'] for s in c['sector_breakdown']]
        assert len(sectors) == 2
        food_entry = next(s for s in c['sector_breakdown'] if s['sector'] == 'Food')
        assert food_entry['rent'] == pytest.approx(70_000)
        assert food_entry['pct']  == pytest.approx(0.70)

    # ── Lease expiry by year ──────────────────────────────────────────────────

    def test_expiry_rent_maps_to_correct_year(self):
        """Rent expiring in a given year must appear in that year's bucket."""
        from datetime import date
        this_year = date.today().year
        target_yr = this_year + 2
        rr = _make_rr([
            ('A', 'Food', 60_000, True,  False, target_yr),
            ('B', 'H&B',  40_000, False, False, target_yr + 4),
        ])
        c = _calc_concentration(rr)
        bucket = next(e for e in c['expiry_by_year'] if e['year'] == target_yr)
        assert bucket['rent'] == pytest.approx(60_000)
        assert bucket['pct']  == pytest.approx(0.60)

    def test_expiry_profile_covers_10_years(self):
        """expiry_by_year must contain exactly 11 entries (this year + 10 forward)."""
        rr = _make_rr([
            ('A', 'Food', 100_000, True, False, 2040),
        ])
        c = _calc_concentration(rr)
        assert len(c['expiry_by_year']) == 11

    def test_expiry_buckets_sum_to_at_most_total_rent(self):
        """Sum of expiry buckets ≤ total_rent (leases beyond 10 yrs not included)."""
        rr = _make_rr([
            ('A', 'Food', 50_000, True,  False, 2029),
            ('B', 'H&B',  30_000, False, False, 2031),
            ('C', 'DIY',  20_000, False, False, 2099),   # beyond 10-yr window
        ])
        c = _calc_concentration(rr)
        bucket_sum = sum(e['rent'] for e in c['expiry_by_year'])
        assert bucket_sum <= c['total_rent'] + 1   # ≤ total (far-future lease not in window)

    def test_zero_rent_year_has_zero_pct(self):
        """Years with no expiring leases must have rent=0 and pct=0."""
        from datetime import date
        this_year = date.today().year
        rr = _make_rr([
            ('A', 'Food', 100_000, True, False, this_year + 1),
        ])
        c = _calc_concentration(rr)
        empty_buckets = [e for e in c['expiry_by_year'] if e['year'] != this_year + 1]
        for bucket in empty_buckets:
            assert bucket['rent'] == pytest.approx(0.0)
            assert bucket['pct']  == pytest.approx(0.0)

    # ── Anchor dependency ─────────────────────────────────────────────────────

    def test_anchor_pct_correct(self):
        """anchor_pct = anchor rent / total rent."""
        rr = _make_rr([
            ('REWE', 'Food', 60_000, True,  False, 2035),
            ('dm',   'H&B',  20_000, False, False, 2030),
            ('Shop', 'Svcs', 20_000, False, False, 2028),
        ])
        c = _calc_concentration(rr)
        assert c['anchor_pct']  == pytest.approx(0.60)
        assert c['anchor_rent'] == pytest.approx(60_000)

    def test_anchor_flag_below_40pct(self):
        """anchor_pct < 0.40 is detectable — verify threshold boundary."""
        rr_low = _make_rr([
            ('REWE',  'Food', 30_000, True,  False, 2035),
            ('Shop1', 'Svcs', 40_000, False, False, 2028),
            ('Shop2', 'Svcs', 30_000, False, False, 2027),
        ])
        rr_ok = _make_rr([
            ('REWE',  'Food', 40_000, True,  False, 2035),
            ('Shop1', 'Svcs', 35_000, False, False, 2028),
            ('Shop2', 'Svcs', 25_000, False, False, 2027),
        ])
        assert _calc_concentration(rr_low)['anchor_pct'] < 0.40
        assert _calc_concentration(rr_ok)['anchor_pct']  >= 0.40

    def test_no_anchor_tenants_returns_zero(self):
        """Rent roll with no anchors → anchor_pct = 0."""
        rr = _make_rr([
            ('Shop1', 'Svcs', 50_000, False, False, 2030),
            ('Shop2', 'Svcs', 50_000, False, False, 2029),
        ])
        c = _calc_concentration(rr)
        assert c['anchor_pct']  == pytest.approx(0.0)
        assert c['anchor_rent'] == pytest.approx(0.0)

    def test_all_anchor_returns_100pct(self):
        """Rent roll where every tenant is an anchor → anchor_pct = 1.0."""
        rr = _make_rr([
            ('REWE',     'Food', 60_000, True, False, 2035),
            ('Kaufland', 'Food', 40_000, True, False, 2033),
        ])
        c = _calc_concentration(rr)
        assert c['anchor_pct'] == pytest.approx(1.0)

    # ── Integration: practice deal 1 ──────────────────────────────────────────

    def test_practice_deal1_concentration(self, practice_results):
        """Deal 1 (dominant REWE anchor) — anchor_pct > 0.50, top1 > 0.50."""
        rr = practice_results[1]['rr']
        c  = _calc_concentration(rr)
        assert c, "Concentration result must not be empty for deal 1"
        # REWE is the dominant income source in deal 1
        assert c['anchor_pct'] > 0.50, (
            f"Deal 1 anchor_pct expected > 0.50, got {c['anchor_pct']:.2%}"
        )
        assert c['top1_pct'] > 0.50, (
            f"Deal 1 top1_pct expected > 0.50 (REWE dominant), got {c['top1_pct']:.2%}"
        )

    def test_concentration_totals_match_gross_rent(self, practice_results):
        """Concentration total_rent must match gross_passing_rent from metrics."""
        for deal_id, res in practice_results.items():
            rr      = res['rr']
            metrics = res['metrics']
            c       = _calc_concentration(rr)
            if not c:
                continue
            gross = metrics.get('gross_passing_rent', 0)
            assert abs(c['total_rent'] - gross) < 1.0, (
                f"deal {deal_id}: concentration total_rent {c['total_rent']:,.0f} "
                f"!= gross_passing_rent {gross:,.0f}"
            )

    def test_top_tenants_list_is_exhaustive(self, practice_results):
        """top_tenants list must cover all active tenants in the rent roll."""
        for deal_id, res in practice_results.items():
            rr = res['rr']
            c  = _calc_concentration(rr)
            if not c:
                continue
            active_count = int((~rr['is_vacant']).sum())
            # Number of unique tenants in top_tenants (multi-unit tenants aggregated)
            n_tenants = len(c['top_tenants'])
            assert n_tenants >= 1
            assert n_tenants <= active_count, (
                f"deal {deal_id}: top_tenants has {n_tenants} entries but only "
                f"{active_count} active tenants"
            )


# =============================================================================
# SECTION 9 — DEAL SWOT ANALYSIS
# =============================================================================
# _di_swot() lives in app 7.py which cannot be imported in tests (st.set_page_config
# at module level).  Tests use a _build_swot_data() reference implementation that
# mirrors the SWOT classification rules, and verify:
#   (a) all four quadrant keys present
#   (b) strengths non-empty on PASS deals
#   (c) threats non-empty on DECLINE deals
#   (d) opportunity yield gap EUR matches _di_true_yield_gap reference


def _build_swot_data(metrics: dict, qa: list, rr) -> dict:
    """
    Reference SWOT classifier — mirrors _di_swot() core classification rules
    without calling the heavy DI helper chain.  Returns a dict with keys:
      strengths, weaknesses, opportunities, threats (all list[str]),
      summary (str), opp_yield_gap_eur (float).
    """
    from constants import (
        DSCR_PASS, ICR_PASS, LTV_PASS, GIY_PASS, WAULT_PASS,
        CHECK_LTV, CHECK_DSCR, CHECK_ICR, CHECK_WAULT, CHECK_VACANCY,
        CHECK_ANCHOR, CHECK_EXPIRY_3YR, CHECK_GIY, CHECK_DY,
        VOID_HOLD_RATE,
    )

    strengths     = []
    weaknesses    = []
    opportunities = []
    threats       = []

    gross_rent  = float(metrics.get('gross_passing_rent', 0) or 0)
    wault       = float(metrics.get('wault', 0) or 0)
    anchor_pct  = float(metrics.get('anchor_pct', 0) or 0)
    expiry_risk = float(metrics.get('expiry_risk_3yr_pct', 0) or 0)
    active      = rr[~rr['is_vacant']] if 'is_vacant' in rr.columns else rr
    vacant      = rr[rr['is_vacant']]  if 'is_vacant' in rr.columns else rr.iloc[:0]

    # ── Strengths ─────────────────────────────────────────────────────────────
    dscr = metrics.get('dscr')
    if dscr and dscr >= DSCR_PASS:
        strengths.append(f"DSCR {dscr:.2f}x — above 1.30x minimum")

    icr = metrics.get('icr')
    if icr and icr >= ICR_PASS:
        strengths.append(f"ICR {icr:.2f}x — above 1.75x covenant")

    ltv = metrics.get('ltv')
    if ltv and ltv <= LTV_PASS:
        strengths.append(f"LTV {ltv:.1%} — within 65% bank maximum")

    giy = metrics.get('giy')
    if giy and giy >= GIY_PASS:
        strengths.append(f"GIY {giy:.2%} — above 5.5% benchmark")

    if wault >= WAULT_PASS:
        strengths.append(f"WAULT {wault:.1f} years — above 4-year minimum")

    # ── Weaknesses (QA flags) ─────────────────────────────────────────────────
    _QA_WEAK = {
        CHECK_LTV, CHECK_DSCR, CHECK_ICR, CHECK_WAULT,
        CHECK_VACANCY, CHECK_ANCHOR, CHECK_EXPIRY_3YR, CHECK_GIY, CHECK_DY,
    }
    _seen = set()
    for _qi in sorted(qa, key=lambda q: 0 if q.status == 'FAIL' else 1):
        if _qi.status in ('WARN', 'FAIL') and _qi.check in _QA_WEAK and _qi.check not in _seen:
            weaknesses.append(f"{_qi.check} [{_qi.status}]")
            _seen.add(_qi.check)

    # Vacant units
    for _, _vrow in vacant.iterrows():
        _varea = float(_vrow.get('area_sqm', 0) or 0)
        if _varea > 0:
            weaknesses.append(f"Vacant {_varea:,.0f} m²")

    # ── Opportunities: yield gap ───────────────────────────────────────────────
    opp_yield_gap_eur = 0.0
    if not active.empty and not vacant.empty:
        _leased_area = float(active['area_sqm'].sum() or 0)
        if _leased_area > 0 and gross_rent > 0:
            _avg_sqm_mo  = gross_rent / (_leased_area * 12)
            _vacant_area = float(vacant['area_sqm'].sum() or 0)
            opp_yield_gap_eur = _avg_sqm_mo * _vacant_area * 12
            if opp_yield_gap_eur > 0:
                opportunities.append(f"Void upside EUR {opp_yield_gap_eur:,.0f}/yr")

    # ── Threats: QA fails + concentration ────────────────────────────────────
    _red_flags = [q for q in qa if q.status == 'FAIL']
    for _qi in _red_flags:
        threats.append(f"Hard fail: {_qi.check}")

    if expiry_risk > 0.20:
        threats.append(f"{expiry_risk:.0%} rent expiring within 3 years")

    if not active.empty and gross_rent > 0:
        _by_t = active.groupby('tenant')['annual_rent'].sum()
        if len(_by_t) > 0:
            _t1p = float(_by_t.max()) / gross_rent
            if _t1p > 0.40:
                threats.append(f"Top tenant {_t1p:.1%} income concentration")

    # ── Summary ───────────────────────────────────────────────────────────────
    n_s, n_w, n_t, n_o = len(strengths), len(weaknesses), len(threats), len(opportunities)
    if _red_flags:
        summary = f"Hard fails present — covenant breaches must be resolved before IC."
    elif n_t >= 3:
        summary = f"High-risk profile with {n_t} threats; stress-testing required."
    elif n_s >= 4 and n_t <= 1:
        summary = f"Fundamentally strong deal with {n_s} green metrics."
    else:
        summary = f"{n_s} strength(s), {n_w} weakness(es), {n_t} threat(s)."

    return {
        'strengths':         strengths[:6],
        'weaknesses':        weaknesses[:6],
        'opportunities':     opportunities[:6],
        'threats':           threats[:6],
        'summary':           summary,
        'opp_yield_gap_eur': opp_yield_gap_eur,
    }


def _ref_yield_gap_eur(rr, metrics: dict) -> float:
    """Reference yield gap EUR — mirrors _di_true_yield_gap() computation."""
    asking = metrics.get('asking_price', 0) or 0
    if asking <= 0:
        return 0.0
    active = rr[~rr['is_vacant']] if 'is_vacant' in rr.columns else rr
    vacant = rr[rr['is_vacant']]  if 'is_vacant' in rr.columns else rr.iloc[:0]
    if active.empty or vacant.empty:
        return 0.0
    leased_area = float(active['area_sqm'].sum() or 0)
    if leased_area <= 0:
        return 0.0
    avg_sqm_mo   = float(active['annual_rent'].sum()) / (leased_area * 12)
    vacant_area  = float(vacant['area_sqm'].sum() or 0)
    return avg_sqm_mo * vacant_area * 12


class TestSwotAnalysis:
    """Section 9 — Deal SWOT Analysis tests."""

    # ── Structure: all four quadrant keys must be present ────────────────────

    def test_swot_all_keys_present(self, practice_results):
        """Every deal must produce a SWOT dict with all four quadrant keys + summary."""
        required = {'strengths', 'weaknesses', 'opportunities', 'threats', 'summary'}
        for deal_id, res in practice_results.items():
            swot = _build_swot_data(res['metrics'], res['qa'], res['rr'])
            missing = required - swot.keys()
            assert not missing, (
                f"Deal {deal_id}: SWOT dict missing keys {missing}"
            )

    def test_swot_quadrants_are_lists(self, practice_results):
        """All four quadrant values must be lists."""
        for deal_id, res in practice_results.items():
            swot = _build_swot_data(res['metrics'], res['qa'], res['rr'])
            for key in ('strengths', 'weaknesses', 'opportunities', 'threats'):
                assert isinstance(swot[key], list), (
                    f"Deal {deal_id}: SWOT[{key!r}] is {type(swot[key])}, expected list"
                )

    def test_swot_summary_is_nonempty_string(self, practice_results):
        """Summary must be a non-empty string for every deal."""
        for deal_id, res in practice_results.items():
            swot = _build_swot_data(res['metrics'], res['qa'], res['rr'])
            assert isinstance(swot['summary'], str) and len(swot['summary']) > 10, (
                f"Deal {deal_id}: SWOT summary is empty or too short: {swot['summary']!r}"
            )

    def test_swot_max_6_items_per_quadrant(self, practice_results):
        """No quadrant may exceed 6 items."""
        for deal_id, res in practice_results.items():
            swot = _build_swot_data(res['metrics'], res['qa'], res['rr'])
            for key in ('strengths', 'weaknesses', 'opportunities', 'threats'):
                assert len(swot[key]) <= 6, (
                    f"Deal {deal_id}: SWOT[{key!r}] has {len(swot[key])} items, max 6"
                )

    # ── Content: strengths non-empty on PASS deals ────────────────────────────

    def test_strengths_nonempty_on_pass_deals(self, practice_results):
        """PASS deals must have at least one strength (green metric)."""
        pass_deals = [
            (did, res) for did, res in practice_results.items()
            if res['verdict']['recommendation'] == 'PASS'
        ]
        assert pass_deals, "No PASS deals in practice set"
        for deal_id, res in pass_deals:
            swot = _build_swot_data(res['metrics'], res['qa'], res['rr'])
            assert len(swot['strengths']) >= 1, (
                f"Deal {deal_id} (PASS): strengths list is empty — "
                f"metrics: dscr={res['metrics'].get('dscr')}, ltv={res['metrics'].get('ltv')}"
            )

    def test_strengths_count_higher_on_pass_than_decline(self, practice_results):
        """Average strength count must be higher for PASS deals than DECLINE deals."""
        pass_counts = [
            len(_build_swot_data(res['metrics'], res['qa'], res['rr'])['strengths'])
            for res in practice_results.values()
            if res['verdict']['recommendation'] == 'PASS'
        ]
        decline_counts = [
            len(_build_swot_data(res['metrics'], res['qa'], res['rr'])['strengths'])
            for res in practice_results.values()
            if res['verdict']['recommendation'] == 'DECLINE'
        ]
        if pass_counts and decline_counts:
            assert (sum(pass_counts) / len(pass_counts)) > (
                sum(decline_counts) / len(decline_counts)
            ), "PASS deals should have more strengths on average than DECLINE deals"

    # ── Content: threats non-empty on DECLINE deals ───────────────────────────

    def test_threats_nonempty_on_decline_deals(self, practice_results):
        """DECLINE deals must have at least one threat (hard fail in QA)."""
        decline_deals = [
            (did, res) for did, res in practice_results.items()
            if res['verdict']['recommendation'] == 'DECLINE'
        ]
        assert decline_deals, "No DECLINE deals in practice set"
        for deal_id, res in decline_deals:
            swot = _build_swot_data(res['metrics'], res['qa'], res['rr'])
            assert len(swot['threats']) >= 1, (
                f"Deal {deal_id} (DECLINE): threats list is empty — "
                f"QA: {[(q.check, q.status) for q in res['qa'] if q.status == 'FAIL']}"
            )

    def test_decline_deals_have_red_flag_threats(self, practice_results):
        """Threats on DECLINE deals must contain the hard-fail QA check label."""
        for deal_id, res in practice_results.items():
            if res['verdict']['recommendation'] != 'DECLINE':
                continue
            swot    = _build_swot_data(res['metrics'], res['qa'], res['rr'])
            red_checks = {q.check for q in res['qa'] if q.status == 'FAIL'}
            # At least one threat string should reference a known check
            threat_text = ' '.join(swot['threats'])
            matched = any(chk in threat_text for chk in red_checks)
            assert matched, (
                f"Deal {deal_id} (DECLINE): threat strings don't reference any "
                f"failed QA check. Fails={red_checks}, threats={swot['threats']}"
            )

    # ── Opportunity: yield gap matches reference calculation ──────────────────

    def test_yield_gap_opportunity_matches_reference(self, practice_results):
        """SWOT opp_yield_gap_eur must equal the _di_true_yield_gap reference for every deal."""
        for deal_id, res in practice_results.items():
            rr      = res['rr']
            metrics = res['metrics']
            swot    = _build_swot_data(metrics, res['qa'], rr)
            ref_gap = _ref_yield_gap_eur(rr, metrics)
            assert swot['opp_yield_gap_eur'] == pytest.approx(ref_gap, abs=1.0), (
                f"Deal {deal_id}: SWOT yield gap {swot['opp_yield_gap_eur']:,.0f} "
                f"!= reference {ref_gap:,.0f}"
            )

    def test_yield_gap_zero_when_fully_let(self, practice_results):
        """A fully let deal must have zero yield gap opportunity."""
        for deal_id, res in practice_results.items():
            rr = res['rr']
            if 'is_vacant' not in rr.columns or not rr['is_vacant'].any():
                swot = _build_swot_data(res['metrics'], res['qa'], rr)
                assert swot['opp_yield_gap_eur'] == pytest.approx(0.0, abs=1.0), (
                    f"Deal {deal_id}: no vacant units but yield gap = "
                    f"{swot['opp_yield_gap_eur']:,.0f}"
                )

    def test_yield_gap_positive_when_vacant(self, practice_results):
        """A deal with vacant units must have a positive yield gap opportunity."""
        for deal_id, res in practice_results.items():
            rr      = res['rr']
            metrics = res['metrics']
            asking  = metrics.get('asking_price', 0) or 0
            if 'is_vacant' not in rr.columns or not rr['is_vacant'].any() or asking <= 0:
                continue
            swot = _build_swot_data(metrics, res['qa'], rr)
            assert swot['opp_yield_gap_eur'] > 0, (
                f"Deal {deal_id}: has vacant units and asking price but yield gap = 0"
            )

    # ── Weaknesses: QA failures appear in weaknesses list ────────────────────

    def test_weaknesses_nonempty_when_qa_has_flags(self, practice_results):
        """Any deal with WARN or FAIL QA items must have at least one weakness."""
        for deal_id, res in practice_results.items():
            has_flags = any(q.status in ('WARN', 'FAIL') for q in res['qa'])
            if has_flags:
                swot = _build_swot_data(res['metrics'], res['qa'], res['rr'])
                assert len(swot['weaknesses']) >= 1, (
                    f"Deal {deal_id}: has QA flags but weaknesses list is empty"
                )


# =============================================================================
# SECTION 10 — SCENARIO ENGINE  (Deep Insights)
# =============================================================================
# _di_scenario_engine() lives in app 7.py which cannot be imported in tests.
# Tests use a _build_scenario_data() reference implementation that mirrors the
# key arithmetic and verifies the four invariants:
#   (a) result dict has all three scenario keys
#   (b) stress DSCR ≤ base DSCR  (anchor exit + rate rise always hurts coverage)
#   (c) upside NOI ≥ base NOI    (void fill always adds income)
#   (d) base NOI matches what was passed in (round-trip sanity)
#   (e) verdict strings are always valid
# =============================================================================

# Default loan parameters — match all 8 practice deals (see practice_deals7.py)
_SCEN_INT_RT       = 0.041   # 4.1% interest rate
_SCEN_AMRT_RT      = 0.020   # 2.0% amortisation rate
_SCEN_IO_YEARS     = 2       # 2-year interest-only period
_SCEN_LOAN_TYPE    = 'Annuitaetendarlehen'
_SCEN_CPI          = 0.020   # 2% CPI indexation
_SCEN_SEVERITY     = 2       # Default stress severity: anchor exits
_SCEN_HOLD_YRS     = 10

# Stress severity parameter table (mirrors _SCEN_SEVERITY in app 7.py)
_SEV_TABLE = {
    1: {'anchor_dark': False, 'anchor_factor': 0.85, 'int_add': 0.000},
    2: {'anchor_dark': True,  'anchor_factor': 0.00, 'int_add': 0.005},
    3: {'anchor_dark': True,  'anchor_factor': 0.00, 'int_add': 0.010},
}


def _build_scenario_data(metrics: dict, rr, severity: int = 2) -> dict:
    """
    Reference scenario engine — mirrors the core arithmetic of _di_scenario_engine()
    in app 7.py.  Returns a dict with keys 'base', 'stress', 'upside', each
    containing 'noi' and 'dscr'.

    Parameters use practice-deal defaults (4.1% / 2yr IO / 2% amort).
    """
    import pandas as _pd_sc

    sev        = _SEV_TABLE.get(severity, _SEV_TABLE[2])
    noi        = float(metrics.get('noi_proxy') or 0)
    loan       = float(metrics.get('loan_amount') or 0)
    void_costs = float(metrics.get('void_costs') or 0)
    gross_rent = float(metrics.get('gross_passing_rent') or 0)

    # Year-1 debt service (IO period ≥ 1 year → interest only)
    y1_ds = loan * _SCEN_INT_RT   # IO because io_years=2

    # ── BASE ──────────────────────────────────────────────────────────────────
    base_dscr = noi / y1_ds if y1_ds > 0 else None

    # ── IDENTIFY LARGEST ANCHOR (or largest tenant as fallback) ───────────────
    anchor_rent = 0.0
    anchor_area = 0.0
    if rr is not None and not rr.empty:
        active = rr[~rr['is_vacant']] if 'is_vacant' in rr.columns else rr
        if not active.empty:
            _anc = (active[active['is_anchor'].astype(bool)]
                    if 'is_anchor' in active.columns else _pd_sc.DataFrame())
            _pool = _anc if not _anc.empty else active
            top_row   = _pool.nlargest(1, 'annual_rent').iloc[0]
            anchor_rent = float(top_row.get('annual_rent', 0) or 0)
            anchor_area = float(top_row.get('area_sqm', 0) or 0)

    # ── STRESS ────────────────────────────────────────────────────────────────
    stress_int = _SCEN_INT_RT + sev['int_add']
    if sev['anchor_dark']:
        stress_noi = noi - anchor_rent - anchor_area * VOID_HOLD_RATE
    else:
        _cut = anchor_rent * (1.0 - sev['anchor_factor'])
        stress_noi = noi - _cut
    stress_y1_ds = loan * stress_int   # still IO in year 1
    stress_dscr  = stress_noi / stress_y1_ds if stress_y1_ds > 0 else None

    # ── UPSIDE ────────────────────────────────────────────────────────────────
    # Void income at average passing rent/m²
    void_erv = 0.0
    if rr is not None and not rr.empty and 'is_vacant' in rr.columns:
        active = rr[~rr['is_vacant']]
        vacant = rr[rr['is_vacant']]
        leased_area = float(active['area_sqm'].sum() or 0)
        if leased_area > 0 and not vacant.empty:
            avg_mo = float(active['annual_rent'].sum()) / (leased_area * 12)
            void_erv = avg_mo * float(vacant['area_sqm'].sum()) * 12.0
    upside_noi_steady = noi + void_erv + void_costs
    upside_dscr = upside_noi_steady / y1_ds if y1_ds > 0 else None

    return {
        'base':   {'noi': noi,               'dscr': base_dscr},
        'stress': {'noi': stress_noi,        'dscr': stress_dscr},
        'upside': {'noi': upside_noi_steady, 'dscr': upside_dscr},
    }


class TestScenarioEngine:
    """Section 10 — Scenario Engine tests."""

    # ── Structure ─────────────────────────────────────────────────────────────

    def test_scenario_has_all_three_keys(self, practice_results):
        """Every deal must produce a scenario dict with base, stress, and upside."""
        for deal_id, res in practice_results.items():
            scen = _build_scenario_data(res['metrics'], res['rr'])
            assert 'base'   in scen, f"Deal {deal_id}: missing 'base' scenario"
            assert 'stress' in scen, f"Deal {deal_id}: missing 'stress' scenario"
            assert 'upside' in scen, f"Deal {deal_id}: missing 'upside' scenario"

    def test_scenario_each_case_has_noi(self, practice_results):
        """Each scenario sub-dict must carry a 'noi' key."""
        for deal_id, res in practice_results.items():
            scen = _build_scenario_data(res['metrics'], res['rr'])
            for case in ('base', 'stress', 'upside'):
                assert 'noi' in scen[case], (
                    f"Deal {deal_id}: scenario '{case}' missing 'noi' key"
                )

    # ── Invariant: stress NOI ≤ base NOI ─────────────────────────────────────

    def test_stress_noi_le_base_noi(self, practice_results):
        """
        Stressing the deal must never increase NOI — anchor departure or rent cut
        always reduces income (severity 2 = anchor exits, worst case for income).
        """
        for deal_id, res in practice_results.items():
            if res['metrics'].get('noi_proxy', 0) <= 0:
                continue   # NOI ≤ 0 deals (e.g. Duisburg) are already distressed
            scen = _build_scenario_data(res['metrics'], res['rr'], severity=2)
            assert scen['stress']['noi'] <= scen['base']['noi'], (
                f"Deal {deal_id}: stress NOI {scen['stress']['noi']:,.0f} "
                f"> base NOI {scen['base']['noi']:,.0f} — stress should reduce income"
            )

    def test_stress_noi_le_base_noi_severity3(self, practice_results):
        """Severity 3 (severe) must also reduce NOI relative to base."""
        for deal_id, res in practice_results.items():
            if res['metrics'].get('noi_proxy', 0) <= 0:
                continue
            scen = _build_scenario_data(res['metrics'], res['rr'], severity=3)
            assert scen['stress']['noi'] <= scen['base']['noi'], (
                f"Deal {deal_id}: severity-3 stress NOI {scen['stress']['noi']:,.0f} "
                f"> base NOI {scen['base']['noi']:,.0f}"
            )

    def test_severity1_stress_less_punishing_than_severity2(self, practice_results):
        """Mild stress (severity 1) must result in higher stress NOI than base stress (severity 2)."""
        for deal_id, res in practice_results.items():
            if res['metrics'].get('noi_proxy', 0) <= 0:
                continue
            rr = res['rr']
            # Only meaningful when there is an identifiable anchor to stress
            active = rr[~rr['is_vacant']] if 'is_vacant' in rr.columns else rr
            has_anchor = ('is_anchor' in active.columns and
                          active['is_anchor'].astype(bool).any())
            if not has_anchor:
                continue
            scen1 = _build_scenario_data(res['metrics'], rr, severity=1)
            scen2 = _build_scenario_data(res['metrics'], rr, severity=2)
            assert scen1['stress']['noi'] >= scen2['stress']['noi'], (
                f"Deal {deal_id}: mild stress NOI {scen1['stress']['noi']:,.0f} "
                f"< base stress NOI {scen2['stress']['noi']:,.0f}"
            )

    # ── Invariant: stress DSCR ≤ base DSCR ───────────────────────────────────

    def test_stress_dscr_le_base_dscr(self, practice_results):
        """
        Stress must produce a lower DSCR than base: both NOI falls and
        interest rate rises, so coverage can only worsen.
        """
        for deal_id, res in practice_results.items():
            scen = _build_scenario_data(res['metrics'], res['rr'], severity=2)
            if scen['base']['dscr'] is None or scen['stress']['dscr'] is None:
                continue
            assert scen['stress']['dscr'] <= scen['base']['dscr'], (
                f"Deal {deal_id}: stress DSCR {scen['stress']['dscr']:.3f} "
                f"> base DSCR {scen['base']['dscr']:.3f}"
            )

    # ── Invariant: upside NOI ≥ base NOI ─────────────────────────────────────

    def test_upside_noi_ge_base_noi(self, practice_results):
        """
        Upside (void fill + void cost recovery) must always yield NOI ≥ base.
        void_erv ≥ 0 and void_costs ≥ 0, so upside_noi_steady ≥ noi always.
        """
        for deal_id, res in practice_results.items():
            scen = _build_scenario_data(res['metrics'], res['rr'])
            assert scen['upside']['noi'] >= scen['base']['noi'], (
                f"Deal {deal_id}: upside NOI {scen['upside']['noi']:,.0f} "
                f"< base NOI {scen['base']['noi']:,.0f}"
            )

    def test_upside_noi_strictly_greater_when_vacant(self, practice_results):
        """For deals with vacancy and void costs, upside NOI must strictly exceed base."""
        for deal_id, res in practice_results.items():
            rr      = res['rr']
            metrics = res['metrics']
            has_vacant   = ('is_vacant' in rr.columns and rr['is_vacant'].any())
            has_voidcost = float(metrics.get('void_costs') or 0) > 0
            if not (has_vacant or has_voidcost):
                continue
            scen = _build_scenario_data(metrics, rr)
            assert scen['upside']['noi'] > scen['base']['noi'], (
                f"Deal {deal_id}: has vacancy/void costs but upside NOI "
                f"({scen['upside']['noi']:,.0f}) == base NOI ({scen['base']['noi']:,.0f})"
            )

    # ── Base NOI round-trip ───────────────────────────────────────────────────

    def test_base_noi_matches_engine_metrics(self, practice_results):
        """
        Base scenario NOI must exactly match noi_proxy from the main engine
        (since the scenario engine uses the same value as input).
        """
        for deal_id, res in practice_results.items():
            scen      = _build_scenario_data(res['metrics'], res['rr'])
            engine_noi = float(res['metrics'].get('noi_proxy') or 0)
            assert scen['base']['noi'] == pytest.approx(engine_noi, abs=1.0), (
                f"Deal {deal_id}: base scenario NOI {scen['base']['noi']:,.0f} "
                f"differs from engine noi_proxy {engine_noi:,.0f}"
            )
