"""Website-first Step 1 mastery: official-site ingest drives the dossier.

Offline. A mock vendor site with Products / Compare / Customers pages must
yield discrete products, named rivals, and customer proof with evidence_ids.
A JS-empty site plus generic SERP blurbs must not invent that picture.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agents.company_research import CompanyResearch
from agents.intake.adapters import retrieval_frame, strategy_from_dossier
from agents.intake.dossier import build_dossier
from agents.intake.extract import (
    extract_surface,
    is_customer_name,
    is_product_name,
    recall_competitors,
    recall_customers,
    recall_products,
    site_has_usable_text,
    usable_site_text,
)
from agents.intake.readiness import evaluate_readiness
from agents.intake.identity import bind_identity
from agents.intake.probes import ResearchProbe
from tools.scrape.site import SITE_MASTERY_MAX_PAGES, ScrapedPage, ScrapeBundle, scrape_site


ROOT = "https://www.acme-net.example"

_HOME_HTML = """<html><body>
Error loading the page. Enable JavaScript.
<nav><a href="/products">Products</a><a href="/compare">Compare</a>
<a href="/customers">Customers</a></nav>
</body></html>"""

_PRODUCTS_HTML = """<html><body>
<h1>Products</h1>
<p>Acme Net sells EOS, the Extensible Operating System, CloudVision,
CloudVision AGNI / Guardian for Network Identity, DANZ Monitoring Fabric,
and the 7050X switch family.</p>
<ul>
<li>EOS</li>
<li>CloudVision</li>
<li>AGNI</li>
<li>DANZ Monitoring Fabric</li>
<li>7050X</li>
</ul>
<footer><a href="/compare">Compare</a><a href="/customers">Customers</a></footer>
</body></html>"""

_COMPARE_HTML = """<html><body>
<h1>Compare</h1>
<p>Unlike Cisco and Juniper, Acme Net ships EOS on every 7050X.
Alternatives to Cisco include Acme Net CloudVision.</p>
</body></html>"""

_CUSTOMERS_HTML = """<html><body>
<h1>Customers</h1>
<p>Customers include Microsoft and Netflix. Trusted by US Army for
campus switching. Case study: Microsoft deployed CloudVision.</p>
</body></html>"""

_PAGES = {
    ROOT: _HOME_HTML,
    ROOT + "/products": _PRODUCTS_HTML,
    ROOT + "/compare": _COMPARE_HTML,
    ROOT + "/customers": _CUSTOMERS_HTML,
}


def _fetcher(url: str):
    key = (url or "").split("#")[0].rstrip("/")
    return _PAGES.get(key)


def _identity():
    return bind_identity(
        "Acme Net",
        [],
        form_website=ROOT,
    )


def test_mastery_budget_is_documented_and_raised():
    assert SITE_MASTERY_MAX_PAGES >= 24


def test_scrape_seeds_hubs_when_homepage_is_js_shell():
    fetched: list[str] = []

    def track(url: str):
        fetched.append(url)
        return _fetcher(url)

    bundle = scrape_site(ROOT, max_pages=SITE_MASTERY_MAX_PAGES, fetcher=track)
    urls = {p.url for p in bundle.pages}
    assert ROOT + "/products" in urls
    assert ROOT + "/compare" in urls
    assert ROOT + "/customers" in urls
    assert ROOT in bundle.render_failures or ROOT not in urls
    assert any("/products" in u for u in fetched)
    assert site_has_usable_text(bundle)
    text = usable_site_text(bundle)
    assert "Enable JavaScript" not in text
    assert "EOS" in text and "Cisco" in text and "Microsoft" in text


def test_mock_site_products_competitors_customers_land_on_dossier():
    ident = _identity()
    assert ident.is_bound
    bundle = scrape_site(ROOT, fetcher=_fetcher)
    research = CompanyResearch(
        company_name="Acme Net",
        website=ROOT,
        scrape=bundle,
    )
    dossier = build_dossier(
        client_name="Acme Net",
        identity=ident,
        research=research,
        probes=[],
    )
    offerings = {o.text for o in dossier.offerings}
    assert {"EOS", "CloudVision", "AGNI", "7050X"} <= offerings
    assert any("danz" in o.casefold() for o in offerings)
    rivals = {c.text for c in dossier.competitors}
    assert {"Cisco", "Juniper"} <= rivals
    assert all(c.evidence_ids for c in dossier.competitors)
    ev = {e.evidence_id: e for e in dossier.evidence}
    for rival in dossier.competitors:
        url = ev[rival.evidence_ids[0]].url or ""
        assert "acme-net.example" in url
        assert not url.casefold().endswith(".pdf")
    customers = {c.text for c in dossier.customers}
    assert "Microsoft" in customers
    assert any(c.casefold() in {"us army", "u.s. army", "netflix"}
               for c in customers)
    assert all(c.evidence_ids for c in dossier.customers)
    ev = {e.evidence_id: e for e in dossier.evidence}
    for claim in list(dossier.competitors) + list(dossier.customers):
        for eid in claim.evidence_ids:
            assert ev[eid].excerpt
            assert claim.text.split()[0].casefold() in ev[eid].excerpt.casefold() \
                or claim.text.casefold() in ev[eid].excerpt.casefold()

    frame = retrieval_frame(dossier)
    assert {"Cisco", "Juniper"} <= set(frame["competitors"])
    assert "Microsoft" in frame["customers"]
    assert {"Cisco", "Juniper"} <= set(
        frame["frame"]["screen_routing"]["tier1_rival_names"])
    assert "Microsoft" in frame["frame"]["screen_routing"]["customer_names"]
    strategy = strategy_from_dossier(dossier)
    entity_names = {e.name for e in strategy.research_entities
                    if e.kind == "competitor"}
    assert {"Cisco", "Juniper"} <= entity_names


def test_empty_site_plus_generic_blurbs_do_not_fabricate_mastery():
    ident = _identity()
    scrape = ScrapeBundle(
        root_url=ROOT,
        pages=[ScrapedPage(
            url=ROOT,
            text="Error loading the page. Enable JavaScript.",
        )],
        sources=[ROOT],
    )
    probes = [
        ResearchProbe(
            name="offerings",
            query="generic",
            findings=(
                "Wikipedia describes Acme Net as a leading cloud networking "
                "vendor offering switching, routing, and software-defined "
                "networking platforms such as EOS and CloudVision. "
                "Industry blurbs also name AGNI and the 7050X family."
            ),
            citations=[
                "https://en.wikipedia.org/wiki/Acme_Net",
                "https://www.crunchbase.com/organization/acme-net",
            ],
        ),
        ResearchProbe(
            name="competitors",
            query="rivals",
            findings=(
                "Analyst notes list Cisco, Juniper, and NVIDIA as primary "
                "rivals in the switching market."
            ),
            citations=["https://www.gartner.com/reviews/acme-net"],
        ),
        ResearchProbe(
            name="customers",
            query="proof",
            findings="Customers include Microsoft, Netflix, and the US Army.",
            citations=["https://www.crunchbase.com/organization/acme-net"],
        ),
    ]
    surface = extract_surface(
        probes=probes,
        scrape=scrape,
        bound_name="Acme Net",
        official_domain="acme-net.example",
        client_name="Acme Net",
    )
    assert surface.offerings == []
    assert surface.competitors == []
    assert surface.customers == []

    dossier = build_dossier(
        client_name="Acme Net",
        identity=ident,
        research=CompanyResearch(
            company_name="Acme Net", website=ROOT, scrape=scrape),
        probes=probes,
    )
    assert dossier.offerings == []
    assert dossier.competitors == []
    assert dossier.customers == []
    unknown_blob = " ".join(dossier.unknowns).casefold()
    assert "thin" in unknown_blob or "generic web" in unknown_blob
    assert "competitors" in unknown_blob
    frame = retrieval_frame(dossier)
    assert frame["competitors"] == []
    assert frame["customers"] == []


def test_official_probe_still_recalls_products_when_homepage_is_js_shell():
    ident = _identity()
    scrape = ScrapeBundle(
        root_url=ROOT,
        pages=[ScrapedPage(
            url=ROOT,
            text="Error loading the page. Enable JavaScript.",
        )],
        sources=[ROOT],
    )
    probes = [
        ResearchProbe(
            name="offerings",
            query="products",
            findings=(
                "The official product page names EOS, CloudVision, AGNI, "
                "DANZ Monitoring Fabric, and the 7050X switch family."
            ),
            citations=[ROOT + "/products"],
        ),
    ]
    dossier = build_dossier(
        client_name="Acme Net",
        identity=ident,
        research=CompanyResearch(
            company_name="Acme Net", website=ROOT, scrape=scrape),
        probes=probes,
    )
    texts = {o.text for o in dossier.offerings}
    assert {"EOS", "CloudVision", "AGNI", "7050X"} <= texts
    assert any("danz" in t.casefold() for t in texts)


def test_failed_scrape_does_not_green_e2():
    ident = _identity()
    scrape = ScrapeBundle(
        root_url=ROOT,
        pages=[ScrapedPage(
            url=ROOT,
            text="Error loading the page. Enable JavaScript.",
        )],
        sources=[ROOT],
        render_failures=[ROOT, ROOT + "/products"],
    )
    research = CompanyResearch(
        company_name="Acme Net", website=ROOT, scrape=scrape)
    dossier = build_dossier(
        client_name="Acme Net", identity=ident, research=research)
    ready = evaluate_readiness(identity=ident, dossier=dossier, research=research)
    e2 = next(row for row in ready["receipts"] if row["id"] == "E2")
    assert e2["ok"] is False
    assert "usable" not in e2["detail"] or "failed to render" in e2["detail"]
    assert "page(s) read" not in e2["detail"]
    unknown_blob = " ".join(dossier.unknowns).casefold()
    assert "thin" in unknown_blob or "load-error" in unknown_blob


def test_junk_titles_are_not_offerings_or_customers():
    assert is_product_name("Also has a telemetry") is False
    assert is_product_name("CaseStudies") is False
    assert is_product_name("OneLogin") is False
    assert is_product_name("EOS") is True
    assert is_product_name("DANZ Monitoring Fabric") is True
    assert is_customer_name("Customer Success Story") is False
    assert is_customer_name("Going Big") is False
    assert is_customer_name("Group VP") is False
    assert is_customer_name("Cognitive Campus") is False
    assert is_customer_name("Microsoft") is True
    assert is_customer_name("US Army") is True

    ident = _identity()
    scrape = ScrapeBundle(
        root_url=ROOT,
        pages=[
            ScrapedPage(
                url=ROOT + "/products",
                text=(
                    "Also has a telemetry. CaseStudies. OneLogin SSO. "
                    "Acme Net sells EOS and CloudVision AGNI."
                ),
            ),
            ScrapedPage(
                url=ROOT + "/customers",
                text=(
                    "Customer Success Story. Going Big. Group VP. "
                    "Cognitive Campus. Customers include Microsoft."
                ),
            ),
        ],
        sources=[ROOT + "/products", ROOT + "/customers"],
    )
    dossier = build_dossier(
        client_name="Acme Net",
        identity=ident,
        research=CompanyResearch(
            company_name="Acme Net", website=ROOT, scrape=scrape),
    )
    texts = {o.text for o in dossier.offerings}
    assert "Also has a telemetry" not in texts
    assert "CaseStudies" not in texts
    assert "OneLogin" not in texts
    assert {"EOS", "CloudVision", "AGNI"} <= texts
    customers = {c.text for c in dossier.customers}
    assert "Customer Success Story" not in customers
    assert "Going Big" not in customers
    assert "Group VP" not in customers
    assert "Cognitive Campus" not in customers
    assert "Microsoft" in customers


def test_compare_page_competitors_use_site_evidence_urls():
    ident = _identity()
    compare = ROOT + "/compare"
    scrape = ScrapeBundle(
        root_url=ROOT,
        pages=[ScrapedPage(
            url=compare,
            text="Unlike Cisco and Juniper, Acme Net ships EOS on every 7050X.",
        )],
        sources=[compare],
    )
    probes = [
        ResearchProbe(
            name="competitors",
            query="rivals",
            findings="Analyst PDF names Aruba and Juniper as NDR rivals.",
            citations=["https://www.darktrace.com/some-brief.pdf"],
        ),
    ]
    dossier = build_dossier(
        client_name="Acme Net",
        identity=ident,
        research=CompanyResearch(
            company_name="Acme Net", website=ROOT, scrape=scrape),
        probes=probes,
    )
    rivals = {c.text: c for c in dossier.competitors}
    assert {"Cisco", "Juniper"} <= set(rivals)
    ev = {e.evidence_id: e for e in dossier.evidence}
    for name in ("Cisco", "Juniper"):
        eids = rivals[name].evidence_ids
        assert eids
        assert ev[eids[0]].url == compare
        assert "darktrace" not in (ev[eids[0]].url or "").casefold()
    assert "Aruba" not in rivals


def test_recall_helpers_need_site_language_not_generic_nouns():
    site = (
        "Unlike Cisco and Juniper, Acme Net ships EOS. "
        "Customers include Microsoft. Trusted by US Army for campus switching."
    )
    assert {"Cisco", "Juniper"} <= set(recall_competitors(site, "Acme Net"))
    assert "Microsoft" in recall_customers(site, "Acme Net")
    assert any("army" in c.casefold() for c in recall_customers(site, "Acme Net"))
    assert "EOS" in recall_products(
        "Acme Net sells EOS and DANZ Monitoring Fabric on the 7050X family.")
    generic = (
        "The company is a leading provider of innovative networking solutions "
        "for enterprises and governments worldwide."
    )
    assert recall_competitors(generic, "Acme Net") == []
    assert recall_customers(generic, "Acme Net") == []
