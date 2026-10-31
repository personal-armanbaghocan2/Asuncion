"""Inactive Phase 1 PR/PO numeric diagnostics, independent of the serving model.

Accept observable item dictionaries, {items, terms} documents, or the application's
explicit ITEM/DESCRIPTION/QUANTITY/UNIT text layout. Numeric clauses use a finite
grammar, separated by semicolons/newlines. Exact unique item identities bind;
similarity, layout numbers and proposed annotations never establish equivalence.

Outputs are evidence-bound findings and diagnostic count features, NOT the V2
559/659-feature contract, trained predictions, procurement policy or approval.
Unsupported prose, unresolved conversions and ambiguous scope remain UNKNOWN.
No I/O, model loading, fitting, database access or application imports occur.
"""
from collections import Counter, defaultdict
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, localcontext
import re

from procurement_shadow_v0_1_1.source_view import canonical_attribute, OPS

FEATURE_SCHEMA = 'PR_PO_BOUND_NUMERIC_DIAGNOSTICS_V0_1_0'
PENDING_POLICIES = ['P01', 'P02', 'P03', 'P04', 'P05', 'P06']
FEATURE_NAMES = ('requested_items', 'offered_items', 'requirements', 'satisfied',
                 'violated', 'unknown', 'unmatched_requested_items',
                 'unmatched_offered_items', 'unparsed_clauses',
                 'policy_dependent_requirements', 'non_evaluated')
NUMBER = r'[+-]?(?:(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?|\.\d+)'
OP = '|'.join(re.escape(s) for s in sorted(OPS, key=len, reverse=True))
UNIT = r'[A-Za-z]+'
ANNOTATIONS = {'proposed_status', 'proposed_primary_issue', 'reviewed_status',
               'reviewed_primary_issue', 'label_source', 'proposed_numeric_attribute_relationships',
               'proposed_item_correspondences', 'proposed_document_relationships'}


def parse_number(value):
    """Strict finite decimal parsing; commas must be proper thousands groups."""
    if isinstance(value, bool) or value is None:
        raise ValueError('INVALID_NUMBER')
    text = str(value).strip()
    if len(text) > 100 or not re.fullmatch(NUMBER, text):
        raise ValueError('INVALID_NUMBER')
    try:
        result = Decimal(text.replace(',', ''))
    except InvalidOperation as error:
        raise ValueError('INVALID_NUMBER') from error
    if not result.is_finite():
        raise ValueError('NONFINITE_NUMBER')
    return result


# Physical conversions are explicit. Count containers require the same unit;
# storage conversions intentionally have no scale pending P06.
_UNITS = {}
for dimension, canonical, scale, aliases in [
    ('mass', 'g', '1', ('g', 'gram', 'grams')),
    ('mass', 'kg', '1000', ('kg', 'kgs', 'kilogram', 'kilograms')),
    ('length', 'mm', '1', ('mm', 'millimeter', 'millimeters', 'millimetre', 'millimetres')),
    ('length', 'cm', '10', ('cm', 'centimeter', 'centimeters', 'centimetre', 'centimetres')),
    ('length', 'm', '1000', ('m', 'meter', 'meters', 'metre', 'metres')),
    ('length', 'inch', '25.4', ('inch', 'inches')),
    ('power', 'W', '1', ('w', 'watt', 'watts')),
    ('power', 'kW', '1000', ('kw', 'kilowatt', 'kilowatts')),
    ('duration', 'month', '1', ('month', 'months', 'mo', 'mos')),
    ('duration', 'year', '12', ('year', 'years', 'yr', 'yrs')),
    ('volume', 'ml', '1', ('ml', 'milliliter', 'milliliters', 'millilitre', 'millilitres')),
    ('volume', 'l', '1000', ('l', 'liter', 'liters', 'litre', 'litres')),
    ('temperature', 'C', '1', ('c', 'celsius')),
    ('count', 'piece', '1', ('piece', 'pieces', 'pc', 'pcs')),
    ('count', 'unit', '1', ('unit', 'units')),
    ('count', 'box', '1', ('box', 'boxes')),
    ('count', 'pack', '1', ('pack', 'packs')),
    ('count', 'set', '1', ('set', 'sets')),
    ('count', 'ream', '1', ('ream', 'reams')),
    ('count', 'bottle', '1', ('bottle', 'bottles', 'btl', 'btls')),
]:
    for alias in aliases:
        _UNITS[alias] = (dimension, canonical, Decimal(scale))
for canonical, names in [('MB', ('megabyte', 'megabytes')), ('GB', ('gigabyte', 'gigabytes')),
                         ('TB', ('terabyte', 'terabytes'))]:
    for name in names:
        _UNITS[name] = ('storage', canonical, None)


