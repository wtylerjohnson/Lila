"""V4.2 genuine integration acceptance (final repair round).

The REAL SubprocessRunnerAdapter drives C1 (assessment mode) and the
REAL refresh adapter drives run_refresh_press.py end to end; only the
network and the external runner boundaries (re-sweep, award re-pull)
are stubbed. Also: publication fault injection with prior-byte
restoration, permutation byte-identity of the complete output, the
zero-LLM production default, nested named failure through the chain,
and legacy operator-mode preservation.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys

import pytest

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _REPO)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from test_composer import (  # noqa: E402
    CAL_HEADER, _mint_receipt, _sweep, _world_client,  # noqa: F401
    _zero_best_fit_sweep, world,  # noqa: F401
)

SITECUSTOMIZE = '''
"""Hermetic child-process boundaries.

1. Network stub: every HTTP fetch answers 200 with empty body.
2. Press-child frozen clock (calendar-rot guard, 2026-08-03):
   run_signal_board.py defaults its render date to date.today() and the
   production refresh chain passes no --render-date, so this world aged in
   real time: the figures' fixed retrieved_at 2026-07-19 crossed the 14-day
   figure-freshness gate and the press began REFUSING before render with no
   code change (verified failing on pristine HEAD). Freeze only the press
   child at the world's own TODAY; every other child keeps the real clock,
   exactly as the parent-side radar tests freeze their module clock.
"""
import io
import os
import sys
import urllib.request

if os.path.basename(sys.argv[0] or "") == "run_signal_board.py":
    import datetime as _datetime_module

    class _FrozenDate(_datetime_module.date):
        @classmethod
        def today(cls):
            return cls(2026, 7, 19)

    class _FrozenDateTime(_datetime_module.datetime):
        @classmethod
        def now(cls, tz=None):
            frozen = cls(2026, 7, 19, 12, 0,
                         tzinfo=_datetime_module.timezone.utc)
            if tz is None:
                return frozen.replace(tzinfo=None)
            return frozen.astimezone(tz)

    _datetime_module.date = _FrozenDate
    _datetime_module.datetime = _FrozenDateTime


class _FakeResponse(io.BytesIO):
    status = 200
    code = 200

    def __init__(self):
        super().__init__(b"ok")
        self.headers = {}

    def getcode(self):
        return 200

    def info(self):
        return self.headers

    def geturl(self):
        return "stub://ok"

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def _fake_urlopen(*_args, **_kwargs):
    return _FakeResponse()


urllib.request.urlopen = _fake_urlopen
'''

RUN_SEARCHES_STUB = '''#!/usr/bin/env python3
"""Re-sweep boundary stub: refreshes the stored sweep deterministically."""
import argparse
import json
import os

parser = argparse.ArgumentParser()
parser.add_argument("--client", required=True)
parser.add_argument("--skip", nargs="*", default=[])
parser.add_argument("--preserve-skipped", action="store_true")
args = parser.parse_args()
root = os.path.dirname(os.path.abspath(__file__))
slug = "".join(c if c.isalnum() else "_" for c in args.client).strip("_").lower()
path = os.path.join(root, "data", "cleaned", f"searches_{slug}.json")
sweep = json.load(open(path, encoding="utf-8"))
sweep["generated_at"] = "2026-07-19T09:00:00+00:00"
json.dump(sweep, open(path, "w", encoding="utf-8"))
print(f"[done] -> {path}")
'''

REPULL_STUB = '''"""Award re-pull boundary stub: records the pulled gids, no network.

