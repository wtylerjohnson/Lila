#!/usr/bin/env python3
"""Offline v3 native-discovery packets and preliminary AI review counts. No network."""
import argparse
import hashlib
import json
import random
import re
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qsl, unquote, urlparse

SYSTEMS = ("LILA", "GovTribe")
PROVENANCE = {"retrieved_new_run", "known_independently_retrieved",
              "injected_or_retained_only", "unresolved"}
RETRIEVED = {"retrieved_new_run", "known_independently_retrieved"}
FIELDS = ("grade", "in_scope", "current_buying_motion", "evidence_supported")
BRANDING = re.compile(r"(?<![A-Za-z])(?:LILA|GovTribe)(?![A-Za-z])", re.IGNORECASE)
RUBRIC = """# Native discovery relevance review — v3

Judge the buyer requirement against the supplied company brief, as of the packet
cutoff. Grade 3: a specific current requirement closely matches an evidenced
offering and has a credible transaction path. Grade 2: a current buying motion
has evidenced fit and a plausible direct or partner route, with a specific
unresolved qualification question. Grade 1: adjacent or weak fit, speculative
route, incidental vocabulary, or insufficient buying evidence. Grade 0: outside
scope, contradicted fit, or no credible relationship to the offering.

Relevant requires grade >=2 and affirmative in_scope, current_buying_motion and
evidence_supported. Expired notices need independent current buying evidence;
an old notice, award alone, forecast alone or contact does not establish an
available opportunity. Honest qualification questions do not disqualify a
credible investigation. Unknown evidence or eligibility remains unknown, never
an automatic rejection. If unable to judge, use review_status="unknown", grade=null,
and "unknown" for unknown eligibility fields. Complete every card with rationale
and evidence_refs matching its evidence_id values; an evidence-free card must
remain unknown with an empty evidence_refs list. Do not invent evidence.

Work independently. Do not open product captures, private mapping, native ranks,
other reviewers' judgments or comparison results. Declare any contamination.
This is an AI review, not independent human domain validation. Disagreements are
reported separately; no adjudication is inferred. The scorer withholds novelty.
"""


def require(condition, message):
    if not condition:
        raise ValueError(message)


def read(path):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            require(key not in result, f"Duplicate JSON key: {key}")
            result[key] = value
        return result
    return json.loads(Path(path).read_text(encoding="utf-8"), object_pairs_hook=unique,
                      parse_constant=lambda x: (_ for _ in ()).throw(ValueError(f"Invalid JSON number: {x}")))


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write(path, value):
    with Path(path).open("x", encoding="utf-8") as stream:
        json.dump(value, stream, indent=2, allow_nan=False)
        stream.write("\n")


def nonempty(value, label):
    require(isinstance(value, str) and bool(value.strip()), f"Missing {label}")
    return value


def stamp(value):
    nonempty(value, "timestamp")
    date = datetime.fromisoformat(value.replace("Z", "+00:00"))
    require(date.tzinfo is not None, "Timestamp needs timezone")
    return date


def bound_file(base, name, sha):
    path = (Path(base) / nonempty(name, "file path")).resolve()
    require(path.is_relative_to(Path(base).resolve()), "Evidence path escapes case folder")
    require(path.is_file(), f"Missing input: {name}")
    require(isinstance(sha, str) and re.fullmatch(r"[a-f0-9]{64}", sha), f"Invalid SHA256: {name}")
    require(digest(path) == sha, f"Changed input: {name}")
    return path


def neutral(text, label):
    nonempty(text, label)
    require(not BRANDING.search(text), f"Product branding in neutral {label}")
    return text


