# Pricing advisory integration 0.2.0

The existing `/nlp/<id>/view` page includes **Pricing and Budget Diagnostics**.
The NLP list, official result/confidence, approval actions and models are unchanged.
There is no new endpoint, browser-posted snapshot, model call or write operation.

`build_for_review(conn, nlp_id, session)` opens one dedicated connection through
the application's existing connection factory. Its first SQL statement sets
REPEATABLE READ READ ONLY, confirms transaction_read_only, sets local timeouts,
then rechecks only the current user's id/role/status. Only Active Admin, Staff and
Inspector accounts (the existing application roles) can read the bounded joined
NLP -> PO -> AOQ -> PR -> APP projection. Existing NLP access is global to those
authenticated roles; there is no office/record ACL in the inspected code. The
integration does not invent or broaden one. Missing/unauthorized records return
the same generic unavailable state. It always rolls back and closes, never commits.

PostgreSQL parent totals are cast to text. JSON numbers are parsed with Decimal;
duplicate keys, nonfinite/negative/oversized values and missing units are unresolved.
Stable `_AOQ Item ID` values, retained by PO generation, bind the winning offer to
the PO. Description similarity, row position and COA history never provide prices
or line correspondence. No credential, supplier name, OCR text or COA data is read
by this adapter. No raw driver error, snapshot or private value is logged.

## Deliberately limited current-source behavior

The current persistence contract lacks explicit per-document currency, decimal
rounding/basis, adjustment terms, unit definitions, scoped approved budget ceilings
and price ceilings. APP/PR totals remain labelled estimates even when their parent
documents are approved. We do not add speculative database columns/JSON keys or
silently reinterpret these estimates as procurement ceilings.

The adapter constructs an intentionally incomplete v0.1.0 snapshot and invokes
the unchanged verifier. Missing monetary policy produces Insufficient Evidence.
It separately shows **narrow source-level diagnostics**, not full compliance:

- Quantity, stored unit cost, exact unrounded Decimal product, stored line amount,
  and document totals with source-field references.
- Exact arithmetic equality and sum-of-stored-amounts equality. Differences that
  require sub-cent rounding remain Insufficient Evidence. Cent-exact multiplication
  discrepancies are described under the existing AOQ line-amount contract.
- AOQ-to-PO copied quantity/price/amount consistency, only for the approved winning
  offer, exact preserved source-line IDs and identical stored unit labels.
- Missing budget/ceiling/policy evidence. No estimated amount or COA value becomes
  a current price, approved budget or compliance pass.

Check Passed means **only the named numeric/source-copy check passed**. No overall
compliance indicator or suggested procurement status exists. Missing/conflicting
metadata cannot become Check Not Applicable. The latter is used only when a
documented source state makes the approved-offer check inapplicable (e.g. draft).

`present_verifier_report()` maps full v0.1.0 results when explicit evidence is
available in a synthetic fixture or future reviewed source adapter. The budget
exceedance test exercises that presenter with an explicit synthetic approved
budget. It does not claim the live current schema can supply such a budget.

## Validation and use

Run from the CAPSTONE project root:

```powershell
python -B -X utf8 -m unittest discover -s tests -p test_pricing_advisory_v0_2_0.py
python -B -X utf8 -m unittest discover -s tests -p test_procurement_pricing_v0_1_0.py
```

Tests use memory-only query spies and AST-loaded actual route/helper definitions
in a tiny Flask test-client app. They never import the operational app.py/db.py,
invoke initialization, query a live database or load a model. PostgreSQL SQL and
transaction enforcement were inspected, not executed against a real server.

Ready for supervised **isolated synthetic local testing**, subject to checking
real read-only SQL behavior on a designated synthetic PostgreSQL instance. Do not
restart the existing municipal instance to test this feature: the existing global
`ensure()` initializes/repairs data before authorization on a cold process. That
pre-existing behavior was not changed. Current advisory values are a coherent
current read snapshot, not an authenticated historical snapshot of the NLP run.

Before full budget/tax/ceiling checking, establish actual reviewed source metadata
and approval/scope semantics without guessing. Before online operation, resolve
existing startup mutations, authentication/session/CSRF/record-access and hosting
readiness work in separately authorized tasks.
