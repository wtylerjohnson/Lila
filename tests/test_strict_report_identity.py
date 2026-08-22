"""Cycle 4 (2026-07-12): strict run identity through QA, caches, and release.

Doctrine under test:
- The agency runner's final scoped write rematerializes an EXISTING strict
  run (pointer-gated, creation-free) so a clean report always postdates its
  pointer; a client without a pointer keeps exact legacy behavior.
- Every path is dormant when no pointer exists: no pointer file is ever
  created agent-side (activation is the operator's cutover decision).
"""

from __future__ import annotations

import json
import os
import sys


sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import run_agency_report  # noqa: E402
from agents.assess.ledger import (  # noqa: E402
    assess_client_storage_key,
    current_assess_pointer_path,
)


def _pointer_dir(tmp_path, monkeypatch, client="Testco"):
    state_dir = tmp_path / "assess_runs"
    monkeypatch.setenv("LILA_ASSESS_RUN_DIR", str(state_dir))
    return state_dir / assess_client_storage_key(client)


class TestRematerializeStrictRun:
    def test_no_pointer_is_a_creation_free_noop(self, tmp_path, monkeypatch):
        root = _pointer_dir(tmp_path, monkeypatch)
        called = []
        monkeypatch.setattr(
            "agents.assess.ledger.materialize_current_assess_run",
            lambda *a, **k: called.append((a, k)))
        outcome = run_agency_report._rematerialize_strict_run(
            "Testco", "agency_dhs", str(tmp_path / "sweep.json"))
        assert outcome.startswith("absent")
        assert called == []
        # dormancy: the runner never activates a cutover pointer itself
        assert not root.exists()
        assert not current_assess_pointer_path("Testco", "agency_dhs").exists()

    def test_existing_pointer_triggers_refresh_with_scoped_sweep(
            self, tmp_path, monkeypatch):
        root = _pointer_dir(tmp_path, monkeypatch)
        root.mkdir(parents=True)
        (root / "agency_dhs.current.json").write_text(
            json.dumps({"run_id": "assess:v1:old"}), encoding="utf-8")
        calls = []

        class _Run:
            run_id = "assess:v1:fresh"

        def _fake_materialize(client_name, **kwargs):
            calls.append((client_name, kwargs))
            return _Run(), tmp_path / "run.json", ("one note",)

        monkeypatch.setattr(
            "agents.assess.ledger.materialize_current_assess_run",
            _fake_materialize)
        sweep = tmp_path / "searches_testco.agency_dhs.json"
        outcome = run_agency_report._rematerialize_strict_run(
            "Testco", "agency_dhs", str(sweep))
        assert "refreshed assess:v1:fresh" in outcome
        assert "1 diagnostic(s)" in outcome
        # the CAS repair (item 23) rides the refresh: observed pointer bytes
        assert calls == [("Testco", {
            "sweep_path": str(sweep),
            "expected_pointer": {"bytes": json.dumps(
                {"run_id": "assess:v1:old"}).encode("utf-8")},
        })]

    def test_refresh_failure_is_loud_but_never_raises(
            self, tmp_path, monkeypatch):
        root = _pointer_dir(tmp_path, monkeypatch)
        root.mkdir(parents=True)
        (root / "agency_dhs.current.json").write_text(
            json.dumps({"run_id": "assess:v1:old"}), encoding="utf-8")

        def _boom(*a, **k):
            raise RuntimeError("ledger adapter exploded")

        monkeypatch.setattr(
            "agents.assess.ledger.materialize_current_assess_run", _boom)
        outcome = run_agency_report._rematerialize_strict_run(
            "Testco", "agency_dhs", str(tmp_path / "sweep.json"))
        assert outcome.startswith("FAILED (RuntimeError")
        assert "holds closed" in outcome

    def test_unresolvable_designator_skips_without_raising(
            self, tmp_path, monkeypatch):
        _pointer_dir(tmp_path, monkeypatch)
        outcome = run_agency_report._rematerialize_strict_run(
            "Testco", "agency_", str(tmp_path / "sweep.json"))
        assert outcome.startswith("skipped (pointer state unavailable")


