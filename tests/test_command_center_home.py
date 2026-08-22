"""Static contract checks for the isolated Command Center redesign."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
INDEX = (ROOT / "ui" / "index.html").read_text()
SCRIPT = (ROOT / "ui" / "static" / "command-center-home.js").read_text()
STYLE = (ROOT / "ui" / "static" / "command-center-home.css").read_text()


def test_redesign_is_loaded_and_owns_only_the_home_surface():
    assert '/static/command-center-home.css' in INDEX
    assert '/static/command-center-home.js' in INDEX
    assert 'window.LILACommandCenterHome.render(main)' in INDEX
    assert 'window.LILACommandCenterHome.deactivate()' in INDEX
    assert 'body.command-center-v2-active' in STYLE


def test_background_client_poll_does_not_repaint_the_command_center():
    assert 'async function loadClients(keepSel = true, options = {})' in INDEX
    assert 'if (options.paint === false) return CLIENTS;' in INDEX
    assert 'loadClients(true, { paint: Boolean(SEL) }).catch(() => {});' in INDEX
    polling_tail = INDEX[INDEX.rfind('setInterval(() => {'):]
    assert 'loadClients();' not in polling_tail


def test_redesign_composes_existing_read_surfaces_and_uses_only_targeting_work_product_mutations():
    for endpoint in ('/api/depository', '/api/calendar', '/api/targets', '/api/ticker'):
        assert endpoint in SCRIPT
    assert 'method: "POST"' in SCRIPT
    assert '/targeting-plan' in SCRIPT
    assert '/targeting-review-approve' in SCRIPT
    assert '/api/run' not in SCRIPT
    assert '/api/review/' not in SCRIPT
    assert '/api/review/target-approve' not in SCRIPT


def test_progressive_home_layers_keep_decisions_primary_and_add_read_only_depth():
    for layer in ('Decide', 'Pursue', 'Verify'):
        assert layer in SCRIPT
    assert 'homeLayer: "decide"' in SCRIPT
    assert 'data-home-layer=' in SCRIPT
    assert 'Single next action' in SCRIPT
    assert '["decide", "Decide", "One decision and the active queue", "bolt", 1]' in SCRIPT
    assert 'Evidence Spine' in SCRIPT
    assert 'cc2-decision-board' in SCRIPT
    assert 'Prioritized account queue' in SCRIPT
    assert 'Pursuit routes and source-bound targets' in SCRIPT
    assert 'Readiness and evidence' in SCRIPT
    assert 'data-view="Target Lists"' in SCRIPT


def test_selected_client_model_binds_full_collapsible_research_network():
    assert '"/cc-model"' in SCRIPT
    assert '"/api/source-network"' in SCRIPT
    assert 'state.ccModel = payloads[2]' in SCRIPT
    assert '<details class="cc2-research-network"' in SCRIPT
    assert 'Research Network' in SCRIPT
    assert 'LILA research surface' in SCRIPT
    for group in ('PROCUREMENT', 'FORECASTS', 'NEWS & PRESS', 'SEARCH', 'VEHICLES'):
        assert group in SCRIPT
    assert 'safeUrl(source.url)' in SCRIPT
    assert 'if (!String(value || "").trim()) return "";' in SCRIPT
    assert 'represented in the current research surface' in SCRIPT
    assert 'href="#"' not in SCRIPT
    assert 'cc2-source-dot' in STYLE
    assert 'cc2-pulse-dot' not in STYLE
    assert 'cc2-network-wave' in STYLE
    assert 'cc2-network-preview' in SCRIPT
    assert '<header><span><i class="cc2-pulse-dot"' not in SCRIPT
    assert 'prefers-reduced-motion: reduce' in STYLE
    assert '@media (max-width: 1240px)' in STYLE
    assert 'queryValue("cc-sources") === "open"' in SCRIPT


def test_command_center_supports_read_only_demo_deep_links():
    assert 'queryValue("cc-client")' in SCRIPT
    assert 'queryValue("cc-layer")' in SCRIPT
    assert '["decide", "pursue", "verify"].includes(requestedLayer)' in SCRIPT


def test_every_navigation_destination_has_a_real_workspace_or_existing_route():
    for label in (
        'Command Center', 'Clients', 'Review Queue', 'Report Library',
        'Research Packs', 'Target Lists', 'Evidence',
        'Templates & Assets', 'Settings',
    ):
        assert label in SCRIPT
    assert '"Target Lists"' in SCRIPT
    assert '"/api/client/" + encodeURIComponent(row.slug) + "/targets"' in SCRIPT
    assert 'global.showTargets()' not in SCRIPT
    assert 'global.showCalendar()' in SCRIPT
    assert 'global.location.href = "/keys"' in SCRIPT


def test_capitol_is_the_main_illustration_and_is_shipped_locally():
    asset = ROOT / 'ui' / 'static' / 'command-center-assets' / 'capitol-why-now.png'
    assert asset.is_file()
    assert asset.stat().st_size > 10_000
    assert STYLE.count('capitol-why-now.png') >= 2
    assert 'mountain' not in STYLE.lower()


def test_lovable_visual_assets_are_local_and_not_runtime_dependencies():
    orbit = ROOT / 'ui' / 'static' / 'command-center-assets' / 'account-orbits.png'
    assert orbit.is_file()
    assert orbit.stat().st_size > 10_000
    assert STYLE.count('account-orbits.png') >= 2
    assert 'command-center-lovable-prototype' not in SCRIPT
    assert 'command-center-lovable-prototype' not in STYLE


def test_workflow_keeps_assessment_and_targeting_as_distinct_required_reviews():
    for label in (
        'Select Client', 'Analyst Layer', 'Assessment Review',
        'Targeting Review', 'Press',
    ):
        assert label in SCRIPT
    assert 'Required work product 1' in SCRIPT
    assert 'Required work product 2' in SCRIPT
    assert 'Assessment + Targeting' in SCRIPT
    assert 'step.name + ": " + step.note + ". " + statusLabel' in SCRIPT


def test_press_readiness_is_explicit_and_fail_closed():
    for component in (
        'Assessment readiness', 'Evidence readiness', 'Targeting readiness',
        'Asset readiness', 'Output readiness',
    ):
        assert component in SCRIPT
    assert 'canPress = assessment && evidence && targeting && assets' in SCRIPT
    assert 'releaseReady: canPress && output' in SCRIPT
    assert 'Authorized generation pending; release remains locked until QA passes' in SCRIPT
    assert 'Press is unavailable.' in SCRIPT
    assert 'this shell cannot override them' in SCRIPT
    assert 'targetingReceipt.ready' in SCRIPT
    assert 'detail.target_approved && targets.rows.length' not in SCRIPT


def test_targeting_surface_exposes_operational_and_provenance_fields():
    for label in (
        'Target type', 'Verified role', 'Associated play',
        'Why this target matters', 'Recommended commercial motion',
        "Company’s proposed role", 'Exact first ask', 'Call to action',
        'Timing / action window', 'Suggested owner', 'Sequence position',
        'Contact-information provenance', 'Relationship status',
        'Facts still requiring verification', 'Disposition',
        'Promotion criteria', 'Reject / defer reason',
    ):
        assert label in SCRIPT
    assert 'No relationship or willingness to engage is claimed' in SCRIPT
    assert 'Authoritative source missing' in SCRIPT
    assert 'Notice POC — role unknown' in SCRIPT
    assert 'Contact-channel source' in SCRIPT
    assert 'Save server-owned action plan' in SCRIPT
    assert 'play_lane_dispositions' in SCRIPT
    assert 'Evidence checked' in SCRIPT
    assert 'Exact next research action' in SCRIPT
    assert 'Research owner' in SCRIPT
    assert 'Research deadline' in SCRIPT


def test_client_context_persistence_does_not_create_parallel_gate_state():
    assert 'lilaCommandCenterClient' in SCRIPT
    assert '.setItem("lilaCommandCenterClient", slug)' in SCRIPT
    assert 'review_approved' in SCRIPT
    assert 'targeting_readiness' in SCRIPT
    assert 'localStorage.setItem("review' not in SCRIPT
    assert 'localStorage.setItem("target' not in SCRIPT


def test_active_edition_persists_per_client_and_drives_report_preview():
    assert 'lilaCommandCenterEdition:' in SCRIPT
    assert 'function selectedEditionDoc(row)' in SCRIPT
    assert 'data-edition-path' in SCRIPT
    assert 'edition && edition.path' in SCRIPT


def test_mobile_navigation_is_modal_and_traps_keyboard_focus():
    assert 'topbar.inert = Boolean(narrow && state.navOpen)' in SCRIPT
    assert 'sidebar.setAttribute("aria-modal", "true")' in SCRIPT
    assert 'function trapNavFocus(event)' in SCRIPT
    assert 'if (trapNavFocus(event)) return' in SCRIPT
    assert 'data-nav-close tabindex="-1" aria-hidden="true"' in SCRIPT


def test_company_and_agency_identity_marks_are_a_command_center_contract():
    assert 'function clientMark(row, cls)' in SCRIPT
    assert 'function agencyMark(agency, cls)' in SCRIPT
    assert 'function companyMark(company, cls)' in SCRIPT
    assert '/api/marks/client/' in SCRIPT
    assert '/api/marks/agency/' in SCRIPT
    assert '/api/marks/company/' in SCRIPT
    assert 'hydrateBrandMarks(root)' in SCRIPT
    assert 'data-brand-kind=' in SCRIPT
    assert '" seal" : " logo") + " required"' in SCRIPT
    assert 'slice(0, 2).toUpperCase()' not in SCRIPT


def test_client_click_opens_intelligence_snapshot_with_relevant_ticker():
    assert 'function openClientSnapshot()' in SCRIPT
    assert 'Best current opportunity signal' in SCRIPT
    assert 'Strongest evidence picture' in SCRIPT
    assert 'Agency concentration' in SCRIPT
    assert 'Best target route' in SCRIPT
    assert 'Client intelligence ticker' in SCRIPT
    assert 'item.slug === row.slug' in SCRIPT
    assert 'No client-specific news or deadline signal cleared the relevance filter.' in SCRIPT
    assert 'Open full client workspace' in SCRIPT
    assert 'View client snapshot' in SCRIPT
    assert 'Client snapshot' in SCRIPT
    assert 'data-client-snapshot' in SCRIPT
    assert 'selectClient(slug).then(function () { state.view = "Command Center"; paint(document.getElementById("main")); openClientSnapshot(); });' in SCRIPT
