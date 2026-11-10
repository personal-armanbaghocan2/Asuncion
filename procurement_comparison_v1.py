"""Source-bound PR/PO evidence. Model prediction never overrides technical facts.

No database, network, downloads, training, procurement mutation or auto-approval.
Semantic scores suggest correspondence; only unambiguous names or recorded human
correspondence resolve identity. Original documents are never rewritten.
"""
from collections import Counter
from decimal import Decimal,InvalidOperation
import hashlib
import json
import math
import re

VERSION='PR_PO_REVIEW_V1'
# Shared planning context is retained in source records and model input, but is
# not an item specification which a supplier must repeat on its purchase order.
PLANNING_CONTEXT = {
 'general description and objective of the project to be procured',
 'type of the project to be procured',
 'quantity and size of the project to be procured',
}
NAME=('Item Name','item','name','Item')
QUANTITY=('Quantity','quantity','qty','QTY','Requested Quantity','Awarded Quantity')
UNIT=('Unit','unit','Unit of Measure','unit_of_measure','uom')
DESCRIPTIVE={'description','description / specification','item description','specification','specifications',
 'detailed specifications','technical specifications','brand','model','item type','category','capacity',
 'dimensions','dimension','size','material','weight','color','colour','technical ratings','rating','source category',
 'compatibility','performance','processor','cpu','ram','memory','ssd','hdd','storage','interface',
 'general description and objective of the project to be procured','full description','detailed specification',
 'type of the project to be procured','article','article / description',
 'quantity and size of the project to be procured','warranty','warranty period','operating system','body material'}
UNITS={'pc':'piece','pcs':'piece','pieces':'piece','piece':'piece','units':'unit','unit':'unit',
 'ream':'ream','reams':'ream','bottle':'bottle','bottles':'bottle','set':'set','sets':'set'}

def norm(value):return re.sub(r'\s+',' ',str(value or '')).strip().casefold()

def specification_tokens(value):
    """Bounded spelling/format equivalence, never human adjudication of prose.

    Keep numbers, operators, negation and punctuation. Unknown wording stays
    different even after a reviewer establishes the corresponding item.
    """
    text=norm(value)
    for pattern,replacement in (
        (r'\bsolid[ -]state drive\b','ssd'),
        (r'\bgigabytes?\b','gb'),(r'\bmegabytes?\b','mb'),(r'\bterabytes?\b','tb'),
        (r'\bat least\b','minimum'),(r'\bat most\b','maximum')):
        text=re.sub(pattern,replacement,text)
    paper_patterns=(r'\b(a[0-6]|b[0-6]|letter|legal)\b',
                    r'\b(\d+(?:\.\d+)?)\s*(?:gsm|g/m[²2])\b',
                    r'\b(\d+)\s*sheets?\s*(?:per|/)\s*ream\b')
    paper=[list(re.finditer(pattern,text)) for pattern in paper_patterns]
    if all(len(matches)==1 for matches in paper):
        remainder=text
        for match in sorted((matches[0] for matches in paper),key=lambda m:m.start(),reverse=True):
            remainder=remainder[:match.start()]+' '+remainder[match.end():]
        if re.fullmatch(r'(?:\s|[,;:]|\bcopy\b|\bpaper\b)*',remainder):
            return ('paper',paper[0][0][1],str(Decimal(paper[1][0][1]).normalize()),str(Decimal(paper[2][0][1]).normalize()))
    # A bounded exact USB-capacity grammar supports reordered factual wording
    # in the existing equivalent-item fixture. Every other word must belong to
    # the optional product phrase; unknown requirements/negations cannot vanish.
    capacities=list(re.finditer(r'\b(\d+(?:\.\d+)?)\s*(gb|mb|tb)\b',text))
    interfaces=list(re.finditer(r'\busb\s*(\d+(?:\.\d+)?)\b',text))
    if len(capacities)==len(interfaces)==1:
        remaining=text
        for match in sorted(capacities+interfaces,key=lambda match:match.start(),reverse=True):
            remaining=remaining[:match.start()]+' '+remaining[match.end():]
        if re.fullmatch(r'\s*(?:flash\s+(?:memory|drive|storage))?\s*',remaining):
            return ('exact USB capacity',str(Decimal(capacities[0][1])),capacities[0][2],
                    'interface',str(Decimal(interfaces[0][1])))
    return re.findall(r'\d+(?:\.\d+)?|[^\W\d_]+|[^\w\s]',text)
