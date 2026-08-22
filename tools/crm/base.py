"""Pluggable contact-finder layer (mirrors tools/api/base.py for data sources).

A ContactFinder takes an approved ContactPlan and either executes it (a live CRM
API) or packages it for execution elsewhere (a handoff file). Adding a new method —
Apollo REST, a different provider, a browser agent — means one adapter class with
@register_finder; the Target engine never changes.

Current finders:
  - apollo-handoff : writes a Cowork-ready handoff JSON (tools/crm/apollo_handoff.py)
"""

from __future__ import annotations

import abc

from agents.decisions.schemas import ContactPlan


class ContactFinder(abc.ABC):
    """Base class for every contact-finding method."""

    #: unique key, e.g. "apollo-handoff" — used in registry lookup
    name: str
    #: flip to False to keep a finder registered but out of the default path
    enabled: bool = True

    @abc.abstractmethod
    def deliver(self, plan: ContactPlan) -> str:
        """Execute or hand off the plan. Returns a locator for the result
        (a file path for handoffs, a batch/job id for live APIs)."""
        raise NotImplementedError


class FinderRegistry:
    def __init__(self) -> None:
        self._finders: dict[str, ContactFinder] = {}

    def add(self, finder: ContactFinder) -> None:
        if finder.name in self._finders:
            raise ValueError(f"duplicate finder name: {finder.name!r}")
        self._finders[finder.name] = finder

    def get(self, name: str) -> ContactFinder:
        return self._finders[name]

    def all(self) -> list[ContactFinder]:
        return list(self._finders.values())

    def enabled(self) -> list[ContactFinder]:
        from tools.toggles import is_enabled  # env switch: LILA_ENABLE_<NAME>

        return [f for f in self._finders.values() if f.enabled and is_enabled(f.name, True)]


#: process-wide registry; finders register at import time
FINDERS = FinderRegistry()


def register_finder(cls: type[ContactFinder]) -> type[ContactFinder]:
    """Class decorator: instantiate the finder and add it to FINDERS."""
    FINDERS.add(cls())
    return cls
