"""Operator lock for LILA's sole external product slot contract."""

from __future__ import annotations

from pathlib import Path

import pytest

from agents.golden_press import external_product_contract as contract


EXPECTED_SLOTS = (
    (1, "priority-pursuits", "Highest Priority Pursuits"),
    (2, "research-mesh",
     "NAICS + Keywords + Semantic (Research Mesh Deployment/Search)"),
    (3, "agency-spending", "Agency Spending (in your category)"),
    (4, "competitors",
     "Competitors (Identified) + Competitor Awards + Spending/Landscape"),
    (5, "federal-opportunities",
     "Federal Opportunities Identified + Targets Specific to Each Opportunity "
     "(listed on notice and then complemented with search and enrichment)"),
    (6, "teaming-opportunities", "Teaming Opportunities + Teaming Targets"),
    (7, "future-forecasts",
     "Future Forecasts (Agency + Budgets + News, a LILA strength), including "
     "directional graphs and vector maps"),
    (8, "industry-days-events", "Industry Days + Events"),
)


def test_operator_locked_external_slot_order_is_exact():
    slots = contract.load_external_product_slots()
    assert tuple(
        (slot.number, slot.slot_id, slot.heading)
        for slot in slots
    ) == EXPECTED_SLOTS
    assert contract.external_product_slot_ids() == tuple(
        slot_id for _, slot_id, _ in EXPECTED_SLOTS
    )


@pytest.mark.parametrize(
    "mutation",
    (
        lambda text: text.replace(
            "| 1 | `priority-pursuits` | Highest Priority Pursuits |\n", ""),
        lambda text: text.replace("| 1 | `priority-pursuits`", "| 2 | `priority-pursuits`"),
        lambda text: text.replace("Highest Priority Pursuits", "Priority Pursuits"),
        lambda text: text.replace(
            "| 1 | `priority-pursuits` | Highest Priority Pursuits |",
            "| 1 | `future-forecasts` | Future Forecasts |",
        ),
    ),
)
def test_add_remove_rename_or_reorder_fails_the_operator_lock(
        tmp_path: Path, monkeypatch, mutation):
    changed = tmp_path / "MARKET_MAP_CONTRACT.md"
    original = contract.CONTRACT_PATH.read_text(encoding="utf-8")
    changed.write_text(mutation(original), encoding="utf-8")
    monkeypatch.setattr(contract, "CONTRACT_PATH", changed)
    with pytest.raises(contract.ExternalProductContractError):
        contract.load_external_product_slots()


def test_competing_contracts_are_explicitly_internal():
    root = Path(__file__).resolve().parents[1]
    report = (root / "docs" / "REPORT_CONTRACT.md").read_text(encoding="utf-8")
    signals = (root / "docs" / "opportunity_signals_format.md").read_text(
        encoding="utf-8"
    )
    assert "internal compatibility contract" in report.splitlines()[1]
    assert "internal signal-board format" in signals.splitlines()[0]
    assert "sole external product contract" in report
    assert "sole external product contract" in signals


def test_public_readme_uses_the_locked_slots_in_order():
    root = Path(__file__).resolve().parents[1]
    readme = (root / "README.md").read_text(encoding="utf-8")
    positions = [readme.index(heading) for _, _, heading in EXPECTED_SLOTS]
    assert positions == sorted(positions)
    assert "eight permanent slots" in readme


def test_slot_schema_is_fixed_but_client_content_is_variable():
    text = contract.CONTRACT_PATH.read_text(encoding="utf-8")
    assert "The eight slots are the permanent product frame" in text
    assert "Evidence changes the contents of a slot, never the slot schema" in text
    assert "A slot with no qualified content" in text
