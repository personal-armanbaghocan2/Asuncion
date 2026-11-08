---
name: asuncion-online-deployment
description: Prepare, implement, test, or troubleshoot hosting compatibility and publication safety for the existing Asuncion Flask system, retaining PostgreSQL, offline OCR and FastText. Use for runtime configuration, portable containers, private persistence, GitHub readiness or authorized deployment; not automatic cloud access or data transfer.
---

# Asuncion Online Deployment

## Scope and execution

Read AGENTS.md first. Follow INSPECT -> REPRODUCE -> IMPLEMENT -> TEST -> VERIFY -> REPORT. Implement clearly scoped, safe, authorized work rather than repeatedly proposing it. Review-only requests permit no writes. Preserve unrelated changes, Windows local development, the exact dashboard/sidebar/login, PostgreSQL layer, procurement workflows, Tesseract, semantic mapping and installed FastText contracts.

One existing Flask application and website remain the product. Vercel and Supabase Cloud are intended options, not proven live infrastructure. Recommend a container-capable Flask host when native engines/resources make that the practical fit; do not replace the application. GitHub Pages cannot execute this Flask backend.

Configuration preparation does not authorize cloud connections, resource creation, account changes, migrations, transfers, publication or deployment. Do not inspect private records, credentials, local-only reviews or training corpora. Public official documentation lookup is permitted without sending project code or data.

## Establish the actual architecture

Inspect app.py entrypoint/ensure()/init_db(), db.py, runtime_configuration.py, requirements.txt, launchers, AOQ routes, durable_ocr_v0_1_0.py, private_document_uploads.py, inventory_dataset.py, server_sessions_v0_1_0.py, web_security_v0_1_0.py and relevant tests. Inspect actual callers and contracts before edits.

Identify WSGI export, Python/packages/native binaries, import/startup/first-request effects, model loading, subprocess/background work, filesystem paths and persistence. Do not assume old file staging or initialization remains active. Never run setup/reset/seed/import scripts to inspect readiness. Initialization, maintenance and migrations must remain explicit operator actions outside request/cold-start code.

Use ../asuncion-aoq-ocr-accuracy/SKILL.md for AOQ accuracy, ../asuncion-ocr-review/SKILL.md for general extraction, ../asuncion-workflow-review/SKILL.md for transitions and atomic approvals, and ../asuncion-fasttext-review/SKILL.md only for separately authorized dataset review.

## Runtime and platform compatibility

- Verify Linux/Python wheels, shared libraries, Tesseract executable/language packs, PDF/image/native-document libraries, model assets and actual bundle/memory/startup/concurrency/processing limits.
- Preserve configurable TESSERACT_CMD and Linux PATH discovery; never hardcode Windows executables into hosted configuration. Importing pytesseract does not prove OCR works.
- Preserve installed FastText weights, labels, feature dimensions/order, classifiers, manifests, paths and no-download semantic mapping. Report loading failures safely; no retraining, replacement or activation.
- Use a production WSGI server. Preserve local launchers; configure host/port and bounded worker/thread capacity explicitly. Do not run Waitress/app.run inside a serverless function.
- Keep health liveness free of database writes, schema repair, model downloads and sensitive diagnostics. Distinguish shallow liveness, configuration checks and dependency/database readiness.
- Review temporary storage limits and bounded cleanup, unique jobs, concurrent OCR capacity and background-task lifetime. Process-local locks/caches are not durable or distributed coordination.
- Inspect current official provider documentation before choosing a platform. Verify Vercel Flask/Python function, native executable, size/duration/memory/disk and plan eligibility if evaluating Vercel. Measure rather than declaring it unsupported from assumptions.
- Prepare a portable container only when it fits existing dependencies. Prefer explicit build inputs and non-root execution. Building does not prove hosted deployment. Never include secrets, private records or experimental training material in an image.

## PostgreSQL, storage and transactions

Require explicit production SUPABASE_DB_URL and persistent protected PSMS_SECRET_KEY. Validate settings without echoing DSNs or credentials. Never silently use the local database in production. Confirm selected endpoint, verified TLS/root certificate, least-privilege role, pooling/prepared-statement behavior, connection bounds and timeouts. PostgreSQL-compatible source does not establish a live Supabase Cloud connection.

Do not execute production migrations, reset/repair tables, transfer records or bootstrap accounts. Preserve existing schema/migration contracts. Keep approval/stock/audit changes atomic on one connection with deterministic row locking and rollback/close. Do not hold transactions across OCR or HTTP uploads.

