-- Additive release contract. NOT AUTHORIZED FOR SUPABASE EXECUTION.
-- Run only as one reviewed transaction after backup/restore verification.
-- Does not activate or replace the existing global policy or original functions.
BEGIN;
SET LOCAL search_path=pg_catalog;
SET LOCAL lock_timeout='5s';
SET LOCAL statement_timeout='60s';
DO $operator$
BEGIN
 IF current_user<>'postgres' OR session_user<>current_user
  OR current_setting('server_version_num')::integer NOT BETWEEN 170000 AND 179999
  OR current_setting('psms.release_install_approved',true) IS DISTINCT FROM 'YES'
  OR current_setting('psms.release_target_project',true) IS DISTINCT FROM 'fenrqjzupidswgrjzdgn' THEN
  RAISE EXCEPTION 'Reviewed operator, PostgreSQL17 and exact target approval required';
 END IF;
 IF (SELECT policy->'enabled' FROM psms_documents.procurement_policy WHERE singleton)
     IS DISTINCT FROM 'false'::jsonb THEN
  RAISE EXCEPTION 'Existing global workflow must remain disabled';
 END IF;
 IF NOT EXISTS(SELECT 1 FROM pg_roles WHERE rolname='psms_aoq_approval_owner'
   AND NOT rolcanlogin AND NOT rolsuper AND NOT rolbypassrls AND NOT rolcreaterole
   AND NOT rolcreatedb AND NOT rolinherit)
  OR NOT EXISTS(SELECT 1 FROM pg_roles WHERE rolname='psms_vercel_backend'
   AND rolcanlogin AND NOT rolsuper AND NOT rolbypassrls AND NOT rolcreaterole
   AND NOT rolcreatedb AND NOT rolinherit)
  OR NOT EXISTS(SELECT 1 FROM pg_roles WHERE rolname='psms_aoq_function_owner'
   AND NOT rolcanlogin AND NOT rolsuper AND NOT rolbypassrls AND NOT rolcreaterole
   AND NOT rolcreatedb AND NOT rolinherit)
  OR EXISTS(SELECT 1 FROM pg_auth_members WHERE member='psms_vercel_backend'::regrole) THEN
  RAISE EXCEPTION 'Existing restricted role contracts required';
 END IF;
END $operator$;
DO $existing_contract$
BEGIN
 IF EXISTS(SELECT 1 FROM (VALUES ('psms_aoq_api.unicode_v1(text,text)','44313906a1b45464ab993b6bbac09bc4','psms_aoq_function_owner',false),
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
('psms_aoq_api.save_pr_po_verification_v1(bigint,bigint,text,text,text,double precision,jsonb,text,text)','aee19be18bf46c956aadce7d450bb172','psms_aoq_approval_owner',true)) required(signature,digest,owner_name,definer)
  WHERE NOT EXISTS(SELECT 1 FROM pg_proc f JOIN pg_roles r ON r.oid=f.proowner
   WHERE f.oid=to_regprocedure(required.signature) AND r.rolname=required.owner_name
   AND f.prosecdef=required.definer
   AND md5(replace(f.prosrc,chr(13)||chr(10),chr(10)))=required.digest)) THEN
  RAISE EXCEPTION 'Existing private procurement definitions differ; no installation attempted';
 END IF;
 IF EXISTS(SELECT 1 FROM (VALUES
 ('save_reviewed_aoq_independent_v1'),('approve_reviewed_aoq_v1'),
 ('create_po_for_review_v2'),('save_pr_po_verification_v1'),
 ('review_pr_po_verification_v1'),('approve_po_after_verification_v1'),
 ('create_pr_from_approved_app_v1'),('approve_pr_v1'),('lock_po_for_workflow_v2')) required(name)
 JOIN pg_proc f ON f.proname=required.name
 JOIN pg_namespace n ON n.oid=f.pronamespace AND n.nspname='psms_aoq_api'
 WHERE has_function_privilege('psms_vercel_backend',f.oid,'EXECUTE')) THEN
  RAISE EXCEPTION 'Unscoped backend entrypoints must remain denied';
 END IF;
 IF EXISTS(SELECT 1 FROM (VALUES ('public.aoq'),('public.po'),('public.nlp')) protected(name)
 WHERE has_table_privilege('psms_vercel_backend',protected.name,'INSERT,UPDATE,DELETE,TRUNCATE')
  OR has_any_column_privilege('psms_vercel_backend',protected.name,'INSERT')
  OR has_any_column_privilege('psms_vercel_backend',protected.name,'UPDATE')) THEN
  RAISE EXCEPTION 'Direct procurement writes must remain denied';
 END IF;
END $existing_contract$;
CREATE TEMP TABLE release_install_memberships ON COMMIT DROP AS
 SELECT * FROM pg_auth_members WHERE roleid IN
 ('psms_aoq_approval_owner'::regrole,'psms_aoq_function_owner'::regrole);
CREATE TEMP TABLE release_install_policy ON COMMIT DROP AS
 SELECT * FROM psms_documents.procurement_policy;
-- Temporary SET capability only; restore the exact previous membership below.
GRANT psms_aoq_approval_owner TO CURRENT_USER WITH SET TRUE GRANTED BY CURRENT_USER;
GRANT psms_aoq_function_owner TO CURRENT_USER WITH SET TRUE GRANTED BY CURRENT_USER;
CREATE SCHEMA psms_revised_release AUTHORIZATION postgres;
REVOKE ALL ON SCHEMA psms_revised_release FROM PUBLIC;
GRANT USAGE ON SCHEMA psms_revised_release TO psms_vercel_backend,psms_aoq_approval_owner;
GRANT CREATE ON SCHEMA psms_revised_release TO psms_aoq_approval_owner;
CREATE TABLE psms_revised_release.deployments (
 release_id text NOT NULL CHECK(release_id='revised-procurement-v1'),
 deployment_host text NOT NULL CHECK(deployment_host ~ '^asuncion-[a-z0-9]{9}-arman-a87f[.]vercel[.]app$'),
 token_sha256 bytea NOT NULL CHECK(octet_length(token_sha256)=32),
 enabled boolean NOT NULL DEFAULT false,
 expires_at timestamptz NOT NULL,
 po_clock text NOT NULL CHECK(po_clock='ASIA_MANILA'),
 link_authority text NOT NULL CHECK(link_authority IN ('ADMIN_DOCUMENTED','EXISTING_REVIEWERS_DOCUMENTED')),
 approved_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 approved_by text NOT NULL DEFAULT current_user,
 approval_reference text NOT NULL CHECK(length(btrim(approval_reference))>=10),
 PRIMARY KEY(release_id,deployment_host)
);
REVOKE ALL ON psms_revised_release.deployments FROM PUBLIC,psms_vercel_backend;
DO $private_table$
DECLARE grant_row record;
BEGIN
 FOR grant_row IN SELECT DISTINCT acl.grantee FROM pg_class c,
  LATERAL aclexplode(coalesce(c.relacl,acldefault('r',c.relowner))) acl
  WHERE c.oid='psms_revised_release.deployments'::regclass AND acl.grantee<>c.relowner LOOP
  EXECUTE format('REVOKE ALL ON psms_revised_release.deployments FROM %s',
   CASE WHEN grant_row.grantee=0 THEN 'PUBLIC' ELSE quote_ident(pg_get_userbyid(grant_row.grantee)) END);
 END LOOP;
