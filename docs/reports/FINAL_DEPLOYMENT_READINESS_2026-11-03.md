# Final Asuncion implementation and deployment-readiness report

Date: 3 November 2026. Branch: `feature/online-integration`. Reviewed baseline:
`b0b09ef` (`Implement integrated Asuncion system upgrades`).

The interrupted implementation is integrated locally and its source package is
complete. Publication and deployment remain pending owner approval. Real database
behavior, hosted capacity and municipal production readiness are not established.

The accepted integrated gate remains **241 executed, 241 passed, zero failures,
errors or skips**. Finalization did not repeat that gate or change product code.
The selected FastText + Random Forest release remains `nlp-online-original-v2`.
Migrations **03 and 04 are already installed; do not rerun them**. The newly
required migration is `deploy/production/05_document_processing.sql`.

## Finalized work

The unfinished work was release preparation: required new runtime files were
untracked, the explicit Git delivery list omitted them, older generated packages
were stale, and deployment instructions did not include migration 05.

This finalization:

- Expanded `deploy/production/integrated-upgrade-files.txt` to 81 explicit paths,
  including the required runtime files, focused regression dependencies, migration
  05, metadata verification and release documentation. Installed migrations 03/04
  are excluded from this continuation's staging list.
- Prepared a fresh, exact source-only production context inside the existing
  ignored `output/procurement_completion_20261103` evidence directory. No second
  application or project directory was created.
- Corrected the stale asset-count assertion in `tests/test_vercel_preparation.py`
  from 18 to 22. The focused asset/certificate checks now pass.
- Added owner-run, read-only `deploy/production/verify_document_processing.sql`.
  It is prepared SQL, not executed database evidence.
- Marked the earlier inference-disabled deployment guide as historical and linked
  the integrated release guide to this current report.

No application module, template, CSS, JavaScript, model, credential or installed
migration was edited during finalization. The existing interrupted changes remain
in the working tree. Nothing was staged, committed, pushed or deployed.

## Feature integration

| Feature | Integrated source and behavior | Evidence and limit |
| --- | --- | --- |
| Automatic PPMP segregation | `ppmp_intake.py` routes source items to DBM, NON-DBM or LIB using explicit item/worksheet/document evidence or unambiguous exact catalog evidence. Unknown/conflicting items remain in Needs Classification. Category routing does not approve procurement. | Accepted actual-route tests include 18 generated items, six per category, and an unresolved item that does not block other categories. Models are doubled in these route tests. |
| Categorized drafts | `templates/ppmp_intake.html`, `templates/ppmp_type.html` and `durable_ocr_v0_1_0.py` expose all/category/unclassified lists, counts, source evidence, correction forms, search, sorting and pagination. Official creation remains separate. | Accepted synthetic route/template checks. Cross-instance SQL behavior remains to be verified. |
| Document History, search and sorting | `document_history.py`, history/detail templates and `templates/record_filters.html` provide owner-bound source history, processing/review status, filters, whitelisted sorting, bounded paging, downloads and recorded workflow links. `app.py` adds record-list filters. | Filters and owner denials have synthetic coverage. New PostgreSQL queries have not been exercised against the live target in this session. |
| Existing source reprocessing | `document_processing.py` and the PPMP reprocess route reuse immutable source bytes and IDs. Durable claims prevent simultaneous processing; revisions/identity checks preserve corrections and official drafts. Changed anchors require reconciliation rather than guessed replacement. | Accepted synthetic checks cover zero-item sources, repeated processing, conflicts and corrected drafts. Migration 05 and real PostgreSQL concurrency verification remain required. This describes PPMP reprocessing; it does not claim a new AOQ reprocess endpoint. |
| AOQ multi-item processing | `aoq_flexible.py`, `procurement_item_contract.py`, `app.py` and `templates/aoq_quotations.html` preserve item/category/supplier relationships, source specifications and independent quotation paging/search. Supplier review and explicit winner confirmation remain separate. | Accepted generated-source checks include two item groups and six quotations from three suppliers. No winner is selected from the lowest price or from merely viewing extraction. |
| Government-style login | `templates/login.html` and scoped additions in `static/css/integrated.css` use the municipal hall, seal, curved split layout and responsive form. Existing dashboard/sidebar source remains preserved. | Source and asset checks passed. No new real-browser visual claim is made during finalization. |
| Authentication and URL security | `app.py`, `session_navigation.py`, server sessions and CSRF middleware validate active accounts before protected routes. Optional opaque GET-link MACs bind the current session, endpoint and route arguments. Source access still checks the current owner. | Accepted authentication, navigation and CSRF regressions use synthetic sessions. Authenticated bare bookmarks remain allowed and must pass route/resource checks. A URL MAC is not authorization. Batch 9B and login-abuse requirements remain open below. |
| Multi-user live updates | `live_updates.py`, `static/js/live-updates.js` and installed migration 04 use database revisions and owner-scoped private topics. Read-only regions/counts refresh while editable forms persist; focused regions retry later. Migration 05 adds processing-run revision events. | Polling is approximately 15–17 seconds, not a WebSocket service. Synthetic/DOM evidence does not establish live independent-browser or database contention behavior. |

