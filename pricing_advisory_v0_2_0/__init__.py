"""Read-only NLP-review adapter. No Flask, DB, model, COA or shadow imports.

Only build_for_review can read records, through its server-supplied connection.
It never commits. Current schema cannot supply all v0.1.0 monetary policy inputs;
source-level arithmetic/copy checks are explicitly distinguished from compliance.
"""

from decimal import Decimal, Context, localcontext, InvalidOperation
import json

__version__ = '0.2.0'
PASS = 'Check Passed'
FAIL = 'Discrepancy Found'
MISSING = 'Insufficient Evidence'
NA = 'Check Not Applicable'

# Exact inspected relationships; no supplier, personal names, raw OCR, passwords,
# inventory, uploads or COA data are projected. Text casts avoid Python float loss.
RECORD_SQL = '''select n.id nlp_id, n.po_id expected_po_id,
    p.id po_id, p.no po_no, p.aoq_id expected_aoq_id, p.status po_status,
    left(p.items,200001) po_items, p.total::text po_total,
    aq.id aoq_id, aq.no aoq_no, aq.pr_id expected_pr_id, aq.status aoq_status,
    (aq.winner is not null and btrim(aq.winner) <> '') has_winner,
    left(aq.winning_offer,200001) aoq_items, aq.winning_amount::text aoq_total,
    r.id pr_id, r.no pr_no, r.app_id expected_app_id, r.status pr_status, r.total::text pr_total,
    a.id app_id, a.no app_no, a.status app_status, a.total::text app_total
    from nlp n left join po p on p.id=n.po_id
    left join aoq aq on aq.id=p.aoq_id
    left join pr r on r.id=aq.pr_id
    left join app_plan a on a.id=r.app_id where n.id=?'''


class EvidenceError(ValueError):
    pass


def empty(message='Pricing evidence is unavailable for this review.'):
    return {'rows': [{'check': 'Pricing evidence', 'state': MISSING, 'message': message, 'sources': []}],
            'values': [], 'lines': [], 'advisory': True, 'suggested_status': None}


def finding(view, check, state, message, *sources):
    view['rows'].append({'check': check, 'state': state, 'message': message,
                         'sources': list(dict.fromkeys(sources))})


def decimal_text(value):
    """Exact conversion of stored decimal text/JSON numbers; never float coercion."""
    if type(value) not in (str, int, Decimal) or type(value) is bool:
        return None
    if type(value) is str and (not value or len(value) > 60 or value.strip() != value):
        return None
    try:
        # Plain numbers/exponents from stored JSON are exact Decimal inputs.
        # Currency symbols/grouping are not removed and cannot silently change units.
        if type(value) is str:
            import re
            if not re.fullmatch(r'(?:0|[1-9][0-9]*)(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?', value):
                return None
        d = Decimal(value)
        if not d.is_finite() or d < 0 or d.adjusted() > 17 or d.as_tuple().exponent < -9:
            return None
        return format(d, 'f')
    except (InvalidOperation, ValueError, OverflowError):
        return None


def source_items(raw):
    if type(raw) is not str or not raw or len(raw) > 200000:
        raise EvidenceError('Missing or oversized item evidence.')

    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise EvidenceError('Duplicate source fields conflict.')
            result[key] = value
        return result

    def invalid_constant(_):
        raise EvidenceError('Invalid numeric evidence.')

    try:
        rows = json.loads(raw, parse_float=Decimal, parse_constant=invalid_constant, object_pairs_hook=pairs)
    except (ValueError, TypeError, RecursionError):
        raise EvidenceError('Invalid or conflicting item evidence.') from None
    if type(rows) is not list or not 1 <= len(rows) <= 200 or any(type(row) is not dict for row in rows):
        raise EvidenceError('Expected 1 to 200 item objects.')
    return rows


def exact_cents(value):
    with localcontext(Context(prec=80)):
        return value == value.quantize(Decimal('.01'))


def reference(side, record, field):
    return f'{side}[id={record.get(side + "_id")}].{field}'


