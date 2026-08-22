/* Operator-only Signal Board mark editor.
 *
 * Loaded by /report?logo_editor=1, never embedded in certified report bytes.
 * Organization marks and evidence-bound portraits are persisted through the
 * Command Center; remote image drops are
 * fetched by the browser only when CORS allows them, then uploaded as bytes.
 */
(() => {
  'use strict';

  function createSerialSizeSaver({
    readPersisted, applyPreview, requestSave, commit, succeed, fail,
  }) {
    const states = new Map();

    function start(state) {
      if (state.active) return state.active;
      state.active = drain(state).finally(() => {
        state.active = null;
        if (state.pending) start(state);
      });
      return state.active;
    }

    async function drain(state) {
      while (state.pending) {
        const item = state.pending;
        state.pending = null;
        try {
          const result = await requestSave(item);
          state.persisted = item.percent;
          commit(item.key, item.percent);
          if (!state.pending) succeed(result, item);
        } catch (error) {
          // A failed save invalidates every unsaved value for this identity.
          // Restore the last server-confirmed value and require a fresh action.
          state.pending = null;
          applyPreview(item.key, state.persisted);
          fail(error, item, state.persisted);
          return;
        }
      }
    }

    function schedule(item) {
      let state = states.get(item.key);
      if (!state) {
        state = {
          active: null,
          pending: null,
          persisted: readPersisted(item.key),
        };
        states.set(item.key, state);
      }
      // Coalesce changes that arrive while this identity is saving. The
      // server sees at most one request at a time and then the latest intent.
      state.pending = item;
      applyPreview(item.key, item.percent);
      return start(state);
    }

    return { schedule };
  }

  function attachmentFilename(disposition, fallback) {
    const value = String(disposition || '');
    const encoded = value.match(/filename\*=UTF-8''([^;]+)/i);
    const quoted = value.match(/filename="([^"]+)"/i);
    let candidate = '';
    try {
      candidate = encoded ? decodeURIComponent(encoded[1]) : '';
    } catch (_error) {
      candidate = '';
    }
    if (!candidate && quoted) candidate = quoted[1];
    candidate = candidate.split(/[\\/]/).pop().replace(/[\u0000-\u001f\u007f]/g, '').trim();
    return candidate && /\.html$/i.test(candidate) ? candidate : fallback;
  }

  async function fetchCertifiedHtml({ downloadUrl, editorToken, fetchImpl }) {
    let response;
    try {
      response = await fetchImpl(downloadUrl, {
        method: 'GET', credentials: 'same-origin',
        headers: { 'X-LILA-Editor-Token': editorToken },
      });
    } catch (_error) {
      throw new Error('Could not reach Command Center to download the report.');
    }
    if (!response.ok) {
      const payload = typeof response.json === 'function'
        ? await response.json().catch(() => ({})) : {};
      throw new Error(
        payload.error || `Report download failed (${response.status})`);
    }
    const blob = await response.blob();
    return {
      blob,
      filename: attachmentFilename(
        response.headers.get('Content-Disposition'),
        'Federal Opportunity Pre-Assessment.html',
      ),
    };
  }

  if (typeof module !== 'undefined' && module.exports) {
    module.exports = {
      attachmentFilename, createSerialSizeSaver, fetchCertifiedHtml,
    };
    return;
  }

  const loader = document.currentScript;
  const slug = (loader && loader.dataset.client) || '';
  const editorToken = (loader && loader.dataset.editorToken) || '';
  const downloadUrl = (loader && loader.dataset.downloadUrl) || '';
  const targets = [...document.querySelectorAll(
    '[data-brand-kind][data-brand-key][data-brand-label]')];
  const headerCompanion = document.querySelector('.sb-brand .sb-brand-sub');
  if (!slug || !editorToken || !targets.length) return;

  const style = document.createElement('style');
  style.textContent = `
    body.sb-mark-editor-active {
      --sb-editor-offset: 54px;
      padding-top: var(--sb-editor-offset) !important;
    }
    body.sb-mark-editor-active .sb-utility {
      top: var(--sb-editor-offset) !important;
    }
    .sb-mark-editor-bar {
      position: fixed; inset: 0 0 auto 0; z-index: 100000;
      min-height: 54px; display: flex; align-items: center; gap: 12px;
      padding: 8px 16px; color: #fff; background: #10243a;
      border-bottom: 3px solid #fb461f; box-shadow: 0 5px 18px rgba(0,0,0,.24);
      font: 700 12px/1.35 "Avenir Next", "Segoe UI", Arial, sans-serif;
    }
    .sb-mark-editor-bar strong { letter-spacing: .08em; text-transform: uppercase; }
    .sb-mark-editor-bar span { color: #c9d6e2; font-weight: 600; }
    .sb-mark-editor-bar button {
      padding: 6px 10px; color: #fff; background: transparent;
      border: 1px solid #8fa6bb; border-radius: 3px; cursor: pointer;
      font: inherit;
    }
    .sb-mark-editor-actions {
      display: flex; flex: 0 0 auto; align-items: center; gap: 8px;
      margin-left: auto;
    }
    .sb-mark-editor-download { background: #157eaf !important; }
    .sb-mark-editor-download:hover,
    .sb-mark-editor-download:focus { background: #fb461f !important; }
    .sb-mark-editor-toast {
      position: fixed; right: 18px; top: 68px; z-index: 100001;
      max-width: 420px; padding: 11px 14px; color: #fff; background: #17654f;
      box-shadow: 0 6px 24px rgba(0,0,0,.25); border-radius: 4px;
      font: 700 12px/1.4 "Avenir Next", "Segoe UI", Arial, sans-serif;
    }
    .sb-mark-editor-toast.bad { background: #8b2c22; }
    [data-brand-kind].sb-mark-target {
      position: relative !important; outline: 2px dashed rgba(21,126,175,.48);
      outline-offset: 3px; transition: outline-color .15s, background-color .15s;
    }
    [data-brand-kind].sb-mark-target:hover,
    [data-brand-kind].sb-mark-target:focus-within,
    [data-brand-kind].sb-mark-target.is-drop {
      outline: 3px solid #fb461f; background-color: rgba(251,70,31,.055) !important;
    }
    [data-brand-kind] > img,
    [data-brand-kind] > .sb-competitor-identity > img {
      transform: none;
      object-fit: contain;
    }
    .sb-brand[data-brand-kind] {
      box-sizing: border-box; min-width: 0; padding-right: 74px;
      flex-wrap: nowrap;
    }
    .sb-brand[data-brand-kind] > .sb-header-logo {
      width: auto;
      height: calc(36px * var(--sb-brand-scale, 1));
      max-width: min(calc(180px * var(--sb-brand-scale, 1)), 42vw);
      max-height: none; flex: 0 0 auto;
    }
    .sb-agency-mark[data-brand-kind] {
      grid-template-columns: auto minmax(0, 1fr);
    }
    .sb-agency-mark[data-brand-kind] > img {
      width: calc(32px * var(--sb-brand-scale, 1));
      height: calc(32px * var(--sb-brand-scale, 1));
      max-width: none; max-height: none;
    }
    .sb-agency-mark[data-brand-kind] > span {
      min-width: 0; overflow-wrap: anywhere;
    }
    .sb-seal-wrap {
      width: auto; height: auto; min-width: 48px; min-height: 48px;
    }
    .sb-agency-code[data-brand-kind] {
      width: calc(42px * var(--sb-brand-scale, 1));
      height: calc(42px * var(--sb-brand-scale, 1));
    }
    .sb-agency-code[data-brand-kind] > .sb-agency-upload {
      width: calc(38px * var(--sb-brand-scale, 1));
      height: calc(38px * var(--sb-brand-scale, 1));
      max-width: none; max-height: none;
    }
    .sb-competitor[data-brand-kind] { min-width: 0; }
    .sb-competitor[data-brand-kind] > .sb-competitor-identity {
      width: 100%; min-width: 0;
    }
    .sb-competitor[data-brand-kind] > .sb-competitor-identity > .sb-competitor-logo {
      width: min(calc(160px * var(--sb-brand-scale, 1)), 100%);
      height: calc(38px * var(--sb-brand-scale, 1));
      max-width: none; max-height: none;
    }
    .sb-company[data-brand-kind] { min-width: 0; }
    .sb-company[data-brand-kind] > .sb-company-logo {
      width: min(calc(180px * var(--sb-brand-scale, 1)), 100%);
      height: calc(38px * var(--sb-brand-scale, 1));
      max-width: none; max-height: none; flex: 0 0 auto;
    }
    .sb-horizon-mark[data-brand-kind] {
      max-width: 100%; flex-wrap: wrap;
    }
    .sb-horizon-mark[data-brand-kind] > .sb-horizon-logo {
      width: calc(28px * var(--sb-brand-scale, 1));
      height: calc(28px * var(--sb-brand-scale, 1));
      max-width: none; max-height: none; flex: 0 0 auto;
    }
    .sb-person-portrait-target[data-brand-kind] {
      width: calc(68px * var(--sb-brand-scale, 1));
      height: calc(68px * var(--sb-brand-scale, 1));
      min-width: 0; min-height: 0; overflow: visible;
    }
    .sb-person-portrait-target[data-brand-kind] > .sb-person-portrait {
      width: 100%; height: 100%; max-width: none; max-height: none;
      object-fit: cover; border-radius: 50%;
    }
    .sb-opp {
      grid-template-columns: 52px minmax(54px, max-content) minmax(190px, 1.55fr) minmax(110px, .62fr) minmax(120px, .8fr) minmax(140px, 1fr) minmax(130px, .9fr);
    }
    @media (max-width: 1050px) {
      .sb-opp {
        grid-template-columns: 44px minmax(48px, max-content) minmax(180px, 1.6fr) repeat(4, minmax(92px, 1fr));
      }
    }
    @media (max-width: 900px) {
      body.sb-mark-editor-active .sb-utility {
        top: 0 !important;
      }
      .sb-opp { grid-template-columns: 40px minmax(44px, max-content) 1fr; }
    }
    @media (max-width: 640px) {
      .sb-utility { grid-template-columns: minmax(0, 1fr); gap: 7px; }
      .sb-utility-meta.has-image { justify-content: flex-start; text-align: left; }
      .sb-brand[data-brand-kind] > .sb-header-logo {
        width: auto;
        height: calc(32px * var(--sb-brand-scale, 1));
        max-width: min(calc(130px * var(--sb-brand-scale, 1)), 100%);
      }
    }
    .sb-mark-edit-button, .sb-mark-size-button {
      position: absolute; z-index: 30; top: 2px; right: 2px;
      min-width: 20px; min-height: 20px; padding: 2px 5px;
      color: #fff; background: #157eaf; border: 1px solid #fff;
      border-radius: 3px; box-shadow: 0 2px 7px rgba(0,0,0,.25);
      cursor: pointer; font: 900 9px/1 "Avenir Next", "Segoe UI", Arial, sans-serif;
    }
    .sb-mark-size-button { right: 29px; min-width: 34px; }
    .sb-mark-edit-button:hover, .sb-mark-edit-button:focus,
    .sb-mark-size-button:hover, .sb-mark-size-button:focus { background: #fb461f; }
    .sb-mark-size-panel {
      position: absolute; z-index: 31; top: 27px; right: 2px;
      display: grid; grid-template-columns: 1fr auto; align-items: center;
      gap: 5px 8px; width: 168px; padding: 8px;
      color: #fff; background: #10243a; border: 1px solid #8fa6bb;
      border-radius: 4px; box-shadow: 0 6px 20px rgba(0,0,0,.3);
      font: 800 10px/1.2 "Avenir Next", "Segoe UI", Arial, sans-serif;
    }
    .sb-mark-size-panel[hidden] { display: none; }
    .sb-mark-size-panel label { grid-column: 1 / -1; }
    .sb-mark-size-panel input { width: 126px; accent-color: #fb461f; }
    .sb-mark-size-panel output { min-width: 34px; text-align: right; }
    .sb-brand .sb-copy-edit-button {
      position: static; flex: 0 0 auto; min-height: 22px; padding: 3px 7px;
      color: #fff; background: #157eaf; border: 1px solid #fff;
      border-radius: 3px; cursor: pointer;
      font: 900 9px/1 "Avenir Next", "Segoe UI", Arial, sans-serif;
      letter-spacing: .03em;
    }
    .sb-brand .sb-copy-edit-button:hover,
    .sb-brand .sb-copy-edit-button:focus { background: #fb461f; }
    .sb-brand .sb-copy-editor {
      position: absolute; z-index: 100; top: calc(100% + 7px); left: 0;
      display: flex; align-items: center; gap: 5px; padding: 7px;
      color: #fff; background: #10243a; border: 1px solid #8fa6bb;
      border-radius: 4px; box-shadow: 0 6px 20px rgba(0,0,0,.3);
    }
    .sb-brand .sb-copy-input {
      box-sizing: border-box; width: min(360px, 58vw); min-width: 220px;
      height: 30px; padding: 5px 8px; color: #10243a; background: #fff;
      border: 2px solid #8fd0ed; border-radius: 3px;
      font: 700 12px/1.2 "Avenir Next", "Segoe UI", Arial, sans-serif;
    }
    .sb-brand .sb-copy-input:focus { border-color: #fb461f; outline: 0; }
    .sb-brand .sb-copy-action {
      min-height: 30px; padding: 5px 9px; color: #fff; background: #157eaf;
      border: 1px solid #fff; border-radius: 3px; cursor: pointer;
      font: 900 10px/1 "Avenir Next", "Segoe UI", Arial, sans-serif;
    }
    .sb-brand .sb-copy-action.cancel { background: transparent; }
    .sb-brand .sb-copy-action:hover,
    .sb-brand .sb-copy-action:focus { background: #fb461f; }
    .sb-brand-sub.sb-copy-target {
      padding: 3px 5px; outline: 2px dashed rgba(143,208,237,.72);
      outline-offset: 2px;
    }
    @media print {
      body.sb-mark-editor-active {
        --sb-editor-offset: 0px !important;
        padding-top: 0 !important;
      }
      .sb-mark-editor-bar, .sb-mark-editor-toast, .sb-mark-edit-button,
      .sb-mark-size-button, .sb-mark-size-panel, .sb-copy-edit-button,
      .sb-copy-editor { display: none !important; }
      [data-brand-kind].sb-mark-target { outline: 0 !important; }
      .sb-brand-sub.sb-copy-target { padding: 0 !important; outline: 0 !important; }
    }
  `;
  document.head.appendChild(style);
  document.body.classList.add('sb-mark-editor-active');

  const bar = document.createElement('div');
  bar.className = 'sb-mark-editor-bar';
  bar.innerHTML = '<strong>Customize report</strong><span>Drop an image onto any highlighted organization mark or person portrait, use Size to resize it, or use Edit label beside the client logo. Portrait slots are bound to the exact sourced person record. The GTM certification mark stays locked. Search-image drops work when the source permits transfer; otherwise save the image and drop the file.</span>';
  const actions = document.createElement('div');
  actions.className = 'sb-mark-editor-actions';
  const download = document.createElement('button');
  download.type = 'button';
  download.className = 'sb-mark-editor-download';
  download.textContent = 'Download HTML';
  download.title = 'Download the clean certified Federal Opportunity Pre-Assessment';
  const toggle = document.createElement('button');
  toggle.type = 'button';
  toggle.textContent = 'Hide highlights';
  actions.append(download, toggle);
  bar.appendChild(actions);
  document.body.prepend(bar);

  function syncEditorOffset() {
    document.body.style.setProperty(
      '--sb-editor-offset', `${Math.ceil(bar.getBoundingClientRect().height)}px`);
  }
  syncEditorOffset();
  if ('ResizeObserver' in window) {
    new ResizeObserver(syncEditorOffset).observe(bar);
  } else {
    window.addEventListener('resize', syncEditorOffset);
  }

  let highlighted = true;
  toggle.addEventListener('click', () => {
    highlighted = !highlighted;
    targets.forEach(target => target.classList.toggle('sb-mark-target', highlighted));
    toggle.textContent = highlighted ? 'Hide highlights' : 'Show highlights';
  });

  let toastTimer = null;
  const sizeControls = [];
  function toast(message, bad = false) {
    document.querySelectorAll('.sb-mark-editor-toast').forEach(node => node.remove());
    const node = document.createElement('div');
    node.className = `sb-mark-editor-toast${bad ? ' bad' : ''}`;
    node.textContent = message;
    document.body.appendChild(node);
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => node.remove(), bad ? 8000 : 5200);
  }

  download.addEventListener('click', async () => {
    if (!downloadUrl) {
      toast('This report does not have a certified HTML download.', true);
      return;
    }
    download.disabled = true;
    download.textContent = 'Preparing HTML…';
    try {
      const result = await fetchCertifiedHtml({
        downloadUrl, editorToken, fetchImpl: window.fetch.bind(window),
      });
      const objectUrl = URL.createObjectURL(result.blob);
      const anchor = document.createElement('a');
      anchor.href = objectUrl;
      anchor.download = result.filename;
      anchor.hidden = true;
      document.body.appendChild(anchor);
      anchor.click();
      anchor.remove();
      setTimeout(() => URL.revokeObjectURL(objectUrl), 1000);
      toast('Clean certified HTML downloaded.');
    } catch (error) {
      toast(error.message || 'Report download failed.', true);
    } finally {
      download.disabled = false;
      download.textContent = 'Download HTML';
    }
  });

  function remoteUrl(dataTransfer) {
    const html = dataTransfer.getData('text/html');
    if (html) {
      const doc = new DOMParser().parseFromString(html, 'text/html');
      const image = doc.querySelector('img[src]');
      if (image) return image.src;
    }
    const list = (dataTransfer.getData('text/uri-list') || '')
      .split(/\r?\n/).map(value => value.trim())
      .find(value => value && !value.startsWith('#'));
    if (list) return list;
    return (dataTransfer.getData('text/plain') || '').trim();
  }

  async function droppedFile(dataTransfer) {
    const file = [...(dataTransfer.files || [])]
      .find(candidate => String(candidate.type || '').startsWith('image/'));
    if (file) return file;
    const url = remoteUrl(dataTransfer);
    if (!url || !/^(https?:|data:image\/)/i.test(url)) {
      throw new Error('Drop a PNG, JPEG, WebP, or an image from a web page.');
    }
    let response;
    try {
      response = await fetch(url, {
        mode: 'cors', credentials: 'omit', referrerPolicy: 'no-referrer',
      });
    } catch (_error) {
      throw new Error('That site blocks direct image transfer. Save the image, then drop the file here.');
    }
    if (!response.ok) throw new Error('The dropped image could not be retrieved.');
    const blob = await response.blob();
    if (!String(blob.type || '').startsWith('image/')) {
      throw new Error('The dropped web result is not an image.');
    }
    if (blob.size > 2 * 1024 * 1024) {
      throw new Error('The dropped image is larger than 2 MiB.');
    }
    const extension = ({
      'image/png': 'png', 'image/jpeg': 'jpg', 'image/webp': 'webp',
    })[blob.type] || 'image';
    return new File([blob], `web-search-logo.${extension}`, { type: blob.type });
  }

  function imageClass(target) {
    if (target.classList.contains('sb-person-portrait-target')) return 'sb-person-portrait';
    if (target.classList.contains('sb-competitor')) return 'sb-competitor-logo';
    if (target.classList.contains('sb-company')) return 'sb-company-logo';
    if (target.classList.contains('sb-horizon-mark')) return 'sb-horizon-logo';
    if (target.classList.contains('sb-agency-code')) return 'sb-agency-upload';
    if (target.classList.contains('sb-brand')) return 'sb-header-logo';
    return '';
  }

  function displayLabel(target) {
    return target.dataset.brandDisplay || target.dataset.brandLabel;
  }

  function applyPreview(key, dataUrl) {
    targets.filter(target => target.dataset.brandKey === key).forEach(target => {
      let image = target.querySelector('img');
      if (!image) {
        image = document.createElement('img');
        const identity = target.querySelector('.sb-competitor-identity');
        (identity || target).prepend(image);
      }
      image.src = dataUrl;
      image.alt = target.dataset.brandKind === 'person'
        ? `Portrait of ${displayLabel(target)}`
        : `${displayLabel(target)} logo`;
      image.removeAttribute('aria-hidden');
      const cls = imageClass(target);
      if (cls) image.classList.add(cls);
      if (target.dataset.brandKind === 'person') {
        target.classList.add('has-portrait');
      } else if (target.classList.contains('sb-agency-code')) {
        target.classList.add('has-image');
      } else {
        target.classList.add('has-logo');
      }
    });
  }

  function previewFile(file, key) {
    const reader = new FileReader();
    reader.addEventListener('load', () => applyPreview(key, reader.result));
    reader.readAsDataURL(file);
  }

  function canonicalSize(value) {
    const numeric = Number(value);
    if (!Number.isFinite(numeric)) return 100;
    return Math.max(50, Math.min(200, Math.round(numeric / 5) * 5));
  }

  function applySize(key, value) {
    const percent = canonicalSize(value);
    targets.filter(target => target.dataset.brandKey === key).forEach(target => {
      target.dataset.brandSize = String(percent);
      target.style.setProperty('--sb-brand-scale', String(percent / 100));
    });
    sizeControls.filter(row => row.key === key).forEach(row => {
      row.input.value = String(percent);
      row.output.value = `${percent}%`;
      row.output.textContent = `${percent}%`;
    });
    return percent;
  }

  const sizeSaver = createSerialSizeSaver({
    readPersisted(key) {
      return (sizeControls.find(row => row.key === key) || {}).saved || 100;
    },
    applyPreview: applySize,
    async requestSave(item) {
      let response;
      try {
        response = await fetch(
          `/api/client/${encodeURIComponent(slug)}/signal-board/logo-size`,
          {
            method: 'POST', credentials: 'same-origin',
            headers: {
              'Content-Type': 'application/json',
              'X-LILA-Editor-Token': editorToken,
            },
            body: JSON.stringify({
              kind: item.target.dataset.brandKind,
              key: item.key,
              label: item.target.dataset.brandLabel,
              percent: item.percent,
            }),
          },
        );
      } catch (_error) {
        throw new Error('Could not reach Command Center to save the logo size.');
      }
      const payload = await response.json().catch(() => ({}));
      if (!response.ok) {
        throw new Error(
          payload.error || `Logo-size update failed (${response.status})`);
      }
      return payload;
    },
    commit(key, percent) {
      sizeControls.filter(row => row.key === key)
        .forEach(row => { row.saved = percent; });
    },
    succeed(payload) {
      toast(payload.message || 'Image size saved. Refresh the report to certify it.');
    },
    fail(error, _item, persisted) {
      const message = error && error.message
        ? error.message : 'Logo-size update failed.';
      toast(`${message} Preview restored to ${persisted}%.`, true);
    },
  });

  function saveSize(target, value) {
    return sizeSaver.schedule({
      key: target.dataset.brandKey,
      percent: canonicalSize(value),
      target,
    });
  }

  async function save(target, file) {
    const form = new FormData();
    form.append('kind', target.dataset.brandKind);
    form.append('key', target.dataset.brandKey);
    form.append('label', target.dataset.brandLabel);
    form.append('file', file, file.name || 'dropped-logo');
    const response = await fetch(`/api/client/${encodeURIComponent(slug)}/signal-board/logo`, {
      method: 'POST', body: form, credentials: 'same-origin',
      headers: { 'X-LILA-Editor-Token': editorToken },
    });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(payload.error || `Upload failed (${response.status})`);
    previewFile(file, target.dataset.brandKey);
    toast(payload.message || 'Image saved. Refresh the report to certify it.');
  }

  function chooseFile(target) {
    const input = document.createElement('input');
    input.type = 'file';
    input.accept = 'image/png,image/jpeg,image/webp';
    input.hidden = true;
    input.addEventListener('change', async () => {
      const file = input.files && input.files[0];
      if (!file) return;
      try { await save(target, file); } catch (error) { toast(error.message, true); }
      input.remove();
    });
    document.body.appendChild(input);
    input.click();
  }

  async function saveHeaderCompanion(value) {
    const response = await fetch(
      `/api/client/${encodeURIComponent(slug)}/signal-board/header-companion`,
      {
        method: 'POST', credentials: 'same-origin',
        headers: {
          'Content-Type': 'application/json',
          'X-LILA-Editor-Token': editorToken,
        },
        body: JSON.stringify({ text: value }),
      },
    );
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) {
      throw new Error(payload.error || `Header-label update failed (${response.status})`);
    }
    headerCompanion.textContent = payload.text;
    toast(payload.message || 'Header label saved. Refresh the report to certify it.');
  }

  function openHeaderCompanionEditor(editButton) {
    const existing = document.querySelector('.sb-copy-editor');
    if (existing) {
      existing.querySelector('.sb-copy-input').focus();
      return;
    }

    const editor = document.createElement('form');
    editor.className = 'sb-copy-editor';
    editor.setAttribute('aria-label', 'Edit header label');
    const input = document.createElement('input');
    input.className = 'sb-copy-input';
    input.type = 'text';
    input.maxLength = 64;
    input.required = true;
    input.autocomplete = 'off';
    input.spellcheck = false;
    input.value = headerCompanion.textContent.trim();
    input.setAttribute('aria-label', 'Header label beside the client logo');
    const saveButton = document.createElement('button');
    saveButton.type = 'submit';
    saveButton.className = 'sb-copy-action';
    saveButton.textContent = 'Save';
    const cancelButton = document.createElement('button');
    cancelButton.type = 'button';
    cancelButton.className = 'sb-copy-action cancel';
    cancelButton.textContent = 'Cancel';
    editor.append(input, saveButton, cancelButton);

    function closeEditor({ restoreFocus = true } = {}) {
      editor.remove();
      editButton.hidden = false;
      editButton.setAttribute('aria-expanded', 'false');
      if (restoreFocus) editButton.focus();
    }

    cancelButton.addEventListener('click', () => closeEditor());
    input.addEventListener('keydown', event => {
      if (event.key === 'Escape') {
        event.preventDefault();
        closeEditor();
      }
    });
    editor.addEventListener('submit', async event => {
      event.preventDefault();
      input.disabled = true;
      saveButton.disabled = true;
      cancelButton.disabled = true;
      try {
        await saveHeaderCompanion(input.value);
        closeEditor({ restoreFocus: false });
      } catch (error) {
        input.disabled = false;
        saveButton.disabled = false;
        cancelButton.disabled = false;
        input.focus();
        toast(error.message, true);
      }
    });

    editButton.hidden = true;
    editButton.setAttribute('aria-expanded', 'true');
    editButton.insertAdjacentElement('afterend', editor);
    input.focus();
    input.select();
  }

  if (headerCompanion) {
    headerCompanion.classList.add('sb-copy-target');
    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'sb-copy-edit-button';
    button.textContent = 'Edit label';
    button.title = 'Edit the text beside the client logo';
    button.setAttribute('aria-label', button.title);
    button.setAttribute('aria-expanded', 'false');
    button.addEventListener('click', event => {
      event.preventDefault(); event.stopPropagation();
      openHeaderCompanionEditor(button);
    });
    headerCompanion.insertAdjacentElement('afterend', button);
  }

  targets.forEach(target => {
    target.classList.add('sb-mark-target');
    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'sb-mark-edit-button';
    button.textContent = '+';
    button.title = `Choose a replacement for ${displayLabel(target)}`;
    button.setAttribute('aria-label', button.title);
    button.addEventListener('click', event => {
      event.preventDefault(); event.stopPropagation(); chooseFile(target);
    });
    target.appendChild(button);

    const sizeButton = document.createElement('button');
    sizeButton.type = 'button';
    sizeButton.className = 'sb-mark-size-button';
    sizeButton.textContent = 'Size';
    sizeButton.title = `Resize ${displayLabel(target)}`;
    sizeButton.setAttribute('aria-label', sizeButton.title);
    sizeButton.setAttribute('aria-expanded', 'false');
    const sizePanel = document.createElement('div');
    sizePanel.className = 'sb-mark-size-panel';
    sizePanel.hidden = true;
    const sizeLabel = document.createElement('label');
    sizeLabel.textContent = `${target.dataset.brandKind === 'person' ? 'Portrait' : 'Logo'} size · ${displayLabel(target)}`;
    const sizeInput = document.createElement('input');
    sizeInput.type = 'range';
    sizeInput.min = '50';
    sizeInput.max = '200';
    sizeInput.step = '5';
    const initialSize = canonicalSize(target.dataset.brandSize);
    sizeInput.value = String(initialSize);
    sizeInput.setAttribute('aria-label', sizeLabel.textContent);
    const sizeOutput = document.createElement('output');
    sizeOutput.value = `${sizeInput.value}%`;
    sizeOutput.textContent = sizeOutput.value;
    sizePanel.append(sizeLabel, sizeInput, sizeOutput);
    sizeControls.push({
      key: target.dataset.brandKey,
      input: sizeInput,
      output: sizeOutput,
      saved: initialSize,
    });
    sizeButton.addEventListener('click', event => {
      event.preventDefault(); event.stopPropagation();
      document.querySelectorAll('.sb-mark-size-panel').forEach(panel => {
        if (panel !== sizePanel) panel.hidden = true;
      });
      document.querySelectorAll('.sb-mark-size-button').forEach(other => {
        if (other !== sizeButton) other.setAttribute('aria-expanded', 'false');
      });
      sizePanel.hidden = !sizePanel.hidden;
      sizeButton.setAttribute('aria-expanded', String(!sizePanel.hidden));
      if (!sizePanel.hidden) sizeInput.focus();
    });
    sizeInput.addEventListener('input', () => {
      applySize(target.dataset.brandKey, sizeInput.value);
    });
    sizeInput.addEventListener('change', () => {
      saveSize(target, sizeInput.value);
    });
    sizePanel.addEventListener('keydown', event => {
      if (event.key === 'Escape') {
        event.preventDefault();
        sizePanel.hidden = true;
        sizeButton.setAttribute('aria-expanded', 'false');
        sizeButton.focus();
      }
    });
    target.append(sizeButton, sizePanel);
    target.addEventListener('dragover', event => {
      event.preventDefault();
      if (event.dataTransfer) event.dataTransfer.dropEffect = 'copy';
      target.classList.add('is-drop');
    });
    target.addEventListener('dragleave', () => target.classList.remove('is-drop'));
    target.addEventListener('drop', async event => {
      event.preventDefault(); event.stopPropagation();
      target.classList.remove('is-drop');
      try {
        const file = await droppedFile(event.dataTransfer);
        await save(target, file);
      } catch (error) {
        toast(error.message, true);
      }
    });
  });
})();
