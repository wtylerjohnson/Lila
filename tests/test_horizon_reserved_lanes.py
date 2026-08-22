"""Reserved source metadata must never masquerade as keyword evidence."""

from agents.reports.horizon_discovery import REGISTRY


def test_federal_register_errors_list_cannot_fabricate_keyword_row():
    payload = {
        "zero trust": [{
            "title": "Federal Zero Trust Strategy Update",
            "type": "Notice",
            "publication_date": "2026-07-10",
            "html_url": "https://www.federalregister.gov/documents/2026/07/10/1",
            "agencies": [{"name": "Department of Homeland Security"}],
        }],
        "errors": [{
            "title": "This is transport metadata, not a notice",
            "type": "Notice",
            "publication_date": "2026-07-11",
            "html_url": "https://www.federalregister.gov/documents/not-real",
        }],
    }

    rows = REGISTRY.get("federal_register").pull({
        "federal_register": payload,
    })

    assert len(rows) == 1
    assert "matched: zero trust" in rows[0]["text"]
    assert all("matched: errors" not in row["text"] for row in rows)
