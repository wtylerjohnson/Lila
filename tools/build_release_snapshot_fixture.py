#!/usr/bin/env python3
"""Seal a real-data ReleaseSnapshot as a non-promotable replay fixture."""

# ruff: noqa: E402 - direct script execution needs the repository root on sys.path.

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agents.golden_press.evidence_pack_v2 import (
    SCHEMA_VERSION,
    build_corrected_pack,
)
from agents.golden_press.evidence_route import CLASSIFIER_VERSION
from agents.golden_press.external_product_contract import (
    load_external_product_slots,
)
from agents.golden_press.external_product_projection import (
    EXTERNAL_PRODUCT_PROJECTION_VERSION,
)
from agents.golden_press.external_product_render import (
    EXTERNAL_PRODUCT_RENDER_VERSION,
)
from agents.golden_press.market_map_projection import (
    MARKET_MAP_PROJECTION_VERSION,
    capture_inputs,
)
from agents.golden_press.market_map_skeleton import (
    MARKET_MAP_SKELETON_VERSION,
    load_css,
    load_runtime,
)
from agents.golden_press.market_map_validate import BANNED_VOCABULARY
from agents.golden_press.records import EvidencePack
from agents.golden_press.release_compiler import RELEASE_COMPILER_VERSION
from agents.golden_press.release_snapshot import (
    REPRODUCIBILITY_FIXTURE_PURPOSE,
    ReleaseSnapshot,
    canonical_json_bytes,
    canonicalize_release_value,
)
from agents.reports.report_assets import client_logo, gtm_logo
from tools.atomic_io import atomic_write_text
from tools.intelligence_graph.cache import stable_hash


_FIXTURE_REPLACEMENTS = (
    "market segment",
    "research timing",
    "full assessment",
    "full assessment",
    "opportunity requiring review",
    "unverified match",
)
if len(_FIXTURE_REPLACEMENTS) != len(BANNED_VOCABULARY):
    raise RuntimeError("fixture vocabulary replacement contract is incomplete")


def _aware(value: str) -> str:
    text = str(value or "").strip()
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"invalid frozen clock: {text}") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"frozen clock has no timezone: {text}")
    return parsed.astimezone(timezone.utc).isoformat()


def _replace_vocabulary(value, *, key: bool = False):
    if isinstance(value, dict):
        normalized = {}
        for raw_name, item in value.items():
            name = _replace_vocabulary(str(raw_name), key=True)
            if name in normalized:
                raise ValueError(
                    f"fixture vocabulary normalization collides at {name!r}")
            normalized[name] = _replace_vocabulary(item)
        return normalized
    if isinstance(value, list):
        return [_replace_vocabulary(item) for item in value]
    if not isinstance(value, str):
        return value
    result = value
    for term, replacement in zip(
            BANNED_VOCABULARY, _FIXTURE_REPLACEMENTS):
        source = term
        target = replacement.replace(" ", "_") if key else replacement
        result = re.sub(
            r"(?<![a-z0-9])" + re.escape(source)
            + r"(?:s|es|'s)?(?![a-z0-9])",
            target, result, flags=re.I)
    return result


def _semantic_cache_receipt(receipt: dict) -> dict:
    providers = {
        name: {
            key: value for key, value in sorted((provider or {}).items())
            if key in {"owner", "kind", "access", "producer"}
        }
        for name, provider in sorted((receipt.get("providers") or {}).items())
    }
    return {
        "schema_version": "incremental-cache-semantic-receipt-v1",
        "adapter": receipt.get("adapter"),
        "client_slug": receipt.get("client_slug"),
        "context_hash": receipt.get("context_hash"),
        "semantic_dependencies": dict(sorted(
            (receipt.get("semantic_dependencies") or {}).items())),
        "providers": providers,
    }


