"""THE canonical client-identity slug (consolidation, 2026-08-19).

One client identity, one slug. "JTG, inc." burned this in live: three
families minted three identities in one pipeline run (jtg__inc from
per-character replacement, jtg_inc from run-collapse, jtg-inc from the
watch's hyphen mint), breaking the intake-to-sweep seam and splitting
one client's candidate-review state across two directories.

client_slug is the only sanctioned derivation for anything keyed by
client identity: client dirs, review packets, sweeps, assess ledgers,
press snapshots, marks cache, runner artifacts, watch generations.

The legacy shapes remain importable HERE AND ONLY HERE, for read
fallbacks over artifacts minted before the alignment. New artifacts
always mint canonical; nothing renames history on disk.

Deliberately NOT consolidated, because they slug arbitrary labels and
never client identities: the golden-press hyphen token family
(authoring/motions/render/report_templates/studio record, report, and
spec-id tokens, where hyphens also keep spec ids from reading as
contract numbers) and the visual-contract space-matching normalizer.
"""

from __future__ import annotations

import re

_RUNS = re.compile(r"[^a-z0-9]+")


def client_slug(name) -> str:
    """Canonical: casefold, collapse every non-alphanumeric run to one
    underscore, strip edge underscores."""
    return _RUNS.sub("_", str(name or "").casefold()).strip("_")


def legacy_client_slug(name) -> str:
    """Pre-alignment per-character shape ("JTG, inc." -> jtg__inc).
    READ FALLBACKS ONLY: never mint new artifacts with this."""
    return "".join(
        c if c.isalnum() else "_" for c in str(name or "")
    ).strip("_").lower()


def legacy_hyphen_client_id(name) -> str:
    """Pre-alignment Candidate Review watch shape ("JTG, inc." ->
    jtg-inc). READ FALLBACKS ONLY."""
    return _RUNS.sub("-", str(name or "").casefold()).strip("-")
