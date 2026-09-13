"""SAM notice depth fetch — full description + attachments for ONE notice.

Breadth is cheap (the daily extract); depth is rationed. This module spends
ONE metered SAM call to pull a notice's full description text, and tries the
keyless resources endpoint for its attachment list. Results cache PERMANENTLY:
a posted notice's text almost never changes, so a notice is paid for once,
ever, across all future runs.

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
from urllib.parse import unquote, urljoin, urlparse

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
                source_url = urljoin("https://sam.gov", str(raw_url)) \
                    if raw_url else None
                parsed = urlparse(source_url) if source_url else None
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


def _notice_link_matches(value: Any, notice_id: str) -> bool:
    if not isinstance(value, str):
        return True
    try:
        parts = unquote(urlparse(value).path).split('/')
    except ValueError:
        return False
    # A file resource route has /opportunities/resources/files/<resource>/,
    # not /opportunities/<notice>/. Resource ids must never become notice ids.
    for i, part in enumerate(parts[:-1]):
        if part in ('opportunities', 'opp') and _PUBLIC_NOTICE_ID.fullmatch(parts[i + 1]):
            if parts[i + 1] != notice_id:
                return False
    return True


def _resource_identity_matches(payload: Any, notice_id: str) -> bool:
    stack = [payload]
    while stack:
        item = stack.pop()
        if isinstance(item, dict):
            for key in ("opportunityId", "noticeId", "notice_id"):
                if key in item and item[key] not in (None, "", notice_id):
                    return False
            for key in ('href', 'downloadUrl', 'resourceUrl', 'uri', 'source_url'):
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


def _capture_origin_valid(capture: dict, notice_id: str) -> bool:
    requested = RESOURCES_URL_TPL.format(id=notice_id)
    chain = capture.get('redirect_chain')
    if (capture.get('requested_url') != requested or not isinstance(chain, list)
            or not chain or chain[0] != requested or chain[-1] != capture.get('source_url')):
        return False
    for value in chain:
        if not isinstance(value, str):
            return False
        parsed = urlparse(value)
        host = (parsed.hostname or '').casefold()
        if (parsed.scheme != 'https' or not (host == 'sam.gov' or host.endswith('.sam.gov'))
                or not _notice_link_matches(value, notice_id)):
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


def fetch_notice_resources(notice_id: str, *, timeout_seconds: float = 15.0) -> dict:
    """Return the public attachment inventory for one notice, cached.

    SAM's public resources route is keyless but requires the HAL media type.
    A recognized empty list is cached; an error or unknown schema is not, so a
    transient response cannot become a permanent false claim of no files.
    """
    if not _PUBLIC_NOTICE_ID.fullmatch(str(notice_id or "")):
        raise ValueError("public SAM notice_id must be 32 hexadecimal characters")
    cache = _cache_dir() / f"{notice_id}.resources.json"
    cached = None
    if cache.exists():
        try:
            cached = json.loads(cache.read_text())
            if not isinstance(cached, dict) or cached.get("id") != notice_id:
                cached = None
        except (ValueError, OSError):
            cached = None
        if cached is not None and _resource_cache_fresh(cached) and _resource_capture_valid(cached):
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
        if not _resource_identity_matches(payload, notice_id):
            out["collection_status"] = "identity_mismatch"
            raise ValueError("resources notice identity mismatch")
        attachments, recognized = _attachment_inventory(payload, notice_id)
        if not recognized:
            out["collection_status"] = "unrecognized_inventory"
            raise ValueError("unrecognized resources schema")
        if out.get('retention_error'):
            out['collection_status'] = 'retention_failed'
            out['diagnostic_inventory'] = attachments
            out['errors'].append('local retention failed: ' + out['retention_error'])
            return {**out, 'from_cache': False, 'stale_cache': False}
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


def fetch_notice_depth(notice_id: str, api_key: str | None = None,
                       min_retrieved_at: Any = None) -> dict:
    """Full text + attachments for one notice. Cached forever; 1 metered call.

    Returns {'id', 'description', 'attachments', 'from_cache', 'errors'}.
    """
    if not notice_id:
        raise ValueError("notice_id required")
    api_key = api_key or os.environ.get("SAM_GOV_API_KEY")
    cache = _cache_dir() / f"{notice_id}.json"
    data = None
    invalid_cache = False
    if cache.exists():
        try:
            data = json.loads(cache.read_text())
            if (not isinstance(data, dict) or data.get('id') != notice_id
                    or not _resource_identity_matches(data, notice_id)
                    or not isinstance(data.get('errors', []), list)
                    or not isinstance(data.get('description', ''), (str, type(None)))
                    or not isinstance(data.get('attachments', []), (list, type(None)))
                    or not isinstance(data.get('withdrawn_attachments', []), list)
                    or any(not isinstance(item, dict) for item in (data.get('attachments') or []))):
                data = None
        except (OSError, ValueError, TypeError):
            data = None
        invalid_cache = data is None
    if data is not None:
        listed = data.get('attachments')
        if isinstance(listed, list) and any(attachment_withdrawn(item) for item in listed):
            data.setdefault('withdrawn_attachments', []).extend(
                item for item in listed if attachment_withdrawn(item))
            data['attachments'] = [item for item in listed if not attachment_withdrawn(item)]
            if data.get('resources_checked') is True:
                data['attachment_inventory_count'] = len(data['attachments'])
                data['attachment_inventory_hash'] = _manifest_hash(data['attachments'])
        # Old cache rows predate explicit depth lineage. Preserve their paid
        # description while marking the attachment surface honestly. A
        # resource failure is retried below because that endpoint is keyless;
        # it must not become a permanent blind spot merely because the costly
        # description fetch succeeded first.
        minimum = _timestamp(min_retrieved_at)
        cached_at = _timestamp(data.get("retrieved_at"))
        stale_for_posting = bool(
            minimum and (cached_at is None or cached_at < minimum))
        legacy_description = (
            "description_checked" not in data or not data.get("retrieved_at"))
        if legacy_description or stale_for_posting:
            data["description_checked"] = False
            if legacy_description:
                data["retrieved_at"] = None
            marker = ("cached description predates current notice modification"
                      if stale_for_posting else
                      "legacy cache lacks verified description retrieval lineage")
            if marker not in (data.get("errors") or []):
                data.setdefault("errors", []).append(marker)
            if api_key:
                try:
                    sam_quota.note_call("deep")
                    payload = get_json(
                        DESC_URL,
                        params={"noticeid": notice_id, "api_key": api_key},
                        timeout=60.0, headers=SAM_HEADERS,
                    )
                    if not _resource_identity_matches(payload, notice_id):
                        raise ValueError('description notice identity mismatch')
                    raw = payload.get("description") \
                        if isinstance(payload, dict) else None
                    checked = strip_html(raw)[:20000] if raw else None
                    if checked:
                        data["description"] = checked
                        data["description_checked"] = True
                        data["retrieved_at"] = datetime.now(timezone.utc) \
                            .isoformat(timespec="seconds")
                        data["errors"] = [
                            err for err in (data.get("errors") or [])
                            if err != marker and "description fetch failed" not in str(err)
                        ]
                        # An in-place notice update may also change resources;
                        # force a fresh keyless inventory reconciliation below.
                        data["resources_schema"] = None
                        data["resources_checked"] = False
                except Exception as e:  # noqa: BLE001 - legacy upgrade is best effort
                    data.setdefault("errors", []).append(
                        f"description lineage refresh failed: {e}")
        if data.get("resources_schema") != "recognized_v1":
            data["resources_checked"] = False
            try:
                res = get_json(
                    RESOURCES_URL_TPL.format(id=notice_id), timeout=30.0,
                    headers=RESOURCES_HEADERS, retries=1,
                )
                attachments, recognized = _attachment_inventory(res, notice_id)
                if recognized:
                    data["attachments"] = attachments
                    data['withdrawn_attachments'] = _withdrawn_inventory(res, notice_id)
                    data["resources_checked"] = True
                    data["resources_schema"] = "recognized_v1"
                    data["attachment_inventory_count"] = len(attachments or [])
                    data["attachment_inventory_hash"] = _manifest_hash(
                        attachments or [])
                    data["errors"] = [
                        err for err in (data.get("errors") or [])
                        if "resources fetch failed" not in str(err)
                        and "resources retry failed" not in str(err)
                        and "unrecognized resources schema" not in str(err)
                    ]
                else:
                    data.setdefault("errors", []).append(
                        "unrecognized resources schema (non-fatal)")
            except Exception as e:  # noqa: BLE001 - keyless retry is best effort
                data.setdefault("errors", []).append(
                    f"resources retry failed (non-fatal): {e}")
        from tools.artifacts import atomic_write_json
        atomic_write_json(cache, data)
        data["from_cache"] = True
        return data

    if not api_key:
        if invalid_cache:
            return {'id': notice_id, 'description': None, 'attachments': None,
                    'from_cache': False, 'retrieved_at': None,
                    'description_checked': False, 'resources_checked': False,
                    'errors': ['invalid or foreign depth cache; refresh unavailable without SAM key']}
        raise RuntimeError("SAM_GOV_API_KEY not set — depth fetch needs the SAM key")

    out: dict = {
        "id": notice_id,
        "description": None,
        "attachments": None,
        "from_cache": False,
        "retrieved_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "description_checked": False,
        "resources_checked": False,
        "resources_schema": None,
        "attachment_inventory_count": None,
        "attachment_inventory_hash": None,
        "errors": [],
    }
    try:
        sam_quota.note_call("deep")
        payload = get_json(DESC_URL, params={"noticeid": notice_id, "api_key": api_key},
                           timeout=60.0, headers=SAM_HEADERS)
        if not _resource_identity_matches(payload, notice_id):
            raise ValueError('description notice identity mismatch')
        raw = payload.get("description") if isinstance(payload, dict) else None
        out["description"] = strip_html(raw)[:20000] if raw else None
        out["description_checked"] = True
        if not out["description"]:
            out["errors"].append("noticedesc returned no description")
    except Exception as e:  # noqa: BLE001
        out["errors"].append(f"description fetch failed: {e}")

    try:  # best-effort, keyless, never fatal
        res = get_json(RESOURCES_URL_TPL.format(id=notice_id), timeout=30.0,
                       headers=RESOURCES_HEADERS, retries=1)
        attachments, recognized = _attachment_inventory(res, notice_id)
        if recognized:
            out["attachments"] = attachments
            out['withdrawn_attachments'] = _withdrawn_inventory(res, notice_id)
            out["resources_checked"] = True
            out["resources_schema"] = "recognized_v1"
            out["attachment_inventory_count"] = len(attachments or [])
            out["attachment_inventory_hash"] = _manifest_hash(attachments or [])
        else:
            out["errors"].append("unrecognized resources schema (non-fatal)")
    except Exception as e:  # noqa: BLE001
        out["errors"].append(f"resources fetch failed (non-fatal): {e}")

    # Cache only if we got the expensive part — never cache a failure.
    if out["description"]:
        _cache_dir().mkdir(parents=True, exist_ok=True)
        from tools.artifacts import atomic_write_json
        atomic_write_json(cache, out)
    return out
