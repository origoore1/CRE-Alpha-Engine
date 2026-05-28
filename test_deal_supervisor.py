"""
test_deal_supervisor.py  —  pytest suite for deal_supervisor.audit_deal()
=========================================================================
Run:  pytest test_deal_supervisor.py -v
"""

import pytest
from deal_supervisor import audit_deal, Alert, _REQUIRED_FIELDS

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

CLEAN_DEAL = {
    "purchase_price": 5_000_000,
    "giy":            0.065,
    "niy":            0.058,
    "wault":          8.0,
    "dscr":           1.45,
    "ltv":            0.62,
    "irr":            0.092,
    "anchor_tenant":  "REWE",
    "anchor_covenant": "strong",
    "vacancy_rate":   0.04,
    "rent_roll":      [{"tenant": "REWE", "rent": 325_000}],
    "verdict":        "PASS",
}


def _deal(**overrides):
    """Return a clean deal with specific fields overridden."""
    d = dict(CLEAN_DEAL)
    d.update(overrides)
    return d


def _remove(field: str):
    """Return a clean deal with one field removed."""
    d = dict(CLEAN_DEAL)
    d.pop(field, None)
    return d


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def alerts_by_severity(result, severity):
    return [a for a in result["alerts"] if a.severity == severity]


def alerts_by_category(result, category):
    return [a for a in result["alerts"] if a.category == category]


# ---------------------------------------------------------------------------
# 1. Return structure
# ---------------------------------------------------------------------------

class TestReturnStructure:
    def test_keys_present(self):
        r = audit_deal(CLEAN_DEAL)
        assert set(r.keys()) == {"readiness_score", "alerts", "mentor_note", "is_ready_for_ic"}

    def test_score_is_float(self):
        r = audit_deal(CLEAN_DEAL)
        assert isinstance(r["readiness_score"], float)

    def test_score_in_range(self):
        r = audit_deal(CLEAN_DEAL)
        assert 0.0 <= r["readiness_score"] <= 100.0

    def test_alerts_is_list(self):
        r = audit_deal(CLEAN_DEAL)
        assert isinstance(r["alerts"], list)

    def test_mentor_note_is_string(self):
        r = audit_deal(CLEAN_DEAL)
        assert isinstance(r["mentor_note"], str)
        assert len(r["mentor_note"]) > 0

    def test_is_ready_is_bool(self):
        r = audit_deal(CLEAN_DEAL)
        assert isinstance(r["is_ready_for_ic"], bool)


# ---------------------------------------------------------------------------
# 2. Clean deal — should be ready
# ---------------------------------------------------------------------------

class TestCleanDeal:
    def test_clean_deal_no_criticals(self):
        r = audit_deal(CLEAN_DEAL)
        assert len(alerts_by_severity(r, "CRITICAL")) == 0

    def test_clean_deal_no_warnings(self):
        r = audit_deal(CLEAN_DEAL)
        assert len(alerts_by_severity(r, "WARNING")) == 0

    def test_clean_deal_is_ready(self):
        r = audit_deal(CLEAN_DEAL)
        assert r["is_ready_for_ic"] is True

    def test_clean_deal_score_high(self):
        r = audit_deal(CLEAN_DEAL)
        assert r["readiness_score"] >= 90.0


# ---------------------------------------------------------------------------
# 3. Input validation — missing required fields
# ---------------------------------------------------------------------------

class TestInputValidation:
    @pytest.mark.parametrize("field", _REQUIRED_FIELDS)
    def test_missing_field_produces_critical(self, field):
        r = audit_deal(_remove(field))
        crits = [a for a in r["alerts"] if a.severity == "CRITICAL" and field in a.message]
        assert len(crits) >= 1, f"Expected CRITICAL for missing field '{field}'"

    def test_missing_field_blocks_ic_readiness(self):
        r = audit_deal(_remove("purchase_price"))
        assert r["is_ready_for_ic"] is False

    def test_empty_rent_roll_is_critical(self):
        r = audit_deal(_deal(rent_roll=[]))
        crits = [a for a in r["alerts"] if a.severity == "CRITICAL" and "rent_roll" in a.message]
        assert len(crits) >= 1

    def test_non_numeric_field_is_critical(self):
        r = audit_deal(_deal(giy="six percent"))
        crits = [a for a in r["alerts"] if a.severity == "CRITICAL" and "giy" in a.message]
        assert len(crits) >= 1

    def test_multiple_missing_fields(self):
        r = audit_deal({"anchor_tenant": "REWE", "rent_roll": [{"x": 1}]})
        crits = alerts_by_severity(r, "CRITICAL")
        # Should have one CRITICAL for each missing numeric field
        assert len(crits) >= 5

    def test_mentor_note_mentions_stop_when_criticals(self):
        r = audit_deal(_remove("purchase_price"))
        assert "STOP" in r["mentor_note"] or "critical" in r["mentor_note"].lower()


