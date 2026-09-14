"""Retained, identity-bound SAM description and attachment inventory for one notice.

Breadth comes from the daily extract. Verified description custody avoids repeat
metered acquisition until the posting changes; legacy unbound descriptions must
be reacquired. Keyless inventory reconciliation has the existing bounded TTL.

Endpoints (grounded from the Get Opportunities API docs, open.gsa.gov —
search results link each notice's `description` to noticedesc):
    GET https://api.sam.gov/prod/opportunities/v1/noticedesc
        ?noticeid=<id>&api_key=...          -> {"description": "<html>"}
    GET https://sam.gov/api/prod/opps/v3/opportunities/<id>/resources
        (keyless; used by the sam.gov frontend; treated as best-effort)
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional
from urllib.parse import parse_qsl, unquote, urlencode, urljoin, urlparse, urlunparse

import tools.api.sam_quota as sam_quota
from tools.api._http import get_json
from tools.api.sam_gov import SAM_HEADERS

DESC_URL = os.environ.get(
    "LILA_NOTICEDESC_URL", "https://api.sam.gov/prod/opportunities/v1/noticedesc")
RESOURCES_URL_TPL = os.environ.get(
    "LILA_NOTICE_RESOURCES_URL",
    "https://sam.gov/api/prod/opps/v3/opportunities/{id}/resources")
RESOURCE_DOWNLOAD_URL_TPL = os.environ.get(
    "LILA_NOTICE_RESOURCE_DOWNLOAD_URL",
    "https://sam.gov/api/prod/opps/v3/opportunities/resources/files/"
    "{resource_id}/download",
)
_DEFAULT_CACHE = Path(__file__).resolve().parents[2] / "data" / "cache" / "sam_notice"
RESOURCES_HEADERS = {**SAM_HEADERS, "Accept": "application/hal+json"}
RESOURCE_CACHE_TTL_SECONDS = int(os.environ.get(
    "LILA_NOTICE_RESOURCES_TTL_SECONDS", str(6 * 60 * 60)))
RESOURCE_CACHE_MAX_STALE_SECONDS = int(os.environ.get(
    "LILA_NOTICE_RESOURCES_MAX_STALE_SECONDS", str(7 * 24 * 60 * 60)))
_PUBLIC_NOTICE_ID = re.compile(r"^[0-9a-f]{32}$", re.IGNORECASE)
DEPTH_SCHEMA = 'sam_depth_capture_v2'


def _cache_dir() -> Path:
    return Path(os.environ.get("LILA_NOTICE_CACHE_DIR", str(_DEFAULT_CACHE)))


def strip_html(html_text: str) -> str:
    text = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", html_text or "", flags=re.S | re.I)
    text = re.sub(r"<br\s*/?>|</p>|</div>|</li>", "\n", text, flags=re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    text = text.replace("&nbsp;", " ").replace("&amp;", "&").replace("&lt;", "<") \
               .replace("&gt;", ">").replace("&quot;", '"').replace("&#39;", "'")
    return re.sub(r"[ \t]{2,}", " ", re.sub(r"\n{3,}", "\n\n", text)).strip()


def _attachments(payload: Any) -> Optional[list[dict]]:
    """Attachment names from the resources payload, schema-defensively."""
    stack = [payload]
    found: list[dict] = []
    while stack:
        cur = stack.pop()
        if isinstance(cur, dict):
            name = cur.get("name") or cur.get("fileName") or cur.get("resourceName")
            resource_id = cur.get("resourceId")
            if name and (resource_id or cur.get("attachmentId")
                         or cur.get("uri") or cur.get("mimeType")):
                raw_url = (cur.get("downloadUrl") or cur.get("resourceUrl")
                           or cur.get("uri"))
                try:
                    source_url = urljoin("https://sam.gov", str(raw_url)) if raw_url else None
                    parsed = urlparse(source_url) if source_url else None
                except ValueError:
                    source_url, parsed = None, None
                host = (parsed.hostname or "").lower() if parsed else ""
                if host != "sam.gov" and not host.endswith(".sam.gov"):
                    source_url = None
                if source_url is None and resource_id:
                    source_url = RESOURCE_DOWNLOAD_URL_TPL.format(
                        resource_id=resource_id)
                found.append({
                    "name": name,
                    "type": cur.get("mimeType") or cur.get("type"),
                    "resource_id": resource_id or cur.get("attachmentId"),
                    "source_url": source_url,
                    "size": cur.get("size"),
                    "access_level": cur.get("accessLevel"),
                    "access_status": cur.get("accessStatus"),
                    "export_controlled": cur.get("exportControlled"),
                    "explicit_access": cur.get("explicitAccess"),
                    **{normalized: cur[original] for original, normalized in (
                        ('deletedFlag', 'deleted_flag'), ('deletedDate', 'deleted_date'),
                        ('fileExists', 'file_exists')) if original in cur},
                })
            stack.extend(cur.values())
        elif isinstance(cur, list):
            stack.extend(cur)
    return found or None


def _notice_link_state(value: Any, notice_id: str) -> str:
    """Inspect original-notice carriers only; malformed links prove no conflict."""
    if not isinstance(value, str):
        return 'uninspectable' if value is not None else 'unbound'
    try:
        parsed = urlparse(value)
        parts = unquote(parsed.path).split('/')
    except ValueError:
        return 'uninspectable'
    ids = []
    for i, part in enumerate(parts[:-1]):
        candidate = parts[i + 1]
        if (part.casefold() in ('opportunities', 'opp') and candidate.casefold() != 'resources'
                and (_PUBLIC_NOTICE_ID.fullmatch(candidate) or candidate.casefold() == notice_id.casefold())):
            ids.append(candidate)
    for key, candidate in parse_qsl(parsed.query, keep_blank_values=True):
        if key.casefold() in ('noticeid', 'notice_id', 'opportunityid', 'opportunity_id'):
            if candidate:
                ids.append(candidate)
    fragment = unquote(parsed.fragment)
    if _PUBLIC_NOTICE_ID.fullmatch(fragment):
        ids.append(fragment)
    else:
        ids.extend(candidate for key, candidate in parse_qsl(fragment, keep_blank_values=True)
                   if key.casefold() in ('noticeid', 'notice_id', 'opportunityid', 'opportunity_id') and candidate)
    if any(candidate.casefold() != notice_id.casefold() for candidate in ids):
        return 'conflict'
    return 'match' if ids else 'unbound'


def _notice_link_matches(value: Any, notice_id: str) -> bool:
    return _notice_link_state(value, notice_id) != 'conflict'


def _resource_identity_matches(payload: Any, notice_id: str, *, gaps: list | None = None) -> bool:
    stack = [payload]
    while stack:
        item = stack.pop()
        if isinstance(item, dict):
            for key in ("opportunityId", "noticeId", "notice_id"):
                if key in item and item[key] not in (None, "") and (
                        not isinstance(item[key], str) or item[key].casefold() != notice_id.casefold()):
                    return False
            for key in ('href', 'downloadUrl', 'resourceUrl', 'uri', 'source_url'):
                if gaps is not None and key in item and _notice_link_state(item[key], notice_id) == 'uninspectable':
                    gaps.append('uninspectable response link: ' + key)
                if not _notice_link_matches(item.get(key), notice_id):
                    return False
            stack.extend(item.values())
        elif isinstance(item, list):
            stack.extend(item)
    return True


def attachment_withdrawn(item: dict) -> bool:
    open_flags = {'', '0', 'false', 'no', 'n', 'none'}
    return (str(item.get('deleted_flag') or '').strip().casefold() not in open_flags
            or bool(item.get('deleted_date'))
            or ('file_exists' in item and str(item['file_exists']).strip().casefold()
                in {'0', 'false', 'no', 'n'}))


def _attachment_inventory(payload: Any, notice_id: str | None = None, *,
                          include_withdrawn: bool = False
                          ) -> tuple[Optional[list[dict]], bool]:
    """Return ``(items, schema_recognized)`` for the known resources shapes.

    An HTTP 200 with an unfamiliar JSON object is not evidence of zero
    attachments. Confirmed zero requires a recognized container whose walk
    yields no attachment rows.
    """
    if notice_id is not None and not _resource_identity_matches(payload, notice_id):
        return None, False
    containers: list[list] = []
    if isinstance(payload, list):
        containers.append(payload)
    elif isinstance(payload, dict):
        for key in ("resources", "attachments", "opportunityResources",
                    "opportunityAttachmentList", "items", "results"):
            if isinstance(payload.get(key), list):
                containers.append(payload[key])
        embedded = payload.get("_embedded")
        if isinstance(embedded, list):
            containers.append(embedded)
        elif isinstance(embedded, dict):
            for key in ("resources", "attachments", "opportunityResources",
                        "opportunityAttachmentList", "items", "results"):
                if isinstance(embedded.get(key), list):
                    containers.append(embedded[key])
    if not containers:
        return None, False
    found = _attachments(containers) or []
    if any(container for container in containers) and not found:
        # A non-empty known container whose rows do not resemble attachment
        # records is schema drift or an error wrapper, not confirmed zero.
        return None, False
    # The HAL walk order is not contractual. Normalize it so an equivalent
    # response cannot change approval evidence merely by reordering entries.
    by_identity: dict[str, list[tuple[str, dict]]] = {}
    for item in found:
        identity = str(
            item.get("resource_id") or item.get("source_url")
            or item.get("name") or "")
        canonical = json.dumps(
            item, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        by_identity.setdefault(identity, []).append((canonical, item))
    normalized = []
    open_flags = {"", "0", "false", "no", "n"}
    for identity in sorted(by_identity):
        entries = by_identity[identity]
        chosen = dict(min(entries, key=lambda entry: entry[0])[1])
        for key in ("access_level", "access_status"):
            values = {
                str(item.get(key) or "").strip().casefold()
                for _, item in entries
            }
            restricted = sorted(
                value for value in values if value and value != "public")
            if restricted:
                chosen[key] = restricted[0]
            elif "public" in values:
                chosen[key] = "public"
        for key in ("export_controlled", "explicit_access"):
            values = {
                str(item.get(key) or "").strip().casefold()
                for _, item in entries
            }
            chosen[key] = "1" if any(
                value not in open_flags for value in values) else "0"
        sizes = []
        for _, item in entries:
            try:
                sizes.append(int(item.get("size") or 0))
            except (TypeError, ValueError):
                sizes.append(0)
        if sizes:
            chosen["size"] = max(sizes)
        if any(attachment_withdrawn(item) for _, item in entries):
            chosen['deleted_flag'] = '1'
            dates = sorted(str(item['deleted_date']) for _, item in entries
                           if item.get('deleted_date'))
            if dates:
                chosen['deleted_date'] = dates[-1]
        if include_withdrawn or not attachment_withdrawn(chosen):
            normalized.append(chosen)
    return normalized, True


def _withdrawn_inventory(payload: Any, notice_id: str) -> list[dict]:
    items, _ = _attachment_inventory(payload, notice_id, include_withdrawn=True)
    return [item for item in (items or []) if attachment_withdrawn(item)]


def _capture_origin_valid(capture: dict, notice_id: str, *, requested: str | None = None) -> bool:
    requested = requested or RESOURCES_URL_TPL.format(id=notice_id)
    chain = capture.get('redirect_chain')
    if (capture.get('requested_url') != requested or not isinstance(chain, list)
            or not chain or chain[0] != requested or chain[-1] != capture.get('source_url')):
        return False
    for value in chain:
        if not isinstance(value, str):
            return False
        try:
            parsed = urlparse(value)
            host = (parsed.hostname or '').casefold()
        except ValueError:
            return False
        parts = unquote(parsed.path).split('/')
        notice_route = any(part.casefold() in ('opportunities', 'opp')
                           and parts[index + 1].casefold() == notice_id.casefold()
                           for index, part in enumerate(parts[:-1]))
        description_route = (parsed.path.rstrip('/').casefold().endswith('/noticedesc')
            and any(key.casefold() in ('noticeid', 'notice_id') and val.casefold() == notice_id.casefold()
                    for key, val in parse_qsl(parsed.query)))
        if (parsed.scheme != 'https' or parsed.username or parsed.password
                or not (host == 'sam.gov' or host.endswith('.sam.gov'))
                or _notice_link_state(value, notice_id) != 'match'
                or not (notice_route or description_route)):
            return False
    return True


def _manifest_hash(items: list[dict]) -> str:
    raw = json.dumps(items, sort_keys=True, separators=(",", ":"),
                     ensure_ascii=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _timestamp(value: Any) -> Optional[datetime]:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str) and value.strip():
        try:
            parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _resource_cache_age(payload: dict) -> Optional[float]:
    capture = payload.get("raw_capture")
    stamp = capture.get("received_at") if isinstance(capture, dict) else payload.get("retrieved_at")
    retrieved = _timestamp(stamp)
    if retrieved is None:
        return None
    return (datetime.now(timezone.utc) - retrieved).total_seconds()


def _resource_cache_fresh(payload: dict) -> bool:
    age = _resource_cache_age(payload)
    return age is not None and 0 <= age <= RESOURCE_CACHE_TTL_SECONDS


def fetch_notice_resources(notice_id: str, *, timeout_seconds: float = 15.0,
                           force_refresh: bool = False) -> dict:
    """Return the public attachment inventory for one notice, cached.

    SAM's public resources route is keyless but requires the HAL media type.
    A recognized empty list is cached; an error or unknown schema is not, so a
    transient response cannot become a permanent false claim of no files.
    """
    if not _PUBLIC_NOTICE_ID.fullmatch(str(notice_id or "")):
        raise ValueError("public SAM notice_id must be 32 hexadecimal characters")
    notice_id = notice_id.casefold()
    cache = _cache_dir() / f"{notice_id}.resources.json"
    cached = None
    if cache.exists():
        try:
            cached = json.loads(cache.read_text())
            if not isinstance(cached, dict) or cached.get("id") != notice_id:
                cached = None
        except (ValueError, OSError):
            cached = None
        if not force_refresh and cached is not None and _resource_cache_fresh(cached) and _resource_capture_valid(cached):
            cached["from_cache"] = True
            cached["stale_cache"] = False
            return cached
    out = {
        "id": notice_id,
        "attachments": None,
        "retrieved_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "resources_checked": False,
        "resources_schema": None,
        "errors": [],
        "collection_status": "lookup_failed",
        "authority": "discovery_only",
    }
    def capture(response, raw, complete):
        from tools.api.sam_capture import retain_bytes
        out["raw_capture"] = {
            "complete": complete,
            "http_status": response.status_code,
            "requested_url": RESOURCES_URL_TPL.format(id=notice_id),
            "source_url": str(response.url),
            "redirect_chain": [str(r.url) for r in [*response.history, response]],
            "received_at": datetime.now(timezone.utc).isoformat(),
            "representation": "decoded_http_entity_bytes",
        }
        try:
            out['raw_capture'].update(retain_bytes(_cache_dir(), raw))
        except (OSError, ValueError) as exc:
            out['raw_capture'].update(sha256=hashlib.sha256(raw).hexdigest(),
                                      bytes=len(raw), retained=False)
            out['retention_error'] = str(exc)[:300]
            out['collection_status'] = 'retention_failed'
        if not _capture_origin_valid(out['raw_capture'], notice_id):
            out['collection_status'] = 'identity_mismatch'
            raise ValueError('resources response origin or notice link mismatch')

    try:
        payload = get_json(
            RESOURCES_URL_TPL.format(id=notice_id),
            timeout=max(0.25, min(15.0, timeout_seconds)),
            headers=RESOURCES_HEADERS, retries=1,
            response_observer=capture, max_response_bytes=2 * 1024 * 1024,
        )
        if not _resource_identity_matches(payload, notice_id, gaps=out['errors']):
            out["collection_status"] = "identity_mismatch"
            raise ValueError("resources notice identity mismatch")
        attachments, recognized = _attachment_inventory(payload, notice_id)
        if not recognized:
            out["collection_status"] = "unrecognized_inventory"
            raise ValueError("unrecognized resources schema")
        if out.get('retention_error'):
            out['collection_status'] = 'retention_failed'
            out['diagnostic_inventory'] = attachments
            raise ValueError('inventory parsed but original-byte retention failed')
        out.update({
            "attachments": attachments,
            "withdrawn_attachments": _withdrawn_inventory(payload, notice_id),
            "resources_checked": True,
            "resources_schema": "recognized_v1",
            "attachment_inventory_count": len(attachments or []),
            "attachment_inventory_hash": _manifest_hash(attachments or []),
            "collection_status": "inventory_captured" if attachments else "confirmed_empty",
        })
        _cache_dir().mkdir(parents=True, exist_ok=True)
        from tools.artifacts import atomic_write_json
        atomic_write_json(cache, out)
    except Exception as exc:  # noqa: BLE001 - one notice never sinks breadth
        if out.get('retention_error'):
            out['source_collection_status'] = out['collection_status']
            out['collection_status'] = 'retention_failed'
            out['errors'].append('local retention failed: ' + out['retention_error'])
        cached_age = _resource_cache_age(cached or {})
        if cached is not None and cached.get("resources_checked") is True \
                and cached.get('id') == notice_id and _resource_capture_valid(cached) \
                and cached_age is not None \
                and 0 <= cached_age <= RESOURCE_CACHE_MAX_STALE_SECONDS:
            cached.setdefault("errors", []).append(
                f"resources refresh failed; using stale inventory: {exc}")
            cached["from_cache"] = True
            cached["stale_cache"] = True
            cached["collection_status"] = "stale_inventory"
            cached["refresh_attempt"] = out
            return cached
        out["errors"].append(f"resources fetch failed: {exc}")
    out["from_cache"] = False
    out["stale_cache"] = False
    return out


def _resource_capture_valid(cached: dict) -> bool:
    """Current cache reuse needs its original response and exact projection."""
    from tools.api.sam_capture import read_retained
    try:
        if not isinstance(cached, dict) or not isinstance(cached.get('errors'), list):
            return False
        capture = cached["raw_capture"]
        if not isinstance(capture, dict) or capture.get("complete") is not True or capture.get("http_status") != 200:
            return False
        if not _capture_origin_valid(capture, cached['id']):
            return False
        received = datetime.fromisoformat(capture["received_at"].replace("Z", "+00:00"))
        if received.utcoffset() is None or received > datetime.now(timezone.utc):
            return False
        raw = read_retained(_cache_dir(), capture)
        payload = json.loads(raw)
        items, recognized = _attachment_inventory(payload, cached["id"])
        return (recognized and cached.get("attachments") == items
                and cached.get('withdrawn_attachments') == _withdrawn_inventory(payload, cached['id'])
                and cached.get('authority') == 'discovery_only'
                and cached.get('resources_schema') == 'recognized_v1'
                and cached.get('collection_status') == ('inventory_captured' if items else 'confirmed_empty')
                and cached.get("resources_checked") is True
                and cached.get("attachment_inventory_count") == len(items or [])
                and cached.get("attachment_inventory_hash") == _manifest_hash(items or []))
    except (AttributeError, KeyError, TypeError, ValueError, OSError):
        return False


def _safe_error(exc: Any) -> str:
    text = str(exc).encode('utf-8', errors='backslashreplace').decode()
    return re.sub(r'(?i)(api_key|apikey|token)=([^&\s]+)', r'\1=REDACTED', text)[:300]


def _public_capture_url(value: Any) -> str:
    parsed = urlparse(str(value))
    query = [(key, val) for key, val in parse_qsl(parsed.query, keep_blank_values=True)
             if key.casefold() not in ('api_key', 'apikey', 'token')]
    return urlunparse(parsed._replace(query=urlencode(query)))


def _description_url(notice_id: str) -> str:
    return _public_capture_url(DESC_URL + '?' + urlencode({'noticeid': notice_id}))


def _captured_payload(capture: dict, notice_id: str, requested: str) -> Any:
    from tools.api.sam_capture import read_retained
    if (capture.get('complete') is not True or capture.get('http_status') != 200
            or not _capture_origin_valid(capture, notice_id, requested=requested)):
        raise ValueError('unbound or incomplete SAM response')
    clock = datetime.fromisoformat(capture['received_at'].replace('Z', '+00:00'))
    if clock.utcoffset() is None or clock > datetime.now(timezone.utc):
        raise ValueError('invalid SAM capture clock')
    payload = json.loads(read_retained(_cache_dir(), capture))
    if not _resource_identity_matches(payload, notice_id):
        raise ValueError('SAM response notice identity mismatch')
    return payload


def _description_capture_valid(data: dict, notice_id: str) -> bool:
    try:
        capture = data['description_capture']
        payload = _captured_payload(capture, notice_id, _description_url(notice_id))
        raw = payload.get('description') if isinstance(payload, dict) else None
        text = strip_html(raw)[:20000] if isinstance(raw, str) else None
        if text:
            text.encode('utf-8')
        return (data.get('description_checked') is True and bool(text)
                and data.get('description') == text
                and data.get('retrieved_at') == capture['received_at'])
    except (AttributeError, KeyError, TypeError, ValueError, OSError):
        return False


def _depth_inventory_valid(data: dict, notice_id: str) -> bool:
    try:
        capture = data['resources_capture']
        candidate = {**data, 'id': notice_id, 'raw_capture': capture,
                     'authority': 'discovery_only',
                     'collection_status': 'inventory_captured' if data['attachments'] else 'confirmed_empty'}
        return (data.get('depth_schema') == DEPTH_SCHEMA
                and type(data.get('attachment_inventory_count')) is int
                and isinstance(data.get('withdrawn_attachments'), list)
                and all(isinstance(row, dict) for row in data['withdrawn_attachments'])
                and _resource_capture_valid(candidate) and _resource_cache_fresh(candidate))
    except (AttributeError, KeyError, TypeError, ValueError, OSError):
        return False


def _depth_shape_valid(data: Any, notice_id: str) -> bool:
    if not isinstance(data, dict) or str(data.get('id', '')).casefold() != notice_id.casefold():
        return False
    try:
        clock = data.get('retrieved_at')
        valid_clock = clock is None or (isinstance(clock, str)
            and datetime.fromisoformat(clock.replace('Z', '+00:00')).utcoffset() is not None)
        return (valid_clock and _resource_identity_matches(data, notice_id)
                and isinstance(data.get('errors', []), list)
                and all(isinstance(x, str) for x in data.get('errors', []))
                and isinstance(data.get('description'), (str, type(None)))
                and all(type(data.get(key)) is bool for key in ('description_checked', 'resources_checked'))
                and isinstance(data.get('attachments'), (list, type(None)))
                and isinstance(data.get('withdrawn_attachments', []), list)
                and all(isinstance(x, dict) for key in ('attachments', 'withdrawn_attachments')
                        for x in (data.get(key) or []))
                and (data.get('attachment_inventory_count') is None
                     or type(data.get('attachment_inventory_count')) is int)
                and (data.get('attachment_inventory_hash') is None or
                     isinstance(data.get('attachment_inventory_hash'), str)
                     and re.fullmatch('[0-9a-f]{64}', data['attachment_inventory_hash']) is not None))
    except (AttributeError, TypeError, ValueError):
        return False


def _depth_inventory_snapshot(data: dict) -> dict:
    return {key: (data.get(key) if data.get(key) is None or type(data.get(key)) in (bool, int)
                  else _safe_error(data.get(key)))
            for key in ('attachment_inventory_count', 'attachment_inventory_hash',
                        'resources_checked', 'description_checked')}


def fetch_notice_depth(notice_id: str, api_key: str | None = None,
                       min_retrieved_at: Any = None) -> dict:
    """Retained same-notice depth; legacy records require independent reconciliation."""
    if not _PUBLIC_NOTICE_ID.fullmatch(str(notice_id or '')):
        raise ValueError('SAM notice_id must be 32 hexadecimal characters')
    notice_id = notice_id.casefold()
    api_key = api_key or os.environ.get('SAM_GOV_API_KEY')
    cache = _cache_dir() / f'{notice_id}.json'
    original = None
    data = {}
    try:
        original = cache.read_bytes()
        loaded = json.loads(original)
        if isinstance(loaded, dict):
            data = loaded
    except (OSError, ValueError):
        pass
    shape_ok = _depth_shape_valid(data, notice_id)
    minimum = _timestamp(min_retrieved_at)
    cached_at = _timestamp(data.get('retrieved_at'))
    stale_for_posting = bool(minimum and (cached_at is None or cached_at < minimum))
    description_ok = shape_ok and _description_capture_valid(data, notice_id) and not stale_for_posting
    inventory_ok = shape_ok and _depth_inventory_valid(data, notice_id)
    resources_at = _timestamp((data.get('resources_capture') or {}).get('received_at')) \
        if isinstance(data.get('resources_capture'), dict) else None
    if minimum and (resources_at is None or resources_at < minimum):
        inventory_ok = False
    out = {'id': notice_id, 'depth_schema': DEPTH_SCHEMA, 'description': None,
           'description_checked': False, 'retrieved_at': None, 'attachments': None,
           'withdrawn_attachments': [], 'resources_checked': False, 'resources_schema': None,
           'attachment_inventory_count': None, 'attachment_inventory_hash': None,
           'errors': [], 'from_cache': bool(description_ok)}
    if isinstance(data.get('legacy_depth_capture'), dict):
        try:
            from tools.api.sam_capture import read_retained
            read_retained(_cache_dir(), data['legacy_depth_capture'])
            out['legacy_depth_capture'] = {key: data['legacy_depth_capture'][key]
                                           for key in ('path', 'sha256', 'bytes')}
        except (KeyError, OSError, TypeError, ValueError):
            out['errors'].append('historical depth receipt unavailable; original history remains untrusted')
    if description_ok:
        for key in ('description', 'description_checked', 'retrieved_at', 'description_capture'):
            out[key] = data[key]
    elif api_key:
        requested = _description_url(notice_id)
        def observe(response, raw, complete):
            from tools.api.sam_capture import retain_bytes
            capture = {'complete': complete, 'http_status': response.status_code,
                       'requested_url': requested, 'source_url': _public_capture_url(response.url),
                       'redirect_chain': [_public_capture_url(r.url) for r in [*response.history, response]],
                       'received_at': datetime.now(timezone.utc).isoformat(),
                       'representation': 'decoded_http_entity_bytes'}
            out['description_capture'] = capture
            try:
                capture.update(retain_bytes(_cache_dir(), raw))
            except (OSError, ValueError) as exc:
                capture.update(retained=False, sha256=hashlib.sha256(raw).hexdigest(), bytes=len(raw))
                raise ValueError('description response retention failed: ' + _safe_error(exc)) from exc
            if not _capture_origin_valid(capture, notice_id, requested=requested):
                raise ValueError('description response origin or notice identity mismatch')
        try:
            sam_quota.note_call('deep')
            get_json(DESC_URL, params={'noticeid': notice_id, 'api_key': api_key}, timeout=60.0,
                     headers=SAM_HEADERS, retries=1, response_observer=observe,
                     max_response_bytes=2 * 1024 * 1024)
            # Parse retained bytes, not an independently returned projection.
            payload = _captured_payload(out['description_capture'], notice_id, requested)
            raw = payload.get('description') if isinstance(payload, dict) else None
            text = strip_html(raw)[:20000] if isinstance(raw, str) else None
            if not text:
                raise ValueError('noticedesc returned no description')
            text.encode('utf-8')
            out.update(description=text, description_checked=True,
                       retrieved_at=out['description_capture']['received_at'], from_cache=False)
        except Exception as exc:  # one notice remains a named gap
            out['errors'].append('description fetch failed: ' + _safe_error(exc))
    else:
        out['errors'].append('untrusted or stale description cache; verified acquisition unavailable without SAM key')
    if inventory_ok and not stale_for_posting:
        for key in ('attachments', 'withdrawn_attachments', 'resources_checked', 'resources_schema',
                    'attachment_inventory_count', 'attachment_inventory_hash', 'resources_capture'):
            out[key] = data[key]
    else:
        try:
            resources = fetch_notice_resources(notice_id, force_refresh=stale_for_posting or bool(
                minimum and (resources_at is None or resources_at < minimum)))
            out['resources_attempt_status'] = resources.get('collection_status')
            if isinstance(resources.get('raw_capture'), dict):
                out['resources_capture'] = resources['raw_capture']
            if resources.get('stale_cache') or not _resource_capture_valid(resources):
                raise ValueError('resources reconciliation unavailable: ' + '; '.join(
                    _safe_error(err) for err in resources.get('errors') or [])
                    + ' [' + str(resources.get('collection_status')) + ']')
            for key in ('attachments', 'withdrawn_attachments', 'resources_checked', 'resources_schema',
                        'attachment_inventory_count', 'attachment_inventory_hash'):
                out[key] = resources[key]
        except Exception as exc:
            out['errors'].append('resources retry failed (non-fatal): ' + _safe_error(exc))
    migrating = original is not None and (data.get('depth_schema') != DEPTH_SCHEMA or not shape_ok
        or data.get('description_checked') is True and not _description_capture_valid(data, notice_id)
        or data.get('resources_checked') is True and not _depth_inventory_valid(data, notice_id))
    if migrating:
        out['migration'] = {'schema': 'depth_migration_v2',
            'before': _depth_inventory_snapshot(data), 'after': _depth_inventory_snapshot(out),
            'reason': 'legacy or malformed depth requires retained same-notice capture and lifecycle projection',
            'inventory_reconciled': out['resources_checked'], 'description_reconciled': out['description_checked'],
            'requires_requirement_re_review': True, 'approvals_copied': False}
        try:
            from tools.api.sam_capture import retain_bytes
            out['legacy_depth_capture'] = retain_bytes(_cache_dir(), original)
        except (OSError, ValueError) as exc:
            out['errors'].append('legacy depth retention failed; original cache left intact: ' + _safe_error(exc))
            return out
    elif isinstance(data.get('migration'), dict):
        previous = data['migration']
        before = previous.get('before') if isinstance(previous.get('before'), dict) else {}
        out['migration'] = {'schema': 'depth_migration_v2',
            'before': _depth_inventory_snapshot(before), 'after': _depth_inventory_snapshot(out),
            'reason': _safe_error(previous.get('reason', 'legacy depth reconciliation')),
            'inventory_reconciled': out['resources_checked'], 'description_reconciled': out['description_checked'],
            'requires_requirement_re_review': True, 'approvals_copied': False}
    # No opaque legacy approval or unknown field is propagated into the replacement.
    # A failed optional index write never prevents returning already verified bytes.
    if original is not None or out['description_checked']:
        try:
            from tools.artifacts import atomic_write_json
            _cache_dir().mkdir(parents=True, exist_ok=True)
            persisted = {key: value for key, value in out.items() if key != 'from_cache'}
            if persisted != data:
                atomic_write_json(cache, persisted)
        except (OSError, ValueError) as exc:
            out['errors'].append('depth cache index write failed: ' + _safe_error(exc))
    return out
