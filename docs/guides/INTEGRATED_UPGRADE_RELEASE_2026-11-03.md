# Integrated upgrade release — 3 November 2026

## Owner deployment progress — 3 November 2026

The local integrated upgrade implementation is complete. The interrupted-session
finalization is recorded in `docs/reports/FINAL_DEPLOYMENT_READINESS_2026-11-03.md`.
That report and `deploy/production/integrated-upgrade-files.txt` supersede the
earlier file counts, package paths, evidence counts and two-migration deployment
instructions below. The accepted gate is 241 passed, zero failures/errors/skips;
it was not repeated during finalization. Migration 05 is newly required and
remains uninstalled and unapproved in this session. Do not rerun 03 or 04.

Supabase Cloud installation:
- 03_account_profiles.sql: Successfully executed.
- 04_live_revisions.sql: Successfully executed.
- Verification: All 16 reported revision-trigger checks passed.
- Existing administrator: One active Admin confirmed.
- Database installation used the owner's explicit no-backup risk-acceptance option.
- Repository migration copies have approval flags restored to NO.

The database migrations must not be executed again.

GitHub publication and Vercel deployment remain pending.
Live application behavior, account isolation and concurrent user operations still require verification after deployment.

Earlier references to migrations being unexecuted describe the state of the original Codex development session.
The owner installation results above supersede that status.

The owner has elected the explicit no-backup path.
Backup-related requirements in the original preparation instructions do not constitute additional deployment steps.

## Implementation and scope

During the original Codex development session on `feature/online-integration`,
existing local changes were preserved and no operational database, credentials,
commit, push or deployment was changed. Subsequently, the owner installed the two
Supabase migrations as documented in the deployment progress section above.

Implemented Batch 1:

- One private mixed-category PPMP intake, with DBM, NON-DBM and LIB assignment per
  item. Suggestions use explicit item, worksheet or document category evidence.
  Human category confirmation remains separate from item review and official creation.
- Original document bytes, extraction warnings/text, item evidence, source links,
  correction/category history, revisions and transactional audit entries are retained.
  Identical uploads reuse the owner's existing source and corrections. Category
  mismatches enter private category review. Pending categories cannot bypass that
  confirmation through the older review endpoints.
- AOQ supplier selection and quotation review remain distinct. A source award hint
  does not select a winner automatically. Reviewed item categories are retained when
  supplied; older item payloads retain their original shape when no category exists.
  Original quotation specifications remain independent of the linked PR.
- PPMP navigation exposes document intake, category reviews and inventory item sources.

Implemented Batch 2:

- Account creation permits Staff and Inspector only. Account management cannot reset
  the designated administrator through the other-user reset action. The existing
  administrator retains the normal current-password change workflow.
- An owner-run migration requires exactly one already-active administrator before
  installing a unique index and guards against additional, removed, demoted or
  disabled administrators. It does not change passwords, usernames or account status.
- Own-account names and profile pictures have stale-revision protection and atomic
  audit writes. Photos are decoded, bounded, stripped of metadata and normalized to
  256 × 256 JPEG. Upload limit is 2 MiB; stored photo limit is 262,144 bytes.
- Pictures reside in `psms_accounts.profiles` as private PostgreSQL bytes with
  owner/session RLS. They are served through authenticated `/account/photo` with
  private ETag revalidation. No public bucket, Storage service key or browser JWT
  bridge is introduced. The shared shell and dashboard use the same avatar endpoint.
- Login uses `static/images/asuncion_hall.jpg`, municipal seal and the existing split
  layout. Shared notifications distinguish errors, success and information.

Implemented Batch 3:

- PostgreSQL revisions cover procurement records, inventory, audit, users and
  owner-scoped document/account changes. Deferred triggers increment revisions at
  commit. The backend receives the bounded `read_versions(text[])` function, not
  direct revision-table access. Browser API roles receive neither new schema.
- Polling refreshes read-only records, counts, eligible source options and avatars.
  It retains editable tables and forms, pauses hidden tabs, backs off after failure
  and refreshes CSRF tokens. Focused tables retry after focus moves. Dirty form data
  is retained; stale submissions still face server-side revision/state validation.
