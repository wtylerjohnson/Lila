# September 18 integration verification

The user approved sending the prepared Veeam inputs through Claude Max and requested commit, merge and push. Runtime implementation is commit `95c0d203476e4cb5ce0a243e96ab33b7e527cda9`; it was fast-forwarded into operating main. No model route was changed. The proposed later OpenAI switch is explicitly deferred.

The initial operating smoke reproduced three test-isolation failures. The mocked sweep writer did not persist a fixture, so the qualification companion read a pre-existing operating NETSCOUT artifact at the same filename. Its diagnostic write replaced the test's captured payload. The fix binds the existing offline runner helper to its temporary fixture directory and asserts that the emitted path stays there. Runtime qualification behavior and assertions about source coverage, packet identity and resume behavior remain unchanged.

A sandboxed full rerun failed eight Chromium/PDF launch tests and skipped 74 additional browser checks. Every failure contained a local browser launch error. The unchanged rerun with local browser execution permission passed: **6,280 passed, 40 skipped, 22 warnings, 26 subtests passed in 145.69 seconds**. Receipt: `strict-prepush-browser-20260918.xml`; original failure logs are retained locally. Tests measure regression behavior, not increased lead yield.

The live Command Center validation is job `98ef02888b2f`, using the September 18 full SAM extract. Fresh lead counts and report verification are recorded after that job finishes. All four named dirty operating files remained byte-identical after the initial merge and smoke attempt. Saved Veeam research inputs were installed locally, excluded from Git, without overwriting an existing input.

The RESULTS payload description now distinguishes the legacy Apollo saved-target adapter (`phone=None`) from the verified-contact supplement and public source passages. Setting the contact field to null does not redact phone values from every evidence path.

The user additionally authorized Apollo mobile collection. Saved contacts were searched and a relevant 34-person reveal batch prepared. Automatic approval review rejected the paid reveal until exact batch/credit approval; no paid enrichment was submitted. This does not block shipping the tested code. No outreach or client release is authorized by this integration receipt.
