"""Normal root Flask upload/review routes with transactional SQL doubles only."""
from copy import deepcopy
from io import BytesIO
import json
from pathlib import Path
import re
import unittest
from unittest.mock import patch

from test_nlp_comparison_routes import ActualApplication, Cursor, SyntheticDatabase
from test_multi_item_extraction import HEADERS, GROUND_TRUTH, GENERAL, BUDGET, METADATA
import offline_ocr_mapper as mapper


class DraftDatabase(SyntheticDatabase):
    def __init__(self):
        super().__init__(); self.sources={}; self.drafts={}; self.rollbacks=0; self.fail_audit=False

    def connect(self):
        store=self
        before=deepcopy((self.sources,self.drafts,self.writes))
        class Connection:
            def execute(self,sql,params=()):return store.execute(sql,params)
            def commit(self):store.commits+=1
            def close(self):store.closes+=1
            def rollback(self):
                store.sources,store.drafts,store.writes=deepcopy(before);store.rollbacks+=1
        return Connection()

    def execute(self,sql,params):
        text=' '.join(sql.lower().split())
        if text.startswith('select pg_advisory_xact_lock'):return Cursor()
        if text.startswith('select id from users where'):
            account=self.accounts.get(params[0]);return Cursor([account] if account and account['status']=='Active' else [])
        if text.startswith('select user_id from auth_sessions where'):
            return Cursor([dict(user_id=params[0])] if params[0] not in self.revoked else [])
        if text.startswith('select s.id from psms_documents.sources'):
            uid,category,digest=params
            return Cursor([dict(id=s['id']) for s in self.sources.values() if s['owner_id']==uid and s['category']==category and s['sha256']==digest
                           and s['kind']=='ppmp'])
        if text.startswith('select id from psms_documents.sources where owner_id=') and 'sha256=?' in text:
            uid,digest=params
            return Cursor([dict(id=s['id']) for s in self.sources.values()
                if s['owner_id']==uid and s['kind']=='ppmp' and s['sha256']==digest][:1])
        if text.startswith('select id from psms_documents.sources'):
            uid,category,name=params
            return Cursor([dict(id=s['id']) for s in self.sources.values() if s['owner_id']==uid and s['category']==category
                           and s['kind']=='ppmp' and s['filename'].lower()==name.lower()])
        if text.startswith('insert into psms_documents.sources'):
            row=dict(zip(('id','owner_id','kind','category','filename','extension','content','sha256','extraction'),params))
            row['extraction']=json.loads(row['extraction']);self.sources[row['id']]=row;return Cursor(rowcount=1)
        if text.startswith('insert into ppmp_ocr_drafts'):
            keys=('upload_token','temp_code','ppmp_category','fields_json','confidence_json','average_confidence','source_filename',
                  'raw_ocr_text','extraction_method','created_by','created_at','updated_at','owner_id','source_id')
            row=dict(zip(keys,params)); did=len(self.drafts)+1
            row.update(id=did,status='CATEGORY_REVIEW' if "'category_review'" in text else 'NEEDS_REVIEW',
                       reviewed=False,converted=False,revision=1,official_ppmp_id=None)
            self.drafts[did]=row;return Cursor([dict(id=did)],rowcount=1)
        if text.startswith('update ppmp_ocr_drafts set temp_code='):
            self.drafts[params[1]]['temp_code']=params[0];return Cursor(rowcount=1)
        if text.startswith('select * from ppmp_ocr_drafts where temp_code='):
            code,category,uid=params
            return Cursor([d for d in self.drafts.values() if d['temp_code']==code and d['ppmp_category']==category and d['owner_id']==uid])
        if text.startswith('select * from ppmp_ocr_drafts where owner_id='):
            uid,category=params[:2];offset=params[-1]
            source=params[2] if 'and source_id=?' in text else None
            return Cursor(sorted([d for d in self.drafts.values() if d['owner_id']==uid and d['ppmp_category']==category
                and (source is None or d['source_id']==source) and d['status'] not in ('CONVERTED_TO_PPMP','CANCELLED')],key=lambda d:d['id'],reverse='id desc' in text)[offset:offset+200])
        if text.startswith('select extraction,id from psms_documents.sources'):
            if 'exists(select 1 from ppmp_ocr_drafts' in text:
                uid,category,_,_,*specific=params
                sources=[s for s in self.sources.values() if s['owner_id']==uid and (s['category']==category or
                    any(d['source_id']==s['id'] and d['ppmp_category']==category and d['status']!='CATEGORY_REVIEW'
                        for d in self.drafts.values()))]
                if specific:sources=[s for s in sources if s['id']==specific[0]]
                return Cursor([dict(extraction=s['extraction'],id=s['id']) for s in sources][-1:])
            return Cursor([dict(extraction=s['extraction'],id=s['id']) for s in reversed(list(self.sources.values()))
                if s['owner_id']==params[0] and s['category']==params[1] and (len(params)<3 or s['id']==params[2])][:1])
        if text.startswith("update ppmp_ocr_drafts set fields_json=") and "status='reviewed'" in text:
            fields,confidence,reviewer,at,updated,did=params
            self.drafts[did].update(fields_json=fields,confidence_json=confidence,reviewed_by=reviewer,reviewed_at=at,
                updated_at=updated,status='REVIEWED',reviewed=True,revision=self.drafts[did]['revision']+1)
            self.writes.append((text,params));return Cursor(rowcount=1)
        if text.startswith('insert into audit_trail') and self.fail_audit:raise RuntimeError('FICTIONAL AUDIT FAILURE')
        return super().execute(sql,params)