def validate_inputs(lock_path, capture_path, pool_path):
    """Check sealed source inputs; never freeze, query, or normalize native rankings."""
    lock_path, capture_path, pool_path = map(lambda p: Path(p).resolve(),
                                           (lock_path, capture_path, pool_path))
    lock, capture, pool = read(lock_path), read(capture_path), read(pool_path)
    require(lock["status"] == "LOCKED_BEFORE_CAPTURE" and lock["root_freeze_authorized"] is True,
            "An authorized final pre-capture lock is required")
    require(lock["mode"] == "same_brief_native_discovery_relevance", "Wrong benchmark mode")
    nonempty(lock["verified_revision"], "verified native revision")
    timing = lock["timing"]
    start, end, cutoff = [stamp(timing[k]) for k in
                          ("window_start_utc", "window_end_utc", "evidence_cutoff_utc")]
    require(stamp(lock["frozen_at_utc"]) <= start < end and (end-start).total_seconds() <= 86400,
            "Invalid frozen capture window")
    require(timing["minutes_per_product"] == 60 and timing["operator_order"] == ["lila", "govtribe"],
            "v3 discovery requires the declared LILA-first 60-minute budgets")
    rule = lock["capture"]
    require(rule["limit"] == 100 and rule["grade_slots"] == 20 and
            all(rule[k] is False for k in ("backfill", "reranking", "replacement_rerun")),
            "v3 requires fixed first20 of first100, no replacement or backfill")
    source_hashes = {}
    for name, item in lock["inputs"].items():
        # Frozen inputs may be inherited from a preserved earlier case.
        path = Path(item["path"]).resolve()
        require(path.is_file() and digest(path) == item["sha256"], f"Changed frozen input: {name}")
        source_hashes[str(path)] = item["sha256"]
    brief = Path(lock["inputs"]["company_brief.md"]["path"]).read_text(encoding="utf-8")
    neutral(brief, "company brief")
    require(capture["lock_sha256"] == digest(lock_path), "Capture does not bind the exact lock")
    require(pool["capture_sha256"] == digest(capture_path), "Pool does not bind the exact captures")
    require(pool["blind_content_reviewed"] is True, "Custodian must review neutral evidence for leakage")
    require(type(capture["synthetic"]) is bool and pool["synthetic"] is capture["synthetic"],
            "Declare matching synthetic/live input provenance")
    custodian = nonempty(pool["custodian"], "custodian")
    base = capture_path.parent
    require(pool_path.parent == base, "Capture and pool must share one case folder")
    runs = capture["runs"]
    require(isinstance(runs, list) and len(runs) == 2 and [r["system"] for r in runs] == list(SYSTEMS),
            "Exactly one sealed run per product in declared order")
    selected, operators, needed = {}, set(), set()
    previous_end = start
    for run in runs:
        system = run["system"]
        require(run["sealed"] is True and run["capture_complete"] is True,
                f"{system}: preserve incomplete attempt; cannot score truncated capture")
        require(all(run[k] is False for k in ("manual_reranking", "backfill", "replacement_rerun",
                                              "other_product_results_seen")), f"{system}: invalid capture behavior")
        a, b = stamp(run["started_at_utc"]), stamp(run["finished_at_utc"])
        require(previous_end <= a < b <= end and (b-a).total_seconds() <= 3600,
                f"{system}: invalid capture time, order or budget")
        previous_end = b
        operators.add(nonempty(run["operator"], "operator"))
        for field in ("native_surface", "native_order", "product_version", "source_freshness", "limitations"):
            nonempty(run[field], f"{system} {field}")
        require(isinstance(run["files"], dict) and run["files"], f"{system}: missing receipt manifest")
        for name, sha in run["files"].items():
            path = bound_file(base, name, sha)
            source_hashes[str(path)] = sha
        require(all(run[k] in run["files"] for k in ("raw_capture_file", "settings_capture_file")),
                f"{system}: bind raw and UI/settings receipts")
        rows = run["results"]
        total = run["returned_total"]
        require(type(total) is int and total >= 0, f"{system}: invalid returned total")
        require(isinstance(rows, list) and len(rows) == min(total, 100), f"{system}: truncated native slots")
        require([r["rank"] for r in rows] == list(range(1, len(rows)+1)) and
                all(type(r["rank"]) is int for r in rows), f"{system}: changed or invalid native ranks")
        for row in rows:
            require(row["provenance"] in PROVENANCE, "Missing or invalid retrieval provenance")
            nonempty(row["provenance_evidence"], "provenance evidence or explicit unresolved gap")
            nonempty(row["card_key"], "neutral custodian card identity")
            # Native IDs can be unavailable; duplicates must remain in their occupied slots.
            require(row["native_id"] is None or isinstance(row["native_id"], str), "Invalid native ID")
            if not row["native_id"]:
                nonempty(row["missing_id_reason"], "native ID gap")
        selected[system] = rows[:20]
        needed.update(r["card_key"] for r in rows[:20])
    require(len(operators) == 2, "Two distinct product operators are required")
    require(custodian not in operators, "Custodian and product operators must be separate")
    cards = pool["cards"]
    require(isinstance(cards, list) and len(cards) == len(needed) and
            {c["card_key"] for c in cards} == needed, "Pool must exactly cover the top20 union without duplicates")
    seen_families = set()
    for card in cards:
        family = card["family_id"]
        require(family is None or isinstance(family, str) and family.strip(), "Invalid family identity")
        if family is not None:
            require(family not in seen_families, "Duplicate family cards; normalize with evidence before packet")
            seen_families.add(family)
        nonempty(card["identity_evidence"], "family identity evidence or unresolved identity gap")
        neutral(card["title"], "title")
        neutral(card["agency"], "agency")
        require(isinstance(card["evidence_gaps"], list), "Explicit evidence gap list required")
        for gap in card["evidence_gaps"]:
            neutral(gap, "evidence gap")
        evidence = card["evidence"]
        require(isinstance(evidence, list) and (evidence or card["evidence_gaps"]), "Need evidence or an explicit gap")
        require(len({e["evidence_id"] for e in evidence}) == len(evidence), "Duplicate evidence locator")
        for ev in evidence:
            nonempty(ev["evidence_id"], "evidence ID")
            neutral(ev["locator"], "evidence locator")
            neutral(ev["passage"], "evidence passage")
            url = urlparse(ev["source_url"])
            require(url.scheme == "https" and (url.hostname or "").lower().endswith((".gov", ".mil")),
                    "Only original government evidence in neutral cards")
            neutral(unquote(ev["source_url"]), "source URL")
            tracking = {"rank", "product", "provider", "origin", "referrer", "ref", "source"}
            require(not url.username and not url.password and
                    not any(k.lower().startswith("utm_") or k.lower() in tracking
                            for k, _ in parse_qsl(url.query) + parse_qsl(url.fragment)),
                    "Product tracking or ranking metadata in source URL")
            published = ev["published_at_utc"]
            if published is not None:
                require(stamp(published) <= cutoff, "Post-cutoff evidence cannot establish relevance")
            else:
                require(card["evidence_gaps"], "Unknown publication time needs explicit gap")
            path = bound_file(base, ev["snapshot_file"], ev["snapshot_sha256"])
            content = path.read_text(encoding="utf-8")
            neutral(content, "source text snapshot")
            require(" ".join(ev["passage"].split()) in " ".join(content.split()), "Passage absent from bound snapshot")
            source_hashes[str(path)] = ev["snapshot_sha256"]
    return lock, capture, pool, selected, source_hashes, brief, operators


