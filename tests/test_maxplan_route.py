"""Max-plan route: routing rules + CLI reply parsing. No network, no CLI needed."""
import json
from unittest import mock

import pytest
from pydantic import BaseModel

from agents.decisions import maxplan_cli
from agents.decisions.engine import DecisionEngine


COMMON_TOKEN_MARKERS = (
    "xoxb-" + "a" * 24,
    "xapp-" + "a" * 24,
    "glpat-" + "a" * 24,
    "AIza" + "a" * 32,
    "ya29." + "a" * 24,
    "npm_" + "a" * 32,
    "sk_live_" + "a" * 24,
    "rk_test_" + "a" * 24,
    "eyJhbGciOiJI.eyJzdWIiOiIxMjM0.NiIsInRlc3Qi",
)


class Toy(BaseModel):
    name: str
    score: int


# ── routing rules ────────────────────────────────────────────────────────


def test_injected_client_always_uses_api_route(monkeypatch):
    monkeypatch.setenv("LILA_LLM_ROUTE", "max")
    eng = DecisionEngine(client=object())
    assert eng._route == "api"


def test_default_route_is_max(monkeypatch):
    monkeypatch.delenv("LILA_LLM_ROUTE", raising=False)
    assert DecisionEngine()._route == "max"


def test_env_can_force_api(monkeypatch):
    monkeypatch.setenv("LILA_LLM_ROUTE", "api")
    assert DecisionEngine()._route == "api"


# ── CLI reply parsing ────────────────────────────────────────────────────


def test_extract_json_plain():
    assert maxplan_cli.extract_json('{"name": "x", "score": 1}') == {"name": "x", "score": 1}


def test_extract_json_fenced_with_prose():
    text = 'Here you go:\n```json\n{"name": "x", "score": 2}\n```\nDone.'
    assert maxplan_cli.extract_json(text)["score"] == 2


def test_extract_json_garbage_raises():
    with pytest.raises(maxplan_cli.MaxPlanError):
        maxplan_cli.extract_json("no json here")


def test_extract_citations_sources_block():
    text = "findings...\nSOURCES:\nhttps://a.example/one\nhttps://b.example/two."
    assert maxplan_cli.extract_citations(text) == [
        "https://a.example/one",
        "https://b.example/two",
    ]


# ── structured round-trip with a faked subprocess ────────────────────────


def _fake_cli_json(result_text: str):
    completed = mock.Mock(returncode=0, stdout=json.dumps({"result": result_text}), stderr="")
    return mock.patch("subprocess.run", return_value=completed)


def test_deliberate_maxplan_validates_schema(monkeypatch):
    monkeypatch.setenv("LILA_LLM_ROUTE", "max")
    monkeypatch.setattr(maxplan_cli, "cli_available", lambda: True)
    with _fake_cli_json('{"name": "fit", "score": 4}'):
        out = DecisionEngine()._deliberate_maxplan(
            system_prompt="s", user_content="u", schema=Toy
        )
    assert out == Toy(name="fit", score=4)


def test_deliberate_maxplan_retries_on_bad_json(monkeypatch):
    monkeypatch.setenv("LILA_LLM_ROUTE", "max")
    monkeypatch.setattr(maxplan_cli, "cli_available", lambda: True)
    replies = iter(["not json at all", '{"name": "fit", "score": 4}'])

    def fake_run(prompt, **kwargs):
        return next(replies)

    monkeypatch.setattr(maxplan_cli, "run_claude", fake_run)
    out = DecisionEngine()._deliberate_maxplan(system_prompt="s", user_content="u", schema=Toy)
    assert out.score == 4


def test_missing_cli_message_is_actionable(monkeypatch):
    monkeypatch.setattr(maxplan_cli.shutil, "which", lambda _: None)
    with pytest.raises(maxplan_cli.MaxPlanError) as e:
        maxplan_cli.run_claude("hi")
    assert "npm install -g @anthropic-ai/claude-code" in str(e.value)