def _unit(raw):
    raw = str(raw or '').strip()
    if raw in ('MB', 'GB', 'TB'):
        return ('storage', raw, None)
    # Do not silently identify mixed/lower-case bit/byte abbreviations.
    if raw.casefold() in ('mb', 'gb', 'tb'):
        return None
    return _UNITS.get(raw.casefold())


@dataclass(frozen=True)
class Clause:
    attribute: str
    operator: str
    values: tuple
    unit: str
    evidence: dict
    problem: str = ''


def _attribute(text):
    text = text.strip().strip(':').strip()
    text = re.sub(r'^(?:minimum|maximum|at least|at most|exactly)\s+', '', text, flags=re.I)
    text = re.sub(r'\s+(?:minimum|maximum)$', '', text, flags=re.I)
    return canonical_attribute(text)


def _parse_clause(text, side, path):
    raw = text.strip().rstrip('.').strip()
    evidence = {'side': side, 'path': path, 'quote': text, 'raw_value': ''}
    if re.search(r'\b(?:model|serial|reference|asset|price|amount|item id)\b', raw, re.I):
        return None
    if re.search(r'\b(?:except|unless|alternative|supersedes|excluded|excluding|not required)\b', raw, re.I):
        return None
    match = re.fullmatch(rf'(?P<a>[A-Za-z][A-Za-z /_-]*?)\s+(?:between|from)\s+(?P<lo>{NUMBER})\s+(?:and|to)\s+(?P<hi>{NUMBER})\s*(?P<u>{UNIT})(?:\s+inclusive)?', raw, re.I)
    if match:
        evidence['raw_value'] = match['lo'] + ' / ' + match['hi']
        values = (parse_number(match['lo']), parse_number(match['hi']))
        return Clause(_attribute(match['a']), 'INTERVAL', values, match['u'], evidence,
                      'CONTRADICTORY_REQUIREMENTS' if values[0] > values[1] else '')
    # Both "minimum memory 8 GB" and "memory minimum 8 GB" are explicit.
    match = re.fullmatch(rf'(?P<op>{OP})\s+(?P<a>[A-Za-z][A-Za-z /_-]*?)\s+(?P<n>{NUMBER})\s*(?P<u>{UNIT})', raw, re.I)
    if not match:
        match = re.fullmatch(rf'(?P<a>[A-Za-z][A-Za-z /_-]*?)\s*:?\s+(?:(?P<op>{OP})\s*)?(?P<n>{NUMBER})\s*(?P<u>{UNIT})', raw, re.I)
    if not match:
        # Compact symbolic operators need no spaces around the operator.
        match = re.fullmatch(rf'(?P<a>[A-Za-z][A-Za-z /_-]*?)\s*(?P<op>>=|<=|>|<|=)\s*(?P<n>{NUMBER})\s*(?P<u>{UNIT})', raw, re.I)
    if not match:
        return None
    operator = OPS[(match['op']).lower()] if match['op'] else ('UNSPECIFIED' if side == 'pr' else 'EQ')
    evidence['raw_value'] = match['n']
    return Clause(_attribute(match['a']), operator, (parse_number(match['n']),), match['u'], evidence)


def _first(row, keys):
    for key in keys:
        if row.get(key) not in (None, ''):
            return row[key]
    return ''


