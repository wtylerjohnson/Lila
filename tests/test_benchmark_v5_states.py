"""Portable 32-case acceptance matrix and explicit trust-boundary adversaries."""
import copy
import itertools
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from tools.benchmark_v5.custody import prepare, validate_case
from tools.benchmark_v5.io import digest, loads, object_hash, read, safe_path
from tools.benchmark_v5.legacy.v3.discovery_score import classification as v3_classification
from tools.benchmark_v5.legacy.v3.discovery_score import metrics as v3_metrics
from tools.benchmark_v5.scorer import score
from tools.benchmark_v5.synthetic import (AUDITED_FACTS, make_case, make_claim, prepare_fixture,
                                         put, score_fixture, seal_case, submit)


class EvidenceStates(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.count = 0

    def fixture(self, kind="positive", *, objective="actionable_buying_now", override=None, pre=None,
                rows=None, complete=True):
        self.count += 1
        self.root = Path(self.tmp.name).resolve() / str(self.count)
        kinds = (kind,) if isinstance(kind, str) else kind
        make_case(self.root, kinds, objective, rows)
        if pre:
            pre(self.root / "case")
            put(self.root / "trust/input.json", seal_case(self.root / "case"))
        prepare_fixture(self.root)
        submit(self.root, kinds, override)
        return score_fixture(self.root) if complete else self.root

    def decision(self, result, reviewer=1, subject=0):
        cards = read(self.root / "packets/reviewer_1/cards.json")["cards"]
        bid = next(c["blind_id"] for c in cards if c["subject_id"] == f"SYN-{subject}")
        return result["validated_reviews"][f"reviewer_{reviewer}"][bid]

    @staticmethod
    def mutate_evidence(case, **changes):
        pool = read(case / "pool.json")
        pool["cards"][0]["evidence"][0].update(changes)
        put(case / "pool.json", pool)

    def change_review(self, edit, number=1):
        path = self.root / f"packets/reviewer_{number}/review.json"
        review = read(path)
        edit(review)
        put(path, review)

    def test_scope_negative_unknown_time(self):
        result = self.fixture("negative")
        row = self.decision(result)
        self.assertEqual(row["classification"], "not_relevant")
        self.assertEqual(row["states"]["response_open"], "unknown")
        self.assertEqual(row["decisive_predicates"], ["fit"])
        self.assertEqual(v3_classification({"review_status": "judged", "grade": 0, "in_scope": False,
                                          "current_buying_motion": "unknown", "evidence_supported": True}), "unknown")

    def test_fit_unknown_action(self):
        result = self.fixture(override=lambda n, c, cs: [x for x in cs if x["predicate"] == "fit"])
        self.assertIs(self.decision(result)["states"]["fit"], True)
        self.assertEqual(self.decision(result)["classification"], "unknown")

    def test_expired_response(self):
        self.assertEqual(self.decision(self.fixture("expired", objective="respond_to_this_notice"))["classification"], "not_relevant")

    def test_expired_broader_action(self):
        row = self.decision(self.fixture("expired"))
        self.assertEqual(row["classification"], "unknown")
        self.assertIs(row["states"]["response_open"], False)

    def test_insufficient_evidence(self):
        for authority in ("discovery_only", "non_substantive"):
            with self.subTest(authority=authority):
                row = self.decision(self.fixture(pre=lambda p: self.mutate_evidence(p, authority=authority)))
                self.assertEqual(row["classification"], "unknown")
                self.assertTrue(all(x["support_status"] == "unsupported" for x in row["support_outcomes"]))
        row = self.decision(self.fixture("insufficient"))
        self.assertIs(row["states"]["evidence_sufficient"], False)
        self.assertEqual(row["classification"], "unknown")

    def test_all_affirmative(self):
        result = self.fixture()
        self.assertEqual(self.decision(result)["classification"], "relevant")
        self.assertEqual(result["agreed_counts_only"]["LILA"]["relevant_distinct_top20"], 1)
        self.assertEqual(result["status"], "PRELIMINARY_AI_REVIEW_COMPLETE")

    def test_unsupported_negative(self):
        def unsupported(n, card, claims):
            return [make_claim(card, "fit", False), make_claim(card, "grade", 0)]
        row = self.decision(self.fixture(override=unsupported))
        self.assertEqual(row["classification"], "unknown")
        self.assertEqual(row["decisive_predicates"], [])
        self.assertTrue(all(c["reason"] == "receipt_not_admitted" for c in row["support_outcomes"]))

    def test_binding_adversaries(self):
        for changes in ({"passage": ""}, {"passage": "Invented unsupported quote"},
                        {"span": [0, 1]}, {"span": [False, 2]}, {"subject_id": "OTHER"},
                        {"source_url": "https://sam.gov/opp/OTHER/view"},
                        {"published_at_utc": "2099-12-31T00:00:00Z"},
                        {"source_url": "https://sam.gov/opp/SYN-0/view?rank=1"}):
            with self.subTest(changes=changes), self.assertRaises((ValueError, KeyError)):
                self.fixture(pre=lambda p: self.mutate_evidence(p, **changes))
        for target in ("packets/reviewer_1/sources", "case/sources"):
            self.fixture(complete=False)
            path = next((self.root / target).glob("*.json"))
            path.write_bytes(path.read_bytes() + b" ")
            with self.assertRaises(ValueError):
                score_fixture(self.root)
        self.fixture(complete=False)
        self.change_review(lambda r: r.update(packet_sha256="0" * 64))
        with self.assertRaises(ValueError):
            score_fixture(self.root)

    def test_supported_negative_matrix(self):
        for response, continuation in itertools.product((True, False, "unknown"), repeat=2):
            row = self.decision(self.fixture(f"negative_{response}_{continuation}"))
            self.assertEqual(row["classification"], "not_relevant")
            self.assertEqual(row["states"]["response_open"], response)
            self.assertEqual(row["states"]["continuation_open"], continuation)
        for kind in ("positive", "expired", "continued", "closed"):
            result = self.fixture(kind)
            self.assertIn(self.decision(result)["action_state"], (True, False, "unknown"))

    def test_counting(self):
        rows = ([(0, "retrieved_new_run"), (0, "retrieved_new_run"), (1, "known_independently_retrieved"),
                 (2, "injected_or_retained_only"), (3, "unresolved"), (4, "retrieved_new_run")],
                [(1, "known_independently_retrieved")])
        def pre(case):
            pool = read(case / "pool.json")
            pool["cards"][4]["family_id"] = None
            put(case / "pool.json", pool)
        result = self.fixture(("positive",) * 5, rows=rows, pre=pre)
        counts = result["agreed_counts_only"]["LILA"]
        for key, value in {"relevant_distinct_top20": 2, "distinct_relevant_yield_at20": .1,
                           "known_retrieved_relevant_distinct_top20": 1, "unfilled_slots_top20": 14,
                           "duplicate_resolved_family_slots_top20": 1, "injected_or_retained_only_slots_top20": 1,
                           "unresolved_provenance_slots_top20": 1, "unresolved_identity_slots_top20": 1}.items():
            self.assertEqual(counts[key], value, key)
        key = read(self.root / "packets/PRIVATE_key.json")
        legacy = {bid: {"review_status": "judged", "grade": 2, "in_scope": True,
                       "current_buying_motion": True, "evidence_supported": True} for bid in key["ids"]}
        self.assertEqual(v3_metrics(key, legacy), result["agreed_counts_only"])
        result = self.fixture(("positive",) * 21,
                              rows=([*( (i, "retrieved_new_run") for i in range(21))], []))
        self.assertEqual(result["agreed_counts_only"]["LILA"]["relevant_distinct_top20"], 20)
        self.assertEqual(len(result["validated_reviews"]["reviewer_1"]), 20)
        self.assertEqual(result["agreed_counts_only"]["GovTribe"]["unfilled_slots_top20"], 20)

    def test_source_certification_failure(self):
        def pre(case):
            sweep = read(case / "native.json")
            sweep["results"]["sam_census"]["complete"] = False
            put(case / "native.json", sweep)
        with self.assertRaisesRegex(ValueError, "complete native extract census"):
            self.fixture(pre=pre)
        self.assertFalse((self.root / "result.json").exists())
        self.fixture(complete=False)
        # Scoring re-certifies case bytes, rather than trusting prepare's result.
        path = self.root / "case/native.json"
        path.write_bytes(path.read_bytes() + b" ")
        with self.assertRaises(ValueError):
            score_fixture(self.root)

    def test_claim_bound_negative(self):
        row = self.decision(self.fixture("negative"))
        self.assertEqual(row["decisive_claim_ids"], ["fit"])
        self.assertEqual(row["support_outcomes"][0]["support_status"], "supported")
        self.assertIn("response_open", row["unresolved_dimensions"])

    def test_untrusted_supported_flag(self):
        def claims(n, card, cs):
            return [make_claim(card, "fit", False) | {"supported": True}, make_claim(card, "grade", 0)]
        row = self.decision(self.fixture(override=claims))
        self.assertEqual(row["classification"], "unknown")
        self.assertIs(row["raw_judgment"]["claims"][0]["supported"], True)
        self.fixture(complete=False)
        registry = read(self.root / "trust/support.json")
        registry["accepted_receipts"] = []
        put(self.root / "trust/support.json", registry)
        self.assertEqual(self.decision(score_fixture(self.root))["classification"], "unknown")

    def test_conflicting_support_receipt(self):
        self.fixture(complete=False)
        self.change_review(lambda r: r["judgments"][0]["claims"][0].update(value=False))
        with self.assertRaisesRegex(ValueError, "context mismatch"):
            score_fixture(self.root)
        def both(n, card, cs):
            return [make_claim(card, "fit", True, tag="a"), make_claim(card, "fit", False, tag="b")][::1 if n == 1 else -1]
        row = self.decision(self.fixture("conflict", override=both))
        self.assertEqual(row["states"]["fit"], "conflicted")
        self.assertEqual(row["classification"], "unknown")
        # Cherry-picking only the negative does not erase the other admitted assessment.
        self.change_review(lambda r: r["judgments"][0].update(claims=r["judgments"][0]["claims"][1:]))
        self.assertEqual(self.decision(score_fixture(self.root))["states"]["fit"], "conflicted")
        def clock_conflict(n, card, cs):
            return cs + [make_claim(card, "response_open", False, tag="-contradiction")]
        row = self.decision(self.fixture("negative_clock_conflict", override=clock_conflict))
        self.assertEqual(row["classification"], "not_relevant")
        self.assertEqual(row["states"]["response_open"], "conflicted")
        result = self.fixture("conflict", override=lambda n, c, cs: [make_claim(c, "fit", n == 1)])
        self.assertEqual(self.decision(result, 1)["states"]["fit"], "conflicted")
        self.assertEqual(self.decision(result, 2)["states"]["fit"], "conflicted")
        self.assertEqual(len(result["disagreements"]), 1)

    def test_changed_support_policy(self):
        for field in ("brief_sha256", "objective_sha256", "support_policy_sha256", "family_identity_sha256"):
            self.fixture(complete=False)
            reg = read(self.root / "trust/support.json")
            old = reg["accepted_receipts"][0]
            receipt = read(self.root / "support" / (old + ".json"))
            receipt["context"][field] = "0" * 64
            # Even an admitted replacement must have a matching checker record
            # and the original frozen context, never only a matching filename.
            new = object_hash(receipt)
            put(self.root / "support" / (new + ".json"), receipt)
            reg["accepted_receipts"][0] = new
            put(self.root / "trust/support.json", reg)
            with self.subTest(field=field), self.assertRaises(ValueError):
                score_fixture(self.root)
        self.fixture(complete=False)
        expected = digest(self.root / "trust/support.json")
        reg = read(self.root / "trust/support.json")
        reg["snapshot_id"] = "replacement"
        put(self.root / "trust/support.json", reg)
        with self.assertRaisesRegex(ValueError, "registry snapshot"):
            score(self.root / "case", self.root / "packets", self.root / "trust/prepared.json",
                  self.root / "trust/support.json", expected, self.root / "support")

    def test_route_unknown(self):
        row = self.decision(self.fixture(override=lambda n, c, cs: [x for x in cs if x["predicate"] != "route_eligible"]))
        self.assertEqual(row["classification"], "unknown")
        self.assertEqual(row["states"]["route_eligible"], "unknown")
        row = self.decision(self.fixture("route_closed"))
        self.assertEqual(row["classification"], "not_relevant")
        self.assertEqual(row["decisive_predicates"], ["route_eligible"])

    def test_route_positive_qualification_open(self):
        row = self.decision(self.fixture())
        self.assertEqual(row["states"]["grade"], 2)
        self.assertEqual(row["classification"], "relevant")
        self.assertIn("qualification", read(self.root / "case/sources/source-0.json")["text"])

    def test_all_material_affirmatives(self):
        for omitted in ("fit", "response_open", "route_eligible", "evidence_sufficient", "grade"):
            row = self.decision(self.fixture(override=lambda n, c, cs: [x for x in cs if x["predicate"] != omitted]))
            self.assertEqual(row["classification"], "unknown", omitted)
        result = self.fixture(rows=([(0, "retrieved_new_run"), (0, "known_independently_retrieved")], []))
        self.assertEqual(result["agreed_counts_only"]["LILA"]["relevant_distinct_top20"], 1)

    def test_negative_assertion_discovery(self):
        row = self.decision(self.fixture("negative", pre=lambda p: self.mutate_evidence(p, authority="discovery_only")))
        self.assertEqual(row["classification"], "unknown")

    def test_positive_assertion_discovery(self):
        row = self.decision(self.fixture(pre=lambda p: self.mutate_evidence(p, authority="discovery_only")))
        self.assertEqual(row["classification"], "unknown")

    def test_cross_card_ref(self):
        self.fixture(("positive", "positive"), complete=False)
        packet = read(self.root / "packets/reviewer_1/cards.json")
        bad = packet["cards"][1]["evidence"][0]["evidence_id"]
        self.change_review(lambda r: r["judgments"][0]["claims"][0].update(evidence_refs=[bad]))
        with self.assertRaisesRegex(ValueError, "cross-card"):
            score_fixture(self.root)

    def test_cross_subject_inside_card(self):
        def pre(case):
            source = read(case / "sources/source-0.json")
            source["notice_id"] = "SYN-OTHER"
            source["source_url"] = "https://sam.gov/opp/SYN-OTHER/view"
            put(case / "sources/source-0.json", source)
        with self.assertRaisesRegex(ValueError, "identity/URL mismatch"):
            self.fixture(pre=pre)

    def test_unreadable_not_empty(self):
        for state in ("missing", "unreadable", "failed"):
            def pre(case):
                pool = read(case / "pool.json")
                pool["cards"][0]["evidence_gaps"] = [state + " statement of work"]
                pool["cards"][0]["evidence"][0]["retrieval_state"] = state
                put(case / "pool.json", pool)
            row = self.decision(self.fixture(pre=pre))
            self.assertEqual(row["classification"], "unknown")
            self.assertEqual(row["evidence_gaps"], [state + " statement of work"])
        def empty(case):
            pool = read(case / "pool.json")
            pool["cards"][0].update(evidence=[], evidence_gaps=["Verified complete source contains no supporting clause"])
            put(case / "pool.json", pool)
        self.assertEqual(self.decision(self.fixture(pre=empty))["classification"], "unknown")

    def test_objective_expiry_alternatives(self):
        for objective, kind, expected in itertools.product(
                ("respond_to_this_notice", "actionable_buying_now"), ("continued", "expired", "closed"), (None,)):
            wanted = "not_relevant" if objective == "respond_to_this_notice" or kind == "closed" else (
                "relevant" if kind == "continued" else "unknown")
            self.assertEqual(self.decision(self.fixture(kind, objective=objective))["classification"], wanted)

    def test_negative_preserves_unknown_status(self):
        result = self.fixture("negative")
        self.assertEqual(self.decision(result)["classification"], "not_relevant")
        self.assertIn("WITH_UNRESOLVED_ITEMS", result["status"])
        self.assertTrue(result["unresolved_card_ids"])

    def test_disagreement_supported_negative_vs_unknown(self):
        result = self.fixture("negative", override=lambda n, c, cs: cs if n == 1 else [])
        self.assertEqual(self.decision(result, 1)["classification"], "not_relevant")
        self.assertEqual(self.decision(result, 2)["classification"], "unknown")
        self.assertEqual(len(result["disagreements"]), 1)
        self.assertEqual(result["agreed_counts_only"]["LILA"]["unknown_relevance_slots_top20"], 1)
        self.assertEqual(result["adjudication"], "NOT_PERFORMED")

    def test_different_evidence_same_decision(self):
        def pre(case):
            pool = read(case / "pool.json")
            second = copy.deepcopy(pool["cards"][0]["evidence"][0])
            source = read(case / second["snapshot_file"])
            source["source_id"] = "alternate-original-record"
            source["text"] = ("SYNTHETIC SECOND CLAUSE. Supplier control software proposals are due September 15, 2026. "
                              "This purchase admits direct suppliers and partners. Its identified continuation is closed. "
                              "All requirements are available; investigate the qualification question before bidding.")
            second.update(evidence_id="alternate-reference", passage_id="alternate-passage", source_id=source["source_id"],
                          snapshot_file="sources/alternate.json", passage=source["text"], span=[0, len(source["text"])])
            put(case / second["snapshot_file"], source)
            pool["cards"][0]["evidence"].append(second)
            put(case / "pool.json", pool)
        def different(n, card, cs):
            return [make_claim(card, c["predicate"], c["value"], tag=f"-review-{n}", ref_index=n-1) for c in cs]
        result = self.fixture(pre=pre, override=different)
        self.assertEqual(result["disagreements"], [])
        self.assertNotEqual(self.decision(result, 1)["raw_judgment"], self.decision(result, 2)["raw_judgment"])

    def test_zero_credit_uncertainty(self):
        result = self.fixture(override=lambda n, c, cs: [])
        self.assertEqual(result["agreed_counts_only"]["LILA"]["relevant_distinct_top20"], 0)
        self.assertIsNone(result["overall_winner"])
        self.assertIsNone(result["agreed_counts_only"]["LILA"]["new_to_client_relevant_top20"])
        self.assertEqual(result["novelty_metric"], "WITHHELD_PARTIAL_BASELINE")

    def test_old_packet_rejected(self):
        for schema in ("discovery-review-packet.v3", "discovery-review-packet.v4"):
            def pre(case):
                lock = read(case / "lock.json")
                lock["schema"] = schema
                put(case / "lock.json", lock)
            with self.assertRaisesRegex(ValueError, "migration required"):
                self.fixture(pre=pre)

    def test_portable_exact_commit(self):
        # Published commit verification is a separate receipt, not fabricated by
        # this test. This checks a dependency-free CLI from the available tree.
        result = subprocess.run([sys.executable, "-m", "tools.benchmark_v5", "demo", str(Path(self.tmp.name).resolve() / "cli")],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertIs(payload["synthetic"], True)
        self.assertIsNone(payload["overall_winner"])

    def test_portable_relocation(self):
        original = self.fixture()
        target = Path(self.tmp.name).resolve() / "relocated"
        shutil.copytree(self.root, target)
        self.assertEqual(score_fixture(target), original)
        path = target / "packets/PRIVATE_key.json"
        key = read(path)
        key["families"] = {k: "replacement" for k in key["families"]}
        put(path, key)
        # A forged case-local seal provides no authority over external anchor.
        put(target / "packets/PRIVATE_seal.json", {"private_key_sha256": digest(path)})
        with self.assertRaises(ValueError):
            score_fixture(target)

    def test_role_label_boundary(self):
        result = self.fixture("negative")
        raw = json.dumps(result)
        self.assertNotIn("known_excluded_identity", raw)
        self.assertIn("S3-ROLE-007", raw)

    def test_strict_json_paths_and_types(self):
        for raw in ('{"a":1,"a":2}', '{"a":NaN}', '{"a":Infinity}'):
            with self.assertRaises(ValueError):
                loads(raw)
        for name in ("../escape", "/etc/passwd", "sources/../source", "./source", "sources//a", "a\\b"):
            with self.assertRaises(ValueError):
                safe_path(Path(self.tmp.name).resolve(), name)
        link = Path(self.tmp.name).resolve() / "link"
        link.symlink_to(Path(self.tmp.name).resolve(), target_is_directory=True)
        with self.assertRaises(ValueError):
            safe_path(Path(self.tmp.name).resolve(), "link/a")
        for value in (1, 0, None, [], "false"):
            with self.assertRaises(ValueError):
                self.fixture(override=lambda n, c, cs: [make_claim(c, "fit", value)])
        with self.assertRaises(ValueError):
            self.fixture(override=lambda n, c, cs: [make_claim(c, "grade", True)])

    def test_independence_and_no_trust_bootstrap(self):
        self.fixture(complete=False)
        reg = read(self.root / "trust/support.json")
        for replacement in ("synthetic-reviewer-1", "synthetic-operator-0", "synthetic-admitter", "synthetic-custodian"):
            changed = copy.deepcopy(reg)
            changed["issuers"]["synthetic-checker"]["principal"] = replacement
            put(self.root / "trust/support.json", changed)
            with self.assertRaises(ValueError):
                score_fixture(self.root)
        put(self.root / "trust/support.json", reg)
        local_anchor = self.root / "packets/self_anchor.json"
        shutil.copyfile(self.root / "trust/prepared.json", local_anchor)
        with self.assertRaisesRegex(ValueError, "outside"):
            score(self.root / "case", self.root / "packets", local_anchor, self.root / "trust/support.json",
                  digest(self.root / "trust/support.json"), self.root / "support")
        reg["issuers"] = {}
        put(self.root / "trust/support.json", reg)
        with self.assertRaisesRegex(ValueError, "issuer"):
            score_fixture(self.root)

    def test_route_action_and_grade_scope_binding(self):
        for scope_field, replacement in (("action_id", "unrelated-family-action"),
                                         ("allowed_routes", ["direct"]), ("offering_id", "different-offering")):
            self.fixture(complete=False)
            def edit(review):
                review["judgments"][0]["claims"][0]["scope"][scope_field] = replacement
            self.change_review(edit)
            with self.assertRaises(ValueError):
                score_fixture(self.root)
        self.fixture(complete=False)
        def edit_grade(review):
            next(c for c in review["judgments"][0]["claims"] if c["predicate"] == "grade")["rationale"] = "Unseen later prose"
        self.change_review(edit_grade)
        with self.assertRaisesRegex(ValueError, "context mismatch"):
            score_fixture(self.root)

    def test_revocation_requires_new_snapshot_and_preserves_old_result(self):
        original = self.fixture()
        path = self.root / "trust/support.json"
        registry = read(path)
        registry["revoked_receipts"] = {sha: "Explicit withdrawal of prior synthetic check" for sha in registry["accepted_receipts"]}
        registry["snapshot_id"] = "SYNTHETIC-snapshot-2"
        put(path, registry)
        later = score_fixture(self.root)
        self.assertEqual(self.decision(original)["classification"], "relevant")
        self.assertEqual(self.decision(later)["classification"], "unknown")
        self.assertNotEqual(original["evidence_hashes"]["support_registry"], later["evidence_hashes"]["support_registry"])

    def test_no_overwrite_and_blind_bundle(self):
        self.fixture(complete=False)
        with self.assertRaises(ValueError):
            prepare_fixture(self.root)
        for folder in (self.root / "packets/reviewer_1", self.root / "packets/reviewer_2"):
            text = "".join(p.read_text() for p in folder.rglob("*") if p.is_file())
            for private in ("GovTribe", '"rank"', '"family_id"', "synthetic-checker", "selected_slots"):
                self.assertNotIn(private, text)


if __name__ == "__main__":
    unittest.main()
