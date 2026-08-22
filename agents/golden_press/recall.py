"""Golden-dock recall scoring (GOLDEN_BUILD Phase 2).

INTEGRITY RULE: this module runs POST-RETRIEVAL ONLY. The golden reference's
record ids are a benchmark, never an input: nothing here is importable by
retrieval, and no id parsed here may ever reach a query or fetch. Retrieval
builds its queries exclusively from strategy entities and capability terms.
"""

from __future__ import annotations

import re
from typing import Iterable

from agents.golden_press.records import GoldenRecord
from agents.golden_press.retrieval import normalize_record_id

_AWARD_GID_RE = re.compile(r"usaspending\.gov/award/([A-Z0-9_\-]+)", re.I)
_APFS_RE = re.compile(r"apfs-cloud\.dhs\.gov/(?:record|forecast)/(\d+)", re.I)
_SAM_RE = re.compile(r"sam\.gov/(?:opp|workspace)[^\"'<> ]*", re.I)


def golden_ids(html_text: str) -> dict:
    """Benchmark identities from the golden reference's evidence links.

    USASpending awards: the PIID segment of CONT_AWD_<piid>_<agency>_... plus
    the full generated id. APFS forecasts: the numeric record id.
    """
    piids: set[str] = set()
    gids: set[str] = set()
    for gid in _AWARD_GID_RE.findall(html_text):
        gid = gid.rstrip("/").upper()
        gids.add(gid)
        parts = gid.split("_")
        if len(parts) >= 3 and parts[0] == "CONT":
            piids.add(normalize_record_id(parts[2]))
    apfs = {int(m) for m in _APFS_RE.findall(html_text)}
    return {
        "award_piids": piids,
        "award_gids": gids,
        "apfs_ids": apfs,
        "sam_present": bool(_SAM_RE.search(html_text)),
        "total": len(gids) + len(apfs),
    }


def _apfs_numeric(record: GoldenRecord) -> int | None:
    for source in (record.url or "", record.record_id or ""):
        match = _APFS_RE.search(str(source))
        if match:
            return int(match.group(1))
    digits = re.sub(r"[^0-9]", "", str(record.record_id or ""))
    return int(digits) if digits else None


def score_recall(records: Iterable[GoldenRecord], golden_html: str) -> dict:
    """Which golden dock identities the retrieved records reproduce."""
    bench = golden_ids(golden_html)
    have_piids = {normalize_record_id(r.record_id) for r in records}
    have_gids = {str(r.generated_internal_id or "").upper() for r in records}
    have_apfs = {n for n in (_apfs_numeric(r) for r in records
                             if r.lane == "L4_forecast") if n is not None}

    matched: list[str] = []
    missed: list[str] = []
    for gid in sorted(bench["award_gids"]):
        piid = gid.split("_")[2] if len(gid.split("_")) >= 3 else gid
        if gid in have_gids or normalize_record_id(piid) in have_piids:
            matched.append(gid)
        else:
            missed.append(gid)
    for apfs_id in sorted(bench["apfs_ids"]):
        label = f"APFS:{apfs_id}"
        if apfs_id in have_apfs:
            matched.append(label)
        else:
            missed.append(label)
    return {
        "golden_total": bench["total"],
        "matched_count": len(matched),
        "matched": matched,
        "missed": missed,
        "non_forecast_total": len(bench["award_gids"]),
        "non_forecast_matched": sum(1 for m in matched if not m.startswith("APFS:")),
    }


# ---- operator golden targets (2026-08-04) ---------------------------------- #
# A hand-marked ideal per client: data/reference/golden_targets/<slug>.json.
# The SAME integrity rule applies: target ids are benchmarks, never inputs.
# Nothing here is importable by retrieval, and no id parsed here may reach a
# query. The operator marks the file; every press scores itself against it.

GOLDEN_TARGETS_DIR = "data/reference/golden_targets"


def load_golden_targets(root, slug: str) -> dict | None:
    """The operator's marked target file, or None when unmarked."""
    import json
    from pathlib import Path

    path = Path(root) / GOLDEN_TARGETS_DIR / f"{slug}.json"
    if not path.exists():
        return None
    payload = json.loads(path.read_text(encoding="utf-8"))
    targets = payload.get("targets")
    if not isinstance(targets, list) or not targets:
        return None
    return payload


