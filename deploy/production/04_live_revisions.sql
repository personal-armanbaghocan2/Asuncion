-- Owner-run after 03_account_profiles.sql. No application startup DDL.
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
BEGIN
 IF current_user<>'postgres' OR current_setting('psms.upgrade_owner_approved')<>'YES'
    OR current_database()<>current_setting('psms.upgrade_database')
    OR current_setting('psms.upgrade_project_ref')<>'fenrqjzupidswgrjzdgn'
    OR current_setting('psms.upgrade_no_backup_risk_accepted') NOT IN ('NO','YES')
    OR current_setting('psms.upgrade_recovery_point') ~* '(REPLACE|PLACEHOLDER|[<>])'
    OR (nullif(btrim(current_setting('psms.upgrade_recovery_point')),'') IS NULL
        AND current_setting('psms.upgrade_no_backup_risk_accepted')<>'YES') THEN
   RAISE EXCEPTION 'Owner approval, existing project/database and recovery decision required';
 END IF;
 IF to_regclass('psms_accounts.profiles') IS NULL
    OR to_regprocedure('psms_workbooks.active_actor_id()') IS NULL THEN
   RAISE EXCEPTION 'Install the reviewed account migration and existing identity bridge first';
 END IF;
END;$review$;

CREATE SCHEMA psms_sync;
REVOKE ALL ON SCHEMA psms_sync FROM PUBLIC;
CREATE TABLE psms_sync.revisions(singleton boolean PRIMARY KEY CHECK(singleton),values_json jsonb NOT NULL DEFAULT '{}');
INSERT INTO psms_sync.revisions(singleton) VALUES(true);
ALTER TABLE psms_sync.revisions ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON psms_sync.revisions FROM PUBLIC;
REVOKE ALL ON psms_sync.revisions FROM psms_vercel_backend;

CREATE FUNCTION psms_sync.record_change() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $change$
DECLARE data jsonb; owner_id bigint:=0; topic text; key text; keys text[];
        touched text:=coalesce(current_setting('psms.sync_touched',true),'');
BEGIN
 IF TG_OP='DELETE' THEN data:=to_jsonb(OLD); ELSE data:=to_jsonb(NEW); END IF;
 topic:=TG_ARGV[0];
 IF TG_ARGV[1]<>'' THEN owner_id:=coalesce((data->>TG_ARGV[1])::bigint,0); END IF;
 keys:=ARRAY[topic||':'||owner_id::text];
 IF TG_TABLE_SCHEMA='public' AND TG_TABLE_NAME='users' THEN
   keys:=keys||ARRAY['account:'||(data->>'id')];
 END IF;
 FOREACH key IN ARRAY keys LOOP
   IF strpos(touched,'|'||key||'|')=0 THEN
     UPDATE psms_sync.revisions SET values_json=jsonb_set(values_json,ARRAY[key],
       to_jsonb(coalesce((values_json->>key)::bigint,0)+1),true) WHERE singleton;
     touched:=touched||'|'||key||'|';
   END IF;
 END LOOP;
 PERFORM set_config('psms.sync_touched',touched,true);
 RETURN NULL;
END;$change$;
REVOKE ALL ON FUNCTION psms_sync.record_change() FROM PUBLIC;
REVOKE ALL ON FUNCTION psms_sync.record_change() FROM psms_vercel_backend;

