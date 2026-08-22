"""Dual-key SAM rotation (2026-07-25): two active keys, one pooled budget."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

import tools.api.sam_gov as sam_gov
from tools.api import sam_quota


class _Throttled(Exception):
    def __init__(self):
        super().__init__("429 too many requests")
        self.response = SimpleNamespace(status_code=429)


def test_search_rotates_to_the_second_key_on_throttle(monkeypatch, tmp_path):
    monkeypatch.setenv("SAM_GOV_API_KEY_2", "SAM-" + "b" * 32)
    monkeypatch.setenv("LILA_SAM_DAILY_QUOTA", "1000")
    monkeypatch.setattr(sam_quota, "_STATE_PATH",
                        str(tmp_path / "sam_calls.json"), raising=False)
    calls = []

    def _fake_get_json(url, params=None, timeout=None, headers=None):
        calls.append(params["api_key"])
        if params["api_key"].endswith("a" * 32):
            raise _Throttled()
        return {"opportunitiesData": [], "totalRecords": 0}

    monkeypatch.setattr(sam_gov, "get_json", _fake_get_json)
    source = sam_gov.SamGovSource(api_key="SAM-" + "a" * 32)
    from datetime import date
    from tools.api.base import SourceQuery
    source.search(SourceQuery(
        keywords=["observability"],
        posted_from=date(2026, 1, 1), posted_to=date(2026, 7, 1)))
    assert calls[0].endswith("a" * 32)
    assert any(key.endswith("b" * 32) for key in calls), \
        "the second key was never tried"


def test_daily_quota_pools_across_configured_keys(monkeypatch):
    monkeypatch.setenv("SAM_GOV_API_KEY", "SAM-" + "a" * 32)
    monkeypatch.setenv("SAM_GOV_API_KEY_2", "SAM-" + "b" * 32)
    monkeypatch.delenv("LILA_SAM_DAILY_QUOTA", raising=False)
    # 10 PER KEY, PERMANENTLY. This asserted 1000/key, which is the
    # role-upgrade figure and there is no role. The guard therefore enforced a
    # 2000-call pool against a real ceiling of 20 and never fired: an
    # apexanalytix press pushed 28 calls out and was stopped by sam.gov's 429,
    # not by us. The test was pinning the wrong ceiling in place.
    assert sam_quota.configured_keys() == 2
    assert sam_quota.daily_quota() == 20
    monkeypatch.delenv("SAM_GOV_API_KEY_2", raising=False)
    assert sam_quota.daily_quota() == 10
