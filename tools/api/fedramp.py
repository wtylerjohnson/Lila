"""FedRAMP marketplace adapter — cloud-authorization intel (keyless).

For software clients, FedRAMP status IS market access: an authorized competitor
can sell tomorrow; an unauthorized one is 12-18 months out. FedRAMP publishes
the Marketplace's underlying data from its official GitHub organization.  The
archived GSA and 18F datasets remain continuity fallbacks.  This adapter tries
the current daily dataset first and reports which source answered — never
silently stale.
"""

from __future__ import annotations

from typing import Any

from tools.api._http import get_json
from tools.api.base import (DataSource, SourceKind, SourceQuery,
                            cap_disclosed, register_source)
from tools.text_match import matching_phrases

CANDIDATE_URLS = [
    ("FedRAMP/marketplace-fedramp-gov-data (current official)",
     "https://raw.githubusercontent.com/FedRAMP/marketplace-fedramp-gov-data/main/data.json"),
    ("GSA/marketplace-fedramp-gov-data (archived fallback)",
     "https://raw.githubusercontent.com/GSA/marketplace-fedramp-gov-data/main/data.json"),
    ("18F/fedramp-data (historical fallback)",
     "https://raw.githubusercontent.com/18F/fedramp-data/master/data/data.json"),
]


def _source_url(label: str) -> str | None:
    return next((url for candidate, url in CANDIDATE_URLS
                 if candidate == label), None)


def _providers(payload: Any) -> list[dict]:
    """Find the provider/product list without betting on one schema."""
    if isinstance(payload, dict):
        # Preserve an explicit empty ``data`` list as a valid screened zero.
        # ``payload.get("data") or payload`` would turn that into an
        # unrecognized envelope and falsely report a source failure.
        inner = payload["data"] if "data" in payload else payload
        if isinstance(inner, dict):
            for key in ("Providers", "providers", "products", "Products"):
                if key not in inner:
                    continue
                rows = inner[key]
                if not isinstance(rows, list):
                    raise ValueError(
                        f"FedRAMP Marketplace response field {key} was not a list"
                    )
                return _validate_providers(rows)
        if isinstance(inner, list):
            return _validate_providers(inner)
    if isinstance(payload, list):
        return _validate_providers(payload)
    raise ValueError(
        "FedRAMP Marketplace response omitted a recognized provider list"
    )


def _field(p: dict, *names: str) -> Any:
    for n in names:
        for k, v in p.items():
            if k.lower().replace("_", "") == n:
                return v
    return None


def _validate_providers(rows: list[Any]) -> list[dict]:
    """Require enough identity to prove each row is a Marketplace entry."""
    providers: list[dict] = []
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError(
                "FedRAMP Marketplace response contained a non-object row"
            )
        package_id = _field(row, "id", "packageid")
        provider = _field(row, "csp", "provider", "name", "cspname")
        product = _field(
            row, "cso", "serviceoffering", "package", "product", "servicename"
        )
        if not any(str(value or "").strip() for value in (
            package_id, provider, product,
        )):
            raise ValueError(
                "FedRAMP Marketplace row omitted provider/product identity"
            )
        providers.append(row)
    return providers


def fetch_marketplace() -> tuple[list[dict], str]:
    last_err: Exception | None = None
    for label, url in CANDIDATE_URLS:
        try:
            provs = _providers(get_json(url, timeout=30.0, retries=1))
            return provs, label
        except Exception as e:  # noqa: BLE001
            last_err = e
    raise RuntimeError(f"no FedRAMP data source answered; last: {last_err}")


@register_source
class FedRampSource(DataSource):
    name = "fedramp"
    kind = SourceKind.ENRICHMENT

    def healthcheck(self) -> tuple[bool, str]:
        try:
            provs, label = fetch_marketplace()
            return True, f"{len(provs)} marketplace entries via {label}"
        except Exception as e:  # noqa: BLE001
            return False, str(e)

    def search(self, query: SourceQuery) -> list:
        raise NotImplementedError("enrichment-only source; use enrich()")

    def enrich(self, query: SourceQuery) -> dict[str, Any]:
        """Marketplace entries whose provider/product name matches a keyword."""
        provs, label = fetch_marketplace()
        kws = [k.strip('"') for k in (query.keywords or []) if k.strip()]
        matched = []
        for p in provs:
            name = str(_field(
                p, "csp", "provider", "name", "cspname", "productname"
            ) or "")
            product = str(_field(
                p, "cso", "serviceoffering", "package", "product", "servicename"
            ) or "")
            hay = f"{name} {product}"
            if not hay.strip() or (kws and not matching_phrases(hay, kws)):
                continue
            package_id = _field(p, "id", "packageid")
            matched.append({
                "id": package_id,
                "provider": name or None,
                "product": product or None,
                "status": _field(p, "designation", "status", "authorizationstatus"),
                "impact": _field(p, "impactlevel", "impact"),
                "authorization_count": _field(p, "authorization"),
                "reuse_count": _field(p, "reuse"),
                "authorization_type": _field(p, "authtype"),
                "service_model": _field(p, "servicemodel"),
                "deployment_model": _field(p, "deploymentmodel"),
                "agency_authorizations": _field(p, "agencyauthorizations"),
                "agency_reuse": _field(p, "agencyreuse"),
                "website": _field(p, "website"),
                "marketplace_url": (
                    f"https://marketplace.fedramp.gov/products/{package_id}/"
                    if package_id else None
                ),
            })
        out = {**cap_disclosed(matched, 15, key="matched"),
               "marketplace_total": len(provs), "source": label,
               "source_url": _source_url(label)}
        if "current official" in label.casefold():
            out["source_mode"] = "current_official"
        else:
            # Both fallback repositories are official historical continuity
            # sources, not the current Marketplace. Keep their evidence usable
            # while preventing the coverage layer from calling it current.
            out.update({
                "partial": True,
                "stale": True,
                "source_mode": "historical_official_fallback",
                "limitations": [
                    "The current FedRAMP Marketplace dataset did not answer; "
                    "results came from an archived official fallback."
                ],
            })
        return out
