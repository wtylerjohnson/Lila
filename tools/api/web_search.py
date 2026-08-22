"""Bounded public-web research lane using Claude's web-search tool.

Given the approved keywords/strategy, Claude searches the open web for federal
opportunity signals the structured APIs miss: agency forecasts, RFIs, industry days,
expiring incumbent contracts, trade-press intel. Results are normalized to
RawOpportunity (source="web") so they flow through the same downstream as SAM/USAspending.

Reuses ANTHROPIC_API_KEY (no separate search-API key). To swap in a dedicated search
API later, replace the engine call with that client — the adapter contract is unchanged.
"""

from __future__ import annotations

import hashlib
from typing import Optional

from agents.decisions.engine import DecisionEngine
from agents.decisions.schemas import WebLeadList
from agents.schemas import RawOpportunity
from tools.api.base import DataSource, SourceKind, SourceQuery, register_source

_RESEARCH_SYSTEM = """\
You are a federal-market researcher. Using web search, find PUBLIC, current signals of
upcoming or active federal contracting opportunities that match the given terms:
agency procurement forecasts, RFIs/sources-sought, industry days, recompetes/expiring
incumbent contracts, and relevant trade-press. Prefer .gov and primary sources. For
each finding give the title, URL, a one-line summary, and the signal type. Be specific
and cite real URLs — do not fabricate.
"""

_STRUCTURE_SYSTEM = """\
Convert the research findings into the structured schema. Keep only items with a real
URL. signal_type is a short label (RFI, forecast, industry day, recompete, news).
"""


def _to_raw(lead) -> RawOpportunity:
    sid = "web-" + hashlib.sha1(lead.url.encode("utf-8")).hexdigest()[:16]
    return RawOpportunity(
        source="web",
        source_id=sid,
        title=lead.title,
        api_url=lead.url,
        raw_payload=lead.model_dump(mode="json"),
    )


@register_source
class WebSearchSource(DataSource):
    name = "web"
    kind = SourceKind.DISCOVERY

    def healthcheck(self) -> tuple[bool, str]:
        """Max-plan route needs the claude CLI; API route needs ANTHROPIC_API_KEY."""
        import os as _os
        import shutil as _shutil
        route = _os.environ.get("LILA_LLM_ROUTE", "max").strip().lower()
        if route == "max":
            if _shutil.which("claude"):
                return True, "Max-plan route: claude CLI on PATH"
            return False, "claude CLI not found (npm install -g @anthropic-ai/claude-code)"
        if _os.environ.get("ANTHROPIC_API_KEY"):
            return True, "API route: ANTHROPIC_API_KEY set"
        return False, "API route selected but ANTHROPIC_API_KEY missing"

    def __init__(self, engine: Optional[DecisionEngine] = None) -> None:
        self._engine = engine

    @property
    def engine(self) -> DecisionEngine:
        if self._engine is None:
            from agents.decisions.engine import research_engine

            self._engine = research_engine()  # fast model: research, not deliberation

        return self._engine

    def search(self, query: SourceQuery) -> list[RawOpportunity]:
        terms = list(query.keywords) + list(query.naics_codes)
        if query.agencies:
            terms += query.agencies
        prompt = (
            "Find federal opportunity signals for these terms: "
            + ", ".join(t for t in terms if t)
            + ". Return concrete, recent, public results."
        )
        findings, citations = self.engine.web_research(
            system_prompt=_RESEARCH_SYSTEM, query=prompt
        )
        if not findings:
            return []
        leads = self.engine.structure(
            instructions=_STRUCTURE_SYSTEM, findings=findings, schema=WebLeadList
        )
        return [_to_raw(ld) for ld in leads.leads[: query.limit]]
