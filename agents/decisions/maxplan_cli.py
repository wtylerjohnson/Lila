"""Max-plan transport — routes DecisionEngine calls through the Claude Code CLI.

Why this exists: the metered API route (`anthropic.Anthropic()`) stalls the
pipeline when credits run out, but the operator already pays for a Claude Max
subscription. The Claude Code CLI (`claude -p`) bills that subscription, so
routing through it makes the pipeline run on the plan instead of the meter.

Requirements on the host machine (one-time):
    npm install -g @anthropic-ai/claude-code
    claude          # sign in with the Max-plan account

Design notes:
  - This is a *transport*, not a decision layer. All prompting/validation logic
    stays in DecisionEngine; this module just runs the CLI and returns text.
  - Structured outputs: the CLI has no `.parse()`, so the engine appends the
    JSON schema to the prompt and validates client-side with Pydantic (with one
    self-correcting retry). Same guarantee, different enforcement point.
  - Model names map to CLI aliases (opus/sonnet/haiku) so LILA model config
    keeps working unchanged.
"""

from __future__ import annotations

from contextlib import nullcontext
import json
import os
from pathlib import Path
import pwd
import re
import shutil
import subprocess
import tempfile

from agents.candidate_review_v1.collaboration import (
    _security_normalize,
    redact_sensitive_material,
    validate_provider_model,
)

_ALIAS = {
    "claude-opus-4-8": "opus",
    "claude-sonnet-5": "sonnet",
    "claude-haiku-4-5": "haiku",
}

_ISOLATED_PATH = "/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin"
_SECRET_ENV_NAME_RE = re.compile(
    r"(?:^|_)(?:api_?)?(?:key|token|secret|password|passwd|credential)s?$"
    r"|(?:anthropic|openai|aws|azure|google|gcp|github|gitlab|slack|stripe)"
    r".*(?:key|token|secret|password|credential|auth)",
    re.I,
)
class MaxPlanError(RuntimeError):
    """Raised when the CLI is missing or a call fails. Message is operator-facing."""


def cli_available() -> bool:
    return shutil.which("claude") is not None


def _require_cli() -> None:
    if not cli_available():
        raise MaxPlanError(
            "Claude Code CLI not found on PATH, so the Max-plan route can't run.\n"
            "One-time setup:\n"
            "  npm install -g @anthropic-ai/claude-code\n"
            "  claude   (sign in with your Max-plan account)\n"
            "Or set LILA_LLM_ROUTE=api to use the metered API instead."
        )


def cli_model(model: str) -> str:
    return _ALIAS.get(model, model)


def _instruction_tree_has_content(root: Path) -> bool:
    try:
        if not root.exists():
            return False
        for directory, _subdirectories, file_names in os.walk(
            root,
            followlinks=False,
        ):
            for file_name in file_names:
                if (Path(directory) / file_name).stat().st_size > 0:
                    return True
        return False
    except OSError:
        raise MaxPlanError(
            "isolated Claude instruction preflight could not be verified"
        ) from None


def _assert_no_isolated_instructions(
    home: str | Path,
    *,
    cwd: str | Path | None = None,
) -> None:
    """Fail closed when Claude could discover ambient instruction files."""

    roots: list[Path] = [Path(home)]
    if cwd is not None:
        current = Path(cwd).resolve()
        roots.extend((current, *current.parents))
    checked: set[Path] = set()
    try:
        for root in roots:
            resolved = root.resolve()
            if resolved in checked:
                continue
            checked.add(resolved)
            for instruction in (
                resolved / "CLAUDE.md",
                resolved / ".claude" / "CLAUDE.md",
            ):
                if instruction.is_file() and instruction.stat().st_size > 0:
                    raise MaxPlanError(
                        "isolated Claude cannot run with ambient instructions"
                    )
            if _instruction_tree_has_content(resolved / ".claude" / "rules"):
                raise MaxPlanError(
                    "isolated Claude cannot run with ambient instructions"
                )
    except MaxPlanError:
        raise
    except OSError:
        raise MaxPlanError(
            "isolated Claude instruction preflight could not be verified"
        ) from None


def _isolated_environment() -> dict[str, str]:
    """Positive allowlist for a canonical-evidence-only Claude subprocess."""

    account = pwd.getpwuid(os.getuid())
    _assert_no_isolated_instructions(account.pw_dir)
    env = {
        "HOME": account.pw_dir,
        "PATH": _ISOLATED_PATH,
        "TMPDIR": "/tmp",
        "USER": account.pw_name,
        "LOGNAME": account.pw_name,
        "SHELL": account.pw_shell or "/bin/sh",
        "TERM": "dumb",
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "LC_CTYPE": "C.UTF-8",
    }
    if any(
        not value
        or any(control in value for control in ("\0", "\n", "\r"))
        or _redact_cli_error(value) != value
        for value in env.values()
    ):
        raise MaxPlanError("isolated Claude environment is unsafe")
    # Fixed, documented opt-outs keep the isolated analytical subprocess from
    # exporting nonessential telemetry, error reports, update checks, or
    # automatic memory while preserving the user's Max-plan authentication.
    env.update({
        "CLAUDE_CODE_DISABLE_AUTO_MEMORY": "1",
        "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
        "DISABLE_AUTOUPDATER": "1",
        "DISABLE_ERROR_REPORTING": "1",
        "DISABLE_TELEMETRY": "1",
    })
    return env


