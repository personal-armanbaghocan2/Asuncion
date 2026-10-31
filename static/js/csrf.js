/* Same-origin CSRF support for optional JavaScript actions and generated forms. */
(() => {
  'use strict';
  const unsafe = method => !['GET', 'HEAD', 'OPTIONS', 'TRACE'].includes(method.toUpperCase());
  const token = () => document.querySelector('meta[name="csrf-token"]')?.content || '';
  const local = target => {
    const url = new URL(target, document.baseURI);
    if (url.origin !== window.location.origin) throw new TypeError('A local action is required.');
    return url;
  };
  function attach(form) {
    if (!unsafe(form.method || 'GET')) return;
    local(form.action || document.baseURI);
    const value = token();
    if (!value) throw new Error('Refresh the page before submitting this form.');
    let field = form.querySelector('input[name="csrf_token"]');
    if (!field) {
      field = document.createElement('input');
      field.type = 'hidden'; field.name = 'csrf_token'; form.append(field);
    }
    field.value = value;
  }
  function request(input, options = {}) {
    const original = typeof Request !== 'undefined' && input instanceof Request;
    local(original ? input.url : input);
    const method = String(options.method || (original ? input.method : 'GET')).toUpperCase();
    const headers = new Headers(options.headers || (original ? input.headers : undefined));
    if (unsafe(method)) {
      const value = token();
      if (!value) throw new Error('Refresh the page before submitting this action.');
      headers.set('X-CSRFToken', value);
    }
    return window.fetch(input, {...options, method, headers, credentials: 'same-origin'});
  }
  window.PSMSCSRF = Object.freeze({attach, fetch: request});
})();
