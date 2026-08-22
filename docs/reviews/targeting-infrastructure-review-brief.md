# CODEX REVIEW BRIEF · targeting infrastructure (Band 09 + Apollo supply lane)

You are reviewing infrastructure that is **not yet in production use**. No
client has been enriched beyond one proof run. The operator's instruction is
explicit: **do not enrich anything.** Harden the infrastructure, troubleshoot
it until it works, then it gets used. Treat every credit-spending path as
something to reason about and test, never to exercise.

## Where the work is

```
worktree : ~/lila-apollo-email
branch   : fable/apollo-email-path   (HEAD da9802c)
```

**The branch chain is four deep and unmerged**, which is itself part of what
you are reviewing:

```
14891b9  (base)
 └── fable/targeting-band          dfbca42   band 09 + contract v1.4
      └── fable/apollo-transport-proof  10b9615   recorded-response fixture
           └── fable/apollo-email-path  da9802c   THIS BRANCH
```

Meanwhile `fable/decision-engine` moved independently to `2d2ac77` (suite
integrity receipts in `tests/conftest.py`). File sets are disjoint so the
merges are clean, but nothing here has ever run under primary's newer
conftest, including its live-HTTP detector.

Read first, per the repo's own session protocol: `CLAUDE.md`,
`docs/CONVENTIONS.md`, `docs/CONTRACT_SURFACES.md`,
`docs/REPORT_CONTRACT.md` (§2/09 and amendment v1.4).

## What was built

| Module | Purpose |
|---|---|
| `agents/golden_press/targeting_rules.py` | T1/T2/T3 spec derivation from R1/R2/R4 decision rows; persona ladder loaded from data |
| `agents/golden_press/targets_store.py` | durable contact store, provenance law, CSV sequence export |
| `agents/golden_press/person_screen.py` | 8 screening rules, 1 reject, 7 flag |
| `agents/golden_press/phone_policy.py` | 4 phone types, classification receipts, render policy |
| `agents/golden_press/render.py` `_render_targeting` | Band 09 |
| `tools/apollo_targets.py` | search, enrich, the one network seam |
| `tools/apollo_credits.py` | retrieval proof, cost estimate, cap, spend record |
| `run_targets.py` | the sweep-class CLI |
| `data/reference/targeting_personas.json` | persona ladder + `person_locations` query bound |

Suite **4018 green** on this branch, ruff clean on every file the work owns
(three pre-existing `F841`s in `render.py` predate it).

## What is genuinely measured versus assumed

**Measured against live Apollo, 2026-08-06:**
- search costs 0 credits; match ~1/person synchronous; phone reveal ~8/person async
- Apollo does not bill a failed reveal (batch projected 40 dial, spent 32)
- one live run: 11 contacts, 10 mobiles, 1 direct line, 82 credits, balance 3887 → 3805
- 1 false positive in 12 records (Ukraine-based "IRS CIO", correct org id/name/domain)
- book: 63 specs across 20 distinct organisations, 7 clients

**Never exercised (this list is the point of the review):**
- **the whole `resolve()` -> `enrich()` -> store pipeline.** See the
  correction section below; the live store was hand-assembled.
- the entire REST path. `BULK_MATCH_URL` and `PROFILE_URL` have never been
  called; there is no `APOLLO_API_KEY`. Everything live went through the MCP
  connector, which is a *different transport*.
- `read_balance()` against a real endpoint
- any pass beyond the first
- the Target gate is **closed for all 7 clients**, so no path can run today

## THE CORRECTION THAT MATTERS MOST

**The `resolve()` → `enrich()` → store pipeline has never run end to end
against Apollo.** The one live store on disk
(`data/state/targets/red_hat.contacts.json`) was assembled by hand in an
ad-hoc script from connector responses and written with
`targets_store.write()`. Verified: its receipt is missing 14 keys that
`resolve()` emits (`endpoint`, `started_at`, `distinct_searches`,
`emails_resolved`, `enrichment_source`, `phone_reveal_requested`,
`spend_note`, ...), and **0 of its 11 rows carry a `screen` block**, which
`enrich()` always writes.

