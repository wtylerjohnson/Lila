# QBE exemplar gate: FAILED the threshold, item 3 not built (R2, 2026-08-18)

Measured on the full 360,384-vector sidecar (bge-base-en-v1.5, doc-to-doc
cosine, artifact `data/state/retrieval/qbe_probe.json`): **mean
cross-family fixture rank 97,189; zero of twenty cross-family pairs
inside the top 50** (best single pair: IATDR finds DTRA DevOps at 489).
The gate said build `exemplar_retrieve` only if fixtures find each other
inside the top 50, so it is not built.

Why it fails, in one paragraph: a client's interest set spans
semantically DISJOINT programs, and doc-to-doc cosine is faithful to
that disjointness. A NASA space-network RFI (UNNO) and a VA Ansible
sources-sought (IATDR) share a commercial thesis ("Red Hat can win
this") but almost no semantic content, so no embedding that is honest
about meaning will rank them as neighbors; what unites them lives in
the CLIENT, not in the text, which is exactly the signal a trained
ranker (or right-swipe labels) carries and an untrained cosine cannot.
The same probe shows where notice-as-query IS strong: each exemplar's
OWN neighborhood is the best dense output measured all day. UNNO's
neighbors are the NASA network-procurement cluster (the UN2O successor
sources-sought at 0.904, Goddard UNNO, the NExUS family, Space Data
Network MOC, Mars Telecom Network), and IATDR's are the entire VA OIT
DA01 pipeline (Enterprise IT Logging RFI, Vulnerability Management,
Enterprise AI, ITSM ELA, Data Center Modernization), close to fully
genuine on hand review; thin-text exemplars (DTRA DevOps, a boilerplate
sources-sought) drift generic. So per-exemplar NEIGHBORHOOD EXPANSION
(max-over-exemplars, each exemplar surfacing its own cluster) would
likely earn its keep for right-swipes even though exemplars never find
each other, but that is a different gate than the one ordered, and it
stays unbuilt until the operator re-gates it.
