"""Public SAM requirement-file retrieval stays bounded and cacheable."""

from __future__ import annotations

import json
import io
import zipfile
import subprocess
import time

import pytest

import tools.api.sam_attachment_text as sat
import tools.api.sam_notice_detail as detail


def test_public_resource_inventory_uses_hal_and_builds_download_url(
        tmp_path, monkeypatch):
    monkeypatch.setenv("LILA_NOTICE_CACHE_DIR", str(tmp_path))
    seen = {}

    def fake_get(url, **kwargs):
        seen.update({"url": url, **kwargs})
        return {"_embedded": {"opportunityAttachmentList": [{
            "opportunityId": "a" * 32,
            "attachments": [{
                "name": "Draft PWS.pdf",
                "resourceId": "b" * 32,
                "mimeType": ".pdf",
                "size": 1234,
                "accessLevel": "public",
                "accessStatus": "public",
                "exportControlled": "0",
                "explicitAccess": "0",
            }],
        }]}}

    monkeypatch.setattr(detail, "get_json", fake_get)
    result = detail.fetch_notice_resources("a" * 32)
    assert seen["headers"]["Accept"] == "application/hal+json"
    assert result["resources_checked"] is True
    attachment = result["attachments"][0]
    assert attachment["source_url"].endswith(
        "/resources/files/" + "b" * 32 + "/download")
    assert attachment["access_status"] == "public"


def test_public_text_attachment_extracts_once_then_reuses_cache(
        tmp_path, monkeypatch):
    monkeypatch.setenv("LILA_SAM_ATTACHMENT_TEXT_DIR", str(tmp_path))
    calls = []
    monkeypatch.setattr(
        sat, "_download_bytes",
        lambda resource_id, **kwargs: calls.append(resource_id)
        or b"network visibility")
    attachment = {
        "resource_id": "c" * 32,
        "name": "requirements.txt",
        "size": 18,
        "access_level": "public",
        "access_status": "public",
        "export_controlled": False,
        "explicit_access": False,
    }
    first = sat.extract_public_attachment_text(attachment)
    assert first["text"] == "network visibility"
    assert first["from_cache"] is False
    second = sat.extract_public_attachment_text(attachment)
    assert second["text"] == first["text"]
    assert second["from_cache"] is True
    assert calls == ["c" * 32]


def test_nonpublic_attachment_never_downloads(monkeypatch):
    monkeypatch.setattr(
        sat, "_download_bytes",
        lambda _resource_id: (_ for _ in ()).throw(
            AssertionError("private attachment was downloaded")))
    result = sat.extract_public_attachment_text({
        "resource_id": "d" * 32,
        "name": "controlled.pdf",
        "access_level": "private",
    })
    assert result["text"] == ""
    assert "not public" in result["error"]


def test_stale_empty_resource_inventory_is_refetched(tmp_path, monkeypatch):
    monkeypatch.setenv("LILA_NOTICE_CACHE_DIR", str(tmp_path))
    notice_id = "e" * 32
    (tmp_path / f"{notice_id}.resources.json").write_text(json.dumps({
        "id": notice_id,
        "attachments": [],
        "retrieved_at": "2020-01-01T00:00:00+00:00",
        "resources_checked": True,
        "resources_schema": "recognized_v1",
        "errors": [],
    }))
    monkeypatch.setattr(detail, "get_json", lambda *args, **kwargs: {
        "attachments": [{
            "name": "Added SOW.pdf", "resourceId": "f" * 32,
            "mimeType": ".pdf",
        }],
    })
    result = detail.fetch_notice_resources(notice_id)
    assert result["from_cache"] is False
    assert [item["name"] for item in result["attachments"]] == ["Added SOW.pdf"]


def test_expired_resource_inventory_is_not_used_after_refresh_failure(
        tmp_path, monkeypatch):
    monkeypatch.setenv("LILA_NOTICE_CACHE_DIR", str(tmp_path))
    notice_id = "0" * 32
    (tmp_path / f"{notice_id}.resources.json").write_text(json.dumps({
        "id": notice_id,
        "attachments": [{"name": "Ancient SOW.pdf", "resource_id": "1" * 32}],
        "retrieved_at": "2020-01-01T00:00:00+00:00",
        "resources_checked": True,
        "resources_schema": "recognized_v1",
        "errors": [],
    }))
    monkeypatch.setattr(
        detail, "get_json",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("offline")))
    result = detail.fetch_notice_resources(notice_id)
    assert result["from_cache"] is False
    assert result["attachments"] is None
    assert "offline" in result["errors"][0]


