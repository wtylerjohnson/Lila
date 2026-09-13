"""Exact admission and substantive-assessment binding, not language entailment."""
from .io import bound, digest, exact, nonempty, object_hash, read, require

DIMENSIONS = ("fit", "response_open", "continuation_open", "route_eligible", "evidence_sufficient")
CLAIM_FIELDS = {"claim_id", "predicate", "value", "rationale", "evidence_refs", "scope"}


def claim_body(claim):
    # supported and any other reviewer annotations are retained in raw output,
    # never used as proof. Only these defined fields form the exact assessment.
    return {k: claim[k] for k in CLAIM_FIELDS}


def validate_claim(claim, card):
    nonempty(claim["claim_id"], "claim ID")
    nonempty(claim["rationale"], "claim rationale")
    predicate, value = claim["predicate"], claim["value"]
    require(predicate in (*DIMENSIONS, "grade"), "Unsupported predicate")
    require(value == "unknown" or (type(value) is int and 0 <= value <= 3 if predicate == "grade"
                                   else type(value) is bool), "Invalid predicate value type")
    refs = claim["evidence_refs"]
    require(isinstance(refs, list) and all(isinstance(x, str) for x in refs) and
            len(refs) == len(set(refs)), "Invalid evidence references")
    by_id = {e["evidence_id"]: e for e in card["evidence"]}
    require(set(refs) <= set(by_id), "Unknown/cross-card reference")
    require(exact(claim["scope"], card["decision_scope"]), "Claim action/route/offering scope mismatch")
    return [by_id[r] for r in refs]


def context(packet, packet_sha, card, family, claim):
    evidence = validate_claim(claim, card)
    return {"packet_sha256": packet_sha, "blind_id": card["blind_id"], "subject_id": card["subject_id"],
            "family_identity_sha256": object_hash({"subject": card["subject_id"], "family": family}),
            "objective_sha256": object_hash(packet["objective"]), "brief_sha256": packet["brief_sha256"],
            "support_policy_sha256": packet["support_policy_sha256"], "claim": claim_body(claim),
            "evidence": evidence}


def validate_context(ctx):
    """Validate the received receipt representation, before exact comparison."""
    fields = {"packet_sha256", "blind_id", "subject_id", "family_identity_sha256", "objective_sha256",
              "brief_sha256", "support_policy_sha256", "claim", "evidence"}
    require(isinstance(ctx, dict) and set(ctx) == fields, "Receipt context schema")
    for field in fields - {"claim", "evidence"}:
        nonempty(ctx[field], "context " + field)
    require(isinstance(ctx["claim"], dict) and set(ctx["claim"]) == CLAIM_FIELDS, "Receipt claim schema")
    evidence = ctx["evidence"]
    require(isinstance(evidence, list), "Receipt evidence list required")
    fields = {"source_id", "passage_id", "subject_id", "source_url", "published_at_utc", "retrieved_at_utc",
              "retrieval_state", "authority", "passage", "span", "locator", "snapshot_sha256", "identity_state",
              "evidence_id", "snapshot_file"}
    for ev in evidence:
        require(isinstance(ev, dict) and set(ev) == fields, "Receipt evidence schema")
        for field in fields - {"span", "published_at_utc", "retrieved_at_utc"}:
            nonempty(ev[field], "receipt evidence " + field)
        for field in ("published_at_utc", "retrieved_at_utc"):
            require(ev[field] is None or isinstance(ev[field], str), "Receipt date type")
        span = ev["span"]
        require(isinstance(span, list) and len(span) == 2 and all(type(n) is int for n in span)
                and 0 <= span[0] < span[1], "Receipt span needs canonical integer offsets")


