-- Owner-run verification only. Run after 03 and 04 in the reviewed project.
-- Returns booleans; never reads credentials, session tokens or image contents.
BEGIN TRANSACTION READ ONLY;
SET LOCAL statement_timeout='10s';
SET LOCAL lock_timeout='2s';
SET LOCAL search_path=pg_catalog;
SELECT current_user='postgres' AS owner_connection,
       current_database()='postgres' AS expected_database,
       (SELECT count(*)=1 AND count(*) FILTER(WHERE status='Active')=1
        FROM public.users WHERE role='Admin') AS one_existing_active_administrator,
       EXISTS(SELECT 1 FROM pg_index WHERE indexrelid='public.psms_single_designated_admin'::regclass
              AND indisunique AND indisvalid) AS administrator_index_ready,
       EXISTS(SELECT 1 FROM pg_trigger WHERE tgrelid='public.users'::regclass
              AND tgname='psms_preserve_administrator' AND tgenabled='O') AS administrator_guard_ready,
       (SELECT relrowsecurity FROM pg_class WHERE oid='psms_accounts.profiles'::regclass) AS private_profiles_rls,
       (SELECT relrowsecurity FROM pg_class WHERE oid='psms_sync.revisions'::regclass) AS private_revisions_rls,
       has_function_privilege('psms_vercel_backend','psms_sync.read_versions(text[])','EXECUTE') AS backend_polling_ready,
       NOT has_any_column_privilege('psms_vercel_backend','psms_sync.revisions','SELECT,INSERT,UPDATE,REFERENCES')
        AND NOT has_table_privilege('psms_vercel_backend','psms_sync.revisions','DELETE,TRUNCATE,TRIGGER')
        AS backend_revision_table_denied;
SELECT role_name,
       NOT has_schema_privilege(role_name,'psms_accounts','USAGE')
        AND NOT has_any_column_privilege(role_name,'psms_accounts.profiles','SELECT,INSERT,UPDATE,REFERENCES')
        AND NOT has_table_privilege(role_name,'psms_accounts.profiles','DELETE,TRUNCATE,TRIGGER')
        AND NOT has_schema_privilege(role_name,'psms_sync','USAGE')
        AND NOT has_any_column_privilege(role_name,'psms_sync.revisions','SELECT,INSERT,UPDATE,REFERENCES')
        AND NOT has_table_privilege(role_name,'psms_sync.revisions','DELETE,TRUNCATE,TRIGGER')
        AND NOT has_function_privilege(role_name,'psms_sync.read_versions(text[])','EXECUTE') AS private_access_denied
FROM (VALUES('anon'),('authenticated'),('service_role')) AS api(role_name)
WHERE EXISTS(SELECT 1 FROM pg_roles WHERE rolname=role_name);
SELECT n.nspname AS schema_name,c.relname AS table_name,t.tgdeferrable AND t.tginitdeferred AND t.tgenabled='O'
       AS deferred_revision_trigger_ready
FROM pg_trigger t JOIN pg_class c ON c.oid=t.tgrelid JOIN pg_namespace n ON n.oid=c.relnamespace
WHERE t.tgname='psms_live_revision'
ORDER BY n.nspname,c.relname;
ROLLBACK;
