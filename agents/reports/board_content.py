"""Operator-approved Signal Board content: the stored input that makes a
press reproducible (2026-07-16).

Every prior shipped board needed manual passes (seal drag-drop, editor
stripping, link fixes) because the analyst-shaped band content lived only
in hand-edited HTML. This module gives that content a schema and a stored
home (clients/<slug>/signal_board_content.json), so the same stored inputs
plus a pinned render date reproduce the same bytes.

Honesty contract: content is COPY, never evidence. Every dollar figure the
content renders must appear in its figure registry, and every registry row
must either reconcile against the stored sweep (raw value found in a stored
record) or carry an explicit operator attestation. An unbacked, unattested
figure is a loud build failure listing exactly what disagrees: a data
discrepancy for the human, never a render that papers over it.
"""
from __future__ import annotations

import json
import os
import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Optional

from pydantic import BaseModel, Field, ValidationInfo, model_validator

from agents.reports.facts import SourceSystem, _parse_retrieved
from agents.reports.links import (
    SAM_NOTICE_BUILDER,
    USASPENDING_AWARD_BUILDER,
    build_sam_notice_link,
    build_usaspending_award_link,
)

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

#: display-form dollars: $289.5M, $677.6K, $20B, $1,140,000
FIGURE_RE = re.compile(r"\$[0-9][0-9,.]*(?:\.[0-9]+)?\s?[MBK]?\b")

_SCALE = {"K": 1e3, "M": 1e6, "B": 1e9, "": 1.0}

_MONEY_DISPLAY_RE = re.compile(
    r"\$([0-9][0-9,]*)(?:\.([0-9]+))?\s?([MBK]?)"
)
_MONEY_DISPLAY_SCALE = {
    "K": Decimal("1000"),
    "M": Decimal("1000000"),
    "B": Decimal("1000000000"),
    "": Decimal("1"),
}


def _money_display_value(value: object) -> Optional[tuple[Decimal, Decimal]]:
    """Return a displayed dollar value and its house-format quantum.

    Suffixed composer figures are always rounded to two decimal places before
    trailing zeroes are trimmed (``$1.20M`` becomes ``$1.2M``). Their quantum
    therefore remains one hundredth of the suffix even when no decimal point
    is visible. Unsuffixed figures are rounded at their visible precision.
    """
    if not isinstance(value, str):
        return None
    match = _MONEY_DISPLAY_RE.fullmatch(value.strip())
    if match is None:
        return None
    whole, fractional, suffix = match.groups()
    try:
        shown = Decimal(
            whole.replace(",", "")
            + (f".{fractional}" if fractional else "")
        )
    except InvalidOperation:
        return None
    multiplier = _MONEY_DISPLAY_SCALE[suffix]
    quantum = (
        multiplier / Decimal("100")
        if suffix
        else Decimal("1").scaleb(-len(fractional or ""))
    )
    return shown * multiplier, quantum


class BoardFigure(BaseModel):
    """One rendered dollar figure with its provenance and backing claim."""

    text: str = Field(description="display form exactly as rendered, e.g. $174.54M")
    raw: Optional[float] = Field(
        default=None, description="exact source value, e.g. 174537374.57")
    source_system: Optional[SourceSystem] = None
    source_record_id: Optional[str] = None
    generated_internal_id: Optional[str] = Field(
        default=None,
        description="USAspending generated record identity used by the "
                    "canonical award-link builder; never parsed from a URL")
    source_url: Optional[str] = None
    retrieved_at: Optional[datetime] = None
    analyst_attested: bool = Field(
        default=False,
        description="operator-set ONLY: this figure was verified by the "
                    "analyst outside the stored sweep (names the operator's "
                    "decision, never a default)")
    component_raws: Optional[list[float]] = Field(
        default=None,
        description="for derived totals: the exact component values; the "
                    "total is backed when every component is backed and the "
                    "sum states the display")

    @model_validator(mode="after")
    def _derive_primary_source_url(self):
        """Primary award provenance is identity-derived, never free-string."""
        if (self.source_system == SourceSystem.USASPENDING
                and self.generated_internal_id):
            from agents.reports.links import build_usaspending_award_link
            self.source_url = build_usaspending_award_link(
                self.generated_internal_id).url
        return self


