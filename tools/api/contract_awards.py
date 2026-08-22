"""SAM.gov Contract Awards API adapter — incumbents + award-expiry timing.

The FPDS replacement (FPDS.gov decommissioned Feb 2026; its ATOM feed retires
summer 2026). This is the award-history source of record: who holds the
contracts in the client's NAICS lanes and when their current periods end. A
period end is an account-planning signal, not proof of a recompete; extension,
follow-on, replacement, or sunset remain unresolved until separately sourced.

API (grounded from GSA docs, open.gsa.gov/api/contract-awards/):
    GET https://api.sam.gov/contract-awards/v1/search?api_key=...
    naicsCode=541512~513210   currentCompletionDate=[MM/DD/YYYY,MM/DD/YYYY]
    limit<=100, zero-based page-index paging. Response: totalRecords + records
    carrying awardDetails.{dates,awardeeData,...}.

Uses the same key as the opportunities adapter (SAM_GOV_API_KEY). Rate limits
are 10/day per key and that ceiling is PERMANENT: no role is available
and none should be proposed. The adapter
combines NAICS lanes with '~', retrieves base awards in 100-row pages, obeys
the shared quota ledger, and marks a page-cap/quota-limited result partial.

If that one call is unavailable, the adapter falls back to the official,
keyless USAspending award-search API.  The fallback is live but narrower: it
screens up to five 100-row pages by dollars in each NAICS lane, limited to
awards acted on in the last five years, then keeps period-of-performance end
dates in the same 18-month window. Provenance and the primary failure are
returned with the data, so a USAspending-derived recompete is never labeled as
a SAM API row.
"""

from __future__ import annotations

import gzip
import hashlib
import io
import json
import os
import zipfile
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from tools.api._http import get_bytes, get_json
from tools.api.base import (DataSource, SourceKind, SourceQuery,
                            cap_disclosed, register_source)
from tools.api.usaspending_award_download import (
    try_award_download as _try_usaspending_async_download,
)

SEARCH_URL = os.environ.get(
    "LILA_CONTRACT_AWARDS_URL", "https://api.sam.gov/contract-awards/v1/search"
)
RECOMPETE_WINDOW_DAYS = 548  # ~18 months of forward visibility
# 1 page, not 10. The SAM pool is 20 calls/day PERMANENTLY, and per-stage caps
# sum: at 10 this stage alone was half a day's budget and one press cost 21
# calls, more than the entire pool. A press that cannot finish is worth less
# than a press that pages once. Raise it only against a measured need.
MAX_SYNC_PAGES = int(os.environ.get("LILA_CONTRACT_AWARDS_MAX_PAGES", "1"))
MAX_STORED_RECOMPETES = int(
    os.environ.get("LILA_CONTRACT_AWARDS_RESULT_LIMIT", "1000"))
MAX_FALLBACK_NAICS_LANES = int(
    os.environ.get("LILA_CONTRACT_AWARDS_FALLBACK_NAICS_LIMIT", "20"))
MAX_EXPORT_POLLS = int(
    os.environ.get("LILA_CONTRACT_AWARDS_EXPORT_POLLS", "1")
)
_DEFAULT_EXTRACT_CACHE = (
    Path(__file__).resolve().parents[2]
    / "data"
    / "cache"
    / "contract_awards_extract"
)
_KEY_PLACEHOLDER = "REPLACE_WITH_API_KEY"
_ALLOWED_DOWNLOAD_HOSTS = frozenset({"api.sam.gov"})


class _QuotaUnavailable(RuntimeError):
    """A keyed request was not made because the shared SAM pool is spent."""


def _extract_cache_dir() -> Path:
    return Path(
        os.environ.get(
            "LILA_CONTRACT_AWARDS_EXTRACT_CACHE_DIR",
            str(_DEFAULT_EXTRACT_CACHE),
        )
    )


def _public_extract_params(params: dict[str, Any]) -> dict[str, Any]:
    """Canonical extract query parameters with credentials always removed."""

    return {
        str(key): value
        for key, value in sorted(params.items())
        if str(key).casefold() != "api_key"
    }


