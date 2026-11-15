/* Presentation only. Existing CSRF, revision, save and approval handlers remain authoritative. */
(() => {
  'use strict';
  const form=document.getElementById('aoqReviewForm');
  if(form){
    const action=form.elements.namedItem('action');
    const reviewer=form.elements.namedItem('review_supplier_index');
    const winner=form.elements.namedItem('winner_index');
    const loaded=form.elements.namedItem('offer_supplier_index');
    // Keep source values compact and read-only until a correction is needed.
    for(const row of form.querySelectorAll?.('[data-quotation]')||[]){
      const fields=[...row.querySelectorAll('input:not([type="hidden"]),textarea')];
      for(const field of fields)field.readOnly=Boolean(field.value.trim());
      const correct=row.querySelector('[data-correct-quotation]');
      correct?.addEventListener('click',()=>{
        const editing=correct.getAttribute('aria-pressed')!=='true';
        correct.setAttribute('aria-pressed',String(editing));
        correct.textContent=editing?'Done':'Correct';
        for(const field of fields)field.readOnly=!editing&&Boolean(field.value.trim());
        if(editing)fields[0]?.focus();
      });
    }
    let changingSupplier=false;
    const submitReview=()=>{
      const originalValidation=form.noValidate;
      changingSupplier=true;form.noValidate=true;
      try {action.value='review_supplier';form.requestSubmit();}
      finally {form.noValidate=originalValidation;changingSupplier=false;}
    };
    winner.addEventListener('change',()=>{if(winner.value){reviewer.value=winner.value;submitReview();}});
    form.addEventListener('submit',event=>{
      const reviewing=changingSupplier;
      action.value=reviewing?'review_supplier':'save_selection';
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
