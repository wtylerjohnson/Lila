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
    MAX_CUSTOMERS,
    aviation_codes_in_text,
    aviation_codes_to_park,
    citation_mismatches_rival,
    excerpt_supports_rival,
    extract_surface,
    is_competitor_name,
    is_customer_chrome,
    is_customer_name,
    is_news_path_citation,
    is_product_name,
    is_sku_fragment_rival,
    is_truncated_org_name,
    known_customers_in_text,
    page_has_customer_proof,
    normalize_rival_name,
    recall_competitors,
    recall_customers,
    recall_products,
    site_has_usable_text,
    usable_site_text,
)
from agents.intake.readiness import evaluate_readiness
from agents.intake.identity import bind_identity
from agents.intake.probes import ResearchProbe, run_structured_probes
from tools.scrape.site import (
    SITE_MASTERY_MAX_PAGES,
    ScrapedPage,
    ScrapeBundle,
    discover_official_hubs,
    js_render_config,
    official_hub_hints,
    scrape_site,
)


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
    cfg = js_render_config()
    assert cfg["LILA_INTAKE_JS_RENDER"] is True
    assert cfg["LILA_INTAKE_JS_STEALTH"] is True
    assert cfg["LILA_INTAKE_JS_WAIT_MS"] >= 2000
    assert cfg["LILA_INTAKE_JS_MIN_CHARS"] >= 20
    assert cfg["LILA_INTAKE_JS_RENDER_CAP"] >= 1


def test_interstitial_html_triggers_js_render_path():
    rendered: list[str] = []

    def fetch(url: str):
        if url.rstrip("/") == ROOT or url.rstrip("/").endswith("/products"):
            return "<html><body>Enable JavaScript to view this page.</body></html>"
        return None

    def render(url: str):
        rendered.append(url)
        return _PRODUCTS_HTML

    bundle = scrape_site(ROOT, max_pages=8, fetcher=fetch, renderer=render)
    assert rendered
    assert any("/products" in u or u.rstrip("/") == ROOT for u in rendered)
    texts = " ".join(p.text for p in bundle.pages)
    assert "EOS" in texts
    assert "Enable JavaScript" not in usable_site_text(bundle)


def test_sitemap_discovers_official_product_hubs():
    special = ROOT + "/en/products/special-fabric"

    def fetch(url: str):
        if url.endswith("/sitemap.xml"):
            return (
                "<?xml version='1.0'?><urlset>"
                f"<loc>{special}</loc>"
                f"<loc>{ROOT}/compare</loc>"
                "</urlset>"
            )
        if url.rstrip("/") == special:
            return (
                "<html><body><h1>Special Fabric</h1>"
                "<p>Acme Net sells EOS and DANZ Monitoring Fabric "
                "on the special fabric page.</p></body></html>"
            )
        return _fetcher(url)

    hubs = discover_official_hubs(ROOT, fetcher=fetch)
    assert special in hubs
    bundle = scrape_site(ROOT, max_pages=12, fetcher=fetch)
    assert special in {p.url for p in bundle.pages}


def test_waf_empty_after_render_fails_e2_and_does_not_invent():
    def fetch(_url: str):
        return "<html><body></body></html>"

    def render(_url: str):
        return "<html><body>  </body></html>"

    bundle = scrape_site(ROOT, max_pages=6, fetcher=fetch, renderer=render)
    assert bundle.pages == []
    assert bundle.render_failures
    ident = _identity()
    probes = [
        ResearchProbe(
            name="offerings",
            query="generic",
            findings=(
                "Wikipedia describes Acme Net as a leading cloud networking "
                "vendor offering EOS, CloudVision, and AGNI."
            ),
            citations=["https://en.wikipedia.org/wiki/Acme_Net"],
        ),
        ResearchProbe(
            name="competitors",
            query="rivals",
            findings="Analysts list Cisco and Juniper.",
            citations=["https://www.gartner.com/reviews"],
        ),
    ]
    dossier = build_dossier(
        client_name="Acme Net",
        identity=ident,
        research=CompanyResearch(
            company_name="Acme Net", website=ROOT, scrape=bundle),
        probes=probes,
    )
    assert dossier.offerings == []
    assert dossier.competitors == []
    ready = evaluate_readiness(
        identity=ident, dossier=dossier,
        research=CompanyResearch(
            company_name="Acme Net", website=ROOT, scrape=bundle),
    )
    e2 = next(row for row in ready["receipts"] if row["id"] == "E2")
    assert e2["ok"] is False
    assert "page(s) read" not in e2["detail"]
    assert "thin" in " ".join(dossier.unknowns).casefold() or (
        "failed to render" in e2["detail"])


