"""Parent-serialized child creation. Call only through authenticated Flask routes.

Number allocation, child insertion and action audit share one transaction. The
parent lock is acquired before a separate child-existence statement, so a
request waiting for another creator observes its committed child. Inference
runs outside the transaction, followed by locked source revalidation.
"""
from contextlib import contextmanager
import json
import re
import unicodedata
from flask import abort, flash, redirect, url_for

RELATIONSHIPS = {('app_plan','pr','app_id'), ('aoq','po','aoq_id'),
                 ('po','nlp','po_id'), ('po','iar','po_id')}


@contextmanager
def locked_parent(api, table, identifier, child, foreign_key):
    if (table, child, foreign_key) not in RELATIONSHIPS:
        raise ValueError('Unsupported procurement relationship.')
    if not re.fullmatch(r'[1-9][0-9]{0,18}', str(identifier or '')) or int(identifier) > 9223372036854775807:
        abort(400, description='Select a valid source document.')
    with api['durable_ocr'].transaction(api, read_committed=True) as (con, uid):
        if table=='aoq':
            parent = con.execute('select * from psms_aoq_api.lock_aoq_for_workflow_v1(?::bigint,?::text)',
                                 (identifier,'po')).fetchone()
        elif table=='po':
            import revised_procurement_v1 as revised_procurement
            if revised_procurement.enabled(api):
                try:
                    parent=con.execute('select * from '+revised_procurement.api_schema()+'.lock_po_for_workflow_v2(?::bigint,?::text)',(identifier,child)).fetchone()
                except Exception as error:
                    state=getattr(error,'sqlstate',None)
                    if state in ('PA002','PA003','PA004'):
                        abort({'PA002':409,'PA003':403,'PA004':404}[state],description='Receiving requires an authorized approved PO and its reviewed verification.')
                    raise
            else:
                parent=con.execute('select * from po where id=? for update',(identifier,)).fetchone()
        else:
            parent = con.execute(f'select * from {table} where id=? for update', (identifier,)).fetchone()
        existing = (con.execute(f'select id,no from {child} where {foreign_key}=? order by id limit 1',
                               (identifier,)).fetchone() if parent else None)
        yield con, parent, existing


def create_pr(api, identifier, purpose=None):
    import revised_procurement_v1 as revised_procurement
    if revised_procurement.enabled(api):
        return revised_procurement.create_pr(api,identifier,purpose)
    purpose = '' if purpose is None else purpose
    if (not isinstance(purpose, str) or len(purpose) > 2000
            or any(unicodedata.category(char) == 'Cc' and char not in '\n\r\t' for char in purpose)):
        flash('Enter a purpose of at most 2,000 characters without unsupported control characters.')
        return redirect(url_for('pr'))
    purpose = purpose.strip()
    with locked_parent(api, 'app_plan', identifier, 'pr', 'app_id') as (con, row, existing):
        if existing or not row or row['status'] != 'Approved':
            flash('Select an approved APP without an existing Purchase Request. No duplicate was created.')
            return redirect(url_for('pr'))
        source = con.execute('select no,items from ppmp where id=? for share', (row['ppmp_id'],)).fetchone()
        if not source:abort(409, description='The source PPMP is unavailable.')
        items = api['parse_items'](row.get('items') or source['items'])
        errors = api['planning_item_errors'](items) if items else ['No approved APP items were found.']
        if errors:
            flash('Cannot create Purchase Request: '+' '.join(errors))
            return redirect(url_for('pr'))
        no = api['next_document_no']('PR','PR','pr',year=row.get('year') or api['current_year'](),connection=con)
        pr_items = api['assign_item_identifiers'](items,'_PR Item ID',no)
        total = api['items_total'](items)
        if total is None or total <= 0:total = api['parse_currency_decimal'](row['total'])
        if total is None or total <= 0:abort(422, description='The approved APP total is invalid.')
        con.execute('''insert into pr(no,app_id,ppmp_id,office,purpose,status,created_at,items,total)
            values(?,?,?,?,?,'For Review',?,?,?)''',
            (no,row['id'],row['ppmp_id'],row['office'],purpose or api['source_pr_purpose'](items),api['now'](),
             json.dumps(pr_items,default=str),total))
        api['log']('Created PR','PR',no,f'Created from approved APP {row["no"]}; source PPMP {source["no"]}; {len(items)} item(s)',connection=con)
    flash(f'Purchase Request {no} created from approved APP {row["no"]}.')
    return redirect(url_for('pr'))


def create_po(api,identifier):
    import revised_procurement_v1 as revised_procurement
    if revised_procurement.enabled(api):
        return revised_procurement.create_po(api,identifier)
    abort(409, description='PO creation requires the authorized revised workflow. The legacy creation path is retired.')


def create_nlp(api,identifier):
    abort(409, description='Select an Approved PR and PO For Review in the authorized revised workflow. Legacy post-approval NLP creation is retired.')


def create_iar(api, identifier):
    import revised_procurement_v1 as revised_procurement
    secure_receiving=revised_procurement.enabled(api)
    with locked_parent(api,'po',identifier,'iar','po_id') as (con,row,existing):
        eligible=None
        if row and not existing:
            if secure_receiving:
                # The restricted parent helper validated and share-locked the
                # approved NLP evidence in this same transaction. The backend
                # deliberately has no NLP UPDATE privilege for a direct lock.
                eligible=row
            else:
                eligible=con.execute("""select id from nlp where po_id=? and status='Approved'
                    and result in ('Acceptable','Needs Review') for share""",(identifier,)).fetchone()
        if existing or not row or row['status']!='Approved' or not eligible:
            flash('The selected PO is not eligible for IAR creation or already has an IAR.')
            return redirect(url_for('iar'))
        no=api['next_document_no']('IAR','IAR','iar',connection=con)
        items=api['assign_item_identifiers'](api['parse_items'](row['items']),'_IAR Item ID',no)
        for item in items:
            item['_Receiving Contract Version']=1
            item['ordered_qty']=api['value_any'](item,'qty','Quantity',default='')
            item['received_qty']=''
        con.execute('insert into iar(no,po_id,status,created_at,items) values(?,?,?,?,?)',
                    (no,row['id'],'For Review',api['now'](),json.dumps(items,default=str)))
        api['log']('Created IAR','IAR',no,'IAR created for review; inventory is updated only after approval',connection=con)
    flash('IAR created. Approve actual receiving before adding inventory.')
    return redirect(url_for('iar'))
