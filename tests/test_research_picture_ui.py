"""Actual native projection/render acceptance; opening the view is read-only."""
import ast
from copy import deepcopy
import json
from pathlib import Path
from typing import Optional


from tests.test_research_picture_evidence import picture, validate
from agents.decisions.research_picture import TopOpportunity

ROOT = Path(__file__).resolve().parents[1]


def project(data):
    # Execute the actual server function without starting a server/service.
    tree = ast.parse((ROOT / 'ui/server.py').read_text())
    fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == '_research_picture')
    scope = {'Optional': Optional}
    exec(compile(ast.Module(body=[fn], type_ignores=[]), 'native_picture_projection', 'exec'), scope)
    return scope['_research_picture']('Apex', searches=data)


def test_saved_case005_ui_withholds_legacy_claims_and_retains_linked_tasks():
    fixture = json.loads((ROOT / 'tests/fixtures/research_picture_case005.json').read_text())
    data = {'results': {**fixture['results'], 'research_picture': fixture['draft']}}
    original = deepcopy(data)
    out = project(data)
    assert out['headline'] != fixture['draft']['headline']
    assert not out['top']
    assert {x['id'] for x in out['research_cards']} == {'web-af425e4fb2f913ce', 'web-b0d1a4671b98953d'}
    assert all(x['source_url'].startswith('https://sam.gov/') for x in out['research_cards'])
    assert all(x['classification'] == 'research_signal' for x in out['research_cards'])
    assert 'exact functional match' not in json.dumps(out).lower()
    assert data == original


def test_persisted_evidence_cards_and_historical_classification_survive_projection():
    data = {'web': [{'source_id': 'W', 'title': 'VA PIVOT', 'url': 'https://sam.gov/x'}],
            'contract_awards': {'recompetes': [{'source_id': 'A', 'title': 'Old award', 'completion': '2026-09-13'}]}}
    draft = picture(top_opportunities=[TopOpportunity(id='A', title='Recompete', why_now='Bid now')])
    checked = validate(draft, data)
    out = project({'results': {'research_picture': checked.model_dump()}})
    assert out['top'][0]['classification'] == 'historical_market_evidence'
    assert out['research_cards'][0]['title'] == 'VA PIVOT'
    assert out['research_cards'][0]['source_url'] == 'https://sam.gov/x'


def test_native_browser_renders_linked_verification_and_history_sections(tmp_path):
    from playwright.sync_api import sync_playwright
    from tools.export_pdf import find_chrome
    data = {'web': [{'source_id': 'W', 'title': 'VA PIVOT', 'url': 'https://sam.gov/x'}],
            'contract_awards': {'recompetes': [{'source_id': 'A', 'title': 'Old award'}]}}
    checked = validate(picture(top_opportunities=[TopOpportunity(id='A', title='Recompete', why_now='Bid now')]), data)
    pic = project({'results': {**data, 'research_picture': checked.model_dump()}})
    html = (ROOT/'ui/index.html').read_text()
    build = html[html.index('function buildPicture('):html.index('\nconst VERDICT_CHIP')]
    safe = html[html.index('const SAFE_MD_TAGS'):html.index('const STATE_CHIP')]
    helpers = """
    const esc = s => String(s ?? '').replace(/[&<>\"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','\"':'&quot;',"'":'&#39;'}[c]));
    const el = s => {const t=document.createElement('template');t.innerHTML=s;return t.content.firstElementChild;};
    """
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True, executable_path=find_chrome())
        page = browser.new_page(viewport={'width': 1100, 'height': 900})
        page.route('**/*', lambda route: route.abort())
        page.set_content('<html><body><div id="card"></div></body></html>')
        page.add_script_tag(content=helpers+safe+build)
        page.evaluate('(pic) => buildPicture(pic, document.querySelector("#card"))', pic)
        page.get_by_role('button', name='Full picture', exact=True).click()
        text = page.locator('#card').inner_text()
        assert 'research signals; verification required' in text.lower()
        assert 'historical market evidence' in text.lower()
        assert 'top opportunities' not in text.lower() and 'bid now' not in text.lower()
        assert page.get_by_role('link', name='VA PIVOT', exact=True).get_attribute('href') == 'https://sam.gov/x'
        assert page.get_by_role('link', name='VA PIVOT', exact=True).get_attribute('rel') == 'noopener noreferrer'
        page.screenshot(path=str(tmp_path/'research-picture.png'), full_page=True)
        page.get_by_role('button', name='Hide', exact=True).click()
        assert not page.locator('.picfull').is_visible()
        browser.close()
