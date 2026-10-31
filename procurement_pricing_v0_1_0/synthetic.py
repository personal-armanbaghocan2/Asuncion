"""Authored fictional development data only. No COA/catalogue or database lookup."""

from copy import deepcopy


def sample_snapshot():
    lines = [
        {"source_line_id": "DEMO-L1", "description": "Cryogenic pollen resonance cassette ZX-914",
         "quantity": "3", "unit": "cassette", "price_unit": "cassette",
         "unit_price": "125.50", "subtotal": "376.50", "amount": "376.50", "currency": "PHP",
         "unit_price_basis": "tax_exclusive", "adjustments": []},
        {"source_line_id": "DEMO-L2", "description": "Subaqueous acoustic calibration module QV-882",
         "quantity": "2", "unit": "module", "price_unit": "module",
         "unit_price": "210.25", "subtotal": "420.50", "amount": "420.50", "currency": "PHP",
         "unit_price_basis": "tax_exclusive", "adjustments": []},
    ]
    po = {"id": "PO-DEMO-001", "aoq_id": "AOQ-DEMO-001", "source_ref": "synthetic/po/001",
          "currency": "PHP", "total_basis": "final_payable", "items": deepcopy(lines),
          "subtotal": "797.00", "total": "797.00", "adjustments": []}
    aoq = {"id": "AOQ-DEMO-001", "source_ref": "synthetic/aoq/001",
           "status": "Approved", "approval_ref": "SYNTHETIC-APPROVAL-NOT-OFFICIAL",
           "winning_offer_confirmed": True, "winning_offer_ref": "synthetic/winning_offer/001",
           "currency": "PHP", "total_basis": "final_payable", "items": deepcopy(lines),
           "subtotal": "797.00", "total": "797.00", "adjustments": []}
    return {"schema_version": "0.1.0",
            "money_policy": {"currency": "PHP", "scale": 2, "rounding": "ROUND_HALF_UP",
                             "rounding_stage": "line_then_document", "source_ref": "synthetic/arithmetic-policy-only"},
            "unit_definitions": {"cassette": "one complete sealed cassette", "module": "one complete assembled module"},
            "po": po, "aoq": aoq,
            "approved_budget": {"document_id": po["id"], "metric": "document_total", "amount": "850.00",
                                "currency": "PHP", "basis": "final_payable", "approved": True,
                                "approval_ref": "SYNTHETIC-BUDGET-APPROVAL", "source_ref": "synthetic/app/approved-budget-field"},
            "ceilings": [{"document_id": po["id"], "metric": "unit_price", "source_line_id": "DEMO-L1",
                          "unit": "cassette", "amount": "130.00", "currency": "PHP", "basis": "tax_exclusive",
                          "source_ref": "synthetic/pr/explicit-unit-ceiling"}]}


def demo_cases():
    correct = sample_snapshot()
    wrong = deepcopy(correct)
    wrong["po"]["items"][0]["subtotal"] = "300.00"
    ceiling = deepcopy(correct)
    ceiling["ceilings"][0]["amount"] = "100.00"
    mismatch = deepcopy(correct)
    mismatch["po"]["items"][0].update(unit_price="126.50", subtotal="379.50", amount="379.50")
    mismatch["po"].update(subtotal="800.00", total="800.00")
    no_budget = deepcopy(correct)
    del no_budget["approved_budget"]
    return [("correct_unfamiliar_multi_item", correct), ("incorrect_subtotal", wrong),
            ("explicit_ceiling_exceeded", ceiling), ("aoq_po_price_mismatch", mismatch),
            ("missing_approved_budget", no_budget)]
