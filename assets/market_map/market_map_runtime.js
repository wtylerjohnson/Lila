(async () => {
    const root = document.documentElement;
    const reportId = root.dataset.reportId || 'federal-market-map';
    const storageKey = `${reportId}:draft:${encodeURIComponent(location.pathname)}`;
    const status = document.querySelector('[data-report-status]');
    const stateNode = document.getElementById('embeddedState');
    const drawer = document.getElementById('workDrawer');
    const topWorkButton = document.querySelector('[data-action="toggle-work"]');
    let editing = false;
    let activeField = null;
    let baseline = null;
    let autoTimer = null;

    const setStatus = (message) => { if (status) status.textContent = message; };
    const fields = () => [...document.querySelectorAll('[data-edit-id]')];
    const logos = () => [...document.querySelectorAll('[data-logo-slot-id]')];

    const capture = () => ({
      version: 2,
      reportId,
      savedAt: new Date().toISOString(),
      fields: Object.fromEntries(fields().map(node => [node.dataset.editId, { html: node.innerHTML, style: node.getAttribute('style') || '' }])),
      logos: Object.fromEntries(logos().map(node => [node.dataset.logoSlotId, { html: node.innerHTML, style: node.getAttribute('style') || '' }])),
      links: Object.fromEntries([...document.querySelectorAll('[data-edit-link]')].map((node, index) => [index, { href: node.getAttribute('href') || '', html: node.innerHTML }]))
    });

    const apply = (state) => {
      if (!state || state.reportId !== reportId) return false;
      Object.entries(state.fields || {}).forEach(([id, value]) => {
        const node = document.querySelector(`[data-edit-id="${CSS.escape(id)}"]`);
        if (!node) return;
        if (typeof value === 'string') node.innerHTML = value;
        else { node.innerHTML = value.html ?? node.innerHTML; if (value.style) node.setAttribute('style', value.style); else node.removeAttribute('style'); }
      });
      Object.entries(state.logos || {}).forEach(([id, value]) => {
        const node = document.querySelector(`[data-logo-slot-id="${CSS.escape(id)}"]`);
        if (!node) return;
        if (typeof value === 'string') node.innerHTML = value;
        else { node.innerHTML = value.html ?? node.innerHTML; if (value.style) node.setAttribute('style', value.style); }
      });
      Object.entries(state.links || {}).forEach(([index, value]) => {
        const node = [...document.querySelectorAll('[data-edit-link]')][Number(index)];
        if (!node) return;
        node.href = value.href || node.href;
        if (value.html) node.innerHTML = value.html;
      });
      return true;
    };

    const readEmbedded = () => { try { return JSON.parse(stateNode?.textContent || '{}'); } catch (_) { return null; } };
    const readLocal = () => { try { return JSON.parse(localStorage.getItem(storageKey)); } catch (_) { return null; } };
    const writeEmbedded = (state = capture()) => { if (stateNode) stateNode.textContent = JSON.stringify(state).replace(/<\//g, '<\\/'); return state; };
    const persist = (label = 'Saved in this browser') => {
      const state = writeEmbedded();
      try { localStorage.setItem(storageKey, JSON.stringify(state)); } catch (_) {}
      setStatus(label);
      return state;
    };

    const toDataUrl = (blob) => new Promise((resolve, reject) => { const reader = new FileReader(); reader.onload = () => resolve(reader.result); reader.onerror = reject; reader.readAsDataURL(blob); });
    const embedExternalImages = async () => {
      const images = [...document.querySelectorAll('img[data-portable-image]')];
      await Promise.all(images.map(async image => {
        const src = image.getAttribute('src') || '';
        if (src.startsWith('data:image/')) return;
        try { const response = await fetch(src); if (!response.ok) return; image.src = await toDataUrl(await response.blob()); } catch (_) {}
      }));
    };

    await embedExternalImages();
    const initialState = capture();
    const embedded = readEmbedded();
    baseline = embedded?.reportId === reportId ? embedded : initialState;
    const local = readLocal();
    if (!apply(local)) apply(embedded);
    writeEmbedded(capture());

    const toggleEdit = () => {
      editing = !editing;
      document.body.classList.toggle('is-editing', editing);
      fields().forEach(node => { node.contentEditable = editing ? 'true' : 'false'; node.spellcheck = editing; });
      const button = document.querySelector('[data-action="toggle-edit"]');
      if (button) { button.classList.toggle('is-active', editing); button.setAttribute('aria-pressed', String(editing)); button.textContent = editing ? 'Finish editing' : 'Edit report'; }
      setStatus(editing ? 'Editing enabled · text, links, logos and seals autosave' : 'Editing finished · portable state retained');
    };

    fields().forEach(node => node.addEventListener('focus', () => { activeField = node; }));
    document.addEventListener('input', event => {
      if (!editing || !event.target.closest('[data-edit-id]')) return;
      clearTimeout(autoTimer);
      autoTimer = setTimeout(() => persist('Draft autosaved'), 350);
    });

    const resizeText = (delta) => {
      if (!editing || !activeField) { setStatus('Select editable text first'); return; }
      const current = parseFloat(getComputedStyle(activeField).fontSize) || 16;
      activeField.style.fontSize = `${Math.max(8, Math.min(120, current + delta))}px`;
      persist('Text size saved');
    };

    const imageInto = (slot, file) => {
      if (!file || !file.type.startsWith('image/')) { setStatus('Please use an image file'); return; }
      const reader = new FileReader();
      reader.onload = () => imageDataInto(slot, reader.result);
      reader.readAsDataURL(file);
    };

    const imageDataInto = (slot, source) => {
      if (!slot || !String(source || '').startsWith('data:image/')) { setStatus('Please use an image file'); return false; }
      slot.innerHTML = '';
      slot.classList.remove('is-unmatched');
      slot.classList.add('has-image');
      const image = document.createElement('img');
      image.src = source;
      image.alt = slot.getAttribute('aria-label') || 'Embedded mark';
      slot.appendChild(image);
      persist('Logo or seal embedded into this report');
      return true;
    };

    window.__memoStudio = Object.freeze({
      embedImage: (slotId, source) => imageDataInto(document.querySelector(`[data-logo-slot-id="${CSS.escape(slotId)}"]`), source),
      capture
    });

    logos().forEach(slot => {
      slot.addEventListener('click', event => { if (!editing) return; event.preventDefault(); event.stopPropagation(); slot.focus(); });
      slot.addEventListener('dragover', event => { if (!editing) return; event.preventDefault(); slot.classList.add('is-drop'); });
      slot.addEventListener('dragleave', () => slot.classList.remove('is-drop'));
      slot.addEventListener('drop', event => { if (!editing) return; event.preventDefault(); slot.classList.remove('is-drop'); imageInto(slot, event.dataTransfer.files?.[0]); });
      slot.addEventListener('paste', event => { if (!editing) return; const file = [...(event.clipboardData?.files || [])][0]; if (file) { event.preventDefault(); imageInto(slot, file); } });
      slot.addEventListener('dblclick', event => { if (!editing) return; event.preventDefault(); event.stopPropagation(); const input = document.createElement('input'); input.type = 'file'; input.accept = 'image/*'; input.onchange = () => imageInto(slot, input.files?.[0]); input.click(); });
      slot.addEventListener('mouseup', () => { if (editing) persist('Logo or seal size saved'); });
    });

    document.querySelectorAll('[data-edit-link]').forEach(link => {
      link.title = 'Edit mode: double-click to change this URL';
      link.addEventListener('dblclick', event => {
        if (!editing) return;
        event.preventDefault(); event.stopPropagation();
        const next = prompt('Link URL', link.getAttribute('href') || '');
        if (next) { link.setAttribute('href', next); persist('Link updated'); }
      });
    });

    /* __MARKET_MAP_DATA__ */

    const setWorkButton = (open) => { if (topWorkButton) topWorkButton.textContent = open ? 'Hide your work' : 'Show your work'; };
    const openWork = (key = 'report') => {
      const detail = workDetails[key] || workDetails.report;
      document.getElementById('drawerTitle').textContent = detail.title;
      document.getElementById('workSummary').textContent = detail.summary;
      document.getElementById('workFormula').textContent = detail.formula;
      document.getElementById('workSelected').textContent = `Selected claim: ${detail.title}`;
      const sources = document.getElementById('workSources'); sources.innerHTML = '';
      detail.sources.forEach(([label, href]) => { const link = document.createElement('a'); link.href = href; link.target = '_blank'; link.rel = 'noopener noreferrer'; link.textContent = label; sources.appendChild(link); });
      drawer.classList.add('open'); setWorkButton(true); document.body.style.overflow = 'hidden';
      drawer.querySelector('.drawer-close')?.focus();
    };
    const closeWork = () => { drawer.classList.remove('open'); setWorkButton(false); document.body.style.overflow = ''; };
    document.querySelectorAll('.work-trigger').forEach(trigger => trigger.addEventListener('click', () => openWork(trigger.dataset.work || 'report')));
    topWorkButton?.addEventListener('click', () => drawer.classList.contains('open') ? closeWork() : openWork('report'));
    document.querySelectorAll('[data-action="close-work"]').forEach(button => button.addEventListener('click', closeWork));
    drawer.addEventListener('click', event => { if (event.target === drawer) closeWork(); });
    document.addEventListener('keydown', event => { if (event.key === 'Escape' && drawer.classList.contains('open')) closeWork(); });

    const download = () => {
      persist('Preparing portable HTML…');
      const clone = document.documentElement.cloneNode(true);
      clone.querySelectorAll('[contenteditable]').forEach(node => { node.removeAttribute('contenteditable'); node.removeAttribute('spellcheck'); });
      clone.querySelector('body')?.classList.remove('is-editing');
      clone.querySelector('#workDrawer')?.classList.remove('open');
      const editButton = clone.querySelector('[data-action="toggle-edit"]'); if (editButton) { editButton.classList.remove('is-active'); editButton.textContent = 'Edit report'; editButton.setAttribute('aria-pressed', 'false'); }
      const workButton = clone.querySelector('[data-action="toggle-work"]'); if (workButton) workButton.textContent = 'Show your work';
      const source = '<!doctype html>\n' + clone.outerHTML;
      const blob = new Blob([source], { type: 'text/html;charset=utf-8' });
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement('a');
      const base = (root.dataset.downloadName || document.title + '.html').replace(/\.html$/i, '');
      const stamp = new Date().toISOString().slice(0, 16).replace(/[:T]/g, '-');
      anchor.href = url; anchor.download = `${base} · SAVED ${stamp}.html`; document.body.appendChild(anchor); anchor.click(); anchor.remove();
      setTimeout(() => URL.revokeObjectURL(url), 1500);
      setStatus('Portable HTML saved · edits and images embedded');
    };

    document.querySelector('[data-action="toggle-edit"]')?.addEventListener('click', toggleEdit);
    document.querySelector('[data-action="text-smaller"]')?.addEventListener('click', () => resizeText(-1));
    document.querySelector('[data-action="text-larger"]')?.addEventListener('click', () => resizeText(1));
    document.querySelector('[data-action="save-browser"]')?.addEventListener('click', () => persist());
    document.querySelector('[data-action="reset"]')?.addEventListener('click', () => { if (!confirm('Reset this copy to its embedded starting state?')) return; apply(baseline); try { localStorage.removeItem(storageKey); } catch (_) {} writeEmbedded(baseline); setStatus('Reset to embedded starting state'); });
    document.querySelector('[data-action="download"]')?.addEventListener('click', download);
    document.querySelector('[data-action="print"]')?.addEventListener('click', () => window.print());
    document.addEventListener('keydown', event => { if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === 's') { event.preventDefault(); download(); } });

    setStatus('Portable memo ready · links, edits, logos and seals can be saved');
  })();
