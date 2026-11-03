/* Actual selector script with DOM doubles. This does not prove browser layout. */
const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm'),path=require('node:path');
class Element {
  constructor(tag){this.tagName=tag;this.dataset={};this.children=[];this.listeners={};this.className='';this.value='';this.attributes={};}
  setAttribute(key,value){this.attributes[key]=value;}
  append(...children){children.forEach(child=>{child.parent=this;this.children.push(child);});}
  before(child){const at=this.parent.children.indexOf(this);child.parent=this.parent;this.parent.children.splice(at,0,child);}
  remove(){this.parent.children.splice(this.parent.children.indexOf(this),1);}
  addEventListener(type,handler){(this.listeners[type] ||= []).push(handler);}
  dispatchEvent(event){for(const handler of this.listeners[event.type] || [])handler(event);}
  focus(){this.focused=true;}
  closest(name){return name==='label' && this.parent.tagName==='label' ? this.parent : null;}
}
const root=new Element('form'),label=new Element('label'),select=new Element('select');
select.dataset.recordSelector='approved PR';select.options=[
  {value:'',textContent:'Select approved PR'},
  {value:'17',textContent:'PR-2027-0017 · Cable · Test Office',dataset:{search:'High-Speed HDMI Cable supplier Northstar'}},
  {value:'23',textContent:'PR-2027-0023 · Paper · Other Office',dataset:{search:'A4 paper supplier Orion'}}
];
Object.defineProperty(select,'value',{get(){return this.selectedValue || '';},set(value){this.selectedValue=value;this.options.forEach(option=>option.selected=option.value===value);}});
select.value='17';root.append(label);label.append(select);
const document={querySelectorAll:selector=>{assert.equal(selector,'select[data-record-selector]');return [select];},createElement:tag=>new Element(tag)};
vm.runInNewContext(fs.readFileSync(path.join(__dirname,'../static/js/record-selectors.js'),'utf8'),{document,Event:class {constructor(type){this.type=type;}}});
const wrapper=root.children[0],search=wrapper.children[1],status=wrapper.children[3];
assert.equal(label.htmlFor,select.id);assert(!label.children.includes(search));
assert.equal(select.value,'17');
function find(value){search.value=value;search.dispatchEvent({type:'input'});}
function key(value){let prevented=false;search.dispatchEvent({type:'keydown',key:value,preventDefault(){prevented=true;}});assert(prevented);}
find('northstar');assert(status.textContent.startsWith('1 matching'));assert(select.options[2].hidden);
find('A4 PAPER');key('Enter');assert.equal(select.value,'23');assert(select.focused);
find('PR-2027-0017');key('Enter');assert.equal(select.value,'17');
find('No such record');key('Enter');assert.equal(select.value,'17');assert(status.textContent.startsWith('0 matching'));
key('Escape');assert.equal(search.value,'');assert.equal(select.value,'17');assert(select.options.slice(1).every(option=>!option.hidden));
find('Test Office');assert(status.textContent.startsWith('1 matching'));key('ArrowDown');assert(select.focused);
assert.deepEqual(select.options.map(option=>option.value),['','17','23']);
console.log('PASS: native IDs, document/title/office/supplier search, labels, Enter, Escape, no-match retention and keyboard focus. DOM doubles only.');
