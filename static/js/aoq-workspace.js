/* Presentation only. Existing CSRF, revision, save and approval handlers remain authoritative. */
(() => {
  'use strict';
  const form=document.getElementById('aoqReviewForm');
  if(form){
    const action=form.elements.namedItem('action');
    const reviewer=form.elements.namedItem('review_supplier_index');
    const winner=form.elements.namedItem('winner_index');
    const loaded=form.elements.namedItem('offer_supplier_index');
    const load=document.getElementById('loadSupplierReview');
    const submitReview=()=>{action.value='review_supplier';form.requestSubmit(load);};
    reviewer.addEventListener('change',submitReview);
    winner.addEventListener('change',()=>{if(winner.value){reviewer.value=winner.value;submitReview();}});
    form.addEventListener('submit',event=>{
      const reviewing=event.submitter===load;
      action.value=reviewing?'review_supplier':'save_ocr';
      if(reviewing)delete form.dataset.confirm;
      else form.dataset.confirm='Save the reviewed quotation and explicitly selected winning supplier?';
      if(!reviewing && (!winner.value || winner.value!==loaded.value)){
        event.preventDefault();document.getElementById('aoq-review-message').textContent='Select and review the winning supplier before saving.';
      }
    });
    // Never overwrite source totals when quantity or unit price changes.
    form.addEventListener('input',event=>{
      const cell=event.target.closest('[data-quotation]');if(!cell)return;
      const number=name=>{const value=cell.querySelector(`[name="${name}"]`)?.value;return value?.trim()?Number(value):NaN;};
      const q=number('offer_qty'),cost=number('offer_unit_cost'),amount=number('offer_amount');
      const warning=cell.querySelector('[data-arithmetic-warning]');
      if(warning)warning.textContent=Number.isFinite(q)&&Number.isFinite(cost)&&Number.isFinite(amount)&&Math.abs(q*cost-amount)>.005?'Quantity × unit price differs from the source total. Review the original.':'';
    });
  }
  const records=document.getElementById('aoq-open-record');
  records?.addEventListener('change',()=>{if(records.value)window.location.assign(records.value);});
})();
