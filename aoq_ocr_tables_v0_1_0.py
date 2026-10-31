"""Inactive local OCR candidate for clearly ruled AOQ tables.

Geometry, not token order, owns cell assignment. No Flask integration, winner
selection, semantic model, file I/O or cloud service. Unruled/ambiguous layouts
remain unsupported. Raw OCR cell evidence is retained separately from numeric
cells withheld for low confidence. Callers must retain manual source review.
"""
from bisect import bisect_right
import math

import numpy as np
from PIL import ImageDraw, ImageOps
import pytesseract

from aoq_flexible import _configure_tesseract, _map_header, _table_text

VERSION = '0.1.0'


def _centres(indices):
    groups = []
    for index in indices:
        index = int(index)
        if not groups or index > groups[-1][-1] + 1:
            groups.append([index])
        else:
            groups[-1].append(index)
    return [int(round(sum(group) / len(group))) for group in groups]


def detect_grid(image):
    """Return only an explicit, bounded grid; do not guess missing columns."""
    if image.width * image.height > 16_000_000 or min(image.size) < 50:
        return None
    ink = np.asarray(image.convert('L')) < 150
    ys = _centres(np.flatnonzero(ink.mean(axis=1) >= 0.60))
    if len(ys) < 3:
        return None
    xs = _centres(np.flatnonzero(ink[ys[0]:ys[-1] + 1].mean(axis=0) >= 0.65))
    if not 6 <= len(xs) <= 31 or len(ys) > 202:
        return None
    if min(b-a for a, b in zip(xs, xs[1:])) < 20 or min(b-a for a, b in zip(ys, ys[1:])) < 12:
        return None
    return xs, ys


def cells_from_words(data, xs, ys, min_confidence=55):
    """Map OCR boxes to physical cells, leaving cross-border boxes unresolved."""
    cells = [[[] for _ in xs[:-1]] for _ in ys[:-1]]
    uncertain = set()
    warnings = []
    for i, value in enumerate(data.get('text', [])):
        text = str(value or '').strip()
        if not text or not any(c.isalnum() or c == '?' for c in text):
            continue
        left, top = int(data['left'][i]), int(data['top'][i])
        right, bottom = left + int(data['width'][i]), top + int(data['height'][i])
        col = bisect_right(xs, (left + right) / 2) - 1
        row = bisect_right(ys, (top + bottom) / 2) - 1
        if not 0 <= row < len(cells) or not 0 <= col < len(cells[0]):
            continue
        try:
            confidence = float(data['conf'][i])
        except (ValueError, TypeError, KeyError):
            confidence = -1
        if not math.isfinite(confidence):
            confidence = -1
        if left < xs[col] - 2 or right > xs[col+1] + 2 or top < ys[row] - 2 or bottom > ys[row+1] + 2:
            # Never relocate a straddling token to make a row look complete.
            uncertain.add((row, col))
            for other in range(len(cells[0])):
                if left < xs[other+1] and right > xs[other]:
                    uncertain.add((row, other))
            warnings.append(f'OCR word crosses a cell boundary at row {row+1}, column {col+1}; verify the source.')
        if confidence < min_confidence:
            uncertain.add((row, col))
        cells[row][col].append((top, left, text, confidence))
    rows, evidence = [], []
    for r, line in enumerate(cells):
        values = []
        for c, words in enumerate(line):
            words.sort()
            text = ' '.join(word[2] for word in words)
            values.append(text)
            if words:
                evidence.append({'row': r+1, 'column': c+1, 'raw_text': text,
                                 'minimum_word_confidence': min(word[3] for word in words),
                                 'uncertain': (r, c) in uncertain})
        rows.append(values)
    # Header ownership stays tied to the physical column. Missing OCR values
    # remain blank; a later bidder's price is never shifted left into the gap.
    numeric_columns = {c for line in rows[:3] for c, text in enumerate(line)
                       if _map_header(text) in {'qty', 'unit_cost', 'amount'}}
    for row, col in sorted(uncertain):
        if col in numeric_columns:
            rows[row][col] = ''
            warnings.append(f'Uncertain numeric OCR cell at row {row+1}, column {col+1} was left blank; raw OCR evidence is retained.')
        else:
            warnings.append(f'Uncertain OCR cell at row {row+1}, column {col+1} requires manual verification.')
    return rows, evidence, list(dict.fromkeys(warnings))


def extract_ruled_table(image, *, source='Local ruled-table OCR candidate', min_confidence=55):
    """Return an extract_document-shaped candidate payload, never an approval."""
    image = ImageOps.exif_transpose(image).convert('RGB')
    grid = detect_grid(image)
    result = {'candidate_version': VERSION, 'integrated': False, 'authoritative': False,
              'source_type': 'Ruled image table (experimental)', 'extraction_method': 'Local Tesseract with physical cell assignment',
              'tables': [], 'raw_text': '', 'warnings': [], 'ocr_cells': []}
    if grid is None:
        result['warnings'] = ['No unambiguous bounded table grid detected. This candidate does not support the layout; manual review is required.']
        return result
    xs, ys = grid
    cleaned = image.copy()
    draw = ImageDraw.Draw(cleaned)
    for x in xs:
        draw.line((x, ys[0], x, ys[-1]), fill='white', width=5)
    for y in ys:
        draw.line((xs[0], y, xs[-1], y), fill='white', width=5)
    _configure_tesseract()
    data = pytesseract.image_to_data(cleaned, config='--oem 3 --psm 6', output_type=pytesseract.Output.DICT)
    rows, evidence, warnings = cells_from_words(data, xs, ys, min_confidence)
    result.update(tables=[{'source': source, 'rows': rows}], raw_text=_table_text(rows),
                  warnings=warnings, ocr_cells=evidence)
    return result
