(() => {
  'use strict';
  const keys = ['general','type','quantity','mode','preproc','start','end','delivery','source','budget','docs','remarks'];
  const stored = ['General Description and Objective of the Project to be Procured','Type of the Project to be Procured','Quantity and Size of the Project to be Procured','Recommended Mode of Procurement','Pre-Procurement Conference Applicable (Yes/No)','Start of Procurement Activity','End of Procurement Activity','Expected Delivery/Implementation Period','Source of Funds','Estimated Budget Allocation (Php)','Attached Supporting Documents','Remarks'];
  const dialog = document.getElementById('ppmpEditor'); if (!dialog) return;
  const saved = dialog.dataset.mode === 'saved', prefix = saved ? 's_' : 'f_';
  if (saved) {
    keys.push('item_name','ordered_quantity','unit','unit_cost');
    stored.push('Item Name','Quantity','Unit','Estimated Unit Cost');
  }
  const form = dialog.querySelector('form'), select = document.getElementById('editorItemSelect');
  let items = [], index = 0, original = '', launcher;
  const value = v => v === null || v === undefined ? '' : String(v);
  const cleanMoney = v => value(v).replace(/[₱,]/g,'').trim();
  const fields = () => Object.fromEntries(keys.map(k => [k, document.getElementById(prefix+k).value]));
  function saveCurrent() { if (items[index]) Object.assign(items[index], fields()); }
  function isDirty() { if (!items.length) return false; if (saved) return JSON.stringify(fields()) !== original; saveCurrent(); return JSON.stringify(items) !== original; }
  function load() {
    keys.forEach(k => { document.getElementById(prefix+k).value = value(items[index][k]); });
    select.value = String(index);
    select.hidden = items.length < 2;
    document.getElementById('editorPosition').textContent = `${index+1} of ${items.length}`;
    document.getElementById('editorCount').textContent = saved ? 'Save changes to this item' : `${items.length} selected item${items.length===1?'':'s'}`;
    document.getElementById('editorPrev').disabled = index===0;
    document.getElementById('editorNext').disabled = index===items.length-1;
    if (!saved) {
      const recorded = items[index].sourceFields || {};
      [['Item Name','Name'],['Quantity','Quantity'],['Unit','Unit'],['Estimated Unit Cost','Cost']].forEach(([label, suffix]) => {
        const control = document.getElementById('sourceItem'+suffix);
        if (control) control.value = value(recorded[label]);
      });
      const status = document.getElementById('sourceReviewStatus');
      if (status) status.textContent = 'Review not recorded. Saving source changes does not confirm item verification or procurement approval.';
    }
    if (saved) { document.getElementById('saved_item_index').value=index; original=JSON.stringify(fields()); }
    window.resizeEditorFields?.(dialog);
  }
  async function navigate(next) {
    if (next===index || next<0 || next>=items.length) return;
    if (saved && isDirty() && !await window.psmsConfirm({category:'discard'})) { select.value=String(index); return; }
    if (!saved) saveCurrent();
    index=next; load();
  }
  function open() {
    if (!items.length) { window.psmsNotify('Select at least one PPMP item to edit.'); return; }
    launcher=document.activeElement; index=0; select.replaceChildren();
    items.forEach((it,i) => { const option=document.createElement('option'); option.value=i; option.textContent=`${i+1}. ${it.general || it.quantity || 'PPMP item'}`; select.append(option); });
    if (!saved) original=JSON.stringify(items);
    window.closeNavigation?.(); dialog.showModal(); load();
    requestAnimationFrame(() => window.resizeEditorFields?.(dialog));
  }
  async function close() { if (isDirty() && !await window.psmsConfirm({category:'discard'})) return; dialog.close(); }
  dialog.addEventListener('cancel', e => { e.preventDefault(); close(); });
  dialog.addEventListener('click', e => { const r=dialog.getBoundingClientRect(); if (e.target===dialog && (e.clientX<r.left||e.clientX>r.right||e.clientY<r.top||e.clientY>r.bottom)) close(); });
  dialog.addEventListener('close', () => launcher?.focus());
  dialog.querySelectorAll('[data-editor-close]').forEach(b => b.addEventListener('click',close));
  select.addEventListener('change',() => navigate(Number(select.value)));
  document.getElementById('editorPrev').addEventListener('click',() => navigate(index-1));
  document.getElementById('editorNext').addEventListener('click',() => navigate(index+1));
  if (saved) {
    window.openSavedPPMP = (id, rows) => {
      items=(rows||[]).map(row => Object.fromEntries(keys.map((k,i) => [k,k==='budget'?cleanMoney(row[stored[i]]):value(row[stored[i]])])));
      form.action=`/ppmp/${id}/items`; open();
    };
  } else {
    const table=document.getElementById('ppmpTable');
    const sourceFields = row => Object.fromEntries(Array.from(row.querySelectorAll('[data-source-item-field]'), control => [control.dataset.sourceItemField, control.value]));
    table.querySelectorAll('[data-review-row]').forEach(button => button.addEventListener('click', () => {
      const row=button.closest('tr'), it={row:button.dataset.reviewRow};
      it.sourceSignature=row.querySelector(`[name="source_signature_${it.row}"]`)?.value || '';
      it.sourceFields=sourceFields(row);
      keys.forEach(k => { it[k]=row.querySelector(`[name="${k}_${it.row}"]`)?.value || ''; });
      it.budget=cleanMoney(it.budget); items=[it]; open();
    }));
    document.getElementById('edit_selected_btn').addEventListener('click',() => {
      items=Array.from(table.querySelectorAll('input[name=rows][type=checkbox]:checked')).map(cb => {
        const row=cb.closest('tr'), it={row:cb.value};
        it.sourceSignature=row.querySelector(`[name="source_signature_${it.row}"]`)?.value || '';
        it.sourceFields=sourceFields(row);
        keys.forEach(k => { it[k]=row.querySelector(`[name="${k}_${cb.value}"]`)?.value || ''; }); it.budget=cleanMoney(it.budget); return it;
      }); open();
    });
    window.filterRows = search => {
      const needle=search.toLocaleLowerCase();
      table.querySelectorAll('tr').forEach(row => {
        if (!row.querySelector('td')) return;
        const text=row.textContent+' '+Array.from(row.querySelectorAll('input,textarea')).map(el => el.value).join(' ');
        row.hidden=!text.toLocaleLowerCase().includes(needle);
      });
    };
  }
  form.addEventListener('submit',e => {
    const amount=cleanMoney(document.getElementById(prefix+'budget').value);
    if (amount && (!Number.isFinite(Number(amount)) || Number(amount)<0)) {
      e.preventDefault(); document.getElementById(prefix+'budget').focus(); window.psmsNotify('Enter a valid non-negative budget amount.'); return;
    }
    document.getElementById(prefix+'budget').value=amount;
    if (!saved) {
      saveCurrent(); const hidden=document.getElementById('hiddenEditFields'); hidden.replaceChildren();
      const add=(name,v) => { const input=document.createElement('input'); input.type='hidden'; input.name=name; input.value=v; hidden.append(input); };
      items.forEach(it => { add('rows',it.row); add(`source_signature_${it.row}`,it.sourceSignature); keys.forEach(k => add(`${k}_${it.row}`,it[k])); });
    }
  });
})();

