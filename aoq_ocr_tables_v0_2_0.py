"""Source-bound ruled-table OCR and decimal diagnostics for manual AOQ review.

No Flask registration, database, model, policy or file persistence. Only explicit
ruled grids and recognizable headers are supported. All results need human review.
Coordinates refer to the orientation-corrected image, never the original page.
"""
from collections import defaultdict
from decimal import Decimal, localcontext
import math
import re

import numpy as np
from PIL import Image, ImageDraw, ImageOps
import pytesseract

from aoq_flexible import _configure_tesseract, _map_header, _table_text
from aoq_ocr_tables_v0_1_0 import _centres, cells_from_words

VERSION = '0.2.0'
NUMBER = re.compile(r'[+-]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?\Z')


def decimal_evidence(value):
    """Validate source syntax, retaining blanks/malformed values as unknown."""
    text = str(value if value is not None else '').strip()
    if not text or len(text) > 40 or not NUMBER.fullmatch(text):
        return None
    number = Decimal(text.replace(',', ''))
    return number if number.is_finite() else None


def _grid(image, threshold):
    ink = np.asarray(image.convert('L')) < threshold
    ys = _centres(np.flatnonzero(ink.mean(axis=1) >= .60))
    if not 3 <= len(ys) <= 202:
        return None
    xs = _centres(np.flatnonzero(ink[ys[0]:ys[-1] + 1].mean(axis=0) >= .65))
    if not 6 <= len(xs) <= 31:
        return None
    if min(b-a for a, b in zip(xs, xs[1:])) < 20 or min(b-a for a, b in zip(ys, ys[1:])) < 12:
        return None
    return xs, ys


def detect_grid(image):
    """Recover faded visible rules; do not infer absent row/column borders."""
    if image.width * image.height > 16_000_000 or min(image.size) < 50:
        return None
    physical = _physical_grid(image)
    if physical is not None:
        return physical
    candidates = [_grid(image, threshold) for threshold in (150, 200, 220)]
    valid = [grid for grid in candidates if grid is not None]
    if not valid:
        return None
    # Contradictory physical topology is not resolved by picking more cells.
    if len({(len(xs), len(ys)) for xs, ys in valid}) != 1:
        return None
    # Projection peaks alone can be letters in a decorative banner. Require
    # continuous vertical rules between the actual horizontal intersections.
    xs, ys = valid[0]
    ink = np.asarray(image.convert('L')) < 220
    if any(ink[ys[0]:ys[-1]+1,max(0,x-1):x+2].mean(axis=1).mean() < .85 for x in xs):
        return None
    return xs, ys