- GET reads reuse one short-lived read-only connection and bounded per-request
  single-row caching. Connections roll back and close at request teardown. Record
  pagination and inventory filtering/counts run in PostgreSQL. No shared account
  cache or cross-request transaction was added.
- Native PDF positioned words are reused within extraction; OCR engines and the
  FastText feature/classifier pipeline remain unchanged.

The FastText runtime, Random Forest runtime/selection, release registry and
original artifact contract are unchanged from HEAD. Semantic mapper source
changes preserve bounded source evidence and uncertainty; model assets and
classifier behavior were not replaced. The generated
deployment context selects the already-approved `nlp-online-original-v2` release.
There was no retraining, algorithm substitution or repeated Linux model verification.

## Essential evidence and limits

Run from the repository root:

```powershell
python -B tests/run_integrated_upgrade.py
node tests/live_updates_dom.cjs
node tests/confirmation_javascript_check.cjs
node tests/record_selectors_javascript_check.cjs
```

The focused Python gate covers actual Flask routes/templates with synthetic data
and SQL doubles, native synthetic Excel/AOQ extraction, existing IAR and RIS/ICS
transaction safeguards, packaging closure and static migration contracts. The
installed-model inference test is explicitly excluded because its separate
verification must not be repeated. JavaScript checks use DOM doubles, not a real
browser. Latest local evidence is `output/integrated_upgrade_20261103/checks.json`.

Final results: 155 Python tests passed, with zero failures, errors or skips. All
three JavaScript checks passed. Source packaging passed with 123 files, 1,660,666
bytes and zero model bytes uploaded. `git diff --check` passed. These are
local-session results; later owner-run Supabase SQL installation and revision-trigger
verification are recorded above. Live application behavior is not yet verified.

During the Codex session, local PostgreSQL verification was unavailable because
Docker returned `permission denied while trying to connect to the docker API`.
Subsequently, the owner executed both Supabase migrations and confirmed all 16
reported revision-trigger rows. Authenticated RLS/session behavior, trigger
contention, simultaneous approvals and live browser behavior have not yet been
verified against the deployed application. Synthetic administrator continuity
does not establish live administrator login access.
The historical scanned two-item PPMP was not retested; no new accuracy claim is made.

The prepared source-only context is
`output/integrated_upgrade_20261103/source-context-final`. Its selected release is
`nlp-online-original-v2`, runtime `retrained_rf`, mode `approved_release`; it contains
no model weights. Source preparation is not a Linux build or hosted verification.

## Exact production changes

The application requires these 27 changed/new source and public UI asset files:

```text
account_profiles.py
aoq_flexible.py
app.py
deploy/build_vercel_assets.py
durable_ocr_v0_1_0.py
live_updates.py
ppmp_intake.py
request_queries.py
static/css/integrated.css
static/images/asuncion_hall.jpg
static/js/confirm-actions.js
static/js/dashboard.js
static/js/live-updates.js
static/js/ppmp-ocr-review.js
static/js/record-selectors.js
templates/account.html
templates/aoq.html
templates/base.html
templates/dashboard.html
templates/login.html
templates/nlp.html
templates/operation_error.html
templates/ppmp_intake.html
templates/public_shell.html
templates/record_pagination.html
templates/users.html
ui_feedback.py
```

Operational delivery additionally changes
`.github/workflows/online-production.yml` and supplies
`deploy/production/03_account_profiles.sql`,
`deploy/production/04_live_revisions.sql` and
`deploy/production/verify_integrated_upgrade.sql`. SQL is not copied into the runtime
or invoked by Flask startup, image builds or the deployment workflow.

The exact 48-file release list, including 15 focused test/support files, this guide
and the list itself, is `deploy/production/integrated-upgrade-files.txt`. Other
untracked deployment proposals, skill files, datasets, model weights, uploads,
secrets and generated output are excluded. This is not a new audit of older Git history.

## Supabase prerequisites and owner-run installation

Use the existing owner-approved project only after identifying its dashboard and
endpoint. These installers are pinned to project ref `fenrqjzupidswgrjzdgn` and
database `postgres`. A database name or a SQL setting alone cannot verify the
project. Preview/demo targets and secrets must remain separate from municipal data.

Historical installation prerequisites (both migrations are already installed):

