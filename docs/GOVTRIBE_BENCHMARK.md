# Temporary GovTribe benchmark, September 8, 2026

GovTribe is a temporary research and comparison tool. LILA owns discovery,
qualification, evidence, contact identity, and reports. No release requires a
GovTribe connection. This records observed product behavior and our proposed
implementation; GovTribe's internal algorithms have not been inspected.

## Observed in the signed-in account

- The saved Apex opportunity query returned 411 results. Match explanations
  separate notice descriptions, government files, and generated summaries.
  The same query surfaced school courseware and pharmaceutical disposal for
  `credit recovery`: a text hit is not product fit.
- [IRS/Treasury contacts](https://govtribe.com/opportunity/federal-contract-opportunity/rfi-fraud-detection-and-payment-integrity-tools-2032l226n00008/contacts)
  displays six references to two distinct people. Each appears as a notice
  POC, in the description, and in a government file. Retain every reference;
  deduplicate the person. Contact-profile activity dates are not dates of
  renewed opportunity activity.
- [Jason Schofield](https://govtribe.com/contact/jason-dot-schofield-treasury-dot-gov)
  links contact identity to six opportunities, eleven awards and one file.
  Job title and telephone are absent. Linked activity does not by itself
  establish ownership of a new procurement or a follow-on to the closed RFI.
- [Ginnie Mae](https://govtribe.com/opportunity/federal-contract-opportunity/third-partyvendor-risk-management-vrm-program-86615526q00010)
  shows a past-due notice without linked forecast, award or vehicle. Our
  separately sourced HUD forecast remains evidence; a missing provider link
  is a coverage gap, not proof of absence.

## First reproducible LILA gap

`tools/api/sam_notice_family.py` admits RFIs and industry days as special
notices but excludes the Army's `Call for Solutions` title. That shared
classifier feeds both attachment candidates and report selection. Extend
its explicit title pattern to calls for solutions. Keep ordinary relevance,
scope, date, duplicate, triage and lead-qualification checks in their owners.
This repairs candidate eligibility; it does not certify product fit or a bid.

## Replacement sequence

1. **Source completeness:** build from SAM notice detail, attachment inventory
   and locally extracted passages. Existing owners are
   `tools/api/sam_notice_detail.py` and `sam_attachment_text.py`. Measure
   notice-versus-file discovery and retain retrieval time and content hash.
2. **Person and reference graph:** use email/agency identity with separately
   stored references to notice fields, passages and awards. Keep a source
   date per relationship. Extend existing targeting and evidence stores;
   never infer authority from a shared name or count references as people.
3. **Procurement lifecycle joins:** bind amendments, forecasts, awards,
   modifications and vehicles by explicit identifiers first. Existing public
   adapters cover SAM, HUD forecasts, FPDS, USAspending and subawards.
   Similar title/agency matches remain candidate joins until verified.
4. **Product-fit filtering:** use the company dossier and buyer requirement
   passages. Keep rejection reasons for ambiguous vocabulary, excluded work,
   route gaps and expired response windows. Narrow the priority view only.
5. **Independent acceptance:** reproduce the Army, IRS/Treasury and Ginnie
   packets from original sources with GovTribe unavailable. Measure record
   recall, unique contacts, contact-reference coverage, current status,
   verified incumbent/vehicle joins, false positives and report completeness.

The temporary benchmark may identify a missing record or relationship. Promote
it only after retrieving the supporting original source or clearly labeling
provider-only evidence. GovTribe summaries and predicted relationships do not
become verified facts. Exit the temporary dependency when the public-source
replay preserves every material claim and contact route in the reviewed cases.

## Verification

From a development checkout using the operating runtime:

```
LILA_SUITE_OFFLINE=1 LILA_SUITE_STRICT=1 /Users/wtjohnson/Lila/.venv/bin/python -m pytest tests/test_sam_extract.py tests/test_composer.py -q
```

Run the full suite before merge and use Command Center for final releases.