So Band 09 genuinely renders real, paid-for contacts and genuinely certifies
— that part is true and was demonstrated. But the *code path* that is
supposed to produce that artifact produced none of it. Every claim below
about the supply lane working should be read as "the pieces are built and
unit-tested", not "the lane has run".

## CONFIRMED DEFECTS — verify these, then look for what they imply

Each is verified on this branch at the line given. Defects 9-14 came from an
adversarial audit, not from me, and are the more serious set.

1. **The store cannot accumulate.** `targets_store` exposes `load`/`write`
   only; `write()` is an atomic wholesale replace. There is no merge, append
   or update. **A second pass destroys the first.** Since the operator's
   ruling is that passes are iterative and cumulative, this is a data-loss
   bug sitting directly under the stated workflow.

2. **`join_record_ids` is unioned across a dedupe group**
   (`tools/apollo_targets.py:268-286`). Identical searches collapse to one
   call, then every returned person is assigned the union of join ids from
   every spec in that group. A contact can therefore carry a record id that
   does not justify calling them, which is the JOIN LAW (contract §2/09)
   failing quietly rather than loudly.

3. **No pagination.** `page` is accepted by `search_payload` but `resolve()`
   never advances it, and `DEFAULT_PER_PAGE` is 10. The one real FBI search
   reported `total_entries: 104` and we saw 10. **We have never seen more
   than ~10% of any account's available population**, which materially
   weakens the "full coverage" the operator just made central.

4. **Silent tier default.** `tier_for_title` returns `sought[0]` for any
   title it cannot match (`targeting_rules.py:175`). An unrecognised title
   is presented as a confident tier rather than as unattributed.

5. **The screener is inert for 71% of the book.** S3 and S5 return `None`
   unless `target_org.kind == 'buying_component'`, and 45 of 63 specs target
   commercial resellers (T3 adjacency). The single REJECT rule is also
   miscalibrated there: its reasoning is about US federal service, which is
   meaningless for a reseller employee abroad.

6. **No rate limiting, retry, backoff or 429 handling** anywhere in the lane.
   Any HTTP error aborts a batch and the partial result is lost.

7. **Apollo sits outside test hermeticity.** The repo's conftest scrubs SAM
   keys but never `APOLLO_API_KEY`, never forces `LILA_ENABLE_APOLLO` off,
   and does not isolate `LILA_TARGETS_STORE_DIR`. A developer with a key in
   their environment could have a test spend real credits.

8. **Contact decay is unmodelled.** Apollo returns `last_refreshed_at` per
   record and the store keeps only our fetch date. One shipped row is ~350
   days stale while printing today's retrieval date.

9. **The credit cap under-enforces by 8x on the only class that burns.**
   `tools/apollo_targets.py:427` calls
   `estimate(..., credit_class="match", ...)` **unconditionally** — there is
   no branch on `reveal_phone_number`. So a 10-person reveal projects 10
   credits against a cap, when the true cost is 80 dial credits plus 10
   match. `apollo_credits.py` claims "a run whose projection exceeds the cap
   does not start"; for phone reveals that guarantee is false. The
   `direct_dial` class is defined, measured, asserted by a test, and **never
   passed by any production call site.**

10. **`spend.calls_made` accumulates triangularly.**
    `tools/apollo_targets.py:450` does `spend.calls_made += calls` *inside*
    the batch loop, adding the running total rather than 1. Three batches
    report 6 calls. The live run was 11 contacts, i.e. two batches, which is
    exactly the regime where this fires. No test covers multi-batch
    enrichment.

11. **Duplicate `contact_id`s are billed twice.**
    `tools/apollo_targets.py:424` builds the id list with no de-duplication,
    while `resolve()` appends one row per person per distinct search. A
    person answering two searches is sent to `bulk_match` twice and priced
    twice; the `by_id` dict then collapses the response, so the second
    credit buys nothing. red_hat's 8 overlapping federal components are
    precisely that regime.

