#!/usr/bin/env python3
"""Max-plan route self-test. Run on the host machine:

    python3 tools/check_maxplan.py

Verifies: CLI present -> signed in -> structured output round-trip works.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pydantic import BaseModel

from agents.decisions import maxplan_cli
from agents.decisions.engine import DecisionEngine


class Ping(BaseModel):
    ok: bool
    word: str


def main() -> int:
    if not maxplan_cli.cli_available():
        print("FAIL: `claude` CLI not on PATH.")
        print("  npm install -g @anthropic-ai/claude-code && claude  (sign in)")
        return 1
    print("1/3  claude CLI found")

    try:
        text = maxplan_cli.run_claude("Reply with exactly: pong", model="sonnet")
    except maxplan_cli.MaxPlanError as e:
        print(f"FAIL at plain call: {e}")
        return 1
    print(f"2/3  plain call ok ({text.strip()[:40]!r})")

    engine = DecisionEngine(model="claude-sonnet-5")
    try:
        result = engine.structure(
            instructions="Return ok=true and word='pong'.",
            findings="ping",
            schema=Ping,
        )
    except Exception as e:
        print(f"FAIL at structured call: {e}")
        return 1
    print(f"3/3  structured output ok ({result})")
    print("\nMax-plan route is live. The pipeline now runs on your subscription.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
