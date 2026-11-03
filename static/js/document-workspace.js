/* Native submissions retain session validation, CSRF and confirmation. */
(() => {
  'use strict';
  document.addEventListener('submit',event=>{
    const form=event.target;
    if(event.defaultPrevented||!form.matches?.('form[data-document-processing]'))return;
    const notice=form.querySelector('[data-processing-message]');
    if(notice){notice.hidden=false;notice.dataset.category='info';notice.textContent='Processing the document request. Wait for the saved extraction result before continuing.';}
  });
  document.addEventListener('click',event=>{
    const link=event.target.closest('a[href^="#edit-"]');
    if(!link)return;
    const editor=document.getElementById(link.hash.slice(1));
    if(editor){editor.open=true;editor.querySelector('select,textarea,input')?.focus();}
  });
})();
