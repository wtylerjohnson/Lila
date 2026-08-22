"""Verified enrichment sources run inside the stored assessment sweep."""

from tools.api.base import SourceQuery, SourceRegistry
from tools.api.source_mesh import SOURCE_IDS, child_attempt_rows, collect


class _Source:
    def __init__(self, name, *, fail=False):
        self.name = name
        self.fail = fail

    def _payload(self, key):
        if self.fail:
            raise RuntimeError("fixture outage")
        return {
            key: [{"id": self.name + "-1", "raw": "fixture source row"}],
            "_provenance": {
                "schema_version": 1,
                "source": self.name,
                "status": "complete",
                "mode": "fixture",
                "retrieval_mode": "stored",
                "retrieved_at": "2026-08-08T12:00:00+00:00",
                "record_count": 1,
                "attempts": [],
                "limitations": [],
                "fallback": False,
                "stale": False,
            },
        }

    def vehicle_holders(self, _query):
        return self._payload("holders")

    def holders(self, _query):
        return self._payload("holders")

    def recent_actions(self, _query, days=7):
        assert days == 7
        return self._payload("actions")

    def enrich(self, _query):
        key = "projects" if self.name == "nih_reporter" else "pairs"
        return self._payload(key)

    def firms(self, _query):
        return self._payload("firms")

    def apportionments(self, _query):
        return self._payload("rows")

    def procurement_list(self, _query):
        return self._payload("items")

    def budget_authority(self, _query):
        return self._payload("accounts")

    def decisions(self, _query):
        return self._payload("decisions")

    def regulations(self, _query):
        return self._payload("sections")

    def budget_execution(self, _query):
        return self._payload("items")

    def scorecards(self, _query):
        return self._payload("items")

    def screen(self, _query):
        return self._payload("items")

    def reports(self, _query):
        return self._payload("items")

    def announcements(self, _query):
        return self._payload("items")

    def wage_determinations(self, _query):
        return self._payload("items")

    def forecast_records(self, _query):
        return self._payload("items")

    def contract_holders(self, _query):
        return self._payload("items")

    def awardees(self, _query):
        return self._payload("items")

    def litigation_events(self, _query):
        return self._payload("items")

    def portfolio_context(self, _query):
        return self._payload("items")

    def category_sweep(self, _query):
        return self._payload("records")

    def agreements(self, _query):
        return self._payload("records")

    def press_items(self, _query):
        return self._payload("records")


def _registry(failing=None):
    registry = SourceRegistry()
    for source_id in SOURCE_IDS:
        registry.add(_Source(source_id, fail=source_id == failing))
    return registry


def test_collect_is_query_bound_hash_receipted_and_ordered():
    payload = collect(
        SourceQuery(
            keywords=["biomedical research"], naics_codes=["541512"],
            agencies=["National Institutes of Health"], limit=20),
        client_name="Example Client",
        scope_designator="agency_va",
        registry=_registry(),
    )
    assert tuple(payload["sources"]) == SOURCE_IDS
    assert payload["client_name"] == "Example Client"
    assert payload["scope_designator"] == "agency_va"
    assert payload["_provenance"]["status"] == "complete"
    assert payload["coverage_contract"] == {
        "required_origins": list(SOURCE_IDS),
        "returned_origins": list(SOURCE_IDS),
        "incomplete_origins": [],
        "complete_within_boundary": True,
    }
    for source_id, receipt in payload["sources"].items():
        assert receipt["source_id"] == source_id
        assert receipt["status"] == "success"
        assert receipt["record_count"] == 1
        assert len(receipt["content_sha256"]) == 64
        assert receipt["normalization_version"].startswith("source_mesh.v1")
        assert receipt["retrieval_query"]["agencies"] == [
            "National Institutes of Health"]
        assert receipt["retrieval_url"].startswith("https://")
        assert receipt["content_kind"] == "normalized-source-payload"
        assert len(receipt["immutable_receipt_sha256"]) == 64
        assert receipt["immutable_receipt_kind"] in {
            "raw-source-response", "canonical-adapter-envelope-equivalent"}


def test_nih_mesh_requires_research_buyer_and_research_work():
    payload = collect(
        SourceQuery(
            keywords=["payment integrity", "supplier onboarding"],
            agencies=["Department of Veterans Affairs"],
        ),
        client_name="Payments Client",
        scope_designator="agency_va",
        registry=_registry(),
    )
    nih = payload["sources"]["nih_reporter"]
    assert nih["status"] == "not-run"
    assert "buyer" in nih["not_run"]
    assert payload["_provenance"]["status"] == "partial"


def test_one_child_failure_is_persisted_and_does_not_sink_the_fleet():
    payload = collect(
        SourceQuery(keywords=["network"], agencies=["VA"]),
        client_name="Example", scope_designator="agency_va",
        registry=_registry(failing="fpds_atom"),
    )
    assert payload["sources"]["fpds_atom"]["status"] == "failed"
    assert payload["sources"]["gsa_elibrary"]["status"] == "success"
    assert payload["_provenance"]["status"] == "partial"
    assert payload["coverage_contract"]["complete_within_boundary"] is False
    assert payload["coverage_contract"]["incomplete_origins"] == [
        "fpds_atom",
        "nih_reporter",
    ]
    attempts = child_attempt_rows(payload)
    assert len(attempts) == len(SOURCE_IDS)
    failed = next(row for row in attempts if row["source"] == "fpds_atom")
    assert failed["ok"] is False
    assert failed["error"] == "source retrieval failed"
    successful = next(row for row in attempts if row["source"] == "gsa_elibrary")
    assert successful["source_receipt"]["normalization_version"].startswith(
        "source_mesh.v1"
    )
    assert successful["provenance"]["source"] == "gsa_elibrary"


def test_omb_is_honestly_not_run_without_an_agency_scope():
    payload = collect(
        SourceQuery(keywords=["network"]), client_name="Example",
        scope_designator="all", registry=_registry(),
    )
    assert payload["sources"]["omb_apportionment"]["status"] == "not-run"
    assert payload["_provenance"]["status"] == "partial"


def test_explicit_partial_provenance_wins_over_explanatory_error_key():
    from tools.api.source_mesh import _receipt

    payload = {
        "error": "no exact regulation citation in the engagement query",
        "items": [],
        "_provenance": {
            "schema_version": 1, "source": "ecfr_title48",
            "status": "partial", "mode": "refused_unscoped",
            "retrieval_mode": "live",
            "retrieved_at": "2026-08-08T12:00:00+00:00",
            "record_count": 0, "attempts": [], "limitations": [],
            "fallback": False, "stale": False,
        },
    }
    receipt = _receipt(
        "ecfr_title48", payload, SourceQuery(keywords=["software"]))
    assert receipt["status"] == "partial"
