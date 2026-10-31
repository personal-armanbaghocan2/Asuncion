(() => {
  'use strict';
  const dataNode = document.getElementById('ocrRowsData'), dialog = document.getElementById('ocrReviewDialog');
  if (!dataNode || !dialog) return;
  let rows;
  try { rows = JSON.parse(dataNode.textContent || '[]'); } catch (_) { return; }
  const byId = new Map(rows.map(row => [String(row.temp_id), row]));
  const form = document.getElementById('ocrReviewForm');
  const fields = Array.from(dialog.querySelectorAll('[data-field-label]'));
  const labels = fields.map(field => field.dataset.fieldLabel);
  const tabs = document.getElementById('ocrItemTabs'), panel = document.getElementById('ocrReviewPanel');
  const message = document.getElementById('ocrReviewMessage'), saveButton = document.getElementById('ocrSaveReview');
  const reviewSelected = document.getElementById('reviewSelectedOCR');
  const checkboxes = Array.from(document.querySelectorAll('input[name="ocr_rows"]'));
  const statusBox = document.getElementById('ocrReviewStatus');
  const specification = 'Quantity and Size of the Project to be Procured';
  const difference = document.getElementById('ocrSourceDifference'), differenceConfirm = document.getElementById('ocrSourceDifferenceConfirm');
  let items = [], index = 0, original = '', saving = false, launcher;
  const clean = value => String(value ?? '').replace(/\u00a0/g, ' ').trim();
  function displayValue(label, value) {
    const text = clean(value);
    if (label === 'Quantity' && /^\d+\.\d+$/.test(text)) return text.replace(/\.?0+$/, '');
    if (!['Start of Procurement Activity', 'End of Procurement Activity', 'Expected Delivery/Implementation Period'].includes(label)) return value ?? '';
    const iso = text.match(/^(\d{4})-(0[1-9]|1[0-2])(?:-\d{2}(?:[ T]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})?)?)?$/);
    if (iso) return `${iso[2]}/${iso[1]}`;
    const named = text.match(/^([A-Za-z]+)\s+(\d{4})$/);
    const months = ['january','february','march','april','may','june','july','august','september','october','november','december'];
    const month = named ? months.indexOf(named[1].toLowerCase()) : -1;
    return month < 0 ? value ?? '' : `${String(month + 1).padStart(2, '0')}/${named[2]}`;
  }
  // Compare a stable field order, independent of backend JSON object ordering.
  const snapshot = () => JSON.stringify(items.map(item => labels.map(label => clean(item.fields[label]))));
  function specificationsOnly(value, quantity, unit, item) {
    const text = clean(value), prefix = `${clean(quantity)} ${clean(unit)}`;
    if (!clean(quantity) || !clean(unit)) return text;
    // Strip only the exact saved quantity/unit prefix, never a capacity or size.
    if (text.toLowerCase().startsWith(prefix.toLowerCase()) && /^(?:\s|[;,\-]|$)/.test(text.slice(prefix.length))) {
      return text.slice(prefix.length).replace(/^[\s;,\-]+/, '');
    }
    // Older table mapping joined separate specification and numeric Qty cells.
    // Remove that duplicated edge component only with independent source roles
    // and an unchanged extracted narrative. Keep the original stored evidence.
    const evidence = item.field_confidence?._source_evidence, originalFields = item.field_confidence?._original_fields || item.fields;
    const headers = (evidence?.headers || []).map(header => clean(header).toLowerCase());
    const separateColumns = headers.some(header => ['quantity','qty','requested quantity'].includes(header)) &&
      headers.some(header => ['specification','specifications','technical specifications','size'].includes(header));
    const sourceQuantity = clean(originalFields.Quantity), components = text.split(';').map(clean);
    if (separateColumns && text === clean(originalFields[specification]) && components.length > 1 && sourceQuantity) {
      if (components[0] === sourceQuantity) return components.slice(1).join('; ');
      if (components.at(-1) === sourceQuantity) return components.slice(0, -1).join('; ');
    }
    return text;
  }
  function values() {
    const result = Object.fromEntries(fields.map(field => [field.dataset.fieldLabel, field.value]));
    const item = items[index];
    // Formatting unchanged source months/quantities must never rewrite stored evidence.
    if (item) labels.forEach(label => {
      if (label !== specification && result[label] === String(displayValue(label, item.loaded[label]))) result[label] = item.loaded[label] ?? '';
    });
    // Keep an unchanged official narrative byte-for-byte. Edited specifications
    // map back to the existing schema without a second editable quantity.
    if (item && (clean(result.Quantity) !== clean(item.loaded.Quantity) || clean(result.Unit) !== clean(item.loaded.Unit) ||
        clean(result[specification]) !== item.loadedSpecifications)) {
      result[specification] = [clean(result.Quantity)+' '+clean(result.Unit), clean(result[specification])].filter(value => value.trim()).join('; ');
    } else if (item) result[specification] = item.loaded[specification];
    return result;
  }
  const stash = () => { if (items[index]) { items[index].fields = values(); items[index].source_changes_confirmed = differenceConfirm.checked; } };
  const selectedIds = () => [...new Set(checkboxes.filter(box => box.checked).map(box => box.value))];
  const readableFields = fields => Object.entries(fields || {}).map(([label, value]) =>
    `${label}:\n  ${typeof value === 'object' && value !== null ? JSON.stringify(value, null, 2) : value || 'Unresolved'}`).join('\n\n');
  const moneyText = value => {
    const text = String(value ?? '').trim();
    if (!text) return 'Unresolved';
    if (!/^\d+(?:\.\d{1,2})?$/.test(text)) return text;
    const [whole, fraction = ''] = text.split('.'); return `${whole}.${fraction.padEnd(2, '0')}`;
  };
  function updateSelection() {
    const count = selectedIds().length;
    reviewSelected.disabled = !count;
    document.getElementById('ocrSelectionCount').textContent = `${count} OCR item(s) selected on this page, including selected rows hidden by search. Changing pages clears selection.`;
  }
  function renderTabs() {
    tabs.replaceChildren();
    tabs.setAttribute('role', items.length > 1 ? 'tablist' : 'group');
    panel.setAttribute('role', items.length > 1 ? 'tabpanel' : 'region');
    if (items.length === 1) {
      const heading = document.createElement('strong'); heading.id = 'ocr-single-item'; heading.className = 'ocr-single-item';
      heading.textContent = 'Item 1'; tabs.append(heading); panel.setAttribute('aria-labelledby', heading.id); return;
    }
    items.forEach((item, position) => {
      const tab = document.createElement('button');
      tab.type = 'button'; tab.setAttribute('role', 'tab'); tab.id = `ocr-tab-${position}`;
      tab.setAttribute('aria-controls', panel.id); tab.setAttribute('aria-selected', String(position === index));
      tab.tabIndex = position === index ? 0 : -1;
      const description = item.fields['Item Name'] || item.fields[labels[0]] || item.temp_id;
      tab.textContent = `Item ${position + 1} · ${description}`; tab.title = description;
      tab.className = item.errors?.length ? 'has-errors' : '';
      tab.addEventListener('click', () => navigate(position));
      tab.addEventListener('keydown', event => {
        let next;
        if (event.key === 'ArrowRight') next = (position + 1) % items.length;
        if (event.key === 'ArrowLeft') next = (position - 1 + items.length) % items.length;
        if (event.key === 'Home') next = 0;
        if (event.key === 'End') next = items.length - 1;
        if (next !== undefined) { event.preventDefault(); navigate(next); tabs.children[next].focus(); }
      });
      tabs.append(tab);
    });
  }
  function load() {
    const item = items[index];
    item.loaded = {...item.fields};
    item.loadedSpecifications = specificationsOnly(item.fields[specification], item.fields.Quantity, item.fields.Unit, item);
    document.getElementById('ocrTempId').value = item.temp_id; document.getElementById('ocrRevision').value = item.revision;
    fields.forEach((field, position) => {
      const label = labels[position], info = item.field_confidence?.[label] || {};
      field.value = label === specification ? item.loadedSpecifications : displayValue(label, item.fields[label]);
      const level = info.level || 'Unmapped', badge = document.getElementById(`ocrConfidence${position}`);
      badge.textContent = level + (!info.human_corrected && typeof info.score === 'number' ? ` · ${Math.round(info.score * 100)}%` : '');
      badge.className = 'field-confidence ' + String(level).toLowerCase().replace(/\s+/g, '-');
      badge.title = info.human_corrected ? 'Human correction; this is not an OCR-verified source value.' : 'Mapping evidence score; not a calibrated probability that OCR is correct.';
      field.closest('label').classList.toggle('needs-attention', !['High', 'Verified', 'Human corrected'].includes(level));
    });
    statusBox.replaceChildren();
    const title = document.createElement('strong'); title.textContent = `${item.temp_id} · Item ${index + 1} of ${items.length}`;
    const state = document.createElement('span'); state.className = 'badge ' + (item.status === 'Reviewed' ? 'ok' : 'warn');
    state.textContent = item.status || 'Needs Review'; statusBox.append(title, state);
    document.getElementById('ocrStructuredData').textContent = readableFields(item.fields);
    const evidence = item.field_confidence?._source_evidence;
    const sourceLines = [];
    if (evidence) {
      for (const key of ['source', 'page', 'worksheet', 'row', 'rows', 'item_number']) {
        if (evidence[key] !== undefined && evidence[key] !== null && evidence[key] !== '') sourceLines.push(`${key}: ${evidence[key]}`);
      }
      if (evidence.headers) sourceLines.push(`Source headers: ${evidence.headers.join(' | ')}`);
      if (evidence.text && !evidence.cells?.length) sourceLines.push(`\nCaptured item text:\n${evidence.text}`);
      for (const cell of evidence.cells || []) {
        const label = evidence.headers?.[cell.column - 1] || `Column ${cell.column}`;
        sourceLines.push(`\n${label}: ${cell.raw_text ?? ''}`);
        if (cell.uncertain) sourceLines.push('Uncertain source cell. Confirm its value and column.');
      }
      if (evidence.warnings?.length) sourceLines.push(`\nReview warnings:\n${evidence.warnings.join('\n')}`);
    }
    document.getElementById('ocrItemEvidence').textContent = sourceLines.join('\n') || 'Item-specific positions were not captured. Compare the full source text.';
    const metadata = item.field_confidence?._document_metadata || {};
    const metadataNode = document.getElementById('ocrDocumentMetadata');
    if (metadataNode) metadataNode.textContent = ['Source-extracted context', ...['office', 'fiscal_year', 'category'].filter(key => metadata[key]).map(key => `${key.replace('_', ' ')}: ${metadata[key]}`),
      `Application category: ${dialog.dataset.category}`, item.extraction_method || 'Extraction method not recorded'].join(' · ');
    const sourceLink = document.getElementById('ocrOriginalSource'), preview = document.getElementById('ocrSourcePreview');
    sourceLink.hidden = !item.source_url; sourceLink.href = item.source_url || '';
    const extension = (item.source_filename || '').split('.').pop().toLowerCase();
    document.getElementById('ocrSourceKind').textContent = ({pdf:'PDF document',xlsx:'Excel workbook (native cell extraction)',xlsm:'Excel workbook (native cell extraction)',csv:'CSV (native extraction)',txt:'Text (native extraction)',docx:'Word document (native extraction)'})[extension] || 'Scanned image';
    preview.hidden = !item.source_url || !['pdf','png','jpg','jpeg','bmp'].includes(extension);
    const sourcePage = Number(evidence?.page);
    preview.dataset.sourceUrl = preview.hidden ? '' : item.source_url + (extension === 'pdf' && Number.isInteger(sourcePage) && sourcePage > 0 ? `#page=${sourcePage}` : '');
    preview.removeAttribute('src');
    if (document.getElementById('ocrSourceComparison').open && !preview.hidden) preview.src = preview.dataset.sourceUrl;
    document.getElementById('ocrPreviewUnavailable').hidden = !preview.hidden;
    const diagnosticLink = document.getElementById('ocrDiagnostics');
    diagnosticLink.hidden = !item.diagnostics_url; diagnosticLink.href = item.diagnostics_url || '';
    differenceConfirm.checked = item.source_changes_confirmed === true;
    updateDifference();
    const errors = document.getElementById('ocrItemErrors'); errors.replaceChildren();
    (item.errors || []).forEach(error => { const li = document.createElement('li'); li.textContent = error; errors.append(li); });
    errors.hidden = !item.errors?.length; panel.setAttribute('aria-labelledby', `ocr-tab-${index}`);
    renderTabs(); window.resizeEditorFields?.(dialog);
  }
  function updateDifference() {
    const item = items[index]; if (!item) return;
    const current = values(), source = item.field_confidence?._original_fields || item.fields;
    const currentQuantity = Number(current.Quantity), sourceQuantity = Number(source.Quantity);
    const persisted = byId.get(item.temp_id), quantityEvidence = item.field_confidence?.Quantity || {};
    const alreadyConfirmed = item.status === 'Reviewed' && quantityEvidence.human_confirmed === true && currentQuantity === Number(persisted?.fields.Quantity);
    const changed = !alreadyConfirmed && clean(current.Quantity) !== '' && Number.isFinite(currentQuantity) && currentQuantity > 0 && currentQuantity !== sourceQuantity;
    difference.hidden = !changed;
    differenceConfirm.required = changed;
    const quantity = Number(current.Quantity), cost = Number(current['Estimated Unit Cost']), budget = Number(current['Estimated Budget Allocation (Php)']);
    const mismatch = current['Estimated Unit Cost'] && Number.isFinite(quantity*cost) && Math.abs(quantity*cost-budget) > .005;
    document.getElementById('ocrConsistencyWarning').textContent = mismatch ? 'Quantity × unit cost differs from estimated budget. Correct the values before confirming.' : '';
    document.getElementById('ocrStructuredData').textContent = readableFields(current);
  }
  function navigate(next) {
    if (saving) return;
    stash(); index = next; load();
    tabs.children[next]?.focus();
    tabs.children[next]?.scrollIntoView?.({block: 'nearest', inline: 'nearest'});
  }
  function open(ids) {
    items = [...new Set(ids)].map(id => byId.get(String(id))).filter(row => row && !row.converted && row.status_code !== 'CANCELLED')
      .map(row => ({...row, fields: Object.fromEntries(labels.map(label => [label, String(row.fields?.[label] ?? '')])), errors: []}));
    if (!items.length) return;
    launcher = document.activeElement; index = 0; original = snapshot(); message.textContent = '';
    load(); dialog.showModal(); tabs.children[0]?.focus();
  }
  async function closeReview() {
    if (saving) return; stash();
    if (snapshot() !== original && !await window.psmsConfirm({category: 'discard'})) return;
    items = []; original = ''; dialog.close(); launcher?.focus();
  }
  checkboxes.forEach(box => box.addEventListener('change', updateSelection));
  reviewSelected.addEventListener('click', () => open(selectedIds()));
  document.addEventListener('click', event => {
    const button = event.target.closest('[data-ocr-review]'); if (button) open([button.dataset.ocrReview]);
    if (event.target.closest('[data-ocr-close]')) closeReview();
  });
  form.addEventListener('submit', async event => {
    event.preventDefault(); if (saving) return;
    stash(); saving = true; saveButton.disabled = true; fields.forEach(field => { field.disabled = true; });
    message.textContent = 'Validating and saving all selected reviews…';
    try {
      const body = new FormData(); body.set('csrf_token', form.querySelector('[name="csrf_token"]').value);
      body.set('action', 'update_ocr_rows'); body.set('ocr_batch', JSON.stringify(items.map(({temp_id, revision, fields, source_changes_confirmed}) => ({temp_id, revision, fields, source_changes_confirmed}))));
      // The hidden control named "action" shadows HTMLFormElement.action.
      // Read the attribute so a native browser posts to this review route.
      const response = await fetch(form.getAttribute('action') || window.location.href, {method: 'POST', body, credentials: 'same-origin', redirect: 'error'});
      const result = (response.headers.get('content-type') || '').includes('application/json') ? await response.json() : null;
      if (!response.ok || !result?.saved) {
        items.forEach(item => { item.errors = result?.errors?.[item.temp_id] || []; });
        const invalid = items.findIndex(item => item.errors.length); if (invalid >= 0) index = invalid; load();
        message.textContent = result?.message || (response.status === 409 ? 'Draft changed. Keep your corrections, then reopen the latest review before retrying.' : 'Save was not confirmed. Your edits remain here. Check saved draft revisions before retrying.');
        return;
      }
      const received = new Set((result.items || []).map(item => item.temp_id));
      if (received.size !== items.length || items.some(item => !received.has(item.temp_id))) throw new Error('Incomplete save confirmation');
      for (const saved of result.items) {
        Object.assign(byId.get(saved.temp_id), saved, {human_reviewed: true, status_code: 'REVIEWED'});
        Object.assign(items.find(item => item.temp_id === saved.temp_id), saved, {errors: []});
        const tr = document.getElementById(`ocr-${saved.temp_id}`);
        if (tr) {
          tr.cells[3].textContent = saved.fields[labels[0]] || saved.fields['Item Name'] || 'Unresolved';
          tr.cells[4].textContent = saved.fields.Quantity || 'Unresolved'; tr.cells[5].textContent = saved.fields.Unit || 'Unresolved';
          tr.cells[6].textContent = moneyText(saved.fields['Estimated Unit Cost']);
          tr.cells[7].textContent = moneyText(saved.fields['Estimated Budget Allocation (Php)']); tr.cells[8].textContent = 'Reviewed · Ready for PPMP';
          tr.querySelector('[name^="ocr_revision_"]').value = saved.revision;
        }
      }
      original = snapshot();
      // Persisted fields and revisions become the only source for reopening.
      // Close without running an unsaved-change prompt after confirmed success.
      items = []; original = ''; dialog.close(); launcher?.focus();
      const officialConfirm = document.querySelector('[name="confirm_reviewed"]');
      if (officialConfirm) officialConfirm.checked = false;
      window.psmsNotify?.(result.message || 'Review confirmed. Table updated.');
    } catch (_) { message.textContent = 'Save was not confirmed. Your corrections remain here. Check saved draft revisions before retrying.'; }
    finally { saving = false; saveButton.disabled = false; fields.forEach(field => { field.disabled = false; }); }
  });
  fields.forEach(field => field.addEventListener('input', () => { differenceConfirm.checked = false; updateDifference(); }));
  document.getElementById('ocrSourceComparison').addEventListener('toggle', event => {
    const preview = document.getElementById('ocrSourcePreview');
    if (event.target.open && preview.dataset.sourceUrl && !preview.getAttribute('src')) preview.src = preview.dataset.sourceUrl;
  });
  dialog.addEventListener('cancel', event => { event.preventDefault(); closeReview(); });
  dialog.addEventListener('click', event => {
    if (event.target !== dialog) return;
    const rect = dialog.getBoundingClientRect();
    if (event.clientX < rect.left || event.clientX > rect.right || event.clientY < rect.top || event.clientY > rect.bottom) closeReview();
  });
  window.addEventListener('beforeunload', event => {
    stash(); if (items.length && snapshot() !== original) { event.preventDefault(); event.returnValue = ''; }
  });
  updateSelection();
})();