END $private_table$;
ALTER TABLE psms_revised_release.deployments ENABLE ROW LEVEL SECURITY;
GRANT SELECT ON psms_revised_release.deployments TO psms_aoq_approval_owner;
CREATE POLICY release_owner_read ON psms_revised_release.deployments
 FOR SELECT TO psms_aoq_approval_owner USING(true);

CREATE FUNCTION psms_revised_release.policy_v1() RETURNS jsonb
LANGUAGE plpgsql VOLATILE SECURITY DEFINER
SET search_path=pg_catalog,pg_temp SET row_security=on AS $release_policy$
DECLARE v_release text;v_host text;v_token text;v_policy jsonb;
BEGIN
 v_release:=current_setting('psms.procurement_release_id',true);
 v_host:=current_setting('psms.procurement_release_host',true);
 v_token:=current_setting('psms.procurement_release_token',true);
 IF v_release IS DISTINCT FROM 'revised-procurement-v1'
  OR v_host IS NULL OR v_host !~ '^asuncion-[a-z0-9]{9}-arman-a87f[.]vercel[.]app$'
  OR v_token IS NULL OR v_token !~ '^[0-9a-f]{64}$' THEN
  RAISE EXCEPTION 'Authorized procurement deployment required' USING ERRCODE='PA007';
 END IF;
 SELECT jsonb_build_object('enabled',true,'po_clock',d.po_clock,'link_authority',d.link_authority)
 INTO v_policy FROM psms_revised_release.deployments d
 WHERE d.release_id=v_release AND d.deployment_host=v_host AND d.enabled
  AND d.expires_at>clock_timestamp()
  AND d.token_sha256=sha256(convert_to(v_token,'UTF8'));
 IF NOT FOUND THEN
  RAISE EXCEPTION 'Procurement deployment disabled or unavailable' USING ERRCODE='PA007';
 END IF;
 RETURN v_policy;
END $release_policy$;

CREATE FUNCTION psms_revised_release.require_release_v1() RETURNS void
LANGUAGE plpgsql VOLATILE SECURITY DEFINER
SET search_path=pg_catalog,pg_temp SET row_security=on AS $release_lock$
BEGIN
 -- Serialize revocation with in-flight mutations. The public read-only policy
 -- check takes no row lock; every mutation checks again under this shared lock.
 PERFORM 1 FROM psms_revised_release.deployments
 WHERE release_id=current_setting('psms.procurement_release_id',true)
  AND deployment_host=current_setting('psms.procurement_release_host',true) FOR SHARE;
 PERFORM psms_revised_release.policy_v1();
END $release_lock$;

CREATE FUNCTION psms_revised_release.save_reviewed_aoq_v1(p_draft_id uuid, p_expected_revision bigint, p_offer_review_token text, p_winner_index integer, p_supplier_corrections jsonb, p_reviewed_items jsonb, p_manual_winner text, p_remarks text, p_confirm_reviewed boolean) RETURNS TABLE(aoq_id bigint, aoq_no text, created boolean, draft_revision bigint)
LANGUAGE plpgsql VOLATILE SECURITY DEFINER
SET search_path=pg_catalog,pg_temp SET row_security=on AS $scoped$
BEGIN
 PERFORM psms_revised_release.require_release_v1();
 RETURN QUERY SELECT * FROM psms_aoq_api.save_reviewed_aoq_v1(p_draft_id,p_expected_revision,p_offer_review_token,p_winner_index,p_supplier_corrections,p_reviewed_items,p_manual_winner,p_remarks,p_confirm_reviewed);
END $scoped$;

CREATE FUNCTION psms_revised_release.save_reviewed_aoq_independent_v1(p_draft_id uuid, p_expected_revision bigint, p_offer_review_token text, p_winner_index integer, p_supplier_corrections jsonb, p_reviewed_items jsonb, p_manual_winner text, p_remarks text, p_confirm_reviewed boolean) RETURNS TABLE(aoq_id bigint, aoq_no text, created boolean, draft_revision bigint)
LANGUAGE plpgsql VOLATILE SECURITY DEFINER
SET search_path=pg_catalog,pg_temp SET row_security=on AS $scoped$
BEGIN
 PERFORM psms_revised_release.require_release_v1();
 RETURN QUERY SELECT * FROM psms_aoq_api.save_reviewed_aoq_independent_v1(p_draft_id,p_expected_revision,p_offer_review_token,p_winner_index,p_supplier_corrections,p_reviewed_items,p_manual_winner,p_remarks,p_confirm_reviewed);
END $scoped$;

CREATE FUNCTION psms_revised_release.approve_reviewed_aoq_v1(p_aoq_id bigint, p_review_confirm boolean, p_final_confirm boolean) RETURNS TABLE(aoq_id bigint, aoq_no text, new_status text)
LANGUAGE plpgsql VOLATILE SECURITY DEFINER
SET search_path=pg_catalog,pg_temp SET row_security=on AS $scoped$
BEGIN
 PERFORM psms_revised_release.require_release_v1();
 RETURN QUERY SELECT * FROM psms_aoq_api.approve_reviewed_aoq_v1(p_aoq_id,p_review_confirm,p_final_confirm);
END $scoped$;