def _redact_cli_error(value: object) -> str:
    """Remove ambient credentials and contact data from operator-facing errors."""

    safe = redact_sensitive_material(value)
    for name, secret in os.environ.items():
        if _SECRET_ENV_NAME_RE.search(name) and len(secret) >= 4:
            normalized_secret = _security_normalize(secret)
            safe = safe.replace(normalized_secret, "[redacted]")
    return safe


def run_claude(
    prompt: str,
    *,
    system: str | None = None,
    model: str = "opus",
    timeout_s: float | None = None,
    allowed_tools: list[str] | None = None,
    isolated: bool = False,
) -> str:
    """One `claude -p` invocation; returns the result text.

    A stuck call must fail visibly in the job console, never hang a pipeline
    step silently — hence the hard timeout (LILA_LLM_TIMEOUT_S, default 300s).
    """
    try:
        validated_model = validate_provider_model(model)
    except (TypeError, ValueError):
        raise MaxPlanError("Claude model is invalid") from None
    _require_cli()
    timeout = timeout_s or float(os.environ.get("LILA_LLM_TIMEOUT_S", "300"))
    cmd = [
        "claude",
        "-p",
        prompt,
        "--output-format",
        "json",
        "--model",
        cli_model(validated_model),
    ]
    if system:
        cmd += [
            "--system-prompt" if isolated else "--append-system-prompt",
            system,
        ]
    if allowed_tools:
        cmd += ["--allowedTools", ",".join(allowed_tools)]
    if isolated:
        # Collaboration first passes must reason only over the canonical
        # sanitized request.  Disable workspace/browser/tool discovery, MCP,
        # skills, and local session persistence without using --bare (which
        # would also disable the Max-plan OAuth/keychain authentication path).
        cmd += [
            "--tools", "",
            "--strict-mcp-config",
            "--mcp-config", '{"mcpServers":{}}',
            "--setting-sources", "",
            "--disable-slash-commands",
            "--no-chrome",
            "--no-session-persistence",
            "--permission-mode", "dontAsk",
        ]

    # CRITICAL: this transport exists to bill the Max SUBSCRIPTION. If
    # ANTHROPIC_API_KEY leaks into the subprocess (e.g. loaded from .env by
    # the server), the CLI silently switches to API-key billing — and a dead
    # key kills every call with exit 1. Diagnosed live 2026-07-03: server
    # calls failed while terminal calls worked, because only the server had
    # loaded .env. Strip it unconditionally here; the API route has its own
    # transport and never passes through this function.
    # The optional OpenAI challenger key is also session-only and belongs only
    # to its own in-process adapter.  Claude must never inherit it merely
    # because both collaborators run during the same assessment.
    if isolated:
        env = _isolated_environment()
    else:
        excluded_keys = {"ANTHROPIC_API_KEY", "OPENAI_API_KEY"}
        env = {k: v for k, v in os.environ.items() if k not in excluded_keys}
    working_directory = (
        tempfile.TemporaryDirectory(prefix="lila-claude-isolated-")
        if isolated else nullcontext(None)
    )
    try:
        with working_directory as isolated_cwd:
            if isolated:
                _assert_no_isolated_instructions(
                    env["HOME"],
                    cwd=isolated_cwd,
                )
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout,
                env=env,
                cwd=isolated_cwd,
            )
    except subprocess.TimeoutExpired:
        raise MaxPlanError(f"claude -p timed out after {timeout:.0f}s (LILA_LLM_TIMEOUT_S)")
    if proc.returncode != 0:
        # The CLI writes its real error (expired login, usage limit, bad flag)
        # to STDOUT in json mode — show both streams or we diagnose blind.
        safe_stdout = _redact_cli_error(proc.stdout or "(empty)")
        safe_stderr = _redact_cli_error(proc.stderr or "(empty)")
        raise MaxPlanError(
            f"claude -p exited {proc.returncode}."
            f"\nstdout tail:\n{safe_stdout[-800:]}"
            f"\nstderr tail:\n{safe_stderr[-400:]}"
        )
    try:
        payload = json.loads(proc.stdout)
        if isinstance(payload, dict) and "result" in payload:
            return payload["result"] or ""
    except json.JSONDecodeError:
        pass
    return proc.stdout


def extract_json(text: str) -> dict:
    """Pull the first JSON object out of a model reply (handles ```json fences)."""
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.S)
    candidate = fenced.group(1) if fenced else None
    if candidate is None:
        start = text.find("{")
        end = text.rfind("}")
        if start == -1 or end <= start:
            raise MaxPlanError(f"No JSON object found in CLI reply:\n{text[:400]}")
        candidate = text[start : end + 1]
    try:
        return json.loads(candidate)
    except json.JSONDecodeError as e:
        raise MaxPlanError(f"CLI reply JSON did not parse: {e}\n{candidate[:400]}")


def extract_citations(text: str) -> list[str]:
    """URLs from a SOURCES: block if present, else any URLs in the text."""
    block = re.split(r"\bSOURCES?:\s*", text, maxsplit=1, flags=re.I)
    haystack = block[1] if len(block) > 1 else text
    urls = re.findall(r"https?://[^\s\)\]>\"']+", haystack)
    seen: list[str] = []
    for u in urls:
        u = u.rstrip(".,;")
        if u not in seen:
            seen.append(u)
    return seen
