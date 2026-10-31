"""Source-bound, deterministic item comparisons; never a procurement decision.

compare_documents(pr, po) accepts source item lists, {items, terms}, or the exact
app serializer layout. No I/O, model loading, learning, database or Flask imports.
Names and literal model references establish correspondence only, not approval.
Source labels, proposed matches and source line IDs never establish equivalence.
"""
from collections import Counter, defaultdict
from decimal import Decimal
import re
from types import SimpleNamespace

from scripts.reviews import pr_po_review_checks_v0_1_0 as checks
from procurement_shadow_v0_1_1.source_view import _could_affect, term_expression

VERSION = '0.1.0'
SCHEMA = 'PR_PO_ITEM_COMPARISON_V0_1_0'
FIELDS = {
    'item': ('item', 'Item Name', 'Item', 'name'),
    'description': ('description', 'Description / Specification', 'Item Description', 'Description'),
    'quantity': ('quantity', 'Quantity', 'qty', 'QTY', 'Requested Quantity', 'Awarded Quantity'),
    'unit': ('unit', 'Unit', 'Unit of Measure', 'unit_of_measure', 'uom'),
    'line_id': ('line_id',),
}
# Explicit source labels only. These fields supplement descriptions; they never
# establish item identity or break an ambiguous allocation. Prefixes provide the
# field's stated context to the existing grammar, without adding requirements.
SPECIFICATION_FIELDS = {
    'processor': 'processor', 'cpu': 'cpu', 'ram': 'RAM', 'memory': 'memory',
    'ssd': 'SSD', 'hdd': 'HDD', 'storage': 'storage',
    'specification': '', 'specifications': '', 'remarks': '',
    'quantity and size of the project to be procured': '',
}
PLACEHOLDERS = {'', 'not provided', 'description not provided', 'no item information was provided'}
MODEL = re.compile(r'product model\s*(?:required\s*:|:|\s)\s*([A-Za-z0-9][A-Za-z0-9._/-]{0,80})', re.I)
QUALIFIER = re.compile(r'\b(?:unless|except|alternative|supersedes|revision|excluded|waived|instead|if)\b|not (?:required|included|covered|available)', re.I)
MATERIAL_WORDS = re.compile(r'\b(?:stainless steel|polypropylene|polycarbonate|aluminium|aluminum|polyester|cotton|nylon|rubber|copper|steel|plastic|wood|PVC)\b', re.I)
CABLE_LENGTH_VALUE = (r'(?<![\w.])(?P<value>[0-9]+(?:\.[0-9]+)?)(?:\s*-\s*|\s+)'
                      r'(?P<unit>meters?|metres?|cm)\b')
CABLE_LENGTH = re.compile(
    r'(?:(?P<operator>minimum|maximum|at least|at most|exactly)\s+)?'
    + CABLE_LENGTH_VALUE + r'\s+(?:HDMI\s+)?cables?', re.I)
CABLE_LENGTH_TRAILING = re.compile(
    r'(?:High[- ]Speed\s+)?HDMI\s+cables?\s*,\s*'
    r'(?:(?P<operator>minimum|maximum|at least|at most|exactly)\s+)?'
    + CABLE_LENGTH_VALUE + r'(?:\s*,\s*(?P<tail>.+))?', re.I)


class InputError(ValueError):
    """A bounded schema error; messages never contain the source payload."""


def _scalar(value):
    if value is None: return ''
    if type(value) in (str, int) or isinstance(value, Decimal): return str(value)
    raise InputError('SOURCE_FIELDS_REQUIRE_TEXT_OR_EXACT_NUMBERS')


def _application_identity_alias(raw, identity):
    """Recognize the name alias written by app.normalize_excel_row, not a label alone.

    Item Description also occurs as independent descriptive/specification text.
    Only the supported version and its consistent saved interpretation establish
    the application-generated role. Neither source IDs nor review/status labels
    establish this role, item equivalence, or an approved procurement decision.
    """
    interpretation = raw.get('_Item Interpretation')
    if (type(raw.get('_Item Contract Version')) is not int or raw['_Item Contract Version'] != 1
            or type(interpretation) is not dict or type(interpretation.get('name')) is not str
            or raw.get('Item Name') in (None, '')):
        return None
    name = _scalar(raw['Item Name'])
    if (not identity or '|' in name or checks.norm(name) in PLACEHOLDERS
            or checks.norm(name) != checks.norm(identity) or interpretation['name'] != name):
        return None
    return name


