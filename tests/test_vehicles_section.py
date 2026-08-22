"""Vehicle considerations: catalog integrity + report integration."""
from tools.vehicles import load_vehicles, vehicles_section_body, vehicles_heading


def test_catalog_loads_and_has_entries():
    data = load_vehicles()
    assert len(data["vehicles"]) >= 20


def test_unverified_entries_never_render_in_confirmed_table():
    data = load_vehicles()
    body = vehicles_section_body()
    table_part = body.split("Tracked, pending verification")[0]
    for v in data["vehicles"]:
        if not v.get("verified"):
            assert f"| {v['name']} |" not in table_part, v["name"]


def test_client_misattributions_are_corrected_not_copied():
    data = load_vehicles()
    by_name = {v["name"]: v for v in data["vehicles"]}
    assert "DHS" in by_name["PACTS III"]["agency"]          # client said Treasury
    assert "Army" in by_name["ADMC-3"]["agency"]            # client said CDC
    assert "DIA" in by_name["SITE III"]["agency"]           # client said NGA
    assert "STARS III" in by_name["8(a) STARS III"]["name"] # client said STARS II


def test_section_heading_stable():
    assert vehicles_heading() == "Contract vehicle considerations"
