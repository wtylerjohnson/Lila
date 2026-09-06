"""Step 1 company mastery: name-only identity, dossier, yield, adversarial.

The existing intake composer, review packet, and decide()/client_files path
remain the persistence and approval owners. This package is the mastery
layer that must finish before opportunity identification or lead generation.
"""

from __future__ import annotations

AUTO_APPROVE_TOGGLE = "intake-auto-approve"
AUTO_APPROVE_ENV = "LILA_ENABLE_INTAKE_AUTO_APPROVE"

# Identity bind floor: a website guess below this never becomes the scrape
# target. Prevents wrong-company contamination from a weak directory hit.
IDENTITY_BIND_MIN_CONFIDENCE = 0.72
