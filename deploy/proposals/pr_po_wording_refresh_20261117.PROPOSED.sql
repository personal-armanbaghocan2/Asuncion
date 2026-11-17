-- PREPARED ONLY. Install after reviewed owner/target/backup approval.
-- Archives prior comparison/review; never reruns a model or approves a record.
-- Rollback app first, DROP only the new function; preserve recorded history.
-- Existing official relationship proof and approved status deny refresh.
BEGIN;
SET LOCAL lock_timeout='5s';
SET LOCAL statement_timeout='60s';
SET LOCAL search_path=pg_catalog,pg_temp;
CREATE TEMP TABLE wording_refresh_before ON COMMIT DROP AS
 SELECT p.oid,to_jsonb(p) metadata FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
 WHERE n.nspname IN ('psms_aoq_api','psms_signatories');
CREATE TEMP TABLE wording_refresh_schema_before ON COMMIT DROP AS
 SELECT has_schema_privilege('psms_aoq_approval_owner','psms_aoq_api','CREATE') had_create;
DO $preconditions$
DECLARE dep record;
BEGIN
 IF current_user<>'postgres' OR session_user<>current_user
  OR current_setting('transaction_isolation')<>'read committed'
  OR current_setting('server_version_num')::integer NOT BETWEEN 170000 AND 179999
  OR NOT pg_has_role(session_user,'psms_aoq_approval_owner','SET') THEN
  RAISE EXCEPTION 'Reviewed owner PostgreSQL17 read-committed installer required';END IF;
 IF NOT EXISTS(SELECT 1 FROM pg_roles WHERE rolname='psms_aoq_approval_owner'
  AND NOT rolcanlogin AND NOT rolsuper AND NOT rolbypassrls AND NOT rolcreatedb AND NOT rolcreaterole)
  OR NOT EXISTS(SELECT 1 FROM pg_roles WHERE rolname='psms_vercel_backend'
  AND rolcanlogin AND NOT rolsuper AND NOT rolbypassrls AND NOT rolcreatedb AND NOT rolcreaterole) THEN
  RAISE EXCEPTION 'Existing restricted owner/backend required';END IF;
 FOR dep IN SELECT * FROM (VALUES
  ('psms_aoq_api.approval_actor_v1()','d38444a829b882fb720f20d0ede9b00f',false),
  ('psms_aoq_api.mark_pr_po_reviewed_v3(bigint,jsonb,jsonb,text,text,boolean)','54a45ecdd82de1bca36f8c4884776fac',true),
  ('psms_aoq_api.require_ten_percent_review_v4(text,jsonb,jsonb,jsonb)','dcba10fecc06cb13c3dc85d887d7c0b1',false)
 ) expected(signature,digest,definer) LOOP
  IF NOT EXISTS(SELECT 1 FROM pg_proc p WHERE p.oid=to_regprocedure(dep.signature)
   AND p.proowner='psms_aoq_approval_owner'::regrole AND p.prosecdef=dep.definer
   AND md5(replace(p.prosrc,chr(13)||chr(10),chr(10)))=dep.digest
   AND 'row_security=on'=ANY(p.proconfig)
   AND EXISTS(SELECT 1 FROM unnest(p.proconfig) s WHERE replace(s,' ','')='search_path=pg_catalog,pg_temp')
   AND has_function_privilege('psms_aoq_approval_owner',p.oid,'EXECUTE')) THEN
   RAISE EXCEPTION 'Reviewed refresh dependency differs: %',dep.signature;END IF;
 END LOOP;
 IF to_regprocedure('psms_aoq_api.refresh_pr_po_comparison_v4(bigint,jsonb,boolean)') IS NOT NULL THEN
  RAISE EXCEPTION 'Refresh routine already exists; inspect instead of replacing';END IF;
 IF EXISTS(SELECT 1 FROM unnest(ARRAY['id','no','po_id','pr_id','workflow_version','result','confidence','details','technical_status','status','model_version','pr_items_hash','po_items_hash','reviewed_by','reviewed_at','review_mapping','relationship_reason','evidence_reference','staff_decision']) c
    WHERE NOT has_column_privilege('psms_aoq_approval_owner','public.nlp',c,'SELECT'))
  OR EXISTS(SELECT 1 FROM unnest(ARRAY['dt','user','action','module','ref','details']) c
    WHERE NOT has_column_privilege('psms_aoq_approval_owner','public.audit_trail',c,'INSERT'))
  OR EXISTS(SELECT 1 FROM unnest(ARRAY['details','technical_status','status','reviewed_by','reviewed_at','review_mapping','relationship_reason','evidence_reference','staff_decision']) c
    WHERE NOT has_column_privilege('psms_aoq_approval_owner','public.nlp',c,'UPDATE')) THEN
  RAISE EXCEPTION 'Existing private owner comparison/review permissions required';END IF;
 IF NOT (SELECT had_create FROM wording_refresh_schema_before) THEN
  GRANT CREATE ON SCHEMA psms_aoq_api TO psms_aoq_approval_owner;END IF;
