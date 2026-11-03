/* Execute the production polling script with a bounded synthetic DOM/network. */
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const path=require('node:path');
const listeners={};let scheduled=null,requests=0,revision='0',focused=false;
const form={dataset:{},getAttribute:()=> 'post'};
const unsaved={value:'KEEP UNSAVED',type:'text',closest:()=>form};
const count={textContent:'0'};
const table={children:[],closest:()=>null,querySelector:()=>null,contains:()=>focused,
  replaceChildren(...values){this.children=values;}};
const editable={closest:()=>form,querySelector:()=>unsaved};
const notice={dataset:{},hidden:true,setAttribute(){}};
const container={prepend(node){assert.equal(node,notice);}};
const freshTable={closest:()=>null,querySelector:()=>null,
  cloneNode(){return {childNodes:['new row '+revision],querySelectorAll:()=>[]};}};
const fresh={
  querySelector:()=>null,getElementById:()=>null,
  querySelectorAll(selector){
    if(selector==='#mainContent table')return [freshTable];
    if(selector.startsWith('[data-live-count]'))return [{textContent:revision}];
    return [];
  }
};
const document={body:{dataset:{syncUrl:'/api/sync/revisions',syncTopics:'["ppmp","account"]'}},hidden:false,activeElement:null,
  createElement(){return notice;},getElementById(){return null;},
  querySelector(selector){if(selector==='#mainContent')return container;if(selector==='form[data-sync-dirty="true"]')return form.dataset.syncDirty?form:null;return null;},
  querySelectorAll(selector){
    if(selector==='form[data-sync-dirty="true"]')return form.dataset.syncDirty?[form]:[];
    if(selector==='#mainContent table')return [table,editable];
    if(selector.startsWith('[data-live-count]'))return [count];
    return [];
  },
  addEventListener(name,callback){listeners[name]=callback;},dispatchEvent(){}
};
const sandbox={document,window:{location:{href:'https://example.invalid/ppmp',origin:'https://example.invalid'}},
  URL,Event,AbortController,console,
  DOMParser:class{parseFromString(){return fresh;}},
  setTimeout(callback){scheduled=callback;return 1;},clearTimeout(){scheduled=null;},
  async fetch(url,options){
    requests++;assert.equal(options.credentials,'same-origin');assert.equal(options.cache,'no-store');
    assert.equal(new URL(url).origin,'https://example.invalid');
    if(String(url).includes('/api/sync/'))return {ok:true,status:200,redirected:false,
      headers:{get(name){return name==='content-type'?'application/json':name==='ETag'?'"'+revision+'"':null;}},
      json:async()=>({versions:{ppmp:revision,account:'0'}})};
    return {ok:true,status:200,redirected:false,text:async()=>'<synthetic view>'};
  }
};
async function flush(){for(let i=0;i<4;i++)await new Promise(resolve=>setImmediate(resolve));}
(async()=>{
  vm.runInNewContext(fs.readFileSync(path.join(__dirname,'../static/js/live-updates.js'),'utf8'),sandbox);
  await flush();assert.equal(count.textContent,'0');assert.equal(table.children[0],'new row 0');
  listeners.input({target:unsaved});assert.equal(form.dataset.syncDirty,'true');
  revision='1';const next=scheduled;assert.equal(typeof next,'function');next();await flush();
  assert.equal(count.textContent,'1');assert.equal(table.children[0],'new row 1');
  assert.equal(unsaved.value,'KEEP UNSAVED');assert.match(notice.textContent,/unsaved edits were retained/i);
  focused=true;revision='2';scheduled();await flush();
  assert.equal(table.children[0],'new row 1');
  focused=false;scheduled();await flush();
  assert.equal(table.children[0],'new row 2');
  document.hidden=true;listeners.visibilitychange();const before=requests;
  assert.equal(scheduled,null);await flush();assert.equal(requests,before);
  document.hidden=false;listeners.visibilitychange();await flush();assert.ok(requests>before);
  assert.equal(unsaved.value,'KEEP UNSAVED');
  console.log('PASS polling updates records/counts, retains edits, retries focused tables, pauses hidden tabs and resumes safely');
})().catch(error=>{console.error(error);process.exitCode=1;});