def test_run_claude_strips_api_key_from_subprocess(monkeypatch):
    """Claude inherits neither its metered key nor the OpenAI session key."""
    import json as _json
    import subprocess as _sp
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-dead-meter")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-openai-session-only")
    monkeypatch.setattr(maxplan_cli, "cli_available", lambda: True)
    seen = {}

    def fake_run(cmd, capture_output, text, timeout, env=None, cwd=None):
        seen["env"] = env
        class P:
            returncode = 0
            stdout = _json.dumps({"result": "ok"})
            stderr = ""
        return P()

    monkeypatch.setattr(_sp, "run", fake_run)
    assert maxplan_cli.run_claude("hi") == "ok"
    assert seen["env"] is not None
    assert "ANTHROPIC_API_KEY" not in seen["env"]
    assert "OPENAI_API_KEY" not in seen["env"]


def test_run_claude_isolated_mode_disables_external_context(monkeypatch):
    """The collaborator cannot inspect files, tools, MCP, Chrome, or sessions."""
    import json as _json
    import subprocess as _sp

    monkeypatch.setattr(maxplan_cli, "cli_available", lambda: True)
    account = mock.Mock(
        pw_dir="/Users/account-home",
        pw_name="account-user",
        pw_shell="/bin/zsh",
    )
    monkeypatch.setattr(maxplan_cli.pwd, "getpwuid", lambda _uid: account)
    ambient_marker = "sk-ambient-secret-123456"
    for name in (
        "HOME",
        "PATH",
        "LANG",
        "LC_ALL",
        "LC_CTYPE",
        "COLORTERM",
        "USER",
        "LOGNAME",
        "SHELL",
        "TERM",
        "TMPDIR",
        "TMP",
        "TEMP",
    ):
        monkeypatch.setenv(name, ambient_marker)
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "anthropic-auth-marker")
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "AKIA-SECRET-MARKER")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "aws-secret-marker")
    monkeypatch.setenv("CLAUDE_CODE_USE_BEDROCK", "1")
    monkeypatch.setenv("LC_OPENAI_API_KEY", "locale-secret-marker")
    seen = {}

    def fake_run(cmd, capture_output, text, timeout, env=None, cwd=None):
        seen["cmd"] = cmd
        seen["cwd"] = cwd
        seen["env"] = env

        class P:
            returncode = 0
            stdout = _json.dumps({"result": "ok"})
            stderr = ""

        return P()

    monkeypatch.setattr(_sp, "run", fake_run)
    assert maxplan_cli.run_claude(
        "hi", system="canonical evidence only", isolated=True
    ) == "ok"
    assert "--system-prompt" in seen["cmd"]
    assert "--append-system-prompt" not in seen["cmd"]
    assert "--tools" in seen["cmd"]
    assert seen["cmd"][seen["cmd"].index("--tools") + 1] == ""
    assert seen["cmd"][seen["cmd"].index("--mcp-config") + 1] == (
        '{"mcpServers":{}}'
    )
    for flag in (
        "--strict-mcp-config",
        "--disable-slash-commands",
        "--no-chrome",
        "--no-session-persistence",
        "--permission-mode",
    ):
        assert flag in seen["cmd"]
    assert seen["cmd"][seen["cmd"].index("--permission-mode") + 1] == "dontAsk"
    assert seen["cmd"][seen["cmd"].index("--setting-sources") + 1] == ""
    assert seen["env"]["HOME"] == "/Users/account-home"
    assert seen["env"]["PATH"] == maxplan_cli._ISOLATED_PATH
    assert seen["env"]["USER"] == "account-user"
    assert seen["env"]["LOGNAME"] == "account-user"
    assert seen["env"]["SHELL"] == "/bin/zsh"
    assert seen["env"]["TERM"] == "dumb"
    assert seen["env"]["TMPDIR"] == "/tmp"
    assert seen["env"]["LANG"] == "C.UTF-8"
    assert seen["env"]["LC_ALL"] == "C.UTF-8"
    assert seen["env"]["LC_CTYPE"] == "C.UTF-8"
    for fixed_opt_out in (
        "CLAUDE_CODE_DISABLE_AUTO_MEMORY",
        "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC",
        "DISABLE_AUTOUPDATER",
        "DISABLE_ERROR_REPORTING",
        "DISABLE_TELEMETRY",
    ):
        assert seen["env"][fixed_opt_out] == "1"
    assert ambient_marker not in json.dumps(seen["env"])
    for omitted in ("COLORTERM", "TMP", "TEMP"):
        assert omitted not in seen["env"]
    assert seen["cwd"]
    assert "lila-claude-isolated-" in seen["cwd"]
    for forbidden in (
        "ANTHROPIC_AUTH_TOKEN",
        "AWS_ACCESS_KEY_ID",
        "AWS_SECRET_ACCESS_KEY",
        "CLAUDE_CODE_USE_BEDROCK",
        "LC_OPENAI_API_KEY",
    ):
        assert forbidden not in seen["env"]