1. The owner used the migration's explicit no-backup-risk acceptance option.
   Preserve protected persistent session-key custody and confirm the existing
   administrator can still sign in after deployment. Do not export credentials.
2. Existing base schema, OCR draft/review columns, server authentication sessions,
   private signatories and durable document-review migrations. Required document
   columns include owner/source IDs and revisions; this upgrade does not backfill
   ownerless sources or change existing documents.
3. Already-reviewed workbook schema/backend installation, specifically
   `psms_workbooks.active_actor_id()` and the session permission-readiness function.
   `01_workbook_schema.sql` and `02_workbook_backend.sql` are prerequisites, not
   instructions to rerun or auto-install them. Stop if either predecessor is missing.
4. `psms_vercel_backend` is an existing nonowner LOGIN role with NOINHERIT,
   NOSUPERUSER, NOBYPASSRLS, NOCREATEDB, NOCREATEROLE and NOREPLICATION. Its approved
   public-table RLS/session/audit permissions must already support the application.
   Account profile writes require the existing user/session SELECT and row-lock
   permissions and transactional audit INSERT/sequence access. The new SQL does
   not grant credential-field access or widen public procurement policies.
5. Exactly one existing Admin, already Active, with usable credentials. Zero,
   inactive or multiple administrators make migration 03 abort without changing
   accounts. The owner resolves any discrepancy separately; never create a recovery
   administrator or modify credentials as an implicit migration step.

Installation record (already completed; **do not rerun these migrations**):

1. The owner successfully executed `03_account_profiles.sql` in Supabase as `postgres`,
   using explicit owner approval and the supported no-backup-risk acceptance option.
2. The owner then successfully executed `04_live_revisions.sql` using the same option.
   The staged repository copies have both approval flags restored to `NO`.
3. The owner executed `verify_integrated_upgrade.sql`. The provided export confirmed
   all 16 reported revision-trigger checks; other results (including API-role
   access checks) are not independently established by that export alone.
4. After application deployment, verify the existing Admin login, user management,
   authenticated profile/RLS access and revision polling. Database catalog checks
   do not establish live session behavior or user isolation.

Historical `psql` command equivalents, shown for reference only; the migrations
have already been installed and **must not be rerun**:

```powershell
psql --no-psqlrc --set=ON_ERROR_STOP=1 --file="<private reviewed copy of 03_account_profiles.sql>"
psql --no-psqlrc --set=ON_ERROR_STOP=1 --file="<private reviewed copy of 04_live_revisions.sql>"
psql --no-psqlrc --set=ON_ERROR_STOP=1 --file=deploy/production/verify_integrated_upgrade.sql
```

Repository installer copies remain unapproved. Do not put execution copies,
passwords or connection URLs in Git. No reset, seed import, initializer, workbook
repair or credential rewrite belongs in this sequence. The owner explicitly
selected the SQL migration's no-backup-risk option; this release has no additional
backup-creation step. Existing security and access checks still apply.

## Exact Git commands

The owner has already staged exactly the 48 listed paths, verified the allowlist
matches the index, and confirmed `git diff --cached --check` passed. No commit,
push or deployment has occurred at the time of this document update. Preserve
all unrelated local work. Recheck the staged list before committing:

```powershell
git branch --show-current
git status --short
git diff --cached --name-only
git add --pathspec-from-file=deploy/production/integrated-upgrade-files.txt
git diff --cached --check
git diff --cached --stat
git diff --cached
git commit -m "feat: finish integrated procurement and account upgrades"
git push -u origin feature/online-integration
```

Expected branch: `feature/online-integration`. Neither broad staging nor forced
pushes are required. Review the branch's existing history separately before any
first publication to a new remote. Pushing this branch does not authorize production
deployment; automatic Vercel Git deployments remain disabled.

## Vercel configuration and deployment

