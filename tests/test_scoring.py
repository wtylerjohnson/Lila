"""Phase 3 scoring tests. Run via pytest OR `python3 tests/test_scoring.py`."""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agents.assess import assess_agent  # noqa: E402
from agents.assess.scoring import aggregate, assess, score_signals  # noqa: E402
from tests.fixtures import FIXED_NOW, PROFILE, fused, raw  # noqa: E402


def _by_dim(signals):
    return {s.dimension: s for s in signals}


def test_strong_match_scores_high():
    result = assess(fused(), PROFILE, now=FIXED_NOW)
    assert result.match_score >= 0.9


def test_golden_score_is_stable():
    # Guards against silent scoring drift. If you change weights/logic intentionally,
    # update this number in the same commit.
    result = assess(fused(), PROFILE, now=FIXED_NOW)
    assert result.match_score == 1.0


def test_signals_cover_all_dimensions():
    dims = set(_by_dim(score_signals(fused(), PROFILE)))
    assert dims == {"naics", "set_aside", "tech", "past_perf", "value_fit", "agency_fit"}


def test_exact_naics_full_credit():
    sig = _by_dim(score_signals(fused(), PROFILE))["naics"]
    assert sig.score == 1.0


def test_adjacent_naics_half_credit():
    sig = _by_dim(score_signals(fused(raw(naics_code="541511")), PROFILE))["naics"]
    assert sig.score == 0.5


def test_ineligible_set_aside_zero():
    sig = _by_dim(score_signals(fused(raw(set_aside="WOSB")), PROFILE))["set_aside"]
    assert sig.score == 0.0


def test_tech_coverage_partial():
    # Document mentions none of the tech stack -> zero coverage
    f = fused(doc_text="Generic janitorial services for a federal building.")
    sig = _by_dim(score_signals(f, PROFILE))["tech"]
    assert sig.score == 0.0


def test_why_this_match_cites_evidence():
    result = assess(fused(), PROFILE, now=FIXED_NOW)
    assert "NAICS" in result.why_this_match
    assert result.why_this_match  # non-empty


def test_traceability_carries_provenance():
    result = assess(fused(), PROFILE, now=FIXED_NOW)
    assert any("sow.pdf" in t for t in result.traceability)


def test_weak_match_message():
    f = fused(
        raw(naics_code="111110", psc_code=None, set_aside="WOSB",
            agency="Dept of Agriculture", estimated_value=10_000.0),
        doc_text="Unrelated scope.",
    )
    result = assess(f, PROFILE, now=FIXED_NOW)
    assert result.match_score < 0.3
    assert "Weak match" in result.why_this_match


def test_aggregate_weighted_mean():
    signals = score_signals(fused(), PROFILE)
    assert 0.0 <= aggregate(signals) <= 1.0


def test_batch_rank_and_threshold():
    strong = fused()
    weak = fused(
        raw(source_id="WEAK", naics_code="111110", psc_code=None, set_aside="WOSB",
            agency="Dept of Agriculture", estimated_value=10_000.0),
        doc_text="Unrelated.",
    )
    ranked = assess_agent.assess_fused_batch([weak, strong], PROFILE, threshold=0.6, now=FIXED_NOW)
    assert [r.opportunity_id for r in ranked] == ["OPP-001"]  # weak dropped


def test_flagged_opp_retained_below_threshold():
    weak_flagged = fused(
        raw(source_id="FLAG", naics_code="111110", psc_code=None, set_aside="WOSB",
            agency="Dept of Agriculture", estimated_value=10_000.0),
        doc_text="Unrelated.",
        requires_review=True,
    )
    ranked = assess_agent.assess_fused_batch([weak_flagged], PROFILE, threshold=0.6, now=FIXED_NOW)
    assert [r.opportunity_id for r in ranked] == ["FLAG"]  # kept for human review


if __name__ == "__main__":
    import traceback

    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = 0
    for fn in fns:
        try:
            fn()
            print(f"PASS {fn.__name__}")
        except Exception:  # noqa: BLE001
            failed += 1
            print(f"FAIL {fn.__name__}")
            traceback.print_exc()
    print(f"\n{len(fns) - failed}/{len(fns)} passed")
    sys.exit(1 if failed else 0)