def seed_targets_skeleton(golden_html: str, client_name: str) -> dict:
    """A markable skeleton from an existing golden document's evidence ids.

    Every row starts must_lead=false with an empty why; the operator
    deletes rows that never belonged, marks the leaders, and adds what the
    machine missed. Seeding never overwrites an existing file.
    """
    bench = golden_ids(golden_html)
    rows = [{"id": gid, "kind": "award_gid", "must_lead": False,
             "title_hint": "", "why": ""}
            for gid in sorted(bench["award_gids"])]
    rows += [{"id": f"APFS:{apfs}", "kind": "apfs", "must_lead": False,
              "title_hint": "", "why": ""}
             for apfs in sorted(bench["apfs_ids"])]
    return {
        "client": client_name,
        "marked_by": "",
        "marked_at": "",
        "note": ("Hand-marked ideal opportunity set. Ids are benchmarks, "
                 "never retrieval inputs. must_lead means the record must "
                 "hold a core seat, not merely appear in the dock."),
        "targets": rows,
    }


def _target_matches(target: dict, have_piids: set, have_gids: set,
                    have_apfs: set, have_ids: set) -> bool:
    kind = str(target.get("kind") or "").strip()
    raw = str(target.get("id") or "").strip()
    if not raw:
        return False
    if kind == "apfs" or raw.upper().startswith("APFS:"):
        digits = re.sub(r"[^0-9]", "", raw)
        return bool(digits) and int(digits) in have_apfs
    if kind == "award_gid" or raw.upper().startswith("CONT_"):
        gid = raw.rstrip("/").upper()
        parts = gid.split("_")
        piid = parts[2] if len(parts) >= 3 else gid
        return gid in have_gids or normalize_record_id(piid) in have_piids
    return normalize_record_id(raw) in have_ids


def score_against_targets(records: Iterable[GoldenRecord],
                          seated_core_ids: set,
                          payload: dict) -> dict:
    """Presence and seating recall against the operator's marked ideal."""
    records = list(records)
    have_piids = {normalize_record_id(r.record_id) for r in records}
    have_ids = set(have_piids)
    have_gids = {str(r.generated_internal_id or "").upper() for r in records}
    have_apfs = {n for n in (_apfs_numeric(r) for r in records)
                 if n is not None}
    seated = {normalize_record_id(rid) for rid in seated_core_ids}

    present, missing, lead_ok, lead_missing = [], [], [], []
    for target in payload.get("targets") or []:
        label = str(target.get("id") or "")
        hit = _target_matches(target, have_piids, have_gids, have_apfs,
                              have_ids)
        (present if hit else missing).append(label)
        if target.get("must_lead"):
            gid = label.rstrip("/").upper()
            parts = gid.split("_")
            piid = normalize_record_id(
                parts[2] if len(parts) >= 3 else label)
            if hit and piid in seated:
                lead_ok.append(label)
            else:
                lead_missing.append(label)
    return {
        "targets_total": len(present) + len(missing),
        "present_count": len(present),
        "present": present,
        "missing": missing,
        "lead_total": len(lead_ok) + len(lead_missing),
        "lead_count": len(lead_ok),
        "lead_missing": lead_missing,
        "marked_by": payload.get("marked_by") or "",
        "marked_at": payload.get("marked_at") or "",
    }


def main(argv=None) -> int:
    """Seed a markable golden-target skeleton (never overwrites).

    python -m agents.golden_press.recall --seed-targets <slug>
    """
    import argparse
    import json
    import sys
    from pathlib import Path

    parser = argparse.ArgumentParser(description=main.__doc__)
    parser.add_argument("--seed-targets", required=True, metavar="SLUG")
    args = parser.parse_args(argv)
    root = Path(__file__).resolve().parents[2]
    out = root / GOLDEN_TARGETS_DIR / f"{args.seed_targets}.json"
    if out.exists():
        print(f"[golden-targets] {out} already exists; the operator's "
              "marks are never overwritten", file=sys.stderr)
        return 2
    from agents.golden_press.press import GOLDEN_DESIGN_SOURCE
    golden_html = (root / GOLDEN_DESIGN_SOURCE).read_text(encoding="utf-8")
    payload = seed_targets_skeleton(golden_html, args.seed_targets)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=1, ensure_ascii=False) + "\n",
                   encoding="utf-8")
    print(f"[golden-targets] seeded {len(payload['targets'])} markable "
          f"rows -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
