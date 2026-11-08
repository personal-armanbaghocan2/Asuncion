---
name: asuncion-ocr-review
description: Review or troubleshoot document extraction and OCR field mapping in the Asuncion Supply & Property Management System, including online upload and processing failures. Use for incorrect text, missing rows, mismatched procurement fields, or OCR regressions. Do not use for unrelated UI changes or general deployment reviews.
---

# Asuncion OCR Review

## Scope and preservation

Read AGENTS.md first and verify documentation against code. Review extraction accuracy and its upload/review path; make fixes only when requested. Preserve Tesseract, native extraction, semantic mapping, FastText interaction where relevant, human approval and completed OCR improvements, particularly Batch 8. Online hosting is the target; Vercel execution and Supabase Storage integration remain subject to verification.

Use `../asuncion-online-deployment/SKILL.md` for overall hosting readiness and `../asuncion-workflow-review/SKILL.md` for approval/inventory semantics. Keep this review focused on the affected document and processing path.

## Review procedure

1. Identify the document type, reported issue, expected output and authorized source sample. Trace upload, processing, mapping, draft, form, save and human-review steps through relevant app.py routes, aoq_flexible.py and offline_ocr_mapper.py.
2. Prefer existing native extraction for digital PDF, Word/DOCX, Excel/XLSX/XLSM, CSV and TXT; retain Tesseract for scanned PDFs/images. Verify actual SUPPORTED_EXTENSIONS/parser behavior before promising a format, including legacy Word/Excel formats. Preserve preprocessing, confidence/warnings, multipage handling and row reconstruction.
3. Compare source and extracted identifiers, descriptions, quantities, units, supplier columns, prices, decimal points and totals. Check AOQ row/column alignment, duplicate/lost rows, missing fields, winning-offer evidence and item/supplier correspondence. Preserve aoq_flexible.py review_number(), reviewed_total(), winning_offer_error() and related numeric/review safeguards when changing callers.
4. Flag missing, ambiguous or uncertain values; never invent replacements, hide uncertainty with zeros or turn OCR/NLP output into approval. Check that reviewed corrections survive saving/reopening drafts without breaking PPMP/APP links or AOQ transfer guards.
5. Inspect online upload authorization, actual type/signature and extension, size/page/pixel/row/decompression limits, parser timeouts and failure handling. secure_filename alone is insufficient. Reject traversal/overwrite and content execution; keep documents out of public/static files, logs and error traces.
6. Verify target Linux Tesseract executable, traineddata, native libraries and TESSERACT_CMD rather than assuming Windows paths work. Check current Vercel executable support, bundle size, memory, duration, cold starts and simultaneous OCR capacity with realistic synthetic scans. Importing pytesseract does not prove executable availability.
7. Review unique temporary request/job/owner paths, bounded retention and cleanup in finally paths, including exceptions/cancellation. Never require temporary files or a warm-process cache to survive another request/instance. Bound concurrency without presenting process-local locks as distributed guarantees.
8. Verify durable private storage and per-document authorization for uploads, previews and downloads. Existing AOQ_UPLOAD_DIR, PPMP_OCR_STAGE_DIR, save_ppmp_ocr_state() and save_aoq_preview() use local files; inspect callers before proposing Storage changes. Check short-lived authorized links, secure downloads and cross-user document/draft denial. Existing Flask accounts do not automatically satisfy Supabase JWT policies; service-key access still needs server authorization.
9. Check storage/database failures and duplicate jobs so retries cannot overwrite another user's document or publish an unreviewed draft. A database transaction does not atomically include a Storage upload. Keep sensitive text/full OCR payloads out of cookies, logs and monitoring; reference authorized durable state instead.

If Vercel cannot execute Tesseract reliably, report the evidenced limitation and propose a container-capable Flask host or authenticated OCR worker retaining Tesseract, mapping accuracy and the same website/review flow. Do not remove Tesseract or automatically introduce Google Cloud Vision or another OCR API. Never send municipal documents to a third party without explicit authorization.

## Verification

- Inspect relevant tests before execution. Use sample_files/ only after confirming the selected file is synthetic or authorized; prefer generated synthetic documents. A packaged municipal-looking sample is not automatically cleared for cloud upload.
- Choose focused checks such as tests/OCR_FILETYPE_REGRESSION_TEST.py, tests/OCR_PPMP_APP_REGRESSION_TEST.py, tests/test_aoq_numerical_validation.py and related missing-field/row tests after inspecting their effects. No installation or database initialization is implied.
- Compare extraction with the actual source. For changed paths test native/scanned formats, malformed/oversized input, parallel requests, timeout/cleanup, cross-user denial and persistence across instances. Use isolated synthetic environments for mutations; no live document/account access without approval.
- If the sample, dependency, executable, model or hosted runtime is unavailable, report what remains unverified. A local extraction pass does not prove hosted compatibility. For requested fixes keep changes focused and rerun affected checks.

## Report

Provide document/code paths, expected versus actual fields, supporting evidence, requested changes, checks/results and remaining limits. Label capabilities Implemented, Tested with environment, Proposed, Unsupported with evidence, or Not yet verified. Never claim extraction is correct without source comparison or cloud processing works from a local-only test.


