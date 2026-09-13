"""Score validated objective states; preserve native fixed-slot accounting."""
from pathlib import Path

from .custody import validate_case
from .io import (bound, code_hash, digest, exact, external_file, nonempty, read, require,
                 safe_path, verify_manifest, write)
from .legacy.v3.discovery_score import RETRIEVED
from .support import DIMENSIONS, context, evaluate, load_registry


def metrics(key, judgments):
    by_card = {key["ids"][bid]: row["classification"] for bid, row in judgments.items()}
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


def score(case, packet_dir, anchor_path, support_trust, support_trust_sha256, support_dir, result_path=None):
    case, root = Path(case), Path(packet_dir)
    anchor_path = external_file(anchor_path, case, root)
    support_trust = external_file(support_trust, case, root, support_dir)
    anchor_sha = digest(anchor_path)
    anchor = read(anchor_path)
    require(anchor["schema"] == "benchmark-prepared-trust.v5", "Wrong anchor schema")
    require(anchor["code_sha256"] == code_hash(), "Prepared scorer changed")
    key = read(bound(root, "PRIVATE_key.json", anchor["private_key_sha256"]))
    require(key["schema"] == "benchmark-private-key.v5" and exact(key["input_trust"], anchor["input_trust"])
            and exact(key["packet_hashes"], anchor["packet_hashes"]), "Private custody/anchor mismatch")
    lock, capture, pool, _, _, _, certification = validate_case(case, anchor["input_trust"])
    key = key | {"capture_runs": capture["runs"], "custodian": pool["custodian"]}
    registry, receipts = load_registry(support_trust, support_trust_sha256, anchor_sha, lock, key, support_dir)
    reviews, evaluations, review_hashes = {}, {}, {}
    for number in (1, 2):
        folder = safe_path(root, f"reviewer_{number}")
        packet_sha = anchor["packet_hashes"][str(number)]
        manifest = read(bound(folder, "packet_manifest.json", packet_sha))
        paths = verify_manifest(folder, manifest, "benchmark-packet-manifest.v5")
        packet = read(paths["cards.json"])
        require(packet["schema"] == "benchmark-packet.v5", "Wrong schema; explicit migration required")
        cards = {r["blind_id"]: r for r in packet["cards"]}
        # Validate every admitted receipt, even those not chosen by a reviewer.
        for receipt in receipts.values():
            ctx = receipt["context"]
            require(ctx["blind_id"] in cards, "Admitted receipt has unknown card")
            card = cards[ctx["blind_id"]]
            family = key["families"][key["ids"][card["blind_id"]]]
            require(exact(ctx, context(packet, packet_sha, card, family, ctx["claim"])), "Admitted context mismatch")
        rpath = safe_path(folder, "review.json")
        raw = rpath.read_bytes()
        from .io import loads, sha_bytes
        review = loads(raw)
        require(review["schema"] == "benchmark-review.v5" and review["synthetic"] is key["synthetic"], "Review schema/domain")
        require(review["packet_sha256"] == packet_sha and review["reviewer_id"] == key["reviewers"][number-1]
                and review["reviewer_type"] == "AI", "Reviewer/packet mismatch")
        nonempty(review["model"], "reviewer model/provenance")
        require(review["submitted"] is True and review["independent"] is True and
                review["origin_or_rank_seen"] is False and review["other_review_seen"] is False, "Contaminated/incomplete review")
        rows = review["judgments"]
        require(isinstance(rows, list) and len(rows) == len(cards) and
                {r["blind_id"] for r in rows} == set(cards), "Missing/duplicate judgments")
        name = f"reviewer_{number}"
        evaluations[name] = {row["blind_id"]: evaluate(packet, packet_sha, cards[row["blind_id"]],
                    key["families"][key["ids"][row["blind_id"]]], row, registry, receipts) for row in rows}
        reviews[name] = review
        review_hashes[name] = sha_bytes(raw)
    one, two = evaluations["reviewer_1"], evaluations["reviewer_2"]
    def signature(row):
        return row["states"], row["action_state"], row["classification"], row["supported_proposals"]
    validated_disagreements = [bid for bid in key["ids"] if signature(one[bid]) != signature(two[bid])]
    def proposals(row):
        # Claims have already been type validated. Ignore only IDs, citations and
        # prose, not unsupported proposals. Empty lists mean not proposed and
        # remain distinct from an explicit unknown assertion.
        return {p: sorted({c["value"] for c in row["raw_judgment"]["claims"] if c["predicate"] == p}, key=str)
                for p in (*DIMENSIONS, "grade")}
    original = {name: {bid: proposals(row) for bid, row in rows.items()} for name, rows in evaluations.items()}
    proposal_disagreements = [bid for bid in key["ids"] if
                             not exact(original["reviewer_1"][bid], original["reviewer_2"][bid])]
    disagreements = [bid for bid in key["ids"] if bid in set(validated_disagreements) | set(proposal_disagreements)]
    agreed = {bid: one[bid] if bid not in validated_disagreements else {"classification": "unknown"} for bid in key["ids"]}
    coverage = {name: {"cards": len(rows), "proposed_predicate_slots": sum(bool(v) for row in rows.values() for v in row.values()),
                       "possible_predicate_slots": len(rows) * (len(DIMENSIONS) + 1)} for name, rows in original.items()}
    complete_initial_agreements = [bid for bid in key["ids"] if bid not in proposal_disagreements and
                                   all(all(original[name][bid].values()) for name in original)]
    unresolved = [bid for bid in key["ids"] if any(
        row["unresolved_dimensions"] or row["evidence_gaps"] or row["classification"] == "unknown"
        or any(c["support_status"] != "supported" for c in row["support_outcomes"])
        for row in (one[bid], two[bid]))]
    unresolved_slots = any(r["provenance"] == "unresolved" or key["families"][r["card_key"]] is None
                           for rows in key["selected_slots"].values() for r in rows)
    result = {"schema": "benchmark-result.v5", "case_id": key["case_id"], "synthetic": key["synthetic"],
              "status": "PRELIMINARY_AI_REVIEW_WITH_UNRESOLVED_ITEMS" if disagreements or unresolved or unresolved_slots
                        else "PRELIMINARY_AI_REVIEW_COMPLETE",
              "source_certification": certification, "objective": lock["objective"],
              "reviewer_counts": {name: metrics(key, rows) for name, rows in evaluations.items()},
              "agreed_counts_only": metrics(key, agreed), "validated_reviews": evaluations, "raw_reviews": reviews,
              "disagreements": disagreements, "unresolved_card_ids": unresolved,
              "original_proposal_disagreements": proposal_disagreements, "original_semantic_proposals": original,
              "original_proposal_coverage": coverage, "validated_state_disagreements": validated_disagreements,
              "initial_complete_decision_agreement": len(complete_initial_agreements)/len(one) if one else None,
              "initial_semantic_proposal_agreement": (len(one)-len(proposal_disagreements))/len(one) if one else None,
              "validated_state_agreement": (len(one)-len(validated_disagreements))/len(one) if one else None,
              "agreed_counts_basis": "Validated states only; original proposal disagreements are retained separately, never adjudicated",
              "adjudication": "NOT_PERFORMED", "overall_winner": None, "novelty_metric": "WITHHELD_PARTIAL_BASELINE",
              "marketing_status": "NOT_AUTHORIZED_BY_THIS_SCORE",
              "evidence_hashes": {"prepared_anchor": anchor_sha, "support_registry": support_trust_sha256,
                                  "case_manifest": anchor["input_trust"]["manifest_sha256"],
                                  "code": anchor["code_sha256"], "reviews": review_hashes},
              "support_registry_snapshot": registry,
              "limitations": ["Offline custody and authorized assessment binding, not government-source authentication or automatic semantic entailment.",
                  "Shared source-support checking is a validation dependency, not independent human validation or adjudication.",
                  "Trust assumes an honest caller, admission approver and source checker; their compromise or semantic error is outside hash guarantees.",
                  "Fixed first20; unresolved, duplicate, injected and unfilled slots are never backfilled.",
                  "Zero confirmed credit proves neither market emptiness, a tie, possible yield nor a winner.",
                  "Only same-subject retained JSON notice records are supported; cross-notice relations need a future identity adapter.",
                  "Source-policy certification precedes score; failure is not a zero-quality finding.",
                  "Original native role labels are not inferred from scorer exclusions; S3-ROLE-007 remains separate.",
                  "No live native yield, new-to-client, seller readiness, release or total-market recall proof."]}
    if result_path is not None:
        write(result_path, result)
    return result
