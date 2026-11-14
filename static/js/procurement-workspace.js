/* Presentation only. Uploads and approvals remain authenticated server actions. */
(() => {
  document.querySelectorAll('input[data-password-policy]').forEach(input => {
    const feedback = input.parentElement.querySelector('[data-password-feedback]');
    if (!feedback) return;
    const update = () => {
      const value = input.value, length = Array.from(value).length;
      const kinds = [/[\p{Lu}]/u,/[\p{Ll}]/u,/[\p{Nd}]/u,/[^\p{L}\p{N}\s]/u].filter(pattern=>pattern.test(value)).length;
      const phrase = Array.from(value.trim()).length >= 16 && new Set(value.trim().toLocaleLowerCase().split(/\s+/u)).size >= 3 && (value.match(/[\p{L}\p{N}]/gu)||[]).length >= 12;
      const meets = length >= 12 && length <= 1024 && !/[\u0000-\u001f\u007f]/.test(value) && (kinds>=3 || phrase);
      feedback.textContent = !value ? '' : meets ? 'Length and character requirements met; the server will validate the password.' :
        'Use at least 12 characters and three character types, or a 16-character passphrase with three distinct words and 12 letters or numbers. Outer spaces do not count toward passphrase length.';
    };
    input.addEventListener('input',update);update();
  });
  const panel = document.getElementById('aoq-upload-panel');
  const fileInput = document.getElementById('aoq-file-input');
  const chooseFile = document.getElementById('aoq-choose-file');
  const selectedFile = document.getElementById('aoq-selected-file');
  if (fileInput && chooseFile && selectedFile) {
    // Keep the actual multipart file input and browser validation. Opening the
    // picker is synchronous with a mouse/keyboard click, never an upload request.
    chooseFile.addEventListener('click', () => {
      if (!fileInput.matches(':disabled')) fileInput.click();
    });
    fileInput.addEventListener('change', () => {
      selectedFile.textContent = fileInput.files?.[0]?.name || 'No file selected';
    });
    fileInput.addEventListener('invalid', event => {
      event.preventDefault();
      selectedFile.textContent = 'Choose an AOQ or bidding result file first.';
      chooseFile.focus();
    });
    // Progressive enhancement: the native picker remains visible without JS.
    fileInput.hidden = true;
    chooseFile.hidden = false;
    selectedFile.hidden = false;
  }
  document.querySelectorAll('[data-open-upload]').forEach(link => {
    link.addEventListener('click', () => {
      if (!panel) return;
      panel.open = true;
      requestAnimationFrame(() => (chooseFile || panel.querySelector('input[type="file"]'))?.focus({preventScroll: true}));
    });
  });
  const params = new URLSearchParams(window.location.search);
  const draft = document.querySelector('[data-active-draft]');
  if (draft && params.has('draft_id') && !['q', 'status', 'page'].some(key => params.has(key))) {
    draft.scrollIntoView({block: 'start'});
  }
  const filters = document.querySelector('.aoq-filter-panel');
  const closeFilters = () => {
    if (!filters?.open) return;
    filters.open = false;
    filters.querySelector('summary')?.focus();
  };
  document.querySelector('[data-close-aoq-filters]')?.addEventListener('click', closeFilters);
  document.addEventListener('keydown', event => {
    if (event.key === 'Escape' && filters?.open && !document.querySelector('dialog[open]')) { closeFilters(); event.preventDefault(); }
  });
  const allowRecordNavigation = () => !document.querySelector('form[data-sync-dirty="true"]') ||
    window.confirm('You have unsaved document edits. Leave this page and discard those edits?');
  document.addEventListener('submit', event => {
    if (event.target.matches('[data-record-navigation]') && !allowRecordNavigation()) event.preventDefault();
  });
  document.addEventListener('click', event => {
    if (event.defaultPrevented || event.ctrlKey || event.metaKey || event.shiftKey || event.altKey || event.button !== 0) return;
    const link = event.target.closest?.('a');
    if (link?.closest('[data-record-navigation]') && !allowRecordNavigation()) event.preventDefault();
  });
  document.querySelectorAll('form[data-auto-filters]').forEach(form => {
    let timer = null, generation = 0;
    const cancel = () => { clearTimeout(timer); timer = null; generation += 1; };
    const apply = () => {
      cancel();
      // Submit a normal GET: browser navigation cancels superseded requests.
      // Do not replace editable rows with asynchronous/stale HTML fragments.
      if (form.reportValidity()) form.requestSubmit();
    };
    form.addEventListener('submit', cancel);
    form.addEventListener('change', event => {
      const control = event.target;
      // Date/number/status filters commit immediately on change. Search fields
      // keep the short typing debounce, including existing q/h_q text inputs.
      if (control.tagName === 'SELECT' || (control.tagName === 'INPUT' &&
          control.type !== 'hidden' && control.type !== 'search' && !/(?:^|_)q$/.test(control.name || ''))) apply();
    });
    form.addEventListener('input', event => {
      if (!event.target.matches('input:not([type="hidden"])')) return;
      cancel();
      const expected = generation;
      timer = setTimeout(() => { if (generation === expected) apply(); }, 400);
    });
    // Keep a usable Apply fallback when JavaScript is unavailable.
    form.querySelector('[data-auto-filter-submit]')?.setAttribute('hidden', '');
    window.addEventListener('pagehide', cancel);
  });
})();
