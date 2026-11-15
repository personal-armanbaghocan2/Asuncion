"""AOQ presentation only. Source evidence and authorization remain in existing services."""
from collections import OrderedDict
from copy import deepcopy
import re


def validate_document(document):
    title=str(document.get('source_type') or document.get('document_type') or '')
    text=str(document.get('raw_text') or '')
    planning=title.strip().upper()=='PPMP' or re.search(r'(?im)^\s*project\s+procurement\s+management\s+plan\b',text)
    quotation=re.search(r'(?im)^\s*(?:abstract\s+of\s+quotations|quotation\s+evaluation|bidding\s+results?)\b',text)
    if planning and not quotation:
        raise ValueError('This is a PPMP planning document, not supplier quotations. Upload it through PPMP; choose an AOQ quotation file here.')


def quotations(preview, official=None):
    if not official:
        try:validate_document(preview)
        except ValueError as error:
            return dict(suppliers=[],indices=[],rows=[],supplier_count=0,item_count=0,quotation_count=0,selected='',document_error=str(error))
    suppliers=deepcopy([s for s in preview.get('suppliers',[]) if isinstance(s,dict)])
    for index,correction in enumerate((preview.get('supplier_form') or []) if not official else []):
        if index<len(suppliers) and isinstance(correction,dict):
            suppliers[index].update({key:correction[key] for key in ('supplier','amount','remarks') if key in correction})
    def identity(item):
        category=str(item.get('source_category') or item.get('category') or '')
        number=str(item.get('source_item_no') or '').strip()
        return (category,'number',number) if number else (category,'description',str(item.get('item') or '').strip().casefold())
    source_names={}
    for supplier in suppliers:
        for item in supplier.get('items') or []:
            if isinstance(item,dict):source_names.setdefault(str(item.get('item') or '').strip().casefold(),set()).add(identity(item))
    selected=str(preview.get('offer_supplier_index',''))
    if official:
        winners=[i for i,s in enumerate(suppliers) if s.get('supplier')==official.get('winner')]
        if len(winners)==1:
            suppliers[winners[0]]['items']=deepcopy(official['winning_items'])
            selected=str(winners[0])
        elif official.get('winner') and official.get('winning_items'):
            suppliers.append(dict(supplier=official['winner'],amount=official.get('winning_amount'),
                                  remarks='',items=deepcopy(official['winning_items'])))
            selected=str(len(suppliers)-1)
    elif selected.isdigit() and int(selected)<len(suppliers):
        original=suppliers[int(selected)].get('items') or []
        corrected=deepcopy(preview.get('offer_items') or original)
        for index,item in enumerate(corrected):
            if index<len(original):
                item['_workspace_identity']=identity(original[index])
                for key in ('source','source_item_no','source_category'):
                    if key not in item and key in original[index]:item[key]=original[index][key]
        suppliers[int(selected)]['items']=corrected
    reviewed=preview.get('reviewed_offers')
    if not isinstance(reviewed,dict):reviewed={}
    for index,supplier in enumerate(suppliers):
        saved=reviewed.get(str(index))
        if str(index)!=selected and isinstance(saved,list) and all(isinstance(i,dict) for i in saved):
            original=supplier.get('items') or [];corrected=deepcopy(saved)
            for ordinal,item in enumerate(corrected):
                if ordinal<len(original) and len(corrected)==len(original):
                    item['_workspace_identity']=identity(original[ordinal])
                if official:item['_private_review_correction']=True
            supplier['items']=corrected
    # A parser placeholder with no quotation is not an extracted supplier.
    visible=[i for i,s in enumerate(suppliers) if s.get('items') or s.get('supplier') not in ('','Unidentified Supplier',None)]
    groups=OrderedDict()
    for index in visible:
        for ordinal,item in enumerate(suppliers[index].get('items') or []):
            if not isinstance(item,dict):continue
            number=str(item.get('source_item_no') or '').strip()
            key=item.get('_workspace_identity') or identity(item)
            candidates=source_names.get(str(item.get('item') or '').strip().casefold(),set())
            # Legacy saved winning lines may lack the extraction-only number.
            # Reuse only one exact-name source group; never compare prices or
            # unrelated quantities to guess correspondence.
            if not number and len(candidates)==1:key=next(iter(candidates))
            group=groups.setdefault(key,dict(item=item.get('item') or 'Item requires review',number=number,cells={}))
            group['cells'].setdefault(index,[]).append(dict(item,ordinal=ordinal))
    return dict(suppliers=suppliers,indices=visible,rows=list(groups.values()),
                supplier_count=len(visible),item_count=len(groups),selected=selected,
                quotation_count=sum(len(suppliers[i].get('items') or []) for i in visible))