def present_verifier_report(report, source_map=None):
    """Shared v0.1.0 presenter; unknown/unsupported never becomes not-applicable.

    Full budget/ceiling results can be presented when a future trusted source
    actually supplies that evidence. Current DB adapter supplies no such budget.
    """
    states = {'CHECK_PASSED': PASS, 'CHECK_FAILED': FAIL,
              'MISSING_EVIDENCE': MISSING, 'CONFLICTING_EVIDENCE': MISSING,
              'UNSUPPORTED': MISSING, 'INVALID_INPUT': MISSING}
    rows = []
    for item in report.get('findings', []):
        refs = []
        for evidence in item.get('evidence', []):
            pointer = evidence.get('pointer', '')
            refs.append((source_map or {}).get(pointer, 'Verifier snapshot ' + pointer))
        message = item['message']
        if item.get('computed'):
            message += ' ' + '; '.join(str(k).replace('_',' ') + ': ' + str(v)
                                       for k,v in item['computed'].items()) + '.'
        rows.append({'check': item['check'].replace('_', ' ').title(),
                     'state': states.get(item['result'], MISSING),
                     'message': message, 'sources': refs})
    return rows


def adapt_record(record):
    """Pure projection of authorized query output; no extra database relationships.

    Missing subtotal, currency, basis, units definition and adjustment policy stay
    missing. No APP estimate is promoted to an explicit budget or ceiling.
    """
    view = {'rows': [], 'values': [], 'lines': [], 'advisory': True, 'suggested_status': None}
    snapshot = {'schema_version': '0.1.0', 'unit_definitions': {}, 'ceilings': []}
    parsed = {}
    for side in ('po', 'aoq'):
        rid = record.get(side + '_id')
        if type(rid) is not int or rid <= 0:
            finding(view, side.upper() + ' source', MISSING, 'The linked source document is missing.', f'nlp[id={record.get("nlp_id")}].po_id' if side == 'po' else 'po.aoq_id')
            continue
        prefix = reference(side, record, '')[:-1]
        doc = {'id': f'{side}:{rid}', 'source_ref': prefix, 'items': [],
               'total': decimal_text(record.get(side + '_total'))}
        snapshot[side] = doc
        view['values'].append({'label': side.upper() + ' stated total', 'value': doc['total'] if doc['total'] is not None else 'Missing or invalid',
                               'source': reference(side, record, 'total' if side == 'po' else 'winning_amount')})
        if side == 'po':
            doc['aoq_id'] = f'aoq:{record.get("aoq_id")}'
        else:
            doc.update(status=record.get('aoq_status'), approval_ref=prefix + '.status',
                       winning_offer_confirmed=record.get('aoq_status') == 'Approved' and record.get('has_winner') is True,
                       winning_offer_ref=prefix + '.winning_offer')
        try:
            rows = source_items(record.get(side + '_items'))
        except EvidenceError:
            finding(view, side.upper() + ' item evidence', MISSING,
                    'Stored items are missing, malformed, conflicting or exceed the supported size.',
                    reference(side, record, 'items' if side == 'po' else 'winning_offer'))
            continue
        parsed[side] = rows
        amounts = []
        for index, row in enumerate(rows):
            field = ('items' if side == 'po' else 'winning_offer') + f'[{index}]'
            ref = reference(side, record, field)
            qty, price, amount = [decimal_text(row.get(key)) for key in ('qty', 'unit_cost', 'amount')]
            unit = row.get('unit')
            valid_unit = type(unit) is str and bool(unit.strip()) and len(unit) <= 80
            sid = row.get('_AOQ Item ID')
            sid = sid if type(sid) is str and 0 < len(sid) <= 160 else None
            doc['items'].append({'source_line_id': sid, 'quantity': qty, 'unit': unit if valid_unit else None,
                                 'price_unit': unit if valid_unit else None, 'unit_price': price, 'amount': amount})
            product = None
            with localcontext(Context(prec=80)):
                if qty is not None and price is not None:
                    product = Decimal(qty) * Decimal(price)
                    calculated = format(product, 'f')
                else:
                    calculated = 'Cannot calculate'
            view['lines'].append({'side': side.upper(), 'identity': sid or f'Row {index + 1} (unbound)',
                                  'quantity': qty if qty is not None else 'Missing or invalid',
                                  'unit': unit if valid_unit else 'Missing or invalid',
                                  'unit_price': price if price is not None else 'Missing or invalid',
                                  'amount': amount if amount is not None else 'Missing or invalid',
                                  'calculated': calculated, 'source': ref})
            if not valid_unit or product is None or amount is None:
                finding(view, side.upper() + f' row {index + 1} arithmetic', MISSING,
                        'Quantity, unit, unit price and amount must be explicit; no missing value was replaced with zero.',
                        ref + '.qty', ref + '.unit', ref + '.unit_cost', ref + '.amount')
            elif product == Decimal(amount):
                finding(view, side.upper() + f' row {index + 1} arithmetic', PASS,
                        'The unrounded quantity × unit price equals the stored amount exactly. Currency, taxes and budget compliance are not established.',
                        ref + '.qty', ref + '.unit_cost', ref + '.amount')
            elif exact_cents(product) and exact_cents(Decimal(amount)):
                finding(view, side.upper() + f' row {index + 1} arithmetic', FAIL,
                        'The stored amount differs from quantity × unit price even without sub-cent rounding. This is a numeric discrepancy under the existing AOQ line-amount contract.',
                        ref + '.qty', ref + '.unit_cost', ref + '.amount', 'aoq_flexible.py:winning_offer_error')
            else:
                finding(view, side.upper() + f' row {index + 1} arithmetic', MISSING,
                        'The unrounded product differs; the stored evidence does not define a Decimal rounding/adjustment policy, so monetary compliance cannot be determined.',
                        ref + '.qty', ref + '.unit_cost', ref + '.amount')
            amounts.append(Decimal(amount) if amount is not None else None)
        total = decimal_text(record.get(side + '_total'))
        if amounts and all(a is not None for a in amounts) and total is not None:
            with localcontext(Context(prec=80)):
                line_sum = sum(amounts, Decimal(0))
            finding(view, side.upper() + ' stored line sum', PASS if line_sum == Decimal(total) else FAIL,
                    'Sum of stored line amounts: ' + format(line_sum, 'f') + '; stated total: ' + total + '. This compares stored numeric fields only.',
                    reference(side, record, 'items' if side == 'po' else 'winning_offer'),
                    reference(side, record, 'total' if side == 'po' else 'winning_amount'))
        else:
            finding(view, side.upper() + ' stored line sum', MISSING, 'A complete line sum and stated total are required.', prefix)
    for side in ('pr', 'app'):
        total = decimal_text(record.get(side + '_total'))
        if record.get(side + '_id') is not None:
            view['values'].append({'label': side.upper() + ' stored estimate (not a verified budget ceiling)',
                                  'value': total if total is not None else 'Missing or invalid',
                                  'source': reference(side, record, 'total')})
    finding(view, 'Approved budget', MISSING,
            'The linked APP/PR totals are estimates. No separately identified approved budget ceiling and scope are available in the inspected source contract.',
            reference('app', record, 'total'), reference('app', record, 'status'), reference('pr', record, 'total'))
    finding(view, 'Explicit price ceiling', MISSING,
            'No documented price-ceiling field is available in the current record contract; no market or historical COA price is substituted.',
            reference('pr', record, 'items'), reference('aoq', record, 'winning_offer'))
    _compare_offer(view, record, parsed)
    from procurement_pricing_v0_1_0 import verify_snapshot
    # Intentionally incomplete: source metadata is missing, not synthesized from
    # display labels, estimates, item names or a hidden configuration default.
    verifier_report = verify_snapshot(snapshot, enabled=True)
    view['rows'].extend(present_verifier_report(verifier_report))
    view['verifier_state'] = verifier_report['processing_state']
    view['snapshot'] = snapshot  # Internal/test contract; template never dumps it.
    return view


