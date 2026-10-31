"""Conservative table roles and coordinate reconstruction for procurement sources.

Coordinates are evidence in the supplied image/page space, never inferred values.
Unknown cells stay blank. This module does not save or approve records.
"""
from statistics import median
import re


def norm(value):
    return re.sub(r'[^a-z0-9]+', ' ', str(value or '').lower()).strip()


ALIASES = {
    'item_no': ('item no', 'itemno', 'item number', 'no', 'item code'),
    'name': ('general description and objective of the project to be procured', 'item', 'item name', 'item particulars', 'description', 'item description', 'material description',
             'product description', 'description of goods', 'particulars', 'general description', 'general description and objective',
             'description specifications', 'description specification'),
    'specification': ('specification', 'specifications', 'technical specifications', 'technical specs', 'technical requirement', 'size'),
    'quantity': ('quantity', 'qty', 'requested quantity', 'quantity required', 'number of units',
                 'required volume', 'required units', 'required units technical specs', 'units needed'),
    'quantity_size': ('quantity and size', 'quantity and size of the project to be procured'),
    'unit': ('unit', 'uom', 'unit of measure', 'measurement unit', 'unit of measurement'),
    'unit_cost': ('unit cost', 'unit price', 'estimated unit cost', 'price per unit', 'rate'),
    'amount': ('amount', 'estimated amount', 'estimated total cost', 'total amount'),
    'supplier': ('supplier', 'supplier name', 'bidder', 'vendor', 'company'),
    'remarks': ('remarks', 'notes', 'comments'),
    'project_type': ('type of the project to be procured', 'type of project', 'project type', 'classification', 'category', 'procurement type'),
    'procurement_mode': ('recommended mode', 'recommended mode of procurement', 'mode of procurement',
                         'procurement mode', 'procurement method', 'method of procurement'),
    'pre_procurement': ('pre procurement conference', 'pre procurement conference applicable yes no',
                        'pre procurement conference applicable', 'pre bid conference', 'conference applicable'),
    'start_date': ('start date', 'start of procurement activity', 'procurement start', 'procurement activity start'),
    'end_date': ('end date', 'end of procurement activity', 'procurement end', 'procurement activity end'),
    'delivery_period': ('expected delivery', 'expected delivery implementation period', 'delivery period',
                        'implementation period', 'delivery schedule'),
    'fund_source': ('source of funds', 'fund source', 'funding source', 'funding', 'fund'),
    'budget': ('estimated budget', 'estimated budget allocation php', 'estimated budget allocation',
               'budget', 'budget estimate', 'approved budget', 'approved budget for the contract',
               'funding allocation', 'budget ceiling', 'financial requirement', 'proposed project cost'),
    'supporting_documents': ('attached supporting documents', 'supporting documents', 'supporting files',
                             'attachments', 'attached documents', 'documents attached'),
}
ROLE_BY_LABEL = {norm(alias): role for role, aliases in ALIASES.items() for alias in aliases}


def header_role(label):
    normalized=norm(label)
    role=ROLE_BY_LABEL.get(normalized,'')
    if not role:
        monetary=ROLE_BY_LABEL.get(re.sub(r'\s+(?:php|pesos?)$', '', normalized),'')
        if monetary in {'unit_cost','budget','amount'}:role=monetary
    return role


def table_header(rows, *, start_only=False):
    """Find an explicit item header at any bounded source row, including wrapping.

    Only known label fragments can form a multi-row header. Numeric body rows
    cannot extend a header. Return its last row so callers skip the whole band.
    """
    vocabulary = set(' '.join(ROLE_BY_LABEL).split()) | {'schedule', 'financial', 'estimated'}
    for index, row in enumerate(rows[:1] if start_only else rows):
        choices = []
        for depth in range(1, min(3, len(rows)-index)+1):
            band = rows[index:index+depth]
            if depth > 1 and any(set(norm(cell).split()) - vocabulary for line in band for cell in line if cell):
                break
            width = max(map(len, band), default=0)
            labels = []
            for column in range(width):
                parts = list(dict.fromkeys(str(line[column]).strip() for line in band
                    if column < len(line) and line[column] is not None and str(line[column]).strip()))
                combined = ' '.join(parts)
                # Specific combined labels outrank an isolated generic Unit.
                label = combined if header_role(combined) else next(
                    (part for part in reversed(parts) if header_role(part)), combined)
                labels.append(label)
            roles = {header_role(label) for label in labels} - {''}
            if 'name' in roles and roles & {'quantity', 'quantity_size', 'budget', 'amount', 'unit_cost'}:
                choices.append((len(roles), -depth, index+depth-1, labels))
        if choices:
            _, _, end, labels = max(choices, key=lambda choice: choice[:2])
            return end, labels
    return -1, []