class TestIdentityTokenRules:
    """Cycle 4: CURRENT -> run id; INVALID -> strict:invalid; ABSENT -> None
    (and None means the caller OMITS the key, keeping legacy digests
    byte-identical)."""

    def test_token_per_state(self):
        from types import SimpleNamespace
        from agents.assess.live_report import LiveReportState
        from agents.reports.compose_cache import identity_token
        current = SimpleNamespace(state=LiveReportState.CURRENT,
                                  run_id="assess:v1:abc")
        assert identity_token(current) == "assess:v1:abc"
        invalid = SimpleNamespace(state=LiveReportState.INVALID, run_id=None)
        assert identity_token(invalid) == "strict:invalid"
        absent = SimpleNamespace(state=LiveReportState.ABSENT, run_id=None)
        assert identity_token(absent) is None
        assert identity_token(None) is None

    def test_unresolved_token_is_pointer_gated(self, tmp_path, monkeypatch):
        from agents.reports.compose_cache import unresolved_token
        root = _pointer_dir(tmp_path, monkeypatch)
        # no pointer file: a legacy client's transient error stays legacy
        assert unresolved_token("Testco", "agency_dhs") is None
        root.mkdir(parents=True)
        (root / "agency_dhs.current.json").write_text("{}", encoding="utf-8")
        assert unresolved_token("Testco", "agency_dhs") == "strict:unresolved"
        # an unresolvable designator can never prove legacy: fail closed
        assert unresolved_token("Testco", "agency_") == "strict:unresolved"

    def test_compose_identity_token_survives_resolver_crash(
            self, tmp_path, monkeypatch):
        from agents.reports import compose_cache

        def _boom(*a, **k):
            raise RuntimeError("resolver down")

        monkeypatch.setattr(
            "agents.assess.live_report.resolve_current_live_report", _boom)
        root = _pointer_dir(tmp_path, monkeypatch)
        assert compose_cache.compose_identity_token(
            "Testco", {}, None, designator="all") is None
        root.mkdir(parents=True)
        (root / "all.current.json").write_text("{}", encoding="utf-8")
        assert compose_cache.compose_identity_token(
            "Testco", {}, None, designator="all") == "strict:unresolved"


class TestAgencyContentFingerprint:
    def test_legacy_fingerprint_is_byte_identical_without_token(self):
        """A pre-cutover cache entry on disk (e.g. NETSCOUT's DHS content)
        must still HIT after this change: with no strict token the payload
        is exactly the pre-Cycle-4 formula."""
        import hashlib

        agency = {"name": "Department of Homeland Security", "abbr": "DHS",
                  "parent": None}
        scoped = {"client": "Testco", "results": {}}
        old_payload = {
            "version": run_agency_report._CONTENT_CACHE_VERSION,
            "client": "Testco",
            "agency": {k: agency.get(k) for k in ("name", "abbr", "parent")},
            "scoped_artifact": scoped,
            "approved_strategy": None,
            "capability_profile": None,
        }
        old_formula = hashlib.sha256(json.dumps(
            old_payload, sort_keys=True, separators=(",", ":"),
            ensure_ascii=False, default=str).encode("utf-8")).hexdigest()
        assert run_agency_report._content_fingerprint(
            client="Testco", agency=agency, scoped_artifact=scoped,
            strategy=None, profile=None) == old_formula
        assert run_agency_report._content_fingerprint(
            client="Testco", agency=agency, scoped_artifact=scoped,
            strategy=None, profile=None, strict_run_id=None) == old_formula

    def test_strict_token_reprices_the_fingerprint(self):
        agency = {"name": "DHS", "abbr": "DHS", "parent": None}
        base = dict(client="Testco", agency=agency,
                    scoped_artifact={"results": {}}, strategy=None,
                    profile=None)
        legacy = run_agency_report._content_fingerprint(**base)
        strict = run_agency_report._content_fingerprint(
            **base, strict_run_id="assess:v1:abc")
        flipped = run_agency_report._content_fingerprint(
            **base, strict_run_id="assess:v1:def")
        assert legacy != strict != flipped


