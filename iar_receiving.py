"""Pure IAR inspection evidence. No database, models, network or approval."""
import hashlib,json
from procurement_item_contract import decimal_number,number_text

QUANTITY=('qty','Quantity','quantity','QTY','Awarded Quantity','Requested Quantity')
COST=('unit_cost','Unit Cost','Estimated Unit Cost','unit_price','Unit Price')
REVIEW_KEYS={'_Source Item ID','_IAR Item ID','_Receiving Contract Version','ordered_qty',
    'received_qty','delivered_qty','rejected_qty','damaged_qty','missing_qty',
    'inspection_findings','inspection_outcome','_Receiving Quantity Difference',
    '_Delivery Quantity Difference','_Inventory Category','_Inspected By','_Inspected At','_Receiving Source Hash'}

def source_hash(items,po_items):
    return hashlib.sha256(json.dumps({'IAR':items,'PO':po_items},sort_keys=True,separators=(',',':'),ensure_ascii=False,default=str).encode()).hexdigest()

def number_from(item,keys,label):
    values=[decimal_number(item[k]) for k in keys if item.get(k) not in (None,'')]
    if not values or any(v is None for v in values) or len(set(values))!=1:
        raise ValueError('Missing, invalid or conflicting '+label+'.')
    return values[0]

def prepare(items,po_items):
    """Bind pending receipt lines to the current approved PO without saving."""
    if (not isinstance(items,list) or not items or not isinstance(po_items,list)
            or len(items)!=len(po_items) or any(not isinstance(i,dict) for i in items+po_items)):
        raise ValueError('IAR item coverage differs from the approved PO. Reopen the source records.')
    ids=[p.get('_PO Item ID') for p in po_items]
    identified=any(ids)
    if identified and (not all(isinstance(i,str) and i for i in ids) or len(set(ids))!=len(ids)):
        raise ValueError('Approved PO item references are incomplete or duplicated.')
    mapping={p['_PO Item ID']:p for p in po_items} if identified else {}
    seen=set();result=[]
    for index,source in enumerate(items):
        ref=source.get('_PO Item ID') or source.get('_Source Item ID')
        po=mapping.get(ref) if identified else po_items[index]
        if po is None or identified and ref in seen:raise ValueError('IAR item correspondence differs from the approved PO.')
        seen.add(ref)
        if any(source.get(k)!=v for k,v in po.items() if k not in REVIEW_KEYS):
            raise ValueError('The approved PO or IAR source values changed. Inspection cannot replace ordered items.')
        ordered=number_from(po,QUANTITY,'ordered quantity')
        if ordered<=0:raise ValueError('Ordered quantities must be positive.')
        item=dict(source);item['ordered_qty']=number_text(ordered)
        item['_Receiving Contract Version']=2
        for key in ('delivered_qty','received_qty','rejected_qty','damaged_qty','inspection_findings'):
            item.setdefault(key,'')
        item['_Receiving Source Hash']=source_hash(items,po_items)
        result.append(item)
    return result

def confirm(items,form,po_items):
    if form.get('receiving_version')!='2':raise ValueError('Reopen IAR review and record the actual inspection before approval.')
    if hasattr(form,'getlist') and any(len(form.getlist(k))!=1 for k in form):raise ValueError('Duplicate receiving fields are not supported.')
    if form.get('receiving_source_hash')!=source_hash(items,po_items):raise ValueError('The IAR or PO changed after this form opened. Reopen review before approving.')
    prepared=prepare(items,po_items);confirmed=[]
    for index,item in enumerate(prepared):
        ordered=decimal_number(item['ordered_qty']);quantities={}
        for key,label in [('delivered_qty','delivered'),('received_qty','accepted'),('rejected_qty','rejected'),('damaged_qty','damaged')]:
            raw=form.get(key+'_'+str(index))
            value=decimal_number(raw) if isinstance(raw,str) and len(raw)<=64 else None
            if value is None or value<0:raise ValueError(f'IAR item {index+1}: enter a valid non-negative {label} quantity, including zero.')
            quantities[key]=value
        delivered=quantities['delivered_qty'];accepted=quantities['received_qty']
        rejected=quantities['rejected_qty'];damaged=quantities['damaged_qty']
        if accepted>ordered or accepted+rejected!=delivered or damaged>rejected:
            raise ValueError(f'IAR item {index+1}: accepted plus rejected must equal delivered; damaged is part of rejected; accepted cannot exceed ordered.')
        findings=form.get('inspection_findings_'+str(index),'')
        if not isinstance(findings,str) or not findings.strip() or len(findings)>1000:
            raise ValueError(f'IAR item {index+1}: record the actual inspection findings (maximum 1000 characters).')
        category=form.get('inventory_category_'+str(index),'')
        if accepted>0:
            if any(str(item.get(k,'')).strip().upper()=='LIB' for k in ('category','Category','Source Category','_Procurement Category')):
                raise ValueError('LIB/non-stock expense receiving requires an approved non-inventory policy; it cannot be posted as stock.')
            if category not in ('Equipment','Consumable'):
                raise ValueError(f'IAR item {index+1}: review Equipment or Consumable for the accepted stock.')
            item['_Inventory Category']=category
        elif category and category not in ('Equipment','Consumable'):
            raise ValueError(f'IAR item {index+1}: invalid inventory classification.')
        for key,value in quantities.items():item[key]=number_text(value)
        item['missing_qty']=number_text(max(ordered-delivered,0))
        item['_Receiving Quantity Difference']=number_text(accepted-ordered)
        item['_Delivery Quantity Difference']=number_text(delivered-ordered)
        item['inspection_findings']=findings.strip()
        item['inspection_outcome']=('Not delivered' if delivered==0 else 'Rejected' if accepted==0
            else 'Accepted' if accepted==ordered and rejected==0 else 'Partially accepted')
        confirmed.append(item)
    return confirmed

def inventory_receipts(items):
    if not isinstance(items,list) or not items:raise ValueError('IAR items are missing or invalid.')
    receipts=[]
    for index,item in enumerate(items,1):
        if not isinstance(item,dict) or item.get('_Receiving Contract Version')!=2:
            raise ValueError('Actual inspection evidence is required before inventory posting.')
        values={k:decimal_number(item.get(k)) for k in ('ordered_qty','delivered_qty','received_qty','rejected_qty','damaged_qty')}
        if any(v is None or v<0 for v in values.values()) or values['ordered_qty']<=0:
            raise ValueError('Inspection quantities are incomplete or invalid.')
        if (values['received_qty']>values['ordered_qty'] or values['received_qty']+values['rejected_qty']!=values['delivered_qty']
                or values['damaged_qty']>values['rejected_qty'] or not str(item.get('inspection_findings') or '').strip()):
            raise ValueError('Inspection evidence is unresolved; no inventory can be posted.')
        qty=values['received_qty']
        if qty==0:continue
        name=item.get('item') or item.get('Item Name');unit=item.get('unit') or item.get('Unit')
        if not isinstance(name,str) or not name.strip() or not isinstance(unit,str) or not unit.strip():
            raise ValueError(f'IAR item {index}: accepted stock requires its original item name and unit.')
        category=item.get('_Inventory Category')
        if category not in ('Equipment','Consumable'):raise ValueError('Accepted stock classification is required.')
        cost=number_from(item,COST,'unit cost')
        if cost<0:raise ValueError('Unit cost cannot be negative.')
        description=item.get('description') or item.get('Description / Specification') or item.get('Description') or ''
        receipts.append((name,description,category,qty,unit,cost,item.get('_Dataset Record ID') or item.get('_dataset_record_id')))
    return receipts
