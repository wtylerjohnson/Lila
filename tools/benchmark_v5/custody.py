"""Freeze, native custody, original passage binding, and neutral packet preparation."""
import random
import shutil
import tempfile
from pathlib import Path
from urllib.parse import parse_qsl, unquote, urlparse

from .io import (bound, code_hash, digest, external_file, neutral, nonempty, object_hash,
                 read, require, safe_path, stamp, verify_manifest, write)
from .legacy.v3.discovery_score import PROVENANCE, SYSTEMS
from .legacy.v4.native_source_policy import validate as source_validate

OBJECTIVES = {"respond_to_this_notice", "actionable_buying_now"}
PUBLIC_EVIDENCE = ("source_id", "passage_id", "subject_id", "source_url", "published_at_utc",
                   "retrieved_at_utc", "retrieval_state", "authority", "passage", "span",
                   "locator", "snapshot_sha256", "identity_state")
RUBRIC = """# Evidence-state review v5

Record each claim separately: fit, response_open, continuation_open,
route_eligible, evidence_sufficient, and grade. Use true/false/unknown for
predicates and integers 0-3 or unknown for grade. Give a specific rationale and
references to original passages. Grade2 permits a credible investigation with
an honest qualification question; grade3 denotes a specific current requirement
with evidenced fit and credible transaction path. Grade alone never establishes
rejection. Missing sources, discovery text and citation membership do not prove
positive or negative claims. Register no support yourself.

Work independently, disclose contamination, and finalize all cards including
unknowns. A separate predeclared checker owns source-support assessments; a
registered assessment establishes binding, not automatic language entailment.
No score authorizes a release, seller-ready lead, winner or novelty claim.
"""


def validate_evidence(base, card, ev, files, cutoff):
    for field in ("source_id", "passage_id", "subject_id", "locator"):
        neutral(ev[field], field)
    require(ev["authority"] in ("original_substantive", "discovery_only", "non_substantive"),
            "Unknown evidence authority")
    require(ev["retrieval_state"] in ("retrieved", "missing", "unreadable", "failed"),
            "Unknown retrieval state")
    require(ev["subject_id"] == card["subject_id"], "Cross-subject evidence")
    url = urlparse(ev["source_url"])
    require(url.scheme == "https" and (url.hostname or "").lower().endswith((".gov", ".mil")),
            "Original government URL required")
    neutral(unquote(ev["source_url"]), "URL")
    tracking = {"rank", "product", "provider", "origin", "referrer", "ref", "source"}
    require(not url.username and not url.password and
            not any(k.lower().startswith("utm_") or k.lower() in tracking
                    for k, _ in parse_qsl(url.query) + parse_qsl(url.fragment)), "URL tracking leak")
    for field in ("published_at_utc", "retrieved_at_utc"):
        if ev[field] is None:
            require(card["evidence_gaps"], "Unknown date needs explicit gap")
        else:
            require(stamp(ev[field]) <= cutoff, "Post-cutoff evidence")
    if ev["published_at_utc"] and ev["retrieved_at_utc"]:
        require(stamp(ev["published_at_utc"]) <= stamp(ev["retrieved_at_utc"]), "Impossible source chronology")
    nonempty(ev["passage"], "passage")
    require(ev["snapshot_file"] in files and files[ev["snapshot_file"]] == ev["snapshot_sha256"],
            "Snapshot outside trusted case manifest")
    path = bound(base, ev["snapshot_file"], ev["snapshot_sha256"])
    # v5 originals are retained JSON notice records. Text-only/HTML/PDF evidence
    # requires a future identity adapter; no URL-only subject attestation.
    original = read(path)
    require(original["schema"] == "benchmark-original-notice.v1", "Unsupported original identity format")
    require(original["notice_id"] == card["subject_id"] == ev["subject_id"] and
            original["source_url"] == ev["source_url"] and original["source_id"] == ev["source_id"],
            "Original identity/URL mismatch")
    require(original["published_at_utc"] == ev["published_at_utc"], "Contradictory publication metadata")
    # State this deliberately narrow identity parser in the packet. It is a
    # custody comparison, not authentication of a government origin.
    require(ev["identity_state"] == "same_subject_record_bound", "Unbound source identity")
    text = original["text"]
    neutral(text, "original snapshot")
    span = ev["span"]
    require(isinstance(span, list) and len(span) == 2 and all(type(n) is int for n in span) and
            0 <= span[0] < span[1] <= len(text) and text[span[0]:span[1]] == ev["passage"],
            "Invalid exact passage span")
    neutral(ev["passage"], "passage")


