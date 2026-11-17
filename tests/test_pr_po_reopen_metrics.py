"""Pure synthetic reopen/range regressions; no app startup, SQL or model calls."""
import copy
import json
from pathlib import Path
import unittest
from unittest.mock import Mock,patch

from flask import Flask,abort
from jinja2 import ChoiceLoader,DictLoader,FileSystemLoader

import procurement_comparison_v1 as evidence
import revised_procurement_v1 as workflow


def modern(**changes):
    row=dict(id=20,pr_id=6,workflow_version=2,pr_items_hash='current-pr',
             po_items_hash='current-po',status='Reviewed',result='Needs Recanvass')
    row.update(changes)
    return row


def item(name='Fictional wireless mouse',quantity=10,specification='2.4 GHz USB receiver'):
    return dict(item=name,description=specification,qty=quantity,unit='pcs')


def show(left,right,*,stale=False,alter=None):
    technical=evidence.compare(left,right)
    if alter is not None:
        alter(technical)
    row=dict(result='Acceptable',technical_status=technical['technical_status'],
             details={'technical_comparison':technical})
    before=copy.deepcopy((row,left,right))
    result=workflow.verification_display(row,left,right,stale=stale)
    assert (row,left,right)==before
    return result


class ReopenCurrentEvidence(unittest.TestCase):
    def test_current_modern_reopens_with_inert_unversioned_history_without_mutation(self):
        current=modern()
        rows=[dict(id=1,workflow_version=None,status='Approved',pr_id=2),current]
        before=copy.deepcopy(rows)
        result=workflow.current_verification(rows,6,'current-pr','current-po')
        self.assertIs(result,current)
        self.assertEqual(rows,before)
        self.assertEqual(current['status'],'Reviewed')
        self.assertEqual(current['result'],'Needs Recanvass')

    def test_current_modern_reopens_with_preserved_version_one_history(self):
        current=modern()
        history=dict(id=1,pr_id=6,workflow_version=1,status='Reviewed',reviewed_by=99,
                     details={'verification_lineage':'unchanged historical evidence'})
        rows=[history,current]
        before=copy.deepcopy(rows)
        self.assertIs(workflow.current_verification(rows,6,'current-pr','current-po'),current)
        self.assertEqual(rows,before)

    def test_wrong_pr_or_stale_source_hash_does_not_reopen(self):
        for changes in ({'pr_id':7},{'pr_items_hash':'stale'},
                        {'po_items_hash':'stale'},{'workflow_version':1}):
            with self.subTest(changes=changes):
                rows=[modern(**changes)]
                before=copy.deepcopy(rows)
                self.assertIsNone(workflow.current_verification(rows,6,'current-pr','current-po'))
                self.assertEqual(rows,before)

    def test_multiple_current_results_are_ambiguous_and_do_not_reopen(self):
        rows=[modern(),modern(id=21)]
        before=copy.deepcopy(rows)
        self.assertIsNone(workflow.current_verification(rows,6,'current-pr','current-po'))
        self.assertEqual(rows,before)

    def test_no_current_result_does_not_create_or_select_history(self):
        for rows in ([],[dict(id=1,workflow_version=None,pr_id=6)]):
            with self.subTest(rows=rows):
                before=copy.deepcopy(rows)
                self.assertIsNone(workflow.current_verification(rows,6,'current-pr','current-po'))
                self.assertEqual(rows,before)


class PendingDiscrepancyBounds(unittest.TestCase):
    def test_pending_wording_shows_bounds_coverage_and_measured_quantity_without_eligibility(self):
        left=[item(specification='2.4 GHz USB receiver; plug-and-play')]
        right=[item()]
        result=show(left,right)
        self.assertEqual(result['percentage'],'0.00-100.00%')
        self.assertFalse(result['percentage_final'])
        self.assertEqual(result['review_coverage'],'75.00%')
        self.assertEqual(result['pending_checks'],1)
        self.assertEqual(result['pending_items'],1)
        self.assertEqual(result['pairs'][0]['quantity_percentage'],'0.00%')
        self.assertEqual(result['pairs'][0]['percentage'],'0.00-100.00%')
        pending=[finding for finding in result['findings'] if finding['status']=='Needs Review']
        self.assertTrue(pending)
        self.assertTrue(all(finding['percentage']=='0.00-100.00%' for finding in pending))
        self.assertNotEqual(result['eligibility_status'],'Match')
        self.assertIn('does not authorize approval',result['percentage_basis'])

    def test_one_confirmed_mismatch_and_one_pending_line_bound_fifty_to_one_hundred(self):
        left=[item('Fictional mouse A'),
              item('Fictional mouse B',specification='2.4 GHz USB receiver; plug-and-play')]
        right=[item('Fictional mouse A',quantity=8),item('Fictional mouse B')]
        result=show(left,right)
        self.assertEqual(result['percentage'],'50.00-100.00%')
        self.assertFalse(result['percentage_final'])
        self.assertEqual(result['pending_items'],1)

    def test_stale_evidence_does_not_get_range_or_coverage(self):
        result=show([item(specification='2.4 GHz USB receiver; plug-and-play')],[item()],stale=True)
        self.assertEqual(result['percentage'],'Not calculated')
        self.assertFalse(result['percentage_final'])
        self.assertIsNone(result['review_coverage'])
        self.assertNotEqual(result['eligibility_status'],'Match')

    def test_malformed_source_reference_never_gets_a_range(self):
        for key in ('pr_reference','po_reference'):
            with self.subTest(key=key):
                def break_reference(technical):
                    technical['rows'][0][key]='FOREIGN-REFERENCE'
                result=show([item(specification='2.4 GHz USB receiver; plug-and-play')],[item()],alter=break_reference)
                self.assertEqual(result['percentage'],'Not calculated')
                self.assertFalse(result['percentage_final'])
                self.assertIsNone(result['pairs'][0]['quantity_percentage'])

    def test_fully_matching_sources_keep_final_zero_and_existing_eligibility(self):
        result=show([item()],[item()])
        self.assertEqual(result['percentage'],'0.00%')
        self.assertTrue(result['percentage_final'])
        self.assertEqual(result['review_coverage'],'100.00%')
        self.assertEqual(result['pending_checks'],0)
        self.assertEqual(result['pending_items'],0)
        self.assertEqual(result['eligibility_status'],'Match')