CREATE FUNCTION psms_revised_release.create_pr_from_approved_app_v1(p_app_id bigint, p_purpose text, p_source_hash text, p_validated_total double precision) RETURNS TABLE(pr_id bigint, pr_no text, created boolean)
LANGUAGE plpgsql VOLATILE SECURITY DEFINER
SET search_path=pg_catalog,pg_temp SET row_security=on AS $scoped$
BEGIN
 PERFORM psms_revised_release.require_release_v1();
 RETURN QUERY SELECT * FROM psms_aoq_api.create_pr_from_approved_app_v1(p_app_id,p_purpose,p_source_hash,p_validated_total);
END $scoped$;

CREATE FUNCTION psms_revised_release.approve_pr_v1(p_pr_id bigint, p_review_confirm boolean) RETURNS TABLE(pr_id bigint, new_status text)
LANGUAGE plpgsql VOLATILE SECURITY DEFINER
SET search_path=pg_catalog,pg_temp SET row_security=on AS $scoped$
BEGIN
 PERFORM psms_revised_release.require_release_v1();
 RETURN QUERY SELECT * FROM psms_aoq_api.approve_pr_v1(p_pr_id,p_review_confirm);
END $scoped$;

CREATE FUNCTION psms_revised_release.create_po_for_review_v2(p_aoq_id bigint) RETURNS TABLE(po_id bigint, po_no text, created boolean)
LANGUAGE plpgsql VOLATILE SECURITY DEFINER
SET search_path=pg_catalog,pg_temp SET row_security=on AS $scoped$
BEGIN
 PERFORM psms_revised_release.require_release_v1();
 RETURN QUERY SELECT * FROM psms_aoq_api.create_po_for_review_v2(p_aoq_id);
END $scoped$;

CREATE FUNCTION psms_revised_release.save_pr_po_verification_v1(p_po_id bigint, p_pr_id bigint, p_pr_hash text, p_po_hash text, p_result text, p_confidence double precision, p_details jsonb, p_model_version text, p_technical_status text) RETURNS TABLE(nlp_id bigint, nlp_no text, created boolean)
LANGUAGE plpgsql VOLATILE SECURITY DEFINER
SET search_path=pg_catalog,pg_temp SET row_security=on AS $scoped$
BEGIN
 PERFORM psms_revised_release.require_release_v1();
 RETURN QUERY SELECT * FROM psms_aoq_api.save_pr_po_verification_v1(p_po_id,p_pr_id,p_pr_hash,p_po_hash,p_result,p_confidence,p_details,p_model_version,p_technical_status);
END $scoped$;

CREATE FUNCTION psms_revised_release.lock_po_for_workflow_v2(p_po_id bigint, p_purpose text) RETURNS TABLE(id bigint, no text, aoq_id bigint, supplier text, status text, created_at text, items text, total double precision, pr_id bigint)
LANGUAGE plpgsql VOLATILE SECURITY DEFINER
SET search_path=pg_catalog,pg_temp SET row_security=on AS $scoped$
BEGIN
 PERFORM psms_revised_release.require_release_v1();
 RETURN QUERY SELECT * FROM psms_aoq_api.lock_po_for_workflow_v2(p_po_id,p_purpose);
END $scoped$;

CREATE FUNCTION psms_revised_release.require_saved_technical_match_v2(
 p_saved_status text,p_saved jsonb,p_mapping jsonb,p_current jsonb
) RETURNS void
LANGUAGE plpgsql IMMUTABLE PARALLEL SAFE
SET search_path=pg_catalog,pg_temp AS $guard$
DECLARE n integer;i integer;s jsonb;c jsonb;position_value jsonb;
BEGIN
 -- This deliberately cannot promote an unresolved saved comparison. A future
 -- legitimate re-evaluation needs its own reviewed source-bound persistence path.
 IF p_saved_status IS DISTINCT FROM 'MATCH'
  OR jsonb_typeof(p_saved) IS DISTINCT FROM 'object'
  OR jsonb_typeof(p_current) IS DISTINCT FROM 'object'
  OR jsonb_typeof(p_mapping) IS DISTINCT FROM 'array' THEN
  RAISE EXCEPTION 'Stored server technical match required' USING ERRCODE='PA001';END IF;
 IF p_saved->>'technical_status' IS DISTINCT FROM 'MATCH'
  OR p_current->>'technical_status' IS DISTINCT FROM 'MATCH'
  OR p_saved->>'version' IS DISTINCT FROM 'PR_PO_REVIEW_V1'
  OR p_current->>'version' IS DISTINCT FROM 'PR_PO_REVIEW_V1'
  OR p_saved->'extra_or_unallocated_items' IS DISTINCT FROM '[]'::jsonb
  OR p_saved->'missing_or_unresolved_pr_items' IS DISTINCT FROM '[]'::jsonb
  OR p_current->'extra_or_unallocated_items' IS DISTINCT FROM '[]'::jsonb
  OR p_current->'missing_or_unresolved_pr_items' IS DISTINCT FROM '[]'::jsonb
  OR jsonb_typeof(p_saved->'rows') IS DISTINCT FROM 'array'
  OR jsonb_typeof(p_current->'rows') IS DISTINCT FROM 'array' THEN
  RAISE EXCEPTION 'Complete stored technical evidence required' USING ERRCODE='PA001';END IF;
 n:=jsonb_array_length(p_saved->'rows');
 IF n<1 OR jsonb_array_length(p_mapping)<>n OR jsonb_array_length(p_current->'rows')<>n
  OR p_saved->'source_fields' IS DISTINCT FROM p_current->'source_fields'
  OR jsonb_typeof(p_saved->'source_fields') IS DISTINCT FROM 'object'
  OR (SELECT count(DISTINCT value) FROM jsonb_array_elements(p_mapping))<>n THEN
  RAISE EXCEPTION 'Stored correspondence required' USING ERRCODE='PA001';END IF;
 FOR i IN 0..n-1 LOOP
  position_value:=p_mapping->i;
  IF jsonb_typeof(position_value) IS DISTINCT FROM 'number'
   OR position_value::text !~ '^[1-9][0-9]*$' THEN
   RAISE EXCEPTION 'Valid correspondence required' USING ERRCODE='PA001';END IF;
  IF position_value::numeric>n THEN
   RAISE EXCEPTION 'Valid correspondence required' USING ERRCODE='PA001';END IF;
  s:=p_saved->'rows'->i;c:=p_current->'rows'->i;
  IF jsonb_typeof(s) IS DISTINCT FROM 'object' OR jsonb_typeof(c) IS DISTINCT FROM 'object'
   OR s->'pr_position' IS DISTINCT FROM to_jsonb(i+1)
   OR c->'pr_position' IS DISTINCT FROM to_jsonb(i+1)
   OR s->'po_position' IS DISTINCT FROM position_value
   OR c->'po_position' IS DISTINCT FROM position_value
   OR s->'pr_reference' IS DISTINCT FROM c->'pr_reference'
   OR s->'po_reference' IS DISTINCT FROM c->'po_reference'
   OR jsonb_typeof(s->'checks') IS DISTINCT FROM 'array'
   OR jsonb_typeof(c->'checks') IS DISTINCT FROM 'array' THEN
   RAISE EXCEPTION 'Stored correspondence required' USING ERRCODE='PA001';END IF;
  IF jsonb_array_length(s->'checks')<2 OR s->'checks' IS DISTINCT FROM c->'checks'
   OR EXISTS(SELECT 1 FROM jsonb_array_elements(s->'checks') AS check_row(value)
       WHERE check_row.value->>'state' IS DISTINCT FROM 'MATCH'
        OR check_row.value->'conditional_on_identity' IS DISTINCT FROM 'false'::jsonb
        OR nullif(btrim(check_row.value->>'attribute'),'') IS NULL) THEN
   RAISE EXCEPTION 'Stored attribute compliance required' USING ERRCODE='PA001';END IF;
 END LOOP;
 -- identity/similarity labels may change during confirmation; positions,
 -- attributes, values and compliance findings cannot change through this call.