class DecisionMaker(BaseModel):
    """One sourced research lead tied to a displayed account/program route.

    This is intentionally not a solicitation POC.  The official leadership
    source is the identity/title authority; an optional hash-pinned local
    portrait is a presentation enrichment only.
    """

    name: str
    title: str
    organization: str
    bio_url: str
    retrieved_at: datetime
    route_kind: str
    route_identity: str
    route_label: str
    relevance_rationale: str
    source_system: str = "official-leadership-source"
    source_record_id: str = ""
    portrait_source_url: Optional[str] = None
    portrait_asset: Optional[str] = None
    portrait_sha256: Optional[str] = None

    @model_validator(mode="after")
    def _validate_official_record(self):
        for field in (
            "name", "title", "organization", "route_kind",
            "route_identity", "route_label",
            "relevance_rationale", "source_system",
        ):
            value = " ".join(str(getattr(self, field) or "").strip().split())
            if not value:
                raise ValueError(f"decision maker {field} is required")
            setattr(self, field, value)
        if re.fullmatch(
                r"[a-z0-9]+(?:[._-][a-z0-9]+)*", self.route_kind) is None:
            raise ValueError(
                "decision maker route_kind must be a canonical source kind")
        if ("://" in self.route_identity
                or any(char.isspace() for char in self.route_identity)
                or any(
                unicodedata.category(char).startswith("C")
                for char in self.route_identity)):
            raise ValueError(
                "decision maker route_identity must be a structured record id")
        if not self.bio_url.lower().startswith("https://"):
            raise ValueError("decision maker bio_url must use HTTPS")
        self.source_record_id = (
            " ".join(self.source_record_id.strip().split()) or self.bio_url
        )
        if self.retrieved_at.tzinfo is None or self.retrieved_at.utcoffset() is None:
            raise ValueError("decision maker retrieved_at must be timezone-aware")
        portrait_fields = (
            self.portrait_source_url, self.portrait_asset, self.portrait_sha256,
        )
        if any(portrait_fields) and not all(portrait_fields):
            raise ValueError(
                "decision maker portrait_source_url, portrait_asset, and "
                "portrait_sha256 must be supplied together")
        if (self.portrait_source_url
                and not self.portrait_source_url.lower().startswith("https://")):
            raise ValueError(
                "decision maker portrait_source_url must use HTTPS")
        if self.portrait_sha256 and not re.fullmatch(
                r"[0-9a-fA-F]{64}", self.portrait_sha256):
            raise ValueError("decision maker portrait_sha256 is invalid")
        return self

    @property
    def route_key(self) -> tuple[str, str]:
        """Exact displayed-card route this research lead must match."""
        return self.route_kind, self.route_identity