class TestComposeCacheStrictIdentity:
    def _review(self):
        from agents.reports.capture_brief import FullPictureReview
        return FullPictureReview(
            thesis_directive="Lead with the DHS lane.",
            storyline_connections=["sweep to award history"],
            must_include=["N1"],
            emphasis_ranking=["N1"],
        )

    def test_review_cache_reprices_on_strict_identity(
            self, tmp_path, monkeypatch):
        import agents.reports.capture_brief as cb
        monkeypatch.setenv("LILA_COMPOSE_CACHE_DIR", str(tmp_path / "cc"))
        calls = []
        monkeypatch.setattr(
            cb, "compose_full_picture",
            lambda client, everything, engine=None:
                calls.append(1) or self._review())
        everything = {"fact_pack": {"facts": []}}
        cb.cached_full_picture_review(
            "Testco", scope="all", everything=everything)
        cb.cached_full_picture_review(
            "Testco", scope="all", everything=everything)
        assert len(calls) == 1  # unchanged legacy inputs stay a HIT
        cb.cached_full_picture_review(
            "Testco", scope="all", everything=everything,
            strict_identity=None)
        assert len(calls) == 1  # None omits the key: same legacy entry
        cb.cached_full_picture_review(
            "Testco", scope="all", everything=everything,
            strict_identity="assess:v1:abc")
        assert len(calls) == 2  # strict truth re-prices the review once
        cb.cached_full_picture_review(
            "Testco", scope="all", everything=everything,
            strict_identity="assess:v1:abc")
        assert len(calls) == 2  # and then caches under the strict key

    def test_sectioned_compose_reprices_on_strict_identity(
            self, tmp_path, monkeypatch):
        import agents.reports.capture_brief as cb
        from agents.reports.facts import FactPack
        monkeypatch.setenv("LILA_COMPOSE_CACHE_DIR", str(tmp_path / "cc"))
        pack = FactPack(client_name="Testco", as_of="2026-07-12", facts=[])
        narrative = {
            "client_name": "Testco", "subtitle": "Federal lane",
            "meta_prepared_for": "Prepared for Testco",
            "thesis": ["one", "two", "three"],
            "stats": [{"number": str(n), "context": "ctx"}
                      for n in range(5)],
            "action_callout": "Act by Aug 1, 2026.",
            "budget_bars": [], "kill_line_opps": "",
            "kill_line_news": "", "footer_verification": "verified",
        }
        news = {"news_funding": [], "news_threat": [], "news_agency": [],
                "news_market": []}
        boards = {"opportunities": [], "pipeline": [],
                  "partner_callout": "drive the prime motion"}
        payload_by_section = {"narrative": narrative, "news": news,
                              "boards": boards}
        calls = []

        class _Engine:
            def deliberate(self, *, layer, system_prompt, context, schema):
                section = layer.rsplit("/", 1)[-1]
                calls.append(section)
                return schema.model_validate(payload_by_section[section])

        for _ in range(2):
            content, hits = cb.compose_capture_brief_sectioned(
                pack, engine=_Engine(), client="Testco", scope="all")
        assert calls == ["narrative", "news", "boards"]
        assert all(hits.values())
        content, hits = cb.compose_capture_brief_sectioned(
            pack, engine=_Engine(), client="Testco", scope="all",
            strict_identity="assess:v1:abc")
        assert calls == ["narrative", "news", "boards"] * 2
        assert not any(hits.values())
        assert content.client_name == "Testco"


class TestDraftResumeFingerprint:
    def test_legacy_fingerprint_shape_unchanged(self):
        import run_capture_brief
        from types import SimpleNamespace
        pack = SimpleNamespace(facts=[1, 2, 3], as_of=None)
        from datetime import date as _date
        legacy = run_capture_brief._draft_fingerprint(pack, None)
        assert legacy == [3, _date.today().isoformat()]
        strict = run_capture_brief._draft_fingerprint(pack, "assess:v1:abc")
        assert strict == [3, _date.today().isoformat(), "assess:v1:abc"]


def _projection(state, notices=()):
    from agents.assess.live_report import LiveReportProjection
    return LiveReportProjection(
        state=state, client_name="Testco", scope_designator="all",
        run_id="assess:v1:abc", notices=tuple(notices))


def _strict_notice(notice_id, *, urls, actionable=True, current=None,
                   excerpt="packet capture requirement"):
    """Duck-typed strict record inside a REAL LiveReportNotice/-Projection
    (both plain frozen dataclasses); .actionable filters on the real enums."""
    from types import SimpleNamespace
    from agents.assess.contracts import LiveClassification, LiveRecommendation
    from agents.assess.live_report import LiveReportNotice
    record = SimpleNamespace(
        notice_id=notice_id,
        classification=(LiveClassification.BID_NOW if actionable
                        else LiveClassification.UNSCREENED),
        recommendation=(LiveRecommendation.PURSUE if actionable
                        else LiveRecommendation.RESEARCH),
        authoritative_evidence=tuple(
            SimpleNamespace(source_url=url) for url in urls),
        requirement_excerpt=excerpt,
    )
    return LiveReportNotice(record=record, current=current or {},
                            postings=())


