/* Source-record previews. APP loads the exact selected PPMP from the server by primary key. */
(() => {
  'use strict';

  function money(value) {
    const number = Number(value);
    return Number.isFinite(number)
      ? new Intl.NumberFormat('en-PH', { style: 'currency', currency: 'PHP', minimumFractionDigits: 2 }).format(number).replace('PHP', '₱')
      : '—';
  }

  function textNode(tag, text, className) {
    const el = document.createElement(tag);
    if (className) el.className = className;
    el.textContent = text == null || text === '' ? '—' : String(text);
    return el;
  }

  function sourceUrl(endpoint, value) {
    const encoded = encodeURIComponent(value);
    if (endpoint.includes('{id}')) return endpoint.replace('{id}', encoded);
    const clean = endpoint.replace(/\/+$/, '');
    // Backward compatibility with older templates that used /api/ppmp/ as a prefix.
    if (/\/api\/ppmp$/i.test(clean)) return `${clean}/${encoded}/source`;
    return `${clean}/${encoded}`;
  }

  function renderPpmp(target, data) {
    target.replaceChildren();
    target.classList.remove('loading', 'error');

    const head = document.createElement('div');
    head.className = 'source-preview-head';
    const titleWrap = document.createElement('div');
    titleWrap.append(textNode('strong', data.no || 'PPMP'));
    titleWrap.append(textNode('span', `${data.type || '—'} · ${data.office || '—'}`));
    head.append(titleWrap, textNode('strong', money(data.total), 'source-preview-total'));
    target.append(head);

    const details = document.createElement('details');
    details.className = 'source-preview-details';
    details.append(textNode('summary', `Source fields (${(data.items || []).length} item(s))`));
    (data.items || []).forEach((item, index) => {
      const card = document.createElement('section');
      card.className = 'source-preview-item';
      const itemTitle = document.createElement('div');
      itemTitle.className = 'source-preview-item-title';
      itemTitle.append(textNode('b', (data.items || []).length > 1 ? `Item ${index + 1}` : 'Selected PPMP data'));
      if (item.item_id) itemTitle.append(textNode('small', item.item_id));
      card.append(itemTitle);

      const grid = document.createElement('dl');
      grid.className = 'source-preview-fields';
      Object.entries(item.fields || {}).forEach(([label, value]) => {
        const wrap = document.createElement('div');
        const shown = label === 'Estimated Budget Allocation (Php)' && value !== ''
          ? money(value)
          : (value == null || value === '' ? '—' : String(value));
        wrap.append(textNode('dt', label), textNode('dd', shown));
        grid.append(wrap);
      });
      card.append(grid);
      details.append(card);
    });
    target.append(details);

    if (!data.items?.length) {
      target.append(textNode('span', 'This PPMP has no saved item details.'));
    }
  }

  document.querySelectorAll('[data-source-preview]').forEach(select => {
    const target = document.getElementById(select.dataset.sourcePreview);
    if (!target) return;
    const initial = target.textContent;
    const parentForm = select.closest('form');
    const submitButton = parentForm?.querySelector('button[type="submit"]');
    let requestVersion = 0;

    function setReady(ready) {
      if (submitButton && select.dataset.sourceEndpoint) submitButton.disabled = !ready;
    }

    async function update() {
      const option = select.selectedOptions[0];
      const purpose = parentForm?.querySelector('[data-source-purpose]');
      if (purpose) purpose.value = option?.dataset.purpose || '';
      const endpoint = select.dataset.sourceEndpoint;
      const version = ++requestVersion;
      if (!option?.value) {
        target.classList.remove('loading', 'error');
        target.textContent = initial;
        setReady(false);
        return;
      }

      if (endpoint) {
        target.classList.remove('error');
        target.classList.add('loading');
        target.textContent = 'Loading selected PPMP data…';
        setReady(false);
        try {
          const response = await fetch(sourceUrl(endpoint, option.value), {
            headers: { 'Accept': 'application/json' },
            credentials: 'same-origin',
            cache: 'no-store'
          });
          if (!response.ok) throw new Error(`HTTP ${response.status}`);
          const contentType = response.headers.get('content-type') || '';
          if (!contentType.includes('application/json')) throw new Error('Unexpected response');
          const data = await response.json();
          if (version !== requestVersion || String(select.value) !== String(option.value)) return;
          renderPpmp(target, data);
          setReady(true);
        } catch (error) {
          if (version !== requestVersion) return;
          target.classList.remove('loading');
          target.classList.add('error');
          target.textContent = 'Unable to load the selected PPMP. Please choose the PPMP again or refresh the page.';
          setReady(false);
        }
        return;
      }

      // Existing lightweight preview behavior for PR/PO/IAR selectors.
      target.replaceChildren();
      for (const [key, tag] of [['heading','strong'],['detail','span'],['amount','span'],['items','small']]) {
        if (option.dataset[key]) target.append(textNode(tag, option.dataset[key]));
      }
    }

    select.addEventListener('change', update);
    update();
  });

  const sourceForm = document.getElementById('datasetRecordsForm');
  sourceForm?.addEventListener('submit', event => {
    const id = event.submitter?.dataset.sendRow;
    if (!id) return;
    const boxes = Array.from(sourceForm.querySelectorAll('[name=record_ids]'));
    const previous = boxes.map(box => box.checked);
    boxes.forEach(box => { box.checked = box.value === id; });
    setTimeout(() => boxes.forEach((box, i) => { box.checked = previous[i]; }), 0);
  });
})();
