"""Capture, compile, and atomically promote one complete LILA product."""

from __future__ import annotations

import hashlib
import io
import json
import os
import re
import shutil
import tempfile
import zipfile
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Optional

from agents.golden_press.external_product_contract import (
    CONTRACT_VERSION,
    OPERATOR_LOCKED_SLOT_SHA256,
)
from agents.golden_press.release_compiler import (
    PRODUCT_BUNDLE_VERSION,
    RELEASE_COMPILER_VERSION,
    CompiledRelease,
    ReleaseCompilationError,
    _content_identity,
    _zip_bytes,
    _zip_datetime,
    compile_release_snapshot,
)
from agents.golden_press.release_snapshot import (
    LIVE_RELEASE_PURPOSE,
    REPRODUCIBILITY_FIXTURE_PURPOSE,
    ReleaseSnapshot,
    canonical_json_bytes,
    canonicalize_release_value,
    contains_local_path,
)


CURRENT_POINTER_VERSION = "lila-release-pointer.v2"
MAX_CAPTURE_AGE = timedelta(days=14)
_EXACT_REVIEW_HASHES = (
    "target_set_sha256", "plan_sha256",
    "assess_approval_sha256", "target_unlock_sha256",
)


class ProductReleaseError(RuntimeError):
    """The complete release transaction could not be built."""


class ProductReleaseBlocked(ProductReleaseError):
    """A gate or certification leg refused external release."""

    def __init__(self, problems: list[str]):
        self.problems = [str(problem) for problem in problems]
        super().__init__("; ".join(self.problems))


@dataclass(frozen=True)
class ProductReleaseResult:
    release_id: str
    release_dir: str
    bundle_path: str
    html_path: str
    manifest_path: str
    manifest_sha256: str
    bundle_sha256: str
    desktop_bundle_path: Optional[str] = None
    desktop_html_path: Optional[str] = None


def _sha_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _write(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())


def _local_path_leaks(payloads: dict[str, bytes]) -> list[str]:
    return [
        name for name, payload in sorted(payloads.items())
        if contains_local_path(payload)
    ]


def _archive_local_path_leaks(bundle_path: Path) -> list[str]:
    leaks: list[str] = []
    with zipfile.ZipFile(bundle_path) as archive:
        for info in archive.infolist():
            if info.is_dir():
                continue
            if (contains_local_path(info.filename)
                    or contains_local_path(archive.read(info))):
                leaks.append(info.filename)
    return sorted(leaks)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _authorization(
    client_name: str, root: Path, *,
    target_inventory: Optional[list[dict]] = None,
    expected_target_set_sha256: Optional[str] = None,
) -> dict:
    """Re-derive Assess, Target, and exact Targeting Review bindings."""
    from agents.assess.approval import assess_approval_for_release
    from agents.review import (
        target_gate_status,
        targeting_review_binding_receipt,
    )

    review_dir = root / "data" / "review"
    _approval, assess_status, assess_problems = assess_approval_for_release(
        client_name, review_dir=str(review_dir))
    target_ok, target_problems = target_gate_status(
        client_name, review_dir=str(review_dir))
    targeting_receipt, targeting_ok, targeting_problems = (
        targeting_review_binding_receipt(
            client_name, targets=target_inventory,
            expected_target_set_sha256=expected_target_set_sha256,
            review_dir=str(review_dir)))
    problems: list[str] = []
    if assess_status != "approved":
        problems.append(
            "Assess approval is not current: "
            + "; ".join(assess_problems or [assess_status]))
    if not target_ok:
        problems.append(
            "Target approval is not current: "
            + "; ".join(target_problems or ["Target is locked"]))
    if not targeting_ok:
        problems.append(
            "Targeting Review is not current: "
            + "; ".join(targeting_problems or ["review is incomplete"]))
    return {
        "authorized": not problems,
        "assess_status": assess_status,
        "target_status": "approved" if target_ok else "blocked",
        "targeting_review_status": (
            "approved" if targeting_ok else "blocked"),
        "targeting_review": targeting_receipt,
        "problems": problems,
    }


def _validate_authorization_receipt(authorization: dict) -> None:
    if not authorization.get("authorized"):
        raise ProductReleaseBlocked(list(authorization.get("problems") or [
            "operator authorization is not current"]))
    targeting = authorization.get("targeting_review")
    if not isinstance(targeting, dict):
        raise ProductReleaseBlocked([
            "operator authorization has no exact Targeting Review receipt"])
    missing = [
        key for key in _EXACT_REVIEW_HASHES
        if not re.fullmatch(r"[0-9a-f]{64}", str(targeting.get(key) or ""))
    ]
    if missing:
        raise ProductReleaseBlocked([
            "operator authorization has incomplete Targeting Review hashes: "
            + ", ".join(missing)])


def _read_authorization(
    client_name: str, root: Path,
    authorization_fn: Optional[Callable[[str, Path], dict]],
    target_inventory: Optional[list[dict]],
    expected_target_set_sha256: Optional[str],
) -> dict:
    authorization = (
        authorization_fn(client_name, root)
        if authorization_fn is not None
        else _authorization(
            client_name, root, target_inventory=target_inventory,
            expected_target_set_sha256=expected_target_set_sha256)
    )
    if not isinstance(authorization, dict):
        raise ProductReleaseBlocked([
            "operator authorization did not return a receipt"])
    _validate_authorization_receipt(authorization)
    return canonicalize_release_value(authorization, root=root)


