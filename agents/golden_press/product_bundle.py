"""One fail-closed release action for the complete LILA product bundle."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Optional

from agents.golden_press.external_product_contract import CONTRACT_VERSION
from agents.golden_press.external_product_projection import (
    build_external_product_document,
)
from agents.golden_press.external_product_render import (
    render_external_product,
    validate_external_product_html,
)
from agents.reports.product_families import external_product_family


PRODUCT_BUNDLE_VERSION = "lila-complete-bundle.v1.2026-08-23"
CURRENT_POINTER_VERSION = "lila-release-pointer.v1"


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


def _json_bytes(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True,
                       indent=2) + "\n").encode("utf-8")


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


def _authorization(client_name: str, root: Path) -> dict:
    """Read the existing Assess and Target operator gates without changing them."""
    from agents.assess.approval import assess_approval_for_release
    from agents.review import target_gate_status

    review_dir = root / "data" / "review"
    _approval, assess_status, assess_problems = assess_approval_for_release(
        client_name, review_dir=str(review_dir))
    target_ok, target_problems = target_gate_status(
        client_name, review_dir=str(review_dir))
    problems = []
    if assess_status != "approved":
        problems.append(
            "Assess approval is not current: "
            + "; ".join(assess_problems or [assess_status]))
    if not target_ok:
        problems.append(
            "Target approval is not current: "
            + "; ".join(target_problems or ["Target is locked"]))
    return {
        "authorized": not problems,
        "assess_status": assess_status,
        "target_status": "approved" if target_ok else "blocked",
        "problems": problems,
    }


def _load_profile(root: Path, slug: str) -> dict:
    path = root / "clients" / slug / "profile.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ProductReleaseError(f"client profile is unreadable: {path}: {exc}") from exc
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
        raise ProductReleaseError(f"pressed evidence pack is unreadable: {path}: {exc}") from exc
    try:
        return path, EvidencePack.model_validate(payload)
    except Exception as exc:  # noqa: BLE001 - schema failure must be named
        raise ProductReleaseError(f"pressed evidence pack is invalid: {exc}") from exc


def _require_output_retention(root: Path, slug: str, pack, leadgen: Optional[dict] = None) -> None:
    """Reject silent source/detail loss before replacing the last good release."""
    state = product_release_state(slug, root=root)
    if not state.get("releasable"):
        return
    previous = Path(state["manifest_path"]).parent / "evidence_pack.json"
    old = json.loads(previous.read_text(encoding="utf-8"))
    current = {row.record_id: row.model_dump(mode="json") for row in pack.records}
    problems = []
    prior_child_path = previous.parent / "leadgen_status.json"
    if leadgen is not None and prior_child_path.exists():
        prior_child = json.loads(prior_child_path.read_text(encoding="utf-8"))
        if prior_child.get("status") == "complete" and leadgen.get("status") != "complete":
            problems.append("previous complete lead generation became unavailable")
    prior_quality = previous.parent / "quality_baseline.json"
    if prior_quality.exists() and leadgen is not None:
        baseline = json.loads(prior_quality.read_text())
        current_cases = {c["record"]["notice_id"]: c
                         for c in (leadgen.get("reviewed_cases") or {}).get("cases", [])}
        for case in (baseline.get("reviewed_cases") or {}).get("cases", []):
            rid = case["record"]["notice_id"]
            newer = current_cases.get(rid)
            if newer is None:
                problems.append(f"reviewed assessment history removed: {rid}")
                continue
            by_name = {t["name"]: t for t in newer.get("targets", [])}
            for target in case.get("targets", []):
                retained = by_name.get(target["name"], {})
                for field in ("role", "email", "phone", "route", "reason_to_contact", "next_ask", "evidence"):
                    if target.get(field) and not retained.get(field):
                        problems.append(f"reviewed target detail removed: {rid} / {target['name']} / {field}")
    for row in old.get("records", []):
        identity = row.get("record_id")
        newer = current.get(identity)
        if newer is None:
            problems.append(f"assessment record removed: {identity}")
            continue
        for field in ("description", "url", "contact_name", "contact_email", "contact_phone"):
            if row.get(field) and not newer.get(field):
                problems.append(f"assessment detail removed: {identity} / {field}")
    if problems:
        raise ProductReleaseBlocked([
            "output retention: prior complete release preserved; reconcile record/detail loss before promotion",
            *problems])


def _build_graph(root: Path, slug: str, client_name: str,
                 pressed_pack_path: Path) -> tuple[Path, dict]:
    from agents.golden_press.evidence_pack_v2 import build_corrected_pack

    connection = None
    try:
        try:
            from tools.notice_store import connect
            connection = connect()
        except Exception:  # noqa: BLE001 - stored pack remains usable
            connection = None
        return build_corrected_pack(
            slug, client_name, root=root,
            pressed_pack_path=pressed_pack_path,
            store_conn=connection,
        )
    finally:
        if connection is not None:
            connection.close()


def _zip_datetime(as_of: str) -> tuple[int, int, int, int, int, int]:
    try:
        year, month, day = (int(value) for value in as_of[:10].split("-"))
        return max(1980, year), month, day, 0, 0, 0
    except (ValueError, TypeError):
        return 1980, 1, 1, 0, 0, 0


def _deterministic_zip(path: Path, files: list[Path], *, root: Path,
                       as_of: str) -> None:
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED,
                         compresslevel=9) as archive:
        for file_path in sorted(files, key=lambda item: item.relative_to(root).as_posix()):
            relative = file_path.relative_to(root).as_posix()
            info = zipfile.ZipInfo(relative, date_time=_zip_datetime(as_of))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            archive.writestr(info, file_path.read_bytes(), compress_type=zipfile.ZIP_DEFLATED,
                             compresslevel=9)


def _validate_existing_release(release_dir: Path) -> Optional[ProductReleaseResult]:
    manifest_path = release_dir / "manifest.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    for name, receipt in (manifest.get("files") or {}).items():
        path = release_dir / name
        if not path.is_file() or _sha_file(path) != receipt.get("sha256"):
            return None
    bundle_path = release_dir / manifest.get("bundle_name", "")
    if not bundle_path.is_file():
        return None
    return ProductReleaseResult(
        release_id=manifest.get("release_id", release_dir.name),
        release_dir=str(release_dir), bundle_path=str(bundle_path),
        html_path=str(release_dir / manifest["primary_html"]),
        manifest_path=str(manifest_path), manifest_sha256=_sha_file(manifest_path),
        bundle_sha256=_sha_file(bundle_path),
    )


def build_complete_bundle(
    *,
    client_name: str,
    slug: str,
    root: Path,
    as_of: str,
    release_requested: bool,
    authorization_fn: Optional[Callable[[str, Path], dict]] = None,
    deliver_to_desktop: bool = False,
    desktop_root: Optional[Path] = None,
) -> ProductReleaseResult:
    """Build and atomically promote one complete external product bundle.

    There is no partial external success. The final release directory and ZIP
    appear only after operator gates, graph certification, renderer validation,
    and every content hash agree.
    """

    root = Path(root).resolve()
    if not release_requested:
        raise ProductReleaseBlocked([
            "the explicit release switch is required for the external bundle"])
    authorization = (authorization_fn or _authorization)(client_name, root)
    if not authorization.get("authorized"):
        raise ProductReleaseBlocked(list(authorization.get("problems") or [
            "operator authorization is not current"]))

    family = external_product_family()
    pressed_pack_path, pack = _load_pack(root, slug)
    from agents.golden_press.product_leadgen import build_leadgen_companion
    try:
        leadgen = build_leadgen_companion(client_name, root, evidence_pack=pack)
    except Exception as exc:  # additive child cannot erase the parent report
        if (root / "data" / "review" / f"{slug}.dossier.json").exists() or (
                root / "data" / "review" / f"{slug}.reviewed_cases.json").exists():
            raise ProductReleaseBlocked([f"lead workflow: {exc}; prior release preserved"]) from exc
        leadgen = {"status": "unavailable", "error": str(exc),
                   "next_action": "Refresh the current assessment input and retry lead generation."}
    from agents.golden_press.product_leadgen import restore_assessment_population
    from tools.notice_store import connect
    connection = connect(root / "data" / "state" / "notice_store" / "notices.db")
    try:
        pack = restore_assessment_population(pack, leadgen, conn=connection)
    finally:
        connection.close()
    _require_output_retention(root, slug, pack, leadgen)
    pressed_pack_path = pressed_pack_path.with_name(f"{slug}.assessment_release.evidence_pack.json")
    _write(pressed_pack_path, _json_bytes(pack.model_dump(mode="json")))
    profile = _load_profile(root, slug)
    graph_path, graph = _build_graph(root, slug, client_name, pressed_pack_path)
    if not graph.get("graph_contract_certified"):
        problems = [
            f"graph contract: {row.get('rule_id')}: {row.get('message')}"
            for row in (graph.get("graph_contract_violations") or [])
        ]
        raise ProductReleaseBlocked(problems or ["the graph contract is not certified"])

    from agents.golden_press.market_map_projection import (
        build_market_map, capture_inputs,
    )
    captured_inputs = capture_inputs(profile=profile, slug=slug, pack=pack)
    market_map = build_market_map(
        pack, profile=profile, slug=slug, as_of=as_of,
        inputs=captured_inputs)
    product = build_external_product_document(
        market_map=market_map, graph_payload=graph, evidence_pack=pack,
        profile=profile, client_name=client_name, slug=slug, as_of=as_of,
        leadgen=leadgen)
    studio_html, client_html = render_external_product(product)
    validation = validate_external_product_html(client_html, product)
    if not validation.get("ok"):
        raise ProductReleaseBlocked([
            f"render: {row.get('rule')}: {row.get('detail')}"
            for row in validation.get("violations") or []
        ])

    product_bytes = _json_bytes(product.to_dict())
    graph_bytes = _json_bytes(graph)
    evidence_bytes = pressed_pack_path.read_bytes()
    inputs_bytes = _json_bytes(captured_inputs)
    validation_bytes = _json_bytes({
        **validation,
        "authorization": authorization,
        "external_family": family.family_id,
        "leadgen": {k: v for k, v in leadgen.items()
                    if k not in {"receipt", "assessment", "html", "scorecard_csv"}},
    })
    fingerprint = _sha_bytes(
        product_bytes + graph_bytes + evidence_bytes + validation_bytes
        + _json_bytes(leadgen))[:12]
    release_id = f"{as_of[:10]}-{fingerprint}"
    release_parent = root / "data" / "releases" / slug
    release_dir = release_parent / release_id
    existing = _validate_existing_release(release_dir)
    if existing is not None:
        return existing
    if release_dir.exists():
        raise ProductReleaseError(
            f"existing release directory is incomplete or corrupt: {release_dir}")

    release_parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{release_id}.", dir=release_parent))
    try:
        names = {
            "primary_html": f"LILA_{slug}_{as_of[:10]}.html",
            "studio_html": f"LILA_{slug}_{as_of[:10]}.studio.html",
            "product": "product.json",
            "graph": "federal_pursuit_graph.json",
            "evidence": "evidence_pack.json",
            "inputs": "captured_inputs.json",
            "validation": "validation.json",
            "readme": "README.txt",
        }
        payloads = {
            names["primary_html"]: client_html.encode("utf-8"),
            names["studio_html"]: studio_html.encode("utf-8"),
            names["product"]: product_bytes,
            names["graph"]: graph_bytes,
            names["evidence"]: evidence_bytes,
            names["inputs"]: inputs_bytes,
            names["validation"]: validation_bytes,
            names["readme"]: (
                "LILA complete release bundle\n\n"
                "Open the LILA_*.html file for the client product.\n"
                "product.json is the exact eight-slot projection.\n"
                "federal_pursuit_graph.json carries the certified relationships.\n"
                "evidence_pack.json and captured_inputs.json preserve replay inputs.\n"
                "validation.json records release and rendered checks.\n"
                "leadgen.html and leadgen.json contain assessment-bound child leads when available.\n"
                "assessment.json preserves the source ledger; leadgen_status.json discloses gaps.\n"
                "manifest.json binds every file by SHA-256.\n"
            ).encode("utf-8"),
        }
        payloads["leadgen_status.json"] = _json_bytes({
            k: v for k, v in leadgen.items()
            if k not in {"receipt", "assessment", "html", "scorecard_csv"}})
        if leadgen.get("status") == "complete":
            payloads["leadgen.json"] = _json_bytes(leadgen["receipt"])
            payloads["assessment.json"] = _json_bytes(leadgen["assessment"])
            payloads["leadgen.html"] = leadgen["html"].encode("utf-8")
            payloads["leadgen_scorecard.csv"] = leadgen["scorecard_csv"].encode("utf-8")
            if leadgen["receipt"].get("company_dossier"):
                dossier_bytes = Path(leadgen["receipt"]["dossier_path"]).read_bytes()
                if _sha_bytes(dossier_bytes) != leadgen["receipt"]["dossier_sha256"]:
                    raise ProductReleaseBlocked(["company dossier changed during release; retry with a stable input"])
                payloads["company_dossier.json"] = dossier_bytes
            if leadgen.get("reviewed_cases"):
                payloads["reviewed_cases.json"] = _json_bytes(leadgen["reviewed_cases"])
            if leadgen.get("quality_baseline"):
                payloads["quality_baseline.json"] = _json_bytes(leadgen["quality_baseline"])
        for name, payload in payloads.items():
            _write(staging / name, payload)
        file_receipts = {
            name: {"sha256": _sha_bytes(payload), "bytes": len(payload)}
            for name, payload in sorted(payloads.items())
        }
        bundle_name = f"LILA_{slug}_{release_id}_COMPLETE.zip"
        manifest = {
            "schema_version": PRODUCT_BUNDLE_VERSION,
            "release_id": release_id,
            "release_state": "release",
            "release_eligible": True,
            "client_name": client_name,
            "slug": slug,
            "as_of": as_of,
            "product_family": family.family_id,
            "product_contract_version": CONTRACT_VERSION,
            "slot_ids": [slot.slot_id for slot in product.slots],
            "authorization": authorization,
            "graph_contract_certified": True,
            "render_validation_ok": True,
            "source_graph": names["graph"],
            "primary_html": names["primary_html"],
            "bundle_name": bundle_name,
            "files": file_receipts,
        }
        manifest_bytes = _json_bytes(manifest)
        _write(staging / "manifest.json", manifest_bytes)
        zip_members = [staging / name for name in payloads] + [staging / "manifest.json"]
        _deterministic_zip(staging / bundle_name, zip_members,
                           root=staging, as_of=as_of)
        os.replace(staging, release_dir)
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise

    manifest_path = release_dir / "manifest.json"
    bundle_path = release_dir / bundle_name
    current = {
        "schema_version": CURRENT_POINTER_VERSION,
        "release_id": release_id,
        "manifest_path": str(manifest_path),
        "manifest_sha256": _sha_file(manifest_path),
        "bundle_path": str(bundle_path),
        "bundle_sha256": _sha_file(bundle_path),
        "primary_html": str(release_dir / names["primary_html"]),
    }
    from tools.artifacts import atomic_write_json
    atomic_write_json(release_parent / "current.json", current)

    desktop_bundle = None
    desktop_html = None
    if deliver_to_desktop:
        target_root = Path(desktop_root or Path.home() / "Desktop") / client_name
        target_root.mkdir(parents=True, exist_ok=True)
        desktop_bundle = target_root / bundle_path.name
        desktop_html = target_root / names["primary_html"]
        shutil.copy2(bundle_path, desktop_bundle)
        shutil.copy2(release_dir / names["primary_html"], desktop_html)

    return ProductReleaseResult(
        release_id=release_id, release_dir=str(release_dir),
        bundle_path=str(bundle_path),
        html_path=str(release_dir / names["primary_html"]),
        manifest_path=str(manifest_path),
        manifest_sha256=current["manifest_sha256"],
        bundle_sha256=current["bundle_sha256"],
        desktop_bundle_path=str(desktop_bundle) if desktop_bundle else None,
        desktop_html_path=str(desktop_html) if desktop_html else None,
    )


def product_release_state(slug: str, *, root: Path) -> dict:
    """Validate the current complete bundle without deriving a second verdict."""
    pointer_path = Path(root) / "data" / "releases" / slug / "current.json"
    try:
        pointer = json.loads(pointer_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"releasable": False, "reason": "no complete LILA bundle on file",
                "family": "lila_federal_market_map", "path": None,
                "bundle_path": None, "release_id": None}
    manifest_path = Path(pointer.get("manifest_path") or "")
    bundle_path = Path(pointer.get("bundle_path") or "")
    html_path = Path(pointer.get("primary_html") or "")
    problems = []
    for path, expected, label in (
        (manifest_path, pointer.get("manifest_sha256"), "manifest"),
        (bundle_path, pointer.get("bundle_sha256"), "bundle"),
    ):
        if not path.is_file():
            problems.append(f"{label} is missing")
        elif _sha_file(path) != expected:
            problems.append(f"{label} hash changed")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        manifest = {}
        problems.append("manifest is unreadable")
    if manifest.get("release_eligible") is not True:
        problems.append("manifest is not release eligible")
    release_dir = manifest_path.parent
    for name, receipt in sorted((manifest.get("files") or {}).items()):
        member = release_dir / name
        expected = (receipt or {}).get("sha256")
        if not member.is_file():
            problems.append(f"bundle member is missing: {name}")
        elif not expected or _sha_file(member) != expected:
            problems.append(f"bundle member hash changed: {name}")
    if not html_path.is_file():
        problems.append("primary HTML is missing")
    return {
        "releasable": not problems,
        "reason": "release" if not problems else "; ".join(problems),
        "family": "lila_federal_market_map",
        "path": str(html_path) if html_path.is_file() else None,
        "bundle_path": str(bundle_path) if bundle_path.is_file() else None,
        "release_id": pointer.get("release_id"),
        "manifest_path": str(manifest_path) if manifest_path.is_file() else None,
        "updated": manifest_path.stat().st_mtime if manifest_path.is_file() else 0.0,
    }


__all__ = (
    "PRODUCT_BUNDLE_VERSION",
    "ProductReleaseBlocked",
    "ProductReleaseError",
    "ProductReleaseResult",
    "build_complete_bundle",
    "product_release_state",
)