class ConcurrentSaveRoute(unittest.TestCase):
    """Actual workflow route in standalone Flask; all persistence/inference are doubles."""
    def setUp(self):
        self.app=Flask('synthetic_pr_po_route')
        self.app.config.update(TESTING=True,SECRET_KEY='synthetic-route-test-only',
                               PSMS_NLP_RUNTIME='retrained_rf')
        root=Path(__file__).resolve().parents[1]
        self.app.jinja_loader=ChoiceLoader([
            DictLoader({'base.html':'{% block content %}{% endblock %}'}),
            FileSystemLoader(str(root/'templates'))])
        self.items=[item()]
        self.raw=json.dumps(self.items)
        self.pr=dict(id=6,no='PR-SYNTHETIC',items=self.raw,status='Approved')
        self.po=dict(id=2,no='PO-SYNTHETIC',items=self.raw,status='Approved')
        self.saved=modern(pr_items_hash=evidence.fingerprint(self.raw),
                          po_items_hash=evidence.fingerprint(self.raw))
        self.query=Mock(side_effect=[[],[self.saved]])
        self.predict=Mock(return_value=('Acceptable',99.0,dict(
            trained_model_used=True,runtime='SYNTHETIC_TEST_DOUBLE',
            model_metadata={'model_version':'SYNTHETIC_TEST_ONLY'})))
        self.api={'q':self.query,'app':self.app,'parse_items':json.loads,'nlp_predict':self.predict}
        self.app.add_url_rule('/nlp','nlp',lambda:workflow.nlp_page(self.api),methods=['GET','POST'])
        self.client=self.app.test_client()
        for manager in (
            patch.object(workflow,'source_pair',return_value=(self.pr,self.po)),
            patch.object(workflow,'final_contract_ready',return_value=True),
            patch.object(workflow,'comparison',return_value=evidence.compare(self.items,self.items)),
            patch('nlp_runtime_selection.require_available',return_value=None),
        ):
            manager.start();self.addCleanup(manager.stop)

    def submit(self):
        return self.client.post('/nlp',data=dict(action='run',pr_id=6,po_id=2))

    def test_concurrent_exact_source_save_reopens_one_saved_result_without_retry(self):
        before=copy.deepcopy(self.saved)
        with patch.object(workflow,'rpc',side_effect=lambda *args:abort(409)) as rpc:
            response=self.submit()
        self.assertEqual(response.status_code,302)
        self.assertIn('verification_id=20',response.location)
        self.assertEqual(self.query.call_count,2)
        self.predict.assert_called_once()
        rpc.assert_called_once()
        self.assertEqual(rpc.call_args.args[1],'save_approved_pr_po_verification_v2')
        self.assertEqual(self.saved,before)
        self.assertEqual(self.saved['status'],'Reviewed')
        self.assertEqual(self.saved['result'],'Needs Recanvass')

    def test_concurrent_stale_source_returns_actionable_conflict_without_retry(self):
        self.saved['po_items_hash']='CHANGED-SOURCE'
        before=copy.deepcopy(self.saved)
        with patch.object(workflow,'rpc',side_effect=lambda *args:abort(409)) as rpc:
            response=self.submit()
        self.assertEqual(response.status_code,409)
        html=response.get_data(as_text=True)
        self.assertIn('changed while processing',html)
        self.assertIn('Open verification 20',html)
        self.assertIn('verification_id=20',html)
        rpc.assert_called_once()
        self.assertEqual(self.query.call_count,2)
        self.assertEqual(self.saved,before)

    def test_concurrent_ambiguous_results_are_not_reported_as_success(self):
        self.query.side_effect=[[],[self.saved,dict(self.saved,id=21)]]
        with patch.object(workflow,'rpc',side_effect=lambda *args:abort(409)) as rpc:
            response=self.submit()
        self.assertEqual(response.status_code,409)
        self.assertIn('Open verification 20',response.get_data(as_text=True))
        self.assertIn('Open verification 21',response.get_data(as_text=True))
        rpc.assert_called_once()

    def test_non_conflict_rpc_error_is_not_reinterpreted_as_concurrent_success(self):
        with patch.object(workflow,'rpc',side_effect=lambda *args:abort(422)) as rpc:
            response=self.submit()
        self.assertEqual(response.status_code,422)
        self.assertEqual(self.query.call_count,1)
        rpc.assert_called_once()

    def test_wrong_pair_opens_bounded_conflict_without_model_or_save(self):
        self.query.side_effect=[[self.saved]]
        with patch.object(workflow,'source_pair',side_effect=lambda *args:abort(409)), \
             patch.object(workflow,'rpc',side_effect=AssertionError('Wrong pair must not save')) as rpc:
            response=self.submit()
        self.assertEqual(response.status_code,409)
        html=response.get_data(as_text=True)
        self.assertIn('approved source relationship or supplier',html)
        self.assertIn('Open verification 20',html)
        self.predict.assert_not_called()
        rpc.assert_not_called()


if __name__=='__main__':
    unittest.main()
