"""Pure source-bound action-sheet presentation. No selection or lead authority."""
import json
from html import escape

from .target_html import render_research, source_text


ACTION_SHEET_CSS = """
.report .hero{padding:30px 42px}.report .hero h1{font-size:42px;line-height:1.08;letter-spacing:-.025em;margin:12px 0}.report .hero .lead{font-size:19px}
.sheet-index{display:grid;grid-template-columns:repeat(auto-fit,minmax(230px,1fr));gap:10px;margin:0 0 25px}
.sheet-index a{padding:10px;border:1px solid var(--line,#d8d8d8);font-size:13px;text-decoration:none;overflow-wrap:anywhere}
.assessment-sheet{margin:0 0 26px;border:1px solid var(--line,#d8d8d8);background:var(--paper,#fff);min-width:0;overflow-wrap:anywhere}
.assessment-sheet>header{padding:20px 24px;border-top:3px solid var(--accent,var(--red,#b50000));border-bottom:1px solid var(--line,#d8d8d8)}
.assessment-sheet>header h3{font-family:Georgia,serif;font-size:27px;line-height:1.2;margin:7px 0}
.assessment-sheet>header p{margin:4px 0;font-size:13px}
.assessment-sheet-body{padding:0 24px 24px;min-width:0}
.action-grid{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1fr);gap:22px;margin:18px 0}
.action-grid>section{min-width:0;overflow-wrap:anywhere}
.action-grid h4,.first-question h4{font-family:Arial,Helvetica,sans-serif;font-size:11px;text-transform:uppercase;letter-spacing:.06em;margin:16px 0 7px;color:var(--accent,var(--red-dark,#b50000))}
.action-grid p{font-size:14px;line-height:1.5}
.first-question{margin:18px 0;padding:3px 18px 14px;background:var(--paper-deep,#f5f5f5);border-left:3px solid var(--accent,var(--red,#b50000))}
.first-question p{font-size:18px;font-weight:600;line-height:1.45;margin:8px 0}
.research-action{margin:18px 0;padding:14px;border-left:3px solid var(--accent,var(--red,#b50000));overflow-wrap:anywhere}
.research-action>h4{margin-bottom:8px}
.product-targets{margin-top:16px}.product-target{padding:12px;margin:10px 0;border:1px solid var(--line,#d8d8d8)}
.product-fields{display:grid;grid-template-columns:minmax(100px,.3fr) minmax(0,1fr);gap:6px 14px}
.product-fields dt{font-size:11px;color:var(--muted,#5f5f5f)}.product-fields dd{margin:0;overflow-wrap:anywhere}
.source-timing{margin:14px 0;padding:12px;background:var(--paper-deep,#f5f5f5)}
.source-timing p{margin:5px 0}.source-requirement{white-space:pre-line}
.assessment-sheet,.product-record{scroll-margin-top:90px}
.forecast-pocs{margin:20px 0}.table-scroll{overflow:auto}
.forecast-pocs .data-table{width:100%;table-layout:fixed}
.forecast-pocs th,.forecast-pocs td{overflow-wrap:anywhere;vertical-align:top;text-align:left;padding:8px;font-size:12px}
.product-record .mono{font-family:ui-monospace,monospace;font-size:12px;overflow-wrap:anywhere}
.source-fields{display:grid;grid-template-columns:minmax(120px,.35fr) minmax(0,1fr);gap:0;margin:12px 0;font-size:12px}
.source-fields dt,.source-fields dd{margin:0;padding:9px;border-bottom:1px solid var(--line,#d8d8d8);overflow-wrap:anywhere;min-width:0}
.source-fields small{display:block;color:var(--muted,#5f5f5f)}
@media(max-width:760px){.action-grid{grid-template-columns:1fr;gap:0}.assessment-sheet>header,.assessment-sheet-body{padding-left:16px;padding-right:16px}.assessment-sheet .data-table{min-width:0;table-layout:auto}.assessment-sheet .data-table th,.assessment-sheet .data-table td{padding:8px 6px}.product-fields{grid-template-columns:1fr}.product-fields dd{margin-bottom:8px}}
@media print{.assessment-sheet{break-inside:auto;page-break-inside:auto}.assessment-sheet>header,.first-question{break-inside:avoid;page-break-inside:avoid}.action-grid{display:block}.assessment-sheet-body>details{break-inside:auto}}
"""


def bound_parent_research(parent):
    """A detached parent review flag cannot select source research as priority."""
    if parent.research_subject is None:
        return parent.research
    from .research_html import reviewed_context
    overlay = reviewed_context(parent.research_subject)
    if overlay is None:
        return None
    if parent.research != overlay.research or parent.targets != overlay.targets:
        raise ValueError('parent research differs from its source-bound reviewed overlay')
    return overlay.research


