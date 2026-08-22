"""DoD Comptroller machine-readable budget exhibits (PROGRAM-tier).

The Comptroller publishes two current JSON exhibit books alongside the broader
P-1/R-1/C-1 Excel books. This dependency-free adapter ingests those official
JSON grids and records the narrower machine-readable coverage explicitly.
Budget requests are forming demand, never live solicitations.
"""

from __future__ import annotations

import os
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from tools.api._http import get_json
from tools.api.base import DataSource, SourceKind, SourceQuery, register_source
from tools.api.program_cache import ProgramPull, cached_program_pull
from tools.text_match import matching_phrases

INDEX_URL = "https://comptroller.war.gov/Budget-Materials/Budget2027/"
JSON_EXHIBITS = {
    "pacific_deterrence_initiative": (
        "https://comptroller.war.gov/Portals/45/Documents/defbudget/FY2027/"
        "FY2027_Pacific_Deterrence_Initiative.json"
    ),
    "counter_drug_activities": (
        "https://comptroller.war.gov/Portals/45/Documents/defbudget/FY2027/"
        "FY2027_Drug_Interdiction_and_Counter-Drug_Activities.json"
    ),
}
_DEFAULT_CACHE = Path(__file__).resolve().parents[2] / "data" / "cache" / "dod_budget"


def _cache_dir() -> Path:
    return Path(os.environ.get("LILA_DOD_BUDGET_CACHE_DIR", str(_DEFAULT_CACHE)))


def _as_of(raw: str | None, budget_year: str | None) -> str:
    if raw:
        try:
            return datetime.strptime(raw.strip(), "%B %Y").strftime("%Y-%m")
        except ValueError:
            return raw.strip()
    return f"FY{budget_year}" if budget_year else "undated"


