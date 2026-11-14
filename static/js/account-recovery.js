/* Bearer token stays in the fragment until copied into a CSRF-protected form.
   No analytics, fetch, auto-submit, logging or third-party resources. */
(() => {
  const params = new URLSearchParams(window.location.hash.slice(1));
  const token = params.get('token') || '', purpose = params.get('purpose') || '';
  window.history.replaceState(null, '', window.location.pathname);
  const form = document.getElementById('recovery-form');
  if (!form) return;
  if (!/^[0-9a-f]{64}$/.test(token) || !['verify', 'reset'].includes(purpose)) {
    document.getElementById('recovery-message').textContent = 'Open the complete link from your email.';
    form.hidden = true; return;
  }
  form.elements.token.value = token;
  form.elements.purpose.value = purpose;
  document.getElementById('recovery-passwords').hidden = purpose !== 'reset';
  form.querySelectorAll('input[type=password]').forEach(input => {input.required = purpose === 'reset';input.disabled = purpose !== 'reset';});
  document.getElementById('recovery-submit').textContent = purpose === 'verify' ? 'Verify email ownership' : 'Set new password';
})();
