"""Native synthetic extraction + actual review template; no database or app import."""
import csv,io,sys,tempfile,unittest
from pathlib import Path
from jinja2 import Environment
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from aoq_flexible import build_preview,extract_document,allow_single_supplier_winner
from aoq_excel_fixtures import HEADERS,SUPPLIER,ITEM,SPEC,PR_SPEC,PR_ITEM,values,workbook

class AOQExcelReviewMappingTests(unittest.TestCase):
    def extract(self,content,extension='.xlsx',pr=None):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/('synthetic'+extension);path.write_bytes(content)
            return build_preview(extract_document(str(path)),[PR_ITEM] if pr is None else pr)

    def assert_quote(self,preview):
        self.assertEqual(preview['suppliers'][0]['supplier'],SUPPLIER)
        item=preview['offer_items'][0]
        self.assertEqual(tuple(item[k] for k in ('item','description','qty','unit','unit_cost','amount')),(ITEM,SPEC,2,'unit',23000,46000))
        self.assertNotIn('i5',item['description']);self.assertNotIn('Linked PR',item['source'])
        self.assertEqual(preview['confidence'],100)

    def test_actual_eight_column_mismatch_structure(self):
        preview=self.extract(workbook());self.assert_quote(preview)
        self.assertEqual(preview['winner_index'],-1);self.assertEqual(preview['offer_supplier_index'],'0')
        self.assertFalse(any(s['is_winner'] for s in preview['suppliers']))

    def test_neutral_sole_supplier_is_not_a_winner(self):
        preview=self.extract(workbook(rows=[HEADERS,values('Quotation received')]))
        self.assert_quote(preview);self.assertEqual(preview['winner_index'],-1)
        self.assertFalse(allow_single_supplier_winner(preview,preview['suppliers']))

    def test_pr_numeric_description_cannot_leak_into_aoq(self):
        self.assert_quote(self.extract(workbook(),pr=[dict(item=ITEM,description='2; unit; 35000',unit_cost=35000)]))

    def test_no_extraction_does_not_copy_pr_placeholders(self):
        preview=build_preview({'raw_text':'Unstructured AOQ title'},[PR_ITEM])
        self.assertEqual(preview['offer_items'],[]);self.assertEqual(preview['confidence'],0)

    def test_reordered_columns_and_unit_price_alias(self):
        order=[2,5,4,0,6,3,1,7];headers=HEADERS.copy();headers[5]='Unit-Price (PHP)'
        self.assert_quote(self.extract(workbook(rows=[[headers[i] for i in order],[values()[i] for i in order]])))

    def test_merged_title_metadata_and_footer_are_not_quotations(self):
        preview=self.extract(workbook(preamble=True,footer=True))
        self.assert_quote(preview);self.assertEqual(len(preview['suppliers']),1)
        self.assertEqual(len(preview['suppliers'][0]['items']),1)
        self.assertEqual(preview['winner_index'],-1) # Source says not yet awarded despite footer.

    def test_positive_source_evidence_can_nominate_supplier(self):
        preview=self.extract(workbook(rows=[HEADERS,values('Awarded')]))
        self.assert_quote(preview);self.assertEqual(preview['winner_index'],0)

    def test_multiple_unawarded_quotes_remain_separate(self):
        second=values('Quotation received');second[0]='Synthetic Beta Office';second[5:7]=[25000,50000]
        preview=self.extract(workbook(rows=[HEADERS,values(),second]))
        self.assertEqual(len(preview['suppliers']),2);self.assertEqual(preview['winner_index'],-1)
        self.assertEqual(preview['offer_items'],[]);self.assertEqual(preview['confidence'],100)
        self.assertEqual(preview['suppliers'][0]['items'][0]['description'],SPEC)
        self.assertEqual(preview['suppliers'][1]['items'][0]['unit_cost'],25000)

    def test_missing_numeric_fields_remain_blank(self):
        row=values();row[3:7]=[None,None,None,None]
        preview=self.extract(workbook(rows=[HEADERS,row]));item=preview['offer_items'][0]
        self.assertEqual(tuple(item[k] for k in ('qty','unit','unit_cost','amount')),('','','',''))
        self.assertEqual(preview['suppliers'][0]['amount'],'');self.assertLess(preview['confidence'],100)

    def test_explicit_source_zero_is_preserved_for_validation(self):
        row=values();row[3]=row[5]=row[6]=0
        item=self.extract(workbook(rows=[HEADERS,row]))['offer_items'][0]
        self.assertEqual((item['qty'],item['unit_cost'],item['amount']),(0,0,0))

    def test_absent_total_is_calculated_only_from_source_and_warned(self):
        row=values();row[6]=None;preview=self.extract(workbook(rows=[HEADERS,row]))
        self.assertEqual(preview['offer_items'][0]['amount'],46000)
        self.assertIn('calculated',' '.join(preview['warnings']));self.assertLess(preview['confidence'],100)

    def test_csv_and_xlsm_follow_same_mapping(self):
        stream=io.StringIO();csv.writer(stream).writerows([HEADERS,values()])
        self.assert_quote(self.extract(stream.getvalue().encode(),'.csv'))
        self.assert_quote(self.extract(workbook(),'.xlsm'))

    def test_two_level_supplier_column_layout(self):
        rows=[['Item','Description','Qty','Unit','Alpha Trading','Alpha Trading','Beta Office','Beta Office'],
              ['Item','Description','Qty','Unit','Unit Price','Amount','Unit Price','Amount'],
              [ITEM,SPEC,2,'unit',23000,46000,25000,50000]]
        preview=self.extract(workbook(rows=rows))
        self.assertEqual(len(preview['suppliers']),2);self.assertEqual(preview['winner_index'],-1)
        self.assertEqual([(s['supplier'],s['items'][0]['unit_cost'],s['items'][0]['amount']) for s in preview['suppliers']],
                         [('Alpha Trading',23000,46000),('Beta Office',25000,50000)])

if __name__=='__main__':unittest.main()
