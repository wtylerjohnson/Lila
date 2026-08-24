"""Pure, in-memory compiler for a frozen LILA ReleaseSnapshot."""

from __future__ import annotations

import hashlib
import io
import json
import re
import zipfile
from dataclasses import dataclass

from agents.golden_press.external_product_projection import (
    EXTERNAL_PRODUCT_PROJECTION_VERSION,
    build_external_product_document,
)
from agents.golden_press.external_product_render import (
    EXTERNAL_PRODUCT_RENDER_VERSION,
    render_external_product,
    validate_external_product_html,
)
from agents.golden_press.market_map_projection import (
    MARKET_MAP_PROJECTION_VERSION,
    build_market_map,
)
from agents.golden_press.market_map_skeleton import MARKET_MAP_SKELETON_VERSION
from agents.golden_press.market_map_validate import BANNED_VOCABULARY
from agents.golden_press.records import EvidencePack
from agents.golden_press.release_snapshot import (
    ReleaseSnapshot,
    canonical_json_bytes,
    contains_local_path,
)


RELEASE_COMPILER_VERSION = "lila-release-compiler.v1.2026-08-24"
PRODUCT_BUNDLE_VERSION = "lila-complete-bundle.v3.2026-08-24"
_FIXTURE_BANNER = (
    '<div data-reproducibility-fixture="1" role="note">'
    "Reproducibility fixture only. "
    "Not approved, not promotable, and not a client champion.</div>"
)


class ReleaseCompilationError(RuntimeError):
    """Frozen inputs could not produce a complete deterministic bundle."""


@dataclass(frozen=True)
class CompiledRelease:
    release_id: str
    content_digest: str
    bundle_name: str
    primary_html: str
    studio_html: str
    payloads: dict[str, bytes]
    content_receipt: dict
    manifest: dict
    manifest_bytes: bytes
    zip_bytes: bytes


def _sha(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _content_identity(
    snapshot: ReleaseSnapshot, payloads: dict[str, bytes],
) -> tuple[dict, str]:
    """Derive the complete pre-manifest receipt and its content digest."""
    file_receipts = {
        name: {"sha256": _sha(payload), "bytes": len(payload)}
        for name, payload in sorted(payloads.items())
    }
    versions = {
        **snapshot.versions,
        "bundle": PRODUCT_BUNDLE_VERSION,
        "compiler": RELEASE_COMPILER_VERSION,
        "snapshot": snapshot.schema_version,
        "market_map_projection": MARKET_MAP_PROJECTION_VERSION,
        "external_product_projection": EXTERNAL_PRODUCT_PROJECTION_VERSION,
        "external_product_render": EXTERNAL_PRODUCT_RENDER_VERSION,
        "market_map_skeleton": MARKET_MAP_SKELETON_VERSION,
    }
    receipt = {
        "schema_version": "lila-content-receipt.v1.2026-08-24",
        "client_name": snapshot.client_name,
        "slug": snapshot.slug,
        "purpose": snapshot.purpose,
        "business_as_of": snapshot.business_as_of,
        "classification_as_of": snapshot.classification_as_of,
        "captured_at": snapshot.captured_at,
        "contract_version": snapshot.contract_version,
        "operator_locked_slot_sha256": (
            snapshot.operator_locked_slot_sha256),
        "projection_evidence_pack_sha256": snapshot.evidence_pack_sha256,
        "source_evidence_pack_sha256": snapshot.source_evidence_pack_sha256,
        "versions": versions,
        "authorization": snapshot.authorization,
        "files": file_receipts,
    }
    return receipt, _sha(canonical_json_bytes(receipt))


def _zip_datetime(day: str) -> tuple[int, int, int, int, int, int]:
    try:
        year, month, day_number = (int(value) for value in day.split("-"))
        return max(1980, year), month, day_number, 0, 0, 0
    except (TypeError, ValueError):
        return 1980, 1, 1, 0, 0, 0


def _zip_bytes(payloads: dict[str, bytes], *, day: str) -> bytes:
    target = io.BytesIO()
    with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_STORED) as archive:
        for name in sorted(payloads):
            info = zipfile.ZipInfo(name, date_time=_zip_datetime(day))
            info.compress_type = zipfile.ZIP_STORED
            info.create_system = 3
            info.external_attr = 0o100644 << 16
            archive.writestr(info, payloads[name])
    return target.getvalue()


