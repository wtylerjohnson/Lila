"""Offline verification of native daily-refresh consumption for new cases only."""
import hashlib
import json
from datetime import date, datetime
from pathlib import Path


POLICY = {
    "schema": "lila-benchmark.native-source-policy.v1",
    "mode": "native_current_day_refresh",
    "cache_date_is_publication_date": False,
    "consumed_extract_receipts_required": True,
    "original_extract_copies_required": True,
    "scoring_evidence_cutoff": "frozen_window_end",
    "govtribe_index_freshness": "unknown_disclosed",
    "source_parity_claim": False,
    "snapshot_override": False,
}


def require(ok, reason):
    if not ok:
        raise ValueError(reason)


def sha(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def read(path):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            require(key not in result, "Duplicate JSON field")
            result[key] = value
        return result
    return json.loads(Path(path).read_text(), object_pairs_hook=unique)


def stamp(value):
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    require(parsed.tzinfo is not None, "Source timestamps need explicit timezone")
    return parsed


def bound(base, name, manifest):
    require(name in manifest, "Source artifact absent from capture manifest")
    path = (base / name).resolve()
    require(path.is_relative_to(base.resolve()), "Source artifact escapes capture directory")
    require(path.is_file() and sha(path) == manifest[name], "Source artifact hash mismatch")
    return path


def validate(lock, capture, base, *, lila_only=False):
    """Validate sealed source receipts; never query, select, rerank or refreeze."""
    base = Path(base)
    require(lock.get("source_policy") == POLICY, "Missing or unsupported native refresh policy")
    require(lock.get("status") == "LOCKED_BEFORE_CAPTURE" and
            lock.get("root_freeze_authorized") is True, "Final authorized lock required")
    require(lock["timing"]["evidence_cutoff_utc"] == lock["timing"]["window_end_utc"],
            "Refresh policy must preregister evidence cutoff at window end")
    require("local_sam_receipt" not in lock,
            "Old pinned-source field conflicts with refresh; use initial_cache_inventory")
    runs = capture["runs"]
    expected_systems = ["LILA"] if lila_only else ["LILA", "GovTribe"]
    require([r["system"] for r in runs] == expected_systems,
            "One sealed LILA capture required" if lila_only else "One native capture per product required")
    run = runs[0]
    require(run["sealed"] is True and run["capture_complete"] is True,
            "Incomplete capture has no source-policy certification")
    started, finished = stamp(run["started_at_utc"]), stamp(run["finished_at_utc"])
    window_start = stamp(lock["timing"]["window_start_utc"])
    window_end = stamp(lock["timing"]["window_end_utc"])
    require(stamp(lock["frozen_at_utc"]) <= window_start <= started < finished <= window_end and
            0 < (window_end-window_start).total_seconds() <= 86400,
            "LILA source capture outside the frozen observation window")
    require(lock["timing"]["minutes_per_product"] == 60 and
            lock["timing"]["operator_order"] == ["lila", "govtribe"] and
            (finished-started).total_seconds() <= 3600, "Invalid native order or LILA capture budget")
    manifest = run["files"]
    # Bind census to the saved native sweep, not an operator-authored summary.
    sweep_path = bound(base, run["native_sweep_file"], manifest)
    census = read(sweep_path)["results"]["sam_census"]
    require(census.get("source") == "sam_extract" and census.get("complete") is True,
            "Missing complete native extract census; fallback is not this source policy")
    require(not census.get("error_type") and not census.get("error"), "Native primary scan failed")
    primary = census.get("extract_receipts", [])
    require(len(primary) == 1 and primary[0].get("scan") == "primary",
            "Exact primary consumed-source receipt required")
    attachments = census.get("attachment_candidates", {})
    secondary = attachments.get("extract_receipts", [])
    require(not attachments.get("error_type") and not attachments.get("error"),
            "Native attachment scan failed")
    if secondary:
        require(attachments.get("complete") is True, "Native attachment census incomplete")
        require([r.get("scan") for r in secondary] == ["attachment_latest", "attachment_candidates"],
                "Both native attachment scan receipts required")
    else:
        require(attachments.get("scan_skipped_reason") in ("limit_zero", "insufficient_anchors") and
                attachments.get("eligible") == 0 and attachments.get("selected") == 0,
                "Missing attachment receipt or native explicit no-scan reason")
    receipts = primary + secondary
    retained = run["consumed_extract_files"]
    require(isinstance(retained, list), "Retained extract map required")
    pairs = [(x["consumed_path"], x["sha256"]) for x in retained]
    require(len(set(pairs)) == len(pairs), "Duplicate retained extract binding")
    require(set(pairs) == {(r["path"], r["sha256"]) for r in receipts},
            "Retained copies must bind every consumed extract exactly")
    retained_map = {pair: x for pair, x in zip(pairs, retained)}
    verified = []
    for receipt in receipts:
        require(receipt.get("schema_version") == 1 and receipt.get("status") == "verified" and
                receipt.get("complete") is True and receipt.get("integrity") == "stable",
                "Unverified, partial or changing source cannot be scored")
        require(not receipt.get("error_type") and not receipt.get("error"), "Native read failed")
        stats = [receipt.get(k) for k in (
            "file_stat_before", "file_stat_after", "path_stat_before", "path_stat_after")]
        stat_fields = {"device", "inode", "size_bytes", "modified_at_ns", "changed_at_ns"}
        require(all(isinstance(x, dict) and set(x) == stat_fields and
                    all(type(v) is int for v in x.values()) for x in stats) and
                all(x == stats[0] for x in stats[1:]) and
                stats[0]["size_bytes"] == receipt["bytes_read"], "Native file identity changed or is incomplete")
        path = Path(receipt["path"])
        cache_date = date.fromisoformat(receipt["cache_date"])
        require(path.is_absolute() and path.name == receipt["filename"] ==
                "opportunities_" + cache_date.isoformat() + ".csv", "Invalid native cache path/date")
        require(receipt["cache_date_basis"] == "local_cache_filename_not_upstream_publication",
                "Cache label must not masquerade as source publication time")
        local_start = date.fromisoformat(receipt["selection_started_local_date"])
        local_end = date.fromisoformat(receipt["selection_finished_local_date"])
        require(local_start <= cache_date <= local_end and (local_end-local_start).days <= 1,
                "Consumed date outside native date-selection bounds")
        selected, selection_finished, read_started, read_finished = [stamp(receipt[k]) for k in (
            "selection_started_at_utc", "selection_finished_at_utc",
            "read_started_at_utc", "read_finished_at_utc")]
        require(started <= selected <= selection_finished <= read_started <= read_finished <= finished,
                "Source consumption outside this native run")
        copy = retained_map[(receipt["path"], receipt["sha256"])]
        saved = bound(base, copy["snapshot_file"], manifest)
        require(manifest[copy["snapshot_file"]] == receipt["sha256"] and
                type(receipt["bytes_read"]) is int and receipt["bytes_read"] > 0 and
                saved.stat().st_size == receipt["bytes_read"], "Consumed bytes differ from retained copy")
        verified.append({"scan": receipt["scan"], "path": str(path), "cache_date": str(cache_date),
                         "sha256": receipt["sha256"], "bytes_read": receipt["bytes_read"],
                         "snapshot_file": copy["snapshot_file"]})
    if secondary:
        require(stamp(primary[0]["read_finished_at_utc"]) <=
                stamp(secondary[0]["selection_started_at_utc"]) and
                stamp(secondary[0]["read_finished_at_utc"]) <=
                stamp(secondary[1]["read_started_at_utc"]), "Impossible native scan order")
        require(all(secondary[0][key] == secondary[1][key] for key in (
            "selection_started_local_date", "selection_finished_local_date",
            "selection_started_at_utc", "selection_finished_at_utc")),
            "Attachment passes must share the same native selection")
        require(secondary[0]["sha256"] == secondary[1]["sha256"] and
                secondary[0]["path"] == secondary[1]["path"] and
                secondary[0]["file_stat_after"] == secondary[1]["file_stat_before"],
                "Attachment passes consumed different extracts")
    if not lila_only:
        require(runs[1].get("source_freshness") and runs[1].get("limitations"),
                "GovTribe freshness and limitations must be disclosed")
    return {"schema": "lila-benchmark.native-source-validation.v1", "status": "VERIFIED_NATIVE_REFRESH",
            "validation_scope": "LILA consumed sources only; no score or GovTribe capture certification",
            "synthetic": capture["synthetic"], "consumed_scans": verified,
            "sam_publication_time": "Not inferred from cache date; use separate original-source evidence",
            "govtribe_index_freshness": "unknown_disclosed", "matched_source_cutoff": False}