class MultiItemReviewRoutes(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.runtime=ActualApplication().__enter__();cls.module=cls.runtime.module
    @classmethod
    def tearDownClass(cls):cls.runtime.firewall.assert_not_called();cls.runtime.__exit__(None,None,None)

    def setUp(self):
        self.db=DraftDatabase();self.runtime.bind(self.db);self.client=self.runtime.client(self.db)
        self.stack=[]
        for target,value in (("available_excel_rows",lambda category:([],[],0)),('excel_meta',lambda:{})):
            p=patch.object(self.module,target,value);p.start();self.addCleanup(p.stop)
        page=self.client.get('/nlp').get_data(as_text=True);self.token=re.search(r'name="csrf-token" content="([^"]+)"',page)[1]
        self.content=(METADATA+'\n'+'\n'.join(' | '.join(row) for row in [HEADERS]+GROUND_TRUTH)).encode()
        self.upload()

    def upload(self,client=None,filename='fictional-three.txt'):
        with patch.object(mapper,'load_local_model',return_value=None),patch.object(mapper,'_semantic_scores',return_value={}),patch.object(mapper,'model_status',return_value={'active':True}):
            return (client or self.client).post('/ppmp/NON-DBM/upload_ocr',data={'csrf_token':self.token,'ocr_file':(BytesIO(self.content),filename)})

    def entries(self):
        return [dict(temp_id=row['temp_code'],revision=row['revision'],fields=json.loads(row['fields_json'])) for row in self.db.drafts.values()]

    def save(self,entries,client=None,**changes):
        return (client or self.client).post('/ppmp/NON-DBM',data=dict(csrf_token=self.token,action='update_ocr_rows',ocr_batch=json.dumps(entries),**changes))

    def test_normal_upload_three_rows_template_evidence_and_no_branding(self):
        self.assertEqual(len(self.db.drafts),3)
        response=self.client.get('/ppmp/NON-DBM');self.assertEqual(response.status_code,200)
        html=response.get_data(as_text=True)
        self.assertEqual(html.count('class="ocr-temp-row"'),3)
        for text in ('ocrItemTabs','reviewSelectedOCR','Compare Extracted Data with Original Document','Confirm Review','ocrSourcePreview','ocrRowsData'):
            self.assertIn(text,html)
        for text in ('Local workspace','Offline supply management'):self.assertNotIn(text,html)
        for row in self.db.drafts.values():
            self.assertEqual(row['raw_ocr_text'],self.content.decode())
            self.assertTrue(json.loads(row['confidence_json'])['_source_evidence']['text'])

    def test_batch_saves_every_correction_reopens_and_audits(self):
        entries=self.entries()
        for i,entry in enumerate(entries):entry['fields']['Remarks']=f'Fictional correction {i}'
        response=self.save(entries);self.assertEqual(response.status_code,200,response.get_data(as_text=True))
        self.assertTrue(response.json['saved']);self.assertEqual(len(response.json['items']),3)
        for i,row in enumerate(self.db.drafts.values()):
            self.assertEqual(json.loads(row['fields_json'])['Remarks'],f'Fictional correction {i}')
            self.assertEqual(row['revision'],2);self.assertTrue(row['reviewed']);self.assertFalse(row['converted'])
        self.assertEqual(sum('Reviewed Private OCR Draft' in params for sql,params in self.db.writes if 'audit_trail' in sql),3)
        self.assertIn('Fictional correction 0',self.client.get('/ppmp/NON-DBM').get_data(as_text=True))

    def test_vertical_excel_upload_saves_and_reopens_both_items(self):
        from openpyxl import Workbook
        workbook=Workbook(); sheet=workbook.active
        sheet.append(['Procurement Mode','Shopping']);sheet.append(['Source of Funds','General Fund'])
        for truth in GROUND_TRUTH[:2]:
            for label,value in zip(('Item No','Description','Specifications','Quantity','Unit','Unit Cost','Amount'),truth):
                sheet.append([label,value])
        stream=BytesIO();workbook.save(stream);workbook.close();self.content=stream.getvalue()
        existing=set(self.db.drafts)
        response=self.upload(filename='fictional-vertical.xlsx');self.assertEqual(response.status_code,302)
        added=[row for did,row in self.db.drafts.items() if did not in existing]
        self.assertEqual(len(added),2)
        entries=[]
        for row,truth in zip(added,GROUND_TRUTH):
            fields=json.loads(row['fields_json']);evidence=json.loads(row['confidence_json'])['_source_evidence']
            self.assertEqual(fields['Item Name'],truth[1]);self.assertEqual(fields['Quantity'],truth[3])
            self.assertEqual(fields[BUDGET],truth[6]);self.assertEqual(evidence['item_number'],truth[0])
            entries.append(dict(temp_id=row['temp_code'],revision=row['revision'],fields=fields))
        self.assertEqual(self.save(entries).status_code,200)
        reopened=self.client.get('/ppmp/NON-DBM').get_data(as_text=True)
        for row,truth in zip(added,GROUND_TRUTH):
            self.assertIn(row['temp_code'],reopened);self.assertIn(truth[1],reopened)
            self.assertEqual(json.loads(self.db.drafts[row['id']]['fields_json'])['Quantity'],truth[3])
            self.assertEqual(self.db.drafts[row['id']]['revision'],2)

    def test_invalid_item_returns_specific_errors_and_saves_nothing(self):
        entries=self.entries();entries[1]['fields']['Quantity']='NaN';before=deepcopy(self.db.drafts)
        response=self.save(entries);self.assertEqual(response.status_code,422)
        self.assertIn(entries[1]['temp_id'],response.json['errors']);self.assertEqual(self.db.drafts,before)

    def test_repeated_batch_and_stale_revision_rejected(self):
        entries=self.entries();self.assertEqual(self.save(entries).status_code,200);before=deepcopy(self.db.drafts)
        self.assertEqual(self.save(entries).status_code,409);self.assertEqual(self.db.drafts,before)

    def test_duplicate_ids_save_once_conflicting_duplicates_rejected(self):
        entries=self.entries();self.assertEqual(self.save(entries+[deepcopy(entries[0])]).status_code,200)
        self.assertEqual(self.db.drafts[1]['revision'],2)
        entries=self.entries();duplicate=deepcopy(entries[0]);duplicate['fields']['Remarks']='Different'
        self.assertEqual(self.save(entries+[duplicate]).status_code,400)

    def test_cross_user_ownership_and_anonymous_denied(self):
        other=self.runtime.client(self.db,uid=3);page=other.get('/nlp').get_data(as_text=True)
        token=re.search(r'name="csrf-token" content="([^"]+)"',page)[1]
        response=other.post('/ppmp/NON-DBM',data=dict(action='update_ocr_rows',csrf_token=token,ocr_batch=json.dumps(self.entries())))
        self.assertEqual(response.status_code,404)
        anonymous=self.module.app.test_client();self.assertIn(anonymous.post('/ppmp/NON-DBM').status_code,(302,400))

    def test_csrf_missing_invalid_and_cross_session_rejected(self):
        other=self.runtime.client(self.db,uid=3);page=other.get('/nlp').get_data(as_text=True)
        foreign=re.search(r'name="csrf-token" content="([^"]+)"',page)[1]
        before=deepcopy(self.db.drafts)
        for token in (None,'fictional-invalid',foreign):
            form=dict(action='update_ocr_rows',ocr_batch=json.dumps(self.entries()))
            if token:form['csrf_token']=token
            self.assertEqual(self.client.post('/ppmp/NON-DBM',data=form).status_code,400)
        self.assertEqual(self.db.drafts,before)

    def test_confirmed_or_cancelled_draft_cannot_be_overwritten(self):
        for status in ('CONVERTED_TO_PPMP','CANCELLED'):
            self.db.drafts[2]['status']=status;before=deepcopy(self.db.drafts)
            self.assertEqual(self.save(self.entries()).status_code,409);self.assertEqual(self.db.drafts,before)

    def test_audit_failure_rolls_back_all_reviews(self):
        before=deepcopy(self.db.drafts);self.db.fail_audit=True
        self.assertEqual(self.save(self.entries()).status_code,503);self.assertEqual(self.db.drafts,before)
        self.assertGreater(self.db.rollbacks,0)

    def test_same_file_retry_reuses_drafts_without_overwriting_edits(self):
        self.assertEqual(self.save(self.entries()).status_code,200);before=deepcopy(self.db.drafts)
        self.assertEqual(self.upload().status_code,302);self.assertEqual(self.db.drafts,before);self.assertEqual(len(self.db.sources),1)

    def test_missing_fields_oversized_values_and_invalid_revisions_rejected(self):
        before=deepcopy(self.db.drafts)
        for mutation in ('missing','oversized','revision'):
            entries=self.entries()
            if mutation=='missing':entries[0]['fields'].pop('Remarks')
            if mutation=='oversized':entries[0]['fields']['Remarks']='x'*5001
            if mutation=='revision':entries[0]['revision']='0'
            self.assertEqual(self.save(entries).status_code,400,mutation)
            self.assertEqual(self.db.drafts,before)

    def test_inactive_account_and_spoofed_role_cannot_save(self):
        before=deepcopy(self.db.drafts);self.db.accounts[2]['status']='Inactive'
        with self.client.session_transaction() as state:state['role']='Admin'
        self.assertIn(self.save(self.entries()).status_code,(302,403));self.assertEqual(self.db.drafts,before)

    def test_unknown_draft_and_over_limit_batch_rejected(self):
        before=deepcopy(self.db.drafts);entries=self.entries();entries[0]['temp_id']='OCR-TEMP-999999'
        self.assertEqual(self.save(entries).status_code,404)
        self.assertEqual(self.save(self.entries()*67).status_code,400)
        self.assertEqual(self.db.drafts,before)

    def test_explicit_category_mismatch_routes_to_private_category_review(self):
        before=deepcopy((self.db.drafts,self.db.sources))
        self.content=b'Category: NON-DBM\nDescription | Quantity | Amount\nSynthetic item | 2 | 10.00'
        with patch.object(mapper,'model_status',return_value={'active':True}),patch.object(mapper,'load_local_model',return_value=None),patch.object(mapper,'_semantic_scores',return_value={}):
            result=self.client.post('/ppmp/DBM/upload_ocr',data={'csrf_token':self.token,'ocr_file':(BytesIO(self.content),'synthetic-category.txt')})
        self.assertEqual(result.status_code,302)
        self.assertIn('/ppmp/intake/',result.location)
        for did,row in before[0].items():self.assertEqual(self.db.drafts[did],row)
        for sid,row in before[1].items():self.assertEqual(self.db.sources[sid],row)
        added=[d for did,d in self.db.drafts.items() if did not in before[0]]
        self.assertEqual(len(added),1)
        self.assertEqual(added[0]['status'],'CATEGORY_REVIEW')
        self.assertFalse(added[0]['reviewed']);self.assertFalse(added[0]['converted'])
        self.assertEqual(self.db.sources[added[0]['source_id']]['content'],self.content)

    def test_latest_source_scope_all_sources_and_source_ownership(self):
        old=list(self.db.drafts.values())[0]
        self.content=b'Description | Quantity | Amount\nSynthetic new upload | 1 | 10.00'
        self.assertEqual(self.upload().status_code,302)
        html=self.client.get('/ppmp/NON-DBM').get_data(as_text=True)
        self.assertEqual(html.count('class="ocr-temp-row"'),1)
        self.assertNotIn('id="ocr-'+old['temp_code']+'"',html)
        self.assertEqual(self.client.get('/ppmp/NON-DBM?ocr_source=all').get_data(as_text=True).count('class="ocr-temp-row"'),4)
        own=self.client.get('/ppmp/NON-DBM?ocr_source='+old['source_id'])
        self.assertEqual(own.get_data(as_text=True).count('class="ocr-temp-row"'),3)
        other=self.runtime.client(self.db,uid=3)
        self.assertEqual(other.get('/ppmp/NON-DBM?ocr_source='+old['source_id']).status_code,404)

    def test_new_parser_ordinary_upload_preserves_old_drafts_without_reprocessing(self):
        old_source=next(iter(self.db.sources.values()))
        old_source['extraction']['structured']['parser']='historical-parser'
        before=deepcopy(self.db.drafts)
        self.assertEqual(self.upload().status_code,302)
        self.assertEqual(len(self.db.drafts),3)
        for did,row in before.items():self.assertEqual(self.db.drafts[did],row)
        self.assertEqual(self.upload().status_code,302)
        self.assertEqual(len(self.db.drafts),3)

    def test_metadata_only_upload_retains_source_without_empty_draft(self):
        before=len(self.db.drafts)
        self.content=b'PROJECT PROCUREMENT MANAGEMENT PLAN\nSource of Funds: GF\nProcurement Mode: Shopping'
        self.assertEqual(self.upload().status_code,302)
        self.assertEqual(len(self.db.drafts),before)
        html=self.client.get('/ppmp/NON-DBM').get_data(as_text=True)
        self.assertNotIn('class="ocr-temp-row"',html)
        self.assertIn('Uncertain Source Regions',html)


def render_fixture(destination):
    case=MultiItemReviewRoutes('test_normal_upload_three_rows_template_evidence_and_no_branding')
    case.setUpClass()
    try:
        case.setUp()
        response=case.client.get('/ppmp/NON-DBM');assert response.status_code==200
        destination.write_text(response.get_data(as_text=True),encoding='utf-8')
    finally:case.doCleanups();case.tearDownClass()
