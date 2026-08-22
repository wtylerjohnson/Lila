"""Forward-looking search gate: never surface an opportunity past its deadline."""

from __future__ import annotations

import os
import sys
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools.api import sam_gov as sam_mod  # noqa: E402
from tools.api.base import SourceQuery  # noqa: E402
from tools.api.sam_gov import SamGovSource  # noqa: E402

TODAY = date(2026, 7, 2)


def _payload():
    return {"opportunitiesData": [
        {"noticeId": "past", "title": "Closed deal", "responseDeadLine": "2026-06-01"},
        {"noticeId": "open", "title": "Open deal", "responseDeadLine": "2026-09-01"},
        {"noticeId": "nodeadline", "title": "Sources sought (no deadline)"},
    ]}


def test_past_deadline_notices_are_dropped(monkeypatch):
    captured = {}

    def fake_get(url, params=None, **kw):
        captured.update(params or {})
        return _payload()

    monkeypatch.setattr(sam_mod, "get_json", fake_get)
    q = SourceQuery(naics_codes=["541512"], posted_from=date(2025, 7, 5), posted_to=TODAY,
                    deadline_from=TODAY, deadline_to=date(2027, 7, 1), limit=10)
    src = SamGovSource(api_key="test")
    ids = [o.source_id for o in src.search(q)]
    assert "past" not in ids           # dead inventory dropped
    assert "open" in ids               # actionable kept
    assert "nodeadline" in ids         # RFIs/sources-sought kept (forward signals)
    assert captured["rdlfrom"] == "07/02/2026"  # API-side filter requested too
    assert captured["rdlto"] == "07/01/2027"


def test_no_deadline_filter_means_no_rdl_params(monkeypatch):
    captured = {}

    def fake_get(url, params=None, **kw):
        captured.update(params or {})
        return _payload()

    monkeypatch.setattr(sam_mod, "get_json", fake_get)
    q = SourceQuery(naics_codes=["541512"], posted_from=date(2025, 7, 5), posted_to=TODAY)
    SamGovSource(api_key="test").search(q)
    assert "rdlfrom" not in captured and "rdlto" not in captured