def is_summary_label(value):
    return bool(re.fullmatch(r'(?:grand total(?: amount)?|subtotal|sub total|overall total|total(?: amount| cost| budget)?)(?: php)?', norm(value)))


def register_schema(fields):
    """Register the established twelve official labels without model inference."""
    for key, field in fields.items():
        ROLE_BY_LABEL[norm(field['label'])] = 'name' if key == 'description' else key
        for alias in field.get('aliases', []):
            ROLE_BY_LABEL.setdefault(norm(alias), 'name' if key == 'description' else key)


SHARED_ROLES = frozenset({'procurement_mode', 'pre_procurement', 'start_date', 'end_date',
                          'delivery_period', 'fund_source', 'supporting_documents', 'remarks'})


def text_region_role(text):
    """Classify explicit document context without using item-name blocklists."""
    text = str(text or '').strip()
    label = re.split(r'[:=]', text, maxsplit=1)[0].strip()
    role = header_role(label)
    if role in SHARED_ROLES:
        return 'shared_metadata'
    if re.match(r'^shared\s+(?:ppmp\s+)?fields\b', text, re.I):
        return 'shared_metadata'
    if norm(label) in {'category', 'ppmp category', 'fiscal year', 'office', 'requesting office'}:
        return 'document_header'
    if is_summary_label(label):
        return 'total'
    if re.match(r'^(?:prepared|approved|noted|certified|received)\s+by\b', text, re.I):
        return 'signature'
    if re.match(r'^---\s*Page\s+\d+', text, re.I):
        return 'page_boundary'
    if re.fullmatch(r'.*\b(?:PROJECT PROCUREMENT MANAGEMENT PLAN|ABSTRACT OF QUOTATIONS)\b.*', text, re.I):
        return 'document_title'
    return 'uncertain_region'


def classify_table_row(headers, row):
    """Require source identity plus independent row evidence inside a table.

    Item numbers support damaged rows but are optional. A lone description is a
    review candidate, never a draft. This decision does not fill any source cell.
    """
    from procurement_item_contract import decimal_number, quantity_number, interpret_item
    values = [(header_role(label), str('' if value is None else value).strip()) for label, value in zip(headers, row)]
    nonempty = [value for _, value in values if value]
    if not nonempty:
        return dict(role='blank', indicators=[])
    names = [value for role, value in values if role == 'name' and value]
    if is_summary_label(nonempty[0]) or any(is_summary_label(value) for value in names):
        return dict(role='total', indicators=[])
    if text_region_role(nonempty[0]) in {'shared_metadata', 'document_header', 'signature', 'document_title'}:
        return dict(role=text_region_role(nonempty[0]), indicators=[])
    roles = {header_role(value) for value in nonempty} - {''}
    if 'name' in roles and roles & {'quantity', 'quantity_size', 'unit_cost', 'budget', 'amount'}:
        return dict(role='item_table_header', indicators=[])
    identity = [value for value in names if not header_role(value) and not re.fullmatch(r'[\d,.]+', value)]
    indicators = ['description_cell'] if identity else []
    for role, value in values:
        if not value:
            continue
        if role == 'item_no' and re.fullmatch(r'(?:[A-Za-z]{0,8}[-/])?\d{1,12}(?:[./-]\d+)*', value):
            indicators.append('source_item_number')
        if role in {'quantity', 'quantity_size'}:
            quantity = quantity_number(value)
            if quantity is not None or interpret_item(value).get('quantity'):
                indicators.append('quantity_cell')
        if role in {'unit_cost', 'budget', 'amount'} and decimal_number(value, money=True) is not None:
            indicators.append('monetary_cell')
    indicators = list(dict.fromkeys(indicators))
    actual = bool(identity) and bool(set(indicators) - {'description_cell'})
    return dict(role='item_row' if actual else 'uncertain_region', indicators=indicators)


