"""Evidence-bound arithmetic, explicit limits and approved-offer comparison.

No catalogue, COA values, NLP prediction, market feed, conversion table or I/O.
Policy and provenance are caller assertions, not independently authenticated facts.
"""

from decimal import Decimal, localcontext, ROUND_HALF_UP, ROUND_HALF_EVEN
import hashlib
import json
import re

from .contracts import InputError, clone_snapshot, evidence, number, read_path, text

ROUNDINGS = {"ROUND_HALF_UP": ROUND_HALF_UP, "ROUND_HALF_EVEN": ROUND_HALF_EVEN}
UNRESOLVED = {"MISSING_EVIDENCE", "UNSUPPORTED", "CONFLICTING_EVIDENCE", "INVALID_INPUT"}


def adjustment_terms(value):
    """Price-affecting fixed terms, preserving order but not document-local refs."""
    if type(value) is not list or any(type(x) is not dict for x in value):
        return None
    return [{k: x.get(k) for k in ("kind", "operation", "amount", "currency", "basis")} for x in value]


class Verifier:
    def __init__(self, snapshot):
        self.s = snapshot
        self.findings = []
        self.currency = None
        self.quantum = None
        self.rounding = None

    def add(self, check, result, message, paths, computed=None):
        finding = {"check": check, "result": result, "message": message,
                   "evidence": [evidence(self.s, p) for p in dict.fromkeys(map(tuple, paths))]}
        if computed is not None:
            finding["computed"] = computed
        self.findings.append(finding)

    def get(self, path):
        return read_path(self.s, path)[1]

    def numeric(self, path, check):
        value = self.get(path)
        if value is None or value == "":
            self.add(check, "MISSING_EVIDENCE", "A numeric source field is missing; no zero was substituted.", [path])
            return None
        try:
            return number(value)
        except InputError:
            self.add(check, "INVALID_INPUT", "Expected a nonnegative finite decimal string without currency symbols or grouping.", [path])
            return None

    def rounded(self, value):
        return value.quantize(self.quantum, rounding=self.rounding)

    def equal(self, check, left, right, paths):
        # Equality is exact unless arithmetic explicitly calls rounded() first.
        self.add(check, "CHECK_PASSED" if left == right else "CHECK_FAILED",
                 "Compared the stated values under the supplied arithmetic policy.", paths,
                 {"left": str(left), "right": str(right)})

    def extensions(self, obj, allowed, path):
        unexpected = sorted(set(obj) - set(allowed))
        if unexpected:
            self.add("UNSUPPORTED_FIELDS", "UNSUPPORTED",
                     "Unrecognized pricing terms cannot be silently ignored.", [path + (k,) for k in unexpected])
            return False
        return True

    def policy(self):
        p = self.get(("money_policy",))
        if type(p) is not dict:
            self.add("ARITHMETIC_POLICY", "MISSING_EVIDENCE", "Explicit currency, scale, rounding and source reference are required.", [("money_policy",)])
            return False
        valid = self.extensions(p, {"currency", "scale", "rounding", "rounding_stage", "source_ref"}, ("money_policy",))
        valid = valid and (type(p.get("currency")) is str and re.fullmatch(r"[A-Z]{3}", p["currency"]) is not None
                           and type(p.get("scale")) is int and 0 <= p["scale"] <= 4
                           and type(p.get("rounding")) is str and p["rounding"] in ROUNDINGS
                           and p.get("rounding_stage") == "line_then_document"
                           and text(p.get("source_ref")))
        if not valid:
            self.add("ARITHMETIC_POLICY", "UNSUPPORTED", "Missing/unsupported explicit arithmetic policy; no default monetary rule is assumed.", [("money_policy",)])
            return False
        self.currency, self.quantum = p["currency"], Decimal(1).scaleb(-p["scale"])
        self.rounding = ROUNDINGS[p["rounding"]]
        return True

    def currency_ok(self, path, check):
        value = self.get(path)
        if not text(value):
            self.add(check, "MISSING_EVIDENCE", "Currency must be explicit.", [path])
            return False
        if value != self.currency:
            self.add(check, "UNSUPPORTED", "Currency differs from the explicit policy; exchange conversion is not supported.", [path, ("money_policy", "currency")])
            return False
        return True

    def unit_ok(self, path):
        unit = self.get(path)
        if not text(unit):
            self.add("PRICE_UNIT", "MISSING_EVIDENCE", "A pricing/quantity unit is missing or invalid.", [path])
            return False
        definition = self.get(("unit_definitions", unit))
        if not text(definition):
            self.add("PRICE_UNIT", "UNSUPPORTED", "The supplied unit has no explicit definition; no pack factor or alias was invented.", [path, ("unit_definitions", unit)])
            return False
        return True

    def adjustments(self, path, base, currency_path):
        adjustments = self.get(path)
        if type(adjustments) is not list:
            self.add("ADJUSTMENTS", "MISSING_EVIDENCE", "Supply an explicit adjustment list, including [] when none apply.", [path])
            return None
        if len(adjustments) > 50:
            self.add("ADJUSTMENTS", "UNSUPPORTED", "Adjustment count exceeds this version's limit.", [path])
            return None
        result = base
        valid = True
        for i, adj in enumerate(adjustments):
            ap = path + (i,)
            if type(adj) is not dict:
                self.add("ADJUSTMENTS", "INVALID_INPUT", "Adjustment must be a source-bound object.", [ap])
                valid = False
                continue
            known = self.extensions(adj, {"kind", "operation", "amount", "currency", "basis", "source_ref"}, ap)
            if (not known or type(adj.get("kind")) is not str or adj["kind"] not in {"tax", "discount", "adjustment"}
                    or type(adj.get("operation")) is not str or adj["operation"] not in {"add", "subtract"}
                    or not text(adj.get("basis")) or not text(adj.get("source_ref"))):
                self.add("ADJUSTMENTS", "UNSUPPORTED", "Only explicitly documented fixed amounts are supported; no tax/rate/discount rule is inferred.", [ap])
                valid = False
                continue
            amount = self.numeric(ap + ("amount",), "ADJUSTMENTS")
            curr_ok = self.currency_ok(ap + ("currency",), "ADJUSTMENT_CURRENCY")
            if amount is None or not curr_ok:
                valid = False
                continue
            if self.rounded(amount) != amount:
                self.add("ADJUSTMENTS", "UNSUPPORTED", "Fixed adjustment precision exceeds the stated currency scale.", [ap + ("amount",), ("money_policy", "scale")])
                valid = False
                continue
            result += amount if adj["operation"] == "add" else -amount
        if result < 0:
            self.add("ADJUSTMENTS", "CONFLICTING_EVIDENCE", "Adjustments produce a negative payable amount; no credit-note policy is supported.", [path, currency_path])
            valid = False
        return self.rounded(result) if valid else None

    def line(self, row, path, doc):
        if type(row) is not dict:
            self.add("LINE", "INVALID_INPUT", "Line must be an object.", [path])
            return None
        known = self.extensions(row, {"source_line_id", "description", "quantity", "unit", "price_unit", "unit_price",
                                     "subtotal", "amount", "currency", "unit_price_basis", "adjustments"}, path)
        q = self.numeric(path + ("quantity",), "LINE_QUANTITY")
        price = self.numeric(path + ("unit_price",), "LINE_UNIT_PRICE")
        subtotal = self.numeric(path + ("subtotal",), "LINE_SUBTOTAL")
        stated = self.numeric(path + ("amount",), "LINE_AMOUNT")
        currency = self.currency_ok(path + ("currency",), "LINE_CURRENCY")
        units = [self.unit_ok(path + (key,)) for key in ("unit", "price_unit")]
        if all(units) and row["unit"] != row["price_unit"]:
            self.add("PRICE_UNIT", "UNSUPPORTED", "Quantity and price use different units; conversion requires a separately reviewed contract.", [path + ("unit",), path + ("price_unit",)])
            units = [False]
        basis = text(row.get("unit_price_basis"))
        if not basis:
            self.add("PRICE_BASIS", "MISSING_EVIDENCE", "Unit-price tax/discount basis is not specified.", [path + ("unit_price_basis",)])
        dependencies = [path + (k,) for k in ("quantity", "unit_price", "unit", "price_unit", "currency", "unit_price_basis")]
        dependencies += [("money_policy",), ("unit_definitions", row.get("unit", ""))]
        computed = None
        if known and currency and all(units) and basis and q is not None and price is not None:
            base = self.rounded(q * price)
            if subtotal is not None:
                self.equal("QUANTITY_TIMES_UNIT_PRICE", base, subtotal, dependencies + [path + ("subtotal",)])
            # The canonical arithmetic uses the supplied q and price, never a bad
            # declared subtotal. A failed subtotal does not hide a second bad amount.
            computed = self.adjustments(path + ("adjustments",), base, path + ("currency",))
            if computed is not None and stated is not None:
                self.equal("LINE_PAYABLE_AMOUNT", computed, stated,
                           dependencies + [path + ("adjustments",), path + ("amount",)])
        reconciled = (computed is not None and stated == computed and subtotal is not None
                      and q is not None and price is not None and subtotal == self.rounded(q * price))
        return {"path": path, "row": row, "computed": computed, "stated": stated, "reconciled": reconciled,
                "price": price, "quantity": q, "valid_context": known and currency and all(units) and basis}

    def document(self, side):
        path = (side,)
        doc = self.get(path)
        if type(doc) is not dict:
            self.add("DOCUMENT", "MISSING_EVIDENCE", "A document snapshot is required.", [path])
            return None
        known = self.extensions(doc, {"id", "currency", "total_basis", "items", "subtotal", "total", "adjustments", "source_ref",
                                    "status", "approval_ref", "winning_offer_confirmed", "winning_offer_ref", "aoq_id"}, path)
        identity = text(doc.get("id")) and text(doc.get("source_ref")) and text(doc.get("total_basis"))
        if not identity:
            self.add("DOCUMENT_PROVENANCE", "MISSING_EVIDENCE", "Document identity, source reference and total basis must be explicit.", [path + (k,) for k in ("id", "source_ref", "total_basis")])
        currency = self.currency_ok(path + ("currency",), "DOCUMENT_CURRENCY")
        rows = doc.get("items")
        if type(rows) is not list or not 1 <= len(rows) <= 200:
            self.add("DOCUMENT_ITEMS", "MISSING_EVIDENCE" if not rows else "UNSUPPORTED", "Expected 1 to 200 complete item rows; an empty document is not compliant.", [path + ("items",)])
            rows = []
        lines = [self.line(row, path + ("items", i), doc) for i, row in enumerate(rows)]
        stated_subtotal = self.numeric(path + ("subtotal",), "DOCUMENT_SUBTOTAL")
        stated_total = self.numeric(path + ("total",), "DOCUMENT_TOTAL")
        computed = None
        paths = [path + ("items",), path + ("adjustments",), ("money_policy",)]
        if known and identity and currency and lines and all(l and l["computed"] is not None for l in lines):
            line_sum = sum((l["computed"] for l in lines), Decimal(0))
            if stated_subtotal is not None:
                self.equal("DOCUMENT_LINE_SUM", line_sum, stated_subtotal, paths + [path + ("subtotal",)])
            computed = self.adjustments(path + ("adjustments",), line_sum, path + ("currency",))
            if computed is not None and stated_total is not None:
                self.equal("DOCUMENT_PAYABLE_TOTAL", computed, stated_total, paths + [path + ("total",)])
        else:
            self.add("DOCUMENT_PAYABLE_TOTAL", "MISSING_EVIDENCE", "A full-document total cannot be verified from incomplete/unsupported lines or document context.", paths)
        reconciled = (computed is not None and stated_total == computed and lines
                      and all(l and l["reconciled"] for l in lines)
                      and stated_subtotal == sum((l["computed"] for l in lines), Decimal(0)))
        return {"doc": doc, "path": path, "lines": lines, "computed": computed,
                "stated": stated_total, "reconciled": bool(reconciled)}

    def limit(self, limit, path, target, *, budget=False):
        check = "APPROVED_BUDGET" if budget else "EXPLICIT_PRICE_CEILING"
        if type(limit) is not dict:
            self.add(check, "MISSING_EVIDENCE", "No explicit source-bound limit was supplied; no budget/market value was inferred.", [path])
            return
        allowed = {"amount", "currency", "document_id", "source_ref", "basis", "metric", "source_line_id", "unit", "approved", "approval_ref"}
        if not self.extensions(limit, allowed, path):
            return
        if not text(limit.get("source_ref")):
            self.add(check, "MISSING_EVIDENCE", "Limit needs a source field/document reference.", [path + ("source_ref",)])
            return
        if budget and (limit.get("approved") is not True or not text(limit.get("approval_ref"))):
            self.add(check, "MISSING_EVIDENCE", "An estimate or unapproved amount is not an explicitly approved budget.", [path + ("approved",), path + ("approval_ref",)])
            return
        if not target or limit.get("document_id") != target["doc"].get("id"):
            self.add(check, "CONFLICTING_EVIDENCE", "Limit scope does not identify the supplied PO.", [path + ("document_id",), ("po", "id")])
            return
        amount = self.numeric(path + ("amount",), check)
        currency = self.currency_ok(path + ("currency",), check)
        metric = limit.get("metric")
        supported = {"document_total"} if budget else {"document_total", "unit_price", "line_amount"}
        if type(metric) is not str or metric not in supported:
            self.add(check, "UNSUPPORTED", "Limit metric is missing or unsupported.", [path + ("metric",)])
            return
        observed, observed_path = target["computed"], ("po", "total")
        observed_stated = target["stated"]
        basis = target["doc"].get("total_basis")
        if metric != "document_total":
            matches = [l for l in target["lines"] if l and text(limit.get("source_line_id"))
                       and l["row"].get("source_line_id") == limit["source_line_id"]]
            if len(matches) != 1:
                self.add(check, "MISSING_EVIDENCE", "Limit requires one unambiguous source-line identity; no description matching is assumed.", [path + ("source_line_id",), ("po", "items")])
                return
            line = matches[0]
            key = "unit_price" if metric == "unit_price" else "amount"
            observed_path = line["path"] + (key,)
            observed_stated = line["price"] if metric == "unit_price" else line["stated"]
            observed = line["price"] if metric == "unit_price" else line["computed"]
            if not line["valid_context"]:
                observed = None
            basis = line["row"].get("unit_price_basis") if metric == "unit_price" else target["doc"].get("total_basis")
            if metric == "unit_price" and limit.get("unit") != line["row"].get("price_unit"):
                self.add(check, "UNSUPPORTED", "Ceiling and offered unit price use different/missing units.", [path + ("unit",), line["path"] + ("price_unit",)])
                return
        if not text(limit.get("basis")) or limit.get("basis") != basis:
            self.add(check, "UNSUPPORTED", "Limit and price tax/discount bases differ or are unspecified.", [path + ("basis",), observed_path])
            return
        if amount is None or not currency:
            return
        if metric == "document_total" and not target["reconciled"]:
            self.add(check, "CONFLICTING_EVIDENCE" if observed is not None and observed_stated is not None else "MISSING_EVIDENCE",
                     "Document and line prices must reconcile before a document-limit check can be completed.",
                     [path, ("po", "items"), ("po", "subtotal"), observed_path])
            return
        # Never pass a budget from an understated declared total. Also never pass
        # from an unverified derived total while the official stated one disagrees.
        if observed is None or observed_stated is None:
            self.add(check, "MISSING_EVIDENCE", "Comparable complete pricing evidence is missing.", [path, observed_path])
            return
        if observed != observed_stated:
            self.add(check, "CONFLICTING_EVIDENCE", "Stated and calculated prices conflict; limit compliance cannot be established.", [path, observed_path], {"calculated": str(observed), "stated": str(observed_stated)})
            return
        self.add(check, "CHECK_PASSED" if observed <= amount else "CHECK_FAILED",
                 "Compared against the explicit limit only; no market-price or procurement-policy conclusion.",
                 [path, observed_path, ("po", "items"), ("money_policy",)],
                 {"observed": str(observed), "limit": str(amount), "difference": str(observed - amount)})

    def cross_offer(self, aoq, po):
        check = "AOQ_PO_LINK"
        if not aoq or not po:
            self.add(check, "MISSING_EVIDENCE", "Approved winning offer and linked PO are both needed.", [("aoq",), ("po",)])
            return
        a, p = aoq["doc"], po["doc"]
        if (a.get("status") != "Approved" or not text(a.get("approval_ref"))
                or a.get("winning_offer_confirmed") is not True or not text(a.get("winning_offer_ref"))):
            self.add(check, "MISSING_EVIDENCE", "AOQ must explicitly identify an approved, confirmed winning offer.", [("aoq", k) for k in ("status", "approval_ref", "winning_offer_confirmed", "winning_offer_ref")])
            return
        if not text(a.get("id")) or p.get("aoq_id") != a["id"]:
            self.add(check, "CONFLICTING_EVIDENCE", "PO does not identify the supplied AOQ.", [("po", "aoq_id"), ("aoq", "id")])
            return
        if a.get("currency") != p.get("currency") or a.get("total_basis") != p.get("total_basis"):
            self.add(check, "UNSUPPORTED", "AOQ and PO need identical explicit currencies and total bases.", [(side, k) for side in ("aoq", "po") for k in ("currency", "total_basis")])
            return
        maps = []
        for side, doc in (("aoq", aoq), ("po", po)):
            by_id = {}
            for line in doc["lines"]:
                identity = line["row"].get("source_line_id") if line else None
                if not text(identity) or identity in by_id:
                    self.add("AOQ_PO_ALIGNMENT", "MISSING_EVIDENCE", "Missing/duplicate source-line identities prevent reliable correspondence.", [(side, "items")])
                    return
                by_id[identity] = line
            maps.append(by_id)
        am, pm = maps
        if not am or not pm:
            self.add("AOQ_PO_ALIGNMENT", "MISSING_EVIDENCE", "Both documents require item evidence.", [("aoq", "items"), ("po", "items")])
            return
        self.add("AOQ_PO_ITEM_COVERAGE", "CHECK_PASSED" if am.keys() == pm.keys() else "CHECK_FAILED",
                 "Compared explicit source-line identities; descriptions/categories do not select matches.", [("aoq", "items"), ("po", "items")])
        for identity in sorted(am.keys() & pm.keys()):
            left, right = am[identity], pm[identity]
            paths = [left["path"], right["path"]]
            fields = ("unit", "price_unit", "unit_price_basis", "currency")
            if (not left["valid_context"] or not right["valid_context"]
                    or any(left["row"].get(k) != right["row"].get(k) for k in fields)):
                self.add("AOQ_PO_LINE_PRICE", "UNSUPPORTED", "Line units/currency/basis are incomplete or differ; no price-equivalence conversion is inferred.", paths)
                continue
            for field, value_key in (("quantity", "quantity"), ("unit_price", "price"), ("amount", "stated")):
                lval, rval = left[value_key], right[value_key]
                refs = [l["path"] + (field,) for l in (left, right)]
                if lval is None or rval is None:
                    self.add("AOQ_PO_" + field.upper(), "MISSING_EVIDENCE", "A comparable source price/quantity is missing.", refs)
                else:
                    self.equal("AOQ_PO_" + field.upper(), lval, rval, refs)
            for l in (left, right):
                if l["computed"] is None or l["stated"] != l["computed"]:
                    self.add("AOQ_PO_LINE_INTEGRITY", "CONFLICTING_EVIDENCE", "Matching stored prices do not establish integrity when a line is incomplete or conflicts with arithmetic.", [l["path"]])
            # An amendment can be documented without authorizing a changed price.
            # This component flags changed fixed terms for separate human review.
            left_terms = left["row"].get("adjustments")
            right_terms = right["row"].get("adjustments")
            if adjustment_terms(left_terms) != adjustment_terms(right_terms):
                self.add("AOQ_PO_ADJUSTMENT_TERMS", "CONFLICTING_EVIDENCE", "Winning-offer and PO adjustment terms differ; no amendment approval is inferred.", [l["path"] + ("adjustments",) for l in (left, right)])
        if adjustment_terms(a.get("adjustments")) != adjustment_terms(p.get("adjustments")):
            self.add("AOQ_PO_ADJUSTMENT_TERMS", "CONFLICTING_EVIDENCE", "Document adjustment terms differ; offsetting tax/discount changes are not evidence of an authorized amendment.", [("aoq", "adjustments"), ("po", "adjustments")])
        if aoq["computed"] is None or po["computed"] is None or aoq["stated"] is None or po["stated"] is None:
            self.add("AOQ_PO_TOTAL", "MISSING_EVIDENCE", "Complete comparable totals are required.", [("aoq", "total"), ("po", "total")])
        elif aoq["computed"] != aoq["stated"] or po["computed"] != po["stated"]:
            self.add("AOQ_PO_TOTAL", "CONFLICTING_EVIDENCE", "AOQ/PO totals conflict with calculated values.", [("aoq", "total"), ("po", "total"), ("aoq", "items"), ("po", "items")])
        else:
            self.equal("AOQ_PO_TOTAL", aoq["computed"], po["computed"], [("aoq", "total"), ("po", "total"), ("aoq", "adjustments"), ("po", "adjustments")])

    def run(self):
        if not self.extensions(self.s, {"schema_version", "money_policy", "unit_definitions", "po", "aoq", "approved_budget", "ceilings"}, ()):
            return
        if self.s.get("schema_version") != "0.1.0":
            self.add("INPUT_SCHEMA", "UNSUPPORTED", "Only snapshot schema 0.1.0 is supported.", [("schema_version",)])
            return
        if not self.policy():
            return
        po = self.document("po")
        aoq = self.document("aoq") if self.s.get("aoq") is not None else None
        self.limit(self.s.get("approved_budget"), ("approved_budget",), po, budget=True)
        ceilings = self.s.get("ceilings")
        if type(ceilings) is not list or len(ceilings) > 200:
            self.add("EXPLICIT_PRICE_CEILING", "MISSING_EVIDENCE", "An explicit ceiling list is required; [] records that no ceiling was supplied, not compliance.", [("ceilings",)])
        elif not ceilings:
            self.add("EXPLICIT_PRICE_CEILING", "MISSING_EVIDENCE", "No explicit price ceiling was supplied; no ceiling compliance conclusion.", [("ceilings",)])
        else:
            for i, ceiling in enumerate(ceilings):
                self.limit(ceiling, ("ceilings", i), po)
        self.cross_offer(aoq, po)