def _cable_length_match(text):
    return CABLE_LENGTH.fullmatch(text) or CABLE_LENGTH_TRAILING.fullmatch(text)


def _fact(quote, side, ref):
    body = checks._body(quote)
    if QUALIFIER.search(body): return None
    cable = _cable_length_match(body)
    if cable and checks.parse_number(cable['value']) > 0:
        # A bare requested size retains UNSPECIFIED; only stated operators bind
        # the requirement. Existing helpers own unit conversion and comparison.
        parsed = checks.parse_fact(
            f'length {cable["operator"] or ""} {cable["value"]} {cable["unit"]}', side, ref)
        if parsed:
            parsed['quote'] = quote
            parsed['_cable_length_context'] = True
            if cable.groupdict().get('tail'):
                # The length is parsed; additional connector/specification prose
                # remains unresolved evidence rather than silently disappearing.
                parsed['_unparsed_tail'] = quote.split(',', 2)[2].strip()
        return parsed
    # Attribute-last measurement wording is common in actual item schedules.
    # Keep the original quote; never reorder product/model identifiers.
    reverse = re.fullmatch(r'(?:(minimum|maximum|at least|at most|exactly)\s+)?([0-9]+(?:\.[0-9]+)?)\s+(GB|MB|TB)\s+(RAM|system memory|memory|SSD|HDD)', body, re.I)
    if reverse:
        attribute = 'memory' if reverse[4].casefold() in ('ram', 'system memory', 'memory') else reverse[4].upper()
        parsed = _fact(f'{attribute} {reverse[1] or ""} {reverse[2]} {reverse[3]}'.replace('  ', ' '), side, ref)
        if parsed: parsed['quote'] = quote
        return parsed
    literal = re.fullmatch(r'material\s*:?\s+([A-Za-z][A-Za-z -]*)', body, re.I)
    if literal:
        if checks.MISSING.fullmatch(literal[1]): return None
        return checks.fact('material', 'LITERAL', [literal[1]], '', side, ref, quote, 'literal')
    model = MODEL.fullmatch(body)
    if model:
        return checks.fact('product model reference', 'MENTION', [model[1]], '', side, ref, quote, 'mention')
    # Keep SSD and HDD capacities distinct. A generic storage total or another
    # drive's capacity is not proof of the requested SSD capacity.
    clause = checks._parse_clause(checks._number_words(body.replace(':', ' ')), side, ref)
    if clause and clause.attribute in ('ssd', 'ssd capacity', 'hdd', 'hdd capacity'):
        medium = clause.attribute.split()[0]
        return checks.fact(medium+' capacity', clause.operator, clause.values, clause.unit, side, ref, quote)
    return checks.parse_fact(quote, side, ref)


