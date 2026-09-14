"""On-disk store for the contact graph.

Layout (data/state/contact_graph/ — a network asset, shared across clients):
    observations.jsonl      append-only source of truth; one ContactObservation/line
    index.json              derived profiles; fully rebuildable from observations
    merge_review.jsonl      ambiguous merge candidates awaiting human decision
    merge_decisions.jsonl   append-only HUMAN decisions on candidates (approve/
                            reject); resolve honors them on every rebuild

The store owns persistence only: appending observations (idempotently) and
reading/writing the derived index. Computing profiles from observations lives in
resolve.py; the store just persists whatever that produces. No external database.

Idempotency: the dedupe key is (source, notice_id, normalized person, channel
kind, channel value). Re-running a harvest or backfill never writes a row whose
key is already on disk — so an interrupted backfill can be re-run safely.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional

from tools.contact_graph.names import normalize_name
from tools.contact_graph.schemas import ContactObservation

# TODO(workspace): move to <LILA_WORKSPACE>/_shared/contact_graph/ once the
# workspace feature exists — contacts are network assets, not per-client data.
# Until then they live under data/state/ with the rest of the cross-run state.
DEFAULT_ROOT = Path(__file__).resolve().parents[2] / "data" / "state" / "contact_graph"

DedupeKey = tuple


def dedupe_key(obs: ContactObservation) -> DedupeKey:
    """Notice + person + channel — the idempotency key (spec: dedupe on
    notice ID + person + channel)."""
    return (
        obs.source,
        obs.notice_id,
        normalize_name(obs.person_name),
        obs.channel_kind,
        (obs.channel_value or "").strip().lower(),
        # Forecast role sightings remain distinct when one person fills two roles.
        *([obs.role_type, obs.observed_at] if obs.notice_type == "Forecast" else []),
    )


@dataclass
class AppendResult:
    written: int = 0
    skipped: int = 0  # already present (dedupe)

    @property
    def total(self) -> int:
        return self.written + self.skipped


class ContactGraphStore:
    def __init__(self, root: Optional[Path] = None) -> None:
        env = os.environ.get("LILA_CONTACT_GRAPH_DIR")  # test isolation hook
        self.root = Path(root) if root else (Path(env) if env else DEFAULT_ROOT)
        self.observations_path = self.root / "observations.jsonl"
        self.index_path = self.root / "index.json"
        self.review_path = self.root / "merge_review.jsonl"
        self.decisions_path = self.root / "merge_decisions.jsonl"
        self._keys: Optional[set[DedupeKey]] = None  # lazy cache of on-disk keys

    # --- observations -------------------------------------------------------
    def read_observations(self) -> list[ContactObservation]:
        if not self.observations_path.exists():
            return []
        out: list[ContactObservation] = []
        with self.observations_path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    out.append(ContactObservation.model_validate_json(line))
        return out

    def _load_keys(self) -> set[DedupeKey]:
        if self._keys is None:
            self._keys = {dedupe_key(o) for o in self.read_observations()}
        return self._keys

    def append(self, observations: Iterable[ContactObservation], *, dedupe: bool = True) -> AppendResult:
        """Append observations, skipping any whose key is already stored.

        Dedupe is also applied *within* the incoming batch, so the same POC seen
        twice in one sweep is written once.
        """
        result = AppendResult()
        keys = self._load_keys() if dedupe else None
        self.root.mkdir(parents=True, exist_ok=True)
        with self.observations_path.open("a", encoding="utf-8") as fh:
            for obs in observations:
                if dedupe:
                    key = dedupe_key(obs)
                    if key in keys:  # type: ignore[operator]
                        result.skipped += 1
                        continue
                    keys.add(key)  # type: ignore[union-attr]
                fh.write(obs.model_dump_json() + "\n")
                result.written += 1
        return result

    # --- derived index (rebuildable) ---------------------------------------
    def write_index(self, payload: dict) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        tmp = self.index_path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
        os.replace(tmp, self.index_path)

    def read_index(self) -> Optional[dict]:
        if not self.index_path.exists():
            return None
        return json.loads(self.index_path.read_text(encoding="utf-8"))

    # --- ambiguous-merge review queue --------------------------------------
    def write_review(self, candidates: Iterable[dict]) -> int:
        """Overwrite the review queue with the current ambiguous candidates.

        Rewritten (not appended) on each rebuild because it is derived from the
        current observation set, exactly like the index.
        """
        self.root.mkdir(parents=True, exist_ok=True)
        n = 0
        tmp = self.review_path.with_suffix(".jsonl.tmp")
        with tmp.open("w", encoding="utf-8") as fh:
            for c in candidates:
                fh.write(json.dumps(c, default=str) + "\n")
                n += 1
        os.replace(tmp, self.review_path)
        return n

    def read_review(self) -> list[dict]:
        if not self.review_path.exists():
            return []
        out = []
        with self.review_path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    out.append(json.loads(line))
        return out

    # --- human merge decisions (append-only, honored by every rebuild) ------
    def append_decision(self, decision: dict) -> None:
        """Record one human call on a review candidate:
        {"action": "approve"|"reject", "agency": str, "names": [normalized...]}.
        Append-only like observations — decisions are facts about what a human
        decided, and the graph never guesses past them."""
        self.root.mkdir(parents=True, exist_ok=True)
        with self.decisions_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(decision, default=str) + "\n")

    def read_decisions(self) -> list[dict]:
        if not self.decisions_path.exists():
            return []
        out = []
        with self.decisions_path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    out.append(json.loads(line))
        return out