class TestSection01Reconciliation:
    def _content(self, entries):
        from types import SimpleNamespace
        return SimpleNamespace(opportunities=[
            SimpleNamespace(notice_id=nid, url=url, headline=f"Card {nid}")
            for nid, url in entries])

    def test_legacy_truth_reconciles_nothing(self):
        from agents.assess.live_report import LiveReportState
        from agents.reports.capture_brief import reconcile_section01_strict
        content = self._content([("N9", "https://sam.gov/opp/N9/view")])
        assert reconcile_section01_strict(content, None) == []
        absent = _projection(LiveReportState.ABSENT)
        assert reconcile_section01_strict(content, absent) == []

    def test_invalid_state_flags_every_composed_live_card(self):
        from agents.assess.live_report import LiveReportState
        from agents.reports.capture_brief import reconcile_section01_strict
        content = self._content([("N1", "https://sam.gov/opp/N1/view"),
                                 ("N2", "https://sam.gov/opp/N2/view")])
        problems = reconcile_section01_strict(
            content, _projection(LiveReportState.INVALID))
        assert len(problems) == 2
        assert all("held closed" in p for p in problems)

    def test_current_state_enforces_the_actionable_allowlist(self):
        from agents.assess.live_report import LiveReportState
        from agents.reports.capture_brief import reconcile_section01_strict
        url = "https://sam.gov/opp/N1/view"
        projection = _projection(LiveReportState.CURRENT, [
            _strict_notice("N1", urls=[url]),
            _strict_notice("N3", urls=["https://sam.gov/opp/N3/view"],
                           actionable=False),
        ])
        clean = self._content([("N1", url)])
        assert reconcile_section01_strict(clean, projection) == []
        foreign = self._content([("N7", "https://sam.gov/opp/N7/view")])
        problems = reconcile_section01_strict(foreign, projection)
        assert len(problems) == 1 and "outside the strict NOTICE allowlist" \
            in problems[0]
        # monitor-grade strict records are NOT Section-01 eligible
        monitor = self._content([("N3", "https://sam.gov/opp/N3/view")])
        problems = reconcile_section01_strict(monitor, projection)
        assert len(problems) == 1 and "outside the strict" in problems[0]
        # right id, wrong URL: the evidence join must be exact
        drifted = self._content([("N1", "https://sam.gov/opp/N1/history")])
        problems = reconcile_section01_strict(drifted, projection)
        assert len(problems) == 1 and "does not match the strict NOTICE " \
            "evidence" in problems[0]

    def test_zero_composed_opportunities_is_always_clean(self):
        from agents.assess.live_report import LiveReportState
        from agents.reports.capture_brief import reconcile_section01_strict
        content = self._content([])
        assert reconcile_section01_strict(
            content, _projection(LiveReportState.CURRENT)) == []
        assert reconcile_section01_strict(
            content, _projection(LiveReportState.INVALID)) == []


