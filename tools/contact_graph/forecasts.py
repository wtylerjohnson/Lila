"""Append-only forecast POC observations, using original publication dates."""
from tools.api.forecasts.contacts import contact_record
from tools.contact_graph.schemas import ContactObservation


def observations_from_forecast(record, *, harvested_at):
    projected = contact_record(record)
    if projected is None:
        return []
    observed = projected["published"]
    if observed and observed > harvested_at.date():
        observed = None
    result = []
    for contact in projected["contacts"]:
        channels = [(kind, getattr(contact, kind)) for kind in ("email", "phone")
                    if getattr(contact, kind)] or [(None, None)]
        for kind, value in channels:
            result.append(ContactObservation(
                source=projected["source"], notice_id=projected["source_id"],
                person_name=contact.name, channel_kind=kind, channel_value=value,
                channel_source="poc_field", role_type=contact.contact_type, title=contact.title,
                agency=projected["agency"], office_path=projected["component"],
                notice_type="Forecast", naics=projected["naics_code"],
                source_url=projected["source_url"], observed_at=observed,
                harvested_at=harvested_at))
    return result
