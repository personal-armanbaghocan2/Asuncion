"""Inactive, source-only technical assistance. No policy/status decisions.

Only descriptions, quantities, units and document terms establish evidence.
Proposed labels, alignments, relationship answers, IDs and model predictions are
not inspected. Unknown grammar, aliases and allocation remain explicit questions.
"""
from collections import defaultdict
from decimal import Decimal, localcontext
from pathlib import Path
import re
import sys
import unicodedata

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
# Reuse the already tested, inactive strict number/unit/attribute grammar.
from fasttext_features_v0_1_0 import parse_number, Clause, _parse_clause, _compatible, _unit, _contradictory

VERSION = '0.1.0'
SOURCE_ONLY = ('description', 'quantity', 'unit')
WORDS = {'one': '1', 'two': '2', 'three': '3', 'four': '4', 'five': '5',
         'six': '6', 'seven': '7', 'eight': '8', 'nine': '9', 'ten': '10',
         'twelve': '12', 'fourteen': '14', 'eighteen': '18', 'twenty': '20',
         'twenty-four': '24', 'thirty-six': '36', 'forty': '40', 'fifty': '50',
         'sixty': '60', 'seventy': '70', 'eighty': '80', 'hundred': '100'}
EXACT_UNITS = {'days', 'day', 'ports', 'port', 'consignments', 'consignment',
               'generation', 'generations', 'dB', 'Hz'}
MISSING = re.compile(r'^(?:not specified|not provided|unspecified|unknown|missing|not stated)$', re.I)
CPU_FAMILY = re.compile(r'\b(?:Intel\s+Core\s+i[3579]|AMD\s+Ryzen\s+[3579])\b', re.I)


def norm(text):
    return ' '.join(unicodedata.normalize('NFKC', str(text)).casefold().split()).rstrip('.')


def observable(record):
    """Positive allowlist; even deliberately wrong gold annotations cannot leak."""
    return {side: [{key: item.get(key, '') for key in SOURCE_ONLY} for item in record.get(side, [])]
            for side in ('pr_items', 'po_items')} | {
                'document_terms': {side: list(record.get('document_terms', {}).get(side, [])) for side in ('pr', 'po')}}


def fact(attribute, operator, values, unit, side, ref, quote, kind='number', **extra):
    return dict(attribute=attribute, operator=operator, values=list(map(str, values)), unit=unit,
                side=side, ref=ref, quote=quote, kind=kind, **extra)


def _body(text):
    text = text.strip().rstrip('.').strip()
    text = re.sub(r'\s+(?:shall|must)\s+be\s+', ' ', text, flags=re.I)
    return re.sub(r'\s+is\s+stated\s+as\s+', ' ', text, flags=re.I)


def _number_words(text):
    # This bounded replacement is for the grammar only. Evidence keeps the exact
    # original quote. Model identifiers and item names never enter this function.
    for word in sorted(WORDS, key=len, reverse=True):
        text = re.sub(r'\b'+re.escape(word)+r'\b', WORDS[word], text, flags=re.I)
    return text


