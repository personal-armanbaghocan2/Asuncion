"""Parent-serialized child creation. Call only through authenticated Flask routes.

Number allocation, child insertion and action audit share one transaction. The
parent lock is acquired before a separate child-existence statement, so a
request waiting for another creator observes its committed child. Inference
runs outside the transaction, followed by locked source revalidation.
"""
from contextlib import contextmanager
import json
import re
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
        parent = con.execute(f'select * from {table} where id=? for update', (identifier,)).fetchone()
        existing = (con.execute(f'select id,no from {child} where {foreign_key}=? order by id limit 1',
                               (identifier,)).fetchone() if parent else None)
        yield con, parent, existing


def create_pr(api, identifier):
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
            (no,row['id'],row['ppmp_id'],row['office'],api['source_pr_purpose'](items),api['now'](),
             json.dumps(pr_items,default=str),total))
        api['log']('Created PR','PR',no,f'Created from approved APP {row["no"]}; source PPMP {source["no"]}; {len(items)} item(s)',connection=con)
    flash(f'Purchase Request {no} created from approved APP {row["no"]}.')
    return redirect(url_for('pr'))


def create_po(api, identifier):
    from aoq_flexible import winning_offer_error, winning_total_error, reviewed_total, review_number
    with locked_parent(api,'aoq',identifier,'po','aoq_id') as (con,row,existing):
        if existing or not row or row['status'] != 'Approved':
            flash('Select an approved AOQ without an existing PO. No duplicate was created.')
            return redirect(url_for('po'))
        # Correct rule: PO is generated from the approved OCR-created AOQ winning offer.
        # It must NOT copy the PR items, otherwise NLP compares PR against itself.
        items=api['parse_items'](row['winning_offer'])
        error=winning_offer_error(items) or winning_total_error(items,row.get('winning_amount'))
        if not error and (not isinstance(row.get('winner'),str) or not row['winner'].strip()):
            error='The AOQ has no official winning supplier.'
        if error:
            flash(error)
            return redirect(url_for('po'))
        items=[dict(item,**{field:review_number(item[field]) for field in ('qty','unit_cost','amount')}) for item in items]
        no=api['next_document_no']('PO','PO','po',connection=con)
        items=api['assign_item_identifiers'](items,'_PO Item ID',no)
        con.execute('insert into po(no,aoq_id,supplier,status,created_at,items,total) values(?,?,?,?,?,?,?)',
                    (no,row['id'],row['winner'],'For Review',api['now'](),json.dumps(items,default=str),round(reviewed_total(items),2)))
        api['log']('Generated PO','PO',no,f'from approved OCR AOQ {row["no"]}; {len(items)} winning offer item(s)',connection=con)
    flash('PO generated successfully.')
    return redirect(url_for('po'))


def create_nlp(api, identifier):
    # Do not keep database locks or a connection open during model execution.
    if not re.fullmatch(r'[1-9][0-9]{0,18}', str(identifier or '')) or int(identifier)>9223372036854775807:
        abort(400, description='Select a valid source document.')
    source=api['q']('''select p.*,aq.pr_id,r.no pr_no,r.items pr_items from po p
        join aoq aq on aq.id=p.aoq_id join pr r on r.id=aq.pr_id
        where p.id=? and p.status='Approved'
          and not exists(select 1 from nlp n where n.po_id=p.id)''',(identifier,),one=True)
    if not source:
        flash('Select an approved PO for verification.')
        return redirect(url_for('nlp'))
    try:
        result,confidence,details=api['nlp_predict'](api['parse_items'](source['pr_items']),api['parse_items'](source['items']))
    except Exception:
        api['log']('PR–PO NLP Check Failed','NLP',source['no'],'Model verification failed; no fallback result was created.')
        flash('NLP verification stopped. No fallback or hardcoded result was created.')
        return redirect(url_for('nlp'))
    with locked_parent(api,'po',identifier,'nlp','po_id') as (con,current,existing):
        if existing:
            flash('This PO already has an NLP verification. No duplicate was created.')
            return redirect(url_for('nlp'))
        linked=con.execute('''select aq.pr_id,r.no pr_no,r.items pr_items from aoq aq
            join pr r on r.id=aq.pr_id where aq.id=? for share of aq,r''',
            (current['aoq_id'],)).fetchone() if current else None
        if (not current or current['status']!='Approved' or not linked or
                any(current.get(key)!=source.get(key) for key in ('items','aoq_id')) or
                any(linked.get(key)!=source.get(key) for key in ('pr_id','pr_items'))):
            abort(409, description='Source documents changed during inference. Reopen and verify again.')
        no=api['next_document_no']('NLP','NLP','nlp',connection=con)
        con.execute('''insert into nlp(no,po_id,result,confidence,staff_decision,remarks,created_at,details,status)
            values(?,?,?,?,?,?,?,?,?)''',(no,current['id'],result,confidence,result,
            api['nlp_dynamic_remarks'](result,details),api['now'](),json.dumps(details,default=str),'For Review'))
        api['log']('PR–PO FastText NLP Check Completed','NLP',no,
                   f'PR {linked["pr_no"]} versus PO {current["no"]}: {result} {confidence}%',connection=con)
    flash(f'FastText NLP verification {no} completed. Human review remains required.')
    return redirect(url_for('nlp'))


def create_iar(api, identifier):
    with locked_parent(api,'po',identifier,'iar','po_id') as (con,row,existing):
        eligible=con.execute("""select id from nlp where po_id=? and status='Approved'
            and result in ('Acceptable','Needs Review') for share""",(identifier,)).fetchone() if row and not existing else None
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
