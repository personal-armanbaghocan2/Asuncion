# First actual Asuncion deployment: website ON, inference OFF

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

Prepared set: `output/first_online_deployment/github-files.txt` with source
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
git add --pathspec-from-file=output/first_online_deployment/github-files.txt
git diff --cached --stat
git diff --cached --check
git diff --cached
git commit --only --pathspec-from-file=output/first_online_deployment/github-files.txt -m "deploy: prepare first online release with durable workbooks and inference disabled"
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
| `SUPABASE_DB_URL` | Restricted backend DSN described below, secret in Vercel Production only |
| `TESSERACT_CMD` | `/usr/bin/tesseract`, image default |

Leave `PSMS_INFERENCE_RELEASE_ID`, `PSMS_MODEL_PART_TOKENS`,
`PSMS_INFERENCE_READ_TOKEN`, `OCR_SEMANTIC_MODEL_PATH` and
`PSMS_RETRAINED_NLP_DIR` unset for this first release. No model secret or release
approval is required for the website-off-inference mode. HF/Transformers offline
flags stay enabled. Do not set PG* override variables or debug/testing flags.

The DSN must contain exactly host, port, dbname, user, password, sslmode and
sslrootcert. Keep verified TLS and a certificate file that actually exists IN
THE LINUX IMAGE. System roots are `/etc/ssl/certs/ca-certificates.crt`; use them
only when they validate the chosen endpoint. A laptop certificate path will
block startup. If that endpoint needs a project-specific public CA instead,
provide its reviewed trust file in the image; do not weaken TLS.

Shared transaction-pooler template (replace placeholders PRIVATELY in Vercel,
not in source or a shell command; URL-encode the password):

```text
postgresql://psms_vercel_backend.fenrqjzupidswgrjzdgn:<BACKEND_PASSWORD>@<ACTUAL_SHARED_POOLER_HOST>:6543/postgres?sslmode=verify-full&sslrootcert=/etc/ssl/certs/ca-certificates.crt
```

Use `PSMS_DB_CONNECTION_MODE=transaction`, `PSMS_DB_TRANSACTION_POOLER=1` and
the exact same host/database expectations for that template. A shared session
pooler uses port 5432, mode `session`, flag 0, and the same project-scoped login.
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
Set-Location output/first_online_deployment/vercel-source
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
