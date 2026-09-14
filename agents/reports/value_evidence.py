"""Explain published amounts without converting award history into pipeline.

Field meaning is retained, never inferred from magnitude or divided into an
annual run rate. Different measures remain separate even when amounts match.
"""
from __future__ import annotations

from html import escape
from collections.abc import Mapping


_FIELDS = (
    ("obligated_dollars", "cumulative_obligations"),
    ("total_obligation", "cumulative_obligations"),
    ("ceiling_dollars", "contract_ceiling"),
    ("base_and_all_options", "contract_ceiling"),
    ("base_exercised_options", "exercised_contract_value"),
    ("federal_action_obligation", "funding_action"),
    ("annual_renewal_amount", "annual_renewal"),
    ("estimated_value_range", "future_estimate"),
    ("published_value", "unspecified_published_value"),
)
_LABELS = {
    "cumulative_obligations": ("Cumulative award obligations", "Obligations recorded across this award. This is not an annual renewal amount or new available spending."),
    "contract_ceiling": ("Potential contract value", "Potential value including options. This is not obligated funding or a new buying decision."),
    "exercised_contract_value": ("Base and exercised options", "Value of the base and exercised options. This is distinct from cumulative obligations even when the amounts match."),
    "funding_action": ("Funding action", "Amount of this individual action, which may add or remove funding. An action alone does not establish annual renewal value."),
    "annual_renewal": ("Reported annual renewal amount", "Explicit annual renewal field. Confirm its covered year and reconcile it with award and action records before using it as a buying amount."),
    "future_estimate": ("Estimated future value", "Published planning estimate. It is not a funded commitment, an award, or a confirmed seller opportunity."),
    "unspecified_published_value": ("Published amount; basis needs confirmation", "The retained field does not establish whether this is annual, cumulative, an action, a ceiling, or an estimate."),
}


def value_evidence(row: Mapping, *, source_url: str = "", source_id: str = "") -> list[dict]:
    """Project known native/API fields, preserving zero and separate measures."""
    pop = row.get("period_of_performance") or {}
    start = row.get("period_start") or pop.get("start_date")
    end = row.get("period_end") or row.get("end_date") or pop.get("end_date")
    potential = row.get("potential_end_date") or pop.get("potential_end_date")
    url = source_url or row.get("source_url") or row.get("url") or ""
    identity = source_id or row.get("generated_unique_award_id") or row.get("generated_internal_id") or row.get("record_id") or row.get("notice_id") or row.get("award_id") or ""
    result = []
    fields = list(_FIELDS)
    if row.get("amount") is not None:
        # Native buyer-map records preserve this explicit API amount basis.
        # An unrecognized basis stays unspecified, never silently annualized.
        basis = row.get("amount_basis")
        measure = {"obligated_to_date": "cumulative_obligations"}.get(basis, "unspecified_published_value")
        fields.append(("amount", measure))
    for field, measure in fields:
        amount = row.get(field)
        if amount is None or amount == "" or isinstance(amount, bool):
            continue
        # published_value is a compatibility projection, not another amount.
        if field == "published_value" and result:
            continue
        label, explanation = _LABELS[measure]
        if measure == "funding_action":
            period = {"action_date": row.get("action_date"), "basis": "Individual action; covered service period needs confirmation"}
        elif measure == "future_estimate":
            period = {"fiscal_year": row.get("fiscal_year"), "planned_award": row.get("anticipated_award"), "basis": "Published forecast; planned dates may change"}
        elif measure == "annual_renewal":
            period = {"year": row.get("renewal_year"), "basis": "Covered renewal year needs confirmation"}
        else:
            period = {"start": start, "end": potential if measure == "contract_ceiling" else end,
                      "basis": "Award period; not an annual allocation" if start or end else "Period not established in this record"}
        result.append({"measure": measure, "label": label, "amount": amount,
                       "period": period, "source_field": "/" + field,
                       **({"source_basis_field": "/amount_basis"} if field == "amount" else {}),
                       "source_id": identity, "source_url": url,
                       "explanation": explanation})
    return result


def render_values(values: list[dict], *, source_link: str = "") -> str:
    """Visible amount meaning and inspectable field/period explanation."""
    rows = []
    for item in values:
        amount = item["amount"]
        shown = f"${amount:,.2f}" if isinstance(amount, (int, float)) else str(amount)
        period = "; ".join(f"{k.replace('_', ' ')}: {v}" for k, v in item["period"].items() if v is not None)
        rows.append('<div><b>' + escape(item["label"]) + ': '
                    + escape(shown) + '</b><details><summary>Show work</summary><p>'
                    + escape(item["explanation"]) + '</p><p>' + escape(period) + '</p><p>'
                    + escape(item["source_id"] + item["source_field"]) + ' '
                    + (source_link or 'Source link not established in this record.')
                    + '</p></details></div>')
    return ''.join(rows)