def validate_case(case, trust):
    case = Path(case)
    mpath = safe_path(case, "case_manifest.json")
    require(digest(mpath) == trust["manifest_sha256"], "Untrusted case manifest")
    manifest = read(mpath)
    paths = verify_manifest(case, manifest, "benchmark-case-manifest.v5")
    for name in ("lock.json", "capture.json", "pool.json", "company_brief.md"):
        require(name in paths, "Missing required input")
    lock, capture, pool = [read(paths[p]) for p in ("lock.json", "capture.json", "pool.json")]
    require(lock["schema"] == "benchmark-lock.v5" and capture["schema"] == "benchmark-capture.v5"
            and pool["schema"] == "benchmark-pool.v5", "Wrong schema; explicit migration required")
    require(type(trust["synthetic"]) is bool and
            all(x["synthetic"] is trust["synthetic"] for x in (lock, capture, pool)), "Synthetic/live mismatch")
    require(lock["code_sha256"] == code_hash(), "Frozen scorer code changed")
    require(lock["status"] == "LOCKED_BEFORE_CAPTURE" and lock["root_freeze_authorized"] is True,
            "Authorized final lock required")
    require(lock["mode"] == "same_brief_native_discovery_relevance", "Wrong mode")
    nonempty(lock["verified_revision"], "native revision")
    timing = lock["timing"]
    start, end, cutoff = [stamp(timing[k]) for k in
                          ("window_start_utc", "window_end_utc", "evidence_cutoff_utc")]
    require(stamp(lock["frozen_at_utc"]) <= start < end and (end-start).total_seconds() <= 86400,
            "Invalid capture window")
    require(timing["minutes_per_product"] == 60 and timing["operator_order"] == ["lila", "govtribe"],
            "Invalid capture budget/order")
    rule = lock["capture"]
    require(rule["limit"] == 100 and rule["grade_slots"] == 20 and
            all(rule[k] is False for k in ("backfill", "reranking", "replacement_rerun")), "Invalid fixed20 rules")
    require(lock["objective"]["id"] in OBJECTIVES and
            stamp(lock["objective"]["as_of_utc"]) == cutoff, "Invalid frozen objective/reference time")
    for field in ("offering_id", "continuation_universe"):
        neutral(lock["objective"][field], field)
    routes = lock["objective"]["allowed_routes"]
    require(isinstance(routes, list) and routes and len(routes) == len(set(routes)), "Explicit route universe required")
    for route in routes:
        neutral(route, "route")
    policy = lock["support_policy"]
    require(policy["schema"] == "benchmark-support-policy.v5" and
            policy["method"] == "independent_original_source_review" and
            policy["synthetic"] is trust["synthetic"], "Invalid support policy")
    issuers = policy["authorized_issuers"]
    require(isinstance(issuers, list) and len(set(issuers)) == len(issuers), "Duplicate checker")
    for issuer in issuers:
        nonempty(issuer, "checker")
    for item in lock["inputs"].values():
        require(item["path"] in paths and manifest["files"][item["path"]] == item["sha256"],
                "Frozen input outside trusted manifest")
    require(lock["inputs"]["company_brief.md"] ==
            {"path": "company_brief.md", "sha256": manifest["files"]["company_brief.md"]}, "Brief binding")
    brief = neutral(paths["company_brief.md"].read_text(), "brief")
    require(capture["lock_sha256"] == digest(paths["lock.json"]) and
            pool["capture_sha256"] == digest(paths["capture.json"]), "Broken capture/pool binding")
    require(pool["blind_content_reviewed"] is True, "Neutral custodian review required")
    custodian = nonempty(pool["custodian"], "custodian")
    runs = capture["runs"]
    require(isinstance(runs, list) and [r["system"] for r in runs] == list(SYSTEMS), "One run per product")
    selected, operators, needed = {}, set(), set()
    previous_end = start
    for run in runs:
        require(run["sealed"] is True and run["capture_complete"] is True, "Incomplete capture")
        require(all(run[k] is False for k in ("manual_reranking", "backfill", "replacement_rerun",
                                             "other_product_results_seen")), "Invalid capture behavior")
        a, b = stamp(run["started_at_utc"]), stamp(run["finished_at_utc"])
        require(previous_end <= a < b <= end and (b-a).total_seconds() <= 3600, "Invalid run order/time")
        previous_end = b
        operators.add(nonempty(run["operator"], "operator"))
        for field in ("native_surface", "native_order", "product_version", "source_freshness", "limitations"):
            nonempty(run[field], field)
        require(isinstance(run["files"], dict) and run["files"], "Missing native receipts")
        for name, sha in run["files"].items():
            require(name in paths and manifest["files"][name] == sha, "Capture receipt outside manifest")
        require(all(run[k] in run["files"] for k in ("raw_capture_file", "settings_capture_file")), "Missing receipts")
        rows, total = run["results"], run["returned_total"]
        require(type(total) is int and total >= 0 and isinstance(rows, list) and
                len(rows) == min(total, 100), "Truncated native slots")
        require([r["rank"] for r in rows] == list(range(1, len(rows)+1)) and
                all(type(r["rank"]) is int for r in rows), "Invalid native ranks")
        for row in rows:
            require(row["provenance"] in PROVENANCE, "Invalid retrieval provenance")
            nonempty(row["provenance_evidence"], "retrieval evidence/gap")
            nonempty(row["card_key"], "card key")
            require(row["native_id"] is None or isinstance(row["native_id"], str), "Invalid native ID")
            if not row["native_id"]:
                nonempty(row["missing_id_reason"], "native ID gap")
        selected[run["system"]] = rows[:20]
        needed.update(r["card_key"] for r in rows[:20])
    require(len(operators) == 2 and custodian not in operators, "Distinct operators/custodian required")
    require(not (set(issuers) & (operators | {custodian})), "Checker must be independent of capture/custody")
    # Every policy-read path was confined and hash checked above. The original
    # v4 source validator is preserved byte for byte and still runs first.
    certification = source_validate(lock, capture, case)
    cards = pool["cards"]
    require(isinstance(cards, list) and len(cards) == len(needed) and
            {c["card_key"] for c in cards} == needed, "Pool must cover only top20 union")
    seen = set()
    for card in cards:
        family = card["family_id"]
        require(family is None or isinstance(family, str) and bool(family.strip()), "Invalid family")
        if family is not None:
            require(family not in seen, "Duplicate family cards")
            seen.add(family)
        for field in ("title", "agency", "subject_id", "action_id"):
            neutral(card[field], field)
        nonempty(card["identity_evidence"], "identity evidence/gap")
        gaps = card["evidence_gaps"]
        require(isinstance(gaps, list), "Explicit evidence gaps required")
        for gap in gaps:
            neutral(gap, "gap")
        evidence = card["evidence"]
        require(isinstance(evidence, list) and (evidence or gaps), "Evidence or explicit gap required")
        require(len({e["evidence_id"] for e in evidence}) == len(evidence), "Duplicate evidence ID")
        for ev in evidence:
            validate_evidence(case, card, ev, manifest["files"], cutoff)
    return lock, capture, pool, selected, brief, operators, certification


