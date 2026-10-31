"""Source-bound procurement item interpretation, shared by OCR and Flask.

Quantities and money use Decimal. No prices, unit conversions, or approval
decisions are inferred from semantic similarity. Original text stays intact.
"""
from decimal import Decimal, InvalidOperation
import re

ITEM_REVIEW_FIELDS = ('Item Name', 'Quantity', 'Unit', 'Estimated Unit Cost')
GENERAL = 'General Description and Objective of the Project to be Procured'
SPECIFICATION = 'Quantity and Size of the Project to be Procured'

_SMALL = dict(zip('zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen seventeen eighteen nineteen'.split(), range(20)))
_TENS = dict(zip('twenty thirty forty fifty sixty seventy eighty ninety'.split(), range(20, 100, 10)))
_WORDS = '|'.join([*_SMALL, *_TENS, 'hundred', 'thousand', 'and'])
_NUMBER = rf'(?:\d[\d,]*(?:\.\d+)?|(?:\b(?:{_WORDS})\b[ -]*)+)(?:\s*\(\s*\d[\d,]*(?:\.\d+)?\s*\))?'
_UNITS = {
    'pc': 'pcs', 'pcs': 'pcs', 'piece': 'pcs', 'pieces': 'pcs',
    'unit': 'units', 'units': 'units', 'ream': 'reams', 'reams': 'reams',
    'box': 'boxes', 'boxes': 'boxes', 'pack': 'packs', 'packs': 'packs',
    'set': 'sets', 'sets': 'sets', 'roll': 'rolls', 'rolls': 'rolls',
    'bottle': 'bottles', 'bottles': 'bottles', 'bag': 'bags', 'bags': 'bags',
    'pair': 'pairs', 'pairs': 'pairs', 'lot': 'lots', 'lots': 'lots',
    'meter': 'meters', 'meters': 'meters', 'metre': 'meters', 'metres': 'meters', 'm': 'meters',
    'liter': 'liters', 'liters': 'liters', 'litre': 'liters', 'litres': 'liters', 'l': 'liters',
    'kg': 'kg', 'kilogram': 'kg', 'kilograms': 'kg', 'gram': 'grams', 'grams': 'grams',
    'sheet': 'sheets', 'sheets': 'sheets', 'carton': 'cartons', 'cartons': 'cartons',
    'gallon': 'gallons', 'gallons': 'gallons', 'can': 'cans', 'cans': 'cans',
}
_QUANTITY_UNIT = re.compile(rf'(?<![\w.])(?P<quantity>{_NUMBER})\s+(?P<unit>{"|".join(sorted(_UNITS, key=len, reverse=True))})\b', re.I)


def decimal_number(value, *, money=False):
    """Reject malformed grouping, nonfinite values, booleans and embedded prose."""
    if value is None or isinstance(value, bool):
        return None
    text = str(value).strip()
    if money:
        text = re.sub(r'^(?:PHP|Php|php|₱)\s*', '', text)
    if not re.fullmatch(r'[+-]?(?:\d+|\d{1,3}(?:,\d{3})+)(?:\.\d+)?', text):
        return None
    try:
        number = Decimal(text.replace(',', ''))
        return number if number.is_finite() else None
    except InvalidOperation:
        return None


def quantity_number(value):
    text = str(value or '').strip().lower()
    numeric = decimal_number(text)
    if numeric is not None:
        return numeric
    parenthesized = re.fullmatch(r'(.+?)\s*\(\s*([\d,.]+)\s*\)', text)
    if parenthesized:
        words = quantity_number(parenthesized[1])
        digits = decimal_number(parenthesized[2])
        return digits if words == digits else None
    words = text.replace('-', ' ').split()
    if not words or any(w not in {*_SMALL, *_TENS, 'hundred', 'thousand', 'and'} for w in words):
        return None
    def under_thousand(tokens):
        hundreds = 0
        if len(tokens) >= 2 and tokens[0] in _SMALL and 1 <= _SMALL[tokens[0]] <= 9 and tokens[1] == 'hundred':
            hundreds = _SMALL[tokens[0]] * 100
            tokens = tokens[2:]
            if tokens[:1] == ['and']:
                tokens = tokens[1:]
                if not tokens:
                    return None
        if not tokens:
            return hundreds
        if len(tokens) == 1 and tokens[0] in (_SMALL | _TENS):
            return hundreds + (_SMALL | _TENS)[tokens[0]]
        if len(tokens) == 2 and tokens[0] in _TENS and tokens[1] in _SMALL and 1 <= _SMALL[tokens[1]] <= 9:
            return hundreds + _TENS[tokens[0]] + _SMALL[tokens[1]]
        return None
    if 'thousand' in words:
        if words.count('thousand') != 1:
            return None
        index = words.index('thousand')
        high = under_thousand(words[:index])
        tail = words[index + 1:]
        if tail[:1] == ['and']:
            tail = tail[1:]
        low = under_thousand(tail)
        return Decimal(high * 1000 + low) if high and low is not None else None
    number = under_thousand(words)
    return Decimal(number) if number is not None else None