def _read_document(value, side):
    unknown = []
    if isinstance(value, dict):
        if 'items' in value and 'text' in value: raise InputError('CHOOSE_STRUCTURED_ITEMS_OR_TEXT')
        terms = value.get('terms', [])
        value = value.get('items', value.get('text', []))
    else: terms = []
    if type(terms) is not list or len(terms) > 100 or any(type(t) is not str or len(t) > 8000 for t in terms):
        raise InputError('INVALID_OR_OVERSIZED_TERMS')
    if type(value) is str:
        if len(value) > 1700000: raise InputError('OVERSIZED_TEXT')
        rows = []
        for n, line in enumerate(value.splitlines(), 1):
            line = line.strip()
            if line in ('PURCHASE REQUEST', 'PURCHASE ORDER') or not line: continue
            if line == 'NO ITEM INFORMATION WAS PROVIDED.':
                unknown.append(dict(ref=f'{side.upper()} text line {n}', quote=line)); continue
            if line.startswith('TERM | '):
                terms = terms + [line[7:]]; continue
            match = re.fullmatch(r'ITEM(?:\s+\d+)?\. DESCRIPTION AND SPECIFICATION: (.*?)\. QUANTITY: (.*?)\. UNIT: (.*?)\.', line)
            if match and line.count('. QUANTITY: ') == line.count('. UNIT: ') == 1:
                rows.append(dict(description=match[1], quantity=match[2], unit=match[3]))
            else:
                unknown.append(dict(ref=f'{side.upper()} text line {n}', quote=line))
        value = rows
    if type(value) is not list or len(value) > 200 or len(terms) > 100: raise InputError('INVALID_OR_OVERSIZED_ITEMS')
    rows = []
    for position, raw in enumerate(value, 1):
        if type(raw) is not dict: raise InputError('ITEM_MUST_BE_A_SOURCE_OBJECT')
        selected, evidence_fields, problems = {}, {}, []
        for key, aliases in FIELDS.items():
            present = {a: _scalar(raw[a]) for a in aliases if a in raw and raw[a] not in (None, '')}
            if any(len(v) > (8000 if key == 'description' else 500) for v in present.values()):
                raise InputError('OVERSIZED_SOURCE_FIELD')
            evidence_fields.update(present)
            if key == 'description' and 'Item Description' in present:
                alias = _application_identity_alias(raw, selected['item'])
                if alias is not None:
                    value = present['Item Description']
                    if value != alias:
                        # This contract declares an identity alias, so a changed
                        # name is a real conflict rather than a new specification.
                        problems.append(dict(ref=f'{side.upper()} item {position}.item',
                                             quote=f'Item Name: {alias}; Item Description: {value}'))
                        selected['item'] = ''
                        present.pop('Item Description')
                    elif len(present) > 1:
                        # Keep every original source value above. Exclude only
                        # the proven redundant name from specification equality.
                        # A sole literal description retains its existing view.
                        present.pop('Item Description')
            distinct = {checks.norm(v) for v in present.values()}
            if len(distinct) > 1:
                problem = dict(ref=f'{side.upper()} item {position}.{key}',
                               quote='; '.join(a+': '+v for a, v in present.items()))
                if key == 'description': problem['specification_alias_conflict'] = True
                problems.append(problem)
                selected[key] = ''
            else: selected[key] = next(iter(present.values()), '')
        description = selected['description']
        parts = [p.strip() for p in re.split(r';|\r?\n', description) if p.strip()]
        explicit_name = selected['item'].strip()
        name = explicit_name
        if not name and parts:
            head = parts[0]
            cable = _cable_length_match(checks._body(head))
            if cable and not cable['operator']:
                # Preserve an existing literal description-only identity while
                # also retaining its length wording for advisory interpretation.
                name = head
            elif not _fact(head, side, '') and not re.search(r'\b(?:minimum|maximum|at least|at most)\b|\d\s*(?:GB|MB|W)\b', head, re.I):
                name = parts.pop(0)
        elif parts and checks.norm(parts[0]) == checks.norm(name):
            if not _cable_length_match(checks._body(parts[0])): parts.pop(0)
        # A joined application description can contain several source fields.
        # Do not guess which pipe fragment is its identity or requirement.
        if '|' in name: name = ''
        if checks.norm(name) in PLACEHOLDERS: name = ''
        # Independent specification conflicts remain UNKNOWN evidence; they do
        # not erase a separately supported explicit source item name.
        if any(p['ref'].endswith('.item') for p in problems): name = ''
        row = dict(position=position, identity=checks.norm(name), name=name,
                   source=selected | {'source_fields': evidence_fields}, raw=selected,
                   quantity_ref=f'{side.upper()} item {position}.quantity', facts=[], unknown=list(problems))
        if len(parts) > 100: raise InputError('TOO_MANY_SOURCE_CLAUSES')
        for n, quote in enumerate(parts, 1):
            ref = f'{side.upper()} item {position}.description clause {n}'
            parsed = _fact(quote, side, ref)
            if parsed: row['facts'].append(parsed)
            elif re.fullmatch(r'(?:loose pieces or equivalent packs permitted|equivalent packs accepted|each (?:pack|box) contains .+)', quote, re.I):
                continue  # Original quote remains in raw source; quantity checks verify it.
            else: row['unknown'].append(dict(ref=ref, quote=quote))
        clause_count = len(parts)
        for key, value in raw.items():
            label = str(key).casefold()
            if label not in SPECIFICATION_FIELDS or value in (None, ''):
                continue
            text = _scalar(value)
            if len(text) > 8000:
                raise InputError('OVERSIZED_SOURCE_FIELD')
            evidence_fields[key] = text
            clauses = [p.strip() for p in re.split(r';|\r?\n', text) if p.strip()]
            clause_count += len(clauses)
            if clause_count > 100:
                raise InputError('TOO_MANY_SOURCE_CLAUSES')
            for n, quote in enumerate(clauses, 1):
                ref = f'{side.upper()} item {position}.{key} clause {n}'
                prefix = SPECIFICATION_FIELDS[label]
                # A labeled numeric field may omit its own label in the value.
                # Parse it with that context, then retain the original quote/ref.
                expression = f'{prefix} {quote}' if prefix and not re.match(r'(?i)^(?:'+re.escape(prefix)+r')\b', quote) else quote
                parsed = _fact(expression, side, ref)
                if parsed:
                    parsed['quote'] = quote
                    row['facts'].append(parsed)
                else:
                    row['unknown'].append(dict(ref=ref, quote=quote, field=key))
        for fact in row['facts']:
            if fact.pop('_cable_length_context', False): row['cable_length_context'] = True
            tail = fact.pop('_unparsed_tail', None)
            if tail: row['unknown'].append(dict(ref=fact['ref'], quote=tail))
        rows.append(row)
    for n, quote in enumerate(terms, 1):
        ref = f'{side.upper()} document term {n}'
        locator, expression = term_expression(quote.strip().rstrip('.'))
        if locator and locator[0] == 'item':
            targets = [r for r in rows if r['identity'] == checks.norm(locator[1])]
        elif locator and locator[0] == 'line':
            targets = [r for r in rows if r['source']['line_id'] == locator[1]]
        else:
            match = re.fullmatch(r'For\s+(.+?),\s*(.+)', quote.strip(), re.I)
            targets = [r for r in rows if match and r['identity'] == checks.norm(match[1])]
            expression = match[2] if match else quote
        if len(targets) != 1:
            unknown.append(dict(ref=ref, quote=quote)); continue
        for clause_number, part in enumerate(expression.split(';'), 1):
            parsed = _fact(part, side, f'{ref}, clause {clause_number}')
            if parsed:
                parsed['quote'] = quote
                if parsed.pop('_cable_length_context', False): targets[0]['cable_length_context'] = True
                tail = parsed.pop('_unparsed_tail', None)
                targets[0]['facts'].append(parsed)
                if tail: targets[0]['unknown'].append(dict(ref=parsed['ref'], quote=tail))
            else: targets[0]['unknown'].append(dict(ref=ref, quote=quote))
    return rows, unknown