Honors BOTH slug domains: clients/ rides the canonical family while the
sweep artifact rides the legacy press/review family."""
import json
import os
import re
import sys


def main(argv=None):
    client = sys.argv[sys.argv.index("--client") + 1]
    root = os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))))
    canonical = re.sub(r"[^a-z0-9]+", "_", client.lower()).strip("_")
    slug = "".join(c if c.isalnum() else "_"
                   for c in client).strip("_").lower()
    content = json.load(open(os.path.join(
        root, "clients", canonical, "signal_board_content.json"),
        encoding="utf-8"))
    sweep_path = os.path.join(root, "data", "cleaned",
                              f"searches_{slug}.json")
    sweep = json.load(open(sweep_path, encoding="utf-8"))
    descriptions = {
        str(row.get("generated_internal_id") or ""): row.get("description")
        for buyer in sweep["results"]["incumbent_buyer_map"]["buyers"]
        for row in buyer.get("records", [])
        if row.get("generated_internal_id")
    }
    rows = {}
    for f in content.get("figures") or []:
        gid = f.get("generated_internal_id")
        if gid and gid not in rows:
            rows[gid] = {
                "generated_id": gid,
                "piid": f.get("source_record_id"),
                "obligated": f.get("raw"),
                "description": descriptions.get(gid),
                "retrieved_at": "2026-07-19T09:05:00+00:00"}
    gids = sorted(rows)
    sweep["results"]["award_repulls"] = [rows[g] for g in gids]
    json.dump(sweep, open(sweep_path, "w", encoding="utf-8"))
    with open(os.path.join(root, "repull_gids.json"), "w",
              encoding="utf-8") as f:
        json.dump(gids, f)
    print(f"[repull] {len(gids)} award rows -> {sweep_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
'''


def _link_tree(tmp_path):
    """Hermetic repo root: code symlinked, external runners stubbed."""
    for name in ("agents",):
        os.symlink(os.path.join(_REPO, name), tmp_path / name)
    (tmp_path / "tools").mkdir()
    for entry in os.listdir(os.path.join(_REPO, "tools")):
        if entry in ("api", "__pycache__"):
            continue
        os.symlink(os.path.join(_REPO, "tools", entry),
                   tmp_path / "tools" / entry)
    (tmp_path / "tools" / "api").mkdir()
    for entry in os.listdir(os.path.join(_REPO, "tools", "api")):
        if entry in ("award_repull.py", "__pycache__"):
            continue
        os.symlink(os.path.join(_REPO, "tools", "api", entry),
                   tmp_path / "tools" / "api" / entry)
    (tmp_path / "tools" / "api" / "award_repull.py").write_text(
        REPULL_STUB, encoding="utf-8")
    # entry scripts are COPIED, not symlinked: CPython resolves a
    # symlinked script's directory into sys.path[0], which would import
    # the real repo's packages into the hermetic world
    import shutil
    for script in ("run_compose.py", "run_signal_board.py",
                   "run_refresh_press.py", "run_candidate_review_watch.py"):
        shutil.copy(os.path.join(_REPO, script), tmp_path / script)
    (tmp_path / "run_searches.py").write_text(RUN_SEARCHES_STUB,
                                              encoding="utf-8")
    # the real watch CLI runs here (2026-07-24): all lanes NOT_RUN offline,
    # a real generation persists, and the refresh marker validator holds
    (tmp_path / "data" / "reference").mkdir(parents=True, exist_ok=True)
    shutil.copy(
        os.path.join(_REPO, "data", "reference", "event_watch_universe.json"),
        tmp_path / "data" / "reference" / "event_watch_universe.json")
    (tmp_path / "sitecustomize.py").write_text(SITECUSTOMIZE,
                                               encoding="utf-8")
    (tmp_path / "docs" / "reference").mkdir(parents=True)
    ref = os.path.join(_REPO, "docs", "reference")
    for entry in os.listdir(ref):
        os.symlink(os.path.join(ref, entry),
                   tmp_path / "docs" / "reference" / entry)
    (tmp_path / "data" / "review").mkdir(parents=True)
    # A full valid APPROVED packet (2026-07-24): the real candidate-review
    # watch runs in this world and model-validates the packet strictly.
    from datetime import datetime, timezone as _tz

    from agents.decisions.schemas import (
        IntakeStrategy, Keyword, KeywordCategory, SearchSpec,
    )
    from agents.review import ReviewPacket, ReviewStatus
    _packet = ReviewPacket(
        client_name="Testco",
        status=ReviewStatus.APPROVED,
        strategy=IntakeStrategy(
            client_name="Testco",
            pursuit_strategy="Find evidence-bound federal requirements.",
            keywords=[Keyword(
                term="network observability",
                category=KeywordCategory.TECHNOLOGY,
                rationale="Approved capability.",
            )],
            inferred_naics=["541512"],
            target_agencies=["Department of Defense"],
            searches=[SearchSpec(
                source="sam.gov",
                query_terms=["network observability"],
                naics_codes=["541512"],
                rationale="Approved live-opportunity search.",
            )],
            confidence=0.8,
            sources_reviewed=["https://example.gov/source"],
            review_gate="Analyst approval required.",
        ),
        decided_at=datetime(2026, 7, 18, 12, 0, tzinfo=_tz.utc),
        search_scope={"all": True},
    )
    (tmp_path / "data" / "review" / "testco.review.json").write_text(
        _packet.model_dump_json(indent=2) + "\n", encoding="utf-8")
    (tmp_path / "data" / "reports").mkdir(parents=True)
    _world_client(tmp_path, "testco", "Testco")
    _bind_sweep_scope(tmp_path)
    return tmp_path


def _bind_sweep_scope(root):
    """The real watch demands the sweep's exact search_scope (2026-07-24).

    Rewriting the sweep bytes invalidates the world's minted relevance
    receipt, so the receipt is re-minted through the chain's own producer.
    """
    sweep_path = root / "data" / "cleaned" / "searches_testco.json"
    payload = json.loads(sweep_path.read_text(encoding="utf-8"))
    payload["search_scope"] = {"all": True}
    sweep_path.write_text(json.dumps(payload), encoding="utf-8")
    _mint_receipt(root)


@pytest.fixture()
def hermetic(tmp_path, monkeypatch):
    root = _link_tree(tmp_path)
    monkeypatch.setenv("PYTHONPATH", str(root))
    monkeypatch.setenv("PATH", "/usr/bin:/bin")
    monkeypatch.setenv("LILA_COMPOSE_CACHE_DIR", str(root / "cache"))
    monkeypatch.setenv("LILA_ENTITIES_DIR", str(root / "entities"))
    return root


def test_real_adapter_c1_and_nested_named_failure(hermetic):
    from agents.assessment_chain import SubprocessRunnerAdapter
    adapter = SubprocessRunnerAdapter(root=hermetic, python=sys.executable)
    outcome = adapter.run("COMPOSING", slug="testco", mode="assessment")
    assert outcome.success is True, (outcome.reason, outcome.stderr)

    # nested named failure: strip core evidence, keep the receipt honest
    bare = _zero_best_fit_sweep()
    _world_client(hermetic, "testco", "Testco", sweep=bare)
    _bind_sweep_scope(hermetic)
    outcome = adapter.run("COMPOSING", slug="testco", mode="assessment")
    assert outcome.success is False
    assert "machine-screened" in (outcome.reason or "")
    assert "keyword/NAICS review checkpoint" in (outcome.fix_surface or "") \
        or "keyword/NAICS" in (outcome.reason or "")


def test_real_refresh_adapter_end_to_end(hermetic):
    from datetime import datetime, timezone

    from agents.assessment_chain import SubprocessRunnerAdapter
    adapter = SubprocessRunnerAdapter(
        root=hermetic, python=sys.executable,
        clock=lambda: datetime(2026, 7, 19, 0, 0, tzinfo=timezone.utc))
    first = adapter.run("COMPOSING", slug="testco", mode="assessment")
    assert first.success is True, (first.reason, first.stderr)

    # The REAL production refresh adapter through run_refresh_press.py:
    # relevance receipt minted, composer recomposed, re-pull merged, press
    # ran (honestly gated DO-NOT-SEND on the fixture's links), snapshot
    # and delta validated. success=True with the outer press status
    # preserved is the C3 doctrine: a gated press is comparable state.
    outcome = adapter.run("GATING_PRESS", slug="testco", mode="refresh")
    assert outcome.success is True, (outcome.reason,
                                     (outcome.stderr or "")[-1200:])
    assert outcome.returncode == 2          # the outer press status survives
    assert "DO-NOT-SEND" in outcome.note
    assert (hermetic / "data" / "reports"
            / "testco.federal_opportunity_signals.DO-NOT-SEND.html").exists()
    assert not (hermetic / "clients" / "testco"
                / "compose_failure.json").exists()
    pulled = json.loads((hermetic / "repull_gids.json")
                        .read_text(encoding="utf-8"))
    content = json.loads(
        (hermetic / "clients" / "testco" / "signal_board_content.json")
        .read_text(encoding="utf-8"))
    lane_gids = sorted({f["generated_internal_id"]
                        for f in content["figures"]
                        if f.get("generated_internal_id")})
    assert pulled == lane_gids and pulled, "re-pull must cover every lane"
    receipt = json.loads(
        (hermetic / "data" / "state" / "relevance" / "testco.run.json")
        .read_text(encoding="utf-8"))
    assert receipt["client_name"] == "Testco"
    assert receipt["command"][2:4] == ["--client", "Testco"]


def test_refresh_relays_the_composer_named_failure(hermetic):
    bare = _zero_best_fit_sweep()
    _world_client(hermetic, "testco", "Testco", sweep=bare)
    _bind_sweep_scope(hermetic)
    proc = subprocess.run(
        [sys.executable, str(hermetic / "run_refresh_press.py"),
         "--client", "Testco"],
        cwd=hermetic, text=True, capture_output=True, check=False,
        env={**os.environ, "PYTHONPATH": str(hermetic),
             "PATH": "/usr/bin:/bin"})
    assert proc.returncode != 0
    lines = [ln for ln in proc.stderr.splitlines()
             if ln.startswith("[refresh:failure] ")]
    assert lines, proc.stderr
    payload = json.loads(lines[-1].split(" ", 1)[1])
    assert payload["stage"] == "compose-refresh"
    assert "machine-screened" in payload["reason"]
    assert "keyword/NAICS" in payload["fix_surface"]
    assert not (hermetic / "clients" / "testco"
                / "compose_failure.json").exists()


def test_real_refresh_adapter_preserves_composer_rc2_named_failure(hermetic):
    """The REAL refresh adapter accepts a single well-formed
    compose-refresh failure at a nonzero exit and preserves C1's reason,
    fix surface, and return code."""
    from agents.assessment_chain import SubprocessRunnerAdapter
    bare = _zero_best_fit_sweep()
    _world_client(hermetic, "testco", "Testco", sweep=bare)
    _bind_sweep_scope(hermetic)
    adapter = SubprocessRunnerAdapter(root=hermetic, python=sys.executable)
    outcome = adapter.run("GATING_PRESS", slug="testco", mode="refresh")
    assert outcome.success is False
    assert outcome.returncode == 2
    assert outcome.note == "compose-refresh failed named"
    assert "machine-screened" in (outcome.reason or "")
    assert "keyword/NAICS" in (outcome.fix_surface or "")
    assert not (hermetic / "clients" / "testco"
                / "compose_failure.json").exists()


def test_publication_faults_restore_prior_pair(world, monkeypatch, capsys):  # noqa: F811
    import run_compose
    from tools import atomic_io
    rc = run_compose.main(["--client", "testco", "--today", "2026-07-19"])
    assert rc == 0
    capsys.readouterr()
    content_path = world / "clients" / "testco" / "signal_board_content.json"
    trail_path = world / "clients" / "testco" / "compose_trail.md"
    prior = (content_path.read_bytes(), trail_path.read_bytes())

    real = atomic_io.atomic_write_text

    def fail_on(name):
        def writer(path, text):
            if os.path.basename(str(path)) == name:
                raise OSError(f"injected {name} failure")
            return real(path, text)
        return writer

    for victim, stage in (("compose_trail.md", "write-trail"),
                          ("signal_board_content.json", "write-content")):
        monkeypatch.setattr(atomic_io, "atomic_write_text", fail_on(victim))
        rc = run_compose.main(["--client", "testco",
                               "--today", "2026-07-20"])
        assert rc == 2
        err = json.loads(capsys.readouterr().err.strip().splitlines()[-1])
        assert err["stage"] == stage
        assert (content_path.read_bytes(), trail_path.read_bytes()) == prior

    monkeypatch.setattr(atomic_io, "atomic_write_text", real)
    import agents.reports.board_content as bc
    monkeypatch.setattr(bc, "load_board_content", lambda _c: None)
    rc = run_compose.main(["--client", "testco", "--today", "2026-07-20"])
    assert rc == 2
    err = json.loads(capsys.readouterr().err.strip().splitlines()[-1])
    assert err["stage"] == "validate"
    assert (content_path.read_bytes(), trail_path.read_bytes()) == prior


def test_no_flag_invocation_makes_zero_llm_calls(world, monkeypatch, capsys):  # noqa: F811
    import run_compose
    from agents.decisions import maxplan_cli

    def bomb(*_a, **_k):
        raise AssertionError("LLM transport must never fire by default")
    monkeypatch.setattr(maxplan_cli, "run_claude", bomb)
    monkeypatch.setattr(maxplan_cli, "cli_available",
                        lambda: (_ for _ in ()).throw(AssertionError(
                            "cli_available must not even be consulted")))
    rc = run_compose.main(["--client", "testco", "--today", "2026-07-19"])
    assert rc == 0
    capsys.readouterr()


def test_complete_output_is_byte_identical_under_permutation(
        world, capsys):  # noqa: F811
    import run_compose
    content_path = world / "clients" / "testco" / "signal_board_content.json"
    trail_path = world / "clients" / "testco" / "compose_trail.md"

    rc = run_compose.main(["--client", "testco", "--today", "2026-07-19"])
    assert rc == 0
    baseline = (content_path.read_bytes(), trail_path.read_bytes())

    permuted = _sweep()
    results = permuted["results"]
    results["sam.gov"] = list(reversed(results["sam.gov"]))
    results["incumbent_buyer_map"]["buyers"] = list(
        reversed(results["incumbent_buyer_map"]["buyers"]))
    for buyer in results["incumbent_buyer_map"]["buyers"]:
        buyer["records"] = list(reversed(buyer["records"]))
    results["forecast_signals"]["matched"] = list(
        reversed(results["forecast_signals"]["matched"]))
    results["subawards"]["primes"] = list(
        reversed(results["subawards"]["primes"]))
    for naics in results["subawards"]["edges"]:
        results["subawards"]["edges"][naics] = list(
            reversed(results["subawards"]["edges"][naics]))
    _world_client(world, "testco", "Testco", sweep=permuted)
    rc = run_compose.main(["--client", "testco", "--today", "2026-07-19"])
    assert rc == 0
    assert (content_path.read_bytes(), trail_path.read_bytes()) == baseline
    capsys.readouterr()


def test_legacy_operator_content_keeps_operator_mode(world):  # noqa: F811
    from agents.reports.board_content import SignalBoardContent
    legacy = SignalBoardContent.model_validate(
        {"client_name": "Insignary"})
    assert legacy.composition_mode == "operator"
    live = json.load(open(os.path.join(
        _REPO, "clients", "insignary", "signal_board_content.json"),
        encoding="utf-8"))
    assert "composition_mode" not in live, (
        "the golden operator artifact must stay byte-identical")
    assert SignalBoardContent.model_validate(live).composition_mode \
        == "operator"


def test_real_refresh_adapter_handles_divergent_slug_families(hermetic):
    """The two slug domains are real: 'Booz Allen, Inc.' is
    booz_allen_inc canonically (client dir, calibration, receipt,
    compose CLI) and booz_allen__inc in the legacy press-artifact family
    (sweep, reports, deltas). The REAL adapter -> refresh flow must
    respect both; nothing here mocks path resolution."""
    from datetime import datetime, timezone

    from agents.assessment_chain import SubprocessRunnerAdapter
    display = "Booz Allen, Inc."
    slug = "booz_allen_inc"            # canonical family
    press = "booz_allen__inc"          # press-artifact family
    root = hermetic
    (root / "clients" / slug).mkdir(parents=True)
    (root / "clients" / slug / "profile.json").write_text(json.dumps({
        "client_name": display,
        "capability_terms": {
            "core": ["computer-aided dispatch", "records management system",
                     "dispatch system", "incident reporting"],
            "adjacent": ["case management"], "excluded": []},
        "naics_boundary": ["541512"],
    }), encoding="utf-8")
    sweep = _sweep()
    sweep["client"] = display
    sweep["search_scope"] = {"all": True}
    (root / "data" / "cleaned" / f"searches_{press}.json").write_text(
        json.dumps(sweep), encoding="utf-8")
    # The real watch (2026-07-24) resolves the APPROVED packet through the
    # legacy press family for this display name; both slug domains stay real.
    from agents.decisions.schemas import (
        IntakeStrategy, Keyword, KeywordCategory,
    )
    from agents.review import ReviewPacket, ReviewStatus
    packet = ReviewPacket(
        client_name=display,
        status=ReviewStatus.APPROVED,
        strategy=IntakeStrategy(
            client_name=display,
            pursuit_strategy="Find evidence-bound federal requirements.",
            keywords=[Keyword(
                term="computer-aided dispatch",
                category=KeywordCategory.TECHNOLOGY,
                rationale="Approved capability.")],
            inferred_naics=["541512"],
            target_agencies=["Department of Justice"],
            confidence=0.8,
            sources_reviewed=["https://example.gov/source"],
            review_gate="Analyst approval required."),
        decided_at=datetime(2026, 7, 18, 12, 0, tzinfo=timezone.utc),
        search_scope={"all": True})
    (root / "data" / "review" / f"{press}.review.json").write_text(
        packet.model_dump_json(indent=2) + "\n", encoding="utf-8")

    adapter = SubprocessRunnerAdapter(
        root=root, python=sys.executable,
        clock=lambda: datetime(2026, 7, 19, 0, 0, tzinfo=timezone.utc))
    outcome = adapter.run("GATING_PRESS", slug=slug, mode="refresh")
    assert outcome.success is True, (outcome.reason,
                                     (outcome.stderr or "")[-1200:])
    assert outcome.returncode == 2
    # canonical-family artifacts
    receipt_path = (root / "data" / "state" / "relevance"
                    / f"{slug}.run.json")
    assert receipt_path.exists()
    assert (root / "data" / "state" / "relevance"
            / f"{slug}.calibration.csv").exists()
    assert (root / "clients" / slug / "signal_board_content.json").exists()
    assert (root / "clients" / slug / "compose_trail.md").exists()
    # press-family artifacts
    assert (root / "data" / "reports"
            / f"{press}.federal_opportunity_signals.DO-NOT-SEND.html"
            ).exists()
    assert (root / "data" / "state" / "deltas" / press).is_dir()
    assert not (root / "data" / "state" / "deltas" / slug).exists()
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    assert receipt["client_name"] == display
    assert receipt["slug"] == slug