def number_text(value):
    return format(value, 'f').rstrip('0').rstrip('.') if '.' in format(value, 'f') else format(value, 'f')


def normalized_unit(value):
    text = re.sub(r'\s+', ' ', str(value or '')).strip().rstrip('.')
    return _UNITS.get(text.lower(), text)


def field_kind(label):
    """Typed label roles constrain semantic mapping and keep financial roles apart."""
    words = set(re.findall(r'[a-z]+', str(label).lower()))
    if not words:
        return ''
    if words & {'delivered', 'received', 'accepted', 'issued', 'expenditure', 'spent', 'paid', 'payment'}:
        return 'different_meaning'
    if words & {'budget', 'allocation', 'funding', 'abc'} and not words & {'source', 'funds'}:
        return 'budget'
    if 'delivery' in words:
        return 'delivery_date' if 'date' in words else 'delivery_period'
    if words & {'price', 'cost', 'rate'}:
        if 'unit' in words or words == {'rate'}:
            return 'unit_cost'
        if words & {'total', 'extended', 'line'}:
            return 'amount'
    if words & {'amount'}:
        return 'amount'
    if 'total' in words and words & {'line', 'extended'}:
        return 'amount'
    if words & {'uom'} or ('unit' in words and words & {'measure', 'measurement'}) or words == {'unit'}:
        return 'unit'
    if words & {'quantity', 'qty', 'volume'} or ('number' in words and 'units' in words):
        return 'quantity'
    if words & {'description', 'particulars'} or ('item' in words and words & {'name', 'specifications'}):
        return 'name'
    return ''


def interpret_item(source, general='', explicit=None):
    """Split a single source item, retaining ambiguity and package relationships."""
    original = str(source or '')
    text = re.sub(r'\s+', ' ', original).strip()
    fallback = re.sub(r'^(?:procurement|acquisition|purchase)\s+of\s+', '', str(general or '').strip(), flags=re.I)
    result = dict(name='', quantity='', unit='', unit_cost='', original_description=original,
                  warnings=[], evidence={}, package_specification='', calculated_total=None)
    matches = list(_QUANTITY_UNIT.finditer(text))
    # A size embedded inside an item's specification is not the ordered quantity.
    usable = [m for m in matches if not text[:m.start()].rstrip().endswith(('+', '-', '/', 'x', '×'))]
    match = usable[0] if usable else None
    if match:
        prefix, suffix = text[:match.start()].strip(' ,;—–-'), text[match.end():].strip(' ,;—–-')
        prefix = re.sub(r'^(?:requested|required|quantity|qty)\s*:\s*', '', prefix, flags=re.I).strip()
        if not prefix or not suffix or re.match(r'^(?:PHP|₱|[\d,]+\.\d{2})', suffix, re.I):
            number = quantity_number(match['quantity'])
            if number is not None and number > 0:
                result.update(quantity=number_text(number), unit=normalized_unit(match['unit']))
                result['evidence']['quantity_unit'] = match.group()
                result['name'] = prefix or re.sub(r'^of\s+', '', suffix, flags=re.I)
        else:
            result['warnings'].append('Quantity may be a specification. Confirm item identity, quantity and unit.')
        if len(usable) > 1:
            package = re.fullmatch(r'\s*[,;]?\s*(.+?)\s+per\s+(\w+)\s*', text[match.end():], re.I)
            if package and len(usable) == 2 and normalized_unit(package[2]) == normalized_unit(match['unit']):
                inner = usable[1]
                outer_qty, inner_qty = quantity_number(match['quantity']), quantity_number(inner['quantity'])
                # Keep the ordered package unit; calculated contents are review evidence only.
                name = text[match.end():inner.start()].strip(' ,;')
                result['name'] = re.sub(r'^of\s+', '', name, flags=re.I)
                result['package_specification'] = text[inner.start():]
                if outer_qty and inner_qty:
                    result['calculated_total'] = dict(quantity=number_text(outer_qty * inner_qty), unit=normalized_unit(inner['unit']),
                        basis='Explicit package relationship; calculated, requires human verification')
            else:
                result.update(quantity='', unit='')
                result['warnings'].append('Multiple quantities found. Confirm the ordered quantity and package specification.')
    if not result['name']:
        result['name'] = fallback or text
    price = re.search(r'(?:PHP|₱)\s*(\d[\d,]*(?:\.\d+)?)\s*(?:each|per\s+\w+)', text, re.I)
    if price:
        amount = decimal_number(price[1], money=True)
        if amount is not None and amount >= 0:
            result['unit_cost'] = number_text(amount)
            result['evidence']['unit_cost'] = price.group()
            result['name'] = result['name'].replace(price.group(), '').strip(' ,;—–-')
    for key, value in (explicit or {}).items():
        if key == 'Item Name':
            result['name'] = str(value or '').strip()
        elif key == 'Quantity':
            parsed = quantity_number(value)
            combined = _QUANTITY_UNIT.fullmatch(str(value or '').strip())
            if parsed is None and combined:
                parsed = quantity_number(combined['quantity'])
                if 'Unit' not in (explicit or {}):
                    result['unit'] = normalized_unit(combined['unit'])
                elif normalized_unit(explicit['Unit']) != normalized_unit(combined['unit']):
                    result['warnings'].append('Quantity cell unit differs from the explicit unit. Confirm both source cells.')
            result['quantity'] = number_text(parsed) if parsed is not None and parsed > 0 else ''
        elif key == 'Unit':
            result['unit'] = normalized_unit(value)
        elif key == 'Estimated Unit Cost':
            parsed = decimal_number(value, money=True)
            result['unit_cost'] = number_text(parsed) if parsed is not None and parsed >= 0 else str(value or '').strip()
        result['evidence'][key] = str(value or '')
    if not result['quantity'] or not result['unit']:
        result['warnings'].append('Ordered quantity or unit is uncertain. Correct the structured fields before approval.')
    result['warnings'] = list(dict.fromkeys(result['warnings']))
    return result