def _model_reference(row):
    # A reference from one specification alias cannot resolve another alias's
    # conflict or allocate duplicate products to a convenient model identifier.
    if any(ev.get('specification_alias_conflict') or QUALIFIER.search(ev['quote']) for ev in row['unknown']): return ''
    # Literal identifiers have no approved case/punctuation equivalence rule.
    refs = {f['values'][0].strip() for f in row['facts'] if f['attribute'] == 'product model reference' and len(f['values']) == 1}
    return next(iter(refs)) if len(refs) == 1 else ''


def _bindings(pr, po):
    left, right = defaultdict(list), defaultdict(list)
    for row in pr:
        if row['identity']: left[row['identity']].append(row)
    for row in po:
        if row['identity']: right[row['identity']].append(row)
    result = []
    for name, requests in left.items():
        offers = right[name]
        if len(requests) == len(offers) == 1:
            pairs = [(requests[0], offers[0], 'UNIQUE_EXACT_NAME')]
        else:
            pairs = []
            for request in requests:
                reference = _model_reference(request)
                candidates = [o for o in offers if reference and _model_reference(o) == reference]
                rivals = [r for r in requests if reference and _model_reference(r) == reference]
                if len(candidates) == len(rivals) == 1:
                    pairs.append((request, candidates[0], 'EXACT_NAME_AND_UNIQUE_MODEL_REFERENCE'))
        result += [dict(requested_position=r['position'], offered_position=o['position'], method=m) for r, o, m in pairs]
    # Word order/plurals are lexical correspondence, not specification equality.
    used_pr = {b['requested_position'] for b in result}; used_po = {b['offered_position'] for b in result}
    for request in pr:
        if request['position'] in used_pr or not _name_key(request['name']): continue
        if right.get(request['identity']): continue  # Never resolve a duplicate/model-reference tie by elimination.
        offers = [o for o in po if o['position'] not in used_po and _name_key(o['name']) == _name_key(request['name'])]
        rivals = [r for r in pr if r['position'] not in used_pr and _name_key(r['name']) == _name_key(request['name'])]
        if len(offers) == len(rivals) == 1:
            result.append(dict(requested_position=request['position'], offered_position=offers[0]['position'], method='UNIQUE_REORDERED_NAME'))
            used_pr.add(request['position']); used_po.add(offers[0]['position'])
    # Unnamed attribute scope is not a fallback for rejected/conflicting names.
    if (len(pr)==len(po)==1 and not pr[0]['identity'] and not po[0]['identity']
            and not any(ev['ref'].endswith(('.item', '.description'))
                        for row in (pr[0], po[0]) for ev in row['unknown'])):
        left_attributes = {f['attribute'] for f in pr[0]['facts']}
        right_attributes = {f['attribute'] for f in po[0]['facts']}
        if len(left_attributes)==1 and left_attributes==right_attributes:
            result.append(dict(requested_position=1,offered_position=1,method='UNIQUE_ATTRIBUTE_SCOPE'))
    return result