def parse_fact(text, side, ref):
    body = _body(text)
    if re.search(r'\b(?:unless|supersedes|alternative|except|not required)\b', body, re.I):
        return None
    # A model mention is text, never an untyped numeric procurement constraint.
    model = re.fullmatch(r'product model\s+(.+)', body, re.I)
    if model:
        return fact('product model mention', 'MENTION', [model[1]], '', side, ref, text, 'mention')
    # Required literals are comparisons of stated wording, not an ontology.
    categorical = re.fullmatch(r'(?P<a>[A-Za-z][A-Za-z /_-]*?)\s+required\s*:\s*(?P<v>.+)', body, re.I)
    if categorical:
        return fact(norm(categorical['a']), 'LITERAL', [categorical['v']], '', side, ref, text, 'literal')
    categorical = re.fullmatch(r'(?P<a>[A-Za-z][A-Za-z /_-]*?)\s+(?:exactly|=\s*)\s*(?P<v>[A-Za-z][A-Za-z /_-]*)', body, re.I)
    if categorical:
        return fact(norm(categorical['a']), 'LITERAL', [categorical['v']], '', side, ref, text, 'literal')
    # Processor generations are ordinal comparisons only, never performance
    # equivalence or a comparison across different declared processor families.
    generation = re.fullmatch(
        r'(?:(?:processor|cpu)\s+(?P<family>Intel Core i[3579]|AMD Ryzen [3579])\s*[:,]?\s*)?'
        r'(?:(?:processor|cpu)\s+)?(?:generation\s*)?'
        r'(?P<op>minimum|at least|maximum|at most|exactly|>=|<=|=|>|<)?\s*'
        r'(?P<n>[0-9]+)(?:st|nd|rd|th)?\s*(?:generation|gen)?', body, re.I)
    if generation and re.search(r'\b(?:generation|gen)\b', body, re.I):
        operators = {'minimum': 'GE', 'at least': 'GE', '>=': 'GE', 'maximum': 'LE',
                     'at most': 'LE', '<=': 'LE', 'exactly': 'EQ', '=': 'EQ', '>': 'GT', '<': 'LT'}
        op = operators.get((generation['op'] or '').lower(), 'EQ' if side == 'po' else 'UNSPECIFIED')
        return fact('processor generation', op, [generation['n']], 'generation', side, ref, text,
                    family=norm(generation['family'] or ''))
    # An attribute-prefix form permits a plain offer such as "generation: 13".
    generation = re.fullmatch(r'(?:processor|cpu) generation\s*:\s*([0-9]+)', body, re.I)
    if generation:
        return fact('processor generation', 'EQ' if side == 'po' else 'UNSPECIFIED',
                    [generation[1]], 'generation', side, ref, text, family='')
    cleaned = _number_words(body.replace(':', ' '))
    clause = _parse_clause(cleaned, side, ref)
    if clause:
        attribute = clause.attribute
        if attribute in ('ram', 'ram capacity', 'memory capacity'): attribute = 'memory'
        if attribute in ('storage', 'disk capacity', 'ssd capacity', 'hdd capacity'): attribute = 'storage capacity'
        return fact(attribute, clause.operator, clause.values, clause.unit, side, ref, text)
    # A categorical offer must name the same attribute as a requirement. Bare
    # prose and product names do not become declarations by token similarity.
    if side == 'po':
        literal = re.fullmatch(r'(?P<a>[A-Za-z][A-Za-z /_-]*?)\s*:\s*(?P<v>.+)', body, re.I)
        if literal:
            return fact(norm(literal['a']), 'LITERAL', [literal['v']], '', side, ref, text, 'literal')
    return None


def parse_document(items, terms, side):
    parsed = []
    for position, item in enumerate(items, 1):
        description = str(item.get('description') or '')
        pieces = [p.strip() for p in re.split(r';|\r?\n', description) if p.strip()]
        head = pieces[0] if pieces else ''
        identity = norm(head) if head and not re.match(r'^(?:minimum|maximum|at least|at most|exactly|\d)', head, re.I) else ''
        clauses = pieces[1:] if identity else pieces
        row = dict(position=position, identity=identity, name=head, raw=dict(item),
                   facts=[], unknown=[], quantity_ref=f'{side.upper()} item {position}.quantity')
        for number, text in enumerate(clauses, 1):
            ref = f'{side.upper()} item {position}.description clause {number}'
            if re.fullmatch(r'(?:component of the fictional .+ supply bundle|both endpoints included|'
                            r'loose pieces or equivalent packs permitted|equivalent packs accepted|'
                            r'consolidated supply for both identical requested lots|'
                            r'each (?:pack|box) contains .+)', text, re.I):
                continue  # Retained in the raw description; no scope inferred.
            value = parse_fact(text, side, ref)
            if value: row['facts'].append(value)
            else: row['unknown'].append(dict(ref=ref, quote=text))
        parsed.append(row)
    unscoped = []
    for number, text in enumerate(terms, 1):
        ref = f'{side.upper()} document term {number}'
        scoped = re.fullmatch(r'For\s+(.+?),\s*(.+)', text.strip(), re.I)
        if not scoped:
            scoped = re.fullmatch(r'Acceptance condition applying to\s+(.+?):\s*(.+)', text.strip(), re.I)
        targets = [row for row in parsed if scoped and row['identity'] == norm(scoped[1])]
        if not targets:
            unscoped.append(dict(ref=ref, quote=text)); continue
        # A named document term belongs to that identity group once, not once
        # per identical lot. It does not allocate particular pieces to lots.
        for clause_no, clause in enumerate(scoped[2].split(';'), 1):
            if norm(clause) == 'both endpoints included': continue
            value = parse_fact(clause, side, f'{ref}, clause {clause_no}')
            if value:
                value['quote'] = text
                targets[0]['facts'].append(value)
            else: targets[0]['unknown'].append(dict(ref=ref, quote=text))
    return parsed, unscoped


