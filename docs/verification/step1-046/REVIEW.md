# Step 1 source-binding repair for Fable recheck

Work: `REPAIR-STEP1-PUBLISH-AND-HARDEN-046`. Review branch:
`codex/step1-publish-harden`. This branch includes the previously unpublished
Step 1 commits `bd87ce7888386ebe3248fea4bc55646efe869994` and
`c15dd5e14568debf12740af8787e8eb467754f1b`, followed by the source-binding repair.
It is a review candidate, with no Fable acceptance or production merge implied.

The original Fable FAIL established that those Step 1 commits were unavailable
on GitHub. Its invented-opportunity probes ran on reachable main `f0f7aec` and
base `b6cba077`, not on the unpublished Step 1 commits. Subsequent local audits
independently reproduced four source-binding defects on actual `c15dd5e`.

The repair requires the owning original result records whenever a saved picture
is projected or rendered. Its saved registry, version and hashes cannot verify
themselves. Missing sources produce a named gap and withhold factual cards.
Whitespace-only or punctuation-only quotes fail verification. A notice cannot
borrow a source-specific route or other fact from another notice. Contradictory
primary identities or deadlines prevent current-notice confirmation. Display-time
currentness uses the current UTC clock. Both existing runners pass original
results to Markdown rendering; the command-line runner is exercised offline.

Review `tests/test_research_picture_hardening.py` for the hostile inputs and valid
controls, alongside `tests/test_research_picture_evidence.py` and
`tests/test_research_picture_ui.py`. The operator reproduced H1-H4, verified all
four fixes, found the missing command-line argument as H5, and verified that fix
through the actual `run_picture.main()` path with model and Assess calls mocked.

Run focused tests with the repository dependencies installed:

```sh
LILA_SUITE_OFFLINE=1 LILA_SUITE_STRICT=1 python -m pytest tests/test_research_picture.py tests/test_research_picture_evidence.py tests/test_research_picture_hardening.py tests/test_truth_purges.py -q
```

Native browser coverage is in `tests/test_research_picture_ui.py`; it uses a
temporary browser profile with page network requests blocked. Full local
regression used `LILA_SUITE_OFFLINE=1 LILA_SUITE_STRICT=1 python -m pytest tests/
-q -rs`: 5,413 passed, 40 skipped, 18 warnings. The earlier pre-final run had a
workstation selector timeout; both parameters passed a bounded retry and the
final full suite passed. No workstation code was changed. See `TEST_RESULTS.json`
for file/log hashes, explicit skips, and the distinction between local receipts
and independent verification.

Full regression restored 31 existing ignored fixture/brand files verified against
the original fixture manifest. They are not included in this review branch; a
cloud full-suite attempt must name absent fixtures or dependencies rather than
equate a reduced count with this local run. The focused source-binding fixtures
are tracked or synthetic. No live model composition, new product query or held-out
comparison was run. Saved calibration replay retained research signals and named
source gaps without producing qualified opportunities; source trace and report
lints passed. Citation binding does not establish semantic entailment, actual
client fit, qualification or seller readiness.

Calibration 005 remains frozen; case004 remains sealed incomparable with score
null. Strict Assess, profile, scope and release gates are unchanged. Step 2 is
suspended pending CoS-routed Fable acceptance of this exact published candidate.