def evaluate(snapshot):
    report = {"schema_version": "0.1.0", "diagnostic_only": True, "authoritative": False,
              "procurement_write": False, "suggested_status": None, "overall_compliance": None,
              "provenance": "CALLER_SUPPLIED_NOT_INDEPENDENTLY_AUTHENTICATED", "findings": []}
    try:
        clean = clone_snapshot(snapshot)
    except InputError as exc:
        report.update(processing_state="NON_EVALUATED", diagnostics=[str(exc)])
        return report
    verifier = Verifier(clean)
    # Decimal arithmetic is independent of the caller's precision, rounding,
    # trap settings and flags. Bounds make 80 digits sufficient for all sums.
    from decimal import Context
    with localcontext(Context(prec=80)):
        verifier.run()
    report["findings"] = verifier.findings
    report["input_sha256"] = hashlib.sha256(json.dumps(clean, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()
    counts = {}
    for finding in verifier.findings:
        counts[finding["result"]] = counts.get(finding["result"], 0) + 1
    report["counts"] = counts
    report["processing_state"] = "PARTIAL" if any(k in UNRESOLVED for k in counts) else "EVALUATED"
    report["observed_outcome"] = "VIOLATIONS_FOUND" if counts.get("CHECK_FAILED") else "NO_VIOLATION_PROVEN"
    return report
