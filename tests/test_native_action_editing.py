"""The existing Studio editor must preserve new action prose across downloads."""
from copy import deepcopy
from dataclasses import replace
import json

import pytest

from agents.golden_press.external_product_render import render_external_product
from tests.test_external_product_projection import _document, _mark


def action_document(monkeypatch):
    from agents.reports import report_assets
    monkeypatch.setattr(report_assets, 'client_logo', lambda name: _mark())
    monkeypatch.setattr(report_assets, 'gtm_logo', _mark)
    doc = _document()
    slots = []
    for slot in doc.slots:
        if slot.slot_id == 'federal-opportunities':
            records = []
            for i, original in enumerate(slot.records):
                row = deepcopy(original)
                row['lead_rows'] = [{'lead_id': f'action-{i}', 'lead_tier': 'HOLD',
                    'next_action': {'verb': 'research', 'object': f'Ask for current requirements on record {i}.',
                                    'due': None, 'owner': 'Operator', 'communication_permission': 'none'}}]
                records.append(row)
            slot = replace(slot, records=tuple(records))
        slots.append(slot)
    return replace(doc, slots=tuple(slots))


def test_action_edit_ids_survive_record_reordering(monkeypatch):
    from html.parser import HTMLParser
    class Edits(HTMLParser):
        def __init__(self): super().__init__(); self.ids=[]
        def handle_starttag(self, tag, attrs):
            value=dict(attrs).get('data-edit-id','')
            if value.startswith('action-'):self.ids.append(value)
    doc=action_document(monkeypatch)
    first=Edits(); first.feed(render_external_product(doc)[0])
    changed=replace(doc, slots=tuple(replace(s,records=tuple(reversed(s.records))) for s in doc.slots))
    second=Edits(); second.feed(render_external_product(changed)[0])
    assert first.ids and len(first.ids)==len(set(first.ids))
    assert set(first.ids)==set(second.ids)


def test_action_edit_download_reopen_second_edit_keeps_source_and_model(monkeypatch,tmp_path):
    sync_playwright=pytest.importorskip('playwright.sync_api').sync_playwright
    from tools.export_pdf import find_chrome
    doc=action_document(monkeypatch)
    before=json.dumps([list(s.records) for s in doc.slots],sort_keys=True)
    studio,client=render_external_product(doc)
    assert '<script' not in client and studio.count('<script')==1
    artifact=tmp_path/'actions.html';artifact.write_text(studio)
    with sync_playwright() as pw:
        browser=pw.chromium.launch(headless=True,executable_path=find_chrome())
        context=browser.new_context(accept_downloads=True)
        context.route('**/*', lambda route: route.continue_() if route.request.url.startswith('file:') else route.abort())
        page=context.new_page();page.goto(artifact.as_uri())
        field=page.locator('.first-question [data-edit-id]').first
        key=field.get_attribute('data-edit-id')
        links=page.locator('a.product-source').evaluate_all('(es)=>es.map(e=>e.getAttribute("href"))')
        for i,text in enumerate(['Ask the buyer who owns the technical decision.', 'Confirm the current buying route before the next step.']):
            page.locator('[data-action="toggle-edit"]').click()
            page.locator(f'[data-edit-id="{key}"]').fill(text)
            with page.expect_download() as download:
                page.locator('[data-action="download"]').click()
            saved=tmp_path/f'saved-{i}.html';download.value.save_as(saved)
            page.goto(saved.as_uri())
            assert page.locator(f'[data-edit-id="{key}"]').inner_text()==text
            assert page.locator('a.product-source').evaluate_all('(es)=>es.map(e=>e.getAttribute("href"))')==links
            assert page.locator('.first-question [data-edit-id]').count()>=1
            assert page.locator('[data-edit-id][contenteditable="true"]').count()==0
        browser.close()
    assert json.dumps([list(s.records) for s in doc.slots],sort_keys=True)==before


def test_native_research_sheets_navigate_and_fit_mobile(monkeypatch,tmp_path):
    sync_playwright=pytest.importorskip('playwright.sync_api').sync_playwright
    from tools.export_pdf import find_chrome
    from tests.test_native_action_projection import fixture, document
    from agents.leadgen.press import PressLeadGenReceipt
    from agents.leadgen.press_html import render_html
    from agents.golden_press.external_product_render import validate_external_product_html
    context,graph,companion,_=fixture(monkeypatch,tmp_path)
    doc=document(context,graph,companion)
    studio,client=render_external_product(doc)
    # This diagnostic graph is deliberately uncertified; report lint still runs.
    errors=validate_external_product_html(client,doc)['violations']
    assert all(e['rule']=='graph_not_certified' for e in errors), errors
    native=render_html(PressLeadGenReceipt.model_validate(companion['receipt']))
    with sync_playwright() as pw:
        browser=pw.chromium.launch(headless=True,executable_path=find_chrome())
        page=browser.new_page()
        page.route('**/*', lambda route: route.continue_() if route.request.url.startswith('file:') else route.abort())
        for name,html in [('native',native),('golden',studio)]:
            path=tmp_path/f'{name}.html';path.write_text(html)
            for width in [1280,390]:
                page.set_viewport_size({'width':width,'height':850});page.goto(path.as_uri())
                assert page.evaluate('document.documentElement.scrollWidth <= innerWidth + 1'), (name,width)
                selector='.sheet-index a' if name=='native' else '.product-reference a'
                link=page.locator(selector).first;href=link.get_attribute('href');link.click()
                assert page.url.endswith(href)
                assert page.locator('[id=' + json.dumps(href[1:]) + ']').is_visible()
        browser.close()
