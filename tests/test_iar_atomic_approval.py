"""IAR transaction regressions; AST-loaded production code, no database access."""
import ast
from copy import deepcopy
from decimal import Decimal, InvalidOperation
import json
from pathlib import Path
import re
import sys
from types import SimpleNamespace
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def item(**changes):
    value = dict(item='Synthetic laptop', description='Reviewed specification',
                 qty=2, unit='piece', unit_cost=125, _dataset_record_id=17)
    value.update(changes)
    if '_Inventory Category' not in changes:
        value['_Inventory Category'] = 'Equipment' if value['item']=='Synthetic laptop' else 'Consumable'
    return value


def load_routes(connect, *, confirm=True):
    """Load only function definitions, never app initialization or database config."""
    functions = {node.name: node for node in ast.parse(
        (ROOT / 'app.py').read_text(encoding='utf-8-sig')).body
        if isinstance(node, ast.FunctionDef)}
    messages = []
    namespace = dict(json=json, re=re, Decimal=Decimal, InvalidOperation=InvalidOperation,
                     conn=connect, now=lambda: 'synthetic time', current_year=lambda: 2026,
                     session={'username': 'synthetic reviewer'}, flash=messages.append,
                     request=SimpleNamespace(form={'review_confirm': 'yes' if confirm else 'no'}),
                     redirect=lambda value: value,
                     url_for=lambda endpoint, **kwargs: (endpoint, kwargs))
    pending = ['iar_approve', 'exec_db', 'log', 'next_document_no']
    selected = {}
    while pending:
        name = pending.pop()
        if name in namespace or name in selected or name not in functions:
            continue
        node = deepcopy(functions[name])
        node.decorator_list = []
        selected[name] = node
        pending.extend(n.id for n in ast.walk(node) if isinstance(n, ast.Name))
    exec(compile(ast.Module(body=list(selected.values()), type_ignores=[]),
                 'app.py (isolated IAR functions)', 'exec'), namespace)
    return namespace, messages


class Cursor:
    def __init__(self, row=None, rowcount=1):
        self.row = row
        self.rowcount = rowcount

    def fetchone(self):
        return deepcopy(self.row)

    def fetchall(self):
        return [deepcopy(self.row)] if self.row is not None else []


class FakeDatabase:
    def __init__(self, items=None, *, fail=None):
        self.state = dict(iar=dict(id=7, no='IAR-2026-0007', po_id=9,
                                  po_no='PO-2026-0009', status='For Review',
                                  items=json.dumps(items if items is not None else
                                                   [item(), item(item='Paper', qty=3)])),
                          inventory=[], audit=[], sequences={})
        self.fail = fail
        self.connections = []
        self.queries = []
        self.insert_attempts = 0
        self.commits = 0
        self.rollbacks = 0

    def connect(self):
        con = FakeConnection(self)
        self.connections.append(con)
        return con


class FakeConnection:
    def __init__(self, db):
        self.db = db
        self.pending = None
        self.closed = False

    def execute(self, sql, params=()):
        sql = ' '.join(sql.lower().split())
        self.db.queries.append((self, sql, params))
        if self.pending is None:
            self.pending = deepcopy(self.db.state)
        state = self.pending
        if sql.startswith('select i.*'):
            return Cursor(state['iar'])
        if sql.startswith('insert into identifier_sequence'):
            key = params[0]
            state['sequences'][key] = state['sequences'].get(key, 0) + 1
            return Cursor({'last_number': state['sequences'][key]})
        if sql.startswith('update identifier_sequence'):
            key = params[0]
            state['sequences'][key] += 1
            return Cursor({'last_number': state['sequences'][key]})
        if sql.startswith('select 1 as found from inventory'):
            return Cursor({'found': 1} if any(row[0] == params[0]
                          for row in state['inventory']) else None)
        if sql.startswith('insert into inventory('):
            self.db.insert_attempts += 1
            if self.db.fail == f'item{self.db.insert_attempts}':
                raise RuntimeError('synthetic inventory failure')
            if self.db.fail == 'no_inventory':
                return Cursor(rowcount=0)
            state['inventory'].append(tuple(params))
        elif sql.startswith('update iar set items='):
            state['iar']['items'] = params[0]
        elif sql.startswith('update iar set status='):
            if self.db.fail == 'status':
                raise RuntimeError('synthetic status failure')
            if self.db.fail == 'no_status':
                return Cursor(rowcount=0)
            state['iar']['status'] = params[0]
        elif sql.startswith('insert into audit_trail('):
            if self.db.fail == 'audit':
                raise RuntimeError('synthetic audit failure')
            if self.db.fail == 'no_audit':
                return Cursor(rowcount=0)
            state['audit'].append(tuple(params))
        else:
            raise AssertionError(f'Unexpected SQL: {sql}')
        return Cursor({'id': 1})

    def commit(self):
        if self.db.fail == 'commit':
            raise RuntimeError('synthetic commit failure')
        self.db.state = deepcopy(self.pending)
        self.pending = None
        self.db.commits += 1

    def rollback(self):
        self.pending = None
        self.db.rollbacks += 1

    def close(self):
        self.pending = None
        self.closed = True


