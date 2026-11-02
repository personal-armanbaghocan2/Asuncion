"""Isolated PPMP edit/approval/OCR guards; never import or initialize the app."""
import ast
from copy import deepcopy
from decimal import Decimal, InvalidOperation
import json
from pathlib import Path
import re
import sys
from types import SimpleNamespace
from unittest.mock import patch
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
BUDGET = 'Estimated Budget Allocation (Php)'
DESCRIPTION = 'General Description and Objective of the Project to be Procured'


def load_functions(names, **overrides):
    tree = ast.parse((ROOT / 'app.py').read_text(encoding='utf-8-sig'))
    functions = {n.name: n for n in tree.body if isinstance(n, ast.FunctionDef)}
    ns = dict(json=json, re=re, Decimal=Decimal, InvalidOperation=InvalidOperation)
    # Literal constants only; no top-level application/database/model execution.
    for node in tree.body:
        if isinstance(node, ast.Assign):
            try:
                value = ast.literal_eval(node.value)
            except (ValueError, TypeError):
                continue
            for target in node.targets:
                if isinstance(target, ast.Name):
                    ns[target.id] = value
    ns.update(overrides)
    pending, selected = list(names), {}
    while pending:
        name = pending.pop()
        if name in ns or name in selected or name not in functions:
            continue
        node = deepcopy(functions[name])
        node.decorator_list = []
        selected[name] = node
        pending.extend(n.id for n in ast.walk(node) if isinstance(n, ast.Name))
    exec(compile(ast.Module(body=list(selected.values()), type_ignores=[]),
                 'app.py (isolated PPMP definitions)', 'exec'), ns)
    return ns


class Cursor:
    def __init__(self, row=None, rows=None, rowcount=1):
        self.row, self.rows, self.rowcount = row, rows, rowcount
    def fetchone(self):
        return deepcopy(self.row)
    def fetchall(self):
        return deepcopy(self.rows if self.rows is not None else ([self.row] if self.row else []))


