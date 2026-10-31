# Procurement pricing verifier 0.1.0

Implemented independent Python component; local diagnostics only. No Flask hook,
database connector, model dependency, COA catalogue access or file/network writes.
Import and default calls are disabled. This is not an official approval engine.

## Run

From the project/package root:

```powershell
python -B -m unittest discover -s tests -p test_procurement_pricing_v0_1_0.py
python -B scripts/experiments/run_procurement_pricing_v0_1_0.py
python -B scripts/experiments/run_procurement_pricing_v0_1_0.py --enabled
```

The default runner prints DISABLED. The enabled runner evaluates five fictional
in-memory cases and prints structured results. It never reads operational records.

```python
from procurement_pricing_v0_1_0 import verify_snapshot
from procurement_pricing_v0_1_0.synthetic import sample_snapshot

snapshot = sample_snapshot()  # Synthetic data only; not an application adapter.
report = verify_snapshot(snapshot, enabled=True)
```

## Explicit input contract

`synthetic.py` and the report's `synthetic_input_example.json` give a complete
working example. This version accepts a normalized JSON-compatible dictionary,
not raw OCR text, uploads, database rows or an existing shadow snapshot.

- `schema_version`: exactly `0.1.0`.
- `money_policy`: explicit three-letter `currency`, `scale` (0-4), `rounding`
  (`ROUND_HALF_UP` or `ROUND_HALF_EVEN`), `rounding_stage` exactly
  `line_then_document`, and a nonempty `source_ref`. These are supplied test or
  documented arithmetic conventions, not automatically approved municipal rules.
- `unit_definitions`: exact unit code -> nonempty documentary definition. A
  future unit can be added without retraining or changing an item catalogue.
  Defining a unit does not establish a pack conversion or real-world correctness.
- `po`: explicit `id`, `aoq_id`, `source_ref`, `currency`, `total_basis`, `items`,
  `subtotal`, `total`, and `adjustments` (explicit empty list when none apply).
- `aoq`: equivalent document fields plus `status: Approved`, `approval_ref`,
  `winning_offer_confirmed: true`, and `winning_offer_ref`. Its `items` must be
  the actual confirmed winning offer; no supplier ranking is inferred.
- Each line: `source_line_id`, optional `description`, `quantity`, `unit`,
  `price_unit`, `unit_price`, `subtotal`, `amount`, `currency`, `unit_price_basis`
  and `adjustments`. Quantity and price units must be identical and documented.
  Same source-line identity binds AOQ to PO; array order/description do not.
- Every numeric quantity/price/limit/adjustment is a nonnegative decimal string
  with at most 18 integer and 9 fractional digits. No floats, booleans, grouping,
  signs, exponents or currency symbols. Zero remains explicit. Missing values
  never become zero. Caller code must resolve raw monetary strings against
  reviewed source evidence rather than silently coercing missing values.
- `approved_budget`: `document_id` matching PO ID, `metric: document_total`,
  `amount`, `currency`, `basis` matching PO `total_basis`, `approved: true`,
  `approval_ref`, and `source_ref`. APP/PR estimates and COA inventory unit values
  are not automatically accepted or fetched as approved limits.
- `ceilings`: explicit list of `document_total`, `line_amount` or `unit_price`
  limits, each with `document_id`, `amount`, `currency`, matching `basis`, and
  `source_ref`. Line limits additionally require one `source_line_id`; unit-price
  limits need exactly matching `unit`. [] means no ceiling was supplied, not
  that the purchase satisfies a ceiling.
- Fixed adjustments: `kind` (`tax`, `discount`, `adjustment`), `operation` (`add`
  or `subtract`), `amount`, `currency`, documentary `basis` and `source_ref`.
  They must use the currency scale, and may not make a payable amount negative.
  Percentage rates, tax exemptions, compound/cascading taxes, allocation,
  undisclosed inclusive taxes and amendment authority are not inferred.

Unknown fields in document/line/limit/adjustment pricing objects are reported as
unsupported, preventing an unknown extra tax/discount/alternate price from being
silently discarded. Root keys are similarly restricted. Policy and explicit unit
definitions are not market, legal or semantic validation.

## Calculations

1. Base line subtotal = quantity * unit price, rounded once using the supplied
   scale/mode. Compare with the stated line subtotal without rounding that
   stated amount to hide extra precision.
2. Line payable = base subtotal plus/minus documented fixed line adjustments.
3. Document subtotal = sum of calculated rounded line payables.
4. Document payable total = document subtotal plus/minus fixed document charges.
5. Compare only reconciled document amounts with explicit document limits. A
   bad/missing stated line or total prevents a budget-pass finding.
6. Compare exact bound AOQ/PO quantity, unit price, line amount and document total;
   identify missing lines and changed adjustment terms separately. Changed terms
   are not made permissible simply because their net effect is zero.

No binary floats enter these computations. A local Decimal context with precision
80 isolates arithmetic from caller settings. The new supplied rounding policy is
not used to change the application's legacy float/Python-round behavior.

## Output and evidence

Each finding contains a check, result, message and exact snapshot evidence paths
(JSON pointers), including actual presence/absence. Scalar evidence carries the
source value; container evidence records type/length without copying entire
documents. Derived results are explicitly marked `computed`, never source facts.
Input SHA-256 ties the diagnostic report to the complete supplied snapshot.

Results: `CHECK_PASSED`, `CHECK_FAILED`, `MISSING_EVIDENCE`,
`CONFLICTING_EVIDENCE`, `UNSUPPORTED`, `INVALID_INPUT`.

`EVALUATED` means all attempted checks were conclusive, including any failed ones;
`PARTIAL` means evidence/coverage remains unresolved. `NON_EVALUATED` means the
external input contract was rejected. `NO_VIOLATION_PROVEN` does not mean compliant.
`overall_compliance` and `suggested_status` are always null. No official NLP status,
approval or procurement transition is suggested or changed.

All provenance is `CALLER_SUPPLIED_NOT_INDEPENDENTLY_AUTHENTICATED`. This component
cannot authenticate a forged approval reference or prove that a source snapshot
is current. Future application integration must construct the snapshot server-side
from authorized, reviewed, version-bound source fields. Do not trust browser-posted
budgets or fabricated correspondence IDs.

## Supported / unsupported boundaries

Supported: any unfamiliar description/category with explicit bound line IDs and
documented units; multiple lines; same-currency arithmetic; supplied rounding;
fixed evidenced tax/discount/charges; explicit approved document budget and scoped
price ceilings; approved winning-offer/PO consistency; missing/conflicting fields.

Unsupported: market-price appraisal, historical-to-current COA valuation,
exchange rates, mixed-currency totals, unit/pack conversions, percentage tax law,
amendment authorization, free-text requirement extraction, semantic item identity,
supplier-selection legality, credit notes/negative prices, cumulative budget
availability across purchases and full approval compliance. Mixed currency or
undocumented units produce incomplete/unsupported findings rather than a pass.

The finite snapshot limits are 200 lines per document, 200 ceilings, 50 fixed
adjustments per object, depth 12 and bounded JSON size. No raw private records or
personal COA names are needed for development testing.
