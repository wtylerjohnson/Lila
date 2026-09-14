"""Deadline-aware depth: notice deep-fetch + pursuit dossiers."""

import copy
import hashlib
import json

import pytest

import tools.api.sam_notice_detail as nd
import tools.api.sam_quota as sq
from agents.decisions.dossier import (
    PursuitDossier, compose_dossier, render_dossier_markdown, select_targets,
)

NOTICES = [
    {"source_id": "N-LATE", "title": "Late", "response_deadline": "2026-10-01"},
    {"source_id": "N-SOON", "title": "Soon", "response_deadline": "2026-07-20"},
    {"source_id": "N-OPEN", "title": "RFI no deadline", "response_deadline": None},
    {"source_id": "N-DISC", "title": "Discarded", "response_deadline": "2026-07-10"},
    {"source_id": "N-MON", "title": "Monitored", "response_deadline": "2026-07-11"},
]
VERDICTS = {
    "N-LATE": {"verdict": "pursue"}, "N-SOON": {"verdict": "pursue"},
    "N-OPEN": {"verdict": "pursue"}, "N-DISC": {"verdict": "discard"},
    "N-MON": {"verdict": "monitor"},
}


def test_select_targets_pursue_only_deadline_first_budgeted():
    t = select_targets(NOTICES, VERDICTS, budget=2)
    assert [n["source_id"] for n in t] == ["N-SOON", "N-LATE"]  # urgent first, RFI last
    t3 = select_targets(NOTICES, VERDICTS, budget=9)
    assert [n["source_id"] for n in t3] == ["N-SOON", "N-LATE", "N-OPEN"]
    assert select_targets(NOTICES, {}, budget=5) == []


def test_strip_html():
    txt = nd.strip_html("<p>Scope of <b>work</b>:</p><br><script>x()</script>threat&nbsp;intel")
    assert "Scope of work" in txt and "threat intel" in txt and "script" not in txt


def test_depth_fetch_meters_caches_and_never_caches_failure(monkeypatch):
    calls = []

    def fake_get(url, *, params=None, timeout=None, headers=None, retries=3):
        calls.append(url)
        if "noticedesc" in url:
            return {"description": "<p>Full scope text</p>"}
        return {"_embedded": [{"name": "SOW.pdf", "mimeType": "application/pdf"}]}

    monkeypatch.setattr(nd, "get_json", fake_get)
    before = sq.calls_today()
    d = nd.fetch_notice_depth("N-1", api_key="k")
    assert d["description"] == "Full scope text"
    assert d["attachments"][0]["name"] == "SOW.pdf"
    assert d["description_checked"] is True
    assert d["resources_checked"] is True
    assert d["retrieved_at"]
    assert sq.calls_today() == before + 1
    # Second call: served from permanent cache — no HTTP, no quota.
    calls.clear()
    d2 = nd.fetch_notice_depth("N-1", api_key="k")
    assert d2["from_cache"] is True and not calls and sq.calls_today() == before + 1

    # Failures are not cached: next attempt retries live.
    def boom(url, **kw):
        raise RuntimeError("429")

    monkeypatch.setattr(nd, "get_json", boom)
    f1 = nd.fetch_notice_depth("N-2", api_key="k")
    assert f1["description"] is None and f1["errors"]
    monkeypatch.setattr(nd, "get_json", fake_get)
    f2 = nd.fetch_notice_depth("N-2", api_key="k")
    assert f2["description"] == "Full scope text" and f2["from_cache"] is False


def test_cached_description_retries_keyless_attachment_failure(monkeypatch):
    calls = []

    def first(url, *, params=None, timeout=None, headers=None, retries=3):
        calls.append(url)
        if "noticedesc" in url:
            return {"description": "Authoritative requirement"}
        raise RuntimeError("resources temporarily unavailable")

    monkeypatch.setattr(nd, "get_json", first)
    d1 = nd.fetch_notice_depth("N-RESOURCE-RETRY", api_key="k")
    assert d1["description"] == "Authoritative requirement"
    assert d1["resources_checked"] is False

    calls.clear()

    def retry(url, *, params=None, timeout=None, headers=None, retries=3):
        calls.append(url)
        assert "resources" in url
        return {"_embedded": [{"name": "PWS.pdf", "mimeType": "application/pdf"}]}

    monkeypatch.setattr(nd, "get_json", retry)
    d2 = nd.fetch_notice_depth("N-RESOURCE-RETRY", api_key="k")
    assert d2["from_cache"] is True
    assert d2["resources_checked"] is True
    assert d2["attachments"][0]["name"] == "PWS.pdf"
    assert len(calls) == 1


