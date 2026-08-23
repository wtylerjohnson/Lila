"""Structural proof that migrated consumers do not bypass the graph boundary."""

from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MIGRATED_CONSUMERS = (
    ROOT / "agents/golden_press/market_map_projection.py",
    ROOT / "agents/golden_press/press.py",
    ROOT / "agents/golden_press/render.py",
    ROOT / "agents/golden_press/validate.py",
)
ADAPTER_MODULE = "tools.intelligence_graph.adapter"


def _imports(path: Path) -> tuple[set[str], set[str]]:
    modules: set[str] = set()
    imported_names: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.ImportFrom):
            modules.add(node.module or "")
            imported_names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
    return modules, imported_names


def test_every_migrated_consumer_uses_the_shared_graph_interface():
    failures = []
    for path in MIGRATED_CONSUMERS:
        modules, names = _imports(path)
        source = path.read_text(encoding="utf-8")
        if "targets_store" in modules or "targets_store." in source:
            failures.append(f"{path.name}: direct targets_store bypass")
        if ADAPTER_MODULE not in modules:
            failures.append(f"{path.name}: graph adapter not imported")
        if not names.intersection({
            "load_target_observations",
            "admissible_target_observations",
            "target_field_provenance",
            "write_target_sequence_csv",
        }):
            failures.append(f"{path.name}: no graph-backed target interface")
    assert failures == []


def test_legacy_target_store_is_only_reached_by_the_adapter():
    production = ROOT / "agents/golden_press"
    bypasses = []
    for path in production.glob("*.py"):
        if path.name == "targets_store.py":
            continue
        modules, names = _imports(path)
        directly_imports_target_store = (
            "agents.golden_press.targets_store" in modules
            or (
                "agents.golden_press" in modules
                and "targets_store" in names
            )
        )
        if directly_imports_target_store:
            bypasses.append(path.name)
    assert bypasses == []
