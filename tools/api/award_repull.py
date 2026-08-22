"""Scoped award re-pull: land the board's cited records in the stored sweep.

Golden data closure (2026-07-17): the Signal Board's figure registry cites
exact federal records; this tool fetches each cited USAspending award by
its generated unique id and lands the rows in the stored sweep under
``results["award_repulls"]`` with USASPENDING provenance and the pull
moment's ``retrieved_at``. Figures whose registry rows carry AGENCY_DOC
provenance land as ``results["agency_docs"]`` evidence rows with their
source URL and retrieval moment; they are secondary by policy
(docs/VERIFICATION_POLICY.md) and can never override a primary record.

The write path is the canonical artifact mutation owner
(tools.artifacts.atomic_write_json): validated, fsynced, atomically
replaced, prior bytes retained by content hash under ``.history/``.
Mutating the sweep intentionally staleness-checks every approval bound to
its bytes; evidence drift re-locking downstream gates is the doctrine,
never a side effect to hide.

Zero LLM spend; zero SAM quota (USAspending award reads are unmetered).

    .venv/bin/python -m tools.api.award_repull --client "Insignary" [--dry-run]
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from typing import Any, Callable, Optional

AWARD_ENDPOINT = "https://api.usaspending.gov/api/v2/awards/{gid}/"

_GID_RE = re.compile(r"usaspending\.gov/award/([A-Z0-9_\-]+)", re.I)


def _default_fetch(url: str) -> Any:
    from tools.api._http import get_json
    return get_json(url)


def _place_of_performance(payload: dict) -> dict:
    """Compact, source-native place fields from the award profile response."""

    place = payload.get("place_of_performance") or {}
    return {
        key: place.get(key)
        for key in (
            "city_name", "state_code", "state_name", "country_name",
            "zip5", "foreign_province", "foreign_postal_code",
        )
        if place.get(key) not in (None, "")
    }


def normalize_award(gid: str, payload: dict, *, retrieved_at: str) -> dict:
    """One repull row: the record's own identity and money fields, flat, so
    record-scoped reconciliation finds id and value in one object."""
    pop = payload.get("period_of_performance") or {}
    contract = payload.get("latest_transaction_contract_data") or {}
    parent = payload.get("parent_award") or {}
    return {
        "kind": "award_repull",
        "generated_id": gid,
        "award_id": payload.get("piid") or payload.get("fain") or gid,
        "recipient": (payload.get("recipient") or {}).get("recipient_name"),
        "description": payload.get("description"),
        "award_type": payload.get("type_description"),
        "amount": payload.get("total_obligation"),
        "potential_ceiling": payload.get("base_and_all_options"),
        "start_date": pop.get("start_date"),
        "end_date": pop.get("end_date"),
        "potential_end_date": (pop.get("potential_end_date") or "")[:10] or None,
        "naics": contract.get("naics"),
        "naics_description": contract.get("naics_description"),
        "psc": contract.get("product_or_service_code"),
        "psc_description": contract.get("product_or_service_description"),
        "set_aside_code": contract.get("type_set_aside"),
        "set_aside": contract.get("type_set_aside_description"),
        "competition_code": contract.get("extent_competed"),
        "competition": contract.get("extent_competed_description"),
        "award_structure": contract.get(
            "multiple_or_single_award_description"),
        "solicitation_id": contract.get("solicitation_identifier"),
        "place_of_performance": _place_of_performance(payload),
        "parent_award_id": parent.get("piid"),
        "parent_generated_id": parent.get("generated_unique_award_id"),
        "parent_vehicle_type": (
            parent.get("type_of_idc_description")
            or parent.get("idv_type_description")
        ),
        "parent_award_structure": parent.get(
            "multiple_or_single_aw_desc"),
        "url": f"https://www.usaspending.gov/award/{gid}",
        "source": AWARD_ENDPOINT.format(gid=gid),
        "source_system": "usaspending",
        "retrieved_at": retrieved_at,
    }


def board_award_gids(content) -> list[str]:
    """Every USAspending generated id the board's figure registry cites
    as structured identity, deduplicated in registry order.

    Legacy source URLs remain a read-only compatibility fallback for older
    client packs.  New content never reconstructs identity from a URL.
    """
    seen: dict[str, None] = {}
    for fig in content.figures:
        if str(fig.source_system or "") not in (
                "usaspending", "SourceSystem.USASPENDING"):
            continue
        gid = fig.generated_internal_id
        if not gid:
            match = _GID_RE.search(fig.source_url or "")
            gid = match.group(1).rstrip("/") if match else None
        if gid:
            seen.setdefault(gid, None)
    return list(seen)


def agency_doc_rows(content, *, retrieved_at: str) -> list[dict]:
    """Evidence rows for AGENCY_DOC-tier registry figures (e.g. the SEWP VI
    ordering ceiling). The row records the stated value and its official
    source URL; a note is honest about where the figure is stated. Never
    attested, never primary."""
    rows = []
    for fig in content.figures:
        system = getattr(fig.source_system, "value", fig.source_system)
        if system != "agency_doc" or fig.raw is None:
            continue
        rows.append({
            "kind": "agency_doc",
            "source_record_id": fig.source_record_id
            or re.sub(r"[^a-z0-9]+", "-",
                      (fig.source_url or "agency-doc").lower())[:40],
            "label": f"{fig.text} · program-stated figure",
            "value": fig.raw,
            "url": fig.source_url,
            "source_system": "agency_doc",
            "retrieved_at": retrieved_at,
            "note": ("program-stated figure; the cited agency page was "
                     "retrieved at retrieved_at and claim visibility is "
                     "checked against its fetched page text at release"),
        })
    return rows


def repull_board_awards(
    client_name: str,
    *,
    fetch: Callable[[str], Any] = _default_fetch,
    now: Optional[datetime] = None,
) -> dict:
    """Fetch every registry-cited award record; return the merge payload
    (pure; no disk writes here)."""
    from agents.reports.board_content import load_board_content
    content = load_board_content(client_name)
    if content is None:
        raise FileNotFoundError(
            f"no board content artifact for {client_name!r}; nothing cites "
            f"records to re-pull")
    moment = (now or datetime.now(timezone.utc)).isoformat()
    rows, errors = [], []
    for gid in board_award_gids(content):
        try:
            rows.append(normalize_award(gid, fetch(AWARD_ENDPOINT.format(gid=gid)),
                                        retrieved_at=moment))
        except Exception as e:  # noqa: BLE001 — every record reported, none silent
            errors.append(f"{gid}: {type(e).__name__}: {e}")
    return {"award_repulls": rows,
            "agency_docs": agency_doc_rows(content, retrieved_at=moment),
            "errors": errors, "retrieved_at": moment}


def merge_into_sweep(sweep_path: str, pulled: dict) -> None:
    """Replace the repull lanes in the stored sweep, atomically, history
    retained. The sweep's generated_at is untouched: it describes the
    original sweep; each repull row carries its own retrieved_at."""
    from tools.artifacts import atomic_write_json
    with open(sweep_path, encoding="utf-8") as f:
        sweep = json.load(f)
    results = sweep.setdefault("results", {})
    results["award_repulls"] = pulled["award_repulls"]
    if pulled["agency_docs"]:
        results["agency_docs"] = pulled["agency_docs"]
    atomic_write_json(sweep_path, sweep)


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--client", required=True)
    ap.add_argument("--dry-run", action="store_true",
                    help="fetch and report; write nothing")
    args = ap.parse_args(argv)

    from agents.review import sweep_artifact_path
    sweep_path = sweep_artifact_path(args.client)
    pulled = repull_board_awards(args.client)
    for row in pulled["award_repulls"]:
        print(f"[pull] {row['award_id']:20} {str(row['recipient'])[:30]:30} "
              f"obligated={row['amount']:>16,.2f} "
              f"ceiling={(row['potential_ceiling'] or 0):>16,.2f} "
              f"end={row['end_date']} potential_end={row['potential_end_date']}")
    for row in pulled["agency_docs"]:
        print(f"[doc]  {row['source_record_id']:20} value={row['value']:,.0f} "
              f"({row['url']})")
    for err in pulled["errors"]:
        print(f"[FAIL] {err}", file=sys.stderr)
    if pulled["errors"]:
        print("[repull] refusing to write a partial pull; every cited record "
              "must land or the stored sweep stays untouched", file=sys.stderr)
        return 2
    if args.dry_run:
        print("[repull] dry run; stored sweep untouched")
        return 0
    merge_into_sweep(sweep_path, pulled)
    print(f"[repull] {len(pulled['award_repulls'])} award rows + "
          f"{len(pulled['agency_docs'])} agency-doc rows -> {sweep_path} "
          f"(history retained)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
