/* ============================================================================
   LILA Command Center — client view renderer (ui/client-view branch)
   Vanilla, framework-free. Consumes the model produced by the read-only
   binder (P2: GET /api/client/<slug>/cc-model). Never restyles the pressed
   report; opens it by href only.

   Entry: LILAClientView.render(mount, model, opts)
   LINKAGE LAW: every record element resolves to its primary source; no dead
   identifiers. Computed aggregates link to the evidence view.
   ========================================================================== */
(function (global) {
  "use strict";

  // ---- tiny DOM helpers ----
  function esc(s) {
    return String(s == null ? "" : s)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }
  function el(html) {
    const t = document.createElement("template");
    t.innerHTML = html.trim();
    return t.content.firstElementChild;
  }
  function openTab(url) {
    if (url) window.open(url, "_blank", "noopener");
  }

  // ---- inline icons (stroke:currentColor) ----
  var ICON = {
    calendar: '<svg class="ico" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6"><rect x="3" y="4.5" width="18" height="16" rx="2"/><path d="M3 9h18M8 2.5v4M16 2.5v4"/></svg>',
    broadcast: '<svg class="ico" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6"><circle cx="12" cy="12" r="2.2"/><path d="M6.5 6.5a7.5 7.5 0 0 0 0 11M17.5 6.5a7.5 7.5 0 0 1 0 11M4 4a11 11 0 0 0 0 16M20 4a11 11 0 0 1 0 16"/></svg>',
    check: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="16" height="16"><path d="M20 6.5 9.5 17 4 11.5"/></svg>',
    circle: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" width="15" height="15"><circle cx="12" cy="12" r="8"/></svg>',
    x: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="15" height="15"><path d="M6 6l12 12M18 6 6 18"/></svg>',
    arrow: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" width="14" height="14" style="vertical-align:-2px"><path d="M5 12h13M12 5.5 18.5 12 12 18.5"/></svg>',
    doc: '<svg class="ico" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6"><path d="M6 2.5h8l4 4V21a1 1 0 0 1-1 1H6a1 1 0 0 1-1-1V3.5a1 1 0 0 1 1-1Z"/><path d="M14 2.5V7h4"/></svg>'
  };

  // record identifier -> anchor when a source exists, mono text otherwise.
  function rid(id, url) {
    if (!id) return "";
    if (url) return '<a class="rid" href="' + esc(url) + '" target="_blank" rel="noopener">' + esc(id) + "</a>";
    return '<span class="rid">' + esc(id) + "</span>";
  }

  var TAG_CLASS = { deadline: "deadline", clock: "clock", "on-ramp": "onramp", forecast: "forecast" };

  // ASSET LAW: harvested agency seal where present, else a monogram chip.
  function sealMark(item, assets, sizeClass) {
    var seals = (assets && assets.seals) || {};
    var uri = item.agency_token && seals[item.agency_token];
    if (uri) {
      return '<img class="cc-seal ' + sizeClass + '" src="' + esc(uri) +
        '" alt="' + esc(item.agency || "") + '" title="' + esc(item.agency || "") + '">';
    }
    return '<span class="cc-mono-seal ' + sizeClass + '" title="' + esc(item.agency || "") + '">' +
      esc(item.monogram || "?") + "</span>";
  }

  // ---------------------------------------------------------------- sections
  function identityHeader(m, opts) {
    var c = m.client || {};
    // brand chain: harvested report-header logo -> favicon mark -> monogram
    var brand = (m.assets && m.assets.logo);
    var fav = opts.logoDomain
      ? "https://www.google.com/s2/favicons?domain=" + encodeURIComponent(opts.logoDomain) + "&sz=64"
      : null;
    var logo = brand
      ? '<div class="cc-logo has-logo"><img src="' + esc(brand) + '" alt="' + esc(c.name) + ' logo"></div>'
      : fav
        ? '<div class="cc-logo has-logo"><img src="' + esc(fav) + '" alt="' + esc(c.name) +
          ' logo" onerror="this.parentNode.textContent=' + "'" + esc(c.initials || "?") + "'" + '"></div>'
        : '<div class="cc-logo">' + esc(c.initials || (c.name || "?").slice(0, 2).toUpperCase()) + "</div>";
    // client name links to the client's own site, from the research packet's
    // ingested domain (packet-sourced, never assumed)
    var nameHtml = c.website
      ? '<a class="cc-client-site" href="' + esc(c.website) + '" target="_blank" rel="noopener" title="' +
        esc(c.website) + '">' + esc(c.name) + "</a>"
      : esc(c.name);
    var node = el(
      '<section class="cc-identity">' + logo +
      '<div class="cc-idtext"><div class="name">' + nameHtml + "</div>" +
      (c.description ? '<div class="desc" title="' + esc(c.description) + '">' + esc(c.description) + "</div>" : "") +
      "</div>" +
      '<button class="cc-btn" data-act="press">Press new report</button></section>');
    node.querySelector('[data-act="press"]').onclick = function () {
      if (opts.onPress) opts.onPress();
    };
    return node;
  }

  function chipRow(m, state, rerender) {
    var comps = m.competitors || [], resellers = m.resellers || [];
    var wrap = el('<section class="cc-chips"></section>');
    wrap.appendChild(el('<span class="chip-label">Competitors</span>'));
    comps.forEach(function (name) {
      var active = state.filter === name;
      var chip = el('<button class="cc-chip" aria-pressed="' + active + '">' + esc(name) + "</button>");
      chip.onclick = function () {
        state.filter = active ? null : name;
        rerender();
      };
      wrap.appendChild(chip);
    });
    if (resellers.length) {
      var head = resellers.slice(0, 2).join(", ");
      var extra = resellers.length > 2 ? " +" + (resellers.length - 2) : "";
      var chip = el('<button class="cc-chip neutral">Resellers: ' + esc(head) + extra + "</button>");
      chip.onclick = function () {
        chip.textContent = state.reOpen ? "Resellers: " + head + extra : "Resellers: " + resellers.join(", ");
        state.reOpen = !state.reOpen;
      };
      wrap.appendChild(chip);
    }
    if (state.filter) {
      var note = el('<span class="cc-filter-note">filtered to ' + esc(state.filter) +
        ' · <a href="#" data-act="clear">clear</a></span>');
      note.querySelector('[data-act="clear"]').onclick = function (e) {
        e.preventDefault(); state.filter = null; rerender();
      };
      wrap.appendChild(note);
    }
    return wrap;
  }

  function scoreboard(m) {
    var mt = m.metrics || {};
    function card(label, spec, ghost) {
      var value = ghost ? "–" : (spec.display != null ? spec.display : spec.value);
      var ctx = ghost ? "populates on first press" : spec.context;
      // context line opens the aggregate's own evidence section in the report
      var ctxHtml = (!ghost && spec.href)
        ? '<a href="' + esc(spec.href) + '" target="_blank" rel="noopener">' + esc(ctx) + "</a>"
        : esc(ctx);
      return '<div class="cc-metric"><div class="label">' + esc(label) + "</div>" +
        '<div class="num' + (ghost ? " ghost" : "") + '">' + esc(value) + "</div>" +
        '<div class="ctx">' + ctxHtml + "</div></div>";
    }
    var g = m.partial;
    var sb = el('<section class="cc-scoreboard"></section>');
    sb.appendChild(el(card("Candidate corridors", mt.corridors || {}, g)));
    sb.appendChild(el(card("Active footprint", mt.footprint || {}, g)));
    sb.appendChild(el(card("Forecast lower bound", mt.forecast_lb || {}, g)));
    sb.appendChild(el(card("Evidence base", mt.evidence || {}, g)));
    return sb;
  }

  function ticker(rows, assets) {
    if (!rows || !rows.length) return null;
    var sec = el('<section class="cc-ticker"><span class="broadcast">' + ICON.broadcast +
      '</span><div class="cc-ticker-window"><div class="cc-ticker-track"></div></div></section>');
    var track = sec.querySelector(".cc-ticker-track");
    function item(t) {
      // real href (source) satisfies the click-through audit; click prefers
      // scroll-to-calendar-row, falling back to opening the source.
      var agencyTok = t.agency_url
        ? secondaryLink(esc(t.agency), t.agency_url, "agency")
        : esc(t.agency);
      var node = el('<a class="cc-tick" href="' + esc(t.url || "#") + '" target="_blank" rel="noopener">' +
        '<span class="t-type">' + esc(t.type) + "</span>" +
        '<span class="dot">·</span><span>' + esc(t.date_display) + "</span>" +
        '<span class="dot">·</span>' + sealMark(t, assets, "sz14") + "<span>" + agencyTok + "</span>" +
        '<span class="dot">·</span><span>' + esc(t.label) + "</span>" +
        (t.dollars ? '<span class="dot">·</span><span class="t-fig">' + esc(t.dollars) + "</span>" : "") +
        "</a>");
      node.onclick = function (e) {
        var target = document.getElementById(t.calendar_id);
        if (target) {
          e.preventDefault();
          target.scrollIntoView({ behavior: "smooth", block: "center" });
          target.classList.add("cc-flash");
          setTimeout(function () { target.classList.remove("cc-flash"); }, 1100);
        }
      };
      wireSecondary(node);
      return node;
    }
    // duplicate the track for a seamless marquee; pace by content so the
    // ticker reads calmly regardless of item count (~15s per item)
    rows.forEach(function (t) { track.appendChild(item(t)); });
    rows.forEach(function (t) { track.appendChild(item(t)); });
    track.style.animationDuration = Math.max(90, rows.length * 15) + "s";
    return sec;
  }

  function keyDates(rows, assets) {
    var card = el('<div class="card"><div class="card-hd">' + ICON.calendar +
      '<h3>Key dates</h3><span class="sub">accretes each press</span></div>' +
      '<div class="cc-cal-rows"></div></div>');
    var body = card.querySelector(".cc-cal-rows");
    if (!rows.length) {
      body.appendChild(el('<div class="cc-empty">No dates yet, press a report</div>'));
      return card;
    }
    rows.forEach(function (r) {
      // the row IS the anchor: opens the record's source_url (linkage law).
      // The agency token is a SECONDARY link to the official agency homepage
      // (static verified map); the record link stays primary.
      var tag = '<span class="cc-tag ' + (TAG_CLASS[r.type] || "clock") + '">' + esc(r.type) + "</span>";
      var agencyBit = r.agency
        ? ' <span class="c-agency">· ' + (r.agency_url
            ? secondaryLink(esc(r.agency), r.agency_url, "agency")
            : esc(r.agency)) + "</span>"
        : "";
      var label = esc(r.label) + agencyBit;
      var href = r.url ? ' href="' + esc(r.url) + '" target="_blank" rel="noopener"' : "";
      var row = el('<a class="cc-cal-row' + (r.past ? " past" : "") + '" id="' + esc(r.id) + '"' + href + '>' +
        '<span class="c-date">' + esc(r.date_display) + "</span>" +
        sealMark(r, assets, "sz16") +
        '<span class="c-label">' + label + "</span>" +
        tag + "</a>");
      wireSecondary(row);
      body.appendChild(row);
    });
    // open the list positioned at the first upcoming date; history sits above,
    // faint and scrollable (never hidden).
    var firstUpcoming = rows.findIndex(function (r) { return !r.past; });
    if (firstUpcoming > 0) {
      requestAnimationFrame(function () {
        var target = body.children[firstUpcoming];
        if (target) body.scrollTop = target.offsetTop - body.offsetTop - 6;
      });
    }
    return card;
  }

  function reportsRail(m, opts, state) {
    var card = el('<div class="card"><div class="card-hd"><h3>Reports</h3></div>' +
      '<div class="cc-reports"></div></div>');
    var body = card.querySelector(".cc-reports");
    (m.reports || []).forEach(function (r) {
      var node = el('<a class="cc-report" href="' + esc(r.href || "#") + '" target="_blank" rel="noopener">' +
        '<div class="r-title">' + esc(r.title) + "</div>" +
        '<div class="r-meta">Pressed ' + esc(r.pressed_display) + " · " + esc(r.records) +
        " records · open report</div></a>");
      body.appendChild(node);
    });
    if (!(m.reports || []).length) {
      body.appendChild(el('<div class="cc-empty">No reports yet</div>'));
    }
    body.appendChild(reportsTeaser());
    return card;
  }

  function targeting(state) {
    var wrap = el("<div></div>");
    var stub = el('<div class="cc-targeting"><div class="tg-text">' +
      '<div class="tg-title">Run targeting</div>' +
      '<div class="tg-sub">Personas, account owners, and outreach map from this evidence base</div>' +
      "</div><button class=\"cc-btn\" data-act=\"run\">Run " + ICON.arrow + "</button></div>");
    var panel = null;
    stub.querySelector('[data-act="run"]').onclick = function () {
      if (panel) return;
      panel = el('<div class="cc-targeting-panel"><button class="tp-dismiss" title="dismiss">' + ICON.x + "</button>" +
        "Targeting builds from this evidence base: buying-office personas, account ownership " +
        "hypotheses, verified contacts, outreach sequencing. Next release.</div>");
      panel.querySelector(".tp-dismiss").onclick = function () { panel.remove(); panel = null; };
      wrap.appendChild(panel);
    };
    wrap.appendChild(stub);
    return wrap;
  }

  function pressRail(stages) {
    if (!stages || !stages.length) return null;
    var card = el('<div class="cc-rail-rows"></div>');
    var body = card;
    var stopped = false;
    stages.forEach(function (s) {
      var st = s.status || "pending";
      if (stopped) st = "pending";
      var ico;
      if (st === "done") ico = ICON.check;
      else if (st === "active") ico = '<span class="cc-spinner"></span>';
      else if (st === "failed") ico = ICON.x;
      else ico = ICON.circle;
      var row = el('<div class="cc-rail-row ' + st + '">' +
        '<span class="r-ico">' + ico + "</span>" +
        '<span class="r-name">' + esc(s.name) + "</span>" +
        '<span class="r-receipt">' + esc(s.receipt || "") + "</span></div>");
      body.appendChild(row);
      if (st === "failed") stopped = true; // rail stops on the failed stage
    });
    return card;
  }

  function hero(m, opts) {
    var h = m.hero || {};
    // live same-origin preview of the pressed report inside the hero card;
    // the report renders itself (design law: iframe, never re-rendered).
    var preview = h.report_href
      ? '<a class="cc-hero-preview" href="' + esc(h.report_href) + '" target="_blank" rel="noopener"' +
        ' title="Open full report">' +
        '<iframe src="' + esc(h.report_href) + '" loading="lazy" tabindex="-1"></iframe></a>'
      : "";
    var node = el('<section class="cc-hero"><div class="eyebrow">' + esc(h.eyebrow) + "</div>" +
      '<div class="hero-name">' + esc(h.name) + "</div>" +
      '<div class="hero-stat">' + esc(h.stat_line) + "</div>" +
      preview +
      '<button class="cc-btn" data-act="open">Open full report</button></section>');
    node.querySelector('[data-act="open"]').onclick = function () { openTab(h.report_href); };
    return node;
  }

  // minimal dashed teaser shown inside the reports rail (Reference 1);
  // scrolls to the first-class Targeting layer panel (section 8) on click.
  function reportsTeaser() {
    var t = el('<div class="cc-targeting-teaser">' +
      '<p class="tt-title">Run targeting</p>' +
      '<p class="tt-sub">Next step · after pre-assessment</p></div>');
    t.onclick = function () {
      var panel = document.getElementById("cc-targeting");
      if (panel) panel.scrollIntoView({ behavior: "smooth", block: "start" });
    };
    return t;
  }

  // ---------------- 5.5 ANALYST LAYER PANEL (interactive) ----------------
  // SAM.gov structured deep links (sfm form) — live-verified this session;
  // the bare ?keywords=/?naics= params are IGNORED by SAM and never used.
  var SAM_BASE = "https://sam.gov/search/?index=opp&page=1&pageSize=25&sort=-modifiedDate";
  function samSearch(params) {
    if (params.naics) {
      var n = encodeURIComponent(params.naics);
      return SAM_BASE + "&sfm%5BserviceClassificationWrapper%5D%5Bnaics%5D%5B0%5D%5Bkey%5D=" + n +
        "&sfm%5BserviceClassificationWrapper%5D%5Bnaics%5D%5B0%5D%5Bvalue%5D=" + n;
    }
    var k = encodeURIComponent(params.keywords || "");
    return SAM_BASE + "&sfm%5BsimpleSearch%5D%5BkeywordRadio%5D=ALL" +
      "&sfm%5BsimpleSearch%5D%5BkeywordTags%5D%5B0%5D%5Bkey%5D=0" +
      "&sfm%5BsimpleSearch%5D%5BkeywordTags%5D%5B0%5D%5Bvalue%5D=" + k;
  }
  function samPsc(code) {  // live-verified (7A21 -> 9,535 filtered results)
    var c = encodeURIComponent(code);
    return SAM_BASE + "&sfm%5BserviceClassificationWrapper%5D%5Bpsc%5D%5B0%5D%5Bkey%5D=" + c +
      "&sfm%5BserviceClassificationWrapper%5D%5Bpsc%5D%5B0%5D%5Bvalue%5D=" + c;
  }
  // USAspending keyword search — the verified award-history substitute for
  // FPDS ezSearch (which now redirects to sam.gov and DROPS its query).
  function usaKeyword(term) {
    return "https://www.usaspending.gov/keyword_search/" + encodeURIComponent(term);
  }

  // official census.gov NAICS detail page for a code (parameterized, real)
  function censusNaics(code) {
    var c = encodeURIComponent(code);
    return "https://www.census.gov/naics/?input=" + c + "&year=2022&details=" + c;
  }

  // secondary link inside an existing <a> row (nested anchors are invalid
  // HTML): a keyboard-accessible span that opens its data-href in a new tab.
  function secondaryLink(text, href, cls) {
    return '<span class="cc-2nd ' + (cls || "") + '" role="link" tabindex="0" data-href="' +
      esc(href) + '" title="' + esc(href) + '">' + text + "</span>";
  }
  function wireSecondary(root) {
    [].slice.call(root.querySelectorAll(".cc-2nd[data-href]")).forEach(function (s) {
      function go(ev) { ev.stopPropagation(); ev.preventDefault(); openTab(s.getAttribute("data-href")); }
      s.onclick = go;
      s.onkeydown = function (ev) { if (ev.key === "Enter" || ev.key === " ") go(ev); };
    });
  }

  function analystPanel(m, opts) {
    var a = m.analyst;
    if (!a) return null;
    var panel = el('<section class="cc-panel cc-analyst"></section>');

    var pill = a.status === "APPROVED"
      ? '<span class="cc-status ok">Approved' + (a.status_date ? " · " + esc(a.status_date) : "") + "</span>"
      : '<span class="cc-status">Draft · from intake research</span>';
    panel.appendChild(el('<div class="cc-a-head"><div><div class="cc-a-title">Analyst layer</div>' +
      '<div class="cc-a-sub">The human gate; refine with sales, or approve as inferred. ' +
      'Click any term or code for its grounding and search launches.</div></div>' +
      pill + "</div>"));

    var strip = el('<div class="cc-rationale" hidden></div>');
    var workshop = el('<div class="cc-workshop" hidden></div>');

    function openWorkshop(anchorNaics) {
      if (!opts.openWorkshop) {
        showStrip("Analyst workshop", "The full editing workshop opens in the Command Center " +
          "(this preview harness renders read-only).", []);
        return;
      }
      if (workshop.hidden) {
        workshop.hidden = false;
        if (!workshop.__mounted) { opts.openWorkshop(workshop); workshop.__mounted = true; }
      }
      var target = anchorNaics ? workshop.querySelector("#naics-workshop-anchor") : workshop;
      (target || workshop).scrollIntoView({ behavior: "smooth", block: "start" });
    }

    // one strip, replaced per click: label + grounded text + action links
    function showStrip(label, text, actions, context) {
      strip.hidden = false;
      var acts = (actions || []).map(function (x) {
        return x.href
          ? '<a class="cc-mini srclink" href="' + esc(x.href) + '" target="_blank" rel="noopener">' + esc(x.label) + " ↗</a>"
          : '<button class="cc-mini" data-strip-act="' + esc(x.act) + '">' + esc(x.label) + "</button>";
      }).join(" ");
      strip.innerHTML = '<span class="cc-rat-label">' + esc(label) + "</span>" +
        '<span class="cc-rat-text">' + text + "</span>" +
        (context ? '<span class="cc-rat-context">Review context: ' + esc(context) + "</span>" : "") +
        (acts ? '<span class="cc-strip-actions">' + acts + "</span>" : "");
      [].slice.call(strip.querySelectorAll("[data-strip-act]")).forEach(function (b) {
        b.onclick = function () { openWorkshop(b.getAttribute("data-strip-act") === "naics"); };
      });
      strip.scrollIntoView({ behavior: "smooth", block: "nearest" });
    }

    function entityRow(label, items, chipCls) {
      var row = el('<div class="cc-ent-row"><div class="cc-ent-label">' + esc(label) +
        '</div><div class="cc-ent-chips"></div></div>');
      var flow = row.querySelector(".cc-ent-chips");
      items.forEach(function (e) {
        // chips link their rationale's OWN source; resellers without one fall
        // back to the verified seller-site map. No match -> unlinked.
        var chipHref = e.source || e.site || null;
        var srcA = chipHref
          ? ' <a class="cc-chip-src" href="' + esc(chipHref) +
            '" target="_blank" rel="noopener" title="' + esc(chipHref) + '">↗</a>'
          : "";
        var c = el('<button class="cc-achip ' + chipCls + '">' + esc(e.name) + srcA + "</button>");
        var a = c.querySelector(".cc-chip-src");
        if (a) a.onclick = function (ev) { ev.stopPropagation(); };
        c.onclick = function () {
          showStrip("Rationale · " + e.name,
            esc(e.rationale) + (e.source ? ' <a href="' + esc(e.source) +
              '" target="_blank" rel="noopener" class="srclink">source</a>' : "") +
            (e.site ? ' <a href="' + esc(e.site) +
              '" target="_blank" rel="noopener" class="srclink">official site</a>' : ""),
            [{ label: "Search SAM.gov", href: samSearch({ keywords: e.name }) },
             { label: "Award history (USAspending)", href: usaKeyword(e.name) }]);
        };
        flow.appendChild(c);
      });
      var add = el('<button class="cc-add">+ add</button>');
      add.onclick = function () { openWorkshop(false); };
      flow.appendChild(add);
      return row;
    }

    panel.appendChild(entityRow("Products", a.entities.products, "navy"));
    panel.appendChild(entityRow("Competitors", a.entities.competitors, "navy"));
    panel.appendChild(entityRow("Resellers", a.entities.resellers, "neutral"));
    panel.appendChild(strip);

    // keyword families: summary chips + working rows, every term explainable
    panel.appendChild(el('<div class="cc-a-label">Keyword families</div>'));
    var kfsum = el('<div class="cc-chipflow"></div>');
    function familyStrip(f) {
      var body = (f.terms || []).map(function (t) {
        return '<span class="cc-kwline"><b>' + esc(t.term) + "</b>" +
          (t.rationale ? " · " + esc(t.rationale) : "") +
          ' <a class="srclink" href="' + samSearch({ keywords: t.term }) +
          '" target="_blank" rel="noopener">SAM ↗</a>' +
          (t.source ? ' <a class="srclink" href="' + esc(t.source) +
            '" target="_blank" rel="noopener">source ↗</a>' : "") +
          "</span>";
      }).join("");
      showStrip(f.term + " · " + f.count + " keywords", body,
        [{ label: "Refine in workshop", act: "kw" }]);
    }
    (a.keyword_families || []).forEach(function (f) {
      var chip = el('<button class="cc-achip outline">' + esc(f.term) + " · " + f.count + "</button>");
      chip.onclick = function () { familyStrip(f); };
      kfsum.appendChild(chip);
    });
    panel.appendChild(kfsum);
    var kfrows = el('<div class="cc-worktable"></div>');
    (a.keyword_families || []).forEach(function (f) {
      // additive: a real parameterized live-search link on the primary term
      var primary = (f.terms && f.terms[0] && f.terms[0].term) || f.term;
      var row = el('<div class="cc-workrow click"><span class="cc-wr-term">' + esc(f.term) +
        '</span><span class="cc-wr-desc">' + esc(f.description) +
        '</span><a class="cc-wr-live srclink" href="' + samSearch({ keywords: primary }) +
        '" target="_blank" rel="noopener" title="sam.gov search: ' + esc(primary) + '">search live ↗</a>' +
        '<span class="cc-wr-tag">KEYWORD</span></div>');
      row.querySelector(".cc-wr-live").onclick = function (ev) { ev.stopPropagation(); };
      row.onclick = function () { familyStrip(f); };
      kfrows.appendChild(row);
    });
    panel.appendChild(kfrows);

    // NAICS working rows: click -> official title, grounding, review context,
    // and live search launches for the code
    panel.appendChild(el('<div class="cc-a-label">NAICS</div>'));
    var nrows = el('<div class="cc-worktable"></div>');
    (a.naics || []).forEach(function (n) {
      // additive: the code is an anchor to its official census.gov detail page
      var row = el('<div class="cc-workrow click"><a class="cc-wr-code rid" href="' +
        esc(censusNaics(n.code)) + '" target="_blank" rel="noopener" title="census.gov NAICS ' +
        esc(n.code) + '">' + esc(n.code) +
        '</a><span class="cc-wr-title">' + esc(n.title) +
        '</span><span class="cc-wr-desc">' + esc(n.why) +
        '</span><span class="cc-wr-tag">NAICS</span></div>');
      row.querySelector(".cc-wr-code").onclick = function (ev) { ev.stopPropagation(); };
      row.onclick = function () {
        showStrip("NAICS " + n.code + " · " + (n.title || "no official title on file") +
          (n.role ? " · " + n.role : ""),
          esc(n.why || "No code-specific rationale in the packet; inspect it in the workshop."),
          [{ label: "NAICS definition (census.gov)", href: censusNaics(n.code) },
           { label: "Open live opps (SAM.gov)", href: samSearch({ naics: n.code }) },
           { label: "Inspect in workshop", act: "naics" }],
          n.context);
      };
      nrows.appendChild(row);
    });
    panel.appendChild(nrows);

    // PSC working rows (code-only where the shipped map has no title)
    if ((a.psc || []).length) {
      panel.appendChild(el('<div class="cc-a-label">PSC · observed in evidence</div>'));
      var prows = el('<div class="cc-worktable"></div>');
      (a.psc || []).forEach(function (p) {
        // additive: verified PSC-filtered live-opps deep link (sfm form)
        var row = el('<div class="cc-workrow click"><span class="cc-wr-code mono">' + esc(p.code) +
          '</span><span class="cc-wr-title">' + esc(p.title || "") +
          '</span><span class="cc-wr-desc">' + (p.count ? esc(p.count) + " records" : "") +
          '</span><a class="cc-wr-live srclink" href="' + esc(samPsc(p.code)) +
          '" target="_blank" rel="noopener" title="sam.gov opportunities, PSC ' + esc(p.code) +
          '">open live opps ↗</a>' +
          '<span class="cc-wr-tag">PSC</span></div>');
        row.querySelector(".cc-wr-live").onclick = function (ev) { ev.stopPropagation(); };
        row.onclick = function () {
          showStrip("PSC " + p.code,
            esc((p.count || 0) + " evidence records carry this product-service code." +
              (p.title ? "" : " No authoritative title map is shipped; the code renders as sourced.")),
            [{ label: "Open live opps (SAM.gov)", href: samPsc(p.code) }]);
        };
        prows.appendChild(row);
      });
      panel.appendChild(prows);
    }

    // boundary layer: reasons + enactment through the real workshop
    if ((a.boundary || []).length) {
      panel.appendChild(el('<div class="cc-a-label boundary">Marginal calls, your decision</div>'));
      var brows = el('<div class="cc-worktable"></div>');
      (a.boundary || []).forEach(function (b) {
        var nm = b.is_code ? '<span class="cc-wr-code mono">' + esc(b.name) + "</span>"
          : '<span class="cc-wr-term muted">' + esc(b.name) + "</span>";
        var row = el('<div class="cc-workrow boundary click">' + nm +
          '<span class="cc-wr-desc">' + esc(b.reason) + "</span>" +
          '<span class="cc-bdec"><button class="cc-mini" data-dec="in">Include</button>' +
          '<button class="cc-mini" data-dec="out">Exclude</button></span></div>');
        row.onclick = function (e) {
          if (e.target.closest("[data-dec]")) {
            openWorkshop(b.kind === "naics");
            return;
          }
          showStrip((b.is_code ? "NAICS " : "") + b.name + " · marginal",
            esc(b.reason) + (b.confidence != null ? " (confidence " + b.confidence + ")" : ""),
            [{ label: "Search SAM.gov", href: b.is_code ? samSearch({ naics: b.name }) : samSearch({ keywords: b.name }) },
             { label: "Decide in workshop", act: b.kind === "naics" ? "naics" : "kw" }]);
        };
        brows.appendChild(row);
      });
      panel.appendChild(brows);
    }

    // the REAL analyst workshop (legacy editor) mounts on a light working
    // sheet inside the dark panel; Revise toggles it
    panel.appendChild(workshop);

    var actions = el('<div class="cc-a-actions"></div>');
    var revise = el('<button class="cc-btn" data-act="revise">Revise</button>');
    revise.onclick = function () {
      if (!workshop.hidden) { workshop.hidden = true; return; }
      openWorkshop(false);
    };
    actions.appendChild(revise);
    actions.appendChild(pressButton(m, opts));
    panel.appendChild(actions);
    return panel;
  }

  // the press button, shared by the full panel and the collapsed summary
  function pressButton(m, opts) {
    var a = m.analyst, c = (m.__ceremony || {});
    var label = c.phase === "failed" ? "Re-press" : a.press.label;
    var press = el('<button class="cc-press" data-act="press">' +
      '<span class="cc-press-main">' + esc(label) + ' →</span>' +
      '<span class="cc-press-sub">' + esc(a.press.sublabel) + "</span></button>");
    if (c.phase === "pressing") {
      press.classList.add("pressing");
      press.disabled = true;
      press.querySelector(".cc-press-main").innerHTML =
        '<span class="cc-spinner light"></span> Pressing…';
      if (c.subline) press.querySelector(".cc-press-sub").textContent = c.subline;
    }
    press.onclick = function () { if (opts.startCeremony) opts.startCeremony(); };
    return press;
  }

  // one-line APPROVED summary shown while/after the ceremony (Reopen restores)
  function analystSummary(m, opts, state, rerender) {
    var panel = el('<section class="cc-panel cc-analyst-summary"></section>');
    var row = el('<div class="cc-a-head" style="margin-bottom:0"><div>' +
      '<div class="cc-a-title">Analyst layer</div></div>' +
      '<span class="cc-status ok">Approved</span>' +
      '<button class="cc-btn" data-act="reopen">Reopen</button></div>');
    row.querySelector('[data-act="reopen"]').onclick = function () {
      state.analystCollapsed = false;
      rerender();
    };
    row.insertBefore(pressButton(m, opts), row.querySelector('[data-act="reopen"]'));
    panel.appendChild(row);
    return panel;
  }

  // ---------------- 6.5 SOURCE WALL (full surface, grouped, collapsible) ----
  var WALL_GROUPS = ["PROCUREMENT", "FORECASTS", "NEWS & PRESS", "SEARCH", "VEHICLES"];

  function dotFor(s, i) {
    // green pulses stagger (delay = i*137ms mod 2s) so the wall shimmers; amber
    // is steady with the reason as a tooltip (truthfulness law).
    return s.status === "degraded"
      ? '<span class="cc-dot amber" title="' + esc(s.reason || "degraded") + '"></span>'
      : '<span class="cc-dot green" style="animation-delay:' + ((i * 137) % 2000) + 'ms"></span>';
  }

  function sourceWall(sources) {
    if (!sources || !sources.length) return null;
    var panel = el('<section class="cc-panel cc-wall"></section>');
    var head = el('<div class="cc-a-head cc-wall-head">' +
      '<div class="cc-wall-hl"><span class="cc-chev"></span>' +
      '<div><div class="cc-a-title">Sources</div>' +
      '<div class="cc-a-sub">Every federal source the sweep reaches</div></div></div>' +
      '<span class="cc-status">' + sources.length + " sources</span></div>");
    panel.appendChild(head);

    // collapsed one-row dot-shimmer strip
    var strip = el('<div class="cc-wall-strip"></div>');
    sources.forEach(function (s, i) { strip.appendChild(el(dotFor(s, i))); });
    panel.appendChild(strip);

    // expanded grouped grid, one block per category with a 10px tag
    var body = el('<div class="cc-wall-body"></div>');
    WALL_GROUPS.forEach(function (g) {
      var items = sources.filter(function (s) { return s.group === g; });
      if (!items.length) return;
      body.appendChild(el('<div class="cc-wall-group"><span class="cc-grouptag">' + esc(g) +
        '</span><span class="cc-groupcount">' + items.length + "</span></div>"));
      var grid = el('<div class="cc-sourcegrid"></div>');
      items.forEach(function (s, i) {
        var nm = esc(s.name);
        if (s.mono) nm = '<span class="mono">' + nm + "</span>";
        var name = s.url
          ? '<a href="' + esc(s.url) + '" target="_blank" rel="noopener" class="srclink">' + nm + "</a>"
          : nm;
        grid.appendChild(el('<div class="cc-source" data-src="' + esc(s.key) + '">' +
          '<div class="cc-src-name">' + dotFor(s, i) + name + "</div>" +
          (s.coverage ? '<div class="cc-src-cov">' + esc(s.coverage) + "</div>" : "") + "</div>"));
      });
      body.appendChild(grid);
    });
    panel.appendChild(body);

    var collapsed = false;
    try { collapsed = localStorage.getItem("cc-wall-collapsed") === "1"; } catch (e) {}
    function apply() { panel.classList.toggle("collapsed", collapsed); }
    head.onclick = function () {
      collapsed = !collapsed;
      try { localStorage.setItem("cc-wall-collapsed", collapsed ? "1" : "0"); } catch (e) {}
      apply();
    };
    apply();
    return panel;
  }

  // ---------------- 8 TARGETING LAYER PANEL ----------------
  function targetingPanel(t) {
    if (!t) return null;
    var panel = el('<section class="cc-panel" id="cc-targeting"></section>');
    panel.appendChild(el('<div class="cc-a-head"><div><div class="cc-a-title">Targeting</div></div>' +
      '<span class="cc-status">' + esc(t.status) + "</span></div>"));
    var grid = el('<div class="cc-targetgrid"></div>');
    (t.cards || []).forEach(function (c) {
      grid.appendChild(el('<div class="cc-ghost"><div class="cc-ghost-title">' + esc(c.title) +
        '</div><div class="cc-ghost-sub">' + esc(c.sub) + "</div></div>"));
    });
    panel.appendChild(grid);
    return panel;
  }

  // ---------------------------------------------------------------- filter
  function applyFilter(m, filter) {
    if (!filter) return m;
    function hit(entities) {
      return (entities || []).some(function (e) { return e === filter; });
    }
    var clone = Object.assign({}, m);
    clone.calendar = (m.calendar || []).filter(function (c) { return hit(c.entities); });
    clone.ticker = (m.ticker || []).filter(function (t) { return hit(t.entities); });
    return clone;
  }

  var STAGE_NAMES = ["Company research", "Analyst layer approved", "Retrieval · 4 lanes",
    "Sufficiency gate", "Composing report", "Adversarial critique", "Fact validation", "Pressed"];

  function railHead(model, live) {
    return (live ? "Pressing " : "Pressed ") + esc(model.report_title || "Federal Opportunity Pre-Assessment") +
      ' · <span class="mono">' + esc(model.client.name) + "</span>";
  }

  // -------------------------------------------------------- APPROVE CEREMONY
  // Truthfulness law: the sublabel and every rail row come from the poll of
  // the real press status (job log parse server-side); nothing is animated
  // that did not happen. Double-fire is blocked here AND server-side (409).
  function makeCeremony(mount, model, opts, state, rerender) {
    function engineOpts() { return { startCeremony: start }; }

    function updateDom() {
      var c = model.__ceremony || {};
      var panel = mount.querySelector("#cc-rail-panel");
      if (panel && c.stages) {
        var head = panel.querySelector(".cc-rail-head");
        if (head) head.innerHTML = railHead(model, c.phase === "pressing");
        var rows = panel.querySelector(".cc-rail-rows");
        var fresh = pressRail(c.stages);
        if (rows && fresh) rows.replaceWith(fresh);
      }
      [].slice.call(mount.querySelectorAll(".cc-press")).forEach(function (old) {
        old.replaceWith(pressButton(model, engineOpts()));
      });
    }

    function stopTimer() {
      if (state.ceremonyTimer) { clearInterval(state.ceremonyTimer); state.ceremonyTimer = null; }
    }

    function tick() {
      if (!opts.pollStatus) return Promise.resolve();
      return opts.pollStatus().then(function (st) {
        if (!st || !st.stages) return;
        var c = model.__ceremony;
        if (!c) return;
        c.stages = st.stages;
        var active = null, failed = null, activeIx = -1;
        st.stages.forEach(function (s, i) {
          if (s.status === "active" && !active) { active = s; activeIx = i; }
          if (s.status === "failed" && !failed) failed = s;
        });
        c.subline = active ? ("STAGE " + (activeIx + 1) + " OF 8 · " + active.name.toUpperCase())
          : failed ? ("FAILED · " + failed.name.toUpperCase()) : "PRESSED";
        updateDom();
        if (!st.live) {
          stopTimer();
          if (failed) {
            c.phase = "failed";
            c.subline = model.analyst.press.sublabel;
            updateDom();
          } else {
            // complete: refresh the model from the new pack, reveal the hero once
            var done = function (fresh) {
              if (fresh && fresh.client) {
                Object.keys(fresh).forEach(function (k) { model[k] = fresh[k]; });
              }
              model.__ceremony = null;
              state.heroReveal = true;
              rerender();
            };
            (opts.reloadModel ? opts.reloadModel() : Promise.resolve(null)).then(done, function () { done(null); });
          }
        }
      }, function () { /* poll errors: keep trying on the next tick */ });
    }

    function start() {
      var c = model.__ceremony;
      if (c && c.phase === "pressing") return; // double-fire block
      if (!opts.dispatch) { if (opts.onPress) opts.onPress(); return; }
      model.__ceremony = { phase: "pressing", subline: "DISPATCHING…", stages: null };
      state.analystCollapsed = true;
      rerender();
      var rail = mount.querySelector("#cc-rail-panel");
      if (rail) rail.scrollIntoView({ behavior: "smooth", block: "start" }); // ease-scroll
      opts.dispatch().then(function (res) {
        if (!res || (!res.ok && !res.running)) {
          model.__ceremony = { phase: "failed", subline: model.analyst.press.sublabel };
          rerender();
          return;
        }
        tick();
        stopTimer();
        state.ceremonyTimer = setInterval(tick, 2000); // poll cadence 2s
      }, function () {
        model.__ceremony = { phase: "failed", subline: model.analyst.press.sublabel };
        rerender();
      });
    }

    // resume polling a press that is already live (state set by render)
    function resume() {
      tick();
      stopTimer();
      state.ceremonyTimer = setInterval(tick, 2000);
    }
    return { start: start, resume: resume };
  }

  // ---------------------------------------------------------------- render
  function render(mount, model, opts) {
    opts = opts || {};
    mount.classList.add("cc");
    mount.innerHTML = "";
    var state = mount.__ccState || (mount.__ccState = { filter: null, reOpen: false });

    function rerender() { render(mount, model, opts); }
    var ceremony = makeCeremony(mount, model, opts, state, rerender);
    var chrome = { startCeremony: ceremony.start, onPress: opts.onPress,
                   logoDomain: opts.logoDomain, openWorkshop: opts.openWorkshop };

    // reopening the page mid-press: enter the pressing state BEFORE building
    // panels, so the collapsed summary + live rail render on first paint
    var resuming = false;
    if (opts.initialStatus && opts.initialStatus.live
        && !state.ceremonyTimer && !model.__ceremony) {
      model.__ceremony = { phase: "pressing", subline: "RESUMING…",
                           stages: opts.initialStatus.stages || null };
      state.analystCollapsed = true;
      resuming = true;
    }

    var view = applyFilter(model, state.filter);

    // Panel A: identity, chips, scoreboard, ticker, two-pane working area
    var panelA = el('<section class="cc-panel"></section>');
    panelA.appendChild(identityHeader(model, chrome));
    panelA.appendChild(chipRow(model, state, rerender));
    panelA.appendChild(scoreboard(model));
    var tk = ticker(view.ticker, model.assets);
    if (tk) panelA.appendChild(tk);
    var work = el('<section class="cc-work"></section>');
    work.appendChild(keyDates(view.calendar || [], model.assets));
    work.appendChild(reportsRail(model, chrome, state));
    panelA.appendChild(work);
    mount.appendChild(panelA);

    // 5.5 Analyst layer (the human gate); collapses to its APPROVED summary
    var analyst = state.analystCollapsed
      ? analystSummary(model, chrome, state, rerender)
      : analystPanel(model, chrome);
    if (analyst) mount.appendChild(analyst);

    // 6 Press rail (+ 6.5 wall, 7 hero). Stage source preference: live
    // ceremony poll > server press-status (job log) > retrospective pack.
    var c = model.__ceremony;
    var initial = opts.initialStatus;
    var stages = (c && c.stages) || (initial && initial.stages) || model.press_stages;
    var liveNow = (c && c.phase === "pressing") || !!(initial && initial.live);
    if ((!stages || !stages.length) && c) {
      // dispatch just fired, no poll yet: truthful all-pending scaffold
      stages = STAGE_NAMES.map(function (n) { return { name: n, status: "pending", receipt: "" }; });
    }
    if (stages && stages.length) {
      var panelB = el('<section class="cc-panel" id="cc-rail-panel"></section>');
      panelB.appendChild(el('<p class="cc-rail-head">' + railHead(model, liveNow) + "</p>"));
      panelB.appendChild(pressRail(stages));
      mount.appendChild(panelB);
    }

    // 6.5 Source wall directly beneath the press rail (renders regardless —
    // the arsenal exists before the first press)
    var wall = sourceWall(model.sources);
    if (wall) mount.appendChild(wall);

    // 7 Hero report card (rail terminus) — only once a press has produced one
    if (model.hero && stages && stages.length) {
      var heroPanel = el('<section class="cc-panel"></section>');
      var heroNode = hero(model, chrome);
      heroPanel.appendChild(heroNode);
      mount.appendChild(heroPanel);
      if (state.heroReveal) {
        state.heroReveal = false; // smooth-scroll to the hero exactly once
        requestAnimationFrame(function () {
          heroNode.scrollIntoView({ behavior: "smooth", block: "center" });
        });
      }
    }

    // 8 Targeting layer (first-class section, always visible)
    var tgt = targetingPanel(model.targeting);
    if (tgt) mount.appendChild(tgt);

    // resume the 2s poll once when reopening mid-press
    if (resuming) ceremony.resume();
    return mount;
  }

  global.LILAClientView = { render: render };
})(window);
