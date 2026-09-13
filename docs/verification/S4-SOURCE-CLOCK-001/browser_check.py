"""Synthetic native report/browser proof. Never reads operating client inputs."""
from pathlib import Path
import hashlib
import json
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[3]))
from tests.test_assess_source_clock import sweep_with_depth
from tests.test_assess_ledger import _build_with_requirement_review
from agents.leadgen import run_press
from agents.leadgen.press_html import render_html
from tools.export_pdf import find_chrome
from playwright.sync_api import sync_playwright

root=Path(__file__).resolve().parent
run,_,_=_build_with_requirement_review(sweep_with_depth())
html=render_html(run_press(assess=run))
path=root/'synthetic-source-clock.html'
path.write_text(html)
expected=hashlib.sha256(path.read_bytes()).hexdigest()
with sync_playwright() as p:
    browser=p.chromium.launch(executable_path=find_chrome(),headless=True)
    page=browser.new_page(viewport={'width':1440,'height':1000})
    errors=[]
    page.on('pageerror',lambda e:errors.append(str(e)))
    page.goto(path.as_uri(),wait_until='load')
    # Use each native evidence disclosure's own summary interaction.
    for summary in page.locator('details > summary').all():
        if not summary.locator('..').get_attribute('open'):
            summary.click()
    body=page.locator('body').inner_text()
    assert 'Original timestamp: 2026-07-10T05:00:00-06:00' in body
    assert 'Collection time not established' in body
    assert page.locator('a[href="https://sam.gov/opp/N1/view"]').count()>0
    assert not errors
    page.get_by_text('Collection time not established',exact=False).first.scroll_into_view_if_needed()
    page.screenshot(path=str(root/'browser-source-clock.png'))
    page.reload(wait_until='load')
    assert hashlib.sha256(path.read_bytes()).hexdigest()==expected
    browser.close()
(root/'BROWSER.json').write_text(json.dumps({'proof':'synthetic native Press HTML loaded from saved local bytes',
    'html':path.name,'html_sha256':expected,'screenshot':'browser-source-clock.png',
    'checks':['native evidence disclosures clicked','known original offset text visible',
              'unknown collection label visible','official source href retained','reload bytes unchanged','no page errors'],
    'live_data_proof':False},indent=2)+'\n')
print('browser proof passed')