def _name_key(name):
    if not name or re.search(r'\b(?:not|no|without|for|with|and|or|model)\b', name, re.I): return ()
    tokens = re.findall(r'[\w]+', checks.norm(name))
    return tuple(sorted(token[:-1] if token.isalpha() and len(token)>3 and token.endswith('s') and not token.endswith(('ss','us','is')) else token for token in tokens))


def _material_candidate(request, offer):
    left, right = MATERIAL_WORDS.findall(request['name']), MATERIAL_WORDS.findall(offer['name'])
    return (len(left) == len(right) == 1 and checks.norm(left[0]) != checks.norm(right[0])
            and _name_key(MATERIAL_WORDS.sub('', request['name']))
            and _name_key(MATERIAL_WORDS.sub('', request['name'])) == _name_key(MATERIAL_WORDS.sub('', offer['name'])))


def _hdmi_cable_name(row):
    """Finite lexical candidate cue, never proof of identity or line allocation."""
    name = row['name']
    return bool(re.match(r'(?:Procurement\s+of\s+)?(?:High[- ]Speed\s+)?HDMI\s+cables?'
                         r'(?=$|[,;]|\s+for\b)', name, re.I)
                and not re.search(r'\b(?:no|not|without|or|USB|VGA|DVI|DisplayPort|'
                                  r'adapters?|converters?|splitters?|extenders?|testers?)\b', name, re.I)
                and not QUALIFIER.search(name))


def _tokens(name):
    return set(re.findall(r'[\w]+', name))


def _candidates(request, offered, used):
    result = []
    for offer in offered:
        overlap = sorted(_tokens(request['identity']) & _tokens(offer['identity']))
        exact = bool(request['identity']) and request['identity'] == offer['identity']
        if exact or overlap:
            result.append(dict(offered_position=offer['position'], offered_name=offer['name'],
                               evidence='EXACT_NAME_WITH_UNRESOLVED_ALLOCATION' if exact else 'SHARED_WORDS_ONLY',
                               shared_words=overlap, already_bound=offer['position'] in used,
                               requested_words_only=sorted(_tokens(request['identity'])-_tokens(offer['identity'])),
                               offered_words_only=sorted(_tokens(offer['identity'])-_tokens(request['identity'])),
                               establishes_equivalence=False))
    return result


