"""CISA KEV adapter + truthful API-switch statuses. No network."""

import pytest

from agents.reports.facts import build_fact_pack
from tools.api.base import REGISTRY, SourceQuery
from tools.api.cisa_kev import CisaKevSource

KEV = {"count": 3, "vulnerabilities": [
    {"cveID": "CVE-2026-1", "vendorProject": "Cisco", "product": "SD-WAN",
     "vulnerabilityName": "RCE", "shortDescription": "remote code execution",
     "dateAdded": "2026-06-25", "knownRansomwareCampaignUse": "Known"},
    {"cveID": "CVE-2026-2", "vendorProject": "Fortinet", "product": "FortiSandbox",
     "vulnerabilityName": "auth bypass", "shortDescription": "threat intelligence platform bypass",
     "dateAdded": "2026-06-28", "knownRansomwareCampaignUse": "Unknown"},
    {"cveID": "CVE-2020-9", "vendorProject": "Old", "product": "Legacy",
     "vulnerabilityName": "old", "shortDescription": "ancient",
     "dateAdded": "2020-01-01", "knownRansomwareCampaignUse": "Unknown"},
]}


def test_kev_registered():
    assert "cisa_kev" in {s.name for s in REGISTRY.all()}


def test_kev_recent_window_and_keyword_match(monkeypatch):
    import tools.api.cisa_kev as k

    class FixedDate(k.date):
        @classmethod
        def today(cls):
            return cls(2026, 7, 9)

    monkeypatch.setattr(k, "date", FixedDate)
    monkeypatch.setattr(k, "get_json", lambda url, **kw: KEV)
    out = CisaKevSource().enrich(SourceQuery(keywords=['"threat intelligence"']))
    assert out["recent_count"] == 2          # 2020 entry excluded
    assert out["total"] == 3
    assert out["matched"][0]["cve"] == "CVE-2026-2"


def test_kev_preserves_more_than_eight_recent_and_matching_records(monkeypatch):
    import tools.api.cisa_kev as k

    today = k.date.today().isoformat()
    feed = {"count": 10, "vulnerabilities": [{
        "cveID": f"CVE-2026-{index}",
        "vendorProject": "Arista",
        "product": "CloudVision",
        "vulnerabilityName": f"Issue {index}",
        "shortDescription": "network telemetry vulnerability",
        "dateAdded": today,
        "knownRansomwareCampaignUse": "Unknown",
    } for index in range(10)]}
    monkeypatch.setattr(k, "get_json", lambda url, **kw: feed)

    out = CisaKevSource().enrich(SourceQuery(keywords=["CloudVision"]))

    assert out["recent_count"] == 10
    assert len(out["recent_sample"]) == 10
    assert len(out["matched"]) == 10
    assert out["matched"][-1]["cve"] == "CVE-2026-9"


def test_kev_healthcheck_truthful(monkeypatch):
    import tools.api.cisa_kev as k
    monkeypatch.setattr(k, "get_json", lambda url, **kw: KEV)
    ok, detail = CisaKevSource().healthcheck()
    assert ok and "3 entries" in detail

    def boom(url, **kw): raise RuntimeError("dns fail")
    monkeypatch.setattr(k, "get_json", boom)
    ok, detail = CisaKevSource().healthcheck()
    assert not ok and "dns fail" in detail


def test_kev_rejects_false_clean_response_but_accepts_explicit_empty(
        monkeypatch):
    import tools.api.cisa_kev as k

    monkeypatch.setattr(k, "get_json", lambda url, **kw: {
        "message": "maintenance"
    })
    with pytest.raises(ValueError, match="vulnerabilities list"):
        CisaKevSource().enrich(SourceQuery(keywords=["cloud"]))
    assert CisaKevSource().healthcheck()[0] is False

    monkeypatch.setattr(k, "get_json", lambda url, **kw: {
        "vulnerabilities": [{"message": "maintenance"}]
    })
    with pytest.raises(ValueError, match="omitted cveID"):
        CisaKevSource().enrich(SourceQuery(keywords=["cloud"]))

    monkeypatch.setattr(k, "get_json", lambda url, **kw: {
        "vulnerabilities": []
    })
    out = CisaKevSource().enrich(SourceQuery(keywords=["cloud"]))
    assert out["total"] == 0
    assert out["matched"] == []


def test_fact_pack_ingests_kev():
    searches = {"results": {"usaspending.gov": [], "cisa_kev": {
        "recent_count": 2, "window_days": 45, "total": 1400,
        "matched": [{"cve": "CVE-2026-2", "vendor": "Fortinet", "product": "FortiSandbox",
                     "added": "2026-06-28", "ransomware": "Known"}],
        "catalog_url": "https://www.cisa.gov/known-exploited-vulnerabilities-catalog"}}}
    pack = build_fact_pack("Testco", searches=searches, qualify_report={"candidates": []})
    texts = " | ".join(f.text for f in pack.facts)
    assert "CISA added 2 actively exploited" in texts
    assert "CVE-2026-2" in texts and "ransomware" in texts


def test_source_statuses_reflect_probe_and_switch(monkeypatch):
    import ui.server as srv
    srv._HEALTH_CACHE.update(at=0.0, data=None)  # bust cache

    class FakeSrc:
        def __init__(self, name, ok, enabled=True):
            self.name, self._ok, self.enabled = name, ok, enabled
        def healthcheck(self):
            if self._ok is None:
                raise RuntimeError("probe exploded")
            return self._ok, "detail-text"

    class FakeReg:
        def all(self):
            return [FakeSrc("up_src", True), FakeSrc("down_src", False),
                    FakeSrc("crash_src", None), FakeSrc("off_src", True, enabled=False)]

    monkeypatch.setattr(srv, "is_enabled", lambda name, default=True: name != "off_src")
    got = {r["name"]: r for r in srv._source_statuses(FakeReg())}
    assert got["up_src"]["on"] and got["up_src"]["status"] == "up"
    assert not got["down_src"]["on"] and got["down_src"]["status"] == "down"
    assert not got["crash_src"]["on"] and "probe crashed" in got["crash_src"]["detail"]
    assert got["off_src"]["status"] == "off" and "switched off" in got["off_src"]["detail"]
    srv._HEALTH_CACHE.update(at=0.0, data=None)  # leave clean for other tests
