-- Additive manual PR-PO approval policy: final affected-item incidence <= 10%.
-- PREPARED ONLY. Execute only after independent owner/target/recovery review.
-- No existing function, model result, source document or record is rewritten by
-- installation. All future approvals retain source locks and transactional audit.
-- Pending, missing, extra, unbound or changed source evidence never qualifies.
-- Rollback application first; then DROP only the four new functions in reverse
-- dependency order. Preserve approval/procurement records and original functions.
BEGIN;
SET LOCAL lock_timeout='5s';
SET LOCAL statement_timeout='60s';
SET LOCAL search_path=pg_catalog;
CREATE TEMP TABLE ten_percent_old_functions ON COMMIT DROP AS
 SELECT p.oid,to_jsonb(p) AS metadata FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
 WHERE n.nspname IN ('psms_aoq_api','psms_signatories');
CREATE TEMP TABLE ten_percent_owner_schema ON COMMIT DROP AS
 SELECT has_schema_privilege('psms_aoq_approval_owner','psms_aoq_api','CREATE') AS had_create;
DO $preconditions$
DECLARE dep record;
BEGIN
 IF current_user<>'postgres' OR session_user<>current_user
  OR current_setting('transaction_isolation')<>'read committed'
  OR current_setting('server_version_num')::integer NOT BETWEEN 170000 AND 179999 THEN
  RAISE EXCEPTION 'Owner PostgreSQL 17 read-committed installer required';END IF;
 IF NOT pg_has_role(session_user,'psms_aoq_approval_owner','SET')
  OR NOT EXISTS(SELECT 1 FROM pg_roles WHERE rolname='psms_aoq_approval_owner'
    AND NOT rolcanlogin AND NOT rolsuper AND NOT rolbypassrls)
  OR NOT EXISTS(SELECT 1 FROM pg_roles WHERE rolname='psms_vercel_backend'
    AND rolcanlogin AND NOT rolsuper AND NOT rolbypassrls AND NOT rolcreatedb
    AND NOT rolcreaterole AND NOT rolreplication AND NOT rolinherit) THEN
  RAISE EXCEPTION 'Existing restricted role contract differs';END IF;
 FOR dep IN SELECT * FROM (VALUES
  ('psms_aoq_api.review_approved_pr_po_verification_v2(bigint,jsonb,jsonb,text,text,boolean)','1ffa25321ca839f203d061983ce25843',true),
  ('psms_aoq_api.approve_reviewed_pr_po_v3(bigint,boolean)','938c2a551d0fefd827e52752b0d91cb2',true),
  ('psms_aoq_api.mark_pr_po_reviewed_v3(bigint,jsonb,jsonb,text,text,boolean)','54a45ecdd82de1bca36f8c4884776fac',true),
  ('psms_aoq_api.lock_po_for_workflow_v2(bigint,text)','f138be8c07b46898bc608e8ad53cda24',true),
  ('psms_aoq_api.approval_actor_v1()','d38444a829b882fb720f20d0ede9b00f',false),
  ('psms_aoq_api.require_saved_technical_match_v2(text,jsonb,jsonb,jsonb)','de6422cdbd430db0b6999fb9579cb26a',false)
 ) expected(signature,digest,definer) LOOP
  IF NOT EXISTS(SELECT 1 FROM pg_proc p WHERE p.oid=to_regprocedure(dep.signature)
    AND p.proowner='psms_aoq_approval_owner'::regrole AND p.prosecdef=dep.definer
    AND md5(replace(p.prosrc,chr(13)||chr(10),chr(10)))=dep.digest) THEN
   RAISE EXCEPTION 'Reviewed dependency differs: %',dep.signature;END IF;
 END LOOP;
 IF to_regprocedure('psms_aoq_api.require_ten_percent_review_v4(text,jsonb,jsonb,jsonb)') IS NOT NULL
  OR to_regprocedure('psms_aoq_api.approve_tolerated_pr_po_v4(bigint,jsonb,jsonb,text,text,boolean)') IS NOT NULL
  OR to_regprocedure('psms_aoq_api.approve_reviewed_pr_po_v4(bigint,boolean)') IS NOT NULL
  OR to_regprocedure('psms_aoq_api.lock_po_for_workflow_v4(bigint,text)') IS NOT NULL THEN
  RAISE EXCEPTION 'Tolerance functions already exist; inspect rather than overwrite';END IF;
 IF NOT (SELECT had_create FROM ten_percent_owner_schema) THEN
  GRANT CREATE ON SCHEMA psms_aoq_api TO psms_aoq_approval_owner;
 END IF;