The source changes in `procurement_document_layout.py`, `aoq_flexible.py` and
`offline_ocr_mapper.py` preserve bounded native header/title evidence, complete
specifications and unresolved applicability warnings. These are extraction/source
mapping changes, not retraining or replacement of the installed NLP models.
No new accuracy claim is made for the historical two-item scanned PDF; its exact
source regression was not rerun during this finalization.

## Production package verification

Prepared context:
`output/procurement_completion_20261103/production-context-final`.

| Property | Verified locally |
| --- | --- |
| Delivery files | 131 total; 127 runtime inputs plus four container/context files |
| Python modules | 54, including new history, processing and session-navigation modules |
| HTML templates | 45, including all four new templates |
| Reviewed public assets | 22 CSS/JS/image files, including `js/document-workspace.js` |
| Source size | 1,759,680 bytes; below the preparer's 90,000,000-byte source limit |
| Mode / runtime / release | `approved_release` / `retrained_rf` / `nlp-online-original-v2` |
| Model weights in source upload | Zero bytes |
| Approved artifact metadata | 28 artifacts, 165,631,728 bytes; original source pins match the approved manifest |
| Approved manifest SHA-256 | `3b6b141c5e798222331662d564eff4e363a605fc5d9b422ab6b68224493dd22b` |

The check followed local Python imports without importing the application, parsed
the packaged Python and Jinja source, verified literal template/static references
and local CSS asset references, and compared every copied runtime input byte.
The generated registry selects only the existing approved release; the working
registry and source artifact contract were preserved. No model was loaded,
downloaded, rehashed in full, retrained, activated or substituted in this session.

The older `output/rf_production_delivery/source-upload/runtime` snapshot lacked
21 current required paths and had 37 changed runtime inputs. Do not deploy that
snapshot. Other historical output directories retain their original contents;
they are not the authoritative current package.

The source context is not a rebuilt Linux image. `Dockerfile.vercel` retains the
pinned Linux dependency lock, native Tesseract/English data and Waitress serving
the existing Flask WSGI application. Its build gate checks approved metadata and
native OCR; installed model inference is checked separately at runtime. Private
approved model delivery/caching remains the existing mechanism. No new external
OCR/NLP API was introduced.

## Validation retained and performed

- **Retained:** `output/procurement_completion_20261103/checks.json`, 241 passed.
  Scope: synthetic actual Flask routes/templates and SQL doubles, no network or
  real database. Installed-model verification is explicitly excluded from that
  gate, not counted as a skipped test.
- **Reproduced packaging defect:** the single asset-reference test failed with
  `AssertionError: 22 != 18`. Only its stale expected count was corrected.
- **Passed:** 13 focused tests from `AssetBuildTests` and
  `PackagedDatabaseCATests`, with network denied. These cover exact asset copying,
  conflicts/private-file exclusion, template references, container configuration
  and the unchanged packaged public database CA.
- **Passed:** source-only production context generation, import closure,
  Python/Jinja parsing, template/static/CSS references, release metadata, exact
  runtime-byte comparisons and protected-source preservation.
- **Passed:** final `git diff --check`; index remains unchanged and empty.
- **Prepared, not executed:** migration 05 and its read-only catalog verification.
  No Supabase connection, credential audit or real account query occurred.

Machine-readable local evidence, source hashes, complete package inventory and
exact Git path classification are in
`output/procurement_completion_20261103/final-readiness-evidence.json`.
These generated/private evidence files are excluded from Git publication.

## Exact Git scope

