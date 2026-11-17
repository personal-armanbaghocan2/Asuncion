"""Synthetic wording-policy regressions; no model, DB or network access."""
from copy import deepcopy
import unittest

import procurement_comparison_v1 as comparison
from revised_procurement_v1 import discrepancy_incidence


NAME='Wireless Optical Mouse, 2.4 GHz, USB Receiver'
REQUEST=('wireless optical mouse, 2.4 ghz, usb receiver 10 pcs; '
         '10 wireless optical mouse; 2.4 ghz connection; usb receiver; '
         'plug-and-play; compatible with standard office computers')


def item(description,qty=10,unit='pcs',**extra):
    return dict(item=NAME,description=description,qty=qty,unit=unit,**extra)


class CoreDescriptionWording(unittest.TestCase):
    def compare(self,request=REQUEST,offer=NAME,**changes):
        left=[item(request)];right=[item(offer,**changes)]
        before=deepcopy((left,right))
        result=comparison.compare(left,right)
        self.assertEqual((left,right),before)
        return result

    def test_exact_screenshot_has_zero_structured_discrepancy(self):
        result=self.compare(qty=10.0)
        self.assertEqual(result['technical_status'],'MATCH')
        self.assertEqual(discrepancy_incidence(result)['value'],0)
        wording=next(c for c in result['rows'][0]['checks'] if c['attribute']=='source specification wording')
        self.assertEqual(wording['requested'],REQUEST)
        self.assertEqual(wording['offered'],NAME.casefold())
        self.assertEqual(result['version'],'PR_PO_REVIEW_V1')
        self.assertEqual(result['wording_policy'],'CORE_DESCRIPTION_V2')
        self.assertFalse(result['automatic_approval'])

    def test_reordered_duplicate_clauses_and_unit_alias_are_equivalent(self):
        result=self.compare(offer='USB receiver; 2.40 GHz connection; wireless optical mouse; USB receiver',unit='pieces')
        self.assertEqual(result['technical_status'],'MATCH')

    def test_actual_frequency_changes_never_match(self):
        for value in ('20 GHz','2.0 GHz','5 GHz'):
            with self.subTest(value=value):
                result=self.compare(offer=NAME.replace('2.4 GHz',value))
                self.assertNotEqual(result['technical_status'],'MATCH')
                self.assertTrue(any(c['state']=='MISMATCH' for c in result['rows'][0]['checks']))

    def test_receiver_cannot_be_omitted_or_replaced(self):
        for offer in ('Wireless Optical Mouse, 2.4 GHz','Wireless Optical Mouse, 2.4 GHz, Bluetooth'):
            with self.subTest(offer=offer):self.assertNotEqual(self.compare(offer=offer)['technical_status'],'MATCH')

    def test_explicit_platform_driver_and_mandatory_requirements_remain(self):
        for clause in ('compatible with Linux','custom driver required','must be plug-and-play',
                       'plug-and-play required','not plug-and-play','not compatible with standard office computers',
                       'compatible with standard office computers running Linux','requires no driver',
                       'brand: SpecificBrand','model: ABC123','1 TB storage','minimum 1600 dpi'):
            with self.subTest(clause=clause):
                self.assertNotEqual(self.compare(request=REQUEST+'; '+clause)['technical_status'],'MATCH')

    def test_offer_custom_driver_or_negation_is_not_silently_ignored(self):
        for clause in ('custom driver required','not plug-and-play','without usb receiver'):
            with self.subTest(clause=clause):
                self.assertNotEqual(self.compare(offer=NAME+'; '+clause)['technical_status'],'MATCH')

    def test_conflicting_embedded_quantity_and_structured_differences_remain(self):
        self.assertNotEqual(self.compare(request=REQUEST.replace('10 pcs','20 pcs'))['technical_status'],'MATCH')
        self.assertNotEqual(self.compare(request=REQUEST.replace('10 wireless','20 wireless'))['technical_status'],'MATCH')
        self.assertNotEqual(self.compare(qty=20)['technical_status'],'MATCH')
        self.assertNotEqual(self.compare(unit='set')['technical_status'],'MATCH')

    def test_other_products_do_not_inherit_mouse_boilerplate_policy(self):
        left=[dict(item='USB drive',description='32 GB USB 3.0; plug-and-play',qty=1,unit='pcs')]
        right=[dict(item='USB drive',description='32 GB USB 3.0',qty=1,unit='pcs')]
        self.assertNotEqual(comparison.compare(left,right)['technical_status'],'MATCH')
        right[0]['description']='16 GB USB 3.0'
        self.assertNotEqual(comparison.compare(left,right)['technical_status'],'MATCH')


if __name__=='__main__':unittest.main()
