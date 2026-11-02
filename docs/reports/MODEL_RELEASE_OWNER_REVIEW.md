# Original model release: owner review and final package

Decision status: **NOT APPROVED**. Original models are ready for a bounded owner
review, but **not ready for final release approval today**. A complete concurrent
Linux process-exit result and an explicit quality/deployment decision are missing.
No original model, approval, production registry, database or deployed site was
changed by this preparation. No Linux/Docker/model campaign was run locally.

## Existing verified evidence reused

The latest isolated Linux run is
`output/linux_validation/20261101T103144Z-c4df3caaef6c4714aafa3af9cd16e0ae/`.
Its aggregate result is **8/9 passed**, not 9/9. All four individual engines
actually loaded/executed on CPython 3.13.16, Debian Bookworm x86_64 and CPU Torch
2.14.1+cpu. The candidate uses the same 28 artifact size/hash records, runtime
expectations and dependency lock. This task reuses those results without rerunning
their completed phases.

| Saved phase | Verified behavior | Existing total phase seconds |
| --- | --- | ---: |
| Native Tesseract | Real synthetic OCR, English data, Tesseract 5.3.0 | 3.912 |
| Legacy FastText + Logistic Regression | Original classifiers; 659/668 features; frozen execution parity | 29.904 |
| FastText + RF setting 7 / seed 42 | Original RF heads; 559 features; reload/prediction parity | 29.525 |
| MiniLM | Local CPU load, 2x384 embeddings, 12x384 schema, four mapping checks | 80.673 |

Dependencies, document formats, document structure and negative/security phases
also passed. Saved preservation checks report original models/release unchanged.
No cloud database was contacted by those model tests.

The concurrent child's `concurrent.json` says passed (two workers, 166.532 phase
seconds), but the authoritative parent summary records `LINUX_PHASE_TIMEOUT`
after 180.498 seconds with a 180-second deadline. Writing a success JSON does
not prove process exit. **Do not use that child file to mark Linux verified.**
The former container-wide peak, 1,253,093,376 bytes under a 4 GiB allowance, is
not proof of a 2 GB Vercel serving profile.

The five local v1 transport parts have already matched the proposal hashes.
Remote bytes remain unverified here. Scoped signed-download implementation checks
passed 8/8 using mocked HTTP; this is not remote Storage verification.

## Quality evidence and limitations

- The pinned legacy `training_manifest.json` reports internal quality thresholds
  passed on 357 test rows: status macro-F1 0.9978336, issue macro-F1 0.9898766,
  false-Acceptable rate 0.0046729. These are saved training/evaluation results,
  not a new independent municipal accuracy assessment or owner approval.
- RF's pinned `comparison.json` reports pooled development selection agreement:
  status accuracy 0.7256098, issue accuracy 0.7445799, joint accuracy 0.5853659;
  seed-42 false-Acceptable count 15/rate 0.0506757. Its labels are explicitly
  `AI_PROPOSED_UNVERIFIED`; validation was previously inspected for selection,
  lineage/template limitations remain, and no human-reviewed independent final
  test is established. `independent_accuracy_claim_allowed=false` and
  `production_promotion_allowed=false` remain unchanged in original artifacts.
- Saved synthetic parity includes `quantity 10 reams` versus `quantity 10 boxes`
  returning Acceptable from both classifiers. Compatibility preserves that
  behavior; it does not make the judgment correct. RF also has contradictory
  status/issue outputs in saved examples. Human review remains mandatory.
- MiniLM/OCR success covers controlled fixtures, not every municipal layout,
  low-quality scan or an independent accuracy study. Historical unavailable
  private PDFs were excluded. Uncertainty warnings and manual confirmation stay.
- Hobby cold starts, signed-token expiry/rotation, monthly quotas and actual
  Vercel request overhead remain operating limits. The actual website is not
  verified by any local or GitHub result. Hobby's personal/non-commercial-use
  eligibility for organizational municipal operations remains separate.

Sources: the Linux run's `results/{summary,native,legacy,rf,semantic,concurrent}.json`,
`preservation-after.json`, the original legacy training manifest and RF
configuration/comparison. No dataset/review campaign or retraining was repeated.

## Exactly what is still missing