def build_fixture(
    *, client_name: str, slug: str, evidence_path: Path,
    capture_receipt_path: Path, output_path: Path,
    classification_as_of: str, captured_at: str,
) -> ReleaseSnapshot:
    source_evidence_bytes = evidence_path.read_bytes()
    capture_receipt_bytes = capture_receipt_path.read_bytes()
    capture_receipt = json.loads(capture_receipt_bytes)
    evidence_bytes = canonical_json_bytes(_replace_vocabulary(
        json.loads(source_evidence_bytes)))
    pack = EvidencePack.model_validate(json.loads(evidence_bytes))
    profile = json.loads(
        (ROOT / "clients" / slug / "profile.json").read_text(encoding="utf-8"))
    classification_as_of = _aware(classification_as_of)
    captured_at = _aware(captured_at)
    receipt_capture = _aware(str(
        capture_receipt.get("captured_at")
        or capture_receipt.get("generated_at") or ""))
    if captured_at != receipt_capture:
        raise ValueError(
            "fixture capture instant does not match its preserved receipt")
    pressed_at = _aware(str(pack.generated_at))
    if classification_as_of != pressed_at:
        raise ValueError(
            "fixture classification instant must equal the captured evidence "
            "pack generated_at receipt")
    with tempfile.TemporaryDirectory(prefix="lila-jtg-fixture-") as work:
        build_dir = Path(work)
        _graph_path, rebuilt_graph = build_corrected_pack(
            slug,
            client_name,
            classification_as_of=classification_as_of,
            captured_at=captured_at,
            root=ROOT,
            pressed_pack_path=evidence_path,
            pack_dir=build_dir,
            cache_dir=build_dir / "cache",
        )
    graph = _replace_vocabulary(rebuilt_graph)
    context = graph.get("classification_context") or {}
    if _aware(str(context.get("as_of") or "")) != classification_as_of:
        raise ValueError("rebuilt graph lost the frozen classification instant")
    if _aware(str(graph.get("captured_at") or "")) != captured_at:
        raise ValueError("rebuilt graph lost the frozen capture instant")
    graph["incremental_cache_receipt"] = _semantic_cache_receipt(
        graph.get("incremental_cache_receipt") or {})
    if graph["incremental_cache_receipt"].get("context_hash") != stable_hash(
            context):
        raise ValueError(
            "rebuilt graph cache receipt does not bind its full context")
    dependencies = graph["incremental_cache_receipt"].setdefault(
        "semantic_dependencies", {})
    if _aware(str(dependencies.get("as_of") or "")) != classification_as_of:
        raise ValueError("rebuilt graph cache receipt has the wrong instant")
    if dependencies.get("rule_version") != CLASSIFIER_VERSION:
        raise ValueError("rebuilt graph does not use the current classifier")
    graph = canonicalize_release_value(graph, root=ROOT)
    inputs = capture_inputs(
        profile=profile, slug=slug, pack=pack, evidence_pack_v2=graph)
    inputs = canonicalize_release_value(inputs, root=ROOT)
    inputs["evidence_pack_v2"] = graph
    snapshot = ReleaseSnapshot.create(
        purpose=REPRODUCIBILITY_FIXTURE_PURPOSE,
        client_name=client_name,
        slug=slug,
        business_as_of=classification_as_of[:10],
        classification_as_of=classification_as_of,
        captured_at=captured_at,
        contract_slots=load_external_product_slots(),
        versions={
            "evidence_pack_v2": SCHEMA_VERSION,
            "external_product_projection": EXTERNAL_PRODUCT_PROJECTION_VERSION,
            "external_product_render": EXTERNAL_PRODUCT_RENDER_VERSION,
            "market_map_projection": MARKET_MAP_PROJECTION_VERSION,
            "market_map_skeleton": MARKET_MAP_SKELETON_VERSION,
            "release_compiler": RELEASE_COMPILER_VERSION,
            "fixture_source": "real-jtg-non-champion.v1",
            "fixture_classification_source": "evidence_pack.generated_at",
            "fixture_classifier": CLASSIFIER_VERSION,
            "fixture_source_evidence_sha256": hashlib.sha256(
                source_evidence_bytes).hexdigest(),
            "fixture_capture_clock_source": "captured_graph.generated_at",
            "fixture_capture_receipt_sha256": hashlib.sha256(
                capture_receipt_bytes).hexdigest(),
            "fixture_projection_vocabulary_normalization": "contract-L7.v1",
        },
        authorization={
            "authorized": False,
            "fixture_status": "not approved and not promotable",
            "problems": [
                "This frozen JTG input proves replay only, not product quality"],
        },
        profile=profile,
        graph=graph,
        market_map_inputs=inputs,
        render_assets={
            "css": load_css(),
            "runtime": load_runtime(),
            "client_mark": client_logo(client_name),
            "gtm_mark": gtm_logo(),
        },
        root=ROOT,
        evidence_pack_bytes=evidence_bytes,
        source_evidence_pack_bytes=source_evidence_bytes,
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(
        output_path, canonical_json_bytes(snapshot.to_dict()).decode("utf-8"))
    return snapshot


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--client", required=True)
    parser.add_argument("--slug", required=True)
    parser.add_argument("--evidence-pack", type=Path, required=True)
    parser.add_argument("--capture-receipt", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--classification-as-of", required=True,
        help="explicit historical timezone-aware classification instant")
    parser.add_argument(
        "--captured-at", required=True,
        help="explicit historical timezone-aware graph capture instant")
    args = parser.parse_args()
    snapshot = build_fixture(
        client_name=args.client,
        slug=args.slug,
        evidence_path=args.evidence_pack,
        capture_receipt_path=args.capture_receipt,
        output_path=args.output,
        classification_as_of=args.classification_as_of,
        captured_at=args.captured_at,
    )
    print(json.dumps({
        "purpose": snapshot.purpose,
        "classification_as_of": snapshot.classification_as_of,
        "captured_at": snapshot.captured_at,
        "output": str(args.output),
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