def _load_target_inventory(
    client_name: str, root: Path,
    loader: Optional[Callable[[str, Path], list[dict]]],
) -> list[dict]:
    if loader is None:
        raise ProductReleaseBlocked([
            "the actual current target inventory loader is required for release"])
    try:
        targets = loader(client_name, root)
    except ProductReleaseBlocked:
        raise
    except Exception as exc:  # noqa: BLE001 - live inventory fails closed
        raise ProductReleaseBlocked([
            "the actual current target inventory is unavailable: "
            f"{type(exc).__name__}: {exc}"]) from exc
    if not isinstance(targets, list) or any(
            not isinstance(row, dict) for row in targets):
        raise ProductReleaseBlocked([
            "the actual current target inventory is not a list of records"])
    return targets


def _target_inventory_sha256(targets: list[dict]) -> str:
    from agents.review import targeting_target_set_sha256

    return targeting_target_set_sha256(targets)


def _require_target_inventory_binding(
    authorization: dict, *, actual_sha256: str,
    expected_sha256: Optional[str],
) -> None:
    if expected_sha256 is not None:
        if not re.fullmatch(r"[0-9a-f]{64}", expected_sha256):
            raise ProductReleaseBlocked([
                "launch target inventory binding is not a SHA-256"])
        if expected_sha256 != actual_sha256:
            raise ProductReleaseBlocked([
                "actual current target inventory differs from its launch binding"])
    authorized_sha256 = str(
        (authorization.get("targeting_review") or {}).get(
            "target_set_sha256") or "")
    if authorized_sha256 != actual_sha256:
        raise ProductReleaseBlocked([
            "Targeting Review does not bind the actual current target inventory"])


def _load_profile(root: Path, slug: str) -> dict:
    path = root / "clients" / slug / "profile.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ProductReleaseError(
            f"client profile is unreadable: {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise ProductReleaseError(f"client profile is not an object: {path}")
    return payload


def _load_pack(root: Path, slug: str):
    from agents.golden_press.records import EvidencePack

    path = (root / "data" / "state" / "candidate_review_v1" / slug
            / f"{slug}.golden_report.evidence_pack.json")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ProductReleaseError(
            f"pressed evidence pack is unreadable: {path}: {exc}") from exc
    try:
        return path, EvidencePack.model_validate(payload)
    except Exception as exc:  # noqa: BLE001 - schema failure must be named
        raise ProductReleaseError(
            f"pressed evidence pack is invalid: {exc}") from exc


def _build_graph(
    root: Path, slug: str, client_name: str, pressed_pack_path: Path, *,
    classification_as_of: str, captured_at: str,
) -> tuple[Path, dict]:
    from agents import review
    from agents.golden_press.evidence_pack_v2 import build_corrected_pack

    connection = None
    try:
        try:
            from tools.notice_store import connect
            connection = connect()
        except Exception:  # noqa: BLE001 - stored pack remains usable
            connection = None
        research_sweep = Path(review.sweep_artifact_path(
            client_name, review_dir=str(root / "data" / "review")))
        if not research_sweep.exists():
            raise ProductReleaseError(
                "governed research sweep is missing: "
                f"{research_sweep.name}")
        return build_corrected_pack(
            slug, client_name, root=root,
            pressed_pack_path=pressed_pack_path,
            deep_sweep_path=(
                root / "data" / "state" / "retrieval" /
                f"deep_sweep_{slug}.json"),
            research_sweep_path=research_sweep,
            classification_as_of=classification_as_of,
            captured_at=captured_at,
            store_conn=connection,
        )
    finally:
        if connection is not None:
            connection.close()


def _capture_render_assets(client_name: str, product: Any) -> dict:
    from agents.golden_press.external_product_render import (
        capture_external_product_identity_assets,
    )
    from agents.golden_press.market_map_skeleton import load_css, load_runtime
    from agents.reports.report_assets import client_logo, gtm_logo

    return {
        "css": load_css(),
        "runtime": load_runtime(),
        "client_mark": client_logo(client_name),
        "gtm_mark": gtm_logo(),
        **capture_external_product_identity_assets(product),
    }


