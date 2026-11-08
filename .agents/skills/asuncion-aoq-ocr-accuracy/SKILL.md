---
name: asuncion-aoq-ocr-accuracy
description: Implement and verify source-backed AOQ extraction accuracy for the Asuncion system using synthetic ground truth, native extraction, and offline Tesseract. Use for supplier-column, item-row, numeric, layout, or OCR regressions; not model training or procurement approvals.
---

# Asuncion AOQ OCR Accuracy

## Scope and execution

Read AGENTS.md, ../asuncion-ocr-review/SKILL.md and ../asuncion-workflow-review/SKILL.md. Inspect current source before trusting historical reports. Follow INSPECT -> REPRODUCE -> IMPLEMENT -> TEST -> VERIFY -> REPORT. Implement safe, clearly authorized fixes; do not repeatedly stop at proposals. Read-only requests still permit no writes. Preserve unrelated changes.

Use generated synthetic documents unless specific source fixtures are expressly authorized. Do not read municipal records, credentials, private documents, training corpora, or local-only labeling worksheets. Repository location or a sample_files name does not prove a fixture is synthetic. Inspect test bodies, setup, helpers, imports and subprocess effects before running them. Exclude unauthorized fixtures; do not silently replace ground truth with inferred values.

## Trace the actual path

Inspect app.py AOQ routes, durable_ocr_v0_1_0.py, private_document_uploads.py, aoq_flexible.py, offline_ocr_mapper.py where used, templates/aoq.html, and relevant tests. Establish upload validation, native extraction, offline fallback, table/header recognition, row construction, mapping, warnings, owner-bound review, correction, confirmation, saving and printing. Verify supported extensions in code; legacy DOC/XLS are not supported merely because DOCX/XLSX are.

Prefer reliable native tables/text for digital PDF, DOCX, XLSX/XLSM, CSV and TXT. Use local Tesseract for supported image/scanned inputs. Never introduce cloud OCR or runtime model downloads. Check executable, language data, pixel/page/archive limits, timeouts and finally-cleanup. Use unique local temporary paths. Do not start Flask or connect to a database merely to test extraction.

## Source and table ownership

- Preserve raw source text, page/frame, physical row/column, bounding boxes and confidence where available. Distinguish transformed-image coordinates from original-page coordinates.
- Separate item numbers from item descriptions. Bind each quotation to its source item row and supplier column. Never fill a missing cell by shifting a later value.
- Recognize repeated headers, merged cells, wrapped text, continuation rows and multipage boundaries. Do not propagate supplier identity through an ambiguous merged/missing header.
- Mark supplier attribution explicit, inherited or uncertain. Preserve legacy grouping needed by award-evidence checks; a grouping hint is not verified supplier identity.
- Detect duplicate, omitted, unexpected and ambiguous rows. Retain evidence rather than deleting duplicates or guessing an item match.
- If borders or OCR words cross cells, withhold affected numeric values and retain raw evidence. Confidence is engine evidence, not calibrated correctness probability.
- Keep unreadable, missing and contradictory values unknown. Do not invent quantities, prices, suppliers, totals or unreadable text. Blank is not zero.

## Numerical evidence and human review

Use finite decimal parsing with validated thousands separators. Preserve quantity, unit, unit price, amount, precision and original strings. Keep extracted totals separate from calculated quantity-times-price and supplier sums. Check each independently; flag disagreements without overwriting evidence. Do not impute a source price from a total or vice versa.

Retain uncertain cells and source evidence for manual correction. Preserve review_number(), reviewed_total(), offer_supplier_error(), winning_offer_error(), winning_total_error() and caller contracts. Preserve manual edits, ownership/revision checks, role guards, audit transactions and explicit confirmation. Never approve an AOQ, select a winner from OCR, or decide procurement policy.

## Reproduction and tests

For each confirmed defect:
1. Generate a minimal synthetic input with independently specified expected rows/fields.
2. Add and execute a regression test against the existing implementation; record expected versus actual and root cause.
3. Apply the smallest safe correction. Put risky OCR/layout changes in a separate versioned candidate.
4. Rerun the failing test, relevant existing native/OCR tests and workflow regressions.
5. Verify protected source, active engines/models, schema, roles and approval rules remain unchanged.

Use tests/run_fasttext_aoq_phase1.py for its guarded baseline after inspecting it. Guarded runners must deny network, real database/application startup and private fixtures; do not use unrestricted unittest discovery over this repository. Synthetic application tests need explicit database/network denial and synthetic environment configuration.

Cover actual supported formats with installed dependencies. Include multiple suppliers/items/pages, repeated/reordered/missing/merged headers, blank prices, wrapped/continuation text, misalignment, duplicates, decimal/thousands values, totals, rotated/degraded scans and unreadable cells. Exercise installed local Tesseract when available. Missing executables, unsupported layouts and untested formats must be reported.

## Accuracy and activation gates

Report SYNTHETIC TEST ACCURACY: document count, expected and fully correct supplier-item rows, missing/unexpected/duplicate rows, incorrect supplier-price assignments, quantity/unit/price/amount exact matches, incorrect totals and unresolved cells. Count blank expected values separately from recovered populated prices. Row identity and denominator must be fixed before extraction. A retained but uncertain value is not verified ground truth.

Historical Phase 1 numbers are not current evidence. Reproduce the baseline before claiming improvement. Do not claim real-world OCR accuracy without authorized, independently validated real documents. Do not claim classifier accuracy from extraction tests.

Keep experimental candidates inactive unless separate integration is explicitly authorized and workflow regressions support it. Do not import candidates into app.py, durable_ocr_v0_1_0.py, aoq_flexible.py or the live FastText path as an accuracy experiment. No training, model activation, uploads, database changes, policy decisions, commits or deployment are implied.

## Report

List actual changed paths, reproductions, executed commands/results, synthetic denominators, uncertainty and limits. Distinguish IMPLEMENTED, TESTED, ISOLATED, PREPARED, BLOCKED and NOT DEPLOYED. Preserve unresolved cases for authorized humans. Never claim perfect general OCR accuracy or deployment success from local tests.
