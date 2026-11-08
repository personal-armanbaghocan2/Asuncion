---
name: asuncion-fasttext-review
description: Inspect Asuncion FastText datasets and human-review CSVs locally. Use for dataset integrity, label provenance, duplicates, class distributions, privacy checks, or review-export creation and verification. Do not use for model fitting or deployment.
---

# Asuncion FastText Review

## Execution workflow

Follow **IMPLEMENT → TEST → VERIFY → REPORT** for clearly scoped, safe work authorized in the current conversation. Implement the requested result, run relevant checks, verify outputs and preserved inputs, then report actual changed paths, checks/results and unresolved blockers concisely.

- Reuse valid prior approval for the exact action and destination; do not stop at another proposal or ask for the same approval again. A read-only or proposal-only request still permits no writes.
- Required security approvals and filesystem execution permissions still apply. Stop only the affected operation if scope changes, a safety check fails or necessary authorization is missing.
- Never invent human-reviewed labels, reviewer provenance, domain-policy decisions or cloud-transfer authorization. Local preparation, fitting, cloud transfer and activation retain separate authorization boundaries.

## Scope and references

Read [AGENTS.md](../../../AGENTS.md) first. Work locally; inspection does not authorize exports, training, cloud access, or model changes. Repository paths below are root-relative.

- Read the [three-classifier report](../../../docs/reports/FASTTEXT_THREE_CLASSIFIER_RETRAINING_REPORT.md) and [authorization metadata](../../../training_data/stage11_general_pr_po_v0_7_3/authorization_and_lineage.json).
- Use [train_compare.py](../../../experiments/fasttext_classifier_comparison_v0_1_0/train_compare.py) for partition, label, and leakage contracts. Read source without executing training.
- Consult the [label policy](../../../training_data/stage11_general_pr_po_v0_7_0/SCHEMA_AND_LABEL_POLICY.md) only when reviewing label meaning. P01-P06 remain unresolved; do not silently resolve them.

## Pinned source and existing export

Source: `training_data/stage11_general_pr_po_v0_7_3/training_validation_STRUCTURED_v0_7_3.jsonl`.

Expected SHA-256: `94ca855784ad38b51f074fd6233aabc8c87bd5488f4e236f1566fe3ec8712b57`.

Expected records: **1,228**; partitions: `train=1096`, `dev_unseen_name=66`, `dev_unseen_family=66`.

Existing review CSV: `C:\Users\User\Documents\FastText_Human_Review_STAGE11_v0_7_3_LOCAL_ONLY_20261024T065448Z.csv`.

That export initially passed integrity checks with 1,228 `UNREVIEWED` records and empty human-review fields. Inspect and reuse it when appropriate. Revalidate its current contents; preserve later human work. Do not regenerate it merely because this skill was invoked. Report a missing export without silently replacing it.

## Workflow

1. Verify the source bytes against the pinned hash, parse UTF-8 JSONL, and check record counts, unique `sample_id` values, partitions, and allowed labels. Report status/issue distributions overall and by partition, missing values, and exact/normalized duplicate pairs. Preserve source bytes, identifiers, text, proposed labels, partitions, and row order when exporting.
2. Keep `AI_PROPOSED_UNVERIFIED` distinct from human decisions. Thirty supplemental case reviews do not verify the entire corpus. Never generate, guess, prefill, or approve human-review labels. Record only explicit human decisions with their supplied role/date/provenance; never invent missing review metadata or infer approval from populated cells.
3. For an existing CSV, match records by `sample_id` and compare all seven preserved source fields exactly. Check completeness, duplicate IDs, blank versus populated review fields, review-state consistency, and label validity. Report incomplete or unsupported review states; do not reset reviewed records or relabel source data.
4. Create a new export only when requested. Before writing outside the repository, summarize source, exact destination, files, and checks; use explicit approval already supplied for that exact operation, or obtain approval if it is missing. Reverify safety and proceed when approved; do not request the same approval again. Use a verified local directory outside Git. Windows Known Documents points into OneDrive on this machine; verify the actual destination and avoid cloud-sync roots, junction redirection, and external services unless separately authorized. Never transmit confidential procurement records.
5. Use the exact schema below. New exports leave all five human-input fields empty and set every `review_status` to `UNREVIEWED`. Use exclusive file creation; never overwrite an existing export. Encode UTF-8 with BOM and use a CSV library with quoted cells and correct embedded-newline handling.
6. Check every cell for formula prefixes `=`, `+`, `-`, or `@`, including after leading whitespace/control characters and Unicode normalization; check unsafe controls and spreadsheet size limits. Quoting alone does not prevent formula injection. Stop on unsafe preserved text rather than silently changing it. Reopen the CSV, compare preserved fields exactly, verify blank review fields/status/counts, and rehash the source. Print aggregate checks and paths only, never procurement text.

```text
sample_id,group_id,assigned_partition,pr_text,po_text,proposed_status,proposed_primary_issue,reviewed_status,reviewed_primary_issue,reviewer_role,review_date,review_notes,review_status
```

## Approved local-only labeling pilots

Use the verified existing review CSV and the approved training IDs. Keep the exact canonical source schema in a coordinator-only subset, preserving original IDs, labels and source order. Blind reviewer files use opaque case IDs and physically omit AI labels and identifying suffixes; keep the mapping separate. Human decisions, roles and dates start empty. Preserve unresolved P01-P06 gates, independent first reviews and adjudication evidence. Create only approved files exclusively; reopen them and verify counts, schemas, evidence, blank decisions, policy gates, safety and unchanged originals. Reuse an existing pilot only after revalidation; creation approval never permits overwriting completed reviews or training.

## Stop conditions and verification

Stop the affected operation on hash/count/schema mismatch, duplicate IDs, unknown labels/partitions, unsafe cells, an existing output path, or missing write/transfer authorization. Continue useful read-only checks while an authorization question is pending. Do not start Flask, query municipal data, run training, alter authorization metadata, or modify active models.

- Verify source hash/counts; report duplicates and proposed/reviewed distributions separately.
- Verify preserved fields, current review provenance, spreadsheet safety, and exclusive output creation.
- Report the requested paths, counts, checks, and privacy/errors. Distinguish checks performed now from historical evidence. Local export approval never authorizes cloud transfer.
