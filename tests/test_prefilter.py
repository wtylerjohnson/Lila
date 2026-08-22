"""Phase 1 pre-filter tests. Run via pytest OR `python3 tests/test_prefilter.py`."""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agents.assess.prefilter import passes_prefilter, prefilter  # noqa: E402
from tests.fixtures import PROFILE, raw  # noqa: E402


def test_perfect_candidate_passes():
    assert passes_prefilter(raw(), PROFILE) is True


def test_wrong_naics_family_dropped():
    assert passes_prefilter(raw(naics_code="111110", psc_code=None), PROFILE) is False


def test_adjacent_naics_family_passes():
    # 5415xx family is in profile via 541512/541519
    assert passes_prefilter(raw(naics_code="541511", psc_code=None), PROFILE) is True


def test_value_below_band_dropped():
    assert passes_prefilter(raw(estimated_value=10_000.0), PROFILE) is False


def test_value_above_band_dropped():
    assert passes_prefilter(raw(estimated_value=99_000_000.0), PROFILE) is False


def test_ineligible_set_aside_dropped():
    assert passes_prefilter(raw(set_aside="WOSB"), PROFILE) is False


def test_full_and_open_passes():
    assert passes_prefilter(raw(set_aside=None), PROFILE) is True


def test_unknown_value_passes_inclusively():
    assert passes_prefilter(raw(estimated_value=None), PROFILE) is True


def test_prefilter_filters_batch():
    opps = [raw(), raw(source_id="X", set_aside="WOSB")]
    kept = prefilter(opps, PROFILE)
    assert [o.source_id for o in kept] == ["OPP-001"]


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