class TestReportFitTraces:
    def _profile(self):
        from tests.test_assess_ledger import _profile
        return _profile()

    def _results(self):
        return {
            "sam.gov": [{
                "source_id": "N1",
                "title": "Network packet capture modernization",
                "agency": "Department of Homeland Security",
                "naics_code": "541512",
                "raw_payload": {"description_snippet": "packet capture"},
            }],
            "triage": {"N1": {"verdict": "pursue",
                              "reason": "legacy triage reason"}},
        }

    def test_legacy_truth_still_builds_from_triage(self):
        import run_capture_brief
        traces = run_capture_brief._report_fit_traces(
            self._profile(), self._results(), None, None)
        assert "N1" in traces
        assert traces["N1"]["screen_inference"] == "legacy triage reason"

    def test_current_state_traces_come_from_the_ledger_not_triage(self):
        import run_capture_brief
        from agents.assess.live_report import LiveReportState
        results = self._results()
        # triage offers a DIFFERENT notice: it must never earn a trace
        results["triage"]["N9"] = {"verdict": "pursue", "reason": "stale"}
        projection = _projection(LiveReportState.CURRENT, [
            _strict_notice(
                "N1", urls=["https://sam.gov/opp/N1/view"],
                current=results["sam.gov"][0]),
        ])
        traces = run_capture_brief._report_fit_traces(
            self._profile(), results, projection, "assess:v1:abc")
        assert set(traces) == {"N1"}
        assert traces["N1"]["screen_inference"].startswith(
            "human-reviewed requirement span: packet capture")
        assert traces["N1"]["matched_terms"]

    def test_non_current_strict_token_disables_the_legacy_rebuild(self):
        import run_capture_brief
        from agents.assess.live_report import LiveReportState
        invalid = _projection(LiveReportState.INVALID)
        assert run_capture_brief._report_fit_traces(
            self._profile(), self._results(), invalid,
            "strict:invalid") == {}
        assert run_capture_brief._report_fit_traces(
            self._profile(), self._results(), None,
            "strict:unresolved") == {}

    def test_no_profile_yields_no_traces(self):
        import run_capture_brief
        assert run_capture_brief._report_fit_traces(
            None, self._results(), None, None) == {}


