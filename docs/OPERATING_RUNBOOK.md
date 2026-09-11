# LILA operating version

Before a build or release, read
[the current weekend build plan](plans/WEEKEND_BUILD_PLAN_2026-09-11.md).
Record its plan ID/hash, work ID, checkout and exact code revision with the
existing run evidence. Check assigned ownership and required acceptance before
starting work. The plan preserves the separate frozen benchmark and its repair
sequence; it does not claim that a development repair has shipped on main.

The operating checkout is `/Users/wtjohnson/Lila`. Other named worktrees are
preserved development or historical lanes; do not start the operating UI there.

Start with `/Users/wtjohnson/Lila/LILA.command`, or from this checkout with
`.venv/bin/python run_ui.py`. The default local
Command Center is http://127.0.0.1:8321. Check the checkout with
`git -C /Users/wtjohnson/Lila log -1 --oneline` before diagnosing a run.

Client releases use Command Center `POST /api/run` with `client_name`,
`step: "lila_release"`, and `args: {}`. Poll `/api/job/<id>` and inspect the
returned manifest. A completed job alone is not proof of a release.

Do not copy approval flags between clients or change engagement scope to get
a green release. Preserve immutable Assess runs and release directories.
A narrowed priority view must retain the underlying assessment, dispositions,
rejection reasons and evidence. Investigation priority does not certify a bid.

Verification: `LILA_SUITE_OFFLINE=1 LILA_SUITE_STRICT=1 .venv/bin/python -m pytest tests/ -q`; inspect the generated
HTML and bundle manifest for both Arista and Apex. Their real inputs provide
acceptance evidence distinct from offline fixtures.
