/* Select a visible source pair; the server remains authoritative for every request. */
(() => {
  'use strict';
  const form = document.querySelector('form[data-pr-po-selection]');
  if (!form) return;
  const pr = form.querySelector('[name="pr_id"]');
  const po = form.querySelector('[name="po_id"]');
  const button = form.querySelector('button[type="submit"]');
  const notice = document.querySelector('[data-pr-po-selection-message]');
  if (!pr || !po || !button || !notice) return;
  const update = (selectLinked = false) => {
    const option = po.selectedOptions[0];
    const expected = option?.dataset.prId || '';
    for (const choice of pr.options) choice.disabled = Boolean(choice.value && expected && choice.value !== expected);
    if (selectLinked && expected) pr.value = expected;
    const pairReady = Boolean(pr.value && po.value && (!expected || pr.value === expected));
    const existing = option?.dataset.hasVerification === 'yes';
    button.textContent = existing ? 'Open Saved Verification' : 'Run Verification';
    button.disabled = !pairReady || (!existing && (form.dataset.inferenceActive !== 'yes' || form.dataset.saveReady !== 'yes'));
    notice.textContent = expected && pr.value !== expected
      ? 'The corresponding PR is not available in the approved list. Open the saved record or review its source status.'
      : !po.value ? 'Choose an approved PO. Its recorded PR is selected automatically when available.'
      : existing ? 'This PO already has verification history. Open its saved result to continue review; this does not rerun the model or change approval.'
      : expected ? 'The recorded PR is selected for this PO. Check both source documents before running verification.'
      : 'This PO has no recorded PR association. Choose the corresponding approved PR; the server will check its authority.';
  };
  po.addEventListener('change', () => update(true));
  pr.addEventListener('change', () => update());
  update(true);
})();