def research_subject_brief(subject, *, research=None, as_of=None, edit_key=None):
    """Expose source description, monetary basis and exact labelled dates.

    Raw source/context downloads still live below this summary. No date is
    treated as a currently available action or a company revenue estimate.
    """
    from agents.assess.contracts import ResearchSubject
    from agents.reports.value_evidence import value_evidence, render_values
    from .research_html import source_link
    subject = ResearchSubject.model_validate(subject.model_dump(mode='json'))
    row = json.loads(subject.source_payload_json)
    link = source_link(subject, 'Original buyer record')
    if subject.source_kind == 'forecast':
        from tools.api.forecasts.posture import withdrawal_evidence
        dates = [('Planned solicitation release', row.get('anticipated_solicitation')),
                 ('Planned solicitation close', row.get('anticipated_solicitation_close')),
                 ('Planned award', row.get('anticipated_award'))]
        note = 'Forecast planning dates. Confirm current status before treating a date as an available buying window.'
        withdrawal = withdrawal_evidence(row)
        if withdrawal:
            note = 'Source withdrawal: ' + withdrawal['quote'] + '. Historical planning context only; no current response window is established.'
    elif subject.source_kind == 'program':
        dates = [('Source data as of', row.get('data_as_of'))]
        note = 'Published program evidence, not a solicitation window. A request is not an appropriation; a ceiling is not available funding. Confirm the next decision.'
    else:
        period = row.get('period_of_performance') or {}
        dates = [('Recorded performance start', period.get('start_date') or row.get('start_date')),
                 ('Recorded performance end', period.get('end_date') or row.get('end_date')),
                 ('Potential performance end', period.get('potential_end_date'))]
        note = 'Recorded contract dates. A performance end or option period does not establish a new purchase.'
    timing = ''.join('<p><b>' + label + ':</b> ' + escape(str(value)) + '</p>'
                     for label, value in dates if value)
    timing = timing or '<p>Confirm the next buying date with the published source.</p>'
    if subject.source_kind == 'forecast':
        apfs = row.get('source_fields') or {} if subject.source_system == 'dhs_apfs' else {}
        companies = [('Company named in forecast', apfs.get('contractor') or row.get('incumbent_stated')),
                     ('Contract named in forecast', apfs.get('contract_number') or row.get('predecessor_contract_id')),
                     ('Published vehicle', apfs.get('contract_vehicle'))]
    else:
        recipient = row.get('recipient') or {}
        parent_award = row.get('parent_award')
        companies = [('Award recipient', recipient.get('recipient_name') if isinstance(recipient, dict) else recipient),
                     ('Parent contract', parent_award.get('piid') if isinstance(parent_award, dict) else None)]
    company_fields = ''.join('<dt>' + label + '</dt><dd>' + source_text(value, subject.source_url) + '</dd>'
                             for label, value in companies if isinstance(value, str) and value.strip())
    company_block = ('<h4>Companies and contract route in the source</h4><dl class="product-fields">'
                     + company_fields + '</dl><p>' + link + '</p>'
                     '<p>A published company or vehicle does not establish a current partner commitment or an eligible selling route.</p>'
                     if company_fields else
                     '<h4>Company or supplier to identify</h4><p>Confirm the named contractor and permitted supplier route in the current acquisition documents.</p>')
    values = render_values(value_evidence(row, source_url=str(subject.source_url),
                                          source_id=subject.source_record_id), source_link=link)
    source_excerpt = subject.evidence[0].excerpt.replace('\\r\\n', '\n')
    body = ('<div class="action-grid"><section><h4>What the buyer published</h4>'
            '<p class="source-requirement" data-thirdparty="1">' + source_text(source_excerpt, subject.source_url)
            + '</p><p>' + link + '</p></section><section><h4>Published timing and amount</h4>'
            '<div class="source-timing">' + timing + '</div><p>' + note + '</p>' + values
            + '<p>Source amounts describe the published record, not expected sales for the client.</p>'
            '</section></div>' + company_block)
    if research:
        return body + render_research(research, edit_key=edit_key, source_subject=subject)
    return (body + '<div class="action-grid"><section><h4>Where the company could fit</h4>'
            '<p>Compare the buyer requirement with the approved company capabilities and confirm the specific product scope.</p>'
            '</section><section><h4>Route to investigate / proposed</h4><p>'
            + source_text(subject.route_hypothesis, subject.source_url) + '</p></section></div>'
            '<div class="first-question"><h4>First question</h4><p>' + source_text(subject.next_ask, subject.source_url)
            + '</p></div><h4>What must be confirmed</h4><ul>'
            + ''.join('<li>' + source_text(q, subject.source_url) + '</li>' for q in subject.open_questions)
            + '</ul><p>Research only. Confirm the requirement, route and permitted next step before outreach or bidding.</p>')