def ordered_word_text(words):
    """Read words left-to-right within tolerant baselines, then top-to-bottom."""
    if not words:
        return ''
    height = median(max(1, word['height']) for word in words)
    lines = []
    for word in sorted(words, key=lambda word: word['top'] + word['height']/2):
        centre = word['top'] + word['height']/2
        if not lines or centre - lines[-1][0] > height * .65:
            lines.append((centre, []))
        lines[-1][1].append(word)
    return '\n'.join(' '.join(word['text'] for word in sorted(line, key=lambda word: word['left'])) for _, line in lines)


def deskew_frame(frame):
    """Correct a small, well-supported tilt; preserve uncertain orientation.

    The bounded projection search never creates text. Evidence coordinates must
    identify this transformed image, not pretend to reference original pixels.
    """
    import numpy as np
    from PIL import ImageOps
    small = ImageOps.grayscale(frame)
    small.thumbnail((1000, 1000))
    ink = ImageOps.invert(small.point(lambda value: 255 if value > 180 else 0))
    def score(image):
        rows = (np.asarray(image, dtype=np.float32) > 0).sum(axis=1)
        return float(np.square(np.diff(rows)).sum())
    baseline = score(ink)
    if not baseline:
        return frame, 0
    candidates = [(score(ink.rotate(angle, expand=False)), angle) for angle in (-3,-2.5,-2,-1.5,-1,-.5,0,.5,1,1.5,2,2.5,3)]
    best, angle = max(candidates, key=lambda item: item[0])
    if angle and best >= baseline*1.3 and abs(angle) < 3:
        return frame.rotate(angle, expand=True, fillcolor='white'), angle
    return frame, 0


def positioned_shared_fields(words, *, tables=(), source='', page=1,
                             coordinate_space='orientation-corrected, deskewed image pixels'):
    """Read explicit shared labels and their own value column outside item tables.

    Labels may wrap. Values follow below the same horizontal anchor and stop at
    the next label or a vertical gap. Original words and bounds remain evidence.
    """
    outside=[w for w in words if not any(t.get('bbox') and
        t['bbox'][0]<=w['left']+w['width']/2<=t['bbox'][2] and
        t['bbox'][1]<=w['top']+w['height']/2<=t['bbox'][3] for t in tables)]
    if not outside:return []
    height=median(max(1,w['height']) for w in outside)
    lines=[]
    for word in sorted(outside,key=lambda w:(w['top']+w['height']/2,w['left'])):
        y=word['top']+word['height']/2
        if not lines or y-lines[-1]['y']>height*.65:lines.append(dict(y=y,words=[]))
        lines[-1]['words'].append(word)
    for line in lines:
        line['words'].sort(key=lambda w:w['left'])
        runs=[]
        for word in line['words']:
            if not runs or word['left']-(runs[-1][-1]['left']+runs[-1][-1]['width'])>height*1.8:runs.append([])
            runs[-1].append(word)
        line['runs']=runs
    anchors=[];label_markers=[]
    for index,line in enumerate(lines):
        for run in line['runs']:
            selected=list(run)
            for end in range(index,min(index+3,len(lines))):
                if end>index:
                    if lines[end]['y']-lines[end-1]['y']>height*1.8:break
                    following=[r for r in lines[end]['runs'] if abs(r[0]['left']-run[0]['left'])<height*2]
                    if len(following)!=1:break
                    selected.extend(following[0])
                label=ordered_word_text(selected).replace('\n',' ')
                role=header_role(label)
                # Item-number aliases include the boolean answer "No". They
                # do not delimit document-level field cards outside the table.
                if role and role != 'item_no':
                    label_markers.append(dict(left=run[0]['left'],line=end,top=selected[0]['top']))
                if role in SHARED_ROLES:
                    anchors.append(dict(label=label,role=role,left=run[0]['left'],
                        bottom=max(w['top']+w['height'] for w in selected),line=end,words=selected))
                    break
    result=[]
    for anchor in anchors:
        peers=[a['left'] for a in anchors if a['left']>anchor['left']+height*3]
        right=min(peers)-height if peers else float('inf')
        next_label=min((a['top'] for a in label_markers if a['line']>anchor['line'] and
            abs(a['left']-anchor['left'])<height*2),default=float('inf'))
        values=[];previous=anchor['bottom']
        for line in lines[anchor['line']+1:]:
            candidates=[w for w in line['words'] if anchor['left']-height<=w['left']<right and w['top']<next_label]
            if not candidates:continue
            top=min(w['top'] for w in candidates)
            if top-previous>height*(2.8 if not values else 1.8):break
            values.extend(candidates);previous=max(w['top']+w['height'] for w in candidates)
        if not values:continue
        bounds=lambda ws:[min(w['left'] for w in ws),min(w['top'] for w in ws),
                          max(w['left']+w['width'] for w in ws),max(w['top']+w['height'] for w in ws)]
        result.append(dict(label=anchor['label'],role=anchor['role'],value=ordered_word_text(values),
            source=source,page=page,label_bbox=bounds(anchor['words']),value_bbox=bounds(values),
            coordinate_space=coordinate_space,
            minimum_word_confidence=min((w['confidence'] for w in anchor['words']+values if w.get('confidence') is not None),default=None)))
    return result


