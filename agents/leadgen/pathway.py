"""ActionableExternalPathway: a public, already-published seller route."""

from __future__ import annotations

from typing import Literal, Optional
from urllib.parse import parse_qs, unquote, urlparse

from pydantic import Field, HttpUrl, model_validator

from agents.assess.contracts import EvidenceKind, EvidenceRef, EvidenceTier

from ._base import SCHEMA_VERSION, _FrozenContract
from .enums import Contactability, PathwayKind

_FORECAST_OR_NON_NOTICE = (
    EvidenceKind.AGENCY_FORECAST,
    EvidenceKind.AWARD,
    EvidenceKind.NEWS,
    EvidenceKind.WEB_LEAD,
)


class PublishedContact(_FrozenContract):
    """A government-published POC. Not an Apollo or invented person."""

    name: str = Field(min_length=1)
    title: Optional[str] = None
    source_url: Optional[HttpUrl] = None


class ActionableExternalPathway(_FrozenContract):
    """One evidence-bound public pathway. Not a live solicitation census row."""

    schema_version: Literal["leadgen.contracts.v1"] = SCHEMA_VERSION
    pathway_id: str = Field(min_length=1)
    kind: PathwayKind
    source_url: HttpUrl
    evidence: tuple[EvidenceRef, ...] = Field(min_length=1)
    notice_id: Optional[str] = None
    published_contacts: tuple[PublishedContact, ...] = Field(
        default_factory=tuple)
    contactability: Contactability = Contactability.ORGANIZATION_ONLY

    @model_validator(mode="after")
    def _pathway_is_published_and_kind_honest(self) -> "ActionableExternalPathway":
        if self.kind == PathwayKind.SAM_NOTICE:
            _require_sam_notice_pathway(self)
        elif any(item.kind in _FORECAST_OR_NON_NOTICE
                 and item.tier == EvidenceTier.NOTICE
                 for item in self.evidence):
            raise ValueError(
                "NOTICE-tier evidence cannot carry forecast, award, news, "
                "or web_lead kinds")
        if (self.kind == PathwayKind.PUBLISHED_POC
                and not self.published_contacts):
            raise ValueError(
                "published_poc pathway requires at least one published contact")
        return self


def _require_sam_notice_pathway(pathway: ActionableExternalPathway) -> None:
    if not pathway.notice_id:
        raise ValueError("sam_notice pathway requires notice_id")
    if any(item.kind in _FORECAST_OR_NON_NOTICE for item in pathway.evidence):
        raise ValueError(
            "sam_notice pathway cannot be proved by forecast, award, "
            "news, or web_lead")
    notice_evidence = [
        item for item in pathway.evidence
        if item.tier == EvidenceTier.NOTICE
        and item.kind == EvidenceKind.NOTICE
        and item.primary_source
    ]
    if not notice_evidence:
        raise ValueError(
            "sam_notice pathway requires NOTICE-tier primary notice evidence")
    evidence_urls = [urlparse(str(item.source_url)) for item in notice_evidence]
    if any(parsed.scheme != "https" for parsed in evidence_urls):
        raise ValueError("sam_notice evidence must be https")
    hosts = [(parsed.hostname or "").lower() for parsed in evidence_urls]
    if any(host != "sam.gov" and not host.endswith(".sam.gov") for host in hosts):
        raise ValueError("sam_notice evidence must resolve to SAM.gov")
    notice_key = pathway.notice_id.strip().lower()
    parsed_source = urlparse(str(pathway.source_url))
    if not _url_has_notice(parsed_source, notice_key):
        raise ValueError("sam_notice source_url must include the notice id")
    if not any(_url_has_notice(parsed, notice_key) for parsed in evidence_urls):
        raise ValueError(
            "sam_notice evidence URL must include the notice id")


def _url_has_notice(parsed, notice_key: str) -> bool:
    segments = [unquote(part).strip().lower()
                for part in parsed.path.split("/") if part.strip()]
    query_ids = [
        value.strip().lower()
        for key, values in parse_qs(parsed.query).items()
        if key.lower() in {"noticeid", "notice_id", "id"}
        for value in values
    ]
    return notice_key in segments or notice_key in query_ids
