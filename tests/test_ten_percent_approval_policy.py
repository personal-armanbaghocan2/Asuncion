"""Pure synthetic policy and route-helper checks; no app import, DB or models."""
from copy import deepcopy
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from flask import Flask
from werkzeug.exceptions import UnprocessableEntity,Conflict,BadRequest
import procurement_comparison_v1 as evidence
import revised_procurement_v1 as workflow


def fixture(count=10,affected=1,label='Reject'):
    left=[dict(item='Fictional supply '+str(i),qty=10,unit='pcs') for i in range(count)]
    right=deepcopy(left)
    for item in right[:affected]:item['qty']=9
    comparison=evidence.compare(left,right)
    row=dict(id=1,po_id=2,pr_id=3,status='Reviewed',workflow_version=2,result=label,
             confidence=0.01,technical_status=comparison['technical_status'],
             pr_items_hash='a'*64,po_items_hash='b'*64,
             details={'technical_comparison':comparison},reviewed_by=1,
             reviewed_at='Fictional date',relationship_reason='Fictional documented review',
             evidence_reference='FICTIONAL-REF')
    return row,comparison,list(range(1,count+1))


class Policy(unittest.TestCase):
    def test_exact_ten_percent_accepts_every_model_label_without_modifying_it(self):
        for label in ('Acceptable','Needs Review','Needs Recanvass','Reject'):
            row,current,mapping=fixture(label=label);before=deepcopy(row)
            self.assertEqual(workflow.discrepancy_incidence(current)['value'],10)
            self.assertTrue(workflow.tolerance_review_matches(row,mapping,current))
            self.assertEqual(row,before)
            self.assertFalse(workflow.stored_review_matches(row,mapping,current))

    def test_over_ten_percent_is_blocked(self):
        row,current,mapping=fixture(count=9)
        self.assertFalse(workflow.tolerance_review_matches(row,mapping,current))

    def test_zero_percent_rejection_still_permits_manual_policy_approval(self):
        row,current,mapping=fixture(affected=0)
        self.assertTrue(workflow.tolerance_review_matches(row,mapping,current))

    def test_pending_check_blocks_even_on_an_already_affected_line(self):
        row,current,mapping=fixture()
        pending=dict(attribute='specification wording',state='REQUIRES_HUMAN_REVIEW',
                     requested='Fictional source',offered='Fictional paraphrase',conditional_on_identity=False)
        current['rows'][0]['checks'].append(pending)
        row['details']['technical_comparison']=deepcopy(current)
        self.assertFalse(workflow.tolerance_review_matches(row,mapping,current))

    def test_tampered_missing_extra_duplicate_and_uncertain_sources_block(self):
        for mode in ('changed','missing','extra','duplicate','candidate','empty_fields'):
            row,current,mapping=fixture()
            if mode=='changed':current=deepcopy(current);current['rows'][0]['checks'][0]['offered']=8
            elif mode=='missing':current['missing_or_unresolved_pr_items']=[1]
            elif mode=='extra':current['extra_or_unallocated_items']=[1]
            elif mode=='duplicate':mapping[0]=2
            elif mode=='candidate':current['rows'][0]['identity']='CANDIDATE'
            else:current['source_fields']['PO'][0]['fields']=[]
            self.assertFalse(workflow.tolerance_review_matches(row,mapping,current),mode)

    def test_persisted_marker_is_bound_to_source_hashes_and_exact_evidence(self):
        row,current,_=fixture()
        marker=dict(version=1,percentage=10,technical_comparison=deepcopy(current),
                    pr_items_hash=row['pr_items_hash'],po_items_hash=row['po_items_hash'])
        row['details']['threshold_approval_v4']=marker
        self.assertTrue(workflow.tolerance_approval_current(row,current))
        marker['pr_items_hash']='changed'
        self.assertFalse(workflow.tolerance_approval_current(row,current))

    def test_absent_reviewed_pins_never_opens_a_connection(self):
        with patch.object(workflow,'TOLERANCE_ROUTINE_MD5',{}),patch.object(workflow,'api_schema',return_value='psms_aoq_api'):
            self.assertFalse(workflow.tolerance_contract_ready({'conn':lambda:self.fail('Unexpected SQL')}))

    def test_explicit_manual_approval_uses_v4_only_with_ready_policy(self):
        row,current,_=fixture()
        app=Flask('fictional-tolerance');app.secret_key='fictional'
        calls=[]
        receipt=lambda api,name,values:calls.append((name,values)) or dict(nlp_id=1,po_id=2,new_status='Approved')
        with app.test_request_context('/',method='POST',data=dict(review_action='approve',review_confirm='yes',final_confirm='yes')):
            with patch.object(workflow,'review_stage_contract_ready',return_value=True),patch.object(workflow,'source_pair',return_value=({},{})),patch.object(workflow,'relationship_review_allowed',return_value=True),patch.object(workflow,'saved_review_evidence',return_value=(current,current,True)),patch.object(workflow,'tolerance_contract_ready',return_value=True),patch.object(workflow,'rpc',side_effect=receipt),patch.object(workflow,'url_for',return_value='/fictional-iar'):
                self.assertEqual(workflow.staged_verification({},row).status_code,302)
        self.assertEqual(calls,[('approve_reviewed_pr_po_v4',(1,True))])
        with app.test_request_context('/',method='POST',data=dict(review_action='approve',review_confirm='yes',final_confirm='yes')):
            with patch.object(workflow,'review_stage_contract_ready',return_value=True),patch.object(workflow,'source_pair',return_value=({},{})),patch.object(workflow,'relationship_review_allowed',return_value=True),patch.object(workflow,'saved_review_evidence',return_value=(current,current,True)),patch.object(workflow,'tolerance_contract_ready',return_value=False),patch.object(workflow,'rpc',side_effect=AssertionError('Unavailable policy called')):
                with self.assertRaises(UnprocessableEntity):workflow.staged_verification({},row)


