/* Shared action confirmation. Authorization and business rules stay server-side. */
(() => {
  'use strict';
  const dialog = document.getElementById('actionConfirmDialog');
  if (!dialog) return;
  const title = document.getElementById('actionConfirmTitle');
  const message = document.getElementById('actionConfirmMessage');
  const accept = document.getElementById('actionConfirmAccept');
  const cancel = document.getElementById('actionConfirmCancel');
  const feedback = document.getElementById('actionFeedback');
  const finalApproval = document.getElementById('actionFinalApproval');
  const finalCheckbox = document.getElementById('actionFinalCheckbox');
  const approvalDetails = document.getElementById('actionApprovalDetails');
  const categories = {
    logout: ['Confirm Sign Out', 'Are you sure you want to sign out of the system?', 'Sign Out'],
    ppmp: ['Approve PPMP?', 'Confirm that the document, items, quantities, units, and amounts have been reviewed.', 'Approve PPMP'],
    app: ['Approve APP?', 'Please verify the procurement items, funding details, and amounts before approving.', 'Approve APP'],
    pr: ['Approve Purchase Request?', 'Are you sure you want to approve this Purchase Request?', 'Approve Request'],
    po: ['Finalize Purchase Order?', 'Confirm the supplier, items, quantities, prices, and total amount before finalizing this order.', 'Finalize Order'],
    iar: ['Confirm Receiving?', 'Verify the received items and quantities before recording this transaction.', 'Confirm Receiving'],
    ris: ['Confirm Stock Release?', 'Confirm the items and quantities to be issued from inventory.', 'Release Stock'],
    delete: ['Delete Record?', 'This action may permanently remove the selected record. Continue?', 'Delete'],
    discard: ['Discard Changes?', 'You have unsaved changes. Are you sure you want to leave?', 'Discard Changes', 'Keep Editing']
  };
  let resolveAction = null, launcher = null, feedbackTimer;
  let submitting = new WeakSet(), approved = new WeakMap();
  function notify(text) {
    feedback.textContent = text;
    feedback.hidden = false;
    clearTimeout(feedbackTimer);
    feedbackTimer = setTimeout(() => { feedback.hidden = true; }, 7000);
  }
  function settle(value) {
    if (!resolveAction) return;
    const resolve = resolveAction;
    resolveAction = null;
    dialog.close();
    launcher?.focus();
    resolve(value);
  }
  function confirmAction(options = {}) {
    if (resolveAction) return Promise.resolve(false);
    const values = categories[options.category] || ['Confirm Action', options.message || 'Continue with this action?', options.confirm || 'Confirm'];
    title.textContent = values[0];
    message.textContent = values[1];
    accept.textContent = values[2];
    cancel.textContent = values[3] || 'Cancel';
    finalApproval.hidden = !options.finalApproval;
    finalCheckbox.checked = false;
    accept.disabled = !!options.finalApproval;
    approvalDetails.replaceChildren();
    if (options.finalApproval) {
      accept.textContent = 'Confirm Approval';
      (options.details || []).forEach(([label, value]) => {
        const term = document.createElement('dt'), description = document.createElement('dd');
        term.textContent = label; description.textContent = value;
        approvalDetails.append(term, description);
      });
    }
    dialog.dataset.danger = String(options.category === 'delete');
    launcher = document.activeElement;
    return new Promise(resolve => {
      resolveAction = resolve;
      dialog.showModal();
      cancel.focus();
    });
  }
  window.psmsConfirm = confirmAction;
  window.psmsNotify = notify;
  finalCheckbox.addEventListener('change', () => { accept.disabled = !finalCheckbox.checked; });
  accept.addEventListener('click', () => { if (!accept.disabled) settle(true); });
  cancel.addEventListener('click', () => settle(false));
  dialog.addEventListener('cancel', event => { event.preventDefault(); settle(false); });
  dialog.addEventListener('close', () => { if (resolveAction) settle(false); });
  function signature(form, button) {
    const data = new FormData(form);
    if (button?.name) data.append(button.name, button.value);
    return JSON.stringify([form.action, form.method, button?.formAction, button?.formMethod,
      // Browsers create a new empty File on each FormData snapshot. Its timestamp
      // changes even when the user never chooses a file. Selected files retain
      // their real timestamp so replacing an upload still requires confirmation.
      Array.from(data, ([key, value]) => [key, value instanceof File ?
        [value.name, value.size, value.type, value.name ? value.lastModified : 0] : value])]);
  }
  function pending(form, button) {
    submitting.add(form);
    form.setAttribute('aria-busy', 'true');
    if (button) {
      // Keep name/value and validation intact; disabling a submitter drops its payload.
      button.dataset.pendingText = button.textContent;
      button.textContent = 'Please wait…';
      button.setAttribute('aria-disabled', 'true');
    }
  }
  document.addEventListener('submit', async event => {
    const form = event.target, button = event.submitter;
    if (event.defaultPrevented || form.method.toLowerCase() !== 'post') return;
    if (submitting.has(form)) {
      event.preventDefault();
      notify('This request is already being submitted. Wait for the server response.');
      return;
    }
    const text = button?.dataset.confirm || form.dataset.confirm;
    const category = button?.dataset.confirmCategory || form.dataset.confirmCategory;
    if (!text && !category) { pending(form, button); return; }
    const current = signature(form, button);
    if (approved.get(form) === current) {
      approved.delete(form);
      pending(form, button);
      return;
    }
    event.preventDefault();
    const requiresFinal = form.dataset.finalApproval === 'true';
    let details = [];
    if (requiresFinal) { try { details = JSON.parse(form.dataset.approvalDetails || '[]'); } catch { return; } }
    if (!await confirmAction({category, message: text, finalApproval: requiresFinal, details})) return;
    if (!form.isConnected || button?.disabled || signature(form, button) !== current) {
      notify('The form changed. Review the current values and confirm again.');
      return;
    }
    if (requiresFinal) {
      let confirmation = form.querySelector('input[name="final_confirm"]');
      if (!confirmation) {
        confirmation = document.createElement('input');
        confirmation.type = 'hidden'; confirmation.name = 'final_confirm'; form.append(confirmation);
      }
      confirmation.value = 'yes';
    }
    approved.set(form, signature(form, button));
    form.requestSubmit(button || undefined);
    // Validation can cancel requestSubmit; never retain an unused confirmation.
    approved.delete(form);
    if (requiresFinal) form.querySelector('input[name="final_confirm"]').value = '';
  });
  document.addEventListener('click', async event => {
    const action = event.target.closest('a[data-confirm],a[data-confirm-category],button[data-confirm][type="button"]');
    if (!action || event.defaultPrevented) return;
    event.preventDefault();
    if (await confirmAction({category: action.dataset.confirmCategory, message: action.dataset.confirm})) {
      if (action.tagName === 'A') location.assign(action.href);
      else action.dispatchEvent(new CustomEvent('psms:confirmed', {bubbles: true}));
    }
  });
  window.addEventListener('pageshow', () => {
    submitting = new WeakSet();
    approved = new WeakMap();
    document.querySelectorAll('form[aria-busy]').forEach(form => form.removeAttribute('aria-busy'));
    document.querySelectorAll('[data-pending-text]').forEach(button => {
      button.textContent = button.dataset.pendingText;
      delete button.dataset.pendingText;
      button.removeAttribute('aria-disabled');
    });
  });
})();
