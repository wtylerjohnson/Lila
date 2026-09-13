"""Explicitly authored synthetic custody/support fixtures. Never a live issuer.

AUDITED_FACTS is an authored test oracle, separate from reviewer claims. The
fixture checker admits only values in that oracle. No language inference runs.
"""
from pathlib import Path

from .custody import prepare
from .io import code_hash, digest, encoded, object_hash, read, require, write
from .legacy.v4.native_source_policy import POLICY
from .scorer import score
from .support import context

TEXTS = {
    "positive": "SYNTHETIC NOTICE. Buy supplier risk control software. Proposals close September 15, 2026. "
                "Direct and partner proposals are permitted for this purchase. The stated continuation call is closed. "
                "This complete requirement is available. Investigate contract qualification before bidding.",
    "negative": "SYNTHETIC NOTICE. The entire purchase is audiovisual hardware. Supplier risk control software "
                "is expressly excluded. The response date and continuation are unknown. Qualification is unknown.",
    "expired": "SYNTHETIC NOTICE. Buy supplier risk control software. The response closed September 8, 2026. "
               "Direct and partner routes are permitted. Continuation status is unknown. The requirement is complete.",
    "continued": "SYNTHETIC NOTICE. Buy supplier risk control software. The response closed September 8, 2026. "
                 "The identified continuation call for this purchase is open September 20, 2026. "
                 "Direct and partner routes are permitted. The requirement is complete.",
    "closed": "SYNTHETIC NOTICE. Buy supplier risk control software. The response closed September 8, 2026. "
              "The identified continuation call for this purchase is explicitly cancelled. "
              "Direct and partner routes are permitted. The requirement is complete.",
    "insufficient": "SYNTHETIC NOTICE. The statement of work could not be acquired. Its requirement scope is unknown.",
    "conflict": "SYNTHETIC NOTICE. Clause A includes supplier risk control software. Clause B excludes it. "
                "No authoritative supersession is given. Response and continuation status are unknown.",
    "negative_clock_conflict": "SYNTHETIC NOTICE. Entire purchase is audiovisual hardware; supplier control software excluded. "
                "Clause A says the response is open, clause B says it closed; no supersession is known.",
    "route_closed": "SYNTHETIC NOTICE. Supplier risk control software purchase. Response open September 15, 2026. "
                    "Both direct and partner routes for this offering and purchase are expressly barred. "
                    "The identified continuation call is cancelled. Complete requirement available.",
}
AUDITED_FACTS = {
    "positive": {"fit": [True], "response_open": [True], "continuation_open": [False],
                 "route_eligible": [True], "evidence_sufficient": [True], "grade": [2, 3]},
    "negative": {"fit": [False], "grade": [0]},
    "expired": {"fit": [True], "response_open": [False], "route_eligible": [True],
                "evidence_sufficient": [True], "grade": [2]},
    "continued": {"fit": [True], "response_open": [False], "continuation_open": [True],
                  "route_eligible": [True], "evidence_sufficient": [True], "grade": [2]},
    "closed": {"fit": [True], "response_open": [False], "continuation_open": [False],
               "route_eligible": [True], "evidence_sufficient": [True], "grade": [2]},
    "insufficient": {"evidence_sufficient": [False]},
    "conflict": {"fit": [True, False]},
    "negative_clock_conflict": {"fit": [False], "response_open": [True, False]},
    "route_closed": {"fit": [True], "response_open": [True], "continuation_open": [False],
                     "route_eligible": [False], "evidence_sufficient": [True], "grade": [1]},
}
# Nine independently authored fixture truth combinations, declared before any
# reviewer input. This enumerates a test oracle; it is not a runtime classifier.
for _response in (True, False, "unknown"):
    for _continuation in (True, False, "unknown"):
        _kind = f"negative_{_response}_{_continuation}"
        _word = {True: "explicitly open", False: "explicitly closed", "unknown": "unknown"}
        TEXTS[_kind] = ("SYNTHETIC NOTICE. Entire purchase is audiovisual hardware; supplier control software excluded. "
                       f"The response is {_word[_response]}. The identified continuation is {_word[_continuation]}.")
        AUDITED_FACTS[_kind] = {"fit": [False], "grade": [0]}
        for _predicate, _value in (("response_open", _response), ("continuation_open", _continuation)):
            if type(_value) is bool:
                AUDITED_FACTS[_kind][_predicate] = [_value]