def prepare(lock_path, capture_path, pool_path, out_dir, reviewer_one, reviewer_two):
    lock, capture, pool, selected, hashes, brief, operators = validate_inputs(lock_path, capture_path, pool_path)
    reviewers = (nonempty(reviewer_one, "reviewer one"), nonempty(reviewer_two, "reviewer two"))
    require(reviewers[0] != reviewers[1] and not (set(reviewers) & (operators | {pool["custodian"]})),
            "Two distinct reviewers must be independent of operators/custodian")
    out = Path(out_dir).resolve()
    require(not out.exists(), "Never overwrite an earlier packet; choose a new output folder")
    out.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=".discovery-packet-", dir=out.parent))
    try:
        cards = list(pool["cards"])
        random.SystemRandom().shuffle(cards)
        ids = {f"C{i+1:03}": card["card_key"] for i, card in enumerate(cards)}
        neutral_cards, copies, evidence_ids = [], {}, {}
        for bid, card in zip(ids, cards):
            evidence = []
            evidence_ids[bid] = {}
            for i, ev in enumerate(card["evidence"], 1):
                name = f"sources/{bid}-E{i:03}.txt"
                copies[name] = (Path(capture_path).resolve().parent / ev["snapshot_file"]).read_bytes()
                evidence_id = f"{bid}-E{i:03}"
                evidence_ids[bid][evidence_id] = ev["evidence_id"]
                evidence.append({k: ev[k] for k in ("source_url", "published_at_utc", "passage", "locator")}
                                | {"evidence_id": evidence_id, "snapshot_file": name,
                                   "snapshot_sha256": ev["snapshot_sha256"]})
            neutral_cards.append({"blind_id": bid, "title": card["title"], "agency": card["agency"],
                                  "evidence": evidence, "evidence_gaps": card["evidence_gaps"]})
        bundle = {"schema": "discovery-review-packet.v3", "synthetic": capture["synthetic"],
                  "evidence_cutoff_utc": lock["timing"]["evidence_cutoff_utc"], "cards": neutral_cards}
        review_hashes = {}
        for number, reviewer in enumerate(reviewers, 1):
            dest = stage / f"reviewer_{number}"
            (dest / "sources").mkdir(parents=True)
            (dest / "company_brief.md").write_text(brief, encoding="utf-8")
            (dest / "rubric.md").write_text(RUBRIC, encoding="utf-8")
            for name, content in copies.items():
                (dest / name).write_bytes(content)
            write(dest / "cards.json", bundle)
            manifest = {str(p.relative_to(dest)): digest(p) for p in sorted(dest.rglob("*")) if p.is_file()}
            write(dest / "packet_manifest.json", {"schema": "discovery-review-manifest.v3", "files": manifest})
            packet_sha = digest(dest / "packet_manifest.json")
            review_hashes[str(number)] = packet_sha
            write(dest / "review.json", {"reviewer_id": reviewer, "reviewer_type": "AI", "model": "",
                  "packet_sha256": packet_sha, "synthetic": capture["synthetic"], "submitted": False,
                  "independent": None, "origin_or_rank_seen": None, "other_review_seen": None,
                  "judgments": [{"blind_id": bid, "review_status": None, "grade": None,
                                 "in_scope": None, "current_buying_motion": None, "evidence_supported": None,
                                 "rationale": "", "evidence_refs": []} for bid in ids]})
        bound_inputs = {str(Path(p).resolve()): digest(p) for p in (lock_path, capture_path, pool_path, __file__)}
        bound_inputs.update(hashes)
        key = {"schema": "discovery-private-key.v3", "case_id": lock["case_id"], "synthetic": capture["synthetic"],
               "input_hashes": bound_inputs, "ids": ids, "original_evidence_ids": evidence_ids, "selected_slots": selected,
               "families": {c["card_key"]: c["family_id"] for c in cards}, "reviewers": list(reviewers),
               "packet_hashes": review_hashes, "prepared_at_utc": datetime.now(timezone.utc).isoformat()}
        write(stage / "PRIVATE_key.json", key)
        write(stage / "PRIVATE_seal.json", {"private_key_sha256": digest(stage / "PRIVATE_key.json"),
                                          "input_hashes": bound_inputs, "packet_hashes": review_hashes})
        stage.rename(out)
    except BaseException:
        shutil.rmtree(stage)
        raise
    return out


