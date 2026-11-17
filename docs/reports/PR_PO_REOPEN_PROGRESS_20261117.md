# PR-PO reopening, percentages and inspection preparation

Base commit: `1645ee2526d347b2b23f9efb7e78696b2e8c4f51`.

## Implemented

The source selectors previously listed PRs and POs independently. Selecting an unrelated PR could trigger the generic conflict page. PO choices now carry their official or saved PR association, select that PR visibly, and explain whether the action will run inference or reopen a saved result. Server-side relationship validation remains authoritative.

A unique current version-2 verification now reopens read-only even when inert historical records exist. Actual inference/replacement save rules remain unchanged. If a concurrent save reports a conflict, the route rereads history once and reopens only one exact-source result. It does not retry a write. Stale, ambiguous and wrong-pair conflicts show bounded recovery instructions and saved-record links instead of the generic error message.

For source-bound pairs with pending checks, the UI shows a bounded discrepancy range, resolved-check coverage and pending counts. Quantity deviation remains independently calculated. For example, three resolved checks and one pending specification show 75% check coverage and a provisional 0-100% affected-item range, alongside 0% quantity deviation when quantities match. The range is not a final discrepancy score or approval authority. Malformed or stale evidence receives no fabricated percentage.

Reviewed records can open a read-only Inspection Preparation view in IAR/AIR. It carries source pairs and pending findings forward without creating an official IAR, changing NLP status or adding stock. Existing eligible approved results retain their direct official IAR handoff. The original creation/acceptance gates, SQL contracts, model results and transactional stock protections remain unchanged.

## Tested

- 15 focused pure/standalone-Flask tests passed: reopening without history mutation, stale/ambiguous rejection, discrepancy bounds, quantity calculations, concurrent-save recovery, wrong-pair handling and unchanged approval eligibility.
- 47 existing screenshot/workflow tests passed. These overlap some focused arithmetic coverage.
- 12 production delivery tests passed against baseline committed test helpers.
- 12 actual Edge/PostgreSQL checks passed in a newly owned disposable PostgreSQL 17 environment. Checks cover normal review/approval, IAR acceptance, RIS/ICS deductions and repeat protection, source auto-selection, reopening with inference disabled, pending percentages, inspection preparation, actionable conflict links and denial of premature official IAR creation. No JavaScript errors occurred.
- Static JavaScript and production asset/context preparation were checked. The new selection script is explicitly packaged.

The browser used saved synthetic classifier outputs. These checks do not establish new classifier accuracy or change trained models. No cloud account operations, credentials, source-record edits, schema migrations, approval overrides or automatic receiving were performed.

## Limits and rollback

A source whose PO omits requested specifications remains pending. A percentage range or a recorded review cannot establish that missing requirements are met. Inspection Preparation is a usable planning handoff, not official receiving authorization. An audited human exception/referral workflow would require a separately designed database contract; this release does not invent one.

Rollback is a normal revert of this source commit and redeployment of the previous release. Prior ready production deployment: `dpl_2fZohzgXLa1ujNK4DJZmcGj85yVK`. No database rollback is required, and new procurement activity must be preserved.