def first(item,keys):return next((item[k] for k in keys if item.get(k) not in (None,'')),None)
def fingerprint(text):return hashlib.sha256(text.encode('utf-8')).hexdigest()

def number(value):
    if isinstance(value,bool) or value is None:return None
    text=str(value).strip()
    if not re.fullmatch(r'[+]?(?:\d+|\d{1,3}(?:,\d{3})+)(?:\.\d+)?',text):return None
    try:
        result=Decimal(text.replace(',',''))
        return result if result.is_finite() and result>=0 else None
    except InvalidOperation:return None

def classification_scope(item,label,value):
    """Recognize only source-backed planning aliases, not arbitrary item types.

    normalize_excel_row copies the PPMP project description into Category.
    AOQ's category input accepts DBM/NON-DBM/LIB; these are procurement classes.
    Unknown categories and explicitly required product types stay technical.
    """
    if label in ('category','source category'):
        if value in ('dbm','non-dbm','lib'):return 'procurement classification'
        general=next((norm(v) for k,v in item.items()
                      if norm(str(k).replace('_',' '))=='general description and objective of the project to be procured'),None)
        if general and value==general:return 'planning description'
    if label in ('item type','type of the project to be procured','type of project'):
        if value in ('goods','services','infrastructure','infrastructure projects','consulting services'):
            return 'procurement project type'
    return None

def evidence(item,position,side,*,technical=False):
    if type(item) is not dict:raise ValueError('SOURCE_ITEM_OBJECT_REQUIRED')
    fields={};context={}
    for key,value in item.items():
        label=norm(str(key).replace('_',' '))
        scope=classification_scope(item,label,norm(value)) if technical else None
        if scope:
            context.setdefault(scope,set()).add(norm(value))
            continue
        if technical and label in PLANNING_CONTEXT:continue
        if not str(key).startswith('_') and (key in NAME or label in DESCRIPTIVE
            or technical and label in {'required category','required item type','product category','product type'} or re.search(
            r'capacity|model|power|voltage|length|width|height|frequency|technical|resolution|speed|compatib|specification|material|colou?r|brand|size|weight|rating|performance|warranty|operating system',label)):
            if value is None or type(value) not in (str,int,float):continue
            text=str(value).strip()
            if len(text)>8000:raise ValueError('SOURCE_DESCRIPTION_LIMIT')
            if text:fields[key]=text
    name=str(first(item,NAME) or first(item,('description','Description / Specification','Item Description')) or '').strip()
    ref=next((str(item[k]) for k in ('_PR Item ID','_PO Item ID','_AOQ Item ID') if item.get(k)),f'{side}:{position}')
    description=' | '.join(f'{key}: {value}' for key,value in fields.items())
    if len(description)>16000:raise ValueError('SOURCE_DESCRIPTION_LIMIT')
    quantities=[number(item[k]) for k in QUANTITY if item.get(k) not in (None,'')]
    units=[UNITS.get(norm(item[k]),norm(item[k])) for k in UNIT if item.get(k) not in (None,'')]
    return dict(position=position,reference=ref,name=name,fields=fields,description=description,context=context,
        quantity=first(item,QUANTITY),unit=first(item,UNIT),
        conflicting_quantity=len(set(quantities))>1,conflicting_unit=len(set(units))>1)

def project(items,side,*,technical=False):
    if type(items) is not list or not 1<=len(items)<=200:raise ValueError('SOURCE_ITEM_LIMIT')
    rows=[evidence(item,index,side,technical=technical) for index,item in enumerate(items,1)]
    if any(not row['description'] for row in rows):raise ValueError('SOURCE_DESCRIPTION_REQUIRED')
    if sum(len(row['description']) for row in rows)>1000000:raise ValueError('SOURCE_DOCUMENT_LIMIT')
    return rows

