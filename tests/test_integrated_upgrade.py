"""Focused integrated-upgrade checks: fictional documents and SQL doubles."""
from io import BytesIO
from pathlib import Path
import json
import re
import unittest
from unittest.mock import patch
from openpyxl import Workbook

from test_nlp_comparison_routes import ActualApplication, Cursor
from test_multi_item_review_routes import DraftDatabase
import offline_ocr_mapper as mapper
import ppmp_intake
import account_profiles
import ui_feedback
from PIL import Image
import request_queries
from flask import Flask


class IntakeStore(DraftDatabase):
    def execute(self,sql,params):
        text=' '.join(sql.lower().split())
        if text.startswith(('set ','select set_config')):return Cursor()
        if text.startswith('insert into ppmp_ocr_drafts') and "'category_review'" in text:
            result=super().execute(sql,params)
            self.drafts[result.rows[0]['id']]['status']='CATEGORY_REVIEW'
            return result
        if text.startswith("select id from psms_documents.sources where owner_id="):
            uid,digest=params
            return Cursor([dict(id=s['id']) for s in self.sources.values() if s['owner_id']==uid and s['sha256']==digest][:1])
        if text.startswith('select id,filename,created_at,owner_id,extraction from psms_documents.sources'):
            sid,uid=params;return Cursor([s for s in self.sources.values() if s['id']==sid and s['owner_id']==uid])
        if text.startswith('select content,extension from psms_documents.sources'):
            sid,uid=params;return Cursor([s for s in self.sources.values() if s['id']==sid and s['owner_id']==uid])
        if text.startswith('select * from ppmp_ocr_drafts where source_id='):
            sid,uid=params[:2];offset=params[2] if len(params)>2 else 0
            return Cursor([d for d in self.drafts.values() if d['source_id']==sid and d['owner_id']==uid][offset:offset+21])
        if text.startswith('select * from ppmp_ocr_drafts where id='):
            did,sid,uid=params;return Cursor([d for d in self.drafts.values() if d['id']==did and d['source_id']==sid and d['owner_id']==uid])
        if text.startswith('update ppmp_ocr_drafts set ppmp_category='):
            category,fields,confidence,at,did=params
            self.drafts[did].update(ppmp_category=category,fields_json=fields,confidence_json=confidence,updated_at=at,
                status='NEEDS_REVIEW',reviewed=False,revision=self.drafts[did]['revision']+1)
            return Cursor(rowcount=1)
        if text.startswith('select extraction,id from psms_documents.sources') and 'exists(select 1 from ppmp_ocr_drafts' in text:
            uid,category,_,_,*specific=params
            values=[s for s in self.sources.values() if s['owner_id']==uid and (s['category']==category or
                any(d['source_id']==s['id'] and d['ppmp_category']==category and d['status']!='CATEGORY_REVIEW' for d in self.drafts.values()))]
            if specific:values=[s for s in values if s['id']==specific[0]]
            return Cursor([dict(extraction=s['extraction'],id=s['id']) for s in values][-1:])
        if text.startswith('select * from ppmp_ocr_drafts where owner_id='):
            uid,category=params[:2];sid=params[2] if 'and source_id=?' in text else None
            return Cursor([d for d in self.drafts.values() if d['owner_id']==uid and d['ppmp_category']==category
                and d['status'] in ('NEEDS_REVIEW','REVIEWED') and (sid is None or d['source_id']==sid)])
        return super().execute(sql,params)


def mixed_workbook():
    book=Workbook();sheet=book.active;sheet.title='Procurement'
    sheet.append(['Item No','PPMP Category','Description','Quantity','Unit','Unit Cost','Amount'])
    for number,category in enumerate(['DBM']*2+['NON-DBM']*3+['LIB'],1):
        sheet.append([number,category,'FICTIONAL item '+str(number),number,'piece',10,number*10])
    stream=BytesIO();book.save(stream);book.close();return stream.getvalue()