class SignalBoardContent(BaseModel):
    """The complete band content for one client's board. Bands mirror the
    locked template's model keys; the figure registry carries provenance."""

    client_name: str
    composition_mode: str = Field(
        default="operator",
        pattern="^(operator|machine)$",
        description="producer origin: operator-authored content keeps its "
                    "hand-approved standing; machine-composed content is "
                    "judged under the machine-screened relevance law. "
                    "Legacy content without the field is operator.")
    hero_context: str = ""
    chips: list[dict] = Field(default_factory=list)
    scale_label: str = ""
    scale_total: str = ""
    scale_counts: str = ""
    scale: Optional[dict] = None
    news: list[dict] = Field(default_factory=list)
    signal_cards: list[dict] = Field(default_factory=list)
    coverage: list[dict] = Field(default_factory=list)
    best_fit: list[dict] = Field(default_factory=list)
    competitors: list[dict] = Field(default_factory=list)
    acquisition_pathways: list[dict] = Field(default_factory=list)
    teaming: list[dict] = Field(default_factory=list)
    horizon: list[dict] = Field(default_factory=list)
    pocs: list[dict] = Field(default_factory=list)
    decision_makers: list[DecisionMaker] = Field(
        default_factory=list,
        max_length=4,
        description="official public leadership records shown only as "
                    "research leads, never solicitation POCs",
    )
    evidence: list[dict] = Field(default_factory=list)
    sequence: str = ""
    figures: list[BoardFigure] = Field(
        default_factory=list,
        description="registry of every dollar figure the bands render")

    @model_validator(mode="after")
    def _validate_machine_scale_semantics(self, info: ValidationInfo):
        """Machine marquee dollars must state what measure they represent."""
        if self.composition_mode != "machine":
            return self
        allow_legacy = bool(
            info.context
            and info.context.get("allow_legacy_machine_scale")
        )
        if (allow_legacy and isinstance(self.scale, dict)
                and not self.scale.get("basis") and not self.scale_label):
            # One-generation migration read only.  Relevance calibration and
            # C1's prior-artifact identity check may inspect the legacy file
            # so the sanctioned composer can replace it.  Normal loads,
            # staged C1 validation, pair validation, and press remain strict.
            return self
        if not isinstance(self.scale, dict) or not self.scale:
            raise ValueError(
                "machine scale requires a structured obligation basis")
        basis = str(self.scale.get("basis") or "")
        expected = {
            "competitor_obligated_to_date": (
                "Competitor-held obligated history"),
            "client_obligated_to_date": "Client-footprint obligated history",
        }.get(basis)
        if expected is None:
            raise ValueError(
                "machine scale requires a recognized obligated-value basis")
        if self.scale_label != expected:
            raise ValueError(
                "machine scale label does not match its obligated-value basis")
        rows = self.scale.get("rows")
        if not isinstance(rows, list) or any(
                not isinstance(row, dict)
                or row.get("amount_basis") != "obligated_to_date"
                for row in rows):
            raise ValueError(
                "machine scale components must bind obligated_to_date")
        if self.scale.get("total") != self.scale_total:
            raise ValueError(
                "machine scale total does not match its structured scale")
        if not self.scale_total:
            if rows:
                raise ValueError(
                    "machine scale rows require a displayed aggregate total")
            return self
        parsed_total = _money_display_value(self.scale_total)
        if parsed_total is None:
            raise ValueError(
                "machine scale total must be a structured dollar display")
        total_value, total_quantum = parsed_total
        if not rows:
            if total_value != 0:
                raise ValueError(
                    "nonzero machine scale requires source rows")
            return self
        parsed_rows = [_money_display_value(row.get("amount")) for row in rows]
        if any(parsed is None for parsed in parsed_rows):
            raise ValueError(
                "machine scale row amounts must be structured dollar displays")
        row_values = [parsed[0] for parsed in parsed_rows if parsed is not None]
        row_quantums = [parsed[1] for parsed in parsed_rows if parsed is not None]
        # Display values are rounded independently. Reconcile their implied
        # intervals rather than demanding equality between already-rounded
        # components and the independently rounded aggregate.
        tolerance = (sum(row_quantums, total_quantum) / Decimal("2"))
        if abs(sum(row_values, Decimal("0")) - total_value) > tolerance:
            raise ValueError(
                "machine scale row amounts do not reconcile to its aggregate")
        return self


class FigureReconciliation(BaseModel):
    """Build-time verdict over the content's figure registry."""

    backed: list[str] = Field(default_factory=list)
    attested: list[str] = Field(default_factory=list)
    unbacked: list[str] = Field(default_factory=list)
    unregistered: list[str] = Field(
        default_factory=list,
        description="figures found in rendered bands but absent from the registry")

    @property
    def clean(self) -> bool:
        return not self.unbacked and not self.unregistered


@dataclass(frozen=True)
class FederalLinkRef:
    """One structured primary-record identity used by client copy."""

    builder: str
    record_id: str
    locations: tuple[str, ...]


@dataclass(frozen=True)
class FederalLinkRecord:
    """Record-level verdict consumed by the canonical renderer."""

    ref: FederalLinkRef
    reconciled: bool
    proof: str


@dataclass
class FederalLinkReconciliation:
    records: list[FederalLinkRecord]
    violations: list

    @property
    def clean(self) -> bool:
        return not self.violations

    @property
    def status_map(self) -> dict[tuple[str, str], bool]:
        return {
            (record.ref.builder, record.ref.record_id): record.reconciled
            for record in self.records
        }


def content_path(client_name: str) -> str:
    from tools.capability import _slug
    return os.path.join(_ROOT, "clients", _slug(client_name),
                        "signal_board_content.json")


def people_path(client_name: str) -> str:
    """Client-curated, compose-stable official leadership input."""
    from tools.capability import _slug

    return os.path.join(
        _ROOT, "clients", _slug(client_name), "signal_board_people.json")