def _document(value, side):
    terms = []
    if isinstance(value, dict):
        if ANNOTATIONS.intersection(value):
            raise ValueError('ANNOTATIONS_ARE_NOT_PREDICTORS')
        supplied_terms = value.get('terms', [])
        if not isinstance(supplied_terms, list):
            raise ValueError('UNSUPPORTED_TERMS')
        terms = list(supplied_terms)
        value = value.get('items', value.get('text', []))
    if isinstance(value, str):
        rows = []
        for line in value.splitlines():
            if line.strip() in ('PURCHASE REQUEST', 'PURCHASE ORDER'):
                continue
            match = re.fullmatch(r'ITEM(?:\s+\d+)?\. DESCRIPTION AND SPECIFICATION: (.*?)\. QUANTITY: (.*?)\. UNIT: (.*?)\.', line.strip())
            if match:
                desc, qty, unit = match.groups()
                rows.append({'description': desc, 'quantity': qty, 'unit': unit})
            elif line.strip():
                terms.append(line)
        value = rows
    if not isinstance(value, list) or not isinstance(terms, list) or len(value) > 200 or len(terms) > 100:
        raise ValueError('UNSUPPORTED_OR_OVERSIZED_DOCUMENT')
    items = []
    for index, row in enumerate(value):
        if not isinstance(row, dict) or ANNOTATIONS.intersection(row):
            raise ValueError('UNSUPPORTED_ITEM_OR_ANNOTATIONS')
        desc = str(_first(row, ('description', 'Description / Specification', 'Description', 'Item Description')))
        name = str(_first(row, ('item', 'Item Name', 'Item', 'name')))
        parts = [s.strip() for s in re.split(r';|\r?\n', desc) if s.strip()]
        if not name and parts:
            # An identity must be explicit and distinct from a numeric clause.
            if not re.search(r'\d|\b(?:minimum|maximum|warranty|power|memory)\b', parts[0], re.I):
                name = parts.pop(0)
        if parts and name and parts[0].casefold() == name.casefold():
            parts.pop(0)
        if len(desc) > 8000 or len(name) > 500 or len(parts) > 100:
            raise ValueError('OVERSIZED_ITEM')
        identity = re.sub(r'\s+', ' ', name).strip().casefold()
        qty = _first(row, ('quantity', 'qty', 'Quantity', 'QTY', 'Requested Quantity', 'Awarded Quantity'))
        unit = str(_first(row, ('unit', 'Unit', 'Unit of Measure', 'uom')))
        ev = {'side': side, 'path': f'items[{index}].quantity', 'quote': str(qty), 'raw_value': str(qty)}
        problem = ''
        try:
            number = parse_number(qty)
            if number <= 0:
                problem = 'SUSPICIOUS_QUANTITY'
            values = (number,)
        except ValueError:
            values = ()
            problem = 'MISSING_QUANTITY' if str(qty).strip() in ('', 'not provided') else 'INVALID_QUANTITY'
        clauses = [Clause('quantity', 'EQ', values, unit, ev, problem)]
        unknown = []
        for n, part in enumerate(parts):
            clause = _parse_clause(part, side, f'items[{index}].description.clause[{n}]')
            if clause:
                clauses.append(clause)
            else:
                unknown.append({'side': side, 'path': f'items[{index}].description.clause[{n}]', 'quote': part})
        items.append({'identity': identity, 'source_index': index, 'clauses': clauses, 'unknown': unknown})
    for n, term in enumerate(terms):
        if not isinstance(term, str):
            raise ValueError('UNSUPPORTED_TERM')
        match = re.fullmatch(r'For item "([^"]+)",\s*(.+)', term, re.I)
        targets = [i for i in items if i['identity'] == match[1].casefold()] if match else (items if len(items) == 1 else [])
        clause = _parse_clause(match[2] if match else term, side, f'terms[{n}]')
        if clause and len(targets) == 1:
            targets[0]['clauses'].append(clause)
        else:
            # Unscoped obligations must not disappear or attach to a convenient item.
            for item in items:
                item['unknown'].append({'side': side, 'path': f'terms[{n}]', 'quote': term})
    return items, terms


def _compatible(required, offered):
    left, right = _unit(required.unit), _unit(offered.unit)
    if not left or not right or left[0] != right[0]:
        return None, None, 'INCOMPATIBLE_UNITS'
    if left[0] == 'storage' and left[1] != right[1]:
        return None, None, 'POLICY_P06_PENDING'
    if left[0] == 'count' and left[1] != right[1]:
        return None, None, 'EXPLICIT_PACK_FACTOR_REQUIRED'
    a = left[2] if left[2] is not None else Decimal(1)
    b = right[2] if right[2] is not None else Decimal(1)
    return _scaled(required.values, a), _scaled(offered.values, b), ''


def _scaled(values, scale):
    # Inputs are bounded to 100 characters; avoid ambient Decimal precision
    # rounding otherwise distinct explicit values during unit conversion.
    with localcontext() as context:
        context.prec = 256
        return tuple(v * scale for v in values)


def _contradictory(clauses):
    """Find impossible explicit bounds of one attribute in one canonical unit."""
    valid = [c for c in clauses if c.values and not c.problem and _unit(c.unit)]
    groups = defaultdict(list)
    for c in valid:
        u = _unit(c.unit)
        groups[(u[0], u[1] if u[0] in ('storage', 'count') else '')].append(c)
    for group in groups.values():
        low = high = None
        low_closed = high_closed = True
        for c in group:
            scale = _unit(c.unit)[2] or Decimal(1)
            values = _scaled(c.values, scale)
            if c.operator in ('GE', 'GT', 'EQ', 'INTERVAL'):
                v, closed = values[0], c.operator != 'GT'
                if low is None or v > low: low, low_closed = v, closed
                elif v == low: low_closed = low_closed and closed
            if c.operator in ('LE', 'LT', 'EQ', 'INTERVAL'):
                v, closed = values[-1], c.operator != 'LT'
                if high is None or v < high: high, high_closed = v, closed
                elif v == high: high_closed = high_closed and closed
        if low is not None and high is not None and (low > high or low == high and not (low_closed and high_closed)):
            return True
    return False


