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
})();