def load_decision_makers(client_name: str) -> list[DecisionMaker]:
    """Load exact official records that survive repeat composition.

    The client-scoped file is intentionally separate from generated board
    content: tomorrow's sanctioned compose may replace
    ``signal_board_content.json`` without erasing reviewed people records.
    """
    path = people_path(client_name)
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, dict) or payload.get("schema_version") != 1:
        raise ValueError("signal_board_people.json schema_version must be 1")
    rows = payload.get("decision_makers")
    if not isinstance(rows, list):
        raise ValueError(
            "signal_board_people.json decision_makers must be a list")
    if len(rows) > 4:
        raise ValueError(
            "signal_board_people.json may contain at most 4 decision makers")
    return [DecisionMaker(**row) for row in rows]


_PRIMARY_ROUTE_FIELDS = {
    "award_generated_id": "usaspending-award",
    "sam_notice_guid": "sam.gov",
    "forecast_id": "agency-forecast",
    "program_route_identity": "program",
}


def primary_displayed_route_keys(content) -> set[tuple[str, str]]:
    """Routes published as direct card identities, never nested evidence."""
    payload = (
        content.model_dump(mode="python")
        if hasattr(content, "model_dump")
        else vars(content) if hasattr(content, "__dict__")
        else content
    )
    if not isinstance(payload, dict):
        raise ValueError("decision-maker route content must be a mapping")
    keys: set[tuple[str, str]] = set()
    for band in (
            "best_fit", "competitors", "acquisition_pathways", "teaming",
            "horizon"):
        rows = payload.get(band) or []
        if not isinstance(rows, list):
            raise ValueError(
                f"decision-maker route band {band!r} must be a list")
        for card in rows:
            if not isinstance(card, dict):
                raise ValueError(
                    f"decision-maker route band {band!r} has a non-card row")
            for field_name, route_kind in _PRIMARY_ROUTE_FIELDS.items():
                identity = str(card.get(field_name) or "").strip()
                if identity:
                    keys.add((route_kind, identity))
    return keys


def _decision_maker_binding_map(
        people: list[DecisionMaker],
) -> dict[tuple[str, str, str, str], tuple[str, str]]:
    bindings: dict[tuple[str, str, str, str], tuple[str, str]] = {}
    for person in people:
        identity = (
            person.name,
            person.organization,
            person.source_system,
            person.source_record_id,
        )
        if identity in bindings:
            raise ValueError(
                f"duplicate reviewed decision maker identity: {person.name}")
        bindings[identity] = person.route_key
    return bindings


def validate_decision_maker_routes(
    content,
    *,
    expected_people: Optional[list[DecisionMaker]] = None,
) -> None:
    """Bind curated leadership records to direct routes in this exact board.

    expected_people is the independent client-scoped source. Comparing it at
    press time prevents a content edit from swapping a person onto another
    otherwise-valid route even when the content/trail pair hash is rebuilt.
    """
    payload = (
        content.model_dump(mode="python")
        if hasattr(content, "model_dump")
        else vars(content) if hasattr(content, "__dict__")
        else content
    )
    if not isinstance(payload, dict):
        raise ValueError("decision-maker route content must be a mapping")
    raw_people = payload.get("decision_makers") or []
    if not isinstance(raw_people, list):
        raise ValueError("decision_makers must be a list")
    people = [
        row if isinstance(row, DecisionMaker) else DecisionMaker(**row)
        for row in raw_people
    ]
    routes = primary_displayed_route_keys(payload)
    unbound = [person for person in people if person.route_key not in routes]
    if unbound:
        failures = "; ".join(
            f"{person.name}: {person.route_kind}::{person.route_identity}"
            for person in unbound
        )
        raise ValueError(
            "reviewed decision maker route is not a primary displayed card "
            f"identity: {failures}")
    if expected_people is not None:
        actual = _decision_maker_binding_map(people)
        expected = _decision_maker_binding_map(expected_people)
        if actual != expected:
            raise ValueError(
                "board decision-maker identities or route bindings differ "
                "from the independent client-scoped reviewed records")