class TestReleaseRunIdPairing:
    """Cycle 4 [CONTRACT SURFACE] release leg: with a current pointer, the
    stable-family QA sidecar must stamp the exact current run id; mtime
    freshness alone no longer releases. No pointer -> stamps are ignored and
    legacy behavior holds exactly."""

    POINTER_NS = 1_700_000_000_000_000_000

    def _fixture(self, tmp_path, monkeypatch, *, pointer=True,
                 pointer_body=None, identity="assess:v1:current"):
        """identity stubs the T5 seam (agents.assess.current_run_identity):
        a string is the validated current run id; None is the seam's answer
        for a present-but-invalid pointer. The seam's own validation is
        covered by its dedicated suite; these tests cover the release leg's
        consumption of it."""
        import hashlib
        import agents.reports.release as release
        monkeypatch.setattr(
            release, "assess_approval_for_release",
            lambda *a, **k: (None, "approved", []))
        monkeypatch.setattr(
            "agents.assess.current_run_identity",
            lambda *a, **k: (
                {"run_id": identity, "pointer_digest": "pd",
                 "blocker_manifest_sha256": "bm",
                 "persisted_at": "2026-07-12T00:00:00+00:00"}
                if identity else None))
        report_dir = tmp_path / "reports"
        report_dir.mkdir()
        review_dir = tmp_path / "review"
        review_dir.mkdir()
        root = _pointer_dir(tmp_path, monkeypatch)
        if pointer:
            root.mkdir(parents=True)
            path = root / "all.current.json"
            path.write_text(
                pointer_body if pointer_body is not None
                else json.dumps({"run_id": "assess:v1:current"}),
                encoding="utf-8")
            os.utime(path, ns=(self.POINTER_NS, self.POINTER_NS))
        self._hashlib = hashlib
        return release, report_dir, review_dir

    def _stable(self, report_dir, *, stamp=None, newer=True):
        html = report_dir / "testco.federal_opportunity_assessment.html"
        body = f"<html>build stamp={stamp}</html>"
        html.write_text(body, encoding="utf-8")
        payload = {
            "state": "release",
            "html_sha256": self._hashlib.sha256(
                body.encode("utf-8")).hexdigest(),
        }
        if stamp:
            payload["assess_run_id"] = stamp
        qa = report_dir / "testco.federal_opportunity_assessment.qa.json"
        qa.write_text(json.dumps(payload), encoding="utf-8")
        when = self.POINTER_NS + (1_000_000_000 if newer else -1_000_000_000)
        os.utime(html, ns=(when, when))
        os.utime(qa, ns=(when, when))
        return html

    def test_no_pointer_ignores_stamps_and_keeps_legacy_behavior(
            self, tmp_path, monkeypatch):
        release, report_dir, review_dir = self._fixture(
            tmp_path, monkeypatch, pointer=False)
        self._stable(report_dir)  # unstamped legacy sidecar
        verdict = release.release_state(
            "Testco", report_dir=str(report_dir),
            review_dir=str(review_dir))
        assert verdict["releasable"] is True

    def test_matching_stamp_and_newer_html_release(
            self, tmp_path, monkeypatch):
        release, report_dir, review_dir = self._fixture(tmp_path, monkeypatch)
        html = self._stable(report_dir, stamp="assess:v1:current")
        verdict = release.release_state(
            "Testco", report_dir=str(report_dir),
            review_dir=str(review_dir))
        assert verdict["releasable"] is True
        assert verdict["path"] == str(html)

    def test_missing_stamp_fails_closed_under_a_pointer(
            self, tmp_path, monkeypatch):
        release, report_dir, review_dir = self._fixture(tmp_path, monkeypatch)
        self._stable(report_dir, stamp=None)
        verdict = release.release_state(
            "Testco", report_dir=str(report_dir),
            review_dir=str(review_dir))
        assert verdict["releasable"] is False
        assert "no strict run stamp" in verdict["reason"]

    def test_superseded_stamp_fails_closed(self, tmp_path, monkeypatch):
        release, report_dir, review_dir = self._fixture(tmp_path, monkeypatch)
        self._stable(report_dir, stamp="assess:v1:superseded")
        verdict = release.release_state(
            "Testco", report_dir=str(report_dir),
            review_dir=str(review_dir))
        assert verdict["releasable"] is False
        assert "does not certify the current strict Assess run" \
            in verdict["reason"]

    def test_sentinel_tokens_can_never_release(self, tmp_path, monkeypatch):
        release, report_dir, review_dir = self._fixture(tmp_path, monkeypatch)
        for sentinel in ("strict:invalid", "strict:unresolved"):
            self._stable(report_dir, stamp=sentinel)
            verdict = release.release_state(
                "Testco", report_dir=str(report_dir),
                review_dir=str(review_dir))
            assert verdict["releasable"] is False, sentinel

    def test_invalid_pointer_identity_fails_closed(
            self, tmp_path, monkeypatch):
        """A present pointer the T5 seam refuses to validate (malformed,
        drifted, cross-scope, torn) can never release, even when a stamped
        sidecar and fresh mtimes look right."""
        release, report_dir, review_dir = self._fixture(
            tmp_path, monkeypatch, pointer_body="{not json", identity=None)
        self._stable(report_dir, stamp="assess:v1:current")
        verdict = release.release_state(
            "Testco", report_dir=str(report_dir),
            review_dir=str(review_dir))
        assert verdict["releasable"] is False
        assert "does not validate to a run identity" in verdict["reason"]

    def test_stale_html_reports_the_freshness_reason_first(
            self, tmp_path, monkeypatch):
        release, report_dir, review_dir = self._fixture(tmp_path, monkeypatch)
        self._stable(report_dir, stamp="assess:v1:current", newer=False)
        verdict = release.release_state(
            "Testco", report_dir=str(report_dir),
            review_dir=str(review_dir))
        assert verdict["releasable"] is False
        assert "predates the current strict Assess run" in verdict["reason"]

    def test_view_family_has_no_sidecar_and_relies_on_freshness(
            self, tmp_path, monkeypatch):
        release, report_dir, review_dir = self._fixture(tmp_path, monkeypatch)
        view = report_dir / "testco.assessment.client.html"
        view.write_text("<html>view build</html>", encoding="utf-8")
        when = self.POINTER_NS + 1_000_000_000
        os.utime(view, ns=(when, when))
        verdict = release.release_state(
            "Testco", report_dir=str(report_dir),
            review_dir=str(review_dir))
        assert verdict["releasable"] is True
        assert verdict["family"] == "view"

    def test_release_state_return_shape_is_frozen(
            self, tmp_path, monkeypatch):
        """CONTRACT SURFACE: the run-id leg adds NO keys to the verdict."""
        release, report_dir, review_dir = self._fixture(tmp_path, monkeypatch)
        self._stable(report_dir, stamp="assess:v1:current")
        verdict = release.release_state(
            "Testco", report_dir=str(report_dir),
            review_dir=str(review_dir))
        assert set(verdict) == {
            "releasable", "path", "blocked_path", "reason",
            "approval_status", "approval_problems", "family", "updated",
            "preview_path"}


