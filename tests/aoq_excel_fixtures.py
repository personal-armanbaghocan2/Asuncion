"""Synthetic quotation fixtures; deliberately different from the linked PR."""
from io import BytesIO
from openpyxl import Workbook

SUPPLIER = 'TechWorld Computer Supplies'
ITEM = 'Desktop Computer Set'
SPEC = 'Desktop Computer, Intel Core i3 10th Gen, 4GB RAM, 256GB SSD, with 18.5-inch LED Monitor, keyboard and mouse included'
PR_SPEC = 'Desktop Computer, Intel Core i5 12th Gen, 8GB RAM, 512GB SSD, with 19-inch LED Monitor, keyboard and mouse included'
HEADERS = ['Supplier', 'Item', 'Description', 'Quantity', 'Unit', 'Unit Cost', 'Amount', 'Remarks']
PR_ITEM = dict(item=ITEM, description=PR_SPEC, qty=2, unit='unit', unit_cost=35000, amount=70000)

def values(remarks='For quotation review - not yet awarded'):
    return [SUPPLIER, ITEM, SPEC, 2, 'unit', 23000, 46000, remarks]

def workbook(*, rows=None, preamble=False, footer=False):
    book=Workbook();sheet=book.active;sheet.title='AOQ'
    if preamble:
        sheet.append(['ABSTRACT OF QUOTATIONS']);sheet.merge_cells('A1:H1')
        sheet.append(['PR Number','SYNTHETIC-PR'])
        sheet.append(['Required Specification',PR_SPEC])
    for row in rows or [HEADERS, values()]:sheet.append(row)
    if footer:
        sheet.append(['Winning Supplier',SUPPLIER]);sheet.append(['Winning Amount',46000])
        sheet.append(['Reason','Source-backed award']);sheet.append(['Status','Awarded'])
    stream=BytesIO();book.save(stream);book.close();return stream.getvalue()