def test_cache_refreshes_when_notice_modification_postdates_depth(monkeypatch):
    descriptions = iter(["Original requirement", "Amended requirement"])

    def fetched(url, *, params=None, timeout=None, headers=None, retries=3):
        if "noticedesc" in url:
            return {"description": next(descriptions)}
        return []

    monkeypatch.setattr(nd, "get_json", fetched)
    first = nd.fetch_notice_depth("N-AMENDED", api_key="k")
    assert first["description"] == "Original requirement"
    cache = nd._cache_dir() / "N-AMENDED.json"
    stored = json.loads(cache.read_text())
    stored["retrieved_at"] = "2020-01-01T00:00:00+00:00"
    cache.write_text(json.dumps(stored))

    refreshed = nd.fetch_notice_depth(
        "N-AMENDED", api_key="k",
        min_retrieved_at="2026-07-10T00:00:00+00:00")
    assert refreshed["description"] == "Amended requirement"
    assert refreshed["description_checked"] is True
    assert not any("predates current notice" in error
                   for error in refreshed["errors"])
    assert list((nd._cache_dir() / ".history" / "N-AMENDED.json").glob("*.json"))


def test_unknown_resources_schema_never_confirms_zero_attachments(monkeypatch):
    def fetched(url, *, params=None, timeout=None, headers=None, retries=3):
        if "noticedesc" in url:
            return {"description": "Authoritative requirement"}
        return {"unexpected": {"rows": []}}

    monkeypatch.setattr(nd, "get_json", fetched)
    depth = nd.fetch_notice_depth("N-SCHEMA-DRIFT", api_key="k")
    assert depth["resources_checked"] is False
    assert depth["resources_schema"] is None
    assert depth["attachments"] is None
    assert any("unrecognized resources schema" in error
               for error in depth["errors"])


def _dossier(**over):
    base = dict(
        id="N-SOON", title="CTI Platform BPA",
        scope_summary="DHS seeks an enterprise threat intelligence platform.",
        evaluation_criteria=["Technical approach", "Past performance"],
        submission_requirements=["10-page limit", "Submit via sam.gov by 07/20"],
        incumbent_signals=["References BigCo's existing license"],
        vehicle="GSA MAS",
        fit_verdict="strong_fit",
        win_themes=["Dark-web coverage depth", "Existing DHS footprint"],
        red_flags=["Brand-name-or-equal language"],
        next_action="Request the Q&A attachment before 07/10.",
    )
    base.update(over)
    return PursuitDossier(**base)


class FakeEngine:
    def deliberate(self, layer, system_prompt, context, schema):
        self.ctx = context
        return _dossier()


def test_compose_passes_depth_text_and_render_covers_sections():
    eng = FakeEngine()
    d = compose_dossier("Recorded Future", "sell CTI", NOTICES[1],
                        {"description": "x" * 20000, "attachments": [], "errors": []},
                        engine=eng)
    assert len(eng.ctx["full_description"]) == 14000  # prompt-size cap holds
    md = render_dossier_markdown(d, NOTICES[1])
    for needle in ("CTI Platform BPA", "STRONG FIT", "due 2026-07-20",
                   "Evaluation criteria", "Submission requirements",
                   "Incumbent signals", "Win themes", "Red flags",
                   "Next action", "GSA MAS"):
        assert needle in md, needle