/* Official draft creation keeps validation and failures on the current page. */
(() => {
  'use strict';
  const form=document.getElementById('ppmpSourceForm');
  const button=document.querySelector('[data-ppmp-create]');
  const feedback=document.getElementById('ppmpCreationFeedback');
  if(!form||!button||!feedback)return;
  const confirmation=form.querySelector('[name="confirm_reviewed"]');
  const selectors='input[name="rows"],input[name="ocr_rows"]';
  let busy=false;
  function clear(){
    feedback.hidden=true;
    form.querySelectorAll('[data-creation-invalid]').forEach(control=>{
      control.removeAttribute('aria-invalid');control.removeAttribute('data-creation-invalid');
    });
  }
  function show(message,field,item){
    feedback.textContent=message;feedback.hidden=false;
    const controls=field==='confirmation'?[confirmation]:field==='selection'?[...form.querySelectorAll(selectors)]:
      field==='item'?[...form.querySelectorAll(selectors)].filter(control=>control.value===item):[];
    controls.forEach(control=>{control.setAttribute('aria-invalid','true');control.setAttribute('data-creation-invalid','true');});
    (controls[0]||feedback).focus();
  }
  form.addEventListener('change',event=>{
    if(event.target===confirmation||event.target.matches(selectors))clear();
  });
  form.addEventListener('submit',async event=>{
    if(event.submitter!==button)return;
    event.preventDefault();
    if(busy)return;
    clear();
    if(![...form.querySelectorAll(selectors)].some(control=>control.checked)){
      show('Select at least one reviewed item before creating a PPMP.','selection');return;
    }
    if(!confirmation.checked){
      show('Please confirm the selected reviewed values before continuing.','confirmation');return;
    }
    busy=true;
    try{
      if(typeof window.psmsConfirm==='function'&&!await window.psmsConfirm({message:'Create official PPMP drafts from the selected reviewed values? Procurement approval remains separate.'}))return;
      const body=new FormData(form);body.set('action','create_ppmp');body.delete('ocr_file');
      button.disabled=true;form.setAttribute('aria-busy','true');
      // Buttons named "action" shadow form.action with a RadioNodeList.
      const response=await fetch(form.getAttribute('action')||window.location.href,{method:'POST',body,
        credentials:'same-origin',headers:{Accept:'application/json'}});
      if(response.redirected&&new URL(response.url).pathname==='/login'){
        window.location.assign(response.url);return;
      }
      const data=response.headers.get('content-type')?.includes('application/json')?await response.json():{};
      if(response.ok&&data.saved===true&&data.redirect){
        const target=new URL(data.redirect,window.location.origin);
        if(target.origin!==window.location.origin)throw new Error('INVALID_LOCAL_REDIRECT');
        window.location.assign(target.href);return;
      }
      const reference=/^[0-9a-f]{16}$/.test(data.reference||'')?' Reference: '+data.reference:'';
      show((data.item?data.item+': ':'')+(data.message||'Creation could not be confirmed. Your selections remain here. Reopen the latest review before retrying.')+reference,data.field,data.item);
    }catch(error){
      show('Creation could not be confirmed. Your selections remain here. Check the saved PPMP records before retrying.');
    }finally{
      busy=false;button.disabled=false;form.removeAttribute('aria-busy');
    }
  });
})();
