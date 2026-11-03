# First actual Asuncion deployment: website ON, inference OFF

Historical first-release procedure. For the current approved FastText + Random
Forest release, follow `docs/reports/FINAL_DEPLOYMENT_READINESS_2026-11-03.md`.
The current workflow requires `approved_release`, `retrained_rf`, and
`nlp-online-original-v2`; its disabled-inference instructions below no longer
apply. Migrations 03 and 04 are already installed; do not run them again.
Migration 05 requires separate owner review and installation approval.

Existing website: https://asuncion-one.vercel.app
Repository: personal-armanbaghocan2/Asuncion (default branch main).
Existing Supabase project: fenrqjzupidswgrjzdgn.

The installed workbook SQL is already in place. Do NOT rerun SQL, initialize the
database, bootstrap accounts or run repair/reset/synchronization scripts at
startup. No push, deployment, model approval or cloud modification was performed
by this implementation. This is one existing app/project/database.

## What this first release does

- Flask/Waitress starts with `PSMS_INFERENCE_ENABLED=0` and no approved release,
  private weights, model download credentials or model verification requirement.
  Tesseract and the existing CPU dependencies/code are retained for later use.
- The image defaults enable `PSMS_PRODUCTION_DB_ENABLED=1` and
  `PSMS_DURABLE_WORKBOOKS_ENABLED=1`. Workbooks use the installed private,
  immutable PostgreSQL `psms_workbooks` adapter, with the existing
  `psms_vercel_backend`, sessions, transactions and audit behavior. Function disk
  is scratch only; no persistent Excel write under /app is required.
- Login, roles, CSRF, dashboard/sidebar/login appearance, existing data and manual
  procurement routes are preserved. Existing PPMP/APP/PR/AOQ/PO/NLP/IAR/inventory/
  RIS/ICS/report/audit/user-management records and relationships are preserved.
- New PPMP OCR/reprocessing, AOQ extraction and NLP classification requests fail
  closed with 503 while inference is off. No other model/heuristic prediction is
  substituted. Existing records can still be viewed and handled through their
  existing authorized flows. Deterministic comparison remains; semantic model
  suggestions are unavailable. A downstream step requiring a NEW NLP verification
  still waits for approved inference; no approval/state prerequisite is bypassed.
- Liveness and login do not initialize SQL or load models. Authenticated /health
  treats deliberately disabled inference separately from database readiness.
- Direct files remain capped at 4,000,000 bytes; request bodies at 4,400,000 bytes.
  The database workbook limit remains 8 MiB. Larger direct uploads remain blocked.

These are prepared code paths, not proof of a successful online deployment or
end-to-end municipal workflow. Initial acceptance uses fictional records only.

## Exact reviewed delivery set

Prepared set including the TLS correction: `output/supabase_ca_delivery/github-files.txt` with source
copies/hashes in `github-source/` and `inventory.json`. Required new files are
listed in `required-untracked.txt`. `vercel-source/` is the source-only CLI context.
The list excludes private weights, archives/parts, tokens/signed URLs, datasets,
SQL installers, generated outputs/reports and the separate model-test workflows.
All existing templates and reviewed static assets are included.

To regenerate AFTER edits, use a NEW output name:

```powershell
python -B deploy/online_delivery.py initial --output output/first_production_delivery_next
```

Before committing, stop if unrelated changes are already staged. These commands
are provided for OWNER execution only; no Git mutation was performed here:

```powershell
git status --short
git diff --cached --name-only
git add --pathspec-from-file=output/supabase_ca_delivery/github-files.txt
git diff --cached --stat
git diff --cached --check
git diff --cached
git commit --only --pathspec-from-file=output/supabase_ca_delivery/github-files.txt -m "fix: package the Supabase database CA for production TLS"
git push -u origin HEAD
```

Current activity branch is `feature/online-integration`; do not force-push main
or discard other work. Review the branch's PR and merge the intended delivery to
main when authorized. The workflow file must exist on the default branch to
offer manual dispatch. Automatic Vercel Git deployment stays disabled by
`vercel.json` (`git.deploymentEnabled=false`). Do not stage .env, output, models,
private documents, the generated runtime directory or model-part credentials.
This file list is not a new audit/certification of older repository history.

## Vercel Production settings (existing project)

