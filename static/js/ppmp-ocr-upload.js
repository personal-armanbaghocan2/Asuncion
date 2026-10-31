(() => {
  'use strict';
  const input = document.getElementById('ocrFile'), button = document.getElementById('ocrUploadButton');
  const status = document.getElementById('ocrUploadStatus'), message = document.getElementById('ocrUploadMessage');
  const link = document.getElementById('ocrExistingUpload'), retry = document.getElementById('ocrRetryCheck');
  const repeat = document.getElementById('ocrReprocessConfirmed');
  if (!input || !button || !status || !message || !link || !retry || !repeat) return;
  const readyStates = ['new', 'same_name', 'repeat_allowed'];
  const allowed = new Set(['pdf', 'png', 'jpg', 'jpeg', 'bmp', 'tif', 'tiff', 'docx', 'xlsx', 'xlsm', 'csv', 'txt']);
  const unavailable = 'Unable to verify whether this document was previously uploaded. Please try again.';
  let sequence = 0, controller, readyFile, uploading = false;
  function show(text, state) {
    message.textContent = text;
    status.dataset.state = state;
    retry.hidden = state !== 'unavailable';
    button.disabled = !readyStates.includes(state);
    repeat.value = state === 'repeat_allowed' ? 'yes' : '';
  }
  async function check() {
    const current = ++sequence, file = input.files?.[0];
    controller?.abort(); controller = new AbortController();
    readyFile = null; link.hidden = true; link.removeAttribute('href');
    if (!file) { show('', 'empty'); return; }
    if (!allowed.has(file.name.split('.').pop().toLowerCase())) {
      show('Unsupported file format. Please select a supported procurement document.', 'unsupported'); return;
    }
    show('Checking document…', 'checking');
    const pending = controller;
    const timeout = setTimeout(() => pending.abort(), 15000);
    try {
      if (!file.size || file.size > 8 * 1024 * 1024) throw new Error('Upload limit');
      const digest = await crypto.subtle.digest('SHA-256', await file.arrayBuffer());
      if (current !== sequence) return;
      const body = new FormData();
      body.set('sha256', Array.from(new Uint8Array(digest), value => value.toString(16).padStart(2, '0')).join(''));
      body.set('filename', file.name);
      body.set('csrf_token', input.form.querySelector('[name=csrf_token]').value);
      const response = await fetch(status.dataset.checkUrl, {method: 'POST', body, credentials: 'same-origin',
        cache: 'no-store', signal: controller.signal, headers: {'Accept': 'application/json'}});
      if (!response.ok || response.redirected) throw new Error('Check unavailable');
      const result = await response.json();
      if (current !== sequence) return;
      if (![...readyStates, 'duplicate', 'unsupported'].includes(result.status)) throw new Error('Invalid check');
      show(result.message, result.status);
      if (readyStates.includes(result.status)) readyFile = file;
      if (['duplicate', 'repeat_allowed'].includes(result.status) && result.existing_url) {
        const target = new URL(result.existing_url, location.href);
        if (target.origin === location.origin) { link.href = target.href; link.hidden = false; }
      }
    } catch (_) {
      if (current === sequence) show(unavailable, 'unavailable');
    } finally {
      clearTimeout(timeout);
    }
  }
  input.addEventListener('change', check);
  retry.addEventListener('click', check);
  input.form.addEventListener('submit', event => {
    if (event.submitter !== button) return;
    if (uploading || readyFile !== input.files?.[0]) { event.preventDefault(); return; }
    uploading = true;
    input.disabled = false;
    message.textContent = 'Extracting document…';
    // Let the browser capture the submitter and its formaction before disabling.
    setTimeout(() => { button.disabled = true; }, 0);
  });
  window.addEventListener('pageshow', () => { uploading = false; check(); });
  check();
})();
