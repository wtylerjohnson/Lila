"""Report-assets resolver for the Signal Board renderer.

Turns a client / agency / company into a base64 data-URI logo, resolved ONCE at
adapt time from the committed local brand-mark caches (tools/brand_marks). The
renderer never fetches at render time (review remediation): it receives resolved
data URIs, and falls back to a text code / monogram when no mark exists.
"""
from __future__ import annotations

import base64
import functools
from pathlib import Path
from typing import Optional

from tools import brand_marks as _bm

_MEDIA = {".png": "image/png", ".svg": "image/svg+xml", ".webp": "image/webp",
          ".jpg": "image/jpeg", ".jpeg": "image/jpeg"}
_INDEPENDENT_SEAL_KEYS = {
    "export-import bank of the us": "exim",
    "export-import bank of the united states": "exim",
    "exim": "exim",
    "federal deposit insurance corporation": "fdic",
    "fdic": "fdic",
    "library of congress": "loc",
    "loc": "loc",
    "senate, the": "senate",
    "united states senate": "senate",
    "u.s. senate": "senate",
    "national transportation safety board": "ntsb",
    "ntsb": "ntsb",
}
_COMPONENT_SEAL_KEYS = {
    "bureau of land management": "doi",
    "blm": "doi",
    "federal highway administration": "dot",
    "fhwa": "dot",
    "internal revenue service": "treas",
    "irs": "treas",
    "office of the comptroller of the currency": "treas",
    "office of comptroller of the currency": "treas",
    "occ": "treas",
    "irs · occ": "treas",
    "treasury, department of the": "treas",
    "department of the treasury": "treas",
    "housing and urban development, department of": "hud",
    "department of housing and urban development": "hud",
    "hud": "hud",
}


def _data_uri(path: Optional[Path]) -> str:
    """base64 data URI for a committed mark, or '' when absent/unreadable."""
    if not path:
        return ""
    try:
        p = Path(path)
        raw = p.read_bytes()
        if raw.startswith(b"\x89PNG\r\n\x1a\n"):
            media = "image/png"
        elif raw.startswith(b"\xff\xd8\xff"):
            media = "image/jpeg"
        elif raw.startswith(b"RIFF") and raw[8:12] == b"WEBP":
            media = "image/webp"
        elif raw.lstrip().startswith(b"<svg"):
            media = "image/svg+xml"
        else:
            media = _MEDIA.get(p.suffix.lower())
        if not media:
            return ""
        return f"data:{media};base64,{base64.b64encode(raw).decode()}"
    except OSError:
        return ""


def _seal_key(agency: str) -> Optional[str]:
    """Agency string or component code -> seal-cache key.

    A component renders its DEPARTMENT's seal (USPTO -> doc, USCIS -> dhs);
    resolution walks the curated agency catalog (exact abbr, then alias
    match, then parent) and never guesses from initials."""
    direct = _bm.recognize_agency_key(agency)
    if direct:
        return direct
    from tools.agencies import AGENCIES, matches_record
    low = agency.strip().lower()
    if low in _COMPONENT_SEAL_KEYS:
        return _COMPONENT_SEAL_KEYS[low]
    if low in _INDEPENDENT_SEAL_KEYS:
        return _INDEPENDENT_SEAL_KEYS[low]
    for row in AGENCIES:
        official = str(row.get("name") or "").strip().lower()
        short = official.removeprefix("department of ")
        if (low in {str(row.get("abbr") or "").lower(), official, short}
                or matches_record(agency, row)):
            dept = row["abbr"] if row.get("parent") is None else row["parent"]
            return _bm.recognize_agency_key(str(dept))
    return None


@functools.lru_cache(maxsize=256)
def agency_seal(agency: Optional[str], client_name: Optional[str] = None) -> str:
    """Data URI for an agency seal, or '' (renderer shows the text code)."""
    if not agency:
        return ""
    if client_name:
        from agents.reports.signal_board_presentation import (
            resolve_logo_override,
        )
        override = resolve_logo_override(
            client_name, kind="agency", label=str(agency))
        if override:
            return override
    return _data_uri(agency_seal_path(agency))


def agency_seal_path(agency: Optional[str]) -> Optional[Path]:
    """Resolve an agency label to its cached department/agency seal path."""
    if not agency:
        return None
    key = _seal_key(str(agency)) or str(agency)
    return _bm.find_mark(_bm.seals_dir(), key)


