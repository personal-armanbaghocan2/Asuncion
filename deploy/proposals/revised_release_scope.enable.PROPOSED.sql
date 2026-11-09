-- Separate exact-deployment authorization; NOT AUTHORIZED FOR CLOUD EXECUTION.
-- Supply reviewed values using protected operator settings. Never commit tokens.
-- Set psms.release_token_sha256 to the SHA-256 hex digest of the protected
-- Preview token. Never paste the raw token into SQL Editor or query history.
-- Hash the token's UTF-8 text exactly; do not hex-decode the token first.
BEGIN;
SET LOCAL search_path=pg_catalog;
SET LOCAL lock_timeout='5s';
DO $authorize_deployment$
DECLARE v_token_hash text;v_host text;v_authority text;v_expires timestamptz;v_reference text;
BEGIN
 IF current_user<>'postgres' OR session_user<>current_user
  OR current_setting('psms.release_enable_approved',true) IS DISTINCT FROM 'YES'
  OR current_setting('psms.release_target_project',true) IS DISTINCT FROM 'fenrqjzupidswgrjzdgn' THEN
  RAISE EXCEPTION 'Exact reviewed deployment and target approval required';
 END IF;
 v_token_hash:=current_setting('psms.release_token_sha256',true);
 v_host:=current_setting('psms.release_host',true);
 v_authority:=current_setting('psms.release_link_authority',true);
 v_expires:=current_setting('psms.release_expires_at',true)::timestamptz;
 v_reference:=current_setting('psms.release_approval_reference',true);
 IF v_token_hash IS NULL OR v_token_hash !~ '^[0-9a-f]{64}$'
  OR v_host IS NULL OR v_host !~ '^asuncion-[a-z0-9]{9}-arman-a87f[.]vercel[.]app$'
  OR v_authority IS NULL OR v_authority NOT IN ('ADMIN_DOCUMENTED','EXISTING_REVIEWERS_DOCUMENTED')
  OR current_setting('psms.release_clock',true) IS DISTINCT FROM 'ASIA_MANILA'
  OR v_expires IS NULL OR v_expires<=clock_timestamp() OR v_expires>clock_timestamp()+interval '7 days'
  OR v_reference IS NULL OR length(btrim(v_reference))<10 THEN
  RAISE EXCEPTION 'Exact host, token digest, authority, clock, expiry and approval reference required';
 END IF;
 IF (SELECT policy->'enabled' FROM psms_documents.procurement_policy WHERE singleton)
  IS DISTINCT FROM 'false'::jsonb THEN
  RAISE EXCEPTION 'Global workflow must remain disabled during Preview';
 END IF;
 -- INSERT only: an existing approval cannot be silently replaced or extended.
 INSERT INTO psms_revised_release.deployments
 (release_id,deployment_host,token_sha256,enabled,expires_at,po_clock,link_authority,approval_reference)
 VALUES('revised-procurement-v1',v_host,decode(v_token_hash,'hex'),true,v_expires,
  'ASIA_MANILA',v_authority,v_reference);
END $authorize_deployment$;
COMMIT;

-- Rollback/feature-disable, after separate owner approval:
-- BEGIN;
-- UPDATE psms_revised_release.deployments SET enabled=false
-- WHERE release_id='revised-procurement-v1' AND deployment_host=:exact_approved_host;
-- COMMIT;
-- This waits for active scoped transactions, then blocks subsequent operations.
-- It preserves procurement records, proofs and the original global policy.
