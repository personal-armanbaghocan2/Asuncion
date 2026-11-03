/* Authenticated revision polling. Never stores form values or reloads a page. */
(() => {
  'use strict';
  const root=document.body;
  if(!root.dataset.syncUrl)return;
  const topics=JSON.parse(root.dataset.syncTopics||'[]');
  if(!topics.length)return;
  let current=null,etag='',delay=15000,timer=null,running=false,controller=null;
  const notice=document.createElement('div');
  notice.className='notice live-update-notice';notice.dataset.category='info';notice.hidden=true;notice.setAttribute('role','status');
  document.querySelector('#mainContent')?.prepend(notice);
  function message(text,error=false){notice.textContent=text;notice.dataset.category=error?'error':'info';notice.hidden=false;}
  function dirty(){return !!document.querySelector('form[data-sync-dirty="true"]');}
  function mark(event){
    if(event.target.type==='search')return;
    const form=event.target.closest?.('form');
    if(form&&(form.getAttribute('method')||'get').toLowerCase()==='post')form.dataset.syncDirty='true';
  }
  document.addEventListener('input',mark);document.addEventListener('change',mark);
  document.addEventListener('psms:form-saved',event=>{if(event.detail?.form)delete event.detail.form.dataset.syncDirty;current=null;etag='';});
  function readonlyTables(doc){
    return [...doc.querySelectorAll('#mainContent table')].filter(table=>!table.closest('form,dialog')&&!table.querySelector('input,textarea,select,form,[contenteditable]'));
  }
  async function updateView(versions,initial=false){
    const response=await fetch(window.location.href,{credentials:'same-origin',cache:'no-store',headers:{'X-PSMS-Fragment':'1'},signal:controller.signal});
    if(!response.ok||response.redirected)throw new Error('VIEW_UNAVAILABLE');
    const fresh=new DOMParser().parseFromString(await response.text(),'text/html');
    let applied=true;
    // Explicit read-only workspaces and timelines can change shape. Editors,
    // search/filter forms and focused content remain in the current document.
    document.querySelectorAll('[data-live-region]').forEach(region=>{
      const key=region.getAttribute('data-live-region');
      const replacement=[...fresh.querySelectorAll('[data-live-region]')].find(node=>node.getAttribute('data-live-region')===key);
      if(!replacement||region.querySelector('form,input,textarea,select,[contenteditable]'))return;
      if(region.contains(document.activeElement)){applied=false;return;}
      const copy=replacement.cloneNode(true);copy.querySelectorAll('script,iframe,object').forEach(node=>node.remove());
      region.replaceChildren(...copy.childNodes);
    });
    document.querySelectorAll('[data-live-status]').forEach(node=>{
      const key=node.getAttribute('data-live-status');
      const replacement=[...fresh.querySelectorAll('[data-live-status]')].find(value=>value.getAttribute('data-live-status')===key);
      if(replacement&&!node.closest('form'))node.textContent=replacement.textContent;
    });
    const editors=document.querySelector('[data-live-editors]');
    if(editors){
      // New drafts need working review anchors. Existing editors keep their
      // values and revisions; a stale save is rejected by the server.
      fresh.querySelectorAll('[data-live-editors] > details[id]').forEach(editor=>{
        if(document.getElementById(editor.id))return;
        const copy=editor.cloneNode(true);
        copy.querySelectorAll('script,iframe,object').forEach(node=>node.remove());
        editors.append(copy);
      });
    }
    document.querySelectorAll('[data-live-pagination]').forEach((node,index)=>{
      const replacement=fresh.querySelectorAll('[data-live-pagination]')[index];
      if(!replacement)return;
      if(node.contains(document.activeElement)){applied=false;return;}
      node.replaceChildren(...[...replacement.childNodes].map(child=>child.cloneNode(true)));
    });
    // Editable tables and forms are retained, even when no field is dirty.
    const oldTables=readonlyTables(document),newTables=readonlyTables(fresh);
    if(oldTables.length===newTables.length){
      oldTables.forEach((table,index)=>{
        if(table.contains(document.activeElement)){applied=false;return;}
        const replacement=newTables[index].cloneNode(true);
        replacement.querySelectorAll('script,iframe,object').forEach(node=>node.remove());
        table.replaceChildren(...replacement.childNodes);
      });
    }
    const pagination=document.querySelector('[aria-label="Record pages"]');
    const newPagination=fresh.querySelector('[aria-label="Record pages"]');
    if(pagination&&newPagination&&!pagination.contains(document.activeElement))pagination.replaceChildren(...newPagination.childNodes);
    const countSelector='[data-live-count],.kpi .num,.dash-kpi-copy strong,.dataset-kpi b';
    const counts=[...document.querySelectorAll(countSelector)],freshCounts=[...fresh.querySelectorAll(countSelector)];
    if(counts.length===freshCounts.length)counts.forEach((node,index)=>{node.textContent=freshCounts[index].textContent;});
    if(!dirty()){
      const oldSelects=[...document.querySelectorAll('select[data-record-selector]')];
      const newSelects=[...fresh.querySelectorAll('select[data-record-selector]')];
      if(oldSelects.length===newSelects.length)oldSelects.forEach((select,index)=>{
        if(select===document.activeElement)return;
        const value=select.value;
        select.replaceChildren(...[...newSelects[index].children].map(node=>node.cloneNode(true)));
        select.value=value;
        select.dispatchEvent(new Event('psms:options-updated'));
        if(value&&select.value!==value)message('A selected source changed. Choose an eligible source again.');
      });
    }
    const data=document.getElementById('dashboard-data'),newData=fresh.getElementById('dashboard-data');
    if(data&&newData){data.textContent=newData.textContent;document.dispatchEvent(new Event('psms:dashboard-data'));}
    if(versions.account!==current?.account){
      document.querySelectorAll('.profile-avatar,.profile-photo').forEach(image=>{
        const url=new URL(image.src,window.location.origin);url.searchParams.set('revision',versions.account||'0');image.src=url.href;
      });
      const names=[...document.querySelectorAll('.user-copy b,.account-dropdown-head b')];
      const freshNames=[...fresh.querySelectorAll('.user-copy b,.account-dropdown-head b')];
      if(names.length===freshNames.length)names.forEach((node,index)=>{node.textContent=freshNames[index].textContent;});
    }
    if(!initial||dirty())message(dirty()?'Shared records changed. Your unsaved edits were retained. Save or cancel them before reloading editable item lists.':'Records and workflow status updated from the shared database.');
    return applied;
  }
  function schedule(){clearTimeout(timer);if(!document.hidden)timer=setTimeout(poll,delay+Math.floor(Math.random()*2000));}
  async function poll(){
    if(document.hidden||running)return;
    running=true;controller=new AbortController();
    try{
      const url=new URL(root.dataset.syncUrl,window.location.origin);url.searchParams.set('topics',topics.join(','));
      const headers={'Accept':'application/json'};if(etag)headers['If-None-Match']=etag;
      const response=await fetch(url,{credentials:'same-origin',cache:'no-store',headers,signal:controller.signal});
      if(response.redirected||response.status===401||response.status===403){message('Your session changed. Sign in again before continuing.',true);delay=120000;return;}
      const csrf=response.headers.get('X-CSRFToken');
      if(csrf&&csrf.length<512){
        document.querySelectorAll('input[name="csrf_token"]').forEach(input=>{input.value=csrf;input.defaultValue=csrf;});
        document.querySelector('meta[name="csrf-token"]')?.setAttribute('content',csrf);
      }
      if(response.status===304){delay=15000;return;}
      if(!response.ok||!response.headers.get('content-type')?.includes('application/json'))throw new Error('SYNC_UNAVAILABLE');
      const result=await response.json();
      let applied=true;
      if(!current||JSON.stringify(current)!==JSON.stringify(result.versions))applied=await updateView(result.versions,!current);
      // Retry a focused record table after focus moves. Advancing its revision
      // here would leave that table stale until another database write.
      if(applied){current=result.versions;etag=response.headers.get('ETag')||'';}
      delay=15000;
    }catch(error){
      if(error.name!=='AbortError'){delay=Math.min(120000,delay*2);message('Automatic updates are temporarily unavailable. Your edits remain on this page.');}
    }finally{running=false;controller=null;schedule();}
  }
  document.addEventListener('visibilitychange',()=>{
    clearTimeout(timer);if(document.hidden)controller?.abort();else poll();
  });
  poll();
})();