# ---------------------------------------------------------------------------
# 4. Range checks
# ---------------------------------------------------------------------------

class TestRangeChecks:
    def test_giy_too_low(self):
        r = audit_deal(_deal(giy=0.030))  # 3 % < 4 %
        warns = [a for a in r["alerts"] if a.severity == "WARNING" and "GIY" in a.message]
        assert len(warns) >= 1

    def test_giy_too_high(self):
        r = audit_deal(_deal(giy=0.135))  # 13.5 % > 12 %
        warns = [a for a in r["alerts"] if a.severity == "WARNING" and "GIY" in a.message]
        assert len(warns) >= 1

    def test_giy_normal_no_warning(self):
        r = audit_deal(_deal(giy=0.065))  # 6.5 % — normal
        giy_warns = [a for a in r["alerts"] if a.severity == "WARNING" and "GIY" in a.message]
        assert len(giy_warns) == 0

    def test_ltv_above_75_pct(self):
        r = audit_deal(_deal(ltv=0.78))
        warns = [a for a in r["alerts"] if a.severity == "WARNING" and "LTV" in a.message and "75" in a.message]
        assert len(warns) >= 1

    def test_ltv_below_75_no_range_warning(self):
        r = audit_deal(_deal(ltv=0.70))
        range_ltv = [
            a for a in r["alerts"]
            if a.severity == "WARNING" and a.category == "RANGE" and "LTV" in a.message
        ]
        assert len(range_ltv) == 0

    def test_dscr_below_1_2(self):
        r = audit_deal(_deal(dscr=1.10, verdict="INVESTIGATE"))
        warns = [a for a in r["alerts"] if a.severity == "WARNING" and "DSCR" in a.message and "1.20" in a.message]
        assert len(warns) >= 1

    def test_dscr_at_1_2_boundary_no_warning(self):
        r = audit_deal(_deal(dscr=1.20, verdict="INVESTIGATE"))
        range_dscr = [
            a for a in r["alerts"]
            if a.category == "RANGE" and "DSCR" in a.message and "1.20" in a.message
        ]
        assert len(range_dscr) == 0

    def test_wault_below_3_years(self):
        r = audit_deal(_deal(wault=2.0, anchor_covenant="strong"))
        warns = [a for a in r["alerts"] if a.severity == "WARNING" and "WAULT" in a.message]
        assert len(warns) >= 1

    def test_vacancy_above_20_pct(self):
        r = audit_deal(_deal(vacancy_rate=0.22))
        warns = [a for a in r["alerts"] if a.severity == "WARNING" and "acancy" in a.message]
        assert len(warns) >= 1

    def test_irr_below_6_pct(self):
        r = audit_deal(_deal(irr=0.045))
        warns = [a for a in r["alerts"] if a.severity == "WARNING" and "IRR" in a.message]
        assert len(warns) >= 1

    def test_irr_at_6_pct_no_warning(self):
        r = audit_deal(_deal(irr=0.06))
        irr_warns = [a for a in r["alerts"] if a.severity == "WARNING" and "IRR" in a.message]
        assert len(irr_warns) == 0


# ---------------------------------------------------------------------------
# 5. Consistency checks
# ---------------------------------------------------------------------------