def capture_release_snapshot(
    *, client_name: str, slug: str, root: Path, business_as_of: str,
    classification_as_of: str, captured_at: str, authorization: dict,
    purpose: str = LIVE_RELEASE_PURPOSE,
) -> ReleaseSnapshot:
    """Perform every live read once and freeze the complete compiler input."""
    from agents.golden_press.evidence_pack_v2 import SCHEMA_VERSION
    from agents.golden_press.external_product_contract import (
        load_external_product_slots,
    )
    from agents.golden_press.external_product_projection import (
        EXTERNAL_PRODUCT_PROJECTION_VERSION,
        build_external_product_document,
    )
    from agents.golden_press.external_product_render import (
        EXTERNAL_PRODUCT_RENDER_VERSION,
    )
    from agents.golden_press.market_map_projection import (
        MARKET_MAP_PROJECTION_VERSION,
        build_market_map,
        capture_inputs,
    )
    from agents.golden_press.market_map_skeleton import (
        MARKET_MAP_SKELETON_VERSION,
    )
    from tools.intelligence_graph.workflow import PURSUIT_PROMOTION_VERSION

    root = Path(root).resolve()
    pressed_pack_path, pack = _load_pack(root, slug)
    profile = _load_profile(root, slug)
    _graph_path, raw_graph = _build_graph(
        root, slug, client_name, pressed_pack_path,
        classification_as_of=classification_as_of,
        captured_at=captured_at)
    graph = canonicalize_release_value(raw_graph, root=root)
    if not graph.get("graph_contract_certified"):
        problems = [
            f"graph contract: {row.get('rule_id')}: {row.get('message')}"
            for row in (graph.get("graph_contract_violations") or [])
        ]
        raise ProductReleaseBlocked(
            problems or ["the graph contract is not certified"])
    market_map_inputs = capture_inputs(
        profile=profile, slug=slug, pack=pack, evidence_pack_v2=graph)
    market_map_inputs = canonicalize_release_value(
        market_map_inputs, root=root)
    market_map_inputs["evidence_pack_v2"] = graph
    slots = load_external_product_slots()
    versions = {
        "evidence_pack_v2": SCHEMA_VERSION,
        "external_product_projection": EXTERNAL_PRODUCT_PROJECTION_VERSION,
        "external_product_render": EXTERNAL_PRODUCT_RENDER_VERSION,
        "market_map_projection": MARKET_MAP_PROJECTION_VERSION,
        "market_map_skeleton": MARKET_MAP_SKELETON_VERSION,
        "release_compiler": RELEASE_COMPILER_VERSION,
        "pursuit_promotion": PURSUIT_PROMOTION_VERSION,
    }
    preview_market_map = build_market_map(
        pack, profile=profile, slug=slug, as_of=business_as_of,
        inputs=market_map_inputs)
    preview_product = build_external_product_document(
        market_map=preview_market_map,
        graph_payload=graph,
        evidence_pack=pack,
        profile=profile,
        client_name=client_name,
        slug=slug,
        as_of=business_as_of,
        contract_slots=slots,
        contract_version=CONTRACT_VERSION,
        contract_sha256=OPERATOR_LOCKED_SLOT_SHA256,
    )
    try:
        evidence_bytes = pressed_pack_path.read_bytes()
    except OSError as exc:
        raise ProductReleaseError(
            f"pressed evidence pack bytes are unreadable: {exc}") from exc
    return ReleaseSnapshot.create(
        purpose=purpose,
        client_name=client_name,
        slug=slug,
        business_as_of=business_as_of,
        classification_as_of=classification_as_of,
        captured_at=captured_at,
        contract_slots=slots,
        versions=versions,
        authorization=authorization,
        profile=profile,
        graph=graph,
        market_map_inputs=market_map_inputs,
        render_assets=_capture_render_assets(client_name, preview_product),
        root=root,
        evidence_pack_bytes=evidence_bytes,
    )


def _parse_aware(value: str, *, label: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError) as exc:
        raise ProductReleaseBlocked([f"{label} is not an ISO instant"]) from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ProductReleaseBlocked([f"{label} has no timezone"])
    return parsed.astimezone(timezone.utc)


def _check_snapshot_freshness(snapshot: ReleaseSnapshot, *, now: str) -> None:
    classified = _parse_aware(
        snapshot.classification_as_of, label="classification_as_of")
    captured = _parse_aware(snapshot.captured_at, label="captured_at")
    current = _parse_aware(now, label="current release time")
    if classified > captured:
        raise ProductReleaseBlocked([
            "classification_as_of is later than snapshot capture"])
    if classified > current:
        raise ProductReleaseBlocked([
            "classification_as_of is in the future"])
    if current - classified > MAX_CAPTURE_AGE:
        raise ProductReleaseBlocked([
            "classification_as_of is older than fourteen days"])
    if captured > current + timedelta(minutes=5):
        raise ProductReleaseBlocked([
            "snapshot capture time is in the future"])
    if current - captured > MAX_CAPTURE_AGE:
        raise ProductReleaseBlocked([
            "snapshot capture is older than fourteen days"])
    try:
        evidence_payload = json.loads(snapshot.evidence_pack_bytes)
        evidence_generated = _parse_aware(
            str(evidence_payload.get("generated_at") or ""),
            label="evidence pack generated_at")
    except (UnicodeDecodeError, json.JSONDecodeError, AttributeError) as exc:
        raise ProductReleaseBlocked([
            f"evidence pack freshness is unreadable: {exc}"]) from exc
    if evidence_generated > current + timedelta(minutes=5):
        raise ProductReleaseBlocked([
            "evidence pack generated_at is in the future"])
    if evidence_generated > classified:
        raise ProductReleaseBlocked([
            "evidence pack is later than classification_as_of"])
    if current - evidence_generated > MAX_CAPTURE_AGE:
        raise ProductReleaseBlocked([
            "evidence pack is older than fourteen days"])
    deep_sweep_receipt = snapshot.graph.get("deep_sweep_receipt") or {}
    if deep_sweep_receipt:
        try:
            deep_sweep_generated = _parse_aware(
                str(deep_sweep_receipt.get("at") or ""),
                label="deep sweep receipt at")
        except (AttributeError, TypeError) as exc:
            raise ProductReleaseBlocked([
                f"deep sweep freshness is unreadable: {exc}"]) from exc
        if deep_sweep_generated > current + timedelta(minutes=5):
            raise ProductReleaseBlocked([
                "deep sweep receipt is in the future"])
        if deep_sweep_generated > classified:
            raise ProductReleaseBlocked([
                "deep sweep is later than classification_as_of"])
        if current - deep_sweep_generated > MAX_CAPTURE_AGE:
            raise ProductReleaseBlocked([
                "deep sweep is older than fourteen days"])
    research_mesh_receipt = snapshot.graph.get("research_mesh_receipt") or {}
    try:
        research_mesh_generated = _parse_aware(
            str(research_mesh_receipt.get("generated_at") or ""),
            label="governed research mesh generated_at")
    except (AttributeError, TypeError) as exc:
        raise ProductReleaseBlocked([
            f"governed research mesh freshness is unreadable: {exc}"]) from exc
    if research_mesh_generated > current + timedelta(minutes=5):
        raise ProductReleaseBlocked([
            "governed research mesh generated_at is in the future"])
    if research_mesh_generated > classified:
        raise ProductReleaseBlocked([
            "governed research mesh is later than classification_as_of"])
    if current - research_mesh_generated > MAX_CAPTURE_AGE:
        raise ProductReleaseBlocked([
            "governed research mesh is older than fourteen days"])


