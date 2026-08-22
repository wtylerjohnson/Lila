"""Report arbiters — independent judges that audit a draft against its FactPack.

The Anthropic critic (report_layers.audit_report) is always the primary judge.
External arbiters from OTHER model providers can be added as cross-checks: a
different model family is unlikely to share the primary's blind spots, which is
the point — an external arbiter against drift.

Dormant by design: the OpenAI arbiter ships OFF. Activation is two lines in .env,
zero code:

    OPENAI_API_KEY=sk-...
    LILA_ENABLE_ARBITER_OPENAI=on

Consensus rule: a draft passes only if EVERY active arbiter passes. Violations are
pooled (union) and feed the same composer revision loop. Per-arbiter results land
in the report's QA trail so you can see who flagged what.

Adding another provider = one subclass + @register_arbiter. Nothing upstream changes.
"""

from __future__ import annotations

import abc
import json
import os
from typing import Optional

from agents.decisions.engine import DecisionEngine
from agents.decisions.report_layers import _CRITIC_SYSTEM, audit_report
from agents.reports.facts import FactPack
from agents.reports.schemas import ReportDraft, SpecificityAudit, SpecViolation
from tools.toggles import is_enabled


class Arbiter(abc.ABC):
    """One independent judge. `default_on` is the state when the env toggle is unset."""

    name: str
    default_on: bool = True

    def available(self) -> bool:
        """Credentials/config present? (Distinct from toggled on.)"""
        return True

    def active(self) -> bool:
        return is_enabled(self.name, self.default_on) and self.available()

    @abc.abstractmethod
    def audit(self, pack: FactPack, draft: ReportDraft) -> SpecificityAudit:
        raise NotImplementedError


class ArbiterRegistry:
    def __init__(self) -> None:
        self._arbiters: dict[str, Arbiter] = {}

    def add(self, arbiter: Arbiter) -> None:
        if arbiter.name in self._arbiters:
            raise ValueError(f"duplicate arbiter name: {arbiter.name!r}")
        self._arbiters[arbiter.name] = arbiter

    def all(self) -> list[Arbiter]:
        return list(self._arbiters.values())

    def active(self) -> list[Arbiter]:
        return [a for a in self._arbiters.values() if a.active()]


ARBITERS = ArbiterRegistry()


def register_arbiter(cls: type[Arbiter]) -> type[Arbiter]:
    ARBITERS.add(cls())
    return cls


# --------------------------------------------------------------------------- #
# External arbiter: OpenAI (dormant until key + toggle)
# --------------------------------------------------------------------------- #
_OPENAI_URL = "https://api.openai.com/v1/chat/completions"


@register_arbiter
class OpenAIArbiter(Arbiter):
    """Second judge from a different model family. OFF until activated in .env."""

    name = "arbiter-openai"
    default_on = False  # ships dormant; flip LILA_ENABLE_ARBITER_OPENAI=on when keyed

    def __init__(self, model: Optional[str] = None) -> None:
        self.model = model or os.environ.get("OPENAI_ARBITER_MODEL", "gpt-4o")

    def available(self) -> bool:
        return bool(os.environ.get("OPENAI_API_KEY"))

    def audit(self, pack: FactPack, draft: ReportDraft) -> SpecificityAudit:
        from tools.api._http import post_json  # shared retry/timeout policy

        schema_hint = json.dumps(SpecificityAudit.model_json_schema(), indent=None)
        body = {
            "model": self.model,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": _CRITIC_SYSTEM
                    + "\nReturn ONLY a JSON object matching this schema (issue must be one of "
                      "unsupported_claim|vague|number_mismatch|missing_citation|drift):\n"
                    + schema_hint},
                {"role": "user", "content": json.dumps({
                    "fact_pack": pack.context(),
                    "draft": draft.model_dump(mode="json"),
                }, default=str)},
            ],
        }
        try:
            resp = post_json(
                _OPENAI_URL, json=body,
                headers={"Authorization": f"Bearer {os.environ['OPENAI_API_KEY']}"},
            )
            content = resp["choices"][0]["message"]["content"]
            return SpecificityAudit.model_validate_json(content)
        except Exception as exc:  # noqa: BLE001 — an arbiter outage must fail LOUD, not pass
            # Scrub any credential an upstream error might echo (some HTTP
            # errors repr the request, headers included) BEFORE it reaches a
            # persisted arbiter note (Cycle 5 review P1). Redact the exact key
            # and any bearer token; keep the exception type so a human still
            # sees what failed.
            import re as _re
            safe = str(exc)
            key = os.environ.get("OPENAI_API_KEY")
            if key:
                safe = safe.replace(key, "***")
            safe = _re.sub(r"(?i)bearer\s+\S+", "Bearer ***", safe)
            safe = _re.sub(r"sk-[A-Za-z0-9_\-]{8,}", "sk-***", safe)
            return SpecificityAudit(
                passed=False,
                violations=[],
                note=f"{self.name} errored ({type(exc).__name__}: "
                     f"{safe[:200]}); treating as non-pass so a human looks.",
            )


# --------------------------------------------------------------------------- #
# Consensus
# --------------------------------------------------------------------------- #
def merge_audits(audits: dict[str, SpecificityAudit]) -> SpecificityAudit:
    """Pass only if every judge passes; pool violations (deduped by quote+issue)."""
    seen: set[tuple[str, str]] = set()
    violations: list[SpecViolation] = []
    notes: list[str] = []
    for name, a in audits.items():
        for v in a.violations:
            key = (v.quote, v.issue.value)
            if key not in seen:
                seen.add(key)
                violations.append(v)
        if a.note:
            notes.append(f"[{name}] {a.note}")
    return SpecificityAudit(
        passed=all(a.passed for a in audits.values()),
        violations=violations,
        note=" ".join(notes),
    )


def run_arbiters(
    pack: FactPack, draft: ReportDraft, engine: DecisionEngine
) -> tuple[SpecificityAudit, dict[str, SpecificityAudit]]:
    """Primary Anthropic critic + every active external arbiter -> merged verdict."""
    audits: dict[str, SpecificityAudit] = {
        "arbiter-anthropic": audit_report(pack, draft, engine)
    }
    for arb in ARBITERS.active():
        audits[arb.name] = arb.audit(pack, draft)
    return merge_audits(audits), audits