def inference_items(items,side):
    return [dict(item=row['name'],description=row['description'],qty=row['quantity'],unit=row['unit'])
            for row in project(items,side)]

def facts(row):
    result={}
    text=row['description']
    for match in re.finditer(r'(?i)\b(A[0-6]|B[0-6]|letter|legal)\b',text):
        result.setdefault('paper size',[]).append(dict(value=norm(match[1]),unit='literal',operator='exact'))
    for attribute,pattern,unit in (
        ('paper weight',r'(?i)\b(\d+(?:\.\d+)?)\s*(?:gsm|g/m[²2])\b','gsm'),
        ('sheets per ream',r'(?i)\b(\d+)\s*sheets?\s*(?:per|/|a)\s*ream\b','sheets/ream')):
        for match in re.finditer(pattern,text):
            result.setdefault(attribute,[]).append(dict(value=str(Decimal(match[1])),unit=unit,operator='exact'))
    # No silent GB/TB binary/decimal conversion: mixed capacity units remain a
    # difference for review. Explicit minimum/maximum applies to that occurrence.
    for match in re.finditer(r'(?i)(?:(minimum|maximum|at least|at most)\s+)?(\d+(?:\.\d+)?)\s*(GB|MB|TB)\b',text):
        context=text[max(0,match.start()-35):match.start()].casefold()
        attribute='memory' if re.search(r'ram|memory',context) and not re.search(r'flash|storage|ssd|hdd',context) else 'capacity'
        result.setdefault(attribute,[]).append(dict(value=str(Decimal(match[2])),unit=match[3].upper(),operator=(match[1] or 'exact').lower()))
    for match in re.finditer(r'(?i)\bUSB\s*(\d+(?:\.\d+)?)\b',text):
        result.setdefault('USB interface',[]).append(dict(value=str(Decimal(match[1]).normalize()),unit='version',operator='exact'))
    for match in re.finditer(r'(?i)\b\d+(?:\.\d+)?\s*[x×]\s*\d+(?:\.\d+)?(?:\s*[x×]\s*\d+(?:\.\d+)?)?\s*(?:mm|cm|m)\b',text):
        result.setdefault('dimensions',[]).append(dict(value=re.sub(r'\s+','',match[0]).lower().replace('×','x'),unit='literal',operator='exact'))
    for match in re.finditer(r'(?i)(?:(minimum|maximum|at least|at most)\s+)?(\d+(?:\.\d+)?)\s*(MB/s|GB/s|Mbps|Gbps|mAh|Ah|GHz|MHz|kHz|Hz|mm|cm|kg|ml|dpi|MP|inches|inch|in|g|m|V|W|A|L)\b',text):
        result.setdefault('measurement '+match[3].lower(),[]).append(dict(value=str(Decimal(match[2])),unit=match[3].lower(),operator=(match[1] or 'exact').lower()))
    for key,value in row['fields'].items():
        if norm(key) in {'brand','model','material','color','colour','compatibility','dimensions','size','weight','technical ratings','rating',
            'processor','cpu','ram','memory','ssd','hdd','storage','interface','resolution','performance','performance characteristics',
            'warranty','warranty period','operating system','body material'}:
            result.setdefault(norm(key).replace('colour','color'),[]).append(dict(value=norm(value),unit='literal',operator='exact'))
    for match in re.finditer(r'(?i)\b(model|brand|material|colou?r)\s*[:=]\s*([\w.-]+)',text):
        result.setdefault(match[1].lower().replace('colour','color'),[]).append(dict(value=norm(match[2]),unit='literal',operator='exact'))
    # Repeated source aliases must not multiply the same requirement. Preserve
    # different values: contradictory specifications must still block a match.
    for values in result.values():
        for value in values:
            if value['unit'] not in ('literal','version'):
                value['value']=format(Decimal(value['value']).normalize(),'f')
    return {attribute:list({json.dumps(value,sort_keys=True):value for value in values}.values())
            for attribute,values in result.items()}