def _expected_release_names(compiled: CompiledRelease) -> set[str]:
    return set(compiled.payloads) | {"manifest.json", compiled.bundle_name}


def _validate_zip_metadata(
    archive: zipfile.ZipFile, compiled: CompiledRelease,
) -> bool:
    expected_names = sorted(set(compiled.payloads) | {"manifest.json"})
    expected_date_time = _zip_datetime(
        str(compiled.manifest.get("as_of") or ""))
    infos = archive.infolist()
    if [info.filename for info in infos] != expected_names:
        return False
    expected_members = {**compiled.payloads,
                        "manifest.json": compiled.manifest_bytes}
    for info in infos:
        if info.is_dir() or info.compress_type != zipfile.ZIP_STORED:
            return False
        if info.create_system != 3:
            return False
        if ((info.external_attr >> 16) & 0o177777) != 0o100644:
            return False
        if info.date_time != expected_date_time:
            return False
        if archive.read(info) != expected_members[info.filename]:
            return False
    return True


def _snapshot_manifest_identity_problems(
    snapshot: ReleaseSnapshot, *, payloads: dict[str, bytes], manifest: dict,
) -> list[str]:
    """Re-derive every content and manifest identity field from one snapshot."""
    problems: list[str] = []
    expected_snapshot_bytes = canonical_json_bytes(snapshot.to_dict())
    if payloads.get("captured_inputs.json") != expected_snapshot_bytes:
        problems.append("captured_inputs.json does not match the release snapshot")

    expected_receipt, expected_digest = _content_identity(snapshot, payloads)
    expected_release_id = (
        f"{snapshot.business_as_of}-{expected_digest[:24]}")
    expected_primary = (
        f"LILA_{snapshot.slug}_{snapshot.business_as_of}.html")
    expected_studio = (
        f"LILA_{snapshot.slug}_{snapshot.business_as_of}.studio.html")
    expected_bundle = (
        f"LILA_{snapshot.slug}_{expected_release_id}_COMPLETE.zip")
    expected_manifest_fields = {
        "schema_version": PRODUCT_BUNDLE_VERSION,
        "release_id": expected_release_id,
        "content_digest": expected_digest,
        "content_receipt": expected_receipt,
        "release_state": (
            "release" if snapshot.purpose == LIVE_RELEASE_PURPOSE else "fixture"),
        "release_eligible": snapshot.purpose == LIVE_RELEASE_PURPOSE,
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
        "slot_ids": [str(row.get("slot_id") or "")
                     for row in snapshot.contract_slots],
        "authorization": snapshot.authorization,
        "graph_contract_certified": (
            snapshot.graph.get("graph_contract_certified") is True),
        "render_validation_ok": True,
        "source_graph": "federal_pursuit_graph.json",
        "primary_html": expected_primary,
        "studio_html": expected_studio,
        "bundle_name": expected_bundle,
        "files": expected_receipt["files"],
    }
    if set(manifest) != set(expected_manifest_fields):
        problems.append("manifest field allowlist does not match the release snapshot")
    for field, expected in expected_manifest_fields.items():
        if manifest.get(field) != expected:
            problems.append(
                f"manifest {field} does not match the release snapshot")
    return problems


