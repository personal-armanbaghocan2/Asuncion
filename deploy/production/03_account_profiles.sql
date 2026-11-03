-- Owner-run, additive account upgrade. Never run during application startup.
-- Inspect the exact SQL and target before changing approval to YES.
BEGIN;
SET LOCAL lock_timeout='5s';
SET LOCAL statement_timeout='60s';
SET LOCAL search_path=pg_catalog;
SET LOCAL psms.upgrade_owner_approved='NO';
SET LOCAL psms.upgrade_project_ref='fenrqjzupidswgrjzdgn';
SET LOCAL psms.upgrade_database='postgres';
SET LOCAL psms.upgrade_recovery_point='';
SET LOCAL psms.upgrade_no_backup_risk_accepted='NO';
DO $review$
DECLARE backend record; admin_count bigint;
BEGIN
 IF current_user<>'postgres' OR current_setting('psms.upgrade_owner_approved')<>'YES'
    OR current_database()<>current_setting('psms.upgrade_database')
    OR current_setting('psms.upgrade_project_ref')<>'fenrqjzupidswgrjzdgn'
    OR current_setting('psms.upgrade_no_backup_risk_accepted') NOT IN ('NO','YES')
    OR current_setting('psms.upgrade_recovery_point') ~* '(REPLACE|PLACEHOLDER|[<>])'
    OR (nullif(btrim(current_setting('psms.upgrade_recovery_point')),'') IS NULL
        AND current_setting('psms.upgrade_no_backup_risk_accepted')<>'YES') THEN
   RAISE EXCEPTION 'Owner approval, correct existing project/database and recovery decision required';
 END IF;
 IF to_regprocedure('psms_workbooks.active_actor_id()') IS NULL THEN
   RAISE EXCEPTION 'Installed workbook/session identity bridge required';
 END IF;
 SELECT * INTO STRICT backend FROM pg_catalog.pg_roles WHERE rolname='psms_vercel_backend';
 IF NOT backend.rolcanlogin OR backend.rolsuper OR backend.rolbypassrls OR backend.rolcreatedb
    OR backend.rolcreaterole OR backend.rolinherit OR backend.rolreplication
    OR EXISTS(SELECT 1 FROM pg_catalog.pg_namespace WHERE nspowner=backend.oid)
    OR EXISTS(SELECT 1 FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
              WHERE c.relowner=backend.oid AND n.nspname IN ('public','psms_documents','psms_workbooks','psms_accounts'))
    OR EXISTS(SELECT 1 FROM pg_catalog.pg_auth_members m WHERE m.member=backend.oid
       OR (m.roleid=backend.oid AND NOT(m.member='postgres'::regrole::oid AND m.admin_option))) THEN
   RAISE EXCEPTION 'Restricted backend role or administrative membership contract failed';
 END IF;
 LOCK TABLE public.users IN SHARE ROW EXCLUSIVE MODE;
 SELECT count(*) INTO admin_count FROM public.users WHERE role='Admin';
 IF admin_count<>1 THEN
   RAISE EXCEPTION 'Expected exactly one existing Admin; found %. No accounts were changed.',admin_count;
 END IF;
 IF NOT EXISTS(SELECT 1 FROM public.users WHERE role='Admin' AND status='Active') THEN
   RAISE EXCEPTION 'The existing Admin must already be active; no account activation is automatic';
 END IF;
END;$review$;