def load_board_content(
        client_name: str, *,
        allow_legacy_machine_scale: bool = False,
) -> Optional[SignalBoardContent]:
    """The stored content artifact, schema-validated; None when absent."""
    path = content_path(client_name)
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as f:
        return SignalBoardContent.model_validate(
            json.load(f),
            context={
                "allow_legacy_machine_scale": allow_legacy_machine_scale,
            },
        )


def _display_matches_raw(text: str, raw: float) -> bool:
    """Does the display form state `raw` at its shown precision?

    $174.54M states 174,537,374.57 (174.54 at 2dp); $289.5M states
    289,535,714.17 (289.5 at 1dp); $677.6K states 677,553.03; $1,140,000
    states itself exactly."""
    m = re.fullmatch(r"\$([0-9][0-9,]*(?:\.([0-9]+))?)\s?([MBK]?)", text.strip())
    if not m:
        return False
    shown = float(m.group(1).replace(",", ""))
    decimals = len(m.group(2) or "")
    scale = _SCALE[m.group(3)]
    if scale == 1.0:
        return abs(raw - shown) < 0.5
    return abs(raw / scale - shown) <= 0.5 * (10 ** -decimals) + 1e-9


_VALUE_KEY_RE = (r'"(?:amount|amounts?|total|obligated|estimated_value|'
                 r'potential_ceiling|value)[^"]*":\s*([0-9.]+)')

def _values_in_blob(blob: str) -> set[str]:
    return set(re.findall(_VALUE_KEY_RE, blob))


def _enclosing_object(blob: str, pos: int) -> str:
    """The nearest JSON object enclosing `pos`: the record row itself, never
    a neighbor. Balanced-brace walk; falls back to a short window only when
    the blob is not brace-balanced around the position."""
    start = pos
    depth = 0
    while start > 0:
        ch = blob[start]
        if ch == "}":
            depth += 1
        elif ch == "{":
            if depth == 0:
                break
            depth -= 1
        start -= 1
    end = pos
    depth = 0
    while end < len(blob):
        ch = blob[end]
        if ch == "{":
            depth += 1
        elif ch == "}":
            if depth == 0:
                break
            depth -= 1
        end += 1
    if blob[start] != "{":
        return blob[max(0, pos - 300):pos + 300]
    return blob[start:end + 1]


def _record_scoped_values(blob: str, record_id: str) -> set[float]:
    """Values stated by the record row(s) carrying this id, scoped to the
    id's own enclosing object so a figure cannot borrow a raw from one
    record and an id from another. A URL slug counts as carrying the id."""
    out: set[float] = set()
    for m in re.finditer(re.escape(record_id), blob):
        row = _enclosing_object(blob, m.start())
        out |= {float(v) for v in re.findall(_VALUE_KEY_RE, row)}
    return out


def _repull_authority(sweep: dict) -> dict[str, dict]:
    """The award re-pull lane indexed by GID and PIID. When a figure's
    record identity matches a re-pulled row, that row's CURRENT values
    are authoritative for reconciliation: a stale buyer-map occurrence
    of the same identity can never back a conflicting figure. An
    identity shared by more than one re-pulled row (PIIDs are only
    per-agency unique) is AMBIGUOUS and asserts no authority; the
    figure falls back to record-scoped backing rather than being judged
    against the wrong row."""
    rows = (sweep.get("results") or {}).get("award_repulls") or []
    by_id: dict[str, dict] = {}
    ambiguous: set[str] = set()
    for row in rows:
        if not isinstance(row, dict):
            continue
        for key in ("generated_id", "award_id"):
            ident = row.get(key)
            if not ident:
                continue
            ident = str(ident)
            if ident in by_id and by_id[ident] is not row:
                ambiguous.add(ident)
            else:
                by_id.setdefault(ident, row)
    for ident in ambiguous:
        by_id.pop(ident, None)
    return by_id


