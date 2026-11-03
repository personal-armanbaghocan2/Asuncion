"""Synthetic RIS/ICS route tests. No app initialization or database access."""
import ast
from contextlib import contextmanager
from copy import deepcopy
from decimal import Decimal, InvalidOperation
import json
from pathlib import Path
import re
import sys
import threading
from types import SimpleNamespace
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import request_queries


def issue(inventory_id=1, qty=2):
    return dict(inventory_id=inventory_id, qty=qty, item_code=f'INV-{inventory_id}',
                item='Synthetic stock', unit='piece',
                **{'_Issue Item ID': f'ISSUE-ITEM-{inventory_id}'})


def load_routes(connect, *, form=None):
    nodes = {n.name: n for n in ast.parse((ROOT / 'app.py').read_text(encoding='utf-8-sig')).body
             if isinstance(n, ast.FunctionDef)}
    messages, numbers = [], []
    class Form(dict):
        def getlist(self, name):
            value = self.get(name, [])
            return value if isinstance(value, list) else [value]
    def number(*args, **kwargs):
        numbers.append(args)
        return 'RIS-2026-TEST'
    @contextmanager
    def transaction(api):
        connection=connect()
        try:
            yield connection,1
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()
    ns = dict(json=json, re=re, Decimal=Decimal, InvalidOperation=InvalidOperation,
              request_queries=request_queries,
              conn=connect, now=lambda: 'test time', session={'username': 'reviewer'},
              request=SimpleNamespace(method='POST', form=Form(form or {'review_confirm': 'yes'})),
              flash=messages.append, redirect=lambda value: value,
              url_for=lambda endpoint, **kw: (endpoint, kw), next_document_no=number)
    ns['durable_ocr']=SimpleNamespace(transaction=transaction)
    pending, selected = ['ris_ics', 'ris_ics_approve'], {}
    while pending:
        name = pending.pop()
        if name in ns or name in selected or name not in nodes:
            continue
        node = deepcopy(nodes[name])
        node.decorator_list = []
        selected[name] = node
        pending.extend(n.id for n in ast.walk(node) if isinstance(n, ast.Name))
    exec(compile(ast.Module(body=list(selected.values()), type_ignores=[]),
                 'app.py (isolated RIS/ICS functions)', 'exec'), ns)
    return ns, messages, numbers


class Cursor:
    def __init__(self, row=None, rowcount=1):
        self.row, self.rowcount = row, rowcount
    def fetchone(self):
        return deepcopy(self.row)
    def fetchall(self):
        return [deepcopy(self.row)] if self.row is not None else []


class FakeDatabase:
    def __init__(self, *, typ='RIS', items=None, fail=None, concurrent=False):
        self.docs = {7: dict(id=7, no=f'{typ}-TEST-7', type=typ, office='Test office',
                            status='For Review', items=json.dumps(items if items is not None else [issue()]))}
        self.stock = {i: dict(id=i, item_code=f'INV-{i}', item='Synthetic stock', description='Spec',
                             category='Equipment' if typ == 'ICS' else 'Consumable', qty=10.0,
                             unit='piece', unit_cost=5, status='Available', source_po='PO-TEST') for i in (1, 2)}
        self.movements, self.audits, self.queries, self.connections = [], [], [], []
        self.fail, self.commits, self.rollbacks = fail, 0, 0
        self.mutex = threading.RLock()
        self.locks = {(table, i): threading.RLock() for table in ('doc', 'stock') for i in (1, 2, 7, 8)}
        self.doc_barrier = threading.Barrier(2) if concurrent else None
        self.stock_barrier = threading.Barrier(2) if concurrent else None
    def connect(self):
        con = FakeConnection(self)
        self.connections.append(con)
        return con
    def snapshot(self):
        return deepcopy((self.docs, self.stock, self.movements, self.audits))


