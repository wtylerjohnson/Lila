"""Source temporal contract independent of Research Picture projection."""
from datetime import datetime, timezone
import pytest
from tools.relevance.temporal import source_time, record_time, latest_posting


@pytest.mark.parametrize('value,status', [(None, 'missing'), ('', 'missing'),
    ('2026-09-12 garbage', 'invalid'), ('2026-09-12T99:00Z', 'invalid'),
    ('2026-09-12', 'date_only'), ('2026-09-12T10:30', 'unknown_timezone')])
def test_unknown_values_cannot_supply_an_instant(value, status):
    parsed = source_time(value, field='raw_payload.deadline', source_id='N1')
    assert parsed['raw'] == value and parsed['parse_status'] == status
    assert parsed['instant_utc'] is None and parsed['source_id'] == 'N1'


def test_offset_precision_and_raw_text_survive():
    parsed = source_time(' 2026-09-12T12:00:01.125-04:00 ', field='raw_payload.deadline', source_id='N1')
    assert parsed['raw'] == ' 2026-09-12T12:00:01.125-04:00 '
    assert parsed['instant_utc'] == '2026-09-12T16:00:01.125000+00:00'
    assert parsed['offset_minutes'] == -240 and parsed['offset_text'] == '-04:00'
    assert parsed['precision'] == 'fraction'


def test_cached_projection_cannot_override_original_fields():
    row = {'source_id': 'N1', 'response_deadline': '2026-09-12',
           'raw_payload': {'responseDeadLine': '2026-09-12T12:00:01-04:00'},
           'temporal_evidence': {'deadline': {'instant_utc': '2099-01-01T00:00Z'}}}
    rebuilt = record_time(row, 'deadline')
    assert rebuilt['status'] == 'aware' and not rebuilt['conflict']
    assert rebuilt['selected']['instant_utc'] == '2026-09-12T16:00:01+00:00'
    assert rebuilt['derived_display_fields'] == ['response_deadline']
    row['response_deadline'] = '2026-09-13'
    assert record_time(row, 'deadline')['conflict']


def test_posting_order_uses_instants_not_ids_or_offset_strings():
    rows = [{'source_id': 'Z', 'posted_date': '2026-09-12', 'raw_payload': {'posted': '2026-09-12T09:00Z'}},
            {'source_id': 'A', 'posted_date': '2026-09-12', 'raw_payload': {'posted': '2026-09-12T12:00-04:00'}}]
    assert latest_posting(rows) == (1, 'ordered')
    rows[0]['raw_payload']['posted'] = '2026-09-12T16:00Z'
    assert latest_posting(rows)[1] == 'unresolved'