def positioned_table(words, *, source, page, coordinate_space):
    """Recover an explicit header and its columns from physical word positions.

    Borderless fallback: a numbered row or an independently populated quantity
    starts an item; description-only lines continue the preceding row. Ambiguous
    nonempty rows remain separate review rows rather than disappearing.
    """
    words = [dict(w) for w in words if str(w.get('text') or '').strip()]
    if not words or len(words) > 50000:
        return None
    height = median(max(1, w['height']) for w in words)
    lines = []
    for word in sorted(words, key=lambda w: (w['top'] + w['height']/2, w['left'])):
        centre = word['top'] + word['height']/2
        if not lines or abs(lines[-1]['y'] - centre) > height * .65:
            lines.append(dict(y=centre, words=[]))
        lines[-1]['words'].append(word)
    for line in lines:
        line['words'].sort(key=lambda w: w['left'])

    def anchors(line):
        found, index = [], 0
        values = line['words']
        while index < len(values):
            match = None
            for end in range(min(len(values), index + 14), index, -1):
                if any(b['left'] - (a['left'] + a['width']) > height * 1.8
                       for a, b in zip(values[index:end], values[index+1:end])):
                    continue
                label = ' '.join(w['text'] for w in values[index:end])
                role = header_role(label)
                if role:
                    match = dict(label=label, role=role, left=values[index]['left'])
                    index = end
                    break
            if match:
                found.append(match)
            else:
                index += 1
        return found

    def band_anchors(band):
        # Wrapped headers own horizontal text bands. Join only overlapping
        # spans across neighboring header baselines, never body rows.
        groups = []
        for line in band:
            run = []
            for word in line['words']:
                if run and word['left']-(run[-1]['left']+run[-1]['width']) > height*1.8:
                    groups.append(run)
                    run = []
                run.append(word)
            if run:
                groups.append(run)
        spans = []
        for run in sorted(groups, key=lambda run: min(w['left'] for w in run)):
            left, right = min(w['left'] for w in run), max(w['left']+w['width'] for w in run)
            if spans and left <= spans[-1]['right']:
                spans[-1]['words'].extend(run)
                spans[-1]['right'] = max(spans[-1]['right'], right)
            else:
                spans.append(dict(left=left, right=right, words=list(run)))
        found = []
        for span in spans:
            label = ordered_word_text(span['words']).replace('\n', ' ')
            role = header_role(label)
            if not role:
                return []
            found.append(dict(label=label, role=role, left=span['left']))
        return found

    header_index, header_end, columns = None, None, None
    for index, line in enumerate(lines):
        choices = [(index, anchors(line))]
        for end in range(index+2, min(index+5, len(lines)+1)):
            if any(b['y']-a['y'] > height*1.8 for a, b in zip(lines[index:end], lines[index+1:end])):
                break
            choices.append((end-1, band_anchors(lines[index:end])))
        valid = []
        for end, found in choices:
            roles = [c['role'] for c in found]
            repeated = {role for role in roles if roles.count(role) > 1}
            if ('name' in roles and {'quantity', 'quantity_size'} & set(roles)
                    and len(set(roles)) >= 3 and not (repeated - {'name'})):
                valid.append((len(set(roles)), end, found))
        if valid:
            _, header_end, columns = max(valid, key=lambda entry: (entry[0], entry[1]))
            header_index = index
            break
    if columns is None:
        return None
    # Header left edges define aligned cells; allow small OCR indentation jitter.
    bounds = [c['left'] - height * .45 for c in columns]
    rows, evidence = [[c['label'] for c in columns]], []
    for col, column in enumerate(columns):
        header_words = [w for line in lines[header_index:header_end+1] for w in line['words'] if w['left'] >= bounds[col]
                        and (col+1 == len(bounds) or w['left'] < bounds[col+1])]
        confidences = [w['confidence'] for w in header_words if w.get('confidence') is not None]
        confidence = min(confidences) if confidences else None
        evidence.append(dict(source=source, page=page, row=1, column=col+1, raw_text=column['label'],
            minimum_word_confidence=confidence, uncertain=confidence is not None and confidence < 55,
            coordinate_space=coordinate_space))
    raw_lines = []
    used_lines = set(range(header_index, header_end+1))
    previous_y = None
    table_active = True
    for line_index, line in enumerate(lines):
        raw_lines.append(' '.join(w['text'] for w in line['words']))
        if line_index <= header_end:
            continue
        repeated = anchors(line)
        if {c['role'] for c in repeated} >= {'name', 'quantity', 'unit'}:
            used_lines.add(line_index)
            table_active = True
            continue
        line_text = ' '.join(w['text'] for w in line['words'])
        if text_region_role(line_text) in {'shared_metadata', 'document_header', 'signature', 'document_title'}:
            table_active = False
            continue
        if not table_active:
            continue
        if re.match(r'^(?:(?:not\s+)?(?:winning supplier|winning bidder|awarded to|recommended supplier)|prepared by|approved by|remarks)\s*[:=]', line_text, re.I):
            continue
        cells, cell_words = [''] * len(columns), [[] for _ in columns]
        straddled = set()
        for word in line['words']:
            eligible = [i for i, left in enumerate(bounds) if word['left'] >= left]
            if not eligible:
                continue
            col = eligible[-1]
            cell_words[col].append(word)
            if col+1 < len(bounds) and word['left']+word['width'] > bounds[col+1] + height*.5:
                straddled.update((col,col+1))
        for col, values in enumerate(cell_words):
            cells[col] = ' '.join(w['text'] for w in values)
        first = next((norm(v) for v in cells if v), '')
        if is_summary_label(first):
            rows.append(cells)
            used_lines.add(line_index)
            table_active = not bool(re.match(r'^(?:grand|overall) total\b', first))
            continue
        roles = {c['role']: i for i, c in enumerate(columns)}
        identifier = cells[roles['item_no']] if 'item_no' in roles else ''
        numeric = any(cells[i] for role, i in roles.items() if role in {'quantity', 'unit_cost', 'amount', 'budget'})
        text_only = any(cells) and all(not v or columns[i]['role'] in {'name', 'specification'} for i, v in enumerate(cells))
        continuation = (text_only and not identifier and not numeric and len(rows) > 1
                        and previous_y is not None and line['y'] - previous_y < height * 1.8)
        row_index = len(rows) - 1 if continuation else len(rows)
        if continuation:
            rows[-1] = [' '.join(filter(None, (old, new))) for old, new in zip(rows[-1], cells)]
        elif any(cells):
            rows.append(cells)
        else:
            continue
        previous_y = line['y']
        used_lines.add(line_index)
        for col, values in enumerate(cell_words):
            if not values:
                continue
            confs = [w['confidence'] for w in values if w.get('confidence') is not None]
            confidence = min(confs) if confs else None
            uncertain = (confidence is not None and confidence < 55) or col in straddled
            evidence.append(dict(source=source, page=page, row=row_index+1, column=col+1,
                line=line_index+1, raw_text=cells[col], minimum_word_confidence=confidence,
                uncertain=uncertain, crosses_column_boundary=col in straddled, coordinate_space=coordinate_space,
                bbox=[min(w['left'] for w in values), min(w['top'] for w in values),
                      max(w['left']+w['width'] for w in values), max(w['top']+w['height'] for w in values)]))
            if uncertain:
                rows[row_index][col] = ''
    if len(rows) < 2:
        return None
    return dict(source=source, page=page, rows=rows, cells=evidence,
                raw_text='\n'.join(raw_lines), coordinate_space=coordinate_space,
                bbox=[min(w['left'] for w in words), lines[header_index]['y']-height,
                      max(w['left']+w['width'] for w in words), (previous_y or lines[header_index]['y'])+height],
                non_table_text='\n'.join(text for i,text in enumerate(raw_lines) if i not in used_lines))