def test_thin_site_probes_require_official_urls():
    ident = _identity()

    class Engine:
        def __init__(self):
            self.queries = []

        def web_research(self, **kwargs):
            self.queries.append(str(kwargs.get("query") or ""))
            return "none found", ["https://www.acme-net.example/products"]

    engine = Engine()
    hubs = official_hub_hints(ROOT, None)
    run_structured_probes(
        ident, engine, official_urls=hubs[:6], site_thin=True)
    blob = " ".join(engine.queries).casefold()
    assert "wikipedia" in blob or "official-domain urls" in blob
    assert "acme-net.example" in blob
    assert any("products" in q for q in engine.queries)


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
    assert is_product_name("Management Team") is False
    assert is_product_name("Senior Management") is False
    assert is_product_name("Platforms page") is False
    assert is_product_name("Detection and Response Overview") is False
    assert is_product_name("JCT600") is False
    assert is_product_name("Intuit") is False
    assert is_customer_name("hedge funds") is False
    assert is_customer_name("financial services") is False
    assert is_customer_name("proof points") is False
    assert is_customer_name("PDF") is False
    assert is_customer_name("PDF. Barclays") is False
    assert "pdf. barclays" not in {c.casefold() for c in recall_customers(
        "Proof points PDF. Barclays, Citigroup, Morgan Stanley, Yahoo! "
        "and Hardis run CloudVision in production."
    )}
    assert is_customer_name("troubleshoot workloads") is False
    assert is_customer_name("ease of deployment") is False
    assert is_customer_name("6") is False
    assert is_customer_name("Barclays") is True
    assert is_customer_name("Citigroup") is True
    assert is_customer_name("Morgan Stanley") is True
    assert is_customer_name("Activ Financial") is True
    assert is_customer_name("Hardis Group") is True
    assert is_customer_name("Named Customers") is False
    assert is_customer_name("Revenue Share") is False
    assert is_customer_name("MARKET DATA FEED PROVIDER") is False
    assert is_customer_name("Costa Rica") is False
    assert is_customer_name("Cloud Titans") is False
    assert is_customer_name("Hardis Grou") is False
    assert is_customer_name("Fixed Mobile Telecoms") is False
    assert is_customer_name("Fixed Mobile") is False
    assert is_customer_name("Mobile Telecoms") is False
    assert is_customer_name("French IT") is False
    assert "Cisco" in recall_competitors(
        "Competition for the Cisco Nexus 1000V in the hypervisor.",
        "Acme Net",
    )
    assert "Cisco" in recall_competitors(
        "The switching market was historically dominated by Cisco.",
        "Acme Net",
    )
    nexus_rivals = recall_competitors(
        "Competition for the Cisco Nexus 1000V in the hypervisor.",
        "Acme Net",
    )
    assert "Cisco Nexus" not in nexus_rivals
    assert "Cisco Nexus 1000V" not in nexus_rivals
    assert "Nexus 1000V" not in nexus_rivals
    assert is_sku_fragment_rival("Cisco Nexus") is True
    assert is_sku_fragment_rival("Cisco Nexus 1000V") is True
    assert is_sku_fragment_rival("Cisco") is False
    assert is_product_name("DoD") is False
    assert is_product_name("DoDIN") is False
    assert is_product_name("DoDIN APL") is False
    assert is_product_name("Approved Products List") is False
    assert is_news_path_citation("https://www.arista.com/en/company/news") is True
    assert is_news_path_citation(
        "https://www.arista.com/en/solutions/media-and-entertainment") is False
    assert is_customer_name("MORGAN STANLEY") is True
    assert is_customer_name("CITIGROUP") is True
    assert is_customer_name("Wireless FAQ") is False
    assert is_customer_name("Real-World Deployments") is False
    assert is_customer_name("Walsh Universi") is False
    assert is_customer_name("Noodles Compa") is False
    assert is_customer_name("PB/UAX Case Stu") is False
    assert is_truncated_org_name("Walsh Universi") is True
    assert is_truncated_org_name("Walsh University") is False
    assert is_customer_chrome("Wireless FAQ") is True
    assert "Barclays" in known_customers_in_text(
        "Arista customers include six cloud titans. Others include "
        "Barclays, Citigroup, Morgan Stanley, Comcast and Equinix."
    )
    assert "Barclays" not in known_customers_in_text(
        "Morgan Stanley, Citigroup, BofA Merrill Lynch, Barclays, "
        "Credit Suisse are acting as joint book-running managers "
        "for the offering."
    )
    assert page_has_customer_proof(
        "Arista customers include six cloud titans. Others include "
        "Barclays, Citigroup and Morgan Stanley.",
        "https://www.arista.com/en/company/news/press-release/s1",
    ) is True
    assert is_customer_name("Yahoo!") is False
    assert is_customer_name("Hardis") is True
    assert is_customer_name("Login Wi-Fi Cloud") is False
    assert is_customer_name("Toggle Navigation") is False
    assert is_customer_name("Series Spine") is False
    assert is_customer_name("Meta") is False
    assert is_customer_name("Facebook") is False
    assert is_competitor_name("Here") is False
    assert is_competitor_name("This") is False
    assert is_product_name("Secure Networks vs. Network Security") is False
    assert is_product_name("1. Operating System") is False
    assert citation_mismatches_rival(
        "https://www.arista.com/en/company/darktrace-comparison", "Cisco",
        official_domain="arista.com") is True
    assert citation_mismatches_rival(
        "https://www.arista.com/en/company/darktrace-comparison", "Cisco",
        official_domain="arista.com",
        excerpt="Unlike Cisco, Arista ships EOS on this compare writeup.",
    ) is False
    assert citation_mismatches_rival(
        "https://www.arista.com/en/company/darktrace-comparison", "Darktrace",
        official_domain="arista.com") is False
    assert citation_mismatches_rival(
        "https://www.arista.com/en/company/news", "VMware",
        official_domain="arista.com") is True
    assert citation_mismatches_rival(
        "https://www.arista.com/en/company/news", "VMware",
        official_domain="arista.com",
        excerpt="Broadcom closed the VMware acquisition last quarter.",
    ) is True
    assert citation_mismatches_rival(
        "https://www.arista.com/en/company/news", "VMware",
        official_domain="arista.com",
        excerpt="Unlike VMware, this news blurb still is not a compare page.",
    ) is True
    assert aviation_codes_to_park("Arista Aviation") == ["336413", "488190"]
    assert is_product_name("CloudVision Data Sheet") is False
    assert is_product_name("SolutionBrief") is False
    assert is_product_name("From network security to secure networks") is False
    assert is_product_name("Unmatched Visibility") is False
    assert is_product_name("Operating System") is False
    assert is_product_name("Switching &amp; Routing") is False
    assert is_competitor_name("Darktrace Comparison") is True
    assert normalize_rival_name("Darktrace Comparison") == "Darktrace"
    assert citation_mismatches_rival(
        "https://www.darktrace.com/compare.pdf", "HPE",
        official_domain="arista.com") is True
    assert citation_mismatches_rival(
        "https://www.arista.com/en/products/eos", "Cisco",
        official_domain="arista.com") is False
    assert citation_mismatches_rival(
        "https://www.arista.com/compare", "Cisco",
        official_domain="arista.com") is False

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