def test_public_resource_notice_id_rejects_path_material(monkeypatch):
    monkeypatch.setattr(
        detail, "get_json",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("invalid notice id reached the network")))
    with pytest.raises(ValueError, match="32 hexadecimal"):
        detail.fetch_notice_resources("../not-a-notice")


def test_attachment_manifest_is_order_and_duplicate_invariant():
    first = {"name": "SOW.pdf", "resourceId": "1" * 32,
             "mimeType": ".pdf"}
    second = {"name": "PWS.pdf", "resourceId": "2" * 32,
              "mimeType": ".pdf"}
    left, recognized = detail._attachment_inventory(
        {"attachments": [first, second, first]})
    right, _ = detail._attachment_inventory(
        {"attachments": [second, first]})
    assert recognized is True
    assert left == right
    assert detail._manifest_hash(left or []) == detail._manifest_hash(right or [])


def test_conflicting_attachment_access_metadata_resolves_restrictively():
    public = {"name": "SOW.pdf", "resourceId": "3" * 32,
              "mimeType": ".pdf", "explicitAccess": "0", "size": 10}
    restricted = {**public, "explicitAccess": "1", "size": 20}
    items, recognized = detail._attachment_inventory(
        {"attachments": [public, restricted]})
    assert recognized is True
    assert items[0]["explicit_access"] == "1"
    assert items[0]["size"] == 20
    assert sat._public_file(items[0])[0] is False


def test_docx_expansion_limit_is_enforced(monkeypatch):
    payload = io.BytesIO()
    with zipfile.ZipFile(payload, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("word/document.xml", "<w>" + "x" * 100 + "</w>")
    monkeypatch.setattr(sat, "MAX_DOCX_XML_BYTES", 50)
    with pytest.raises(ValueError, match="document XML exceeds"):
        sat._docx_text(payload.getvalue())


def test_pdf_parser_timeout_is_killable(monkeypatch):
    def timed_out(*args, **kwargs):
        raise subprocess.TimeoutExpired(args[0], kwargs["timeout"])

    monkeypatch.setattr(sat.subprocess, "run", timed_out)
    with pytest.raises(TimeoutError, match="PDF text extraction exceeded"):
        sat._pdf_text(b"%PDF", deadline_monotonic=time.monotonic() + 1)


def test_systemic_attachment_failure_preserves_complete_sam_rows():
    import run_searches as searches

    class BrokenSource:
        def attachment_candidates(self, *args, **kwargs):
            raise RuntimeError("candidate scan broke")

    original = [{"source_id": "kept", "title": "Complete extract row"}]
    rows, stats = searches._safe_enrich_sam_public_attachments(
        original, BrokenSource(), object(), object(), None, limit=8)
    assert rows == original
    assert stats["matched"] == 1
    assert stats["attachment_added"] == 0
    assert stats["attachment_errors"] == [
        "attachment stage failed: candidate scan broke"]


def test_irrelevant_attachment_near_match_does_not_change_sam_census(
        monkeypatch):
    import run_searches as searches
    from agents.schemas import RawOpportunity
    from tools.relevance.taxonomy import CapabilityTaxonomy, TaxonomyTerm

    candidate = RawOpportunity(
        source="sam.gov", source_id="a" * 32,
        title="Generic modernization", agency="NASA",
        raw_payload={"type": "Solicitation"},
    )

    class Source:
        last_attachment_census = {"eligible": 1, "threads": 1, "selected": 1}

        def attachment_candidates(self, *args, **kwargs):
            assert kwargs["limit"] <= searches._SAM_ATTACHMENT_CANDIDATE_HARD_CAP
            return [candidate]

    monkeypatch.setattr(detail, "fetch_notice_resources", lambda notice_id, **kwargs: {
        "attachments": [{"name": "SOW.txt", "resource_id": "b" * 32}],
        "attachment_inventory_hash": "hash",
    })
    monkeypatch.setattr(
        sat, "extract_public_attachment_text", lambda item, **kwargs: {
        "resource_id": item["resource_id"], "name": item["name"],
        "text": "janitorial staffing schedule", "sha256": "hash",
        "retrieved_at": "2026-07-21T00:00:00+00:00", "source_url": "sam",
        "error": "",
        })
    original = [{"source_id": "kept", "title": "Description match"}]
    taxonomy = CapabilityTaxonomy(
        client_name="Testco", version=1, updated="2026-07-21",
        core=[TaxonomyTerm(term="network visibility")],
    )
    rows, stats = searches._enrich_sam_public_attachments(
        original, Source(), object(), taxonomy, None, limit=999)
    assert rows == original
    assert stats["description_matched"] == stats["matched"] == 1
    assert stats["attachment_enriched"] == 1
    assert stats["attachment_relevant"] == stats["attachment_added"] == 0


def test_stale_attachment_inventory_cannot_create_relevance(monkeypatch):
    import run_searches as searches
    from agents.schemas import RawOpportunity
    from tools.relevance.taxonomy import CapabilityTaxonomy, TaxonomyTerm

    candidate = RawOpportunity(
        source="sam.gov", source_id="4" * 32,
        title="Network requirement", agency="NASA",
        raw_payload={"type": "Solicitation"},
    )

    class Source:
        last_attachment_census = {"eligible": 1, "threads": 1, "selected": 1}

        def attachment_candidates(self, *args, **kwargs):
            return [candidate]

    monkeypatch.setattr(detail, "fetch_notice_resources", lambda *args, **kwargs: {
        "stale_cache": True,
        "attachments": [{"name": "SOW.txt", "resource_id": "5" * 32}],
    })
    monkeypatch.setattr(
        sat, "extract_public_attachment_text",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("stale inventory reached attachment download")))
    rows, stats = searches._enrich_sam_public_attachments(
        [], Source(), object(), CapabilityTaxonomy(
            client_name="Testco", version=1, updated="2026-07-21",
            core=[TaxonomyTerm(term="network visibility")],
        ), None, limit=8)
    assert rows == []
    assert stats["attachment_relevant"] == 0
    assert "stale attachment inventory" in stats["attachment_errors"][0]