def reconcile_figures(content: SignalBoardContent,
                      sweep: dict) -> FigureReconciliation:
    """Every registry figure must be backed by a stored-sweep value ON ITS
    OWN RECORD or carry an explicit operator attestation; every rendered
    figure must be in the registry. Derived sums are backed when every
    component value is. When the award re-pull lane holds the figure's
    GID/PIID, the re-pulled row is the authoritative value source
    (docs/VERIFICATION_POLICY.md: the primary record wins)."""
    blob = json.dumps(sweep)
    stored = {float(v) for v in _values_in_blob(blob)}
    repull_by_id = _repull_authority(sweep)
    rec = FigureReconciliation()

    for fig in content.figures:
        label = f"{fig.text} ({fig.source_record_id or fig.source_url or 'no record'})"
        if fig.raw is not None and not _display_matches_raw(fig.text, fig.raw):
            rec.unbacked.append(
                f"{label}: display does not state raw {fig.raw!r}")
            continue
        id_backed = bool(fig.source_record_id) and fig.source_record_id in blob
        authority = None
        authority_ident = ""
        for ident in (fig.generated_internal_id, fig.source_record_id):
            if ident and str(ident) in repull_by_id:
                authority = repull_by_id[str(ident)]
                authority_ident = str(ident)
                break
        if authority is not None:
            scoped = {float(v) for v in re.findall(
                _VALUE_KEY_RE, json.dumps(authority))}
        else:
            scoped = (_record_scoped_values(blob, fig.source_record_id)
                      if id_backed else stored)
        raw_backed = fig.raw is not None and any(
            abs(fig.raw - v) < 0.005 for v in scoped)
        if raw_backed and (id_backed or fig.source_record_id is None):
            rec.backed.append(fig.text)
        elif fig.analyst_attested:
            rec.attested.append(fig.text)
        else:
            why = []
            if fig.raw is None:
                why.append("no raw value declared")
            elif not id_backed and fig.source_record_id:
                why.append("record id not in the stored sweep")
            elif authority is not None and not raw_backed:
                fresh = sorted(scoped)
                why.append(
                    f"figure drift: the fresh re-pull states {fresh} for "
                    f"{authority_ident} and the figure states {fig.raw!r}; "
                    "a stale buyer-map occurrence cannot back it")
            elif not raw_backed:
                why.append("value not stated by its own stored record")
            rec.unbacked.append(f"{label}: {'; '.join(why) or 'unbacked'}")

    # derived sums: a total whose components are all backed is backed.
    # A derived total that carries a re-pull-governed identity (the
    # established shape: one component's record identity rides the sum)
    # is rescued ONLY when a component states that row's CURRENT value;
    # otherwise the first pass's figure-drift refusal stands. The
    # whole-blob component match alone still contains the stale
    # occurrence the authority overruled and can never override it.
    for fig in content.figures:
        if fig.text in rec.backed or fig.analyst_attested:
            continue
        governing = [repull_by_id[str(ident)]
                     for ident in (fig.generated_internal_id,
                                   fig.source_record_id)
                     if ident and str(ident) in repull_by_id]
        if governing:
            fresh_values = {float(v) for v in re.findall(
                _VALUE_KEY_RE, json.dumps(governing[0]))}
            if not any(any(abs(c - v) < 0.005 for v in fresh_values)
                       for c in fig.component_raws or []):
                continue
        if fig.raw is not None and fig.component_raws and \
                all(any(abs(c - v) < 0.005 for v in stored)
                    for c in fig.component_raws) and \
                abs(sum(fig.component_raws) - fig.raw) < 0.01 and \
                _display_matches_raw(fig.text, fig.raw):
            entry = next((u for u in rec.unbacked if u.startswith(fig.text)), None)
            if entry:
                rec.unbacked.remove(entry)
            rec.backed.append(fig.text)

    registry = {f.text for f in content.figures}
    rendered: set[str] = set()
    for band in (content.chips, content.news, content.signal_cards,
                 content.best_fit, content.competitors, content.teaming,
                 content.acquisition_pathways,
                 content.horizon, content.pocs, content.evidence,
                 [content.scale or {}],
                 [{"t": content.scale_total}, {"t": content.scale_counts},
                  {"t": content.hero_context}, {"t": content.sequence}]):
        for row in band:
            # quoted source evidence bound to a machine card is verbatim
            # record text, never rendered copy; rendered dollars in every
            # other field still register
            scanned = ({k: v for k, v in row.items()
                        if k != "machine_evidence"}
                       if isinstance(row, dict) else row)
            rendered |= {m.group(0).strip()
                         for m in FIGURE_RE.finditer(json.dumps(scanned))}
    rec.unregistered = sorted(x for x in rendered if x not in registry)
    return rec


