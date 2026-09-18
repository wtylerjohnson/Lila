# Apollo mobile path 063

Apollo person phones were being lost in three places: grouped provider arrays
were not parsed as phone entries, storage dropped provider verification fields,
and the saved-contact research adapter unconditionally assigned `phone=None`.
The repair carries typed mobile/direct channels, status, DNC and observation
evidence through enrichment, storage, research capture and the native target.
An email-only refresh preserves a saved mobile and its older observation date.
Search records with day-only observations retain that precision. A changed
adapter binding forces older captures to refresh on the next normal run.

Organization main lines, home numbers and unclassified numbers do not substitute
for a personal mobile/direct channel. DNC-listed and invalid numbers remain in
stored evidence but are excluded from displayed/selected channels. Missing
validation remains unknown. Provider contact data does not establish current
employment, opportunity ownership or buying authority. Model routing, reveal
authorization, source joins, qualification and release gates are unchanged.

## Supplied Veeam client deliverable

The user's edited September 17 HTML was preserved. A separate September 18
copy adds 25 contact rows and refreshes seven existing rows, including 23 newly
collected mobiles. The result has 62 contact rows across the original seven
opportunity sheets. Two DNC-listed batch records are excluded. Contacts sit
within the matching MDA, CBP, TSA and ICE opportunities. An existing published
office number is retained where Apollo returned no mobile.

Only the targeting snapshot and two contact-cell conditions changed: no null
email link, and no-mobile records correctly labeled. Reversing those exact
changes reproduces the original file bytes. Rendered non-contact text and
images match the original. Original design, portable text edits, sources,
filters, navigation and editing/export runtime are preserved.

Browser checks exercised source disclosures, phone links, agency and rep
filters, navigation, 390px layout and print media. Actual text editing, Save,
Download HTML, reopening, another edit, restoration and another download/reopen
all passed with 62 contacts retained. There were no browser page errors.
This is a narrow edit to the supplied client artifact, not a fresh pipeline
release or a claim of increased qualified-lead yield.

## Validation

- Final strict offline suite: 6,300 passed, 40 skipped,
  26 subtests passed, 22 warnings, 144.54 seconds.
- Focused path and compatibility checks: 90 passed. The 20 new cases cover
  enrichment through saved store, capture, bound target and rendered channel;
  grouped responses, duplicate types, DNC, invalid numbers, stale/future
  observations, day precision, email refresh and the existing suppression switch.
- Replayed all 34 retained Apollo results through the repaired parser without
  network calls: 23 selectable mobiles, two DNC mobiles withheld, nine records
  without a mobile. Selected numbers matched the retained provider values.
- Exact tested-file hashes, original/delivered artifact hashes, browser checks
  and aggregate results are in `VERIFICATION.json`. Raw provider data, contact
  values, HTML and screenshots stay in the local evidence package outside Git.

The first broad run passed before additional freshness/deduplication cases.
A duplicate-merge variable-shadowing error exposed by a new test was fixed;
the failure was deterministic and explained. Earlier passing broad runs are
not presented as verification of later edits. The final suite above covers
the frozen final code. No additional Apollo credits or model calls were used.