class ItemIntakeTests(unittest.TestCase):
    def setUp(self):
        self.runtime=ActualApplication().__enter__();self.addCleanup(self.runtime.__exit__,None,None,None)
        self.module=self.runtime.module;self.store=IntakeStore();self.runtime.bind(self.store)
        self.content=mixed_workbook()
        self.client=self.runtime.client(self.store)
        for name,value in [('load_local_model',None),('_semantic_scores',{}),('model_status',{'active':True})]:
            manager=patch.object(mapper,name,return_value=value);manager.start();self.addCleanup(manager.stop)
        for name,value in [('available_excel_rows',([],[],0)),('excel_meta',{})]:
            manager=patch.object(self.module,name,return_value=value);manager.start();self.addCleanup(manager.stop)
        html=self.client.get('/ppmp/intake').get_data(as_text=True)
        self.token=re.search(r'name="csrf_token" value="([^"]+)"',html)[1]

    def upload(self):
        return self.client.post('/ppmp/intake',data={'csrf_token':self.token,'ocr_file':(BytesIO(self.content),'fictional-mixed.xlsx')})

    def test_six_items_single_source_explicit_category_review_and_reopen(self):
        response=self.upload();self.assertEqual(response.status_code,302,response.get_data(as_text=True))
        self.assertEqual(len(self.store.sources),1);self.assertEqual(len(self.store.drafts),6)
        source=next(iter(self.store.sources.values()))
        self.assertEqual(source['content'],self.content)
        source['extraction']['warnings']=['Synthetic manual review warning']
        self.assertEqual({d['status'] for d in self.store.drafts.values()},{'CATEGORY_REVIEW'})
        page=self.client.get(response.location)
        self.assertEqual(page.status_code,200)
        self.assertIn('Synthetic manual review warning',page.get_data(as_text=True))
        self.assertIn('Original extracted text',page.get_data(as_text=True))
        original=self.client.get(response.location+'/source')
        self.assertEqual(original.status_code,200)
        self.assertEqual(original.data,self.content)
        self.assertIn('private',original.headers['Cache-Control'])
        self.assertEqual(self.upload().location,response.location)
        self.assertEqual(len(self.store.drafts),6)
        expected=['DBM']*2+['NON-DBM']*3+['LIB']
        for draft,category in zip(self.store.drafts.values(),expected):
            confidence=json.loads(draft['confidence_json'])
            self.assertEqual(confidence['_category_suggestion']['category'],category)
            fields=json.loads(draft['fields_json'])
            form=dict(csrf_token=self.token,draft_id=draft['id'],revision=1,category=category,confirm_category='yes')
            form.update({'field_'+str(i):str(fields.get(label,'')) for i,label in enumerate(self.module.ppmp_review_labels())})
            saved=self.client.post(response.location,data=form)
            self.assertEqual(saved.status_code,302,saved.get_data(as_text=True))
            self.assertEqual(self.client.post(response.location,data=form).status_code,409)
        counts={category:sum(d['ppmp_category']==category for d in self.store.drafts.values()) for category in ppmp_intake.CATEGORIES}
        self.assertEqual(counts,{'DBM':2,'NON-DBM':3,'LIB':1})
        self.assertEqual({d['source_id'] for d in self.store.drafts.values()},{source['id']})
        self.assertEqual(sum('Assigned Reviewed Item Category' in params for sql,params in self.store.writes if 'audit_trail' in sql),6)
        for category,count in counts.items():
            page=self.client.get('/ppmp/'+category+'?ocr_source='+source['id'])
            self.assertEqual(page.status_code,200,page.get_data(as_text=True))
            self.assertEqual(page.get_data(as_text=True).count('class="ocr-temp-row"'),count)
        other=self.runtime.client(self.store,uid=3)
        self.assertEqual(other.get(response.location).status_code,404)
        self.assertEqual(other.get(response.location+'/source').status_code,404)
        self.runtime.firewall.assert_not_called()

    def test_ambiguous_category_requires_manual_choice(self):
        self.assertEqual(ppmp_intake.suggest_category({'field_confidence':{}})[0],None)
        row={'field_confidence':{'_source_evidence':{'headers':['Category','PPMP Category'],'text':'DBM | LIB'}}}
        self.assertEqual(ppmp_intake.suggest_category(row)[0],None)

    def test_pending_category_cannot_bypass_assignment_through_legacy_review(self):
        self.upload()
        draft=next(iter(self.store.drafts.values()))
        fields=json.loads(draft['fields_json'])
        form=dict(csrf_token=self.token,action='update_ocr_rows',ocr_batch=json.dumps([
            dict(temp_id=draft['temp_code'],revision=draft['revision'],fields=fields)]))
        response=self.client.post('/ppmp/'+draft['ppmp_category'],data=form)
        self.assertEqual(response.status_code,409)
        self.assertEqual(draft['status'],'CATEGORY_REVIEW')
        self.assertFalse(draft['reviewed']);self.assertFalse(draft['converted'])