class FakeConnection:
    def __init__(self, db):
        self.db, self.closed = db, False
        self.docs, self.stock, self.movements, self.audits, self.held = {}, {}, [], [], []
    def execute(self, sql, params=()):
        sql = ' '.join(sql.lower().split())
        self.db.queries.append((sql, tuple(params)))
        if sql == 'begin immediate':
            return Cursor()  # db.py translates this to plain BEGIN, without locks.
        if sql.startswith('select * from ris_ics') or sql.startswith('select * from inventory'):
            table = 'doc' if 'from ris_ics' in sql else 'stock'
            key = params[0]
            if 'for update' in sql:
                lock = self.db.locks[table, key]
                if lock not in self.held:
                    lock.acquire()
                    self.held.append(lock)
            source = self.db.docs if table == 'doc' else self.db.stock
            pending = self.docs if table == 'doc' else self.stock
            with self.db.mutex:
                row = deepcopy(pending.get(key, source.get(key)))
            barrier = self.db.doc_barrier if table == 'doc' else self.db.stock_barrier
            if barrier and 'for update' not in sql:
                barrier.wait(timeout=3)  # Force the legacy stale-read race.
            if row and len(params) > 1 and (row['category'] != params[1] or row['qty'] <= 0):
                row = None
            return Cursor(row)
        if sql.startswith('update inventory set qty='):
            stage = 'stock'
            if self.db.fail == stage:
                raise RuntimeError('synthetic stock failure')
            if self.db.fail == 'no_stock':
                return Cursor(rowcount=0)
            qty, status, key = params
            self.stock[key] = dict(self.stock.get(key, self.db.stock[key]), qty=qty, status=status)
        elif sql.startswith('insert into inventory_transactions'):
            if self.db.fail == 'movement' or (self.db.fail == 'second_movement' and self.movements):
                raise RuntimeError('synthetic movement failure')
            if self.db.fail == 'no_movement':
                return Cursor(rowcount=0)
            self.movements.append(tuple(params))
        elif sql.startswith('update ris_ics set status='):
            if self.db.fail == 'status':
                raise RuntimeError('synthetic status failure')
            if self.db.fail == 'no_status':
                return Cursor(rowcount=0)
            self.docs[params[1]] = dict(self.db.docs[params[1]], status=params[0])
        elif sql.startswith('insert into audit_trail'):
            if self.db.fail == 'audit':
                raise RuntimeError('synthetic audit failure')
            if self.db.fail == 'no_audit':
                return Cursor(rowcount=0)
            self.audits.append(tuple(params))
        elif sql.startswith('insert into ris_ics'):
            no, typ, office, status, created, items = params
            self.docs[8] = dict(id=8, no=no, type=typ, office=office, status=status, created_at=created, items=items)
        else:
            raise AssertionError('Unexpected SQL: ' + sql)
        return Cursor({'id': 8})
    def commit(self):
        if self.db.fail == 'commit':
            raise RuntimeError('synthetic commit failure')
        with self.db.mutex:
            self.db.docs.update(deepcopy(self.docs))
            self.db.stock.update(deepcopy(self.stock))
            self.db.movements.extend(self.movements)
            self.db.audits.extend(self.audits)
            self.db.commits += 1
        self.clear()
    def clear(self):
        self.docs, self.stock, self.movements, self.audits = {}, {}, [], []
        while self.held:
            self.held.pop().release()
    def rollback(self):
        self.db.rollbacks += 1
        self.clear()
    def close(self):
        self.clear()
        self.closed = True


