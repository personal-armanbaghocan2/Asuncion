/* Presentation only: all existing server-side validation remains authoritative. */
(() => {
  'use strict';
  const $ = id => document.getElementById(id);
  const sidebar = $('sideNav'), toggle = $('navToggle'), edge = $('navEdge');

  // Local date/time display. No network request is used.
  function updateHeaderClock() {
    const now = new Date();
    const dateEl = $('header-date'), timeEl = $('header-time');
    if (dateEl) dateEl.textContent = now.toLocaleDateString('en-US', {weekday:'long', year:'numeric', month:'long', day:'numeric'});
    if (timeEl) timeEl.textContent = now.toLocaleTimeString('en-US', {hour:'numeric', minute:'2-digit', second:'2-digit', hour12:true});
  }
  updateHeaderClock();
  if ($('header-time')) setInterval(updateHeaderClock, 1000);

  // Accessible account dropdown shared by all authenticated pages.
  const accountButton = $('accountMenuButton'), accountMenu = $('accountMenu');
  function setAccountMenu(open) {
    if (!accountButton || !accountMenu) return;
    accountButton.setAttribute('aria-expanded', String(open));
    accountMenu.hidden = !open;
    if (open) accountMenu.querySelector('[role=menuitem]')?.focus();
  }
  accountButton?.addEventListener('click', e => { e.stopPropagation(); setAccountMenu(accountButton.getAttribute('aria-expanded') !== 'true'); });
  document.addEventListener('click', e => { if (accountMenu && !accountMenu.hidden && !e.target.closest('.account-menu-wrap')) setAccountMenu(false); });
  document.addEventListener('keydown', e => {
    if (e.key === 'Escape' && accountMenu && !accountMenu.hidden) { setAccountMenu(false); accountButton?.focus(); }
    if (accountMenu && !accountMenu.hidden && ['ArrowDown','ArrowUp'].includes(e.key)) {
      const items = Array.from(accountMenu.querySelectorAll('[role=menuitem]'));
      if (!items.length) return;
      e.preventDefault();
      const i = items.indexOf(document.activeElement);
      const next = e.key === 'ArrowDown' ? (i + 1) % items.length : (i <= 0 ? items.length - 1 : i - 1);
      items[next].focus();
    }
  });
  const desktopShell = () => matchMedia('(min-width:901px)').matches;
  let hideTimer, openedByClick = false;
  function hasDialog() { return !!document.querySelector('dialog[open]'); }
  function openNav(clicked = false) {
    if (!sidebar || hasDialog()) return;
    clearTimeout(hideTimer); openedByClick = clicked;
    sidebar.inert = false;
    document.body.classList.add('nav-open');
    toggle?.setAttribute('aria-expanded','true'); edge?.setAttribute('aria-expanded','true');
    $('navScrim').hidden = !clicked;
  }
  function closeNav(restore = false) {
    if (!sidebar) return;
    if (desktopShell()) { sidebar.inert = false; document.body.classList.remove('nav-open'); return; }
    clearTimeout(hideTimer);
    if (sidebar.contains(document.activeElement)) { if (restore) toggle.focus(); else document.activeElement.blur(); }
    sidebar.inert = true;
    document.body.classList.remove('nav-open');
    toggle?.setAttribute('aria-expanded','false'); edge?.setAttribute('aria-expanded','false');
    $('navScrim').hidden = true; openedByClick = false;
  }
  function scheduleHide() {
    clearTimeout(hideTimer);
    hideTimer = setTimeout(() => {
      if (!sidebar.matches(':hover') && !sidebar.contains(document.activeElement)) closeNav();
    }, 220);
  }
  window.closeNavigation = closeNav;
  if (sidebar) {
    const hover = matchMedia('(hover:hover) and (pointer:fine)');
    if (edge) {
      edge.addEventListener('pointerenter', () => { if (hover.matches) openNav(); });
      edge.addEventListener('pointerleave', () => { if (hover.matches) scheduleHide(); });
      edge.addEventListener('click', () => { openNav(true); sidebar.querySelector('a')?.focus(); });
    }
    toggle?.addEventListener('click', () => {
      if (document.body.classList.contains('nav-open')) closeNav(true);
      else { openNav(true); sidebar.querySelector('a')?.focus(); }
    });
    sidebar.addEventListener('pointerenter', () => clearTimeout(hideTimer));
    sidebar.addEventListener('pointerleave', () => { if (hover.matches) scheduleHide(); });
    sidebar.addEventListener('focusout', () => { if (hover.matches) scheduleHide(); });
    // Pointer users should not leave stale focus holding the hover drawer open.
    sidebar.addEventListener('pointerdown', () => { openedByClick = true; });
    $('navClose')?.addEventListener('click', () => closeNav(true));
    $('navScrim')?.addEventListener('click', () => closeNav(true));
    document.addEventListener('keydown', e => { if (e.key === 'Escape' && !hasDialog()) closeNav(true); });
    document.addEventListener('pointermove', e => {
      if (!hover.matches || !document.body.classList.contains('nav-open') || !openedByClick) return;
      if (e.clientX > sidebar.getBoundingClientRect().right + 8 && sidebar.contains(document.activeElement)) {
        document.activeElement.blur(); scheduleHide();
      }
    });
    sidebar.inert = false;
    const sub = $('ppmpSub'), parent = $('ppmpToggle');
    parent?.addEventListener('click', () => {
      const expanded = parent.getAttribute('aria-expanded') !== 'true';
      parent.setAttribute('aria-expanded', String(expanded)); sub.hidden = !expanded;
    });
  }
  const logout = $('logoutModal');
  if (logout) {
    $('logoutOpenBtn').addEventListener('click', () => { closeNav(); logout.showModal(); });
    $('logoutCancelBtn').addEventListener('click', () => logout.close());
    logout.addEventListener('click', e => { if (e.target === logout && (e.clientX < logout.getBoundingClientRect().left || e.clientX > logout.getBoundingClientRect().right || e.clientY < logout.getBoundingClientRect().top || e.clientY > logout.getBoundingClientRect().bottom)) logout.close(); });
  }
  // Submit buttons use the submit handler, avoiding duplicate confirmations.
  // Do not disable the submitter: its name/value may choose the server action.
  // Shared confirmation and pending state live in confirm-actions.js.
  function grow(el) {
    if (!el || !el.offsetWidth) return;
    el.style.height = 'auto';
    el.style.height = Math.min(260, Math.max(el.rows > 1 ? 64 : 39, el.scrollHeight + 2)) + 'px';
  }
  window.resizeEditorFields = root => (root || document).querySelectorAll('textarea[data-grow]').forEach(grow);
  document.addEventListener('input', e => { if (e.target.matches('textarea[data-grow]')) grow(e.target); });
  window.addEventListener('resize', () => window.resizeEditorFields());
  window.resizeEditorFields();
  document.querySelectorAll('[data-password]').forEach(button => {
    button.addEventListener('click', () => {
      const input = $(button.dataset.password), visible = input.type === 'password';
      input.type = visible ? 'text' : 'password';
      button.textContent = visible ? 'Hide' : 'Show';
      button.setAttribute('aria-label', visible ? 'Hide password' : 'Show password');
      button.setAttribute('aria-pressed',String(visible));
    });
  });
  // Keep tables contained on narrow viewports; retain native table semantics.
  document.querySelectorAll('table.table').forEach(table => {
    if (!table.closest('.table-scroll,.review-table-wrap')) {
      const wrap = document.createElement('div'); wrap.className = 'table-scroll';
      table.before(wrap); wrap.append(table);
    }
    const wrap = table.parentElement; wrap.tabIndex = 0;
    wrap.setAttribute('role','region');
    wrap.setAttribute('aria-label', (table.closest('.card')?.querySelector('h3')?.textContent || 'Records') + ' table, scroll for more columns');
    table.querySelectorAll('th').forEach(th => th.setAttribute('scope','col'));
    // Older templates place header rows directly in tbody; move the all-th row to thead.
    if (!table.tHead && table.rows[0] && Array.from(table.rows[0].cells).every(c => c.tagName === 'TH')) {
      const row = table.rows[0]; table.createTHead().append(row);
    }
  });
  // Accessible labels for the original form controls, without changing payloads.
  document.querySelectorAll('input:not([type=hidden]),select,textarea').forEach((field, i) => {
    if (!field.id) field.id = 'ui-field-' + i;
    if (field.labels?.length || field.hasAttribute('aria-label')) return;
    const nearby = field.closest('.form-field')?.querySelector('label') || field.previousElementSibling;
    if (nearby?.tagName === 'LABEL' && !nearby.control) { nearby.htmlFor = field.id; return; }
    const cell = field.closest('td'), header = cell?.closest('table')?.rows[0]?.cells[cell.cellIndex]?.textContent;
    field.setAttribute('aria-label', header?.trim() || field.getAttribute('placeholder') || field.name?.replace(/_/g,' ') || 'Select record');
  });
  // Selection visibility for source and PPMP tables. Selection remains current-page only.
  ['ppmpTable','datasetTable'].forEach(id => {
    const table = $(id); if (!table) return;
    const status = document.createElement('p'); status.className = 'selection-summary'; status.setAttribute('aria-live','polite');
    table.closest('.table-scroll').before(status);
    function update() {
      const checks = Array.from(table.querySelectorAll('tbody input[type=checkbox]'));
      status.textContent = `${checks.filter(c => c.checked).length} selected on this page · ${checks.length} available`;
    }
    table.addEventListener('change', update); update();
  });
  // RIS and ICS keep separate existing forms, with an accessible tab presentation.
  const issueGrid = document.querySelector('.issuance-panels');
  if (issueGrid) {
    const panels = Array.from(issueGrid.children), tabs = document.createElement('div');
    tabs.className = 'issuance-tabs'; tabs.setAttribute('role','tablist'); tabs.setAttribute('aria-label','Issuance type');
    panels.forEach((panel,i) => {
      panel.id = 'issue-panel-'+i; panel.setAttribute('role','tabpanel'); panel.setAttribute('aria-labelledby','issue-tab-'+i);
      const tab = document.createElement('button'); tab.type='button'; tab.id='issue-tab-'+i; tab.setAttribute('role','tab');
      tab.setAttribute('aria-controls',panel.id); tab.textContent = i ? 'ICS · Equipment' : 'RIS · Consumables';
      tab.addEventListener('click', () => select(i));
      tab.addEventListener('keydown', e => { if (['ArrowLeft','ArrowRight','Home','End'].includes(e.key)) { e.preventDefault(); const next = e.key==='Home'?0:e.key==='End'?1:1-i; select(next); tabs.children[next].focus(); } });
      tabs.append(tab);
    });
    function select(index) { panels.forEach((p,i) => { p.hidden=i!==index; tabs.children[i].setAttribute('aria-selected',String(i===index)); tabs.children[i].tabIndex=i===index?0:-1; }); }
    issueGrid.before(tabs); select(0);
  }
})();

// Reject oversized files before the host's gateway receives the upload.
document.addEventListener('submit', function (event) {
  const limit = Number(document.querySelector('meta[name="document-upload-limit"]')?.content);
  if (!Number.isFinite(limit) || limit <= 0) return;
  for (const input of event.target.querySelectorAll('input[type="file"]')) {
    if (Array.from(input.files || []).some(file => file.size > limit)) {
      event.preventDefault();
      alert(`The selected document exceeds the ${limit.toLocaleString()}-byte upload limit. Split or reduce it before uploading.`);
      return;
    }
  }
}, true);
