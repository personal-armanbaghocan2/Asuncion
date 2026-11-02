"""Actual root Flask app with synthetic database doubles; no PostgreSQL access.

Run: python -B tests/test_nlp_comparison_routes.py
Optional --browser exercises this same app over loopback HTTP with Playwright.
All psycopg connections are blocked, including during app import. These checks
prove route/template integration, not PostgreSQL transactions or persistence.
"""
from contextlib import ExitStack
from copy import deepcopy
import importlib.util
import json
import os
from pathlib import Path
import re
import secrets
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import psycopg
from server_sessions_v0_1_0 import token_hash


def fixtures():
    requested = [
        {'Item Name': 'SYNTHETIC Laptop', 'Description / Specification':
         'processor Intel Core i5 minimum 12th generation; RAM minimum 16 GB; SSD minimum 512 GB',
         'Quantity': 2.5, 'Unit': 'pieces'},
        {'item': 'SYNTHETIC Paper', 'qty': 10, 'unit': 'ream'},
        {'item': 'SYNTHETIC Printer', 'description': 'power maximum 100 W', 'qty': 1, 'unit': 'pieces'},
        {'item': 'SYNTHETIC Printer', 'description': 'power maximum 200 W', 'qty': 1, 'unit': 'pieces'},
        {'item': 'SYNTHETIC Cart', 'qty': 1, 'unit': 'pieces'},
    ]
    offered = [
        {'item': 'SYNTHETIC Paper', 'qty': 9, 'unit': 'ream'},
        {'item': 'SYNTHETIC Laptop', 'description':
         'processor Intel Core i5 11th generation; RAM 8 GB; HDD 1 TB', 'qty': 2.5, 'unit': 'pieces'},
        {'item': 'SYNTHETIC Printer', 'qty': 1, 'unit': 'pieces'},
        {'item': 'SYNTHETIC Printer', 'qty': 1, 'unit': 'pieces'},
        {'item': 'SYNTHETIC Extra offer', 'qty': 1, 'unit': 'pieces'},
    ]
    return requested, offered


class Cursor:
    def __init__(self, rows=(), rowcount=0):
        self.rows = deepcopy(list(rows))
        self.rowcount = rowcount

    def fetchall(self): return deepcopy(self.rows)
    def fetchone(self): return deepcopy(self.rows[0]) if self.rows else None