END $guard$;

CREATE FUNCTION psms_revised_release.review_pr_po_verification_v1(
 p_nlp_id bigint,p_mapping jsonb,p_checks jsonb,p_reason text,p_evidence_reference text,p_confirm boolean)
RETURNS TABLE(nlp_id bigint,po_id bigint)
LANGUAGE plpgsql VOLATILE SECURITY DEFINER
SET search_path=pg_catalog,pg_temp SET row_security=on SET TimeZone='UTC' SET lc_time='C'
SET lock_timeout='5s' AS $review_nlp$
DECLARE v_actor record;v_role text;v_preview record;v_aoq record;v_pr record;v_po record;v_nlp record;
 v_policy text;v_nlp_hash text;v_stamp timestamptz:=clock_timestamp();v_pr_count integer;v_po_count integer;
BEGIN
 PERFORM psms_revised_release.require_release_v1();
 IF current_setting('transaction_isolation')<>'read committed' THEN RAISE EXCEPTION 'Read committed required' USING ERRCODE='PA002';END IF;
 SELECT * INTO STRICT v_actor FROM psms_aoq_api.approval_actor_v1();
 SELECT role INTO v_role FROM public.users WHERE id=v_actor.actor_id;
 SELECT n.po_id,n.pr_id,p.aoq_id INTO v_preview FROM public.nlp n JOIN public.po p ON p.id=n.po_id WHERE n.id=p_nlp_id;
 IF NOT FOUND THEN RAISE EXCEPTION 'Verification unavailable' USING ERRCODE='PA004';END IF;
 SELECT id,pr_id,status INTO v_aoq FROM public.aoq WHERE id=v_preview.aoq_id FOR SHARE;
 SELECT id,no,status,items,app_id,ppmp_id,office INTO v_pr FROM public.pr WHERE id=v_preview.pr_id FOR UPDATE;
 SELECT id,no,aoq_id,pr_id,status,items INTO v_po FROM public.po WHERE id=v_preview.po_id FOR UPDATE;
 SELECT verification.id,verification.no,verification.po_id,verification.pr_id,verification.workflow_version,verification.status,
  verification.result,verification.confidence,verification.details,verification.technical_status,verification.model_version,
  verification.pr_items_hash,verification.po_items_hash INTO v_nlp FROM public.nlp AS verification WHERE verification.id=p_nlp_id FOR UPDATE;
 IF v_nlp.workflow_version IS DISTINCT FROM 1 OR v_nlp.status IS DISTINCT FROM 'For Review'
  OR v_nlp.po_id IS DISTINCT FROM v_po.id OR v_nlp.pr_id IS DISTINCT FROM v_pr.id
  OR v_po.aoq_id IS DISTINCT FROM v_aoq.id OR v_po.status IS DISTINCT FROM 'For Review'
  OR v_pr.status IS DISTINCT FROM 'Approved' OR v_aoq.status IS DISTINCT FROM 'Approved'
  OR (v_aoq.pr_id IS NOT NULL AND v_aoq.pr_id IS DISTINCT FROM v_pr.id)
  OR (v_po.pr_id IS NOT NULL AND v_po.pr_id IS DISTINCT FROM v_pr.id) THEN
  RAISE EXCEPTION 'Sources or verification changed' USING ERRCODE='PA002';END IF;
 IF v_nlp.pr_items_hash IS DISTINCT FROM encode(sha256(convert_to(coalesce(v_pr.items,''),'UTF8')),'hex')
  OR v_nlp.po_items_hash IS DISTINCT FROM encode(sha256(convert_to(coalesce(v_po.items,''),'UTF8')),'hex') THEN
  RAISE EXCEPTION 'Sources or verification changed' USING ERRCODE='PA002';END IF;
 IF v_aoq.pr_id IS NULL THEN
  SELECT psms_revised_release.policy_v1()->>'link_authority' INTO v_policy;
  IF v_policy IS NULL OR v_policy='PENDING' THEN RAISE EXCEPTION 'Independent PR-PO authorization policy pending' USING ERRCODE='PA005';END IF;
  IF NOT ((v_policy='ADMIN_DOCUMENTED' AND v_role='Admin') OR
          (v_policy='EXISTING_REVIEWERS_DOCUMENTED' AND v_role IN ('Admin','Staff','Inspector'))) THEN
   RAISE EXCEPTION 'Authorized procurement relationship reviewer required' USING ERRCODE='PA003';END IF;
  PERFORM 1 FROM public.app_plan a JOIN public.ppmp m ON m.id=a.ppmp_id
      WHERE a.id=v_pr.app_id AND a.status='Approved' AND m.status='Approved'
       AND (v_pr.ppmp_id IS NULL OR v_pr.ppmp_id=m.id) FOR SHARE OF a,m;
  IF NOT FOUND THEN
   RAISE EXCEPTION 'Approved planning evidence required for independent relationship' USING ERRCODE='PA006';END IF;
 END IF;
 IF p_confirm IS DISTINCT FROM true OR p_reason IS NULL OR length(btrim(p_reason)) NOT BETWEEN 10 AND 2000
  OR p_evidence_reference IS NULL OR length(btrim(p_evidence_reference)) NOT BETWEEN 3 AND 500 THEN
  RAISE EXCEPTION 'Documented relationship confirmation required' USING ERRCODE='PA001';END IF;
 v_pr_count:=jsonb_array_length(v_pr.items::jsonb);v_po_count:=jsonb_array_length(v_po.items::jsonb);
 IF jsonb_typeof(p_mapping) IS DISTINCT FROM 'array' OR jsonb_typeof(p_checks) IS DISTINCT FROM 'object'
  OR jsonb_array_length(p_mapping)<>v_pr_count OR v_pr_count<>v_po_count
  OR p_checks->>'technical_status' IS DISTINCT FROM 'MATCH'
  OR jsonb_typeof(p_checks->'rows') IS DISTINCT FROM 'array'
  OR jsonb_array_length(p_checks->'rows')<>v_pr_count
  OR p_checks->>'version' IS DISTINCT FROM 'PR_PO_REVIEW_V1'
  OR p_checks->'extra_or_unallocated_items' IS DISTINCT FROM '[]'::jsonb
  OR p_checks->'missing_or_unresolved_pr_items' IS DISTINCT FROM '[]'::jsonb
  OR EXISTS(SELECT 1 FROM jsonb_array_elements(p_checks->'rows') WITH ORDINALITY AS review_row(value,position)
     WHERE review_row.value->>'identity' IS NULL OR review_row.value->>'identity' NOT IN ('EXACT_NAME','HUMAN_CONFIRMED')
      OR review_row.value->>'pr_position' IS DISTINCT FROM review_row.position::text
      OR review_row.value->'po_position' IS DISTINCT FROM p_mapping->(review_row.position::integer-1)
      OR jsonb_typeof(review_row.value->'checks') IS DISTINCT FROM 'array'
      OR jsonb_array_length(review_row.value->'checks')<2
      OR EXISTS(SELECT 1 FROM jsonb_array_elements(review_row.value->'checks') AS item_check(value)
          WHERE item_check.value->>'state' IS DISTINCT FROM 'MATCH'))
  OR EXISTS(SELECT 1 FROM jsonb_array_elements(p_mapping) AS mapping_entry(value)
     WHERE jsonb_typeof(mapping_entry.value)<>'number' OR mapping_entry.value::text !~ '^[1-9][0-9]*$'
      OR (mapping_entry.value::text)::numeric>v_po_count)
  OR (SELECT count(DISTINCT mapping_entry.value) FROM jsonb_array_elements(p_mapping) AS mapping_entry(value))<>v_po_count
  OR v_nlp.result IS NULL OR v_nlp.result NOT IN ('Acceptable','Needs Review') THEN
  RAISE EXCEPTION 'Complete reviewed correspondence and technical compliance required' USING ERRCODE='PA001';END IF;
 PERFORM psms_revised_release.require_saved_technical_match_v2(v_nlp.technical_status,
  v_nlp.details::jsonb->'technical_comparison',p_mapping,p_checks);
 IF EXISTS(SELECT 1 FROM psms_documents.pr_po_reviews AS proof WHERE proof.po_id=v_po.id) THEN
  RAISE EXCEPTION 'Official relationship already reviewed' USING ERRCODE='PA002';END IF;
 IF EXISTS(SELECT 1 FROM psms_documents.pr_po_reviews WHERE pr_id=v_pr.id)
  OR EXISTS(SELECT 1 FROM public.po other_po JOIN public.aoq other_aoq ON other_aoq.id=other_po.aoq_id
     WHERE other_po.id<>v_po.id AND coalesce(other_po.pr_id,other_aoq.pr_id)=v_pr.id
       AND other_po.status IN ('For Review','Approved')) THEN
  RAISE EXCEPTION 'PR already has an active purchase order' USING ERRCODE='PA002';END IF;
 UPDATE public.nlp SET status='Approved',staff_decision=result,reviewed_by=v_actor.actor_id,reviewed_at=v_stamp,
  technical_status='MATCH',review_mapping=p_mapping,relationship_reason=btrim(p_reason),evidence_reference=btrim(p_evidence_reference),
  details=(details::jsonb||jsonb_build_object('reviewed_technical_checks',p_checks,'reviewed_procurement_context',
    jsonb_build_object('pr_app_id',v_pr.app_id,'pr_ppmp_id',v_pr.ppmp_id,'pr_office',v_pr.office)))::text WHERE id=p_nlp_id;
 UPDATE public.po SET pr_id=v_pr.id WHERE id=v_po.id;
 SELECT encode(sha256(convert_to(jsonb_build_array(n.pr_id,n.po_id,n.result,n.confidence,n.details,n.technical_status,n.model_version,n.pr_items_hash,n.po_items_hash)::text,'UTF8')),'hex')
  INTO v_nlp_hash FROM public.nlp n WHERE n.id=p_nlp_id;
 INSERT INTO psms_documents.pr_po_reviews(po_id,pr_id,nlp_id,pr_items_hash,po_items_hash,nlp_hash,
  reviewer_id,reviewed_at,item_mapping,justification,evidence_reference)
 VALUES(v_po.id,v_pr.id,p_nlp_id,v_nlp.pr_items_hash,v_nlp.po_items_hash,v_nlp_hash,v_actor.actor_id,v_stamp,
  p_mapping,btrim(p_reason),btrim(p_evidence_reference));
 INSERT INTO public.audit_trail(dt,"user",action,module,ref,details)
 VALUES(to_char(v_stamp AT TIME ZONE 'Asia/Manila','Mon DD, YYYY HH12:MI AM'),v_actor.actor_username,
  'Authorized PR-PO Relationship','NLP',v_nlp.no,'PR '||v_pr.no||'; PO '||v_po.no||'; evidence: '||btrim(p_evidence_reference));
 RETURN QUERY SELECT p_nlp_id,v_po.id;