def _compiled_snapshot_identity_problems(
    compiled: CompiledRelease, snapshot: ReleaseSnapshot,
) -> list[str]:
    """Prove a supplied compilation is exactly the product of this snapshot."""
    problems: list[str] = []
    try:
        expected_compiled = compile_release_snapshot(snapshot)
    except ReleaseCompilationError as exc:
        problems.append(f"release snapshot no longer compiles: {exc}")
    else:
        if compiled != expected_compiled:
            problems.append(
                "supplied compilation differs from a fresh snapshot compilation")
    problems.extend(_snapshot_manifest_identity_problems(
        snapshot, payloads=compiled.payloads, manifest=compiled.manifest))
    expected_receipt, expected_digest = _content_identity(
        snapshot, compiled.payloads)
    expected_release_id = (
        f"{snapshot.business_as_of}-{expected_digest[:24]}")
    expected_bundle = (
        f"LILA_{snapshot.slug}_{expected_release_id}_COMPLETE.zip")
    expected_primary = (
        f"LILA_{snapshot.slug}_{snapshot.business_as_of}.html")
    expected_studio = (
        f"LILA_{snapshot.slug}_{snapshot.business_as_of}.studio.html")
    expected_zip_bytes = _zip_bytes(
        {**compiled.payloads, "manifest.json": compiled.manifest_bytes},
        day=snapshot.business_as_of,
    )
    for label, actual, expected in (
        ("content receipt", compiled.content_receipt, expected_receipt),
        ("content digest", compiled.content_digest, expected_digest),
        ("release id", compiled.release_id, expected_release_id),
        ("bundle name", compiled.bundle_name, expected_bundle),
        ("primary HTML", compiled.primary_html, expected_primary),
        ("studio HTML", compiled.studio_html, expected_studio),
        ("manifest bytes", compiled.manifest_bytes,
         canonical_json_bytes(compiled.manifest)),
        ("ZIP bytes", compiled.zip_bytes, expected_zip_bytes),
    ):
        if actual != expected:
            problems.append(
                f"compiled {label} does not match the release snapshot")
    try:
        with zipfile.ZipFile(io.BytesIO(compiled.zip_bytes)) as archive:
            if not _validate_zip_metadata(archive, compiled):
                problems.append(
                    "compiled ZIP does not match the release snapshot")
    except (OSError, KeyError, zipfile.BadZipFile, RuntimeError):
        problems.append("compiled ZIP is unreadable")
    return problems


def _stored_snapshot_compilation_problems(
    snapshot: ReleaseSnapshot, *, payloads: dict[str, bytes], manifest: dict,
    bundle_path: Path,
) -> list[str]:
    """Compare a stored release with a fresh pure compilation of its snapshot."""
    try:
        expected = compile_release_snapshot(snapshot)
    except ReleaseCompilationError as exc:
        return [f"stored release snapshot no longer compiles: {exc}"]
    problems: list[str] = []
    if payloads != expected.payloads:
        problems.append(
            "stored release payloads differ from a fresh snapshot compilation")
    if manifest != expected.manifest:
        problems.append(
            "stored manifest differs from a fresh snapshot compilation")
    try:
        stored_zip = bundle_path.read_bytes()
    except OSError:
        stored_zip = b""
    if stored_zip != expected.zip_bytes:
        problems.append(
            "stored ZIP differs from a fresh snapshot compilation")
    return problems


def _validate_existing_release(
    release_dir: Path, expected: CompiledRelease,
) -> bool:
    """Validate exact bytes and allowlists against the expected compilation."""
    try:
        actual_names = {entry.name for entry in release_dir.iterdir()}
    except OSError:
        return False
    if actual_names != _expected_release_names(expected):
        return False
    expected_loose = {
        **expected.payloads,
        "manifest.json": expected.manifest_bytes,
        expected.bundle_name: expected.zip_bytes,
    }
    for name, payload in expected_loose.items():
        path = release_dir / name
        try:
            if path.is_symlink() or path.read_bytes() != payload:
                return False
        except OSError:
            return False
    if _local_path_leaks({
            name: payload for name, payload in expected_loose.items()
            if name != expected.bundle_name}):
        return False
    try:
        with zipfile.ZipFile(release_dir / expected.bundle_name) as archive:
            if not _validate_zip_metadata(archive, expected):
                return False
    except (OSError, KeyError, zipfile.BadZipFile, RuntimeError):
        return False
    return True


def _materialize_release(
    compiled: CompiledRelease, *, root: Path, slug: str,
) -> Path:
    release_parent = root / "data" / "releases" / slug
    release_dir = release_parent / compiled.release_id
    if release_dir.exists():
        if not _validate_existing_release(release_dir, compiled):
            raise ProductReleaseError(
                f"existing release directory is incomplete or corrupt: {release_dir}")
        return release_dir

    release_parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(
        prefix=f".{compiled.release_id}.", dir=release_parent))
    try:
        for name, payload in compiled.payloads.items():
            _write(staging / name, payload)
        _write(staging / "manifest.json", compiled.manifest_bytes)
        _write(staging / compiled.bundle_name, compiled.zip_bytes)
        if not _validate_existing_release(staging, compiled):
            raise ProductReleaseError(
                "staged release does not match the expected compilation")
        os.replace(staging, release_dir)
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return release_dir


def _promote_pointer(
    compiled: CompiledRelease, *, root: Path, slug: str,
) -> dict:
    from tools.artifacts import atomic_write_json

    release_parent = root / "data" / "releases" / slug
    pointer = {
        "schema_version": CURRENT_POINTER_VERSION,
        "release_id": compiled.release_id,
        "manifest_path": f"{compiled.release_id}/manifest.json",
        "manifest_sha256": _sha_bytes(compiled.manifest_bytes),
        "bundle_path": f"{compiled.release_id}/{compiled.bundle_name}",
        "bundle_sha256": _sha_bytes(compiled.zip_bytes),
        "primary_html": f"{compiled.release_id}/{compiled.primary_html}",
    }
    atomic_write_json(release_parent / "current.json", pointer)
    return pointer


