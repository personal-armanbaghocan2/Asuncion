---
name: asuncion-document-structure-ocr
description: Fix Asuncion procurement document structure, false item rows, PPMP field ownership, and multi-item review regressions using authorized source evidence. Use for PPMP/AOQ extraction and review; not model training, deployment, or procurement approval.
---

# Asuncion Document Structure OCR

Read project AGENTS.md and the existing OCR, AOQ accuracy, and workflow review skills. Preserve their unrelated requirements. Caveman governs chat brevity; keep code, evidence, tests, and persisted reports complete. The installed Caveman SessionStart hook controls voice only. This skill needs no hook registration or global configuration change.

## Source and reproduction

Locate the actual authorized failing source before claiming a fix. Request its exact local path if unavailable. Record filename, SHA-256, page/sheet count, native-text availability, and independently specified expected values. Do not search or read unrelated municipal documents. Generated fixtures test mechanisms; they never establish accuracy on an unavailable upload.

Mandatory historical regression: `PPMP_Complete_OCR_Test_2_Items(1).pdf`, a one-page scanned synthetic NON-DBM PPMP, fiscal year 2027, office IT SERVICES (TEST). Expect exactly two items: A4 Copy Paper (5 Ream, 245.50, 1227.50), USB Flash Drive (3 Piece, 385.00, 1155.00); total 2382.50. Preserve complete source descriptions/specifications. Verify all eight shared fields against the original source. Headings and metadata previously produced eight drafts. Require two items, zero false items, and zero missing items. An unavailable source is a blocker, even if a similarly named generated file passes.

Trace normal routes, not a copied/test-only application: upload, `aoq_flexible.extract_document`, `procurement_document_layout`, `offline_ocr_mapper`, `procurement_item_contract`, `durable_ocr_v0_1_0`, normal PPMP/AOQ templates, review JavaScript, and PostgreSQL persistence. Inspect intermediate words, coordinates, cells, header roles, physical rows, mapped fields, drafts, and rendered values. Distinguish recognition, structure, mapping, persistence, and display errors.

## Structure and field ownership

- Identify file type and document regions before mapping. Prefer native PDF tables/positioned words and native Excel cells. Preserve Tesseract and installed offline semantic/NLP engines. Never flatten columns before reconstructing cells and rows.
- Separate document context, item tables/headers, actual item rows, shared metadata, supporting documents, totals, signatures, footers, and uncertain regions. A description alone cannot establish an item. Require independent table/row evidence: description plus source numbering, quantity, or monetary cells. Numbering is optional when other evidence establishes a row.
- Retain uncertain candidates and raw evidence separately; do not create an empty/falsely confident draft. Do not suppress arbitrary phrases, deduplicate legitimate descriptions, invent missing values, or choose the lowest AOQ quotation as winner.
- Preserve all twelve PPMP fields plus Item Name, Quantity, Unit, and Estimated Unit Cost. Narrative Quantity/Size must not replace numeric Quantity. Document category must not become Type of Project. Apply shared metadata to each item without replacing item-specific values or silently resolving conflicting shared values.
- Use finite Decimal validation. Preserve quantity, unit, source unit cost, and source amount independently. Warn when quantity times cost disagrees with amount; never repair the source silently. Preserve explicit zero evidence for human validation.
- Retain page/sheet, physical row, source cells, captured bounds, coordinate space, OCR confidence, warnings, original text, and original bytes. Transformed-image coordinates are not original-page coordinates.

## Review and saving

Show one actual source item per table row, including quantity, unit, unit cost, amount, original number, and stable draft code. Keep metadata and uncertain candidates separate. Preserve single-item review and dynamic tabs for the selected checkboxes. Verify independent corrections survive tab navigation, failed saves, successful saving, reload, and reopening. Present structured values, selected-item evidence, document metadata, and complete original OCR text separately with safe text escaping.

Test DBM/NON-DBM isolation, owner/source filters, upload retry behavior, and parser-version re-extraction. Preserve existing associations and reviews; never silently move, cancel, or delete old drafts. Retain authentication, CSRF, revisions, deterministic locking, atomic batch validation, transactional audit records, and separate official confirmation/approval.

## Evidence gates

Inspect effects before running tests. Use guarded structure/multi-item/AOQ runners and the owned disposable PostgreSQL harness. Cover single/two/five/ten items, native/scanned/multipage PDFs, visible/borderless tables, wrapped/repeated headers, multiline descriptions, missing cells, totals, footers/watermarks, Excel sheets/formula caches, and AOQ two-item/three-supplier relationships (two item groups, six quotations).

Protect the Black Printer Ink single-item Excel regression (2 Bottle, 685.75, 1371.50). Report precision, recall, false/missing items, exact field matches, mapping errors, populated versus absent fields, and document/item errors per source. Distinguish real Tesseract, SQL doubles, DOM doubles, real browser, and real PostgreSQL evidence. Never claim browser or database verification from mocks. Never initialize/reset operational data, change credentials/models, or deploy to establish OCR correctness.
