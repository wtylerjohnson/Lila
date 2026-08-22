"""DecisionEngine — the seam where the system calls Claude to contextualize research.

This is the only place that talks to the Anthropic API. Every decision layer
(contextualize, target-strategy, execute-drafting) calls `deliberate()` with a
system prompt, a context dict, and a Pydantic output schema; Claude returns a
validated object — no parsing, no free-form text.

Defaults, per Anthropic guidance for agentic/judgment work:
  - model   : claude-opus-4-8 (most capable Opus-tier)
  - thinking: adaptive (Claude decides depth per request)
  - effort  : high (quality-sensitive reasoning)

The client is injected so layers are unit-testable without network access; in
production, omit it and the engine lazily constructs `anthropic.Anthropic()`
(which reads ANTHROPIC_API_KEY from the environment).
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Any, Optional, Type, TypeVar

from pydantic import BaseModel

DEFAULT_MODEL = "claude-opus-4-8"
DEFAULT_EFFORT = "high"

# Research (web search, scraping support) is latency-sensitive and does not need
# Opus-grade reasoning — the judgment lives in the strategy/fit/critic layers.
# Override with LILA_RESEARCH_MODEL / LILA_RESEARCH_EFFORT in .env.
RESEARCH_MODEL = "claude-sonnet-5"
RESEARCH_EFFORT = "medium"

T = TypeVar("T", bound=BaseModel)


def research_engine() -> "DecisionEngine":
    """Fast engine for research layers (company research, web-lead search)."""
    return DecisionEngine(
        model=os.environ.get("LILA_RESEARCH_MODEL", RESEARCH_MODEL),
        effort=os.environ.get("LILA_RESEARCH_EFFORT", RESEARCH_EFFORT),
    )


class DecisionEngine:
    def __init__(
        self,
        client: Any | None = None,
        model: str = DEFAULT_MODEL,
        effort: str = DEFAULT_EFFORT,
        max_tokens: int = 8000,
    ) -> None:
        self._client = client
        self.model = model
        self.effort = effort
        self.max_tokens = max_tokens

    # ----------------------------------------------------------------- #
    # Routing: "max" (default) runs through the Claude Code CLI on the
    # operator's Max subscription; "api" uses the metered Anthropic API.
    # An injected client always wins — that keeps unit tests hermetic and
    # lets callers force the API path explicitly.
    # ----------------------------------------------------------------- #
    @property
    def _route(self) -> str:
        if self._client is not None:
            return "api"
        return os.environ.get("LILA_LLM_ROUTE", "max").strip().lower()

    @property
    def client(self) -> Any:
        if self._client is None:
            import anthropic  # imported lazily so offline tests need not install it

            # Hard per-request timeout: a stuck call must FAIL VISIBLY in the job
            # console, never hang a pipeline step silently.
            self._client = anthropic.Anthropic(
                timeout=float(os.environ.get("LILA_LLM_TIMEOUT_S", "300")),
                max_retries=1,
            )
        return self._client

    def deliberate(
        self,
        *,
        layer: str,
        system_prompt: str,
        context: dict,
        schema: Type[T],
        now: Optional[datetime] = None,
    ) -> T:
        """Run one decision layer and return a validated schema instance.

        `context` is serialized to JSON and handed to Claude as the user turn, so
        the prompt is deterministic and the model sees structured input — including
        the provenance/source fields it must cite back for traceability.
        """
        user_content = self._render(layer, context)

        if self._route == "max":
            result = self._deliberate_maxplan(
                system_prompt=system_prompt, user_content=user_content, schema=schema
            )
            if hasattr(result, "generated_at") and getattr(result, "generated_at") is None:
                result.generated_at = now or datetime.now(timezone.utc)
            return result

        # `.parse()` enforces the Pydantic schema as a structured output and strips
        # JSON-schema constraints the API doesn't support, validating them client-side.
        kwargs: dict[str, Any] = dict(
            model=self.model,
            max_tokens=self.max_tokens,
            thinking={"type": "adaptive"},
            output_config={"effort": self.effort},
            system=system_prompt,
            messages=[{"role": "user", "content": user_content}],
            output_format=schema,
        )

        try:
            response = self.client.messages.parse(**kwargs)
        except TypeError:
            # Older SDKs may not accept output_config alongside output_format on
            # .parse(); drop the effort hint rather than fail the layer.
            kwargs.pop("output_config", None)
            response = self.client.messages.parse(**kwargs)

        result: T = response.parsed_output
        if hasattr(result, "generated_at") and getattr(result, "generated_at") is None:
            result.generated_at = now or datetime.now(timezone.utc)
        return result

    # ----------------------------------------------------------------- #
    # Max-plan transport (Claude Code CLI). Prompt/validation logic lives
    # here; the subprocess mechanics live in maxplan_cli.
    # ----------------------------------------------------------------- #
    def _deliberate_maxplan(self, *, system_prompt: str, user_content: str, schema: Type[T]) -> T:
        from . import maxplan_cli
        from pydantic import ValidationError

        schema_json = json.dumps(schema.model_json_schema(), indent=2, default=str)
        instruction = (
            f"{user_content}\n\n"
            "Respond with ONLY a single JSON object (no prose, no markdown fence) that "
            "validates against this JSON schema:\n"
            f"```json\n{schema_json}\n```"
        )
        reply = maxplan_cli.run_claude(
            instruction, system=system_prompt, model=self.model,
        )
        try:
            return schema.model_validate(maxplan_cli.extract_json(reply))
        except (ValidationError, maxplan_cli.MaxPlanError) as first_err:
            # One self-correcting retry: show the model its own output and the error.
            retry = maxplan_cli.run_claude(
                f"{instruction}\n\nYour previous reply was:\n{reply[:4000]}\n\n"
                f"It failed validation with:\n{first_err}\n\n"
                "Return ONLY the corrected JSON object.",
                system=system_prompt, model=self.model,
            )
            return schema.model_validate(maxplan_cli.extract_json(retry))

    def _web_research_maxplan(self, *, system_prompt: str, query: str) -> tuple[str, list[str]]:
        from . import maxplan_cli

        reply = maxplan_cli.run_claude(
            f"{query}\n\nUse web search to ground every claim. End your reply with a "
            "line 'SOURCES:' followed by one URL per line for every source you used.",
            system=system_prompt, model=self.model, allowed_tools=["WebSearch"],
        )
        return reply, maxplan_cli.extract_citations(reply)

    # ----------------------------------------------------------------- #
    # General web research via Claude's server-side web_search tool.
    # Used by the "general web scrape" search (tools/api/web_search.py).
    # ----------------------------------------------------------------- #
    def web_research(
        self, *, system_prompt: str, query: str, max_uses: int = 5
    ) -> tuple[str, list[str]]:
        """Run Claude with the web_search tool; return (findings_text, citation_urls).

        Handles the server-tool `pause_turn` loop. Exercised live with ANTHROPIC_API_KEY.
        """
        if self._route == "max":
            return self._web_research_maxplan(system_prompt=system_prompt, query=query)

        tool = {"type": "web_search_20260209", "name": "web_search", "max_uses": max_uses}
        messages: list[dict[str, Any]] = [{"role": "user", "content": query}]
        text_parts: list[str] = []
        citations: list[str] = []

        for _ in range(6):  # bound the loop
            resp = self.client.messages.create(
                model=self.model,
                max_tokens=self.max_tokens,
                thinking={"type": "adaptive"},
                output_config={"effort": self.effort},
                system=system_prompt,
                tools=[tool],
                messages=messages,
            )
            for block in getattr(resp, "content", []) or []:
                btype = getattr(block, "type", None)
                if btype == "text":
                    text_parts.append(getattr(block, "text", ""))
                elif btype == "web_search_tool_result":
                    for r in getattr(block, "content", []) or []:
                        url = getattr(r, "url", None)
                        if url:
                            citations.append(url)
            if getattr(resp, "stop_reason", None) == "pause_turn":
                messages.append({"role": "assistant", "content": resp.content})
                continue
            break
        if getattr(resp, "stop_reason", None) == "pause_turn":
            raise RuntimeError(
                "web search did not reach a terminal response within the "
                "six-turn continuation boundary"
            )
        return "\n".join(p for p in text_parts if p), citations

    def structure(self, *, instructions: str, findings: str, schema: Type[T]) -> T:
        """Second pass: turn free-text findings into a validated schema instance."""
        if self._route == "max":
            return self._deliberate_maxplan(
                system_prompt=instructions, user_content=findings, schema=schema
            )
        resp = self.client.messages.parse(
            model=self.model,
            max_tokens=self.max_tokens,
            system=instructions,
            messages=[{"role": "user", "content": findings}],
            output_format=schema,
        )
        return resp.parsed_output

    @staticmethod
    def _render(layer: str, context: dict) -> str:
        payload = json.dumps(context, indent=2, default=str, sort_keys=True)
        return (
            f"Decision layer: {layer}\n\n"
            "Here is the structured research context. Reason over it and return the "
            "required structured report. Ground every claim in the provided sources "
            "and cite them; do not invent facts beyond this context.\n\n"
            f"```json\n{payload}\n```"
        )