class SyntheticDatabase:
    """Explicit query double. Unknown SQL fails instead of reaching a database."""
    def __init__(self):
        requested, offered = fixtures()
        self.prs = {i: dict(id=i, no=f'SYNTHETIC-PR-{i}', items=json.dumps(requested),
                           status='Approved', app_id=None, ppmp_id=None) for i in (1, 3, 10)}
        self.prs[3]['status'] = 'For Review'
        self.aoqs = {i: dict(id=i, pr_id=i if i != 2 else 1, no=f'SYNTHETIC-AOQ-{i}',
                            status='Approved') for i in (1, 2, 3, 10)}
        self.pos = {i: dict(id=i, aoq_id=i, no=f'SYNTHETIC-PO-{i}', supplier='SYNTHETIC VENDOR',
                           status='Approved', items=json.dumps(offered)) for i in (1, 2, 3, 10)}
        self.pos[2]['status'] = 'For Review'
        self.nlps = {20: dict(id=20, no='SYNTHETIC-NLP-20', po_id=1, result='Needs Recanvass',
                             confidence=91.37, details='{}', status='For Review', created_at='2026-01-01')}
        self.accounts = {i: dict(id=i, username=f'synthetic_{i}', role=role, status='Active')
                         for i, role in ((1, 'Admin'), (2, 'Staff'), (3, 'Inspector'))}
        self.sids = {i: secrets.token_urlsafe(32) for i in self.accounts}
        self.revoked = set()
        self.calls, self.writes = [], []
        self.commits = self.closes = 0
        self.fail_session = False

    def joined(self, po):
        a = self.aoqs.get(po['aoq_id'])
        r = self.prs.get(a['pr_id']) if a else None
        if not r: return None
        return dict(po, po_no=po['no'], po_items=po['items'], aoq_no=a['no'],
                    pr_no=r['no'], pr_items=r['items'])

    def eligible(self, po):
        a = self.aoqs.get(po['aoq_id'])
        r = self.prs.get(a['pr_id']) if a else None
        return po['status'] == 'Approved' and a and a['status'] == 'Approved' and r and r['status'] == 'Approved'

    def connect(self):
        store = self
        class Connection:
            def execute(self, sql, params=()): return store.execute(sql, params)
            def commit(self): store.commits += 1
            def rollback(self): pass
            def close(self): store.closes += 1
        return Connection()

    def execute(self, sql, params):
        text = ' '.join(sql.lower().split())
        self.calls.append((text, tuple(params)))
        if text == 'set transaction read only': return Cursor()
        if text == 'set transaction isolation level read committed': return Cursor()
        if 'psms_auth_session_permissions_ready()' in text: return Cursor([dict(ready=True)])
        if text.startswith('select user_id from auth_sessions where user_id='):
            uid,digest=params
            account=self.accounts.get(uid)
            valid=account and uid not in self.revoked and digest==token_hash(self.sids[uid])
            return Cursor([dict(user_id=uid)] if valid else [])
        if 'from public.auth_sessions s' in text:
            if self.fail_session: raise RuntimeError('SYNTHETIC session database failure')
            digest, uid = params
            account = self.accounts.get(uid)
            valid = account and digest == token_hash(self.sids[uid]) and uid not in self.revoked
            return Cursor([account] if valid and account['status'] == 'Active' and account['role'] in ('Admin', 'Staff', 'Inspector') else [])
        if 'from users where id=' in text:
            account=self.accounts.get(params[0])
            if 'for share' in text and account and (account['status']!='Active' or account['role'] not in ('Admin','Staff','Inspector')):
                account=None
            return Cursor([account] if account else [])
        if text.startswith('select p.*,aq.pr_id,r.no pr_no,r.items pr_items'):
            po=self.pos.get(int(params[0]))
            exists=any(n['po_id']==int(params[0]) for n in self.nlps.values())
            joined=self.joined(po) if po and po['status']=='Approved' and not exists else None
            return Cursor([dict(joined,pr_id=self.aoqs[po['aoq_id']]['pr_id'])] if joined else [])
        if text.startswith('select * from po where id='):
            row=self.pos.get(int(params[0]))
            return Cursor([row] if row else [])
        if text.startswith('select id,no from nlp where po_id='):
            return Cursor([n for n in self.nlps.values() if n['po_id']==int(params[0])][:1])
        if text.startswith('select aq.pr_id,r.no pr_no,r.items pr_items'):
            aoq=self.aoqs.get(params[0]);pr=self.prs.get(aoq['pr_id']) if aoq else None
            return Cursor([dict(pr_id=pr['id'],pr_no=pr['no'],pr_items=pr['items'])] if pr else [])
        if text.startswith('select n.*,'):
            records = [dict(self.joined(self.pos[n['po_id']]), **n) for n in self.nlps.values()]
            return Cursor([n for n in records if n['id'] == params[0]] if params else records)
        if text.startswith('select p.id, p.no, p.supplier, r.no pr_no'):
            assert "p.status='approved'" in text and "r.status='approved'" in text and "aq.status='approved'" in text
            return Cursor([self.joined(p) for p in self.pos.values() if self.eligible(p)])
        if text.startswith('select p.id, p.no po_no, p.items po_items'):
            assert "p.status='approved'" in text and "r.status='approved'" in text and "aq.status='approved'" in text
            p = self.pos.get(params[0])
            return Cursor([self.joined(p)] if p and self.eligible(p) else [])
        if text.startswith('select p.* from po p'):
            rows = [p for p in self.pos.values() if p['status'] == 'Approved'
                    and not any(n['po_id'] == p['id'] for n in self.nlps.values())]
            if 'join nlp n on' in text and 'left join nlp' not in text: return Cursor()
            return Cursor([p for p in rows if str(p['id']) == str(params[0])] if params else rows)
        if text.startswith('select * from aoq where id='): return Cursor([self.aoqs[int(params[0])]])
        if text.startswith('select * from pr where id='): return Cursor([self.prs[int(params[0])]])
        if text.startswith('select ') and any(table in text for table in ('from ppmp p', 'from app_plan a', 'from pr r', 'from aoq aq')):
            return Cursor()
        if text.startswith('insert into nlp('):
            keys = ('no', 'po_id', 'result', 'confidence', 'staff_decision', 'remarks', 'created_at', 'details', 'status')
            row = dict(zip(keys, params)); row.update(id=21, po_id=int(row['po_id']))
            self.nlps[21] = row; self.writes.append((text, tuple(params)))
            return Cursor([dict(id=21)], 1)
        if text.startswith('update nlp set status='):
            status, decision, uid = params[:3]
            if len(params)>3 and any(self.nlps[uid].get(key)!=expected for key,expected in zip(('status','result','details'),params[3:])):
                return Cursor(rowcount=0)
            self.nlps[uid].update(status=status, staff_decision=decision)
            self.writes.append((text, tuple(params))); return Cursor(rowcount=1)
        if text.startswith('insert into audit_trail('):
            self.writes.append((text, tuple(params))); return Cursor([dict(id=1)], 1)
        raise AssertionError('Unexpected SQL in synthetic route test: ' + text)


