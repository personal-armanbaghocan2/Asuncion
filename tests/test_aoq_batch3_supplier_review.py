"""Synthetic supplier-attribution/save regressions; never imports app or db.

The actual AOQ save route and local helper dependencies are AST-loaded against
in-memory forms and query/write spies. Supplier quotation values are synthetic.
"""
import ast
import copy
import json
from pathlib import Path
import re
import sys
from types import SimpleNamespace
import unittest
from contextlib import contextmanager
from unittest.mock import patch
from flask import Flask, get_flashed_messages, session as flask_session
from werkzeug.exceptions import HTTPException
import durable_ocr_v0_1_0 as durable_ocr

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import aoq_flexible


class SyntheticForm(dict):
    def getlist(self, name):
        value = self.get(name, [])
        return value if isinstance(value, list) else [value]


class AOQBatch3SupplierReviewTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        tree = ast.parse((ROOT / "app.py").read_text(encoding="utf-8-sig"))
        definitions = {node.name: node for node in tree.body if isinstance(node, ast.FunctionDef)}
        supplied = {
            "load_aoq_preview", "save_aoq_preview", "q", "exec_db", "to_float", "flash", "redirect", "url_for",
            "next_document_no", "assign_item_identifiers", "now", "log", "clear_aoq_preview",
        }
        needed, pending = {}, ["aoq", "posted_aoq_suppliers", "posted_aoq_items", "_norm_text"]
        while pending:
            name = pending.pop()
            if name in needed or name in supplied or name not in definitions:
                continue
            node = copy.deepcopy(definitions[name])
            node.decorator_list = []
            needed[name] = node
            pending.extend(call.func.id for call in ast.walk(node)
                           if isinstance(call, ast.Call) and isinstance(call.func, ast.Name))
        cls.route_code = compile(ast.Module(body=list(needed.values()), type_ignores=[]), "app.py", "exec")

    @staticmethod
    def item(name="Chair", qty=2, unit="piece", cost=100, amount=200):
        return {
            "item": name, "description": name + " specification", "qty": qty, "unit": unit,
            "unit_cost": cost, "amount": amount, "remarks": "", "source": "Synthetic AOQ",
        }

    def preview(self):
        alpha, beta = self.item(), self.item("Desk", 4, "unit", 400, 1600)
        return {
            "pr_id": 71, "winner_index": 0, "raw_text": "", "offer_items": [alpha],
            "offer_supplier_index": "0", "offer_review_token": "token-alpha",
            "suppliers": [
                {"supplier": "Alpha Trading", "amount": 200, "remarks": "Recommended",
                 "is_winner": True, "items": [alpha]},
                {"supplier": "Beta Office", "amount": 1600, "remarks": "Quotation received",
                 "is_winner": False, "items": [beta]},
            ],
        }

    @staticmethod
    def item_form(item):
        return {"offer_" + key: [str(item[key])] for key in
                ("item", "description", "qty", "unit", "unit_cost", "amount", "remarks")}

    def reviewed_preview(self, index):
        preview = self.preview()
        preview["offer_supplier_index"] = str(index)
        preview["offer_review_token"] = "token-" + str(index)
        preview["offer_items"] = (copy.deepcopy(preview["suppliers"][index]["items"])
                                  if isinstance(index, int) else [])
        return preview

    def run_save(self, *, item=None, selected="0", source="0", token="token-alpha", custom="", preview=None,
                 action="save_ocr", form_extra=None):
        preview = copy.deepcopy(preview if preview is not None else self.preview())
        form = SyntheticForm({"action": action, "winner_index": selected, "winner_custom": custom})
        form.update(self.item_form(item if item is not None else self.item()))
        if source is not None:
            form["offer_supplier_index"] = source
            form["offer_review_token"] = token
        form.update(form_extra or {})
        events = {name: [] for name in ("queries", "writes", "messages", "numbers", "logs", "cleared", "previews")}

        def query(sql, params=(), one=False):
            events["queries"].append((sql, params, one))
            return {"id": 71, "no": "PR-SYNTHETIC", "status": "Approved"}

        namespace = {name: value for name, value in vars(aoq_flexible).items()
                     if callable(value) and not name.startswith("__")}
        namespace.update({
            "__builtins__": __builtins__, "json": json, "re": re,
            "request": SimpleNamespace(method="POST", form=form),
            "load_aoq_preview": lambda: preview, "q": query,
            "save_aoq_preview": lambda value: events["previews"].append(copy.deepcopy(value)),
            "secrets": SimpleNamespace(token_urlsafe=lambda _length: "new-review-token"),
            "exec_db": lambda *args: events["writes"].append(args),
            "to_float": lambda value: float(value or 0), "flash": events["messages"].append,
            "redirect": lambda target: target, "url_for": lambda endpoint, **_kwargs: endpoint,
            "next_document_no": lambda *args, **kwargs: events["numbers"].append(args) or "AOQ-SYNTHETIC",
            "assign_item_identifiers": lambda items, *_args: items, "now": lambda: "synthetic time",
            "log": lambda *args, **kwargs: events["logs"].append(args),
            "clear_aoq_preview": lambda **kwargs: events["cleared"].append(kwargs),
        })
        # Keep every original supplier/numeric fixture and assertion. The
        # application now delegates to its durable controller; use that actual
        # controller with a transaction spy, rather than the retired file writer.
        draft_id="00000000-0000-4000-8000-000000000001"
        form.update(draft_id=draft_id,draft_revision="1",confirm_reviewed="yes")
        preview.update(draft_id=draft_id,draft_revision=1,_status="REVIEW",_official_id=None)
        class Cursor:
            rowcount=1
            def __init__(self,row=None):self.row=row
            def fetchone(self):return self.row
        class Connection:
            def execute(self,sql,params=()):
                normalized=" ".join(sql.lower().split())
                events["queries"].append((sql,params,True))
                if normalized.startswith("select * from pr"):return Cursor({"id":71,"status":"Approved"})
                if normalized.startswith("select * from psms_documents.aoq_drafts"):return Cursor({"status":"REVIEW","revision":1,"official_aoq_id":None})
                if normalized.startswith("select 1 from aoq"):return Cursor()
                if normalized.startswith("insert into aoq"):
                    events["writes"].append((sql,params));return Cursor({"id":881})
                if "set payload=" in normalized:events["previews"].append(json.loads(params[0]))
                return Cursor()
        @contextmanager
        def transaction(_api):yield Connection(),1
        app=Flask("aoq-controller-unit");app.secret_key="synthetic-unit-only"
        app.add_url_rule("/aoq","aoq",lambda:"")
        app.add_url_rule("/aoq/<int:id>/view","aoq_review",lambda id:"")
        namespace["durable_ocr"]=durable_ocr
        exec(self.route_code,namespace)
        with app.test_request_context("/aoq",method="POST",data=form):
            flask_session.update(user_id=1,username="synthetic reviewer")
            with patch.object(durable_ocr,"aoq_load",return_value=preview), patch.object(durable_ocr,"transaction",transaction), patch.object(durable_ocr.secrets,"token_urlsafe",return_value="new-review-token"):
                try:
                    response=namespace["aoq"]()
                    self.assertEqual(response.status_code,302)
                except HTTPException as error:
                    self.assertIn(error.code,(400,409))
                    events["messages"].append(error.description)
            events["messages"].extend(get_flashed_messages())
        return events

    def assert_rejected(self, events):
        self.assertEqual(events["writes"], [], "Unmatched/incomplete rows must not reach AOQ insert")
        self.assertEqual(events["numbers"], [])
        self.assertEqual(events["logs"], [])
        self.assertEqual(events["cleared"], [])
        self.assertTrue(events["messages"], "A rejected save must explain the correction needed")

    def saved(self, events, winner):
        self.assertEqual(len(events["writes"]), 1, events["messages"])
        sql, params = events["writes"][0]
        self.assertIn("insert into aoq", sql.lower())
        columns=re.search(r"insert into aoq\((.*?)\)\s*values",sql,re.I|re.S).group(1).replace("\n","").split(",")
        tokens=re.search(r"values\((.*?)\)\s*returning",sql,re.I|re.S).group(1).split(",")
        supplied=iter(params)
        stored={column.strip():next(supplied) if "?" in token else token.strip().strip("'") for column,token in zip(columns,tokens)}
        self.assertEqual(stored["winner"],winner)
        self.assertEqual(stored["status"],"For Review")
        return json.loads(stored["suppliers"]),json.loads(stored["winning_offer"])

    def test_switching_to_beta_cannot_silently_save_alpha_rows(self):
        self.assert_rejected(self.run_save(selected="1"))

    def test_switching_to_beta_without_source_marker_rejects_alpha_rows(self):
        self.assert_rejected(self.run_save(selected="1", source=None))

    def test_forged_beta_marker_cannot_attribute_exact_alpha_quotation(self):
        self.assert_rejected(self.run_save(selected="1", source="1"))

    def test_stale_review_token_rejects_even_the_selected_supplier_rows(self):
        self.assert_rejected(self.run_save(token="stale-token"))

    def test_selected_beta_quotation_saves_beta_items_and_prices(self):
        beta = self.item("Desk", 4, "unit", 400, 1600)
        suppliers, offers = self.saved(self.run_save(item=beta, selected="1", source="1", token="token-1",
                                                    preview=self.reviewed_preview(1)), "Beta Office")
        self.assertEqual((offers[0]["item"], offers[0]["qty"], offers[0]["unit"],
                          offers[0]["unit_cost"], offers[0]["amount"]), ("Desk", 4, "unit", 400, 1600))
        self.assertEqual(suppliers[0]["items"], [self.item()])
        self.assertTrue(suppliers[1]["is_winner"])

    def test_same_supplier_review_can_correct_quotation_values(self):
        corrected = self.item(qty=3, cost=105, amount=315)
        corrected["description"] = "Chair corrected specification"
        _, offers = self.saved(self.run_save(item=corrected), "Alpha Trading")
        self.assertEqual((offers[0]["qty"], offers[0]["unit_cost"], offers[0]["amount"]), (3, 105, 315))

    def test_beta_missing_quantity_or_unit_requires_correction(self):
        for field in ("qty", "unit"):
            with self.subTest(field=field):
                preview = self.reviewed_preview(1)
                preview["suppliers"][1]["items"][0][field] = ""
                self.assert_rejected(self.run_save(item=preview["suppliers"][1]["items"][0],
                                                   selected="1", source="1", token="token-1", preview=preview))

    def test_beta_review_can_supply_missing_source_quantity_and_unit(self):
        preview = self.reviewed_preview(1)
        preview["suppliers"][1]["items"][0].update(qty="", unit="")
        corrected = self.item("Desk", 4, "piece", 400, 1600)
        _, offers = self.saved(self.run_save(item=corrected, selected="1", source="1", token="token-1", preview=preview),
                               "Beta Office")
        self.assertEqual((offers[0]["qty"], offers[0]["unit"]), (4, "piece"))

    def test_custom_winner_cannot_reuse_detected_alpha_rows_unchanged(self):
        self.assert_rejected(self.run_save(selected="-1", source="custom", token="token-custom",
                                          custom="Gamma Supplies", preview=self.reviewed_preview("custom")))

    def test_custom_winner_preserves_manually_entered_offer(self):
        manual = self.item("Cabinet", 3, "set", 500, 1500)
        _, offers = self.saved(self.run_save(item=manual, selected="-1", source="custom", token="token-custom",
                                             custom="Gamma Supplies", preview=self.reviewed_preview("custom")), "Gamma Supplies")
        self.assertEqual((offers[0]["item"], offers[0]["qty"], offers[0]["unit"]), ("Cabinet", 3, "set"))

    def test_reviewed_beta_context_allows_manual_correction(self):
        corrected = self.item("Desk", 5, "piece", 410, 2050)
        _, offers = self.saved(self.run_save(item=corrected, selected="1", source="1", token="token-1",
                                             preview=self.reviewed_preview(1)), "Beta Office")
        self.assertEqual((offers[0]["qty"], offers[0]["unit_cost"], offers[0]["amount"]), (5, 410, 2050))

    def test_nonwinning_unresolved_fields_round_trip_existing_json_structure(self):
        preview = self.preview()
        preview["suppliers"][1]["items"][0].update(qty="", unit="")
        suppliers, offers = self.saved(self.run_save(preview=preview), "Alpha Trading")
        self.assertEqual(suppliers[1]["items"], preview["suppliers"][1]["items"])
        self.assertEqual((suppliers[1]["items"][0]["qty"], suppliers[1]["items"][0]["unit"]), ("", ""))
        self.assertEqual(set(suppliers[1]), {"supplier", "amount", "remarks", "is_winner", "items"})
        self.assertEqual(set(offers[0]), {"item", "description", "qty", "unit", "unit_cost", "amount", "remarks"})

    def test_direct_post_missing_required_fields_is_rejected(self):
        for field in ("item", "description", "qty", "unit"):
            for value in ("", "   "):
                with self.subTest(field=field, value=value):
                    item = self.item()
                    item[field] = value
                    self.assert_rejected(self.run_save(item=item))
            with self.subTest(field=field, value="absent"):
                self.assert_rejected(self.run_save(form_extra={"offer_" + field: []}))

    def test_direct_post_malformed_numbers_is_rejected(self):
        for field in ("qty", "unit_cost", "amount"):
            for value in ("not numeric", "nan", "inf"):
                with self.subTest(field=field, value=value):
                    item = self.item()
                    item[field] = value
                    self.assert_rejected(self.run_save(item=item))

    def test_direct_post_explicit_zeros_are_not_substituted(self):
        for field in ("qty", "unit_cost", "amount"):
            with self.subTest(field=field):
                item = self.item()
                item[field] = 0
                # Batch 4's approved consistency rule keeps zero lines valid,
                # with another valid line preserving the existing positive total.
                item["amount"] = 0
                if field == "amount":
                    item["qty"] = 0
                other = self.item()
                rows = {"offer_" + key: [str(item[key]), str(other[key])] for key in
                        ("item", "description", "qty", "unit", "unit_cost", "amount", "remarks")}
                _, offers = self.saved(self.run_save(item=item, form_extra=rows), "Alpha Trading")
                self.assertEqual(offers[0][field], 0)

    def test_loading_beta_uses_its_quote_and_rotates_context_without_a_record_write(self):
        preview = self.preview()
        events = self.run_save(selected="1", action="review_supplier", preview=preview)
        self.assertEqual(events["writes"], [])
        self.assertEqual(events["numbers"], [])
        self.assertEqual(len(events["previews"]), 1)
        loaded = events["previews"][0]
        self.assertEqual(loaded["offer_items"], preview["suppliers"][1]["items"])
        self.assertEqual(loaded["offer_supplier_index"], "1")
        self.assertEqual(loaded["offer_review_token"], "new-review-token")
        self.assertEqual(loaded["suppliers"], preview["suppliers"])
        self.assertNotEqual(loaded["offer_review_token"], preview["offer_review_token"])
        beta = loaded["offer_items"][0]
        self.saved(self.run_save(item=beta, selected="1", source="1", token="new-review-token", preview=loaded),
                   "Beta Office")

    def test_loading_custom_winner_clears_detected_rows_for_manual_entry(self):
        events = self.run_save(selected="-1", custom="Gamma Supplies", action="review_supplier")
        self.assertEqual(events["writes"], [])
        loaded = events["previews"][0]
        self.assertEqual(loaded["offer_items"], [])
        self.assertEqual(loaded["offer_supplier_index"], "custom")
        self.assertEqual(loaded["offer_custom_winner"], "Gamma Supplies")

    def test_supplier_load_cannot_accept_an_invalid_index(self):
        events = self.run_save(selected="99", action="review_supplier")
        self.assert_rejected(events)
        self.assertEqual(events["previews"], [])

    def test_duplicate_supplier_names_cannot_overwrite_both_quotations_as_winners(self):
        self.assert_rejected(self.run_save(form_extra={"supplier_name": ["Alpha Trading", "Alpha Trading"]}))


if __name__ == "__main__":
    unittest.main()