def finding(kind, state, attribute, required, offered, explanation, issue=None, question='', **extra):
    return dict(kind=kind, state=state, attribute=attribute, requested_evidence=required,
                offered_evidence=offered, explanation=explanation, issue_candidate=issue,
                question=question, evidence_provenance='DETERMINISTIC_SOURCE_CHECK',
                classification_provenance='RULE_SUGGESTION_UNVERIFIED' if issue else None, **extra)


def _clause(value):
    return Clause(value['attribute'], value['operator'], tuple(parse_number(v) for v in value['values']),
                  value['unit'], dict(quote=value['quote'], path=value['ref']))


def comparable(required, offered):
    left, right = _clause(required), _clause(offered)
    values_a, values_b, reason = _compatible(left, right)
    if not reason:
        # The shared extractor returns canonical base units. This report prints
        # requested units, so convert BOTH sides back before reporting arithmetic.
        scale = _unit(required['unit'])[2] or Decimal(1)
        with localcontext() as context:
            context.prec = 256
            return tuple(v/scale for v in values_a), tuple(v/scale for v in values_b), ''
    a, b = required['unit'], offered['unit']
    if a in EXACT_UNITS and b in EXACT_UNITS and a.rstrip('s') == b.rstrip('s'):
        return left.values, right.values, ''
    return None, None, reason


