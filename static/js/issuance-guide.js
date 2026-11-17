/* Issuance selection aid; stock, quantities and approval remain server-validated. */
(() => {
  'use strict';
  const selected = new URLSearchParams(window.location.search).get('inventory_id');
  document.querySelectorAll('form[data-issuance-guide]').forEach(form => {
    const summary = document.createElement('p');
    summary.className = 'notice';
    summary.setAttribute('role', 'status');
    summary.setAttribute('aria-live', 'polite');
    const submit = form.querySelector('button[type="submit"]');
    if (!submit) return;
    submit.before(summary);
    const boxes = [...form.querySelectorAll('input[name="inventory_id"]')];
    boxes.forEach(box => { if (box.value === selected) box.checked = true; });
    if (selected && boxes.some(box => box.checked)) {
      const panel = form.closest('[role="tabpanel"]');
      const tab = panel && document.getElementById(panel.getAttribute('aria-labelledby'));
      if (tab) tab.click();
    }
    const update = () => {
      let count = 0, invalid = false;
      const quantities = new Map();
      boxes.filter(box => box.checked).forEach(box => {
        count++;
        const row = box.closest('tr');
        const input = row.querySelector('input[name^="issue_qty_"]');
        const unit = row.lastElementChild.textContent.trim();
        const quantity = Number(input.value);
        if (!input.checkValidity() || !Number.isFinite(quantity) || quantity <= 0) invalid = true;
        else quantities.set(unit, (quantities.get(unit) || 0) + quantity);
      });
      submit.disabled = count === 0 || invalid;
      summary.textContent = count === 0
        ? (boxes.length ? 'Select stock and enter the quantity to issue.' : 'No available stock. Complete IAR acceptance before creating an issuance.')
        : invalid ? 'Correct the selected quantities. Each must be positive and within available stock.'
        : `${count} selected line(s): ${[...quantities].map(([unit, quantity]) => `${Number(quantity.toFixed(4))} ${unit}`).join(', ')}. Create saves For Review; stock changes only after approval.`;
    };
    form.addEventListener('input', update);
    form.addEventListener('change', update);
    update();
  });
})();