def load_registry(path, expected_sha, anchor_sha, lock, key, support_dir):
    # Read once. The caller pins this exact registry before the score event.
    raw = path.read_bytes()
    from .io import loads, sha_bytes
    require(sha_bytes(raw) == expected_sha, "Changed support registry snapshot")
    registry = loads(raw)
    fields = {"schema", "snapshot_id", "synthetic", "prepared_anchor_sha256", "policy_sha256",
              "admission_approver", "issuers", "accepted_receipts", "revoked_receipts"}
    require(set(registry) == fields and registry["schema"] == "benchmark-support-trust.v5", "Support registry schema")
    require(registry["synthetic"] is key["synthetic"] and
            registry["prepared_anchor_sha256"] == anchor_sha and
            registry["policy_sha256"] == object_hash(lock["support_policy"]), "Registry context mismatch")
    nonempty(registry["snapshot_id"], "registry version")
    approver = registry["admission_approver"]
    nonempty(approver, "admission principal")
    excluded = set(key["reviewers"]) | {r["operator"] for r in key["capture_runs"]}
    require(approver not in excluded, "Admission/reviewer/operator conflict")
    issuers = registry["issuers"]
    require(isinstance(issuers, dict), "Issuer registry required")
    principals = set()
    for issuer_id, issuer in issuers.items():
        require(issuer_id in lock["support_policy"]["authorized_issuers"], "Unfrozen checker")
        require(set(issuer) == {"principal", "kind", "provenance", "independent", "role"} and
                issuer["kind"] in ("human", "AI", "synthetic") and issuer["independent"] is True and
                issuer["role"] == "source_support_checker", "Invalid checker provenance/role")
        principal = nonempty(issuer["principal"], "checker principal")
        require(principal == issuer_id and principal not in excluded | {approver, key["custodian"]},
                "Checker principal/independence mismatch")
        require(principal not in principals, "Duplicate checker principal")
        principals.add(principal)
        nonempty(issuer["provenance"], "checker provenance")
        require(key["synthetic"] or issuer["kind"] != "synthetic", "Synthetic checker in live registry")
    accepted, revoked = registry["accepted_receipts"], registry["revoked_receipts"]
    require(isinstance(accepted, list) and all(isinstance(x, str) for x in accepted) and
            len(accepted) == len(set(accepted)), "Duplicate receipt admission")
    require(isinstance(revoked, dict) and set(revoked) <= set(accepted), "Invalid revocation map")
    for reason in revoked.values():
        nonempty(reason, "revocation reason")
    receipts = {}
    for receipt_sha in accepted:
        receipt = read(bound(support_dir, receipt_sha + ".json", receipt_sha))
        require(set(receipt) == {"schema", "synthetic", "issuer_id", "verdict", "context", "assessment"} and
                receipt["schema"] == "benchmark-support-receipt.v5", "Receipt schema")
        require(receipt["synthetic"] is key["synthetic"] and receipt["issuer_id"] in issuers,
                "Receipt issuer/domain not authorized")
        require(receipt["verdict"] == "supported", "Unsupported receipt verdict")
        validate_context(receipt["context"])
        assessment = receipt["assessment"]
        require(set(assessment) == {"original_source_reviewed", "rationale", "record_file", "record_sha256"}
                and assessment["original_source_reviewed"] is True, "Missing source-grounded assessment")
        nonempty(assessment["rationale"], "source-support assessment rationale")
        record = read(bound(support_dir, assessment["record_file"], assessment["record_sha256"]))
        require(isinstance(record, dict) and type(record.get("synthetic")) is bool,
                "Checker record domain must be boolean")
        require(exact(record, {"schema": "benchmark-check-record.v5", "synthetic": key["synthetic"],
                           "issuer_id": receipt["issuer_id"], "context_sha256": object_hash(receipt["context"]),
                           "rationale": assessment["rationale"]}), "Checker original-review record mismatch")
        receipts[receipt_sha] = receipt
    return registry, receipts


def admissibility(evidence):
    if not evidence:
        return "no_original_passage"
    if any(e["retrieval_state"] != "retrieved" for e in evidence):
        return "source_not_retrieved"
    if any(e["authority"] != "original_substantive" for e in evidence):
        return "non_substantive_or_discovery_only"
    if any(e["published_at_utc"] is None or e["retrieved_at_utc"] is None for e in evidence):
        return "unknown_source_date"
    return None