| Setting | Required first-deployment value |
| --- | --- |
| Framework preset | Container, Fluid Compute; existing Hobby project |
| Root / Build / Install overrides | Context root has Dockerfile.vercel; clear old Python build/install/output overrides |
| `PORT` | `8080` explicitly in project settings |
| `VERCEL_SUPPORT_LARGE_FUNCTIONS` | `1` if needed for the existing CPU dependency image's large-function opt-in |
| Memory / maximum duration | Hobby 2 GB / 300 seconds; no paid upgrade |
| `PSMS_ENV` | `production` |
| `PSMS_PRODUCTION_DB_ENABLED` | `1` |
| `PSMS_DURABLE_WORKBOOKS_ENABLED` | `1` |
| `PSMS_INFERENCE_ENABLED` | `0` |
| `PSMS_DATA_DIR` | `/tmp/psms` |
| `PSMS_THREADS` | `2` |
| `PSMS_BIND_HOST` | `0.0.0.0`, image default |
| `PSMS_TRUSTED_HOSTS` | `asuncion-one.vercel.app`; add only separately reviewed actual production domains |
| `PSMS_SECRET_KEY` | KEEP the existing persistent protected key, minimum 32 characters; never regenerate per start |
| `PSMS_DB_BACKEND_ROLE` | `psms_vercel_backend` |
| `PSMS_SUPABASE_PROJECT_REF` | `fenrqjzupidswgrjzdgn` |
| `PSMS_DB_EXPECTED_DATABASE` | Existing actual database, normally `postgres` |
| `PSMS_DB_EXPECTED_HOST` | Exact existing Supabase direct/pooler hostname, without scheme; must match DSN |
| `PSMS_DB_CONNECTION_MODE` | Existing reviewed endpoint mode: `transaction`, `session`, or supported `direct` |
| `PSMS_DB_TRANSACTION_POOLER` | `1` for transaction/6543; `0` for session/direct/5432 |
| `PSMS_DB_SSLROOTCERT` | `/app/certs/supabase-ca.crt`, image default and required Vercel Production override |
| `SUPABASE_DB_URL` | Restricted backend DSN described below, secret in Vercel Production only |
| `TESSERACT_CMD` | `/usr/bin/tesseract`, image default |

Leave `PSMS_INFERENCE_RELEASE_ID`, `PSMS_MODEL_PART_TOKENS`,
`PSMS_INFERENCE_READ_TOKEN`, `OCR_SEMANTIC_MODEL_PATH` and
`PSMS_RETRAINED_NLP_DIR` unset for this first release. No model secret or release
approval is required for the website-off-inference mode. HF/Transformers offline
flags stay enabled. Do not set PG* override variables or debug/testing flags.

The DSN accepts the Supabase Session Pooler `postgresql://` URI with explicit
host, port, database, login and URL-encoded password. Missing `sslmode` defaults
to `verify-full`; missing `sslrootcert` uses `PSMS_DB_SSLROOTCERT`. Those validated
TLS settings are passed to psycopg, not merely checked. Explicit weaker modes
are rejected, and explicit URI certificate settings take precedence.
The image packages the owner's locally tested `supabase-ca.crt` unchanged at
`/app/certs/supabase-ca.crt`, readable by the nonroot runtime. It is one PUBLIC
Supabase Root 2021 CA certificate, valid April 2021 through April 2031, with DER
SHA-256 `807025ad50d4ed219d2c9c7d299c004f824eb00cf7f65afef607d07b72e6cafa`.
Packaging and the image build reject changed certificate material, private keys,
or extra PEM blocks. The original root file remains available for local use.
No certificate is fetched during build or runtime.

Update Vercel Production `PSMS_DB_SSLROOTCERT` to the image path. IMPORTANT: if
`SUPABASE_DB_URL` includes `sslrootcert`, remove that query parameter to use the
environment setting, or change its value to `/app/certs/supabase-ca.crt`.
An old URI certificate path overrides the environment setting. Keep
`sslmode=verify-full`; never switch to require/verify-ca or disable hostname
checks. A laptop certificate path will block startup.

Local parsing/packaging proves certificate identity, not the live pooler chain.
The owner's successful local connection is endpoint evidence only if it used
this same Session Pooler hostname/5432 and verify-full. After deployment, confirm
`/health/live`, normal administrator sign-in and authenticated `/health` with
`database=true`. No SQL, credential or permission change is part of this TLS fix.

Shared session-pooler template (replace placeholders PRIVATELY in Vercel,
not in source or a shell command; URL-encode the password):

```text
postgresql://psms_vercel_backend.fenrqjzupidswgrjzdgn:<BACKEND_PASSWORD>@<ACTUAL_SHARED_POOLER_HOST>:5432/postgres?sslmode=verify-full
```

Use `PSMS_DB_CONNECTION_MODE=session`, `PSMS_DB_TRANSACTION_POOLER=0` and
the exact same host/database expectations for that template. A shared transaction
pooler uses port 6543, mode `transaction`, flag 1, and the same project-scoped login.
Direct mode uses `db.fenrqjzupidswgrjzdgn.supabase.co`, port 5432, flag 0 and login
`psms_vercel_backend`. Do not use postgres/admin/service-role credentials.
No password, endpoint reachability or TLS trust was inferred or changed here.

GitHub repository's existing `production` environment needs
`VERCEL_ORG_ID`, `VERCEL_PROJECT_ID`, `VERCEL_TOKEN` for the EXISTING project.
Copy the exact IDs from its Vercel settings, not from a guessed domain/project
name. Keep owner environment approval enabled where configured. No Supabase,
session or model credential is needed by the deployment job.

## Actual manual deployment action

After settings, source review and owner push/merge approval:

