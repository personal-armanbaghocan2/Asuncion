# Review, receiving and issuance release

Base: `52c5d773ae68d74a026069bdac981c4177124f87`.

## Changes

- Show source-bound quantity deviation and the existing item-line discrepancy separately from model confidence. Unresolved or stale evidence cannot authorize approval.
- Expose eligible PR–PO approval, explain blocked approval, and open IAR/AIR with the approved PO selected. List eligible approved NLP records in IAR/AIR.
- Display DBM/NON-DBM/LIB classification from accepted receipts and approved source plans. Preserve independently combinable stock-type and planning filters. LIB planning expenses do not fabricate stock.
- Link inventory items to the correct RIS/ICS tab, preselect the item and explain quantities, review and approval. Register the new JavaScript in the production asset allowlist.
- Repair the existing production-upload test's positive GET fixture using the real signed navigation link. Authentication, navigation and CSRF guards remain active.

## Verification

The narrow release was reconstructed against current production main, preserving unrelated development changes outside this release.

- 47 focused workflow tests passed against the candidate. Six independent source-binding/percentage regression tests are included in this commit; these overlap the focused suite.
- All 12 delivery tests passed with the exact committed baseline helpers and the corrected upload fixture.
- A fresh owned PostgreSQL 17 instance and Edge browser passed eight actual workflow checks: login, separate review/approval, IAR handoff and acceptance, inventory creation, RIS/ICS approval and repeat-approval stock protection. No application HTTP responses were mocked; saved NLP classifier outputs were explicit synthetic fixtures.
- Production context preparation passed for the existing approved `nlp-online-original-v2`/`retrained_rf` configuration: 157 source files, 2,144,307 bytes, zero model bytes uploaded. Asset validation passed for 28 assets.
- Read-only Supabase metadata checks confirmed existing private upload/processing tables, owner columns, actor bridge, revision API, planning-table SELECT privileges, restricted backend and matching approval-function source pins. No account fields, municipal rows or cloud mutations were used. Metadata checks do not establish full hosted workflow success.

The public PPMP draft table currently has RLS disabled, with no SELECT privilege for `anon` or `authenticated`. This pre-existing configuration was not changed or certified as a complete security review.

## Release and rollback

This release contains no SQL migration, credential, model, uploaded document or synthetic database records. Existing database contracts and approval gates remain in place.

The production workflow in the base commit is manually dispatched; a Git push alone does not deploy this release. The current ready production deployment is `dpl_kQuYDN66rHthtRkTiGCoWP6FP1DS`.

To undo the source change, revert this release commit with a new commit and push normally. If separately deployed, production traffic can be rolled back to the recorded deployment. Preserve database records and new procurement activity; code rollback does not reverse database mutations. This release requires no database rollback.

Not yet verified: a hosted deployment of this commit, production account compatibility, or new classifier/OCR accuracy. This report does not claim complete production readiness.