END $preconditions$;
SET LOCAL ROLE psms_aoq_approval_owner;

CREATE FUNCTION psms_aoq_api.require_ten_percent_review_v4(p_technical_status text,p_saved jsonb,p_mapping jsonb,p_checks jsonb)
RETURNS numeric LANGUAGE plpgsql IMMUTABLE SECURITY INVOKER
SET search_path=pg_catalog,pg_temp SET row_security=on
AS $tolerance$
DECLARE n integer;pos integer;q integer;line jsonb;c jsonb;side text;source jsonb;field jsonb;
 seen integer[]:='{}';refs text[];check_count integer;quantity_count integer;unit_count integer;
 affected integer:=0;line_affected boolean;a numeric;b numeric;left_unit text;right_unit text;percent numeric;
BEGIN
 IF p_technical_status IS NULL OR p_technical_status NOT IN ('MATCH','NEEDS_REVIEW')
  OR jsonb_typeof(p_saved) IS DISTINCT FROM 'object' OR p_saved IS DISTINCT FROM p_checks
  OR p_saved->>'version' IS DISTINCT FROM 'PR_PO_REVIEW_V1'
  OR p_saved->>'technical_status' IS DISTINCT FROM p_technical_status
  OR p_saved->'extra_or_unallocated_items' IS DISTINCT FROM '[]'::jsonb
  OR p_saved->'missing_or_unresolved_pr_items' IS DISTINCT FROM '[]'::jsonb
  OR jsonb_typeof(p_saved->'rows') IS DISTINCT FROM 'array'
  OR jsonb_typeof(p_saved->'source_fields') IS DISTINCT FROM 'object'
  OR jsonb_typeof(p_mapping) IS DISTINCT FROM 'array' THEN
  RAISE EXCEPTION 'Complete unchanged source-bound review required' USING ERRCODE='PA001';END IF;
 n:=jsonb_array_length(p_saved->'rows');
 IF n NOT BETWEEN 1 AND 200 OR jsonb_array_length(p_mapping)<>n THEN
  RAISE EXCEPTION 'Complete one-to-one item coverage required' USING ERRCODE='PA001';END IF;
 FOREACH side IN ARRAY ARRAY['PR','PO'] LOOP
  IF jsonb_typeof(p_saved->'source_fields'->side) IS DISTINCT FROM 'array'
   OR jsonb_array_length(p_saved->'source_fields'->side)<>n THEN
   RAISE EXCEPTION 'Complete source fields required' USING ERRCODE='PA001';END IF;
  refs:='{}';
  FOR source IN SELECT value FROM jsonb_array_elements(p_saved->'source_fields'->side) LOOP
   IF jsonb_typeof(source) IS DISTINCT FROM 'object'
    OR jsonb_typeof(source->'reference') IS DISTINCT FROM 'string'
    OR length(btrim(source->>'reference'))=0 OR source->>'reference'=ANY(refs)
    OR jsonb_typeof(source->'fields') IS DISTINCT FROM 'array' OR jsonb_array_length(source->'fields')=0 THEN
    RAISE EXCEPTION 'Unique bound source references required' USING ERRCODE='PA001';END IF;
   refs:=array_append(refs,source->>'reference');
   FOR field IN SELECT value FROM jsonb_array_elements(source->'fields') LOOP
    IF jsonb_typeof(field) IS DISTINCT FROM 'string' OR length(btrim(field#>>'{}'))=0 THEN
     RAISE EXCEPTION 'Nonempty source fields required' USING ERRCODE='PA001';END IF;
   END LOOP;
  END LOOP;
 END LOOP;
 FOR line,pos IN SELECT value,ordinality::integer FROM jsonb_array_elements(p_saved->'rows') WITH ORDINALITY LOOP
  IF jsonb_typeof(line) IS DISTINCT FROM 'object' OR line->>'identity' IS DISTINCT FROM 'EXACT_NAME'
   OR jsonb_typeof(line->'pr_position') IS DISTINCT FROM 'number' OR line->>'pr_position' IS DISTINCT FROM pos::text
   OR jsonb_typeof(line->'po_position') IS DISTINCT FROM 'number'
   OR line->>'po_position' !~ '^[1-9][0-9]*$'
   OR jsonb_typeof(p_mapping->(pos-1)) IS DISTINCT FROM 'number'
   OR p_mapping->(pos-1) IS DISTINCT FROM line->'po_position'
   OR jsonb_typeof(line->'checks') IS DISTINCT FROM 'array' THEN
   RAISE EXCEPTION 'Exact one-to-one item identity required' USING ERRCODE='PA001';END IF;
  IF (line->>'po_position')::numeric NOT BETWEEN 1 AND n THEN
   RAISE EXCEPTION 'Item position outside source coverage' USING ERRCODE='PA001';END IF;
  q:=(line->>'po_position')::integer;
  IF q=ANY(seen) OR line->>'pr_reference' IS DISTINCT FROM p_saved#>>ARRAY['source_fields','PR',(pos-1)::text,'reference']
   OR line->>'po_reference' IS DISTINCT FROM p_saved#>>ARRAY['source_fields','PO',(q-1)::text,'reference'] THEN
   RAISE EXCEPTION 'Source item references changed or duplicated' USING ERRCODE='PA001';END IF;
  seen:=array_append(seen,q);check_count:=0;quantity_count:=0;unit_count:=0;line_affected:=false;
  FOR c IN SELECT value FROM jsonb_array_elements(line->'checks') LOOP
   check_count:=check_count+1;
   IF jsonb_typeof(c) IS DISTINCT FROM 'object' OR jsonb_typeof(c->'attribute') IS DISTINCT FROM 'string'
    OR length(btrim(c->>'attribute'))=0 OR c->>'state' IS NULL OR c->>'state' NOT IN ('MATCH','MISMATCH')
    OR c->'conditional_on_identity' IS DISTINCT FROM 'false'::jsonb
    OR c->'requested' IS NULL OR c->'offered' IS NULL
    OR c->'requested'=ANY(ARRAY['null','false','true','""','[]','{}']::jsonb[])
    OR c->'offered'=ANY(ARRAY['null','false','true','""','[]','{}']::jsonb[])
    OR (jsonb_typeof(c->'requested')='string' AND length(btrim(c->>'requested'))=0)
    OR (jsonb_typeof(c->'offered')='string' AND length(btrim(c->>'offered'))=0) THEN
    RAISE EXCEPTION 'Every saved check must have a final explicit finding' USING ERRCODE='PA001';END IF;
   IF c->>'state'='MISMATCH' THEN line_affected:=true;END IF;
   IF c->>'attribute'='quantity' THEN
    quantity_count:=quantity_count+1;
    BEGIN a:=(c->>'requested')::numeric;b:=(c->>'offered')::numeric;
    EXCEPTION WHEN invalid_text_representation OR numeric_value_out_of_range THEN
     RAISE EXCEPTION 'Finite positive quantities required' USING ERRCODE='PA001';END;
    IF a::text IN ('NaN','Infinity','-Infinity') OR b::text IN ('NaN','Infinity','-Infinity') OR a<=0 OR b<=0
     OR c->>'state' IS DISTINCT FROM (CASE WHEN a=b THEN 'MATCH' ELSE 'MISMATCH' END) THEN
     RAISE EXCEPTION 'Quantity finding differs from source numbers' USING ERRCODE='PA001';END IF;
   ELSIF c->>'attribute'='unit' THEN
    unit_count:=unit_count+1;
    IF jsonb_typeof(c->'requested') IS DISTINCT FROM 'string' OR jsonb_typeof(c->'offered') IS DISTINCT FROM 'string'
     OR c->>'state' IS DISTINCT FROM 'MATCH' THEN
     RAISE EXCEPTION 'Compatible confirmed source units required' USING ERRCODE='PA001';END IF;
    left_unit:=lower(regexp_replace(btrim(c->>'requested'),'\s+',' ','g'));
    right_unit:=lower(regexp_replace(btrim(c->>'offered'),'\s+',' ','g'));
    left_unit:=CASE left_unit WHEN 'pc' THEN 'piece' WHEN 'pcs' THEN 'piece' WHEN 'pieces' THEN 'piece'
     WHEN 'units' THEN 'unit' WHEN 'reams' THEN 'ream' WHEN 'bottles' THEN 'bottle' WHEN 'sets' THEN 'set' ELSE left_unit END;
    right_unit:=CASE right_unit WHEN 'pc' THEN 'piece' WHEN 'pcs' THEN 'piece' WHEN 'pieces' THEN 'piece'
     WHEN 'units' THEN 'unit' WHEN 'reams' THEN 'ream' WHEN 'bottles' THEN 'bottle' WHEN 'sets' THEN 'set' ELSE right_unit END;
    IF left_unit<>right_unit THEN RAISE EXCEPTION 'Source units do not match' USING ERRCODE='PA001';END IF;
   END IF;
  END LOOP;
  IF check_count<2 OR quantity_count<>1 OR unit_count<>1 THEN
   RAISE EXCEPTION 'Exactly one quantity and unit finding required per item' USING ERRCODE='PA001';END IF;
  IF line_affected THEN affected:=affected+1;END IF;
 END LOOP;
 IF (affected=0) IS DISTINCT FROM (p_technical_status='MATCH') THEN
  RAISE EXCEPTION 'Technical status differs from saved findings' USING ERRCODE='PA001';END IF;
 percent:=100::numeric*affected/n;
 IF percent>10 THEN RAISE EXCEPTION 'Final discrepancy exceeds 10 percent' USING ERRCODE='PA001';END IF;
 RETURN percent;
END $tolerance$;


CREATE FUNCTION psms_aoq_api.approve_tolerated_pr_po_v4(p_nlp_id bigint, p_mapping jsonb, p_checks jsonb, p_reason text, p_evidence_reference text, p_confirm boolean)
 RETURNS TABLE(nlp_id bigint, po_id bigint)
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'pg_catalog', 'pg_temp'
 SET row_security TO 'on'
 SET "TimeZone" TO 'UTC'
 SET lc_time TO 'C'
 SET lock_timeout TO '5s'
AS $function$
DECLARE v_actor record;v_role text;v_preview record;v_aoq record;v_pr record;v_po record;v_nlp record;
 v_policy text;v_nlp_hash text;v_stamp timestamptz:=clock_timestamp();v_pr_count integer;v_po_count integer;v_percentage numeric;
BEGIN
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
 IF v_nlp.workflow_version IS DISTINCT FROM 2 OR v_nlp.status IS DISTINCT FROM 'For Review'
  OR v_nlp.po_id IS DISTINCT FROM v_po.id OR v_nlp.pr_id IS DISTINCT FROM v_pr.id
  OR v_po.aoq_id IS DISTINCT FROM v_aoq.id OR v_po.status IS DISTINCT FROM 'Approved'
  OR v_pr.status IS DISTINCT FROM 'Approved' OR v_aoq.status IS DISTINCT FROM 'Approved'
  OR (v_aoq.pr_id IS NOT NULL AND v_aoq.pr_id IS DISTINCT FROM v_pr.id)
  OR (v_po.pr_id IS NOT NULL AND v_po.pr_id IS DISTINCT FROM v_pr.id) THEN
  RAISE EXCEPTION 'Sources or verification changed' USING ERRCODE='PA002';END IF;
 IF v_nlp.pr_items_hash IS DISTINCT FROM encode(sha256(convert_to(coalesce(v_pr.items,''),'UTF8')),'hex')
  OR v_nlp.po_items_hash IS DISTINCT FROM encode(sha256(convert_to(coalesce(v_po.items,''),'UTF8')),'hex') THEN
  RAISE EXCEPTION 'Sources or verification changed' USING ERRCODE='PA002';END IF;

 -- A replacement may be reviewed only while its immutable lineage still matches.
 IF v_nlp.details::jsonb ? 'verification_lineage' OR EXISTS(
  SELECT 1 FROM psms_documents.nlp_legacy_replacements l WHERE l.replacement_nlp_id=v_nlp.id) THEN
  PERFORM 1 FROM psms_documents.nlp_legacy_replacements l JOIN public.nlp old ON old.id=l.legacy_nlp_id
   WHERE l.replacement_nlp_id=v_nlp.id AND l.po_id=v_po.id AND l.pr_id=v_pr.id
    AND v_nlp.details::jsonb->'verification_lineage'->>'legacy_nlp_id'=l.legacy_nlp_id::text
    AND v_nlp.details::jsonb->'verification_lineage'->>'legacy_record_sha256'=l.legacy_record_sha256
    AND encode(sha256(convert_to(to_jsonb(old)::text,'UTF8')),'hex')=l.legacy_record_sha256
   FOR SHARE OF old;
  IF NOT FOUND THEN RAISE EXCEPTION 'Saved legacy lineage changed' USING ERRCODE='PA002';END IF;
 END IF;

 IF v_aoq.pr_id IS NULL THEN
  SELECT policy->>'link_authority' INTO v_policy FROM psms_documents.procurement_policy WHERE singleton;
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
 IF v_nlp.result IS NULL OR v_nlp.result NOT IN ('Acceptable','Needs Review','Needs Recanvass','Reject') THEN
  RAISE EXCEPTION 'Saved original model output required' USING ERRCODE='PA001';END IF;
 v_percentage:=psms_aoq_api.require_ten_percent_review_v4(v_nlp.technical_status,
  v_nlp.details::jsonb->'technical_comparison',p_mapping,p_checks);
 IF EXISTS(SELECT 1 FROM psms_documents.pr_po_reviews AS proof WHERE proof.po_id=v_po.id) THEN
  RAISE EXCEPTION 'Official relationship already reviewed' USING ERRCODE='PA002';END IF;
 IF EXISTS(SELECT 1 FROM psms_documents.pr_po_reviews WHERE pr_id=v_pr.id)
  OR EXISTS(SELECT 1 FROM public.po other_po JOIN public.aoq other_aoq ON other_aoq.id=other_po.aoq_id
     WHERE other_po.id<>v_po.id AND coalesce(other_po.pr_id,other_aoq.pr_id)=v_pr.id
       AND other_po.status IN ('For Review','Approved')) THEN
  RAISE EXCEPTION 'PR already has an active purchase order' USING ERRCODE='PA002';END IF;
 UPDATE public.nlp SET status='Approved',staff_decision=result,reviewed_by=v_actor.actor_id,reviewed_at=v_stamp,
  review_mapping=p_mapping,relationship_reason=btrim(p_reason),evidence_reference=btrim(p_evidence_reference),
  details=(details::jsonb||jsonb_build_object('threshold_approval_v4',jsonb_build_object(
    'version',1,'percentage',v_percentage,'technical_comparison',v_nlp.details::jsonb->'technical_comparison',
    'pr_items_hash',v_nlp.pr_items_hash,'po_items_hash',v_nlp.po_items_hash),
    'reviewed_technical_checks',p_checks,'reviewed_procurement_context',
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
  'Authorized PR-PO Relationship','NLP',v_nlp.no,'PR '||v_pr.no||'; PO '||v_po.no||'; final affected-item discrepancy '||v_percentage::text||'%; manual <=10% policy; evidence: '||btrim(p_evidence_reference));
 RETURN QUERY SELECT p_nlp_id,v_po.id;
END $function$;

CREATE FUNCTION psms_aoq_api.approve_reviewed_pr_po_v4(p_nlp_id bigint, p_confirm boolean)
 RETURNS TABLE(nlp_id bigint,po_id bigint,new_status text)
 LANGUAGE plpgsql SECURITY DEFINER
 SET search_path TO 'pg_catalog','pg_temp' SET row_security TO 'on'
 SET "TimeZone" TO 'UTC' SET lc_time TO 'C' SET lock_timeout TO '5s'
AS $function$
DECLARE v_actor record;v_preview record;v_saved public.nlp%ROWTYPE;v_review jsonb;v_hash text;
BEGIN
 IF current_setting('transaction_isolation')<>'read committed' THEN
  RAISE EXCEPTION 'Read committed required' USING ERRCODE='PA002';END IF;
 SELECT * INTO STRICT v_actor FROM psms_aoq_api.approval_actor_v1();
 IF p_confirm IS DISTINCT FROM true THEN RAISE EXCEPTION 'Final approval confirmation required' USING ERRCODE='PA001';END IF;
 SELECT n.pr_id,n.po_id,p.aoq_id INTO v_preview FROM public.nlp n JOIN public.po p ON p.id=n.po_id WHERE n.id=p_nlp_id;
 IF NOT FOUND THEN RAISE EXCEPTION 'Verification unavailable' USING ERRCODE='PA004';END IF;
 -- Same source-first lock order as the existing final approval function.
 PERFORM 1 FROM public.aoq WHERE id=v_preview.aoq_id FOR SHARE;
 PERFORM 1 FROM public.pr WHERE id=v_preview.pr_id FOR UPDATE;
 PERFORM 1 FROM public.po WHERE id=v_preview.po_id FOR UPDATE;
 SELECT * INTO v_saved FROM public.nlp n WHERE n.id=p_nlp_id FOR UPDATE;
 IF NOT FOUND OR v_saved.workflow_version IS DISTINCT FROM 2 OR v_saved.status IS DISTINCT FROM 'Reviewed' THEN
  RAISE EXCEPTION 'Saved Reviewed verification required' USING ERRCODE='PA002';END IF;
 v_review:=v_saved.details::jsonb->'workflow_review_v3';
 SELECT encode(sha256(convert_to(jsonb_build_array(n.pr_id,n.po_id,n.result,n.confidence,
  n.details::jsonb-'workflow_review_v3',n.technical_status,n.model_version,n.pr_items_hash,n.po_items_hash)::text,'UTF8')),'hex') INTO v_hash FROM public.nlp n WHERE n.id=p_nlp_id;
 IF jsonb_typeof(v_review) IS DISTINCT FROM 'object' OR v_review->>'version' IS DISTINCT FROM '1'
  OR v_review->>'actor_id' IS DISTINCT FROM v_saved.reviewed_by::text
  OR v_review->>'reviewed_at' IS DISTINCT FROM (to_jsonb(v_saved.reviewed_at)#>>'{}')
  OR v_review->>'verification_sha256' IS DISTINCT FROM v_hash
  OR v_review->'mapping' IS DISTINCT FROM v_saved.review_mapping
  OR v_review->'checks' IS DISTINCT FROM v_saved.details::jsonb->'technical_comparison'
  OR v_review->>'reason' IS DISTINCT FROM v_saved.relationship_reason
  OR v_review->>'evidence_reference' IS DISTINCT FROM v_saved.evidence_reference THEN
  RAISE EXCEPTION 'Saved review evidence changed' USING ERRCODE='PA002';END IF;
 -- This temporary state exists only inside the locked approval transaction.
 -- Any old-contract, audit or trigger failure rolls it back to Reviewed.
 UPDATE public.nlp SET status='For Review' WHERE id=p_nlp_id AND status='Reviewed';
 RETURN QUERY SELECT approved.nlp_id,approved.po_id,'Approved'::text
  FROM psms_aoq_api.approve_tolerated_pr_po_v4(p_nlp_id,v_review->'mapping',v_review->'checks',
    v_review->>'reason',v_review->>'evidence_reference',true) AS approved;
END $function$;

CREATE FUNCTION psms_aoq_api.lock_po_for_workflow_v4(p_po_id bigint,p_purpose text)
RETURNS TABLE(id bigint,no text,aoq_id bigint,supplier text,status text,created_at text,items text,total double precision,pr_id bigint)
LANGUAGE plpgsql VOLATILE SECURITY DEFINER
SET search_path=pg_catalog,pg_temp SET row_security=on SET TimeZone='UTC' SET lc_time='C'
SET lock_timeout='5s' AS $lock_tolerance$
DECLARE v_actor record;v_preview record;v_po record;v_pr record;v_nlp record;v_proof record;
 v_marker jsonb;v_percentage numeric;v_hash text;eligible boolean:=false;
BEGIN
 SELECT * INTO STRICT v_actor FROM psms_aoq_api.approval_actor_v1();
 IF p_purpose IS DISTINCT FROM 'iar' THEN RAISE EXCEPTION 'Unsupported receiving boundary' USING ERRCODE='PA001';END IF;
 SELECT p.aoq_id,p.pr_id INTO v_preview FROM public.po p WHERE p.id=p_po_id;
 IF NOT FOUND THEN RAISE EXCEPTION 'Approved PO required' USING ERRCODE='PA002';END IF;
 -- Source-first order is shared with review/approval. No new lock inversion.
 PERFORM 1 FROM public.aoq a WHERE a.id=v_preview.aoq_id FOR SHARE;
 SELECT r.id,r.status,r.items INTO v_pr FROM public.pr r WHERE r.id=v_preview.pr_id FOR SHARE;
 SELECT p.id,p.no,p.aoq_id,p.supplier,p.status,p.created_at,p.items,p.total,p.pr_id
  INTO v_po FROM public.po p WHERE p.id=p_po_id FOR UPDATE;
 IF NOT FOUND OR v_po.status IS DISTINCT FROM 'Approved'
  OR v_po.aoq_id IS DISTINCT FROM v_preview.aoq_id OR v_po.pr_id IS DISTINCT FROM v_preview.pr_id THEN
  RAISE EXCEPTION 'Approved source PO changed' USING ERRCODE='PA002';END IF;
 FOR v_nlp IN SELECT n.* FROM public.nlp n WHERE n.po_id=p_po_id AND n.status='Approved' ORDER BY n.id FOR SHARE LOOP
  IF v_nlp.workflow_version IS NULL AND v_nlp.result IN ('Acceptable','Needs Review') THEN eligible:=true;EXIT;END IF;
  v_marker:=v_nlp.details::jsonb->'threshold_approval_v4';
  IF v_marker IS NULL THEN
   IF v_nlp.result IN ('Acceptable','Needs Review') AND (v_nlp.workflow_version IS NULL OR EXISTS(
    SELECT 1 FROM psms_documents.pr_po_reviews proof WHERE proof.po_id=p_po_id
     AND proof.nlp_id=v_nlp.id AND proof.pr_id=v_po.pr_id)) THEN eligible:=true;EXIT;END IF;
   CONTINUE;
  END IF;
  IF jsonb_typeof(v_marker) IS DISTINCT FROM 'object' OR v_marker->'version' IS DISTINCT FROM '1'::jsonb
   OR v_nlp.workflow_version IS DISTINCT FROM 2 OR v_nlp.pr_id IS DISTINCT FROM v_po.pr_id
   OR v_pr.id IS NULL OR v_pr.status IS DISTINCT FROM 'Approved'
   OR v_nlp.pr_items_hash IS DISTINCT FROM encode(sha256(convert_to(coalesce(v_pr.items,''),'UTF8')),'hex')
   OR v_nlp.po_items_hash IS DISTINCT FROM encode(sha256(convert_to(coalesce(v_po.items,''),'UTF8')),'hex')
   OR v_marker->>'pr_items_hash' IS DISTINCT FROM v_nlp.pr_items_hash
   OR v_marker->>'po_items_hash' IS DISTINCT FROM v_nlp.po_items_hash
   OR v_marker->'technical_comparison' IS DISTINCT FROM v_nlp.details::jsonb->'technical_comparison'
   OR v_nlp.details::jsonb->'reviewed_technical_checks' IS DISTINCT FROM v_nlp.details::jsonb->'technical_comparison' THEN
   CONTINUE;END IF;
  SELECT proof.* INTO v_proof FROM psms_documents.pr_po_reviews proof
   WHERE proof.po_id=p_po_id AND proof.nlp_id=v_nlp.id AND proof.pr_id=v_po.pr_id;
  IF NOT FOUND OR v_proof.pr_items_hash IS DISTINCT FROM v_nlp.pr_items_hash
   OR v_proof.po_items_hash IS DISTINCT FROM v_nlp.po_items_hash THEN CONTINUE;END IF;
  v_hash:=encode(sha256(convert_to(jsonb_build_array(v_nlp.pr_id,v_nlp.po_id,v_nlp.result,v_nlp.confidence,
    v_nlp.details,v_nlp.technical_status,v_nlp.model_version,v_nlp.pr_items_hash,v_nlp.po_items_hash)::text,'UTF8')),'hex');
  IF v_proof.nlp_hash IS DISTINCT FROM v_hash THEN CONTINUE;END IF;
  BEGIN
   v_percentage:=psms_aoq_api.require_ten_percent_review_v4(v_nlp.technical_status,
     v_nlp.details::jsonb->'technical_comparison',v_proof.item_mapping,v_nlp.details::jsonb->'reviewed_technical_checks');
  EXCEPTION WHEN SQLSTATE 'PA001' THEN CONTINUE;END;
  IF v_marker->'percentage' IS DISTINCT FROM to_jsonb(v_percentage) THEN CONTINUE;END IF;
  eligible:=true;EXIT;
 END LOOP;
 IF NOT eligible THEN RAISE EXCEPTION 'Approved reviewed verification required' USING ERRCODE='PA002';END IF;
 RETURN QUERY SELECT v_po.id,v_po.no,v_po.aoq_id,v_po.supplier,v_po.status,v_po.created_at,v_po.items,v_po.total,v_po.pr_id;
END $lock_tolerance$;

REVOKE ALL ON FUNCTION psms_aoq_api.require_ten_percent_review_v4(text,jsonb,jsonb,jsonb) FROM PUBLIC,anon,authenticated,service_role,psms_vercel_backend;

REVOKE ALL ON FUNCTION psms_aoq_api.approve_tolerated_pr_po_v4(bigint,jsonb,jsonb,text,text,boolean) FROM PUBLIC,anon,authenticated,service_role,psms_vercel_backend;

REVOKE ALL ON FUNCTION psms_aoq_api.approve_reviewed_pr_po_v4(bigint,boolean) FROM PUBLIC,anon,authenticated,service_role,psms_vercel_backend;

REVOKE ALL ON FUNCTION psms_aoq_api.lock_po_for_workflow_v4(bigint,text) FROM PUBLIC,anon,authenticated,service_role,psms_vercel_backend;
GRANT EXECUTE ON FUNCTION psms_aoq_api.approve_reviewed_pr_po_v4(bigint,boolean) TO psms_vercel_backend;
GRANT EXECUTE ON FUNCTION psms_aoq_api.lock_po_for_workflow_v4(bigint,text) TO psms_vercel_backend;
RESET ROLE;
DO $restore_schema$
BEGIN IF NOT (SELECT had_create FROM ten_percent_owner_schema) THEN
 REVOKE CREATE ON SCHEMA psms_aoq_api FROM psms_aoq_approval_owner;END IF;
END $restore_schema$;
DO $postconditions$
DECLARE wanted record;actual record;
BEGIN
 IF EXISTS(SELECT 1 FROM ten_percent_old_functions old LEFT JOIN pg_proc p ON p.oid=old.oid
   WHERE p.oid IS NULL OR to_jsonb(p) IS DISTINCT FROM old.metadata) THEN
  RAISE EXCEPTION 'Existing function metadata changed';END IF;
 IF has_schema_privilege('psms_aoq_approval_owner','psms_aoq_api','CREATE')
   IS DISTINCT FROM (SELECT had_create FROM ten_percent_owner_schema) THEN
  RAISE EXCEPTION 'Temporary owner schema privilege was not restored';END IF;
 FOR wanted IN SELECT * FROM (VALUES
  ('psms_aoq_api.require_ten_percent_review_v4(text,jsonb,jsonb,jsonb)','dcba10fecc06cb13c3dc85d887d7c0b1',false,false),
  ('psms_aoq_api.approve_tolerated_pr_po_v4(bigint,jsonb,jsonb,text,text,boolean)','f2c750de246964af449df4e9ebbe6e1f',false,true),
  ('psms_aoq_api.approve_reviewed_pr_po_v4(bigint,boolean)','5eef2d7b2e84d32e1ae1a4630cf70057',true,true),
  ('psms_aoq_api.lock_po_for_workflow_v4(bigint,text)','cebda6b491a15e82be49b821eb7a8f71',true,true)
 ) expected(signature,digest,backend_allowed,definer) LOOP
  SELECT p.* INTO actual FROM pg_proc p WHERE p.oid=to_regprocedure(wanted.signature);
  IF NOT FOUND OR actual.proowner<>'psms_aoq_approval_owner'::regrole OR actual.prosecdef<>wanted.definer
   OR md5(replace(actual.prosrc,chr(13)||chr(10),chr(10)))<>wanted.digest
   OR NOT ('row_security=on'=ANY(actual.proconfig))
   OR NOT EXISTS(SELECT 1 FROM unnest(actual.proconfig) c WHERE replace(c,' ','')='search_path=pg_catalog,pg_temp')
   OR has_function_privilege('psms_vercel_backend',actual.oid,'EXECUTE') IS DISTINCT FROM wanted.backend_allowed
   OR EXISTS(SELECT 1 FROM aclexplode(coalesce(actual.proacl,acldefault('f',actual.proowner))) acl
    WHERE acl.privilege_type='EXECUTE' AND (acl.grantee NOT IN (actual.proowner,'psms_vercel_backend'::regrole)
     OR acl.grantee='psms_vercel_backend'::regrole AND NOT wanted.backend_allowed OR acl.is_grantable)) THEN
   RAISE EXCEPTION 'New tolerance function owner, source or ACL differs: %',wanted.signature;END IF;
 END LOOP;
END $postconditions$;
COMMIT;