def compare_requirement(required, offers, has_unparsed_offer=False):
    attribute = required['attribute']
    if not offers:
        return finding('attribute', 'UNKNOWN', attribute, [required], [],
                       f'No reliable offered value was parsed for {attribute}.',
                       None if has_unparsed_offer else 'Missing or ambiguous information',
                       f'Confirm the offered {attribute}; an unparsed sentence must not be treated as a missing procurement fact.')
    if any(MISSING.fullmatch(v.strip()) for offered in offers for v in offered['values']):
        return finding('attribute', 'UNKNOWN', attribute, [required], offers,
                       f'The offered {attribute} is explicitly unspecified; it is not zero.',
                       'Missing or ambiguous information', f'What is the committed offered {attribute}?')
    if required['kind'] in ('literal', 'mention'):
        distinct = {norm(v) for offer in offers for v in offer['values']}
        if len(distinct) != 1:
            return finding('attribute', 'UNKNOWN', attribute, [required], offers,
                           'More than one stated value exists for this named property; neither claim was selected.',
                           'Missing or ambiguous information',
                           'Do these statements concern one exclusive property, separate additions, or alternative offers?')
        equal = distinct == {norm(v) for v in required['values']}
        if required['kind'] == 'mention' and not equal:
            return finding('attribute', 'UNKNOWN', attribute, [required], offers,
                           'The model mentions differ. A mention alone does not establish a mandatory model or substitution rule.',
                           question='Is this model mention an exact requirement, an example, or an authorized equivalence reference?')
        return finding('attribute', 'PASS' if equal else 'WORDING_DIFFERENCE', attribute, [required], offers,
                       'The stated values match exactly.' if equal else 'The stated values differ; no material/technology equivalence was invented.',
                       None if equal else 'Specification mismatch',
                       '' if equal else 'Does the offered wording satisfy the exact requested property under the authorized rubric?')
    if any(offer['kind'] != 'number' for offer in offers):
        return finding('attribute', 'UNKNOWN', attribute, [required], offers, 'The offered numeric value is not reliably parsed.',
                       question='Confirm a numeric offer with its unit and scope.')
    if any(offer['operator'] != 'EQ' for offer in offers):
        return finding('attribute', 'UNKNOWN', attribute, [required], offers,
                       'The offer states a bound or range, not one committed value.',
                       question='Confirm the committed offered value or the permitted interpretation of the offered bound.')
    converted = [comparable(required, offered) for offered in offers]
    reasons = [reason for _, _, reason in converted if reason]
    if reasons:
        reason = reasons[0]
        return finding('attribute', 'UNKNOWN', attribute, [required], offers,
                       'No valid unit comparison was made: '+reason+'.',
                       question='Choose and document the MB/GB convention (P06).' if reason == 'POLICY_P06_PENDING' else 'Confirm compatible units or an explicitly declared equivalence.',
                       pending_policies=['P06'] if reason == 'POLICY_P06_PENDING' else [])
    if attribute == 'processor generation' and (
            not required.get('family') or any(offer.get('family') != required['family'] for offer in offers)):
        return finding('attribute', 'UNKNOWN', attribute, [required], offers,
                       'Generation numbers cannot establish equivalence across different or unstated processor families.',
                       question='Confirm the offered processor family; do not infer performance from generation or i5/i7 numbers.')
    if len({tuple(values) for _, values, _ in converted}) != 1:
        return finding('attribute', 'UNKNOWN', attribute, [required], offers,
                       'Different offered values remain in the source. A convenient value was not selected.',
                       'Missing or ambiguous information', 'Which value is committed, or are these permitted alternatives with different scope?')
    left, right, _ = converted[0]
    if required['operator'] == 'UNSPECIFIED':
        return finding('attribute', 'UNKNOWN', attribute, [required], offers,
                       'The required number has no explicit minimum, maximum, exact or range operator.',
                       question=f'Does the stated {attribute} mean a minimum, maximum, exact value or permitted range?')
    low, high, offered = left[0], left[-1], right[0]
    if required['operator'] == 'INTERVAL' and low > high:
        return finding('attribute', 'UNKNOWN', attribute, [required], offers, 'The source range has reversed endpoints.',
                       question='What is the corrected authorized requirement range?')
    good = {'GE': offered >= low, 'GT': offered > low, 'LE': offered <= low,
            'LT': offered < low, 'EQ': offered == low, 'INTERVAL': low <= offered <= high}[required['operator']]
    issue = None if good else ('Below minimum' if required['operator'] in ('GE', 'GT') else
                              'Above maximum' if required['operator'] in ('LE', 'LT') else 'Exact or range mismatch')
    with localcontext() as context:
        context.prec = 256
        difference = offered-low
    operator_words = {'GE': 'at least', 'GT': 'more than', 'LE': 'at most', 'LT': 'less than',
                      'EQ': 'exactly', 'INTERVAL': 'between'}
    explanation = (f'Required {operator_words[required["operator"]]} {low}' + (f' and {high}' if required['operator'] == 'INTERVAL' else '') +
                   f' {required["unit"]}; offered equivalent {offered} {required["unit"]}. '+
                   f'Difference from the required lower/value bound: {difference} {required["unit"]}.')
    if attribute == 'processor generation': explanation += ' This is ordinal arithmetic, not a CPU performance benchmark.'
    return finding('attribute', 'PASS' if good else 'VIOLATED', attribute, [required], offers, explanation, issue,
                   normalized_required=list(map(str, left)), normalized_offered=str(offered), difference=str(difference))


def quantity_value(row, side):
    raw = row['raw']
    ev = fact('quantity', 'EQ', [raw.get('quantity', '')], str(raw.get('unit') or ''),
              side, row['quantity_ref'], str(raw.get('quantity')) if raw.get('quantity') not in (None, '') else '[not supplied]')
    try:
        number = parse_number(raw.get('quantity'))
        if number <= 0: return None, ev, 'A zero or negative procurement quantity needs clarification.'
    except ValueError:
        return None, ev, 'The source quantity is missing or not a valid signed/decimal/thousands-formatted number.'
    return number, ev, ''