def reconciliation_error(rec: FigureReconciliation) -> Optional[str]:
    if rec.clean:
        return None
    lines = ["board content figures disagree with the stored sweep "
             "(data discrepancy for the human; docs/VERIFICATION_POLICY.md):"]
    lines += [f"  UNBACKED {u}" for u in rec.unbacked]
    lines += [f"  UNREGISTERED {u} (rendered but not in the figure registry)"
              for u in rec.unregistered]
    return "\n".join(lines)


_LINK_BANDS = {
    "scale": "Scale evidence",
    "news": "Signal strip",
    "best_fit": "Best-fit opportunities",
    "competitors": "Competitor lanes",
    "acquisition_pathways": "Federal acquisition pathways",
    "teaming": "Candidate teaming opportunities",
    "horizon": "Prospective horizon",
    "pocs": "Published contacts",
    "evidence": "Evidence dock",
}
def federal_link_refs(content: SignalBoardContent) -> list[FederalLinkRef]:
    """Collect structured SPA identities without reading any URL string."""
    found: dict[tuple[str, str], list[str]] = {}

    def walk(value, path: str, label: str) -> None:
        if isinstance(value, list):
            for index, item in enumerate(value):
                walk(item, f"{path}[{index}]", label)
            return
        if not isinstance(value, dict):
            return
        identities = (
            ("award_generated_id", USASPENDING_AWARD_BUILDER),
            ("sam_notice_guid", SAM_NOTICE_BUILDER),
            ("opp_notice_guid", SAM_NOTICE_BUILDER),
        )
        for field, builder in identities:
            if value.get(field):
                record_id = str(value[field])
                found.setdefault((builder, record_id), []).append(
                    f"{label} ({path}.{field})")
        for key, item in value.items():
            walk(item, f"{path}.{key}", label)

    for field, label in _LINK_BANDS.items():
        value = getattr(content, field)
        if value:
            walk(value, field, label)
    # A recompete calendar may add a link to an award already carried by the
    # verified figure registry after content reconciliation. Seed the status
    # map from that fact-pack identity as well: the calendar never promotes a
    # generated ID that the pack itself did not verify.
    for index, figure in enumerate(content.figures):
        if figure.generated_internal_id:
            found.setdefault(
                (USASPENDING_AWARD_BUILDER,
                 figure.generated_internal_id),
                [],
            ).append(
                f"Figure registry (figures[{index}].generated_internal_id)")
    return [
        FederalLinkRef(builder=builder, record_id=record_id,
                       locations=tuple(dict.fromkeys(locations)))
        for (builder, record_id), locations in found.items()
    ]