def _numeric_conflict(facts):
    """Intersect explicit bounds only within compatible unit/family groups.

    Reuse the assistant's exact conversions, including its P06 refusal. This
    also handles ordinal/port units absent from the physical-unit registry.
    """
    groups = []
    for value in facts:
        if value['kind'] != 'number': continue
        for group in groups:
            if group[0].get('family') == value.get('family') and not checks.comparable(group[0], value)[2]:
                group.append(value); break
        else: groups.append([value])
    for group in groups:
        low = high = None
        low_closed = high_closed = True
        for value in group:
            _, numbers, reason = checks.comparable(group[0], value)
            if reason: continue
            operator = value['operator']
            if operator in ('GE', 'GT', 'EQ', 'INTERVAL'):
                bound, closed = numbers[0], operator != 'GT'
                if low is None or bound > low: low, low_closed = bound, closed
                elif bound == low: low_closed = low_closed and closed
            if operator in ('LE', 'LT', 'EQ', 'INTERVAL'):
                bound, closed = numbers[-1], operator != 'LT'
                if high is None or bound < high: high, high_closed = bound, closed
                elif bound == high: high_closed = high_closed and closed
        if low is not None and high is not None and (low > high or low == high and not (low_closed and high_closed)):
            return True
    return False


def _row_checks(request, offer):
    result = [checks.compare_quantities([request], [offer])]
    cable_context = bool(re.search(r'\bcables?\b', request['name']+' '+offer['name'], re.I)) or any(
        row.get('cable_length_context', False) for row in (request, offer))
    ssd = [f for f in request['facts'] if f['attribute'] == 'ssd capacity']
    hdd = [f for f in offer['facts'] if f['attribute'] == 'hdd capacity']
    if ssd and hdd and not any(f['attribute'] == 'ssd capacity' for f in offer['facts']):
        result.append(checks.finding('storage_technology', 'WORDING_DIFFERENCE', 'storage technology', ssd, hdd,
                      'The PR explicitly names SSD capacity; the PO states HDD capacity. HDD capacity is not evidence of SSD capacity. The PO may still have unreported components.',
                      'Specification mismatch', 'Is an SSD also committed, and what is its stated capacity? Do not assume HDD is an authorized substitute.'))
    for required in request['facts']:
        available = [f for f in offer['facts'] if f['attribute'] == required['attribute']]
        related = [f for f in request['facts'] if f['attribute'] == required['attribute']]
        literal_conflict = required['kind'] == 'literal' and len({checks.norm(v) for f in related for v in f['values']}) > 1
        numeric_conflict = required['kind'] == 'number' and _numeric_conflict(related)
        relevant_unknown = []
        for row in (request, offer):
            for ev in row['unknown']:
                field = str(ev.get('field', '')).casefold()
                field_attribute = {'ram': 'memory', 'memory': 'memory', 'ssd': 'ssd capacity',
                                   'hdd': 'hdd capacity', 'processor': 'processor generation',
                                   'cpu': 'processor generation', 'storage': 'storage capacity'}.get(field)
                quote = re.sub(r'\bRAM\b', 'memory', field+' '+ev['quote'], flags=re.I)
                field_affects = field_attribute == required['attribute'] or (
                    field == 'storage' and required['attribute'] in ('ssd capacity', 'hdd capacity'))
                # A second unresolved cable-length phrase cannot be hidden by
                # another, convenient parsed value in the same source item.
                cable_ambiguity = required['attribute'] == 'length' and (
                    CABLE_LENGTH.search(ev['quote']) or
                    cable_context and re.search(CABLE_LENGTH_VALUE, ev['quote'], re.I))
                if (ev.get('specification_alias_conflict') or field_affects or cable_ambiguity
                        or _could_affect(SimpleNamespace(quote=quote), required['attribute']) or QUALIFIER.search(quote)):
                    relevant_unknown.append(ev)
        if literal_conflict or numeric_conflict or relevant_unknown:
            result.append(checks.finding('attribute', 'UNKNOWN', required['attribute'], related, available,
                          'Contradictory requirements or unresolved qualifications prevent a reliable comparison. No favorable claim was selected.',
                          question='Which requirement and qualification is authoritative for this item?',
                          unresolved_evidence=relevant_unknown))
        else:
            if required['attribute'] == 'product model reference' and available and any(f['values'] != required['values'] for f in available):
                value = checks.finding('attribute', 'UNKNOWN', 'product model reference', [required], available,
                          'Literal model references differ. Case or punctuation equivalence was not assumed.',
                          question='Do these identifiers denote the same product, and is this mention a requirement or an example?')
            else:
                value = checks.compare_requirement(required, available, bool(offer['unknown']))
            if required['attribute'] in ('product type', 'product identity') and value['state'] == 'WORDING_DIFFERENCE':
                value['issue_candidate'] = 'Different item'
            result.append(value)
    for row, side in ((request, 'pr'), (offer, 'po')):
        for ev in row['unknown']:
            result.append(checks.finding('parser_coverage', 'UNKNOWN', 'unparsed or conflicting source field',
                          [ev] if side == 'pr' else [], [ev] if side == 'po' else [],
                          'This source field was preserved but not interpreted. It is not automatically a procurement defect.',
                          question='Confirm the exact meaning and scope of this source field.'))
    return result


