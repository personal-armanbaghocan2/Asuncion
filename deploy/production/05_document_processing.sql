-- PREPARED ONLY: owner-run additive migration after reviewed 03 and 04.
-- Never execute during application startup, requests, builds or deployment.
-- Review this WHOLE transaction and the real recovery decision before approval.
-- Verify the actual existing project/endpoint independently: these nonsecret
-- project/database settings are metadata assertions, not proof of the target.
-- Forward: add run history; leave immutable sources and existing drafts intact.
-- Rollback: roll back this transaction on error. After installation, roll back
-- the application without dropping run history or restoring over newer records.
-- A successful rerun is deliberately refused; inspect existing objects instead.
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
DECLARE backend record;
BEGIN
 IF current_user<>'postgres' OR current_setting('psms.upgrade_owner_approved')<>'YES'
    OR current_database()<>current_setting('psms.upgrade_database')
    OR current_setting('psms.upgrade_project_ref')<>'fenrqjzupidswgrjzdgn'
    OR current_setting('psms.upgrade_no_backup_risk_accepted') NOT IN ('NO','YES')
    OR current_setting('psms.upgrade_recovery_point') ~* '(REPLACE|PLACEHOLDER|[<>])'
    OR (nullif(btrim(current_setting('psms.upgrade_recovery_point')),'') IS NULL
        AND current_setting('psms.upgrade_no_backup_risk_accepted')<>'YES') THEN
   RAISE EXCEPTION 'Owner approval, independently identified existing target and recovery decision required';
 END IF;
 IF to_regclass('psms_accounts.profiles') IS NULL
    OR to_regclass('psms_sync.revisions') IS NULL
    OR to_regclass('psms_documents.sources') IS NULL
    OR to_regclass('public.ppmp_ocr_drafts') IS NULL
    OR to_regprocedure('psms_workbooks.active_actor_id()') IS NULL
    OR to_regprocedure('psms_sync.read_versions(text[])') IS NULL
    OR to_regprocedure('psms_sync.record_change()') IS NULL THEN
   RAISE EXCEPTION 'Reviewed 03 account, 04 synchronization and existing document/session prerequisites required';
 END IF;
 IF to_regclass('psms_documents.processing_runs') IS NOT NULL THEN
   RAISE EXCEPTION 'Processing history already exists; inspect the installed migration without replacing it';
 END IF;
 IF NOT EXISTS(SELECT 1 FROM pg_catalog.pg_class
        WHERE oid='psms_documents.sources'::regclass AND relrowsecurity)
    OR NOT EXISTS(SELECT 1 FROM pg_catalog.pg_trigger
        WHERE tgrelid='psms_documents.sources'::regclass AND tgname='source_immutable'
          AND NOT tgisinternal AND tgenabled IN ('O','A'))
    OR NOT EXISTS(SELECT 1 FROM pg_catalog.pg_proc
        WHERE oid=to_regprocedure('psms_workbooks.active_actor_id()')
          AND prorettype='bigint'::regtype AND NOT prosecdef)
    OR NOT EXISTS(SELECT 1 FROM pg_catalog.pg_proc
        WHERE oid=to_regprocedure('psms_sync.record_change()')
          AND prorettype='trigger'::regtype AND prosecdef) THEN
   RAISE EXCEPTION 'Existing immutable source, identity bridge or synchronization contract requires review';
 END IF;
 SELECT oid,rolcanlogin,rolsuper,rolbypassrls,rolcreatedb,rolcreaterole,rolinherit,rolreplication
   INTO STRICT backend FROM pg_catalog.pg_roles WHERE rolname='psms_vercel_backend';
 IF NOT backend.rolcanlogin OR backend.rolsuper OR backend.rolbypassrls OR backend.rolcreatedb
    OR backend.rolcreaterole OR backend.rolinherit OR backend.rolreplication
    OR EXISTS(SELECT 1 FROM pg_catalog.pg_database WHERE datdba=backend.oid)
    OR EXISTS(SELECT 1 FROM pg_catalog.pg_namespace WHERE nspowner=backend.oid)
    OR EXISTS(SELECT 1 FROM pg_catalog.pg_class WHERE relowner=backend.oid)
    OR EXISTS(SELECT 1 FROM pg_catalog.pg_proc WHERE proowner=backend.oid)
    OR EXISTS(SELECT 1 FROM pg_catalog.pg_type WHERE typowner=backend.oid)
    OR EXISTS(SELECT 1 FROM pg_catalog.pg_auth_members m WHERE m.member=backend.oid
       OR (m.roleid=backend.oid AND NOT(m.member='postgres'::regrole::oid AND m.admin_option)))
    OR has_database_privilege('psms_vercel_backend',current_database(),'CREATE')
    OR EXISTS(SELECT 1 FROM pg_catalog.pg_namespace n
       WHERE has_schema_privilege('psms_vercel_backend',n.oid,'CREATE')) THEN
   RAISE EXCEPTION 'Restricted nonowner backend, no outgoing memberships and no database/schema CREATE required';
 END IF;
 -- Preserve the established incoming postgres administrative edge. It gives
 -- the backend no membership in postgres or any other role. Database TEMP is
 -- allowed; this migration never grants database or schema CREATE privileges.
 IF NOT has_function_privilege('psms_vercel_backend','psms_workbooks.active_actor_id()','EXECUTE')
    OR NOT has_schema_privilege('psms_vercel_backend','psms_workbooks','USAGE')
    OR NOT has_column_privilege('psms_vercel_backend','psms_documents.sources','id','SELECT')
    OR NOT has_column_privilege('psms_vercel_backend','psms_documents.sources','owner_id','SELECT')
    OR NOT has_column_privilege('psms_vercel_backend','psms_documents.sources','kind','SELECT')
    OR NOT has_column_privilege('psms_vercel_backend','psms_documents.sources','sha256','SELECT') THEN
   RAISE EXCEPTION 'Existing backend identity/source privileges must already be reviewed and installed';
 END IF;
