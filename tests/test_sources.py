"""Pluggable source-layer tests. Run via pytest OR `python3 tests/test_sources.py`."""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agents.schemas import RawOpportunity  # noqa: E402
from tools.api import REGISTRY, SourceKind  # noqa: E402
from tools.api.base import (  # noqa: E402
    DataSource,
    SourceQuery,
    SourceRegistry,
    discover,
)


def test_bundled_adapters_register():
    names = {s.name for s in REGISTRY.all()}
    assert {"sam.gov", "usaspending.gov"} <= names


def test_default_sweep_adapters_reach_the_command_center_health_registry():
    """Direct runner imports must not make a live lane invisible in the rack."""
    names = {source.name for source in REGISTRY.all()}
    assert {
        "dod_contracts",
        "budget_pressure",
        "congress_gov",
        "watchdogs",
    } <= names


def test_sam_is_discovery_usaspending_is_enrichment():
    assert REGISTRY.get("sam.gov").kind == SourceKind.DISCOVERY
    assert REGISTRY.get("usaspending.gov").kind == SourceKind.ENRICHMENT


def test_enabled_filters_by_kind():
    discovery = REGISTRY.enabled(SourceKind.DISCOVERY)
    assert all(s.kind == SourceKind.DISCOVERY for s in discovery)
    assert "usaspending.gov" not in {s.name for s in discovery}


def test_duplicate_registration_rejected():
    reg = SourceRegistry()

    class A(DataSource):
        name = "dup"
        kind = SourceKind.DISCOVERY

        def search(self, query):
            return []

    reg.add(A())
    try:
        reg.add(A())
    except ValueError:
        return
    raise AssertionError("expected ValueError on duplicate name")


def test_discover_fans_out_and_dedupes():
    """A new source plugs in with no engine changes; discover() merges + dedupes."""
    reg = SourceRegistry()

    def _opp(src, sid):
        return RawOpportunity(source=src, source_id=sid, title="t")

    class SrcA(DataSource):
        name = "a"
        kind = SourceKind.DISCOVERY

        def search(self, query):
            return [_opp("a", "1"), _opp("a", "1")]  # internal dup

    class SrcB(DataSource):
        name = "b"
        kind = SourceKind.DISCOVERY
        enabled = False  # disabled -> excluded from fan-out

        def search(self, query):
            return [_opp("b", "1")]

    reg.add(SrcA())
    reg.add(SrcB())
    out = discover(SourceQuery(naics_codes=["541512"]), registry=reg)
    assert [(o.source, o.source_id) for o in out] == [("a", "1")]


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
