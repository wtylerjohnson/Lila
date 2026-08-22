# LILA Roadmap — widening the gap over a bare Claude search

Ordered by expected capture value. Items graduate off this list into
API_SETUP.md (sources) or the changelog (features) when shipped.

0. ~~**Deadline-aware depth**~~ — SHIPPED 2026-07-03: pursuit dossiers on the
   pursue-grade shortlist, budgeted deep-fetch (LILA_DEEP_BUDGET, default 8),
   permanent notice cache, most-urgent-deadline-first.
1. **Cross-model verification** (key budget in progress — Tyler) — run the triage verdicts and research-picture
   claims past a second screen (different model family, or same family at a
   different tier) and flag disagreements for human review. Disagreement rate
   becomes a per-run confidence metric printed in the provenance block. The
   deterministic verify_numbers pass stays; this adds judgment-level checking
   on pursue/discard calls, where a wrong discard silently costs a deal.
2. **Cross-run memory (deltas)** — diff each sweep against the previous one:
   NEW notices, verdict changes, approaching deadlines, fresh recompetes.
   "What changed this week" is the report clients renew for.
3. **Dollar joins** — attach USAspending award amounts to every recompete and
   incumbent so the brief anchors dollars without manual lookup.
4. **Congress.gov key** (2-min signup) — appropriations/NDAA line items as
   funding-proof citations.
5. **USAJOBS key** (2-min signup) — agency hiring posts as 6-12-month-early
   buying signals.
6. **Apollo retrieval loop** — close Target stage from handoff file to actual
   contact retrieval inside LILA.
7. **Eval harness** — score research pictures against what actually got
   pursued/won; feeds prompt tuning with ground truth.
8. **Paid-API tier** (Keith case pending head-to-head eval) — GovWin /
   HigherGov / GovTribe for forecast pipelines and pre-RFP intel the free
   stack cannot see.
9. **TODO: Contact extraction from solicitation attachments (SOW PDFs)** —
   phase two of the contact graph; requires download pipeline and quota
   guards. Phase one (shipped on feature/contact-graph-13a) covers structured
   POC fields and notice description text only.