def evaluate(packet, packet_sha, card, family, row, registry, receipts):
    nonempty(row["rationale"], "judgment rationale")
    claims = row["claims"]
    require(isinstance(claims, list) and len({c["claim_id"] for c in claims}) == len(claims), "Duplicate claim ID")
    outcomes, values, accepted_ids = [], {p: set() for p in (*DIMENSIONS, "grade")}, {p: [] for p in (*DIMENSIONS, "grade")}
    for claim in claims:
        ctx = context(packet, packet_sha, card, family, claim)
        receipt_sha = claim.get("support_receipt_sha256")
        reason = admissibility(ctx["evidence"])
        receipt = receipts.get(receipt_sha)
        if claim["value"] == "unknown":
            reason = "asserted_unknown"
        elif receipt is None:
            reason = reason or "receipt_not_admitted"
        elif receipt_sha in registry["revoked_receipts"]:
            reason = "receipt_revoked"
        else:
            require(exact(receipt["context"], ctx), "Receipt/claim context mismatch")
        supported = reason is None
        outcomes.append({"claim_id": claim["claim_id"], "predicate": claim["predicate"], "value": claim["value"],
                         "support_status": "supported" if supported else "unsupported", "reason": reason,
                         "receipt_sha256": receipt_sha})
        if supported:
            values[claim["predicate"]].add(claim["value"])
            accepted_ids[claim["predicate"]].append(claim["claim_id"])
    supported_proposals = {p: sorted(v, key=str) for p, v in values.items()}
    context_receipts = []
    # All admitted unsuperseded assertions in this exact card/predicate context
    # can expose a conflict, even if a reviewer selected only one of them.
    for p in values:
        if not values[p]:
            continue
        for receipt_sha, receipt in receipts.items():
            ctx = receipt["context"]
            if receipt_sha in registry["revoked_receipts"] or ctx.get("blind_id") != card["blind_id"]:
                continue
            other = ctx["claim"]
            if other["predicate"] != p:
                continue
            expected = context(packet, packet_sha, card, family, other)
            require(exact(ctx, expected), "Admitted support context mismatch")
            if other["value"] != "unknown" and admissibility(ctx["evidence"]) is None:
                values[p].add(other["value"])
                context_receipts.append({"receipt_sha256": receipt_sha, "predicate": p, "value": other["value"]})
    states = {p: next(iter(v)) if len(v) == 1 else "conflicted" if v else "unknown" for p, v in values.items()}
    def branch_or(a, b):
        if a is True or b is True:
            return True
        if a is False and b is False:
            return False
        return "conflicted" if "conflicted" in (a, b) else "unknown"
    action = states["response_open"] if packet["objective"]["id"] == "respond_to_this_notice" else branch_or(
        states["response_open"], states["continuation_open"])
    decisive = [p for p in ("fit", "route_eligible") if states[p] is False]
    if action is False:
        decisive += ["response_open"] if packet["objective"]["id"] == "respond_to_this_notice" else [
            "response_open", "continuation_open"]
    affirmative = all(states[p] is True for p in ("fit", "route_eligible", "evidence_sufficient"))
    grade = states["grade"]
    result = "not_relevant" if decisive else "relevant" if (
        affirmative and action is True and type(grade) is int and grade >= 2) else "unknown"
    unresolved = [p for p, value in states.items() if value in ("unknown", "conflicted")]
    if states["evidence_sufficient"] is False:
        unresolved.append("evidence_sufficient_false_is_knowledge_gap")
    return {"classification": result, "states": states, "action_state": action,
            "decisive_predicates": decisive, "decisive_claim_ids": [i for p in decisive for i in accepted_ids[p]],
            "unresolved_dimensions": unresolved, "evidence_gaps": card["evidence_gaps"],
            "supported_proposals": supported_proposals, "context_receipts": context_receipts,
            "support_outcomes": outcomes, "raw_judgment": row}
