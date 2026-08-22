"""Apollo handoff finder — packages an approved ContactPlan for the Apollo MCP.

The Apollo REST API has been unreliable, so instead of calling it directly this
finder writes data/handoff/<client>.apollo.json: a self-describing spec that a
Cowork session (with the Apollo MCP connected) can execute verbatim — one
apollo_mixed_people_api_search per entry in `searches`, with known POCs carried
alongside so nothing already verified gets re-searched.

Swap-in path for a live integration later: implement another ContactFinder (e.g.
ApolloApiFinder) against the same ContactPlan input; nothing upstream changes.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone

from agents.decisions.schemas import ContactPlan, ContactSearchSpec
from tools.crm.base import ContactFinder, register_finder

HANDOFF_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "data",
    "handoff",
)

_INSTRUCTIONS = (
    "Execute in a Cowork session with the Apollo MCP connected: for each entry in "
    "'searches', run an Apollo people search (apollo_mixed_people_api_search) using "
    "person_titles + organization (domain preferred, name otherwise) + seniorities/"
    "locations when present. Do NOT re-search 'known_pocs' — they came verified from "
    "the source notices; enrich them only if an email/phone is missing. Return results "
    "grouped by related_opportunity_ids with each search's rationale attached."
)


def _slug(name: str) -> str:
    return name.lower().replace(" ", "_")


def _search_payload(spec: ContactSearchSpec) -> dict:
    """Shape one search the way an Apollo people search expects its filters."""
    payload: dict = {
        "person_titles": spec.person_titles,
        "organization_names": [spec.org_name],
        "org_type": spec.org_type.value,
        "rationale": spec.rationale,
        "related_opportunity_ids": spec.related_opportunity_ids,
    }
    if spec.domain:
        payload["q_organization_domains"] = [spec.domain]
    if spec.seniorities:
        payload["person_seniorities"] = spec.seniorities
    if spec.locations:
        payload["person_locations"] = spec.locations
    return payload


@register_finder
class ApolloHandoffFinder(ContactFinder):
    name = "apollo-handoff"

    def __init__(self, out_dir: str | None = None) -> None:
        self.out_dir = out_dir or HANDOFF_DIR

    def deliver(self, plan: ContactPlan) -> str:
        os.makedirs(self.out_dir, exist_ok=True)
        doc = {
            "kind": "apollo_handoff",
            "version": 1,
            "client_name": plan.client_name,
            "generated_at": (plan.generated_at or datetime.now(timezone.utc)).isoformat(),
            "instructions": _INSTRUCTIONS,
            "strategy_note": plan.strategy_note,
            "known_pocs": [p.model_dump(mode="json") for p in plan.known_pocs],
            "searches": [_search_payload(s) for s in plan.searches],
            "citations": plan.citations,
        }
        path = os.path.join(self.out_dir, f"{_slug(plan.client_name)}.apollo.json")
        with open(path, "w") as f:
            json.dump(doc, f, indent=2)
        return path