def test_run_claude_redacts_cli_error_streams(monkeypatch):
    import subprocess as _sp

    auth = "anthropic-auth-marker-123"
    aws = "aws-secret-marker-456"
    aws_access_key = "AKIA" + "A" * 16
    github_token = "ghp_" + "a" * 36
    slack_token = "xoxb-" + "a" * 24
    pem_body = "dHJ1bmNhdGVkLXByaXZhdGUta2V5LWJvZHk="
    monkeypatch.setattr(maxplan_cli, "cli_available", lambda: True)
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", auth)
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", aws)

    def fake_run(cmd, capture_output, text, timeout, env=None, cwd=None):
        class P:
            returncode = 1
            stdout = (
                f"ANTHROPIC_AUTH_TOKEN={auth} AWS_SECRET_ACCESS_KEY={aws} "
                f"{aws_access_key} {github_token} {slack_token} "
                "password is correct horse battery staple; "
                "secret is alpha beta gamma delta; "
                "operator@example.test 2025550199"
            )
            stderr = (
                "Authorization: Bearer sk-secret-marker-789 "
                f"-----BEGIN PRIVATE KEY-----\n{pem_body}"
            )

        return P()

    monkeypatch.setattr(_sp, "run", fake_run)
    with pytest.raises(maxplan_cli.MaxPlanError) as exc_info:
        maxplan_cli.run_claude("hi", isolated=True)
    message = str(exc_info.value)
    for forbidden in (
        auth,
        aws,
        "ANTHROPIC_AUTH_TOKEN",
        "AWS_SECRET_ACCESS_KEY",
        "operator@example.test",
        "2025550199",
        "sk-secret-marker-789",
        aws_access_key,
        github_token,
        slack_token,
        "correct horse battery staple",
        "alpha beta gamma delta",
        "PRIVATE KEY",
        pem_body,
    ):
        assert forbidden not in message


def test_run_claude_error_includes_stdout(monkeypatch):
    """CLI errors land on stdout in json mode — the message must show them."""
    import subprocess as _sp
    monkeypatch.setattr(maxplan_cli, "cli_available", lambda: True)

    def fake_run(cmd, capture_output, text, timeout, env=None, cwd=None):
        class P:
            returncode = 1
            stdout = '{"type":"result","is_error":true,"result":"Credit balance is too low"}'
            stderr = ""
        return P()

    monkeypatch.setattr(_sp, "run", fake_run)
    try:
        maxplan_cli.run_claude("hi")
        raise AssertionError("expected MaxPlanError")
    except maxplan_cli.MaxPlanError as e:
        assert "Credit balance is too low" in str(e)


def test_run_claude_redacts_full_error_stream_before_tail_slicing(monkeypatch):
    import subprocess as _sp

    leaked_tail = "c2VjcmV0LWJvZHk=" * 100
    monkeypatch.setattr(maxplan_cli, "cli_available", lambda: True)

    def fake_run(cmd, capture_output, text, timeout, env=None, cwd=None):
        class P:
            returncode = 1
            stdout = "password is " + leaked_tail
            stderr = "-----BEGIN PRIVATE KEY-----\n" + leaked_tail

        return P()

    monkeypatch.setattr(_sp, "run", fake_run)
    with pytest.raises(maxplan_cli.MaxPlanError) as exc_info:
        maxplan_cli.run_claude("hi", isolated=True)

    message = str(exc_info.value)
    assert "[redacted]" in message
    assert leaked_tail[-400:] not in message
    assert "PRIVATE KEY" not in message