class ActualApplication:
    def __init__(self, comparison_enabled='1'):
        self.comparison_enabled = comparison_enabled

    def __enter__(self):
        self.stack = ExitStack()
        directory = self.stack.enter_context(tempfile.TemporaryDirectory(prefix='capstone-nlp-synthetic-'))
        self.stack.enter_context(patch.dict(os.environ, dict(
            PSMS_ENV='test', PSMS_SECRET_KEY=secrets.token_urlsafe(48),
            SUPABASE_DB_URL='postgresql://synthetic:synthetic@127.0.0.1:1/synthetic_no_connection',
            PSMS_DATA_DIR=directory, PSMS_ITEM_COMPARISON_ENABLED=self.comparison_enabled,
            HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1')))
        self.firewall = self.stack.enter_context(patch.object(psycopg, 'connect', side_effect=AssertionError('Database connections forbidden')))
        name = 'actual_nlp_comparison_application'
        spec = importlib.util.spec_from_file_location(name, ROOT/'app.py')
        self.module = importlib.util.module_from_spec(spec)
        sys.modules[name] = self.module
        try:
            spec.loader.exec_module(self.module)
            self.module.app.config.update(TESTING=True)
            self.firewall.assert_not_called()
            return self
        except BaseException:
            self.stack.close()
            raise

    def bind(self, database):
        self.module.conn = database.connect
        # Route doubles never load installed encoders; dedicated tests inject
        # semantic candidates or exercise the local encoder separately.
        self.stack.enter_context(patch('semantic_item_alignment.suggest_candidates', return_value=[]))
        # Existing optional pricing diagnostics are outside this test's scope.
        self.module.nlp_pricing_advisory = lambda record_id: None

    def client(self, database, uid=2, **claims):
        client = self.module.app.test_client()
        with client.session_transaction() as state:
            state.update(logged_in=True, user_id=uid, role='Staff', username='synthetic_claim', _auth_sid=database.sids[uid])
            state.update(claims)
        return client

    def __exit__(self, *args): return self.stack.__exit__(*args)


class NLPComparisonRouteTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.runtime = ActualApplication().__enter__()
        cls.module = cls.runtime.module

    @classmethod
    def tearDownClass(cls): cls.runtime.__exit__(None, None, None)

    def setUp(self):
        self.db = SyntheticDatabase()
        self.runtime.bind(self.db)
        self.module.app.config['PR_PO_ITEM_COMPARISON_ENABLED'] = True
        self.client = self.runtime.client(self.db)

    def csrf(self):
        html = self.client.get('/nlp').get_data(as_text=True)
        return re.search(r'name="csrf-token" content="([^"]+)"', html)[1]

    def page(self, path='/nlp?comparison_po_id=1'):
        response = self.client.get(path)
        self.assertEqual(response.status_code, 200)
        return response.get_data(as_text=True)

    def test_startup_templates_and_liveness_without_database(self):
        self.runtime.firewall.assert_not_called()
        self.assertEqual(self.module.app.test_client().get('/health/live').json, {'status': 'alive'})
        for name in self.module.app.jinja_env.list_templates():
            self.module.app.jinja_env.get_template(name)

    def test_actual_nlp_list_uses_one_selector_for_independent_actions(self):
        html = self.page('/nlp')
        self.assertIn('Read-only PR-PO Item Comparison', html)
        self.assertIn('id="nlpSourceForm"', html)
        self.assertEqual(html.count('<select name="po_id"'), 1)
        self.assertNotIn('<select name="comparison_po_id"', html)
        self.assertIn('value="compare"', html)
        self.assertIn('name="po_id"', html)
        self.assertIn('name="csrf_token"', html)
        self.assertNotIn('id="prPoItemComparison"', html)

    def test_multiple_item_sources_specs_quantity_and_ambiguity_render(self):
        before = deepcopy(self.db.nlps)
        html = self.page()
        for text in ('Requested PR items', 'Offered PO items', 'processor generation', 'memory', 'ssd capacity',
                     '11th generation', 'RAM 8 GB', 'HDD 1 TB', 'Difference: -1',
                     'Ambiguous item relationship', 'SYNTHETIC Cart', 'Unmatched offered items',
                     'SYNTHETIC Extra offer', 'PR item 1.description clause', 'PO item 2.description clause'):
            self.assertIn(text, html)
        self.assertEqual(before, self.db.nlps)
        self.assertFalse(self.db.writes)
        self.assertEqual(self.db.commits, 0)
        self.assertGreater(self.db.closes, 0)

    def test_actual_existing_review_page_includes_panel_and_preserves_result(self):
        html = self.page('/nlp/20/view')
        for text in ('id="prPoItemComparison"', 'Needs Recanvass', '91.37%', 'review_confirm', '/nlp/20/approve'):
            self.assertIn(text, html)
        self.assertFalse(self.db.writes)

    def test_missing_item_candidate_is_explicitly_unverified(self):
        requested = [dict(item='Paper', qty=1, unit='pieces'), dict(item='Cart', qty=1, unit='pieces')]
        self.db.prs[1]['items'] = json.dumps(requested)
        self.db.pos[1]['items'] = json.dumps(requested[:1])
        self.assertIn('Potential missing requested item. Confirm omission', self.page())

    def test_similar_names_do_not_establish_equivalence_or_compare_specs(self):
        self.db.prs[1]['items'] = json.dumps([dict(item='Laser Printer', RAM='minimum 16 GB')])
        self.db.pos[1]['items'] = json.dumps([dict(item='Inkjet Printer', RAM='8 GB')])
        html = self.page()
        self.assertIn('Shared words only', html)
        self.assertNotIn('Source correspondence for comparison:</b> PO item', html)
        self.assertNotIn('Difference from the required', html)

    def test_labeled_specification_fields_reuse_existing_grammar_and_quote_source(self):
        self.db.prs[1]['items'] = json.dumps([dict(item='Laptop', qty=1, unit='pieces', RAM='minimum 16 GB',
                                                    Processor='Intel Core i5 minimum 12th generation', SSD='minimum 512 GB')])
        self.db.pos[1]['items'] = json.dumps([dict(item='Laptop', qty=1, unit='pieces', RAM='8 GB',
                                                    Processor='Intel Core i5 11th generation', SSD='256 GB')])
        html = self.page()
        for text in ('PR item 1.RAM clause 1', 'PO item 1.Processor clause 1', 'ssd capacity', 'Technical difference'):
            self.assertIn(text, html)
        view = self.module.nlp_item_comparison(self.db.prs[1]['items'], self.db.pos[1]['items'])
        facts = view['result']['rows'][0]['checks']
        memory = next(c for c in facts if c['attribute'] == 'memory')
        self.assertEqual(memory['requested_evidence'][0]['quote'], 'minimum 16 GB')

    def test_exact_decimal_json_and_unit_differences(self):
        self.db.prs[1]['items'] = '[{"item":"Paper","qty":0.1,"unit":"pieces"}]'
        self.db.pos[1]['items'] = '[{"item":"Paper","qty":0.1,"unit":"pieces"}]'
        self.assertIn('Difference: 0.0 pieces', self.page())
        self.db.pos[1]['items'] = '[{"item":"Paper","qty":0.1,"unit":"box"}]'
        self.assertIn('Different units were not silently equated', self.page())

    def test_unsupported_fields_and_html_are_preserved_and_escaped(self):
        source = dict(item='Laptop', description='unusual unsupported specification <img src=x onerror=alert(1)>', qty=1,
                      unit='pieces', UnknownField='<script>synthetic()</script>')
        self.db.prs[1]['items'] = self.db.pos[1]['items'] = json.dumps([source])
        html = self.page()
        self.assertIn('Unsupported or uncertain', html)
        self.assertIn('&lt;script&gt;synthetic()', html)
        self.assertIn('&lt;img src=x', html)
        self.assertNotIn('<img src=x', html)

    def test_optional_parser_failure_does_not_break_review_or_expose_error(self):
        with patch('pr_po_item_matching_v0_1_0.compare_documents', side_effect=RuntimeError('SENSITIVE-SYNTHETIC-ERROR')):
            html = self.page('/nlp/20/view')
        self.assertIn('Technical comparison is unavailable', html)
        self.assertIn('Needs Recanvass', html)
        self.assertNotIn('SENSITIVE-SYNTHETIC-ERROR', html)
        self.assertFalse(self.db.writes)

    def test_invalid_empty_or_oversized_comparison_sources_fail_safely(self):
        for text in ('{', '{}', '[null]', 'x'*1700001):
            with self.subTest(source_type=text[:10]):
                view = self.module.nlp_item_comparison(text, '[]')
                self.assertIsNone(view['result']); self.assertTrue(view['error'])
        view = self.module.nlp_item_comparison('[]', '[]')
        self.assertTrue(view['result']['document_checks'])

    def test_feature_switch_disables_panel_queries_and_matcher_without_affecting_review(self):
        self.module.app.config['PR_PO_ITEM_COMPARISON_ENABLED'] = False
        with patch('pr_po_item_matching_v0_1_0.compare_documents', side_effect=AssertionError('Disabled matcher called')):
            self.assertNotIn('Read-only PR-PO Item Comparison', self.page('/nlp?comparison_po_id=bad'))
            html = self.page('/nlp/20/view')
            self.assertNotIn('id="prPoItemComparison"', html)
            self.assertIn('Needs Recanvass', html); self.assertIn('91.37%', html)
        self.assertFalse(any(sql.startswith('select p.id,') for sql, _ in self.db.calls))
        self.assertFalse(self.db.writes)

    def test_environment_switch_disables_feature_at_startup(self):
        with ActualApplication(comparison_enabled='0') as runtime:
            runtime.bind(self.db)
            self.assertFalse(runtime.module.app.config['PR_PO_ITEM_COMPARISON_ENABLED'])
            response = runtime.client(self.db).get('/nlp?comparison_po_id=1')
            self.assertEqual(response.status_code, 200)
            self.assertNotIn('id="prPoItemComparison"', response.get_data(as_text=True))

    def test_anonymous_access_never_reads_source_records(self):
        for path in ('/nlp?comparison_po_id=1', '/nlp/20/view'):
            self.assertEqual(self.module.app.test_client().get(path).status_code, 302)
        self.assertFalse(self.db.calls)

    def test_active_roles_preserve_existing_access_and_refresh_forged_claims(self):
        for uid in (1, 2, 3):
            client = self.runtime.client(self.db, uid, role='Admin')
            self.assertEqual(client.get('/nlp?comparison_po_id=1').status_code, 200)
            with client.session_transaction() as state:
                self.assertEqual(state['role'], self.db.accounts[uid]['role'])

    def test_inactive_revoked_and_unsupported_roles_cannot_read_sources(self):
        for mode in ('inactive', 'revoked', 'role'):
            with self.subTest(mode=mode):
                self.db.calls.clear()
                self.db.accounts[2].update(status='Inactive' if mode == 'inactive' else 'Active',
                                           role='Unknown' if mode == 'role' else 'Staff')
                self.db.revoked = {2} if mode == 'revoked' else set()
                response = self.runtime.client(self.db).get('/nlp?comparison_po_id=1')
                self.assertEqual(response.status_code, 302)
                self.assertFalse(any('from po p' in sql for sql, _ in self.db.calls))

    def test_failed_session_lookup_blocks_route(self):
        self.db.fail_session = True
        self.assertEqual(self.client.get('/nlp?comparison_po_id=1').status_code, 503)
        self.assertFalse(any('from po p' in sql for sql, _ in self.db.calls))

    def test_invalid_unapproved_and_unlinked_ids_are_not_comparison_sources(self):
        for value in ('bad', '-1', '0', '999', '2', '3'):
            with self.subTest(value=value):
                self.assertEqual(self.client.get('/nlp?comparison_po_id='+value).status_code, 404)
        self.assertFalse(self.db.writes)

    def test_po_relationship_selects_pr_and_ignores_client_supplied_pr(self):
        html = self.page('/nlp?comparison_po_id=1&pr_id=3')
        self.assertIn('<b>Compared PR:</b> SYNTHETIC-PR-1', html)
        self.assertNotIn('<b>Compared PR:</b> SYNTHETIC-PR-3', html)

    def test_existing_csrf_and_review_confirmation_still_guard_approval(self):
        self.assertEqual(self.client.post('/nlp/20/approve', data={'review_confirm': 'yes'}).status_code, 400)
        self.assertEqual(self.client.post('/nlp/20/approve', data={'csrf_token': self.csrf()}).status_code, 302)
        self.assertEqual(self.db.nlps[20]['status'], 'For Review')
        self.assertFalse(self.db.writes)
        self.assertEqual(self.client.post('/nlp/20/approve', data={'csrf_token': self.csrf(), 'review_confirm': 'yes'}).status_code, 302)
        self.assertEqual(self.db.nlps[20]['status'], 'Approved')
        self.assertEqual(self.db.nlps[20]['staff_decision'], 'Needs Recanvass')
        writes = len(self.db.writes)
        self.client.post('/nlp/20/approve', data={'csrf_token': self.csrf(), 'review_confirm': 'yes'})
        self.assertEqual(len(self.db.writes), writes)

    def test_installed_fasttext_inference_and_real_verification_post_are_unchanged(self):
        from fasttext_nlp_runtime import predict_pair, model_status
        self.assertTrue(model_status(self.module.NLP_FASTTEXT_DIR)['active'])
        p = self.db.pos[10]; r = self.db.prs[10]
        expected = predict_pair(self.module.NLP_FASTTEXT_DIR,
                               self.module.build_pr_text_for_trained_nlp(self.module.parse_items(r['items'])),
                               self.module.build_po_text_for_trained_nlp(self.module.parse_items(p['items'])))
        self.page()  # Rendering advisory findings cannot modify runtime inference.
        for enabled in (True, False):
            self.module.app.config['PR_PO_ITEM_COMPARISON_ENABLED'] = enabled
            prediction = self.module.nlp_predict(self.module.parse_items(r['items']), self.module.parse_items(p['items']))
            self.assertEqual(prediction[:2], (expected['status'], expected['status_confidence']))
            self.assertEqual(prediction[2]['trained_primary_issue'], expected['issue'])
            self.assertEqual(prediction[2]['trained_status_probabilities'], expected['status_probabilities'])
            self.assertEqual(prediction[2]['feature_dimension'], expected['feature_dimension'])
        with patch.object(self.module, 'next_document_no', return_value='SYNTHETIC-NLP-21'):
            response = self.client.post('/nlp', data={'csrf_token': self.csrf(), 'po_id': '10'})
        self.assertEqual(response.status_code, 302)
        recorded = self.db.nlps[21]
        self.assertEqual((recorded['result'], recorded['confidence']), (expected['status'], expected['status_confidence']))
        self.assertEqual(recorded['status'], 'For Review')
        self.assertTrue(json.loads(recorded['details'])['trained_model_used'])
        self.assertNotIn('PR_PO_ITEM_COMPARISON', recorded['details'])
        count = len(self.db.writes)
        self.client.post('/nlp', data={'csrf_token': self.csrf(), 'po_id': '10'})
        self.assertEqual(len(self.db.writes), count)
        self.runtime.firewall.assert_not_called()

    def test_model_failure_creates_no_fallback_verification(self):
        with patch.object(self.module, 'run_trained_nlp_prediction', side_effect=RuntimeError('SYNTHETIC unavailable model')):
            response = self.client.post('/nlp', data={'csrf_token': self.csrf(), 'po_id': '10'})
        self.assertEqual(response.status_code, 302)
        self.assertNotIn(21, self.db.nlps)
        self.assertFalse(any(sql.startswith('insert into nlp') for sql, _ in self.db.writes))


def browser_checks():
    """Exercise actual routes, templates, CSS and JS; synthetic SQL double only."""
    from waitress import create_server
    with ActualApplication() as runtime:
        database = SyntheticDatabase(); runtime.bind(database)
        client = runtime.client(database)
        cookie = client.get_cookie('session')
        server = create_server(runtime.module.app, host='127.0.0.1', port=0, threads=2)
        thread = threading.Thread(target=server.run, daemon=True); thread.start()
        try:
            env = dict(os.environ, PSMS_BROWSER_BASE='http://127.0.0.1:'+str(server.effective_port),
                       PSMS_BROWSER_COOKIE=cookie.value)
            result = subprocess.run(['node', str(ROOT/'tests/nlp_comparison_browser_checks.cjs')],
                                    env=env, capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=90)
            print(result.stdout.strip())
            if result.returncode:
                print(result.stderr.strip().encode('ascii', errors='backslashreplace').decode('ascii'))
                raise AssertionError('Browser checks failed')
            runtime.firewall.assert_not_called()
            if database.writes: raise AssertionError('Browser comparison changed procurement data')
        finally:
            server.close(); server.task_dispatcher.shutdown(timeout=5)


if __name__ == '__main__':
    browser = '--browser' in sys.argv
    if browser: sys.argv.remove('--browser')
    result = unittest.main(exit=False)
    if not result.result.wasSuccessful(): sys.exit(1)
    if browser: browser_checks()
