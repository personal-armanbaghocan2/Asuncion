/* Progressive enhancement: native IDs, validation and keyboard selection remain authoritative. */
(() => {
  'use strict';
  document.querySelectorAll('select[data-record-selector]').forEach((select, index) => {
    const wrapper = document.createElement('div');
    wrapper.className = 'record-selector';
    const search = document.createElement('input');
    search.type = 'search';
    search.placeholder = 'Search reference, item, office or supplier';
    search.setAttribute('aria-label', `Search ${select.dataset.recordSelector}`);
    const status = document.createElement('small');
    status.id = `record-search-status-${index}`;
    status.setAttribute('role', 'status');
    search.setAttribute('aria-describedby', status.id);
    const label = select.closest('label');
    if (label) {
      select.id ||= `procurement-record-${index}`;
      label.htmlFor = select.id;
      wrapper.className += ` record-field ${label.className}`;
      select.remove();
      label.before(wrapper);
      wrapper.append(label, search, select, status);
    } else {
      select.before(wrapper);
      wrapper.append(search, select, status);
    }
    const records = Array.from(select.options).filter(option => option.value);
    let matches = records;
    function filter() {
      const query = search.value.trim().toLocaleLowerCase();
      matches = records.filter(option => `${option.textContent} ${option.dataset.search || ''}`.toLocaleLowerCase().includes(query));
      records.forEach(option => { option.hidden = !matches.includes(option) && !option.selected; });
      status.textContent = `${matches.length} matching record(s). Use the list to select a record.`;
    }
    search.addEventListener('input', filter);
    search.addEventListener('keydown', event => {
      if (event.key === 'Enter') {
        event.preventDefault();
        if (matches.length) {
          select.value = matches[0].value;
          select.dispatchEvent(new Event('change', {bubbles: true}));
          select.focus();
        }
      } else if (event.key === 'Escape') {
        event.preventDefault();
        search.value = '';
        filter();
        select.focus();
      } else if (event.key === 'ArrowDown') {
        event.preventDefault();
        select.focus();
      }
    });
    select.addEventListener('change', filter);
    filter();
  });
})();