class AccountStore(IntakeStore):
    def __init__(self):
        super().__init__();self.profiles={}
        for row in self.accounts.values():
            row.update(full_name='',display_name='',created_at='FICTIONAL',last_login='',password='SYNTHETIC HASH ONLY')

    def connect(self):
        inner=super().connect();store=self
        from copy import deepcopy
        before=deepcopy((self.profiles,self.accounts))
        class Connection:
            execute=inner.execute;commit=inner.commit;close=inner.close
            def rollback(self):
                inner.rollback();store.profiles,store.accounts=deepcopy(before)
        return Connection()

    def execute(self,sql,params):
        text=' '.join(sql.lower().split())
        if text=='select id,username,role from users order by lower(username)':
            return Cursor(self.accounts.values())
        if text.startswith(('set ','select set_config')):return Cursor()
        if 'from psms_accounts.profiles where user_id=' in text:
            row=self.profiles.get(params[0]);return Cursor([row] if row else [])
        if text.startswith('insert into psms_accounts.profiles'):
            self.profiles[params[0]]=dict(user_id=params[0],revision=0,updated_at='FICTIONAL',photo_content=None,photo_mime=None,photo_sha256=None)
            return Cursor(rowcount=1)
        if text.startswith('update users set full_name='):
            name,display,uid=params;self.accounts[uid].update(full_name=name,display_name=display);return Cursor(rowcount=1)
        if text.startswith('update psms_accounts.profiles'):
            row=self.profiles[params[-1]];row['revision']+=1
            if 'photo_content=' in text:row.update(zip(('photo_content','photo_mime','photo_sha256'),params[:3]))
            return Cursor(rowcount=1)
        return super().execute(sql,params)