## Multi-item extraction and review contract

Apply fixes to the normal operational application, not a synthetic-only parser, route, template, account or environment branch. Trace `app.py` through `aoq_flexible.py`, `procurement_document_layout.py`, `offline_ocr_mapper.py`, `procurement_item_contract.py`, `durable_ocr_v0_1_0.py`, `templates/ppmp_type.html`, and `static/js/ppmp-ocr-review.js`. Verify the current persistence implementation; older file-state helpers are not evidence of the active path.

- One distinct source item must produce one structured draft and one visible table row. Preserve stable draft codes, original item numbers, ownership, source identifiers, page/worksheet/physical row, captured coordinates, confidence, warnings and review status. Do not silently discard uncertain or duplicate-looking rows.
- Recognize headers separately from values. Prefer exact typed column roles; do not let semantic similarity turn an item description, fiscal year, footer or neighboring row into a header or monetary field. Keep all twelve official PPMP fields plus Item Name, Quantity, Unit and Estimated Unit Cost. Shared procurement metadata must not replace item-specific descriptions, quantities or budgets.
- Keep quantity, unit, unit price and amount distinct. Use finite Decimal parsing, preserve missing values, and warn on quantity-times-price disagreement without replacing source values. Unit is not Unit Price. Do not infer an absent unit price from a budget.
- Prefer native searchable-PDF cells or positioned words; use local Tesseract word coordinates or physical grid cells for scanned tables when appropriate. Preserve page boundaries, wrapped descriptions and repeated headers. Do not flatten multiple item rows before field mapping. Unsupported or unreadable layouts require manual review, never fabricated values or high mapping confidence.
- Use native Excel cells across all worksheets. Preserve merged-header evidence, repeated headings, separators, physical row numbers, cached formula values and missing-cache warnings. Never evaluate formulas using an external service or invent missing results. Subtotals and grand totals are not procurement items.
- For AOQ, preserve supplier-to-item and quotation relationships: two items with three suppliers are two item groups and six quotations. Retain explicit award evidence, missing/ambiguous suppliers, specifications, quantities, prices, amounts and remarks. Never choose a winner from the lowest price. Keep existing PR, PO and NLP transfer/review guards.
- Preserve original OCR text and source bytes separately from parsed fields and human corrections. Readable display may group repeated source headings only in a separate representation; never deduplicate item descriptions or overwrite original evidence. Show Extracted Item Data, Selected Item Source Text and Full Raw OCR Text. Display only captured source page, row, headers, confidence and coordinates; do not fabricate evidence.
- Preserve single-item Review. Checkbox-based Review Selected must show only selected drafts using dynamic Item 1 / Item 2 / Item 3 tabs. Verify keyboard navigation, active-tab styling, horizontal scrolling, mobile layout and predictable selection across filters/pages. Switching tabs must preserve every item's corrections, validation messages and review state.
- Validate each item server-side. Batch saves must preserve ownership, active-session authority, CSRF, revisions, deterministic locking, transactional audit logs and protection of converted/cancelled drafts. Prefer an atomic PostgreSQL transaction; otherwise return truthful per-item outcomes. Reject stale/conflicting submissions; handle duplicate IDs safely. Upload retries must not create duplicate drafts or overwrite reviewed corrections. OCR review never grants procurement approval.

### Required regression and evidence

Generate a fictional scanned PDF containing A4 Copy Paper (5 Ream, 245.50, 1227.50) and USB Flash Drive (3 Piece, 385.00, 1155.00). Manually specify the source and expected values before extraction. Require TWO separate records, correct item identity and numbers, and numeric budgets. Never accept one Unmapped description, USB Flash Drive in Quantity / Size, A4 Copy Paper in Estimated Budget, or missing quantities when source values are recognizable. Preserve the single-item Black Printer Ink XLSX case (2 Bottle, 685.75, 1371.50).

Inspect and run `tests/run_multi_item_ocr.py`, appropriate existing OCR/AOQ gates, and `tests/multi_item_review_browser.cjs` when an installed browser can launch. Include one/two/ten items, multipage scans, searchable PDF, native multi-sheet Excel, wrapping, repeated headings/watermarks, totals, uncertain cells, missing formula caches, AOQ one/two items with three suppliers, batch validation, ownership, CSRF, audit rollback, stale revisions and duplicate retries. Inspect every test's effects first; use an explicitly isolated disposable PostgreSQL target for real persistence/concurrency proof. Never test destructively against operational procurement data or initialize the municipal database.

Do not claim multi-item success from OCR text recognition alone. Verify extracted count, exact field values against independent ground truth, real review UI behavior, and persistence of EVERY item after reopening. Report item precision/recall, missing/false rows, field-level accuracy and denominator per source. Distinguish executed local Tesseract/native tests, route/SQL doubles, DOM doubles, real browser checks and real PostgreSQL checks. Blocked browser/database checks remain unverified, even when mocked tests pass. Synthetic results do not establish accuracy on real documents. Preserve installed NLP artifacts, the 559-feature contract, authentication, official approvals and unrelated work.

