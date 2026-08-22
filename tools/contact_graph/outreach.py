"""Outreach list — the operator's working shortlist over the contact graph.

The graph records what official publications said (append-only, source-graded).
This module owns the OPERATOR layer on top of it: a single ordered list of
people worth reaching out to, grown automatically from research passes and then
curated by a human — reordered, pruned, hand-corrected, enriched.

Hard boundary: enriched or hand-entered channels live HERE (overrides on a list
entry), never in observations.jsonl. The graph stays official-capacity-only;
the list is a working asset. Deleting an entry tombstones it so the next
research pass doesn't resurrect what the operator already rejected — and
tombstones follow approved merge aliases, so a person deleted under one
published name spelling stays deleted after the graph merges the spellings.

Concurrency: the Flask dashboard and CLI research passes mutate the same file,
so every read-modify-write holds an exclusive flock on a sidecar lock file.
Growth does its expensive derivation outside the lock and re-loads inside it.

Storage: outreach.json next to the graph store (data/state/contact_graph/),
so LILA_CONTACT_GRAPH_DIR isolates it in tests along with everything else.
The research artifacts it ranks against default to the repo data/ dir,
overridable with LILA_RESEARCH_DATA_DIR (test isolation).
"""
from __future__ import annotations

import fcntl
import glob
import json
import os
from contextlib import contextmanager
from datetime import date, datetime
from pathlib import Path
from typing import Any, Optional

from tools.contact_graph.names import normalize_name
from tools.contact_graph.resolve import derive_profiles
from tools.contact_graph.schemas import ContactProfile
from tools.contact_graph.store import ContactGraphStore

_DATA_DIR = Path(__file__).resolve().parents[2] / "data"
_GRADE_SCORE = {"A": 3, "B": 2, "C": 1}

ENRICHMENT_SPEC_INSTRUCTIONS = (
    "Execute in a Cowork session with the Apollo MCP connected: for each person in "
    "'people', enrich ONLY the channels listed in 'need' (apollo_people_match / "
    "apollo_mixed_people_api_search by name + organization). Do not overwrite "
    "channels already present. Produce a JSON array of {name, agency, email, phone} "
    "rows; the operator drags that file onto the outreach rail to merge it back. "
    "Enriched data stays in the outreach list — it is never written into the "
    "contact graph, which holds official-publication observations only."
)


class OutreachReadError(ValueError):
    """The curated outreach file is unreadable and was left untouched."""


def _s(value: Any) -> str:
    """String-coerce untrusted input (numbers from spreadsheet-exported JSON,
    None, whatever) so no caller path hits .strip() on a non-string."""
    if value is None or isinstance(value, bool):
        return ""
    return str(value).strip()


def _entry_id(normalized: str, agency: Optional[str]) -> str:
    return f"{_s(agency).lower()}||{normalized}"


def _best_grade(p: ContactProfile) -> int:
    return max((_GRADE_SCORE.get(c.grade, 0) for c in p.channels), default=0)


def _channel(p: Optional[ContactProfile], kind: str) -> Optional[dict]:
    if not p:
        return None
    best = None
    for c in p.channels:
        if c.kind == kind and (best is None or _GRADE_SCORE.get(c.grade, 0) > _GRADE_SCORE.get(best.grade, 0)):
            best = c
    if best is None:
        return None
    return {"value": best.value, "grade": best.grade,
            "last_observed": best.last_observed.isoformat() if best.last_observed else None,
            "source_url": best.source_url}


def _research_data_dir(data_dir: Optional[Path]) -> Path:
    if data_dir:
        return Path(data_dir)
    env = os.environ.get("LILA_RESEARCH_DATA_DIR")
    return Path(env) if env else _DATA_DIR