class AccountTests(unittest.TestCase):
    def setUp(self):
        self.runtime=ActualApplication().__enter__();self.addCleanup(self.runtime.__exit__,None,None,None)
        self.module=self.runtime.module;self.store=AccountStore();self.runtime.bind(self.store)
        self.client=self.runtime.client(self.store)
        html=self.client.get('/account').get_data(as_text=True)
        self.token=re.search(r'name="csrf_token" value="([^"]+)"',html)[1]

    def test_own_name_photo_replacement_removal_and_stale_revision(self):
        result=self.client.post('/account',data=dict(csrf_token=self.token,action='profile',revision=0,full_name='Fictional Staff Member'))
        self.assertEqual(result.status_code,302)
        stream=BytesIO();Image.new('RGB',(500,300),'green').save(stream,format='PNG');content=stream.getvalue()
        def upload(revision):
            return self.client.post('/account',data=dict(csrf_token=self.token,action='photo',revision=revision,photo=(BytesIO(content),'avatar.png')))
        self.assertEqual(upload(1).status_code,302)
        avatar=self.client.get('/account/photo')
        self.assertEqual(avatar.mimetype,'image/jpeg')
        with Image.open(BytesIO(avatar.data)) as image:self.assertEqual(image.size,(256,256))
        self.assertIn('private',avatar.headers['Cache-Control'])
        self.assertEqual(self.client.get('/account/photo',headers={'If-None-Match':avatar.headers['ETag']}).status_code,304)
        self.assertEqual(upload(1).status_code,409)
        self.assertEqual(upload(2).status_code,302)
        other=self.runtime.client(self.store,uid=3)
        self.assertEqual(other.get('/account/photo?user_id=2').mimetype,'image/svg+xml')
        self.assertEqual(self.client.post('/account',data=dict(csrf_token=self.token,action='remove_photo',revision=3)).status_code,302)
        self.assertIsNone(self.store.profiles[2]['photo_content'])
        self.assertEqual(self.store.accounts[2]['full_name'],'Fictional Staff Member')
        self.runtime.firewall.assert_not_called()

    def test_readonly_metadata_and_admin_creation_tampering_are_rejected(self):
        for field in ('username','role','status','user_id','display_name'):
            response=self.client.post('/account',data=dict(csrf_token=self.token,action='profile',revision=0,full_name='Fictional',**{field:'Admin'}))
            self.assertEqual(response.status_code,400)
        admin=self.runtime.client(self.store,uid=1)
        html=admin.get('/users').get_data(as_text=True)
        token=re.search(r'name="csrf_token" value="([^"]+)"',html)[1]
        self.assertNotIn('<option>Admin</option>',html)
        response=admin.post('/users',data=dict(csrf_token=token,username='fictional_new',password='SYNTHETIC-ONLY-PASSWORD',role='Admin',action='create'))
        self.assertEqual(response.status_code,400)
        self.assertEqual(self.store.accounts[1]['role'],'Admin')
        self.assertEqual(self.store.accounts[2]['role'],'Staff')

    def test_wrong_current_password_remains_an_error(self):
        with patch.object(self.module,'check_password_hash',return_value=False),patch.object(self.module.server_sessions,'replace_password') as change:
            response=self.client.post('/account/change-password',data=dict(csrf_token=self.token,current_password='SYNTHETIC-WRONG',new_password='SYNTHETIC-NEW',confirm_password='SYNTHETIC-NEW'))
            self.assertEqual(response.status_code,302);change.assert_not_called()
        html=self.client.get('/account').get_data(as_text=True)
        self.assertIn('data-category="error"',html)

    def test_existing_administrator_keeps_credentials_role_and_session_after_profile_edit(self):
        admin=self.runtime.client(self.store,uid=1)
        page=admin.get('/account')
        self.assertEqual(page.status_code,200)
        token=re.search(r'name="csrf_token" value="([^"]+)"',page.get_data(as_text=True))[1]
        before=dict(self.store.accounts[1]);sid=self.store.sids[1]
        response=admin.post('/account',data=dict(csrf_token=token,action='profile',revision=0,full_name='Fictional Administrator'))
        self.assertEqual(response.status_code,302)
        for key in ('id','username','password','role','status'):
            self.assertEqual(self.store.accounts[1][key],before[key])
        self.assertEqual(self.store.sids[1],sid)
        self.assertEqual(admin.get('/users').status_code,200)

    def test_audit_failure_rolls_back_profile_changes(self):
        before=dict(self.store.accounts[2]);self.store.fail_audit=True
        response=self.client.post('/account',data=dict(csrf_token=self.token,action='profile',revision=0,full_name='Rejected synthetic update'))
        self.assertEqual(response.status_code,503)
        self.assertEqual(self.store.accounts[2],before)
        self.assertNotIn(2,self.store.profiles)


class FeedbackTests(unittest.TestCase):
    def test_success_error_and_information_semantics(self):
        for message,expected in [('Wrong password','error'),('No items saved. Correct values.','error'),
            ('Upload failed. Retry.','error'),('Profile updated successfully.','success'),
            ('Private review updated. No inventory changed.','success'),('Pending review.','info')]:
            self.assertEqual(ui_feedback.notification_category('message',message),expected)
        self.assertEqual(ui_feedback.notification_category('success','Operation failed.'),'error')