def read_decisions(root, number, key):
    folder = root / f"reviewer_{number}"
    require(digest(folder / "packet_manifest.json") == key["packet_hashes"][str(number)], "Changed packet manifest")
    manifest = read(folder / "packet_manifest.json")
    for name, sha in manifest["files"].items():
        bound_file(folder, name, sha)
    packet = read(folder / "cards.json")
    cards = {r["blind_id"]: r for r in packet["cards"]}
    review = read(folder / "review.json")
    require(review["packet_sha256"] == key["packet_hashes"][str(number)], "Decisions bind a different packet")
    require(review["reviewer_id"] == key["reviewers"][number-1] and review["reviewer_type"] == "AI",
            "Reviewer identity/type mismatch")
    nonempty(review["model"], "actual reviewer model or explicit unknown model provenance")
    require(review["submitted"] is True and review["independent"] is True and
            review["origin_or_rank_seen"] is False and review["other_review_seen"] is False,
            "Unsubmitted, non-independent or contaminated review")
    require(review["synthetic"] is key["synthetic"], "Reviewer synthetic/live provenance mismatch")
    rows = review["judgments"]
    require(isinstance(rows, list) and len(rows) == len(cards) and {r["blind_id"] for r in rows} == set(cards),
            "Missing or duplicate judgments")
    for row in rows:
        require(row["review_status"] in ("judged", "unknown"), "Finalize every judgment, including unknowns")
        require((row["grade"] is None and row["review_status"] == "unknown") or
                (row["review_status"] == "judged" and type(row["grade"]) is int and 0 <= row["grade"] <= 3),
                "Invalid grade or unknown status")
        require(all(type(row[f]) is bool or row[f] == "unknown" for f in FIELDS[1:]), "Invalid eligibility state")
        nonempty(row["rationale"], "judgment rationale")
        refs = row["evidence_refs"]
        evidence = {e["evidence_id"]: e for e in cards[row["blind_id"]]["evidence"]}
        require(isinstance(refs, list) and all(isinstance(r, str) for r in refs) and
                len(refs) == len(set(refs)) and set(refs) <= set(evidence), "Unknown or duplicate evidence reference")
        require(bool(refs) or row["review_status"] == "unknown", "Judged card requires evidence references")
        if classification(row) == "relevant":
            require(any(evidence[r]["published_at_utc"] is not None for r in refs),
                    "Relevant decision needs dated, cutoff-eligible source evidence")
    return {r["blind_id"]: r for r in rows}, {k: review[k] for k in
           ("reviewer_id", "reviewer_type", "model", "packet_sha256", "independent")}


