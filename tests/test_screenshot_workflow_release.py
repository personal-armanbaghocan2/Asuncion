"""Synthetic quantity/source-binding regressions; no application startup, models or database."""
import copy
import unittest
import procurement_comparison_v1 as evidence
import revised_procurement_v1 as workflow

def displayed(pr_qty=10,po_qty=10,pr_name='Fictional mouse',po_name='Fictional mouse',pr_unit='pcs',po_unit='pcs',spec='2.4 GHz USB receiver',result='Acceptable',stale=False):
    left=[dict(item=pr_name,description=spec,qty=pr_qty,unit=pr_unit)]
    right=[dict(item=po_name,description=spec,qty=po_qty,unit=po_unit)]
    technical=evidence.compare(left,right)
    record=dict(result=result,technical_status=technical['technical_status'],details={'technical_comparison':technical})
    before=copy.deepcopy(record)
    display=workflow.verification_display(record,left,right,stale=stale)
    assert record==before
    return display

class QuantityCalculations(unittest.TestCase):
    def test_equal_decimal_quantities_calculate_zero(self):
        row=displayed('10','10.0')['pairs'][0]
        self.assertEqual(row['quantity_percentage'],'0.00%')
        self.assertIn('/ 10 x 100 = 0.00%',row['quantity_formula'])
    def test_deviation_uses_requested_quantity_not_confidence_or_binary_incidence(self):
        self.assertEqual(displayed(10,8)['pairs'][0]['quantity_percentage'],'20.00%')
        self.assertEqual(displayed(10,30)['pairs'][0]['quantity_percentage'],'200.00%')
    def test_model_disagreement_keeps_measured_zero_but_blocks_eligibility(self):
        display=displayed(result='Needs Recanvass')
        self.assertEqual(display['percentage'],'0.00%')
        self.assertEqual(display['pairs'][0]['quantity_percentage'],'0.00%')
        self.assertNotEqual(display['eligibility_status'],'Match')
    def test_stale_missing_units_and_unrelated_identity_never_get_zero(self):
        for kwargs in ({'stale':True},{'pr_qty':None},{'pr_qty':0},{'po_unit':'ream'}, {'po_name':'Fictional printer'}):
            with self.subTest(kwargs=kwargs):self.assertIsNone(displayed(**kwargs)['pairs'][0]['quantity_percentage'])
    def test_model_recanvass_does_not_erase_measured_zero_or_authorize_receiving(self):
        value=displayed(result='Needs Recanvass')
        self.assertEqual(value['percentage'],'0.00%')
        self.assertEqual(value['pairs'][0]['quantity_percentage'],'0.00%')
        self.assertNotEqual(value['eligibility_status'],'Match')

class SourceBinding(unittest.TestCase):
    def test_malformed_source_references_do_not_get_a_quantity_percentage(self):
        item=dict(item='Fictional mouse',description='2.4 GHz USB receiver',qty=10,unit='pcs')
        for key in ('pr_reference','po_reference'):
            technical=evidence.compare([item],[item]);technical['rows'][0][key]='FOREIGN-REFERENCE'
            row=dict(result='Acceptable',technical_status='MATCH',details={'technical_comparison':technical})
            display=workflow.verification_display(row,[item],[item])
            self.assertEqual(display['percentage'],'Not calculated')
            self.assertIsNone(display['pairs'][0]['quantity_percentage'])

if __name__ == "__main__":
    unittest.main()
