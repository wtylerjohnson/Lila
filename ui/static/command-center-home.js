/* Federal Sales OS Command Center.
   This surface composes server-owned read models and hands every protected
   decision back to the established client workspace. */
(function (global) {
  "use strict";

  var state = {
    rows: [], calendar: [], targets: [], ticker: [],
    view: "Command Center", selected: null, detail: null, targetData: null, ccModel: null, sourceNetwork: null,
    homeLayer: "decide",
    reviewView: "hub", analystView: "evidence", navOpen: false, loading: true,
    editionPaths: {},
  };
  var HOME_LAYER_KEY = "lilaCommandCenterHomeLayer";
  var lastFocus = null;
  var NAV = [
    ["Operate", [["Command Center", "home"], ["Clients", "group"], ["Review Queue", "fact_check"], ["Report Library", "library_books"]]],
    ["Build", [["Research Packs", "auto_awesome"], ["Target Lists", "target"], ["Evidence", "verified_user"]]],
    ["Utility", [["Templates & Assets", "folder_open"], ["Settings", "settings"]]],
  ];

  function escapeHtml(value) {
    return String(value == null ? "" : value).replace(/\uFFFD/g, "—")
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
  }

  function safeUrl(value) {
    if (!String(value || "").trim()) return "";
    try {
      var url = new URL(String(value || ""), global.location.origin);
      return /^(https?:)$/.test(url.protocol) ? url.href : "";
    } catch (_error) { return ""; }
  }

  function queryValue(name) {
    try { return new URL(global.location.href).searchParams.get(name) || ""; }
    catch (_error) { return ""; }
  }

  function icon(name, cls) {
    return '<span class="material-symbols-rounded' + (cls ? " " + cls : "") + '" aria-hidden="true">' + escapeHtml(name) + '</span>';
  }

  function markInitials(label) {
    var words = String(label || "?").trim().split(/\s+/).filter(Boolean);
    return (words.length > 1 ? words[0][0] + words[words.length - 1][0] : (words[0] || "?").slice(0, 2)).toUpperCase();
  }

  function brandMark(kind, value, label, cls) {
    var endpoint = kind === "client" ? "/api/marks/client/" : kind === "agency" ? "/api/marks/agency/" : "/api/marks/company/";
    var name = label || value || "Brand";
    return '<span class="cc2-brand-mark ' + escapeHtml(cls || "") + '" data-brand-mark data-brand-kind="' + escapeHtml(kind) + '" title="' + escapeHtml(name) + '">' +
      '<img src="' + endpoint + encodeURIComponent(value || "") + '" alt="' + escapeHtml(name + (kind === "agency" ? " seal" : " logo")) + '">' +
      '<span class="cc2-mark-fallback" aria-label="' + escapeHtml(name + (kind === "agency" ? " seal" : " logo") + " required") + '">' + escapeHtml(markInitials(name)) + '</span></span>';
  }

  function clientMark(row, cls) {
    row = row || {};
    return brandMark("client", row.slug || "", row.client_name || row.slug || "Client", cls || "cc2-client-mark");
  }

  function agencyMark(agency, cls) {
    return brandMark("agency", agency || "", agency || "Agency", cls || "cc2-agency-mark");
  }

  function companyMark(company, cls) {
    return brandMark("company", company || "", company || "Company", cls || "cc2-company-mark");
  }

  function hydrateBrandMarks(root) {
    [].slice.call(root.querySelectorAll("[data-brand-mark]")).forEach(function (mark) {
      var image = mark.querySelector("img");
      if (!image) return;
      function loaded() { mark.classList.add("loaded"); mark.classList.remove("missing"); }
      function missing() { mark.classList.remove("loaded"); mark.classList.add("missing"); image.hidden = true; }
      image.addEventListener("load", loaded, { once: true });
      image.addEventListener("error", missing, { once: true });
      if (image.complete) { if (image.naturalWidth) loaded(); else missing(); }
    });
  }

  function json(path) {
    return fetch(path, { headers: { Accept: "application/json" } }).then(function (response) {
      if (!response.ok) throw new Error(response.status + " " + response.statusText);
      return response.json();
    });
  }

  function postJson(path, body) {
    return fetch(path, {
      method: "POST",
      headers: { Accept: "application/json", "Content-Type": "application/json" },
      body: JSON.stringify(body || {}),
    }).then(function (response) {
      return response.json().catch(function () { return {}; }).then(function (payload) {
        if (!response.ok) throw new Error(payload.error || (response.status + " " + response.statusText));
        return payload;
      });
    });
  }

  function selectedRow() {
    return state.rows.find(function (row) { return row.slug === state.selected; }) || state.rows[0] || null;
  }

  function targetSummary() {
    var rows = (state.targetData && state.targetData.targets) || [];
    var pocs = (state.targetData && state.targetData.pursuit_pocs) || [];
    var plan = (state.targetData && state.targetData.targeting_plan) || { actions: {}, play_lane_dispositions: {} };
    var actions = plan.actions || {};
    function laneCount(lane) { return Object.keys(actions).filter(function (id) { return actions[id] && actions[id].lane === lane && !["reject", "defer"].includes(actions[id].disposition); }).length; }
    return {
      rows: rows,
      pocs: pocs,
      plan: plan,
      actions: actions,
      buyer: laneCount("buyer"),
      acquisition: laneCount("acquisition"),
      partner: laneCount("partner"),
      incumbent: laneCount("incumbent"),
      positioning: laneCount("positioning"),
      event: laneCount("event"),
      gaps: rows.filter(function (row) { return (row.needs || []).length || !row.title; }).length,
      actionGaps: rows.filter(function (row) { return !(actions[row.id] && actions[row.id].owner && actions[row.id].action_window && actions[row.id].first_ask); }).length,
    };
  }

  function scopeLabel() {
    var steps = (state.detail && state.detail.steps) || [];
    var detail = (steps.find(function (step) { return step.key === "strategy" || step.key === "approve" || step.key === "approved"; }) || {}).detail || {};
    var scope = detail.search_scope || ((detail.strategy || {}).search_scope);
    if (scope && scope.all) return "All Federal";
    if (scope && Array.isArray(scope.agencies) && scope.agencies.length) return scope.agencies.join(" + ");
    return "Current approved scope";
  }

  function selectedEditionDoc(row) {
    var docs = (row && row.documents) || [];
    if (!row || !docs.length) return null;
    var saved = state.editionPaths[row.slug];
    if (!saved) {
      try { saved = global.localStorage.getItem("lilaCommandCenterEdition:" + row.slug); } catch (_error) {}
    }
    var doc = docs.find(function (item) { return item.path === saved; }) || docs[0];
    if (doc && doc.path) state.editionPaths[row.slug] = doc.path;
    return doc;
  }

  function editionLabel(row) {
    var doc = selectedEditionDoc(row);
    if (doc && doc.label) return doc.label;
    var pressed = row && row.cc_meta && row.cc_meta.pressed_display;
    return pressed ? "Pressed " + pressed : "Current working edition";
  }

  function readinessModel() {
    var row = selectedRow() || {};
    var detail = state.detail || {};
    var targets = targetSummary();
    var stages = detail.stages || row.stages || {};
    var records = row.cc_meta && row.cc_meta.records;
    var assessment = Boolean(detail.review_approved);
    var evidence = Boolean(detail.assess_ready && stages.searched);
    var targetingReceipt = (state.targetData && state.targetData.targeting_readiness) || { ready: false, problems: [] };
    var targeting = Boolean(targetingReceipt.ready);
    var assets = Boolean((detail.documents || row.documents || []).length);
    var output = Boolean(detail.final_product && detail.final_product.qa_pass);
    var canPress = assessment && evidence && targeting && assets;
    var components = [
      { key: "assessment", label: "Assessment readiness", ok: assessment, state: assessment ? "ready" : "blocked", note: assessment ? "Current assessment approval is bound" : "Current assessment approval required" },
      { key: "evidence", label: "Evidence readiness", ok: evidence, state: evidence ? "ready" : "blocked", note: evidence ? ((records == null ? "Accepted research is present" : records + " bound records")) : "Accepted search and evidence review required" },
      { key: "targeting", label: "Targeting readiness", ok: targeting, state: targeting ? "ready" : "blocked", note: targeting ? targets.rows.length + " source-bound targets · completion receipt current" : ((targetingReceipt.problems || [])[0] || (targets.rows.length ? targets.rows.length + " targets found · Targeting Review incomplete" : "No qualified target found")) },
      { key: "assets", label: "Asset readiness", ok: assets, state: assets ? "ready" : "blocked", note: assets ? (detail.documents || row.documents || []).length + " registered artifacts" : "No registered output assets" },
      { key: "output", label: "Output readiness", ok: output, state: output ? "ready" : (canPress ? "pending" : "blocked"), note: output ? "QA-clean releasable artifact" : (canPress ? "Authorized generation pending; release remains locked until QA passes" : "No QA-clean releasable artifact") },
    ];
    return { components: components, assessment: assessment, evidence: evidence, targeting: targeting, assets: assets, output: output, readyCount: components.filter(function (item) { return item.ok; }).length, canPress: canPress, releaseReady: canPress && output };
  }

  function nextAction() {
    var ready = readinessModel();
    var targets = targetSummary();
    if (!ready.evidence) return { stage: "analyst", label: "Complete the Analyst Layer", detail: "Review the accepted search, evidence, thesis, and opportunity screen before client review.", owner: "Analyst + operator", time: "20–30 min", button: "Open Analyst Layer" };
    if (!ready.assessment) return { stage: "assessment", label: "Approve the Assessment Review", detail: "Resolve the report thesis and evidence decisions in the server-owned assessment workspace.", owner: "Operator review", time: "10–20 min", button: "Review assessment" };
    if (!targets.rows.length) return { stage: "targets", label: "Build a qualified targeting route", detail: "Every top play needs a source-bound buyer, acquisition, partner, or positioning route before Press.", owner: "Analyst + account owner", time: "20–30 min", button: "Open Targeting Review" };
    if (!ready.targeting) return { stage: "targets", label: "Complete the Targeting Review", detail: targets.actionGaps + " target records still lack an operator-confirmed owner, action window, or first ask; Target unlock alone does not satisfy this review.", owner: "Account owner", time: "15–25 min", button: "Review targeting gaps" };
    if (!ready.output) return { stage: "press", label: "Generate the authorized outputs", detail: "Assessment, evidence, targeting, and assets are ready. Open the server-owned Press to generate; release stays locked until output QA passes.", owner: "Report operator", time: "10–20 min", button: "Open Press controls" };
    return { stage: "press", label: "Open the authorized Press", detail: "Assessment, evidence, targeting, assets, and output are all ready in the server-owned run.", owner: "Report operator", time: "5–10 min", button: "Open Press controls" };
  }

  function currentStage() {
    var next = nextAction().stage;
    if (next === "analyst") return "Analyst Layer";
    if (next === "assessment" || next === "targets") return "Review";
    return "Press";
  }

  function activate(mount) {
    document.body.classList.add("command-center-v2-active");
    mount.classList.add("command-center-home");
  }

  function deactivate() {
    document.body.classList.remove("command-center-v2-active");
    var mount = document.getElementById("main");
    if (mount) mount.classList.remove("command-center-home");
    closeOverlay();
  }

  function openClient(row) {
    if (!row) return;
    deactivate();
    if (typeof global.select === "function") global.select(row.slug);
    else global.location.href = "/client/" + encodeURIComponent(row.slug);
  }

  function reportPath() {
    var edition = selectedEditionDoc(selectedRow());
    if (edition && edition.path && /\.(html|pdf)$/i.test(edition.path)) return edition.path;
    var detail = state.detail || {};
    var report = detail.final_product || null;
    return report && report.path;
  }

  function openReport(path) {
    if (!path) return;
    var row = selectedRow();
    global.open("/report?path=" + encodeURIComponent(path) + "&client=" + encodeURIComponent((row && row.slug) || ""), "_blank", "noopener");
  }

  function loadSelected() {
    var row = selectedRow();
    state.detail = null;
    state.targetData = null;
    state.ccModel = null;
    if (!row) return Promise.resolve();
    return Promise.all([
      json("/api/client/" + encodeURIComponent(row.slug)).catch(function (error) { return { error: error.message }; }),
      json("/api/client/" + encodeURIComponent(row.slug) + "/targets").catch(function (error) { return { targets: [], pursuit_pocs: [], error: error.message }; }),
      json("/api/client/" + encodeURIComponent(row.slug) + "/cc-model").catch(function (error) { return { sources: [], error: error.message }; }),
      state.sourceNetwork ? Promise.resolve(state.sourceNetwork) : json("/api/source-network").catch(function (error) { return { sources: [], error: error.message }; }),
    ]).then(function (payloads) {
      state.detail = payloads[0] || {};
      state.targetData = payloads[1] || { targets: [], pursuit_pocs: [] };
      state.ccModel = payloads[2] || { sources: [] };
      state.sourceNetwork = payloads[3] || { sources: [] };
    });
  }

  function selectClient(slug) {
    if (!state.rows.some(function (row) { return row.slug === slug; })) return Promise.resolve();
    state.selected = slug;
    try { global.localStorage.setItem("lilaCommandCenterClient", slug); } catch (_error) {}
    return loadSelected();
  }

  function navMarkup() {
    return NAV.map(function (group) {
      return '<div class="cc2-nav-label">' + group[0] + '</div><nav class="cc2-nav" aria-label="' + group[0] + '">' +
        group[1].map(function (item) {
          return '<button type="button" data-view="' + escapeHtml(item[0]) + '" aria-label="' + escapeHtml(item[0]) + '" title="' + escapeHtml(item[0]) + '"' + (state.view === item[0] ? ' aria-current="page"' : '') + '>' + icon(item[1], "cc2-nav-icon") + '<span>' + escapeHtml(item[0]) + '</span></button>';
        }).join("") + '</nav>';
    }).join("");
  }

  function shell(content) {
    var row = selectedRow();
    var selected = row ? row.client_name : "No client selected";
    var context = state.view === "Command Center" ? "" : '<div class="cc2-context" aria-label="Selected report context"><button type="button" class="cc2-context-client" data-client-snapshot aria-label="Open ' + escapeHtml(selected) + ' intelligence snapshot">' + (row ? clientMark(row, "cc2-client-mark cc2-mark-tiny") : "") + '<span><b>Selected client</b>' + escapeHtml(selected) + '</span>' + icon("insights") + '</button><span><b>Scope</b>' + escapeHtml(scopeLabel()) + '</span><span><b>Active edition</b>' + escapeHtml(editionLabel(row)) + '</span><span><b>Current stage</b>' + escapeHtml(currentStage()) + '</span><button type="button" data-stage="select">Switch client</button></div>';
    return '<div class="cc2-shell' + (state.navOpen ? " nav-open" : "") + '">' +
      '<button class="cc2-nav-scrim" type="button" data-nav-close tabindex="-1" aria-hidden="true" aria-label="Close navigation"></button>' +
      '<header class="cc2-topbar"><div class="cc2-topbar-brand"><button class="cc2-mobile-menu" type="button" data-nav-open aria-label="Open navigation" aria-controls="cc2Sidebar" aria-expanded="' + (state.navOpen ? "true" : "false") + '">' + icon("menu") + '</button><img src="/api/marks/gtm" alt="GTM Group"><strong>LILA Report Press</strong></div><div class="cc2-top-actions"><button type="button" class="cc2-icon-button" data-search aria-label="Search">' + icon("search") + '</button><button type="button" class="cc2-icon-button" data-calendar aria-label="Open calendar">' + icon("calendar_month") + '</button><span class="cc2-operator"><b>OP</b><span>Operator workspace</span></span></div></header>' +
      '<aside class="cc2-sidebar" id="cc2Sidebar"><div class="cc2-sidebar-head"><img src="/api/marks/gtm" alt=""><div><strong>LILA</strong><span>Federal Sales OS</span></div><button class="cc2-mobile-close" type="button" data-nav-close aria-label="Close navigation">' + icon("close") + '</button></div>' + navMarkup() + '<div class="cc2-sidebar-spacer"></div><button type="button" class="cc2-new-client" data-new-client>' + icon("add") + 'Start new client</button><div class="cc2-sidebar-foot">' + icon("lock") + '<span>Gates, approvals, and releases remain server-owned.</span></div></aside>' +
      '<main class="cc2-main">' + context + content + '</main><div class="cc2-live sr-only" role="status" aria-live="polite"></div></div>';
  }

  function stageRail() {
    var ready = readinessModel();
    var current = currentStage();
    var steps = [
      { name: "Select Client", key: "select", note: "Account, scope & edition", icon: "domain", status: state.selected ? "complete" : "current" },
      { name: "Analyst Layer", key: "analyst", note: "Evidence, thesis & targets", icon: "auto_awesome", status: current === "Analyst Layer" ? "current" : (ready.evidence ? "complete" : "locked") },
      { name: "Review", key: "review", note: "Assessment + Targeting", icon: "fact_check", status: current === "Review" ? "current" : (ready.assessment && ready.targeting ? "complete" : (ready.evidence ? "locked" : "locked")) },
      { name: "Press", key: "press", note: "Generate authorized outputs", icon: "rocket_launch", status: ready.canPress ? (ready.releaseReady ? "complete" : "current") : "locked" },
    ];
    return '<section class="cc2-stage-rail" aria-label="Report production workflow">' + steps.map(function (step) {
      var statusLabel = step.status === "complete" ? "Complete" : step.status === "current" ? "Current" : "Locked";
      return '<button type="button" class="cc2-stage ' + step.status + '" data-stage="' + step.key + '" aria-label="' + escapeHtml(step.name + ": " + step.note + ". " + statusLabel) + '"' + (step.name === current ? ' aria-current="step"' : '') + '><span class="cc2-stage-icon">' + icon(step.icon) + '</span><span><strong>' + step.name + '</strong><small>' + step.note + '</small></span><span class="cc2-stage-status">' + (step.status === "complete" ? icon("check_circle") + " Complete" : step.status === "current" ? "Current" : icon("lock") + " Locked") + '</span></button>';
    }).join("") + '</section>';
  }

  function readinessRows(compact) {
    var ready = readinessModel();
    return '<div class="cc2-readiness-list' + (compact ? " compact" : "") + '">' + ready.components.map(function (item) {
      return '<div class="cc2-readiness-row ' + item.state + '"><span class="cc2-state-icon">' + icon(item.state === "ready" ? "check_circle" : item.state === "pending" ? "pending" : "error") + '</span><span><strong>' + item.label + '</strong><small>' + item.note + '</small></span><em>' + (item.state === "ready" ? "Ready" : item.state === "pending" ? "Pending" : "Blocked") + '</em></div>';
    }).join("") + '</div>';
  }

  function homeLayerTabs() {
    var target = targetSummary();
    var ready = readinessModel();
    var tabs = [
      ["decide", "Decide", "One decision and the active queue", "bolt", 1],
      ["pursue", "Pursue", "Routes, targets, and account lanes", "target", target.rows.length],
      ["verify", "Verify", "Readiness, evidence, and research proof", "verified_user", ready.readyCount],
    ];
    return '<nav class="cc2-home-layers" aria-label="Command Center intelligence layers" role="tablist">' + tabs.map(function (tab) {
      var active = state.homeLayer === tab[0];
      return '<button type="button" role="tab" data-home-layer="' + tab[0] + '" aria-selected="' + active + '"' + (active ? ' aria-current="page"' : '') + '>' + icon(tab[3]) + '<span><strong>' + tab[1] + ' <b class="cc2-tab-count">' + tab[4] + '</b></strong><small>' + tab[2] + '</small></span></button>';
    }).join("") + '</nav>';
  }

  function evidenceSpineMarkup(ready) {
    return '<section class="cc2-evidence-spine" aria-label="Evidence Spine"><header><span>Evidence Spine</span><small>One approval unlocks downstream readiness and decisions</small></header><div class="cc2-spine-track">' + ready.components.map(function (item, index) {
      var status = item.state === "ready" ? "Ready" : item.state === "pending" ? "Pending" : "Blocked";
      return '<article class="cc2-spine-node ' + escapeHtml(item.state) + '"><span class="cc2-spine-index">' + (index + 1) + '</span><strong>' + escapeHtml(item.label) + '</strong><b>' + status + '</b><small>' + escapeHtml(item.note) + '</small></article>';
    }).join("") + '</div></section>';
  }

  function evidenceMetricsMarkup() {
    var model = state.ccModel || {};
    var metrics = model.metrics || {};
    var selected = selectedRow() || {};
    var selectedRecords = selected.cc_meta && selected.cc_meta.records;
    var evidenceValue = metrics.evidence && metrics.evidence.value;
    if ((evidenceValue == null || evidenceValue === 0) && selectedRecords != null) evidenceValue = selectedRecords;
    var items = [
      ["description", "Cited records", evidenceValue, (metrics.evidence && metrics.evidence.context) || (selectedRecords != null ? "Bound depository records" : "Populates from linked records"), metrics.evidence && metrics.evidence.href],
      ["account_balance", "Historical obligations", metrics.footprint && metrics.footprint.display, metrics.footprint && metrics.footprint.context, metrics.footprint && metrics.footprint.href],
      ["trending_up", "Projected demand", metrics.forecast_lb && metrics.forecast_lb.display, metrics.forecast_lb && metrics.forecast_lb.context, metrics.forecast_lb && metrics.forecast_lb.href],
    ];
    return '<section class="cc2-metric-strip" aria-label="Selected client evidence metrics">' + items.map(function (item) {
      var href = safeUrl(item[4]);
      var value = item[2] == null || item[2] === "" ? "—" : item[2];
      var body = '<span class="cc2-metric-icon">' + icon(item[0]) + '</span><span><strong>' + escapeHtml(value) + '</strong><b>' + escapeHtml(item[1]) + '</b><small>' + escapeHtml(item[3] || "Populates from linked records") + (item[1] === "Projected demand" ? " · not additive" : "") + '</small></span>' + (href ? icon("open_in_new") : "");
      return href ? '<a href="' + escapeHtml(href) + '" target="_blank" rel="noopener noreferrer">' + body + '</a>' : '<div>' + body + '</div>';
    }).join("") + '</section>';
  }

  function decideLayerMarkup(row, ready, target) {
    var next = nextAction();
    var alternatives = state.rows.filter(function (item) { return item.slug !== row.slug; }).slice(0, 3);
    return '<section class="cc2-decision-board"><article class="cc2-next-action"><div class="cc2-next-copy"><span class="cc2-next-label">' + icon("bolt") + 'Single next action</span><h2>' + escapeHtml(next.label) + '</h2><span class="cc2-root-blocker">Root blocker</span><p>' + escapeHtml(next.detail) + '</p><div class="cc2-action-meta"><span>' + icon("person") + '<b>Owner</b>' + escapeHtml(next.owner) + '</span><span>' + icon("timer") + '<b>Expected time</b>' + escapeHtml(next.time) + '</span><span>' + icon("target") + '<b>Target set</b>' + target.rows.length + " source-bound records" + '</span></div><div class="cc2-next-buttons"><button type="button" class="cc2-primary" data-next-action>' + escapeHtml(next.button) + icon("arrow_forward") + '</button><button type="button" class="cc2-secondary" data-client-snapshot>' + icon("visibility") + 'View client snapshot</button></div></div></article>' + evidenceSpineMarkup(ready) + '</section>' + evidenceMetricsMarkup() + researchNetworkMarkup() +
      '<section class="cc2-queue cc2-priority-queue"><div class="cc2-section-head"><div><span>Prioritized account queue</span><h2>' + alternatives.length + ' other active runs</h2></div><button type="button" data-view="Clients">View all clients ' + icon("arrow_forward") + '</button></div>' + alternatives.map(function (item) {
        var rec = item.cc_meta && item.cc_meta.records;
        var missing = rec == null;
        return '<article class="cc2-queue-row">' + clientMark(item) + '<span><strong>' + escapeHtml(item.client_name) + '</strong><small>' + escapeHtml((item.attention && item.attention.label) || item.next_step || "Open client") + '</small></span><span class="cc2-receipt ' + (missing ? "pending" : "verified") + '">' + icon(missing ? "pending" : "verified") + (missing ? "Research not pressed" : rec + " bound records") + '</span><button type="button" data-select-client="' + escapeHtml(item.slug) + '" aria-label="Preview ' + escapeHtml(item.client_name) + ' intelligence">' + icon("arrow_forward") + '</button></article>';
      }).join("") + '</section>';
  }

  function routeSummaryMarkup(target) {
    var lanes = [
      ["buyer", "Buyer", target.buyer], ["acquisition", "Acquisition", target.acquisition],
      ["partner", "Partner & access", target.partner], ["incumbent", "Incumbent", target.incumbent],
      ["positioning", "Positioning", target.positioning], ["event", "Event", target.event],
    ];
    var classified = lanes.reduce(function (total, lane) { return total + Number(lane[2] || 0); }, 0);
    var unclassified = Math.max(0, target.rows.length - classified);
    var populated = lanes.filter(function (lane) { return lane[2]; });
    return '<section class="cc2-route-summary"><div><span>Route classification</span><strong>' + target.rows.length + ' source-bound targets</strong><small>Compact account-route view; open a target for its authoritative action record.</small></div><div class="cc2-route-chips">' + populated.map(function (lane) { return '<span><b>' + lane[2] + '</b>' + escapeHtml(lane[1]) + '</span>'; }).join("") + (unclassified ? '<span class="unclassified"><b>' + unclassified + '</b>Unclassified</span>' : "") + '</div><b>' + target.actionGaps + ' action gaps</b></section>';
  }

  function pursueLayerMarkup(target) {
    return '<section class="cc2-layer-panel cc2-pursue-layer"><header><div><span class="cc2-eyebrow">Account execution</span><h2>Pursuit routes and source-bound targets</h2><p>See the full route architecture before opening the canonical target workspace.</p></div><div><button type="button" class="cc2-secondary" data-client-snapshot>' + icon("insights") + 'Client snapshot</button><button type="button" class="cc2-primary" data-view="Target Lists">Open Target Lists ' + icon("arrow_forward") + '</button></div></header>' +
      routeSummaryMarkup(target) + researchNetworkMarkup() + '<div class="cc2-target-layer-head"><span><strong>' + target.rows.length + ' selected-client targets</strong><small>Each visible source route returns to its published evidence.</small></span><b>Ranked source routes</b></div><div class="cc2-target-list">' + targetRowsMarkup(12) + '</div></section>';
  }

  function researchNetworkMarkup() {
    var sources = (state.sourceNetwork && state.sourceNetwork.sources) || (state.ccModel && state.ccModel.sources) || [];
    var groups = ["PROCUREMENT", "VEHICLES", "FORECASTS", "NEWS & PRESS", "SEARCH"];
    var labels = { "PROCUREMENT": "Procurement", "FORECASTS": "Forecasts", "NEWS & PRESS": "News & Press", "SEARCH": "Search", "VEHICLES": "Vehicles" };
    var grouped = {};
    groups.forEach(function (group) { grouped[group] = []; });
    sources.forEach(function (source) { (grouped[source.group] || (grouped[source.group] = [])).push(source); });
    var populatedGroups = groups.filter(function (group) { return grouped[group].length; });
    var preview = populatedGroups.map(function (group) { return grouped[group][0]; });
    sources.forEach(function (source) { if (preview.length < 6 && preview.indexOf(source) < 0) preview.push(source); });
    function sourceState(source) {
      var status = String(source.status || "registered").toLowerCase();
      if (status === "degraded") return { cls: "is-degraded", label: "Attention" };
      if (status === "unverified") return { cls: "is-unverified", label: "Review" };
      if (status === "catalog-verified") return { cls: "is-catalog-verified", label: "Catalog verified" };
      return { cls: "is-registered", label: "Indexed" };
    }
    function sourceRow(source, previewRow) {
      var url = safeUrl(source.url);
      var stateMark = sourceState(source);
      var body = '<span class="cc2-source-dot ' + stateMark.cls + '" aria-hidden="true"></span><span><strong>' + escapeHtml(source.name || source.key || "Source") + '</strong><small>' + escapeHtml(source.coverage || "Research coverage") + '</small></span>';
      var cls = previewRow ? ' class="cc2-network-preview-row"' : "";
      var label = escapeHtml((source.name || source.key || "Source") + " represented in the current research surface");
      return url ? '<a' + cls + ' href="' + escapeHtml(url) + '" target="_blank" rel="noopener noreferrer" aria-label="' + label + '">' + body + icon("open_in_new") + '</a>' : '<div' + cls + ' aria-label="' + label + '">' + body + '<em>' + escapeHtml(stateMark.label) + '</em></div>';
    }
    return '<details class="cc2-research-network"' + (queryValue("cc-sources") === "open" ? " open" : "") + '><summary><div class="cc2-network-summary-head"><span class="cc2-network-beacon" aria-hidden="true"><i></i><i></i><i></i></span><span><strong>Research Network</strong><small>LILA research surface · ' + sources.length + ' registered sources across ' + populatedGroups.length + ' research groups</small></span><span class="cc2-network-count">Show all ' + sources.length + ' sources</span>' + icon("expand_more") + '</div><div class="cc2-network-categories">' + populatedGroups.map(function (group) { return '<span>' + escapeHtml(labels[group]) + '<b>' + grouped[group].length + '</b></span>'; }).join("") + '</div><div class="cc2-network-preview">' + preview.map(function (source) { return sourceRow(source, true); }).join("") + '</div></summary><div class="cc2-network-groups">' + populatedGroups.map(function (group) {
      return '<section><header><span><strong>' + labels[group] + '</strong></span><b>' + grouped[group].length + '</b></header><div>' + grouped[group].map(function (source) {
        var url = safeUrl(source.url);
        var stateMark = sourceState(source);
        var body = '<span class="cc2-source-dot ' + stateMark.cls + '" aria-hidden="true"></span><span><strong>' + escapeHtml(source.name || source.key || "Source") + '</strong><small>' + escapeHtml(source.coverage || "Research coverage") + '</small></span>';
        var label = escapeHtml((source.name || source.key || "Source") + " represented in the current research surface");
        return url ? '<a href="' + escapeHtml(url) + '" target="_blank" rel="noopener noreferrer" aria-label="' + label + '">' + body + icon("open_in_new") + '</a>' : '<div aria-label="' + label + '">' + body + '<em>' + escapeHtml(stateMark.label) + '</em></div>';
      }).join("") + '</div></section>';
    }).join("") + '</div></details>';
  }

  function verifyLayerMarkup(ready) {
    return '<section class="cc2-layer-panel cc2-verify-layer"><header><div><span class="cc2-eyebrow">Research proof</span><h2>Readiness and evidence</h2><p>Inspect the selected run, its bound evidence, and the full research surface behind it.</p></div><button type="button" class="cc2-secondary" data-review-kind="hub">Open Assessment + Targeting Review ' + icon("arrow_forward") + '</button></header>' + evidenceSpineMarkup(ready) + evidenceMetricsMarkup() + researchNetworkMarkup() + '</section>';
  }

  function homeMarkup() {
    var row = selectedRow();
    if (!row) return '<div class="cc2-empty">No clients yet. Start a new client to create the first assessment run.</div>';
    var ready = readinessModel();
    var target = targetSummary();
    var layer = state.homeLayer === "pursue" ? pursueLayerMarkup(target) : state.homeLayer === "verify" ? verifyLayerMarkup(ready) : decideLayerMarkup(row, ready, target);
    var sourceCount = ((state.sourceNetwork && state.sourceNetwork.sources) || []).length;
    return '<section class="cc2-account-hero cc2-account-masthead"><div class="cc2-masthead-identity">' + clientMark(row, "cc2-client-mark cc2-mark-hero") + '<span><small>Selected client</small><h1>' + escapeHtml(row.client_name) + '</h1></span></div><div class="cc2-masthead-fact">' + icon("account_balance") + '<span><small>Scope</small><strong>' + escapeHtml(scopeLabel()) + '</strong></span></div><div class="cc2-masthead-fact">' + icon("layers") + '<span><small>Stage</small><strong>' + escapeHtml(currentStage()) + '</strong></span></div><div class="cc2-masthead-fact press">' + icon(ready.canPress ? "rocket_launch" : "warning") + '<span><small>Press</small><strong>' + (ready.releaseReady ? "Release ready" : ready.canPress ? "Allowed" : "Blocked") + '</strong></span></div><button type="button" class="cc2-masthead-sources" data-home-layer-jump="verify"><i class="cc2-source-dot is-registered" aria-hidden="true"></i>' + sourceCount + ' research sources</button></section>' + homeLayerTabs() + '<div class="cc2-home-layer" role="tabpanel">' + layer + '</div>';
  }

  function targetType(row) {
    var action = targetSummary().actions[row.id] || {};
    var named = {
      buyer: "Buyer / mission-owner route", acquisition: "Acquisition route",
      partner: "Partner / access route", incumbent: "Incumbent-displacement route",
      positioning: "Positioning route", event: "Published event route",
    }[action.lane];
    if (named) return named;
    if (row.bucket === "solicitation") return row.title ? "Published notice POC" : "Notice POC — role unknown";
    if (row.bucket === "prime") return "Potential partner contact — validate role";
    return row.title ? "Published role — route unclassified" : "Published contact — route unclassified";
  }

  function targetDisposition(row) {
    var action = targetSummary().actions[row.id] || {};
    if (action.disposition) return action.disposition.replace(/_/g, " ").replace(/\b\w/g, function (letter) { return letter.toUpperCase(); });
    if ((row.needs || []).length) return "Needs enrichment";
    return "Unreviewed";
  }

  function targetSource(row) {
    return (row.email && row.email.source_url) || (row.phone && row.phone.source_url) || "";
  }

  function targetRowsMarkup(limit) {
    var rows = targetSummary().rows.slice(0, limit || 99);
    if (!rows.length) return '<div class="cc2-no-target"><strong>No qualified target found</strong><p>The selected play has no source-bound person or organization in the current target store. Preserve this as a targeting gap and open the client workspace to research the verified role.</p></div>';
    return rows.map(function (row, index) {
      var source = safeUrl(targetSource(row));
      var organization = row.company ? companyMark(row.company, "cc2-company-mark cc2-mark-small") : agencyMark(row.agency, "cc2-agency-mark cc2-mark-small");
      return '<article class="cc2-target-row"><span class="cc2-target-rank">' + String(index + 1).padStart(2, "0") + '</span>' + organization + '<span class="cc2-target-person"><strong>' + escapeHtml(row.person_name || row.company || row.agency || "Role target") + '</strong><small>' + escapeHtml(row.title || targetType(row)) + ' · ' + escapeHtml(row.agency || "Organization not stated") + '</small></span><span class="cc2-target-play">' + escapeHtml(row.reason || "Reason for contact not available") + '</span><span class="cc2-target-disposition">' + escapeHtml(targetDisposition(row)) + '</span>' + (source ? '<a href="' + escapeHtml(source) + '" target="_blank" rel="noopener noreferrer" aria-label="Open source for ' + escapeHtml(row.person_name || "target") + '">' + icon("open_in_new") + '</a>' : '<span class="cc2-source-missing" aria-label="Source missing">' + icon("link_off") + '</span>') + '<button type="button" data-target-index="' + index + '" aria-label="Inspect ' + escapeHtml(row.person_name || "target") + '">' + icon("arrow_forward") + '</button></article>';
    }).join("");
  }

  function laneMarkup() {
    var t = targetSummary();
    var lanes = [
      ["Buyer", t.buyer, "Mission or program owner"],
      ["Acquisition", t.acquisition, "Notice, forecast, contract strategy"],
      ["Partner & access", t.partner, "Prime, holder, reseller, teaming route"],
      ["Incumbent displacement", t.incumbent, "Current holder and successor motion"],
      ["Positioning", t.positioning, "Pre-procurement point of view"],
      ["Event", t.event, "Published engagement route"],
    ];
    return '<div class="cc2-lane-grid">' + lanes.map(function (lane) {
      return '<article class="' + (lane[1] ? "qualified" : "gap") + '"><span>' + escapeHtml(lane[0]) + '</span><strong>' + lane[1] + '</strong><small>' + (lane[1] ? escapeHtml(lane[2]) : "No qualified target found") + '</small></article>';
    }).join("") + '</div>';
  }

  function lanePlannerMarkup() {
    var plan = targetSummary().plan;
    var dispositions = plan.play_lane_dispositions || {};
    var lanes = [["buyer", "Buyer"], ["acquisition", "Acquisition"], ["partner", "Partner & access"], ["incumbent", "Incumbent displacement"], ["positioning", "Positioning"], ["event", "Event"]];
    var playsById = {};
    targetSummary().rows.filter(function (target) { return Number(target.reason_rank || 999) < 90 && target.play_id; }).forEach(function (target) { playsById[target.play_id] = target.reason || "Promoted play"; });
    var plays = Object.keys(playsById).map(function (playId) { return { id: playId, label: playsById[playId] }; });
    var done = plays.reduce(function (count, play) { return count + lanes.filter(function (lane) { return dispositions[play.id] && dispositions[play.id][lane[0]] && dispositions[play.id][lane[0]].status; }).length; }, 0);
    var total = plays.length * lanes.length;
    return '<details class="cc2-lane-planner-wrap"><summary>' + icon("rule") + '<span><strong>Play-by-play lane disposition review</strong><small>' + done + '/' + total + ' recorded · every promoted play keeps its own route decisions and evidence gaps</small></span>' + icon("expand_more") + '</summary><form class="cc2-lane-planner cc2-play-lane-planner" data-lane-plan><div><span>Required lane review</span><strong>For each promoted play, bind a complete action or record what was checked, what is missing, and the assigned, time-bounded next research step.</strong></div>' + (plays.length ? plays.map(function (play) {
      var playRows = dispositions[play.id] || {};
      return '<details class="cc2-play-plan"><summary><span><strong>Promoted play</strong><small>' + escapeHtml(play.label) + '</small></span>' + icon("expand_more") + '</summary><div class="cc2-play-plan-grid">' + lanes.map(function (lane) {
        var value = playRows[lane[0]] || {};
        var prefix = play.id + '__' + lane[0] + '__';
        return '<fieldset><legend>' + escapeHtml(lane[1]) + '</legend><label><span>Disposition</span><select name="' + prefix + 'status" required><option value="">Select disposition</option><option value="covered"' + (value.status === "covered" ? " selected" : "") + '>Covered by a complete action for this play</option><option value="no_qualified_target"' + (value.status === "no_qualified_target" ? " selected" : "") + '>No qualified target found</option><option value="not_applicable"' + (value.status === "not_applicable" ? " selected" : "") + '>Not applicable to this play</option></select></label><label><span>Evidence checked</span><textarea name="' + prefix + 'evidence_checked" rows="2" placeholder="Sources, awards, forecasts, holders, or event records checked">' + escapeHtml(value.evidence_checked || "") + '</textarea></label><label><span>Missing role or route</span><input name="' + prefix + 'missing" value="' + escapeHtml(value.missing || "") + '" placeholder="Specific unresolved person, role, holder, or access path"></label><label><span>Exact next research action</span><textarea name="' + prefix + 'next_action" rows="2" placeholder="Concrete source or verification action">' + escapeHtml(value.next_action || "") + '</textarea></label><label><span>Research owner</span><input name="' + prefix + 'owner" value="' + escapeHtml(value.owner || "") + '" placeholder="Named operator"></label><label><span>Research deadline</span><input name="' + prefix + 'deadline" value="' + escapeHtml(value.deadline || "") + '" placeholder="Date or bounded window"></label></fieldset>';
      }).join("") + '</div></details>';
    }).join("") : '<div class="cc2-no-target"><strong>No promoted plays available</strong><p>Return to Assessment Review and promote a source-bound play before Targeting Review can be completed.</p></div>') + '<button class="cc2-secondary" type="submit">Save play-specific lane dispositions</button></form></details>';
  }

  function workspaceMarkup(name) {
    var row = selectedRow();
    var ready = readinessModel();
    var meta = {
      "Clients": ["Operate · client context", "Clients", "Choose the exact client, approved scope, and active report edition."],
      "Review Queue": ["Operate · required work products", "Review Queue", "Assessment Review and Targeting Review remain distinct, required, and bound to the same selected run."],
      "Report Library": ["Operate · report editions", "Report Library", "Open exact registered artifacts; QA and release state remain server-owned."],
      "Research Packs": ["Build · analyst layer", "Research Packs", "Inspect accepted research, opportunity paths, evidence, and targeting supply for the selected client."],
      "Target Lists": ["Build · account action", "Target Lists", "Operational targeting for the selected client: route, reason, provenance, gaps, and authoritative handoff."],
      "Evidence": ["Build · source truth", "Evidence", "Read the five readiness components without treating artifact count as proof of release readiness."],
      "Templates & Assets": ["Utility · output system", "Templates & Assets", "Review registered output artifacts and open the exact report path."],
      "Settings": ["Utility · authoritative controls", "Settings", "Open the existing client intake and credential surfaces. No parallel configuration state is stored here."],
    }[name];
    var body = "";
    if (name === "Clients") {
      body = '<div class="cc2-client-grid">' + state.rows.map(function (item) {
        return '<button type="button" class="cc2-client-card' + (item.slug === state.selected ? " selected" : "") + '" data-select-client="' + escapeHtml(item.slug) + '" aria-label="Preview ' + escapeHtml(item.client_name) + ' intelligence">' + clientMark(item) + '<span><strong>' + escapeHtml(item.client_name) + '</strong><small>' + escapeHtml(editionLabel(item)) + '</small></span><em>' + escapeHtml((item.attention && item.attention.label) || item.next_step || "Open") + '</em>' + icon(item.slug === state.selected ? "check_circle" : "arrow_forward") + '</button>';
      }).join("") + '</div>';
    } else if (name === "Review Queue") {
      body = '<div class="cc2-review-cards"><button type="button" data-review-kind="assessment"><span class="violet">' + icon("description") + '</span><span><em>Required work product 1</em><strong>Assessment Review</strong><small>' + (ready.assessment ? "Current approval is bound to this run." : "Current approval is required before Press.") + '</small><b>' + (ready.assessment ? "Ready" : "Blocked") + '</b></span>' + icon("arrow_forward") + '</button><button type="button" data-review-kind="targets"><span class="coral">' + icon("target") + '</span><span><em>Required work product 2</em><strong>Targeting Review</strong><small>' + (ready.targeting ? "Target gate and source-bound routes are ready." : "Routes or Target approval are incomplete.") + '</small><b>' + (ready.targeting ? "Ready" : "Blocked") + '</b></span>' + icon("arrow_forward") + '</button></div>';
    } else if (name === "Target Lists") {
      body = '<div class="cc2-target-toolbar"><div><span>Selected target set</span><h2>' + escapeHtml(row.client_name) + ' account-action routes</h2></div><button type="button" class="cc2-primary" data-approve-targeting>Approve complete Targeting Review ' + icon("verified") + '</button></div>' + laneMarkup() + lanePlannerMarkup() + '<div class="cc2-target-list">' + targetRowsMarkup(12) + '</div>';
    } else if (name === "Evidence") {
      body = readinessRows(false) + '<div class="cc2-evidence-note">' + icon("verified_user") + '<span><strong>Confidence-honesty contract</strong>Assessment strength cannot conceal weak targeting. Press eligibility requires all five visible components.</span></div>';
    } else if (name === "Report Library" || name === "Templates & Assets") {
      var docs = (state.detail && state.detail.documents) || row.documents || [];
      body = '<div class="cc2-document-list">' + docs.map(function (doc) {
        var externalReady = Boolean(doc.external_product && doc.qa_pass);
        var status = doc.external_product ? (externalReady ? "Release ready" : "Release state must be checked") : "Internal view";
        return '<article><span>' + icon(doc.fmt === "pdf" ? "picture_as_pdf" : "article") + '</span><span><strong>' + escapeHtml(doc.label || doc.path || "Report") + '</strong><small>' + escapeHtml(doc.fmt || "artifact") + ' · ' + status + '</small></span><em class="' + (externalReady ? "ready" : "review") + '">' + (externalReady ? "Release ready" : (doc.external_product ? "Review" : "Internal")) + '</em><button type="button" data-report="' + escapeHtml(doc.path || "") + '">Open ' + icon("open_in_new") + '</button></article>';
      }).join("") + '</div>';
    } else if (name === "Research Packs") {
      var globalTarget = state.targets.find(function (item) { return item.client_id === row.slug; }) || { candidates: [], target_agencies: [] };
      body = '<div class="cc2-analyst-grid"><button type="button" data-stage="analyst" data-analyst-view="evidence"><span>' + icon("verified_user") + '</span><strong>Evidence & math</strong><small>' + Number((row.cc_meta || {}).records || 0) + ' bound records in the current read model</small></button><button type="button" data-stage="analyst" data-analyst-view="plays"><span>' + icon("route") + '</span><strong>Opportunity paths</strong><small>' + globalTarget.candidates.length + ' pressed candidates · ' + globalTarget.target_agencies.length + ' target agencies</small></button><button type="button" data-stage="analyst" data-analyst-view="targets"><span>' + icon("target") + '</span><strong>Targets & contacts</strong><small>' + targetSummary().rows.length + ' source-bound records · ' + targetSummary().gaps + ' gaps</small></button></div>';
    } else {
      body = '<div class="cc2-settings-grid"><button type="button" data-api-keys>' + icon("key") + '<span><strong>API keys</strong><small>Open the existing provider credential workspace.</small></span>' + icon("arrow_forward") + '</button><button type="button" data-new-client>' + icon("person_add") + '<span><strong>Client intake</strong><small>Open the established client and strategy workflow.</small></span>' + icon("arrow_forward") + '</button></div>';
    }
    return '<section class="cc2-workspace"><header><div><span class="cc2-eyebrow">' + meta[0] + '</span><h1>' + meta[1] + '</h1><p>' + meta[2] + '</p></div><aside>' + clientMark(row, "cc2-client-mark cc2-mark-medium") + '<span><b>Selected client</b><strong>' + escapeHtml(row.client_name) + '</strong><small>' + escapeHtml(scopeLabel()) + '</small></span></aside></header><div class="cc2-workspace-body">' + body + '</div></section>';
  }

  function paint(mount) {
    activate(mount);
    mount.innerHTML = shell(state.view === "Command Center" ? homeMarkup() : workspaceMarkup(state.view));
    bind(mount);
    syncNavA11y(mount);
  }

  function syncNavA11y(mount) {
    var narrow = global.matchMedia && global.matchMedia("(max-width: 850px)").matches;
    var sidebar = mount.querySelector(".cc2-sidebar");
    var main = mount.querySelector(".cc2-main");
    var topbar = mount.querySelector(".cc2-topbar");
    if (!sidebar || !main || !topbar) return;
    sidebar.inert = Boolean(narrow && !state.navOpen);
    sidebar.setAttribute("aria-hidden", narrow && !state.navOpen ? "true" : "false");
    main.inert = Boolean(narrow && state.navOpen);
    topbar.inert = Boolean(narrow && state.navOpen);
    if (narrow && state.navOpen) {
      sidebar.setAttribute("role", "dialog");
      sidebar.setAttribute("aria-modal", "true");
      sidebar.setAttribute("aria-label", "Primary navigation");
    } else {
      sidebar.removeAttribute("role");
      sidebar.removeAttribute("aria-modal");
      sidebar.removeAttribute("aria-label");
    }
  }

  function trapNavFocus(event) {
    if (!state.navOpen || event.key !== "Tab") return false;
    var sidebar = document.getElementById("cc2Sidebar");
    if (!sidebar) return false;
    var focusable = [].slice.call(sidebar.querySelectorAll('button:not([disabled]), a[href], input:not([disabled]), select:not([disabled]), textarea:not([disabled])')).filter(function (item) { return item.offsetParent !== null; });
    if (!focusable.length) return false;
    var first = focusable[0];
    var last = focusable[focusable.length - 1];
    if (event.shiftKey && (document.activeElement === first || !sidebar.contains(document.activeElement))) {
      event.preventDefault();
      last.focus();
      return true;
    }
    if (!event.shiftKey && (document.activeElement === last || !sidebar.contains(document.activeElement))) {
      event.preventDefault();
      first.focus();
      return true;
    }
    return false;
  }

  function openNav() {
    state.navOpen = true;
    var mount = document.getElementById("main");
    paint(mount);
    var close = mount.querySelector(".cc2-mobile-close");
    if (close) close.focus();
  }

  function closeNav() {
    state.navOpen = false;
    var mount = document.getElementById("main");
    paint(mount);
    var trigger = mount.querySelector("[data-nav-open]");
    if (trigger) trigger.focus();
  }

  function overlay(content, cls) {
    closeOverlay();
    lastFocus = document.activeElement;
    var node = document.createElement("div");
    node.className = "cc2-overlay";
    node.id = "cc2Overlay";
    var dialogName = cls === "cc2-search-modal" ? ' aria-label="Search clients, reviews, or reports"' : ' aria-labelledby="cc2-dialog-title"';
    node.innerHTML = '<section class="' + (cls || "cc2-drawer") + '" role="dialog" aria-modal="true"' + dialogName + '>' + content + '</section>';
    node.onclick = function (event) { if (event.target === node) closeOverlay(); };
    node.addEventListener("keydown", function (event) {
      if (event.key !== "Tab") return;
      var focusable = [].slice.call(node.querySelectorAll('button:not([disabled]), a[href], input:not([disabled])')).filter(function (item) { return item.offsetParent !== null; });
      if (!focusable.length) return;
      var first = focusable[0], last = focusable[focusable.length - 1];
      if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
      else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
    });
    document.body.appendChild(node);
    var firstButton = node.querySelector("button, a[href], input");
    if (firstButton) firstButton.focus();
    return node;
  }

  function closeOverlay() {
    var node = document.getElementById("cc2Overlay");
    if (node) node.remove();
    if (lastFocus && lastFocus.focus) lastFocus.focus();
    lastFocus = null;
  }

  function studioHeader(title, kicker) {
    return '<header><div><span>' + escapeHtml(kicker || "Report production studio") + '</span><h2 id="cc2-dialog-title">' + escapeHtml(title) + '</h2></div><button class="cc2-icon-button" type="button" data-close aria-label="Close dialog">' + icon("close") + '</button></header>';
  }

  function intelligenceSnapshotModel() {
    var row = selectedRow() || {};
    var steps = (state.detail && state.detail.steps) || [];
    var review = steps.find(function (step) { return step.key === "review"; }) || {};
    var search = steps.find(function (step) { return step.key === "search"; }) || {};
    var opportunities = ((review.detail || {}).opportunities || []).slice();
    var globalTarget = state.targets.find(function (item) { return item.client_id === row.slug; }) || { candidates: [], target_agencies: [] };
    if (!opportunities.length) opportunities = (globalTarget.candidates || []).slice();
    var best = opportunities.find(function (item) { return item.verdict === "pursue"; }) || opportunities[0] || null;
    var agencies = [];
    opportunities.forEach(function (item) {
      if (item.agency && !agencies.includes(item.agency)) agencies.push(item.agency);
    });
    targetSummary().rows.forEach(function (item) {
      if (item.agency && !agencies.includes(item.agency)) agencies.push(item.agency);
    });
    var target = targetSummary().rows.slice().sort(function (a, b) { return Number(a.reason_rank || 999) - Number(b.reason_rank || 999); })[0] || null;
    var news = state.ticker.filter(function (item) {
      return item.slug === row.slug || String(item.client_name || "").toLowerCase() === String(row.client_name || "").toLowerCase();
    }).slice(0, 5);
    return {
      row: row, best: best, agencies: agencies.slice(0, 4), target: target,
      evidence: search.summary || "The current research run has not published an evidence summary yet.",
      opportunities: opportunities.length,
      records: Number((row.cc_meta || {}).records || opportunities.length || 0),
      next: nextAction(), ready: readinessModel(), news: news,
    };
  }

  function openClientSnapshot() {
    var snap = intelligenceSnapshotModel();
    var row = snap.row;
    var best = snap.best;
    var bestUrl = best && safeUrl(best.url || best.source_url);
    var opportunity = best ? '<article class="cc2-snapshot-opportunity"><div>' + agencyMark(best.agency, "cc2-agency-mark cc2-mark-medium") + '<span><em>Best current opportunity signal</em><strong>' + escapeHtml(best.title || "Qualified opportunity") + '</strong><small>' + escapeHtml(best.agency || "Agency not stated") + '</small></span></div><p>' + escapeHtml(best.reason || best.why_now || "This is the highest-ranked current opportunity in the accepted intelligence set.") + '</p>' + (bestUrl ? '<a href="' + escapeHtml(bestUrl) + '" target="_blank" rel="noopener noreferrer">Open source ' + icon("open_in_new") + '</a>' : '<span class="cc2-snapshot-source-missing">Source link not available</span>') + '</article>' : '<div class="cc2-no-target"><strong>No qualified opportunity snapshot yet</strong><p>The client is visible, but the accepted research run has not produced a ranked opportunity.</p></div>';
    var agencies = snap.agencies.length ? snap.agencies.map(function (agency) { return '<span class="cc2-snapshot-agency">' + agencyMark(agency, "cc2-agency-mark cc2-mark-small") + '<span>' + escapeHtml(agency) + '</span></span>'; }).join("") : '<span class="cc2-snapshot-empty">No agency concentration available yet.</span>';
    var target = snap.target ? '<article class="cc2-snapshot-target">' + agencyMark(snap.target.agency, "cc2-agency-mark cc2-mark-small") + '<span><em>Best target route</em><strong>' + escapeHtml(snap.target.person_name || snap.target.agency || "Role target") + '</strong><small>' + escapeHtml(snap.target.reason || targetType(snap.target)) + '</small></span></article>' : '<article class="cc2-snapshot-target empty">' + icon("person_search") + '<span><em>Target route</em><strong>No qualified target found</strong><small>Targeting remains an explicit research gap.</small></span></article>';
    var ticker = snap.news.length ? snap.news.map(function (item) {
      var href = safeUrl(item.source_url || item.url);
      var body = '<span><time>' + escapeHtml(item.when || "Current") + '</time><strong>' + escapeHtml(item.text || "Client intelligence update") + '</strong></span>';
      return href ? '<a href="' + escapeHtml(href) + '" target="_blank" rel="noopener noreferrer">' + body + icon("open_in_new") + '</a>' : '<div>' + body + '</div>';
    }).join("") : '<div class="cc2-snapshot-ticker-empty"><span><time>No current item</time><strong>No client-specific news or deadline signal cleared the relevance filter.</strong></span></div>';
    var content = studioHeader(row.client_name + " intelligence snapshot", "A quick window into this account") + '<div class="cc2-drawer-body cc2-snapshot-body"><section class="cc2-snapshot-identity">' + clientMark(row, "cc2-client-mark cc2-mark-snapshot") + '<div><span>Federal account intelligence</span><h3>' + escapeHtml(row.client_name) + '</h3><p>' + escapeHtml(scopeLabel()) + ' · ' + escapeHtml(editionLabel(row)) + '</p></div><b>' + snap.ready.readyCount + '/5 ready</b></section><div class="cc2-snapshot-metrics"><span><strong>' + snap.records + '</strong><small>bound records</small></span><span><strong>' + snap.opportunities + '</strong><small>opportunity signals</small></span><span><strong>' + targetSummary().rows.length + '</strong><small>source-bound targets</small></span></div><section class="cc2-snapshot-ticker"><header><span>' + icon("breaking_news") + '</span><div><em>Client intelligence ticker</em><small>Only signals bound to ' + escapeHtml(row.client_name) + '</small></div></header><div>' + ticker + '</div></section>' + opportunity + '<section class="cc2-snapshot-evidence"><span>' + icon("verified_user") + '</span><div><em>Strongest evidence picture</em><p>' + escapeHtml(snap.evidence) + '</p></div></section><section class="cc2-snapshot-agencies"><div><em>Agency concentration</em><small>Official seals identify the federal organizations in the accepted intelligence.</small></div><div>' + agencies + '</div></section>' + target + '<section class="cc2-snapshot-next"><span>' + icon("bolt") + '</span><div><em>Exact next action</em><strong>' + escapeHtml(snap.next.label) + '</strong><p>' + escapeHtml(snap.next.detail) + '</p></div></section></div><footer><span>Snapshot only · full evidence and protected actions stay in the client workspace</span><div><button class="cc2-secondary" type="button" data-preview-enter>Stay in Command Center</button><button class="cc2-primary" type="button" data-open-client="' + escapeHtml(row.slug) + '">Open full client workspace ' + icon("arrow_forward") + '</button></div></footer>';
    var node = overlay(content, "cc2-drawer cc2-snapshot-drawer");
    bind(node);
  }

  function openStudio(kind, requestedReview) {
    if (requestedReview) state.reviewView = requestedReview;
    var row = selectedRow();
    var ready = readinessModel();
    var content = "";
    if (kind === "select") {
      var selectedEdition = selectedEditionDoc(row);
      var editions = (row.documents || []).slice(0, 8);
      content = studioHeader("Select the client and edition") + '<div class="cc2-drawer-body"><div class="cc2-studio-intro"><span>01</span><div><strong>Choose the operating context</strong><small>Client, scope, edition, evidence, targets, and report state stay bound together.</small></div></div><div class="cc2-choice-grid">' + state.rows.map(function (item) {
        return '<button class="cc2-choice' + (item.slug === state.selected ? " selected" : "") + '" type="button" data-pick="' + escapeHtml(item.slug) + '" aria-pressed="' + (item.slug === state.selected ? "true" : "false") + '" aria-label="Preview ' + escapeHtml(item.client_name) + ' intelligence">' + clientMark(item, "cc2-client-mark cc2-choice-mark") + '<span><strong>' + escapeHtml(item.client_name) + '</strong><small>' + escapeHtml(editionLabel(item)) + '</small></span><em>' + escapeHtml((item.attention && item.attention.label) || item.next_step || "Open") + '</em>' + icon(item.slug === state.selected ? "check_circle" : "arrow_forward") + '</button>';
      }).join("") + '</div><section class="cc2-edition-picker"><div><span>Active edition for ' + escapeHtml(row.client_name) + '</span><strong>' + escapeHtml(editionLabel(row)) + '</strong><small>Edition choice persists per client; review and Press keep this exact context visible.</small></div>' + (editions.length ? editions.map(function (doc) { return '<button type="button" data-edition-path="' + escapeHtml(doc.path || "") + '" aria-pressed="' + (selectedEdition && selectedEdition.path === doc.path ? "true" : "false") + '"><span>' + icon(doc.fmt === "pdf" ? "picture_as_pdf" : "article") + '<strong>' + escapeHtml(doc.label || doc.path) + '</strong></span><em>' + (selectedEdition && selectedEdition.path === doc.path ? "Active" : "Select") + '</em></button>'; }).join("") : '<div class="cc2-no-target"><strong>No registered edition yet</strong><p>The current working edition will appear after the first authorized Press.</p></div>') + '</section></div><footer><span>' + escapeHtml(row.client_name) + ' · ' + escapeHtml(editionLabel(row)) + '</span><button class="cc2-primary" type="button" data-continue="analyst">Continue to Analyst Layer ' + icon("arrow_forward") + '</button></footer>';
    } else if (kind === "analyst") {
      var globalTarget = state.targets.find(function (item) { return item.client_id === row.slug; }) || { candidates: [], target_agencies: [] };
      content = studioHeader("Shape the Analyst Layer") + '<div class="cc2-drawer-body"><div class="cc2-studio-intro"><span>02</span><div><strong>Turn accepted research into judgment</strong><small>These read-only receipts open the existing client workspace for protected decisions.</small></div></div><div class="cc2-analyst-grid studio"><button type="button" class="' + (state.analystView === "evidence" ? "selected" : "") + '" data-analyst-view="evidence" aria-pressed="' + (state.analystView === "evidence" ? "true" : "false") + '">' + icon("verified_user") + '<strong>Evidence & math</strong><small>' + Number((row.cc_meta || {}).records || 0) + ' bound records</small></button><button type="button" class="' + (state.analystView === "plays" ? "selected" : "") + '" data-analyst-view="plays" aria-pressed="' + (state.analystView === "plays" ? "true" : "false") + '">' + icon("route") + '<strong>Opportunity paths</strong><small>' + globalTarget.candidates.length + ' pressed candidates</small></button><button type="button" class="' + (state.analystView === "targets" ? "selected" : "") + '" data-analyst-view="targets" aria-pressed="' + (state.analystView === "targets" ? "true" : "false") + '">' + icon("target") + '<strong>Targets & contacts</strong><small>' + targetSummary().rows.length + ' source-bound targets</small></button></div><div class="cc2-analyst-detail"><span>Active analyst receipt</span><h3>' + (state.analystView === "evidence" ? "Evidence and math" : state.analystView === "plays" ? "Opportunity paths" : "Target supply") + '</h3><p>' + (state.analystView === "evidence" ? "Accepted search and evidence must be current before Assessment Review." : state.analystView === "plays" ? globalTarget.target_agencies.length + " approved target agencies and " + globalTarget.candidates.length + " pressed opportunity paths are visible." : targetSummary().rows.length + " target records carry a reason for contact; " + targetSummary().gaps + " still expose enrichment or role gaps.") + '</p></div></div><footer><span>Server-owned evidence and decisions remain authoritative</span><button class="cc2-primary" type="button" data-open-client="' + escapeHtml(row.slug) + '">Open client Analyst workspace ' + icon("arrow_forward") + '</button></footer>';
    } else if (kind === "review") {
      if (state.reviewView === "hub") {
        content = studioHeader("Complete both required reviews") + '<div class="cc2-drawer-body"><div class="cc2-studio-intro"><span>03</span><div><strong>Assessment and Targeting are distinct work products</strong><small>Both stay bound to the same client, scope, edition, and evidence.</small></div></div><div class="cc2-review-cards studio"><button type="button" data-review-kind="assessment"><span class="violet">' + icon("description") + '</span><span><em>Required work product 1</em><strong>Assessment Review</strong><small>Thesis, opportunity calls, evidence receipts, and client-safe framing.</small><b>' + (ready.assessment ? "Ready" : "Blocked") + '</b></span>' + icon("arrow_forward") + '</button><button type="button" data-review-kind="targets"><span class="coral">' + icon("target") + '</span><span><em>Required work product 2</em><strong>Targeting Review</strong><small>Routes, reason for contact, provenance, first ask, owner, and action window.</small><b>' + (ready.targeting ? "Ready" : "Blocked") + '</b></span>' + icon("arrow_forward") + '</button></div>' + readinessRows(true) + '</div><footer><span>Press remains locked until every required component is ready</span><button class="cc2-primary" type="button" data-continue="press">Inspect Press readiness ' + icon("arrow_forward") + '</button></footer>';
      } else if (state.reviewView === "assessment") {
        var steps = (state.detail && state.detail.steps) || [];
        content = studioHeader("Review the assessment") + '<div class="cc2-drawer-body"><button class="cc2-back" type="button" data-back-review>' + icon("arrow_back") + 'Review choices</button><div class="cc2-review-summary"><div><span>' + escapeHtml(row.client_name) + '</span><h3>' + escapeHtml(editionLabel(row)) + '</h3><small>' + (ready.assessment ? "Current assessment approval is bound" : "Assessment approval is required") + '</small></div>' + (reportPath() ? '<button type="button" class="cc2-secondary" data-report="' + escapeHtml(reportPath()) + '">Open report preview</button>' : "") + '</div><div class="cc2-step-list">' + steps.slice(0, 7).map(function (step, index) { return '<article><span>' + String(index + 1).padStart(2, "0") + '</span><span><strong>' + escapeHtml(step.title || step.key) + '</strong><small>' + escapeHtml(step.summary || "Open the client workspace for detail") + '</small></span><em class="' + escapeHtml(step.state || "waiting") + '">' + escapeHtml(step.state || "waiting") + '</em></article>'; }).join("") + '</div></div><footer><span>Assessment decisions are taken in the client workspace</span><button class="cc2-primary" type="button" data-open-client="' + escapeHtml(row.slug) + '">Open authoritative Assessment Review ' + icon("arrow_forward") + '</button></footer>';
      } else {
        content = studioHeader("Review the targeting routes") + '<div class="cc2-drawer-body"><button class="cc2-back" type="button" data-back-review>' + icon("arrow_back") + 'Review choices</button><div class="cc2-review-summary"><div><span>Required work product 2</span><h3>' + escapeHtml(row.client_name) + ' Targeting Review</h3><small>' + (ready.targeting ? "Completion receipt current · release gate satisfied" : "Targeting incomplete · Target unlock alone is not completion") + '</small></div><button type="button" class="cc2-secondary" data-open-client="' + escapeHtml(row.slug) + '">Open Assessment / Target unlock controls</button></div>' + laneMarkup() + lanePlannerMarkup() + '<div class="cc2-target-list">' + targetRowsMarkup(8) + '</div></div><footer><span>No relationship or role is inferred from a name alone</span><button class="cc2-primary" type="button" data-approve-targeting>Approve complete Targeting Review ' + icon("verified") + '</button></footer>';
      }
    } else {
      content = studioHeader(ready.canPress ? "Open the authorized Press" : "Press is blocked") + '<div class="cc2-drawer-body"><div class="cc2-press-summary">' + clientMark(row) + '<span><strong>' + escapeHtml(row.client_name) + '</strong><small>' + escapeHtml(scopeLabel()) + ' · ' + escapeHtml(editionLabel(row)) + '</small></span><b class="' + (ready.canPress ? "ready" : "blocked") + '">' + (ready.releaseReady ? "Release ready" : ready.canPress ? "Generation authorized" : "Press blocked") + '</b></div>' + readinessRows(false) + (ready.canPress ? '<div class="cc2-output-note">' + icon(ready.output ? "verified" : "pending") + '<span><strong>' + (ready.output ? "The current output is QA-clean." : "The first authorized Press is ready to run.") + '</strong>' + (ready.output ? "The existing client workspace remains the only surface that can release protected outputs." : "Generate in the server-owned Press; download and report preview remain locked until output QA passes.") + '</span></div>' : '<div class="cc2-output-note blocked">' + icon("lock") + '<span><strong>Press is unavailable.</strong>Resolve the named readiness blockers; this shell cannot override them.</span></div>') + '</div><footer><span>' + (ready.canPress ? "Authorized formats remain server-controlled" : nextAction().label) + '</span><button class="cc2-primary" type="button" ' + (ready.canPress ? 'data-open-client="' + escapeHtml(row.slug) + '"' : 'data-continue-action') + '>' + escapeHtml(ready.canPress ? "Open server-owned Press controls" : nextAction().button) + ' ' + icon("arrow_forward") + '</button></footer>';
    }
    var node = overlay(content);
    bind(node);
  }

  function openTargetDetail(index) {
    var row = targetSummary().rows[index];
    if (!row) return;
    var source = safeUrl(targetSource(row));
    var play = String(row.reason || "").split("·").slice(1).join("·").trim() || row.reason || "Associated play not stated";
    var missing = (row.needs || []).slice();
    if (!row.title) missing.push("verified role or title");
    var firstAsk = "Confirm whether “" + play + "” is still active, identify the technical evaluation owner, and verify the acquisition channel for the next response.";
    var action = targetSummary().actions[row.id] || {};
    var routeDraft = row.bucket === "prime" ? "Validate a partner or access route without implying an existing relationship" : row.bucket === "solicitation" ? "Validate the published notice role and the acquisition route" : "Validate a pre-procurement positioning route before promotion";
    function option(value, label, current) { return '<option value="' + value + '"' + (current === value ? " selected" : "") + '>' + label + '</option>'; }
    var fields = [
      ["Target", row.person_name || row.company || row.agency || "Role target"],
      ["Target type", targetType(row)],
      ["Agency / organization", row.agency || "Not stated"],
      ["Verified role", row.title || "Role not stated in the current source"],
      ["Associated play", play],
      ["Why this target matters", row.reason || "Reason for contact not available"],
      ["Source date", row.last_observed || "No source date available"],
      ["Sequence position", row.reason_rank < 90 ? "Priority route " + row.reason_rank : "Validation queue"],
      ["Contact-information provenance", ((row.email && row.email.grade) || (row.phone && row.phone.grade) || "Unknown") + " grade · the linked publication supports the published channel only"],
      ["Evidence strength", source ? "Source-bound contact sighting; role and commercial route remain separately reviewable" : "Insufficient — authoritative source missing"],
      ["Relationship status", "No relationship or willingness to engage is claimed"],
      ["Facts still requiring verification", missing.length ? missing.join(", ") : "Current role, relationship, and action window"],
      ["Disposition", targetDisposition(row)],
      ["Commercial guidance status", "Operator-authored plan; it is not asserted by the contact source"],
    ];
    var form = '<form class="cc2-action-plan" data-target-plan data-target-id="' + escapeHtml(row.id) + '" data-plan-index="' + index + '"><div class="cc2-action-plan-head"><span>Account-action work product</span><strong>Confirm the route, message, first ask, owner, timing, outcome, and stop condition.</strong></div><div class="cc2-action-fields">' +
      '<label><span>Route lane</span><select name="lane" required><option value="">Select verified route</option>' + option("buyer", "Buyer / mission owner", action.lane) + option("acquisition", "Acquisition / contracting", action.lane) + option("partner", "Partner & access", action.lane) + option("incumbent", "Incumbent displacement", action.lane) + option("positioning", "Positioning", action.lane) + option("event", "Event", action.lane) + '</select></label>' +
      '<label><span>Disposition</span><select name="disposition" required><option value="">Select disposition</option>' + option("ready_to_contact", "Ready to contact", action.disposition) + option("needs_enrichment", "Needs enrichment", action.disposition) + option("needs_relationship_validation", "Needs relationship validation", action.disposition) + option("role_identified_person_unknown", "Role identified, person unknown", action.disposition) + option("partner_route", "Partner route", action.disposition) + option("buyer_route", "Buyer route", action.disposition) + option("influencer_route", "Influencer route", action.disposition) + option("watch", "Watch", action.disposition) + option("defer", "Defer", action.disposition) + option("reject", "Reject", action.disposition) + '</select></label>' +
      '<label class="wide"><span>Recommended commercial motion</span><textarea name="route" rows="2" required>' + escapeHtml(action.route || routeDraft) + '</textarea></label>' +
      '<label><span>Company’s proposed role</span><input name="proposed_role" value="' + escapeHtml(action.proposed_role || "Confirm the company role before outreach") + '" required></label>' +
      '<label><span>Suggested owner</span><input name="owner" value="' + escapeHtml(action.owner || "") + '" placeholder="Named account owner" required></label>' +
      '<label><span>Timing / action window</span><input name="action_window" value="' + escapeHtml(action.action_window || "") + '" placeholder="Date or bounded window" required></label>' +
      '<label class="wide"><span>Exact first ask</span><textarea name="first_ask" rows="2" required>' + escapeHtml(action.first_ask || firstAsk) + '</textarea></label>' +
      '<label class="wide"><span>Recommended message / outreach thesis</span><textarea name="message" rows="2" required>' + escapeHtml(action.message || ("Use the documented " + play + " need to test fit; do not claim a current relationship or verified responsibility.")) + '</textarea></label>' +
      '<label><span>Call to action</span><input name="call_to_action" value="' + escapeHtml(action.call_to_action || "Secure a short validation conversation or verified referral") + '" required></label>' +
      '<label><span>What must be learned</span><input name="learn" value="' + escapeHtml(action.learn || "Confirm current role, route, timing, and evaluation ownership") + '" required></label>' +
      '<label><span>Desired outcome</span><input name="desired_outcome" value="' + escapeHtml(action.desired_outcome || "Verified next step, responsible role, and acquisition path") + '" required></label>' +
      '<label><span>Qualification question</span><input name="qualification_question" value="' + escapeHtml(action.qualification_question || "Is this requirement active, funded, and owned by the published route?") + '" required></label>' +
      '<label><span>Stop condition</span><input name="stop_condition" value="' + escapeHtml(action.stop_condition || "Stop if the role, requirement, or timing cannot be verified") + '" required></label>' +
      '<label><span>Promotion criteria</span><input name="promotion_criteria" value="' + escapeHtml(action.promotion_criteria || "Promote only after route, owner, window, first ask, and source are current") + '" required></label>' +
      '<label class="wide"><span>Reject / defer reason</span><textarea name="reject_reason" rows="2" placeholder="Required when rejected or deferred">' + escapeHtml(action.reject_reason || "") + '</textarea></label></div><button class="cc2-primary" type="submit">Save server-owned action plan ' + icon("save") + '</button></form>';
    var organization = row.company ? companyMark(row.company, "cc2-company-mark cc2-mark-medium") : agencyMark(row.agency, "cc2-agency-mark cc2-mark-medium");
    var content = studioHeader(row.person_name || "Target detail", "Target evidence and action record") + '<div class="cc2-drawer-body"><div class="cc2-target-detail-head">' + organization + '<div><span>' + escapeHtml(targetType(row)) + '</span><h3>' + escapeHtml(row.person_name || row.agency || "Role target") + '</h3><p>' + escapeHtml(targetDisposition(row)) + '</p></div></div><dl class="cc2-target-detail">' + fields.map(function (field) { return '<div><dt>' + escapeHtml(field[0]) + '</dt><dd>' + escapeHtml(field[1]) + '</dd></div>'; }).join("") + '</dl>' + (source ? '<a class="cc2-source-link" href="' + escapeHtml(source) + '" target="_blank" rel="noopener noreferrer">' + icon("open_in_new") + '<span><strong>Contact-channel source</strong>' + escapeHtml(source) + '</span></a>' : '<div class="cc2-source-link missing">' + icon("link_off") + '<span><strong>Authoritative source missing</strong>This target cannot be promoted until provenance is restored.</span></div>') + form + '</div><footer><span>Draft commercial guidance is separate from sourced identity and contact facts</span><button class="cc2-secondary" type="button" data-open-client="' + escapeHtml(selectedRow().slug) + '">Open Assessment / Target unlock controls ' + icon("arrow_forward") + '</button></footer>';
    var node = overlay(content);
    bind(node);
  }

  function openSearch() {
    var content = '<div class="cc2-search-field"><span>' + icon("search") + '</span><input type="search" aria-label="Search clients" placeholder="Search clients, reviews, or reports"><button class="cc2-icon-button" type="button" data-close aria-label="Close search">' + icon("close") + '</button></div><div class="cc2-search-results"></div>';
    var node = overlay(content, "cc2-search-modal");
    var input = node.querySelector("input");
    var results = node.querySelector(".cc2-search-results");
    function draw() {
      var query = input.value.trim().toLowerCase();
      var rows = state.rows.filter(function (row) { return !query || [row.client_name, row.next_step, editionLabel(row)].join(" ").toLowerCase().indexOf(query) >= 0; });
      results.innerHTML = rows.map(function (row) { return '<button type="button" data-select-client="' + escapeHtml(row.slug) + '" aria-label="Preview ' + escapeHtml(row.client_name) + ' intelligence">' + clientMark(row) + '<span><strong>' + escapeHtml(row.client_name) + '</strong><small>' + escapeHtml(editionLabel(row)) + '</small></span>' + icon("arrow_forward") + '</button>'; }).join("") || '<div class="cc2-empty">No matching clients.</div>';
      bind(results);
    }
    input.oninput = draw;
    draw();
    input.focus();
    bind(node);
  }

  function takeNextAction() {
    var next = nextAction();
    if (next.stage === "analyst") openStudio("analyst");
    else if (next.stage === "assessment") openStudio("review", "assessment");
    else if (next.stage === "targets") openStudio("review", "targets");
    else openStudio("press");
  }

  function announce(message) {
    var live = document.querySelector(".cc2-live");
    if (live) live.textContent = message;
  }

  function saveTargetPlan(form) {
    var button = form.querySelector('button[type="submit"]');
    var data = new FormData(form);
    var action = {};
    ["lane", "disposition", "route", "proposed_role", "first_ask", "message", "call_to_action", "action_window", "owner", "learn", "desired_outcome", "qualification_question", "stop_condition", "promotion_criteria", "reject_reason"].forEach(function (key) { action[key] = String(data.get(key) || "").trim(); });
    button.disabled = true;
    button.textContent = "Saving action plan…";
    return postJson("/api/client/" + encodeURIComponent(selectedRow().slug) + "/targeting-plan", {
      target_id: form.getAttribute("data-target-id"), action: action,
    }).then(function (payload) {
      state.targetData.targeting_plan = payload.plan;
      return loadSelected();
    }).then(function () {
      announce("Target action plan saved");
      openTargetDetail(Number(form.getAttribute("data-plan-index")));
    }).catch(function (error) {
      button.disabled = false;
      button.textContent = "Save failed: " + String(error.message || error).slice(0, 90);
      announce(button.textContent);
    });
  }

  function saveLanePlan(form) {
    var data = new FormData(form);
    var lanes = {};
    var playIds = [];
    targetSummary().rows.filter(function (target) { return Number(target.reason_rank || 999) < 90 && target.play_id; }).forEach(function (target) { if (!playIds.includes(target.play_id)) playIds.push(target.play_id); });
    playIds.forEach(function (playId) {
      lanes[playId] = {};
      ["buyer", "acquisition", "partner", "incumbent", "positioning", "event"].forEach(function (lane) {
        var prefix = playId + "__" + lane + "__";
        lanes[playId][lane] = { status: String(data.get(prefix + "status") || ""), evidence_checked: String(data.get(prefix + "evidence_checked") || "").trim(), missing: String(data.get(prefix + "missing") || "").trim(), next_action: String(data.get(prefix + "next_action") || "").trim(), owner: String(data.get(prefix + "owner") || "").trim(), deadline: String(data.get(prefix + "deadline") || "").trim() };
      });
    });
    var inDrawer = Boolean(document.getElementById("cc2Overlay"));
    var button = form.querySelector('button[type="submit"]');
    button.disabled = true;
    button.textContent = "Saving lane dispositions…";
    return postJson("/api/client/" + encodeURIComponent(selectedRow().slug) + "/targeting-plan", { play_lane_dispositions: lanes }).then(function (payload) {
      state.targetData.targeting_plan = payload.plan;
      return loadSelected();
    }).then(function () {
      announce("Targeting lane dispositions saved");
      if (inDrawer) openStudio("review", "targets"); else paint(document.getElementById("main"));
    }).catch(function (error) {
      button.disabled = false;
      button.textContent = "Save failed: " + String(error.message || error).slice(0, 90);
      announce(button.textContent);
    });
  }

  function approveTargeting(button) {
    var inDrawer = Boolean(document.getElementById("cc2Overlay"));
    button.disabled = true;
    button.textContent = "Validating Targeting Review…";
    return postJson("/api/client/" + encodeURIComponent(selectedRow().slug) + "/targeting-review-approve", {}).then(function () {
      return loadSelected();
    }).then(function () {
      announce("Targeting Review approved and bound to the current target set");
      if (inDrawer) openStudio("review", "targets"); else paint(document.getElementById("main"));
    }).catch(function (error) {
      button.disabled = false;
      button.textContent = "Blocked: " + String(error.message || error).slice(0, 120);
      announce(button.textContent);
    });
  }

  function bind(root) {
    hydrateBrandMarks(root);
    [].slice.call(root.querySelectorAll("[data-home-layer], [data-home-layer-jump]")).forEach(function (button) {
      button.onclick = function () {
        state.homeLayer = button.getAttribute("data-home-layer") || button.getAttribute("data-home-layer-jump") || "decide";
        try { global.localStorage.setItem(HOME_LAYER_KEY, state.homeLayer); } catch (_error) {}
        paint(document.getElementById("main"));
      };
    });
    [].slice.call(root.querySelectorAll("[data-client-snapshot]")).forEach(function (button) { button.onclick = openClientSnapshot; });
    [].slice.call(root.querySelectorAll("[data-view]")).forEach(function (button) {
      button.onclick = function () { state.view = button.getAttribute("data-view"); state.navOpen = false; closeOverlay(); paint(document.getElementById("main")); };
    });
    [].slice.call(root.querySelectorAll("[data-stage]")).forEach(function (button) {
      button.onclick = function () { var view = button.getAttribute("data-analyst-view"); if (view) state.analystView = view; openStudio(button.getAttribute("data-stage")); };
    });
    [].slice.call(root.querySelectorAll("[data-review-kind]")).forEach(function (button) {
      button.onclick = function () { openStudio("review", button.getAttribute("data-review-kind")); };
    });
    [].slice.call(root.querySelectorAll("[data-select-client], [data-pick]")).forEach(function (button) {
      button.onclick = function () {
        var slug = button.getAttribute("data-select-client") || button.getAttribute("data-pick");
        closeOverlay();
        selectClient(slug).then(function () { state.view = "Command Center"; paint(document.getElementById("main")); openClientSnapshot(); });
      };
    });
    [].slice.call(root.querySelectorAll("[data-open-client]")).forEach(function (button) { button.onclick = function () { var slug = button.getAttribute("data-open-client"); openClient(state.rows.find(function (row) { return row.slug === slug; }) || selectedRow()); }; });
    [].slice.call(root.querySelectorAll("[data-report]")).forEach(function (button) { button.onclick = function () { openReport(button.getAttribute("data-report")); }; });
    [].slice.call(root.querySelectorAll("[data-edition-path]")).forEach(function (button) {
      button.onclick = function () {
        var row = selectedRow();
        var path = button.getAttribute("data-edition-path");
        if (!row || !(row.documents || []).some(function (doc) { return doc.path === path; })) return;
        state.editionPaths[row.slug] = path;
        try { global.localStorage.setItem("lilaCommandCenterEdition:" + row.slug, path); } catch (_error) {}
        announce("Active edition updated for " + row.client_name);
        openStudio("select");
      };
    });
    [].slice.call(root.querySelectorAll("[data-target-index]")).forEach(function (button) { button.onclick = function () { openTargetDetail(Number(button.getAttribute("data-target-index"))); }; });
    [].slice.call(root.querySelectorAll("[data-target-plan]")).forEach(function (form) { form.onsubmit = function (event) { event.preventDefault(); saveTargetPlan(form); }; });
    [].slice.call(root.querySelectorAll("[data-lane-plan]")).forEach(function (form) { form.onsubmit = function (event) { event.preventDefault(); saveLanePlan(form); }; });
    [].slice.call(root.querySelectorAll("[data-approve-targeting]")).forEach(function (button) { button.onclick = function () { approveTargeting(button); }; });
    [].slice.call(root.querySelectorAll("[data-analyst-view]")).forEach(function (button) { button.onclick = function () { state.analystView = button.getAttribute("data-analyst-view"); openStudio("analyst"); }; });
    var next = root.querySelector("[data-next-action]"); if (next) next.onclick = takeNextAction;
    var continueAction = root.querySelector("[data-continue-action]"); if (continueAction) continueAction.onclick = takeNextAction;
    var back = root.querySelector("[data-back-review]"); if (back) back.onclick = function () { state.reviewView = "hub"; openStudio("review"); };
    [].slice.call(root.querySelectorAll("[data-continue]")).forEach(function (button) { button.onclick = function () { openStudio(button.getAttribute("data-continue")); }; });
    [].slice.call(root.querySelectorAll("[data-close]")).forEach(function (button) { button.onclick = closeOverlay; });
    [].slice.call(root.querySelectorAll("[data-preview-enter]")).forEach(function (button) { button.onclick = function () { closeOverlay(); var trigger = document.querySelector('[data-stage="select"]'); if (trigger) trigger.focus(); }; });
    [].slice.call(root.querySelectorAll("[data-nav-open]")).forEach(function (button) { button.onclick = openNav; });
    [].slice.call(root.querySelectorAll("[data-nav-close]")).forEach(function (button) { button.onclick = closeNav; });
    var search = root.querySelector("[data-search]"); if (search) search.onclick = openSearch;
    var calendar = root.querySelector("[data-calendar]"); if (calendar) calendar.onclick = function () { deactivate(); if (typeof global.showCalendar === "function") global.showCalendar(); };
    var intake = root.querySelector("[data-new-client]"); if (intake) intake.onclick = function () { deactivate(); if (typeof global.openIntake === "function") global.openIntake(); };
    var keys = root.querySelector("[data-api-keys]"); if (keys) keys.onclick = function () { global.location.href = "/keys"; };
  }

  function render(mount) {
    activate(mount);
    mount.innerHTML = '<div class="cc2-empty">Loading the Command Center…</div>';
    state.loading = true;
    return Promise.all([
      json("/api/depository"),
      json("/api/calendar").catch(function () { return { items: [] }; }),
      json("/api/targets").catch(function () { return { clients: [] }; }),
      json("/api/ticker").catch(function () { return { items: [] }; }),
    ]).then(function (payloads) {
      state.rows = (payloads[0] || []).filter(function (row) { return !row.error; });
      state.calendar = payloads[1].items || [];
      state.targets = payloads[2].clients || [];
      state.ticker = payloads[3].items || [];
      var saved = null;
      var requestedClient = queryValue("cc-client");
      var requestedLayer = queryValue("cc-layer");
      try { saved = global.localStorage.getItem("lilaCommandCenterClient"); } catch (_error) {}
      try {
        var savedLayer = global.localStorage.getItem(HOME_LAYER_KEY);
        if (["decide", "pursue", "verify"].includes(requestedLayer)) state.homeLayer = requestedLayer;
        else if (["decide", "pursue", "verify"].includes(savedLayer)) state.homeLayer = savedLayer;
      } catch (_error) {}
      if (state.rows.some(function (row) { return row.slug === requestedClient; })) state.selected = requestedClient;
      else if (!state.selected || !state.rows.some(function (row) { return row.slug === state.selected; })) state.selected = state.rows.some(function (row) { return row.slug === saved; }) ? saved : (state.rows[0] && state.rows[0].slug);
      return loadSelected();
    }).then(function () {
      state.loading = false;
      state.view = "Command Center";
      paint(mount);
    }).catch(function (error) {
      state.loading = false;
      mount.innerHTML = '<div class="cc2-empty">Command Center unavailable: ' + escapeHtml(error.message || error) + '</div>';
    });
  }

  document.addEventListener("keydown", function (event) {
    if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k" && document.body.classList.contains("command-center-v2-active")) { event.preventDefault(); openSearch(); }
    if (trapNavFocus(event)) return;
    if (event.key === "Escape") {
      if (document.getElementById("cc2Overlay")) closeOverlay();
      else if (state.navOpen) closeNav();
    }
  });

  global.LILACommandCenterHome = { render: render, deactivate: deactivate };
})(window);
