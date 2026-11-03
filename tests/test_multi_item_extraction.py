"""Fictional source-backed regressions for shared operational extraction code."""
from decimal import Decimal
from pathlib import Path
import json
import tempfile
import unittest
import zipfile
from unittest.mock import patch

from PIL import Image, ImageDraw, ImageFont
from openpyxl import Workbook
import fitz
import offline_ocr_mapper as mapper
from aoq_flexible import extract_document, build_preview

GENERAL = mapper.KEY_TO_LABEL['description']
BUDGET = mapper.KEY_TO_LABEL['budget']
HEADERS = ['Item No', 'Description', 'Specifications', 'Quantity', 'Unit', 'Unit Cost', 'Amount']
# Independently specified source values; never populated from predictions.
GROUND_TRUTH = [
    ['01', 'A4 Copy Paper', '80 gsm, 500 sheets per ream', '5', 'Ream', '245.50', '1227.50'],
    ['02', 'USB Flash Drive', '32 GB, USB 3.0', '3', 'Piece', '385.00', '1155.00'],
    ['03', 'Black Printer Ink', '70 mL bottle, pigment-based', '2', 'Bottle', '685.75', '1371.50'],
]
METADATA = '\n'.join([
    'Type of Project: NON-DBM | Fiscal Year: 2027 | Office: IT SERVICES (TEST)',
    'Procurement Mode: Shopping | Pre-Procurement Conference: No',
    'Start of Procurement Activity: November 2026 | End of Procurement Activity: December 2026',
    'Expected Delivery: January 2027 | Source of Funds: General Fund (GF)',
    'Attached Supporting Documents: Synthetic specifications | Remarks: Fictional review only',
])
METRICS = []


def expected_rows(count):
    rows = [list(row) for row in GROUND_TRUTH[:count]]
    for i in range(len(rows), count):
        rows.append([f'{i+1:02}', f'Fictional calibration item {i+1}', 'Synthetic specification', '2', 'Piece', '10.25', '20.50'])
    return rows


def mapped(document):
    # Parsing tests isolate deterministic source mapping. Installed-model tests
    # remain separate, with no change to the application's model requirement.
    with patch.object(mapper, 'load_local_model', return_value=None), patch.object(mapper, '_semantic_scores', return_value={}):
        return mapper.map_document(document)


def fixture_font(size):
    # Keep Windows fixtures unchanged; Linux validators supply DejaVu explicitly.
    for name in ('C:/Windows/Fonts/arial.ttf', 'DejaVuSans.ttf'):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    raise RuntimeError('A readable TrueType fixture font is required')


def scan_image(rows, *, grid=False, repeated=False, wrapped=False):
    font = fixture_font(24)
    image = Image.new('RGB', (2200, 525 + len(rows)*100), 'white')
    draw = ImageDraw.Draw(image)
    draw.text((35, 15), 'FICTIONAL PROCUREMENT TEST - NOT AN OFFICIAL DOCUMENT', font=font, fill='black')
    if repeated:
        draw.text((35, 50), 'FICTIONAL PROCUREMENT TEST - NOT AN OFFICIAL DOCUMENT', font=font, fill='black')
    for line, text in enumerate(METADATA.splitlines()):
        draw.text((35, 90+line*32), text, font=font, fill='black')
    xs = [25, 150, 500, 1120, 1320, 1520, 1800, 2160]
    source_total=sum((Decimal(row[6]) for row in rows if row[6]),Decimal('0'))
    table = [HEADERS] + rows + [['Grand Total', '', '', '', '', '', format(source_total,'.2f')]]
    for r, row in enumerate(table):
        y = 285 + r*100
        for c, value in enumerate(row):
            text = value
            if wrapped and c == 2 and r == 1:
                text = text.replace(', ', ',\n', 1)
            draw.multiline_text((xs[c]+8, y+12), text, font=font, fill='black', spacing=6)
    if grid:
        for x in xs: draw.line((x, 285, x, 285+len(table)*100), fill='black', width=2)
        for r in range(len(table)+1): draw.line((xs[0], 285+r*100, xs[-1], 285+r*100), fill='black', width=2)
    return image


