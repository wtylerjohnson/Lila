/* Events and industry days · adaptive calendar (EVENTS_LANE, 2026-07-27).
 *
 * Design rule, per the frozen spec:
 *   - AGENDA grouped by month is the DEFAULT view: it is the view that works
 *     when events are months apart.
 *   - A DENSITY RIBBON sits above it, one cell per month across the horizon,
 *     each carrying a count and a dot cluster. Clicking a month jumps to it.
 *     Sparse months stay VISIBLE AND EMPTY rather than collapsing, so the
 *     spread reads as coverage, not as gaps.
 *   - A month renders as a MINI GRID only at 4+ items. Below that a grid is
 *     worse than a list, so it is not rendered.
 *   - Registration deadlines are their OWN amber rows, distinct from the
 *     event date. An event whose registration has closed is FLAGGED, never
 *     hidden.
 *   - Every row links out; rows inherit a harvested agency seal when one
 *     exists, a monogram otherwise.
 */
(function (global) {
  "use strict";

  const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
                  "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
  const GRID_THRESHOLD = 4;   // spec: mini grid only at 4 or more items

  function monthKey(iso) { return String(iso || "").slice(0, 7); }

  function parseISO(iso) {
    const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(String(iso || ""));
    return m ? new Date(+m[1], +m[2] - 1, +m[3]) : null;
  }

  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, c => ({
      "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
    }[c]));
  }

  function monogram(host) {
    const words = String(host || "").replace(/[^A-Za-z ]/g, " ")
      .split(/\s+/).filter(Boolean);
    return (words.length > 1
      ? words[0][0] + words[words.length - 1][0]
      : (words[0] || "?").slice(0, 2)).toUpperCase();
  }

  /* One event yields up to TWO timeline rows: the event itself, and its
     registration deadline as a separate amber row. Never merged: the spec
     wants the deadline legible on its own date. */
  function toRows(events, today) {
    const rows = [];
    (events || []).forEach((e, i) => {
      if (e.event_start) {
        rows.push({ kind: "event", date: e.event_start, event: e, id: `e${i}` });
      }
      if (e.registration_deadline) {
        rows.push({
          kind: "deadline", date: e.registration_deadline, event: e,
          id: `d${i}`,
          closed: !!e.registration_closed
            || (parseISO(e.registration_deadline) < today),
        });
      }
    });
    rows.sort((a, b) => a.date.localeCompare(b.date)
      || a.kind.localeCompare(b.kind));
    return rows;
  }

  /* Every month between the first and last row, INCLUDING empty ones: the
     spec is explicit that sparse months stay visible so the ribbon reads as
     coverage rather than as gaps. */
  function monthSpan(rows, today) {
    const keys = rows.map(r => monthKey(r.date)).sort();
    const startKey = keys.length ? keys[0] : monthKey(today.toISOString());
    const endKey = keys.length ? keys[keys.length - 1] : startKey;
    const [sy, sm] = startKey.split("-").map(Number);
    const [ey, em] = endKey.split("-").map(Number);
    const out = [];
    for (let y = sy, m = sm; y < ey || (y === ey && m <= em);) {
      out.push(`${y}-${String(m).padStart(2, "0")}`);
      m += 1; if (m > 12) { m = 1; y += 1; }
    }
    return out;
  }

  function seal(event) {
    if (event.agency_seal) {
      return `<img class="fx-seal" src="${esc(event.agency_seal)}" alt="">`;
    }
    return `<span class="fx-seal fx-seal-mono">${esc(monogram(event.host))}</span>`;
  }

  function rowHtml(row) {
    const e = row.event;
    const d = parseISO(row.date);
    const day = d ? d.getDate() : "--";
    const mon = d ? MONTHS[d.getMonth()] : "";
    const isDeadline = row.kind === "deadline";
    const tag = isDeadline
      ? (row.closed ? "REGISTRATION CLOSED" : "REGISTRATION DEADLINE")
      : "EVENT";
    const where = e.is_virtual ? "Virtual" : (e.location || "");
    return `
      <a class="fx-row${isDeadline ? " fx-row-deadline" : ""}${row.closed ? " fx-row-closed" : ""}"
         href="${esc(e.url)}" target="_blank" rel="noopener">
        <span class="fx-date"><b>${day}</b><i>${mon}</i></span>
        ${seal(e)}
        <span class="fx-body">
          <span class="fx-tag">${tag}</span>
          <span class="fx-name">${esc(e.name)}</span>
          <span class="fx-meta">${esc(e.host)}${where ? " · " + esc(where) : ""}</span>
          <span class="fx-why">${esc(e.relevance || "")}</span>
        </span>
      </a>`;
  }

  function gridHtml(monthRows, key) {
    const [y, m] = key.split("-").map(Number);
    const first = new Date(y, m - 1, 1);
    const days = new Date(y, m, 0).getDate();
    const byDay = {};
    monthRows.forEach(r => {
      const d = parseISO(r.date);
      if (d) (byDay[d.getDate()] = byDay[d.getDate()] || []).push(r);
    });
    let cells = "";
    for (let i = 0; i < first.getDay(); i++) cells += `<span class="fx-cell fx-cell-pad"></span>`;
    for (let day = 1; day <= days; day++) {
      const items = byDay[day] || [];
      const cls = items.length
        ? (items.some(r => r.kind === "deadline") ? " fx-cell-deadline" : " fx-cell-event")
        : "";
      cells += `<span class="fx-cell${cls}"><i>${day}</i>${
        items.length ? `<b>${items.length}</b>` : ""}</span>`;
    }
    return `<div class="fx-grid" role="grid">${cells}</div>`;
  }

  function render(container, events, opts) {
    const options = opts || {};
    const today = options.today ? parseISO(options.today) : new Date();
    const rows = toRows(events, today);
    if (!rows.length) {
      container.innerHTML = `<div class="fx-empty">No verified events in the
        current horizon. Events are only shown when a live-verified source
        URL exists.</div>`;
      return;
    }
    const months = monthSpan(rows, today);
    const byMonth = {};
    months.forEach(k => { byMonth[k] = []; });
    rows.forEach(r => { (byMonth[monthKey(r.date)] ||= []).push(r); });

    const ribbon = months.map(k => {
      const items = byMonth[k] || [];
      const [y, m] = k.split("-").map(Number);
      const dots = items.slice(0, 6).map(r =>
        `<i class="fx-dot${r.kind === "deadline" ? " fx-dot-deadline" : ""}"></i>`
      ).join("");
      return `<button class="fx-mcell${items.length ? "" : " fx-mcell-empty"}"
        data-month="${k}" title="${MONTHS[m - 1]} ${y}: ${items.length} item(s)">
        <span class="fx-mlabel">${MONTHS[m - 1]}</span>
        <span class="fx-mcount">${items.length || ""}</span>
        <span class="fx-dots">${dots}</span></button>`;
    }).join("");

    const body = months.map(k => {
      const items = byMonth[k] || [];
      const [y, m] = k.split("-").map(Number);
      const heading = `<h4 class="fx-month" id="fx-m-${k}">${MONTHS[m - 1]} ${y}
        <span class="fx-mnote">${items.length || "no"} item${items.length === 1 ? "" : "s"}</span></h4>`;
      if (!items.length) {
        // Sparse months stay VISIBLE and empty: coverage, not gaps.
        return `${heading}<div class="fx-quiet">No events this month.</div>`;
      }
      // Mini grid ONLY at the threshold; below it the list is better.
      const grid = items.length >= GRID_THRESHOLD ? gridHtml(items, k) : "";
      return heading + grid + items.map(rowHtml).join("");
    }).join("");

    container.innerHTML = `
      <div class="fx-wrap" data-view="agenda">
        <div class="fx-head">
          <div class="fx-ribbon">${ribbon}</div>
          <button class="fx-toggle" type="button">Grid view</button>
        </div>
        <div class="fx-agenda">${body}</div>
      </div>`;

    container.querySelectorAll(".fx-mcell").forEach(btn => {
      btn.addEventListener("click", () => {
        const target = container.querySelector(`#fx-m-${btn.dataset.month}`);
        if (target) target.scrollIntoView({ behavior: "smooth", block: "start" });
      });
    });
    const wrap = container.querySelector(".fx-wrap");
    const toggle = container.querySelector(".fx-toggle");
    toggle.addEventListener("click", () => {
      const grid = wrap.dataset.view === "grid";
      wrap.dataset.view = grid ? "agenda" : "grid";
      toggle.textContent = grid ? "Grid view" : "Agenda view";
    });
  }

  global.LILAEventsCalendar = { render, toRows, monthSpan, GRID_THRESHOLD };
})(typeof window !== "undefined" ? window : globalThis);
