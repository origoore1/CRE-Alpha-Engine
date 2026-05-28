"""
Regression test suite — 12 practice deals.
Locks in expected metric outputs for all deals.
Run: pytest tests/test_regression.py -v

Verdict: exact match.
Ratio metrics (dscr, icr, ltv, giy, niy, anchor_pct, vacancy): ±0.01 absolute.
NOI / gross_rent: ±0.5% relative.
WAULT: ±0.01 absolute.
"""
import io
import json
import pathlib
import importlib.util
import pytest

# ── load engine ──────────────────────────────────────────────────────────────
ROOT = pathlib.Path(__file__).parent.parent

spec = importlib.util.spec_from_file_location(
    "rent_roll_parser_7", ROOT / "rent_roll_parser 7.py"
)
rrp = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rrp)

# ── load practice deals generator ────────────────────────────────────────────
spec2 = importlib.util.spec_from_file_location(
    "practice_deals7", ROOT / "practice_deals7.py"
)
pd7 = importlib.util.module_from_spec(spec2)
spec2.loader.exec_module(pd7)

# ── load baselines ────────────────────────────────────────────────────────────
BASELINES = json.loads(
    (pathlib.Path(__file__).parent / "regression_baselines.json").read_text()
)

RATIO_TOL = 0.01        # ±0.01 absolute for ratios
PCT_TOL   = 0.005       # ±0.5% relative for EUR amounts


def _run_deal(deal_config: dict) -> dict:
    """Generate Excel, run engine, return metrics + verdict."""
    import tempfile, os
    with tempfile.TemporaryDirectory() as tmp:
        path = pd7.generate_practice_deal(deal_config, tmp)
        with open(path, "rb") as f:
            data = io.BytesIO(f.read())
        deal  = rrp.load_deal_overview(data)
        rr    = rrp.load_rent_roll(data)
        act   = rr[~rr["is_vacant"]]
        vac   = rr[rr["is_vacant"]]
        costs = rrp.load_cost_sheet(
            data,
            rr_gross_rent  = float(act["annual_rent"].sum()),
            rr_gla_total   = float(rr["area_sqm"].sum()),
            rr_vacant_area = float(vac["area_sqm"].sum()),
        )
        m  = rrp.compute_metrics(deal, rr, costs)
        qa = rrp.run_qa_audit(m, deal)
        v  = rrp.generate_verdict(m, qa)
        return {
            "verdict":    v["recommendation"],
            "dscr":       m.get("dscr") or 0.0,
            "icr":        m.get("icr")  or 0.0,
            "ltv":        m.get("ltv")  or 0.0,
            "giy":        m.get("giy")  or 0.0,
            "niy":        m.get("niy")  or 0.0,
            "noi":        m.get("noi_proxy") or m.get("noi") or 0.0,
            "wault":      m.get("wault") or 0.0,
            "anchor_pct": m.get("anchor_pct") or 0.0,
            "vacancy":    m.get("vacancy_rate") or 0.0,
            "gross_rent": m.get("gross_passing_rent") or 0.0,
        }


# ── parametrise over all 12 deals ─────────────────────────────────────────────
@pytest.mark.parametrize(
    "deal_config",
    pd7.PRACTICE_DEALS,
    ids=[f"deal_{d['id']:02d}" for d in pd7.PRACTICE_DEALS],
)
def test_deal_regression(deal_config):
    did = str(deal_config["id"])
    baseline = BASELINES[did]
    actual   = _run_deal(deal_config)

    # Verdict — exact match
    assert actual["verdict"] == baseline["verdict"], (
        f"Deal {did}: verdict changed {baseline['verdict']} → {actual['verdict']}"
    )

    # Ratio metrics — ±0.01 absolute
    for key in ("dscr", "icr", "ltv", "giy", "niy", "anchor_pct", "vacancy"):
        base_v = baseline[key]
        act_v  = actual[key]
        assert abs(act_v - base_v) <= RATIO_TOL, (
            f"Deal {did} {key}: baseline={base_v:.4f}, actual={act_v:.4f}, "
            f"delta={abs(act_v - base_v):.4f} > {RATIO_TOL}"
        )

    # WAULT — ±0.01 absolute
    assert abs(actual["wault"] - baseline["wault"]) <= RATIO_TOL, (
        f"Deal {did} wault: baseline={baseline['wault']:.3f}, "
        f"actual={actual['wault']:.3f}"
    )

    # EUR amounts — ±0.5% relative
    for key in ("noi", "gross_rent"):
        base_v = baseline[key]
        act_v  = actual[key]
        if base_v == 0:
            assert act_v == 0, f"Deal {did} {key}: baseline=0, actual={act_v}"
        else:
            rel_err = abs(act_v - base_v) / abs(base_v)
            assert rel_err <= PCT_TOL, (
                f"Deal {did} {key}: baseline={base_v:.2f}, actual={act_v:.2f}, "
                f"relative_err={rel_err:.4%} > {PCT_TOL:.1%}"
            )
