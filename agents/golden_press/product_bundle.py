"""One fail-closed release action for the complete LILA product bundle."""

from __future__ import annotations

import hashlib
import json
import os
import re
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


PRODUCT_BUNDLE_VERSION = "lila-complete-bundle.v2.2026-08-24"
CURRENT_POINTER_VERSION = "lila-release-pointer.v1"
_LOCAL_PATH = re.compile(
    rb"(?:file://|(?:^|[\"'\s(=:])/(?:users|home)/)", re.I | re.M)


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


def _portable_sidecar(value: Any, *, root: Path) -> Any:
    """Copy a JSON sidecar while replacing host-local path values.

    The graph and captured inputs remain unchanged in memory for projection and
    diagnostics.  Only their outgoing replay copies become portable.  Evidence
    fields are otherwise preserved verbatim; an embedded local path that is not
    itself a path value is refused by the whole-bundle gate below.
    """
    if isinstance(value, dict):
        return {
            str(key): _portable_sidecar(item, root=root)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [_portable_sidecar(item, root=root) for item in value]
    if isinstance(value, (set, frozenset)):
        return [
            _portable_sidecar(item, root=root)
            for item in sorted(value, key=repr)
        ]
    if isinstance(value, Path):
        value = str(value)
    if not isinstance(value, str):
        return value

    folded = value.casefold()
    if folded.startswith("file://"):
        local = value[len("file://"):]
    elif folded.startswith(("/users/", "/home/")):
        local = value
    else:
        return value
    path = Path(local)
    try:
        return path.resolve(strict=False).relative_to(root).as_posix()
    except (OSError, ValueError):
        return f"local-artifact:{path.name or 'available'}"


def _refuse_local_path_leaks(payloads: dict[str, bytes]) -> None:
    """Fail closed when any outgoing bundle member exposes a host-local path."""
    leaking = _local_path_leaks(payloads)
    if leaking:
        raise ProductReleaseBlocked([
            "local filesystem path appears in outgoing bundle member: " + name
            for name in leaking
        ])


def _local_path_leaks(payloads: dict[str, bytes]) -> list[str]:
    """Name payloads that expose a host-local path marker."""
    return [
        name for name, payload in sorted(payloads.items())
        if _LOCAL_PATH.search(payload)
    ]


def _archive_local_path_leaks(bundle_path: Path) -> list[str]:
    """Scan every stored ZIP member, including unlisted legacy members."""
    leaks = []
    with zipfile.ZipFile(bundle_path) as archive:
        for info in archive.infolist():
            if info.is_dir():
                continue
            if (_LOCAL_PATH.search(info.filename.encode("utf-8")) or
                    _LOCAL_PATH.search(archive.read(info))):
                leaks.append(info.filename)
    return sorted(leaks)


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


def _build_graph(root: Path, slug: str, client_name: str,
                 pressed_pack_path: Path, *, as_of: str) -> tuple[Path, dict]:
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
            deep_sweep_path=(
                root / "data" / "state" / "retrieval" /
                f"deep_sweep_{slug}.json"
            ),
            classification_as_of=as_of,
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
        manifest_bytes = manifest_path.read_bytes()
        manifest = json.loads(manifest_bytes.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    if manifest.get("schema_version") != PRODUCT_BUNDLE_VERSION:
        return None
    files = manifest.get("files")
    if not isinstance(files, dict):
        return None
    stored_payloads = {"manifest.json": manifest_bytes}
    for name, receipt in files.items():
        path = release_dir / name
        try:
            payload = path.read_bytes()
        except OSError:
            return None
        if _sha_bytes(payload) != (receipt or {}).get("sha256"):
            return None
        stored_payloads[name] = payload
    if _local_path_leaks(stored_payloads):
        return None
    bundle_path = release_dir / manifest.get("bundle_name", "")
    if not bundle_path.is_file():
        return None
    try:
        if _archive_local_path_leaks(bundle_path):
            return None
    except (OSError, zipfile.BadZipFile, RuntimeError):
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
    profile = _load_profile(root, slug)
    graph_path, graph = _build_graph(
        root, slug, client_name, pressed_pack_path, as_of=as_of)
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
        profile=profile, client_name=client_name, slug=slug, as_of=as_of)
    studio_html, client_html = render_external_product(product)
    validation = validate_external_product_html(client_html, product)
    if not validation.get("ok"):
        raise ProductReleaseBlocked([
            f"render: {row.get('rule')}: {row.get('detail')}"
            for row in validation.get("violations") or []
        ])

    product_bytes = _json_bytes(product.to_dict())
    graph_bytes = _json_bytes(_portable_sidecar(graph, root=root))
    evidence_bytes = pressed_pack_path.read_bytes()
    inputs_bytes = _json_bytes(_portable_sidecar(captured_inputs, root=root))
    validation_bytes = _json_bytes({
        **validation,
        "authorization": authorization,
        "external_family": family.family_id,
    })
    fingerprint = _sha_bytes(
        product_bytes + graph_bytes + evidence_bytes + validation_bytes)[:12]
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
                "manifest.json binds every file by SHA-256.\n"
            ).encode("utf-8"),
        }
        _refuse_local_path_leaks(payloads)
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
        _refuse_local_path_leaks({
            **payloads,
            "manifest.json": manifest_bytes,
        })
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
    """Validate hashes and re-read the current operator authorization gates."""
    root = Path(root).resolve()
    pointer_path = root / "data" / "releases" / slug / "current.json"
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
    manifest_bytes = b""
    try:
        manifest_bytes = manifest_path.read_bytes()
        manifest = json.loads(manifest_bytes.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        manifest = {}
        problems.append("manifest is unreadable")
    if manifest.get("schema_version") != PRODUCT_BUNDLE_VERSION:
        problems.append(
            "manifest schema version is not current: expected "
            f"{PRODUCT_BUNDLE_VERSION}")
    if manifest_bytes and _LOCAL_PATH.search(manifest_bytes):
        problems.append("local filesystem path appears in manifest.json")
    if manifest.get("release_eligible") is not True:
        problems.append("manifest is not release eligible")
    client_name = manifest.get("client_name")
    if not isinstance(client_name, str) or not client_name.strip():
        problems.append("manifest has no client identity for current authorization")
    else:
        try:
            current_authorization = _authorization(client_name, root)
        except Exception as exc:  # noqa: BLE001 - current release state fails closed
            problems.append(
                "operator authorization is unavailable: "
                f"{type(exc).__name__}: {exc}")
        else:
            if not current_authorization.get("authorized"):
                problems.extend(
                    str(problem) for problem in (
                        current_authorization.get("problems") or
                        ["operator authorization is not current"]
                    )
                )
    release_dir = manifest_path.parent
    for name, receipt in sorted((manifest.get("files") or {}).items()):
        member = release_dir / name
        expected = (receipt or {}).get("sha256")
        if not member.is_file():
            problems.append(f"bundle member is missing: {name}")
            continue
        try:
            payload = member.read_bytes()
        except OSError:
            problems.append(f"bundle member is unreadable: {name}")
            continue
        if not expected or _sha_bytes(payload) != expected:
            problems.append(f"bundle member hash changed: {name}")
        if _LOCAL_PATH.search(payload):
            problems.append(
                f"local filesystem path appears in bundle member: {name}")
    if bundle_path.is_file():
        try:
            archive_leaks = _archive_local_path_leaks(bundle_path)
        except (OSError, zipfile.BadZipFile, RuntimeError):
            problems.append("bundle archive is unreadable")
        else:
            problems.extend(
                "local filesystem path appears in archive member: " + name
                for name in archive_leaks
            )
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