def scanned_pdf(path, pages):
    import io
    with fitz.open() as document:
        for image in pages:
            stream = io.BytesIO(); image.save(stream, format='PNG')
            page = document.new_page(width=image.width/2.5, height=image.height/2.5)
            page.insert_image(page.rect, stream=stream.getvalue())
        document.save(path)


class MultiItemExtractionTests(unittest.TestCase):
    def assert_items(self, result, expected, case):
        actual = result['rows']; matches = {key: 0 for key in ('description', 'quantity', 'unit', 'unit_cost', 'amount')}
        for row, truth in zip(actual, expected):
            fields = row['fields']
            checks = dict(description=fields['Item Name'] == truth[1],
                quantity=Decimal(fields['Quantity'] or '-1') == Decimal(truth[3]),
                unit=False)
            from procurement_item_contract import normalized_unit
            checks['unit'] = fields['Unit'] == normalized_unit(truth[4])
            checks['unit_cost'] = (Decimal(fields['Estimated Unit Cost'] or '-1') == Decimal(truth[5])
                                   if truth[5] else fields['Estimated Unit Cost'] == '')
            checks['amount'] = (Decimal(fields[BUDGET] or '-1') == Decimal(truth[6])
                                if truth[6] else fields[BUDGET] == '')
            specification = fields[mapper.KEY_TO_LABEL['quantity_size']]
            self.assertIn(truth[2], specification, f'{case}: specification was lost or reordered: {specification}')
            evidence = row['field_confidence'].get('_source_evidence', {})
            from procurement_document_layout import header_role
            if truth[0] and any(header_role(label) == 'item_no' for label in evidence.get('headers', [])):
                self.assertEqual(evidence['item_number'], truth[0], f'{case}: source item number changed')
            for key, correct in checks.items(): matches[key] += int(correct)
        identities = [r['fields']['Item Name'] for r in actual]
        detected = sum(row[1] in identities for row in expected)
        metric = dict(case=case, expected=len(expected), actual=len(actual), missing=len(expected)-detected,
            false_extra=len(actual)-detected, correct=matches,
            extracted=[r['fields'] for r in actual],
            field_accuracy=sum(matches.values())/(5*len(expected)),
            precision=detected/len(actual) if actual else 0, recall=detected/len(expected))
        METRICS.append(metric)
        self.assertEqual(len(actual), len(expected), json.dumps(metric))
        self.assertTrue(all(value == len(expected) for value in matches.values()), json.dumps(metric))

    def test_known_two_item_cells_and_adjacent_shared_fields(self):
        result = mapped(dict(raw_text=METADATA, tables=[dict(source='Fictional cells', rows=[HEADERS]+expected_rows(2)+[['Grand Total','','','','','','2382.50']])]))
        self.assert_items(result, expected_rows(2), 'structured-known-two')
        for row in result['rows']:
            f = row['fields']
            for key, expected in dict(project_type='NON-DBM', procurement_mode='Shopping', pre_procurement='No',
                start_date='November 2026', end_date='December 2026', delivery_period='January 2027', fund_source='General Fund (GF)',
                supporting_documents='Synthetic specifications', remarks='Fictional review only').items():
                self.assertEqual(f[mapper.KEY_TO_LABEL[key]], expected, key)
        self.assertEqual(result['raw_text'], METADATA)

    def test_delimited_rows_keep_empty_cells_and_numeric_unknowns(self):
        rows = expected_rows(2); rows[1][5] = ''
        result = mapped(dict(raw_text='\n'.join(' | '.join(row) for row in [HEADERS]+rows)))
        self.assertEqual(len(result['rows']), 2)
        self.assertEqual(result['rows'][1]['fields']['Estimated Unit Cost'], '')
        self.assertEqual(result['rows'][1]['fields'][BUDGET], '1155.00')

    def test_delimited_rows_preserve_empty_first_and_last_columns(self):
        rows = expected_rows(2)
        rows[1][0] = ''
        rows[1][-1] = ''
        for separator in (' | ', '\t'):
            with self.subTest(separator=separator):
                result = mapped(dict(raw_text='\n'.join(separator.join(row) for row in [HEADERS]+rows)))
                self.assertEqual(len(result['rows']), 2)
                fields = result['rows'][1]['fields']
                self.assertEqual(fields['Item Name'], 'USB Flash Drive')
                self.assertEqual(fields['Quantity'], '3')
                self.assertEqual(fields['Estimated Unit Cost'], '385')
                self.assertEqual(fields[BUDGET], '')

    def test_reordered_repeated_table_headers_keep_each_item(self):
        first_headers = ['Item No', 'Description', 'Quantity', 'Unit', 'Unit Cost', 'Amount']
        second_headers = ['Item No', 'Quantity', 'Unit', 'Description', 'Specifications', 'Amount', 'Unit Cost']
        first = ['01', 'A4 Copy Paper', '5', 'Ream', '245.50', '1227.50']
        second = ['02', '3', 'Piece', 'USB Flash Drive', '32 GB, USB 3.0', '1155.00', '385.00']
        result = mapped(dict(raw_text='', tables=[dict(rows=[first_headers, first, second_headers, second])]))
        self.assertEqual(len(result['rows']), 2)
        for row, truth in zip(result['rows'], GROUND_TRUTH):
            self.assertEqual(row['fields']['Item Name'], truth[1])
            self.assertEqual(row['fields']['Quantity'], truth[3])
            self.assertEqual(Decimal(row['fields']['Estimated Unit Cost']), Decimal(truth[5]))
            self.assertEqual(Decimal(row['fields'][BUDGET]), Decimal(truth[6]))
        self.assertEqual(result['rows'][1]['field_confidence']['_source_evidence']['headers'], second_headers)

    def test_repeated_vertical_form_items_keep_their_own_values(self):
        rows = [['Procurement Mode', 'Shopping'], ['Source of Funds', 'General Fund']]
        for truth in GROUND_TRUTH[:2]:
            rows.extend([['Description', truth[1]], ['Specifications', truth[2]],
                         ['Quantity', truth[3]], ['Unit', truth[4]], ['Unit Cost', truth[5]], ['Amount', truth[6]]])
        result = mapped(dict(raw_text='', tables=[dict(source='Fictional vertical forms', rows=rows)]))
        self.assert_items(result, expected_rows(2), 'vertical-two')
        for row in result['rows']:
            self.assertEqual(row['fields'][mapper.KEY_TO_LABEL['procurement_mode']], 'Shopping')
            self.assertEqual(row['fields'][mapper.KEY_TO_LABEL['fund_source']], 'General Fund')

    def test_inline_labeled_text_items_keep_each_quantity_and_price(self):
        lines = ['Procurement Mode: Shopping', 'Source of Funds: General Fund']
        for truth in GROUND_TRUTH[:2]:
            lines.append(f'Item Name: {truth[1]} Quantity: {truth[3]} Unit: {truth[4]} Unit Cost: {truth[5]} Amount: {truth[6]}')
        result = mapped(dict(raw_text='\n'.join(lines)))
        self.assertEqual(len(result['rows']), 2)
        for row, truth in zip(result['rows'], GROUND_TRUTH):
            self.assertEqual(row['fields']['Item Name'], truth[1])
            self.assertEqual(row['fields']['Quantity'], truth[3])
            self.assertEqual(Decimal(row['fields']['Estimated Unit Cost']), Decimal(truth[5]))
            self.assertEqual(Decimal(row['fields'][BUDGET]), Decimal(truth[6]))

    def test_quantity_cell_with_unit_preserves_ordered_quantity(self):
        headers = ['Description', 'Qty', 'Amount']
        rows = [[truth[1], f'{truth[3]} {truth[4]}', truth[6]] for truth in GROUND_TRUTH[:2]]
        result = mapped(dict(raw_text='', tables=[dict(rows=[headers]+rows)]))
        from procurement_item_contract import normalized_unit
        self.assertEqual(len(result['rows']), 2)
        for row, truth in zip(result['rows'], GROUND_TRUTH):
            self.assertEqual(row['fields']['Quantity'], truth[3])
            self.assertEqual(row['fields']['Unit'], normalized_unit(truth[4]))
            self.assertEqual(row['fields']['Estimated Unit Cost'], '')

    def test_three_column_planning_table_searchable_and_scanned_pdf(self):
        from procurement_item_contract import normalized_unit
        rows = [['Description', 'Qty', 'Estimated Budget']]
        rows += [[truth[1], f'{truth[3]} {truth[4]}', truth[6]] for truth in GROUND_TRUTH[:2]]
        with tempfile.TemporaryDirectory(prefix='fictional-three-column-') as folder:
            for scanned in (False, True):
                with self.subTest(scanned=scanned):
                    path = Path(folder)/f'fictional-three-column-{scanned}.pdf'
                    if scanned:
                        image = Image.new('RGB', (1600, 500), 'white')
                        draw = ImageDraw.Draw(image)
                        font = fixture_font(30)
                        for r, row in enumerate(rows):
                            for x, value in zip((35, 650, 1100), row):
                                draw.text((x, 75+r*95), value, font=font, fill='black')
                        scanned_pdf(path, [image])
                    else:
                        with fitz.open() as document:
                            page = document.new_page(width=800, height=250)
                            for r, row in enumerate(rows):
                                for x, value in zip((18, 325, 550), row):
                                    page.insert_text((x, 50+r*40), value, fontsize=15)
                            document.save(path)
                    document = extract_document(str(path))
                    result = mapped(document)
                    expected = [['', truth[1], '', truth[3], truth[4], '', truth[6]] for truth in GROUND_TRUTH[:2]]
                    self.assert_items(result, expected, f'three-column-pdf-scanned-{scanned}')
                    self.assertEqual(len(result['rows']), 2)
                    for row, truth in zip(result['rows'], GROUND_TRUTH):
                        self.assertEqual(row['fields']['Item Name'], truth[1])
                        self.assertEqual(row['fields']['Quantity'], truth[3])
                        self.assertEqual(row['fields']['Unit'], normalized_unit(truth[4]))
                        self.assertEqual(Decimal(row['fields'][BUDGET]), Decimal(truth[6]))
                        self.assertEqual(row['fields']['Estimated Unit Cost'], '')

    def test_inline_metadata_without_pipe_does_not_absorb_adjacent_labels(self):
        text=METADATA.replace(' | ', ' ')
        result=mapped(dict(raw_text=text,tables=[dict(rows=[HEADERS]+expected_rows(2))]))
        for row in result['rows']:
            self.assertEqual(row['fields'][mapper.KEY_TO_LABEL['project_type']],'NON-DBM')
            self.assertEqual(row['fields'][mapper.KEY_TO_LABEL['delivery_period']],'January 2027')
            self.assertEqual(row['fields'][mapper.KEY_TO_LABEL['fund_source']],'General Fund (GF)')

    def test_uncertain_cell_stays_blank_with_item_specific_raw_evidence(self):
        from procurement_document_layout import positioned_table
        words=[]
        for row_index,row in enumerate([HEADERS]+expected_rows(2)):
            for col,text in enumerate(row):
                words.append(dict(text=text,left=10+col*400,top=10+row_index*40,width=150,height=15,
                    confidence=20 if row_index==1 and col==5 else 95))
        table=positioned_table(words,source='Fictional word boxes',page=2,coordinate_space='test image pixels')
        result=mapped(dict(raw_text='Fictional evidence',tables=[table]))
        self.assertEqual(len(result['rows']),2)
        self.assertEqual(result['rows'][0]['fields']['Estimated Unit Cost'],'')
        evidence=result['rows'][0]['field_confidence']['_source_evidence']
        self.assertEqual(evidence['page'],2)
        self.assertTrue(any(cell['raw_text']=='245.50' and cell['uncertain'] for cell in evidence['cells']))
        self.assertEqual(result['rows'][1]['fields']['Quantity'],'3')

    def test_description_only_uncertain_item_is_not_silently_merged(self):
        result=mapped(dict(raw_text='',tables=[dict(rows=[HEADERS,GROUND_TRUTH[0],['02','Fictional unreadable item','','','','','']])]))
        self.assertEqual(len(result['rows']),2)
        self.assertEqual(result['rows'][1]['fields']['Quantity'],'')

    def test_item_name_starting_with_total_is_not_a_summary(self):
        headers=['Description','Quantity','Unit','Unit Cost','Amount']
        result=mapped(dict(raw_text='',tables=[dict(rows=[headers,['Total Station','1','Set','100.00','100.00'],['Total Amount','','','','100.00']])]))
        self.assertEqual(len(result['rows']),1)
        self.assertEqual(result['rows'][0]['fields']['Item Name'],'Total Station')

    def test_totals_repeated_headers_wrapped_cells_and_conflicting_amount(self):
        rows = [HEADERS, expected_rows(1)[0], ['', '', 'wrapped continuation', '', '', '', ''], HEADERS,
                expected_rows(2)[1], ['Subtotal','','','','','','2382.50']]
        result = mapped(dict(raw_text='', tables=[dict(rows=rows)]))
        self.assertEqual(len(result['rows']), 2)
        self.assertIn('wrapped continuation', result['rows'][0]['fields'][mapper.KEY_TO_LABEL['quantity_size']])
        rows[1][-1] = '999.00'
        result = mapped(dict(raw_text='', tables=[dict(rows=rows)]))
        self.assertIn('differs', ' '.join(result['rows'][0]['warnings']))
        self.assertEqual(result['rows'][0]['fields'][BUDGET], '999.00')

    def test_excel_one_two_ten_multiple_sheets_and_formulas(self):
        with tempfile.TemporaryDirectory(prefix='fictional-ppmp-') as folder:
            for count in (1,2,5,10):
                wb = Workbook(); sheet = wb.active; sheet.title = 'Fictional A'
                sheet.append(['FICTIONAL WORKBOOK']); sheet.merge_cells('A1:G1'); sheet.append(HEADERS)
                truth = [GROUND_TRUTH[2]] if count == 1 else expected_rows(count)
                for row in truth: sheet.append(row); sheet.append([])
                sheet.append(['Grand Total']); path = Path(folder)/f'fictional-{count}.xlsx'; wb.save(path); wb.close()
                document = extract_document(str(path)); self.assert_items(mapped(document), truth, f'xlsx-{count}')
                self.assertTrue(mapped(document)['rows'][0]['field_confidence']['_table_source']['worksheet_row'])
            wb = Workbook(); wb.active.append(HEADERS); wb.active.append(GROUND_TRUTH[0]);
            other = wb.create_sheet('Fictional B'); other.append(HEADERS); other.append(GROUND_TRUTH[1]); other['F2'] = '=10+20'
            path = Path(folder)/'fictional-formula.xlsx'; wb.save(path); wb.close()
            document = extract_document(str(path)); result = mapped(document)
            self.assertEqual(len(result['rows']), 2)
            self.assertEqual(result['rows'][1]['fields']['Estimated Unit Cost'], '')
            self.assertTrue(any('no usable cached' in warning for warning in document['warnings']))

    def test_excel_formula_cache_retained_without_evaluation(self):
        with tempfile.TemporaryDirectory(prefix='fictional-cache-') as folder:
            path=Path(folder)/'fictional-cached.xlsx';wb=Workbook();wb.active.append(HEADERS);wb.active.append(GROUND_TRUTH[2]);wb.active['F2']='=600+85.75';wb.save(path);wb.close()
            with zipfile.ZipFile(path) as archive:parts={name:archive.read(name) for name in archive.namelist()}
            parts['xl/worksheets/sheet1.xml']=parts['xl/worksheets/sheet1.xml'].replace(b'<f>600+85.75</f><v></v>',b'<f>600+85.75</f><v>685.75</v>')
            with zipfile.ZipFile(path,'w') as archive:
                for name,content in parts.items():archive.writestr(name,content)
            document=extract_document(str(path));self.assert_items(mapped(document),[GROUND_TRUTH[2]],'xlsx-cached-formula')
            self.assertTrue(any('freshness' in warning for warning in document['warnings']))

    def test_searchable_pdf_borderless_multiple_items(self):
        with tempfile.TemporaryDirectory(prefix='fictional-ppmp-') as folder:
            path = Path(folder)/'fictional-native.pdf'
            with fitz.open() as document:
                page = document.new_page(width=1100, height=450)
                xs = [15, 90, 260, 570, 680, 780, 920]
                for r, row in enumerate([HEADERS]+expected_rows(2)):
                    for x, value in zip(xs, row): page.insert_text((x, 50+r*35), value, fontsize=10)
                document.save(path)
            doc = extract_document(str(path)); self.assertIn('native PDF', doc['extraction_method'])
            self.assert_items(mapped(doc), expected_rows(2), 'native-pdf-two')

    def test_scanned_pdf_one_two_ten_multipage_and_grid(self):
        with tempfile.TemporaryDirectory(prefix='fictional-ppmp-') as folder:
            for count, grid, pages, repeated, wrapped in ((1,False,False,False,False),(2,False,False,True,True),
                    (5,False,False,False,False),(10,False,False,False,False),(2,True,False,False,False),(2,False,True,True,False)):
                truth = expected_rows(count)
                images = [scan_image([row], repeated=repeated) for row in truth] if pages else [scan_image(truth, grid=grid, repeated=repeated, wrapped=wrapped)]
                path = Path(folder)/f'fictional-scan-{count}-{grid}-{pages}.pdf'; scanned_pdf(path, images)
                document = extract_document(str(path)); result = mapped(document)
                self.assert_items(result, truth, path.stem)
                self.assertIn('--- Page 1 ---', document['raw_text'])
                self.assertTrue(result['rows'][0]['field_confidence']['_source_evidence']['cells'])
                if repeated: self.assertGreaterEqual(document['raw_text'].count('FICTIONAL PROCUREMENT TEST'),2)

    def test_aoq_two_items_three_suppliers_remain_six_quotations(self):
        table = [['Supplier','Item No','Item','Description','Qty','Unit','Unit Price','Amount','Remarks']]
        for supplier in ('Fictional Alpha','Fictional Beta','Fictional Gamma'):
            for truth in GROUND_TRUTH[:2]:
                table.append([supplier, truth[0], truth[1], truth[2], truth[3], truth[4],truth[5],truth[6],
                              'Awarded' if supplier == 'Fictional Beta' else ''])
        preview = build_preview(dict(raw_text='',tables=[dict(source='Fictional quotations',rows=table)],warnings=[]), [])
        self.assertEqual(len(preview['suppliers']),3)
        self.assertEqual(preview['procurement_item_count'],2)
        self.assertEqual(preview['quotation_count'],6)
        self.assertTrue(all(len(supplier['items'])==2 for supplier in preview['suppliers']))
        self.assertEqual(preview['suppliers'][preview['winner_index']]['supplier'], 'Fictional Beta')

    def test_native_aoq_row_award_keeps_supplier_scope_and_outside_negation(self):
        headers=['Supplier','Item No','Item','Description','Qty','Unit','Unit Price','Amount','Remarks']
        rows=[]
        for supplier in ('Fictional Alpha','Fictional Beta','Fictional Gamma'):
            for truth in GROUND_TRUTH[:2]:rows.append([supplier,truth[0],truth[1],truth[2],*truth[3:7],
                'Awarded' if supplier=='Fictional Beta' else ''])
        with tempfile.TemporaryDirectory(prefix='fictional-aoq-native-') as folder:
            path=Path(folder)/'fictional-aoq.pdf'
            with fitz.open() as document:
                page=document.new_page(width=1400,height=450)
                for r,row in enumerate([headers]+rows):
                    for x,value in zip([25,160,230,430,800,860,940,1060,1190],row):page.insert_text((x,65+r*40),value,fontsize=10)
                document.save(path)
            source=extract_document(str(path));preview=build_preview(source,[])
            self.assertEqual((preview['procurement_item_count'],preview['quotation_count']),(2,6))
            self.assertEqual(preview['suppliers'][preview['winner_index']]['supplier'],'Fictional Beta')
            self.assertIn('Awarded',source['raw_text'])
            with fitz.open(path) as document:
                document[0].insert_text((25,420),'Remarks: Not recommended',fontsize=10)
                conflicting=Path(folder)/'fictional-conflict.pdf';document.save(conflicting)
            self.assertEqual(build_preview(extract_document(str(conflicting)),[])['winner_index'],-1)


if __name__ == '__main__': unittest.main()