def check_pair(left,right,identity):
    findings=[]
    def add(attribute,state,requested,offered):
        findings.append(dict(attribute=attribute,state=state,requested=requested,offered=offered,
                             conditional_on_identity=identity=='CANDIDATE'))
    lq,rq=number(left['quantity']),number(right['quantity'])
    add('quantity','UNKNOWN' if left['conflicting_quantity'] or right['conflicting_quantity'] or lq is None or rq is None or lq<=0 or rq<=0 else 'MATCH' if lq==rq else 'MISMATCH',left['quantity'],right['quantity'])
    lu,ru=norm(left['unit']),norm(right['unit'])
    add('unit','UNKNOWN' if left['conflicting_unit'] or right['conflicting_unit'] or not lu or not ru else 'MATCH' if UNITS.get(lu,lu)==UNITS.get(ru,ru) else 'MISMATCH',left['unit'],right['unit'])
    # Missing copies of planning classifications are not missing supplier
    # specifications. Conflicting recorded classifications still block review.
    for attribute in ('procurement classification','procurement project type'):
        required=sorted(left['context'].get(attribute,()))
        offered=sorted(right['context'].get(attribute,()))
        if len(required)>1 or len(offered)>1 or required and offered and required!=offered:
            add(attribute,'MISMATCH',required,offered)
    categories=('category','source category','item type','type of the project to be procured')
    for category,aliases in [('category',('category','source category')),('item type',('item type',)),
                             ('type of the project to be procured',('type of the project to be procured',))]:
        required=sorted({norm(value) for key,value in left['fields'].items() if norm(key.replace('_',' ')) in aliases})
        offered=sorted({norm(value) for key,value in right['fields'].items() if norm(key.replace('_',' ')) in aliases})
        # An unproven classification is not automatically a supplier technical
        # requirement. Keep it unresolved (and approval-blocking) when absent.
        if required:add(category,'UNKNOWN' if not offered else 'MATCH' if required==offered else 'MISMATCH',required,offered)
    narrative_keys={'description','description / specification','item description','specification','specifications','detailed specifications','technical specifications'}
    requested_text=' '.join(dict.fromkeys(norm(v) for k,v in left['fields'].items() if norm(k) in narrative_keys))
    offered_text=' '.join(dict.fromkeys(norm(v) for k,v in right['fields'].items() if norm(k) in narrative_keys))
    if requested_text:
        add('source specification wording', 'MATCH' if specification_tokens(requested_text)==specification_tokens(offered_text) else
            'MISSING_SPECIFICATION' if not offered_text else
            'REQUIRES_HUMAN_REVIEW', requested_text,offered_text)
    requested,offered=facts(left),facts(right)
    # A retained structured attribute without a dedicated fact parser must not
    # disappear from compliance checks (e.g. "Warranty duration (months)").
    # Only exact bounded spelling/format equivalence resolves it; mapping never
    # adjudicates a different value. Known parsed attributes keep their rules.
    for key,value in left['fields'].items():
        label=norm(key.replace('_',' '))
        if key in NAME or label in narrative_keys or label in categories or label in requested:continue
        available=[v for k,v in right['fields'].items() if norm(k.replace('_',' '))==label]
        state=('MISSING_SPECIFICATION' if not available else 'MATCH'
               if all(specification_tokens(value)==specification_tokens(v) for v in available)
               else 'REQUIRES_HUMAN_REVIEW')
        add('source specification field '+label,state,value,available)
    for attribute,values in requested.items():
        available=offered.get(attribute,[])
        for required in values:
            compatible=[value for value in available if value['unit']==required['unit']]
            if not compatible:state='MISSING_SPECIFICATION'
            else:
                def satisfies(value):
                    if required['unit']=='literal':return value['value']==required['value']
                    a,b=Decimal(required['value']),Decimal(value['value'])
                    if required['operator'] in ('minimum','at least'):return b>=a
                    if required['operator'] in ('maximum','at most'):return b<=a
                    return a==b
                # A matching occurrence must not hide a contradictory value in
                # the same offered attribute (e.g. "32GB / 16GB"). Ambiguous
                # quotations require correction/review instead of cherry-picking.
                state='MATCH' if all(satisfies(value) for value in compatible) else 'MISMATCH'
            add(attribute,state,required,available)
    return findings