def compare_quantities(requested, offered):
    left = [quantity_value(row, 'pr') for row in requested]
    right = [quantity_value(row, 'po') for row in offered]
    req, off = [v[1] for v in left], [v[1] for v in right]
    if any(problem for _, _, problem in left+right):
        return finding('quantity', 'UNKNOWN', 'quantity', req, off,
                       next(problem for _, _, problem in left+right if problem),
                       'Missing or ambiguous information', 'Confirm each source quantity; missing is not zero.')
    units = {v['unit'] for v in req}
    if len(units) != 1:
        return finding('quantity', 'UNKNOWN', 'quantity', req, off, 'Requested lots use different units.',
                       question='Confirm the requested lot units before aggregating.')
    target_unit = req[0]['unit']
    values = []
    for amount, ev, _ in right:
        trial = dict(ev, values=[str(amount)])
        probe = dict(req[0], values=['1'])
        _, converted, reason = comparable(probe, trial)
        if reason:
            text = offered[0]['raw']['description'] if len(offered) == 1 else ''
            pack = re.search(r'\beach (pack|box) contains ([0-9][0-9,.]*) (pieces?|units?)\b', text, re.I)
            requested_permission = all(re.search(r'\bequivalent (?:counted )?packs (?:permitted|accepted)\b',
                                                row['raw']['description'], re.I) for row in requested)
            if pack and len(requested) == len(offered) == 1 and requested_permission:
                container = _unit(ev['unit']); base = _unit(pack[3]); target = _unit(target_unit)
                factors = re.findall(r'\beach (pack|box) contains ([0-9][0-9,.]*) (pieces?|units?)\b', text, re.I)
                try:
                    scoped_factors = {(parse_number(n), _unit(u)[1]) for c, n, u in factors
                                      if c.lower() == pack[1].lower() and _unit(u)}
                    factor = parse_number(pack[2])
                except ValueError:
                    scoped_factors, factor = set(), Decimal(0)
                if container and base and target and container[1] == pack[1].lower() and base[1] == target[1] and len(scoped_factors) == 1:
                    if factor > 0:
                        with localcontext() as context:
                            context.prec = 256
                            values.append(amount*factor)
                        continue
            return finding('quantity', 'UNKNOWN', 'quantity', req, off,
                           'Different units were not silently equated. No sufficient source pack-equivalence evidence was found.',
                           'Quantity or unit mismatch', 'Are the forms equivalent, and what explicitly stated pack size and acceptance permission apply?')
        values.append(converted[0])
    with localcontext() as context:
        context.prec = 256
        required_total = sum(v[0] for v in left)
        offered_total = sum(values)
        difference = offered_total-required_total
    return finding('quantity', 'PASS' if difference == 0 else 'VIOLATED', 'quantity', req, off,
                   f'Requested total {required_total} {target_unit}; offered equivalent {offered_total} {target_unit}. Difference: {difference} {target_unit}.',
                   None if difference == 0 else 'Quantity or unit mismatch',
                   normalized_required=str(required_total), normalized_offered=str(offered_total), difference=str(difference))