def _refuse_local_paths(payloads: dict[str, bytes]) -> None:
    leaking = [
        name for name, payload in sorted(payloads.items())
        if contains_local_path(payload)
    ]
    if leaking:
        raise ReleaseCompilationError(
            "local filesystem path appears in compiled payload: "
            + ", ".join(leaking))


def _refuse_banned_vocabulary(payloads: dict[str, bytes]) -> None:
    violations: list[str] = []
    for name, payload in sorted(payloads.items()):
        text = payload.decode("utf-8", errors="replace").casefold()
        for term in BANNED_VOCABULARY:
            pattern = re.compile(
                r"(?<![a-z0-9])" + re.escape(term.casefold())
                + r"(?:s|es|'s)?(?![a-z0-9])")
            if pattern.search(text):
                violations.append(f"{name}: {term}")
    if violations:
        raise ReleaseCompilationError(
            "banned vocabulary appears in compiled payload: "
            + ", ".join(violations))


def _refuse_disallowed_dash(payloads: dict[str, bytes]) -> None:
    leaking = [
        name for name, payload in sorted(payloads.items())
        if b"\xe2\x80\x94" in payload
    ]
    if leaking:
        raise ReleaseCompilationError(
            "disallowed dash appears in compiled payload: "
            + ", ".join(leaking))


