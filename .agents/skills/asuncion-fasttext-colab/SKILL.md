---
name: asuncion-fasttext-colab
description: Prepare or validate reproducible Google Colab guidance for Asuncion FastText with Linear SVM, Random Forest, and Logistic Regression. Use for the three-classifier experiment, feature/split checks, serialization validation, and one-cell-at-a-time Python guidance. Do not use for model activation or deployment.
---

# Asuncion FastText Colab

## Execution workflow

Follow **IMPLEMENT → TEST → VERIFY → REPORT** for clearly scoped, safe work authorized in the current conversation. Implement the requested result, run relevant checks, verify outputs and preserved inputs, then report actual changed paths, checks/results and unresolved blockers concisely.

- Reuse valid prior approval for the exact action and destination; do not stop at another proposal or ask for the same approval again. A read-only or proposal-only request still permits no writes.
- Required security approvals and filesystem execution permissions still apply. Stop only the affected operation if scope changes, a safety check fails or necessary authorization is missing.
- Never invent human-reviewed labels, reviewer provenance, domain-policy decisions or cloud-transfer authorization. Local preparation, fitting, cloud transfer and activation retain separate authorization boundaries.

## Scope and references

Read [AGENTS.md](../../../AGENTS.md), the [three-classifier report](../../../docs/reports/FASTTEXT_THREE_CLASSIFIER_RETRAINING_REPORT.md), and [authorization metadata](../../../training_data/stage11_general_pr_po_v0_7_3/authorization_and_lineage.json). Prepare guidance for the user to execute manually; invoking this skill does not authorize fitting, uploads, installation, deployment, or active-model replacement.

Use these maintained references instead of the older representation-comparison trainer or Stage 7 notebook:

- [train_compare.py](../../../experiments/fasttext_classifier_comparison_v0_1_0/train_compare.py) and [experiment README](../../../experiments/fasttext_classifier_comparison_v0_1_0/README.md): preprocessing, parameters, metrics, selection, serialization, and known limitations.
- [feature module](../../../model/fasttext_nlp/fasttext_feature_schema_v2.py): exact feature order; read without editing.
- [recorded configuration](../../../experiments/fasttext_classifier_comparison_v0_1_0/runs/20261024T041521840882Z/configuration.json): environment and artifact provenance. This locally ignored run may be absent elsewhere; report missing evidence instead of inventing it.
- [dataset review skill](../asuncion-fasttext-review/SKILL.md): pinned source, existing human-review export, integrity and label-review checks when relevant.
- [Flask runtime](../../../fasttext_nlp_runtime.py): compatibility inspection only.

For this skill, implementation means the requested local preparation or manual Colab guidance. User-executed fitting remains one cell at a time under the existing workflow; authorization for preparation does not authorize training, uploads or model activation.

## Experimental contract

- Use Gensim FastText trained from scratch on training text only. Preserve `vector_size=100`, skip-gram, window 5, 15 epochs, `min_count=1`, 20,000 buckets, subword lengths 3-6, negative sampling 5, and one worker. Preserve seeds **42, 123, 2026**, deterministic initialization hash, and the runner's portable frozen-inference serialization handling.
- Keep `FASTTEXT_NUMERIC_RELATION_V2`: ordered `[PR mean, PO mean, PO-PR, abs(PR-PO), PR*PO, numeric]`, with **59 numeric features**, **559 total float32 features**, and exact existing tokenization. Do not add preprocessing, pretrained vectors, external embedding APIs, or gold relationship annotations as predictors.
- Fit separate four-class status and nine-class primary-issue heads on the same 559 features. Preserve declared label order and targets. Keep IDs, partitions, provenance, proposed relationships, and reviewed labels out of predictors.
- Match `SETTINGS` and `make_classifier`: LR `C=0.1/1/10`; LinearSVC `C=0.01/0.1/1`, `tol=0.001`, `max_iter=5000`, `dual='auto'`; RF 200 trees and `min_samples_leaf=1/2`. All use balanced class weights and the experimental seed. LR uses `max_iter=5000`; RF uses `n_jobs=1`. Fit LR/SVM scalers on training rows only; RF remains unscaled. Preserve version-dependent defaults and one-thread numerical limits from the runner.

## Workflow