def analyze(record):
    source = observable(record)
    requested, pr_unscoped = parse_document(source['pr_items'], source['document_terms']['pr'], 'pr')
    offered, po_unscoped = parse_document(source['po_items'], source['document_terms']['po'], 'po')
    left, right = defaultdict(list), defaultdict(list)
    for row in requested: left[row['identity']].append(row)
    for row in offered: right[row['identity']].append(row)
    checks, bindings, used_pr, used_po = [], [], set(), set()
    if not requested or not offered:
        checks.append(finding('input', 'UNKNOWN', 'document contents', [], [],
                              'At least one item schedule is empty. No placeholder or successful comparison was invented.',
                              question='Provide both complete source item schedules.'))
    for identity, requests in left.items():
        offers = right.get(identity, []) if identity else []
        single = len(requests) == len(offers) == 1
        consolidation = (identity and len(requests) > 1 and len(offers) == 1
                         and len({row['raw']['description'] for row in requests}) == 1
                         and 'consolidated supply for both identical requested lots' in norm(offers[0]['raw']['description']))
        if not single and not consolidation:
            continue
        used_pr.update(row['position'] for row in requests); used_po.update(row['position'] for row in offers)
        bindings.append(dict(requested_positions=[r['position'] for r in requests],
                             offered_positions=[r['position'] for r in offers],
                             method='EXACT_UNIQUE_NAME' if single else 'EXPLICIT_PO_CONSOLIDATION_CLAIM',
                             identity_source=identity))
        checks.append(compare_quantities(requests, offers))
        if consolidation:
            checks.append(finding('allocation', 'UNKNOWN', 'lot consolidation', [], [],
                                  'The PO explicitly claims one line covers both identical named lots; totals were counted once.',
                                  question='Is consolidated fulfilment permitted for these requested lots? Arithmetic alone does not grant permission.'))
        required_facts = [f for row in requests for f in row['facts']]
        offered_facts = [f for row in offers for f in row['facts']]
        unparsed_offer = any(row['unknown'] for row in offers)
        for requirement in required_facts:
            matches = [f for f in offered_facts if f['attribute'] == requirement['attribute']]
            related = [f for f in required_facts if f['attribute'] == requirement['attribute'] and f['kind'] == 'number']
            if requirement['kind'] == 'number' and _contradictory([_clause(f) for f in related]):
                checks.append(finding('attribute', 'UNKNOWN', requirement['attribute'], related, matches,
                                      'The requested bounds contradict each other; no offered value was declared compliant or defective.',
                                      'Missing or ambiguous information', 'Which requirement is authoritative, or do these clauses concern different alternatives?'))
            else:
                checks.append(compare_requirement(requirement, matches, unparsed_offer))
        for row in requests+offers:
            for value in row['unknown']:
                checks.append(finding('parser_coverage', 'UNKNOWN', 'unparsed source clause',
                                      [value] if row in requests else [], [value] if row in offers else [],
                                      'The checker did not interpret this source clause; that does not prove a procurement defect.',
                                      question='Confirm the meaning and scope of this exact clause without inventing a missing value.'))
    unmatched_pr = [row for row in requested if row['position'] not in used_pr]
    unmatched_po = [row for row in offered if row['position'] not in used_po]
    for row in unmatched_pr:
        reason = 'No unique, non-reused exact-name offer was established for this requested source line.'
        checks.append(finding('item_coverage', 'UNKNOWN', 'requested item coverage',
                              [dict(ref=f'PR item {row["position"]}', quote=row['raw']['description'])],
                              [dict(ref=f'PO item {r["position"]}', quote=r['raw']['description']) for r in unmatched_po],
                              reason, 'Missing item' if not unmatched_po and row['identity'] else None,
                              'Confirm whether this is an omission, an explicitly equivalent differently named item, or a split/consolidated allocation.'))
    for row in unmatched_po:
        checks.append(finding('item_coverage', 'UNKNOWN', 'unallocated offered item', [],
                              [dict(ref=f'PO item {row["position"]}', quote=row['raw']['description'])],
                              'This offered source line has no verified unique allocation. It was not automatically treated as a substitute or extra purchase.',
                              question='Which requested line does this offer supply, or is it extra scope requiring authorization?'))
    for side, unknown in (('pr', pr_unscoped), ('po', po_unscoped)):
        for value in unknown:
            checks.append(finding('document_scope', 'UNKNOWN', 'unscoped document term',
                                  [value] if side == 'pr' else [], [value] if side == 'po' else [],
                                  'This document term was not attached to a convenient item.',
                                  question='Which item, group or order-wide obligation does this exact term govern?'))
    issues = sorted({check['issue_candidate'] for check in checks if check['issue_candidate']})
    if checks and all(check['state'] == 'PASS' for check in checks):
        issues = ['Requirement satisfied']
    questions = list(dict.fromkeys(c['question'] for c in checks if c['question']))
    if len(issues) > 1:
        questions.append('Which issue is primary when these different findings coexist? No priority rule was invented.')
    return dict(checker_version=VERSION, source_only=True, bindings=bindings, checks=checks,
                requested_items=len(requested), offered_items=len(offered),
                matched_requested_items=len(used_pr), matched_offered_items=len(used_po),
                issue_candidates=issues, suggested_primary_issue=issues[0] if len(issues) == 1 else None,
                suggestion_provenance='RULE_SUGGESTION_UNVERIFIED', supported_status_suggestion=None,
                status_reason='No authorized machine-readable status/severity policy is available; dataset conventions remain proposals.',
                human_decision_required=True, human_verified=False, official_decision=False,
                questions=questions, multiple_issue_priority_pending=len(issues) > 1)