def enrich_ocr_record(record, cells=()):
    """Bind typed cells/labels to this row only, never a nearby item's values."""
    fields = record['fields']
    explicit, evidence, conflicts = {}, {}, set()
    names = dict(name='Item Name', quantity='Quantity', unit='Unit', unit_cost='Estimated Unit Cost')
    for label, value in cells:
        kind = field_kind(label)
        from procurement_document_layout import header_role
        role = header_role(label)
        if role in names:
            kind = role
        if kind in names:
            target = names[kind]
            if label == SPECIFICATION:
                continue
            # Narrative Quantity/Size is evidence for interpretation; it must
            # not conflict with a separate, explicit numeric Quantity cell.
            if kind == 'quantity' and re.search(r'\b(?:size|specifications?|specs)\b', label, re.I):
                continue
            if target == 'Item Name' and (label == GENERAL or re.search(r'\b(?:project|objective|general)\b', label, re.I)):
                source_meta = record.get('field_confidence', {})
                if not (source_meta.get('_table_source') or source_meta.get('_source_evidence', {}).get('item_number')):
                    continue
            if target in explicit and str(explicit[target]).strip() != str(value).strip():
                conflicts.add(target)
            else:
                explicit[target] = value
            evidence.setdefault(target, []).append(dict(label=label, value=value))
        elif kind in {'amount', 'budget', 'delivery_date', 'delivery_period', 'different_meaning'}:
            evidence.setdefault(kind, []).append(dict(label=label, value=value))
    # Official PPMP quantity/size and project objectives remain narrative evidence.
    for target in conflicts:
        explicit[target] = ''
    interpreted = interpret_item(fields.get(SPECIFICATION, ''), fields.get(GENERAL, ''), explicit)
    for target, key in zip(ITEM_REVIEW_FIELDS, ('name', 'quantity', 'unit', 'unit_cost')):
        fields[target] = interpreted[key]
        record['field_confidence'][target] = dict(score=0.75 if interpreted[key] else 0.0,
            level='Needs Review' if interpreted[key] else 'Unmapped', source_label='Source-bound item interpretation',
            source_evidence=evidence.get(target) or interpreted['evidence'])
    record['field_confidence']['_item_interpretation'] = dict(interpreted, source_cells=evidence)
    return record


def item_errors(item):
    """Validate structured planning items without treating budget as expenditure."""
    errors = []
    if not str(item.get('Item Name') or '').strip():
        errors.append('Item name is required.')
    quantity = decimal_number(item.get('Quantity'))
    if quantity is None or quantity <= 0:
        errors.append('Quantity must be a positive number; review the source and correct the structured quantity.')
    if not str(item.get('Unit') or '').strip():
        errors.append('Unit is required; confirm the source unit without converting it.')
    cost = item.get('Estimated Unit Cost')
    if cost not in ('', None):
        parsed = decimal_number(cost, money=True)
        if parsed is None or parsed < 0 or parsed.as_tuple().exponent < -2:
            errors.append('Unit cost must be a non-negative amount with at most two decimal places, or blank when absent.')
    return errors
