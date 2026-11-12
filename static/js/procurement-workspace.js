/* Presentation only. Uploads and approvals remain authenticated server actions. */
(() => {
  const panel = document.getElementById('aoq-upload-panel');
  document.querySelectorAll('[data-open-upload]').forEach(link => {
    link.addEventListener('click', () => {
      if (!panel) return;
      panel.open = true;
      requestAnimationFrame(() => panel.querySelector('input[type="file"]')?.focus({preventScroll: true}));
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
      if (event.target.tagName === 'SELECT') apply();
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
