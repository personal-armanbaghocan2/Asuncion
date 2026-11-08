---
name: asuncion-workflow-review
description: Review procurement workflow correctness in the Asuncion Supply & Property Management System, including online multi-user approvals and transaction races. Use for document transitions, permissions, totals, inventory updates, duplicate processing, or audit records. Do not use for OCR accuracy or unrelated visual styling.
---

# Asuncion Procurement Workflow Review

## Scope and preservation

Read AGENTS.md and relevant documentation, then verify against code. Review only unless a fix is requested. Preserve workflow definitions and completed batch protections when preparing the same application for online operation; hosting changes do not authorize changing approval rules. Keep PostgreSQL through db.py, human review, the approved dashboard/sidebar and existing role/status restrictions.

Use `../asuncion-ocr-review/SKILL.md` for extraction accuracy and `../asuncion-online-deployment/SKILL.md` for hosting/configuration readiness. This skill owns business invariants, permissions and transactional behavior.

## Review procedure

1. Identify the module, role, starting state, action and expected result. Trace relevant app.py routes/helpers, db.py operations, templates and actual Supabase schema/migrations; do not apply migrations while reviewing.
2. Follow implemented PPMP, APP, Purchase Request, AOQ, Purchase Order, NLP Verification, IAR/AIR, Inventory and RIS/ICS relationships; include Reports, Audit Trail and User Management when affected. Check PPMP/APP item/category/total consistency, identifiers, PR/AOQ/PO item/supplier links and downstream edit protections. Verify actual transitions rather than assuming a simple linear sequence.
3. Verify server-side role and per-record authorization on direct requests and browser actions, disabled-account restrictions, review confirmation and valid starting states. Re-read state inside the mutation transaction; never trust stale forms, cookie claims, hidden controls or posted quantities/prices/totals.
4. Check finite/positive quantities, monetary parsing/rounding, subtotals and authoritative totals against source records. Keep missing/invalid values distinct from zero. Verify NLP results/status remain linked to reviewed PR/PO data; preserve FastText inference and human verification, including explicit failure when the model is unavailable.
5. Inspect locks, transaction boundaries, stale-state guards and repeated approvals. Preserve SELECT ... FOR UPDATE and deterministic inventory lock ordering where required. Process-local flags/locks and read-then-write outside a transaction are not equivalent multi-instance protections.
6. Trace iar_approve(): locked approval/state checks, inventory additions, transaction-scoped numbering and log(..., connection=con) must commit together or all roll back. A second approval must not create duplicate stock or misleading success/audit entries.
7. Trace ris_ics_approve(): lock request and inventory consistently, validate aggregate demand against locked stock, enforce RIS/ICS rules, and commit deductions/status/audit together. Preserve concurrent overdraw prevention and all-or-nothing behavior for multiple items; retries/double-clicks/reversed item order must not bypass it.
8. Check PPMP/APP and other affected writes, identifiers and audit entries use the intended transaction/connection. Inspect exceptions, rollback, connection cleanup and failed audit writes; helpers must not commit part of a larger operation or log success after failure.
9. For Supabase Cloud/serverless, review explicit endpoint/pooling mode, bounded connections, psycopg prepared-statement compatibility, timeouts, TLS and transaction lifetime. Keep row locks on the same transaction through commit/rollback. Do not rely on cross-transaction session state, global shared connections, `_DATABASE_READY` or warm instances. After an uncertain response following commit, check state/idempotency before retrying a stock mutation.
10. Review independent sessions and concurrent browser actions: cross-user drafts/records, stale approvals after another user's edit, duplicate POSTs, CSRF and server validation. Preserve original permissions; online migration must not silently grant access or change roles.

## Verification

- Inspect test effects before execution. Use a dedicated isolated database, synthetic records and explicit endpoint checks; never fall back to municipal configuration. No reset, legacy seed import, account change or live query is implied by this skill.
- Test valid, invalid/unauthorized, repeated and interrupted transitions. Compare links, totals, stock and audit records before/after, including rollback on injected failures.
- For inventory changes inspect tests/test_iar_atomic_approval.py, tests/test_ris_ics_atomic_approval.py, tests/test_iar_postgres_concurrency.py and tests/test_ris_ics_postgres_concurrency.py. Real race checks require separate connections to approved isolated PostgreSQL; mocks alone do not prove locking behavior.
- Exercise simultaneous approval of one document, competing stock requests and multi-item requests in different item orders. Use independent browser sessions where relevant without collecting actual passwords. Preserve inactive-account and role denials.
- Do not start app.py or invoke init_db()/ensure() against municipal data as a read-only check: initialization can apply schema, rewrite credentials and repair records. Batch 9B remains pending; no authentication patch/audit is authorized here.
- If the database/dependencies/hosted runtime are unavailable, separate static findings from unverified behavior. For requested fixes keep changes focused and rerun affected checks.

## Report

Provide workflows/roles/code paths, expected versus actual behavior, evidence, requested changes, checks/results and limits. Distinguish Implemented, Tested, Proposed, Unsupported and Not yet verified. Never claim full workflow or hosted concurrency success from individual steps or local mocks alone.