class IARAtomicApprovalTests(unittest.TestCase):
    def test_actual_receipt_is_separate_and_committed_with_stock(self):
        receipt = item(qty=2, ordered_qty='2', received_qty='', **{'_Receiving Contract Version':1})
        db = FakeDatabase([receipt])
        routes, messages = load_routes(db.connect)
        routes['request'].form['received_qty_0'] = '1'
        routes['iar_approve'](7)
        saved = json.loads(db.state['iar']['items'])[0]
        self.assertEqual(saved['qty'], 2)
        self.assertEqual(saved['received_qty'], '1')
        self.assertEqual(db.state['inventory'][0][4], 1)
        self.assertEqual(db.commits, 1)

    def test_actual_receipt_and_stock_roll_back_when_audit_fails(self):
        receipt = item(qty=2, ordered_qty='2', received_qty='', **{'_Receiving Contract Version':1})
        db = FakeDatabase([receipt], fail='audit')
        before = deepcopy(db.state)
        routes, _ = load_routes(db.connect)
        routes['request'].form['received_qty_0'] = '1'
        with self.assertRaises(RuntimeError):
            routes['iar_approve'](7)
        self.assertEqual(db.state, before)
        self.assertEqual(db.commits, 0)

    def test_new_receipt_cannot_default_to_ordered_quantity(self):
        for value in ('', '0', '-1', '3', 'NaN'):
            db = FakeDatabase([item(ordered_qty='2', received_qty='', **{'_Receiving Contract Version':1})])
            before = deepcopy(db.state)
            routes, _ = load_routes(db.connect)
            routes['request'].form['received_qty_0'] = value
            routes['iar_approve'](7)
            self.assertEqual(db.state, before)
            self.assertEqual(db.insert_attempts, 0)

    def run_approval(self, db, **kwargs):
        routes, messages = load_routes(db.connect, **kwargs)
        routes['iar_approve'](7)
        return messages

    def test_success_is_one_transaction_with_original_receipts_and_audit(self):
        db = FakeDatabase()
        original = db.state['iar']['items']
        self.run_approval(db)
        self.assertEqual(db.commits, 1)
        self.assertEqual(len(db.connections), 1)
        self.assertTrue(all(con.closed for con in db.connections))
        self.assertIn('for update of i', db.queries[0][1])
        self.assertEqual(db.state['iar']['status'], 'Approved')
        self.assertEqual(db.state['iar']['items'], original)
        self.assertEqual(db.state['inventory'][0], (
            'INV-2026-00001', 'Synthetic laptop', 'Reviewed specification',
            'Equipment', 2.0, 'piece', 'PO-2026-0009', 'Available',
            'synthetic time', 125.0, 17))
        self.assertEqual(db.state['inventory'][1][0], 'INV-2026-00002')
        self.assertEqual(db.state['inventory'][1][3], 'Consumable')
        self.assertEqual(db.state['audit'], [(
            'synthetic time', 'synthetic reviewer', 'Approved IAR', 'IAR',
            'IAR-2026-0007', 'Delivery accepted and items added to inventory')])

    def assert_failure_atomic(self, stage):
        db = FakeDatabase(fail=stage)
        before = deepcopy(db.state)
        with self.assertRaisesRegex(RuntimeError, 'synthetic'):
            self.run_approval(db)
        self.assertEqual(db.state, before, 'No receipt, status, audit or number may commit')
        self.assertEqual(db.commits, 0)
        self.assertGreaterEqual(db.rollbacks, 1)
        self.assertTrue(all(con.closed for con in db.connections))

    def test_first_inventory_failure_rolls_back(self):
        self.assert_failure_atomic('item1')

    def test_second_inventory_failure_rolls_back_first_receipt(self):
        self.assert_failure_atomic('item2')

    def test_status_failure_rolls_back_all_receipts(self):
        self.assert_failure_atomic('status')

    def test_audit_failure_rolls_back_status_and_receipts(self):
        self.assert_failure_atomic('audit')

    def test_commit_failure_rolls_back(self):
        self.assert_failure_atomic('commit')

    def test_unapplied_insert_update_or_audit_rolls_back(self):
        for stage in ('no_inventory', 'no_status', 'no_audit'):
            with self.subTest(stage=stage):
                db = FakeDatabase(fail=stage)
                before = deepcopy(db.state)
                with self.assertRaises(RuntimeError):
                    self.run_approval(db)
                self.assertEqual(db.state, before)
                self.assertEqual(db.commits, 0)
                self.assertEqual(db.rollbacks, 1)
                self.assertTrue(all(con.closed for con in db.connections))

    def test_repeat_approval_does_not_add_receipts_or_audit(self):
        db = FakeDatabase()
        self.run_approval(db)
        before = deepcopy(db.state)
        messages = self.run_approval(db)
        self.assertEqual(db.state, before)
        self.assertEqual(db.commits, 1)
        self.assertTrue(any('already approved' in msg for msg in messages))

    def test_already_approved_legacy_record_is_not_validated_or_repaired(self):
        db = FakeDatabase()
        db.state['iar'].update(status='Approved', items='malformed historic data')
        before = deepcopy(db.state)
        self.run_approval(db)
        self.assertEqual(db.state, before)
        self.assertEqual(db.commits, 0)

    def test_human_confirmation_required(self):
        db = FakeDatabase()
        before = deepcopy(db.state)
        self.run_approval(db, confirm=False)
        self.assertEqual(db.state, before)
        self.assertEqual(db.commits, 0)

    def test_missing_record_has_no_writes(self):
        db = FakeDatabase()
        db.state['iar'] = None
        self.assertIn('IAR record not found.', self.run_approval(db))
        self.assertEqual(db.commits, 0)

    def test_invalid_items_are_rejected_before_any_inventory_or_number_write(self):
        invalid = [[], {}, None, 'not-json', '[1]', '[null]',
                   [item(), item(qty='')], [item(qty='not a number')],
                   [item(qty=float('nan'))], [item(qty=float('inf'))],
                   [item(qty=-1)], [item(qty=True)], [item(unit='')],
                   [item(unit_cost='')], [item(unit_cost=-1)],
                   [item(unit_cost=float('inf'))], [item(item='')]]
        for value in invalid:
            with self.subTest(items=value):
                db = FakeDatabase()
                db.state['iar']['items'] = value if isinstance(value, str) else json.dumps(value)
                before = deepcopy(db.state)
                messages = self.run_approval(db)
                self.assertEqual(db.state, before)
                self.assertEqual(db.insert_attempts, 0)
                self.assertEqual(db.commits, 0)
                self.assertTrue(any('IAR' in msg for msg in messages))

    def test_supported_legacy_aliases_preserve_zero_and_item_information(self):
        legacy = {'Item Name': 'Paper', 'Description / Specification': 'A4',
                  'Quantity': 0, 'Unit': 'ream', 'Estimated Unit Cost': 0,
                  '_Dataset Record ID': 28, '_Inventory Category': 'Consumable'}
        db = FakeDatabase([legacy])
        self.run_approval(db)
        receipt = db.state['inventory'][0]
        self.assertEqual(receipt[1:6], ('Paper', 'A4', 'Consumable', 0.0, 'ream'))
        self.assertEqual(receipt[9:], (0.0, 28))

    def test_identifier_collision_still_advances_with_existing_format(self):
        db = FakeDatabase([item()])
        db.state['inventory'].append(('INV-2026-00001',))
        self.run_approval(db)
        self.assertEqual(db.state['inventory'][-1][0], 'INV-2026-00002')

    def test_zero_in_primary_fields_is_not_replaced_by_legacy_aliases(self):
        db = FakeDatabase([item(qty=0, Quantity=9, unit_cost=0, **{'Unit Cost': 99})])
        self.run_approval(db)
        self.assertEqual(db.state['inventory'][0][4], 0)
        self.assertEqual(db.state['inventory'][0][9], 0)

    def test_shared_helpers_keep_standalone_commit_behavior(self):
        db = FakeDatabase()
        routes, _ = load_routes(db.connect)
        self.assertEqual(routes['next_document_no'](
            'INVENTORY', 'INV', 'inventory', column='item_code', width=5), 'INV-2026-00001')
        routes['log']('Synthetic action', 'IAR', 'IAR-TEST', 'Details')
        self.assertEqual(db.commits, 2)
        self.assertTrue(all(con.closed for con in db.connections))


if __name__ == '__main__':
    unittest.main()