12. **The phone-reveal path is unreachable by construction.**
    `_webhook_probe` (`tools/apollo_targets.py:381`) is the only prover of
    the retrieval path and **is never called from anywhere**. `enrich()`
    builds a fresh empty `RetrievalProof`, so `require()` always raises.
    `run_targets.py` has no `--reveal-phone` flag and never passes
    `reveal_phone_number`. Correct fail-closed posture, but it means the
    gate has never been exercised in the open position, and the 10 mobiles
    in the live store came from outside this code entirely.

13. **`admissible()` strips the `screen` block, so 7 of 8 rules are
    invisible.** `targets_store.py:136` rebuilds each row from a fixed key
    list; `screen` is not in it, so the block `enrich()` writes never
    reaches the render or the export. Seven of the eight screen rules can
    only ever FLAG, and `_render_targeting` surfaces only `screened_out`
    (REJECT) from the receipt. **Seven eighths of the screener's output is
    computed, stored, and then discarded before the page** — directly
    contradicting `person_screen.py`'s stated contract that a flag "renders
    a caution and keeps the person".

14. **The export's `Pass` column is permanently blank.**
    `targets_store.py:273` reads `row.get("pass_label")`, but the rows come
    from `admissible()`, which never copies it. Proven against the live
    store.

## Where I want your judgement, not just verification

1. **Store accumulation semantics.** What is the dedupe key across passes,
   what supersedes what, and how does a re-resolved person whose title
   changed get reconciled against a stored row a client may already have
   called? Is append-with-supersession right, or a generation model?

2. **What should "full coverage" mean?** The operator's ruling is every
   account, every pass, batching until relevance is exhausted. Given
   `total_entries: 104` on one account, does that mean paginate to
   exhaustion, or is there a defensible relevance floor? What ends a pass?

3. **Should the screener have a reseller rule set at all**, or is being inert
   for `paper_holder` correct by design? If a reseller-class screen is
   wanted, what would it even test?

4. **The join-union fix.** Preserve the dedupe (it is a real 3.2x saving) and
   still bind each person to only the specs that actually asked for them.

5. **Is the two-stage split right?** Match-then-screen-then-reveal costs ~1
   extra credit per person and saves 8 per rejection. Is there a better
   sequence, given the reveal is async and the match is not?

6. **Free contacts already on disk.** There is a claim from an audit pass
   that ~1,972 distinct `.gov`/`.mil`-verified people already exist in this
   repo's SAM POC and contact-graph data, an order of magnitude more than
   this paid lane's book ceiling. **Verify or refute that**, and if true, say
   whether the paid lane should be reprioritised behind harvesting it.

7. **The R2 join loss.** Book-wide, ~30 of 37 displacement alerts produce no
   spec (thinklogical alone: 20 alerts → 1 spec). Diagnose the cause and say
   whether recovering them is worth more than any enrichment. It costs zero
   credits.

## Fence

- **Do not spend Apollo credits.** No enrichment, no reveals, no live calls.
- Do not commit to `~/federal-sales-os` (primary). Work in your own worktree
  on your own branch; Tyler merges.
- Do not change bands 00-08, the report contract, or any operator gate.
- Do not weaken the provenance, join, or zero-live-calls-at-press laws. If
  you think one is wrong, argue it; do not quietly relax it.

## What to return

1. Confirm or refute each of the 14 defects, with `file:line`.
2. Anything I missed, ranked by what would cause a wrong claim to reach a
   client versus what is merely untidy.
3. Direct answers to the 7 judgement questions.
4. A recommended build order for hardening, with the reasoning, given that
   nothing is enriched yet and the operator wants the infrastructure proven
   before it is used.
5. Say plainly if you think the architecture is wrong. It is four branches
   deep and unmerged, and now is the cheapest moment to change direction.