def compile_release_snapshot(snapshot: ReleaseSnapshot) -> CompiledRelease:
    """Compile one frozen snapshot without clocks, stores, files, or network."""
    if not isinstance(snapshot, ReleaseSnapshot):
        raise TypeError("compile_release_snapshot requires ReleaseSnapshot")
    try:
        evidence_payload = json.loads(snapshot.evidence_pack_bytes)
        evidence_pack = EvidencePack.model_validate(evidence_payload)
    except Exception as exc:  # noqa: BLE001 - snapshot schema must fail named
        raise ReleaseCompilationError(
            f"captured evidence pack is invalid: {exc}") from exc

    market_map = build_market_map(
        evidence_pack,
        profile=snapshot.profile,
        slug=snapshot.slug,
        as_of=snapshot.business_as_of,
        inputs=snapshot.market_map_inputs,
    )
    product = build_external_product_document(
        market_map=market_map,
        graph_payload=snapshot.graph,
        evidence_pack=evidence_pack,
        profile=snapshot.profile,
        client_name=snapshot.client_name,
        slug=snapshot.slug,
        as_of=snapshot.business_as_of,
        contract_slots=snapshot.contract_slots,
        contract_version=snapshot.contract_version,
        contract_sha256=snapshot.operator_locked_slot_sha256,
    )
    studio_html, client_html = render_external_product(
        product, render_assets=snapshot.render_assets)
    if snapshot.purpose != "release_input":
        studio_html = studio_html.replace(
            "<body>", "<body>" + _FIXTURE_BANNER, 1)
        client_html = client_html.replace(
            "<body>", "<body>" + _FIXTURE_BANNER, 1)
    validation = validate_external_product_html(
        client_html, product, contract_slots=snapshot.contract_slots)
    if not validation.get("ok"):
        details = [
            f"{row.get('rule')}: {row.get('detail')}"
            for row in (validation.get("violations") or [])
        ]
        raise ReleaseCompilationError(
            "external product validation failed: " + "; ".join(details))

    primary_name = (
        f"LILA_{snapshot.slug}_{snapshot.business_as_of}.html")
    studio_name = (
        f"LILA_{snapshot.slug}_{snapshot.business_as_of}.studio.html")
    validation_payload = {
        **validation,
        "authorization": snapshot.authorization,
        "external_family": "lila_federal_market_map",
        "classification_as_of": snapshot.classification_as_of,
        "captured_at": snapshot.captured_at,
        "contract_version": snapshot.contract_version,
        "operator_locked_slot_sha256": (
            snapshot.operator_locked_slot_sha256),
    }
    payloads = {
        primary_name: client_html.encode("utf-8"),
        studio_name: studio_html.encode("utf-8"),
        "product.json": canonical_json_bytes(product.to_dict()),
        "federal_pursuit_graph.json": canonical_json_bytes(snapshot.graph),
        "evidence_pack.json": snapshot.evidence_pack_bytes,
        "captured_inputs.json": canonical_json_bytes(snapshot.to_dict()),
        "validation.json": canonical_json_bytes(validation_payload),
        "README.txt": ((
            "LILA complete release bundle\n\n"
            "Open the LILA_*.html file for the client product.\n"
            "product.json is the exact eight-slot projection.\n"
            "federal_pursuit_graph.json is the canonical certified graph.\n"
            "evidence_pack.json preserves the exact pressed evidence bytes.\n"
            "captured_inputs.json is the complete frozen ReleaseSnapshot.\n"
            "validation.json records rendered and release checks.\n"
            "manifest.json binds every pre-manifest payload by SHA-256.\n"
        ) if snapshot.purpose == "release_input" else (
            "LILA reproducibility fixture bundle\n\n"
            "This artifact proves deterministic replay only. It is not "
            "approved, promotable, client ready, or a product-quality "
            "champion. captured_inputs.json preserves the exact frozen "
            "source evidence bytes; evidence_pack.json is the named, "
            "release-safe projection copy.\n"
        )).encode("utf-8"),
    }
    _refuse_local_paths(payloads)
    _refuse_banned_vocabulary(payloads)
    _refuse_disallowed_dash(payloads)
    content_receipt, content_digest = _content_identity(snapshot, payloads)
    file_receipts = content_receipt["files"]
    release_id = f"{snapshot.business_as_of}-{content_digest[:24]}"
    bundle_name = f"LILA_{snapshot.slug}_{release_id}_COMPLETE.zip"
    manifest = {
        "schema_version": PRODUCT_BUNDLE_VERSION,
        "release_id": release_id,
        "content_digest": content_digest,
        "content_receipt": content_receipt,
        "release_state": (
            "release" if snapshot.purpose == "release_input" else "fixture"),
        "release_eligible": snapshot.purpose == "release_input",
        "purpose": snapshot.purpose,
        "client_name": snapshot.client_name,
        "slug": snapshot.slug,
        "as_of": snapshot.business_as_of,
        "classification_as_of": snapshot.classification_as_of,
        "captured_at": snapshot.captured_at,
        "product_family": "lila_federal_market_map",
        "product_contract_version": snapshot.contract_version,
        "operator_locked_slot_sha256": (
            snapshot.operator_locked_slot_sha256),
        "slot_ids": [slot.slot_id for slot in product.slots],
        "authorization": snapshot.authorization,
        "graph_contract_certified": (
            snapshot.graph.get("graph_contract_certified") is True),
        "render_validation_ok": True,
        "source_graph": "federal_pursuit_graph.json",
        "primary_html": primary_name,
        "studio_html": studio_name,
        "bundle_name": bundle_name,
        "files": file_receipts,
    }
    manifest_bytes = canonical_json_bytes(manifest)
    _refuse_local_paths({**payloads, "manifest.json": manifest_bytes})
    archive_payloads = {**payloads, "manifest.json": manifest_bytes}
    return CompiledRelease(
        release_id=release_id,
        content_digest=content_digest,
        bundle_name=bundle_name,
        primary_html=primary_name,
        studio_html=studio_name,
        payloads=payloads,
        content_receipt=content_receipt,
        manifest=manifest,
        manifest_bytes=manifest_bytes,
        zip_bytes=_zip_bytes(
            archive_payloads, day=snapshot.business_as_of),
    )


__all__ = (
    "CompiledRelease",
    "PRODUCT_BUNDLE_VERSION",
    "RELEASE_COMPILER_VERSION",
    "ReleaseCompilationError",
    "compile_release_snapshot",
)
