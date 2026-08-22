"""Decision-layer tests with a fake Claude client (offline).

Run via pytest OR `python3 tests/test_decisions.py`.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agents.decisions.contextualize import contextualize  # noqa: E402
from agents.decisions.engine import DecisionEngine  # noqa: E402
from agents.decisions.schemas import (  # noqa: E402
    Artifact,
    ArtifactKind,
    DecisionOption,
    DecisionPoint,
    DecisionReport,
)
from tests.fixtures import FIXED_NOW, PROFILE, fused  # noqa: E402

# Reuse the real Assess engine to produce genuine input for the decision layer.
from agents.assess import scoring  # noqa: E402


class _FakeMessages:
    def __init__(self, recorder):
        self._recorder = recorder

    def parse(self, **kwargs):
        self._recorder["kwargs"] = kwargs
        schema = kwargs["output_format"]
        assert schema is DecisionReport
        report = DecisionReport(
            layer="contextualize",
            strategic_summary="Focus on the top match; it clears every dimension.",
            decision_points=[
                DecisionPoint(
                    key="bid_no_bid:OPP-001",
                    question="Bid OPP-001?",
                    options=[
                        DecisionOption(label="Bid", rationale="strong fit", confidence=0.9,
                                       is_recommended=True),
                        DecisionOption(label="No-bid", rationale="capacity", confidence=0.1),
                    ],
                    recommendation="Bid",
                    reasoning="Exact NAICS + full tech coverage per the SOW.",
                    evidence=["https://sam.gov/opp/OPP-001/sow.pdf"],
                )
            ],
            artifacts=[
                Artifact(
                    kind=ArtifactKind.PURSUIT_BRIEF,
                    title="Pursuit brief: OPP-001",
                    body_markdown="# OPP-001\nRecommend bid.",
                    opportunity_id="OPP-001",
                    traceability=["https://sam.gov/opp/OPP-001/sow.pdf"],
                )
            ],
            overall_confidence=0.88,
            citations=["https://sam.gov/opp/OPP-001/sow.pdf"],
            review_gate="Sales lead approves the bid/no-bid call before pursuit.",
        )
        return type("Resp", (), {"parsed_output": report})()


class FakeClient:
    def __init__(self):
        self.recorder = {}
        self.messages = _FakeMessages(self.recorder)


def _engine():
    return DecisionEngine(client=FakeClient())


def test_engine_uses_opus_adaptive_thinking_and_effort():
    eng = _engine()
    eng.deliberate(layer="x", system_prompt="sys", context={"a": 1}, schema=DecisionReport)
    kw = eng.client.recorder["kwargs"]
    assert kw["model"] == "claude-opus-4-8"
    assert kw["thinking"] == {"type": "adaptive"}
    assert kw["output_config"] == {"effort": "high"}
    assert kw["output_format"] is DecisionReport


def test_context_is_serialized_into_user_turn():
    eng = _engine()
    eng.deliberate(layer="contextualize", system_prompt="sys",
                   context={"marker": "ZZZ123"}, schema=DecisionReport)
    user_msg = eng.client.recorder["kwargs"]["messages"][0]["content"]
    assert "ZZZ123" in user_msg
    assert "cite" in user_msg.lower()  # traceability instruction present


def test_generated_at_is_stamped():
    eng = _engine()
    report = eng.deliberate(layer="x", system_prompt="s", context={}, schema=DecisionReport,
                            now=FIXED_NOW)
    assert report.generated_at == FIXED_NOW


def test_contextualize_passes_assessments_and_returns_report():
    # Real Assess output -> decision layer
    assessment = scoring.assess(fused(), PROFILE, now=FIXED_NOW)
    eng = _engine()
    report = contextualize([assessment], client_name=PROFILE.client_name, engine=eng)

    # The assessment's id reached the model context.
    user_msg = eng.client.recorder["kwargs"]["messages"][0]["content"]
    assert "OPP-001" in user_msg
    # System prompt carries the HITL + traceability guardrails.
    sys_prompt = eng.client.recorder["kwargs"]["system"]
    assert "HUMAN-IN-THE-LOOP" in sys_prompt
    assert "TRACEABILITY" in sys_prompt

    assert isinstance(report, DecisionReport)
    assert report.requires_human_review is True
    assert report.review_gate
    assert report.decision_points[0].recommendation == "Bid"
    assert report.artifacts[0].status == "draft"  # never auto-sent


if __name__ == "__main__":
    import traceback

    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = 0
    for fn in fns:
        try:
            fn()
            print(f"PASS {fn.__name__}")
        except Exception:  # noqa: BLE001
            failed += 1
            print(f"FAIL {fn.__name__}")
            traceback.print_exc()
    print(f"\n{len(fns) - failed}/{len(fns)} passed")
    sys.exit(1 if failed else 0)