END $review_nlp$;

CREATE FUNCTION psms_revised_release.approve_po_after_verification_v1(p_po_id bigint,p_review_confirm boolean,p_final_confirm boolean)
RETURNS TABLE(po_id bigint,new_status text)
LANGUAGE plpgsql VOLATILE SECURITY DEFINER
SET search_path=pg_catalog,pg_temp SET row_security=on SET TimeZone='UTC' SET lc_time='C'
SET lock_timeout='5s' AS $approve_po$
DECLARE v_actor record;v_preview record;v_aoq record;v_pr record;v_po record;v_review record;v_nlp record;v_hash text;
BEGIN
 PERFORM psms_revised_release.require_release_v1();
 IF current_setting('transaction_isolation')<>'read committed' THEN RAISE EXCEPTION 'Read committed required' USING ERRCODE='PA002';END IF;
 SELECT * INTO STRICT v_actor FROM psms_aoq_api.approval_actor_v1();
 SELECT aoq_id,pr_id INTO v_preview FROM public.po WHERE id=p_po_id;
 IF NOT FOUND THEN RAISE EXCEPTION 'PO unavailable' USING ERRCODE='PA004';END IF;
 SELECT id,pr_id,status INTO v_aoq FROM public.aoq WHERE id=v_preview.aoq_id FOR SHARE;
 SELECT id,no,status,items,app_id,ppmp_id,office INTO v_pr FROM public.pr WHERE id=v_preview.pr_id FOR SHARE;
 SELECT id,no,aoq_id,pr_id,status,items INTO v_po FROM public.po WHERE id=p_po_id FOR UPDATE;
 SELECT proof.* INTO v_review FROM psms_documents.pr_po_reviews AS proof WHERE proof.po_id=p_po_id;
 IF NOT FOUND THEN RAISE EXCEPTION 'Authorized PR-PO relationship required before final PO approval' USING ERRCODE='PA002';END IF;
 SELECT verification.id,verification.no,verification.po_id,verification.pr_id,verification.workflow_version,verification.status,
  verification.result,verification.confidence,verification.details,verification.technical_status,verification.model_version,
  verification.pr_items_hash,verification.po_items_hash INTO v_nlp FROM public.nlp AS verification WHERE verification.id=v_review.nlp_id FOR SHARE;
 IF p_review_confirm IS DISTINCT FROM true OR p_final_confirm IS DISTINCT FROM true
  OR v_po.status IS DISTINCT FROM 'For Review' OR v_pr.status IS DISTINCT FROM 'Approved'
  OR v_aoq.status IS DISTINCT FROM 'Approved' OR v_po.aoq_id IS DISTINCT FROM v_preview.aoq_id
  OR v_po.pr_id IS DISTINCT FROM v_review.pr_id OR v_pr.id IS DISTINCT FROM v_review.pr_id
  OR (v_aoq.pr_id IS NOT NULL AND v_aoq.pr_id IS DISTINCT FROM v_review.pr_id)
  OR v_nlp.status IS DISTINCT FROM 'Approved' OR v_nlp.result IS NULL OR v_nlp.result NOT IN ('Acceptable','Needs Review')
  OR v_nlp.technical_status IS DISTINCT FROM 'MATCH' OR v_nlp.po_id IS DISTINCT FROM v_po.id
  OR v_nlp.pr_id IS DISTINCT FROM v_pr.id THEN
  RAISE EXCEPTION 'Confirmed reviewed procurement relationship required' USING ERRCODE='PA002';END IF;
 IF v_nlp.details::jsonb->'reviewed_procurement_context' IS DISTINCT FROM
   jsonb_build_object('pr_app_id',v_pr.app_id,'pr_ppmp_id',v_pr.ppmp_id,'pr_office',v_pr.office) THEN
  RAISE EXCEPTION 'Reviewed procurement scope changed' USING ERRCODE='PA002';END IF;
 IF v_aoq.pr_id IS NULL THEN
  PERFORM 1 FROM public.app_plan a JOIN public.ppmp m ON m.id=a.ppmp_id
   WHERE a.id=v_pr.app_id AND a.status='Approved' AND m.status='Approved'
    AND (v_pr.ppmp_id IS NULL OR v_pr.ppmp_id=m.id) FOR SHARE OF a,m;
  IF NOT FOUND THEN RAISE EXCEPTION 'Approved planning evidence changed' USING ERRCODE='PA006';END IF;
 END IF;
 IF v_review.pr_items_hash IS DISTINCT FROM encode(sha256(convert_to(coalesce(v_pr.items,''),'UTF8')),'hex')
  OR v_review.po_items_hash IS DISTINCT FROM encode(sha256(convert_to(coalesce(v_po.items,''),'UTF8')),'hex') THEN
  RAISE EXCEPTION 'Reviewed documents changed' USING ERRCODE='PA002';END IF;
 v_hash:=encode(sha256(convert_to(jsonb_build_array(v_nlp.pr_id,v_nlp.po_id,v_nlp.result,v_nlp.confidence,v_nlp.details,
  v_nlp.technical_status,v_nlp.model_version,v_nlp.pr_items_hash,v_nlp.po_items_hash)::text,'UTF8')),'hex');
 IF v_hash IS DISTINCT FROM v_review.nlp_hash THEN RAISE EXCEPTION 'Reviewed verification changed' USING ERRCODE='PA002';END IF;
 PERFORM psms_revised_release.require_saved_technical_match_v2(v_nlp.technical_status,
  v_nlp.details::jsonb->'technical_comparison',v_review.item_mapping,
  v_nlp.details::jsonb->'reviewed_technical_checks');
 -- The existing Python signatory adapter binds context only for direct UPDATE
 -- statements. This secure SELECT/RPC path must bind the same validated actor
 -- before the existing approval trigger runs; never bypass that trigger.
 PERFORM set_config('psms.signatory_actor',jsonb_build_object('user_id',v_actor.actor_id,
  'sid',(nullif(current_setting('psms.workbook_actor',true),'')::jsonb)->>'sid')::text,true);
 UPDATE public.po SET status='Approved' WHERE id=p_po_id;
 INSERT INTO public.audit_trail(dt,"user",action,module,ref,details)
 VALUES(to_char(clock_timestamp() AT TIME ZONE 'Asia/Manila','Mon DD, YYYY HH12:MI AM'),v_actor.actor_username,
  'Approved PO','PO',v_po.no,'Final approval after authorized PR-PO relationship and human NLP review');
 RETURN QUERY SELECT p_po_id,'Approved'::text;