def test_press11_nav_verticals_and_mismatched_rivals_are_rejected():
    ident = _identity()
    eos = ROOT + "/en/products/eos"
    compare = ROOT + "/compare"
    darktrace = ROOT + "/en/company/darktrace-comparison"
    scrape = ScrapeBundle(
        root_url=ROOT,
        pages=[
            ScrapedPage(
                url=ROOT + "/company/management",
                text=(
                    "Management Team. Senior Management. Platforms page. "
                    "Detection and Response Overview. Case study JCT600 "
                    "and RACSA. Intuit is a logo wall entry."
                ),
            ),
            ScrapedPage(
                url=ROOT + "/products",
                text=(
                    "Acme Net sells EOS, CloudVision, AGNI, DANZ Monitoring "
                    "Fabric, and the 7050X switch family on product pages."
                ),
            ),
            ScrapedPage(
                url=ROOT + "/customers",
                text=(
                    "Customers include hedge funds, financial services, "
                    "and Microsoft. Trusted by US Army for campus switching."
                ),
            ),
            ScrapedPage(
                url=eos,
                text=(
                    "EOS is the Extensible Operating System. Some operators "
                    "also run Cisco gear in the same closet. No head-to-head."
                ),
            ),
            ScrapedPage(
                url=compare,
                text=(
                    "Unlike Cisco and Juniper, Acme ships EOS on every "
                    "official compare page. This is a head-to-head "
                    "alternatives writeup, not a product SKU."
                ),
            ),
            ScrapedPage(
                url=darktrace,
                text=(
                    "Darktrace Comparison. Here is what the NDR rival "
                    "looks like next to CloudVision AGNI on this page."
                ),
            ),
        ],
        sources=[eos, compare, darktrace],
    )
    probes = [
        ResearchProbe(
            name="competitors",
            query="rivals",
            findings=(
                "The 10-K names Extreme and HPE among switching vendors. "
                "Unlike Cisco, Acme Net ships EOS."
            ),
            citations=["https://www.darktrace.com/resources/compare.pdf"],
        ),
    ]
    ingest = {
        "capabilities": [
            {"name": "Management Team", "found_on": ROOT + "/company"},
            {"name": "Intuit", "found_on": ROOT + "/customers"},
            {"name": "JCT600", "found_on": ROOT + "/customers"},
        ],
    }
    dossier = build_dossier(
        client_name="Acme Net",
        identity=ident,
        research=CompanyResearch(
            company_name="Acme Net", website=ROOT, scrape=scrape),
        probes=probes,
        product_ingest=ingest,
    )
    texts = {o.text for o in dossier.offerings}
    assert "Management Team" not in texts
    assert "Senior Management" not in texts
    assert "Platforms page" not in texts
    assert "Detection and Response Overview" not in texts
    assert "JCT600" not in texts
    assert "RACSA" not in texts
    assert "Intuit" not in texts
    assert {"EOS", "CloudVision", "AGNI", "7050X"} <= texts
    assert any("danz" in t.casefold() for t in texts)
    customers = {c.text.casefold() for c in dossier.customers}
    assert "hedge funds" not in customers
    assert "financial services" not in customers
    assert "microsoft" in customers
    rivals = {c.text: c for c in dossier.competitors}
    assert "Darktrace Comparison" not in rivals
    assert "Cisco" in rivals or "Juniper" in rivals
    ev = {e.evidence_id: e for e in dossier.evidence}
    for name in ("Cisco", "Juniper"):
        if name not in rivals:
            continue
        url = (ev[rivals[name].evidence_ids[0]].url or "").casefold()
        excerpt = ev[rivals[name].evidence_ids[0]].excerpt or ""
        assert "unlike" in excerpt.casefold() or "versus" in excerpt.casefold()
        assert "darktrace.com" not in url
        assert "darktrace-comparison" not in url
        assert "/eos" not in url
    assert "Extreme" not in rivals
    assert "HPE" not in rivals