class SyncStore(AccountStore):
    def __init__(self):
        super().__init__();self.shared={'ppmp':'0','users':'0'};self.private={2:{'documents':'0','account':'0'},3:{'documents':'0','account':'0'}}
        self.actor=None
    def execute(self,sql,params):
        text=' '.join(sql.lower().split())
        if text.startswith("select set_config('psms.workbook_actor'"):
            self.actor=json.loads(params[0])['uid'];return Cursor()
        if text.startswith('select psms_sync.read_versions('):
            return Cursor([dict(versions={topic:(self.private.get(self.actor,{}).get(topic,'0') if topic in ('documents','account') else self.shared.get(topic,'0')) for topic in params[0]})])
        return super().execute(sql,params)


class SynchronizationTests(unittest.TestCase):
    def setUp(self):
        self.runtime=ActualApplication().__enter__();self.addCleanup(self.runtime.__exit__,None,None,None)
        self.module=self.runtime.module;self.store=SyncStore();self.runtime.bind(self.store)
        self.first=self.runtime.client(self.store,uid=2);self.second=self.runtime.client(self.store,uid=3)

    def test_two_users_observe_shared_changes_and_only_their_private_revisions(self):
        url='/api/sync/revisions?topics=ppmp,documents,account'
        response=self.first.get(url,headers={'Accept':'application/json'})
        self.assertEqual(response.status_code,200)
        self.assertEqual(response.json['versions'],dict(ppmp='0',documents='0',account='0'))
        self.assertTrue(response.headers.get('X-CSRFToken'))
        self.assertEqual(self.first.get(url,headers={'If-None-Match':response.headers['ETag']}).status_code,304)
        # This fixture represents committed database changes, not Flask memory state.
        self.store.shared['ppmp']='1';self.store.private[2]['documents']='1'
        self.assertEqual(self.first.get(url).json['versions']['documents'],'1')
        second=self.second.get(url).json['versions']
        self.assertEqual(second['ppmp'],'1');self.assertEqual(second['documents'],'0')
        self.assertNotIn('user_id',second)
        self.runtime.firewall.assert_not_called()

    def test_anonymous_revoked_and_nonadmin_requests_cannot_read_restricted_topics(self):
        self.assertEqual(self.module.app.test_client().get('/api/sync/revisions').status_code,302)
        self.assertEqual(self.first.get('/api/sync/revisions?topics=users',headers={'Accept':'application/json'}).status_code,403)
        self.assertEqual(self.first.get('/api/sync/revisions?topics=not_a_table').status_code,400)
        self.store.revoked.add(2)
        self.assertEqual(self.first.get('/api/sync/revisions').status_code,302)


class QueryPerformanceTests(unittest.TestCase):
    def test_repeated_get_reads_use_one_connection_and_one_select_then_close(self):
        metrics={'opens':0,'selects':0,'closes':0,'rollbacks':0}
        class Connection:
            def execute(self,sql,params=()):
                if sql.startswith('select '):metrics['selects']+=1
                return Cursor([dict(id=1)])
            def close(self):metrics['closes']+=1
            def rollback(self):metrics['rollbacks']+=1
        def factory():metrics['opens']+=1;return Connection()
        # Baseline is the preceding q() implementation: a connection/read per call.
        for _ in range(9):
            con=factory();con.execute('select id from synthetic where id=?',(1,)).fetchall();con.close()
        baseline=dict(metrics)
        app=Flask('query-performance');app.teardown_request(request_queries.close)
        with app.test_request_context('/'):
            for _ in range(9):
                row=request_queries.read(factory,'select id from synthetic where id=?',(1,),True)
                self.assertEqual(row['id'],1);row['id']=99
        self.assertEqual((baseline['opens'],baseline['selects']),(9,9))
        self.assertEqual((metrics['opens']-baseline['opens'],metrics['selects']-baseline['selects']),(1,1))
        self.assertEqual(metrics['rollbacks'],1)
        with app.test_request_context('/'):
            request_queries.read(factory,'select id from synthetic where id=?',(1,),True)
        self.assertEqual(metrics['opens'],11)  # no cross-request cache or connection