def _attachment_fingerprint(text, files):
    """Mirror the stored discovery producer, not the new context consumer."""
    basis = {
        "files": sorted(
            [{"resource_id": str(item.get("resource_id") or ""),
              "sha256": str(item.get("sha256") or "")}
             for item in files],
            key=lambda item: (item["resource_id"], item["sha256"])),
        "text_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
    }
    return hashlib.sha256(json.dumps(
        basis, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def _attachment_research_inputs(text=None):
    text = text or (
        "The contractor shall provide enterprise packet capture for network operations.")
    resource_id = "b" * 32
    file_row = {
        "resource_id": resource_id,
        "name": "SOW.txt",
        "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
        "retrieved_at": "2026-07-10T10:00:00+00:00",
        "source_url": nd.RESOURCE_DOWNLOAD_URL_TPL.format(resource_id=resource_id),
    }
    attachments = [{"name": file_row["name"], "resource_id": resource_id,
                    "source_url": file_row["source_url"]}]
    inventory_hash = nd._manifest_hash(attachments)
    notice = {
        "source": "sam.gov", "source_id": "a" * 32,
        "title": "Network modernization", "agency": "DHS / CISA",
        "api_url": f"https://sam.gov/opp/{'a' * 32}/view",
        "raw_payload": {
            "text": text,
            "attachment_evidence": [file_row],
            "attachment_evidence_sha256": _attachment_fingerprint(text, [file_row]),
            "attachment_inventory_hash": inventory_hash,
            "attachment_relevance_excerpt": text,
        },
    }
    depth = {
        "id": notice["source_id"],
        "description": "See attached statement of work for the requirement.",
        "description_checked": True,
        "attachments": attachments,
        "resources_checked": True,
        "resources_schema": "recognized_v1",
        "attachment_inventory_hash": inventory_hash,
        "attachment_inventory_count": len(attachments),
        "retrieved_at": "2026-07-10T11:00:00+00:00",
        "errors": [],
    }
    return notice, depth


def _compose_research_context(notice, depth):
    engine = FakeEngine()
    compose_dossier("Testco", "Packet capture software", notice, depth, engine=engine)
    return engine.ctx


def test_attachment_research_reaches_dossier_with_original_provenance_without_mutation():
    # 2026-09-09: discovery research is analysis context, never reviewed depth.
    notice, depth = _attachment_research_inputs()
    original = copy.deepcopy((notice, depth))
    context = _compose_research_context(notice, depth)
    research = context["attachment_research"]
    raw = notice["raw_payload"]
    assert research["status"] == "discovery_only"
    assert research["notice_id"] == notice["source_id"]
    assert research["files"] == raw["attachment_evidence"]
    assert research["evidence_sha256"] == raw["attachment_evidence_sha256"]
    assert research["attachment_inventory_hash"] == raw["attachment_inventory_hash"]
    assert research["inventory_binding"] == "matched"
    assert research["text"] == raw["text"]
    assert research["relevance_excerpt"] == raw["attachment_relevance_excerpt"]
    assert research["captured_characters"] == research["included_characters"] == len(raw["text"])
    assert research["truncated"] is False
    assert context["full_description"] == depth["description"]
    assert context["attachments"] == depth["attachments"]
    assert (notice, depth) == original


def test_attachment_research_absence_preserves_legacy_context():
    notice, depth = _attachment_research_inputs()
    notice["raw_payload"] = {"description_snippet": "An ordinary notice."}
    context = _compose_research_context(notice, depth)
    assert "attachment_research" not in context
    assert set(context) == {
        "client_name", "client_pursuit_strategy", "as_of", "notice",
        "full_description", "attachments", "depth_fetch_errors",
    }
    assert context["full_description"] == depth["description"]


def test_attachment_research_uses_sorted_fingerprint_without_relabeling_files():
    notice, depth = _attachment_research_inputs()
    raw = notice["raw_payload"]
    extra_text = "Additional technical background."
    extra = {
        "resource_id": "c" * 32,
        "name": "Appendix.txt",
        "sha256": hashlib.sha256(extra_text.encode("utf-8")).hexdigest(),
        "retrieved_at": "2026-07-10T10:05:00+00:00",
        "source_url": nd.RESOURCE_DOWNLOAD_URL_TPL.format(resource_id="c" * 32),
    }
    # Deliberately reverse resource-ID order; the producer sorts hash inputs.
    raw["attachment_evidence"].insert(0, extra)
    raw["text"] = extra_text + "\n\n" + raw["text"]
    raw["attachment_evidence_sha256"] = _attachment_fingerprint(
        raw["text"], raw["attachment_evidence"])
    depth["attachments"].append({
        "name": extra["name"], "resource_id": extra["resource_id"],
        "source_url": extra["source_url"],
    })
    depth["attachment_inventory_count"] = 2
    raw["attachment_inventory_hash"] = depth["attachment_inventory_hash"] = (
        nd._manifest_hash(depth["attachments"]))
    research = _compose_research_context(notice, depth)["attachment_research"]
    assert research["status"] == "discovery_only"
    assert research["text"] == raw["text"]
    assert research["files"] == raw["attachment_evidence"]
    assert research["evidence_sha256"] == raw["attachment_evidence_sha256"]


@pytest.mark.parametrize("field", ["text", "attachment_evidence_sha256", "file_sha256"])
def test_attachment_research_rejects_tampered_fingerprint_inputs(field):
    notice, depth = _attachment_research_inputs()
    raw = notice["raw_payload"]
    if field == "text":
        raw["text"] += " A fabricated requirement."
    elif field == "file_sha256":
        raw["attachment_evidence"][0]["sha256"] = "f" * 64
    else:
        raw[field] = "f" * 64
    research = _compose_research_context(notice, depth)["attachment_research"]
    assert research["status"] == "unavailable"
    assert research["notice_id"] == notice["source_id"]
    assert research["reason"]
    assert "text" not in research


@pytest.mark.parametrize("mismatch", ["notice", "inventory"])
def test_attachment_research_rejects_mismatched_depth_identity(mismatch):
    notice, depth = _attachment_research_inputs()
    if mismatch == "notice":
        depth["id"] = "c" * 32
    else:
        depth["attachment_inventory_hash"] = "f" * 64
    research = _compose_research_context(notice, depth)["attachment_research"]
    assert research["status"] == "unavailable"
    assert research["reason"]
    assert "text" not in research


def test_attachment_research_without_current_inventory_is_not_reconfirmed():
    notice, depth = _attachment_research_inputs()
    depth.pop("attachment_inventory_hash")
    depth["resources_checked"] = False
    depth["attachments"] = None
    research = _compose_research_context(notice, depth)["attachment_research"]
    assert research["status"] == "discovery_only"
    assert research["inventory_binding"] == "not_reconfirmed"
    assert research["attachment_inventory_hash"] == notice["raw_payload"]["attachment_inventory_hash"]
    assert research["files"][0]["retrieved_at"] == "2026-07-10T10:00:00+00:00"


@pytest.mark.parametrize("field", [
    "text", "attachment_evidence", "attachment_evidence_sha256",
    "attachment_inventory_hash",
])
def test_attachment_research_rejects_missing_capture_metadata(field):
    notice, depth = _attachment_research_inputs()
    notice["raw_payload"].pop(field)
    research = _compose_research_context(notice, depth)["attachment_research"]
    assert research["status"] == "unavailable"
    assert research["reason"]
    assert "text" not in research


@pytest.mark.parametrize("field", [
    "resource_id", "name", "sha256", "retrieved_at", "source_url",
])
def test_attachment_research_rejects_missing_file_metadata(field):
    notice, depth = _attachment_research_inputs()
    raw = notice["raw_payload"]
    raw["attachment_evidence"][0].pop(field)
    raw["attachment_evidence_sha256"] = _attachment_fingerprint(
        raw["text"], raw["attachment_evidence"])
    research = _compose_research_context(notice, depth)["attachment_research"]
    assert research["status"] == "unavailable"
    assert research["reason"]
    assert "text" not in research


@pytest.mark.parametrize(("field", "value"), [
    ("resource_id", "../file"),
    ("name", {"invalid": "shape"}),
    ("sha256", "not-a-content-hash"),
    ("retrieved_at", "not-a-date"),
    ("retrieved_at", "2026-07-10T10:00:00"),
    ("source_url", "https://example.com/SOW.txt"),
])
def test_attachment_research_rejects_malformed_file_metadata(field, value):
    notice, depth = _attachment_research_inputs()
    raw = notice["raw_payload"]
    raw["attachment_evidence"][0][field] = value
    raw["attachment_evidence_sha256"] = _attachment_fingerprint(
        raw["text"], raw["attachment_evidence"])
    research = _compose_research_context(notice, depth)["attachment_research"]
    assert research["status"] == "unavailable"
    assert research["reason"]
    assert "text" not in research


def test_attachment_research_bounds_text_but_retains_full_hash_and_late_excerpt():
    passage = "The contractor shall provide enterprise packet capture."
    original_text = "Background acquisition context. " * 600 + passage
    notice, depth = _attachment_research_inputs(original_text)
    notice["raw_payload"]["attachment_relevance_excerpt"] = passage
    research = _compose_research_context(notice, depth)["attachment_research"]
    assert research["text"] == original_text[:14000]
    assert passage not in research["text"]
    assert research["captured_characters"] == len(original_text)
    assert research["included_characters"] == 14000
    assert research["truncated"] is True
    assert research["evidence_sha256"] == notice["raw_payload"]["attachment_evidence_sha256"]
    assert research["relevance_excerpt"] == passage


@pytest.mark.parametrize("tampered", [False, True])
def test_attachment_research_excerpt_requires_every_original_segment(tampered):
    first = "The contractor shall provide packet capture."
    last = "Submission shall include a technical approach."
    notice, depth = _attachment_research_inputs(first + "\nBackground.\n" + last)
    excerpt = first + "\n…\n" + ("Invented requirement." if tampered else last)
    notice["raw_payload"]["attachment_relevance_excerpt"] = excerpt
    research = _compose_research_context(notice, depth)["attachment_research"]
    assert research["status"] == "discovery_only"
    if tampered:
        assert "relevance_excerpt" not in research
    else:
        assert research["relevance_excerpt"] == excerpt


def test_attachment_research_analysis_does_not_promote_assess_or_lead():
    # 2026-09-09: dossier visibility cannot invent reviewed NOTICE evidence.
    from agents.assess.contracts import LiveClassification
    from agents.leadgen.enums import LeadTier
    from agents.leadgen.from_assess import draft_lead_rows
    from tests.test_assess_ledger import _build, _depth_record, _sweep

    researched_notice, depth = _attachment_research_inputs()
    notice_id = researched_notice["source_id"]
    sweep = _sweep()
    notice = sweep["results"]["sam.gov"][0]
    notice.update({"source_id": notice_id, "title": researched_notice["title"],
                   "api_url": researched_notice["api_url"]})
    notice["raw_payload"].update(researched_notice["raw_payload"])
    notice["raw_payload"]["notice_id"] = notice_id
    sweep["results"]["sam.gov"] = [notice]
    sweep["results"]["sam_census"]["matched"] = 1
    sweep["results"]["triage"] = {
        notice_id: {"verdict": "pursue", "reason": "Public SOW requirement"}}
    depth_record = _depth_record(notice_id, attachments=depth["attachments"])
    depth_record["source_depth"]["description"] = depth["description"]
    sweep["results"]["dossiers"] = {"records": [depth_record]}
    before, _, _ = _build(sweep)

    engine = FakeEngine()
    composed = compose_dossier("Testco", "Packet capture software", notice, depth,
                               engine=engine)
    assert engine.ctx["attachment_research"]["status"] == "discovery_only"
    assert composed.fit_verdict == "strong_fit"
    sweep["results"]["dossiers"]["records"] = [{
        **composed.model_dump(mode="json"), "id": notice_id,
        "source_depth": depth_record["source_depth"],
    }]
    after, _, _ = _build(sweep)
    assert after.model_dump(mode="json") == before.model_dump(mode="json")
    record = after.live.records[0]
    assert record.classification == LiveClassification.UNSCREENED
    assert record.requirement_excerpt is None
    assert record.requirement_reviewed_by is None
    assert record.attachments_checked is False
    assert record.attachment_reviewed_by is None
    drafts = draft_lead_rows(after)
    assert drafts.parents[0].requirement_span is None
    assert not drafts.leads  # parent-only research, no supported buying motion
