"""
deal_supervisor.py  —  GRU Platform: Deal Mentor / Pre-IC Supervisor
=====================================================================
Audits every deal before it is shown to the user.  Acts as a senior
analyst who checks inputs, sanity-ranges, and internal consistency,
then produces a readiness score and mentor recommendation.

Usage
-----
    from deal_supervisor import audit_deal

    result = audit_deal(deal_data)
    # result keys: readiness_score, alerts, mentor_note, is_ready_for_ic

Input dict keys (all optional — missing keys produce CRITICAL alerts)
---------------------------------------------------------------------
    purchase_price  float   EUR
    giy             float   e.g. 0.065 (= 6.5 %)
    niy             float   e.g. 0.058
    wault           float   years
    dscr            float   e.g. 1.35
    ltv             float   e.g. 0.62
    irr             float   e.g. 0.085
    anchor_tenant   str     name of primary anchor, e.g. "REWE"
    anchor_covenant str     "strong" | "medium" | "weak"
    vacancy_rate    float   e.g. 0.08 (= 8 %)
    rent_roll       list    raw rent-roll rows (just needs to be non-empty)
    verdict         str     "PASS" | "INVESTIGATE" | "DECLINE"
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# ---------------------------------------------------------------------------
# Thresholds (mirrored from constants.py where applicable)
# ---------------------------------------------------------------------------
_GIY_WARN_LOW   = 0.04   # < 4 %   → suspicious for DE retail
_GIY_WARN_HIGH  = 0.12   # > 12%   → suspicious for DE retail
_LTV_WARN       = 0.75   # > 75 %  → WARNING
_LTV_COMBINED   = 0.70   # > 70 %  combined-risk check
_DSCR_HARD      = 1.20   # < 1.20x → WARNING (hard bank floor)
_DSCR_COMBINED  = 1.35   # < 1.35x combined-risk check
_WAULT_WARN     = 3.0    # < 3 yr  → WARNING
_VACANCY_WARN   = 0.20   # > 20 %  → WARNING
_IRR_WARN       = 0.06   # < 6 %   → WARNING

# Required fields — each missing one creates a CRITICAL alert
_REQUIRED_FIELDS: list[str] = [
    "purchase_price",
    "giy",
    "niy",
    "wault",
    "dscr",
    "ltv",
    "irr",
    "anchor_tenant",
    "vacancy_rate",
    "rent_roll",
]


# ---------------------------------------------------------------------------
# Alert dataclass
# ---------------------------------------------------------------------------
@dataclass
class Alert:
    severity: str        # "CRITICAL" | "WARNING" | "INFO"
    category: str        # "INPUT" | "RANGE" | "CONSISTENCY"
    message: str

    def __str__(self) -> str:  # pragma: no cover
        return f"[{self.severity}] {self.category}: {self.message}"


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------
def _check_inputs(data: dict[str, Any]) -> list[Alert]:
    """Step 1 — validate that all required fields are present and non-None."""
    alerts: list[Alert] = []
    for field_name in _REQUIRED_FIELDS:
        value = data.get(field_name)
        if value is None:
            alerts.append(Alert(
                severity="CRITICAL",
                category="INPUT",
                message=f"Required field '{field_name}' is missing.",
            ))
        elif field_name == "rent_roll" and not value:
            # empty list is also missing
            alerts.append(Alert(
                severity="CRITICAL",
                category="INPUT",
                message="'rent_roll' is present but empty — no tenants loaded.",
            ))
        elif field_name not in ("anchor_tenant", "rent_roll") and not isinstance(value, (int, float)):
            alerts.append(Alert(
                severity="CRITICAL",
                category="INPUT",
                message=f"Field '{field_name}' has non-numeric value: {value!r}.",
            ))
    return alerts


def _check_ranges(data: dict[str, Any]) -> list[Alert]:
    """Step 2 — flag suspicious values with WARNING."""
    alerts: list[Alert] = []

    giy = data.get("giy")
    if isinstance(giy, (int, float)):
        if giy < _GIY_WARN_LOW:
            alerts.append(Alert(
                severity="WARNING",
                category="RANGE",
                message=(
                    f"GIY {giy:.1%} is below 4 % — unusually low for German retail; "
                    "confirm asking price is not inflated."
                ),
            ))
        elif giy > _GIY_WARN_HIGH:
            alerts.append(Alert(
                severity="WARNING",
                category="RANGE",
                message=(
                    f"GIY {giy:.1%} is above 12 % — very high; check for hidden vacancy, "
                    "structural issues, or distressed pricing."
                ),
            ))

    ltv = data.get("ltv")
    if isinstance(ltv, (int, float)) and ltv > _LTV_WARN:
        alerts.append(Alert(
            severity="WARNING",
            category="RANGE",
            message=(
                f"LTV {ltv:.1%} exceeds 75 % — above typical senior debt ceiling; "
                "lender will likely require equity injection."
            ),
        ))

    dscr = data.get("dscr")
    if isinstance(dscr, (int, float)) and dscr < _DSCR_HARD:
        alerts.append(Alert(
            severity="WARNING",
            category="RANGE",
            message=(
                f"DSCR {dscr:.2f}x is below 1.20x bank floor — "
                "deal cannot service debt at current NOI."
            ),
        ))

    wault = data.get("wault")
    if isinstance(wault, (int, float)) and wault < _WAULT_WARN:
        alerts.append(Alert(
            severity="WARNING",
            category="RANGE",
            message=(
                f"WAULT {wault:.1f} yr is below 3 years — "
                "short income visibility; refinancing and exit will be challenged."
            ),
        ))

    vacancy_rate = data.get("vacancy_rate")
    if isinstance(vacancy_rate, (int, float)) and vacancy_rate > _VACANCY_WARN:
        alerts.append(Alert(
            severity="WARNING",
            category="RANGE",
            message=(
                f"Vacancy {vacancy_rate:.1%} exceeds 20 % — "
                "high structural vacancy; confirm void holding costs are modelled."
            ),
        ))

    irr = data.get("irr")
    if isinstance(irr, (int, float)) and irr < _IRR_WARN:
        alerts.append(Alert(
            severity="WARNING",
            category="RANGE",
            message=(
                f"IRR {irr:.1%} is below 6 % — "
                "thin return over typical German CRE cost of equity; "
                "challenge pricing or assumptions."
            ),
        ))

    return alerts


def _check_consistency(data: dict[str, Any]) -> list[Alert]:
    """Step 3 — flag logical contradictions between metrics."""
    alerts: list[Alert] = []

    verdict = data.get("verdict", "")
    dscr    = data.get("dscr")
    ltv     = data.get("ltv")
    wault   = data.get("wault")
    anchor_covenant = (data.get("anchor_covenant") or "").strip().lower()

    # Rule A: PASS verdict but DSCR below hard floor
    if (
        isinstance(verdict, str)
        and verdict.upper() == "PASS"
        and isinstance(dscr, (int, float))
        and dscr < _DSCR_HARD
    ):
        alerts.append(Alert(
            severity="WARNING",
            category="CONSISTENCY",
            message=(
                f"Verdict is PASS but DSCR is {dscr:.2f}x (< 1.20x). "
                "Verdict and DSCR contradict each other — re-run QA engine."
            ),
        ))

    # Rule B: LTV > 70 % AND DSCR < 1.35x → combined leverage + coverage risk
    if (
        isinstance(ltv, (int, float))
        and isinstance(dscr, (int, float))
        and ltv > _LTV_COMBINED
        and dscr < _DSCR_COMBINED
    ):
        alerts.append(Alert(
            severity="WARNING",
            category="CONSISTENCY",
            message=(
                f"LTV {ltv:.1%} > 70 % combined with DSCR {dscr:.2f}x < 1.35x — "
                "high leverage + thin coverage is a dual-risk flag; most lenders will decline."
            ),
        ))

    # Rule C: WAULT < 3 yr AND anchor covenant is weak → income cliff
    if (
        isinstance(wault, (int, float))
        and wault < _WAULT_WARN
        and anchor_covenant == "weak"
    ):
        alerts.append(Alert(
            severity="WARNING",
            category="CONSISTENCY",
            message=(
                f"WAULT {wault:.1f} yr < 3 years and anchor covenant is rated 'weak' — "
                "income cliff risk: lease expiry and tenant vulnerability coincide."
            ),
        ))

    return alerts


def _score_and_note(
    input_alerts: list[Alert],
    range_alerts: list[Alert],
    consistency_alerts: list[Alert],
    data: dict[str, Any],
) -> tuple[float, str]:
    """
    Step 4 — compute readiness score (0–100 %) and one-line mentor note.

    Scoring logic
    -------------
    Total checks = len(REQUIRED_FIELDS) + 6 range checks + 3 consistency checks.
    Each check either passes (no alert) or fails (has alert).
    Score = passing_checks / total_checks × 100.
    """
    n_input_checks       = len(_REQUIRED_FIELDS)
    n_range_checks       = 6    # GIY, LTV, DSCR, WAULT, vacancy, IRR
    n_consistency_checks = 3    # A, B, C
    total_checks         = n_input_checks + n_range_checks + n_consistency_checks

    # Count unique fields that failed input validation
    input_fails = len([a for a in input_alerts if a.severity == "CRITICAL"])
    range_fails = len(range_alerts)
    consistency_fails = len(consistency_alerts)

    # Cap at total checks so score never goes negative
    failing = min(input_fails + range_fails + consistency_fails, total_checks)
    score = ((total_checks - failing) / total_checks) * 100.0

    # Compose mentor note
    all_alerts = input_alerts + range_alerts + consistency_alerts
    criticals  = [a for a in all_alerts if a.severity == "CRITICAL"]
    warnings   = [a for a in all_alerts if a.severity == "WARNING"]

    if criticals:
        note = (
            f"STOP — {len(criticals)} critical input(s) missing. "
            "Complete the deal sheet before IC review."
        )
    elif score >= 90:
        note = "Deal data is complete and metrics are internally consistent — ready for IC."
    elif score >= 70:
        note = (
            f"{len(warnings)} warning(s) require attention; "
            "address flagged items and re-run before IC submission."
        )
    elif score >= 50:
        note = (
            f"Multiple issues detected ({len(warnings)} warnings). "
            "Significant DD gaps — do not present to IC without resolution."
        )
    else:
        note = (
            "Deal has severe data or metric issues. "
            "Return to origination team for full rework before underwriting."
        )

    return round(score, 1), note


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
def audit_deal(data: dict[str, Any]) -> dict[str, Any]:
    """
    Audit a deal dict and return a supervisor report.

    Parameters
    ----------
    data : dict
        Deal metrics.  See module docstring for expected keys.

    Returns
    -------
    dict with keys:
        readiness_score : float   0–100 (%)
        alerts          : list[Alert]
        mentor_note     : str
        is_ready_for_ic : bool    True when score ≥ 80 and no CRITICALs
    """
    input_alerts       = _check_inputs(data)
    range_alerts       = _check_ranges(data)
    consistency_alerts = _check_consistency(data)

    score, note = _score_and_note(
        input_alerts, range_alerts, consistency_alerts, data
    )

    all_alerts = input_alerts + range_alerts + consistency_alerts
    has_criticals = any(a.severity == "CRITICAL" for a in all_alerts)

    return {
        "readiness_score": score,
        "alerts": all_alerts,
        "mentor_note": note,
        "is_ready_for_ic": (score >= 80.0 and not has_criticals),
    }
