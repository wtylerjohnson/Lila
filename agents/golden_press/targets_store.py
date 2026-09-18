"""The durable targeting-contact store, read by the press, written outside it.

WHY A STORE (contract amendment v1.4, mirroring the L1 store pattern). The
press must make zero live calls. The L1 lane solved the same problem by
reading a durable local store instead of calling sam.gov, and this is that
shape for contacts: a separate sweep-class step resolves identities and
writes them here, and the press only ever reads the file. A press with no
network still renders Band 09 from whatever the store holds, and a press
with an empty store renders the certified zero.

THE STORE IS NOT EVIDENCE. Everything in here is ENRICHMENT: a route to a
federal record, never a federal record. Nothing in this file may be
promoted into the fact registry, the evidence dock, or the link-class
primary tally.

NO PROVENANCE, NO RENDER. Every row states its provenance class (apollo or
operator) and the date it was retrieved. A row that cannot state both is
dropped by ``admissible`` and counted in the receipt: an unlabeled identity
looks exactly like a verified one on the page, which is the whole reason
the law exists.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any, Optional

_ROOT = Path(__file__).resolve().parents[2]
STORE_DIR_ENV = "LILA_TARGETS_STORE_DIR"

# The only two things a contact identity can come from. `apollo` is the
# supply step's API result; `operator` is a human-entered correction. There
# is deliberately no third class: a value with no stated origin is not an
# identity, it is a guess.
PROVENANCE_CLASSES = ("apollo", "operator")

# The fields the provenance law covers (contract amendment v1.4).
# linkedin_url is an IDENTITY field, not a decoration: it is the cheapest
# way a caller confirms they have the right person before dialling, and it
# carries provenance like every other identity value.
IDENTITY_FIELDS = ("name", "title", "email", "phone", "linkedin_url")

_ISO_DAY = re.compile(r"^\d{4}-\d{2}-\d{2}")


def store_dir(root: Optional[Path] = None) -> Path:
    override = os.environ.get(STORE_DIR_ENV)
    if override:
        return Path(override)
    return (root or _ROOT) / "data" / "state" / "targets"


def store_path(slug: str, root: Optional[Path] = None) -> Path:
    return store_dir(root) / f"{slug}.contacts.json"


def load(slug: str, root: Optional[Path] = None) -> Optional[dict]:
    """The stored payload, or None when the supply step has never run.

    None is the honest answer for "never attempted" and is what makes the
    band able to tell an untested lane from a tested zero. An unreadable or
    malformed file is also None: the band then states that the supply step
    has not run, which is true of the usable evidence either way.
    """
    import json

    path = store_path(slug, root)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(payload, dict):
        return None
    if not isinstance(payload.get("receipt"), dict):
        return None
    payload.setdefault("contacts", [])
    if not isinstance(payload["contacts"], list):
        return None
    return payload


def write(slug: str, payload: dict, root: Optional[Path] = None) -> Path:
    """Atomically replace the store for one client (tools.artifacts owner)."""
    from tools.artifacts import atomic_write_json

    path = store_path(slug, root)
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(path, payload)
    return path


def _valid_provenance(node: Any) -> Optional[dict]:
    if not isinstance(node, dict):
        return None
    cls = str(node.get("class") or "").strip().casefold()
    retrieved = str(node.get("retrieved_at") or "").strip()
    if cls not in PROVENANCE_CLASSES or not _ISO_DAY.match(retrieved):
        return None
    return {"class": cls, "retrieved_at": retrieved[:10],
            "source": str(node.get("source") or "").strip() or None}


def admissible(payload: Optional[dict],
               spec_ids: Optional[Any] = None) -> tuple:
    """(rows, dropped) after the provenance and join laws.

    A row is dropped when it carries no readable row-level provenance, when
    it has no name, or when ``spec_ids`` is supplied and the row names no
    spec in it (the join law: a contact hangs off a targeting spec, which
    itself hangs off a rendered record). A single identity FIELD whose own
    override provenance is unreadable is stripped rather than sinking the
    whole row, and both counts ride the returned receipt so nothing is
    silently gone.
    """
    dropped = {"no_provenance": 0, "no_name": 0, "no_spec": 0,
               "fields_stripped": 0}
    rows: list = []
    allowed = None if spec_ids is None else {str(s) for s in spec_ids}
    for raw in ((payload or {}).get("contacts") or []):
        if not isinstance(raw, dict):
            dropped["no_provenance"] += 1
            continue
        provenance = _valid_provenance(raw.get("provenance"))
        if provenance is None:
            dropped["no_provenance"] += 1
            continue
        if not str(raw.get("name") or "").strip():
            dropped["no_name"] += 1
            continue
        spec_id = str(raw.get("spec_id") or "")
        if allowed is not None and spec_id not in allowed:
            dropped["no_spec"] += 1
            continue
        row = {
            "contact_id": str(raw.get("contact_id") or ""),
            "spec_id": spec_id,
            # One route legitimately answers several specs (ten forecast
            # rows through one reseller). The anchor spec_id carries the
            # join law; these name the rest so the page can say so instead
            # of printing the same person once per spec.
            "spec_ids": [str(s) for s in (raw.get("spec_ids") or [spec_id])
                         if str(s)],
            "tier": raw.get("tier"),
            "organization": " ".join(str(raw.get("organization") or "").split()),
            "join_record_ids": [str(r) for r in
                                (raw.get("join_record_ids") or [])],
            "provenance": provenance,
            "field_provenance": {},
        }
        overrides = raw.get("field_provenance")
        overrides = overrides if isinstance(overrides, dict) else {}
        for field in IDENTITY_FIELDS:
            value = " ".join(str(raw.get(field) or "").split())
            if not value:
                continue
            if field in overrides:
                field_prov = _valid_provenance(overrides[field])
                if field_prov is None:
                    dropped["fields_stripped"] += 1
                    continue
                row["field_provenance"][field] = field_prov
            row[field] = value
        if not row.get("name"):
            dropped["no_name"] += 1
            continue
        # TASK 5: every number carries a type and the receipt of how it got
        # one. An entry with no number or no known type is kept in the store
        # (the operator can still work it) but the render policy owns
        # whether it reaches the artifact.
        from agents.golden_press.phone_policy import PHONE_TYPES, PHONE_METADATA

        phones = []
        for entry in (raw.get("phones") or []):
            if not isinstance(entry, dict):
                dropped["phones_unclassified"] = \
                    dropped.get("phones_unclassified", 0) + 1
                continue
            number = " ".join(str(entry.get("number") or "").split())
            kind = entry.get("type")
            if not number or kind not in PHONE_TYPES or not entry.get("basis"):
                dropped["phones_unclassified"] = \
                    dropped.get("phones_unclassified", 0) + 1
                continue
            phones.append({"number": number, "type": kind,
                           "basis": str(entry.get("basis")),
                           "basis_kind": str(entry.get("basis_kind") or ""),
                           **{key: entry[key] for key in PHONE_METADATA
                              if isinstance(entry.get(key), str) and entry[key]}})
        row["phones"] = phones
        # SCREENING IS A PUBLICATION REQUIREMENT (operator ruling
        # 2026-08-06). The block survives into the row so Band 09 and the
        # export can show its cautions; an absent or non-renderable state
        # withholds the person from both. Previously admissible() rebuilt
        # rows from a fixed key list and silently dropped `screen`, so seven
        # of the eight rules were computed, stored, and discarded before the
        # page, and an unmatched person carried no screen at all yet still
        # rendered.
        from agents.golden_press.person_screen import (
            PENDING_SCREEN, renderable)

        screen = raw.get("screen")
        if not isinstance(screen, dict):
            screen = {"state": PENDING_SCREEN, "findings": [],
                      "cautions": [],
                      "why": "no screen block on this row; never screened"}
        row["screen"] = screen
        if not renderable(screen):
            dropped["not_screened"] = dropped.get("not_screened", 0) + 1
            dropped.setdefault("withheld_states", {})
            state = str(screen.get("state") or "unknown")
            dropped["withheld_states"][state] = \
                dropped["withheld_states"].get(state, 0) + 1
            continue
        # the pass label rides the row so the export's Pass column is not
        # permanently blank
        if raw.get("pass_label"):
            row["pass_label"] = str(raw["pass_label"]).strip()
        if raw.get("apollo_refreshed_at"):
            # Apollo's OWN per-record freshness, kept DISTINCT from our
            # retrieved_at so a year-old record cannot print as fetched today
            row["source_refreshed_at"] = str(raw["apollo_refreshed_at"])[:10]
        if raw.get("email_status"):
            row["email_status"] = str(raw["email_status"]).strip()
        # Apollo's own attribution, carried for measurement only. It does
        # NOT decide the rendered tier in this edition.
        for key in ("apollo_seniority", "apollo_departments",
                    "apollo_subdepartments", "apollo_functions"):
            if raw.get(key):
                row[key] = raw[key]
        rows.append(row)
    rows.sort(key=lambda r: (str(r.get("spec_id")), int(r.get("tier") or 99),
                             str(r.get("name"))))
    return rows, dropped


# --------------------------------------------------------------------------- #
# the Apollo-ready export · an OPERATOR TOOL, never a certified artifact
# --------------------------------------------------------------------------- #
# Sequence-load shape: the five columns Apollo's own CSV import reads, then
# the LILA columns that carry the join so a loaded sequence still knows
# which federal record each person hangs off. This file sits BESIDE the
# client artifact and is never delivered as one (contract amendment v1.4).
SEQUENCE_COLUMNS = (
    "First Name", "Last Name", "Email", "Email Status", "Company", "Title",
    "LinkedIn URL", "Mobile", "Direct Phone", "Main Line",
    "Persona Tier", "Row Class", "Buying Component",
    "Joined Record IDs", "Joined Record URLs", "Why This Call",
    "Screening", "Cautions",
    "Provenance Class", "Retrieved At", "Source Refreshed", "Pass", "Spec ID",
)


def phone_of(row: dict, kind: str) -> Optional[dict]:
    """The first classified number of one type on a store row, or None.

    Reads the stored classification only; it never infers a type. A row
    whose numbers were never typed exports blank cells rather than
    guessing which column a number belongs in.
    """
    for entry in (row.get("phones") or ()):
        if isinstance(entry, dict) and entry.get("type") == kind \
                and entry.get("number") \
                and entry.get('dnc_status_cd') not in {'found', 'listed'} \
                and entry.get('status_cd') != 'invalid':
            return entry
    return None


def _split_name(value: str) -> tuple:
    parts = str(value or "").split()
    if not parts:
        return "", ""
    return parts[0], " ".join(parts[1:])


def sequence_export_rows(targeting: Optional[dict]) -> list:
    """One export row per admissible contact, joined back to its spec.

    A contact with no spec is already gone by the store's join law, so an
    exported row can never name a person with no federal record behind it.
    """
    targeting = targeting or {}
    specs = {str(s.get("spec_id")): s for s in (targeting.get("specs") or [])}
    rows, _dropped = admissible({"contacts": targeting.get("contacts") or []},
                                spec_ids=list(specs))
    out = []
    for row in rows:
        spec = specs.get(str(row.get("spec_id"))) or {}
        joins = spec.get("join_records") or []
        first, last = _split_name(row.get("name"))
        provenance = row["provenance"]

        def _num(kind):
            entry = phone_of(row, kind)
            return entry["number"] if entry else ""

        out.append({
            "First Name": first,
            "Last Name": last,
            "Email": row.get("email", ""),
            "Email Status": row.get("email_status", ""),
            "Company": row.get("organization", ""),
            "Title": row.get("title", ""),
            "LinkedIn URL": row.get("linkedin_url", ""),
            "Mobile": _num("mobile"),
            "Direct Phone": _num("work_direct"),
            "Main Line": _num("org_main"),
            "Pass": row.get("pass_label", ""),
            "Screening": (row.get("screen") or {}).get("state", ""),
            "Cautions": " · ".join((row.get("screen") or {}).get("cautions") or []),
            "Persona Tier": (row.get("tier") if row.get("tier") is not None
                             else "unattributed"),
            "Source Refreshed": row.get("source_refreshed_at", ""),
            "Row Class": spec.get("row_class", ""),
            "Buying Component": spec.get("buying_component", ""),
            "Joined Record IDs": " ".join(
                str(j.get("record_id") or "") for j in joins),
            "Joined Record URLs": " ".join(
                str(j.get("url") or "") for j in joins if j.get("url")),
            "Why This Call": spec.get("rationale", ""),
            "Provenance Class": provenance["class"],
            "Retrieved At": provenance["retrieved_at"],
            "Spec ID": row.get("spec_id", ""),
        })
    return out


def write_sequence_csv(path: Path, targeting: Optional[dict]) -> int:
    """Write the export and return the row count. Header-only is a valid
    result: it states that the export ran and resolved nobody."""
    import csv

    rows = sequence_export_rows(targeting)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    with open(temporary, "w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(SEQUENCE_COLUMNS))
        writer.writeheader()
        for row in rows:
            writer.writerow(row)
    os.replace(temporary, path)
    return len(rows)


def field_provenance(row: dict, field: str) -> dict:
    """The provenance governing ONE identity field: its own override when it
    has one, otherwise the row's. Never absent by construction, because
    ``admissible`` already dropped anything that could not state it."""
    return (row.get("field_provenance") or {}).get(field) or row["provenance"]