def classification(row):
    if row["review_status"] == "unknown" or any(row[f] == "unknown" for f in FIELDS[1:]):
        return "unknown"
    return "relevant" if row["grade"] >= 2 and all(row[f] is True for f in FIELDS[1:]) else "not_relevant"


def metrics(key, judgments):
    by_card = {key["ids"][bid]: classification(row) for bid, row in judgments.items()}
    result = {}
    for system, rows in key["selected_slots"].items():
        families = [key["families"][r["card_key"]] for r in rows]
        eligible = [r for r in rows if r["provenance"] in RETRIEVED and
                    key["families"][r["card_key"]] is not None and by_card[r["card_key"]] == "relevant"]
        found = {key["families"][r["card_key"]] for r in eligible}
        known = {key["families"][r["card_key"]] for r in eligible if r["provenance"] == "known_independently_retrieved"}
        resolved = [f for f in families if f is not None]
        result[system] = {"relevant_distinct_top20": len(found), "distinct_relevant_yield_at20": len(found)/20,
            "known_retrieved_relevant_distinct_top20": len(known), "new_to_client_relevant_top20": None,
            "returned_slots_top20": len(rows), "unfilled_slots_top20": 20-len(rows),
            "duplicate_resolved_family_slots_top20": len(resolved)-len(set(resolved)),
            "injected_or_retained_only_slots_top20": sum(r["provenance"] == "injected_or_retained_only" for r in rows),
            "unresolved_provenance_slots_top20": sum(r["provenance"] == "unresolved" for r in rows),
            "unresolved_identity_slots_top20": families.count(None),
            "unknown_relevance_slots_top20": sum(by_card[r["card_key"]] == "unknown" for r in rows),
            "confirmed_non_relevant_slots_top20": sum(by_card[r["card_key"]] == "not_relevant" for r in rows)}
    return result