def fasttext_candidates(pr_items,po_items,model_root):
    """Real installed FastText vectors; no synthetic similarity or rule fallback."""
    import fasttext_nlp_runtime as runtime
    import numpy as np
    assets=runtime.load_assets(model_root)
    left,right=project(pr_items,'PR'),project(po_items,'PO')
    av=[assets.feature_module.sentence_vector(assets.fasttext_model,row['description']) for row in left]
    bv=[assets.feature_module.sentence_vector(assets.fasttext_model,row['description']) for row in right]
    result=[]
    for index,row in enumerate(left):
        a=av[index]
        for other,offer in enumerate(right):
            b=bv[other]
            denominator=float(np.linalg.norm(a)*np.linalg.norm(b))
            score=float(np.dot(a,b)/denominator) if denominator else None
            if score is not None and math.isfinite(score):
                result.append(dict(pr_position=row['position'],po_position=offer['position'],
                    similarity=max(-1.,min(1.,score)),source='INSTALLED_FASTTEXT_COSINE_CANDIDATE_ONLY'))
    return result

def compare(pr_items,po_items,*,semantic_candidates=(),manual_mapping=None):
    left,right=project(pr_items,'PR',technical=True),project(po_items,'PO',technical=True)
    if manual_mapping is not None:
        if (type(manual_mapping) is not list or len(manual_mapping)!=len(left)
            or any(type(n) is not int or n<1 or n>len(right) for n in manual_mapping)
            or len(set(manual_mapping))!=len(manual_mapping)):
            raise ValueError('INVALID_ITEM_CORRESPONDENCE')
    lcounts=Counter(norm(row['name']) for row in left);rcounts=Counter(norm(row['name']) for row in right)
    used=set();rows=[]
    for index,row in enumerate(left):
        target=None;identity='UNRESOLVED';score=None
        if manual_mapping is not None:target=manual_mapping[index];identity='HUMAN_CONFIRMED'
        elif row['name'] and lcounts[norm(row['name'])]==rcounts[norm(row['name'])]==1:
            target=next(r['position'] for r in right if norm(r['name'])==norm(row['name']));identity='EXACT_NAME'
        else:
            ranked=sorted((v for v in semantic_candidates if v['pr_position']==row['position']),key=lambda v:v['similarity'],reverse=True)
            if ranked:target=ranked[0]['po_position'];score=ranked[0]['similarity'];identity='CANDIDATE'
            elif len(left)==len(right)==1:target=1;identity='CANDIDATE'
        offer=right[target-1] if target else None
        checks=check_pair(row,offer,identity) if offer else []
        if target and identity!='CANDIDATE':used.add(target)
        rows.append(dict(pr_position=row['position'],po_position=target,identity=identity,similarity=score,
            pr_reference=row['reference'],po_reference=offer['reference'] if offer else None,checks=checks))
    extra=[r['position'] for r in right if r['position'] not in used]
    unresolved=any(r['identity'] in ('UNRESOLVED','CANDIDATE') for r in rows)
    clean=not extra and not unresolved and all(c['state']=='MATCH' for row in rows for c in row['checks'])
    return dict(version=VERSION,technical_status='MATCH' if clean else 'NEEDS_REVIEW',rows=rows,
        extra_or_unallocated_items=extra,missing_or_unresolved_pr_items=[r['pr_position'] for r in rows if r['identity'] in ('UNRESOLVED','CANDIDATE')],
        source_fields=dict(PR=[dict(reference=r['reference'],fields=list(r['fields'])) for r in left],
                           PO=[dict(reference=r['reference'],fields=list(r['fields'])) for r in right]),
        automatic_approval=False,human_review_required=True)