class TestReleaseDormancyUnderCorruptRunsDir:
    """Cycle 3 review finding: a pointer-LESS client must stay releasable
    even when the assess-runs store path is corrupt (e.g. LILA_ASSESS_RUN_DIR
    names a regular file -> NotADirectoryError from stat). Absence semantics
    must match the approval leg's Path.exists()."""

    def test_runs_dir_as_regular_file_keeps_legacy_clients_releasable(
            self, tmp_path, monkeypatch):
        import hashlib
        import agents.reports.release as release
        monkeypatch.setattr(
            release, "assess_approval_for_release",
            lambda *a, **k: (None, "approved", []))
        report_dir = tmp_path / "reports"
        report_dir.mkdir()
        review_dir = tmp_path / "review"
        review_dir.mkdir()
        bogus = tmp_path / "assess_runs_is_a_file"
        bogus.write_text("not a directory", encoding="utf-8")
        monkeypatch.setenv("LILA_ASSESS_RUN_DIR", str(bogus))
        html = report_dir / "testco.federal_opportunity_assessment.html"
        body = "<html>legacy build</html>"
        html.write_text(body, encoding="utf-8")
        (report_dir / "testco.federal_opportunity_assessment.qa.json"
         ).write_text(json.dumps({
             "state": "release",
             "html_sha256": hashlib.sha256(body.encode()).hexdigest(),
         }), encoding="utf-8")
        verdict = release.release_state(
            "Testco", report_dir=str(report_dir),
            review_dir=str(review_dir))
        assert verdict["releasable"] is True
        assert verdict["reason"] == "release"


class TestFreshnessJudgesTheScannedBuild:
    def test_strict_leg_consumes_scan_time_mtime_not_a_re_stat(
            self, tmp_path, monkeypatch):
        """A regeneration between candidate scan and the strict leg must not
        let the OLD build's clean verdict ride the NEW build's mtime past the
        pointer-freshness check."""
        import hashlib
        import agents.reports.release as release
        monkeypatch.setattr(
            release, "assess_approval_for_release",
            lambda *a, **k: (None, "approved", []))
        report_dir = tmp_path / "reports"
        report_dir.mkdir()
        review_dir = tmp_path / "review"
        review_dir.mkdir()
        root = _pointer_dir(tmp_path, monkeypatch)
        root.mkdir(parents=True)
        pointer = root / "all.current.json"
        pointer.write_text(json.dumps({"run_id": "assess:v1:current"}),
                           encoding="utf-8")
        pointer_ns = 1_700_000_000_000_000_000
        os.utime(pointer, ns=(pointer_ns, pointer_ns))
        html = report_dir / "testco.federal_opportunity_assessment.html"
        body = "<html>pre-pointer build</html>"
        html.write_text(body, encoding="utf-8")
        (report_dir / "testco.federal_opportunity_assessment.qa.json"
         ).write_text(json.dumps({
             "state": "release",
             "html_sha256": hashlib.sha256(body.encode()).hexdigest(),
             "assess_run_id": "assess:v1:current",
         }), encoding="utf-8")
        stale_ns = pointer_ns - 1_000_000_000  # scanned build predates pointer
        os.utime(html, ns=(stale_ns, stale_ns))

        real_candidates = release._candidates

        def _scan_then_regenerate(*args, **kwargs):
            cands = real_candidates(*args, **kwargs)
            # mid-request regeneration AFTER the scan: newer bytes on disk
            fresh_ns = pointer_ns + 1_000_000_000
            os.utime(html, ns=(fresh_ns, fresh_ns))
            return cands

        monkeypatch.setattr(release, "_candidates", _scan_then_regenerate)
        verdict = release.release_state(
            "Testco", report_dir=str(report_dir),
            review_dir=str(review_dir))
        assert verdict["releasable"] is False
        assert "predates the current strict Assess run" in verdict["reason"]


class TestScopedDesignatorMatchesLedger:
    def test_agency_designator_shape_accepted_by_pointer_resolver(
            self, tmp_path, monkeypatch):
        """The runner derives agency_<abbr-lower>; the ledger must accept it
        for every abbr in the reference table (drift here would turn every
        post-cutover agency press into a spurious 'skipped')."""
        _pointer_dir(tmp_path, monkeypatch)
        from tools.agencies import AGENCIES
        for agency in AGENCIES:
            designator = f"agency_{agency['abbr'].lower()}"
            path = current_assess_pointer_path("Testco", designator)
            assert path.name == f"{designator}.current.json"
