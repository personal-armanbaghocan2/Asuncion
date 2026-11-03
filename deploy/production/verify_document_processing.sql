-- PREPARED ONLY: owner-run metadata verification after migration 05.
-- Do not rerun installed migrations 03 or 04. No credentials or records returned.
-- Independently verify the project/endpoint before connecting. These booleans
-- do not establish hosted behavior, account recovery or publication approval.
BEGIN TRANSACTION READ ONLY;
SET LOCAL statement_timeout='10s';
SET LOCAL lock_timeout='2s';
SET LOCAL search_path=pg_catalog;

SELECT current_user='postgres' AS owner_connection,
       current_database()='postgres' AS expected_database,
       to_regclass('psms_accounts.profiles') IS NOT NULL AS installed_03_present,
       to_regclass('psms_sync.revisions') IS NOT NULL AS installed_04_present,
       to_regclass('psms_documents.processing_runs') IS NOT NULL AS installed_05_present;

SELECT c.relrowsecurity AS processing_rls_enabled,
       pg_get_userbyid(c.relowner)<>'psms_vercel_backend' AS backend_not_table_owner,
       NOT r.rolsuper AND NOT r.rolbypassrls AND NOT r.rolcreatedb
        AND NOT r.rolcreaterole AND NOT r.rolinherit AND NOT r.rolreplication
        AND r.rolcanlogin AS backend_restricted,
       has_schema_privilege('psms_vercel_backend','psms_documents','USAGE')
        AND NOT has_schema_privilege('psms_vercel_backend','psms_documents','CREATE')
        AS backend_schema_access_bounded,
       has_table_privilege('psms_vercel_backend',c.oid,'SELECT')
        AND NOT has_table_privilege('psms_vercel_backend',c.oid,
            'INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER') AS backend_table_access_bounded
FROM pg_class c CROSS JOIN pg_roles r
WHERE c.oid='psms_documents.processing_runs'::regclass AND r.rolname='psms_vercel_backend';

SELECT a.attname AS column_name,
       has_column_privilege('psms_vercel_backend',a.attrelid,a.attname,'SELECT')
        AND has_column_privilege('psms_vercel_backend',a.attrelid,a.attname,'INSERT') =
            (a.attname=ANY(ARRAY['id','source_id','owner_id','kind','parser_version','source_sha256']))
        AND has_column_privilege('psms_vercel_backend',a.attrelid,a.attname,'UPDATE') =
            (a.attname=ANY(ARRAY['status','completed_at','extraction','summary','error_code']))
        AND NOT has_column_privilege('psms_vercel_backend',a.attrelid,a.attname,'REFERENCES')
        AS column_privileges_match_05
FROM pg_attribute a
WHERE a.attrelid='psms_documents.processing_runs'::regclass AND a.attnum>0 AND NOT a.attisdropped
ORDER BY a.attnum;

SELECT EXISTS(SELECT 1 FROM pg_index WHERE indexrelid=to_regclass('psms_documents.one_processing_run_per_source')
        AND indrelid='psms_documents.processing_runs'::regclass AND indisunique AND indisvalid
        AND pg_get_expr(indpred,indrelid) LIKE '%PROCESSING%') AS processing_claim_index_ready,
       to_regclass('psms_documents.owner_source_processing_history') IS NOT NULL AS history_index_present,
       to_regclass('psms_documents.owner_kind_source_history') IS NOT NULL AS source_index_present,
       to_regclass('public.owner_source_ppmp_drafts') IS NOT NULL AS draft_index_present,
       EXISTS(SELECT 1 FROM pg_trigger
        WHERE tgrelid='psms_documents.processing_runs'::regclass AND tgname='psms_live_revision'
          AND NOT tgisinternal AND tgenabled='O' AND tgdeferrable AND tginitdeferred
          AND tgfoid=to_regprocedure('psms_sync.record_change()')) AS deferred_revision_trigger_ready;

-- Owner reviews the exact installed policy expressions against migration 05.
SELECT polname AS policy_name,polcmd AS command,
       pg_get_expr(polqual,polrelid) AS owner_condition,
       pg_get_expr(polwithcheck,polrelid) AS write_condition
FROM pg_policy WHERE polrelid='psms_documents.processing_runs'::regclass ORDER BY polname;

SELECT role_name,
       NOT has_schema_privilege(role_name,'psms_documents','USAGE,CREATE')
        AND NOT has_any_column_privilege(role_name,'psms_documents.processing_runs','SELECT,INSERT,UPDATE,REFERENCES')
        AND NOT has_table_privilege(role_name,'psms_documents.processing_runs','DELETE,TRUNCATE,TRIGGER')
        AS private_processing_access_denied
FROM (VALUES('anon'),('authenticated'),('service_role')) AS api(role_name)
WHERE EXISTS(SELECT 1 FROM pg_roles WHERE rolname=role_name);
ROLLBACK;