def load_triage_verdicts(data_dir: Optional[Path] = None) -> dict[str, str]:
    """notice_id -> strongest verdict seen for it across every client sweep."""
    data = _research_data_dir(data_dir)
    rank = {"pursue": 3, "monitor": 2, "discard": 1}
    out: dict[str, str] = {}
    for f in sorted(glob.glob(str(data / "cleaned" / "searches_*.json"))):
        try:
            doc = json.load(open(f, encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        triage = (doc.get("results") or {}).get("triage")
        if not isinstance(triage, dict):
            continue
        for nid, v in triage.items():
            verdict = (v or {}).get("verdict") if isinstance(v, dict) else None
            if verdict in rank and rank.get(verdict, 0) > rank.get(out.get(nid, ""), 0):
                out[nid] = verdict
    return out


def _merge_alias_names(decisions: list[dict]) -> dict[tuple[str, str], set[str]]:
    """(agency_key, normalized_name) -> the transitive set of normalized names
    a person is known by via APPROVED merge decisions. After the graph merges
    'sam vale' and 'sam a vale', an outreach entry (or tombstone) recorded under
    either spelling must keep matching the merged profile."""
    groups: dict[str, list[set[str]]] = {}
    for d in decisions or []:
        if d.get("action") != "approve":
            continue
        ak = _s(d.get("agency")).lower()
        names = {normalize_name(_s(n)) for n in d.get("names") or []}
        names.discard("")
        if len(names) < 2:
            continue
        merged = [g for g in groups.get(ak, []) if g & names]
        for g in merged:
            names |= g
        groups[ak] = [g for g in groups.get(ak, []) if not (g & names)] + [names]
    out: dict[tuple[str, str], set[str]] = {}
    for ak, sets in groups.items():
        for g in sets:
            for n in g:
                out[(ak, n)] = g
    return out


class OutreachList:
    """Ordered, curated shortlist. Array order IS display order."""

    def __init__(self, store: Optional[ContactGraphStore] = None) -> None:
        self.store = store or ContactGraphStore()
        self.path = self.store.root / "outreach.json"
        self.lock_path = self.store.root / ".outreach.lock"

    # ---- persistence -------------------------------------------------------
    @contextmanager
    def _locked(self):
        """Exclusive cross-process lock for every read-modify-write. The UI
        server and CLI research passes share this file; without it a growth
        pass could silently clobber a concurrent delete/reorder."""
        self.store.root.mkdir(parents=True, exist_ok=True)
        with open(self.lock_path, "w") as lf:
            fcntl.flock(lf, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(lf, fcntl.LOCK_UN)

    def load(self, *, recover_corrupt: bool = True) -> dict:
        if not self.path.exists():
            return {"version": 1, "entries": [], "removed": []}
        try:
            doc = json.loads(self.path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            # Never let one corrupt read silently wipe the operator's curation:
            # mutation workflows sideline it for recovery. Observational GETs
            # request recover_corrupt=False and leave every byte untouched.
            if not recover_corrupt:
                return {"version": 1, "entries": [], "removed": [],
                        "read_error": f"invalid outreach JSON: {exc}"}
            aside = self.path.with_name(
                f"outreach.corrupt-{datetime.now().strftime('%Y%m%d-%H%M%S')}.json")
            os.replace(self.path, aside)
            return {"version": 1, "entries": [], "removed": [],
                    "recovered_from": str(aside)}
        doc.setdefault("entries", [])
        doc.setdefault("removed", [])
        return doc

    def _save(self, doc: dict) -> None:
        self.store.root.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(doc, indent=2, default=str), encoding="utf-8")
        os.replace(tmp, self.path)

    # ---- growth from research ---------------------------------------------
    def grow_from_research(self, *, now: Optional[date] = None,
                           data_dir: Optional[Path] = None) -> dict:
        """Append newly outreach-relevant profiles from the research so far.

        Relevance, most to least: seen on a pursue-grade notice; on a monitor
        notice; untriaged but carrying a fresh (A/B) channel. Contacts whose
        notices research explicitly discarded — and profiles the operator
        already deleted, under ANY merged alias — never (re)enter. Existing
        order is untouched; new arrivals append at the end in relevance order.
        """
        now = now or date.today()
        # expensive derivation OUTSIDE the lock…
        decisions = self.store.read_decisions()
        profiles, _ = derive_profiles(
            self.store.read_observations(), now=now, decisions=decisions)
        verdicts = load_triage_verdicts(data_dir)
        aliases = _merge_alias_names(decisions)

        # …mutation INSIDE it, against a fresh load.
        with self._locked():
            doc = self.load()
            have = {e["id"] for e in doc["entries"]}
            tombstones = {tuple(t) for t in doc["removed"]}

            def known(p: ContactProfile) -> bool:
                ak = _s(p.agency).lower()
                names = aliases.get((ak, p.normalized_name), {p.normalized_name})
                return any(_entry_id(n, p.agency) in have or (n, ak) in tombstones
                           for n in names)

            candidates = []
            for p in profiles:
                if known(p):
                    continue
                seen = [verdicts.get(n) for n in p.notice_ids if n in verdicts]
                if "pursue" in seen:
                    tier, why = 3, "on a pursue-grade notice"
                elif "monitor" in seen:
                    tier, why = 2, "on a monitor notice"
                elif seen:
                    continue  # research triaged everything it appears on as discard
                elif _best_grade(p) >= 2:
                    tier, why = 1, "fresh graded channel, untriaged"
                else:
                    continue
                candidates.append((tier, _best_grade(p), p.sighting_count,
                                   p.last_observed or date.min, p, why))

            candidates.sort(key=lambda t: t[:4], reverse=True)
            added = []
            for tier, _g, _sc, _d, p, why in candidates:
                entry = {
                    "id": _entry_id(p.normalized_name, p.agency),
                    "person_name": p.person_name,
                    "normalized_name": p.normalized_name,
                    "agency": p.agency,
                    "title": p.titles[0] if p.titles else None,
                    "added_at": now.isoformat(),
                    "added_by": "research",
                    "source_note": f"auto · {why}",
                    "overrides": {},
                    "enrich_status": "none",
                }
                doc["entries"].append(entry)
                added.append(entry)
            if added:
                self._save(doc)
        return {"added": len(added), "total": len(doc["entries"]),
                "names": [e["person_name"] for e in added]}

    # ---- operator curation --------------------------------------------------
    def reorder(self, ids: list[str]) -> bool:
        """Replace display order. Every current id must appear exactly once."""
        if not isinstance(ids, list) or not all(isinstance(i, str) for i in ids):
            return False
        with self._locked():
            doc = self.load()
            by_id = {e["id"]: e for e in doc["entries"]}
            if sorted(ids) != sorted(by_id):
                return False
            doc["entries"] = [by_id[i] for i in ids]
            self._save(doc)
        return True

    def remove(self, entry_id: str) -> bool:
        """Delete + tombstone, so research passes never resurrect it."""
        with self._locked():
            doc = self.load()
            entry = next((e for e in doc["entries"] if e["id"] == entry_id), None)
            if entry is None:
                return False
            # tombstone by the entry's own fields, not by parsing the id —
            # agency text is arbitrary and may contain the separator
            nn = entry.get("normalized_name") or ""
            ak = _s(entry.get("agency")).lower()
            if [nn, ak] not in doc["removed"]:
                doc["removed"].append([nn, ak])
            doc["entries"] = [e for e in doc["entries"] if e["id"] != entry_id]
            self._save(doc)
        return True

    def add_manual(self, person_name: str, agency: Optional[str],
                   *, title: Optional[str] = None, client: Optional[str] = None,
                   email: Optional[str] = None, phone: Optional[str] = None) -> dict:
        """Operator adds a name by hand (also lifts any tombstone).

        client tags the entry to a client slug so client-scoped views can
        show their own prospecting adds; known channels land as manual
        overrides, exactly as if typed into the rail afterwards."""
        person_name = _s(person_name)
        agency = _s(agency) or None
        nn = normalize_name(person_name)
        eid = _entry_id(nn, agency)
        with self._locked():
            doc = self.load()
            doc["removed"] = [t for t in doc["removed"]
                              if t != [nn, _s(agency).lower()]]
            existing = next((e for e in doc["entries"] if e["id"] == eid), None)
            if existing:
                if _s(client) and not existing.get("client"):
                    existing["client"] = _s(client)  # a client-view re-add claims it
                for k, v in (("email", _s(email)), ("phone", _s(phone))):
                    if v and not existing["overrides"].get(k):
                        existing["overrides"][k] = v
                self._save(doc)
                return existing
            entry = {"id": eid, "person_name": person_name, "normalized_name": nn,
                     "agency": agency, "title": _s(title) or None,
                     "client": _s(client) or None,
                     "added_at": date.today().isoformat(), "added_by": "manual",
                     "source_note": "manual add", "overrides": {}, "enrich_status": "none"}
            for k, v in (("email", _s(email)), ("phone", _s(phone))):
                if v:
                    entry["overrides"][k] = v
            doc["entries"].append(entry)
            self._save(doc)
        return entry

    def set_override(self, entry_id: str, **fields: Any) -> Optional[dict]:
        """Hand-corrected channels/name. Empty string clears an override."""
        with self._locked():
            doc = self.load()
            entry = next((e for e in doc["entries"] if e["id"] == entry_id), None)
            if entry is None:
                return None
            for k in ("email", "phone"):
                if k in fields:
                    v = _s(fields[k])
                    if v:
                        entry["overrides"][k] = v
                        if entry["enrich_status"] == "requested":
                            entry["enrich_status"] = "enriched"
                    else:
                        entry["overrides"].pop(k, None)
            if _s(fields.get("person_name")):
                # display name only; normalized_name stays the graph join key.
                # merge_enriched matches on both, so the round trip survives.
                entry["person_name"] = _s(fields["person_name"])
            self._save(doc)
            return entry

    # ---- enrichment loop ----------------------------------------------------
    def _needs(self, entry: dict, profile: Optional[ContactProfile]) -> list[str]:
        need = []
        for kind in ("email", "phone"):
            if entry["overrides"].get(kind):
                continue
            ch = _channel(profile, kind)
            if ch is None:
                need.append(kind)
            elif ch["grade"] == "C":
                need.append(f"{kind}_refresh")
        return need

    def export_enrichment_spec(self, out_dir: Optional[str] = None) -> tuple[Optional[str], int]:
        """Write the Apollo-handoff-style spec for every entry missing or stale
        on a channel; mark those entries 'requested'. Returns (path, count)."""
        out = out_dir or os.environ.get("LILA_HANDOFF_DIR") or str(_DATA_DIR / "handoff")
        profiles = self._profiles_by_id()
        with self._locked():
            doc = self.load()
            people = []
            for e in doc["entries"]:
                p = profiles.get(e["id"])
                need = self._needs(e, p)
                if not need:
                    continue
                people.append({
                    "name": e["person_name"], "agency": e["agency"], "title": e["title"],
                    "offices": p.offices if p else [],
                    "have": {k: (e["overrides"].get(k) or (_channel(p, k) or {}).get("value"))
                             for k in ("email", "phone")},
                    "need": need,
                })
                e["enrich_status"] = "requested"
            if not people:
                return None, 0
            os.makedirs(out, exist_ok=True)
            path = os.path.join(out, "outreach_enrichment.apollo.json")
            with open(path, "w", encoding="utf-8") as f:
                json.dump({"kind": "apollo_enrichment", "version": 1,
                           "generated_at": datetime.now().isoformat(),
                           "instructions": ENRICHMENT_SPEC_INSTRUCTIONS,
                           "people": people}, f, indent=2)
            self._save(doc)
        return path, len(people)

    def merge_enriched(self, rows: list[dict]) -> dict:
        """Fold an enrichment-results file back into overrides.

        Rows: {name, email?, phone?, agency?}. Matching is conservative like the
        graph itself: exact normalized name + agency when agency is given, else
        the name must match exactly one entry — anything ambiguous is reported
        back for the operator, never guessed. Matches against both the stored
        normalized_name and the (possibly operator-corrected) display name.
        Non-dict rows and non-string values are tolerated, not crashes.
        """
        with self._locked():
            doc = self.load()
            by_nn: dict[str, list[dict]] = {}
            for e in doc["entries"]:
                keys = {e["normalized_name"], normalize_name(e["person_name"])}
                for k in keys:
                    if k:
                        bucket = by_nn.setdefault(k, [])
                        if e not in bucket:
                            bucket.append(e)
            updated, ambiguous, unmatched, skipped = [], [], [], 0
            for row in rows or []:
                if not isinstance(row, dict):
                    skipped += 1
                    continue
                name = _s(row.get("name"))
                nn = normalize_name(name)
                if not nn:
                    skipped += 1
                    continue
                matches = by_nn.get(nn, [])
                if _s(row.get("agency")):
                    ak = _s(row.get("agency")).lower()
                    matches = [e for e in matches if _s(e["agency"]).lower() == ak]
                # one HUMAN may match via both name keys — dedupe by id
                uniq = list({e["id"]: e for e in matches}.values())
                if not uniq:
                    unmatched.append(name)
                    continue
                if len(uniq) > 1:
                    ambiguous.append(name)
                    continue
                entry = uniq[0]
                changed = False
                for k in ("email", "phone"):
                    v = _s(row.get(k))
                    if v and entry["overrides"].get(k) != v:
                        entry["overrides"][k] = v
                        changed = True
                if changed:
                    entry["enrich_status"] = "enriched"
                    updated.append(entry["person_name"])
            if updated:
                self._save(doc)
        return {"updated": updated, "ambiguous": ambiguous,
                "unmatched": unmatched, "skipped": skipped}

    # ---- render join ---------------------------------------------------------
    def _profiles_by_id(self) -> dict[str, ContactProfile]:
        """Profiles indexed under EVERY id they may be listed as — their current
        (nn, agency) plus approved-merge aliases — so an entry created before a
        merge decision keeps joining its profile after it."""
        decisions = self.store.read_decisions()
        profiles, _ = derive_profiles(
            self.store.read_observations(), now=date.today(), decisions=decisions)
        aliases = _merge_alias_names(decisions)
        out: dict[str, ContactProfile] = {}
        for p in profiles:
            ak = _s(p.agency).lower()
            for n in aliases.get((ak, p.normalized_name), {p.normalized_name}):
                out.setdefault(_entry_id(n, p.agency), p)
        return out

    def render(self, *, recover_corrupt: bool = True) -> list[dict]:
        """Entries in display order, joined with live profile data (grades are
        recomputed, so the rail never shows a stale grade)."""
        doc = self.load(recover_corrupt=recover_corrupt)
        if doc.get("read_error"):
            raise OutreachReadError(str(doc["read_error"]))
        profiles = self._profiles_by_id()
        out = []
        for e in doc["entries"]:
            p = profiles.get(e["id"])
            out.append({
                **e,
                "email": {"value": e["overrides"]["email"], "grade": None, "manual": True}
                         if e["overrides"].get("email") else _channel(p, "email"),
                "phone": {"value": e["overrides"]["phone"], "grade": None, "manual": True}
                         if e["overrides"].get("phone") else _channel(p, "phone"),
                "sighting_count": p.sighting_count if p else 0,
                "last_observed": p.last_observed.isoformat() if p and p.last_observed else None,
                "in_graph": p is not None,
                "needs": self._needs(e, p),
            })
        return out
