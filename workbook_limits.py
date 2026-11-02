"""Shared finite worksheet budgets; never opens or changes operational files."""
import time

MAX_SHEETS = 32
MAX_ROWS = 50000
MAX_COLUMNS = 256
MAX_GRID_CELLS = 2000000
MAX_TOTAL_GRID_CELLS = 4000000
MAX_POPULATED_CELLS = 500000
MAX_TEXT_CHARACTERS = 16000000
PARSE_SECONDS = 20


def validate_dimensions(workbook):
    if len(workbook.worksheets) > MAX_SHEETS:
        raise ValueError('Workbook exceeds the worksheet limit.')
    cells = 0
    for sheet in workbook.worksheets:
        rows, columns = sheet.max_row or 0, sheet.max_column or 0
        if rows > MAX_ROWS or columns > MAX_COLUMNS or rows * columns > MAX_GRID_CELLS:
            raise ValueError('Worksheet exceeds the row, column or cell limit.')
        cells += rows * columns
    if cells > MAX_TOTAL_GRID_CELLS:
        raise ValueError('Workbook exceeds the combined cell limit.')


def parsing_budget():
    return dict(rows=0, cells=0, text=0, deadline=time.monotonic() + PARSE_SECONDS)


def bounded_rows(sheet, budget):
    for number, row in enumerate(sheet.iter_rows(values_only=True), 1):
        if time.monotonic() > budget['deadline']:
            raise ValueError('Workbook parsing exceeded the time limit.')
        if number > MAX_ROWS or len(row) > MAX_COLUMNS:
            raise ValueError('Worksheet exceeds the row or column limit.')
        budget['rows'] += 1
        budget['cells'] += sum(value is not None and value != '' for value in row)
        budget['text'] += sum(len(value) for value in row if isinstance(value, str))
        if (budget['rows'] > MAX_ROWS * MAX_SHEETS or budget['cells'] > MAX_POPULATED_CELLS
                or budget['text'] > MAX_TEXT_CHARACTERS):
            raise ValueError('Workbook exceeds the populated-cell or text limit.')
        yield row