END $approve_po$;

ALTER FUNCTION psms_revised_release.policy_v1() OWNER TO psms_aoq_approval_owner;
REVOKE ALL ON FUNCTION psms_revised_release.policy_v1() FROM PUBLIC,psms_vercel_backend;
GRANT EXECUTE ON FUNCTION psms_revised_release.policy_v1() TO psms_vercel_backend;

ALTER FUNCTION psms_revised_release.require_release_v1() OWNER TO psms_aoq_approval_owner;
REVOKE ALL ON FUNCTION psms_revised_release.require_release_v1() FROM PUBLIC,psms_vercel_backend;

ALTER FUNCTION psms_revised_release.save_reviewed_aoq_v1(uuid,bigint,text,integer,jsonb,jsonb,text,text,boolean) OWNER TO psms_aoq_approval_owner;
REVOKE ALL ON FUNCTION psms_revised_release.save_reviewed_aoq_v1(uuid,bigint,text,integer,jsonb,jsonb,text,text,boolean) FROM PUBLIC,psms_vercel_backend;
GRANT EXECUTE ON FUNCTION psms_revised_release.save_reviewed_aoq_v1(uuid,bigint,text,integer,jsonb,jsonb,text,text,boolean) TO psms_vercel_backend;

ALTER FUNCTION psms_revised_release.save_reviewed_aoq_independent_v1(uuid,bigint,text,integer,jsonb,jsonb,text,text,boolean) OWNER TO psms_aoq_approval_owner;
REVOKE ALL ON FUNCTION psms_revised_release.save_reviewed_aoq_independent_v1(uuid,bigint,text,integer,jsonb,jsonb,text,text,boolean) FROM PUBLIC,psms_vercel_backend;
GRANT EXECUTE ON FUNCTION psms_revised_release.save_reviewed_aoq_independent_v1(uuid,bigint,text,integer,jsonb,jsonb,text,text,boolean) TO psms_vercel_backend;