def test_maxplan_error_redactor_removes_long_credential_clauses():
    marker = "x" * 600

    redacted = maxplan_cli._redact_cli_error(f"password is {marker}")

    assert redacted == "[redacted]"
    assert marker not in redacted


@pytest.mark.parametrize("token", COMMON_TOKEN_MARKERS)
def test_maxplan_error_redactor_removes_common_token_shapes(token):
    redacted = maxplan_cli._redact_cli_error(f"provider error {token}")

    assert "[redacted]" in redacted
    assert token not in redacted


@pytest.mark.parametrize("raw", [
    "password is correct horse\nbattery staple",
    "password is correct.horse.battery.staple",
    "api key is alpha!beta?gamma",
    "ｐａｓｓｗｏｒｄ is fullwidth secret",
    "sk－secret-value-123456",
    "operator＠example.com",
])
def test_maxplan_error_redactor_closes_multiline_punctuation_and_unicode_bypasses(
    raw,
):
    redacted = maxplan_cli._redact_cli_error(raw)

    assert "[redacted]" in redacted
    assert "horse" not in redacted
    assert "beta" not in redacted
    assert "secret-value" not in redacted
    assert "operator@example.com" not in redacted


@pytest.mark.parametrize("raw", (
    "password-is-hunter2",
    "client-secret-is-alpha",
    "access-token/is/alpha",
    "password was correct.horse",
    "password equals alpha!beta",
    "password -> alpha",
    "OPENAI_API_KEY hunter2value",
    "password\nis hunter",
    "client secret\nwas hunter",
    "名前@例え.テスト",
    "+44 20 7946 0958",
    "pass\N{COMBINING ACUTE ACCENT}word",
    "api ke\N{COMBINING ACUTE ACCENT}y",
    "password%2520is%2520hunter",
    "password&#32;is&#32;hunter",
))
def test_maxplan_redactor_uses_shared_privacy_boundary(raw):
    assert maxplan_cli._redact_cli_error(raw) == "[redacted]"


@pytest.mark.parametrize(("raw", "expected"), (
    ("key issues include FedRAMP", "key issues include FedRAMP"),
    ("token issuance service", "token issuance service"),
    ("hello\nworld", "hello world"),
    ("café résumé", "café résumé"),
))
def test_maxplan_redactor_preserves_benign_analysis(raw, expected):
    assert maxplan_cli._redact_cli_error(raw) == expected


def test_run_claude_rejects_malicious_model_before_cli_or_subprocess(monkeypatch):
    cli_checks = []
    subprocess_calls = []
    bad_model = "sk-secret-value-123456"
    monkeypatch.setattr(
        maxplan_cli,
        "cli_available",
        lambda: cli_checks.append(True) or True,
    )
    monkeypatch.setattr(
        maxplan_cli.subprocess,
        "run",
        lambda *_args, **_kwargs: subprocess_calls.append(True),
    )

    with pytest.raises(maxplan_cli.MaxPlanError) as exc_info:
        maxplan_cli.run_claude("safe prompt", model=bad_model)

    assert bad_model not in str(exc_info.value)
    assert cli_checks == []
    assert subprocess_calls == []


def test_isolated_run_refuses_ambient_claude_instructions_before_child(
    tmp_path,
    monkeypatch,
):
    rules = tmp_path / ".claude" / "rules"
    rules.mkdir(parents=True)
    secret_instruction = "private instruction sk-secret-value-123456"
    (rules / "global.md").write_text(secret_instruction, encoding="utf-8")
    account = mock.Mock(
        pw_dir=str(tmp_path),
        pw_name="operator",
        pw_shell="/bin/zsh",
    )
    child_calls = []
    monkeypatch.setattr(maxplan_cli, "cli_available", lambda: True)
    monkeypatch.setattr(maxplan_cli.pwd, "getpwuid", lambda _uid: account)
    monkeypatch.setattr(
        maxplan_cli.subprocess,
        "run",
        lambda *_args, **_kwargs: child_calls.append(True),
    )

    with pytest.raises(maxplan_cli.MaxPlanError) as exc_info:
        maxplan_cli.run_claude("safe prompt", isolated=True)

    assert child_calls == []
    assert secret_instruction not in str(exc_info.value)
