"""Deterministic client-link audit and external GET classifier.

USAspending and SAM are never requested here: their validity is proven by
structured record reconciliation.  Every other HTTP(S) link is checked once
per day with a browser-like GET, bounded retry, and a worktree-local cache.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from enum import Enum
import hashlib
from html.parser import HTMLParser
import ipaddress
import json
import os
from pathlib import Path
import re
import socket
import time
from typing import Any, Callable, Iterable, Optional
from urllib.parse import urljoin, urlsplit

import httpx

from agents.reports.lint import LintViolation
from agents.reports.links import (
    SAM_NOTICE_BUILDER,
    USASPENDING_AWARD_BUILDER,
    build_sam_notice_link,
    build_usaspending_award_link,
    federal_link_domain,
    parse_client_anchors,
)
from tools.atomic_io import atomic_write_text


USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36 "
    "LILA-LinkIntegrity/1.0"
)
DEFAULT_TIMEOUT_S = 15.0
DEFAULT_BACKOFF_S = 0.25
DEFAULT_POLITENESS_S = 0.10
MAX_CACHED_TEXT = 2_000_000

_ROOT = Path(__file__).resolve().parents[2]
CACHE_VERSION = 2
MAX_REDIRECTS = 5


class LinkClass(str, Enum):
    BUILDER_RECONCILED = "BUILDER-DERIVED+RECONCILED"
    BUILDER_BLOCKED = "BUILDER-DERIVED+BLOCKED"
    OK = "OK"
    DEAD = "DEAD"
    UNVERIFIABLE = "UNVERIFIABLE"
    INVALID = "INVALID"


@dataclass(frozen=True)
class LinkOccurrence:
    url: str
    section: str
    builder: Optional[str] = None
    record_id: Optional[str] = None
    reconciled: bool = False
    problem: Optional[str] = None


@dataclass(frozen=True)
class LinkCheckResult:
    url: str
    classification: LinkClass
    sections: tuple[str, ...]
    proof: str
    status_code: Optional[int] = None
    final_url: Optional[str] = None
    text: str = ""
    from_cache: bool = False
    status_history: tuple[int, ...] = ()
    checked_at: Optional[str] = None


@dataclass(frozen=True)
class ClientLinkGateOutcome:
    """One post-render link-gate verdict ready for a release runner."""

    results: tuple[LinkCheckResult, ...]
    violations: tuple[LintViolation, ...]
    manual_checks: tuple[str, ...]
    claim_warnings: tuple[str, ...]


@dataclass(frozen=True)
class AgencyDocClaim:
    """One scalar claim extracted from an AGENCY_DOC Fact value."""

    source_system: str
    source_url: str
    source_record_id: Optional[str]
    text: str
    raw: Any
    fact_id: Optional[str] = None
    claim_field: Optional[str] = None


@dataclass(frozen=True)
class _BufferedResponse:
    status_code: int
    url: str
    headers: dict[str, str]
    text: str = ""


def extract_link_occurrences(html_text: str) -> list[LinkOccurrence]:
    """Extract clickable outbound anchors in document order."""
    occurrences: list[LinkOccurrence] = []
    for anchor in parse_client_anchors(html_text):
        url = anchor.href
        if not url.lower().startswith(("http://", "https://", "//")):
            continue
        duplicate = sorted(set(anchor.duplicate_attrs) & {
            "href", "data-link-builder", "data-link-record",
            "data-link-reconciled",
        })
        occurrences.append(LinkOccurrence(
            url=url,
            section=anchor.section,
            builder=anchor.attrs.get("data-link-builder"),
            record_id=anchor.attrs.get("data-link-record"),
            reconciled=anchor.attrs.get("data-link-reconciled") == "1",
            problem=(f"duplicate link attributes: {', '.join(duplicate)}"
                     if duplicate else None),
        ))
    return occurrences


def _is_spa_domain(url: str) -> bool:
    return federal_link_domain(url) is not None


def _cache_root(cache_dir: Optional[str | Path]) -> Path:
    configured = cache_dir or os.environ.get("LILA_LINK_CACHE_DIR")
    return Path(configured) if configured else _ROOT / "data" / "cache" / "links"


def _cache_path(cache_dir: Path, url: str, day: date) -> Path:
    digest = hashlib.sha256(url.encode("utf-8")).hexdigest()
    return cache_dir / day.isoformat() / f"{digest}.json"


class _UnsafeTarget(ValueError):
    pass


class _ResponseTooLarge(RuntimeError):
    pass


class _RedirectLimit(RuntimeError):
    pass


def _url_safety_problem(url: str) -> Optional[str]:
    try:
        parsed = urlsplit(url)
        if "%" in parsed.netloc:
            return "percent-encoding in URL authority is forbidden"
        if "\\" in parsed.netloc:
            return "backslash in URL authority is forbidden"
        host = parsed.hostname or ""
        _ = parsed.port  # force malformed-port validation
    except ValueError as exc:
        return f"malformed URL: {exc}"
    if parsed.scheme.lower() not in {"http", "https"}:
        return "URL must use an explicit http or https scheme"
    if parsed.username is not None or parsed.password is not None:
        return "URL credentials are forbidden"
    if not host:
        return "URL host is absent"
    try:
        host = host.encode("idna").decode("ascii")
    except UnicodeError:
        return "URL host is not valid IDNA"
    if host.endswith("."):
        return "trailing-dot hosts are forbidden"
    lowered = host.lower()
    if lowered == "localhost" or lowered.endswith((".localhost", ".local")):
        return "local hostnames are forbidden"
    try:
        address = ipaddress.ip_address(host.strip("[]"))
    except ValueError:
        return None
    if not address.is_global:
        return f"non-public IP target is forbidden ({address})"
    return None


def _resolve_public_target(url: str) -> None:
    parsed = urlsplit(url)
    port = parsed.port or (443 if parsed.scheme.lower() == "https" else 80)
    addresses = {
        result[4][0]
        for result in socket.getaddrinfo(
            parsed.hostname, port, type=socket.SOCK_STREAM)
    }
    if not addresses:
        raise OSError(f"DNS returned no addresses for {parsed.hostname}")
    unsafe = sorted(address for address in addresses
                    if not ipaddress.ip_address(address).is_global)
    if unsafe:
        raise _UnsafeTarget(
            f"DNS resolved to non-public address(es): {', '.join(unsafe)}")


def _validate_url_structure(url: str) -> None:
    problem = _url_safety_problem(url)
    if problem:
        raise _UnsafeTarget(problem)
    if _is_spa_domain(url):
        raise _UnsafeTarget(
            "federal SPA domains require builder reconciliation")


def _cached_class(payload: dict) -> Optional[LinkClass]:
    try:
        statuses = tuple(int(item) for item in payload.get("status_history", []))
    except (TypeError, ValueError):
        return None
    status = statuses[-1] if statuses else payload.get("status_code")
    try:
        status = int(status) if status is not None else None
    except (TypeError, ValueError):
        return None
    if status is not None and 200 <= status < 300:
        return LinkClass.OK
    if status in {404, 410}:
        return LinkClass.DEAD
    if (len(statuses) >= 2
            and all(500 <= item < 600 for item in statuses[-2:])):
        return LinkClass.DEAD
    if status is not None or payload.get("transport_error"):
        return LinkClass.UNVERIFIABLE
    return None


def _cache_history_valid(payload: dict, statuses: tuple[int, ...]) -> bool:
    """Accept only response histories the live classifier can produce."""
    if len(statuses) > 2:
        return False
    if len(statuses) == 2 and not 500 <= statuses[0] < 600:
        return False
    if statuses:
        try:
            if int(payload.get("status_code")) != statuses[-1]:
                return False
        except (TypeError, ValueError):
            return False
    elif payload.get("status_code") is not None:
        return False
    return True


def _read_cache(
    path: Path,
    url: str,
    day: date,
) -> Optional[LinkCheckResult]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if (payload.get("version") != CACHE_VERSION
                or payload.get("checked_on") != day.isoformat()
                or payload.get("url") != url):
            return None
        statuses = tuple(int(item)
                         for item in payload.get("status_history", []))
        if not _cache_history_valid(payload, statuses):
            return None
        classification = _cached_class(payload)
        if (classification is None
                or payload.get("classification") != classification.value):
            return None
        raw_final_url = payload.get("final_url")
        final_url = (str(raw_final_url)
                     if raw_final_url is not None else None)
        if final_url is not None:
            try:
                _validate_url_structure(final_url)
            except _UnsafeTarget:
                return None
        status = statuses[-1] if statuses else payload.get("status_code")
        proof = payload.get("proof")
        if not isinstance(proof, str) or not proof:
            return None
        return LinkCheckResult(
            url=url,
            classification=classification,
            sections=(),
            proof=proof,
            status_code=status,
            final_url=final_url,
            text=str(payload.get("text") or ""),
            from_cache=True,
            status_history=statuses,
            checked_at=str(payload.get("checked_at") or "") or None,
        )
    except (OSError, ValueError, KeyError, TypeError, IndexError):
        return None


def _write_cache(path: Path, result: LinkCheckResult, day: date) -> None:
    if result.classification in {
            LinkClass.INVALID, LinkClass.BUILDER_BLOCKED,
            LinkClass.BUILDER_RECONCILED}:
        return
    payload = {
        "version": CACHE_VERSION,
        "url": result.url,
        "checked_on": day.isoformat(),
        "checked_at": result.checked_at,
        "classification": result.classification.value,
        "proof": result.proof,
        "status_code": result.status_code,
        "status_history": list(result.status_history),
        "transport_error": (
            result.classification == LinkClass.UNVERIFIABLE
            and result.status_code is None),
        "final_url": result.final_url,
        "text": result.text[:MAX_CACHED_TEXT],
    }
    atomic_write_text(str(path), json.dumps(
        payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n")


def _response_url(response: Any, fallback: str) -> str:
    return str(getattr(response, "url", None) or fallback)


def _response_text(response: Any) -> str:
    try:
        return str(response.text or "")[:MAX_CACHED_TEXT]
    except Exception:  # noqa: BLE001 - body is optional proof, never a crash
        return ""


def _response_header(response: Any, name: str) -> Optional[str]:
    headers = getattr(response, "headers", None)
    if headers is None:
        return None
    try:
        direct = headers.get(name)
        if direct is not None:
            return direct
        lowered = name.lower()
        for key, value in headers.items():
            if str(key).lower() == lowered:
                return value
    except (AttributeError, TypeError):
        return None
    return None


def _fetch_terminal(
    url: str,
    fetch: Callable[[str], Any],
    validator: Callable[[str], None],
) -> Any:
    current = url
    for hop in range(MAX_REDIRECTS + 1):
        # Structural checks are mandatory even when tests or callers provide
        # a custom DNS resolver.  This also prevents an external redirect
        # from crossing into a SAM/USAspending SPA request.
        _validate_url_structure(current)
        if validator is not _validate_url_structure:
            validator(current)
        response = fetch(current)
        status = int(response.status_code)
        if not 300 <= status < 400:
            return response
        location = _response_header(response, "location")
        if not location:
            return response
        if hop == MAX_REDIRECTS:
            raise _RedirectLimit(f"more than {MAX_REDIRECTS} redirects")
        current = urljoin(_response_url(response, current), location)
    raise _RedirectLimit(f"more than {MAX_REDIRECTS} redirects")


def _classify_external(
    url: str,
    fetch: Callable[[str], Any],
    *,
    validator: Callable[[str], None],
    sleeper: Callable[[float], None],
    backoff_s: float,
) -> LinkCheckResult:
    statuses: list[int] = []
    errors: list[str] = []
    last_response = None
    checked_at = datetime.now(timezone.utc).isoformat()
    for attempt in range(2):
        try:
            response = _fetch_terminal(url, fetch, validator)
            last_response = response
            status = int(response.status_code)
            statuses.append(status)
        except _UnsafeTarget as exc:
            return LinkCheckResult(
                url=url, classification=LinkClass.INVALID, sections=(),
                proof=f"unsafe external link: {exc}", checked_at=checked_at)
        except (_ResponseTooLarge, _RedirectLimit) as exc:
            return LinkCheckResult(
                url=url, classification=LinkClass.UNVERIFIABLE, sections=(),
                proof=str(exc), checked_at=checked_at)
        except (httpx.TimeoutException, TimeoutError) as exc:
            errors.append(f"timeout: {exc}")
            if attempt == 0:
                sleeper(backoff_s)
                continue
            break
        except (httpx.RequestError, ConnectionError, OSError) as exc:
            errors.append(f"connection error: {exc}")
            if attempt == 0:
                sleeper(backoff_s)
                continue
            break

        final_url = _response_url(response, url)
        if 200 <= status < 300:
            return LinkCheckResult(
                url=url, classification=LinkClass.OK, sections=(),
                proof=f"GET {status} terminal {final_url}",
                status_code=status, final_url=final_url,
                text=_response_text(response),
                status_history=tuple(statuses), checked_at=checked_at,
            )
        if status in {404, 410}:
            return LinkCheckResult(
                url=url, classification=LinkClass.DEAD, sections=(),
                proof=f"GET {status} terminal {final_url}",
                status_code=status, final_url=final_url,
                text=_response_text(response),
                status_history=tuple(statuses), checked_at=checked_at,
            )
        if status in {403, 429}:
            return LinkCheckResult(
                url=url, classification=LinkClass.UNVERIFIABLE, sections=(),
                proof=f"GET {status} cannot verify access at {final_url}",
                status_code=status, final_url=final_url,
                status_history=tuple(statuses), checked_at=checked_at,
            )
        if 500 <= status < 600 and attempt == 0:
            sleeper(backoff_s)
            continue
        if 500 <= status < 600 and len(statuses) == 2 \
                and all(500 <= item < 600 for item in statuses):
            return LinkCheckResult(
                url=url, classification=LinkClass.DEAD, sections=(),
                proof=f"GET returned {statuses[0]} then {statuses[1]}",
                status_code=status, final_url=final_url,
                text=_response_text(response),
                status_history=tuple(statuses), checked_at=checked_at,
            )
        return LinkCheckResult(
            url=url, classification=LinkClass.UNVERIFIABLE, sections=(),
            proof=f"GET terminal status {status} is not proof of life",
            status_code=status, final_url=final_url,
            status_history=tuple(statuses), checked_at=checked_at,
        )

    proof = "; ".join(errors) or (
        f"GET attempts did not establish life (statuses {statuses})")
    return LinkCheckResult(
        url=url, classification=LinkClass.UNVERIFIABLE, sections=(),
        proof=proof[:400],
        status_code=(int(last_response.status_code)
                     if last_response is not None else None),
        final_url=(_response_url(last_response, url)
                   if last_response is not None else None),
        status_history=tuple(statuses), checked_at=checked_at,
    )


def audit_client_links(
    html_text: str,
    *,
    enabled: bool = True,
    as_of: Optional[date] = None,
    cache_dir: Optional[str | Path] = None,
    fetch: Optional[Callable[[str], Any]] = None,
    sleeper: Callable[[float], None] = time.sleep,
    backoff_s: float = DEFAULT_BACKOFF_S,
    politeness_s: float = DEFAULT_POLITENESS_S,
    extra_links: Iterable[tuple[str, str]] = (),
    expected_federal_links: Optional[Iterable[Any]] = None,
    resolver: Optional[Callable[[str], None]] = None,
) -> list[LinkCheckResult]:
    """Audit each unique outbound URL once, preserving all section labels.

    A SAM/USAspending anchor is reconciled only when its occurrence also
    appears in ``expected_federal_links``, the structured manifest retained by
    the renderer.  Omitting the manifest therefore fails federal links closed
    without ever sending an HTTP request to either SPA domain.
    """
    occurrences = extract_link_occurrences(html_text)
    occurrences.extend(
        LinkOccurrence(url=url, section=section)
        for url, section in extra_links
        if str(url).lower().startswith(("http://", "https://", "//"))
    )
    grouped: dict[str, list[LinkOccurrence]] = {}
    for occurrence in occurrences:
        grouped.setdefault(occurrence.url, []).append(occurrence)
    expected_federal: dict[str, list[tuple[str, str, bool]]] = {}
    if expected_federal_links is not None:
        for link in expected_federal_links:
            expected_federal.setdefault(str(getattr(link, "url", "")), []).append((
                str(getattr(link, "builder", "")),
                str(getattr(link, "record_id", "")),
                bool(getattr(link, "reconciled", False)),
            ))

    day = as_of or date.today()
    cache_root = _cache_root(cache_dir)

    def run(
        requester: Callable[[str], Any],
        validator: Callable[[str], None],
    ) -> list[LinkCheckResult]:
        audited: list[LinkCheckResult] = []
        fetched_misses = 0
        for url, rows in grouped.items():
            sections = tuple(dict.fromkeys(row.section for row in rows))
            if _is_spa_domain(url):
                records = tuple(dict.fromkeys(
                    row.record_id for row in rows if row.record_id))
                builders = tuple(dict.fromkeys(
                    row.builder for row in rows if row.builder))
                canonical_rows = []
                for row in rows:
                    try:
                        expected = (
                            build_usaspending_award_link(row.record_id or "").url
                            if row.builder == USASPENDING_AWARD_BUILDER
                            else build_sam_notice_link(row.record_id or "").url
                            if row.builder == SAM_NOTICE_BUILDER else ""
                        )
                    except ValueError:
                        expected = ""
                    canonical_rows.append(
                        not row.problem and row.reconciled and row.url == expected)
                actual_manifest = sorted(
                    (row.builder or "", row.record_id or "", row.reconciled)
                    for row in rows
                )
                manifest_matches = (
                    expected_federal_links is not None
                    and actual_manifest == sorted(expected_federal.get(url, []))
                )
                reconciled = (bool(rows) and all(canonical_rows)
                              and manifest_matches)
                proof = (f"builder={','.join(builders) or 'absent'}; "
                         f"record={','.join(records) or 'absent'}; "
                         f"manifest={'yes' if manifest_matches else 'no'}; "
                         f"reconciled={'yes' if reconciled else 'no'}")
                audited.append(LinkCheckResult(
                    url=url,
                    classification=(LinkClass.BUILDER_RECONCILED
                                    if reconciled
                                    else LinkClass.BUILDER_BLOCKED),
                    sections=sections, proof=proof,
                ))
                continue
            problems = tuple(dict.fromkeys(
                row.problem for row in rows if row.problem))
            safety_problem = _url_safety_problem(url)
            if problems or safety_problem:
                details = [*problems]
                if safety_problem:
                    details.append(safety_problem)
                audited.append(LinkCheckResult(
                    url=url, classification=LinkClass.INVALID,
                    sections=sections, proof="; ".join(details),
                ))
                continue
            if not enabled:
                audited.append(LinkCheckResult(
                    url=url, classification=LinkClass.UNVERIFIABLE,
                    sections=sections,
                    proof="checks disabled for replay/offline",
                ))
                continue
            path = _cache_path(cache_root, url, day)
            cached = _read_cache(path, url, day) if path.exists() else None
            if cached is not None:
                audited.append(LinkCheckResult(
                    **{**cached.__dict__, "sections": sections}))
                continue
            if fetched_misses:
                sleeper(politeness_s)
            result = _classify_external(
                url, requester, validator=validator,
                sleeper=sleeper, backoff_s=backoff_s)
            try:
                _write_cache(path, result, day)
            except OSError:
                # Cache persistence is an optimization; the live verdict is
                # still valid and must not disappear behind a cache outage.
                pass
            fetched_misses += 1
            audited.append(LinkCheckResult(
                **{**result.__dict__, "sections": sections}))
        return audited

    if fetch is not None:
        return run(fetch, resolver or _validate_url_structure)
    if not enabled:
        return run(lambda _url: None, _validate_url_structure)
    with httpx.Client(
        headers={"User-Agent": USER_AGENT},
        timeout=DEFAULT_TIMEOUT_S,
        follow_redirects=False,
    ) as client:
        def stream_get(url: str) -> _BufferedResponse:
            with client.stream("GET", url) as response:
                text = ""
                if 200 <= response.status_code < 300:
                    length = response.headers.get("content-length")
                    if length:
                        try:
                            if int(length) > MAX_CACHED_TEXT:
                                raise _ResponseTooLarge(
                                    f"response exceeds {MAX_CACHED_TEXT} bytes")
                        except ValueError:
                            pass
                    content = bytearray()
                    for chunk in response.iter_bytes():
                        content.extend(chunk)
                        if len(content) > MAX_CACHED_TEXT:
                            raise _ResponseTooLarge(
                                f"response exceeds {MAX_CACHED_TEXT} bytes")
                    encoding = response.encoding or "utf-8"
                    try:
                        text = bytes(content).decode(encoding, errors="replace")
                    except LookupError:
                        text = bytes(content).decode("utf-8", errors="replace")
                return _BufferedResponse(
                    status_code=response.status_code,
                    url=str(response.url),
                    headers=dict(response.headers),
                    text=text,
                )

        return run(stream_get, resolver or _resolve_public_target)


def dead_link_violations(
    results: Iterable[LinkCheckResult],
) -> list[LintViolation]:
    violations: list[LintViolation] = []
    for result in results:
        sections = ", ".join(result.sections)
        if result.classification == LinkClass.DEAD:
            violations.append(LintViolation(
                rule="external_link_dead",
                detail=(f"DEAD external link in section(s) {sections}: "
                        f"{result.url} ({result.proof})"),
                excerpt=result.url,
            ))
        elif result.classification == LinkClass.INVALID:
            violations.append(LintViolation(
                rule="external_link_invalid",
                detail=(f"INVALID external link in section(s) {sections}: "
                        f"{result.url} ({result.proof})"),
                excerpt=result.url,
            ))
        elif result.classification == LinkClass.BUILDER_BLOCKED:
            violations.append(LintViolation(
                rule="federal_link_record_blocked",
                detail=(f"federal link is not builder-derived and reconciled "
                        f"in section(s) {sections}: {result.url} "
                        f"({result.proof})"),
                excerpt=result.url,
            ))
    return violations


def manual_link_check_lines(
    results: Iterable[LinkCheckResult],
) -> list[str]:
    """One INTERNAL-sidecar line per unique unverifiable URL."""
    return [
        f"{result.url} · {', '.join(result.sections)} · {result.proof}"
        for result in results
        if result.classification == LinkClass.UNVERIFIABLE
    ]


_HIDDEN_TAGS = {"head", "script", "style", "noscript", "template"}
_VOID_TAGS = {
    "area", "base", "br", "col", "embed", "hr", "img", "input", "link",
    "meta", "param", "source", "track", "wbr",
}
_AMOUNT_TOKEN = re.compile(
    r"(?<![\w.])\$?\s*(?P<number>[0-9][0-9,]*(?:\.[0-9]+)?)"
    r"(?:(?P<short_scale>[bmk])\b|\s+"
    r"(?P<long_scale>billion|million|thousand)\b)?",
    re.I,
)
_AGENCY_DOC_CLAIM_FIELDS = ("amount", "value", "count", "date")


def _agency_doc_scalar_claims(value: Any) -> list[tuple[Optional[str], Any]]:
    """Return deterministic scalar figure/date/count values from a Fact value.

    Fact values are dictionaries, but matching their rendered prose would let
    an agency page satisfy the check without stating the cited value.  Prefer
    the four schema-neutral claim names and their suffixed forms (for example
    ``award_count`` or ``publication_date``).  A singleton scalar mapping is
    retained for older Fact producers that used a domain-specific field name.
    """
    if not isinstance(value, dict):
        candidates = [(None, value)]
    else:
        keys = [
            key for field in _AGENCY_DOC_CLAIM_FIELDS
            for key in value
            if str(key).lower() == field
        ]
        keys.extend(
            key for key in value
            if key not in keys
            and any(str(key).lower().endswith(f"_{field}")
                    for field in _AGENCY_DOC_CLAIM_FIELDS)
        )
        if not keys and len(value) == 1:
            keys = list(value)
        candidates = [(str(key), value[key]) for key in keys]

    scalar_types = (str, int, float, Decimal, date, datetime)
    return [
        (field, raw)
        for field, raw in candidates
        if raw is not None and not isinstance(raw, bool)
        and isinstance(raw, scalar_types)
    ]


def agency_doc_link_inputs(
    figures: Iterable[Any],
) -> tuple[list[tuple[str, str]], list[AgencyDocClaim]]:
    """Adapt AGENCY_DOC Facts into external links and scalar page claims.

    The function is deliberately duck-typed so board figures and Fact rows use
    the same release-gate path without importing either schema here.
    """
    links: list[tuple[str, str]] = []
    claims: list[AgencyDocClaim] = []
    for figure in figures:
        system = getattr(getattr(figure, "source_system", None), "value",
                         getattr(figure, "source_system", None))
        url = (getattr(figure, "source_url", None)
               or getattr(figure, "source", None))
        if system != "agency_doc" or not url:
            continue
        url = str(url)
        links.append((url, "Figure provenance"))
        raw = getattr(figure, "raw", None)
        if raw is None:
            raw = getattr(figure, "value", None)
        record_id = getattr(figure, "source_record_id", None)
        fact_id = getattr(figure, "fact_id", None) or getattr(figure, "id", None)
        is_structured = isinstance(raw, dict)
        existing_field = getattr(figure, "claim_field", None)
        for field, scalar in _agency_doc_scalar_claims(raw):
            field = field or existing_field
            existing_display = getattr(figure, "text", None)
            display = (
                str(existing_display)
                if not is_structured and existing_display
                else scalar.isoformat()
                if isinstance(scalar, (date, datetime))
                else str(scalar)
            )
            claims.append(AgencyDocClaim(
                source_system="agency_doc",
                source_url=url,
                source_record_id=record_id,
                text=display,
                raw=scalar,
                fact_id=(str(fact_id) if fact_id is not None else None),
                claim_field=field,
            ))
    return links, claims


class _VisibleTextParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.stack: list[tuple[str, bool]] = []

    def handle_starttag(self, tag, attrs) -> None:
        tag = tag.lower()
        values = {str(name).lower(): "" if value is None else str(value)
                  for name, value in attrs}
        style = re.sub(r"\s+", "", values.get("style", "")).lower()
        own_hidden = (
            tag in _HIDDEN_TAGS
            or "hidden" in values
            or values.get("aria-hidden", "").lower() == "true"
            or "display:none" in style
            or "visibility:hidden" in style
        )
        parent_hidden = self.stack[-1][1] if self.stack else False
        if tag not in _VOID_TAGS:
            self.stack.append((tag, parent_hidden or own_hidden))

    def handle_startendtag(self, tag, attrs) -> None:
        del tag, attrs

    def handle_endtag(self, tag) -> None:
        lowered = tag.lower()
        for index in range(len(self.stack) - 1, -1, -1):
            if self.stack[index][0] == lowered:
                del self.stack[index:]
                break

    def handle_data(self, data) -> None:
        if not self.stack or not self.stack[-1][1]:
            self.parts.append(data)


def _visible_page_text(body: str) -> str:
    parser = _VisibleTextParser()
    parser.feed(body or "")
    parser.close()
    return " ".join(" ".join(parser.parts).lower().split())


def _amount_values(text: str) -> set[Decimal]:
    scales = {
        "b": Decimal("1000000000"),
        "billion": Decimal("1000000000"),
        "m": Decimal("1000000"),
        "million": Decimal("1000000"),
        "k": Decimal("1000"),
        "thousand": Decimal("1000"),
    }
    values: set[Decimal] = set()
    for match in _AMOUNT_TOKEN.finditer(text):
        try:
            number = Decimal(match.group("number").replace(",", ""))
        except InvalidOperation:
            continue
        scale = (match.group("short_scale")
                 or match.group("long_scale") or "").lower()
        values.add(number * scales.get(scale, Decimal(1)))
    return values


def claim_value_visible(display: str, raw: Any, body: str) -> bool:
    """Exact numeric-equivalence check for common federal figure spellings."""
    visible = _visible_page_text(body)
    try:
        amount = Decimal(str(raw))
    except (InvalidOperation, TypeError, ValueError):
        needle = " ".join(str(display or "").lower().split())
        return bool(needle and needle in visible)
    display_values = _amount_values(str(display or ""))
    if display_values and amount not in display_values:
        return False
    return amount in _amount_values(visible)


def agency_doc_claim_warnings(
    figures: Iterable[Any],
    results: Iterable[LinkCheckResult],
) -> list[str]:
    """Warn when an OK AGENCY_DOC page does not visibly state its figure."""
    by_url = {result.url: result for result in results}
    warnings: list[str] = []
    _, claims = agency_doc_link_inputs(figures)
    for figure in claims:
        system = getattr(getattr(figure, "source_system", None), "value",
                         getattr(figure, "source_system", None))
        url = (getattr(figure, "source_url", None)
               or getattr(figure, "source", None))
        raw = getattr(figure, "raw", None)
        if raw is None:
            raw = getattr(figure, "value", None)
        if system != "agency_doc" or raw is None or not url:
            continue
        result = by_url.get(url)
        if result is None or result.classification != LinkClass.OK:
            continue
        display = str(getattr(figure, "text", raw))
        if not claim_value_visible(display, raw, result.text):
            record = (getattr(figure, "source_record_id", None)
                      or getattr(figure, "fact_id", None) or display)
            field = getattr(figure, "claim_field", None)
            suffix = f".{field}" if field else ""
            warnings.append(
                f"{record}{suffix}: value {display} is not visible in fetched text "
                f"at {url}")
    return warnings


def run_client_link_gate(
    html_text: str,
    *,
    enabled: bool = True,
    extra_links: Iterable[tuple[str, str]] = (),
    figures: Iterable[Any] = (),
    **audit_kwargs: Any,
) -> ClientLinkGateOutcome:
    """Run the shared link release gate over one final client HTML string."""
    figures = tuple(figures)
    derived_links, _ = agency_doc_link_inputs(figures)
    all_extra_links = tuple(dict.fromkeys(
        (*tuple(extra_links), *derived_links)))
    results = tuple(audit_client_links(
        html_text, enabled=enabled,
        extra_links=all_extra_links, **audit_kwargs))
    return ClientLinkGateOutcome(
        results=results,
        violations=tuple(dead_link_violations(results)),
        manual_checks=tuple(manual_link_check_lines(results)),
        claim_warnings=tuple(agency_doc_claim_warnings(figures, results)),
    )


def render_link_integrity_internal_md(
    label: str,
    outcome: ClientLinkGateOutcome,
) -> str:
    """Render the audit and warning-only manual list for an INTERNAL sidecar."""
    lines = [
        f"# INTERNAL · {label} · Link integrity",
        f"_{date.today().isoformat()} · never leaves the shop_",
        "",
        "## Link integrity audit",
    ]
    if outcome.results:
        lines.extend(
            f"- {result.classification.value} · {result.url} · "
            f"{', '.join(result.sections)} · {result.proof}"
            for result in outcome.results
        )
    else:
        lines.append("- no outbound HTTP(S) links")
    lines.append("")
    if outcome.manual_checks:
        lines.append("## MANUAL LINK CHECK")
        lines.extend(f"- {line}" for line in outcome.manual_checks)
        lines.append("")
    if outcome.claim_warnings:
        lines.append("## AGENCY_DOC claim-visibility warnings")
        lines.extend(f"- {line}" for line in outcome.claim_warnings)
        lines.append("")
    return "\n".join(lines)