def test_compare_page_competitors_use_site_evidence_urls():
    ident = _identity()
    compare = ROOT + "/compare"
    scrape = ScrapeBundle(
        root_url=ROOT,
        pages=[ScrapedPage(
            url=compare,
            text=(
                "Unlike Cisco and Juniper, Acme Net ships EOS on every 7050X. "
                "The official compare page names those rivals next to CloudVision."
            ),
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


def test_press12_slogans_junk_customers_and_empty_rivals_are_balanced():
    ident = _identity()
    products = ROOT + "/products"
    customers_url = ROOT + "/customers"
    compare = ROOT + "/compare"
    scrape = ScrapeBundle(
        root_url=ROOT,
        pages=[
            ScrapedPage(
                url=products,
                text=(
                    "CloudVision Data Sheet. SolutionBrief. From network "
                    "security to secure networks. Unmatched Visibility. "
                    "Operating System. Switching &amp; Routing. "
                    "Acme Net sells EOS, CloudVision, AGNI, DANZ Monitoring "
                    "Fabric, and the 7050X switch family on product pages."
                ),
            ),
            ScrapedPage(
                url=customers_url,
                text=(
                    "Proof points PDF. Troubleshoot workloads. Ease of "
                    "deployment. 6. Barclays, Citigroup, Morgan Stanley, "
                    "Yahoo! and Hardis run CloudVision in production."
                ),
            ),
            ScrapedPage(
                url=compare,
                text=(
                    "Unlike Cisco and Juniper, Acme Net ships EOS as the "
                    "alternative on this official head-to-head compare page."
                ),
            ),
        ],
        sources=[products, customers_url, compare],
    )
    probes = [
        ResearchProbe(
            name="boundaries",
            query="exclusions",
            findings=(
                "Do not confuse Acme Net with Acme Aviation Services. "
                "Aviation stays out of the search lane."
            ),
            citations=["https://en.wikipedia.org/wiki/Acme_Aviation"],
        ),
        ResearchProbe(
            name="notes",
            query="collision-codes",
            findings=(
                "Aviation namesake listing 336413 is not this company's "
                "search lane. Keep the code off core NAICS."
            ),
            citations=["https://www.acme-net.example/about"],
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
    assert "CloudVision Data Sheet" not in texts
    assert "SolutionBrief" not in texts
    assert "From network security to secure networks" not in texts
    assert "Unmatched Visibility" not in texts
    assert "Operating System" not in texts
    assert "Switching &amp; Routing" not in texts
    assert {"EOS", "CloudVision", "AGNI", "7050X"} <= texts
    assert any("danz" in t.casefold() for t in texts)
    customers = {c.text.casefold() for c in dossier.customers}
    assert "proof points" not in customers
    assert "pdf" not in customers
    assert "pdf. barclays" not in customers
    assert "troubleshoot workloads" not in customers
    assert "ease of deployment" not in customers
    assert "6" not in customers
    assert {"barclays", "citigroup", "morgan stanley", "hardis"} <= customers
    assert "yahoo" not in customers
    rivals = {c.text: c for c in dossier.competitors}
    assert "Darktrace Comparison" not in rivals
    assert {"Cisco", "Juniper"} <= set(rivals)
    ev = {e.evidence_id: e for e in dossier.evidence}
    for name in ("Cisco", "Juniper"):
        excerpt = ev[rivals[name].evidence_ids[0]].excerpt or ""
        url = ev[rivals[name].evidence_ids[0]].url or ""
        assert "unlike" in excerpt.casefold() or "alternative" in excerpt.casefold()
        assert "darktrace.com" not in url.casefold()
    kept = {k.term.casefold() for k in dossier.kept_out}
    assert any("aviation" in k for k in kept)
    parked = {n.code for n in dossier.kept_out_naics}
    assert "336413" in aviation_codes_in_text(
        "Aviation namesake listing 336413 is not this company's search lane.")
    assert "336413" in parked
    assert "336413" not in {n.code for n in dossier.naics}


def test_press12_promotes_orgs_and_rivals_already_in_evidence():
    """Surface extract may miss; evidence ledger still promotes real names."""
    ident = _identity()
    scrape = ScrapeBundle(
        root_url=ROOT,
        pages=[ScrapedPage(
            url=ROOT + "/products",
            text=(
                "Acme Net sells EOS and CloudVision AGNI on the official "
                "product page. No customer hub is linked from this shell."
            ),
        )],
        sources=[ROOT + "/products"],
    )
    probes = [
        ResearchProbe(
            name="essay",
            query="site-dump",
            findings=(
                "Proof points PDF troubleshoot workloads ease of deployment 6. "
                "Barclays, Citigroup, Morgan Stanley, Yahoo! and Hardis run "
                "CloudVision in production. Unlike Cisco and Juniper, Acme "
                "ships EOS on the official compare writeup."
            ),
            citations=[ROOT + "/customers"],
        ),
    ]
    dossier = build_dossier(
        client_name="Acme Net",
        identity=ident,
        research=CompanyResearch(
            company_name="Acme Net", website=ROOT, scrape=scrape),
        probes=probes,
    )
    customers = {c.text.casefold() for c in dossier.customers}
    assert "proof points" not in customers
    assert "pdf" not in customers
    assert "barclays" in customers
    assert "citigroup" in customers
    rivals = {c.text: c for c in dossier.competitors}
    assert rivals, "compare-claim rivals in evidence must not leave competitors empty"
    assert "Cisco" in rivals or "Juniper" in rivals
    ev = {e.evidence_id: e for e in dossier.evidence}
    for name, claim in rivals.items():
        if name not in {"Cisco", "Juniper"}:
            continue
        excerpt = ev[claim.evidence_ids[0]].excerpt or ""
        assert excerpt_supports_rival(name, excerpt)


def test_press13_chrome_here_and_aviation_park_are_stable():
    """Invariants that fail both the empty-list and 342-chrome cases."""
    ident = _identity()
    customers_url = ROOT + "/customers"
    compare = ROOT + "/compare"
    darktrace = ROOT + "/en/company/darktrace-comparison"
    scrape = ScrapeBundle(
        root_url=ROOT,
        pages=[
            ScrapedPage(
                url=ROOT,
                text=(
                    "Login Wi-Fi Cloud. Toggle Navigation. Series Spine. "
                    "Meta Facebook Yahoo share widgets sit in the footer. "
                    "Secure Networks vs. Network Security. "
                    "1. Operating System. 2. Platform. "
                    "Acme Net sells EOS, CloudVision, AGNI, DANZ Monitoring "
                    "Fabric, and the 7050X switch family."
                ),
            ),
            ScrapedPage(
                url=customers_url,
                text=(
                    "Customers include Barclays, Citigroup, Morgan Stanley, "
                    "Hardis Group, and Microsoft. Trusted by US Army for "
                    "campus switching. Case study: Barclays deployed "
                    "CloudVision in production."
                ),
            ),
            ScrapedPage(
                url=compare,
                text=(
                    "Unlike Cisco and Juniper, Acme Net ships EOS on this "
                    "official compare page. Alternatives to Cisco include "
                    "Acme CloudVision."
                ),
            ),
            ScrapedPage(
                url=darktrace,
                text=(
                    "Darktrace Comparison. Here is what the NDR writeup "
                    "says next to CloudVision. Unlike Darktrace, Acme "
                    "positions AGNI as the identity control point."
                ),
            ),
        ],
        sources=[ROOT, customers_url, compare, darktrace],
    )
    probes = [
        ResearchProbe(
            name="boundaries",
            query="exclusions",
            findings=(
                "Do not confuse Acme Net with Acme Aviation Services. "
                "Aviation stays out of the search lane."
            ),
            citations=["https://en.wikipedia.org/wiki/Acme_Aviation"],
        ),
        ResearchProbe(
            name="essay",
            query="site-dump",
            findings=(
                "Here is what the crawl found. Login Wi-Fi. Toggle "
                "Navigation. Series Spine. Aviation namesake listing "
                "336413 is not this company's search lane."
            ),
            citations=[ROOT + "/about"],
        ),
    ]
    dossier = build_dossier(
        client_name="Acme Net",
        identity=ident,
        research=CompanyResearch(
            company_name="Acme Net", website=ROOT, scrape=scrape),
        probes=probes,
    )
    customers = [c.text for c in dossier.customers]
    customer_keys = {c.casefold() for c in customers}
    assert 1 <= len(customers) <= MAX_CUSTOMERS
    assert len(customers) <= 24
    chrome = (
        "login", "toggle", "navigation", "series spine", "wi-fi",
        "meta", "facebook", "yahoo",
    )
    assert not any(tok in " ".join(customer_keys) for tok in chrome)
    assert {
        "barclays", "citigroup", "morgan stanley", "microsoft",
    } <= customer_keys
    assert any("hardis" in k for k in customer_keys)
    assert any("army" in k for k in customer_keys)
    texts = {o.text for o in dossier.offerings}
    assert "Secure Networks vs. Network Security" not in texts
    assert "1. Operating System" not in texts
    assert {"EOS", "CloudVision", "AGNI", "7050X"} <= texts
    rivals = {c.text: c for c in dossier.competitors}
    assert "Here" not in rivals
    assert "This" not in rivals
    assert "Darktrace" in rivals
    assert "Cisco" in rivals or "Juniper" in rivals
    ev = {e.evidence_id: e for e in dossier.evidence}
    if "Cisco" in rivals:
        url = (ev[rivals["Cisco"].evidence_ids[0]].url or "").casefold()
        assert "darktrace-comparison" not in url
        assert "darktrace.com" not in url
    kept = {k.term.casefold() for k in dossier.kept_out}
    assert any("aviation" in k for k in kept)
    parked = {n.code for n in dossier.kept_out_naics}
    assert "336413" in parked
    assert "336413" not in {n.code for n in dossier.naics}


def test_aviation_kept_out_parks_canonical_codes_without_digits_on_the_site():
    """Live arista.com never cites 336413; Aviation kept_out still parks it."""
    ident = _identity()
    scrape = ScrapeBundle(
        root_url=ROOT,
        pages=[ScrapedPage(
            url=ROOT + "/products",
            text=(
                "Acme Net sells EOS and CloudVision. No NAICS digits "
                "appear on this official product page."
            ),
        )],
        sources=[ROOT + "/products"],
    )
    probes = [
        ResearchProbe(
            name="boundaries",
            query="exclusions",
            findings=(
                "Do not confuse Acme Net with Acme Aviation. Aviation "
                "is a namesake collision, not a product line."
            ),
            citations=["https://en.wikipedia.org/wiki/Acme_Aviation"],
        ),
    ]
    dossier = build_dossier(
        client_name="Acme Net",
        identity=ident,
        research=CompanyResearch(
            company_name="Acme Net", website=ROOT, scrape=scrape),
        probes=probes,
    )
    assert any("aviation" in k.term.casefold() for k in dossier.kept_out)
    site_blob = " ".join(p.text for p in scrape.pages)
    probe_blob = " ".join(p.findings for p in probes)
    assert "336413" not in site_blob
    assert "336413" not in probe_blob
    parked = {n.code for n in dossier.kept_out_naics}
    assert {"336413", "488190"} <= parked
    strategy = strategy_from_dossier(dossier)
    assert {"336413", "488190"} <= {e.code for e in strategy.kept_out_naics}


def test_press14_compare_rivals_and_banks_not_fragments():
    """Fail empty rivals, VMware-from-news-only, and banks omitted for labels."""
    ident = _identity()
    news = ROOT + "/en/company/news"
    darktrace = ROOT + "/en/company/darktrace-comparison"
    customers_url = ROOT + "/en/company/customers"
    scrape = ScrapeBundle(
        root_url=ROOT,
        pages=[
            ScrapedPage(
                url=news,
                text=(
                    "Broadcom closed the VMware acquisition. The press note "
                    "mentions VMware products in the same sentence as Acme "
                    "campus switching, with no head-to-head compare claim."
                ),
            ),
            ScrapedPage(
                url=darktrace,
                text=(
                    "Darktrace Comparison. Unlike Cisco and Juniper, Acme "
                    "ships EOS. Unlike Darktrace, Acme positions AGNI as "
                    "the identity control point on this official writeup."
                ),
            ),
            ScrapedPage(
                url=customers_url,
                text=(
                    "Named Customers. Revenue Share. MARKET DATA FEED "
                    "PROVIDER. Costa Rica. Cloud Titans. Hardis Grou… "
                    "Customers include Barclays, Citigroup, Morgan Stanley, "
                    "Activ Financial, Hardis Group, and Microsoft."
                ),
            ),
            ScrapedPage(
                url=ROOT + "/products",
                text=(
                    "Acme Net sells EOS, CloudVision, AGNI, DANZ Monitoring "
                    "Fabric, and the 7050X switch family."
                ),
            ),
        ],
        sources=[news, darktrace, customers_url],
    )
    probes = [
        ResearchProbe(
            name="boundaries",
            query="exclusions",
            findings=(
                "Do not confuse Acme Net with Acme Aviation. Aviation "
                "stays out of the search lane."
            ),
            citations=["https://en.wikipedia.org/wiki/Acme_Aviation"],
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
    assert rivals, "compare pages must not leave competitors empty"
    assert {"Cisco", "Juniper", "Darktrace"} <= set(rivals)
    ev = {e.evidence_id: e for e in dossier.evidence}
    for name in ("Cisco", "Juniper", "Darktrace"):
        excerpt = ev[rivals[name].evidence_ids[0]].excerpt or ""
        url = (ev[rivals[name].evidence_ids[0]].url or "").casefold()
        assert excerpt_supports_rival(name, excerpt)
        assert "/news" not in url
        assert "darktrace.com" not in url
    if "VMware" in rivals or "Vmware" in rivals:
        vm = rivals.get("VMware") or rivals["Vmware"]
        assert "/news" not in (ev[vm.evidence_ids[0]].url or "").casefold()
    customers = {c.text.casefold() for c in dossier.customers}
    assert 1 <= len(customers) <= MAX_CUSTOMERS
    assert {
        "barclays", "citigroup", "morgan stanley",
        "activ financial", "hardis group", "microsoft",
    } <= customers
    for junk in (
        "named customers", "revenue share", "market data feed provider",
        "costa rica", "cloud titans", "hardis grou",
    ):
        assert junk not in customers
    texts = {o.text for o in dossier.offerings}
    assert {"EOS", "CloudVision", "AGNI", "7050X"} <= texts
    assert any("aviation" in k.term.casefold() for k in dossier.kept_out)
    assert "336413" in {n.code for n in dossier.kept_out_naics}


def test_press15_scrape_competition_sentence_promotes_cisco():
    """E105-like site text must promote Cisco without a competitor probe."""
    ident = _identity()
    nexus = ROOT + "/en/solutions/v-eos-router"
    news = ROOT + "/en/company/news"
    darktrace = ROOT + "/en/company/darktrace-comparison"
    customers_url = ROOT + "/en/company/customers"
    scrape = ScrapeBundle(
        root_url=ROOT,
        pages=[
            ScrapedPage(
                url=nexus,
                text=(
                    "vEOS Router overview. Competition for the Cisco Nexus "
                    "1000V in the hypervisor switching lane. Acme ships EOS "
                    "and CloudVision on the same page."
                ),
            ),
            ScrapedPage(
                url=darktrace,
                text=(
                    "Darktrace Comparison. Unlike Juniper and Darktrace, "
                    "Acme positions AGNI as the identity control point."
                ),
            ),
            ScrapedPage(
                url=news,
                text=(
                    "Broadcom closed the VMware acquisition. The press note "
                    "names VMware next to Acme campus switching. No compare "
                    "claim appears on this news page."
                ),
            ),
            ScrapedPage(
                url=customers_url,
                text=(
                    "Fixed Mobile Telecoms. Fixed Mobile. Mobile Telecoms. "
                    "French IT. Hardis. Hardis Group. Activ Financial. "
                    "Customers include Barclays, Citigroup, Morgan Stanley, "
                    "Activ Financial, Hardis Group, and Microsoft."
                ),
            ),
            ScrapedPage(
                url=ROOT + "/products",
                text=(
                    "Acme Net sells EOS, CloudVision, AGNI, DANZ Monitoring "
                    "Fabric, and the 7050X switch family."
                ),
            ),
        ],
        sources=[nexus, darktrace, news, customers_url],
    )
    probes = [
        ResearchProbe(
            name="competitors",
            query="rivals",
            findings="",
            citations=[],
        ),
        ResearchProbe(
            name="boundaries",
            query="exclusions",
            findings=(
                "Do not confuse Acme Net with Acme Aviation. Aviation "
                "stays out of the search lane."
            ),
            citations=["https://en.wikipedia.org/wiki/Acme_Aviation"],
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
    assert "Cisco" in rivals
    ev = {e.evidence_id: e for e in dossier.evidence}
    cisco_url = (ev[rivals["Cisco"].evidence_ids[0]].url or "").casefold()
    cisco_ex = ev[rivals["Cisco"].evidence_ids[0]].excerpt or ""
    assert "nexus" in cisco_url or "v-eos" in cisco_url or "/solutions" in cisco_url
    assert "/news" not in cisco_url
    assert "competition" in cisco_ex.casefold() and "cisco" in cisco_ex.casefold()
    assert "Juniper" in rivals or "Darktrace" in rivals
    assert "Here" not in rivals
    vm_url = ""
    if "VMware" in rivals or "Vmware" in rivals:
        vm = rivals.get("VMware") or rivals["Vmware"]
        vm_url = (ev[vm.evidence_ids[0]].url or "").casefold()
    assert "/news" not in vm_url
    customers = {c.text.casefold() for c in dossier.customers}
    assert 1 <= len(customers) <= MAX_CUSTOMERS
    assert {
        "barclays", "citigroup", "morgan stanley",
        "activ financial", "hardis group", "microsoft",
    } <= customers
    assert "hardis" not in customers
    for junk in (
        "fixed mobile telecoms", "fixed mobile", "mobile telecoms",
        "french it", "named customers",
    ):
        assert junk not in customers
    texts = {o.text for o in dossier.offerings}
    assert {"EOS", "CloudVision", "AGNI", "7050X"} <= texts
    assert "336413" in {n.code for n in dossier.kept_out_naics}


def test_press16_collateral_does_not_undo_cisco_darktrace():
    """Cisco + Darktrace stay; news VMware, Nexus SKU, DoD offerings do not."""
    ident = _identity()
    cisco_url = ROOT + "/en/company/competitor-comparisons"
    darktrace = ROOT + "/en/ndr-darktrace-comparison"
    nexus = ROOT + "/en/solutions/v-eos-router"
    news = ROOT + "/en/company/news"
    gov = ROOT + "/en/company/government"
    customers_url = ROOT + "/en/company/customers"
    scrape = ScrapeBundle(
        root_url=ROOT,
        pages=[
            ScrapedPage(
                url=cisco_url,
                text=(
                    "The data-center switching market was historically "
                    "dominated by Cisco. Unlike Cisco, Acme ships EOS on "
                    "every 7050X and positions CloudVision as the compare "
                    "alternative on this official writeup."
                ),
            ),
            ScrapedPage(
                url=darktrace,
                text=(
                    "NDR Darktrace comparison. Unlike Darktrace, Acme "
                    "positions AGNI as the identity control point on this "
                    "official page."
                ),
            ),
            ScrapedPage(
                url=nexus,
                text=(
                    "vEOS Router overview. Competition for the Cisco Nexus "
                    "1000V in the hypervisor switching lane. Acme ships EOS "
                    "on the same page. Do not promote the SKU name as a rival."
                ),
            ),
            ScrapedPage(
                url=news,
                text=(
                    "Broadcom closed the VMware acquisition. VMware Cloud "
                    "Foundation 9.1 sits next to an Acme campus note. "
                    "Pioneers vs Protectors is a blog teaser on the same "
                    "press room. This is not a VMware compare claim."
                ),
            ),
            ScrapedPage(
                url=gov,
                text=(
                    "Federal certifications include the DoD, DoDIN, and "
                    "DoDIN APL approved products list. Acme sells EOS and "
                    "CloudVision, not those program names."
                ),
            ),
            ScrapedPage(
                url=customers_url,
                text=(
                    "Fixed Mobile Telecoms. French IT. Named Customers. "
                    "Customers include BARCLAYS, CITIGROUP, MORGAN STANLEY, "
                    "Activ Financial, Hardis Group, and Microsoft."
                ),
            ),
            ScrapedPage(
                url=ROOT + "/products",
                text=(
                    "Acme Net sells EOS, CloudVision, AGNI, DANZ Monitoring "
                    "Fabric, and the 7050X switch family."
                ),
            ),
        ],
        sources=[cisco_url, darktrace, nexus, news, gov, customers_url],
    )
    probes = [
        ResearchProbe(
            name="competitors",
            query="rivals",
            findings="",
            citations=[],
        ),
        ResearchProbe(
            name="boundaries",
            query="exclusions",
            findings=(
                "Do not confuse Acme Net with Acme Aviation. Aviation "
                "stays out of the search lane."
            ),
            citations=["https://en.wikipedia.org/wiki/Acme_Aviation"],
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
    assert "Cisco" in rivals
    assert "Darktrace" in rivals
    ev = {e.evidence_id: e for e in dossier.evidence}
    cisco_url_hit = (ev[rivals["Cisco"].evidence_ids[0]].url or "").casefold()
    cisco_ex = ev[rivals["Cisco"].evidence_ids[0]].excerpt or ""
    assert "/news" not in cisco_url_hit
    assert (
        "competitor-comparisons" in cisco_url_hit
        or "v-eos" in cisco_url_hit
        or "/solutions" in cisco_url_hit
    )
    assert "cisco" in cisco_ex.casefold()
    dark_url = (ev[rivals["Darktrace"].evidence_ids[0]].url or "").casefold()
    dark_ex = ev[rivals["Darktrace"].evidence_ids[0]].excerpt or ""
    assert "darktrace" in dark_url
    assert "/news" not in dark_url
    assert excerpt_supports_rival("Darktrace", dark_ex)
    assert "Cisco Nexus" not in rivals
    assert "Cisco Nexus 1000V" not in rivals
    assert "Nexus 1000V" not in rivals
    assert "Here" not in rivals
    assert "VMware" not in rivals and "Vmware" not in rivals
    texts = {o.text for o in dossier.offerings}
    assert {"EOS", "CloudVision", "AGNI", "DANZ Monitoring Fabric",
            "7050X"} <= texts
    low_off = {t.casefold() for t in texts}
    assert "dod" not in low_off
    assert "dodin" not in low_off
    assert "dodin apl" not in low_off
    assert "apl" not in low_off
    customers = {c.text.casefold() for c in dossier.customers}
    assert 1 <= len(customers) <= MAX_CUSTOMERS
    assert {
        "barclays", "citigroup", "morgan stanley",
        "activ financial", "hardis group", "microsoft",
    } <= customers
    for junk in (
        "fixed mobile telecoms", "french it", "named customers",
        "hardis grou",
    ):
        assert junk not in customers
    assert any("aviation" in k.term.casefold() for k in dossier.kept_out)
    parked = {n.code for n in dossier.kept_out_naics}
    assert {"336413", "488190"} <= parked


def test_press17_banks_from_page_proof_not_chrome_or_underwriter():
    """Live P17 shape: S-1 proof far from the names; card titles truncated."""
    ident = _identity()
    cisco_url = ROOT + "/en/company/competitor-comparisons"
    darktrace = ROOT + "/en/ndr-darktrace-comparison"
    news = ROOT + "/en/company/news"
    s1 = ROOT + "/en/company/news/press-release/s1-customers"
    ipo = ROOT + "/en/company/news/press-release/ipo-pricing"
    testimonials = ROOT + "/en/products/product-testimonials"
    campus = ROOT + "/en/solutions/cognitive-campus/literature"
    scrape = ScrapeBundle(
        root_url=ROOT,
        pages=[
            ScrapedPage(
                url=cisco_url,
                text=(
                    "The switching market was historically dominated by "
                    "Cisco. Unlike Cisco, Acme ships EOS on every 7050X."
                ),
            ),
            ScrapedPage(
                url=darktrace,
                text=(
                    "NDR Darktrace comparison. Unlike Darktrace, Acme "
                    "positions AGNI as the identity control point."
                ),
            ),
            ScrapedPage(
                url=news,
                text=(
                    "Broadcom closed the VMware acquisition. VMware Cloud "
                    "Foundation sits next to an Acme campus note."
                ),
            ),
            ScrapedPage(
                url=s1,
                text=(
                    "Arista's customers include six of the largest cloud "
                    "services providers based on annual revenue, including "
                    "Facebook, Microsoft and Yahoo. Others include Barclays, "
                    "Citigroup, Morgan Stanley, Comcast, Equinix and ESPN."
                ),
            ),
            ScrapedPage(
                url=ipo,
                text=(
                    "Morgan Stanley, Citigroup, BofA Merrill Lynch, Barclays "
                    "and Credit Suisse are acting as joint book-running "
                    "managers for the offering."
                ),
            ),
            ScrapedPage(
                url=testimonials,
                text=(
                    "The first sub-500ns switching platform keeps our "
                    "competitive advantage. Steve McNeany, CEO, Activ "
                    "Financial."
                ),
            ),
            ScrapedPage(
                url=campus,
                text=(
                    "Wireless FAQ. Real-World Deployments. Walsh Universi "
                    "Noodles Compa PB/UAX Case Stu. Hardis Grou."
                ),
            ),
            ScrapedPage(
                url=ROOT + "/products",
                text=(
                    "Acme Net sells EOS, CloudVision, AGNI, DANZ Monitoring "
                    "Fabric, and the 7050X switch family."
                ),
            ),
        ],
        sources=[cisco_url, darktrace, news, s1, ipo, testimonials, campus],
    )
    probes = [
        ResearchProbe(
            name="competitors", query="rivals", findings="", citations=[]),
        ResearchProbe(
            name="boundaries",
            query="exclusions",
            findings=(
                "Do not confuse Acme Net with Acme Aviation. Aviation "
                "stays out of the search lane."
            ),
            citations=["https://en.wikipedia.org/wiki/Acme_Aviation"],
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
    assert "Cisco" in rivals and "Darktrace" in rivals
    ev = {e.evidence_id: e for e in dossier.evidence}
    assert "/news" not in (ev[rivals["Cisco"].evidence_ids[0]].url or "").casefold()
    assert "darktrace" in (ev[rivals["Darktrace"].evidence_ids[0]].url or "").casefold()
    assert "Cisco Nexus" not in rivals
    assert "VMware" not in rivals and "Vmware" not in rivals
    texts = {o.text for o in dossier.offerings}
    assert {"EOS", "CloudVision", "AGNI", "DANZ Monitoring Fabric",
            "7050X"} <= texts
    assert "dod" not in {t.casefold() for t in texts}
    customers = {c.text.casefold() for c in dossier.customers}
    assert 1 <= len(customers) <= MAX_CUSTOMERS
    assert {
        "barclays", "citigroup", "morgan stanley", "activ financial",
    } <= customers
    for junk in (
        "wireless faq", "real-world deployments", "walsh universi",
        "noodles compa", "pb/uax case stu", "uax case stu", "hardis grou",
        "fixed mobile telecoms", "french it",
    ):
        assert junk not in customers
    assert {"336413", "488190"} <= {n.code for n in dossier.kept_out_naics}
