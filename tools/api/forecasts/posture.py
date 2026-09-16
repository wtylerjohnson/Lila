"""Narrow original-source withdrawal signals; never infer a successor buy."""
import re


def withdrawal_evidence(row):
    status = str(row.get('forecast_status') or '').strip().casefold()
    if status in {'cancelled', 'canceled', 'withdrawn', 'no longer planned'}:
        return {'field': 'forecast_status', 'quote': row['forecast_status']}
    description = str(row.get('description') or '')
    patterns = (
        r'\bthis requirement is no longer planned\b[^\r\n.]*',
        r'\bAPFS no longer required\b[^\r\n]*',
    )
    for pattern in patterns:
        match = re.search(pattern, description, re.I)
        if match:
            return {'field': 'description', 'quote': match.group(0)}
    return None
