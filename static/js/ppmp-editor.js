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
    document.getElementById('editorPosition').textContent = `${index+1} of ${items.length}`;
    document.getElementById('editorCount').textContent = saved ? 'Save changes to this item' : `${items.length} selected item${items.length===1?'':'s'}`;
    document.getElementById('editorPrev').disabled = index===0;
    document.getElementById('editorNext').disabled = index===items.length-1;
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
    table.querySelectorAll('[data-review-row]').forEach(button => button.addEventListener('click', () => {
      const row=button.closest('tr'), it={row:button.dataset.reviewRow};
      keys.forEach(k => { it[k]=row.querySelector(`[name="${k}_${it.row}"]`)?.value || ''; });
      it.budget=cleanMoney(it.budget); items=[it]; open();
    }));
    document.getElementById('edit_selected_btn').addEventListener('click',() => {
      items=Array.from(table.querySelectorAll('input[name=rows][type=checkbox]:checked')).map(cb => {
        const row=cb.closest('tr'), it={row:cb.value};
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
      items.forEach(it => { add('rows',it.row); keys.forEach(k => add(`${k}_${it.row}`,it[k])); });
    }
  });
})();