class TestConsistencyChecks:
    def test_pass_verdict_with_low_dscr(self):
        """PASS + DSCR < 1.20 → consistency WARNING."""
        r = audit_deal(_deal(verdict="PASS", dscr=1.05))
        warns = [
            a for a in r["alerts"]
            if a.category == "CONSISTENCY" and "PASS" in a.message and "DSCR" in a.message
        ]
        assert len(warns) >= 1

    def test_investigate_verdict_low_dscr_no_consistency_warning(self):
        """INVESTIGATE + DSCR < 1.20 → no Rule-A consistency flag."""
        r = audit_deal(_deal(verdict="INVESTIGATE", dscr=1.05))
        warns = [
            a for a in r["alerts"]
            if a.category == "CONSISTENCY" and "PASS" in a.message
        ]
        assert len(warns) == 0

    def test_combined_ltv_dscr_risk(self):
        """LTV > 70 % AND DSCR < 1.35 → combined-risk WARNING."""
        r = audit_deal(_deal(ltv=0.72, dscr=1.25, verdict="INVESTIGATE"))
        warns = [
            a for a in r["alerts"]
            if a.category == "CONSISTENCY" and "dual-risk" in a.message.lower()
        ]
        assert len(warns) >= 1

    def test_combined_ltv_dscr_not_triggered_when_dscr_ok(self):
        """LTV > 70 % but DSCR ≥ 1.35 → no combined-risk flag."""
        r = audit_deal(_deal(ltv=0.72, dscr=1.40, verdict="INVESTIGATE"))
        warns = [
            a for a in r["alerts"]
            if a.category == "CONSISTENCY" and "dual-risk" in a.message.lower()
        ]
        assert len(warns) == 0

    def test_income_cliff_wault_and_weak_covenant(self):
        """WAULT < 3 yr AND anchor_covenant = 'weak' → income cliff WARNING."""
        r = audit_deal(_deal(wault=1.5, anchor_covenant="weak"))
        warns = [
            a for a in r["alerts"]
            if a.category == "CONSISTENCY" and "income cliff" in a.message.lower()
        ]
        assert len(warns) >= 1

    def test_income_cliff_not_triggered_for_strong_covenant(self):
        """WAULT < 3 yr but strong covenant → no income cliff flag."""
        r = audit_deal(_deal(wault=1.5, anchor_covenant="strong"))
        warns = [
            a for a in r["alerts"]
            if a.category == "CONSISTENCY" and "income cliff" in a.message.lower()
        ]
        assert len(warns) == 0

    def test_income_cliff_not_triggered_when_wault_ok(self):
        """Weak covenant but WAULT ≥ 3 → no income cliff flag."""
        r = audit_deal(_deal(wault=5.0, anchor_covenant="weak"))
        warns = [
            a for a in r["alerts"]
            if a.category == "CONSISTENCY" and "income cliff" in a.message.lower()
        ]
        assert len(warns) == 0


# ---------------------------------------------------------------------------
# 6. Readiness score & IC gate
# ---------------------------------------------------------------------------

class TestReadinessScore:
    def test_score_drops_with_missing_field(self):
        clean_score = audit_deal(CLEAN_DEAL)["readiness_score"]
        missing_score = audit_deal(_remove("irr"))["readiness_score"]
        assert missing_score < clean_score

    def test_score_drops_with_range_warning(self):
        clean_score = audit_deal(CLEAN_DEAL)["readiness_score"]
        bad_score = audit_deal(_deal(ltv=0.80))["readiness_score"]
        assert bad_score < clean_score

    def test_score_drops_with_consistency_warning(self):
        clean_score = audit_deal(CLEAN_DEAL)["readiness_score"]
        bad_score = audit_deal(_deal(ltv=0.72, dscr=1.25, verdict="INVESTIGATE"))["readiness_score"]
        assert bad_score < clean_score

    def test_not_ready_when_critical_present(self):
        r = audit_deal(_remove("dscr"))
        assert r["is_ready_for_ic"] is False

    def test_not_ready_when_score_below_80(self):
        # Force many warnings: bad LTV + bad DSCR + bad IRR + bad vacancy + bad WAULT
        r = audit_deal(_deal(
            ltv=0.80, dscr=1.05, irr=0.04,
            vacancy_rate=0.25, wault=1.0,
            verdict="INVESTIGATE",
            anchor_covenant="weak",
        ))
        assert r["is_ready_for_ic"] is False

    def test_ready_for_ic_requires_no_criticals_and_high_score(self):
        r = audit_deal(CLEAN_DEAL)
        assert r["is_ready_for_ic"] is True
        assert r["readiness_score"] >= 80.0

    def test_score_is_100_for_perfect_deal(self):
        r = audit_deal(CLEAN_DEAL)
        assert r["readiness_score"] == 100.0