END;$review$;

CREATE TABLE psms_documents.processing_runs(
 id uuid PRIMARY KEY,
 source_id uuid NOT NULL REFERENCES psms_documents.sources(id),
 owner_id bigint NOT NULL REFERENCES public.users(id),
 kind text NOT NULL CHECK(kind IN ('ppmp','aoq')),
 parser_version text NOT NULL CHECK(length(parser_version)<=100 AND length(btrim(parser_version))>=1),
 source_sha256 char(64) NOT NULL CHECK(source_sha256 ~ '^[0-9a-f]{64}$'),
 status text NOT NULL DEFAULT 'PROCESSING' CHECK(status IN ('PROCESSING','COMPLETE','INCOMPLETE','FAILED')),
 started_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 completed_at timestamptz,
 extraction jsonb NOT NULL DEFAULT '{}'::jsonb
   CHECK(jsonb_typeof(extraction)='object' AND octet_length(extraction::text)<=4194304),
 summary jsonb NOT NULL DEFAULT '{}'::jsonb
   CHECK(jsonb_typeof(summary)='object' AND octet_length(summary::text)<=65536),
 error_code varchar(80) CHECK(error_code IS NULL OR error_code ~ '^[A-Z][A-Z0-9_]{0,79}$'),
 CHECK((status='PROCESSING')=(completed_at IS NULL))
);
-- One durable claim per immutable source prevents simultaneous reprocessing.
CREATE UNIQUE INDEX one_processing_run_per_source ON psms_documents.processing_runs(source_id)
 WHERE status='PROCESSING';
CREATE INDEX owner_source_processing_history ON psms_documents.processing_runs(owner_id,source_id,started_at DESC);
-- Owner/kind history listings and source-scoped draft pagination already read
-- these predicates. Add only the two corresponding bounded composite indexes.
CREATE INDEX owner_kind_source_history ON psms_documents.sources(owner_id,kind,created_at DESC,id);
CREATE INDEX owner_source_ppmp_drafts ON public.ppmp_ocr_drafts(owner_id,source_id,id);

ALTER TABLE psms_documents.processing_runs ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON SCHEMA psms_documents FROM PUBLIC;
REVOKE ALL ON TABLE psms_documents.processing_runs FROM PUBLIC;
REVOKE ALL ON TABLE psms_documents.processing_runs FROM psms_vercel_backend;
GRANT USAGE ON SCHEMA psms_documents TO psms_vercel_backend;
GRANT SELECT ON psms_documents.processing_runs TO psms_vercel_backend;
GRANT INSERT(id,source_id,owner_id,kind,parser_version,source_sha256)
 ON psms_documents.processing_runs TO psms_vercel_backend;