def _compare(identity, required, offered, requested_clauses, qualifications):
    row = {'item_identity': identity, 'attribute': required.attribute, 'operator': required.operator,
           'required_values': [str(v) for v in required.values], 'required_unit': required.unit,
           'required_evidence': dict(required.evidence), 'offered_values': [], 'offered_unit': '',
           'offered_evidence': [dict(c.evidence) for c in offered], 'state': 'UNKNOWN',
           'reason': '', 'conversion_used': False, 'pending_policies': []}
    reason = required.problem
    if not reason and _contradictory(requested_clauses): reason = 'CONTRADICTORY_REQUIREMENTS'
    if not reason and not offered: reason = 'MISSING_ATTRIBUTE'
    if not reason and any(c.problem for c in offered): reason = next(c.problem for c in offered if c.problem)
    if not reason and required.operator == 'UNSPECIFIED': reason = 'REQUIREMENT_OPERATOR_UNSPECIFIED'
    if not reason and any(c.operator != 'EQ' for c in offered): reason = 'NONCOMMITTED_OFFER'
    if not reason and len({(c.values, c.unit) for c in offered}) != 1: reason = 'CONTRADICTORY_OFFERS'
    if not reason and qualifications: reason = 'UNPARSED_QUALIFICATION_OR_SCOPE'
    if not reason:
        offer = offered[0]
        row.update(offered_values=[str(v) for v in offer.values], offered_unit=offer.unit)
        left, right, reason = _compatible(required, offer)
        if not reason:
            value = right[0]
            satisfied = {'EQ': lambda: value == left[0], 'GE': lambda: value >= left[0],
                         'GT': lambda: value > left[0], 'LE': lambda: value <= left[0],
                         'LT': lambda: value < left[0], 'INTERVAL': lambda: left[0] <= value <= left[-1]}[required.operator]()
            row.update(state='SATISFIED' if satisfied else 'VIOLATED', reason='EXPLICIT_BOUND_COMPARISON',
                       conversion_used=_unit(required.unit)[1] != _unit(offer.unit)[1])
    if reason:
        row['reason'] = reason
        if reason == 'POLICY_P06_PENDING': row['pending_policies'] = ['P06']
    return row


def extract_pair_features(pr, po):
    """Return non-authoritative, versioned findings and count features only."""
    requested, pr_terms = _document(pr, 'pr')
    offered, po_terms = _document(po, 'po')
    left = Counter(i['identity'] for i in requested)
    findings, used = [], set()
    unmatched_pr = unmatched_po = 0
    for item in requested:
        identity = item['identity']
        matches = [i for i in offered if identity and i['identity'] == identity]
        bound = left[identity] == len(matches) == 1 and bool(identity)
        if not bound:
            unmatched_pr += 1
            findings.append({'item_identity': identity, 'attribute': 'item_identity', 'operator': 'PRESENT',
                             'state': 'UNKNOWN', 'reason': 'NO_UNIQUE_CORRESPONDING_ITEM', 'pending_policies': []})
        else:
            used.add(matches[0]['source_index'])
        for clause in item['clauses']:
            candidates = [c for match in matches for c in match['clauses'] if c.attribute == clause.attribute] if bound else []
            same_attr = [c for c in item['clauses'] if c.attribute == clause.attribute]
            qualifications = item['unknown'] + [q for match in matches for q in match['unknown']]
            findings.append(_compare(identity, clause, candidates, same_attr, qualifications))
    for item in offered:
        if item['source_index'] not in used:
            unmatched_po += 1
            findings.append({'item_identity': item['identity'], 'attribute': 'extra_item', 'operator': 'PRESENT',
                             'state': 'UNKNOWN', 'reason': 'UNMATCHED_OFFER_NOT_ASSUMED_SUBSTITUTION', 'pending_policies': []})
    unparsed = [q for item in requested + offered for q in item['unknown']]
    features = dict(zip(FEATURE_NAMES, [len(requested), len(offered), len(findings),
                    sum(f['state'] == 'SATISFIED' for f in findings), sum(f['state'] == 'VIOLATED' for f in findings),
                    sum(f['state'] == 'UNKNOWN' for f in findings), unmatched_pr, unmatched_po, len(unparsed),
                    sum(bool(f['pending_policies']) for f in findings), int(not requested or not offered)]))
    return {'schema': FEATURE_SCHEMA, 'active_model_compatible': False, 'authoritative': False,
            'processing_state': 'EVALUATED' if requested and offered else 'NON_EVALUATED',
            'status': None, 'primary_issue': None, 'pending_policies': list(PENDING_POLICIES),
            'completeness_verified': bool(requested and offered) and not unparsed and not features['unknown'],
            'findings': findings, 'unparsed_evidence': unparsed, 'feature_names': list(FEATURE_NAMES),
            'feature_values': [features[name] for name in FEATURE_NAMES], 'features': features}
