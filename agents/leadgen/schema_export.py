"""JSON Schema export for the versioned lead-gen contracts.

Checked-in files live in ``agents/leadgen/schemas/``. Regenerate with:

    python -m agents.leadgen.schema_export
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ._base import SCHEMA_VERSION
from .contracts import LeadRow, OpportunityAssessment
from .enums import enum_registry_values
from .motion import BuyingMotion
from .next_action import CurrentNextAction
from .pathway import ActionableExternalPathway
from .seller_path import SellerTransactionPath
from .traces import DecisionTrace

SCHEMA_DIR = Path(__file__).resolve().parent / "schemas"

EXPORT_MODELS = {
    "BuyingMotion": BuyingMotion,
    "OpportunityAssessment": OpportunityAssessment,
    "LeadRow": LeadRow,
    "DecisionTrace": DecisionTrace,
    "ActionableExternalPathway": ActionableExternalPathway,
    "SellerTransactionPath": SellerTransactionPath,
    "CurrentNextAction": CurrentNextAction,
}


def json_schemas() -> dict[str, dict[str, Any]]:
    """Pydantic JSON Schema for every exported contract, plus the enum registry."""

    schemas = {
        name: model.model_json_schema()
        for name, model in EXPORT_MODELS.items()
    }
    schemas["EnumRegistry"] = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "title": "Lead-gen enum registry",
        "schema_version": SCHEMA_VERSION,
        "properties": {
            name: {
                "type": "string",
                "enum": list(values),
            }
            for name, values in enum_registry_values().items()
        },
    }
    return schemas


def render_schema(schema: dict[str, Any]) -> str:
    return json.dumps(schema, indent=2, sort_keys=True, default=str) + "\n"


def write_schemas(directory: Path | None = None) -> list[Path]:
    dest = directory or SCHEMA_DIR
    dest.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for name, schema in json_schemas().items():
        path = dest / f"{_file_stem(name)}.schema.json"
        path.write_text(render_schema(schema), encoding="utf-8")
        written.append(path)
    return written


def _file_stem(name: str) -> str:
    return "".join(
        ch if ch.isalnum() else "_"
        for ch in name
    ).strip("_").lower()


def main() -> None:
    for path in write_schemas():
        print(f"[out] {path}")


if __name__ == "__main__":
    main()
