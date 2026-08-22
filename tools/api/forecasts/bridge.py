"""Bridge verified agency workbooks into the canonical ForecastRecord lane.

The Wave-1 adapters retain their fixture-tested RawOpportunity normalization
surface, but forecasts must enter the assessment as PROGRAM intent.  This
module is the single conversion owner so those rows cannot leak into the live
solicitation population.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Mapping

from agents.schemas import ForecastRecord, RawOpportunity


def _text(value: Any) -> str | None:
    cleaned = " ".join(str(value or "").split())
    return cleaned or None


def _first(row: Mapping[str, Any], *keys: str) -> str | None:
    for key in keys:
        value = _text(row.get(key))
        if value:
            return value
    return None


def _joined(*parts: Any) -> str | None:
    values = [_text(part) for part in parts]
    return " ".join(value for value in values if value) or None


def _retrieved(provenance: Mapping[str, Any] | None) -> datetime:
    raw = (provenance or {}).get("retrieved_at")
    if isinstance(raw, datetime):
        value = raw
    elif isinstance(raw, str) and raw.strip():
        try:
            value = datetime.fromisoformat(raw.strip().replace("Z", "+00:00"))
        except ValueError:
            value = datetime.now(timezone.utc)
    else:
        value = datetime.now(timezone.utc)
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def from_raw(
    opportunity: RawOpportunity,
    provenance: Mapping[str, Any] | None,
) -> ForecastRecord:
    """Convert one already-normalized row without inventing source fields."""
    raw = opportunity.raw_payload or {}
    normalized = raw.get("_normalized")
    normalized = normalized if isinstance(normalized, Mapping) else {}
    source = opportunity.source

    if source == "state_forecast":
        component = _first(raw, "contracting_office")
        description = _first(raw, "requirement_description", "description")
        value_range = _first(raw, "estimated_contract_value")
        solicitation = _first(raw, "estimated_solicitation_date")
        award = _joined(
            _first(raw, "fiscal_year"),
            _first(raw, "estimated_award_fy_quarter"),
        )
        fiscal_year = _first(raw, "fiscal_year")
        award_type = _first(raw, "acquisition_phase")
        incumbent = _first(raw, "incumbent_contractor_name")
        status = _first(raw, "acquisition_phase")
    elif source == "nasa_naf":
        component = _first(normalized, "center", "buying_office_code")
        description = _first(normalized, "summary")
        value_range = _first(normalized, "estimated_value_range")
        solicitation = _joined(
            _first(normalized, "solicitation_release_fy"),
            _first(normalized, "solicitation_release_quarter"),
        )
        award = _joined(
            _first(normalized, "anticipated_award_fy"),
            _first(normalized, "anticipated_award_quarter"),
        )
        fiscal_year = _first(normalized, "solicitation_release_fy")
        award_type = _first(normalized, "contract_vehicle", "contract_type")
        incumbent = _first(normalized, "incumbent_stated")
        status = _first(
            normalized, "acquisition_status", "awarded_or_withdrawn")
    elif source == "ed_forecast":
        component = _first(raw, "funding_office", "contracting_office")
        description = _first(raw, "description", "contract_description")
        value_range = _first(raw, "estimated_value_range")
        solicitation = _first(
            raw, "target_solicitation_date", "target_solicitation_quarter")
        award = _first(raw, "target_award_quarter")
        fiscal_year = _first(raw, "fiscal_year")
        award_type = _first(raw, "contract_type", "type_of_competition")
        incumbent = _first(raw, "incumbent_contractor_name")
        status = _first(raw, "status", "acquisition_phase")
    elif source == "hhs_sbcx":
        component = _first(
            normalized, "division_acronym", "division_name")
        description = _first(raw, "description", "summary", "scope")
        value_range = _first(normalized, "contract_range_label")
        solicitation = _first(normalized, "target_solicitation")
        award = _first(normalized, "target_award")
        fiscal_year = (solicitation or award or "")[:4] or None
        award_type = _first(normalized, "strategy_label")
        incumbent = _first(normalized, "incumbent_stated")
        status = "published"
    elif source == "hud_forecast":
        component = _first(normalized, "office")
        description = _first(normalized, "description")
        value_range = _first(normalized, "value_range")
        solicitation = _first(normalized, "solicitation_month")
        award = _first(normalized, "award_month")
        fiscal_year = _first(normalized, "fiscal_year")
        award_type = _first(
            normalized, "vehicle", "requirement_type", "competition")
        incumbent = None
        status = _first(normalized, "status") or "published"
    elif source == "navy_nawcwd_lraf":
        component = _first(normalized, "group_name", "procurement_office")
        description = _first(normalized, "description")
        value_range = _first(normalized, "value_range")
        solicitation = _joined(
            _first(normalized, "solicitation_fy"),
            _first(normalized, "solicitation_quarter"),
        )
        award = _joined(
            _first(normalized, "award_fy"),
            _first(normalized, "award_quarter"),
        )
        fiscal_year = _first(normalized, "solicitation_fy", "award_fy")
        award_type = _first(
            normalized, "procurement_instrument", "contract_type")
        incumbent = _first(normalized, "incumbent_stated")
        status = _first(normalized, "follow_on_or_new") or "published"
    elif source == "navy_nawcad_lraf":
        component = _first(normalized, "component")
        description = _first(normalized, "description")
        value_range = _first(normalized, "value_range_stated")
        solicitation = _first(normalized, "anticipated_solicitation")
        award = _first(normalized, "anticipated_award")
        fiscal_year = (solicitation or award or "")[:4] or None
        award_type = _first(normalized, "vehicle_stated")
        incumbent = _first(normalized, "incumbent_stated")
        status = (_first(normalized, "revised_from_prior_posting")
                  or "published")
    elif source == "sec_procurement_forecast":
        component = _first(normalized, "component")
        description = _first(normalized, "description")
        # SEC publishes an opaque A-E code without a legend. It is not a
        # value range and the file publishes no forecast solicitation/award
        # dates, so those fields remain intentionally empty.
        value_range = solicitation = award = None
        fiscal_year = "2026"
        award_type = _first(normalized, "contract_type")
        incumbent = _first(normalized, "incumbent_stated")
        status = _first(normalized, "forecast_status") or "published"
    else:  # pragma: no cover - caller registry constrains the source set
        component = description = value_range = solicitation = award = None
        fiscal_year = award_type = incumbent = status = None

    contacts = [
        " · ".join(value for value in (
            _text(contact.name), _text(contact.email), _text(contact.phone))
            if value)
        for contact in opportunity.contacts
    ]
    predecessor_contract_id = _first(
        normalized,
        "incumbent_contract_stated",
        "incumbent_contract_number",
        "incumbent_contract_id",
    ) or _first(raw, "piid", "awarded_contract_order")
    source_fields = dict(normalized) if normalized else {
        key: value
        for key, value in raw.items()
        if not str(key).startswith("_")
    }
    return ForecastRecord(
        source=source,
        source_id=opportunity.source_id,
        agency=opportunity.agency or source,
        component=component,
        title=opportunity.title,
        description=description,
        naics_code=opportunity.naics_code,
        estimated_value_range=value_range,
        anticipated_solicitation=solicitation,
        anticipated_award=award,
        fiscal_year=fiscal_year,
        award_type=award_type,
        set_aside=opportunity.set_aside,
        small_business_poc=next((value for value in contacts if value), None),
        url=str(opportunity.api_url),
        retrieved_at=_retrieved(provenance),
        data_as_of=_text((provenance or {}).get("data_as_of")),
        forecast_status=status,
        psc=opportunity.psc_code,
        incumbent_stated=incumbent,
        predecessor_contract_id=predecessor_contract_id,
        source_fields=source_fields,
    )
