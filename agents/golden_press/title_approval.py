"""The title-set approval: the decision that could not exist until now.

WHY THIS IS A THIRD DECISION, not a leg on an existing one.

    1 Assess approval   "is this evidence sound"      exists, evidence-bound
    2 Target approval   "may we do outreach at all"   exists
    3 THIS              "may we spend against THIS
                         vocabulary"                  did not exist

Decisions 1 and 2 both PREDATE the titles. The Target gate is what unlocks
the lane that generates them, so neither can bind a set that did not exist
when it was signed. Verified on disk: target_approval.json carries client,
approved_at, approved_by and note. No evidence binding of its own, and no
title concept anywhere in it.

WHY IT IS NOT A THIRD LEG ON target_gate_status. That function is documented
as "the one gate derivation", has five production consumers (run_targets.py
and four call sites in ui/server.py), and two clients hold valid Target
approvals today. Adding a leg would RE-LOCK both of them and every future
client until a title set exists, which is a breaking change to a documented
door. It would also conflate two genuinely different questions: may we do
outreach, and is this vocabulary right.

So this gate is NARROWER and consulted only by the supply step.
target_gate_status is untouched, nothing currently unlocked becomes locked,
and supply still requires the Target gate first. Both must hold.

DRIFT RE-LOCKS WITH NO NEW CLICK, mirroring how evidence drift already
re-locks Produce. The approval binds the R7 fingerprint over the canonical
inference payload, so a changed prompt, schema, capability vocabulary,
organisation resolution or evidence row stales it. An operator approved a
specific vocabulary derived from specific evidence; when that changes, what
they approved no longer exists.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Optional

TITLE_APPROVAL_VERSION = "title_approval.v1.2026-08-06"


def approval_path(client_name: str, review_dir: Optional[str] = None) -> Path:
    """Mirrors agents.review._target_approval_path exactly.

    ONE SLUG FUNCTION, deliberately. The earlier review found two slug
    helpers in one tree producing files for the same client under different
    names; borrowing the review module's own helper rather than writing a
    second is the fix for that class, not a stylistic choice.
    """
    from agents.review import REVIEW_DIR, _artifact_slug

    root = review_dir or os.environ.get("LILA_REVIEW_DIR") or REVIEW_DIR
    return Path(root) / f"{_artifact_slug(client_name)}.title_approval.json"


def load_title_approval(client_name: str,
                        review_dir: Optional[str] = None) -> Optional[dict]:
    try:
        payload = json.loads(
            approval_path(client_name, review_dir).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return payload if isinstance(payload, dict) else None


def title_gate_status(client_name: str, *, fingerprint: str = "",
                      motion_id: str = "",
                      review_dir: Optional[str] = None) -> tuple:
    """(unlocked, problems) for ONE motion's title set.

    Fails closed in every direction that matters: no approval, a revoked
    approval, an approval for a different motion, or an approval whose
    fingerprint does not match the set about to be searched.
    """
    problems: list = []
    payload = load_title_approval(client_name, review_dir)
    if payload is None:
        return (False, ["no title set has been approved for this client; "
                        "approve the search vocabulary before supply runs"])
    if payload.get("revoked_at"):
        return (False, ["the approved title set was revoked on "
                        f"{payload.get('revoked_at')}"])
    approvals = payload.get("motions") or {}
    entry = approvals.get(motion_id) if motion_id else None
    if entry is None:
        return (False, [f"no approved title set for motion {motion_id!r}"])
    if entry.get("revoked_at"):
        return (False, [f"the title set for motion {motion_id!r} was revoked"])
    approved_fp = str(entry.get("fingerprint") or "")
    if not approved_fp:
        problems.append("the stored approval carries no fingerprint")
    elif fingerprint and approved_fp != fingerprint:
        problems.append(
            "the evidence, vocabulary or prompt behind this title set has "
            "changed since it was approved; re-inference and a fresh "
            "approval are required")
    if not (entry.get("titles") or []):
        problems.append("the approved title set is empty")
    return (not problems, problems)


def record_title_approval(client_name: str, *, motion_id: str,
                          fingerprint: str, titles: Any,
                          approved_by: str = "operator", note: str = "",
                          approved_at: str = "",
                          review_dir: Optional[str] = None) -> Path:
    """Persist one motion's approved vocabulary. Append-only across motions.

    NEVER called by an agent on its own initiative. This is the persistence
    owner for an explicit human act, the same posture as the Assess and
    Target approvals: the code records a decision, it does not make one.
    """
    from tools.artifacts import atomic_write_json

    path = approval_path(client_name, review_dir)
    payload = load_title_approval(client_name, review_dir) or {
        "client": client_name, "version": TITLE_APPROVAL_VERSION,
        "motions": {}}
    payload.setdefault("motions", {})[motion_id] = {
        "fingerprint": fingerprint,
        "titles": [str(t) for t in (titles or []) if str(t).strip()],
        "approved_by": approved_by,
        "approved_at": approved_at,
        "note": note,
    }
    payload.pop("revoked_at", None)
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(path, payload)
    return path


def revoke_title_approval(client_name: str, *, revoked_at: str,
                          motion_id: str = "",
                          review_dir: Optional[str] = None) -> Optional[Path]:
    """Stamp a revocation. NEVER deletes the audit event."""
    from tools.artifacts import atomic_write_json

    payload = load_title_approval(client_name, review_dir)
    if payload is None:
        return None
    if motion_id:
        entry = (payload.get("motions") or {}).get(motion_id)
        if entry is None:
            return None
        entry["revoked_at"] = revoked_at
    else:
        payload["revoked_at"] = revoked_at
    path = approval_path(client_name, review_dir)
    atomic_write_json(path, payload)
    return path


def supply_may_run(client_name: str, *, motion_id: str, fingerprint: str,
                   review_dir: Optional[str] = None) -> tuple:
    """BOTH gates, in order. The Target door first, then this one.

    This is the only function the supply step should consult. It never
    weakens target_gate_status and never unlocks outreach from a new path:
    a closed Target door still closes everything, exactly as today.
    """
    from agents.review import target_gate_status

    unlocked, problems = target_gate_status(client_name,
                                            review_dir=review_dir)
    if not unlocked:
        return (False, list(problems))
    return title_gate_status(client_name, fingerprint=fingerprint,
                             motion_id=motion_id, review_dir=review_dir)