def _physical_grid(image):
    """Find thin continuous rules, then validate their shared region and edges.

    Opening removes glyph strokes. Removing wide fills separates rules from
    coloured headers; original runs restore the header's true upper boundary.
    No document-specific coordinates or source values participate.
    """
    from scipy.ndimage import minimum_filter1d, maximum_filter1d, label, find_objects
    gray = np.asarray(image.convert('L'))
    ink = gray < 235
    length = max(35,min(160,image.height//10))
    vertical = maximum_filter1d(minimum_filter1d(ink,size=length,axis=0,mode='constant'),
                                size=length,axis=0,mode='constant')
    thin = vertical & ink & ~minimum_filter1d(ink,size=9,axis=1,mode='constant')
    labelled,_ = label(thin)
    lines=[]
    for region in find_objects(labelled):
        if region is None:continue
        yy,xx=region
        if xx.stop-xx.start>8 or yy.stop-yy.start<length:continue
        x=(xx.start+xx.stop-1)//2
        column=vertical[:,x]
        changes=np.diff(np.pad(column.astype(np.int8),(1,1)))
        for start,end in zip(np.flatnonzero(changes==1),np.flatnonzero(changes==-1)):
            if start<=yy.start+3 and end>=yy.stop-3:
                lines.append((x,int(start),int(end)));break
    groups=[]
    for line in sorted(lines,key=lambda v:(v[1],v[2],v[0])):
        group=next((g for g in groups if abs(g[0][2]-line[2])<=6 and
                    min(g[0][2],line[2])-max(g[0][1],line[1]) >= .85*min(g[0][2]-g[0][1],line[2]-line[1])),None)
        if group is None:groups.append([line])
        else:group.append(line)
    candidates=[]
    for group in groups:
        xs=sorted(set(x for x,_,_ in group))
        xs=_centres(np.array(xs))
        if not 6<=len(xs)<=31 or min(b-a for a,b in zip(xs,xs[1:]))<20:continue
        top=min(a for _,a,_ in group)
        bottom=round(float(np.median([b for _,_,b in group])))
        strip=gray[max(0,top-3):min(image.height,bottom+3),xs[0]:xs[-1]+1].astype(np.int16)
        # Real horizontal borders/colour transitions span most columns. Header
        # letters change only a small part of a row and cannot establish a rule.
        edges=(np.abs(np.diff(strip,axis=0))>20).mean(axis=1)
        positions=np.flatnonzero(edges>=.75)+max(0,top-3)
        ys=[round(float(band.mean())) for band in np.split(positions,np.flatnonzero(np.diff(positions)>6)+1) if len(band)]
        if not 3<=len(ys)<=202 or min(b-a for a,b in zip(ys,ys[1:]))<12:continue
        if abs(ys[0]-top)>8 or abs(ys[-1]-bottom)>8:continue
        candidates.append((len(xs)*len(ys),(xs[-1]-xs[0])*(ys[-1]-ys[0]),xs,ys))
    if not candidates:return None
    _,_,xs,ys=max(candidates,key=lambda c:(c[0],c[1]))
    return xs,ys


def _clean(image, xs, ys):
    # Preserve clear-image pixels and decimal punctuation. Any contradictory
    # OCR numbers remain unverified; arithmetic never repairs source evidence.
    cleaned = image.copy()
    draw = ImageDraw.Draw(cleaned)
    # Faded/blurred rules are wider than their darkest centre. Remove bounded
    # bands only; do not threshold text or synthesize characters.
    width = 11 if _grid(image, 150) is None else 5
    for x in xs:
        draw.line((x, ys[0], x, ys[-1]), fill='white', width=width)
    for y in ys:
        draw.line((xs[0], y, xs[-1], y), fill='white', width=width)
    return cleaned


def _ocr(image, timeout):
    return pytesseract.image_to_data(image, config='--oem 3 --psm 6',
                                     output_type=pytesseract.Output.DICT, timeout=timeout)


def _header_score(rows):
    fields = {_map_header(value) for row in rows[:2] for value in row if value}
    return len(fields & {'supplier', 'item', 'item_no', 'description', 'qty', 'unit', 'unit_cost', 'amount'})


def _ppmp_header_valid(rows):
    from procurement_document_layout import header_role
    roles={header_role(value) for row in rows[:2] for value in row if value}
    return {'name','project_type','quantity_size','unit_cost','budget'} <= roles


def _safe_cells(data, xs, ys, minimum):
    rows, evidence, warnings = cells_from_words(data, xs, ys, minimum)
    # Font ascenders/descenders shift word tops by a few pixels. Sorting exact
    # tops scrambled a single line ("80 gsm, 500 sheets") inside physical cells.
    from bisect import bisect_right
    from procurement_document_layout import ordered_word_text
    positioned = defaultdict(list)
    for i, text in enumerate(data.get('text', [])):
        if not str(text).strip():continue
        left, top, width, height = (int(data[key][i]) for key in ('left', 'top', 'width', 'height'))
        r = bisect_right(ys, top + height/2)-1
        c = bisect_right(xs, left + width/2)-1
        if 0 <= r < len(rows) and 0 <= c < len(rows[r]):
            positioned[r+1,c+1].append(dict(text=str(text),left=left,top=top,width=width,height=height))
    for cell in evidence:
        text = ordered_word_text(positioned[cell['row'],cell['column']])
        cell['raw_text'] = text
        if not cell['uncertain']:
            rows[cell['row']-1][cell['column']-1] = text
    # A vertical straddle contaminates both physical rows, not just its centre.
    affected = set()
    for i, text in enumerate(data.get('text', [])):
        if not str(text).strip():
            continue
        left, top = int(data['left'][i]), int(data['top'][i])
        right, bottom = left + int(data['width'][i]), top + int(data['height'][i])
        rr = [r for r in range(len(ys)-1) if top < ys[r+1]-2 and bottom > ys[r]+2]
        cc = [c for c in range(len(xs)-1) if left < xs[c+1]-2 and right > xs[c]+2]
        if len(rr) > 1 or len(cc) > 1:
            affected.update((r+1, c+1) for r in rr for c in cc)
    by_cell = {(cell['row'], cell['column']): cell for cell in evidence}
    for r in range(len(rows)):
        for c in range(len(rows[r])):
            if (r+1, c+1) not in by_cell:
                cell = dict(row=r+1, column=c+1, raw_text='', minimum_word_confidence=None, uncertain=False)
                evidence.append(cell)
                by_cell[(r+1, c+1)] = cell
    for cell in evidence:
        r, c = cell['row']-1, cell['column']-1
        cell['uncertain'] = cell['uncertain'] or (r+1, c+1) in affected
        cell['bbox'] = [xs[c], ys[r], xs[c+1], ys[r+1]]
        cell['blank'] = not cell['raw_text']
        if cell['uncertain']:
            # Supplier and item text can misattribute a confident price too.
            rows[r][c] = ''
    return rows, evidence, warnings


def _merged_headers(image, xs, ys, rows):
    """Only absent interior rules can establish a physical merged header span."""
    ink = np.asarray(image.convert('L')) < 220
    spans = []
    for r in range(min(2, len(rows))):
        start = 0
        for boundary in range(1, len(xs)):
            present = boundary == len(xs)-1 or ink[ys[r]+6:ys[r+1]-6, max(0, xs[boundary]-2):xs[boundary]+3].mean() >= .5
            if present:
                if boundary-start > 1:
                    texts = [v for v in rows[r][start:boundary] if v]
                    if len(texts) == 1:
                        spans.append(dict(row=r+1, first_column=start+1, last_column=boundary, text=texts[0]))
                start = boundary
    return spans


def _wide_review(rows, source, page, header_spans):
    """Two-level supplier headers; no forward filling without a physical span."""
    if len(rows) < 3:
        return [], [], ['Supplier/field ownership is unresolved; quotation mapping was withheld.']
    common = {f: c for c, value in enumerate(rows[1]) if (f := _map_header(value)) in {'item', 'item_no', 'description', 'qty', 'unit'}}
    if not ({'item', 'description'} & common.keys()) or 'qty' not in common:
        return [], [], ['Supplier/field ownership is unresolved; quotation mapping was withheld.']
    suppliers = defaultdict(dict)
    warnings = []
    for c, value in enumerate(rows[1]):
        field = _map_header(value)
        if field not in {'unit_cost', 'amount'}:
            continue
        name = str(rows[0][c]).strip() if c < len(rows[0]) else ''
        if not name:
            matches = [s['text'] for s in header_spans if s['row'] == 1 and s['first_column'] <= c+1 <= s['last_column']]
            name = matches[0] if len(matches) == 1 else ''
        if not name or _map_header(name):
            warnings.append(f'Unresolved supplier at physical column {c+1}; values were not assigned to another bidder.')
            continue
        if field in suppliers[name]:
            suppliers[name]['ambiguous'] = True
        suppliers[name][field] = c
    quotations, checks = [], []
    names = ['supplier', 'item', 'item_no', 'description', 'qty', 'unit', 'unit_cost', 'amount']
    headers = ['Supplier', 'Item', 'Item No.', 'Description', 'Qty', 'Unit', 'Unit Price', 'Amount']
    for name, metrics in suppliers.items():
        if metrics.get('ambiguous') or not {'unit_cost', 'amount'} <= metrics.keys():
            warnings.append('Incomplete or repeated supplier price headers; that bidder requires manual mapping.')
            continue
        columns = dict(common, **metrics)
        converted = [headers]
        for row in rows[2:]:
            converted.append([name if f == 'supplier' else str(row[columns[f]]) if f in columns and columns[f] < len(row) else '' for f in names])
        q, c, w = review_rows(converted, source=source, page=page)
        for item in q:
            item['row'] += 1
            item['field_columns'] = {f: col+1 for f, col in columns.items()}
            item['supplier_attribution'] = 'explicit_header_span'
        for check in c:
            check['row'] += 1
        quotations.extend(q); checks.extend(c); warnings.extend(w)
    return quotations, checks, warnings


def _mapping(rows):
    """Require a row-oriented explicit supplier header; no guessed bidders."""
    for r, row in enumerate(rows[:3]):
        fields = [_map_header(value) for value in row]
        known = [f for f in fields if f]
        if len(known) != len(set(known)):
            continue
        mapping = {field: c for c, field in enumerate(fields) if field}
        if {'supplier', 'qty', 'unit_cost'} <= mapping.keys() and ({'item', 'description'} & mapping.keys()):
            return r, mapping
    return None


def review_rows(rows, *, source, page=1, evidence=(), header_spans=()):
    """Keep source numbers separate from calculations. Never infer source cells."""
    quotations, checks, warnings = [], [], []
    mapped = _mapping(rows)
    if mapped is None:
        return _wide_review(rows, source, page, header_spans)
    header, mapping = mapped
    uncertain = {(cell['row'], cell['column']) for cell in evidence if cell.get('uncertain')}
    totals = defaultdict(list)
    source_totals = []
    for r, row in enumerate(rows[header+1:], header+2):
        get = lambda field: str(row[mapping[field]]).strip() if field in mapping and mapping[field] < len(row) else ''
        if sum(_map_header(value) == field for field, c in mapping.items() if c < len(row) for value in [row[c]]) >= 3:
            continue
        if not any(str(value).strip() for value in row):
            warnings.append(f'Blank physical row {r} requires source verification.')
            continue
        label = next((str(value).strip().casefold() for value in row if str(value).strip()), '')
        item_label = get('item') or get('description')
        if label in {'total', 'grand total', 'subtotal'} or item_label.casefold() in {'total', 'grand total', 'subtotal'}:
            source_totals.append((get('supplier'), get('amount'), r))
            continue
        item = {field: get(field) for field in ('supplier', 'item', 'item_no', 'description', 'qty', 'unit', 'unit_cost')}
        item['source_amount'] = get('amount')
        item.update(source=source, page=page, row=r, supplier_attribution='explicit' if item['supplier'] else 'uncertain',
                    field_columns={f: c+1 for f, c in mapping.items()}, requires_human_review=True)
        values = {f: decimal_evidence(item[f]) for f in ('qty', 'unit_cost', 'source_amount')}
        pending = [f for f in ('supplier', 'item', 'qty', 'unit', 'unit_cost', 'source_amount') if not item.get(f)]
        pending += [f for f, value in values.items() if value is None and item[f]]
        pending += [f for f, c in mapping.items() if (r, c+1) in uncertain]
        if not item['item'] and item['description']:
            # The source description remains a description, not invented item text.
            warnings.append(f'Item name absent at row {r}; use source description during review.')
        if any(value is not None and value < 0 for value in values.values()):
            pending.append('negative_numeric_evidence')
        qty, price, amount = (values[f] for f in ('qty', 'unit_cost', 'source_amount'))
        with localcontext() as context:
            context.prec = 100
            calculated = qty * price if qty is not None and price is not None else None
        item['calculated_amount'] = str(calculated) if calculated is not None else None
        status = 'unknown' if calculated is None or amount is None else ('match' if calculated == amount else 'mismatch')
        if status != 'match':
            pending.append('line_total_' + status)
        item['pending_fields'] = sorted(set(pending))
        item['source_numeric_values'] = {f: item[f] for f in ('qty', 'unit_cost', 'source_amount')}
        # These are mathematical checks, not procurement approval. A conflict
        # cannot establish which of quantity, price or amount is incorrect.
        item['numeric_evidence_status'] = 'consistent' if status == 'match' and not pending else 'unknown'
        item['validated_numeric_values'] = {
            f: str(value) if item['numeric_evidence_status'] == 'consistent' and value is not None else None
            for f, value in values.items()}
        quotations.append(item)
        checks.append(dict(kind='line_total', page=page, row=r, status=status,
                           source_value=item['source_amount'], calculated_value=item['calculated_amount']))
        totals[item['supplier']].append(amount)
    for supplier, raw, row in source_totals:
        amounts = totals.get(supplier, [])
        with localcontext() as context:
            context.prec = 100
            calculated = sum(amounts, Decimal(0)) if amounts and all(v is not None for v in amounts) else None
        supplied = decimal_evidence(raw)
        status = 'unknown' if supplied is None or calculated is None else ('match' if supplied == calculated else 'mismatch')
        checks.append(dict(kind='supplier_total', supplier=supplier, page=page, row=row, status=status,
                           source_value=raw, calculated_value=str(calculated) if calculated is not None else None,
                           calculation_scope='observed_rows_on_this_page'))
    if any(q['pending_fields'] for q in quotations) or any(c['status'] != 'match' for c in checks):
        warnings.append('Incomplete, uncertain or contradictory source evidence requires manual verification.')
    identities = [(q['supplier'].casefold(), q['item_no'], q['item'].casefold(), q['description'].casefold()) for q in quotations]
    if len(identities) != len(set(identities)):
        warnings.append('Possible duplicate source rows retained for human review.')
    return quotations, checks, warnings


def extract_ruled_table(image, *, source='Synthetic/local candidate', page=1, min_confidence=55, timeout=20, time_remaining=None):
    """Recognize orthogonal orientation using source headers, with bounded OCR."""
    result = dict(candidate_version=VERSION, integrated=False, authoritative=False, source_type='Experimental ruled image',
                  extraction_method='Offline Tesseract, physical cells', tables=[], raw_text='', warnings=[],
                  ocr_cells=[], quotations=[], numeric_checks=[], orientation_degrees=None,
                  coordinate_space='orientation-corrected image', source_page=page, original_size=list(image.size),
                  unresolved_orientations=[], header_spans=[],
                  total_scope_note='Page checks do not establish a cumulative multipage grand total.')
    if not math.isfinite(min_confidence) or not 0 <= min_confidence <= 100 or not 0 < timeout <= 60:
        raise ValueError('Invalid bounded OCR settings.')
    if image.width * image.height > 16_000_000:
        result['warnings'] = ['Image exceeds candidate pixel bound; manual review required.']
        return result
    original = ImageOps.exif_transpose(image).convert('RGB')
    _configure_tesseract()
    selected = None
    def bounded_ocr(frame):
        remaining = min(timeout, time_remaining()) if time_remaining is not None else timeout
        return _ocr(frame, remaining)
    try:
        for rotation in (0, 90, 180, 270):
            oriented = original.rotate(rotation, expand=True)
            grid = detect_grid(oriented)
            if grid is None:
                continue
            xs, ys = grid
            cleaned = _clean(oriented, xs, ys)
            top = cleaned.crop((0, 0, cleaned.width, ys[min(2, len(ys)-1)]))
            header_rows, header_evidence, _ = _safe_cells(bounded_ocr(top), xs, ys, min_confidence)
            if _ppmp_header_valid(header_rows):
                # A narrow number header may be omitted by full-width PSM 6.
                # Retry only a blank physical header cell, never infer its text.
                for col,value in enumerate(header_rows[0]):
                    if value or col>1:continue
                    left,upper,right,lower=xs[col]+3,ys[0]+3,xs[col+1]-3,ys[1]-3
                    data=bounded_ocr(cleaned.crop((left,upper,right,lower)))
                    data['left']=[x+left for x in data['left']]
                    data['top']=[y+upper for y in data['top']]
                    recovered,cells,_=_safe_cells(data,xs,ys,min_confidence)
                    if recovered[0][col]:
                        header_rows[0][col]=recovered[0][col]
                        header_evidence=[c for c in header_evidence if (c['row'],c['column'])!=(1,col+1)]
                        header_evidence.extend(c for c in cells if c['row']==1 and c['column']==col+1)
            result['unresolved_orientations'].append(dict(rotation=rotation, header_score=_header_score(header_rows),
                                                         header_cells=header_evidence))
            if _header_score(header_rows) >= 5 or _ppmp_header_valid(header_rows):
                selected = rotation, cleaned, xs, ys
                break
        if selected is None:
            result['warnings'] = ['No source-backed grid/header orientation; no quotation values were inferred.']
            return result
        rotation, cleaned, xs, ys = selected
        rows, evidence, warnings = _safe_cells(bounded_ocr(cleaned), xs, ys, min_confidence)
        # The independently captured header may contain a cell missed again by
        # the full-page pass. Retain that actual header text and its confidence.
        for col,value in enumerate(header_rows[0]):
            if not rows[0][col] and value:
                rows[0][col]=value
                evidence=[c for c in evidence if (c['row'],c['column'])!=(1,col+1)]
                evidence.extend(c for c in header_evidence if c['row']==1 and c['column']==col+1)
    except (RuntimeError, pytesseract.TesseractNotFoundError):
        result['warnings'] = ['Local OCR unavailable or timed out; source requires manual verification.']
        return result
    for cell in evidence:
        cell.update(page=page, source=source, coordinate_space=result['coordinate_space'])
    spans = _merged_headers(original.rotate(rotation, expand=True), xs, ys, rows)
    if _ppmp_header_valid(rows):
        # Planning estimates are not supplier quotations or award evidence.
        quotations,checks,diagnostics=[],[],[]
    else:
        quotations, checks, diagnostics = review_rows(rows, source=source, page=page, evidence=evidence, header_spans=spans)
    result.update(tables=[dict(source=source, page=page, rows=rows, bbox=[xs[0],ys[0],xs[-1],ys[-1]],
                              coordinate_space=result['coordinate_space'])], raw_text=_table_text(rows),
                  ocr_cells=evidence, quotations=quotations, numeric_checks=checks,
                  orientation_degrees=rotation, header_spans=spans, unresolved_orientations=[],
                  warnings=list(dict.fromkeys(warnings + diagnostics)))
    return result
