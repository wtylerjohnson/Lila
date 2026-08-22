"""Toggle + arbiter tests: dormancy, activation, consensus, loud failure.

Offline — fake engines, monkeypatched HTTP. No keys, no network.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agents.decisions.arbiters import (  # noqa: E402
    ARBITERS,
    OpenAIArbiter,
    merge_audits,
    run_arbiters,
)
from agents.reports.schemas import (  # noqa: E402
    ReportDraft,
    ReportKind,
    SpecificityAudit,
    SpecIssue,
    SpecViolation,
)
from tools.api.base import REGISTRY, SourceKind  # noqa: E402
from tools.toggles import is_enabled, toggle_key  # noqa: E402


def _draft():
    return ReportDraft(client_name="Acme", kind=ReportKind.TEASER, title="T")


def _pack():
    from agents.reports.facts import FactPack
    from datetime import date
    return FactPack(client_name="Acme", as_of=date(2026, 7, 1))


# ---- toggles ------------------------------------------------------------------ #
def test_toggle_key_normalization():
    assert toggle_key("sam.gov") == "LILA_ENABLE_SAM_GOV"
    assert toggle_key("apollo-handoff") == "LILA_ENABLE_APOLLO_HANDOFF"


def test_toggle_default_and_override(monkeypatch):
    monkeypatch.delenv("LILA_ENABLE_SAM_GOV", raising=False)
    assert is_enabled("sam.gov", True) is True
    monkeypatch.setenv("LILA_ENABLE_SAM_GOV", "off")
    assert is_enabled("sam.gov", True) is False
    monkeypatch.setenv("LILA_ENABLE_SAM_GOV", "on")
    assert is_enabled("sam.gov", True) is True
    monkeypatch.setenv("LILA_ENABLE_SAM_GOV", "garbage")
    assert is_enabled("sam.gov", True) is True  # unparseable -> default


def test_source_registry_respects_toggle(monkeypatch):
    names = {s.name for s in REGISTRY.enabled(SourceKind.DISCOVERY)}
    assert "sam.gov" in names
    monkeypatch.setenv("LILA_ENABLE_SAM_GOV", "off")
    names = {s.name for s in REGISTRY.enabled(SourceKind.DISCOVERY)}
    assert "sam.gov" not in names


# ---- OpenAI arbiter dormancy / activation --------------------------------------- #
def test_openai_arbiter_registered_but_dormant(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("LILA_ENABLE_ARBITER_OPENAI", raising=False)
    assert "arbiter-openai" in [a.name for a in ARBITERS.all()]
    assert [a.name for a in ARBITERS.active()] == []  # off by default, no key


def test_openai_arbiter_needs_both_key_and_toggle(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.delenv("LILA_ENABLE_ARBITER_OPENAI", raising=False)
    assert ARBITERS.active() == []  # key alone is not activation
    monkeypatch.setenv("LILA_ENABLE_ARBITER_OPENAI", "on")
    assert [a.name for a in ARBITERS.active()] == ["arbiter-openai"]
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    assert ARBITERS.active() == []  # toggle alone is not activation either


def test_openai_arbiter_error_fails_loud_not_silent(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")

    def boom(url, *, json, headers=None, **kw):
        raise RuntimeError("openai down")

    import tools.api._http as http_mod
    monkeypatch.setattr(http_mod, "post_json", boom)
    audit = OpenAIArbiter().audit(_pack(), _draft())
    assert audit.passed is False
    assert "openai down" in audit.note


def test_openai_arbiter_parses_valid_response(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    payload = SpecificityAudit(
        passed=False,
        violations=[SpecViolation(section="Market size", quote="huge spend",
                                  issue=SpecIssue.VAGUE, fix="use the $ figure from F1")],
    ).model_dump_json()

    def fake(url, *, json, headers=None, **kw):
        assert headers["Authorization"] == "Bearer sk-test"
        return {"choices": [{"message": {"content": payload}}]}

    import tools.api._http as http_mod
    monkeypatch.setattr(http_mod, "post_json", fake)
    audit = OpenAIArbiter().audit(_pack(), _draft())
    assert audit.passed is False
    assert audit.violations[0].issue == SpecIssue.VAGUE


# ---- consensus ------------------------------------------------------------------- #
def test_merge_audits_any_fail_fails_and_dedups():
    v = SpecViolation(section="s", quote="q", issue=SpecIssue.DRIFT, fix="f")
    merged = merge_audits({
        "a": SpecificityAudit(passed=True, violations=[v]),
        "b": SpecificityAudit(passed=False, violations=[v], note="dup flagged"),
    })
    assert merged.passed is False
    assert len(merged.violations) == 1  # same quote+issue pooled once
    assert "[b]" in merged.note


def test_run_arbiters_primary_only_when_external_dormant(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("LILA_ENABLE_ARBITER_OPENAI", raising=False)

    class FakeEngine:
        def deliberate(self, *, layer, system_prompt, context, schema):
            assert layer == "report_audit"
            return SpecificityAudit(passed=True)

    merged, per = run_arbiters(_pack(), _draft(), FakeEngine())
    assert merged.passed is True
    assert list(per) == ["arbiter-anthropic"]