def put(path, value):
    """Fixture-only construction, including intentional mutation before trust freeze."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(encoded(value))


def seal_case(case):
    """Fixture-only caller freeze. Not used by live prepare/score."""
    case = Path(case)
    capture = read(case / "capture.json")
    capture["lock_sha256"] = digest(case / "lock.json")
    for run in capture["runs"]:
        run["files"] = {name: digest(case / name) for name in run["files"]}
    put(case / "capture.json", capture)
    pool = read(case / "pool.json")
    pool["capture_sha256"] = digest(case / "capture.json")
    for card in pool["cards"]:
        for ev in card["evidence"]:
            ev["snapshot_sha256"] = digest(case / ev["snapshot_file"])
    put(case / "pool.json", pool)
    files = {p.relative_to(case).as_posix(): digest(p) for p in sorted(case.rglob("*"))
             if p.is_file() and p.name != "case_manifest.json"}
    put(case / "case_manifest.json", {"schema": "benchmark-case-manifest.v5", "files": files})
    return {"schema": "benchmark-input-trust.v5", "synthetic": True,
            "manifest_sha256": digest(case / "case_manifest.json")}


def make_case(root, kinds=("positive",), objective="actionable_buying_now", rows=None):
    root = Path(root)
    require(not root.exists(), "Synthetic demo needs a new directory")
    case = root / "case"
    case.mkdir(parents=True)
    (root / "trust").mkdir()
    (root / "support").mkdir()
    (case / "company_brief.md").write_text("SYNTHETIC COMPANY: supplier risk control software; direct or partner route.\n")
    lock = {"schema": "benchmark-lock.v5", "synthetic": True, "case_id": "SYNTHETIC-V5",
            "code_sha256": code_hash(), "status": "LOCKED_BEFORE_CAPTURE", "root_freeze_authorized": True,
            "mode": "same_brief_native_discovery_relevance", "verified_revision": "SYNTHETIC-NATIVE",
            "frozen_at_utc": "2026-09-10T05:00:00Z", "timing": {
                "window_start_utc": "2026-09-10T05:30:00Z", "window_end_utc": "2026-09-10T09:00:00Z",
                "evidence_cutoff_utc": "2026-09-10T09:00:00Z", "minutes_per_product": 60,
                "operator_order": ["lila", "govtribe"]},
            "capture": {"limit": 100, "grade_slots": 20, "backfill": False, "reranking": False, "replacement_rerun": False},
            "source_policy": POLICY, "objective": {"id": objective, "as_of_utc": "2026-09-10T09:00:00Z",
                "offering_id": "supplier-controls", "allowed_routes": ["direct", "partner"],
                "continuation_universe": "Only the explicitly identified continuation call for this purchase"},
            "support_policy": {"schema": "benchmark-support-policy.v5", "synthetic": True,
                "method": "independent_original_source_review", "authorized_issuers": ["synthetic-checker"]},
            "inputs": {"company_brief.md": {"path": "company_brief.md", "sha256": digest(case / "company_brief.md")}}}
    put(case / "lock.json", lock)
    csv = case / "opportunities_2026-09-09.csv"
    csv.write_text("SYNTHETIC CSV ONLY\n2026-09-09\n")
    stat = {"device": 1, "inode": 1, "size_bytes": csv.stat().st_size, "modified_at_ns": 1, "changed_at_ns": 1}
    consumed = "/SYNTHETIC-NATIVE-CACHE/" + csv.name
    receipt = {"schema_version": 1, "scan": "primary", "path": consumed, "filename": csv.name,
               "cache_date": "2026-09-09", "cache_date_basis": "local_cache_filename_not_upstream_publication",
               "sha256": digest(csv), "bytes_read": csv.stat().st_size, "complete": True,
               "status": "verified", "integrity": "stable",
               "selection_started_local_date": "2026-09-09", "selection_finished_local_date": "2026-09-09",
               **{k: "2026-09-10T05:59:01Z" for k in (
                   "selection_started_at_utc", "selection_finished_at_utc", "read_started_at_utc")},
               "read_finished_at_utc": "2026-09-10T05:59:03Z",
               **{k: stat.copy() for k in ("file_stat_before", "file_stat_after", "path_stat_before", "path_stat_after")}}
    put(case / "native.json", {"results": {"sam_census": {"source": "sam_extract", "complete": True,
        "extract_receipts": [receipt], "attachment_candidates": {
            "eligible": 0, "selected": 0, "scan_skipped_reason": "limit_zero", "extract_receipts": []}}}})
    runs = []
    for index, system in enumerate(("LILA", "GovTribe")):
        raw, settings = f"raw{index}.json", f"settings{index}.json"
        put(case / raw, {"synthetic": True, "surface": "authored fixture, no native execution"})
        put(case / settings, {"synthetic": True, "fixed_native_order": True})
        rs = rows[index] if rows is not None else [(i, "retrieved_new_run") for i in range(len(kinds))]
        run = {"system": system, "sealed": True, "capture_complete": True,
               **{k: False for k in ("manual_reranking", "backfill", "replacement_rerun", "other_product_results_seen")},
               "operator": f"synthetic-operator-{index}", "started_at_utc": "2026-09-10T05:59:00Z" if index == 0 else "2026-09-10T07:00:00Z",
               "finished_at_utc": "2026-09-10T06:30:00Z" if index == 0 else "2026-09-10T07:30:00Z",
               **{k: "SYNTHETIC authored fixture" for k in (
                   "native_surface", "native_order", "product_version", "source_freshness", "limitations")},
               "files": {raw: digest(case / raw), settings: digest(case / settings)},
               "raw_capture_file": raw, "settings_capture_file": settings, "returned_total": len(rs),
               "results": [{"rank": n, "card_key": f"card-{i}", "native_id": f"SYN-{i}", "provenance": provenance,
                            "provenance_evidence": "SYNTHETIC recorded retrieval state"}
                           for n, (i, provenance) in enumerate(rs, 1)]}
        if index == 0:
            run.update(native_sweep_file="native.json", consumed_extract_files=[{
                "consumed_path": consumed, "sha256": digest(csv), "snapshot_file": csv.name}])
            run["files"].update({"native.json": digest(case / "native.json"), csv.name: digest(csv)})
        runs.append(run)
    put(case / "capture.json", {"schema": "benchmark-capture.v5", "synthetic": True, "runs": runs})
    needed = {r["card_key"] for run in runs for r in run["results"][:20]}
    cards = []
    for i, kind in enumerate(kinds):
        if f"card-{i}" not in needed:
            continue
        source = {"schema": "benchmark-original-notice.v1", "notice_id": f"SYN-{i}", "source_id": f"source-{i}",
                  "source_url": f"https://sam.gov/opp/SYN-{i}/view", "published_at_utc": "2026-09-09T10:00:00Z",
                  "text": TEXTS[kind]}
        name = f"sources/source-{i}.json"
        put(case / name, source)
        evidence = {"evidence_id": f"ev-{i}", "source_id": source["source_id"], "passage_id": f"passage-{i}",
                    "subject_id": source["notice_id"], "source_url": source["source_url"],
                    "published_at_utc": source["published_at_utc"], "retrieved_at_utc": "2026-09-10T05:59:05Z",
                    "retrieval_state": "retrieved", "authority": "original_substantive", "passage": source["text"],
                    "span": [0, len(source["text"])], "locator": "Full synthetic clause", "snapshot_file": name,
                    "snapshot_sha256": digest(case / name), "identity_state": "same_subject_record_bound"}
        cards.append({"card_key": f"card-{i}", "family_id": f"family-{i}", "subject_id": f"SYN-{i}",
                      "action_id": f"purchase-{i}", "identity_evidence": "SYNTHETIC authored identity",
                      "title": "Synthetic purchase", "agency": "Synthetic agency", "evidence_gaps": [], "evidence": [evidence]})
    put(case / "pool.json", {"schema": "benchmark-pool.v5", "synthetic": True, "blind_content_reviewed": True,
                             "custodian": "synthetic-custodian", "cards": cards})
    put(root / "trust/input.json", seal_case(case))
    return root


def prepare_fixture(root):
    root = Path(root)
    return prepare(root / "case", root / "packets", root / "trust/input.json", root / "trust/prepared.json",
                   "synthetic-reviewer-1", "synthetic-reviewer-2")


def make_claim(card, predicate, value, *, tag="", ref_index=0):
    return {"claim_id": predicate + tag, "predicate": predicate, "value": value,
            "rationale": "SYNTHETIC audited " + predicate + tag, "scope": card["decision_scope"],
            "evidence_refs": [card["evidence"][ref_index]["evidence_id"]] if card["evidence"] else []}


def submit(root, kinds, overrides=None):
    """Generate explicit synthetic reviews, then audit their exact claims.

    Overrides select reviewer assertions. AUDITED_FACTS controls admission; a
    contrary reviewer assertion receives no digest admission, even supported=true.
    """
    root = Path(root)
    key = read(root / "packets/PRIVATE_key.json")
    require(key["synthetic"] is True, "Synthetic fixture issuer refuses live input")
    lock = read(root / "case/lock.json")
    registry = {"schema": "benchmark-support-trust.v5", "snapshot_id": "SYNTHETIC-snapshot-1", "synthetic": True,
                "prepared_anchor_sha256": digest(root / "trust/prepared.json"),
                "policy_sha256": object_hash(lock["support_policy"]), "admission_approver": "synthetic-admitter",
                "issuers": {"synthetic-checker": {"principal": "synthetic-checker", "kind": "synthetic",
                    "provenance": "Authored fixture oracle, no real person or service", "independent": True,
                    "role": "source_support_checker"}}, "accepted_receipts": [], "revoked_receipts": {}}
    for number in (1, 2):
        folder = root / f"packets/reviewer_{number}"
        review, packet = read(folder / "review.json"), read(folder / "cards.json")
        review.update(model="SYNTHETIC-authored-review", submitted=True, independent=True,
                      origin_or_rank_seen=False, other_review_seen=False)
        for card, row in zip(packet["cards"], review["judgments"]):
            kind = kinds[int(card["subject_id"].split("-")[1])]
            facts = AUDITED_FACTS[kind]
            claims = [make_claim(card, p, vals[0]) for p, vals in facts.items()]
            if overrides:
                claims = overrides(number, card, claims)
            row.update(rationale="SYNTHETIC exact proposed judgment", claims=claims)
            for claim in claims:
                p, value = claim["predicate"], claim["value"]
                # Type-strict oracle membership (True never equals grade1).
                if not any(type(v) is type(value) and v == value for v in facts.get(p, [])):
                    continue
                ctx = context(packet, review["packet_sha256"], card, key["families"][key["ids"][card["blind_id"]]], claim)
                rationale = "SYNTHETIC independently authored support oracle: " + kind + "/" + p
                record = {"schema": "benchmark-check-record.v5", "synthetic": True, "issuer_id": "synthetic-checker",
                          "context_sha256": object_hash(ctx), "rationale": rationale}
                record_name = "records/" + object_hash(record) + ".json"
                put(root / "support" / record_name, record)
                receipt = {"schema": "benchmark-support-receipt.v5", "synthetic": True, "issuer_id": "synthetic-checker",
                           "verdict": "supported", "context": ctx, "assessment": {"original_source_reviewed": True,
                           "rationale": rationale, "record_file": record_name, "record_sha256": object_hash(record)}}
                sha = object_hash(receipt)
                put(root / "support" / (sha + ".json"), receipt)
                claim["support_receipt_sha256"] = sha
                if sha not in registry["accepted_receipts"]:
                    registry["accepted_receipts"].append(sha)
        put(folder / "review.json", review)
    put(root / "trust/support.json", registry)
    return registry


def score_fixture(root, result=None):
    root = Path(root)
    return score(root / "case", root / "packets", root / "trust/prepared.json", root / "trust/support.json",
                 digest(root / "trust/support.json"), root / "support", result)


def demo(root):
    kinds = ("positive", "negative", "expired", "continued", "closed", "insufficient")
    root = make_case(root, kinds)
    prepare_fixture(root)
    submit(root, kinds)
    return score_fixture(root, root / "result.json")