def score(out_dir, result_path):
    root = Path(out_dir).resolve()
    key, seal = read(root / "PRIVATE_key.json"), read(root / "PRIVATE_seal.json")
    require(digest(root / "PRIVATE_key.json") == seal["private_key_sha256"], "Changed private mapping")
    require(key["input_hashes"] == seal["input_hashes"] and key["packet_hashes"] == seal["packet_hashes"], "Broken seal")
    for path, sha in key["input_hashes"].items():
        require(Path(path).is_file() and digest(path) == sha, f"Changed or missing input: {path}")
    one, meta_one = read_decisions(root, 1, key)
    two, meta_two = read_decisions(root, 2, key)
    disagreements = [bid for bid in key["ids"] if any(one[bid][f] != two[bid][f]
                     for f in ("review_status",) + FIELDS)]
    agreed = {bid: one[bid] if bid not in disagreements else {"review_status": "unknown", "grade": None,
              "in_scope": "unknown", "current_buying_motion": "unknown", "evidence_supported": "unknown"}
              for bid in key["ids"]}
    unknown = [bid for bid in key["ids"] if classification(one[bid]) == "unknown" or classification(two[bid]) == "unknown"]
    unresolved_slots = any(r["provenance"] == "unresolved" or key["families"][r["card_key"]] is None
                           for rows in key["selected_slots"].values() for r in rows)
    complete = not (disagreements or unknown or unresolved_slots)
    result = {"schema": "discovery-result.v3", "case_id": key["case_id"], "synthetic": key["synthetic"],
        "status": "PRELIMINARY_AI_REVIEW_COMPLETE" if complete else "PRELIMINARY_AI_REVIEW_WITH_UNRESOLVED_ITEMS",
        "primary_metric": "Distinct evidence-supported relevant retrieved families in fixed first20 native discovery slots",
        "reviewer_counts": {"reviewer_1": metrics(key, one), "reviewer_2": metrics(key, two)},
        "agreed_counts_only": metrics(key, agreed),
        "disagreements": [{"blind_id": bid, "reviewer_1": one[bid], "reviewer_2": two[bid]} for bid in disagreements],
        "unknown_review_card_ids": unknown, "initial_complete_decision_agreement":
            (len(one)-len(disagreements))/len(one) if one else None,
        "adjudication": "NOT_PERFORMED", "overall_winner": None, "novelty_metric": "WITHHELD_PARTIAL_BASELINE",
        "reviewers": [meta_one, meta_two], "marketing_status": "NOT_AUTHORIZED_BY_THIS_SCORE",
        "evidence_hashes": {**key["input_hashes"], str(root/"PRIVATE_key.json"): digest(root/"PRIVATE_key.json"),
                           str(root/"PRIVATE_seal.json"): digest(root/"PRIVATE_seal.json"),
                           **{str(root/f"reviewer_{i}"/"review.json"): digest(root/f"reviewer_{i}"/"review.json") for i in (1, 2)}},
        "limitations": ["Same-brief native discovery only; different sources, semantics, ranks and freshness are disclosed in capture receipts.",
            "Fixed denominator20; duplicate, injected and unresolved slots are not backfilled.",
            "Zero confirmed relevant credit is not evidence that unresolved slots are irrelevant.",
            "Disagreements and unknowns are not adjudicated; agreed counts are a conservative subtotal.",
            "Two separate AI reviews rely on workflow attestations, not OS-enforced blindness or human validation.",
            "Local hashes detect changed inputs, not malicious replacement of every receipt.",
            "No total-market recall, seller-release readiness, new-to-client or superiority claim."]}
    write(result_path, result)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    blind = commands.add_parser("prepare", help="Build two separate neutral reviewer bundles and private custody files")
    for name in ("lock", "capture", "pool", "output"):
        blind.add_argument(name)
    blind.add_argument("--reviewer-one", required=True)
    blind.add_argument("--reviewer-two", required=True)
    score_cmd = commands.add_parser("score", help="Validate two locked reviews and report counts plus unresolved differences")
    score_cmd.add_argument("packet_dir")
    score_cmd.add_argument("result")
    args = parser.parse_args()
    try:
        if args.command == "prepare":
            prepare(args.lock, args.capture, args.pool, args.output, args.reviewer_one, args.reviewer_two)
        else:
            score(args.packet_dir, args.result)
    except (ValueError, KeyError, TypeError, OSError) as exc:
        parser.exit(2, f"Validation failed: {exc}\n")


if __name__ == "__main__":
    main()