def _promote_compiled_release(
    compiled: CompiledRelease, snapshot: ReleaseSnapshot, *, root: Path,
    deliver_to_desktop: bool = False, desktop_root: Optional[Path] = None,
) -> ProductReleaseResult:
    """Materialize exact bytes and atomically promote a live snapshot."""
    if snapshot.purpose == REPRODUCIBILITY_FIXTURE_PURPOSE:
        raise ProductReleaseBlocked([
            "a reproducibility fixture cannot be promoted as a client release"])
    if snapshot.purpose != LIVE_RELEASE_PURPOSE:
        raise ProductReleaseBlocked([
            f"snapshot purpose is not releasable: {snapshot.purpose}"])
    if (snapshot.contract_version != CONTRACT_VERSION
            or snapshot.operator_locked_slot_sha256
            != OPERATOR_LOCKED_SLOT_SHA256):
        raise ProductReleaseBlocked([
            "live promotion requires the current ratified product contract"])
    if compiled.manifest.get("release_eligible") is not True:
        raise ProductReleaseBlocked([
            "compiled manifest is not release eligible"])
    identity_problems = _compiled_snapshot_identity_problems(compiled, snapshot)
    if identity_problems:
        raise ProductReleaseError(
            "compiled release does not match supplied snapshot: "
            + "; ".join(identity_problems))
    root = Path(root).resolve()
    release_dir = _materialize_release(
        compiled, root=root, slug=snapshot.slug)
    bundle_path = release_dir / compiled.bundle_name
    html_path = release_dir / compiled.primary_html

    desktop_bundle = None
    desktop_html = None
    if deliver_to_desktop:
        target_root = Path(
            desktop_root or Path.home() / "Desktop") / snapshot.client_name
        target_root.mkdir(parents=True, exist_ok=True)
        desktop_bundle = target_root / bundle_path.name
        desktop_html = target_root / compiled.primary_html
        shutil.copy2(bundle_path, desktop_bundle)
        shutil.copy2(html_path, desktop_html)
        if desktop_bundle.read_bytes() != compiled.zip_bytes:
            raise ProductReleaseError(
                "Desktop bundle copy does not match the sealed release")
        if desktop_html.read_bytes() != compiled.payloads[compiled.primary_html]:
            raise ProductReleaseError(
                "Desktop HTML copy does not match the sealed release")

    pointer = _promote_pointer(
        compiled, root=root, slug=snapshot.slug)

    return ProductReleaseResult(
        release_id=compiled.release_id,
        release_dir=str(release_dir),
        bundle_path=str(bundle_path),
        html_path=str(html_path),
        manifest_path=str(release_dir / "manifest.json"),
        manifest_sha256=pointer["manifest_sha256"],
        bundle_sha256=pointer["bundle_sha256"],
        desktop_bundle_path=(
            str(desktop_bundle) if desktop_bundle else None),
        desktop_html_path=str(desktop_html) if desktop_html else None,
    )


def build_complete_bundle(
    *, client_name: str, slug: str, root: Path, as_of: str,
    release_requested: bool,
    authorization_fn: Optional[Callable[[str, Path], dict]] = None,
    target_inventory_loader: Optional[
        Callable[[str, Path], list[dict]]] = None,
    expected_target_set_sha256: Optional[str] = None,
    deliver_to_desktop: bool = False,
    desktop_root: Optional[Path] = None,
    classification_as_of: Optional[str] = None,
    captured_at: Optional[str] = None,
) -> ProductReleaseResult:
    """Capture, compile, reauthorize, and atomically promote one bundle."""
    root = Path(root).resolve()
    if not release_requested:
        raise ProductReleaseBlocked([
            "the explicit release switch is required for the external bundle"])
    frozen_capture = captured_at or _utc_now()
    frozen_classification = classification_as_of or frozen_capture
    initial_targets = _load_target_inventory(
        client_name, root, target_inventory_loader)
    initial_target_sha256 = _target_inventory_sha256(initial_targets)
    initial_authorization = _read_authorization(
        client_name, root, authorization_fn, initial_targets,
        expected_target_set_sha256)
    _require_target_inventory_binding(
        initial_authorization, actual_sha256=initial_target_sha256,
        expected_sha256=expected_target_set_sha256)
    snapshot = capture_release_snapshot(
        client_name=client_name,
        slug=slug,
        root=root,
        business_as_of=as_of[:10],
        classification_as_of=frozen_classification,
        captured_at=frozen_capture,
        authorization=initial_authorization,
    )
    try:
        compiled = compile_release_snapshot(snapshot)
    except ReleaseCompilationError as exc:
        raise ProductReleaseBlocked([str(exc)]) from exc

    current_targets = _load_target_inventory(
        client_name, root, target_inventory_loader)
    current_target_sha256 = _target_inventory_sha256(current_targets)
    if current_target_sha256 != initial_target_sha256:
        raise ProductReleaseBlocked([
            "actual target inventory changed during release compilation"])
    current_authorization = _read_authorization(
        client_name, root, authorization_fn, current_targets,
        expected_target_set_sha256)
    _require_target_inventory_binding(
        current_authorization, actual_sha256=current_target_sha256,
        expected_sha256=expected_target_set_sha256)
    if canonical_json_bytes(current_authorization) != canonical_json_bytes(
            snapshot.authorization):
        raise ProductReleaseBlocked([
            "operator authorization changed during release compilation"])
    _check_snapshot_freshness(snapshot, now=_utc_now())
    return _promote_compiled_release(
        compiled, snapshot, root=root,
        deliver_to_desktop=deliver_to_desktop,
        desktop_root=desktop_root,
    )


