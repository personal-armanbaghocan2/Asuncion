"""Read-only dashboard projections. No startup, model loading, or database writes."""
from collections import Counter
from datetime import datetime
import math


# Identifiers are fixed application tables/endpoints, never request parameters.
MODULES = (
    ('ppmp', 'PPMP', 'ppmp_home', 'ppmp_review'),
    ('app_plan', 'APP', 'app_plan', 'app_review'),
    ('pr', 'PR', 'pr', 'pr_review'),
    ('aoq', 'AOQ', 'aoq', 'aoq_review'),
    ('po', 'PO', 'po', 'po_review'),
    ('nlp', 'NLP', 'nlp', 'nlp_review'),
    ('iar', 'IAR', 'iar', 'iar_review'),
    ('ris_ics', 'RIS / ICS', 'ris_ics', 'ris_ics_review'),
)
PALETTE = ['#428fff', '#ffbf32', '#ff882d', '#22a568', '#ff4865', '#a775ff', '#60d6ef', '#96aaba']
STATUS_COLORS = {'Draft': '#428fff', 'For Review': '#ffbf32', 'Reviewed': '#ff882d',
                 'Approved': '#22a568', 'Blocked': '#ff4865'}


def timestamp(value):
    """Accept only full, recognized dates; never sort legacy dates lexically."""
    if isinstance(value, datetime):
        parsed = value
    else:
        text = str(value or '').strip()
        if not text:
            return None
        try:
            parsed = datetime.fromisoformat(text.replace('Z', '+00:00'))
        except ValueError:
            parsed = None
            for fmt in ('%b %d, %Y %I:%M %p', '%B %d, %Y %I:%M %p'):
                try:
                    parsed = datetime.strptime(text, fmt)
                    break
                except ValueError:
                    continue
            if parsed is None:
                return None
    # Aware imported timestamps are converted to the application's local clock.
    return parsed.astimezone().replace(tzinfo=None) if parsed.tzinfo else parsed


def segments(counts, colors=None):
    total = sum(counts.values())
    return [dict(label=label, count=count, percent=round(count * 100 / total, 1) if total else 0,
                 color=(colors or {}).get(label, PALETTE[i % len(PALETTE)]))
            for i, (label, count) in enumerate(counts.items())]


def build_dashboard(query, today=None):
    """Use the existing q() PostgreSQL layer; project only needed public fields."""
    today = today or datetime.now()
    data, pipeline, reviews = {}, [], []
    statuses = {'all': Counter()}
    monthly = {label: [0] * 12 for label in ('PPMP', 'APP', 'PR', 'PO', 'Others')}
    invalid_dates = 0
    for table, label, endpoint, review_endpoint in MODULES:
        party = 'office' if table in ('ppmp', 'app_plan', 'pr', 'ris_ics') else 'winner' if table == 'aoq' else 'supplier' if table == 'po' else 'null'
        amount = 'winning_amount' if table == 'aoq' else 'total' if table in ('ppmp', 'app_plan', 'pr', 'po') else 'null'
        extra = ', result, po_id' if table == 'nlp' else ', po_id' if table == 'iar' else ''
        rows = [dict(r) for r in query(f'select id, no, status, created_at, {party} as party, {amount} as amount{extra} from {table}')]
        data[table] = rows
        pipeline.append(dict(label=label, count=len(rows), endpoint=endpoint, kind='documents'))
        statuses[label] = Counter()
        for row in rows:
            status = str(row.get('status') or '').strip() or 'Not recorded'
            statuses[label][status] += 1
            statuses['all'][status] += 1
            created = timestamp(row.get('created_at'))
            if created and created <= today and created.year == today.year:
                monthly[label if label in monthly else 'Others'][created.month - 1] += 1
            elif not created or created > today:
                invalid_dates += 1
            # Draft PPMPs have an explicit review step. Blocked/unknown statuses
            # are corrective work, not pending approval; don't advertise them here.
            actionable = status in ('Draft', 'Reviewed') if table == 'ppmp' else status == 'For Review'
            if actionable:
                reviews.append(dict(row, module=label, endpoint=review_endpoint,
                                    date=created.strftime('%b %d, %Y') if created and created <= today else None,
                                    days=(today.date() - created.date()).days if created and created <= today else None,
                                    sort_date=created if created and created <= today else datetime.min))
    # NLP/IAR review routes require a surviving PO (inner join in app.py).
    purchase_orders = {row['id']: row for row in data['po']}
    reviews = [r for r in reviews if r['module'] not in ('NLP', 'IAR') or r.get('po_id') in purchase_orders]
    for row in reviews:
        if row['module'] in ('NLP', 'IAR'):
            row['party'] = purchase_orders[row['po_id']].get('party')
    inventory = [dict(r) for r in query('select category, qty from inventory')]
    stock = Counter({'In Stock': 0, 'Low Stock': 0, 'Out of Stock': 0})
    categories = Counter()
    for row in inventory:
        try:
            qty = float(row.get('qty') or 0)
        except (ValueError, TypeError):
            qty = 0
        if not math.isfinite(qty):
            qty = 0
        stock['Out of Stock' if qty <= 0 else 'Low Stock' if qty <= 5 else 'In Stock'] += 1
        categories[str(row.get('category') or '').strip() or 'Not recorded'] += 1
    pipeline.insert(7, dict(label='Inventory', count=len(inventory), endpoint='inventory', kind='inventory records'))
    nlp = Counter({key: 0 for key in ('Acceptable', 'Needs Review', 'Needs Recanvass', 'Reject')})
    ungrouped_nlp = 0
    for row in data['nlp']:
        if row.get('result') in nlp:
            nlp[row['result']] += 1
        else:
            ungrouped_nlp += 1
    recent = []
    for row in query('select id, dt, "user", module, action from audit_trail'):
        created = timestamp(row.get('dt'))
        if created and created <= today:
            recent.append(dict(row, sort_date=created, date=created.strftime('%b %d, %Y %I:%M %p')))
    recent.sort(key=lambda r: (r['sort_date'], r['id']), reverse=True)
    reviews.sort(key=lambda r: (r['sort_date'], r['id']))
    # Don't serialize datetime objects into the chart payload.
    for row in reviews + recent:
        row.pop('sort_date', None)
    return dict(
        pipeline=pipeline, review_rows=reviews, recent=recent[:5],
        kpis=[len(data['ppmp']), len(reviews), statuses['all']['Approved'], stock['Low Stock']],
        statuses={key: segments(counts, STATUS_COLORS) for key, counts in statuses.items()},
        inventory=dict(total=len(inventory), stock=segments(stock, {'In Stock': '#22a568', 'Low Stock': '#ffbf32', 'Out of Stock': '#ff4865'}),
                       categories=segments(dict(sorted(categories.items())))),
        nlp=segments(nlp, dict(zip(nlp, ['#22a568', '#ffbf32', '#ffa15c', '#ff4865']))),
        ungrouped_nlp=ungrouped_nlp, monthly=monthly, year=today.year,
        invalid_dates=invalid_dates,
    )