def client_assets_dir(client_name: str) -> Path:
    """Operator-curated per-client assets: clients/<slug>/assets/."""
    from tools.capability import _slug
    root = Path(__file__).resolve().parents[2]
    return root / "clients" / _slug(client_name) / "assets"


def _client_asset(client_name: str, stem: str) -> str:
    """First matching clients/<slug>/assets/<stem>.<ext>, as a data URI."""
    directory = client_assets_dir(client_name)
    if not directory.is_dir():
        return ""
    return _data_uri(_bm.find_mark(directory, stem))


@functools.lru_cache(maxsize=64)
def client_logo(client_name: Optional[str]) -> str:
    """Client logo: clients/<slug>/assets/logo.* wins, then the marks cache,
    then '' (text fallback). Never a broken image."""
    if not client_name:
        return ""
    from agents.reports.signal_board_presentation import resolve_logo_override
    override = resolve_logo_override(
        client_name, kind="client", label=str(client_name))
    if override:
        return override
    own = _client_asset(str(client_name), "logo")
    if own:
        return own
    # A replay worktree may carry the client's cached mark without carrying
    # the mutable review roster. The canonical slug is still the cache key;
    # roster recognition only adds aliases and must not be a precondition for
    # finding an already approved asset.
    from tools.slug import client_slug, legacy_client_slug
    key = (_bm.recognize_client_key(str(client_name))
           or client_slug(str(client_name)))
    found = _bm.find_mark(_bm.client_marks_dir(), key)
    if not found:
        # Marks cached before the slug consolidation carry the legacy
        # per-character shape; read them, never mint them.
        legacy = legacy_client_slug(client_name)
        if legacy != key:
            found = _bm.find_mark(_bm.client_marks_dir(), legacy)
    return _data_uri(found)


@functools.lru_cache(maxsize=1)
def gtm_logo() -> str:
    """Committed GTM Strategies identity mark as a portable data URI.

    The GTM mark is part of the report template, not client research.  It
    therefore comes from the codebase's locked template asset instead of a
    live lookup or an operator-managed cache.  A missing template constant is
    an installation defect and returns ``""`` so the renderer can show its
    explicit identity fallback rather than a broken image.
    """
    root = Path(__file__).resolve().parents[2]
    template = root / "agents" / "reports" / "templates"
    try:
        b64 = (template / "gtm_logo.b64").read_text(encoding="utf-8").strip()
        mime_path = template / "gtm_logo.mime"
        mime = (mime_path.read_text(encoding="utf-8").strip()
                if mime_path.exists() else "image/png")
    except OSError:
        return ""
    if not b64 or not mime.startswith("image/"):
        return ""
    return f"data:{mime};base64,{b64}"


@functools.lru_cache(maxsize=256)
def company_logo(name: Optional[str], client_name: Optional[str] = None) -> str:
    """Data URI for a partner/competitor company logo, or '' (text fallback)."""
    if not name:
        return ""
    if client_name:
        from agents.reports.signal_board_presentation import (
            resolve_logo_override,
        )
        override = resolve_logo_override(
            client_name, kind="company", label=str(name))
        if override:
            return override
    return _data_uri(_bm.find_mark(_bm.company_marks_dir(), _bm.company_slug(str(name))))


@functools.lru_cache(maxsize=256)
def partner_logo(client_name: Optional[str], company: Optional[str]) -> str:
    """Partner logo scoped to the client's curated assets first:
    clients/<slug>/assets/partners/<company_slug>.*, then the shared company
    cache, then '' (text fallback)."""
    if not company:
        return ""
    if client_name:
        from agents.reports.signal_board_presentation import (
            resolve_logo_override,
        )
        override = resolve_logo_override(
            client_name, kind="company", label=str(company))
        if override:
            return override
        directory = client_assets_dir(str(client_name)) / "partners"
        if directory.is_dir():
            own = _data_uri(_bm.find_mark(directory, _bm.company_slug(str(company))))
            if own:
                return own
    return company_logo(company)


def clear_caches() -> None:
    """Invalidate all in-process mark lookups after a presentation upload."""
    agency_seal.cache_clear()
    client_logo.cache_clear()
    gtm_logo.cache_clear()
    company_logo.cache_clear()
    partner_logo.cache_clear()