# ---------------------------------------------------------------------------
# 7. Mentor note quality
# ---------------------------------------------------------------------------

class TestMentorNote:
    def test_note_is_nonempty(self):
        r = audit_deal(CLEAN_DEAL)
        assert r["mentor_note"].strip() != ""

    def test_note_positive_for_clean_deal(self):
        r = audit_deal(CLEAN_DEAL)
        note_lower = r["mentor_note"].lower()
        # Should say something about being ready
        assert any(word in note_lower for word in ("ready", "consistent", "ic"))

    def test_note_warns_for_bad_deal(self):
        r = audit_deal(_deal(ltv=0.80, dscr=1.05, irr=0.04, verdict="INVESTIGATE"))
        note_lower = r["mentor_note"].lower()
        assert any(word in note_lower for word in ("warning", "issue", "attention", "gap", "rework", "address"))

    def test_note_stop_for_missing_inputs(self):
        r = audit_deal({})
        assert "STOP" in r["mentor_note"] or "critical" in r["mentor_note"].lower()


# ---------------------------------------------------------------------------
# 8. Alert object integrity
# ---------------------------------------------------------------------------

class TestAlertObjects:
    def test_alert_has_severity(self):
        r = audit_deal(_remove("purchase_price"))
        for alert in r["alerts"]:
            assert alert.severity in ("CRITICAL", "WARNING", "INFO")

    def test_alert_has_category(self):
        r = audit_deal(_remove("purchase_price"))
        for alert in r["alerts"]:
            assert alert.category in ("INPUT", "RANGE", "CONSISTENCY")

    def test_alert_has_nonempty_message(self):
        r = audit_deal(_remove("purchase_price"))
        for alert in r["alerts"]:
            assert len(alert.message.strip()) > 0

    def test_alert_is_alert_instance(self):
        r = audit_deal(_remove("purchase_price"))
        for alert in r["alerts"]:
            assert isinstance(alert, Alert)


# ---------------------------------------------------------------------------
# 9. Edge cases
# ---------------------------------------------------------------------------

class TestEdgeCases:
    def test_empty_dict_does_not_crash(self):
        r = audit_deal({})
        assert isinstance(r["readiness_score"], float)
        assert r["is_ready_for_ic"] is False

    def test_extra_keys_are_ignored(self):
        d = _deal(unknown_field="something", another_field=999)
        r = audit_deal(d)
        assert r is not None

    def test_none_values_treated_as_missing(self):
        r = audit_deal(_deal(ltv=None))
        crits = [a for a in r["alerts"] if "ltv" in a.message]
        assert len(crits) >= 1

    def test_zero_vacancy_is_valid(self):
        r = audit_deal(_deal(vacancy_rate=0.0))
        vacancy_warns = [
            a for a in r["alerts"]
            if "acancy" in a.message and a.category == "RANGE"
        ]
        assert len(vacancy_warns) == 0

    def test_anchor_covenant_case_insensitive(self):
        """'WEAK' and 'weak' should both trigger income cliff when WAULT is short."""
        r_lower = audit_deal(_deal(wault=1.5, anchor_covenant="weak"))
        r_upper = audit_deal(_deal(wault=1.5, anchor_covenant="WEAK"))
        cliff_lower = [a for a in r_lower["alerts"] if "income cliff" in a.message.lower()]
        cliff_upper = [a for a in r_upper["alerts"] if "income cliff" in a.message.lower()]
        assert len(cliff_lower) == len(cliff_upper) == 1

    def test_score_never_exceeds_100(self):
        r = audit_deal(CLEAN_DEAL)
        assert r["readiness_score"] <= 100.0

    def test_score_never_below_zero(self):
        # Maximally broken deal
        r = audit_deal({
            "purchase_price": -1,
            "giy": "bad",
            "ltv": 2.0,
            "dscr": 0.1,
            "verdict": "PASS",
        })
        assert r["readiness_score"] >= 0.0
