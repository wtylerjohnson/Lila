"""Hermetic-test guardrails: no test may touch the real SAM cache, ledger,
or the operator's live sam.gov quota."""

import os
from urllib.parse import urlsplit

import pytest


def _flag(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in {"1", "on", "true", "yes"}


# --------------------------------------------------------------------------- #
# SUITE INTEGRITY RECEIPTS (2026-08-06)
#
# Two things the suite could not see about itself, both found the night the
# competitive band merged:
#
#   1. A module whose TOP-LEVEL pytest.importorskip misses collapses to one
#      skip, and its tests are never collected or counted. 79 tests were
#      silently absent on the operator's machine (3,789 collected against
#      3,868 on a fully provisioned interpreter) with nothing in the output
#      naming which files went missing.
#   2. A press test reached apfs-cloud.dhs.gov mid-suite because the
#      operator's .env armed an adapter the conftest had not isolated. A
#      suite that touches the live internet is not deterministic, and
#      REPORT_CONTRACT §4 certifies five presses off one commit on exactly
#      that premise.
#
# Both are DISCLOSED and never change behaviour: the receipt prints, the run
# proceeds. LILA_SUITE_STRICT=1 turns either into a nonzero exit, which is
# what a gate run sets. Same posture as the term-yield receipt: the names
# are the receipt, the counts are only the index.
# --------------------------------------------------------------------------- #

_SKIPPED_MODULES: list[tuple[str, str]] = []
_LIVE_HTTP: list[tuple[str, str]] = []


def pytest_collectreport(report):
    """A whole file that never collected is the thing worth naming."""
    if not report.skipped:
        return
    longrepr = getattr(report, "longrepr", None)
    reason = ""
    if isinstance(longrepr, tuple) and len(longrepr) == 3:
        reason = str(longrepr[2])
    elif longrepr is not None:
        reason = str(longrepr)
    reason = reason.replace("Skipped: ", "").strip() or "no reason given"
    _SKIPPED_MODULES.append((report.nodeid, reason))


@pytest.fixture(autouse=True)
def _observe_live_http(request, monkeypatch):
    """Record every call that leaves the machine, and name the test that made
    it. `_request` backs get_json/post_json; `get_text` builds its own client,
    so both seams are wrapped."""
    from tools.api import _http

    real_request, real_text = _http._request, _http.get_text
    offline = _flag("LILA_SUITE_OFFLINE")

    def _note(url: str) -> None:
        host = urlsplit(str(url)).netloc or str(url)
        _LIVE_HTTP.append((host, request.node.nodeid))
        if offline:
            raise RuntimeError(
                f"offline suite: {request.node.nodeid} tried to reach {host}. "
                "Isolate it the way the guardrails above isolate the rest, or "
                "arm the adapter inside the test and say so in its name.")

    def _watched_request(method, url, **kw):
        _note(url)
        return real_request(method, url, **kw)

    def _watched_text(url, **kw):
        _note(url)
        return real_text(url, **kw)

    monkeypatch.setattr(_http, "_request", _watched_request)
    monkeypatch.setattr(_http, "get_text", _watched_text)

    # APOLLO IS A SECOND SEAM. tools/apollo_targets.py builds its own
    # urllib request and never touches tools.api._http, so every guard
    # above was blind to it. Wrapping it here means an Apollo call during
    # the suite is named in the receipt like any other live call, and under
    # LILA_SUITE_OFFLINE it raises instead of spending credits.
    try:
        from tools import apollo_targets as _apollo
    except Exception:                                    # pragma: no cover
        return
    real_post = _apollo._http_post

    def _watched_post(url, payload, key, timeout=_apollo.DEFAULT_TIMEOUT):
        _note(url)
        return real_post(url, payload, key, timeout)

    monkeypatch.setattr(_apollo, "_http_post", _watched_post)


def pytest_terminal_summary(terminalreporter, exitstatus, config):
    tr = terminalreporter
    if _SKIPPED_MODULES:
        tr.write_sep("-", "modules skipped whole: their tests never collected")
        for nodeid, reason in _SKIPPED_MODULES:
            tr.write_line(f"  {nodeid}  ({reason})")
        tr.write_line(
            f"  {len(_SKIPPED_MODULES)} files absent from this run. A count "
            "compared across machines means nothing until this list is empty.")
    if _LIVE_HTTP:
        by_host: dict[str, set[str]] = {}
        for host, node in _LIVE_HTTP:
            by_host.setdefault(host, set()).add(node)
        tr.write_sep("-", "live network reached during the suite")
        for host, nodes in sorted(by_host.items()):
            calls = sum(1 for h, _ in _LIVE_HTTP if h == host)
            tr.write_line(f"  {host}: {calls} calls from {len(nodes)} tests")
            for node in sorted(nodes)[:3]:
                tr.write_line(f"      {node}")
            if len(nodes) > 3:
                tr.write_line(f"      ... and {len(nodes) - 3} more")
    if (_SKIPPED_MODULES or _LIVE_HTTP) and _flag("LILA_SUITE_STRICT"):
        tr.write_line("")
        tr.write_line(
            "LILA_SUITE_STRICT: this run is not certifiable. A press gate "
            "needs the whole suite, offline, or the five-press claim is "
            "measuring an unknown subset.")


def pytest_sessionfinish(session, exitstatus):
    if (_SKIPPED_MODULES or _LIVE_HTTP) and _flag("LILA_SUITE_STRICT"):
        session.exitstatus = 1


@pytest.fixture(autouse=True)
def _isolate_sam_state(tmp_path, monkeypatch):
    monkeypatch.setenv("LILA_ENABLE_AGENCY_PROGRAM_DOCUMENTS", "off")
    monkeypatch.setenv("LILA_PROGRAM_DOCUMENT_CACHE_DIR", str(tmp_path / "program_documents"))
    monkeypatch.setenv("LILA_INVESTIGATION_DIR", str(tmp_path / "investigations"))
    # THE KEYS COME OUT FIRST (2026-07-28). This fixture isolated the cache
    # and the ledger but left SAM_GOV_API_KEY and SAM_GOV_API_KEY_2 in the
    # environment, so the suite reached the LIVE api and spent the operator's
    # real budget: 10 calls/day per key, 20 across the two, exhausted in a
    # single afternoon of repeated suite runs. sam.gov answered HTTP 429
    # "You have exceeded your quota" with access restored the next day.
    #
    # It also silently broke two tests that assume a key-free machine.
    # daily_quota() multiplies the per-key figure by configured_keys(), so
    # LILA_SAM_DAILY_QUOTA=10 became 20 and the census guard never tripped;
    # SamGovSource.__init__ appends SAM_GOV_API_KEY_2 unconditionally, so a
    # test constructing SamGovSource(api_key="k") got a pool of two and the
    # throttle test counted a rotation it never asked for.
    #
    # Any test that genuinely needs live access must set these itself and
    # say so in its own name.
    monkeypatch.delenv("SAM_GOV_API_KEY", raising=False)
    monkeypatch.delenv("SAM_GOV_API_KEY_2", raising=False)
    monkeypatch.setenv("LILA_SAM_CACHE_DIR", str(tmp_path / "sam_cache"))
    monkeypatch.setenv("LILA_SAM_LEDGER", str(tmp_path / "sam_ledger.json"))
    monkeypatch.setenv("LILA_NOTICE_CACHE_DIR", str(tmp_path / "notice_cache"))
    monkeypatch.setenv("LILA_FORECAST_CACHE_DIR", str(tmp_path / "forecast_cache"))
    monkeypatch.setenv("LILA_FORECAST_SNAP_DIR", str(tmp_path / "forecast_snaps"))
    monkeypatch.setenv("LILA_CONTACT_GRAPH_DIR", str(tmp_path / "contact_graph"))
    monkeypatch.setenv("LILA_HANDOFF_DIR", str(tmp_path / "handoff"))
    monkeypatch.setenv("LILA_RESEARCH_DATA_DIR", str(tmp_path / "research_data"))
    monkeypatch.setenv("LILA_SWEEP_SNAP_DIR", str(tmp_path / "sweep_snapshots"))
    # entity crosswalk (L14): hermetic tests never read the evolving seed
    # crosswalk or write the real unresolved traffic log
    monkeypatch.setenv("LILA_ENTITIES_DIR", str(tmp_path / "entities"))
    # Notice store + conference roster (2026-07-30): the press events lane
    # reads both, so without isolation every full-press unit test screened
    # the REAL 78K-row notice store (the suite went 1:31 -> 6:51 before this
    # line existed) and tier C read the real conference roster and then hit
    # the LIVE network to verify its URLs inside offline tests.
    monkeypatch.setenv("LILA_NOTICE_STORE_DIR", str(tmp_path / "notice_store"))
    # APOLLO (2026-08-06). The targeting supply lane spends REAL MONEY: a
    # work-email match is ~1 credit and a phone reveal ~8, and 16 credits
    # were burned in one afternoon on numbers that could not be retrieved.
    # Nothing above isolated it, so a developer with APOLLO_API_KEY in their
    # environment could have had the suite spend their balance. Same
    # doctrine as the SAM keys: the key comes out, the switches go off, the
    # store is redirected, and a test that genuinely wants an armed Apollo
    # arms it ITSELF and says so in its own name (see apollo_enabled_env).
    monkeypatch.delenv("APOLLO_API_KEY", raising=False)
    monkeypatch.setenv("LILA_ENABLE_APOLLO", "off")
    monkeypatch.setenv("LILA_APOLLO_ALLOW_PHONE_REVEAL", "off")
    monkeypatch.setenv("LILA_TARGETS_STORE_DIR", str(tmp_path / "targets"))
    monkeypatch.setenv("LILA_TARGETS_PLAN_DIR", str(tmp_path / "targets_plan"))
    # The credit cap is an operator gate value; a test must never inherit a
    # raised ceiling from the developer's shell.
    monkeypatch.delenv("LILA_APOLLO_CREDIT_CAP", raising=False)
    monkeypatch.setenv("LILA_EVENT_ROSTER_DIR", str(tmp_path / "event_roster"))
    monkeypatch.setenv("LILA_TERM_YIELD_LOG",
                       str(tmp_path / "term_yield_log.jsonl"))
    # The Acquisition Gateway adapter ships enabled (verified public
    # endpoint), and with the forecast cache isolated above every cache miss
    # in a test becomes a LIVE ~5-minute paged crawl: one press test cost
    # 301s the day the adapter joined run_l4. Same doctrine as the SAM keys:
    # a test that genuinely needs live access sets this itself and says so.
    monkeypatch.setenv("LILA_ENABLE_ACQ_GATEWAY", "off")
    # DHS APFS, the same hole one adapter over (2026-08-06). It ships
    # enabled_default=False, so a key-free machine never noticed. The
    # operator's .env carries LILA_ENABLE_DHS_APFS=on, and with the forecast
    # cache isolated above, run_l4 called apfs-cloud.dhs.gov mid-suite: the
    # press test test_the_press_prefers_the_store_and_falls_back_loudly
    # passed only where that host was reachable and failed with HTTP 403
    # where it was not. Whether a unit test passes must not depend on the
    # operator's toggle or the network. A test that genuinely needs the live
    # adapter arms it itself and says so in its own name.
    monkeypatch.setenv("LILA_ENABLE_DHS_APFS", "off")
    # forecast store/coverage + recompete calendar (L15/L16): same isolation
    monkeypatch.setenv("LILA_FORECAST_STORE_DIR", str(tmp_path / "forecast_store"))
    monkeypatch.setenv("LILA_FORECAST_COVERAGE",
                       str(tmp_path / "forecast_coverage.json"))
    monkeypatch.setenv("LILA_RECOMPETE_DIR", str(tmp_path / "recompete"))
    # Approving a packet now GENERATES clients/<slug>/*.json. Without this a
    # test that approves anything writes into the real client tree.
    monkeypatch.setenv("LILA_CLIENTS_DIR", str(tmp_path / "clients"))
    # Hermetic extracts are a few hundred bytes. The production floor that
    # refuses a truncated file would refuse every fixture, so tests run at 0
    # and the refusal test sets its own floor explicitly.
    monkeypatch.setenv("LILA_MIN_EXTRACT_BYTES", "0")
    # The extract cache/archive and the FY-archive workdir (2026-08-04): with
    # the floor zeroed above, any code path that reaches download_extract or
    # the rotation during a test would promote whatever the live upstream
    # serves INTO THE REAL data/ dirs and gzip real rotated days. During the
    # July-August outage that is exactly how 685-byte poison reached the
    # permanent archive. Same doctrine as the notice store above: isolate by
    # default; a test that genuinely needs the real dirs sets these itself.
    monkeypatch.setenv("LILA_SAM_EXTRACT_DIR", str(tmp_path / "sam_extract"))
    monkeypatch.setenv("LILA_SAM_EXTRACT_ARCHIVE_DIR",
                       str(tmp_path / "sam_extract_archive"))
    monkeypatch.setenv("LILA_SAM_ARCHIVE_DIR", str(tmp_path / "sam_archive"))
    # THE NOTICE STORE. This was never isolated: the store's own tests pass an
    # explicit conn so nothing noticed, but the moment the press started
    # reading the store, every press test began reading the PRODUCTION
    # database of 78,577 real notices. A retrieval test asserting a degraded
    # L1 lane started passing "live" off real data.
    monkeypatch.setenv("LILA_NOTICE_STORE_DIR", str(tmp_path / "notice_store"))
    # APOLLO (2026-08-06). The targeting supply lane spends REAL MONEY: a
    # work-email match is ~1 credit and a phone reveal ~8, and 16 credits
    # were burned in one afternoon on numbers that could not be retrieved.
    # Nothing above isolated it, so a developer with APOLLO_API_KEY in their
    # environment could have had the suite spend their balance. Same
    # doctrine as the SAM keys: the key comes out, the switches go off, the
    # store is redirected, and a test that genuinely wants an armed Apollo
    # arms it ITSELF and says so in its own name (see apollo_enabled_env).
    monkeypatch.delenv("APOLLO_API_KEY", raising=False)
    monkeypatch.setenv("LILA_ENABLE_APOLLO", "off")
    monkeypatch.setenv("LILA_APOLLO_ALLOW_PHONE_REVEAL", "off")
    monkeypatch.setenv("LILA_TARGETS_STORE_DIR", str(tmp_path / "targets"))
    monkeypatch.setenv("LILA_TARGETS_PLAN_DIR", str(tmp_path / "targets_plan"))
    # The credit cap is an operator gate value; a test must never inherit a
    # raised ceiling from the developer's shell.
    monkeypatch.delenv("LILA_APOLLO_CREDIT_CAP", raising=False)


@pytest.fixture()
def apollo_enabled_env(monkeypatch, tmp_path):
    """OPT IN, BY NAME, TO A MOCKED ARMED APOLLO.

    The autouse fixture above disarms Apollo for every test. A test that
    needs the switch on must request THIS fixture, which arms only the
    switch and a throwaway key and still leaves the network seam watched.
    It deliberately does not enable the phone reveal: that path costs eight
    times as much and has its own gate, so a test wanting it says so
    separately.

    The point is that arming Apollo is visible in the test's signature, so
    `grep apollo_enabled_env tests/` lists every test that could ever spend.
    """
    monkeypatch.setenv("LILA_ENABLE_APOLLO", "on")
    monkeypatch.setenv("APOLLO_API_KEY", "test-key-never-a-real-key")
    monkeypatch.setenv("LILA_TARGETS_STORE_DIR", str(tmp_path / "targets"))
    return {"key": "test-key-never-a-real-key"}