def _compare_offer(view, record, parsed):
    if record.get('aoq_status') != 'Approved':
        known_status = record.get('aoq_status') in ('For Review', 'Draft', 'Reviewed', 'Rejected')
        finding(view, 'Approved AOQ comparison', NA if known_status else MISSING,
                'Winning-offer comparison requires a documented approved AOQ; this source is not approved or its status is missing. No price compliance conclusion.',
                reference('aoq', record, 'status'))
        return
    if record.get('has_winner') is not True or 'aoq' not in parsed or 'po' not in parsed:
        finding(view, 'Approved AOQ comparison', MISSING, 'The approved winning offer and PO rows must both be available.',
                reference('aoq', record, 'winning_offer'), reference('po', record, 'items'))
        return
    maps = []
    for side in ('aoq', 'po'):
        result = {}
        for index, row in enumerate(parsed[side]):
            identity = row.get('_AOQ Item ID')
            if type(identity) is not str or not identity or identity in result:
                finding(view, 'AOQ–PO line correspondence', MISSING,
                        'Missing or duplicate original AOQ item IDs prevent safe line matching. Descriptions and row positions are not used to guess correspondence.',
                        reference(side, record, ('winning_offer' if side == 'aoq' else 'items') + f'[{index}]._AOQ Item ID'))
                return
            result[identity] = (index, row)
        maps.append(result)
    a, p = maps
    finding(view, 'AOQ–PO item coverage', PASS if a.keys() == p.keys() else FAIL,
            'Compared the preserved original AOQ item IDs in both documents.',
            reference('aoq', record, 'winning_offer'), reference('po', record, 'items'))
    for identity in sorted(a.keys() & p.keys()):
        ai, ar = a[identity]
        pi, pr = p[identity]
        refs = [reference('aoq', record, f'winning_offer[{ai}]'), reference('po', record, f'items[{pi}]')]
        if type(ar.get('unit')) is not str or not ar['unit'].strip() or ar.get('unit') != pr.get('unit'):
            finding(view, 'AOQ–PO price comparison', MISSING, 'Units are missing or differ; no unit-price equivalence is inferred.', *[r + '.unit' for r in refs])
            continue
        for key, label in (('qty', 'quantity'), ('unit_cost', 'unit price'), ('amount', 'line amount')):
            left, right = decimal_text(ar.get(key)), decimal_text(pr.get(key))
            if left is None or right is None:
                finding(view, 'AOQ–PO ' + label, MISSING, 'Comparable stored numeric evidence is missing or invalid.', *[r + '.' + key for r in refs])
            else:
                finding(view, 'AOQ–PO ' + label, PASS if Decimal(left) == Decimal(right) else FAIL,
                        'Approved AOQ stored value: ' + left + '; PO stored value: ' + right + '. This tests copy consistency, not market value or tax equivalence.',
                        *[r + '.' + key for r in refs])


