-- Run in the SQL Editor of project fenrqjzupidswgrjzdgn only.
-- SQL cannot attest the Supabase project reference: verify the dashboard URL.
-- Metadata only. No credentials, account rows, capability tokens or token hashes.
-- Run before installation, and again afterward to verify the exact contracts.
BEGIN READ ONLY;
SET LOCAL search_path=pg_catalog;
SET LOCAL statement_timeout='15s';
WITH reused(signature,digest,owner_name,definer) AS (VALUES ('psms_aoq_api.unicode_v1(text,text)','44313906a1b45464ab993b6bbac09bc4','psms_aoq_function_owner',false),
('psms_aoq_api.space_v1(text)','afd860e9a3c02188eea533d519e0ff69','psms_aoq_function_owner',false),
('psms_aoq_api.strip_v1(text)','bdc877e1a9cfa751ca83cfba65e320b5','psms_aoq_function_owner',false),
('psms_aoq_api.supplier_key_v1(text)','8f1f9443d16b2ca804470b8497eeaf65','psms_aoq_function_owner',false),
('psms_aoq_api.number_v1(jsonb)','670397718628ce05bede175e68c462ca','psms_aoq_function_owner',false),
('psms_aoq_api.round2_v1(double precision)','67b1a5ca05d78b9a2270b36f5fea39bd','psms_aoq_function_owner',false),
('psms_aoq_api.shortest_decimal_v1(double precision)','4f35923975c1f814e0819312a90362da','psms_aoq_function_owner',false),
('psms_aoq_api.signature_v1(jsonb)','6254f90a3ab7a2b649ee71805a91d8a5','psms_aoq_function_owner',false),
('psms_aoq_api.save_reviewed_aoq_v1(uuid,bigint,text,integer,jsonb,jsonb,text,text,boolean)','5bf078a7d06ff7a1670b4f49300892fa','psms_aoq_function_owner',true),
('psms_aoq_api.approval_actor_v1()','d38444a829b882fb720f20d0ede9b00f','psms_aoq_approval_owner',false),
('psms_aoq_api.workflow_offer_v1(text,text,double precision)','c8ff1db2992ce0dcc32bbc0cf5ee790e','psms_aoq_approval_owner',false),
('psms_aoq_api.approve_reviewed_aoq_v1(bigint,boolean,boolean)','521dd8ec4a53ce228de82c7590b72d68','psms_aoq_approval_owner',true),
('psms_aoq_api.create_po_from_approved_aoq_v1(bigint)','f542c632521843b21917c79cec79667e','psms_aoq_approval_owner',true),
('psms_aoq_api.save_reviewed_aoq_independent_v1(uuid,bigint,text,integer,jsonb,jsonb,text,text,boolean)','cf8f6bed992ac696d8d73b620d1e8dd7','psms_aoq_function_owner',true),
('psms_aoq_api.create_pr_from_approved_app_v1(bigint,text,text,double precision)','89c61242f89b763681d9ec79368cfdaa','psms_aoq_approval_owner',true),
('psms_aoq_api.approve_pr_v1(bigint,boolean)','58570b0dd287584358d2785ff5c5d6fa','psms_aoq_approval_owner',true),
('psms_aoq_api.lock_po_for_workflow_v2(bigint,text)','f138be8c07b46898bc608e8ad53cda24','psms_aoq_approval_owner',true),
('psms_aoq_api.create_po_for_review_v2(bigint)','621131067c270614afee4e7289bebc48','psms_aoq_approval_owner',true),
('psms_aoq_api.save_pr_po_verification_v1(bigint,bigint,text,text,text,double precision,jsonb,text,text)','aee19be18bf46c956aadce7d450bb172','psms_aoq_approval_owner',true)),
scoped(signature,digest,definer,backend_execute_expected) AS (VALUES ('psms_revised_release.policy_v1()','708461349509a23a532125c1cadf6b85',true,true),
('psms_revised_release.require_release_v1()','6ca34abf0edf396ecbcff9dff7da762f',true,false),
('psms_revised_release.save_reviewed_aoq_v1(uuid,bigint,text,integer,jsonb,jsonb,text,text,boolean)','eeae5fc9952cf8451caa4b490c7b87d7',true,true),
('psms_revised_release.save_reviewed_aoq_independent_v1(uuid,bigint,text,integer,jsonb,jsonb,text,text,boolean)','4c777bd267f8cffcc1fe561c488b786a',true,true),
('psms_revised_release.approve_reviewed_aoq_v1(bigint,boolean,boolean)','8fc82bd7edf6c42b977afb335ccd84a6',true,true),
('psms_revised_release.create_pr_from_approved_app_v1(bigint,text,text,double precision)','364e99e5ba8b7ff1ffe0fc6b5d9564e4',true,true),
('psms_revised_release.approve_pr_v1(bigint,boolean)','97f3f8d630dbb518f77567edddf5e9ae',true,true),
('psms_revised_release.create_po_for_review_v2(bigint)','ec92dd74b7f1f912c1cdb0a5018143d7',true,true),
('psms_revised_release.save_pr_po_verification_v1(bigint,bigint,text,text,text,double precision,jsonb,text,text)','80aa55fa3f138fcb7da88b97e0ba596b',true,true),
('psms_revised_release.lock_po_for_workflow_v2(bigint,text)','a5485a580e4bf536e102524d520a77db',true,true),
('psms_revised_release.require_saved_technical_match_v2(text,jsonb,jsonb,jsonb)','de6422cdbd430db0b6999fb9579cb26a',false,false),
('psms_revised_release.review_pr_po_verification_v1(bigint,jsonb,jsonb,text,text,boolean)','44d516b9109b982bb0c5a682bedab17a',true,true),
('psms_revised_release.approve_po_after_verification_v1(bigint,boolean,boolean)','a8c1f7f13891cacf08e6d4fca588197c',true,true)),
backend AS (SELECT oid FROM pg_roles WHERE rolname='psms_vercel_backend')
SELECT jsonb_pretty(jsonb_build_object(
 'expected_project_ref','fenrqjzupidswgrjzdgn',
 'server_version_num',current_setting('server_version_num')::integer,
 'operator',jsonb_build_object('current_user',current_user,'session_user',session_user,
   'installer_identity_matches',current_user='postgres' AND session_user='postgres'),
 'global_policy',(SELECT policy FROM psms_documents.procurement_policy WHERE singleton),
 'scoped_schema_exists',to_regnamespace('psms_revised_release') IS NOT NULL,
 'reused_contracts',(SELECT jsonb_agg(jsonb_build_object(
   'signature',r.signature,'exists',f.oid IS NOT NULL,
   'source_matches',md5(replace(f.prosrc,chr(13)||chr(10),chr(10)))=r.digest,
   'owner_matches',pg_get_userbyid(f.proowner)=r.owner_name,
   'definer_matches',f.prosecdef=r.definer,
   'backend_execute',has_function_privilege((SELECT oid FROM backend),f.oid,'EXECUTE'),
   'acl',(SELECT jsonb_agg(jsonb_build_object('role',CASE WHEN a.grantee=0 THEN 'PUBLIC'
       ELSE pg_get_userbyid(a.grantee) END,'privilege',a.privilege_type,'grantable',a.is_grantable)
       ORDER BY a.grantee,a.privilege_type)
     FROM aclexplode(coalesce(f.proacl,acldefault('f',f.proowner))) a))
   ORDER BY r.signature) FROM reused r LEFT JOIN pg_proc f ON f.oid=to_regprocedure(r.signature)),
 'scoped_contracts',(SELECT jsonb_agg(jsonb_build_object(
   'signature',s.signature,'exists',f.oid IS NOT NULL,
   'source_matches',md5(replace(f.prosrc,chr(13)||chr(10),chr(10)))=s.digest,
   'owner_matches',pg_get_userbyid(f.proowner)='psms_aoq_approval_owner',
   'definer_matches',f.prosecdef=s.definer,
   'backend_execute',has_function_privilege((SELECT oid FROM backend),f.oid,'EXECUTE'),
   'backend_execute_expected',s.backend_execute_expected,
   'settings',ARRAY(SELECT setting FROM unnest(f.proconfig) setting
     WHERE setting ~ '^(search_path|row_security|TimeZone|lc_time|standard_conforming_strings|extra_float_digits|lock_timeout)='),
   'unexpected_setting_keys',ARRAY(SELECT split_part(setting,'=',1) FROM unnest(f.proconfig) setting
     WHERE setting !~ '^(search_path|row_security|TimeZone|lc_time|standard_conforming_strings|extra_float_digits|lock_timeout)='),
   'acl',(SELECT jsonb_agg(jsonb_build_object('role',CASE WHEN a.grantee=0 THEN 'PUBLIC'
       ELSE pg_get_userbyid(a.grantee) END,'privilege',a.privilege_type,'grantable',a.is_grantable)
       ORDER BY a.grantee,a.privilege_type)
     FROM aclexplode(coalesce(f.proacl,acldefault('f',f.proowner))) a))
   ORDER BY s.signature) FROM scoped s LEFT JOIN pg_proc f ON f.oid=to_regprocedure(s.signature)),
 'roles',(SELECT jsonb_agg(jsonb_build_object('name',r.rolname,'login',r.rolcanlogin,
   'super',r.rolsuper,'bypassrls',r.rolbypassrls,'inherit',r.rolinherit,
   'createrole',r.rolcreaterole,'createdb',r.rolcreatedb,
   'operator_can_set',pg_has_role(current_user,r.oid,'SET')) ORDER BY r.rolname)
   FROM pg_roles r WHERE r.rolname IN
   ('psms_vercel_backend','psms_aoq_function_owner','psms_aoq_approval_owner')),
 'owner_memberships',(SELECT jsonb_agg(jsonb_build_object('owner_role',r.rolname,
   'member',pg_get_userbyid(m.member),'grantor',pg_get_userbyid(m.grantor),
   'admin',m.admin_option,'inherit',m.inherit_option,'set',m.set_option)
   ORDER BY r.rolname,m.member,m.grantor) FROM pg_auth_members m
   JOIN pg_roles r ON r.oid=m.roleid WHERE r.rolname IN
   ('psms_vercel_backend','psms_aoq_function_owner','psms_aoq_approval_owner')),
 'protected_backend_writes',(SELECT jsonb_agg(jsonb_build_object('relation',c.relname,
   'table_writes',has_table_privilege((SELECT oid FROM backend),c.oid,'INSERT,UPDATE,DELETE,TRUNCATE'),
   'column_insert',has_any_column_privilege((SELECT oid FROM backend),c.oid,'INSERT'),
   'column_update',has_any_column_privilege((SELECT oid FROM backend),c.oid,'UPDATE'))
   ORDER BY c.relname) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
   WHERE n.nspname='public' AND c.relname IN ('aoq','po','nlp')),
 'registry',(SELECT jsonb_build_object('owner',pg_get_userbyid(c.relowner),
   'rls',c.relrowsecurity,
   'backend_table_access',has_table_privilege((SELECT oid FROM backend),c.oid,'SELECT,INSERT,UPDATE,DELETE,TRUNCATE'),
   'backend_column_select',has_any_column_privilege((SELECT oid FROM backend),c.oid,'SELECT'),
   'backend_column_insert',has_any_column_privilege((SELECT oid FROM backend),c.oid,'INSERT'),
   'backend_column_update',has_any_column_privilege((SELECT oid FROM backend),c.oid,'UPDATE'),
   'acl',(SELECT jsonb_agg(jsonb_build_object('role',CASE WHEN a.grantee=0 THEN 'PUBLIC'
       ELSE pg_get_userbyid(a.grantee) END,'privilege',a.privilege_type,'grantable',a.is_grantable)
       ORDER BY a.grantee,a.privilege_type)
     FROM aclexplode(coalesce(c.relacl,acldefault('r',c.relowner))) a),
   'policies',(SELECT jsonb_agg(jsonb_build_object('name',p.polname,'command',p.polcmd,
       'roles',p.polroles,'using',pg_get_expr(p.polqual,p.polrelid),
       'check',pg_get_expr(p.polwithcheck,p.polrelid)) ORDER BY p.polname)
     FROM pg_policy p WHERE p.polrelid=c.oid))
   FROM pg_class c WHERE c.oid=to_regclass('psms_revised_release.deployments')),
 'backup_restore_status','Not attested by this SQL diagnostic'
)) AS release_scope_catalog;
ROLLBACK;