-- Deferred triggers take their one shared revision-row lock at commit, after
-- business writes/locks. This avoids adding inverse module/audit counter locks
-- to inventory and procurement transactions. Each topic changes once per TX.
DO $triggers$
DECLARE item record;
BEGIN
 FOR item IN SELECT * FROM (VALUES
  ('public','ppmp','ppmp',''),('public','app_plan','app',''),('public','pr','pr',''),
  ('public','aoq','aoq',''),('public','po','po',''),('public','nlp','nlp',''),
  ('public','iar','iar',''),('public','inventory','inventory',''),('public','ris_ics','ris',''),
  ('public','dataset_records','dataset',''),('public','users','users',''),('public','audit_trail','audit',''),
  ('public','ppmp_ocr_drafts','documents','owner_id'),('psms_documents','sources','documents','owner_id'),
  ('psms_documents','aoq_drafts','documents','owner_id'),('psms_accounts','profiles','account','user_id')
 ) AS t(schema_name,table_name,topic,owner_column) LOOP
  IF to_regclass(format('%I.%I',item.schema_name,item.table_name)) IS NULL THEN
    RAISE EXCEPTION 'Required existing synchronization relation is missing: %.%',item.schema_name,item.table_name;
  END IF;
  EXECUTE format('CREATE CONSTRAINT TRIGGER psms_live_revision AFTER INSERT OR UPDATE OR DELETE ON %I.%I DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION psms_sync.record_change(%L,%L)',
    item.schema_name,item.table_name,item.topic,item.owner_column);
 END LOOP;
END;$triggers$;

CREATE FUNCTION psms_sync.read_versions(requested text[]) RETURNS jsonb
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $read$
DECLARE uid bigint; actor_role text; topic text; key text; state jsonb; result jsonb:='{}';
BEGIN
 uid:=psms_workbooks.active_actor_id();
 SELECT role INTO actor_role FROM public.users WHERE id=uid AND status='Active';
 IF uid IS NULL OR actor_role IS NULL OR actor_role NOT IN ('Admin','Staff','Inspector') THEN
   RAISE EXCEPTION 'Active authenticated actor required' USING ERRCODE='42501';
 END IF;
 IF requested IS NULL OR cardinality(requested)>14 THEN RAISE EXCEPTION 'Invalid topics'; END IF;
 SELECT values_json INTO state FROM psms_sync.revisions WHERE singleton;
 FOREACH topic IN ARRAY requested LOOP
   IF topic IS NULL OR topic NOT IN ('ppmp','app','pr','aoq','po','nlp','iar','inventory','ris','dataset','documents','account','users','audit')
      OR (topic='users' AND actor_role<>'Admin') THEN
     RAISE EXCEPTION 'Unauthorized topic' USING ERRCODE='42501';
   END IF;
   key:=topic||':'||CASE WHEN topic IN ('documents','account') THEN uid::text ELSE '0' END;
   result:=result||jsonb_build_object(topic,coalesce(state->>key,'0'));
 END LOOP;
 RETURN result;
END;$read$;
REVOKE ALL ON FUNCTION psms_sync.read_versions(text[]) FROM PUBLIC;
GRANT USAGE ON SCHEMA psms_sync TO psms_vercel_backend;
GRANT EXECUTE ON FUNCTION psms_sync.read_versions(text[]) TO psms_vercel_backend;
DO $private$
DECLARE api_role text;
BEGIN
 FOREACH api_role IN ARRAY ARRAY['anon','authenticated','service_role'] LOOP
  IF EXISTS(SELECT 1 FROM pg_roles WHERE rolname=api_role) THEN
   EXECUTE format('REVOKE ALL ON SCHEMA psms_sync FROM %I',api_role);
   EXECUTE format('REVOKE ALL ON TABLE psms_sync.revisions FROM %I',api_role);
   EXECUTE format('REVOKE ALL ON FUNCTION psms_sync.record_change() FROM %I',api_role);
   EXECUTE format('REVOKE ALL ON FUNCTION psms_sync.read_versions(text[]) FROM %I',api_role);
   IF has_schema_privilege(api_role,'psms_sync','USAGE')
      OR has_any_column_privilege(api_role,'psms_sync.revisions','SELECT,INSERT,UPDATE,REFERENCES')
      OR has_table_privilege(api_role,'psms_sync.revisions','DELETE,TRUNCATE,TRIGGER')
      OR has_function_privilege(api_role,'psms_sync.record_change()','EXECUTE')
      OR has_function_privilege(api_role,'psms_sync.read_versions(text[])','EXECUTE') THEN
    RAISE EXCEPTION 'Unexpected inherited synchronization API privilege requires owner review';
   END IF;
  END IF;
 END LOOP;
END;$private$;
COMMIT;
