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
})();