def reconcile_federal_link_records(
    content: SignalBoardContent,
    sweep: dict,
    *,
    now: datetime,
) -> FederalLinkReconciliation:
    """Join every structured link identity to its current primary record.

    The join creates a tiny record-only FactPack and runs the existing
    freshness/dispute gate over it.  This function never resolves a dispute
    and never performs HTTP.  Missing/mismatched records are explicit hard
    violations; only a present, fresh, undisputed record may mark an anchor
    ``data-link-reconciled=1``.
    """
    from agents.reports.facts import (
        UNKNOWN_FRESHNESS,
        Fact,
        FactPack,
    )
    from agents.reports.lint import LintViolation
    from agents.reports.verification import freshness_violations

    results = sweep.get("results") or {}
    award_rows = {
        str(row.get("generated_id")): row
        for row in (results.get("award_repulls") or [])
        if isinstance(row, dict) and row.get("generated_id")
    }
    sam_rows = {}
    for row in results.get("sam.gov") or []:
        if not isinstance(row, dict):
            continue
        raw = row.get("raw_payload") or {}
        guid = (row.get("source_id") or row.get("notice_id")
                or raw.get("noticeId") or raw.get("notice_id"))
        if guid:
            sam_rows.setdefault(str(guid), row)

    figures_by_gid: dict[str, list[BoardFigure]] = {}
    for figure in content.figures:
        gid = figure.generated_internal_id
        if gid:
            figures_by_gid.setdefault(gid, []).append(figure)

    default_retrieved = _parse_retrieved(sweep.get("generated_at"))
    records: list[FederalLinkRecord] = []
    violations: list[LintViolation] = []

    for index, ref in enumerate(federal_link_refs(content), 1):
        locations = "; ".join(ref.locations)
        try:
            if ref.builder == USASPENDING_AWARD_BUILDER:
                canonical = build_usaspending_award_link(ref.record_id).url
            else:
                canonical = build_sam_notice_link(ref.record_id).url
        except ValueError as exc:
            violations.append(LintViolation(
                rule="FEDERAL_LINK_ID_INVALID",
                detail=f"{locations}: {exc}", excerpt=ref.record_id))
            records.append(FederalLinkRecord(
                ref=ref, reconciled=False, proof=str(exc)))
            continue

        row = (award_rows.get(ref.record_id)
               if ref.builder == USASPENDING_AWARD_BUILDER
               else sam_rows.get(ref.record_id))
        if row is None:
            detail = (f"primary record absent for {canonical} in {locations}; "
                      "the link cannot render as reconciled")
            violations.append(LintViolation(
                rule="FEDERAL_LINK_RECORD_ABSENT",
                detail=detail, excerpt=canonical))
            records.append(FederalLinkRecord(
                ref=ref, reconciled=False, proof="primary record absent"))
            continue

        figures = figures_by_gid.get(ref.record_id, [])
        if ref.builder == USASPENDING_AWARD_BUILDER:
            if not figures:
                detail = (f"verified fact pack has no figure row for generated "
                          f"award id {ref.record_id} in {locations}")
                violations.append(LintViolation(
                    rule="FEDERAL_LINK_RECORD_ABSENT",
                    detail=detail, excerpt=canonical))
                records.append(FederalLinkRecord(
                    ref=ref, reconciled=False,
                    proof="generated id absent from figure registry"))
                continue
            row_award_id = str(row.get("award_id") or "")
            figure_award_ids = {
                str(figure.source_record_id or "") for figure in figures
            }
            if row_award_id not in figure_award_ids:
                detail = (f"generated award id {ref.record_id} resolves to "
                          f"{row_award_id or 'no PIID'}, but {locations} cites "
                          f"{sorted(figure_award_ids)}")
                violations.append(LintViolation(
                    rule="FEDERAL_LINK_RECORD_MISMATCH",
                    detail=detail, excerpt=canonical))
                records.append(FederalLinkRecord(
                    ref=ref, reconciled=False, proof="PIID/GID mismatch"))
                continue

        retrievals = [_parse_retrieved(row.get("retrieved_at"))
                      or default_retrieved]
        if figures:
            retrievals.extend(figure.retrieved_at for figure in figures)
        retrieved_at = (None if any(moment is None for moment in retrievals)
                        else min(retrievals))
        fact = Fact(
            id=f"F{index}", kind="link_record",
            text=f"Primary record identity for {canonical}",
            value={"canonical_url": canonical}, source=canonical,
            source_system=(SourceSystem.USASPENDING
                           if ref.builder == USASPENDING_AWARD_BUILDER
                           else SourceSystem.SAM),
            source_record_id=ref.record_id,
            retrieved_at=retrieved_at,
            freshness=(UNKNOWN_FRESHNESS if retrieved_at is None else None),
            disputed=bool(row.get("disputed", False)),
            competing_values=row.get("competing_values") or [],
        )
        pack = FactPack(client_name=content.client_name, as_of=now.date(),
                        facts=[fact])
        gate = freshness_violations(pack, {fact.id}, now=now)
        violations.extend(gate)
        reconciled = not gate
        when = (retrieved_at.isoformat() if retrieved_at else
                "UNKNOWN_FRESHNESS")
        proof = (f"{ref.builder} record {ref.record_id} retrieved {when}"
                 if reconciled else f"record gate failed: {gate[0].rule}")
        records.append(FederalLinkRecord(
            ref=ref, reconciled=reconciled, proof=proof))

    return FederalLinkReconciliation(records=records,
                                     violations=violations)


def figure_provenance_rows(content: SignalBoardContent) -> list[dict]:
    """The registry as data-current provenance rows (min retrieved_at feeds
    the evidence dock line). Attested rows without a retrieval time read as
    unknown and hold the computed line closed."""
    rows = []
    for fig in content.figures:
        rows.append({"retrieved_at": _parse_retrieved(fig.retrieved_at)})
    return rows