CREATE UNIQUE INDEX psms_single_designated_admin ON public.users(role) WHERE role='Admin';
CREATE SCHEMA psms_accounts;
REVOKE ALL ON SCHEMA psms_accounts FROM PUBLIC;
CREATE TABLE psms_accounts.profiles(
 user_id bigint PRIMARY KEY REFERENCES public.users(id),
 photo_content bytea, photo_mime text, photo_sha256 text,
 revision bigint NOT NULL DEFAULT 0 CHECK(revision>=0),
 updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 CHECK((photo_content IS NULL AND photo_mime IS NULL AND photo_sha256 IS NULL)
    OR (photo_content IS NOT NULL AND octet_length(photo_content) BETWEEN 1 AND 262144
      AND photo_mime='image/jpeg' AND photo_sha256 ~ '^[0-9a-f]{64}$'
      AND pg_catalog.encode(pg_catalog.sha256(photo_content),'hex')=photo_sha256))
);
ALTER TABLE psms_accounts.profiles ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON psms_accounts.profiles FROM PUBLIC;
GRANT USAGE ON SCHEMA psms_accounts TO psms_vercel_backend;
GRANT SELECT,INSERT,UPDATE ON psms_accounts.profiles TO psms_vercel_backend;
GRANT UPDATE(full_name,display_name) ON public.users TO psms_vercel_backend;
CREATE POLICY own_profile_read ON psms_accounts.profiles FOR SELECT TO psms_vercel_backend
 USING(user_id=psms_workbooks.active_actor_id());
CREATE POLICY own_profile_insert ON psms_accounts.profiles FOR INSERT TO psms_vercel_backend
 WITH CHECK(user_id=psms_workbooks.active_actor_id());
CREATE POLICY own_profile_update ON psms_accounts.profiles FOR UPDATE TO psms_vercel_backend
 USING(user_id=psms_workbooks.active_actor_id()) WITH CHECK(user_id=psms_workbooks.active_actor_id());

CREATE FUNCTION psms_accounts.preserve_administrator() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog AS $guard$
BEGIN
 IF TG_OP='DELETE' THEN
   IF OLD.role='Admin' THEN RAISE EXCEPTION 'The designated administrator cannot be removed'; END IF;
   RETURN OLD;
 END IF;
 IF TG_OP='INSERT' AND NEW.role='Admin' THEN RAISE EXCEPTION 'Additional administrators are prohibited'; END IF;
 IF TG_OP='UPDATE' AND NEW.role IS DISTINCT FROM OLD.role
    AND (OLD.role='Admin' OR NEW.role='Admin') THEN
   RAISE EXCEPTION 'The designated administrator cannot be changed or replaced';
 END IF;
 IF TG_OP='UPDATE' AND OLD.role='Admin' AND NEW.status IS DISTINCT FROM 'Active' THEN
   RAISE EXCEPTION 'The designated administrator cannot be disabled by account updates';
 END IF;
 RETURN NEW;
END;$guard$;
REVOKE ALL ON FUNCTION psms_accounts.preserve_administrator() FROM PUBLIC;
REVOKE ALL ON FUNCTION psms_accounts.preserve_administrator() FROM psms_vercel_backend;
CREATE TRIGGER psms_preserve_administrator BEFORE INSERT OR UPDATE OF role,status OR DELETE ON public.users
 FOR EACH ROW EXECUTE FUNCTION psms_accounts.preserve_administrator();
DO $private$
DECLARE api_role text;
BEGIN
 FOREACH api_role IN ARRAY ARRAY['anon','authenticated','service_role'] LOOP
  IF EXISTS(SELECT 1 FROM pg_roles WHERE rolname=api_role) THEN
   EXECUTE format('REVOKE ALL ON SCHEMA psms_accounts FROM %I',api_role);
   EXECUTE format('REVOKE ALL ON TABLE psms_accounts.profiles FROM %I',api_role);
   EXECUTE format('REVOKE ALL ON FUNCTION psms_accounts.preserve_administrator() FROM %I',api_role);
   IF has_schema_privilege(api_role,'psms_accounts','USAGE')
      OR has_any_column_privilege(api_role,'psms_accounts.profiles','SELECT,INSERT,UPDATE,REFERENCES')
      OR has_table_privilege(api_role,'psms_accounts.profiles','DELETE,TRUNCATE,TRIGGER')
      OR has_function_privilege(api_role,'psms_accounts.preserve_administrator()','EXECUTE') THEN
    RAISE EXCEPTION 'Unexpected inherited account API privilege requires owner review';
   END IF;
  END IF;
 END LOOP;
END;$private$;
COMMIT;
