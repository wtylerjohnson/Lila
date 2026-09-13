#!/usr/bin/env python3
"""V4 adds native consumed-source validation to the unchanged v3 relevance rules."""
import argparse
import importlib.util
import json
import tempfile
from pathlib import Path

import native_source_policy as sources

V3_PATH = Path(__file__).resolve().parents[1] / "v3/discovery_score.py"
spec = importlib.util.spec_from_file_location("discovery_v3", V3_PATH)
v3 = importlib.util.module_from_spec(spec)
spec.loader.exec_module(v3)


def check(lock_path, capture_path, *, lila_only=False):
    lock, capture = v3.read(lock_path), v3.read(capture_path)
    v3.require(capture["lock_sha256"] == v3.digest(lock_path), "Capture lock hash mismatch")
    bindings = {str(Path(x["path"]).resolve()): x["sha256"] for x in lock["inputs"].values()}
    for path in (Path(__file__).resolve(), Path(sources.__file__).resolve(), V3_PATH):
        v3.require(bindings.get(str(path)) == v3.digest(path), "Freeze the exact v4 and v3 harness files")
    return sources.validate(lock, capture, Path(capture_path).resolve().parent, lila_only=lila_only)


def prepare(lock_path, capture_path, pool_path, output, one, two):
    result = check(lock_path, capture_path)
    out = v3.prepare(lock_path, capture_path, pool_path, output, one, two)
    v3.write(out / "SOURCE_POLICY_INPUTS.json", {
        "lock": str(Path(lock_path).resolve()), "capture": str(Path(capture_path).resolve()),
        "lock_sha256": v3.digest(lock_path), "capture_sha256": v3.digest(capture_path),
        "source_verification": result})
    return out


def score(packet_dir, output):
    root = Path(packet_dir).resolve()
    record = v3.read(root / "SOURCE_POLICY_INPUTS.json")
    key = v3.read(root / "PRIVATE_key.json")
    for field in ("lock", "capture"):
        path = record[field]
        v3.require(key["input_hashes"].get(path) == record[field+"_sha256"] == v3.digest(path),
                   "Source validation must bind this packet's original inputs")
    source_result = check(record["lock"], record["capture"])
    with tempfile.TemporaryDirectory(prefix="source-checked-score-") as temporary:
        result = v3.score(root, Path(temporary) / "v3.json")
    result["schema"] = "discovery-result.v4"
    result["source_validation"] = source_result
    v3.write(output, result)
    return result


def main():
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest="command", required=True)
    single = sub.add_parser("check-lila", help="Validate one sealed LILA capture before GovTribe dispatch")
    for name in ("lock", "capture", "output"):
        single.add_argument(name)
    prep = sub.add_parser("prepare")
    for name in ("lock", "capture", "pool", "output"):
        prep.add_argument(name)
    prep.add_argument("--reviewer-one", required=True)
    prep.add_argument("--reviewer-two", required=True)
    scorer = sub.add_parser("score")
    scorer.add_argument("packet_dir")
    scorer.add_argument("output")
    args = p.parse_args()
    try:
        if args.command == "check-lila":
            result = check(args.lock, args.capture, lila_only=True)
            v3.write(args.output, {"lock_sha256": v3.digest(args.lock),
                                  "capture_sha256": v3.digest(args.capture), **result})
        elif args.command == "prepare":
            prepare(args.lock, args.capture, args.pool, args.output, args.reviewer_one, args.reviewer_two)
        else:
            score(args.packet_dir, args.output)
    except (ValueError, KeyError, TypeError, OSError) as exc:
        p.exit(2, "Validation failed: " + str(exc) + "\n")


if __name__ == "__main__":
    main()
