"""Offline extraction/review contracts without importing the Flask application.

Render the actual AOQ offer-row template and AST-load only its posted-item
helpers against synthetic forms. No application import or database is needed.
"""
import ast
from decimal import Decimal, InvalidOperation
from html.parser import HTMLParser
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest

from jinja2 import Environment

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from aoq_flexible import _pr_fallback_items, build_preview


class SyntheticForm(dict):
    def getlist(self, name):
        value = self.get(name, [])
        return value if isinstance(value, list) else [value]


class InputCollector(HTMLParser):
    def __init__(self):
        super().__init__()
        self.fields = {}

    def handle_starttag(self, tag, attrs):
        fields = dict(attrs)
        if tag == "input" and fields.get("name"):
            self.fields[fields["name"]] = fields


class AOQMissingReviewContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        template = (ROOT / "templates" / "aoq.html").read_text(encoding="utf-8-sig")
        start = template.index("{% for item in ocr_data.offer_items %}")
        # The offer row also contains a category loop. Match the outer loop,
        # rather than cutting the template at the first nested endfor.
        import re
        depth=0
        for token in re.finditer(r'{%\s*(for\b.*?|endfor)\s*%}',template[start:]):
            depth += -1 if token.group(1)=='endfor' else 1
            if depth==0:
                end=start+token.end()
                break
        tree = ast.parse((ROOT / "app.py").read_text(encoding="utf-8-sig"))
        quantity = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == 'quantity_text')
        namespace = dict(Decimal=Decimal, InvalidOperation=InvalidOperation)
        exec(compile(ast.Module(body=[quantity], type_ignores=[]), 'app.py (quantity display)', 'exec'), namespace)
        environment = Environment(autoescape=True)
        environment.filters['quantity'] = namespace['quantity_text']
        cls.offer_template = environment.from_string(template[start:end])
        names = {"_posted_list", "posted_aoq_items"}
        functions = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name in names]
        if {node.name for node in functions} != names:
            raise AssertionError("AOQ posted-item review helpers are missing")
        cls.posted_code = compile(ast.Module(body=functions, type_ignores=[]), "app.py", "exec")

    @staticmethod
    def preview(qty="", unit=""):
        return build_preview({"tables": [{"source": "Synthetic AOQ", "rows": [
            ["Supplier", "Item", "Description", "Quantity", "Unit", "Unit Price", "Amount"],
            ["Alpha Trading", "Bond paper", "A4 paper", qty, unit, 25, 50],
        ]}]}, [])

    def rendered_inputs(self, preview):
        collector = InputCollector()
        collector.feed(self.offer_template.render(ocr_data=preview))
        return collector.fields

    def test_missing_quantity_renders_blank_required_review_input(self):
        preview = self.preview(unit="ream")
        self.assertEqual(preview["offer_items"][0]["qty"], "")
        fields = self.rendered_inputs(preview)
        self.assertEqual(fields["offer_qty"]["value"], "")
        self.assertIn("required", fields["offer_qty"])
        self.assertTrue(preview["warnings"])

    def test_missing_unit_renders_blank_required_review_input(self):
        preview = self.preview(qty=2)
        self.assertEqual(preview["offer_items"][0]["unit"], "")
        fields = self.rendered_inputs(preview)
        self.assertEqual(fields["offer_unit"]["value"], "")
        self.assertIn("required", fields["offer_unit"])
        self.assertTrue(preview["warnings"])

    def test_explicit_zero_remains_source_zero_in_preview(self):
        preview = self.preview(qty=0, unit="ream")
        item = preview["offer_items"][0]
        self.assertEqual((item["qty"], item["unit"], item["unit_cost"], item["amount"]), (0.0, "ream", 25.0, 50.0))
        self.assertEqual(float(self.rendered_inputs(preview)["offer_qty"]["value"]), 0.0)

    def test_completed_review_uses_existing_saved_item_structure(self):
        preview = self.preview()
        source_item = preview["offer_items"][0]
        form = SyntheticForm({
            "offer_item": [source_item["item"]],
            "offer_description": [source_item["description"]],
            "offer_qty": ["2.5"],
            "offer_unit": ["ream"],
            "offer_unit_cost": ["25.00"],
            "offer_amount": ["62.50"],
            "offer_remarks": ["Reviewed against original AOQ"],
            "source": [source_item["source"]],
            "_missing_fields": ["qty", "unit"],
        })
        namespace = {
            "request": SimpleNamespace(form=form),
            "to_float": lambda value: float(value or 0),
        }
        exec(self.posted_code, namespace)
        self.assertEqual(namespace["posted_aoq_items"](), [{
            "item": "Bond paper", "description": "A4 paper", "qty": 2.5,
            "unit": "ream", "unit_cost": 25.0, "amount": 62.5,
            "remarks": "Reviewed against original AOQ",
        }])
        self.assertNotIn("_missing_fields", source_item)

    def test_pr_placeholder_missing_values_remain_unresolved_and_labelled(self):
        item = _pr_fallback_items([{"item": "Bond paper", "description": "A4 paper"}])[0]
        self.assertEqual((item["qty"], item["unit"], item["unit_cost"], item["amount"]), ("", "", 0.0, 0.0))
        self.assertEqual(item["source"], "Linked PR fallback")
        self.assertRegex(item["remarks"].lower(), r"suggest")
        preview = build_preview({}, [{"item": "Bond paper", "description": "A4 paper"}])
        self.assertEqual(preview["offer_items"], [])
        self.assertRegex(" ".join(preview["warnings"]).lower(), r"linked pr")

    def test_pr_placeholder_preserves_explicit_zero_without_inventing_unit(self):
        item = _pr_fallback_items([{"item": "Bond paper", "qty": 0, "unit": ""}])[0]
        self.assertEqual((item["qty"], item["unit"]), (0.0, ""))
        self.assertEqual(item["source"], "Linked PR fallback")


if __name__ == "__main__":
    unittest.main()
