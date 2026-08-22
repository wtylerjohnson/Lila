# Client Flow Refinement — intake → strategy → scope → search → review → assess
2026-07-09 · Tyler's spec, decisions locked: antagonist DORMANT-READY (activates
when OPENAI_API_KEY lands in .env) · iterate loop offers BOTH re-screen (free)
and re-search (SAM quota, labeled) · near-miss retrofit: Osprey + new clients.

## The flow (as specced)
1. INTAKE (auto, exists): form + parallel site scrape + company web search →
   engine infers keywords + NAICS.
2. NEW — inference returns PICKS **and NEAR-MISSES** ("close but didn't make
   the cut", each with a reason) persisted on the review packet.
3. ANALYST LAYER (strategy gate): intuitive workshops for pursuit strategy,
   keyword vocabulary, NAICS boundary, agency notes, set-asides, and search inputs
   (server: /api/strategy/revise, EDITABLE_FIELDS already whitelists these);
   near-miss tray renders "considered but cut" terms, one click promotes.
4. LOCK terms → SCOPE pick: All agencies (default) or specific agencies
   (multi-select using the agency picker). Persists as packet.search_scope.
5. CONFIRM terms+scope → sweep runs with a LIVE per-source activity board
   (rows light as each source's code executes; parsed from job lines).
6. REVIEW ↔ REFINE loop: term chips editable at review; two cost-labeled
   buttons — "Re-screen (no quota)" (re-triage existing haul, run_picture)
   and "Re-search (spends SAM quota)" (fresh sweep). Iterate freely.
7. GENERATE ASSESSMENT: compose + ARBITER PANEL (agents/decisions/arbiters.py,
   exists) ported to the assessment path — consensus required; OpenAI
   antagonist dormant until keyed. Per-arbiter results land in internal QA
   only (white-label holds client-side).
8. NAMING: all-scope renders plain "Federal Opportunity Assessment" (no "all
   agencies" label — that IS the assumption). Only agency-scoped reports carry
   the focus designator (agency-focus machinery from 71c17b4 reused).

## Build order (small green commits)
A. Candidate inference: schema TermCandidate{value,kind,category,reason,
   confidence,cut}; profile layer returns picks+near-misses; persists on
   packet as term_candidates. Re-infer Osprey ADDITIVELY (locked strategy
   untouched; tray is new data). [tomorrow-critical]
B. Strategy gate editor v2 (UI): chip editor + near-miss tray + promote.
   [tomorrow-critical]
C. Scope: packet.search_scope {all}|{agencies:[...]}; gate UI multi-select;
   run_searches honors scope (alias post-filter pre-triage, artifact tagged);
   report naming follows artifact scope automatically.
D. Live search board (UI): parse fan-out job lines → per-source spinner rows.
E. Review-iterate: term-delta editor + the two buttons at the review step.
F. Arbiter port: panel audits composed assessment content vs FactPack;
   violations → one revision pass; results → internal QA table. Dormant
   OpenAI arbiter activates via .env (two lines, per arbiters.py docstring).
G. congress_gov adapter (key in .env): bills/appropriations by capability
   terms → sweep + refresh + horizon bank kind "appropriations".

## Standing constraints
No invented data · white-label client-side (arbiter names internal-only) ·
counts through counts() · em-dash ban · client-bleed gate · small commits,
suite green each. Osprey report ships TOMORROW — A+B first.