def _pointer_member(release_parent: Path, value: Any) -> Path:
    raw = Path(str(value or ""))
    if raw.is_absolute():
        raise ProductReleaseError("release pointer contains an absolute path")
    candidate = (release_parent / raw).resolve(strict=False)
    try:
        candidate.relative_to(release_parent.resolve(strict=False))
    except ValueError as exc:
        raise ProductReleaseError(
            "release pointer escapes its release directory") from exc
    return candidate


def _inspect_stored_release(
    manifest_path: Path, bundle_path: Path,
) -> tuple[dict, dict[str, bytes], list[str]]:
    problems: list[str] = []
    try:
        manifest_bytes = manifest_path.read_bytes()
        manifest = json.loads(manifest_bytes.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return {}, {}, ["manifest is unreadable"]
    if canonical_json_bytes(manifest) != manifest_bytes:
        problems.append("manifest serialization is not canonical")
    if manifest.get("schema_version") != PRODUCT_BUNDLE_VERSION:
        problems.append(
            "manifest schema version is not current: expected "
            f"{PRODUCT_BUNDLE_VERSION}")
    if contains_local_path(manifest_bytes):
        problems.append("local filesystem path appears in manifest.json")
    receipt = manifest.get("content_receipt")
    if not isinstance(receipt, dict):
        problems.append("manifest has no content receipt")
        receipt = {}
    elif _sha_bytes(canonical_json_bytes(receipt)) != manifest.get(
            "content_digest"):
        problems.append("content receipt digest changed")
    files = manifest.get("files")
    if not isinstance(files, dict):
        problems.append("manifest has no file receipt mapping")
        files = {}
    if files != receipt.get("files"):
        problems.append("manifest file receipts disagree with content receipt")
    release_dir = manifest_path.parent
    expected_names = set(files) | {"manifest.json", str(
        manifest.get("bundle_name") or "")}
    try:
        actual_names = {path.name for path in release_dir.iterdir()}
    except OSError:
        actual_names = set()
    if actual_names != expected_names:
        problems.append("release directory member allowlist changed")
    loose: dict[str, bytes] = {}
    for name, file_receipt in sorted(files.items()):
        if (not isinstance(name, str) or not name
                or Path(name).name != name or name in {".", ".."}):
            problems.append(f"bundle member name is not local: {name}")
            continue
        member = release_dir / name
        try:
            if member.is_symlink():
                problems.append(f"bundle member is a symlink: {name}")
                continue
            payload = member.read_bytes()
        except OSError:
            problems.append(f"bundle member is missing or unreadable: {name}")
            continue
        loose[name] = payload
        if _sha_bytes(payload) != (file_receipt or {}).get("sha256"):
            problems.append(f"bundle member hash changed: {name}")
        if len(payload) != (file_receipt or {}).get("bytes"):
            problems.append(f"bundle member length changed: {name}")
        if contains_local_path(payload):
            problems.append(
                f"local filesystem path appears in bundle member: {name}")
    try:
        bundle_bytes = bundle_path.read_bytes()
        expected_bundle_bytes = _zip_bytes(
            {**loose, "manifest.json": manifest_bytes},
            day=str(manifest.get("as_of") or ""),
        )
        if bundle_bytes != expected_bundle_bytes:
            problems.append("bundle archive bytes are not deterministic")
        with zipfile.ZipFile(bundle_path) as archive:
            infos = archive.infolist()
            expected_archive_names = sorted(set(files) | {"manifest.json"})
            expected_date_time = _zip_datetime(
                str(manifest.get("as_of") or ""))
            if [info.filename for info in infos] != expected_archive_names:
                problems.append("bundle archive member allowlist or order changed")
            expected_payloads = {**loose, "manifest.json": manifest_bytes}
            for info in infos:
                if info.is_dir() or info.compress_type != zipfile.ZIP_STORED:
                    problems.append(
                        f"bundle archive metadata changed: {info.filename}")
                    continue
                if (info.create_system != 3
                        or ((info.external_attr >> 16) & 0o177777) != 0o100644
                        or info.date_time != expected_date_time):
                    problems.append(
                        f"bundle archive metadata changed: {info.filename}")
                if archive.read(info) != expected_payloads.get(info.filename):
                    problems.append(
                        f"bundle archive member bytes changed: {info.filename}")
    except (OSError, KeyError, zipfile.BadZipFile, RuntimeError):
        problems.append("bundle archive is unreadable")
    return manifest, loose, problems


def product_release_state(
    slug: str, *, root: Path,
    target_inventory: Optional[list[dict]] = None,
) -> dict:
    """Validate the current bundle and re-read operator authorization."""
    root = Path(root).resolve()
    release_parent = root / "data" / "releases" / slug
    pointer_path = release_parent / "current.json"
    try:
        pointer = json.loads(pointer_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {
            "releasable": False,
            "reason": "no complete LILA bundle on file",
            "family": "lila_federal_market_map",
            "path": None,
            "bundle_path": None,
            "release_id": None,
        }
    problems: list[str] = []
    if pointer.get("schema_version") != CURRENT_POINTER_VERSION:
        problems.append("current release pointer version is not supported")
    try:
        manifest_path = _pointer_member(
            release_parent, pointer.get("manifest_path"))
        bundle_path = _pointer_member(
            release_parent, pointer.get("bundle_path"))
        html_path = _pointer_member(
            release_parent, pointer.get("primary_html"))
    except ProductReleaseError as exc:
        manifest_path = bundle_path = html_path = release_parent / ".invalid"
        problems.append(str(exc))
    for path, expected, label in (
        (manifest_path, pointer.get("manifest_sha256"), "manifest"),
        (bundle_path, pointer.get("bundle_sha256"), "bundle"),
    ):
        if not path.is_file():
            problems.append(f"{label} is missing")
        elif _sha_file(path) != expected:
            problems.append(f"{label} hash changed")
    manifest, loose_payloads, stored_problems = _inspect_stored_release(
        manifest_path, bundle_path)
    problems.extend(stored_problems)
    if manifest.get("release_eligible") is not True:
        problems.append("manifest is not release eligible")
    if manifest.get("purpose") != LIVE_RELEASE_PURPOSE:
        problems.append("manifest is not a live release input")
    if manifest.get("release_id") != pointer.get("release_id"):
        problems.append("pointer release identity disagrees with manifest")
    manifest_path_valid = False
    bundle_path_valid = False
    primary_path_valid = False
    release_id = manifest.get("release_id")
    if (not isinstance(release_id, str)
            or not re.fullmatch(r"\d{4}-\d{2}-\d{2}-[0-9a-f]{24}", release_id)):
        problems.append("manifest release identity is malformed")
    else:
        expected_release_dir = (release_parent / release_id).resolve(
            strict=False)
        if manifest_path != expected_release_dir / "manifest.json":
            problems.append(
                "pointer manifest path is not the declared release manifest")
        else:
            manifest_path_valid = True
    bundle_name = manifest.get("bundle_name")
    if (not isinstance(bundle_name, str) or not bundle_name
            or Path(bundle_name).name != bundle_name):
        problems.append("manifest bundle name is not a release member")
    elif bundle_path != (manifest_path.parent / bundle_name).resolve(
            strict=False):
        problems.append("pointer bundle path is not the declared release bundle")
    else:
        bundle_path_valid = True
    primary_html = manifest.get("primary_html")
    if (not isinstance(primary_html, str) or not primary_html.endswith(".html")
            or Path(primary_html).name != primary_html):
        problems.append("manifest primary HTML is not a declared HTML member")
    elif html_path != (manifest_path.parent / primary_html).resolve(
            strict=False):
        problems.append("pointer primary HTML is not the declared primary HTML")
    else:
        primary_path_valid = True
    if (manifest.get("product_contract_version") != CONTRACT_VERSION
            or manifest.get("operator_locked_slot_sha256")
            != OPERATOR_LOCKED_SLOT_SHA256):
        problems.append(
            "stored release does not use the current ratified product contract")
    client_name = manifest.get("client_name")
    if not isinstance(client_name, str) or not client_name.strip():
        problems.append("manifest has no client identity for current authorization")
    else:
        try:
            current_authorization = _authorization(
                client_name, root, target_inventory=target_inventory)
            _validate_authorization_receipt(current_authorization)
        except Exception as exc:  # noqa: BLE001 - shelf state fails closed
            problems.append(
                "operator authorization is unavailable or stale: "
                f"{type(exc).__name__}: {exc}")
        else:
            if canonical_json_bytes(current_authorization) != canonical_json_bytes(
                    manifest.get("authorization") or {}):
                problems.append(
                    "operator authorization differs from the release receipt")
    try:
        captured_inputs = loose_payloads["captured_inputs.json"]
        snapshot_payload = json.loads(captured_inputs.decode("utf-8"))
        snapshot = ReleaseSnapshot.from_dict(snapshot_payload)
        if snapshot.slug != slug:
            problems.append(
                "release snapshot client slug disagrees with the current pointer")
        if (snapshot.contract_version != CONTRACT_VERSION
                or snapshot.operator_locked_slot_sha256
                != OPERATOR_LOCKED_SLOT_SHA256):
            problems.append(
                "release snapshot does not use the current ratified product contract")
        problems.extend(_snapshot_manifest_identity_problems(
            snapshot, payloads=loose_payloads, manifest=manifest))
        problems.extend(_stored_snapshot_compilation_problems(
            snapshot, payloads=loose_payloads, manifest=manifest,
            bundle_path=bundle_path))
        _check_snapshot_freshness(snapshot, now=_utc_now())
    except Exception as exc:  # noqa: BLE001 - malformed snapshot fails closed
        problems.append(
            "release snapshot is unavailable or stale: "
            f"{type(exc).__name__}: {exc}")
    if not html_path.is_file():
        problems.append("primary HTML is missing")
    return {
        "releasable": not problems,
        "reason": "release" if not problems else "; ".join(problems),
        "family": "lila_federal_market_map",
        "path": (
            str(html_path) if primary_path_valid and html_path.is_file()
            else None),
        "bundle_path": (
            str(bundle_path) if bundle_path_valid and bundle_path.is_file()
            else None),
        "release_id": pointer.get("release_id"),
        "manifest_path": (
            str(manifest_path)
            if manifest_path_valid and manifest_path.is_file() else None),
        "updated": (
            manifest_path.stat().st_mtime if manifest_path.is_file() else 0.0),
    }


__all__ = (
    "CURRENT_POINTER_VERSION",
    "PRODUCT_BUNDLE_VERSION",
    "ProductReleaseBlocked",
    "ProductReleaseError",
    "ProductReleaseResult",
    "build_complete_bundle",
    "capture_release_snapshot",
    "compile_release_snapshot",
    "product_release_state",
)