Retain the existing Container framework, `Dockerfile.vercel`, Fluid Compute and
one Flask website. Deploy the generated source context, never the development
checkout. The builder preserves the approved release and rewrites only its copied
Dockerfile's inference/runtime selection. The source registry is not reapproved or
edited. Container support is documented by [Vercel](https://vercel.com/docs/functions/runtimes/container).

Effective Vercel settings below apply to the separately authorized Production
target only. Keep existing secret values private. Config placeholders require
owner-confirmed existing values; this session did not read live settings.

| Key | Type (Secret or Config) | Value | Environment |
| --- | --- | --- | --- |
| `SUPABASE_DB_URL` | Secret | KEEP the verified restricted `psms_vercel_backend` DSN with verified TLS | Production only |
| `PSMS_SECRET_KEY` | Secret | KEEP the persistent protected Batch 9A key; never regenerate during this rollout | Production only |
| `PSMS_MODEL_PART_TOKENS` | Secret | KEEP valid scoped download-token JSON for `nlp-online-original-v2`; renew privately before expiration | Production only |
| `PSMS_ENV` | Config | `production` | Production |
| `PSMS_PRODUCTION_DB_ENABLED` | Config | `1`, only after owner authorization and predecessor checks | Production |
| `PSMS_DURABLE_WORKBOOKS_ENABLED` | Config | `1`, with the existing reviewed workbook backend | Production |
| `PSMS_NLP_RUNTIME` | Config | `retrained_rf` | Production |
| `PSMS_INFERENCE_ENABLED` | Config | `1` | Production |
| `PSMS_INFERENCE_RELEASE_ID` | Config | `nlp-online-original-v2` | Production |
| `PSMS_INFERENCE_CACHE_ENABLED` | Config | `1` | Production |
| `PSMS_DB_BACKEND_ROLE` | Config | `psms_vercel_backend` | Production |
| `PSMS_SUPABASE_PROJECT_REF` | Config | `fenrqjzupidswgrjzdgn`, after owner confirms this is the intended project | Production |
| `PSMS_DB_EXPECTED_DATABASE` | Config | `postgres` | Production |
| `PSMS_DB_EXPECTED_HOST` | Config | KEEP the exact verified existing direct/pooler hostname | Production |
| `PSMS_DB_CONNECTION_MODE` | Config | KEEP the existing reviewed `direct`, `session` or `transaction` mode | Production |
| `PSMS_DB_TRANSACTION_POOLER` | Config | `1` only with transaction mode/6543; otherwise `0` with session/direct/5432 | Production |
| `PSMS_DB_SSLROOTCERT` | Config | `/app/certs/supabase-ca.crt` | Production |
| `PSMS_TRUSTED_HOSTS` | Config | Existing owner-verified production hostname(s), comma-separated; no arbitrary forwarded hosts | Production |
| `PORT` | Config | `8080`, explicitly in project settings | Production |
| `PSMS_BIND_HOST` | Config | `0.0.0.0`, image default | Production |
| `PSMS_DATA_DIR` | Config | `/tmp/psms`, temporary work only; documents/profiles/revisions remain in PostgreSQL | Production |
| `PSMS_THREADS` | Config | `2`, image default; verify total connections across instances separately | Production |
| `TESSERACT_CMD` | Config | `/usr/bin/tesseract`, image default | Production |
| `HF_HUB_OFFLINE` | Config | `1`, image default | Production |
| `TRANSFORMERS_OFFLINE` | Config | `1`, image default | Production |
| `HF_DATASETS_OFFLINE` | Config | `1`, image default | Production |

No new Supabase Storage credentials are needed for profile photos. Retain the
existing approved private model transport; do not upload models again or substitute
an external NLP/OCR service. Do not point Preview at the municipal database or copy
Production secrets into it. A synthetic hosted demo requires its own separately
verified target, backend permissions and staging configuration.

The existing workflow uses these GitHub environment secrets, not browser values:

| Key | Type (Secret or Config) | Value | Environment |
| --- | --- | --- | --- |
| `VERCEL_ORG_ID` | Secret | KEEP existing verified organization ID | GitHub Actions `production` |
| `VERCEL_PROJECT_ID` | Secret | KEEP existing verified project ID | GitHub Actions `production` |
| `VERCEL_TOKEN` | Secret | KEEP existing restricted deployment token | GitHub Actions `production` |

Before deployment, confirm the already-installed Supabase upgrades and review
live RLS/session behavior when the application is available. The owner explicitly
accepted no-backup risk during installation; no additional backup step is required.
Batch 9B bounded authentication remains a separate pending change; this release
does not approve or apply it. Verify current plan
eligibility and actual capacity. Vercel's [Hobby plan](https://vercel.com/docs/plans/hobby)
is for personal, noncommercial use; it is not established here as suitable for
municipal operations. Current [function limits](https://vercel.com/docs/functions/limitations)
also constrain memory, duration and HTTP payloads. Application document uploads
remain bounded to 4,000,000 bytes and total requests to 4,400,000 bytes on Vercel.

After those gates and explicit deployment authorization, run the existing manual
workflow for the reviewed commit with exactly these inputs:

```powershell
gh workflow run online-production.yml --ref feature/online-integration -f inference_mode=approved_release -f nlp_runtime=retrained_rf -f release_id=nlp-online-original-v2 -f deploy_approved=true -f database_upgrades_ready=true
```

`database_upgrades_ready=true` now means installed 03 and 04 plus newly reviewed
05 and the corresponding owner checks passed; it never runs SQL. Do not rerun
03 or 04. `deploy_approved=true` is the owner's final deployment
authorization. The workflow must already be available on the repository's default
branch for manual dispatch. Monitor the selected run and existing Vercel project.

If preparing a fresh local context for owner review, use a new output directory:

```powershell
python -B deploy/online_delivery.py context --production --inference-mode approved_release --release nlp-online-original-v2 --runtime retrained_rf --output output/integrated-upgrade-owner-review
```

Do not commit that generated context. A build or liveness response alone does not
prove OCR, NLP, private persistence or concurrent procurement behavior.

## Live feature verification after an authorized rollout

Use synthetic documents and approved test accounts. Keep two independent browser
sessions and retain the prior application/configuration rollback target.

1. Confirm HTTPS login shows the municipal hall and seal. Confirm the existing
   administrator signs in and still opens User Management. Creating or posting an
   Admin role must fail; existing credentials must remain unchanged.
2. Update a Staff name/photo, replace and remove the photo, and inspect the shared
   header, menu, dashboard and account page. Verify another account receives its
   own image. Anonymous requests must redirect to sign-in. The image endpoint must
   remain private and support ETag/304 responses. Invalid images must fail.
3. Upload a synthetic six-item mixed workbook: two DBM, three NON-DBM, one LIB.
   Check original download bytes, warnings/text, item evidence and unconfirmed
   category status. Confirm categories, then perform normal item review and
   explicit official creation. Verify source/category history and audit entries.
   Re-upload identical bytes and submit a stale revision; corrections must survive
   and duplicates must not appear. Another account must not access the source.
4. Upload a synthetic AOQ linked to an approved PR. Verify quotations retain the
   uploaded descriptions, quantities, units and prices. Explicitly choose/load a
   supplier; verify merely viewing extraction does not award it. Correct categories
   and missing values, save with confirmation, then follow normal AOQ/PO approval.
5. In session A, leave unsaved values and a source selection. Change an eligible
   shared record in session B. Within the polling interval (approximately 15–17
   seconds), records/counts should update while A's form values remain intact.
   Focus a record link during a change, then move focus; the table should update
   on a subsequent poll. Hide/show the tab and verify polling pauses/resumes.
6. Verify `/api/sync/revisions?topics=ppmp,documents,account` returns only bounded
   revision strings, including only the current owner's private revisions. A
   nonadministrator request for `topics=users` must fail with 403. Revoke a test
   session and verify subsequent requests lose access.
7. On a cold instance, run the existing approved FastText + Random Forest
   verification feature and native OCR on synthetic material. Confirm approved
   release identity, manual review and uncertainty behavior. This is live feature
   verification, not retraining or a new Linux model-verification campaign.
8. Reopen a private review in another instance. Confirm its saved state survives.
   Verify CSRF rejects a missing token, and test repeated/conflicting synthetic
   approvals, audit rollback and simultaneous stock operations in the approved
   isolated environment. Confirm no doubled inventory or deductions.

Rollback means restoring a separately reviewed compatible application/configuration
target while preserving new procurement activity, private documents and security
controls. Leave additive account/revision schema in place unless an owner reviews
a specific rollback. Never restore a whole database or restart legacy initialization
to fix a UI or authentication problem.