Actions -> **Existing Vercel production deployment** -> Run workflow on main ->
`inference_mode=disabled`, leave `release_id` EMPTY, check `deploy_approved` ->
approve the GitHub Production environment if configured.

Equivalent explicit CLI action (OWNER runs after approving deployment):

```powershell
gh workflow run online-production.yml --repo personal-armanbaghocan2/Asuncion --ref main -f inference_mode=disabled -F deploy_approved=true
```

The existing workflow assembles the exact source-only context, checks <90 MB,
builds on Vercel and deploys using the existing project IDs. It cannot run on a
push, enable inference or require Docker Desktop. The container listens on
0.0.0.0:8080 through `python serve.py` -> existing Waitress -> `wsgi.application`.

For an owner-authorized DIRECT Vercel CLI deployment instead of GitHub Actions,
from the same prepared context with existing project IDs already set in the
terminal environment and an authenticated Vercel CLI session:

```powershell
Set-Location output/supabase_ca_delivery/vercel-source
npx --yes vercel@latest deploy --prod --yes
```

Never deploy the development checkout root. Never put a Vercel token in a script,
Git commit or log. The manual workflow is the preferred path.

## First online acceptance and genuine remaining blockers

1. Open https://asuncion-one.vercel.app/health/live, then sign in normally.
   Confirm the existing dashboard/sidebar/login remain. A liveness result alone
   does not prove database/login access.
2. At the owner's intended demonstration source replacement, upload a fictional
   Excel workbook below 4 MB. Hard-refresh and refresh the dataset/source;
   confirm it persists without reupload. Existing version history and replacement
   semantics remain; rows omitted from a replacement may be deactivated.
3. Open existing procurement/inventory/reports pages and an existing record using
   normal roles. OCR/NLP actions must return disabled/unavailable, never a
   prediction. Do not create approvals merely to exercise a module.

Only real setup/build/connectivity problems block this first website release:
missing/invalid backend DSN/role/password/TLS/host configuration or session key,
missing existing-project deployment credentials, unavailable Container runtime,
or a genuine Vercel image/build/runtime failure. Model approval, private model
tokens and the Linux inference campaign are NOT blockers while inference is off.
Actual credentials, cloud access and the first build/online acceptance have not
been verified by this task. Existing login hash/account compatibility and actual
workflow transitions must be demonstrated normally; no protection is bypassed.

Later model activation is separate: approve the immutable release, prepare an
`approved_release` context with its explicit ID, configure its scoped credentials
and set `PSMS_INFERENCE_ENABLED=1` deliberately. All existing approval, Linux,
artifact/classifier/feature checks remain mandatory. Flipping the flag on this
empty-registry first-release image cannot activate an unapproved model.

For the existing FastText + Random Forest, the CURRENT registry is still
unapproved. Keep the working website's inference flag at 0 until the existing
owner approves `retrained_rf` explicitly using the existing finalizer procedure
in `docs/reports/MODEL_RELEASE_OWNER_REVIEW.md`. GitHub Linux verification #4 has
passed; reuse its bound evidence under
`output/model_approval_preparation/linux-evidence`. Do not repeat it. The prepared
v2 decision supports `AUTHORIZED_ONLINE_REVIEW_ONLY`, with explicit acceptance
of experimental limitations, authorized documents and mandatory human review.
The decision remains `NOT_APPROVED`; successful compatibility is not owner consent.

After genuine approval, publish the NEW immutable release parts privately and
verify their transport hashes. Configure only Vercel Production:

- `PSMS_INFERENCE_ENABLED=1`
- `PSMS_NLP_RUNTIME=retrained_rf`
- `PSMS_INFERENCE_RELEASE_ID=<EXACT_APPROVED_RELEASE_ID>`
- `PSMS_INFERENCE_CACHE_ENABLED=1`
- `PSMS_MODEL_PART_TOKENS`: object-scoped signed-download token JSON for the NEW
  approved parts, stored only as a production secret; no service-role key.

Keep the working database, packaged CA, session key and workbook settings.
Leave the legacy broad read token unset. Never commit token values or signed URLs.
The original local launcher still defaults to legacy inference and needs neither
this production profile nor Supabase/network access for local model execution.

Run the EXISTING production workflow with `inference_mode=approved_release`,
`nlp_runtime=retrained_rf`, the approved release ID, and `deploy_approved=true`.
The selected runtime is checked against release approval AND placed in the
generated image's ENV before its build gate executes; it is no longer fixed to
legacy. No production setting is changed automatically by source preparation.

Acceptance: authenticated `/health` must report the matching `nlp_runtime` and
`inference_release_id`, enabled/active inference and database readiness. Process
an explicitly authorized supported document through its existing review screen,
link the appropriate authorized PR/PO, then run NLP Verification. Verify the saved record shows
FastText + Random Forest, 559 features, status/issue probabilities and warnings,
and remains For Review after refreshing. A health flag alone does not prove the
upload/OCR/model/database workflow. Do not approve an official procurement transaction
merely to test model output. Production end-to-end acceptance is pending.