Trace actual persistent files and database document bytes/drafts. Ephemeral instance disk cannot preserve cross-request state. Keep private documents out of public static paths and cookies. Any proposed Supabase Storage change needs an explicit design: Flask accounts are not Supabase JWTs, service keys bypass RLS, and every upload/download/signed link needs current server authorization. Database and object Storage operations are not one atomic transaction.

Inventory workbook paths need approved private durable storage or separately verified behavior. Do not mount existing municipal folders into test containers. Plan bounded retention, private downloads, orphan handling and encrypted database/document/key backups with isolated restoration evidence.

## Security gates

Inspect actual protections before claiming them. Safely correct confirmed backward-compatible configuration defects with synthetic regressions:
- Production debug disabled; stable environment secrets; HTTPS, secure/HttpOnly/SameSite cookies and reviewed lifetime.
- Server-side role/status/record access, CSRF on state changes, cross-user isolation and independent-session/stale-form behavior.
- Login abuse controls and non-enumerating errors; no confidential payloads, DSNs, passwords, hashes or signed links in logs.
- File signature/type/size/page/pixel/archive limits, safe generated paths, cleanup and private retrieval.
- Trusted hosts and explicitly reviewed proxy counts. Never trust arbitrary forwarded headers or enable ProxyFix implicitly.
- Suitable security headers without breaking existing pages/printing. Do not invent an untested content-security policy.

Batch 9B remains a separate pending authorization gate. Read docs/reports/BATCH_9B_POSTGRES_FINAL_PREPARATION_REPORT.md and docs/reports/BATCH_9B_DEPLOYMENT_READINESS_PLAN.md when relevant. Inspect current code; proposed bounded verification is not automatically live. Do not apply its authentication patch, access municipal credential fields, rewrite/reset credentials, enable plaintext fallback or create recovery administrators.

Credential-field access, bounded aggregate audit, owner account identification/recovery, patch application and production restart retain separate approvals. Integer-only format aggregates do not prove administrator access. Require owner-verified recovery, protected key custody, encrypted backups and isolated restore evidence before production cutover.

## GitHub and container publication safety

Inspect git status/branch before edits. Use tracked-file and history path/size metadata first. Flag municipal spreadsheets, private documents, dumps/seeds, uploads, datasets, review worksheets, generated outputs, keys and environment files without opening protected contents. Scan authorized source/config examples for possible embedded secrets without printing values. Do not inspect credential files or private historical blobs just to certify publication.

Distinguish untracked/private material, sensitive tracked paths and historical objects. Adding .gitignore does not remove tracked files or past commits. Review large model assets and their release/privacy rights. .dockerignore is independent of .gitignore; protect build contexts and image layers.

Never delete sensitive material, untrack files, rewrite history, change remotes, commit, push or publish automatically. Report exact blocker paths/categories and required owner actions. A source scan or ignore check alone cannot certify all history clean.

## Verification and reporting

Inspect test effects and transitive fixtures/imports first. Use generated synthetic inputs and explicit isolated settings. Deny production/database network access; do not fall back to municipal configuration. tests/SYSTEM_SMOKE_TEST.py reads a database; tests/validate_installation.py initializes data. Neither is a safe read-only gate.

Test configuration validation, WSGI startup without initialization, dependencies, no-secret error paths, native OCR, installed-model contracts and private persistence contracts. Run applicable login/role/CSRF, PPMP/APP/PR/AOQ/PO/NLP/IAR/inventory/RIS/ICS/report/print/audit regressions. Mock/AST tests do not establish real PostgreSQL locking or complete end-to-end success; real concurrency tests need approved isolated infrastructure.

Verify final changed-file scope and protected model/schema/workflow hashes. Distinguish passed, failed, skipped, excluded private fixtures and infrastructure-blocked checks. Fix introduced regressions or revert only the unsafe change while preserving others' work.

Report IMPLEMENTED actual paths, TESTED commands/environment/results, ISOLATED candidates, PREPARED future configuration, BLOCKED specific missing evidence/approvals, and NOT DEPLOYED when no verified deployment occurred. A build, health response or written guide is not deployment or production readiness.

## Official references

Check current versions/limits when needed:
- https://flask.palletsprojects.com/en/stable/deploying/
- https://flask.palletsprojects.com/en/stable/deploying/waitress/
- https://vercel.com/docs/frameworks/backend/flask
- https://vercel.com/docs/functions/runtimes/python
- https://vercel.com/docs/functions/limitations
- https://supabase.com/docs/guides/database/connecting-to-postgres
- https://supabase.com/docs/guides/platform/ssl-enforcement
- https://supabase.com/docs/guides/storage/security/access-control

Demo/production deployment requires explicit target, transfer scope, authorization and rollback criteria. Never discard new procurement activity or blindly restore a database for an authentication defect. Repeat affected regressions after any separately authorized rollout.