def prepare(case, output, input_trust, anchor_path, reviewer_one, reviewer_two):
    case, out = Path(case), Path(output)
    trust_path = external_file(input_trust, case, out)
    anchor_path = external_file(anchor_path, case, out)
    require(not anchor_path.exists() and not out.exists(), "Never overwrite an existing packet/anchor")
    trust = read(trust_path)
    require(trust["schema"] == "benchmark-input-trust.v5", "Wrong trust schema")
    lock, capture, pool, selected, brief, operators, certification = validate_case(case, trust)
    reviewers = [nonempty(reviewer_one, "reviewer one"), nonempty(reviewer_two, "reviewer two")]
    excluded = operators | {pool["custodian"]} | set(lock["support_policy"]["authorized_issuers"])
    require(len(set(reviewers)) == 2 and not (set(reviewers) & excluded), "Independent reviewers required")
    out.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=".v5-", dir=out.parent))
    try:
        cards = list(pool["cards"])
        random.SystemRandom().shuffle(cards)
        ids, public, copies = {}, [], {}
        for i, card in enumerate(cards, 1):
            bid = f"C{i:03}"
            ids[bid] = card["card_key"]
            evidence = []
            for j, ev in enumerate(card["evidence"], 1):
                name = f"sources/{bid}-E{j:03}.json"
                copies[name] = safe_path(case, ev["snapshot_file"]).read_bytes()
                evidence.append({k: ev[k] for k in PUBLIC_EVIDENCE} |
                                {"evidence_id": f"{bid}-E{j:03}", "snapshot_file": name})
            public.append({k: card[k] for k in ("subject_id", "title", "agency", "evidence_gaps")} |
                          {"blind_id": bid, "evidence": evidence, "decision_scope": {
                              "action_id": card["action_id"], "offering_id": lock["objective"]["offering_id"],
                              "allowed_routes": lock["objective"]["allowed_routes"],
                              "continuation_universe": lock["objective"]["continuation_universe"]}})
        bundle = {"schema": "benchmark-packet.v5", "synthetic": trust["synthetic"], "cards": public,
                  "objective": lock["objective"], "support_policy_sha256": object_hash(lock["support_policy"]),
                  "brief_sha256": lock["inputs"]["company_brief.md"]["sha256"]}
        hashes = {}
        for number, reviewer in enumerate(reviewers, 1):
            folder = stage / f"reviewer_{number}"
            folder.mkdir()
            for name, data in {"company_brief.md": brief.encode(), "rubric.md": RUBRIC.encode(), **copies}.items():
                path = safe_path(folder, name)
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(data)
            write(folder / "cards.json", bundle)
            manifest = {p.relative_to(folder).as_posix(): digest(p)
                        for p in sorted(folder.rglob("*")) if p.is_file()}
            write(folder / "packet_manifest.json", {"schema": "benchmark-packet-manifest.v5", "files": manifest})
            hashes[str(number)] = digest(folder / "packet_manifest.json")
            write(folder / "review.json", {"schema": "benchmark-review.v5", "synthetic": trust["synthetic"],
                  "reviewer_id": reviewer, "reviewer_type": "AI", "model": "", "submitted": False,
                  "independent": None, "origin_or_rank_seen": None, "other_review_seen": None,
                  "packet_sha256": hashes[str(number)], "judgments": [
                      {"blind_id": bid, "rationale": "", "claims": []} for bid in ids]})
        key = {"schema": "benchmark-private-key.v5", "case_id": lock["case_id"], "synthetic": trust["synthetic"],
               "input_trust": trust, "ids": ids, "selected_slots": selected,
               "families": {c["card_key"]: c["family_id"] for c in cards},
               "reviewers": reviewers, "packet_hashes": hashes}
        write(stage / "PRIVATE_key.json", key)
        anchor = {"schema": "benchmark-prepared-trust.v5", "input_trust": trust, "code_sha256": code_hash(),
                  "private_key_sha256": digest(stage / "PRIVATE_key.json"), "packet_hashes": hashes}
        stage.rename(out)
        write(anchor_path, anchor)
    except BaseException:
        if stage.exists():
            shutil.rmtree(stage)
        raise
    return {"anchor": anchor, "source_certification": certification}