def build_for_review(connection_factory, nlp_id, user_session):
    """Narrow authorized reader; all statements run in one read-only transaction.

    Existing NLP policy allows Admin, Staff and Inspector authenticated users to
    view all NLP records. No record/office ACL exists in inspected source/schema;
    no broader scope is granted. Current account status/role are rechecked here.
    """
    if (user_session.get('logged_in') is not True or type(user_session.get('user_id')) is not int
            or user_session['user_id'] <= 0 or type(nlp_id) is not int or nlp_id <= 0):
        return empty('Pricing evidence is unavailable for this session or record.')
    con = None
    view = None
    try:
        con = connection_factory()
        con.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
        con.execute("SET LOCAL statement_timeout = '2000ms'")
        con.execute("SET LOCAL lock_timeout = '500ms'")
        mode = con.execute('SHOW transaction_read_only').fetchone()
        if not mode or mode.get('transaction_read_only') != 'on':
            raise EvidenceError('Read-only mode not confirmed.')
        account = con.execute('select id,role,status from users where id=?', (user_session['user_id'],)).fetchone()
        if (not account or account.get('status') != 'Active'
                or account.get('role') not in ('Admin', 'Staff', 'Inspector')):
            view = empty('Pricing evidence is unavailable for this session or record.')
        else:
            record = con.execute(RECORD_SQL, (nlp_id,)).fetchone()
            view = adapt_record(dict(record)) if record else empty('Pricing evidence is unavailable for this session or record.')
    except Exception:
        # Do not expose driver messages, source values, credentials or SQL params.
        view = empty('Pricing diagnostics are temporarily unavailable. Official NLP review is unchanged.')
    finally:
        if con is not None:
            try:
                con.rollback()
            except Exception:
                view = empty('Pricing diagnostics are temporarily unavailable. Official NLP review is unchanged.')
            finally:
                try:
                    con.close()
                except Exception:
                    view = empty('Pricing diagnostics are temporarily unavailable. Official NLP review is unchanged.')
    return view