END $preconditions$;
SET LOCAL ROLE psms_aoq_approval_owner;
CREATE FUNCTION psms_aoq_api.refresh_pr_po_comparison_v4(p_nlp_id bigint,p_checks jsonb,p_confirm boolean)
RETURNS TABLE(nlp_id bigint,po_id bigint,new_status text)
LANGUAGE plpgsql VOLATILE SECURITY DEFINER
SET search_path=pg_catalog,pg_temp SET row_security=on SET TimeZone='UTC'
SET lc_time='C' SET lock_timeout='5s' SET statement_timeout='15s'
AS $refresh$
DECLARE actor record;preview record;aoq record;pr record;po record;n record;
 role_name text;policy text;old_details jsonb;saved jsonb;old_line jsonb;new_line jsonb;
 old_check jsonb;new_check jsonb;mapping jsonb:='[]';history jsonb;archive jsonb;updated jsonb;
 i integer;j integer;side integer;changed integer:=0;position integer;source_item jsonb;
 source_quantity text;source_unit text;quantity numeric;tail text[];product text[];frequency text[];
 clause text;description text;core jsonb;left_core jsonb;stamp timestamptz:=clock_timestamp();
BEGIN
 IF current_setting('transaction_isolation')<>'read committed' THEN
  RAISE EXCEPTION 'Read committed required' USING ERRCODE='PA002';END IF;
 IF p_confirm IS DISTINCT FROM true THEN RAISE EXCEPTION 'Explicit recalculation confirmation required' USING ERRCODE='PA001';END IF;
 SELECT * INTO STRICT actor FROM psms_aoq_api.approval_actor_v1();
 SELECT role INTO role_name FROM public.users WHERE id=actor.actor_id;
 PERFORM 1 FROM psms_documents.procurement_policy p WHERE singleton
  AND p.policy->'enabled'='true'::jsonb AND p.policy->>'po_clock'='ASIA_MANILA';
 IF NOT FOUND THEN RAISE EXCEPTION 'Procurement workflow disabled' USING ERRCODE='PA007';END IF;
 SELECT x.po_id,x.pr_id,p.aoq_id INTO preview FROM public.nlp x JOIN public.po p ON p.id=x.po_id WHERE x.id=p_nlp_id;
 IF NOT FOUND THEN RAISE EXCEPTION 'Verification unavailable' USING ERRCODE='PA004';END IF;
 SELECT id,pr_id,status INTO aoq FROM public.aoq WHERE id=preview.aoq_id FOR SHARE;
 SELECT id,no,status,items,app_id,ppmp_id,office INTO pr FROM public.pr WHERE id=preview.pr_id FOR UPDATE;
 SELECT id,no,aoq_id,pr_id,status,items INTO po FROM public.po WHERE id=preview.po_id FOR UPDATE;
 SELECT x.id,x.no,x.po_id,x.pr_id,x.workflow_version,x.result,x.confidence,x.details,x.technical_status,x.status,x.model_version,
  x.pr_items_hash,x.po_items_hash,x.reviewed_by,x.reviewed_at,x.review_mapping,x.relationship_reason,x.evidence_reference,x.staff_decision
 INTO n FROM public.nlp x WHERE x.id=p_nlp_id FOR UPDATE;
 IF n.id IS NULL OR n.workflow_version IS DISTINCT FROM 2 OR n.status IS NULL OR n.status NOT IN ('For Review','Reviewed')
  OR n.po_id IS DISTINCT FROM po.id OR n.pr_id IS DISTINCT FROM pr.id OR po.aoq_id IS DISTINCT FROM aoq.id
  OR po.status IS DISTINCT FROM 'Approved' OR pr.status IS DISTINCT FROM 'Approved' OR aoq.status IS DISTINCT FROM 'Approved'
  OR (aoq.pr_id IS NOT NULL AND aoq.pr_id IS DISTINCT FROM pr.id)
  OR (po.pr_id IS NOT NULL AND po.pr_id IS DISTINCT FROM pr.id)
  OR n.pr_items_hash IS DISTINCT FROM encode(sha256(convert_to(coalesce(pr.items,''),'UTF8')),'hex')
  OR n.po_items_hash IS DISTINCT FROM encode(sha256(convert_to(coalesce(po.items,''),'UTF8')),'hex') THEN
  RAISE EXCEPTION 'Sources or verification changed' USING ERRCODE='PA002';END IF;
 IF EXISTS(SELECT 1 FROM psms_documents.pr_po_reviews proof WHERE proof.po_id=po.id OR proof.pr_id=pr.id OR proof.nlp_id=n.id) THEN
  RAISE EXCEPTION 'Official approval or receiving evidence cannot be recalculated' USING ERRCODE='PA002';END IF;
 IF aoq.pr_id IS NULL THEN
  SELECT p.policy->>'link_authority' INTO policy FROM psms_documents.procurement_policy p WHERE singleton;
  IF policy IS NULL OR policy='PENDING' THEN RAISE EXCEPTION 'Relationship authority pending' USING ERRCODE='PA005';END IF;
  IF NOT ((policy='ADMIN_DOCUMENTED' AND role_name='Admin') OR (policy='EXISTING_REVIEWERS_DOCUMENTED'
    AND role_name IN ('Admin','Staff','Inspector'))) THEN RAISE EXCEPTION 'Authorized reviewer required' USING ERRCODE='PA003';END IF;
  PERFORM 1 FROM public.app_plan a JOIN public.ppmp m ON m.id=a.ppmp_id WHERE a.id=pr.app_id
   AND a.status='Approved' AND m.status='Approved' AND (pr.ppmp_id IS NULL OR pr.ppmp_id=m.id) FOR SHARE OF a,m;
  IF NOT FOUND THEN RAISE EXCEPTION 'Approved planning evidence required' USING ERRCODE='PA006';END IF;
 END IF;
 old_details:=n.details::jsonb;saved:=old_details->'technical_comparison';
 IF old_details ? 'verification_lineage' OR EXISTS(SELECT 1 FROM psms_documents.nlp_legacy_replacements l WHERE l.replacement_nlp_id=n.id) THEN
  PERFORM 1 FROM psms_documents.nlp_legacy_replacements l JOIN public.nlp original ON original.id=l.legacy_nlp_id
   WHERE l.replacement_nlp_id=n.id AND l.po_id=po.id AND l.pr_id=pr.id
    AND old_details->'verification_lineage'->>'legacy_nlp_id'=l.legacy_nlp_id::text
    AND old_details->'verification_lineage'->>'legacy_record_sha256'=l.legacy_record_sha256
    AND encode(sha256(convert_to(to_jsonb(original)::text,'UTF8')),'hex')=l.legacy_record_sha256 FOR SHARE OF original;
  IF NOT FOUND THEN RAISE EXCEPTION 'Saved lineage changed' USING ERRCODE='PA002';END IF;
 END IF;
 IF jsonb_typeof(saved) IS DISTINCT FROM 'object' OR jsonb_typeof(p_checks) IS DISTINCT FROM 'object'
  OR saved->>'version' IS DISTINCT FROM 'PR_PO_REVIEW_V1' OR p_checks->>'wording_policy' IS DISTINCT FROM 'CORE_DESCRIPTION_V2'
  OR (p_checks-ARRAY['rows','technical_status','wording_policy']) IS DISTINCT FROM (saved-ARRAY['rows','technical_status','wording_policy'])
  OR jsonb_typeof(saved->'rows') IS DISTINCT FROM 'array' OR jsonb_typeof(p_checks->'rows') IS DISTINCT FROM 'array'
  OR jsonb_array_length(saved->'rows') NOT BETWEEN 1 AND 200
  OR jsonb_array_length(saved->'rows')<>jsonb_array_length(p_checks->'rows')
  OR p_checks->>'technical_status' IS DISTINCT FROM 'MATCH' THEN
  RAISE EXCEPTION 'Only bounded wording comparison refresh is supported' USING ERRCODE='PA001';END IF;
 FOR i IN 0..jsonb_array_length(saved->'rows')-1 LOOP
  old_line:=saved->'rows'->i;new_line:=p_checks->'rows'->i;
  IF (old_line-'checks') IS DISTINCT FROM (new_line-'checks') OR jsonb_typeof(old_line->'checks') IS DISTINCT FROM 'array'
   OR jsonb_typeof(new_line->'checks') IS DISTINCT FROM 'array'
   OR jsonb_array_length(old_line->'checks')<>jsonb_array_length(new_line->'checks') THEN
   RAISE EXCEPTION 'Item correspondence or check coverage changed' USING ERRCODE='PA001';END IF;
  mapping:=mapping||jsonb_build_array(new_line->'po_position');
  FOR j IN 0..jsonb_array_length(old_line->'checks')-1 LOOP
   old_check:=old_line->'checks'->j;new_check:=new_line->'checks'->j;
   IF old_check IS NOT DISTINCT FROM new_check THEN CONTINUE;END IF;
   IF old_check->>'attribute' IS DISTINCT FROM 'source specification wording'
    OR old_check->>'state' IS DISTINCT FROM 'REQUIRES_HUMAN_REVIEW'
    OR new_check->>'state' IS DISTINCT FROM 'MATCH' OR (old_check-'state') IS DISTINCT FROM (new_check-'state')
    OR old_check->'conditional_on_identity' IS DISTINCT FROM 'false'::jsonb THEN
    RAISE EXCEPTION 'Only an equivalent wording check may change' USING ERRCODE='PA001';END IF;
   -- Independent SQL grammar mirrors CORE_DESCRIPTION_V2; unknown requirements
   -- cannot be waived by a forged browser comparison or a similarity score.
   FOR side IN 0..1 LOOP
    description:=CASE WHEN side=0 THEN old_check->>'requested' ELSE old_check->>'offered' END;
    position:=CASE WHEN side=0 THEN (old_line->>'pr_position')::integer ELSE (old_line->>'po_position')::integer END;
    source_item:=(CASE WHEN side=0 THEN pr.items::jsonb ELSE po.items::jsonb END)->(position-1);
    source_quantity:=coalesce(nullif(source_item->>'Quantity',''),nullif(source_item->>'quantity',''),nullif(source_item->>'qty',''),
     nullif(source_item->>'QTY',''),nullif(source_item->>'Requested Quantity',''),nullif(source_item->>'Awarded Quantity',''));
    source_unit:=lower(btrim(coalesce(nullif(source_item->>'Unit',''),nullif(source_item->>'unit',''),
     nullif(source_item->>'Unit of Measure',''),nullif(source_item->>'unit_of_measure',''),nullif(source_item->>'uom',''))));
    IF source_quantity IS NULL OR source_quantity !~ '^[+]?[0-9]+([.][0-9]+)?$' THEN
     RAISE EXCEPTION 'Valid source quantity required for wording refresh' USING ERRCODE='PA001';END IF;
    quantity:=source_quantity::numeric;
    source_unit:=CASE WHEN source_unit IN ('pc','pcs','piece','pieces') THEN 'piece'
                     WHEN source_unit IN ('unit','units') THEN 'unit' ELSE source_unit END;
    core:='{}';
    FOR clause IN SELECT btrim(x) FROM regexp_split_to_table(lower(regexp_replace(description,'\s+',' ','g')),'[,;]') x LOOP
     IF clause='' OR clause IN ('plug-and-play','plug and play','compatible with standard office computers') THEN CONTINUE;END IF;
     tail:=regexp_match(clause,'\s+([0-9]+(?:\.[0-9]+)?)\s+(pcs?|pieces?|units?)$');
     IF tail IS NOT NULL THEN
      IF tail[1]::numeric<>quantity OR (CASE WHEN tail[2] IN ('pc','pcs','piece','pieces') THEN 'piece' ELSE 'unit' END) IS DISTINCT FROM source_unit THEN
       RAISE EXCEPTION 'Embedded quantity or unit differs' USING ERRCODE='PA001';END IF;
      clause:=btrim(regexp_replace(clause,'\s+([0-9]+(?:\.[0-9]+)?)\s+(pcs?|pieces?|units?)$',''));
     END IF;
     product:=regexp_match(clause,'^(?:([0-9]+(?:\.[0-9]+)?)\s+)?wireless optical mouse$');
     IF clause='wireless optical mouse' OR product IS NOT NULL THEN
      IF product[1] IS NOT NULL AND product[1]::numeric<>quantity THEN
       RAISE EXCEPTION 'Embedded quantity differs' USING ERRCODE='PA001';END IF;
      core:=core||'{"product":"wireless optical mouse"}';CONTINUE;
     END IF;
     frequency:=regexp_match(clause,'^([0-9]+(?:\.[0-9]+)?)\s*ghz(?: connection)?$');
     IF frequency IS NOT NULL THEN
      core:=core||jsonb_build_object('frequency',coalesce(core->'frequency','{}')||jsonb_build_object(trim_scale(frequency[1]::numeric)::text,true));CONTINUE;
     END IF;
     IF clause='usb receiver' THEN core:=core||'{"receiver":"usb"}';CONTINUE;END IF;
     RAISE EXCEPTION 'Unknown, mandatory or negative description clause' USING ERRCODE='PA001';
    END LOOP;
    IF core->>'product' IS DISTINCT FROM 'wireless optical mouse' THEN RAISE EXCEPTION 'Explicit mouse product required' USING ERRCODE='PA001';END IF;
    IF side=0 THEN left_core:=core;
    ELSIF core IS DISTINCT FROM left_core THEN RAISE EXCEPTION 'Core product attributes differ' USING ERRCODE='PA001';END IF;
   END LOOP;
   changed:=changed+1;
  END LOOP;
 END LOOP;
 IF changed=0 OR psms_aoq_api.require_ten_percent_review_v4('MATCH',p_checks,mapping,p_checks) IS DISTINCT FROM 0 THEN
  RAISE EXCEPTION 'Refresh requires final zero discrepancy and changed wording' USING ERRCODE='PA001';END IF;
 history:=coalesce(old_details->'comparison_revisions_v4','[]');
 IF jsonb_typeof(history) IS DISTINCT FROM 'array' OR jsonb_array_length(history)>=50 THEN
  RAISE EXCEPTION 'Comparison history needs separate review' USING ERRCODE='PA002';END IF;
 archive:=jsonb_build_object('archived_at',stamp,'archived_by',actor.actor_id,'reason','CORE_DESCRIPTION_V2 confirmed recalculation',
  'technical_comparison',saved,'technical_status',n.technical_status,'status',n.status,'reviewed_by',n.reviewed_by,'reviewed_at',n.reviewed_at,
  'review_mapping',n.review_mapping,'relationship_reason',n.relationship_reason,'evidence_reference',n.evidence_reference,
  'staff_decision',n.staff_decision,'workflow_review_v3',old_details->'workflow_review_v3',
  'threshold_approval_v4',old_details->'threshold_approval_v4','reviewed_technical_checks',old_details->'reviewed_technical_checks');
 updated:=(old_details-ARRAY['workflow_review_v3','threshold_approval_v4','reviewed_technical_checks'])||jsonb_build_object(
  'technical_comparison',p_checks,'comparison_revisions_v4',history||jsonb_build_array(archive));
 IF octet_length(updated::text)>4194304 THEN RAISE EXCEPTION 'Comparison history size limit' USING ERRCODE='PA001';END IF;
 UPDATE public.nlp SET details=updated::text,technical_status='MATCH',status='For Review',reviewed_by=NULL,reviewed_at=NULL,
  review_mapping=NULL,relationship_reason=NULL,evidence_reference=NULL,staff_decision=NULL WHERE id=p_nlp_id;
 INSERT INTO public.audit_trail(dt,"user",action,module,ref,details)
 VALUES(to_char(stamp AT TIME ZONE 'Asia/Manila','Mon DD, YYYY HH12:MI AM'),actor.actor_username,
  'Recalculated PR-PO Comparison','NLP',n.no,'Prior review archived; unchanged sources; bounded wording comparison recalculated to 0%; fresh review required');
 RETURN QUERY SELECT n.id,po.id,'For Review'::text;
