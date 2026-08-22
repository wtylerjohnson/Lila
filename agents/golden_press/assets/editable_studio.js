(() => {
  "use strict";

  if (window.__lilaEditableStudioInitialized) return;
  window.__lilaEditableStudioInitialized = true;

  const root = document.querySelector("#signal-board");
  const reportTools = document.querySelector("[data-editor-ui]");
  if (!root || !reportTools) return;

  const STUDIO_SCHEMA = "lila.layout-studio";
  const STUDIO_VERSION = 2;
  const MAX_HISTORY = 60;
  const MAX_IMAGE_BYTES = 8 * 1024 * 1024;
  const UNIT_MIME = "application/x-lila-studio-unit";
  const BLOCK_MIME = "application/x-lila-studio-block";
  const clientName = String(
    document.documentElement.dataset.studioClient ||
    root.querySelector(".sb-brand-word")?.textContent ||
    "Federal"
  ).replace(/\s+/g, " ").trim();
  const reportId = document.documentElement.dataset.reportId || "lila-editable-studio";
  const storageKey = `lila:layout:${reportId}:v2`;
  const footer = root.querySelector(":scope > footer.sb-footer");
  const nav = root.querySelector(":scope > header.sb-utility nav.sb-nav");
  const embeddedStateId = "lila-studio-state";

  let studioOn = false;
  let inlineEditStartedByStudio = false;
  let selected = null;
  let selectedUnitId = null;
  let savedSelectionRange = null;
  let draggedUnitId = null;
  let draggedBlockId = null;
  let dirty = false;
  let changeTimer = null;
  let history = [];
  let historyIndex = -1;
  let baselineState = null;
  let baseFingerprint = "";
  let panel = null;
  let launcher = null;
  let studioStatus = null;
  let initialUnitOrder = [];

  const $ = (selector, scope = document) => scope.querySelector(selector);
  const $$ = (selector, scope = document) => [...scope.querySelectorAll(selector)];
  const deepClone = (value) => JSON.parse(JSON.stringify(value));
  const unitNodes = (unit) => {
    if (!unit) return [];
    const id = unit.dataset.studioUnitId;
    return [unit, ...$$(`:scope > [data-studio-companion-of="${CSS.escape(id)}"]`, root)];
  };
  const allUnits = () => $$(":scope > [data-studio-unit-id]", root);
  const unitById = (id) => $(`:scope > [data-studio-unit-id="${CSS.escape(id)}"]`, root);

  function hashSource(source) {
    let hash = 2166136261;
    for (let index = 0; index < source.length; index += 1) {
      hash ^= source.charCodeAt(index);
      hash = Math.imul(hash, 16777619);
    }
    return (hash >>> 0).toString(36);
  }

  function uniqueId(prefix = "studio") {
    const random = globalThis.crypto?.randomUUID?.() || `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`;
    return `${prefix}-${random.replace(/[^a-z0-9-]/gi, "").toLowerCase()}`;
  }

  function safeText(value) {
    return String(value || "").replace(/\s+/g, " ").trim();
  }

  function safeHref(value) {
    const href = String(value || "").trim();
    if (!href) return "";
    if (/^#[-_a-z0-9:.]+$/i.test(href)) return href;
    if (/^mailto:[^<>\s]+$/i.test(href)) return href;
    try {
      const parsed = new URL(href, location.href);
      if (!["http:", "https:"].includes(parsed.protocol)) return "";
      return parsed.href;
    } catch {
      return "";
    }
  }

  function sectionLabel(unit) {
    if (!unit) return "Report section";
    if (unit.dataset.studioUnitLabel) return unit.dataset.studioUnitLabel;
    const heading = unit.querySelector("h1,h2,h3");
    const fallback = [...unit.querySelectorAll("[aria-label]")].find((node) => !node.closest("[data-studio-unit-controls]"));
    const label = safeText(heading?.textContent || fallback?.getAttribute("aria-label") || unit.getAttribute("aria-label"));
    if (label) return label.slice(0, 88);
    if (unit.matches(".sb-reading-guide")) return "Federal-space vocabulary";
    if (unit.matches(".sb-recommended")) return "What to do next";
    return unit.dataset.studioUnitId || "Report section";
  }

  function stampUnits() {
    const hero = root.querySelector(":scope > .sb-hero");
    const agencyRail = root.querySelector(":scope > .sb-agency-rail");
    const signalStrip = root.querySelector(":scope > .sb-signal-strip");
    const readingGuide = root.querySelector(":scope > .sb-reading-guide");
    const recommended = root.querySelector(":scope > .sb-recommended");

    if (hero) {
      hero.dataset.studioUnitId ||= "studio-hero";
      hero.dataset.studioUnitLabel ||= "Cover and headline metrics";
      if (agencyRail) agencyRail.dataset.studioCompanionOf = hero.dataset.studioUnitId;
      if (signalStrip) signalStrip.dataset.studioCompanionOf = hero.dataset.studioUnitId;
    }
    if (readingGuide) {
      readingGuide.dataset.studioUnitId ||= "studio-reading-guide";
      readingGuide.dataset.studioUnitLabel ||= "Federal-space vocabulary";
    }
    $$(":scope > section.sb-band[id]", root).forEach((section) => {
      section.dataset.studioUnitId ||= section.id;
    });
    if (recommended) {
      recommended.dataset.studioUnitId ||= "studio-recommended";
      recommended.dataset.studioUnitLabel ||= "What to do next";
    }
    $$(":scope > [data-studio-custom-section]", root).forEach((section) => {
      section.dataset.studioUnitId ||= section.id || uniqueId("custom-section");
    });
  }

  function ensureLinkId(link) {
    if (!link || link.closest(".sb-news-tape") || link.closest("[data-editor-transient]")) return "";
    if (!link.dataset.studioLinkId) link.dataset.studioLinkId = uniqueId("studio-link");
    return link.dataset.studioLinkId;
  }

  function ensureStyleKey(target) {
    if (!target) return "";
    if (!target.dataset.studioStyleKey) {
      if (target.dataset.editId) target.dataset.studioStyleKey = `text:${target.dataset.editId}`;
      else if (target.dataset.logoSlotId) target.dataset.studioStyleKey = `image:${target.dataset.logoSlotId}`;
      else if (target.dataset.logoId) target.dataset.studioStyleKey = `image:${target.dataset.logoId}`;
      else if (target.dataset.studioBlockId) target.dataset.studioStyleKey = `block:${target.dataset.studioBlockId}`;
      else if (target.dataset.studioUnitId) target.dataset.studioStyleKey = `section:${target.dataset.studioUnitId}`;
      else target.dataset.studioStyleKey = uniqueId("style");
      target.dataset.studioOriginalStyle = target.getAttribute("style") || "";
    }
    return target.dataset.studioStyleKey;
  }

  function cleanClone(node) {
    const clone = node.cloneNode(true);
    clone.removeAttribute?.("contenteditable");
    clone.removeAttribute?.("spellcheck");
    clone.removeAttribute?.("role");
    clone.removeAttribute?.("draggable");
    clone.removeAttribute?.("data-image-drag-source");
    clone.removeAttribute?.("data-studio-selected");
    clone.classList?.remove("is-studio-selected", "is-editor-selected", "is-dragging", "is-dragover");
    clone.querySelectorAll?.("[data-editor-transient],[data-studio-unit-controls]").forEach((item) => item.remove());
    clone.querySelectorAll?.("[contenteditable],[spellcheck],[draggable],[data-image-drag-source]").forEach((item) => {
      item.removeAttribute("contenteditable");
      item.removeAttribute("spellcheck");
      item.removeAttribute("role");
      item.removeAttribute("draggable");
      item.removeAttribute("data-image-drag-source");
    });
    clone.querySelectorAll?.(".is-studio-selected,.is-editor-selected,.is-dragging,.is-dragover,.is-studio-dragover").forEach((item) => {
      item.classList.remove("is-studio-selected", "is-editor-selected", "is-dragging", "is-dragover", "is-studio-dragover");
      item.removeAttribute("data-studio-selected");
    });
    return clone;
  }

  function parseSafeNode(html) {
    const template = document.createElement("template");
    template.innerHTML = String(html || "").trim();
    template.content.querySelectorAll("script,iframe,object,embed,base,meta,link").forEach((node) => node.remove());
    template.content.querySelectorAll("*").forEach((node) => {
      [...node.attributes].forEach((attribute) => {
        if (/^on/i.test(attribute.name)) node.removeAttribute(attribute.name);
      });
    });
    return template.content.firstElementChild;
  }

  function captureState() {
    const units = allUnits();
    const customSections = units
      .filter((unit) => unit.hasAttribute("data-studio-custom-section"))
      .map((unit) => cleanClone(unit).outerHTML);
    const blocks = $$(".studio-user-block", root)
      .filter((block) => !block.closest("[data-studio-custom-section]"))
      .map((block) => ({
        sectionId: block.closest("[data-studio-unit-id]")?.dataset.studioUnitId || "",
        html: cleanClone(block).outerHTML,
      }));
    const styles = Object.fromEntries(
      $$("[data-studio-style-key]", root).map((target) => [
        target.dataset.studioStyleKey,
        {
          style: target.getAttribute("style") || "",
          sized: target.dataset.studioSized || "",
          span: target.dataset.studioGridSpan || "",
        },
      ])
    );
    const links = Object.fromEntries(
      $$("[data-studio-link-id][data-studio-link-edited='true']", root).map((link) => [
        link.dataset.studioLinkId,
        {
          href: link.getAttribute("href") || "",
          target: link.getAttribute("target") || "",
          rel: link.getAttribute("rel") || "",
        },
      ])
    );
    const fieldHtml = Object.fromEntries(
      $$("[data-edit-id]", root).map((field) => [field.dataset.editId, field.innerHTML])
    );
    const images = Object.fromEntries(
      $$("[data-studio-image-edited='true']", root)
        .map((target) => [imageIdentity(target), captureImageValue(target)])
        .filter(([id]) => id)
    );
    return {
      version: STUDIO_VERSION,
      order: units.map((unit) => unit.dataset.studioUnitId),
      hidden: units
        .filter((unit) => unit.dataset.studioHidden === "true")
        .map((unit) => unit.dataset.studioUnitId),
      customSections,
      blocks,
      styles,
      links,
      fieldHtml,
      images,
    };
  }

  function fingerprintForState(state) {
    const payload = {
      order: state.order,
      unitCount: state.order.length,
      editIds: $$("[data-edit-id]", root).map((node) => node.dataset.editId).sort(),
      logoIds: $$("[data-logo-slot-id],img[data-logo-id]", root)
        .map((node) => node.dataset.logoSlotId || node.dataset.logoId)
        .sort(),
      evidence: $$(".sb-evidence-row", root).map((row) => safeText(row.querySelector(".sb-evidence-record")?.textContent)),
    };
    return hashSource(JSON.stringify(payload));
  }

  function stateScript() {
    let script = document.getElementById(embeddedStateId);
    if (!script) {
      script = document.createElement("script");
      script.type = "application/json";
      script.id = embeddedStateId;
      document.body.append(script);
    }
    return script;
  }

  function readEmbeddedEnvelope() {
    const script = document.getElementById(embeddedStateId);
    if (!script?.textContent?.trim()) return null;
    try {
      const parsed = JSON.parse(script.textContent);
      if (parsed?.schema !== STUDIO_SCHEMA || parsed?.version !== STUDIO_VERSION) {
        throw new Error("unsupported embedded Studio state schema");
      }
      return parsed;
    } catch (error) {
      throw new Error(`embedded Studio state is invalid: ${error?.message || String(error)}`);
    }
  }

  function writeEmbeddedEnvelope({ rebase = false } = {}) {
    const current = captureState();
    const envelope = {
      schema: STUDIO_SCHEMA,
      version: STUDIO_VERSION,
      reportId,
      baseFingerprint,
      savedAt: new Date().toISOString(),
      base: rebase ? current : baselineState,
      state: current,
    };
    stateScript().textContent = JSON.stringify(envelope).replace(/</g, "\\u003c");
    return envelope;
  }

  function applyCustomSections(state) {
    const desired = new Map();
    (state.customSections || []).forEach((html) => {
      const candidate = parseSafeNode(html);
      if (!candidate?.hasAttribute("data-studio-custom-section")) return;
      const id = candidate.dataset.studioUnitId || candidate.id;
      if (id) desired.set(id, candidate);
    });
    $$(":scope > [data-studio-custom-section]", root).forEach((section) => {
      if (!desired.has(section.dataset.studioUnitId)) section.remove();
    });
    desired.forEach((candidate, id) => {
      const existing = unitById(id);
      if (existing?.hasAttribute("data-studio-custom-section")) existing.replaceWith(candidate);
      else if (!existing) root.insertBefore(candidate, footer);
    });
    stampUnits();
  }

  function applyBlocks(state) {
    $$(".studio-user-block", root)
      .filter((block) => !block.closest("[data-studio-custom-section]"))
      .forEach((block) => block.remove());
    (state.blocks || []).forEach((entry) => {
      const section = unitById(entry.sectionId);
      const block = parseSafeNode(entry.html);
      if (!section || !block?.classList.contains("studio-user-block")) return;
      let area = section.querySelector(":scope > .studio-user-blocks");
      if (!area) {
        area = document.createElement("div");
        area.className = "studio-user-blocks";
        area.dataset.studioUserBlocks = "";
        section.append(area);
      }
      area.append(block);
    });
    $$(":scope > .studio-user-blocks:empty", root).forEach((area) => area.remove());
  }

  function applyStyles(state) {
    $$("[data-studio-style-key]", root).forEach((target) => {
      target.setAttribute("style", target.dataset.studioOriginalStyle || "");
      delete target.dataset.studioSized;
      delete target.dataset.studioGridSpan;
    });
    Object.entries(state.styles || {}).forEach(([key, value]) => {
      const target = $(`[data-studio-style-key="${CSS.escape(key)}"]`, root);
      if (!target) return;
      target.setAttribute("style", value.style || "");
      if (value.sized) target.dataset.studioSized = value.sized;
      if (value.span) target.dataset.studioGridSpan = value.span;
    });
  }

  function applyLinks(state) {
    $$("[data-studio-link-id]", root).forEach((link) => {
      if (link.dataset.studioOriginalHref != null) link.setAttribute("href", link.dataset.studioOriginalHref);
      if (link.dataset.studioOriginalTarget) link.setAttribute("target", link.dataset.studioOriginalTarget);
      else link.removeAttribute("target");
      if (link.dataset.studioOriginalRel) link.setAttribute("rel", link.dataset.studioOriginalRel);
      else link.removeAttribute("rel");
      delete link.dataset.studioLinkEdited;
    });
    Object.entries(state.links || {}).forEach(([id, value]) => {
      const link = $(`[data-studio-link-id="${CSS.escape(id)}"]`, root);
      if (!link) return;
      link.setAttribute("href", value.href || "#");
      if (value.target) link.setAttribute("target", value.target);
      else link.removeAttribute("target");
      if (value.rel) link.setAttribute("rel", value.rel);
      else link.removeAttribute("rel");
      link.dataset.studioLinkEdited = "true";
    });
  }

  function applyFieldHtml(state) {
    Object.entries(state.fieldHtml || {}).forEach(([id, html]) => {
      const field = $(`[data-edit-id="${CSS.escape(id)}"]`, root);
      if (field) field.innerHTML = html;
    });
  }

  function applyImages(state) {
    Object.entries(state.images || {}).forEach(([id, value]) => {
      const target = $(`[data-logo-slot-id="${CSS.escape(id)}"],img[data-logo-id="${CSS.escape(id)}"]`, root);
      if (!target || !value) return;
      if (target.matches("img")) {
        target.src = value.src || "";
        target.alt = value.alt || "";
      } else {
        target.innerHTML = value.html || "";
      }
      target.dataset.studioImageEdited = "true";
    });
  }

  function applyOrder(order) {
    const seen = new Set();
    (order || []).forEach((id) => {
      if (seen.has(id)) return;
      seen.add(id);
      const unit = unitById(id);
      if (!unit) return;
      unitNodes(unit).forEach((node) => root.insertBefore(node, footer));
    });
    allUnits().forEach((unit) => {
      if (seen.has(unit.dataset.studioUnitId)) return;
      unitNodes(unit).forEach((node) => root.insertBefore(node, footer));
    });
  }

  function applyHidden(hidden) {
    const hiddenSet = new Set(hidden || []);
    allUnits().forEach((unit) => {
      const isHidden = hiddenSet.has(unit.dataset.studioUnitId);
      unit.dataset.studioHidden = String(isHidden);
      unit.hidden = isHidden;
      unitNodes(unit).slice(1).forEach((companion) => {
        companion.dataset.studioCompanionHidden = String(isHidden);
        companion.hidden = isHidden;
      });
    });
  }

  function applyState(state, { announceChange = false } = {}) {
    if (!state || state.version !== STUDIO_VERSION) return;
    const openDetails = new Map($$("details[id],details", root).map((item, index) => [item.id || `details-${index}`, item.open]));
    applyCustomSections(state);
    applyBlocks(state);
    stampUnits();
    applyOrder(state.order);
    applyHidden(state.hidden);
    applyFieldHtml(state);
    applyImages(state);
    applyStyles(state);
    applyLinks(state);
    ensureUnitControls();
    syncReportPresentation();
    enableDynamicEditing(studioOn);
    $$(".sb-news-dup [data-studio-link-id]", root).forEach((link) => link.removeAttribute("data-studio-link-id"));
    $$("details[id],details", root).forEach((item, index) => {
      const key = item.id || `details-${index}`;
      if (openDetails.has(key)) item.open = openDetails.get(key);
    });
    renderSectionList();
    clearSelection();
    if (announceChange) announce("Layout restored");
  }

  function renumberBands() {
    const visibleBands = allUnits().filter((unit) => unit.matches("section.band[id]") && unit.dataset.studioHidden !== "true");
    visibleBands.forEach((section, index) => {
      const number = String(index + 1).padStart(2, "0");
      const numberField =
        section.querySelector(":scope > .b-no > [data-edit-id]") ||
        section.querySelector(":scope > .b-no");
      if (numberField) numberField.textContent = number;
    });
  }

  function syncNavigation() {
    if (!nav) return;
    const anchors = $$(":scope > a[href^='#']", nav);
    const byId = new Map(anchors.map((anchor) => [anchor.getAttribute("href").slice(1), anchor]));
    allUnits().forEach((unit) => {
      const anchor = byId.get(unit.id);
      if (!anchor) return;
      anchor.hidden = unit.dataset.studioHidden === "true";
      nav.append(anchor);
    });
    anchors.forEach((anchor) => {
      const id = anchor.getAttribute("href").slice(1);
      if (!unitById(id)) nav.append(anchor);
    });
  }

  function syncReportPresentation() {
    renumberBands();
    syncNavigation();
  }

  function historyState() {
    return captureState();
  }

  function updateHistoryButtons() {
    if (!panel) return;
    const undo = panel.querySelector("[data-studio-action='undo']");
    const redo = panel.querySelector("[data-studio-action='redo']");
    if (undo) undo.disabled = historyIndex <= 0;
    if (redo) redo.disabled = historyIndex >= history.length - 1;
  }

  function invalidateIntegrity() {
    document.documentElement.dataset.studioIntegrity = "analyst-edited-unverified";
  }

  function pushHistory(label = "Layout changed") {
    const state = historyState();
    const serialized = JSON.stringify(state);
    const current = history[historyIndex] ? JSON.stringify(history[historyIndex]) : "";
    if (serialized === current) return;
    history = history.slice(0, historyIndex + 1);
    history.push(deepClone(state));
    if (history.length > MAX_HISTORY) history.shift();
    historyIndex = history.length - 1;
    dirty = true;
    invalidateIntegrity();
    writeEmbeddedEnvelope();
    updateHistoryButtons();
    announce(`${label} · unsaved`);
  }

  function commitSoon(label) {
    clearTimeout(changeTimer);
    changeTimer = setTimeout(() => pushHistory(label), 450);
  }

  function undoLayout() {
    if (historyIndex <= 0) return;
    historyIndex -= 1;
    applyState(deepClone(history[historyIndex]));
    dirty = true;
    invalidateIntegrity();
    writeEmbeddedEnvelope();
    updateHistoryButtons();
    announce("Layout undo");
  }

  function redoLayout() {
    if (historyIndex >= history.length - 1) return;
    historyIndex += 1;
    applyState(deepClone(history[historyIndex]));
    dirty = true;
    invalidateIntegrity();
    writeEmbeddedEnvelope();
    updateHistoryButtons();
    announce("Layout redo");
  }

  function announce(message) {
    if (studioStatus) studioStatus.textContent = message;
  }

  function inlineEditButton() {
    return reportTools.querySelector("[data-action='toggle-edit']");
  }

  function enterStudio() {
    if (studioOn) return;
    studioOn = true;
    document.body.classList.add("studio-mode");
    launcher?.setAttribute("aria-pressed", "true");
    const editButton = inlineEditButton();
    if (editButton?.getAttribute("aria-pressed") !== "true") {
      inlineEditStartedByStudio = true;
      editButton.click();
    } else {
      inlineEditStartedByStudio = false;
    }
    enableDynamicEditing(true);
    renderSectionList();
    announce("Layout Studio active · select a section, text field, link, or image");
    panel?.querySelector(".studio-close")?.focus();
  }

  function exitStudio({ keepInlineEdit = false } = {}) {
    if (!studioOn) return;
    studioOn = false;
    document.body.classList.remove("studio-mode");
    launcher?.setAttribute("aria-pressed", "false");
    enableDynamicEditing(false);
    clearSelection();
    if (inlineEditStartedByStudio && !keepInlineEdit) {
      const editButton = inlineEditButton();
      if (editButton?.getAttribute("aria-pressed") === "true") editButton.click();
    }
    inlineEditStartedByStudio = false;
    announce(dirty ? "Layout Studio closed · unsaved changes remain" : "Layout Studio closed");
  }

  function enableDynamicEditing(on) {
    $$(".studio-user-block [data-edit-id]", root).forEach((field) => {
      if (on) {
        field.contentEditable = "true";
        field.spellcheck = true;
        field.setAttribute("role", "textbox");
      } else {
        field.removeAttribute("contenteditable");
        field.removeAttribute("spellcheck");
        field.removeAttribute("role");
      }
    });
    $$(".studio-user-block img", root).forEach((image) => {
      if (on) image.draggable = true;
      else image.removeAttribute("draggable");
    });
  }

  function ensureUnitControls() {
    allUnits().forEach((unit) => {
      let controls = unit.querySelector(":scope > [data-studio-unit-controls]");
      if (controls) return;
      controls = document.createElement("div");
      controls.dataset.studioUnitControls = "";
      controls.dataset.editorTransient = "";
      controls.setAttribute("aria-label", `Layout controls for ${sectionLabel(unit)}`);
      controls.innerHTML = `
        <button type="button" data-studio-unit-action="select" title="Select section">EDIT</button>
        <button type="button" data-studio-unit-drag draggable="true" title="Drag section" aria-label="Drag section">↕</button>
        <button type="button" data-studio-unit-action="up" title="Move section up" aria-label="Move section up">↑</button>
        <button type="button" data-studio-unit-action="down" title="Move section down" aria-label="Move section down">↓</button>
        <button type="button" data-studio-unit-action="hide" title="Remove section; recover from the drawer" aria-label="Remove section">×</button>`;
      unit.prepend(controls);
    });
  }

  function renderSectionList() {
    if (!panel) return;
    const list = panel.querySelector("[data-studio-section-list]");
    if (!list) return;
    list.replaceChildren();
    allUnits().forEach((unit, index) => {
      const id = unit.dataset.studioUnitId;
      const hidden = unit.dataset.studioHidden === "true";
      const row = document.createElement("div");
      row.className = `studio-section-row${hidden ? " is-hidden" : ""}${selectedUnitId === id ? " is-selected" : ""}`;
      row.dataset.studioSectionRef = id;
      row.draggable = true;
      row.innerHTML = `
        <span class="studio-section-drag" aria-hidden="true">⋮⋮</span>
        <button type="button" class="studio-section-name" data-studio-row-action="select" title="${sectionLabel(unit)}">${String(index + 1).padStart(2, "0")} · ${sectionLabel(unit)}</button>
        <button type="button" data-studio-row-action="up" title="Move up" aria-label="Move ${sectionLabel(unit)} up">↑</button>
        <button type="button" data-studio-row-action="down" title="Move down" aria-label="Move ${sectionLabel(unit)} down">↓</button>
        <button type="button" data-studio-row-action="${hidden ? "restore" : "hide"}" title="${hidden ? "Restore" : "Remove"}" aria-label="${hidden ? "Restore" : "Remove"} ${sectionLabel(unit)}">${hidden ? "◉" : "○"}</button>`;
      list.append(row);
    });
  }

  function unitIndex(id) {
    return allUnits().findIndex((unit) => unit.dataset.studioUnitId === id);
  }

  function moveUnit(id, delta) {
    const units = allUnits();
    const index = units.findIndex((unit) => unit.dataset.studioUnitId === id);
    const targetIndex = index + delta;
    if (index < 0 || targetIndex < 0 || targetIndex >= units.length) return;
    const order = units.map((unit) => unit.dataset.studioUnitId);
    [order[index], order[targetIndex]] = [order[targetIndex], order[index]];
    applyOrder(order);
    syncReportPresentation();
    ensureUnitControls();
    renderSectionList();
    pushHistory(`Moved ${sectionLabel(unitById(id))}`);
    selectElement(unitById(id));
  }

  function moveUnitBefore(sourceId, targetId) {
    if (!sourceId || !targetId || sourceId === targetId) return;
    const order = allUnits().map((unit) => unit.dataset.studioUnitId).filter((id) => id !== sourceId);
    const targetIndex = order.indexOf(targetId);
    if (targetIndex < 0) return;
    order.splice(targetIndex, 0, sourceId);
    applyOrder(order);
    syncReportPresentation();
    ensureUnitControls();
    renderSectionList();
    pushHistory(`Reordered ${sectionLabel(unitById(sourceId))}`);
    selectElement(unitById(sourceId));
  }

  function setUnitHidden(id, hidden) {
    const unit = unitById(id);
    if (!unit) return;
    unit.dataset.studioHidden = String(hidden);
    unit.hidden = hidden;
    unitNodes(unit).slice(1).forEach((companion) => {
      companion.dataset.studioCompanionHidden = String(hidden);
      companion.hidden = hidden;
    });
    syncReportPresentation();
    renderSectionList();
    if (hidden && selectedUnitId === id) clearSelection();
    pushHistory(`${hidden ? "Removed" : "Restored"} ${sectionLabel(unit)}`);
  }

  function restoreAllHidden() {
    allUnits().forEach((unit) => {
      unit.dataset.studioHidden = "false";
      unit.hidden = false;
      unitNodes(unit).slice(1).forEach((companion) => {
        companion.dataset.studioCompanionHidden = "false";
        companion.hidden = false;
      });
    });
    syncReportPresentation();
    renderSectionList();
    pushHistory("Restored all sections");
  }

  function selectedUnit() {
    return selectedUnitId ? unitById(selectedUnitId) : selected?.closest?.("[data-studio-unit-id]") || null;
  }

  function defaultInsertUnit() {
    return selectedUnit() || allUnits().find((unit) => unit.dataset.studioHidden !== "true" && unit.matches(".sb-band")) || allUnits()[0] || null;
  }

  function userBlockArea(unit) {
    if (!unit) return null;
    let area = unit.querySelector(":scope > .studio-user-blocks");
    if (!area) {
      area = document.createElement("div");
      area.className = "studio-user-blocks";
      area.dataset.studioUserBlocks = "";
      unit.append(area);
    }
    return area;
  }

  function blockMarkup(type, blockId) {
    const editBase = `studio-${blockId.replace(/[^a-z0-9-]/gi, "")}`;
    if (type === "text") {
      return `<article class="studio-user-block" data-studio-block-id="${blockId}" data-studio-block-type="text" data-studio-grid-span="12" style="--studio-grid-span:12"><div data-edit-id="${editBase}-text">New text box — click and type.</div></article>`;
    }
    if (type === "callout") {
      return `<aside class="studio-user-block" data-studio-block-id="${blockId}" data-studio-block-type="callout" data-studio-grid-span="12" style="--studio-grid-span:12"><strong data-edit-id="${editBase}-title" data-edit-singleline>Key point</strong><div data-edit-id="${editBase}-copy">Add the context, decision, or recommendation here.</div></aside>`;
    }
    if (type === "link") {
      const linkId = `studio-link-${blockId}`;
      return `<article class="studio-user-block" data-studio-block-id="${blockId}" data-studio-block-type="link" data-studio-grid-span="6" style="--studio-grid-span:6"><a href="https://example.com/" target="_blank" rel="noopener noreferrer" data-studio-link-id="${linkId}" data-studio-original-href="https://example.com/" data-studio-original-target="_blank" data-studio-original-rel="noopener noreferrer" data-studio-link-edited="true"><strong data-edit-id="${editBase}-label" data-edit-singleline>New external link ↗</strong></a><small data-edit-id="${editBase}-context">Replace the URL in the inspector.</small></article>`;
    }
    return `<figure class="studio-user-block" data-studio-block-id="${blockId}" data-studio-block-type="image" data-studio-grid-span="6" style="--studio-grid-span:6"><div class="studio-image-slot" data-logo-slot-id="${editBase}-image" data-studio-image-slot><span>Drop a logo or seal here, or use Choose image.</span></div><figcaption class="studio-image-caption" data-edit-id="${editBase}-caption" data-edit-singleline>Logo or seal caption</figcaption></figure>`;
  }

  function addBlock(type) {
    const unit = defaultInsertUnit();
    if (!unit) return;
    const blockId = uniqueId("user-block");
    const block = parseSafeNode(blockMarkup(type, blockId));
    userBlockArea(unit).append(block);
    enableDynamicEditing(studioOn);
    pushHistory(`Added ${type} block`);
    selectElement(block);
    block.scrollIntoView({ behavior: "smooth", block: "center" });
  }

  function addCustomSection() {
    const sectionId = uniqueId("studio-section");
    const titleId = `${sectionId}-title`;
    const section = document.createElement("section");
    section.className = "band";
    section.id = sectionId;
    section.dataset.studioCustomSection = "";
    section.dataset.studioUnitId = sectionId;
    section.setAttribute("aria-labelledby", titleId);
    section.innerHTML = `
      <div class="b-no"><span data-edit-id="${sectionId}-number" data-edit-singleline>00</span></div>
      <h2 id="${titleId}"><span data-edit-id="${sectionId}-title-text" data-edit-singleline>New report section</span></h2>
      <div class="b-meta"><span data-edit-id="${sectionId}-action" data-edit-singleline>CLIENT-EDITED SECTION</span> · <span data-edit-id="${sectionId}-fact" data-edit-singleline>ADD EVIDENCE AND CONTEXT</span></div>
      <p class="b-lede"><span data-edit-id="${sectionId}-context">Explain what this section shows and why it matters.</span></p>
      <div class="studio-user-blocks" data-studio-user-blocks></div>`;
    root.insertBefore(section, footer);
    stampUnits();
    ensureUnitControls();
    syncReportPresentation();
    enableDynamicEditing(studioOn);
    pushHistory("Added custom section");
    selectElement(section);
    renderSectionList();
    section.scrollIntoView({ behavior: "smooth", block: "start" });
  }

  function remapBlockIds(block) {
    const clone = cleanClone(block);
    const newId = uniqueId("user-block");
    clone.dataset.studioBlockId = newId;
    clone.querySelectorAll("[data-edit-id]").forEach((field, index) => {
      field.dataset.editId = `studio-${newId}-text-${index + 1}`;
    });
    clone.querySelectorAll("[data-logo-slot-id]").forEach((slot, index) => {
      slot.dataset.logoSlotId = `studio-${newId}-image-${index + 1}`;
    });
    clone.querySelectorAll("[data-studio-link-id]").forEach((link, index) => {
      link.dataset.studioLinkId = `studio-${newId}-link-${index + 1}`;
    });
    [clone, ...clone.querySelectorAll("[data-studio-style-key],[data-studio-original-style]")].forEach((target) => {
      delete target.dataset.studioStyleKey;
      delete target.dataset.studioOriginalStyle;
    });
    clone.removeAttribute("data-studio-selected");
    return clone;
  }

  function duplicateSelectedBlock() {
    const block = selected?.closest?.(".studio-user-block");
    if (!block) return;
    const clone = remapBlockIds(block);
    block.after(clone);
    enableDynamicEditing(studioOn);
    pushHistory("Duplicated added block");
    selectElement(clone);
  }

  function deleteSelectedBlock() {
    const block = selected?.closest?.(".studio-user-block");
    if (!block) return;
    const area = block.parentElement;
    block.remove();
    if (area?.matches(".studio-user-blocks") && !area.children.length && !area.closest("[data-studio-custom-section]")) area.remove();
    clearSelection();
    pushHistory("Removed added block");
  }

  function moveSelectedBlock(delta) {
    const block = selected?.closest?.(".studio-user-block");
    if (!block) return;
    const sibling = delta < 0 ? block.previousElementSibling : block.nextElementSibling;
    if (!sibling?.classList.contains("studio-user-block")) return;
    if (delta < 0) sibling.before(block);
    else sibling.after(block);
    pushHistory("Moved added block");
    selectElement(block);
  }

  function selectElement(target) {
    if (!studioOn || !target) return;
    selected?.removeAttribute?.("data-studio-selected");
    selected?.classList?.remove("is-studio-selected");
    allUnits().forEach((unit) => unit.classList.remove("is-studio-selected"));
    selected = target;
    selected.setAttribute?.("data-studio-selected", "true");
    const unit = target.matches?.("[data-studio-unit-id]") ? target : target.closest?.("[data-studio-unit-id]");
    selectedUnitId = unit?.dataset.studioUnitId || selectedUnitId;
    unit?.classList.add("is-studio-selected");
    renderSectionList();
    refreshInspector();
  }

  function clearSelection() {
    selected?.removeAttribute?.("data-studio-selected");
    selected?.classList?.remove("is-studio-selected");
    allUnits().forEach((unit) => unit.classList.remove("is-studio-selected"));
    selected = null;
    selectedUnitId = null;
    renderSectionList();
    refreshInspector();
  }

  function selectedTextTarget() {
    if (!selected) return null;
    if (selected.matches?.("[data-edit-id]")) return selected;
    return selected.querySelector?.("[data-edit-id]") || null;
  }

  function selectedLinkTarget() {
    if (!selected) return null;
    if (selected.matches?.("a")) return selected;
    return selected.closest?.("a") || selected.querySelector?.("a") || null;
  }

  function selectedImageTarget() {
    if (!selected) return null;
    if (selected.matches?.("[data-logo-slot-id],img[data-logo-id],.studio-image-slot")) return selected;
    return selected.querySelector?.("[data-logo-slot-id],img[data-logo-id],.studio-image-slot") || null;
  }

  function selectedBlockTarget() {
    return selected?.closest?.(".studio-user-block") || null;
  }

  function refreshInspector() {
    if (!panel) return;
    const title = panel.querySelector("[data-studio-selection-title]");
    const meta = panel.querySelector("[data-studio-selection-meta]");
    const textPanel = panel.querySelector("[data-studio-text-controls]");
    const linkPanel = panel.querySelector("[data-studio-link-controls]");
    const imagePanel = panel.querySelector("[data-studio-image-controls]");
    const blockPanel = panel.querySelector("[data-studio-block-controls]");
    const sectionPanel = panel.querySelector("[data-studio-section-controls]");
    const textTarget = selectedTextTarget();
    const linkTarget = selectedLinkTarget();
    const imageTarget = selectedImageTarget();
    const blockTarget = selectedBlockTarget();
    const unit = selectedUnit();

    if (title) title.textContent = selected ? safeText(textTarget?.textContent || sectionLabel(unit) || "Selected element").slice(0, 80) : "Select something in the report";
    if (meta) {
      if (!selected) meta.textContent = "Click a section, text field, link, logo, or seal.";
      else if (blockTarget) meta.textContent = `Added ${blockTarget.dataset.studioBlockType || "content"} block`;
      else if (imageTarget) meta.textContent = "Logo or seal placement";
      else if (linkTarget) meta.textContent = linkTarget.closest("#evidence,[data-source-link]") ? "Verified evidence link" : "Hyperlink";
      else if (textTarget) meta.textContent = "Editable text";
      else meta.textContent = "Report section";
    }

    [textPanel, linkPanel, imagePanel, blockPanel, sectionPanel].forEach((item) => { if (item) item.hidden = true; });
    if (textPanel && textTarget) {
      textPanel.hidden = false;
      const computed = getComputedStyle(textTarget);
      panel.querySelector("[data-studio-font-size]").value = Math.round(parseFloat(computed.fontSize) || 12);
      panel.querySelector("[data-studio-line-height]").value = Number.isFinite(parseFloat(computed.lineHeight) / parseFloat(computed.fontSize))
        ? (parseFloat(computed.lineHeight) / parseFloat(computed.fontSize)).toFixed(2)
        : "1.25";
      panel.querySelector("[data-studio-text-align]").value = ["left", "center", "right"].includes(computed.textAlign) ? computed.textAlign : "left";
      const rect = textTarget.getBoundingClientRect();
      panel.querySelector("[data-studio-box-width]").value = Math.round(rect.width || 0);
      panel.querySelector("[data-studio-box-height]").value = Math.round(rect.height || 0);
    }
    if (linkPanel && (linkTarget || textTarget)) {
      linkPanel.hidden = false;
      const input = panel.querySelector("[data-studio-link-url]");
      input.value = linkTarget?.getAttribute("href") || "";
      const newTab = panel.querySelector("[data-studio-link-new-tab]");
      newTab.checked = linkTarget?.getAttribute("target") === "_blank";
      const warning = panel.querySelector("[data-studio-link-warning]");
      warning.hidden = !linkTarget?.closest("#evidence,[data-source-link]");
    }
    if (imagePanel && imageTarget) {
      imagePanel.hidden = false;
      const image = imageTarget.matches("img") ? imageTarget : imageTarget.querySelector("img");
      panel.querySelector("[data-studio-image-alt]").value = image?.alt || "";
      panel.querySelector("[data-studio-image-url]").value = image?.src?.startsWith("data:") ? "" : image?.src || "";
      const rect = imageTarget.getBoundingClientRect();
      panel.querySelector("[data-studio-image-width]").value = Math.round(rect.width || 0);
      panel.querySelector("[data-studio-image-height]").value = Math.round(rect.height || 0);
    }
    if (blockPanel && blockTarget) {
      blockPanel.hidden = false;
      panel.querySelector("[data-studio-grid-span]").value = blockTarget.dataset.studioGridSpan || "12";
    }
    if (sectionPanel && unit) {
      sectionPanel.hidden = false;
      const computed = getComputedStyle(unit);
      panel.querySelector("[data-studio-section-top]").value = Math.round(parseFloat(computed.paddingTop) || 0);
      panel.querySelector("[data-studio-section-bottom]").value = Math.round(parseFloat(computed.paddingBottom) || 0);
      panel.querySelector("[data-studio-selected-section]").textContent = sectionLabel(unit);
      panel.querySelector("[data-studio-action='hide-section']").textContent = unit.dataset.studioHidden === "true" ? "Restore section" : "Remove section";
    }
  }

  function setTextStyle(property, value) {
    const target = selectedTextTarget();
    if (!target) return;
    ensureStyleKey(target);
    if (property === "fontSize") target.style.setProperty("font-size", `${Math.max(7, Math.min(96, Number(value) || 12))}px`, "important");
    if (property === "lineHeight") target.style.setProperty("line-height", String(Math.max(.8, Math.min(3, Number(value) || 1.25))), "important");
    if (property === "textAlign") target.style.setProperty("text-align", ["left", "center", "right"].includes(value) ? value : "left", "important");
    if (property === "width") {
      const width = Math.max(24, Math.min(root.clientWidth || 1220, Number(value) || target.getBoundingClientRect().width));
      target.style.setProperty("--studio-user-width", `${Math.round(width)}px`);
      target.dataset.studioSized = "true";
    }
    if (property === "height") {
      const height = Math.max(16, Math.min(1200, Number(value) || target.getBoundingClientRect().height));
      target.style.setProperty("--studio-user-min-height", `${Math.round(height)}px`);
      target.dataset.studioSized = "true";
    }
    dirty = true;
    commitSoon("Text style changed");
  }

  function setSectionSpacing(position, value) {
    const unit = selectedUnit();
    if (!unit) return;
    ensureStyleKey(unit);
    const px = Math.max(0, Math.min(240, Number(value) || 0));
    unit.style.setProperty(position === "top" ? "padding-top" : "padding-bottom", `${px}px`, "important");
    dirty = true;
    commitSoon("Section spacing changed");
  }

  function setImageSize(dimension, value) {
    const target = selectedImageTarget();
    if (!target) return;
    ensureStyleKey(target);
    if (dimension === "width") {
      const width = Math.max(24, Math.min(root.clientWidth || 1220, Number(value) || target.getBoundingClientRect().width));
      target.style.setProperty("--studio-user-width", `${Math.round(width)}px`);
    } else {
      const height = Math.max(24, Math.min(1200, Number(value) || target.getBoundingClientRect().height));
      target.style.setProperty("--studio-user-min-height", `${Math.round(height)}px`);
    }
    target.dataset.studioSized = "true";
    dirty = true;
    commitSoon("Image box resized");
  }

  function setBlockSpan(value) {
    const block = selectedBlockTarget();
    if (!block) return;
    const span = ["3", "4", "6", "8", "12"].includes(String(value)) ? String(value) : "12";
    ensureStyleKey(block);
    block.dataset.studioGridSpan = span;
    block.style.setProperty("--studio-grid-span", span);
    dirty = true;
    pushHistory("Block width changed");
  }

  function rememberOriginalLink(link) {
    ensureLinkId(link);
    if (link.dataset.studioOriginalHref == null) {
      link.dataset.studioOriginalHref = link.getAttribute("href") || "";
      link.dataset.studioOriginalTarget = link.getAttribute("target") || "";
      link.dataset.studioOriginalRel = link.getAttribute("rel") || "";
    }
  }

  function applyLinkFromInspector() {
    const raw = panel.querySelector("[data-studio-link-url]").value;
    const href = safeHref(raw);
    if (!href) {
      announce("Enter an http, https, mailto, or #anchor link");
      return;
    }
    let link = selectedLinkTarget();
    const proof = link?.closest("#evidence,[data-source-link]");
    if (proof && !confirm("This is a government evidence link. Change this individual proof URL? The saved file remains recoverable through Reset Studio edits.")) return;
    if (!link) {
      if (!savedSelectionRange || !root.contains(savedSelectionRange.commonAncestorContainer)) {
        announce("Select text in the report, then choose Link selected text");
        return;
      }
      const selection = getSelection();
      selection.removeAllRanges();
      selection.addRange(savedSelectionRange);
      document.execCommand("createLink", false, href);
      link = selection.anchorNode?.parentElement?.closest("a") || selection.focusNode?.parentElement?.closest("a");
      if (!link) {
        announce("The selected text could not be linked");
        return;
      }
      selectElement(link);
    }
    rememberOriginalLink(link);
    link.setAttribute("href", href);
    const newTab = panel.querySelector("[data-studio-link-new-tab]").checked;
    if (newTab && !href.startsWith("#") && !href.startsWith("mailto:")) {
      link.setAttribute("target", "_blank");
      link.setAttribute("rel", "noopener noreferrer");
    } else {
      link.removeAttribute("target");
      link.removeAttribute("rel");
    }
    link.dataset.studioLinkEdited = "true";
    dirty = true;
    pushHistory("Hyperlink updated");
    refreshInspector();
  }

  function imageForTarget(target) {
    return target?.matches?.("img") ? target : target?.querySelector?.("img") || null;
  }

  function imageIdentity(target) {
    return target?.dataset?.logoSlotId || target?.dataset?.logoId || "";
  }

  function captureImageValue(target) {
    if (!target) return null;
    if (target.matches("img")) {
      return { mode: "img", src: target.getAttribute("src") || "", alt: target.alt || "" };
    }
    return { mode: "slot", html: cleanClone(target).innerHTML };
  }

  function rememberOriginalImage(target) {
    const id = imageIdentity(target);
    if (!id) return;
    const original = captureImageValue(target);
    [baselineState, ...history].forEach((state) => {
      if (!state) return;
      state.images ||= {};
      if (!state.images[id]) state.images[id] = deepClone(original);
    });
    target.dataset.studioImageEdited = "true";
  }

  function setImageSource(target, source, alt = "") {
    if (!target || !source) return;
    rememberOriginalImage(target);
    let image = imageForTarget(target);
    if (!image) {
      image = document.createElement("img");
      target.replaceChildren(image);
    }
    image.src = source;
    image.alt = alt || image.alt || "";
    image.draggable = studioOn;
    target.classList?.remove("is-studio-dragover");
    dirty = true;
    pushHistory("Image replaced");
    selectElement(target);
  }

  function loadImageFile(file, target = selectedImageTarget()) {
    if (!target || !file) return;
    if (!file.type.startsWith("image/") && !/\.(png|jpe?g|gif|webp|svg)$/i.test(file.name || "")) {
      announce("Choose a PNG, JPG, SVG, GIF, or WebP image");
      return;
    }
    if (file.size > MAX_IMAGE_BYTES) {
      announce("Image is larger than 8 MB · choose a smaller file");
      return;
    }
    const reader = new FileReader();
    reader.addEventListener("load", () => setImageSource(target, String(reader.result || "")));
    reader.addEventListener("error", () => announce("That image could not be loaded"));
    reader.readAsDataURL(file);
  }

  function applyImageUrl() {
    const target = selectedImageTarget();
    const source = safeHref(panel.querySelector("[data-studio-image-url]").value);
    if (!target || !source || !/^https?:/i.test(source)) {
      announce("Enter a valid http or https image URL");
      return;
    }
    setImageSource(target, source, panel.querySelector("[data-studio-image-alt]").value);
  }

  function applyImageAlt() {
    const target = selectedImageTarget();
    const image = imageForTarget(target);
    if (!image) return;
    rememberOriginalImage(target);
    image.alt = panel.querySelector("[data-studio-image-alt]").value.trim();
    dirty = true;
    pushHistory("Image description changed");
  }

  function saveStudioDraft() {
    const envelope = writeEmbeddedEnvelope();
    try {
      localStorage.setItem(storageKey, JSON.stringify(envelope));
      dirty = false;
      announce("Text, assets, links, and layout saved in this browser");
    } catch {
      announce("Browser storage is full · use Download HTML to preserve this copy");
    }
  }

  function saveAllDrafts() {
    const originalSave = reportTools.querySelector("[data-action='save-draft']");
    if (originalSave) originalSave.click();
    else saveStudioDraft();
  }

  function resetStudioLayout() {
    if (!confirm("Reset all unsaved and browser-saved Studio edits—text, seals, links, added content, section order, and layout—to this HTML file's saved baseline?")) return;
    try {
      localStorage.removeItem(storageKey);
      localStorage.removeItem(`lila:draft:${reportId}`);
    } catch {}
    location.reload();
  }

  function buildPanel() {
    launcher = document.createElement("button");
    launcher.type = "button";
    launcher.dataset.action = "toggle-studio";
    launcher.dataset.editorTransient = "";
    launcher.setAttribute("aria-pressed", "false");
    launcher.textContent = "Layout Studio";
    reportTools.insertBefore(launcher, reportTools.querySelector("[data-action='save-draft']"));

    panel = document.createElement("aside");
    panel.id = "lila-studio-panel";
    panel.dataset.editorTransient = "";
    panel.setAttribute("aria-label", `${clientName} report Layout Studio`);
    panel.innerHTML = `
      <div class="studio-panel-shell">
        <header class="studio-panel-head">
          <div>
            <div class="studio-panel-kicker">${clientName} editable studio</div>
            <h2>Structure the report</h2>
            <p>Move or recover sections, add content blocks, edit hyperlinks, and tune text without breaking the evidence underneath.</p>
          </div>
          <button type="button" class="studio-close" data-studio-action="close" aria-label="Close Layout Studio">×</button>
        </header>
        <div class="studio-history-bar">
          <button type="button" data-studio-action="undo">Undo</button>
          <button type="button" data-studio-action="redo">Redo</button>
          <button type="button" data-studio-action="save">Save</button>
          <button type="button" data-studio-action="reset">Reset Studio edits</button>
        </div>
        <div class="studio-panel-body">
          <details open>
            <summary>Sections · reorder or remove</summary>
            <div>
              <div class="studio-section-list" data-studio-section-list></div>
              <div class="studio-inspector-actions">
                <button type="button" data-studio-action="restore-all">Restore hidden</button>
                <button type="button" data-studio-action="add-section">Add section</button>
              </div>
              <p class="studio-help">Removed sections stay in this list and can be restored. Drag a row or use the arrows to change report order.</p>
            </div>
          </details>

          <details open>
            <summary>Add to selected section</summary>
            <div>
              <div class="studio-add-grid">
                <button type="button" data-studio-add="text">Text box</button>
                <button type="button" data-studio-add="callout">Callout</button>
                <button type="button" data-studio-add="link">Link box</button>
                <button type="button" data-studio-add="image">Logo / seal</button>
              </div>
              <p class="studio-help">Select a section first. Added blocks use the report grid so the downloaded HTML stays responsive and printable.</p>
            </div>
          </details>

          <details open>
            <summary>Selection inspector</summary>
            <div>
              <div class="studio-inspector-kicker">Selected</div>
              <h3 class="studio-selection-title" data-studio-selection-title>Select something in the report</h3>
              <p class="studio-selection-meta" data-studio-selection-meta>Click a section, text field, link, logo, or seal.</p>

              <div data-studio-text-controls hidden>
                <div class="studio-field-row">
                  <label class="studio-field"><span>Font size</span><input type="number" min="7" max="96" step="1" data-studio-font-size></label>
                  <label class="studio-field"><span>Line height</span><input type="number" min=".8" max="3" step=".05" data-studio-line-height></label>
                </div>
                <label class="studio-field"><span>Alignment</span>
                  <select data-studio-text-align><option value="left">Left</option><option value="center">Center</option><option value="right">Right</option></select>
                </label>
                <div class="studio-field-row">
                  <label class="studio-field"><span>Box width px</span><input type="number" min="24" max="1220" step="1" data-studio-box-width></label>
                  <label class="studio-field"><span>Min height px</span><input type="number" min="16" max="1200" step="1" data-studio-box-height></label>
                </div>
              </div>

              <div data-studio-link-controls hidden>
                <label class="studio-field"><span>Hyperlink URL</span><input type="url" placeholder="https://…" data-studio-link-url></label>
                <label class="studio-field"><span><input type="checkbox" data-studio-link-new-tab checked> Open external link in a new tab</span></label>
                <div class="studio-warning" data-studio-link-warning hidden><span class="studio-proof-lock">Evidence link:</span> changing this URL changes the proof for this record. The saved file remains recoverable through Reset Studio edits.</div>
                <div class="studio-inspector-actions">
                  <button type="button" data-studio-action="apply-link">Apply URL</button>
                  <button type="button" data-studio-action="open-link">Open link</button>
                </div>
                <p class="studio-help">To hyperlink only part of a sentence, select that text in the report, enter a URL here, and choose Apply URL.</p>
              </div>

              <div data-studio-image-controls hidden>
                <label class="studio-field"><span>Image description</span><input type="text" data-studio-image-alt></label>
                <label class="studio-field"><span>Image URL · externally hosted</span><input type="url" placeholder="https://…" data-studio-image-url></label>
                <div class="studio-field-row">
                  <label class="studio-field"><span>Box width px</span><input type="number" min="24" max="1220" step="1" data-studio-image-width></label>
                  <label class="studio-field"><span>Box height px</span><input type="number" min="24" max="1200" step="1" data-studio-image-height></label>
                </div>
                <input type="file" accept="image/png,image/jpeg,image/gif,image/webp,image/svg+xml" data-studio-image-file hidden>
                <div class="studio-inspector-actions">
                  <button type="button" data-studio-action="choose-image">Choose image</button>
                  <button type="button" data-studio-action="apply-image-url">Use URL</button>
                  <button type="button" data-studio-action="apply-image-alt">Save description</button>
                </div>
                <p class="studio-help">Choose or drop a file to keep the HTML self-contained. URL images remain externally hosted and must stay online to render.</p>
              </div>

              <div data-studio-block-controls hidden>
                <label class="studio-field"><span>Added block width</span>
                  <select data-studio-grid-span>
                    <option value="12">Full width</option>
                    <option value="8">Two thirds</option>
                    <option value="6">Half width</option>
                    <option value="4">One third</option>
                    <option value="3">Quarter width</option>
                  </select>
                </label>
                <div class="studio-inspector-actions">
                  <button type="button" data-studio-action="block-up">Move block up</button>
                  <button type="button" data-studio-action="block-down">Move block down</button>
                  <button type="button" data-studio-action="duplicate-block">Duplicate block</button>
                  <button type="button" class="studio-danger" data-studio-action="delete-block">Remove block</button>
                </div>
              </div>

              <div data-studio-section-controls hidden>
                <div class="studio-fieldset-title">Section · <span data-studio-selected-section></span></div>
                <div class="studio-field-row">
                  <label class="studio-field"><span>Top spacing px</span><input type="number" min="0" max="240" step="1" data-studio-section-top></label>
                  <label class="studio-field"><span>Bottom spacing px</span><input type="number" min="0" max="240" step="1" data-studio-section-bottom></label>
                </div>
                <div class="studio-inspector-actions">
                  <button type="button" data-studio-action="section-up">Move section up</button>
                  <button type="button" data-studio-action="section-down">Move section down</button>
                  <button type="button" class="studio-danger" data-studio-action="hide-section">Remove section</button>
                </div>
              </div>
            </div>
          </details>
        </div>
        <footer class="studio-panel-foot">
          <div data-studio-status role="status" aria-live="polite">Layout Studio ready</div>
          <button type="button" data-studio-action="download">Download editable HTML</button>
        </footer>
      </div>`;
    document.body.append(panel);
    studioStatus = panel.querySelector("[data-studio-status]");
  }

  function panelAction(actionName) {
    const unit = selectedUnit();
    if (actionName === "close") exitStudio();
    else if (actionName === "undo") undoLayout();
    else if (actionName === "redo") redoLayout();
    else if (actionName === "save") saveAllDrafts();
    else if (actionName === "reset") resetStudioLayout();
    else if (actionName === "restore-all") restoreAllHidden();
    else if (actionName === "add-section") addCustomSection();
    else if (actionName === "apply-link") applyLinkFromInspector();
    else if (actionName === "open-link") {
      const link = selectedLinkTarget();
      if (link?.href) window.open(link.href, "_blank", "noopener,noreferrer");
    }
    else if (actionName === "choose-image") panel.querySelector("[data-studio-image-file]").click();
    else if (actionName === "apply-image-url") applyImageUrl();
    else if (actionName === "apply-image-alt") applyImageAlt();
    else if (actionName === "block-up") moveSelectedBlock(-1);
    else if (actionName === "block-down") moveSelectedBlock(1);
    else if (actionName === "duplicate-block") duplicateSelectedBlock();
    else if (actionName === "delete-block") deleteSelectedBlock();
    else if (actionName === "section-up" && unit) moveUnit(unit.dataset.studioUnitId, -1);
    else if (actionName === "section-down" && unit) moveUnit(unit.dataset.studioUnitId, 1);
    else if (actionName === "hide-section" && unit) setUnitHidden(unit.dataset.studioUnitId, unit.dataset.studioHidden !== "true");
    else if (actionName === "download") {
      saveStudioDraft();
      reportTools.querySelector("[data-action='download-html']")?.click();
    }
  }

  function bindEvents() {
    launcher.addEventListener("click", () => studioOn ? exitStudio() : enterStudio());

    document.addEventListener("keydown", (event) => {
      const mod = event.metaKey || event.ctrlKey;
      if (mod && event.shiftKey && event.key.toLowerCase() === "l") {
        event.preventDefault();
        studioOn ? exitStudio() : enterStudio();
      }
    });

    panel.addEventListener("click", (event) => {
      const actionButton = event.target.closest("[data-studio-action]");
      if (actionButton) {
        panelAction(actionButton.dataset.studioAction);
        return;
      }
      const addButton = event.target.closest("[data-studio-add]");
      if (addButton) addBlock(addButton.dataset.studioAdd);
      const rowButton = event.target.closest("[data-studio-row-action]");
      if (rowButton) {
        const row = rowButton.closest("[data-studio-section-ref]");
        const id = row?.dataset.studioSectionRef;
        if (!id) return;
        const action = rowButton.dataset.studioRowAction;
        if (action === "select") {
          const unit = unitById(id);
          if (unit?.dataset.studioHidden === "true") setUnitHidden(id, false);
          selectElement(unit);
          unit?.scrollIntoView({ behavior: "smooth", block: "start" });
        } else if (action === "up") moveUnit(id, -1);
        else if (action === "down") moveUnit(id, 1);
        else if (action === "hide") setUnitHidden(id, true);
        else if (action === "restore") setUnitHidden(id, false);
      }
    });

    panel.addEventListener("input", (event) => {
      const target = event.target;
      if (target.matches("[data-studio-font-size]")) setTextStyle("fontSize", target.value);
      else if (target.matches("[data-studio-line-height]")) setTextStyle("lineHeight", target.value);
      else if (target.matches("[data-studio-text-align]")) setTextStyle("textAlign", target.value);
      else if (target.matches("[data-studio-box-width]")) setTextStyle("width", target.value);
      else if (target.matches("[data-studio-box-height]")) setTextStyle("height", target.value);
      else if (target.matches("[data-studio-image-width]")) setImageSize("width", target.value);
      else if (target.matches("[data-studio-image-height]")) setImageSize("height", target.value);
      else if (target.matches("[data-studio-grid-span]")) setBlockSpan(target.value);
      else if (target.matches("[data-studio-section-top]")) setSectionSpacing("top", target.value);
      else if (target.matches("[data-studio-section-bottom]")) setSectionSpacing("bottom", target.value);
    });

    panel.querySelector("[data-studio-image-file]").addEventListener("change", (event) => {
      const file = event.target.files?.[0];
      if (file) loadImageFile(file);
      event.target.value = "";
    });

    panel.addEventListener("dragstart", (event) => {
      const row = event.target.closest("[data-studio-section-ref]");
      if (!row || !event.dataTransfer) return;
      draggedUnitId = row.dataset.studioSectionRef;
      event.dataTransfer.effectAllowed = "move";
      event.dataTransfer.setData(UNIT_MIME, draggedUnitId);
    });
    panel.addEventListener("dragover", (event) => {
      if (!event.target.closest("[data-studio-section-ref]")) return;
      event.preventDefault();
      if (event.dataTransfer) event.dataTransfer.dropEffect = "move";
    });
    panel.addEventListener("drop", (event) => {
      const row = event.target.closest("[data-studio-section-ref]");
      if (!row) return;
      event.preventDefault();
      const sourceId = event.dataTransfer?.getData(UNIT_MIME) || draggedUnitId;
      moveUnitBefore(sourceId, row.dataset.studioSectionRef);
      draggedUnitId = null;
    });

    root.addEventListener("click", (event) => {
      if (!studioOn) return;
      const unitControl = event.target.closest("[data-studio-unit-action]");
      if (unitControl) {
        const unit = unitControl.closest("[data-studio-unit-id]");
        const id = unit?.dataset.studioUnitId;
        const action = unitControl.dataset.studioUnitAction;
        if (action === "select") selectElement(unit);
        else if (action === "up") moveUnit(id, -1);
        else if (action === "down") moveUnit(id, 1);
        else if (action === "hide") setUnitHidden(id, true);
        event.preventDefault();
        event.stopPropagation();
        return;
      }
      const link = event.target.closest("a");
      if (link && !(event.metaKey || event.ctrlKey)) event.preventDefault();
      const candidate =
        event.target.closest(".studio-user-block") ||
        event.target.closest("[data-logo-slot-id],img[data-logo-id]") ||
        event.target.closest("[data-edit-id]") ||
        link ||
        event.target.closest("[data-studio-unit-id]");
      if (candidate) selectElement(candidate);
    }, true);

    root.addEventListener("input", (event) => {
      if (!event.target.closest("[data-edit-id]")) return;
      dirty = true;
      invalidateIntegrity();
      commitSoon(event.target.closest(".studio-user-block")
        ? "Added block text changed"
        : "Report text changed");
    });

    root.addEventListener("dragstart", (event) => {
      if (!studioOn) return;
      const handle = event.target.closest("[data-studio-unit-drag]");
      if (handle && event.dataTransfer) {
        const unit = handle.closest("[data-studio-unit-id]");
        draggedUnitId = unit?.dataset.studioUnitId || null;
        if (draggedUnitId) {
          event.dataTransfer.effectAllowed = "move";
          event.dataTransfer.setData(UNIT_MIME, draggedUnitId);
        }
        return;
      }
      const block = event.target.closest(".studio-user-block");
      if (block && event.target.closest("[data-studio-block-drag]") && event.dataTransfer) {
        draggedBlockId = block.dataset.studioBlockId;
        event.dataTransfer.setData(BLOCK_MIME, draggedBlockId);
      }
    }, true);

    root.addEventListener("dragover", (event) => {
      if (!studioOn) return;
      const imageSlot = event.target.closest(".studio-image-slot");
      if (imageSlot && [...(event.dataTransfer?.types || [])].includes("Files")) {
        event.preventDefault();
        imageSlot.classList.add("is-studio-dragover");
        return;
      }
      const targetUnit = event.target.closest("[data-studio-unit-id]");
      const sourceId = event.dataTransfer?.getData(UNIT_MIME) || draggedUnitId;
      if (targetUnit && sourceId) {
        event.preventDefault();
        if (event.dataTransfer) event.dataTransfer.dropEffect = "move";
      }
    }, true);

    root.addEventListener("dragleave", (event) => {
      const imageSlot = event.target.closest(".studio-image-slot");
      if (imageSlot && !imageSlot.contains(event.relatedTarget)) imageSlot.classList.remove("is-studio-dragover");
    }, true);

    root.addEventListener("drop", (event) => {
      if (!studioOn) return;
      const imageSlot = event.target.closest(".studio-image-slot");
      const file = [...(event.dataTransfer?.files || [])].find((item) => item.type.startsWith("image/") || /\.(png|jpe?g|gif|webp|svg)$/i.test(item.name || ""));
      if (imageSlot && file) {
        event.preventDefault();
        event.stopImmediatePropagation();
        loadImageFile(file, imageSlot);
        return;
      }
      const targetUnit = event.target.closest("[data-studio-unit-id]");
      const sourceId = event.dataTransfer?.getData(UNIT_MIME) || draggedUnitId;
      if (targetUnit && sourceId) {
        event.preventDefault();
        event.stopImmediatePropagation();
        moveUnitBefore(sourceId, targetUnit.dataset.studioUnitId);
        draggedUnitId = null;
      }
    }, true);

    document.addEventListener("selectionchange", () => {
      if (!studioOn) return;
      const selection = getSelection();
      if (!selection.rangeCount) return;
      const range = selection.getRangeAt(0);
      if (!range.collapsed && root.contains(range.commonAncestorContainer)) savedSelectionRange = range.cloneRange();
    });

    document.addEventListener("keydown", (event) => {
      if (!studioOn) return;
      const mod = event.metaKey || event.ctrlKey;
      const editingText = event.target.closest?.("[contenteditable='true'],input,textarea,select");
      if (mod && event.key.toLowerCase() === "z" && !editingText) {
        event.preventDefault();
        if (event.shiftKey) redoLayout();
        else undoLayout();
      } else if (event.altKey && event.key === "ArrowUp") {
        event.preventDefault();
        if (selectedBlockTarget()) moveSelectedBlock(-1);
        else if (selectedUnit()) moveUnit(selectedUnit().dataset.studioUnitId, -1);
      } else if (event.altKey && event.key === "ArrowDown") {
        event.preventDefault();
        if (selectedBlockTarget()) moveSelectedBlock(1);
        else if (selectedUnit()) moveUnit(selectedUnit().dataset.studioUnitId, 1);
      } else if (event.key === "Escape" && selected) {
        event.preventDefault();
        clearSelection();
      }
    });

    reportTools.querySelector("[data-action='save-draft']")?.addEventListener("click", () => saveStudioDraft());
    reportTools.querySelector("[data-action='print']")?.addEventListener("click", () => exitStudio(), true);
    reportTools.querySelector("[data-action='download-html']")?.addEventListener("click", () => {
      clearTimeout(changeTimer);
      if (dirty) pushHistory("Prepared download");
      const normalEnvelope = writeEmbeddedEnvelope();
      stateScript().textContent = JSON.stringify({
        ...normalEnvelope,
        base: normalEnvelope.state,
        savedAt: new Date().toISOString(),
      }).replace(/</g, "\\u003c");
      exitStudio();
      setTimeout(() => {
        stateScript().textContent = JSON.stringify(normalEnvelope).replace(/</g, "\\u003c");
      }, 0);
    }, true);

    window.addEventListener("beforeunload", (event) => {
      if (!dirty) return;
      event.preventDefault();
      event.returnValue = "";
    });
  }

  function loadInitialState() {
    stampUnits();
    initialUnitOrder = allUnits().map((unit) => unit.dataset.studioUnitId);
    const pristine = captureState();
    baseFingerprint = document.documentElement.dataset.studioSourceSha256 || fingerprintForState(pristine);
    baselineState = deepClone(pristine);

    const embedded = readEmbeddedEnvelope();
    let restored = null;
    if (embedded) {
      if (embedded.reportId !== reportId) {
        throw new Error("embedded Studio state belongs to a different report");
      }
      if (embedded.baseFingerprint !== baseFingerprint) {
        throw new Error("embedded Studio state does not match this report source");
      }
      if (embedded.state?.version !== STUDIO_VERSION) {
        throw new Error("embedded Studio state payload has an unsupported version");
      }
      baselineState = deepClone(embedded.base || pristine);
      restored = deepClone(embedded.state);
    } else {
      try {
        const saved = JSON.parse(localStorage.getItem(storageKey) || "null");
        if (saved?.schema === STUDIO_SCHEMA && saved?.version === STUDIO_VERSION &&
            saved?.reportId === reportId && saved?.baseFingerprint === baseFingerprint) {
          restored = deepClone(saved.state);
        }
      } catch {}
    }
    if (restored) applyState(restored);
    history = [deepClone(captureState())];
    historyIndex = 0;
    writeEmbeddedEnvelope();
  }

  function boot() {
    // The inherited downloader serializes the live DOM. A previously opened
    // Studio copy can therefore contain launcher, panel, or section controls.
    // They are runtime chrome, never report content: remove them before
    // rebuilding so download -> reopen stays idempotent.
    $$('[data-editor-transient]', document).forEach((node) => node.remove());
    stampUnits();
    loadInitialState();
    buildPanel();
    ensureUnitControls();
    bindEvents();
    renderSectionList();
    refreshInspector();
    updateHistoryButtons();
    $$(".sb-news-dup [data-studio-link-id]", root).forEach((link) => link.removeAttribute("data-studio-link-id"));
    if (new URLSearchParams(location.search).get("studio") === "1") {
      requestAnimationFrame(() => enterStudio());
    }
  }

  function start() {
    try {
      boot();
      document.documentElement.dataset.studioBoot = "ready";
    } catch (error) {
      document.documentElement.dataset.studioBoot = "error";
      document.documentElement.dataset.studioBootError = `${error?.name || "Error"}: ${error?.message || String(error)}`.slice(0, 500);
      console.error("Editable Studio failed to initialize", error);
    }
  }

  // A downloaded live DOM may serialize transient Studio controls after this
  // script tag. Wait for the parser to finish so boot can remove the entire
  // old UI before creating exactly one fresh launcher and panel.
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", start, { once: true });
  } else {
    start();
  }
})();