1. Clean completion AND actual process exit for the existing two-worker workload
   within its unchanged 180 seconds, using the current source and original bytes.
2. A resource-bounded Linux result at 2 GiB / one CPU with private v1 remote
   part/archive/artifact verification. Existing individual passes are reused;
   there is no repeat of the nine-phase campaign.
3. An owner-signed-off quality/use decision and deployment review for the exact
   profiles. Recommended initial scope: **legacy + semantic_ocr, fictional
   Capstone advisory workflows only, human confirmation mandatory**. Keep RF
   production-inactive unless separately and explicitly accepting its experimental
   limitations. Independent RF accuracy/municipal suitability remains unproven.
4. After approval: new immutable transport, fresh signed-download tokens and
   remote verification of the final bytes. Actual Vercel acceptance follows
   separately authorized deployment; it is not claimed in advance.

## One manual Ubuntu verification workflow

`.github/workflows/model-release-verification.yml` is dispatch-only, defaults
authorization to false and has read-only repository permission. It uses a separate
repository Actions secret, `PSMS_MODEL_VERIFICATION_PART_TOKENS`, only for fetch.
It requires no GitHub environment: those require paid GitHub plans for private
repositories ([GitHub documentation](https://docs.github.com/en/actions/how-tos/deploy/configure-and-manage-deployments/manage-environments)).
Existing production environment/approval controls remain unchanged. The source-only
Bookworm appliance uses the exact existing Python/dependency versions. No model
or credential enters a build layer.

Only the fetch step receives short-lived `PSMS_MODEL_PART_TOKENS`; no database,
service-role, signing, Vercel or user-account credential is needed. All five remote
part hashes and the archive/28 original files are checked before mounting the
models read-only into a non-root, network-disabled container. The container has
2 GiB memory, one CPU, two request workers and 512 MiB temporary storage.

The workflow calls the **existing concurrent workload unchanged**, including
real Tesseract extraction/mapping, MiniLM, legacy/RF predictions, safe loader
failure and timeout assertions. It does not run the eight completed phases again.
A supervisor requires normal process exit within 180 seconds; success written
before a hang cannot pass. Checkpoints survive failure. Only four explicit,
sanitized JSON/JSONL evidence files are uploaded; never weights, archives, tokens
or private subprocess logs. A pass grants no approval and deploys nothing.

Owner steps:

1. Review the new source set, then commit/push only when authorized. Make the
   workflow available on the default branch, as required for GitHub
   [manual dispatch](https://docs.github.com/en/actions/how-tos/manage-workflow-runs/manually-running-a-workflow).
2. In repository Settings -> Secrets and variables -> Actions, create ONLY the
   short-lived repository secret `PSMS_MODEL_VERIFICATION_PART_TOKENS`. Generate
   short-lived signed DOWNLOAD URLs for the five existing v1 root objects. Use
   the existing hidden collector locally without printing the JSON:

   ```powershell
   .\deploy\Set-PrivateModelTokens.ps1
   Set-Clipboard -Value $env:PSMS_MODEL_PART_TOKENS
   ```

   Paste into that repository secret, then clear clipboard/local process
   secret. This explicit verification workflow is the only GitHub job that
   needs these limited capabilities; the production deploy workflow still does not.
   Remove/rotate this verification-only secret after retaining a passing result.
3. Actions -> **Original model release verification** -> Run workflow on the
   reviewed ref -> check `verification_authorized`. A red/timed-out job remains
   a blocker. Do not increase the deadline or mark the candidate verified.
4. Download its evidence artifact privately. Retain `verification.json`,
   `fetch.json`, `resources.json` and checkpoints. Require a green job,
   `passed=true`, `process_exit_code=0`, matching candidate/commit hashes and
   successful remote byte checks. The old aggregate still remains 8/9; this new
   focused evidence is a separate completion gate, not a rewritten old result.

## Explicit owner decision and final packaging

Copy `deploy/proposals/model_release_owner_decision.PROPOSED.json` to
`output/model_approval_preparation/owner-decision.json`. It defaults to
`NOT_APPROVED`. The OWNER must inspect the real GitHub run and limitations before
setting `decision=APPROVE`. Fill all of:

- `release_id=nlp-online-original-v2` (new immutable ID); exact candidate ID/pin.
- Exact `workflow_run_url` and `source_commit` from the real verified run.
- `linux_evidence_sha256`: SHA-256 of the downloaded `verification.json`:

  ```powershell
  Get-FileHash -Algorithm SHA256 output/model_approval_preparation/linux-evidence/verification.json
  ```

- `approved_by`, UTC `approved_at_utc`, actual quality/deployment review
  references, `limitations_accepted=true` and explicit `approved_profiles`.
  Recommended profiles are `legacy` and `semantic_ocr`. Including `retrained_rf`
  additionally requires `experimental_rf_limitations_accepted=true`; this does
  not create an independent accuracy claim or alter its original research flags.
- Keep `use_scope=CAPSTONE_SYNTHETIC_ADVISORY_ONLY`. This procedure is not an
  authorization for real municipal use or automatic procurement approval.

The tool validates the evidence/decision hashes, workflow/commit binding, current
code/dependency/feature/weight contracts, 2 GB/one-CPU limits and real process-exit
result. Owner verification of the actual GitHub provenance is still essential;
JSON metadata alone is not an independently authenticated signed attestation.

ONLY after that real decision, run:

```powershell
python -B deploy/finalize_model_release.py --decision output/model_approval_preparation/owner-decision.json --linux-evidence output/model_approval_preparation/linux-evidence/verification.json --candidate-dir output/hobby_delivery/model-proposal/private-models/nlp-online-original-v1 --output output/model_approval_preparation/final-v2
```

Default/nonapproval, incomplete/failed evidence, stale source fingerprints,
changed bytes or reused IDs fail closed. The finalizer copies original weights
unchanged, binds the reviewed approval/evidence, and generates a NEW manifest
SHA-256, archive SHA-256 and per-part size/hash pins plus unsigned root object
URLs. These SHA-256 pins are not digital signatures. Supabase supplies the later
signed-download capabilities. No production registry, Git or cloud is modified.

Publish only the new `nlp-online-original-v2.000.part` through `.004.part` to the
same PRIVATE bucket root without overwriting v1. Obtain fresh v2 signed URLs:

```powershell
.\deploy\Set-PrivateModelTokens.ps1 -DescriptorPath output/model_approval_preparation/final-v2/registry-entry.APPROVED.json -CopyForVercel
python -B deploy/verify_model_parts.py --descriptor output/model_approval_preparation/final-v2/registry-entry.APPROVED.json --remote
```

After successful final-byte verification, put those v2 capabilities in EXISTING
Vercel Production `PSMS_MODEL_PART_TOKENS`, keep the old broad token unset, and
manually merge the exact approved entry into `inference_release_registry.json`.
Preserve the original entries; no source pointer is automatically activated.
Use the refreshed exact Git delivery list, including the existing verification
workflow, `deploy/linux_validation/probe.py` and `tests/OCR_FILETYPE_REGRESSION_TEST.py`.
Then manually dispatch **Existing Vercel production deployment** with
`inference_mode=approved_release`, `nlp_runtime=retrained_rf` only when that profile
was explicitly approved, release ID `nlp-online-original-v2`, and its explicit
approval checkbox. Set the matching Vercel Production runtime/release ID and
`PSMS_INFERENCE_ENABLED=1` only for this approved deployment. Keep the packaged
Supabase CA and working database configuration unchanged. Follow the existing fictional workbook persistence,
OCR/NLP and human-review acceptance steps. No SQL needs to be rerun.

No workflow, owner approval, original-model finalization, upload, push or
deployment was performed by this task. Windows tests use synthetic bytes only;
they do not supply the missing genuine Linux result.

Preparation checks: **6/6 passed** on Windows, covering default refusal,
evidence/hash binding, original-byte preservation in a synthetic finalization,
explicit RF review, real-exit timeout rejection and manual/secret/artifact workflow
contracts. The source-only appliance context was assembled without building or
running it. `output/model_approval_preparation/evidence-linkage.json` records the
read-only linkage to the existing eight Linux passes and all 28 original hashes.
The exact refreshed Git delivery list is
`output/model_approval_preparation/source-ready/github-files.txt`; no weights,
archives, tokens or generated reports are part of that source list.