def _number(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    cleaned = value.strip().replace(",", "")
    if not cleaned:
        return None
    negative = cleaned.startswith("(") and cleaned.endswith(")")
    if negative:
        cleaned = cleaned[1:-1]
    if not re.fullmatch(r"-?\d+(?:\.\d+)?", cleaned):
        return value.strip()
    parsed: int | float = float(cleaned) if "." in cleaned else int(cleaned)
    return -parsed if negative else parsed


def _grid_records(
    grid: dict,
    *,
    initiative: str,
    canonical_url: str,
    metadata: dict,
    path: list[str],
) -> list[dict]:
    grid_code = str(grid.get("Code") or grid.get("Name") or "grid").strip()
    columns = {
        str(column.get("Code")): {
            "label": str(column.get("Text") or column.get("Code") or "").strip(),
            "type": column.get("Type"),
        }
        for column in (grid.get("Columns") or [])
        if isinstance(column, dict) and column.get("Code")
    }
    out: list[dict] = []
    for position, raw_row in enumerate(grid.get("Rows") or []):
        if not isinstance(raw_row, dict) or raw_row.get("Type") not in (None, "data"):
            continue
        cells: dict[str, Any] = {}
        title: str | None = None
        for cell in raw_row.get("Cells") or []:
            if not isinstance(cell, dict):
                continue
            code = str(cell.get("ColumnCode") or "").strip()
            column = columns.get(code, {"label": code, "type": cell.get("Type")})
            label = column["label"] or code
            value = cell.get("Value")
            cells[label] = _number(value) if column["type"] == "numeric" else value
            if title is None and column["type"] != "numeric" and value:
                title = " ".join(str(value).split())
        row_code = str(raw_row.get("Code") or position).strip()
        exhibit_code = str(metadata.get("_exhibit_code") or "exhibit").strip()
        out.append(
            {
                "record_id": (
                    f"dod-budget:{initiative}:{exhibit_code}:{grid_code}:{row_code}"
                ),
                "canonical_url": canonical_url,
                "kind": "budget",
                "title": title or f"{grid.get('Name') or grid_code} row {row_code}",
                "agency": "Department of Defense",
                "initiative": initiative,
                "budget_year": metadata.get("BudgetYear"),
                "budget_cycle": metadata.get("BudgetCycle"),
                "submission_date": metadata.get("SubmissionDate"),
                "appropriation_number": metadata.get("AppropriationNumber"),
                "exhibit": path[-2] if len(path) > 1 else None,
                "table": grid.get("Name"),
                "table_code": grid_code,
                "values": cells,
                "path": path,
                "data_as_of": _as_of(
                    metadata.get("SubmissionDate"), metadata.get("BudgetYear")
                ),
            }
        )
    return out


def _walk(
    node: Any,
    *,
    initiative: str,
    canonical_url: str,
    inherited_metadata: dict,
    path: list[str],
) -> list[dict]:
    if not isinstance(node, dict):
        return []
    metadata = {**inherited_metadata, **(node.get("Metadata") or {})}
    output = node.get("GeneratedOutput") if isinstance(node.get("GeneratedOutput"), dict) else node
    name = str(output.get("Name") or output.get("Code") or "").strip()
    next_path = [*path, name] if name else path
    if output.get("Type") == "Exhibit":
        metadata = {**metadata, "_exhibit_code": output.get("Code") or name}
    rows: list[dict] = []
    if isinstance(output.get("Rows"), list):
        rows.extend(
            _grid_records(
                output,
                initiative=initiative,
                canonical_url=canonical_url,
                metadata=metadata,
                path=next_path,
            )
        )
    for child in output.get("Children") or []:
        rows.extend(
            _walk(
                child,
                initiative=initiative,
                canonical_url=canonical_url,
                inherited_metadata=metadata,
                path=next_path,
            )
        )
    return rows


def _parse_exhibit(payload: dict, *, initiative: str, canonical_url: str) -> list[dict]:
    if not isinstance(payload, dict):
        raise ValueError("DoD budget exhibit root must be an object")
    rows = _walk(
        payload,
        initiative=initiative,
        canonical_url=canonical_url,
        inherited_metadata={},
        path=[],
    )
    if not rows:
        raise ValueError(f"DoD budget exhibit {initiative} contained no data grids")
    return rows


def _fetch_live() -> ProgramPull:
    records: list[dict] = []
    attempts: list[dict] = []
    for initiative, url in JSON_EXHIBITS.items():
        try:
            records.extend(
                _parse_exhibit(get_json(url), initiative=initiative, canonical_url=url)
            )
            attempts.append({"source": initiative, "status": "success", "url": url})
        except Exception as exc:  # noqa: BLE001 - preserve the other official book
            attempts.append(
                {"source": initiative, "status": "failed", "url": url, "error": str(exc)}
            )
    if not records:
        errors = "; ".join(attempt.get("error", "") for attempt in attempts)
        raise RuntimeError(f"all DoD JSON budget exhibits failed: {errors}")
    data_as_of = max(str(row["data_as_of"]) for row in records)
    return ProgramPull(
        records,
        data_as_of,
        partial=True,
        source_attempts=attempts,
        limitations=(
            "Official JSON coverage is limited to Pacific Deterrence and "
            "counter-drug exhibits; P-1, R-1, C-1, O-1, and M-1 remain separate "
            "Excel books. Budget-request figures are not enacted or obligated funds"
        ),
    )


def _matches(row: dict, query: SourceQuery) -> bool:
    haystack = " ".join(str(value) for value in row.values() if value is not None).lower()
    keywords = [word.strip('"') for word in query.keywords if word.strip('"')]
    agencies = [agency.lower() for agency in query.agencies if agency]
    return (not keywords or bool(matching_phrases(haystack, keywords))) and (
        not agencies or any(agency in haystack for agency in agencies)
    )


def _budget_rank(row: dict) -> tuple:
    values = row.get("values")
    numeric_values = values.values() if isinstance(values, dict) else ()
    largest = max(
        (
            float(value) for value in numeric_values
            if isinstance(value, (int, float))
            and not isinstance(value, bool)
        ),
        default=0.0,
    )
    return (
        -largest,
        str(row.get("title") or "").casefold(),
        str(row.get("record_id") or ""),
    )


@register_source
class DodBudgetExhibitsSource(DataSource):
    name = "dod_budget_exhibits"
    kind = SourceKind.ENRICHMENT

    def healthcheck(self) -> tuple[bool, str]:
        initiative, url = next(iter(JSON_EXHIBITS.items()))
        try:
            rows = _parse_exhibit(get_json(url, timeout=10.0, retries=1), initiative=initiative, canonical_url=url)
            return True, f"official DoD JSON exhibit returned {len(rows)} program lines"
        except Exception as exc:  # noqa: BLE001
            return False, f"DoD JSON budget exhibits unavailable: {exc}"

    def search(self, query: SourceQuery) -> list:
        raise NotImplementedError("PROGRAM-tier enrichment source; use enrich()")

    def enrich(self, query: SourceQuery) -> dict[str, Any]:
        records, provenance = cached_program_pull(
            source=self.name,
            canonical_url=INDEX_URL,
            cache_dir=_cache_dir(),
            fetch_live=_fetch_live,
        )
        candidates = [row for row in records if _matches(row, query)]
        candidates.sort(key=_budget_rank)
        matched = candidates[: query.limit]
        required_origins = set(JSON_EXHIBITS)
        retrieved_origins = sorted({
            str(attempt.get("source") or "")
            for attempt in (provenance.get("source_attempts") or [])
            if isinstance(attempt, dict)
            and attempt.get("status") == "success"
            and attempt.get("source") in required_origins
        })
        return {
            "records": matched,
            "_provenance": {
                **provenance,
                "supported_assets": sorted(required_origins),
                "retrieved_assets": retrieved_origins,
                "records_matched": len(candidates),
                "candidate_total": len(candidates),
                "selected_count": len(matched),
                "truncated": len(matched) < len(candidates),
                "selection_order": "largest reported line value first",
            },
        }