1. Confirm the requested stage and exact dataset release. Verify the pinned hash and 1,228 records using the review reference. Preserve `train=1096`, `dev_unseen_name=66`, `dev_unseen_family=66`; do not randomly repartition or combine historical releases. Reject duplicate IDs and cross-partition groups, normalized whole-side texts, and pairs. Inspect available document-family/counterfactual ancestry; report missing lineage and semantic independence limits. One connected training group and shared templates prevent ordinary group-fold independence claims. Never inspect protected final tests or municipal workbooks to fill gaps.
2. Separate human verification, local fitting, privacy clearance, cloud transfer, and activation permissions. Metadata currently records no privacy/cloud-transfer approval; later local experimental fitting did not grant either. Require explicit authorization for the exact data and cloud destination before recommending uploads, Drive mounting, or upload cells. Do not send records to external services or put confidential text in prompts, code examples, logs, or shared artifacts. While transfer is pending, provide local preflight guidance only.
3. Inspect actual human-review fields when using a reviewed release; do not infer completion from a filename or prior export check. All original targets remain `AI_PROPOSED_UNVERIFIED`; the existing CSV initially has no reviewed labels. Require an explicit target policy for an AI-label exploratory run. A human-verified run needs documented human decisions and provenance. Never silently mix target sources, approve procurement decisions, or resolve P01-P06. A reviewed-label release needs separate version/hash/target handling; the original loader accepts only the pinned AI-proposed release.
4. When Colab Python guidance is requested, provide **one code cell at a time**. Explain its purpose, expected aggregate output, and stop checks; wait for the user's result before the next cell. Do not create an `.ipynb` unless explicitly requested. Do not execute the user's retraining yourself. Adapt local path/preservation checks explicitly; do not run the repository trainer blindly in Colab.
5. Record Python and package versions before fitting. Recorded local baseline: Python 3.11.9; NumPy 2.4.6; SciPy 1.17.1; Gensim 4.4.0; scikit-learn 1.8.0; joblib 1.5.3; threadpoolctl 3.6.0. Verify Colab availability and compatibility rather than claiming these pins already work there. No Flask, Supabase, OCR engine, transformer model, or GPU is required for this comparison. Stop on convergence warnings, nonfinite features, or unexpected dimensions.
6. Track per-head macro-F1, per-class precision/recall/F1/support, confusion matrices, accuracy, joint accuracy, and false-Acceptable counts/rates. The rate denominator is reference non-Acceptable cases; repeated seeds are not distinct cases. Preserve equal weighting of heads/panels and mean across seeds; ties use fewer false Acceptable predictions, alphabetical family, then declared setting order. Keep seed 42 as representative, not the best-scoring seed. Report seed SD as descriptive. Development selection against AI proposals is not independent test accuracy or evidence of municipal reliability.
7. Save each experiment in a unique directory outside `model/fasttext_nlp`; never overwrite active files. Retain all seeds/settings, Gensim model and sidecars, both classifier pipelines, feature-module hash/source, label order, dataset hash/partition IDs, dependency versions, configuration, metrics, and prediction evidence without document text. Reload trusted artifacts in a fresh process and reproduce predictions/metrics. Inspect helpers before use: `verify_run.py` writes shared `verification.json`; `report_run.py` rewrites historical outputs. Do not overwrite those automatically. Previous 131-file/6,336-prediction verification is historical evidence, not validation of a new Colab run.

## Deployment boundary and stop checks

These independent 559-feature heads are experimental artifacts. The installed Flask bundle uses 120-dimensional embeddings, issue input 659, and status input 668 after appending nine issue probabilities. Runtime dimensions derive from embedding size, but stacking, integer class IDs, ordered probabilities, and exact metadata remain mandatory. LinearSVC has no `predict_proba`; do not rename classifiers or falsify manifests to claim compatibility. Calibration, stacking, integration, or activation needs separate review and authorization.

Stop dependent steps on failed integrity/leakage/feature checks, unresolved target policy, missing cloud-transfer authorization, unsafe serialization, or attempted active-model writes. Do not silently repair labels, splits, features, or checkpoints. Report the limitation and continue only safe preparation.

- Verify authorization scope, target provenance, exact source hash/partitions, feature order/dimensions, and training-only fitting.
- Verify environment, all-seed evaluation/selection, trusted reload parity, sidecars, and unchanged active assets.
- Distinguish Implemented, Tested with environment/scope, Proposed, and Not yet verified. Stop after the requested preparation/validation stage; do not proceed to training or deployment automatically.