class PhotoValidationTests(unittest.TestCase):
    def test_active_content_animation_and_oversized_uploads_are_rejected(self):
        from werkzeug.datastructures import FileStorage
        from werkzeug.exceptions import BadRequest,RequestEntityTooLarge
        for data in (b'<svg onload="alert(1)"/>',b'not an image'):
            with self.assertRaises(BadRequest):account_profiles.image_bytes(FileStorage(stream=BytesIO(data),filename='photo.png'))
        with self.assertRaises(RequestEntityTooLarge):
            account_profiles.image_bytes(FileStorage(stream=BytesIO(b'x'*(account_profiles.MAX_UPLOAD+1)),filename='photo.jpg'))


class ProductionIntegrationContracts(unittest.TestCase):
    root=Path(__file__).resolve().parents[1]

    def test_owner_migrations_are_additive_guarded_and_private(self):
        account=(self.root/'deploy/production/03_account_profiles.sql').read_text()
        sync=(self.root/'deploy/production/04_live_revisions.sql').read_text()
        for text in (account,sync):
            self.assertIn("psms.upgrade_owner_approved='NO'",text)
            self.assertIn("psms.upgrade_no_backup_risk_accepted='NO'",text)
            self.assertLess(text.index('SET LOCAL search_path=pg_catalog'),text.index('DO $review$'))
            self.assertIn("ARRAY['anon','authenticated','service_role']",text)
            self.assertIn('Unexpected inherited',text)
            self.assertNotRegex(text,r'(?im)^\s*(UPDATE|DELETE FROM|TRUNCATE|DROP)\s+public\.users\b')
        self.assertIn('IF admin_count<>1 THEN',account)
        self.assertIn("role='Admin' AND status='Active'",account)
        self.assertIn('CREATE UNIQUE INDEX psms_single_designated_admin',account)
        self.assertIn('user_id=psms_workbooks.active_actor_id()',account)
        self.assertIn('ENABLE ROW LEVEL SECURITY',account)
        self.assertNotRegex(account,r'(?i)GRANT\s+.*\b(password|role|status)\b.*ON public\.users')
        self.assertIn('REVOKE ALL ON psms_sync.revisions FROM psms_vercel_backend',sync)
        self.assertIn('GRANT EXECUTE ON FUNCTION psms_sync.read_versions(text[]) TO psms_vercel_backend',sync)
        self.assertIn('DEFERRABLE INITIALLY DEFERRED',sync)
        self.assertIn("topic IN ('documents','account') THEN uid::text",sync)
        self.assertIn("topic='users' AND actor_role<>'Admin'",sync)

    def test_login_identity_shared_avatar_and_production_delivery_closure(self):
        from deploy.online_delivery import source_files
        from deploy.build_vercel_assets import ASSETS
        files=source_files(self.root)
        for name in ('account_profiles.py','live_updates.py','ppmp_intake.py','request_queries.py','ui_feedback.py',
                     'templates/ppmp_intake.html','templates/operation_error.html','templates/public_shell.html',
                     'templates/record_pagination.html','static/css/integrated.css','static/js/live-updates.js',
                     'static/images/asuncion_hall.jpg'):
            self.assertIn(name,files)
        self.assertIn('images/asuncion_hall.jpg',ASSETS)
        self.assertIn("filename='images/asuncion_hall.jpg'",(self.root/'templates/login.html').read_text())
        with Image.open(self.root/'static/images/asuncion_hall.jpg') as hall:
            self.assertEqual(hall.format,'JPEG');hall.verify()
        base=(self.root/'templates/base.html').read_text()
        self.assertEqual(base.count("url_for('account_photo')"),2)
        self.assertIn('profile-avatar',(self.root/'templates/dashboard.html').read_text())