`deploy/production/integrated-upgrade-files.txt` is the continuation's explicit
81-path staging list. It includes required source/UI changes, focused tests and
reviewed SQL/documentation. Some already-tracked entries are unchanged; staging
them creates no new diff. The final staged diff must still be reviewed by the owner.

Eight required runtime paths are currently untracked and must accompany `app.py`:

```text
document_history.py
document_processing.py
session_navigation.py
static/js/document-workspace.js
templates/aoq_quotations.html
templates/document_detail.html
templates/document_history.html
templates/record_filters.html
```

Other untracked material is outside this release: `.agents/`, `.env.example`,
`AGENTS.md`, historical launch/setup files, the alternate root `Dockerfile`,
adviser staging, older deployment/proposal/diagnostic files, historical reports,
research/shadow tooling, scripts, unrelated tests and the untracked `supabase/`
tree. The evidence inventory records each exact excluded path. None was deleted,
untracked or opened as municipal record content.

Tracked-path metadata found no tracked CSV/XLSX/XLSM/PDF/model/archive/private-key
files in the extensions checked; the tracked SQL files were reviewed migration
metadata paths. This is not certification of Git history, other extensions or
all source contents. Existing history/publication rights and any earlier sensitive
objects still require owner review before public publication. Ignoring files
does not remove historical objects. Do not stage the whole repository.

## Remaining deployment blockers

1. **New database contract:** migration 05 is prepared only. It creates private
   processing-run history, claim/history indexes, bounded column grants, owner RLS
   and a deferred revision trigger. Upload/reprocess paths require it and fail
   safely when absent; history reports its unavailable state. No new schema is
   needed for categorized display, URL MACs or login appearance.
2. **Authentication policy:** Batch 9B remains pending. Current `login()` calls
   `check_password_hash` without the proposed bounded verification policy and
   retains a successful non-scrypt/non-pbkdf2 credential-rewrite branch. Explicit
   initialization still contains legacy account/record repair behavior. Ordinary
   WSGI startup and `ensure()` do not call initialization. No plaintext fallback
   was added. Do not run initialization to resolve deployment problems.
3. **Login abuse controls and account continuity:** a shared, cross-instance login
   throttle is not established by the inspected source. Live administrator access,
   municipal hash compatibility, recovery and session-replacement behavior are not
   proven. Credential-field access, aggregate audit, recovery, Batch 9B application
   and production restart each retain their separate approval requirements.
4. **Live database permissions and transactions:** owner-confirmed 03/04 installation
   is accepted. It does not prove new history SQL, actor RLS, real row locks,
   simultaneous approvals, audit rollback or production pooling behavior.
5. **Hosted runtime and model delivery:** the new context has not been built or
   deployed here. Actual cold-cache private model retrieval, credential expiry,
   native OCR/FastText/RF execution, peak memory and concurrent extraction duration
   remain to be measured on the existing host. Do not repeat model approval or
   retraining as a deployment prerequisite.
6. **Hosting eligibility:** current Vercel documentation supports this container
   approach in beta; actual project entitlement/settings and sufficient capacity
   remain unverified. Hobby personal/noncommercial terms are not established as
   suitable for municipal operations.
7. **Operational production transition:** municipal use still requires the separately
   authorized security/recovery/cutover and rollback review. The earlier owner
   no-backup election for installed 03/04 is historical evidence, not permission
   to execute migration 05 or waive unrelated municipal production gates.

Private documents/review state currently persist through the existing PostgreSQL
document/workbook adapters. This release does not implement a new Supabase Storage
document bucket or Flask-to-Supabase-Auth bridge. Do not make documents public or
assume Storage RLS recognizes Flask sessions. A future object-storage design
requires separate authorization; it is not needed to use the current database
adapter and must not be claimed as completed integration.

## Exact owner next actions

### GitHub

After approving this exact source set, verify branch and staged scope, then stage
only the manifest paths:

```powershell
git branch --show-current
git diff --cached --name-only
git add --pathspec-from-file=deploy/production/integrated-upgrade-files.txt
git diff --cached --check
git diff --cached --stat
git diff --cached
```

Stop if the index contains unrelated changes. Review all newly added tests/source
and SQL before committing. Commit/push only with owner approval, then review the
PR to the intended release branch. Do not force-push or change projects/remotes.
The existing manual workflow must be present on the default branch for dispatch;
verify the selected release ref resolves to the reviewed commit. No Git mutation
above was executed by this session.

