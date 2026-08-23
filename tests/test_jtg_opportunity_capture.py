"""Regression witnesses for the JTG opportunity-capture seam."""

from __future__ import annotations

from types import SimpleNamespace

from agents.golden_press.market_map_projection import (
    _pack_notice_terms,
    fetch_store_candidates,
    store_opportunities,
)
from agents.golden_press.records import LaneQuery
from tools import notice_store as ns


def _insert(conn, notice_id, *, title, description, naics,
            notice_type="Solicitation"):
    with conn:
        conn.execute(
            "INSERT INTO notices (notice_id, title, notice_type, agency, "
            "subtier, office, posted, deadline, naics, psc, set_aside, url, "
            "description_prefix, description_sha256, description_len, "
            "first_seen, last_seen, sol_number) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (notice_id, title, notice_type, "DEPT OF X", "SUB", "OFFICE",
             "2026-08-19", "2026-09-25", naics, "R608", "",
             f"https://sam.gov/opp/{notice_id}/view", description, "",
             len(description), "2026-08-19", "2026-08-19", notice_id))


def test_pack_notice_query_is_reused_by_the_market_map():
    pack = SimpleNamespace(queries=[LaneQuery(
        lane="L1_notice", method="notice_store", endpoint="local",
        body={"capability_terms": ["language services", "DLITE"]})])
    assert _pack_notice_terms(pack) == ["language services", "DLITE"]


def test_client_boundary_recovers_live_solicitations_and_kills_collisions(
        tmp_path):
    conn = ns.connect(tmp_path / "notices.db")
    _insert(conn, "dlite",
            title=("Defense Language Interpretation and Translation "
                   "Enterprise DLITE III Draft Solicitation"),
            description=("Defense Language Interpretation Translation "
                         "Enterprise language services"), naics="541930")
    _insert(conn, "instructor", title="Language Instructor Services",
            description="foreign language instructors and training",
            naics="611630", notice_type="Combined Synopsis/Solicitation")
    _insert(conn, "building",
            title="Building Maintenance at Japanese Language Training Center",
            description="facility repair and custodial support", naics="236220")
    _insert(conn, "acoustic", title="Acoustic UAS Detection and Localization",
            description="sensor localization for unmanned aircraft",
            naics="334511")

    candidates = fetch_store_candidates(
        conn,
        exact_terms=["language services", "language instructor services",
                     "language training", "localization"],
        naics_boundary=["541930", "611630"])
    found = {row["notice_id"] for rows in candidates.values() for row in rows}
    assert found == {"dlite", "instructor"}
    kinds = {row["notice_type"] for rows in candidates.values() for row in rows}
    assert "Solicitation" in kinds
    assert "Combined Synopsis/Solicitation" in kinds
    conn.close()


def test_client_exclusions_and_report_clock_keep_the_lead_sheet_current(
        tmp_path):
    conn = ns.connect(tmp_path / "notices.db")
    _insert(conn, "current", title="Language Instructor Services",
            description="foreign language instruction", naics="611630",
            notice_type="Presolicitation")
    _insert(conn, "asl", title="American Sign Language Services",
            description="ASL interpreting", naics="541930")
    _insert(conn, "closed", title="Translation and Interpretation Services",
            description="translation services", naics="541930")
    with conn:
        conn.execute("UPDATE notices SET deadline='2026-08-19' "
                     "WHERE notice_id='closed'")
    terms = ["language instructor services", "language services",
             "translation and interpretation"]
    candidates = fetch_store_candidates(
        conn, exact_terms=terms, naics_boundary=["541930", "611630"])
    rejected = []
    rows = store_opportunities(
        candidates, as_of="2026-08-20", excluded_terms=["sign language"],
        rejected_out=rejected)
    assert [row.identifier for row in rows] == ["current"]
    assert {row["reason_class"] for row in rejected} == {
        "rejected_client_exclusion", "closed_historic"}
    conn.close()


def test_saved_pack_replay_uses_jtg_contract_and_exclusions():
    """Old pack rows are screened by today's approved client frame."""
    from agents.golden_press.coverage_families import load
    from agents.golden_press.market_map_projection import _opportunities

    def row(record_id, title, description):
        return SimpleNamespace(
            record_id=record_id, lane="L1_notice", title=title,
            description=description, source_url=(
                f"https://sam.gov/opp/{record_id}/view"), agency="Army",
            office="", notice_type="Solicitation", period_end="2026-09-25",
            value_display="", published_value="", ceiling_dollars=0,
            obligated_dollars=0)

    pack = SimpleNamespace(client_name="JTG, inc.", records=[
        row("DLITEIII", "Language Interpretation and Translation Enterprise",
            "Enterprise translation and interpreter services."),
        row("medical", "Ultrasound equipment",
            "Electromedical imaging equipment and support."),
        row("incidental", "Enterprise support tasking",
            "The procurement boilerplate references language services."),
        row("asl", "American Sign Language interpreter services",
            "ASL interpreter requirement."),
    ])
    rejected = []
    kept = _opportunities(
        pack, {}, contract=load("jtg_inc"), rejected_out=rejected,
        excluded_terms=("American Sign Language", "sign language"))

    assert [item.identifier for item in kept] == ["DLITEIII"]
    assert {item["reason_class"] for item in rejected} == {
        "rejected_out_of_frame", "rejected_client_exclusion"}