class Refresh(unittest.TestCase):
    def fixture(self):
        row,new,mapping=fixture(affected=0)
        new['wording_policy']='CORE_DESCRIPTION_V2'
        old=deepcopy(new);old.pop('wording_policy')
        old['rows'][0]['checks'].append(dict(attribute='source specification wording',state='REQUIRES_HUMAN_REVIEW',requested='Fictional requested',offered='Fictional equivalent',conditional_on_identity=False))
        new['rows'][0]['checks'].append(dict(old['rows'][0]['checks'][-1],state='MATCH'))
        old['technical_status']='NEEDS_REVIEW'
        row.update(technical_status='NEEDS_REVIEW',details={'technical_comparison':old})
        pr=dict(items='Fictional unchanged PR');po=dict(items='Fictional unchanged PO',status='Approved')
        row.update(pr_items_hash=evidence.fingerprint(pr['items']),po_items_hash=evidence.fingerprint(po['items']))
        return row,new,pr,po

    def test_same_sources_complete_policy_refresh_without_model_or_approval(self):
        row,new,pr,po=self.fixture();before=deepcopy(row)
        self.assertTrue(workflow.refresh_comparison_allowed(row,pr,po,new))
        calls=[];app=Flask('fictional-refresh');app.secret_key='fictional'
        with app.test_request_context('/',method='POST',data=dict(review_action='recalculate',review_confirm='yes')):
            with patch.object(workflow,'source_pair',return_value=(pr,po)),patch.object(workflow,'relationship_review_allowed',return_value=True),patch.object(workflow,'refresh_contract_ready',return_value=True),patch.object(workflow,'refresh_current_comparison',return_value=new),patch.object(workflow,'saved_review_evidence',side_effect=AssertionError('Legacy replay must not block bounded refresh')),patch.object(workflow,'rpc',side_effect=lambda api,name,values:calls.append((name,values)) or dict(nlp_id=1,po_id=2,new_status='For Review')),patch.object(workflow,'url_for',return_value='/fictional-nlp'):
                self.assertEqual(workflow.staged_verification({},row).status_code,302)
        self.assertEqual(calls[0][0],'refresh_pr_po_comparison_v4')
        self.assertEqual(json.loads(calls[0][1][1]),new)
        self.assertEqual(row,before)

    def test_stale_incomplete_approved_or_different_sources_cannot_refresh(self):
        for mode in ('stale','incomplete','approved','fields','policy','po_pending'):
            row,new,pr,po=self.fixture()
            if mode=='stale':pr['items']='Changed'
            elif mode=='incomplete':new['rows'][0]['checks'][0]['state']='UNKNOWN'
            elif mode=='approved':row['status']='Approved'
            elif mode=='fields':new['source_fields']['PR'][0]['fields'].append('New unsaved field')
            elif mode=='po_pending':po['status']='For Review'
            else:new.pop('wording_policy')
            self.assertFalse(workflow.refresh_comparison_allowed(row,pr,po,new),mode)

    def test_route_requires_confirmation_and_pending_record(self):
        app=Flask('fictional-refresh-denial');app.secret_key='fictional'
        row,new,pr,po=self.fixture()
        with app.test_request_context('/',method='POST',data=dict(review_action='recalculate')):
            with self.assertRaises(BadRequest):workflow.staged_verification({},row)
        row['status']='Approved'
        with app.test_request_context('/',method='POST',data=dict(review_action='recalculate',review_confirm='yes')):
            with self.assertRaises(Conflict):workflow.staged_verification({},row)

    def test_refresh_without_reviewed_pin_never_opens_connection(self):
        with patch.object(workflow,'REFRESH_ROUTINE_MD5',''),patch.object(workflow,'api_schema',return_value='psms_aoq_api'):
            self.assertFalse(workflow.refresh_contract_ready({'conn':lambda:self.fail('Unexpected SQL')}))

    def test_marker_only_and_other_evidence_changes_do_not_offer_refresh(self):
        row,new,pr,po=self.fixture()
        marker_only=deepcopy(row['details']['technical_comparison']);marker_only['wording_policy']='CORE_DESCRIPTION_V2'
        self.assertFalse(workflow.refresh_comparison_allowed(row,pr,po,marker_only))
        for mode in ('requested','offered','quantity','identity','semantic'):
            row,new,pr,po=self.fixture()
            if mode in ('requested','offered'):new['rows'][0]['checks'][-1][mode]='Changed source value'
            elif mode=='quantity':new['rows'][0]['checks'][0]['offered']=11
            elif mode=='identity':new['rows'][0]['identity']='HUMAN_CONFIRMED'
            else:new['semantic_candidates']=[]
            self.assertFalse(workflow.refresh_comparison_allowed(row,pr,po,new),mode)

    def test_saved_semantic_candidates_and_metadata_are_retained_exactly(self):
        row,new,pr,po=self.fixture()
        candidates=[dict(pr_position=1,po_position=1,similarity=0.91)]
        row['details']['technical_comparison'].update(semantic_candidates=candidates,semantic_state='INSTALLED_FASTTEXT_INFERENCE')
        before=deepcopy(row)
        with patch.object(evidence,'compare',return_value=new) as compare:
            current=workflow.refresh_current_comparison({'parse_items':lambda text:[{}]*10},row,pr,po)
        self.assertEqual(current['semantic_candidates'],candidates)
        self.assertEqual(current['semantic_state'],'INSTALLED_FASTTEXT_INFERENCE')
        self.assertEqual(compare.call_args.kwargs['semantic_candidates'],candidates)
        self.assertTrue(workflow.refresh_comparison_allowed(row,pr,po,current))
        self.assertEqual(row,before)


if __name__=='__main__':unittest.main(verbosity=2)