ALTER FUNCTION psms_revised_release.approve_reviewed_aoq_v1(bigint,boolean,boolean) OWNER TO psms_aoq_approval_owner;
REVOKE ALL ON FUNCTION psms_revised_release.approve_reviewed_aoq_v1(bigint,boolean,boolean) FROM PUBLIC,psms_vercel_backend;
GRANT EXECUTE ON FUNCTION psms_revised_release.approve_reviewed_aoq_v1(bigint,boolean,boolean) TO psms_vercel_backend;

ALTER FUNCTION psms_revised_release.create_pr_from_approved_app_v1(bigint,text,text,double precision) OWNER TO psms_aoq_approval_owner;
REVOKE ALL ON FUNCTION psms_revised_release.create_pr_from_approved_app_v1(bigint,text,text,double precision) FROM PUBLIC,psms_vercel_backend;
GRANT EXECUTE ON FUNCTION psms_revised_release.create_pr_from_approved_app_v1(bigint,text,text,double precision) TO psms_vercel_backend;

ALTER FUNCTION psms_revised_release.approve_pr_v1(bigint,boolean) OWNER TO psms_aoq_approval_owner;
REVOKE ALL ON FUNCTION psms_revised_release.approve_pr_v1(bigint,boolean) FROM PUBLIC,psms_vercel_backend;
GRANT EXECUTE ON FUNCTION psms_revised_release.approve_pr_v1(bigint,boolean) TO psms_vercel_backend;

ALTER FUNCTION psms_revised_release.create_po_for_review_v2(bigint) OWNER TO psms_aoq_approval_owner;
REVOKE ALL ON FUNCTION psms_revised_release.create_po_for_review_v2(bigint) FROM PUBLIC,psms_vercel_backend;
GRANT EXECUTE ON FUNCTION psms_revised_release.create_po_for_review_v2(bigint) TO psms_vercel_backend;

ALTER FUNCTION psms_revised_release.save_pr_po_verification_v1(bigint,bigint,text,text,text,double precision,jsonb,text,text) OWNER TO psms_aoq_approval_owner;
REVOKE ALL ON FUNCTION psms_revised_release.save_pr_po_verification_v1(bigint,bigint,text,text,text,double precision,jsonb,text,text) FROM PUBLIC,psms_vercel_backend;
GRANT EXECUTE ON FUNCTION psms_revised_release.save_pr_po_verification_v1(bigint,bigint,text,text,text,double precision,jsonb,text,text) TO psms_vercel_backend;

ALTER FUNCTION psms_revised_release.lock_po_for_workflow_v2(bigint,text) OWNER TO psms_aoq_approval_owner;
REVOKE ALL ON FUNCTION psms_revised_release.lock_po_for_workflow_v2(bigint,text) FROM PUBLIC,psms_vercel_backend;
GRANT EXECUTE ON FUNCTION psms_revised_release.lock_po_for_workflow_v2(bigint,text) TO psms_vercel_backend;

ALTER FUNCTION psms_revised_release.require_saved_technical_match_v2(text,jsonb,jsonb,jsonb) OWNER TO psms_aoq_approval_owner;
REVOKE ALL ON FUNCTION psms_revised_release.require_saved_technical_match_v2(text,jsonb,jsonb,jsonb) FROM PUBLIC,psms_vercel_backend;

ALTER FUNCTION psms_revised_release.review_pr_po_verification_v1(bigint,jsonb,jsonb,text,text,boolean) OWNER TO psms_aoq_approval_owner;
REVOKE ALL ON FUNCTION psms_revised_release.review_pr_po_verification_v1(bigint,jsonb,jsonb,text,text,boolean) FROM PUBLIC,psms_vercel_backend;
GRANT EXECUTE ON FUNCTION psms_revised_release.review_pr_po_verification_v1(bigint,jsonb,jsonb,text,text,boolean) TO psms_vercel_backend;

ALTER FUNCTION psms_revised_release.approve_po_after_verification_v1(bigint,boolean,boolean) OWNER TO psms_aoq_approval_owner;
REVOKE ALL ON FUNCTION psms_revised_release.approve_po_after_verification_v1(bigint,boolean,boolean) FROM PUBLIC,psms_vercel_backend;
GRANT EXECUTE ON FUNCTION psms_revised_release.approve_po_after_verification_v1(bigint,boolean,boolean) TO psms_vercel_backend;

-- Only the scoped function owner gains these two private call edges. The
-- backend gains no new permission on the original API or procurement tables.
SET LOCAL ROLE psms_aoq_function_owner;
GRANT EXECUTE ON FUNCTION psms_aoq_api.save_reviewed_aoq_v1(uuid,bigint,text,integer,jsonb,jsonb,text,text,boolean)
 TO psms_aoq_approval_owner;
