# Step 2 capability alignment and rejection audit

Work: `REPAIR-STEP2-IMPLEMENT-045`. The existing draft was preserved in a local
stash and applied onto accepted Step 1 commit
`600a3e210844610a8f407fc0684d40c104bf8134`. The Step 1 Fable verdict was
`PASS_WITH_RESIDUALS`; citation binding still does not establish entailment or
qualification. This Step 2 branch is a candidate for its own Fable review.

Previously, retrieval and capability screening used different literal vocabularies,
and all 356 calibration records received the same no-core rejection. The revised
Apex definition maps all 14 approved retrieval phrases: 10 supported and 4 explicitly
exploratory. Twelve reviewed buyer-language aliases map to canonical concepts and
score each concept once. Generic recovery-audit wording requires supplier/AP context
within the same bounded sentence. Exploratory terms do not become strict matches.

The matcher retains exact source fields, spans, canonical identity and company
evidence IDs. Retrieval additions and screening come from this same versioned
definition. Existing operator terms remain present and unmapped terms are disclosed;
operator scope, code boundaries and scoring thresholds are unchanged. The public
company-source references and evidence basis are in the taxonomy. They support
offering vocabulary, not the fit or buying status of a particular federal notice.

The prefilter now distinguishes weak fit evidence, text gaps, attachment gaps,
existing exclusions, supported capability awaiting judgment and historical awards.
Awards stay historical even with future deadlines. An incomplete record does not
automatically create a model call. Attachment receipts preserve unattempted, failed,
stale, verified-empty, unavailable-text and discovery-text states. Even nonmatching
captured text retains a bounded audit receipt. Discovery text does not approve an
Assess requirement or promote a lead.

Separately labeled C1 contract extension: an evidenced current-taxonomy alias can
support the existing client-relevance basis while retaining its canonical CORE
identity. Both the quoted guard context and the current stored source must reproduce
the match. Stale taxonomy, missing subject and unmapped phrases refuse. Existing
canonical quote formatting, closed basis schema and release gates remain unchanged.
Review this diff independently in `agents/reports/composer.py`,
`docs/CONTRACT_SURFACES.md`, and `tests/test_client_relevance_alias_contract.py`.
Its implementation is covered by the user's weekend auto-passthrough; it remains
subject to Fable acceptance before advancement.

The prespecified synthetic acceptance plan contains 20 development cases, including
positive supplier-master/AP synonyms, unrelated patient/API/projector cases,
duplicate aliases, split fields, awards, scope and missing-source controls. These
are software tests, not an independent recall holdout. `tests/test_capability_alignment.py`
also exercises actual mocked native-query and attachment-producer paths.

```sh
LILA_SUITE_OFFLINE=1 LILA_SUITE_STRICT=1 python -m pytest tests/test_capability_alignment.py tests/test_relevance_engine.py tests/test_relevance_taxonomy.py tests/test_triage_prefilter.py tests/test_sam_attachment_text.py tests/test_term_yield.py tests/test_sam_extract_source_receipt.py tests/test_client_relevance_alias_contract.py tests/test_research_picture_hardening.py -q
```

The local frozen-input development replay retains all 356 native positions and
identities: 305 weak/no-supported-fit, 50 historical awards, 1 missing-text gap,
and 0 model candidates. No newly recovered opportunity is asserted. The two
recovery-audit phrase hits are awards. The older 9 supplier-risk/4 recovery-audit
term-yield counts come from an accumulated unfiltered store, whereas the final
daily extract contains 0/2; they are different populations, not demonstrated losses.

The audit CLI validates source SHA, record hashes and native-order identity against
its declared baseline, and writes only to an explicit output directory:
`python -m tools.relevance.audit_saved_screen --help`.

Final local results and exact file hashes are in `LOCAL_VERIFICATION.json` beside
this guide. Full regression uses offline strict mode and 31 existing ignored
baseline fixtures. Those fixtures and the original frozen captures are local and
are not included in this branch. A cloud review must disclose missing fixtures and
its actual test count rather than claim parity with the producer's full suite.

Buyer requirement versus boilerplate adjudication remains Step 3. Capability
matching alone does not prove a requested deliverable, current opportunity or
seller readiness. Case004 and case005 remain frozen; no production merge, live
model composition, client release, new competitive capture or paid enrichment was
performed. CoS routes this handoff to Fable before Step 3 starts.