### Supabase

1. Keep installed 03/04 unchanged. Independently identify the existing approved
   project and target. Review recovery/cutover and **approve migration 05 separately**.
2. Review the whole `05_document_processing.sql` transaction and its role/session
   prerequisites. Use an owner-only execution copy; leave repository approval flags
   at `NO`. The actual target and recovery decision must satisfy the installer's
   guards. Do not silently reuse earlier risk acceptance. No reset, seed, repair,
   bootstrap, account conversion or migration runner belongs in this sequence.
3. Owner executes only the approved migration 05, then
   `verify_document_processing.sql`. All expected access/index/trigger booleans
   must pass; compare all three installed owner policies with the reviewed SQL.
   If the table already exists, inspect it rather than rerunning the installer.
4. Verify the new queries/RLS and repeated/concurrent processing with synthetic
   records in an explicitly approved test environment. Confirm backend access and
   browser API-role denial. Do not query municipal credentials for these checks.

### Vercel

1. Use the existing project and the manual `Existing Vercel production deployment`
   workflow. Keep automatic Git deployment disabled. Confirm Container/Fluid,
   `PORT=8080`, the existing protected session key and restricted database role.
2. Confirm secret settings privately: `PSMS_ENV=production`,
   `PSMS_PRODUCTION_DB_ENABLED=1`, `PSMS_DURABLE_WORKBOOKS_ENABLED=1`,
   `PSMS_INFERENCE_ENABLED=1`, `PSMS_NLP_RUNTIME=retrained_rf`,
   `PSMS_INFERENCE_RELEASE_ID=nlp-online-original-v2`,
   `PSMS_INFERENCE_CACHE_ENABLED=1`, `PSMS_DATA_DIR=/tmp/psms`,
   `PSMS_THREADS=2`, trusted hosts and `/app/certs/supabase-ca.crt`.
   Preserve existing scoped private model-part credentials and object pins; verify
   expiry without exposing tokens or signed URLs. Do not use a v1 proposal to
   replace the approved v2 release. Keep model/offline settings and native engines.
3. Confirm the exact Supabase endpoint/mode and `verify-full` TLS. For transaction
   pooling set `PSMS_DB_TRANSACTION_POOLER=1`; `db.py` disables prepared statements.
   For the reviewed session/direct mode use its matching setting. Keep actual
   DSN, session/model secrets and GitHub `VERCEL_*` secrets outside source/logs.
4. Resolve the applicable security/production gates and obtain explicit approval
   for the exact commit and target. Only after migration 05 and owner checks pass,
   dispatch these inputs on the reviewed ref:

```text
inference_mode=approved_release
nlp_runtime=retrained_rf
release_id=nlp-online-original-v2
deploy_approved=true
database_upgrades_ready=true
```

The workflow prepares the exact context and deploys it; it does not run SQL.
`database_upgrades_ready=true` now includes migration 05. A successful build alone
does not satisfy live feature acceptance.

After an authorized rollout, use synthetic documents and independent test sessions
to check HTTPS login/logout/CSRF/role changes, cross-user URL/source denial,
automatic PPMP segregation, history filters and downloads, original reprocessing,
AOQ item/supplier correspondence, manual approval, live updates with unsaved edits,
cold-instance OCR/RF, durable review across instances, concurrent approvals/stock
operations and audit rollback. Preserve new records during any compatible
application/configuration rollback; leave additive history schema intact.

Current provider references checked for this report:

- [Vercel Container Runtime](https://vercel.com/docs/functions/runtimes/container):
  OCI/Dockerfile support, beta availability, explicit HTTP port and instance scale-in.
- [Vercel Functions limits](https://vercel.com/docs/functions/limitations): current
  Hobby limits include 2 GB memory, 300 seconds and a 4.5 MB HTTP payload cap.
  The app uses its stricter 4,000,000-byte document and 4,400,000-byte request caps.
- [Vercel Hobby terms](https://vercel.com/docs/plans/hobby): personal,
  noncommercial use only.
- [Supabase PostgreSQL connection guidance](https://supabase.com/docs/guides/database/connecting-to-postgres):
  endpoint/mode selection, TLS and transaction-pooler prepared-statement limitations.

**Disposition:** local implementation and package finalized; GitHub publication,
migration 05 installation, live verification and Vercel deployment remain pending.