def test_triage_fingerprint_binds_exact_extracted_evidence(monkeypatch):
    import run_searches as searches
    from agents.schemas import RawOpportunity
    from tools.relevance.taxonomy import CapabilityTaxonomy, TaxonomyTerm

    candidate = RawOpportunity(
        source="sam.gov", source_id="6" * 32,
        title="Network modernization", agency="NASA",
        raw_payload={"type": "Solicitation"},
    )

    class Source:
        last_attachment_census = {"eligible": 1, "threads": 1, "selected": 1}

        def attachment_candidates(self, *args, **kwargs):
            return [candidate]

    monkeypatch.setattr(detail, "fetch_notice_resources", lambda *args, **kwargs: {
        "stale_cache": False,
        "attachments": [{"name": "SOW.txt", "resource_id": "7" * 32}],
        "attachment_inventory_hash": "same-inventory",
    })
    current = {
        "text": "Boilerplate. The requirement needs network visibility.",
        "sha256": "file-version-one",
    }
    monkeypatch.setattr(
        sat, "extract_public_attachment_text",
        lambda *args, **kwargs: {
            "resource_id": "7" * 32, "name": "SOW.txt",
            "text": current["text"], "sha256": current["sha256"],
            "retrieved_at": "2026-07-21T00:00:00+00:00",
            "source_url": "sam", "error": "",
        })
    taxonomy = CapabilityTaxonomy(
        client_name="Testco", version=1, updated="2026-07-21",
        core=[TaxonomyTerm(term="network visibility")],
    )
    first, _ = searches._enrich_sam_public_attachments(
        [], Source(), object(), taxonomy, None, limit=8)
    first_raw = first[0]["raw_payload"]
    first_fingerprint = first_raw["attachment_evidence_sha256"]
    assert first_fingerprint != "same-inventory"
    assert "network visibility" in first_raw["attachment_relevance_excerpt"]

    current.update({
        "text": "Amended requirement needs network visibility and monitoring.",
        "sha256": "file-version-two",
    })
    second, _ = searches._enrich_sam_public_attachments(
        [], Source(), object(), taxonomy, None, limit=8)
    assert second[0]["raw_payload"][
        "attachment_evidence_sha256"] != first_fingerprint