END $refresh$;
REVOKE ALL ON FUNCTION psms_aoq_api.refresh_pr_po_comparison_v4(bigint,jsonb,boolean) FROM PUBLIC,anon,authenticated,service_role,psms_vercel_backend;
GRANT EXECUTE ON FUNCTION psms_aoq_api.refresh_pr_po_comparison_v4(bigint,jsonb,boolean) TO psms_vercel_backend;
RESET ROLE;
DO $restore_schema$
BEGIN IF NOT (SELECT had_create FROM wording_refresh_schema_before) THEN
 REVOKE CREATE ON SCHEMA psms_aoq_api FROM psms_aoq_approval_owner;END IF;END $restore_schema$;
DO $postconditions$
DECLARE f record;
BEGIN
 IF EXISTS(SELECT 1 FROM wording_refresh_before b LEFT JOIN pg_proc p ON p.oid=b.oid
  WHERE p.oid IS NULL OR to_jsonb(p) IS DISTINCT FROM b.metadata) THEN RAISE EXCEPTION 'Existing function changed';END IF;
 IF has_schema_privilege('psms_aoq_approval_owner','psms_aoq_api','CREATE') IS DISTINCT FROM
  (SELECT had_create FROM wording_refresh_schema_before) THEN RAISE EXCEPTION 'Owner schema privilege not restored';END IF;
 SELECT * INTO f FROM pg_proc WHERE oid=to_regprocedure('psms_aoq_api.refresh_pr_po_comparison_v4(bigint,jsonb,boolean)');
 IF f.proowner<>'psms_aoq_approval_owner'::regrole OR NOT f.prosecdef OR NOT ('row_security=on'=ANY(f.proconfig))
  OR NOT EXISTS(SELECT 1 FROM unnest(f.proconfig) s WHERE replace(s,' ','')='search_path=pg_catalog,pg_temp')
  OR md5(replace(f.prosrc,chr(13)||chr(10),chr(10)))<>'a4a3cca709023a74e4a87e670da5213d'
  OR NOT has_function_privilege('psms_vercel_backend',f.oid,'EXECUTE')
  OR EXISTS(SELECT 1 FROM aclexplode(coalesce(f.proacl,acldefault('f',f.proowner))) a
   WHERE a.privilege_type='EXECUTE' AND (a.grantee NOT IN(f.proowner,'psms_vercel_backend'::regrole) OR a.is_grantable)) THEN
  RAISE EXCEPTION 'Refresh owner, source or ACL differs';END IF;
END $postconditions$;
COMMIT;
