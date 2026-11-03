/* Deterministic DOM doubles exercising the actual shared JS. No browser claim. */
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
class Element {
  constructor(id='') {this.id=id;this.dataset={};this.attributes={};this.listeners={};this.textContent='';this.open=false;this.isConnected=true;this.classList={add(){}};}
  addEventListener(type,handler){(this.listeners[type] ||= []).push(handler);}
  emit(type,event={}){for(const handler of this.listeners[type]||[])handler(event);}
  setAttribute(name,value){this.attributes[name]=value;}
  removeAttribute(name){delete this.attributes[name];}
  showModal(){this.open=true;}
  close(){this.open=false;this.emit('close');}
  focus(){document.activeElement=this;}
  replaceChildren(){this.children=[];}
  append(...values){(this.children ||= []).push(...values);}
}
const ids=Object.fromEntries(['actionConfirmDialog','actionConfirmTitle','actionConfirmMessage','actionConfirmAccept','actionConfirmCancel','actionFeedback','actionFinalApproval','actionFinalCheckbox','actionApprovalDetails'].map(id=>[id,new Element(id)]));
const document=new Element('document');document.activeElement=new Element('launcher');document.getElementById=id=>ids[id];document.querySelectorAll=()=>[];
document.createElement=tag=>new Element(tag);
const window=new Element('window');
class FileDouble {constructor(name=''){this.name=name;this.size=0;this.type='application/octet-stream';this.lastModified=Date.now()+Math.random();}}
class FormDataDouble {
  constructor(form){this.values=Object.entries(form.fields).map(([key,value])=>[key,value==='EMPTY_FILE'?new FileDouble():value]);}
  append(name,value){this.values.push([name,value]);}
  [Symbol.iterator](){return this.values[Symbol.iterator]();}
}
vm.runInNewContext(fs.readFileSync(path.join(__dirname,'../static/js/confirm-actions.js'),'utf8'),{
  document,window,FormData:FormDataDouble,File:FileDouble,setTimeout:()=>1,clearTimeout:()=>{},location:{assign:()=>{throw Error('Unexpected navigation');}},CustomEvent:class {}
});
function form(category='ppmp'){
  const f=new Element('form');f.method='post';f.action='/synthetic';f.dataset.confirmCategory=category;f.fields={csrf_token:'SYNTHETIC',review_confirm:'yes'};f.posts=[];
  const b=new Element('submit');b.name='action';b.value='save_ocr_rows';b.disabled=false;b.formAction=f.action;b.formMethod='post';
  f.requestSubmit=button=>{const event={target:f,submitter:button,defaultPrevented:false,preventDefault(){this.defaultPrevented=true;}};document.emit('submit',event);if(!event.defaultPrevented)f.posts.push([...new FormDataDouble(f),[button.name,button.value]]);};
  return [f,b];
}
const tick=()=>new Promise(resolve=>setImmediate(resolve));
(async()=>{
  for(const [category,title] of Object.entries({logout:'Confirm Sign Out',ppmp:'Approve PPMP?',app:'Approve APP?',pr:'Approve Purchase Request?',po:'Finalize Purchase Order?',iar:'Confirm Receiving?',ris:'Confirm Stock Release?',delete:'Delete Record?'})){
    const [f,b]=form(category);b.focus();f.requestSubmit(b);
    assert(ids.actionConfirmDialog.open);assert.equal(ids.actionConfirmTitle.textContent,title);assert.equal(document.activeElement.id,'actionConfirmCancel');
    ids.actionConfirmCancel.emit('click');await tick();assert.equal(f.posts.length,0);assert.equal(document.activeElement,b);
    f.requestSubmit(b);ids.actionConfirmDialog.emit('cancel',{preventDefault(){}});await tick();assert.equal(f.posts.length,0);
    f.requestSubmit(b);ids.actionConfirmAccept.emit('click');ids.actionConfirmAccept.emit('click');await tick();
    assert.equal(f.posts.length,1);assert(f.posts[0].some(([key,value])=>key==='action'&&value==='save_ocr_rows'));
    f.requestSubmit(b);await tick();assert.equal(f.posts.length,1);assert(!b.disabled);assert.equal(f.attributes['aria-busy'],'true');
  }
  const [changed,button]=form();changed.requestSubmit(button);changed.fields.Quantity='3';ids.actionConfirmAccept.emit('click');await tick();
  assert.equal(changed.posts.length,0);assert(ids.actionFeedback.textContent.includes('form changed'));
  const [emptyUpload,create]=form();emptyUpload.fields.ocr_file='EMPTY_FILE';emptyUpload.requestSubmit(create);ids.actionConfirmAccept.emit('click');await tick();
  assert.equal(emptyUpload.posts.length,1,'Unselected upload timestamps must not invalidate confirmation');
  const [replacedUpload,upload]=form();replacedUpload.fields.ocr_file=new FileDouble('source.xlsx');replacedUpload.requestSubmit(upload);
  replacedUpload.fields.ocr_file=new FileDouble('other.xlsx');ids.actionConfirmAccept.emit('click');await tick();assert.equal(replacedUpload.posts.length,0);
  const [duplicate,b]=form();duplicate.requestSubmit(b);duplicate.requestSubmit(b);ids.actionConfirmAccept.emit('click');await tick();assert.equal(duplicate.posts.length,1);
  const pending=window.psmsConfirm({category:'discard'});assert.equal(ids.actionConfirmCancel.textContent,'Keep Editing');ids.actionConfirmCancel.emit('click');assert.equal(await pending,false);
  for (const category of ['aoq','po']) {
    let result;
    const first=window.psmsConfirm({category,finalApproval:true,details:[[category.toUpperCase(),'SYNTHETIC-10'],['Amount','PHP 830.00']]}).then(value=>{result=value;});
    assert.equal(ids.actionFinalCheckbox.checked,false);
    assert.equal(ids.actionConfirmAccept.disabled,true);
    assert.equal(ids.actionConfirmAccept.textContent,'Confirm Approval');
    assert.equal(ids.actionApprovalDetails.children[1].textContent,'SYNTHETIC-10');
    ids.actionConfirmAccept.emit('click');await tick();assert.equal(result,undefined);
    ids.actionConfirmCancel.emit('click');await first;assert.equal(result,false);
    const second=window.psmsConfirm({category,finalApproval:true});
    assert.equal(ids.actionFinalCheckbox.checked,false);
    ids.actionFinalCheckbox.checked=true;ids.actionFinalCheckbox.emit('change');
    assert.equal(ids.actionConfirmAccept.disabled,false);
    ids.actionConfirmAccept.emit('click');assert.equal(await second,true);
  }
  console.log('PASS: shared confirmation scenarios, including empty-file timestamps and changed uploads. DOM doubles, no real browser.');
})().catch(error=>{console.error(error);process.exitCode=1;});