class PPMPProtectionTests(unittest.TestCase):
    def harness(self, *, status='Draft', downstream=False, race=None, form=None):
        row = dict(id=7, no='PPMP-DBM-2026-0007', type='DBM', status=status,
                   total=100, items=json.dumps([{DESCRIPTION:'Original', BUDGET:100,
                                                '_PPMP Item ID':'PPMP-DBM-2026-0007-ITEM-01'}]),
                   reviewed_by='reviewer', reviewed_at='old review', approved_by='approver', approved_at='old approval')
        events = dict(writes=[], logs=[], messages=[], queries=[])
        def query(sql, params=(), one=False):
            events['queries'].append(sql)
            if 'from app_plan' in sql:
                return {'id': 9} if downstream else []  # Exact q(one=True) contract.
            return deepcopy(row)
        def write(sql, params=()):
            if race:
                row['status'] = race
            normalized = ' '.join(sql.lower().split())
            if "status='draft'" in normalized and row['status'] != 'Draft':
                return 0
            if "status='reviewed'" in normalized and row['status'] != 'Reviewed':
                return 0
            if "<> 'approved'" in normalized and row['status'] == 'Approved':
                return 0
            if 'not exists' in normalized and downstream:
                return 0
            events['writes'].append((sql, params))
            if 'set items=' in normalized:
                row.update(items=params[0], total=params[1], status=params[2],
                           reviewed_by=None, reviewed_at=None, approved_by=None, approved_at=None)
            else:
                row['status'] = params[0]
            return 1
        ns = load_functions(['ppmp_update_saved_items','ppmp_approve','ppmp_mark_reviewed'],
            q=query, exec_db=write, request=SimpleNamespace(form=form or {'general':'Corrected','budget':'PHP 250.00','review_confirm':'yes'}),
            flash=events['messages'].append, redirect=lambda value:value,
            url_for=lambda endpoint, **kw:(endpoint,kw), session={'username':'reviewer'},
            log=lambda *args:events['logs'].append(args), now=lambda:'test time',
            audited_update=lambda sql,params,*audit: (write(sql,params) and (events['logs'].append(audit) or 1)),
            normalize_excel_row=lambda item, ptype:item)
        return ns, row, events

    def test_actual_query_helper_returns_empty_list_for_missing_single_row(self):
        ns = load_functions(['q'], conn=lambda:SimpleNamespace(
            execute=lambda *args:Cursor(rows=[]), close=lambda:None))
        self.assertEqual(ns['q']('select id from app_plan', one=True), [])

    def test_downstream_check_distinguishes_missing_and_existing_record(self):
        for result, expected in (([],False), ({'id':9},True)):
            ns = load_functions(['ppmp_has_downstream_app'], q=lambda *a, **kw:result)
            self.assertIs(ns['ppmp_has_downstream_app'](7), expected)

    def test_draft_and_reviewed_edit_without_app_preserve_number_and_item_id(self):
        for status in ('Draft','Reviewed'):
            with self.subTest(status=status):
                ns, row, events = self.harness(status=status)
                ns['ppmp_update_saved_items'](7)
                self.assertEqual(len(events['writes']), 1)
                self.assertEqual(row['status'], 'Draft')
                item = json.loads(row['items'])[0]
                self.assertEqual(item[DESCRIPTION], 'Corrected')
                self.assertEqual(item[BUDGET], 250)
                self.assertEqual(row['no'], 'PPMP-DBM-2026-0007')
                self.assertEqual(item['_PPMP Item ID'], row['no']+'-ITEM-01')
                self.assertIsNone(row['reviewed_by'])

    def test_existing_downstream_app_blocks_edit(self):
        ns, row, events = self.harness(downstream=True)
        before = deepcopy(row)
        ns['ppmp_update_saved_items'](7)
        self.assertEqual(row, before)
        self.assertEqual(events['writes'], [])
        self.assertEqual(events['logs'], [])

    def test_approved_direct_post_is_rejected_even_without_downstream_app(self):
        ns, row, events = self.harness(status='Approved')
        before = deepcopy(row)
        ns['ppmp_update_saved_items'](7)
        self.assertEqual(row, before)
        self.assertEqual(events['writes'], [])
        self.assertEqual(events['logs'], [])
        self.assertTrue(any('approved' in msg.lower() for msg in events['messages']))

    def test_approval_after_edit_read_cannot_be_overwritten(self):
        ns, row, events = self.harness(race='Approved')
        original_items = row['items']
        ns['ppmp_update_saved_items'](7)
        self.assertEqual(row['status'], 'Approved')
        self.assertEqual(row['items'], original_items)
        self.assertEqual(events['writes'], [])
        self.assertEqual(events['logs'], [])

    def test_invalid_budget_is_rejected_without_changes(self):
        ns, row, events = self.harness(form={'budget':'not money'})
        before = deepcopy(row)
        ns['ppmp_update_saved_items'](7)
        self.assertEqual(row,before)
        self.assertEqual(events['writes'], [])

    def test_existing_explicit_review_approval_stages_are_preserved(self):
        ns, row, events = self.harness()
        ns['ppmp_approve'](7)
        self.assertEqual(events['writes'], [])
        ns['ppmp_mark_reviewed'](7)
        self.assertEqual(row['status'], 'Reviewed')
        ns['ppmp_approve'](7)
        self.assertEqual(row['status'], 'Approved')
        self.assertEqual(len(events['writes']), 2)

    def test_approval_requires_confirmation_and_preserves_approved_records(self):
        for status, form in (('Reviewed',{'review_confirm':'no'}), ('Approved',{'review_confirm':'yes'})):
            ns, row, events = self.harness(status=status, form=form)
            ns['ppmp_approve'](7)
            ns['ppmp_mark_reviewed'](7)
            self.assertEqual(events['writes'], [])

    def test_stale_review_and_approval_requests_do_not_override_current_status(self):
        for route, status, changed in (('ppmp_mark_reviewed','Draft','Approved'),
                                      ('ppmp_approve','Reviewed','Draft')):
            ns, row, events = self.harness(status=status, race=changed)
            ns[route](7)
            self.assertEqual(row['status'], changed)
            self.assertEqual(events['writes'], [])
            self.assertEqual(events['logs'], [])

    def repair_harness(self, status='Draft', race=False):
        record = dict(id=7, status=status, items=json.dumps([{DESCRIPTION:'Original',BUDGET:100}]), total=100)
        draft = dict(id=3, official_ppmp_id=7, ppmp_no='PPMP-TEST', ppmp_type='DBM', ppmp_status=status,
                     ppmp_items=record['items'], ppmp_total=100, reviewed=True,
                     fields_json=json.dumps({DESCRIPTION:'Changed OCR draft',BUDGET:250}))
        events = []
        class Con:
            def execute(self, sql, params=()):
                normalized = ' '.join(sql.lower().split())
                if normalized.startswith('select'):
                    rows = [] if status == 'Approved' and "<> 'approved'" in normalized else [draft]
                    return Cursor(rows=rows)
                if normalized.startswith('update ppmp '):
                    if race:
                        record['status']='Approved'
                    if "<> 'approved'" in normalized and record['status']=='Approved':
                        return Cursor(rowcount=0)
                    record.update(items=params[0], total=params[1])
                events.append((sql,params))
                return Cursor()
        ns=load_functions(['repair_ocr_ppmps_from_reviewed_drafts'],
            normalize_reviewed_ocr_fields=lambda fields:fields,
            validated_ppmp_item_from_review=lambda fields, ptype:dict(fields),
            canonical_ppmp_fields=lambda item:{k:v for k,v in item.items() if not k.startswith('_')},
            OCR_PPMP_FIELDS={'budget':{'label':BUDGET}}, now=lambda:'test time')
        return ns, Con(), record, events

    def test_reviewed_ocr_draft_cannot_overwrite_linked_approved_ppmp(self):
        ns, con, row, events=self.repair_harness(status='Approved')
        before=deepcopy(row)
        self.assertEqual(ns['repair_ocr_ppmps_from_reviewed_drafts'](con),0)
        self.assertEqual(row,before)
        self.assertEqual(events,[])

    def test_ocr_repair_rechecks_approval_at_write_without_false_audit(self):
        ns, con, row, events=self.repair_harness(race=True)
        original=row['items']
        self.assertEqual(ns['repair_ocr_ppmps_from_reviewed_drafts'](con),0)
        self.assertEqual(row['items'],original)
        self.assertEqual(row['status'],'Approved')
        self.assertEqual(events,[])

    def test_existing_nonapproved_ocr_helper_behavior_remains_available(self):
        ns, con, row, events=self.repair_harness()
        self.assertEqual(ns['repair_ocr_ppmps_from_reviewed_drafts'](con),1)
        self.assertEqual(row['total'],250)
        self.assertEqual(row['status'],'Draft')
        self.assertEqual(len(events),2)

    def conversion(self, linked=False, reviewed=True):
        import durable_ocr_v0_1_0 as durable
        from flask import Flask
        from werkzeug.exceptions import BadRequest
        draft = dict(id=3, temp_code='OCR-TEST', status='REVIEWED', reviewed=reviewed,
                     official_ppmp_id=7 if linked else None, fields_json=json.dumps({BUDGET:250}),
                     converted=linked, revision=1, extraction_method='Synthetic text', source_filename='synthetic.txt',
                     average_confidence=.7, raw_ocr_text='SYNTHETIC SOURCE', reviewed_by='synthetic', reviewed_at='test', created_at='test',
                     confidence_json='{}')
        events=dict(writes=[], numbers=[], messages=[], commits=0, rollbacks=0)
        class Form(dict):
            def getlist(self,key): return self.get(key,[])
        class Con:
            def execute(self,sql,params=()):
                if sql.lower().startswith('select'):
                    if 'select no from ppmp' in sql:
                        return Cursor(row=dict(no='EXISTING-SYNTHETIC'))
                    return Cursor(row=draft)
                events['writes'].append((sql,params))
                return Cursor(row={'id':8})
            def commit(self): events['commits']+=1
            def rollback(self): events['rollbacks']+=1
            def close(self): pass
        def number(*args,**kw):
            events['numbers'].append(args)
            return 'PPMP-DBM-2026-NEW'
        ns=load_functions(['ppmp_type', '_json_dict', 'assign_item_identifiers'], conn=Con,
            available_excel_rows=lambda p:([],[],0), excel_meta=lambda:{},
            normalize_reviewed_ocr_fields=lambda fields:fields,
            validated_ppmp_item_from_review=lambda fields,p:dict(fields),
            current_year=lambda:2026, next_document_no=number,
            log=lambda *a,**kw:None,
            OCR_PPMP_FIELDS={'budget':{'label':BUDGET}},
            clear_ppmp_ocr_state=lambda:None, session={'username':'reviewer'},
            now=lambda:'test time', flash=events['messages'].append,
            redirect=lambda x:x, url_for=lambda endpoint,**kw:(endpoint,kw))
        app=Flask('synthetic-durable-conversion');app.secret_key='synthetic-test-secret-for-unit-context-only'
        app.add_url_rule('/ppmp', 'ppmp_home', lambda:'SYNTHETIC')
        with app.test_request_context('/ppmp/DBM', method='POST', data=dict(action='save_ocr_rows',confirm_reviewed='yes',
                ocr_rows=['OCR-TEST'], **{'ocr_revision_OCR-TEST':'1'})):
            durable.session.update(user_id=1,username='synthetic')
            with patch.object(durable,'_active',return_value=1):
                try:durable.ppmp_post(ns,'DBM')
                except BadRequest:pass
        return events

    def test_ocr_conversion_inserts_new_draft_without_updating_existing_ppmp(self):
        events=self.conversion()
        inserts=[(sql,p) for sql,p in events['writes'] if sql.startswith('insert into ppmp(')]
        self.assertEqual(len(inserts),1)
        self.assertIn("'Draft'",inserts[0][0])
        self.assertFalse(any(sql.lower().startswith('update ppmp set') for sql,_ in events['writes']))
        self.assertEqual(events['commits'],1)

    def test_linked_conversion_is_idempotent_and_unreviewed_draft_is_rejected(self):
        for changes in ({'linked':True},{'reviewed':False}):
            events=self.conversion(**changes)
            self.assertEqual(events['writes'],[])
            self.assertEqual(events['numbers'],[])
            self.assertEqual(events['rollbacks'],0 if changes.get('linked') else 1)


if __name__ == '__main__':
    unittest.main()