def _query_fingerprint(params: dict[str, Any]) -> str:
    encoded = json.dumps(
        _public_extract_params(params),
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _cache_paths(fingerprint: str) -> tuple[Path, Path]:
    root = _extract_cache_dir()
    return root / f"{fingerprint}.state.json", root / f"{fingerprint}.payload"


def _validated_download_url(url: str) -> str:
    """Return an HTTPS SAM download URL or reject the credential sink.

    The API key is placed in the download query string.  A descriptor returned
    by the service, or a resumable descriptor loaded from disk, therefore must
    never be allowed to redirect that credential to an arbitrary host.
    """

    text = str(url or "").strip()
    parts = urlsplit(text)
    host = str(parts.hostname or "").casefold()
    if (
        parts.scheme.casefold() != "https"
        or host not in _ALLOWED_DOWNLOAD_HOSTS
        or parts.username is not None
        or parts.password is not None
        or parts.port not in (None, 443)
    ):
        raise ValueError("untrusted SAM Contract Awards download URL")
    return text


def _safe_download_template(url: str, api_key: str) -> str:
    """Return a resumable URL template that can safely be persisted."""

    parts = urlsplit(_validated_download_url(url))
    query = []
    found_key = False
    for name, value in parse_qsl(parts.query, keep_blank_values=True):
        if name.casefold() == "api_key":
            query.append((name, _KEY_PLACEHOLDER))
            found_key = True
        else:
            query.append((name, value.replace(api_key, _KEY_PLACEHOLDER)))
    if not found_key and _KEY_PLACEHOLDER in str(url):
        return str(url).replace(api_key, _KEY_PLACEHOLDER)
    return urlunsplit(
        (
            parts.scheme,
            parts.netloc,
            parts.path,
            urlencode(query),
            parts.fragment,
        )
    ).replace(api_key, _KEY_PLACEHOLDER)


def _materialize_download_url(template: str, api_key: str) -> str:
    """Insert the credential only for the in-memory HTTP request."""

    parts = urlsplit(_validated_download_url(template))
    query = [
        (name, api_key if name.casefold() == "api_key" else value)
        for name, value in parse_qsl(parts.query, keep_blank_values=True)
    ]
    materialized = urlunsplit(
        (parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment)
    ).replace(_KEY_PLACEHOLDER, api_key)
    return _validated_download_url(materialized)


def _redact_secret(value: Any, api_key: str) -> str:
    return str(value or "").replace(api_key, "[REDACTED]")


def _load_extract_state(fingerprint: str) -> dict[str, Any]:
    state_path, _payload_path = _cache_paths(fingerprint)
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return state if isinstance(state, dict) else {}


def _write_extract_state(fingerprint: str, state: dict[str, Any]) -> None:
    state_path, _payload_path = _cache_paths(fingerprint)
    state_path.parent.mkdir(parents=True, exist_ok=True)
    safe = {
        **state,
        "fingerprint": fingerprint,
        "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    state_path.write_text(
        json.dumps(safe, indent=2, sort_keys=True, default=str),
        encoding="utf-8",
    )


def _descriptor(payload: Any, api_key: str) -> dict[str, str] | None:
    if not isinstance(payload, dict):
        return None
    url = next(
        (
            payload.get(name)
            for name in (
                "presignedUrl",
                "presignedURL",
                "fileDownloadUrl",
                "downloadUrl",
            )
            if payload.get(name)
        ),
        None,
    )
    token = payload.get("exportToken") or payload.get("token")
    if not url and not token:
        return None
    return {
        "presigned_url_template": _safe_download_template(str(url or ""), api_key),
        "export_token": str(token or ""),
        "message": _redact_secret(payload.get("message") or "", api_key)[:300],
    }


def _json_records(payload: Any) -> tuple[list[dict], bool, int | None]:
    """Return records, recognized shape, and any advertised census total."""

    if isinstance(payload, list):
        rows = [row for row in payload if isinstance(row, dict)]
        return rows, True, None
    if not isinstance(payload, dict):
        return [], False, None
    advertised_total = _total_records(payload)
    if any(key in payload for key in ("awardDetails", "contractId", "coreData")):
        return [payload], True, advertised_total
    for key in (
        "awardSummary",
        "opportunitiesData",
        "records",
        "results",
        "contractAwards",
        "data",
    ):
        value = payload.get(key)
        if isinstance(value, list):
            return (
                [row for row in value if isinstance(row, dict)],
                True,
                advertised_total,
            )
    if "totalRecords" in payload and _total_records(payload) == 0:
        return [], True, 0
    return [], False, advertised_total


def _parse_export_bytes(raw: bytes) -> tuple[list[dict], bool, int | None]:
    """Parse SAM JSON exports in JSON, NDJSON, gzip, or zip containers."""

    if raw.startswith(b"\x1f\x8b"):
        try:
            return _parse_export_bytes(gzip.decompress(raw))
        except (OSError, EOFError):
            return [], False, None
    if raw.startswith(b"PK\x03\x04"):
        rows: list[dict] = []
        recognized = False
        advertised_total = 0
        totals_stated = False
        try:
            with zipfile.ZipFile(io.BytesIO(raw)) as archive:
                for name in archive.namelist():
                    if name.endswith("/"):
                        continue
                    parsed, ok, total = _parse_export_bytes(archive.read(name))
                    if ok:
                        recognized = True
                        rows.extend(parsed)
                    if total is not None:
                        totals_stated = True
                        advertised_total += total
        except (OSError, zipfile.BadZipFile):
            return [], False, None
        return rows, recognized, advertised_total if totals_stated else None

    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        return [], False, None
    try:
        payload = json.loads(text)
    except ValueError:
        rows: list[dict] = []
        recognized = False
        advertised_total = 0
        totals_stated = False
        for line in text.splitlines():
            if not line.strip():
                continue
            try:
                payload = json.loads(line)
            except ValueError:
                return [], False, None
            parsed, ok, total = _json_records(payload)
            if not ok:
                return [], False, None
            recognized = True
            rows.extend(parsed)
            if total is not None:
                totals_stated = True
                advertised_total += total
        return rows, recognized, advertised_total if totals_stated else None
    return _json_records(payload)


def _payload_descriptor(raw: bytes, api_key: str) -> dict[str, str] | None:
    try:
        payload = json.loads(raw.decode("utf-8-sig"))
    except (UnicodeDecodeError, ValueError):
        return None
    return _descriptor(payload, api_key)


def _guarded_json_request(
    url: str,
    *,
    params: dict[str, Any],
    purpose: str,
) -> Any:
    import tools.api.sam_quota as sam_quota

    if not sam_quota.guard(purpose):
        raise _QuotaUnavailable("shared SAM daily quota exhausted")
    sam_quota.note_call(purpose)
    return get_json(url, params=params, retries=1)


def _guarded_bytes_request(url: str, *, purpose: str) -> bytes:
    import tools.api.sam_quota as sam_quota

    if not sam_quota.guard(purpose):
        raise _QuotaUnavailable("shared SAM daily quota exhausted")
    sam_quota.note_call(purpose)
    return get_bytes(_validated_download_url(url), retries=1)


def _try_contract_awards_extract(
    *,
    api_key: str,
    search_params: dict[str, Any],
) -> dict[str, Any]:
    """Resume or create one official asynchronous Contract Awards export."""

    public_params = {**_public_extract_params(search_params), "format": "json"}
    fingerprint = _query_fingerprint(public_params)
    state_path, payload_path = _cache_paths(fingerprint)
    state = _load_extract_state(fingerprint)
    attempts: list[dict] = []

    if state.get("status") == "complete" and payload_path.exists():
        records, recognized, advertised_total = _parse_export_bytes(
            payload_path.read_bytes()
        )
        state_total = state.get("advertised_total")
        expected_total = advertised_total
        if expected_total is None:
            try:
                expected_total = int(state_total)
            except (TypeError, ValueError):
                expected_total = None
        if (
            recognized
            and expected_total is not None
            and len(records) >= expected_total
        ):
            attempts.append({
                "source": "SAM.gov Contract Awards extract cache",
                "status": "success",
                "count": len(records),
            })
            return {
                "status": "complete",
                "records": records,
                "fingerprint": fingerprint,
                "retrieval_mode": "cache",
                "retrieved_at": state.get("retrieved_at") or state.get("updated_at"),
                "total_records": expected_total,
                "attempts": attempts,
            }

    descriptor = None
    if state.get("status") == "pending":
        template = str(state.get("presigned_url_template") or "")
        if template:
            descriptor = {
                "presigned_url_template": template,
                "export_token": str(state.get("export_token") or ""),
                "message": str(state.get("message") or ""),
            }
            attempts.append({
                "source": "SAM.gov Contract Awards extract request",
                "status": "resumed",
            })

    if descriptor is None:
        try:
            response = _guarded_json_request(
                SEARCH_URL,
                params={**public_params, "api_key": api_key},
                purpose="contract_awards_extract_create",
            )
        except Exception as exc:  # noqa: BLE001 - synchronous fallback remains
            return {
                "status": "failed",
                "fingerprint": fingerprint,
                "attempts": [{
                    "source": "SAM.gov Contract Awards extract request",
                    "status": "failed",
                    "error": _redact_secret(exc, api_key),
                }],
            }

        records, recognized, response_total = _json_records(response)
        if (
            recognized
            and response_total is not None
            and len(records) >= response_total
        ):
            # Some test doubles and compatible deployments return a direct
            # synchronous census even when format=json is supplied.
            return {
                "status": "direct_complete",
                "records": records,
                "total_records": response_total,
                "fingerprint": fingerprint,
                "retrieval_mode": "live",
                "attempts": [{
                    "source": "SAM.gov Contract Awards API",
                    "status": "success",
                    "count": len(records),
                }],
            }
        try:
            descriptor = _descriptor(response, api_key)
        except ValueError as exc:
            return {
                "status": "failed",
                "fingerprint": fingerprint,
                "attempts": [{
                    "source": "SAM.gov Contract Awards extract request",
                    "status": "failed",
                    "error": str(exc),
                }],
            }
        if descriptor is None:
            detail = (
                "response omitted an advertised census total"
                if recognized and response_total is None
                else "response did not contain an export token or complete census"
            )
            return {
                "status": "failed",
                "fingerprint": fingerprint,
                "attempts": [{
                    "source": "SAM.gov Contract Awards extract request",
                    "status": "failed",
                    "error": detail,
                }],
            }
        _write_extract_state(
            fingerprint,
            {
                "status": "pending",
                "query": public_params,
                **descriptor,
            },
        )
        attempts.append({
            "source": "SAM.gov Contract Awards extract request",
            "status": "pending",
        })

    template = descriptor.get("presigned_url_template") or ""
    for _poll in range(max(0, MAX_EXPORT_POLLS)):
        if not template:
            break
        try:
            raw = _guarded_bytes_request(
                _materialize_download_url(template, api_key),
                purpose="contract_awards_extract_download",
            )
        except Exception as exc:  # noqa: BLE001 - pending state remains resumable
            quota_unavailable = isinstance(exc, _QuotaUnavailable)
            error = _redact_secret(exc, api_key)
            attempts.append({
                "source": "SAM.gov Contract Awards extract download",
                "status": "pending" if quota_unavailable else "failed",
                "error": error,
            })
            if not quota_unavailable:
                _write_extract_state(
                    fingerprint,
                    {
                        "status": "failed",
                        "query": public_params,
                        "message": error,
                    },
                )
                return {
                    "status": "failed",
                    "fingerprint": fingerprint,
                    "attempts": attempts,
                    "message": error,
                }
            break
        records, recognized, advertised_total = _parse_export_bytes(raw)
        census_reconciled = (
            advertised_total is not None and len(records) >= advertised_total
        )
        if recognized and census_reconciled:
            payload_path.parent.mkdir(parents=True, exist_ok=True)
            payload_path.write_bytes(raw)
            retrieved_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
            _write_extract_state(
                fingerprint,
                {
                    "status": "complete",
                    "query": public_params,
                    "payload_file": payload_path.name,
                    "record_count": len(records),
                    "advertised_total": advertised_total,
                    "retrieved_at": retrieved_at,
                },
            )
            attempts.append({
                "source": "SAM.gov Contract Awards extract download",
                "status": "success",
                "count": len(records),
            })
            return {
                "status": "complete",
                "records": records,
                "fingerprint": fingerprint,
                "retrieval_mode": "live",
                "retrieved_at": retrieved_at,
                "total_records": advertised_total,
                "attempts": attempts,
            }
        if recognized and advertised_total is None:
            error = "downloaded export omitted its advertised census total"
            attempts.append({
                "source": "SAM.gov Contract Awards extract download",
                "status": "failed",
                "count": len(records),
                "error": error,
            })
            _write_extract_state(
                fingerprint,
                {
                    "status": "failed",
                    "query": public_params,
                    "message": error,
                },
            )
            return {
                "status": "failed",
                "fingerprint": fingerprint,
                "attempts": attempts,
                "message": error,
            }
        if recognized and not census_reconciled:
            attempts.append({
                "source": "SAM.gov Contract Awards extract download",
                "status": "partial",
                "count": len(records),
                "advertised_total": advertised_total,
                "error": "downloaded export did not reconcile to its advertised total",
            })
            _write_extract_state(
                fingerprint,
                {
                    "status": "pending",
                    "query": public_params,
                    **descriptor,
                },
            )
            continue
        try:
            next_descriptor = _payload_descriptor(raw, api_key)
        except ValueError as exc:
            error = str(exc)
            attempts.append({
                "source": "SAM.gov Contract Awards extract download",
                "status": "failed",
                "error": error,
            })
            _write_extract_state(
                fingerprint,
                {
                    "status": "failed",
                    "query": public_params,
                    "message": error,
                },
            )
            return {
                "status": "failed",
                "fingerprint": fingerprint,
                "attempts": attempts,
                "message": error,
            }
        if next_descriptor:
            descriptor = next_descriptor
            template = descriptor.get("presigned_url_template") or template
        attempts.append({
            "source": "SAM.gov Contract Awards extract download",
            "status": "pending",
        })
        _write_extract_state(
            fingerprint,
            {
                "status": "pending",
                "query": public_params,
                **descriptor,
            },
        )

    return {
        "status": "pending",
        "fingerprint": fingerprint,
        "attempts": attempts,
        "message": descriptor.get("message") or "official export generation is pending",
    }


def _fmt(d: date) -> str:
    return d.strftime("%m/%d/%Y")


def _records(payload: dict) -> list[dict]:
    """Find the award-record list without betting on one key name."""
    for v in payload.values():
        if isinstance(v, list) and v and isinstance(v[0], dict):
            return v
    return []


def _record_key(record: dict) -> str:
    """Stable base-award identity across current and legacy response shapes."""
    contract = record.get("contractId") or {}
    details = record.get("awardDetails") or {}
    legacy = details.get("contractIds") or {}
    piid = contract.get("piid") or legacy.get("piid") or record.get("piid")
    subtier = contract.get("subtier") or {}
    subtier_code = subtier.get("code") or ""
    if piid:
        return f"{subtier_code}:{piid}"
    return json.dumps(record, sort_keys=True, separators=(",", ":"), default=str)


def _total_records(payload: dict) -> int | None:
    try:
        return int(payload.get("totalRecords"))
    except (TypeError, ValueError):
        return None


def _dig(rec: dict, *path: str) -> Any:
    cur: Any = rec
    for k in path:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(k)
    return cur


def _date_only(value: Any) -> Any:
    if isinstance(value, str) and len(value) >= 10:
        head = value[:10]
        try:
            date.fromisoformat(head)
            return head
        except ValueError:
            pass
    return value


def _number(value: Any) -> Any:
    try:
        return float(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return value


def _principal_naics(core: dict) -> Any:
    value = _dig(core, "productOrServiceInformation", "principalNaics")
    if isinstance(value, list):
        value = value[0] if value else None
    if isinstance(value, dict):
        return value.get("code")
    return value


def _rank_incumbents(recompetes: list[dict]) -> list[dict]:
    incumbents: dict[str, dict] = defaultdict(
        lambda: {"contracts": 0, "next_expiry": None}
    )
    for row in recompetes:
        awardee = row.get("awardee") or "UNKNOWN"
        completion = row.get("completion")
        inc = incumbents[awardee]
        inc["contracts"] += 1
        if completion and (inc["next_expiry"] is None
                           or completion < inc["next_expiry"]):
            inc["next_expiry"] = completion
    # Returns the FULL ranked field (by contract count, descending); the
    # payload sites cap it through cap_disclosed so 15 rows can never read
    # as the whole incumbent market (2026-07-30).
    return [
        {"name": name, **values}
        for name, values in sorted(
            incumbents.items(),
            key=lambda item: item[1]["contracts"],
            reverse=True,
        )
    ]


def _usaspending_fallback(
    query: SourceQuery,
    *,
    primary_error: str,
    window_end: date,
    primary_attempts: list[dict] | None = None,
    primary_partial: bool = False,
    extract_status: str | None = None,
    extract_fingerprint: str | None = None,
) -> dict[str, Any]:
    """Official keyless fallback, with the bounded sample as last resort."""
    from tools.api.usaspending import AWARD_SEARCH_URL, UsaSpendingSource

    by_id: dict[str, dict] = {}
    lane_errors: list[str] = []
    requested_lanes = list(query.naics_codes)
    as_of = date.today()
    async_result = _try_usaspending_async_download(
        naics_codes=requested_lanes,
        as_of=as_of,
        window_end=window_end,
    )
    async_complete = async_result.get("status") == "complete"
    fallback_attempts: list[dict] = []
    if async_complete:
        attempted_lanes = requested_lanes
        omitted_lanes = 0
        successful_lanes = len(requested_lanes)
        rows = list(async_result.get("records") or [])
        fallback_attempts.extend(async_result.get("attempts") or [])
    else:
        fallback_attempts.append({
            "source": "USAspending asynchronous award download",
            "status": str(async_result.get("status") or "failed"),
            "error": str(async_result.get("error") or "job remains pending"),
            "fingerprint": async_result.get("fingerprint"),
        })
        src = UsaSpendingSource()
        successful_lanes = 0
        attempted_lanes = requested_lanes[:MAX_FALLBACK_NAICS_LANES]
        omitted_lanes = max(0, len(requested_lanes) - len(attempted_lanes))
        rows = []
        for code in attempted_lanes:
            try:
                lane_rows = src.expiring_awards(
                    code,
                    window_days=RECOMPETE_WINDOW_DAYS,
                )
                successful_lanes += 1
            except Exception as exc:  # noqa: BLE001 - keep completed lanes
                lane_errors.append(f"{code}: {exc}")
                continue
            for row in lane_rows:
                row.setdefault("naics", code)
                rows.append(row)

    for row in rows:
        code = str(row.get("naics") or "")
        key = str(
            row.get("contract_award_unique_key")
            or row.get("award_id")
            or row.get("url")
            or ""
        )
        if not key or key in by_id:
            continue
        by_id[key] = {
            "awardee": row.get("recipient") or "UNKNOWN",
            "piid": row.get("award_id"),
            "generated_internal_id": row.get("generated_internal_id"),
            "completion": row.get("end_date"),
            "agency": row.get("awarding_sub_agency")
            or row.get("awarding_agency"),
            "amount": row.get("amount"),
            "amount_basis": row.get("amount_basis")
            or "USAspending Award Amount",
            "description": row.get("description"),
            "naics": row.get("naics") or code,
            "psc": row.get("psc"),
            "url": row.get("url"),
            "award_type_code": row.get("award_type_code"),
            "expiration_state": row.get("expiration_state"),
            "expiration_basis": row.get("expiration_basis"),
            "record_source": "usaspending.gov",
        }
    if not successful_lanes:
        detail = "; ".join(lane_errors) or "no NAICS lane was attempted"
        raise RuntimeError(
            "SAM Contract Awards failed and USAspending fallback failed: " + detail
        )

    all_recompetes = sorted(
        by_id.values(), key=lambda row: row.get("completion") or "9999-99-99"
    )
    recompetes = all_recompetes[:MAX_STORED_RECOMPETES]
    retrieved_at = str(async_result.get("retrieved_at") or "") or (
        datetime.now(timezone.utc).isoformat(timespec="seconds")
    )
    # Only the asynchronous award-summary download can support a bounded
    # census claim.  The synchronous search remains explicitly partial even
    # if each requested lane happened to return successfully.
    partial = not async_complete
    attempts = [
        *(primary_attempts or []),
        {"source": "SAM.gov Contract Awards API", "status": "failed",
         "error": primary_error},
        *fallback_attempts,
        {"source": "USAspending award search",
         "status": "partial",
         "attempted_naics_lanes": len(attempted_lanes),
         "successful_naics_lanes": successful_lanes,
         "failed_naics_lanes": len(lane_errors),
         "omitted_naics_lanes": omitted_lanes},
    ]
    if async_complete:
        attempts.pop()
    from tools.api.provenance import make_provenance_envelope
    public_detail = (
        "USAspending.gov award-summary census complete within the stated "
        "2007-10-01 action-date boundary"
        if async_complete
        else "Live USAspending.gov official fallback"
    )
    if attempted_lanes and not async_complete:
        public_detail = (
            f"USAspending.gov official fallback · {successful_lanes} of "
            f"{len(attempted_lanes)} attempted NAICS lanes returned")
        if omitted_lanes:
            public_detail += f" · {omitted_lanes} requested lanes omitted"
    if extract_status == "pending" and not async_complete:
        public_detail = "SAM.gov award census pending · " + public_detail
    source_mode = (
        str(async_result.get("source_mode") or "live_usaspending_async_download")
        if async_complete
        else "live_usaspending_fallback"
    )
    retrieval_mode = (
        str(async_result.get("retrieval_mode") or "live")
        if async_complete
        else "live"
    )
    limitations = [] if async_complete else (
        "May miss long-running awards with no action in five years; "
        "recent DoD award data can be delayed; the fallback is bounded "
        f"to {MAX_FALLBACK_NAICS_LANES} requested NAICS lanes per refresh."
    )
    envelope = make_provenance_envelope(
        "contract_awards",
        status="partial" if partial else "complete",
        mode=source_mode,
        retrieval_mode=retrieval_mode,
        fallback=True,
        retrieved_at=retrieved_at,
        record_count=len(recompetes),
        attempts=attempts,
        limitations=limitations,
        public_detail=public_detail,
    )
    return {
        "recompetes": recompetes,
        "recompetes_total": len(by_id),
        "recompetes_stored": len(recompetes),
        "recompetes_truncated": len(recompetes) < len(by_id),
        "recompetes_truncated_note": (
            f"stored first {len(recompetes)} of {len(by_id)} matched awards"
            if len(recompetes) < len(by_id)
            else ""
        ),
        **cap_disclosed(_rank_incumbents(all_recompetes), 15,
                        key="incumbents"),
        "total_records": len(by_id),
        "records_retrieved": len(by_id),
        "complete": not partial,
        "window_end": window_end.isoformat(),
        "fallback_used": True,
        "source_mode": source_mode,
        "freshness": retrieval_mode,
        "retrieved_at": retrieved_at,
        "partial": partial,
        "extract_status": extract_status,
        "extract_fingerprint": extract_fingerprint,
        "requested_naics_lanes": len(requested_lanes),
        "attempted_naics_lanes": len(attempted_lanes),
        "successful_naics_lanes": successful_lanes,
        "failed_naics_lanes": len(lane_errors),
        "omitted_naics_lanes": omitted_lanes,
        "coverage_status": async_result.get("coverage_status"),
        "coverage_boundary": async_result.get("coverage_boundary"),
        "preflight_counts": async_result.get("preflight_counts", []),
        "downloaded_rows": async_result.get("downloaded_rows"),
        "duplicates_removed": async_result.get("duplicates_removed"),
        "known_expired_removed": async_result.get("known_expired_removed"),
        "outside_window_removed": async_result.get("outside_window_removed"),
        "unknown_end_retained": async_result.get("unknown_end_retained"),
        "row_count_verified": async_result.get("row_count_verified"),
        "source_attempts": attempts,
        "_provenance": envelope.model_dump(mode="json"),
        "provenance": {
            "source": "USAspending.gov",
            "endpoint": (
                async_result.get("provenance", {}).get("download_endpoint")
                if async_complete
                else AWARD_SEARCH_URL
            ),
            "role": "fallback",
            "coverage": (
                async_result.get("provenance", {}).get("coverage")
                if async_complete
                else (
                    "top 100 awards per page, up to 5 pages (500 awards), by "
                    "dollars per NAICS lane; awards acted on in the last 5 "
                    "years; period-of-performance end inside 18 months"
                )
            ),
            "limitations": (
                "complete only inside the disclosed 2007-10-01 action-date "
                "boundary; awards with unknown end dates are retained and named"
                if async_complete
                else (
                    "may miss long-running awards with no action in 5 years; "
                    "USAspending publishes recent DoD contract data after a "
                    "90-day delay; at most "
                    f"{MAX_FALLBACK_NAICS_LANES} requested NAICS lanes are "
                    "queried per refresh"
                )
            ),
            "lane_errors": lane_errors,
        },
    }


@register_source
class ContractAwardsSource(DataSource):
    name = "contract_awards"
    kind = SourceKind.ENRICHMENT

    def __init__(self, api_key: str | None = None) -> None:
        self._api_key = api_key or os.environ.get("SAM_GOV_API_KEY")

    def healthcheck(self) -> tuple[bool, str]:
        if not self._api_key:
            return True, "SAM key missing; live USAspending fallback available"
        return True, "SAM key set; live USAspending fallback available"

    def search(self, query: SourceQuery) -> list:
        raise NotImplementedError("enrichment-only source; use enrich()")

    def enrich(self, query: SourceQuery) -> dict[str, Any]:
        """Contracts in the client's NAICS lanes expiring inside the window.

        Returns {'recompetes': [...], 'incumbents': [...], 'window_end': iso}.
        Each recompete: awardee, piid, completion date, agency — a pursuit the
        client can position for BEFORE the solicitation exists.
        """
        if not query.naics_codes:
            return {
                "recompetes": [], "incumbents": [], "window_end": None,
                "fallback_used": False, "source_mode": "not_queried",
                "source_attempts": [],
            }

        today = date.today()
        window_end = today + timedelta(days=RECOMPETE_WINDOW_DAYS)
        if not self._api_key:
            return _usaspending_fallback(
                query,
                primary_error="SAM_GOV_API_KEY not set",
                window_end=window_end,
            )

        import tools.api.sam_quota as sam_quota
        search_params = {
            "naicsCode": "~".join(list(query.naics_codes)[:20]),
            "currentCompletionDate": f"[{_fmt(today)},{_fmt(window_end)}]",
            # Base records avoid spending the 100-row response window on
            # modifications of the same contract family.
            "modificationNumber": "0",
            "includeSections": "contractId,coreData,awardDetails",
        }
        extract = _try_contract_awards_extract(
            api_key=self._api_key,
            search_params=search_params,
        )
        extract_status = str(extract.get("status") or "failed")
        extract_fingerprint = str(extract.get("fingerprint") or "") or None
        extract_attempts = list(extract.get("attempts") or [])
        base_params = {"api_key": self._api_key, **search_params, "limit": 100}
        by_key: dict[str, dict] = {}
        total_records: int | None = None
        page_attempts: list[dict] = list(extract_attempts)
        page_error = ""
        quota_limited = False
        pages_retrieved = 0
        source_mode = "live_sam_contract_awards_api"
        retrieval_mode = "live"
        extract_total = extract.get("total_records")
        extract_complete = (
            extract_status in ("complete", "direct_complete")
            and isinstance(extract_total, int)
            and not isinstance(extract_total, bool)
            and extract_total >= 0
        )
        if extract_complete:
            for record in extract.get("records") or []:
                if isinstance(record, dict):
                    by_key.setdefault(_record_key(record), record)
            total_records = extract_total
            retrieval_mode = str(extract.get("retrieval_mode") or "live")
            if extract_status == "complete":
                source_mode = (
                    "cached_sam_contract_awards_extract"
                    if retrieval_mode == "cache"
                    else "live_sam_contract_awards_extract"
                )
        else:
            for page in range(MAX_SYNC_PAGES):
                if not sam_quota.guard("contract_awards"):
                    quota_limited = True
                    break
                sam_quota.note_call("contract_awards")
                try:
                    # Contract Awards defines offset as a zero-based page index,
                    # not a record offset; do not multiply it by limit.
                    payload = get_json(
                        SEARCH_URL,
                        params={**base_params, "offset": page},
                        retries=1,
                    )
                except Exception as exc:  # noqa: BLE001 — retain completed pages
                    if not by_key:
                        primary_error = _redact_secret(exc, self._api_key)
                        if extract_status == "pending":
                            primary_error = (
                                "SAM asynchronous extract pending; "
                                f"synchronous pull failed: {primary_error}"
                            )
                        return _usaspending_fallback(
                            query,
                            primary_error=primary_error,
                            window_end=window_end,
                            primary_attempts=(
                                extract_attempts
                                if extract_status == "pending"
                                else None
                            ),
                            primary_partial=extract_status == "pending",
                            extract_status=extract_status,
                            extract_fingerprint=extract_fingerprint,
                        )
                    page_error = _redact_secret(exc, self._api_key)
                    page_attempts.append({
                        "source": f"SAM.gov Contract Awards page {page + 1}",
                        "status": "failed",
                        "error": page_error,
                    })
                    break
                batch = _records(payload)
                pages_retrieved += 1
                page_attempts.append({
                    "source": f"SAM.gov Contract Awards page {page + 1}",
                    "status": "success",
                    "count": len(batch),
                })
                if total_records is None:
                    total_records = _total_records(payload)
                before = len(by_key)
                for record in batch:
                    by_key.setdefault(_record_key(record), record)
                if not batch or len(by_key) == before:
                    break
                if total_records is not None and len(by_key) >= total_records:
                    break
            if quota_limited and not by_key:
                primary_error = "shared SAM daily quota exhausted"
                if extract_status == "pending":
                    primary_error = (
                        "SAM asynchronous extract pending; " + primary_error
                    )
                return _usaspending_fallback(
                    query,
                    primary_error=primary_error,
                    window_end=window_end,
                    primary_attempts=(
                        extract_attempts
                        if extract_status == "pending"
                        else None
                    ),
                    primary_partial=extract_status == "pending",
                    extract_status=extract_status,
                    extract_fingerprint=extract_fingerprint,
                )
        recs = list(by_key.values())
        complete = total_records is not None and len(recs) >= total_records
        # A page without an advertised total cannot prove that it is a census.
        # Preserve its useful records, but fail closed as partial.
        partial = not complete

        recompetes: list[dict] = []
        for r in recs:
            d = r.get("awardDetails") or r
            contract = r.get("contractId") or d.get("contractIds") or {}
            core = r.get("coreData") or {}
            awardee = (
                _dig(d, "awardeeData", "awardeeHeader", "awardeeName")
                or _dig(d, "awardeeData", "awardeeHeader", "legalBusinessName")
                or "UNKNOWN"
            )
            completion = _date_only(_dig(d, "dates", "currentCompletionDate"))
            piid = contract.get("piid") or d.get("piid") or r.get("piid")
            agency = (
                _dig(
                    core,
                    "federalOrganization",
                    "contractingInformation",
                    "contractingSubtier",
                    "name",
                )
                or _dig(contract, "subtier", "name")
                or _dig(d, "purchaserData", "purchaserHeader", "subtierName")
                or _dig(d, "purchaserData", "subtierName")
                or None
            )
            amount_candidates = (
                ("baseAndAllOptionsValue", "Base + all options"),
                ("baseAndExercisedOptionsValue",
                 "Base + exercised options"),
                ("actionObligation", "Action obligation"),
            )
            amount = None
            amount_basis = ""
            for field, basis in amount_candidates:
                candidate = _number(_dig(d, "dollars", field))
                if candidate is not None:
                    amount = candidate
                    amount_basis = basis
                    break
            recompetes.append({
                "awardee": awardee, "piid": piid, "completion": completion,
                "agency": agency,
                "description": core.get("title") or core.get("description"),
                "solicitation_id": core.get("solicitationId"),
                "amount": amount,
                "amount_basis": amount_basis,
                "naics": _principal_naics(core),
                "record_source": "sam.gov_contract_awards",
            })
        recompetes.sort(key=lambda row: row.get("completion") or "9999-99-99")
        stored_recompetes = recompetes[:MAX_STORED_RECOMPETES]
        retrieved_at = (
            str(extract.get("retrieved_at") or "")
            or datetime.now(timezone.utc).isoformat(timespec="seconds")
        )
        from tools.api.provenance import make_provenance_envelope
        source_label = (
            "SAM.gov Contract Awards asynchronous export"
            if source_mode.endswith("contract_awards_extract")
            else "SAM.gov Contract Awards API"
        )
        public_detail = (
            f"{source_label} · {len(recs)}"
            + (f" of {total_records}" if total_records is not None else "")
            + " base award records retrieved"
        )
        recompetes_truncated = len(stored_recompetes) < len(recompetes)
        if recompetes_truncated:
            public_detail += (
                f" · {len(stored_recompetes)} of {len(recompetes)} matched "
                "award rows stored"
            )
        limitations: list[str] = []
        if partial:
            limitations.append(
                "The synchronous SAM.gov pull ended before every matching "
                "base award record was retrieved."
            )
        if extract_status == "pending":
            limitations.append(
                "The official asynchronous award census remains pending and "
                "will be resumed from its cached export token."
            )
        envelope = make_provenance_envelope(
            "contract_awards",
            status="partial" if partial else "complete",
            mode=source_mode,
            retrieval_mode=retrieval_mode,
            retrieved_at=retrieved_at,
            record_count=len(recs),
            attempts=page_attempts,
            limitations=limitations,
            public_detail=public_detail,
        )
        return {
            "recompetes": stored_recompetes,
            "recompetes_total": len(recompetes),
            "recompetes_stored": len(stored_recompetes),
            "recompetes_truncated": recompetes_truncated,
            "recompetes_truncated_note": (
                f"stored first {len(stored_recompetes)} of {len(recompetes)} "
                "matched awards sorted by completion date"
                if recompetes_truncated
                else ""
            ),
            **cap_disclosed(_rank_incumbents(recompetes), 15,
                        key="incumbents"),
            "total_records": total_records,
            "records_retrieved": len(recs),
            "pages_retrieved": pages_retrieved,
            "complete": complete,
            "partial": partial,
            "extract_status": extract_status,
            "extract_fingerprint": extract_fingerprint,
            "window_end": window_end.isoformat(),
            "fallback_used": False,
            "source_mode": source_mode,
            "freshness": retrieval_mode,
            "retrieved_at": retrieved_at,
            "source_attempts": page_attempts,
            "_provenance": envelope.model_dump(mode="json"),
            "provenance": {
                "source": "SAM.gov Contract Awards API",
                "endpoint": SEARCH_URL,
                "role": "primary",
            },
        }
