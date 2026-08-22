"""Offline Horizon mapping never owns the mutable live-pull ledger."""

from agents.reports.horizon_discovery import REGISTRY


def test_offline_mapping_never_touches_coverage_writer(monkeypatch):
    from tools.api.forecasts import coverage

    calls = []
    monkeypatch.setattr(
        coverage, "record_pull", lambda *args: calls.append(args),
    )
    rows = REGISTRY.get("federal_register").pull({
        "federal_register": {
            "zero trust": [{
                "title": "Federal Zero Trust Strategy Update",
                "type": "Notice",
                "publication_date": "2026-07-10",
                "html_url": (
                    "https://www.federalregister.gov/documents/2026/07/10/1"
                ),
            }],
        },
    })

    assert len(rows) == 1
    assert calls == []