GRANT UPDATE(status,completed_at,extraction,summary,error_code)
 ON psms_documents.processing_runs TO psms_vercel_backend;

CREATE POLICY own_processing_read ON psms_documents.processing_runs FOR SELECT TO psms_vercel_backend
 USING(owner_id=psms_workbooks.active_actor_id());
CREATE POLICY own_processing_insert ON psms_documents.processing_runs FOR INSERT TO psms_vercel_backend
 WITH CHECK(owner_id=psms_workbooks.active_actor_id()
   AND EXISTS(SELECT 1 FROM psms_documents.sources s
     WHERE s.id=processing_runs.source_id AND s.owner_id=processing_runs.owner_id
       AND s.kind=processing_runs.kind AND s.sha256=processing_runs.source_sha256));
-- Metadata is immutable through column ACLs. Completed runs remain immutable
-- history; retries create new IDs after completing/failing an existing claim.
CREATE POLICY own_processing_complete ON psms_documents.processing_runs FOR UPDATE TO psms_vercel_backend
 USING(owner_id=psms_workbooks.active_actor_id() AND status='PROCESSING')
 WITH CHECK(owner_id=psms_workbooks.active_actor_id());

-- Reuse installed 04 behavior: revision lock is deferred until business locks
-- have been taken, and owner-scoped document revision changes once per TX.
CREATE CONSTRAINT TRIGGER psms_live_revision AFTER INSERT OR UPDATE OR DELETE
 ON psms_documents.processing_runs DEFERRABLE INITIALLY DEFERRED
 FOR EACH ROW EXECUTE FUNCTION psms_sync.record_change('documents','owner_id');

DO $private$
DECLARE api_role text; column_name text; insert_allowed boolean; update_allowed boolean;
BEGIN
 FOREACH api_role IN ARRAY ARRAY['anon','authenticated','service_role'] LOOP
  IF EXISTS(SELECT 1 FROM pg_catalog.pg_roles WHERE rolname=api_role) THEN
   EXECUTE format('REVOKE ALL ON SCHEMA psms_documents FROM %I',api_role);
   EXECUTE format('REVOKE ALL ON TABLE psms_documents.processing_runs FROM %I',api_role);
   IF has_schema_privilege(api_role,'psms_documents','USAGE,CREATE')
      OR has_any_column_privilege(api_role,'psms_documents.processing_runs','SELECT,INSERT,UPDATE,REFERENCES')
      OR has_table_privilege(api_role,'psms_documents.processing_runs','DELETE,TRUNCATE,TRIGGER') THEN
    RAISE EXCEPTION 'Unexpected inherited document processing API privilege requires owner review';
   END IF;
  END IF;
 END LOOP;
 -- Assert effective privileges, including grants inherited through PUBLIC.
 IF has_schema_privilege('psms_vercel_backend','psms_documents','CREATE')
    OR has_table_privilege('psms_vercel_backend','psms_documents.processing_runs','INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER') THEN
   RAISE EXCEPTION 'Unexpected broad document processing backend privilege';
 END IF;
 FOR column_name IN SELECT a.attname FROM pg_catalog.pg_attribute a
     WHERE a.attrelid='psms_documents.processing_runs'::regclass AND a.attnum>0 AND NOT a.attisdropped LOOP
  insert_allowed:=column_name=ANY(ARRAY['id','source_id','owner_id','kind','parser_version','source_sha256']);
  update_allowed:=column_name=ANY(ARRAY['status','completed_at','extraction','summary','error_code']);
  IF NOT has_column_privilege('psms_vercel_backend','psms_documents.processing_runs',column_name,'SELECT')
     OR has_column_privilege('psms_vercel_backend','psms_documents.processing_runs',column_name,'INSERT') IS DISTINCT FROM insert_allowed
     OR has_column_privilege('psms_vercel_backend','psms_documents.processing_runs',column_name,'UPDATE') IS DISTINCT FROM update_allowed
     OR has_column_privilege('psms_vercel_backend','psms_documents.processing_runs',column_name,'REFERENCES') THEN
   RAISE EXCEPTION 'Document processing column privilege contract failed';
  END IF;
 END LOOP;
END;$private$;
COMMIT;