def compare_documents(pr_document, po_document, *, semantic_candidates=None):
    """Return per-request evidence, one-to-one bindings, candidates and counts.

    A BOUND row means a justified technical correspondence, not a matching spec
    or permitted procurement. Duplicate specifications/quantities never break a
    tie. Split and consolidated deliveries require separate allocation review.
    """
    pr, pr_unknown = _read_document(pr_document, 'pr')
    po, po_unknown = _read_document(po_document, 'po')
    bindings = _bindings(pr, po)
    by_pr = {b['requested_position']: b for b in bindings}
    by_po = {r['position']: r for r in po}
    used = {b['offered_position'] for b in bindings}
    cable_requests = [r for r in pr if _hdmi_cable_name(r)]
    cable_offers = [o for o in po if _hdmi_cable_name(o)]
    results, document_checks = [], []
    for request in pr:
        candidates = _candidates(request, po, used)
        binding = by_pr.get(request['position'])
        if binding:
            offer = by_po[binding['offered_position']]
            state, reason = 'BOUND', binding['method']
            findings = _row_checks(request, offer)
        else:
            offer = None
            material_offers = [o for o in po if o['position'] not in used and _material_candidate(request, o)]
            semantic = []
            for candidate in semantic_candidates or []:
                if (candidate.get('requested_position') == request['position'] and candidate.get('offered_position') in by_po
                        and candidate['offered_position'] not in used and type(candidate.get('score')) in (float, int)
                        and .4 <= candidate['score'] <= 1):
                    semantic.append(candidate)
            semantic.sort(key=lambda c: c['score'], reverse=True)
            selected = semantic[0] if semantic and (len(semantic)==1 or semantic[0]['score']-semantic[1]['score'] >= .08) else None
            if selected and any(c.get('offered_position') == selected['offered_position'] and c.get('requested_position') != request['position']
                                and c.get('score', 0) >= selected['score']-.08 for c in semantic_candidates or []): selected = None
            potential = material_offers[0] if len(material_offers)==1 else by_po[selected['offered_position']] if selected else None
            # A unique HDMI-name candidate can expose conditional source checks
            # without a semantic model, fabricated score, or invented PR link.
            # Count the whole schedules so duplicates cannot resolve by elimination.
            if (potential is None and len(cable_requests) == len(cable_offers) == 1
                    and cable_requests[0] is request and cable_offers[0]['position'] not in used
                    and len([c for c in candidates if not c['already_bound']]) == 1):
                potential = cable_offers[0]
            exact = [c for c in candidates if c['evidence'] == 'EXACT_NAME_WITH_UNRESOLVED_ALLOCATION']
            lexical_tie = len([c for c in candidates if not c['already_bound']]) > 1
            state = 'AMBIGUOUS' if exact or lexical_tie else 'NO_EXACT_NAMED_OFFER' if request['identity'] else 'INVALID_IDENTITY'
            reason = ('Duplicate names, references or split/consolidated lines have no unique allocation.' if exact else
                      'Several candidate names share words; none establishes product equivalence or a unique allocation.' if lexical_tie else
                      'No unique exact-name offer is established. Shared words never establish equivalence.' if request['identity'] else
                      'A reliable source item identity is missing or conflicting.')
            no_remaining = not any(r['position'] not in used for r in po) and not po_unknown
            issue = 'Missing item' if state == 'NO_EXACT_NAMED_OFFER' and no_remaining else None
            findings = [checks.finding('item_coverage', 'UNKNOWN', 'requested item coverage',
                        [dict(ref=f'PR item {request["position"]}', quote=request['name'] or request['raw']['description'])], [], reason, issue,
                        'Confirm omission, a different product, an explicitly justified alias, or split/consolidated allocation. No quantities or specifications were compared across uncertain matches.')]
            if potential and not exact:
                offer = potential; state = 'CANDIDATE'
                reason = 'Candidate correspondence only. Confirm item identity before relying on specification or quantity compliance.'
                findings = _row_checks(request, offer)
                if material_offers:
                    findings.append(checks.finding('attribute','WORDING_DIFFERENCE','material wording',
                        [dict(ref=f'PR item {request["position"]}.item',quote=request['name'])],
                        [dict(ref=f'PO item {offer["position"]}.item',quote=offer['name'])],
                        'Material words differ in the source names. Confirm the intended product and required material.', 'Specification mismatch'))
                for finding in findings:
                    finding['conditional_state'] = finding['state']; finding['state'] = 'UNKNOWN'
                    finding['conditional_on_item_identity'] = True
                    finding['explanation'] = 'Candidate identity requires manual confirmation. ' + finding['explanation']
        results.append(dict(requested_position=request['position'], requested=request['source'],
                            offered_position=offer['position'] if offer else None, offered=offer['source'] if offer else None,
                            matching_state=state, matching_reason=reason, candidates=candidates, checks=findings,
                            issue_candidates=sorted({f['issue_candidate'] for f in findings if f['issue_candidate']})))
    for side, unknown in (('pr', pr_unknown), ('po', po_unknown)):
        for ev in unknown:
            document_checks.append(checks.finding('document_scope', 'UNKNOWN', 'unparsed document text or scope',
                [ev] if side == 'pr' else [], [ev] if side == 'po' else [],
                'This source text remains unresolved; it was not assigned to a convenient item.',
                question='Locate this source text and confirm the item, group or order-wide scope.'))
    if not pr or not po:
        document_checks.append(checks.finding('input', 'UNKNOWN', 'document contents', [], [],
            'At least one source item schedule is empty. No placeholder creates a successful match.',
            question='Provide complete source item schedules.'))
    extra = [dict(position=r['position'], source=r['source'], state='UNALLOCATED',
                  explanation='No unique requested-item allocation. Extra purchase, substitute, split or alias remains a human question.') for r in po if r['position'] not in used]
    all_checks = document_checks + [c for r in results for c in r['checks']]
    return dict(schema=SCHEMA, version=VERSION, isolated=True, source_only=True, rows=results, bindings=bindings,
                unallocated_offers=extra, document_checks=document_checks,
                counts=dict(requested=len(pr), offered=len(po), bound=len(bindings),
                            unresolved_requested=len(pr)-len(bindings), unallocated_offers=len(extra),
                            checks=dict(Counter(c['state'] for c in all_checks))),
                pending_policy_ids=['P01', 'P02', 'P03', 'P04', 'P05', 'P06'],
                suggested_status=None, final_status=None, primary_issue=None,
                human_decision_required=True, human_verified=False, procurement_write=False,
                feature_vector=None, operational_feature_contract_changed=False)