GRANT EXECUTE ON FUNCTION psms_aoq_api.save_reviewed_aoq_independent_v1(uuid,bigint,text,integer,jsonb,jsonb,text,text,boolean)
 TO psms_aoq_approval_owner;
RESET ROLE;
SET LOCAL ROLE psms_aoq_approval_owner;
DO $private_functions$
DECLARE grant_row record;
BEGIN
 FOR grant_row IN SELECT DISTINCT f.oid,acl.grantee FROM pg_proc f
 JOIN pg_namespace n ON n.oid=f.pronamespace,
 LATERAL aclexplode(coalesce(f.proacl,acldefault('f',f.proowner))) acl
 WHERE n.nspname='psms_revised_release'
  AND acl.grantee NOT IN (f.proowner,'psms_vercel_backend'::regrole::oid) LOOP
  EXECUTE format('REVOKE ALL ON FUNCTION %s FROM %s',grant_row.oid::regprocedure,
   CASE WHEN grant_row.grantee=0 THEN 'PUBLIC' ELSE quote_ident(pg_get_userbyid(grant_row.grantee)) END);
 END LOOP;
END $private_functions$;
RESET ROLE;
REVOKE CREATE ON SCHEMA psms_revised_release FROM psms_aoq_approval_owner;
DO $restore_custody$
DECLARE owner_name text;previous record;
BEGIN
 FOREACH owner_name IN ARRAY ARRAY['psms_aoq_function_owner','psms_aoq_approval_owner'] LOOP
  SELECT * INTO previous FROM release_install_memberships
   WHERE roleid=owner_name::regrole AND member=current_user::regrole AND grantor=current_user::regrole;
  IF FOUND THEN
   EXECUTE format('GRANT %I TO %I WITH SET %s GRANTED BY %I',owner_name,current_user,
    CASE WHEN previous.set_option THEN 'TRUE' ELSE 'FALSE' END,current_user);
  ELSE
   EXECUTE format('REVOKE %I FROM %I GRANTED BY %I RESTRICT',owner_name,current_user,current_user);
  END IF;
 END LOOP;
 IF EXISTS((SELECT * FROM release_install_memberships EXCEPT
   SELECT * FROM pg_auth_members WHERE roleid IN ('psms_aoq_function_owner'::regrole,'psms_aoq_approval_owner'::regrole))
  UNION ALL (SELECT * FROM pg_auth_members WHERE roleid IN ('psms_aoq_function_owner'::regrole,'psms_aoq_approval_owner'::regrole)
   EXCEPT SELECT * FROM release_install_memberships)) THEN
  RAISE EXCEPTION 'Owner custody was not restored';
 END IF;
 IF EXISTS((SELECT * FROM release_install_policy EXCEPT SELECT * FROM psms_documents.procurement_policy)
  UNION ALL (SELECT * FROM psms_documents.procurement_policy EXCEPT SELECT * FROM release_install_policy)) THEN
  RAISE EXCEPTION 'Original global policy changed';
 END IF;
 IF has_table_privilege('psms_vercel_backend','psms_revised_release.deployments','SELECT,INSERT,UPDATE,DELETE')
  OR has_function_privilege('psms_vercel_backend','psms_revised_release.require_release_v1()','EXECUTE')
  OR has_function_privilege('psms_vercel_backend','psms_revised_release.require_saved_technical_match_v2(text,jsonb,jsonb,jsonb)','EXECUTE')
  OR has_schema_privilege('psms_aoq_approval_owner','psms_revised_release','CREATE') THEN
  RAISE EXCEPTION 'Private release custody failed';
 END IF;
END $restore_custody$;
DO $verify_release_contract$
BEGIN
 IF (SELECT count(*) FROM pg_proc f JOIN pg_namespace n ON n.oid=f.pronamespace
     WHERE n.nspname='psms_revised_release')<>13
  OR EXISTS(SELECT 1 FROM (VALUES ('psms_revised_release.policy_v1()','708461349509a23a532125c1cadf6b85',true,true),
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
('psms_revised_release.approve_po_after_verification_v1(bigint,boolean,boolean)','a8c1f7f13891cacf08e6d4fca588197c',true,true)) expected(signature,digest,definer,backend_execute)
   WHERE NOT EXISTS(SELECT 1 FROM pg_proc f JOIN pg_roles owner_role ON owner_role.oid=f.proowner
    WHERE f.oid=to_regprocedure(expected.signature)
     AND md5(replace(f.prosrc,chr(13)||chr(10),chr(10)))=expected.digest
     AND f.prosecdef=expected.definer AND owner_role.rolname='psms_aoq_approval_owner'
     AND NOT owner_role.rolcanlogin AND NOT owner_role.rolsuper AND NOT owner_role.rolbypassrls
     AND has_function_privilege('psms_vercel_backend',f.oid,'EXECUTE')=expected.backend_execute
     AND EXISTS(SELECT 1 FROM unnest(f.proconfig) setting
       WHERE replace(setting,' ','')='search_path=pg_catalog,pg_temp')
     AND (NOT expected.definer OR 'row_security=on'=ANY(f.proconfig))
     AND NOT EXISTS(SELECT 1 FROM aclexplode(coalesce(f.proacl,acldefault('f',f.proowner))) acl
      WHERE acl.grantee NOT IN (f.proowner,'psms_vercel_backend'::regrole::oid)
       OR acl.privilege_type<>'EXECUTE' OR (acl.is_grantable AND acl.grantee<>f.proowner)))) THEN
  RAISE EXCEPTION 'Exact scoped function ownership, source or ACL verification failed';
 END IF;
 IF NOT EXISTS(SELECT 1 FROM pg_class WHERE oid='psms_revised_release.deployments'::regclass
    AND relrowsecurity AND relowner='postgres'::regrole)
  OR has_any_column_privilege('psms_vercel_backend','psms_revised_release.deployments','SELECT')
  OR has_any_column_privilege('psms_vercel_backend','psms_revised_release.deployments','INSERT')
  OR has_any_column_privilege('psms_vercel_backend','psms_revised_release.deployments','UPDATE')
  OR EXISTS(SELECT 1 FROM psms_revised_release.deployments) THEN
  RAISE EXCEPTION 'Registry must remain private, RLS-protected and unactivated';
 END IF;
END $verify_release_contract$;
-- No deployment is enabled by installation. A separate, approved exact-host
-- registration is required. Failure anywhere above rolls back the whole DDL.
COMMIT;