class RISICSAtomicTests(unittest.TestCase):
    def approve(self, db, doc_id=7, form=None):
        routes, messages, _ = load_routes(db.connect, form=form)
        routes['ris_ics_approve'](doc_id)
        return messages
    def test_success_preserves_receipt_details_and_commits_once(self):
        for typ in ('RIS', 'ICS'):
            with self.subTest(type=typ):
                db = FakeDatabase(typ=typ, items=[issue(qty=1)])
                original = db.docs[7]['items']
                self.approve(db)
                self.assertEqual(db.stock[1]['qty'], 9)
                self.assertEqual(db.docs[7]['status'], 'Approved')
                self.assertEqual(db.docs[7]['items'], original)
                self.assertEqual(db.movements, [(1, f'{typ}-TEST-7', typ, 1.0, 'piece', 'Test office', 'test time', 'reviewer')])
                self.assertEqual(db.audits, [('test time', 'reviewer', f'Approved {typ}', 'RIS/ICS', f'{typ}-TEST-7', 'Inventory quantities deducted for 1 item(s)')])
                self.assertEqual(db.commits, 1)
                self.assertEqual(len(db.connections), 1)
                self.assertTrue(all(c.closed for c in db.connections))
    def test_exhausted_status_matches_existing_ris_and_ics_responsibilities(self):
        for typ, status in (('RIS', 'Issued'), ('ICS', 'Assigned')):
            db = FakeDatabase(typ=typ, items=[issue(qty=1)])
            db.stock[1]['qty'] = 1
            self.approve(db)
            self.assertEqual((db.stock[1]['qty'], db.stock[1]['status']), (0, status))
    def test_each_write_and_commit_failure_rolls_back_everything(self):
        for stage in ('stock', 'movement', 'second_movement', 'status', 'audit', 'commit',
                      'no_stock', 'no_movement', 'no_status', 'no_audit'):
            with self.subTest(stage=stage):
                db = FakeDatabase(items=[issue(), issue(2)], fail=stage)
                before = db.snapshot()
                self.approve(db)
                self.assertEqual(db.snapshot(), before)
                self.assertEqual(db.commits, 0)
                self.assertTrue(all(c.closed for c in db.connections))
    def test_insufficient_stock_late_in_document_has_no_partial_writes(self):
        db = FakeDatabase(items=[issue(), issue(2, 11)])
        before = db.snapshot()
        self.approve(db)
        self.assertEqual(db.snapshot(), before)
        self.assertFalse(any(sql.startswith('update inventory') for sql, _ in db.queries))
    def test_repeat_approval_does_not_deduct_or_log_again(self):
        db = FakeDatabase()
        self.approve(db)
        before = db.snapshot()
        self.approve(db)
        self.assertEqual(db.snapshot(), before)
    def test_confirmation_required(self):
        db = FakeDatabase()
        before = db.snapshot()
        self.approve(db, form={'review_confirm': 'no'})
        self.assertEqual(db.snapshot(), before)
        self.assertEqual(db.commits, 0)
    def test_missing_and_already_approved_documents_have_no_writes(self):
        db = FakeDatabase()
        self.approve(db, 8)
        self.assertEqual(db.commits, 0)
        db.docs[7].update(status='Approved', items='legacy invalid JSON')
        before = db.snapshot()
        self.approve(db)
        self.assertEqual(db.snapshot(), before)
        self.assertTrue(all(c.closed for c in db.connections))
    def test_fractional_ris_and_ics_quantities_remain_supported(self):
        for typ in ('RIS', 'ICS'):
            db = FakeDatabase(typ=typ, items=[issue(qty=0.25)])
            self.approve(db)
            self.assertEqual(db.stock[1]['qty'], 9.75)
            self.assertEqual(db.movements[0][3], 0.25)
    def test_malformed_or_invalid_items_are_blocked_before_writes(self):
        cases = ['bad JSON', '[]', '{}', '[null]', '[1]']
        cases += [json.dumps([issue(qty=q)]) for q in ('', None, 0, -1, True, '2 pieces', '1,,2', float('nan'), float('inf'))]
        cases += [json.dumps([issue(inventory_id=i)]) for i in ('', None, True, 1.5, '1junk', 0, -1)]
        cases += [json.dumps([issue(), issue()])]
        for raw in cases:
            with self.subTest(raw=raw):
                db = FakeDatabase()
                db.docs[7]['items'] = raw
                before = db.snapshot()
                self.approve(db)
                self.assertEqual(db.snapshot(), before)
                self.assertFalse(any(sql.startswith('update inventory') for sql, _ in db.queries))
    def test_current_inventory_category_and_identifier_are_rechecked(self):
        for change in ({'category': 'Equipment'}, {'item_code': 'DIFFERENT'}, {'qty': float('nan')}, {'unit': 'box'}):
            db = FakeDatabase()
            db.stock[1].update(change)
            self.approve(db)
            self.assertEqual(db.docs[7]['status'], 'For Review')
            self.assertEqual(db.movements, [])
    def test_ics_existing_one_unit_limit_is_enforced(self):
        db = FakeDatabase(typ='ICS', items=[issue(qty=2)])
        self.approve(db)
        self.assertEqual(db.stock[1]['qty'], 10)
        self.assertEqual(db.docs[7]['status'], 'For Review')
    def test_inventory_locks_are_sorted_but_movement_order_is_preserved(self):
        db = FakeDatabase(items=[issue(2), issue(1)])
        self.approve(db)
        locks = [params[0] for sql, params in db.queries if 'from inventory' in sql and 'for update' in sql]
        self.assertEqual(locks, [1, 2])
        self.assertEqual([m[0] for m in db.movements], [2, 1])
    def contend(self, db, ids):
        errors = []
        def run(i):
            try:
                self.approve(db, i)
            except Exception as exc:
                errors.append(exc)
        threads = [threading.Thread(target=run, args=(i,), daemon=True) for i in ids]
        for t in threads: t.start()
        for t in threads: t.join(6)
        self.assertFalse(any(t.is_alive() for t in threads))
        self.assertEqual(errors, [])
    def test_simultaneous_same_document_deducts_once(self):
        db = FakeDatabase(concurrent=True)
        self.contend(db, (7, 7))
        self.assertEqual(db.stock[1]['qty'], 8)
        self.assertEqual(len(db.movements), 1)
        self.assertEqual(len(db.audits), 1)
    def test_conflicting_documents_cannot_both_spend_the_same_stock(self):
        db = FakeDatabase(items=[issue(qty=7)], concurrent=True)
        db.docs[8] = dict(db.docs[7], id=8, no='RIS-TEST-8')
        self.contend(db, (7, 8))
        self.assertEqual(db.stock[1]['qty'], 3)
        self.assertEqual(sum(d['status'] == 'Approved' for d in db.docs.values()), 1)
        self.assertEqual(len(db.movements), 1)
        self.assertEqual(len(db.audits), 1)
    def test_creation_keeps_stock_unchanged_until_reviewed_approval(self):
        db = FakeDatabase()
        stock = deepcopy(db.stock)
        routes, _, numbers = load_routes(db.connect, form={'type': 'RIS', 'inventory_id': ['1'], 'issue_qty_1': '2'})
        routes['ris_ics']()
        self.assertEqual(db.stock, stock)
        self.assertEqual(db.movements, [])
        self.assertEqual(db.docs[8]['status'], 'For Review')
        self.assertEqual(len(numbers), 1)
    def test_invalid_direct_post_does_not_fabricate_or_clamp_quantities(self):
        for qty in ('', 'junk', '0', '-1', 'NaN', 'Infinity', '11'):
            with self.subTest(qty=qty):
                db = FakeDatabase()
                before = db.snapshot()
                routes, _, numbers = load_routes(db.connect, form={'type': 'RIS', 'inventory_id': ['1'], 'issue_qty_1': qty})
                routes['ris_ics']()
                self.assertEqual(db.snapshot(), before)
                self.assertEqual(numbers, [])
    def test_creation_rejects_duplicate_invalid_and_unavailable_selections(self):
        cases = [({'type': 'RIS', 'inventory_id': ['1', '1'], 'issue_qty_1': '2'}, 'RIS'),
                 ({'type': 'RIS', 'inventory_id': ['1junk'], 'issue_qty_1': '2'}, 'RIS'),
                 ({'type': 'RIS', 'inventory_id': ['1', '9'], 'issue_qty_1': '2'}, 'RIS'),
                 ({'type': 'ICS', 'inventory_id': ['1'], 'issue_qty_1': '2'}, 'ICS'),
                 ({'type': 'RIS', 'inventory_id': ['1'], 'issue_qty_1': '1'}, 'ICS')]
        for form, category_type in cases:
            with self.subTest(form=form):
                db = FakeDatabase(typ=category_type)
                before = db.snapshot()
                routes, _, numbers = load_routes(db.connect, form=form)
                routes['ris_ics']()
                self.assertEqual(db.snapshot(), before)
                self.assertEqual(numbers, [])


if __name__ == '__main__':
    unittest.main()
