from flask import Flask, render_template, request, redirect, url_for, flash, send_from_directory, Response, abort, jsonify
import os, json, datetime, random, re, shutil, csv, io, secrets
from decimal import Decimal, InvalidOperation
import click
from private_workbooks import serialize_workbook, atomic_save, WorkbookBusy, WorkbookStorageUnavailable
from durable_workbooks import Unavailable as PersistentWorkbookUnavailable, protected as protect_workbook_request
import durable_ocr_v0_1_0 as durable_ocr
import ppmp_creation
from runtime_configuration import configure as configure_runtime
import server_sessions_v0_1_0 as server_sessions
from web_security_v0_1_0 import init_csrf
from openpyxl import load_workbook
from flask import session, g
from werkzeug.utils import secure_filename
from werkzeug.security import generate_password_hash, check_password_hash
from PIL import Image, ImageOps, ImageFilter
import pytesseract
from db import connect_db, database_url, apply_schema, ping as db_ping
from aoq_flexible import (
    SUPPORTED_EXTENSIONS as FLEXIBLE_AOQ_EXTENSIONS,
    extract_document as extract_aoq_document,
    build_preview as build_flexible_aoq_preview,
    allow_single_supplier_winner,
)
from inventory_dataset import (
    DatasetCommitUncertain,
    ensure_schema as ensure_inventory_dataset_schema,
    sync_workbook as sync_inventory_dataset_workbook,
    get_status as get_inventory_dataset_status,
)
from offline_ocr_mapper import (
    map_document as map_ppmp_ocr_document,
    model_status as ocr_mapper_model_status,
    validate_record as validate_ocr_ppmp_record,
    PPMP_FIELDS as OCR_PPMP_FIELDS,
)

BASE_DIR = os.path.abspath(os.path.dirname(__file__))
DATABASE_URL = database_url()
DATA_DIR = os.path.abspath(os.environ.get('PSMS_DATA_DIR', os.path.join(BASE_DIR, 'data')))
EXCEL_PATH = os.path.join(DATA_DIR, 'PPMP_Data.xlsx')
COA_DATASET_PATH = os.path.join(DATA_DIR, 'COA_Inventory_Dataset.xlsx')
ALLOWED_DATASET_EXTENSIONS = {'.xlsx', '.xlsm'}
NLP_FASTTEXT_DIR = os.path.join(BASE_DIR, 'model', 'fasttext_nlp')
AOQ_UPLOAD_DIR = os.path.join(BASE_DIR, 'uploads', 'aoq')
PPMP_OCR_STAGE_DIR = os.path.join(BASE_DIR, 'temp', 'ppmp_ocr')
ALLOWED_OCR_EXTENSIONS = set(FLEXIBLE_AOQ_EXTENSIONS)

app = Flask(__name__)

configure_runtime(app)
from nlp_runtime_selection import configure as configure_nlp_runtime
configure_nlp_runtime(app, BASE_DIR)
app.config['PR_PO_ITEM_COMPARISON_ENABLED'] = os.environ.get('PSMS_ITEM_COMPARISON_ENABLED', '1') == '1'
durable_ocr.register(app)
app.secret_key = os.environ.get('PSMS_SECRET_KEY')
if (not app.secret_key or len(app.secret_key.strip()) < 32
        or app.secret_key.strip() in ('asuncion-psms-offline-change-me', 'replace-with-a-long-random-secret')):
    raise RuntimeError('PSMS_SECRET_KEY must be configured with a persistent random secret before startup.')


@app.errorhandler(WorkbookBusy)
def workbook_update_busy(error):
    return Response('The private workbook is being updated. Refresh and retry.', status=409,
                    mimetype='text/plain', headers={'Cache-Control': 'no-store'})


@app.errorhandler(WorkbookStorageUnavailable)
@app.errorhandler(PersistentWorkbookUnavailable)
def workbook_storage_unavailable(error):
    return Response(error.description, status=503, mimetype='text/plain',
                    headers={'Cache-Control': 'private, no-store'})




@app.context_processor
def inject_layout_context():
    account = getattr(g, 'psms_account', None)
    return {
        'header_date': datetime.datetime.now().strftime('%A, %B %-d, %Y') if os.name != 'nt' else datetime.datetime.now().strftime('%A, %B %d, %Y').replace(' 0', ' '),
        'offline_mode': True,
        'current_account': dict(account) if account else None,
    }

import signatory_management_v0_1_0 as managed_signatories
import procurement_creation

def conn():
    return managed_signatories.Connection(connect_db(DATABASE_URL))

def q(sql, params=(), one=False):
    con = conn()
    try:
        rows = con.execute(sql, params).fetchall()
        return rows[0] if one and rows else rows
    finally:
        con.close()

def exec_db(sql, params=()):
    """Execute one write and return the inserted id when applicable."""
    con = conn()
    try:
        statement = str(sql).strip()
        wants_id = statement.lower().startswith('insert into ') and ' returning ' not in statement.lower()
        cur = con.execute(statement + (' returning id' if wants_id else ''), params)
        result = None
        if wants_id:
            row = cur.fetchone()
            result = row.get('id') if row else None
        else:
            result = cur.rowcount
        con.commit()
        return result
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()

def log(action, module, ref, details='', *, connection=None):
    sql = 'insert into audit_trail(dt,"user",action,module,ref,details) values(?,?,?,?,?,?)'
    params = (now(), session.get('username', 'admin'), action, module, ref, details)
    if connection is None:
        exec_db(sql, params)
    else:
        # The caller owns commit/rollback when this audit is part of a workflow.
        if connection.execute(sql, params).rowcount != 1:
            raise RuntimeError('Audit entry was not inserted.')

def audited_update(sql, params, action, module, reference, details):
    """Commit a guarded update and its intended action audit together."""
    with durable_ocr.transaction(globals()) as (con, uid):
        count = con.execute(sql, params).rowcount
        if count == 1:
            log(action, module, reference, details, connection=con)
        return count


def now(): return datetime.datetime.now().strftime('%b %d, %Y %I:%M %p')
def today(): return datetime.date.today().strftime('%Y-%m-%d')


def _ppmp_ocr_stage_path(token):
    token = str(token or '').strip()
    if not re.fullmatch(r'[A-Za-z0-9_-]{16,100}', token):
        return None
    return os.path.join(PPMP_OCR_STAGE_DIR, token + '.json')

def clear_ppmp_ocr_state():
    for key in ('ppmp_ocr_source','ppmp_ocr_token','ocr_text','ocr_method'):
        session.pop(key, None)

def save_ppmp_ocr_state(text, method='', warnings=None, filename='', structured=None, extraction_timestamp=None):
    raise RuntimeError('Use the authenticated PostgreSQL OCR review routes; legacy file-state writes are disabled.')

def update_ppmp_ocr_state(data):
    raise RuntimeError('Use the authenticated PostgreSQL OCR review routes; legacy file-state writes are disabled.')

def load_ppmp_ocr_state():
    return durable_ocr.ppmp_state(globals(), (request.view_args or {}).get('ptype'))



def _json_dict(value):
    if isinstance(value, dict):
        return value
    try:
        parsed = json.loads(value or '{}')
        return parsed if isinstance(parsed, dict) else {}
    except Exception:
        return {}


def _ocr_draft_to_ui(row):
    d = dict(row or {})
    d['temp_id'] = d.get('temp_code') or f'OCR-TEMP-{int(d.get("id") or 0):06d}'
    d['fields'] = _json_dict(d.get('fields_json'))
    d['field_confidence'] = _json_dict(d.get('confidence_json'))
    d['human_reviewed'] = bool(d.get('reviewed'))
    d['average_confidence'] = float(d.get('average_confidence') or 0)
    status = str(d.get('status') or 'NEEDS_REVIEW').upper()
    d['status_code'] = status
    d['status'] = {
        'EXTRACTED': 'OCR Extracted',
        'NEEDS_REVIEW': 'Needs Review',
        'REVIEWED': 'Reviewed',
        'CONVERTED_TO_PPMP': 'Converted',
    }.get(status, status.replace('_', ' ').title())
    return d


def persist_ppmp_ocr_rows(ptype, state):
    raise RuntimeError('Use the authenticated PostgreSQL OCR review routes; legacy file-state writes are disabled.')


def load_active_ocr_drafts(ptype):
    return durable_ocr.ppmp_rows(globals(), ptype)


def clear_active_ocr_drafts(ptype):
    raise RuntimeError('Use the authenticated PostgreSQL OCR review routes; legacy file-state writes are disabled.')


def app_source_preview_from_items(items_json):
    items = parse_items(items_json)
    if not items:
        return 'No PPMP item details saved.'
    it = items[0]
    desc = value_any(it, 'General Description and Objective of the Project to be Procured', 'Item Name', 'Category', default='—')
    qty = value_any(it, 'Quantity and Size of the Project to be Procured', 'Description / Specification', 'Description', default='—')
    mode = value_any(it, 'Recommended Mode of Procurement', 'Procurement Method', default='—')
    funds = value_any(it, 'Source of Funds', default='—')
    start = value_any(it, 'Start of Procurement Activity', 'Start', default='—')
    end = value_any(it, 'End of Procurement Activity', 'End', default='—')
    budget = value_any(it, 'Estimated Budget Allocation (Php)', 'Estimated Total Cost', 'Total Cost', default='')
    budget_text = money_text(budget) if parse_currency_decimal(budget) is not None else '—'
    return f'{desc} | {qty} | Mode: {mode} | Funds: {funds} | {start}–{end} | Budget: {budget_text}'

def current_role():
    return str(session.get('role') or '').strip() or 'Staff'


def is_admin():
    return current_role().lower() == 'admin'


def require_admin_access():
    if not is_admin():
        flash('Administrator access is required for that page.')
        return redirect(url_for('dashboard'))
    return None
def parse_currency_decimal(value):
    """Parse a procurement amount without silently converting invalid text to zero.

    Accepted examples include 80,000, ₱80,000.00, PHP 80,000 and 80000.00.
    Returns ``None`` for blank/invalid values so callers can distinguish a genuine
    zero from missing or corrupt data.
    """
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, Decimal):
        return value if value.is_finite() else None
    if isinstance(value, (int, float)):
        try:
            parsed = Decimal(str(value))
            return parsed if parsed.is_finite() else None
        except (InvalidOperation, ValueError):
            return None

    raw = str(value).replace('\u00a0', ' ').strip()
    if not raw:
        return None
    negative = raw.startswith('(') and raw.endswith(')')
    if negative:
        raw = raw[1:-1].strip()
    raw = raw.replace('₱', '')
    raw = re.sub(r'(?i)(?:php|ph\.?p|pesos?)', '', raw)
    raw = re.sub(r'(?i)^\s*p(?=\s*[-+]?\d)', '', raw)
    raw = raw.replace(',', '').replace(' ', '')
    if not re.fullmatch(r'[-+]?\d+(?:\.\d+)?', raw):
        return None
    try:
        parsed = Decimal(raw)
        if negative:
            parsed = -abs(parsed)
        return parsed if parsed.is_finite() else None
    except InvalidOperation:
        return None


def to_float(v):
    """Legacy-friendly numeric conversion with correct PHP/currency handling.

    Critical OCR/PPMP save paths use ``parse_currency_decimal`` directly and reject
    invalid values.  This helper remains permissive for legacy quantity/id callers.
    """
    parsed = parse_currency_decimal(v)
    if parsed is not None:
        return float(parsed)
    if v in (None, ''):
        return 0.0
    cleaned = str(v).replace(',', ' ')
    match = re.search(r'-?\d+(?:\.\d+)?', cleaned)
    try:
        return float(match.group(0)) if match else 0.0
    except Exception:
        return 0.0


def current_year():
    return datetime.date.today().year


def next_document_no(kind, prefix, table, column='no', year=None, width=4, *, connection=None):
    """Generate a concurrency-safe identifier with a PostgreSQL UPSERT sequence."""
    year = int(year or current_year())
    key = f'{kind}:{year}'
    owns_connection = connection is None
    con = conn() if owns_connection else connection
    try:
        row = con.execute(
            """insert into identifier_sequence(sequence_key,last_number) values(?,1)
               on conflict(sequence_key) do update
               set last_number=identifier_sequence.last_number + 1
               returning last_number""",
            (key,),
        ).fetchone()
        number = int(row['last_number'])
        candidate = f'{prefix}-{year}-{number:0{width}d}'
        # In the unlikely case a manually imported number collides, keep advancing.
        while con.execute(f'select 1 as found from {table} where {column}=? limit 1', (candidate,)).fetchone():
            row = con.execute(
                "update identifier_sequence set last_number=last_number+1 where sequence_key=? returning last_number",
                (key,),
            ).fetchone()
            number = int(row['last_number'])
            candidate = f'{prefix}-{year}-{number:0{width}d}'
        if owns_connection:
            con.commit()
        return candidate
    except Exception:
        if owns_connection:
            con.rollback()
        raise
    finally:
        if owns_connection:
            con.close()


def source_item_identifier(record_id):
    return f'SRC-INV-{int(record_id):06d}'


def assign_item_identifiers(items, field_name, document_no):
    """Copy item dictionaries and add a stable line identifier for this document."""
    result = []
    for index, item in enumerate(items or [], start=1):
        copied = dict(item or {})
        source_id = next((copied.get(key) for key in ('_IAR Item ID', '_PO Item ID', '_AOQ Item ID', '_PR Item ID', '_APP Item ID', '_PPMP Item ID') if copied.get(key)), None)
        if source_id and field_name not in copied:
            copied['_Source Item ID'] = source_id
        copied[field_name] = f'{document_no}-ITEM-{index:02d}'
        result.append(copied)
    return result

def configure_tesseract():
    """Locate Tesseract on Windows when it is not already on PATH."""
    env_cmd = os.environ.get('TESSERACT_CMD')
    candidates = [
        env_cmd,
        r'C:\Program Files\Tesseract-OCR\tesseract.exe',
        r'C:\Program Files (x86)\Tesseract-OCR\tesseract.exe',
    ]
    for candidate in candidates:
        if candidate and os.path.exists(candidate):
            pytesseract.pytesseract.tesseract_cmd = candidate
            return candidate
    return None


def preprocess_ocr_image(image):
    """Improve scanned procurement forms before OCR."""
    img = ImageOps.exif_transpose(image).convert('L')
    if img.width < 1800:
        scale = 1800 / max(img.width, 1)
        img = img.resize((int(img.width * scale), int(img.height * scale)))
    img = ImageOps.autocontrast(img)
    img = img.filter(ImageFilter.SHARPEN)
    return img


def extract_ocr_text(filepath):
    """Extract text from an AOQ/bidding-result image or PDF."""
    configure_tesseract()
    ext = os.path.splitext(filepath)[1].lower()
    if ext == '.pdf':
        try:
            import fitz
        except ImportError as exc:
            raise RuntimeError('PDF OCR requires PyMuPDF. Run: pip install -r requirements.txt') from exc
        pages = []
        with fitz.open(filepath) as doc:
            for page_index, page in enumerate(doc):
                if page_index >= 10:
                    break
                native = (page.get_text('text') or '').strip()
                if len(native) >= 20:
                    pages.append(native)
                    continue
                pix = page.get_pixmap(matrix=fitz.Matrix(2.5, 2.5), alpha=False)
                mode = 'RGB' if pix.n < 4 else 'RGBA'
                image = Image.frombytes(mode, (pix.width, pix.height), pix.samples)
                pages.append(pytesseract.image_to_string(preprocess_ocr_image(image), config='--psm 6'))
        return '\n\n'.join(x.strip() for x in pages if x and x.strip()).strip()

    with Image.open(filepath) as image:
        return pytesseract.image_to_string(preprocess_ocr_image(image), config='--psm 6').strip()


def _clean_ocr_value(value):
    value = re.sub(r'^[\s:;|\-–—]+', '', str(value or ''))
    value = re.sub(r'\s+', ' ', value).strip()
    return value


def _find_labeled_value(lines, labels):
    label_pattern = '|'.join(re.escape(x) for x in labels)
    pattern = re.compile(rf'(?i)^\s*(?:{label_pattern})\s*(?:[:\-–—]|is)?\s*(.+?)\s*$')
    for idx, line in enumerate(lines):
        match = pattern.match(line)
        if match and _clean_ocr_value(match.group(1)):
            return _clean_ocr_value(match.group(1))
        if re.fullmatch(rf'(?i)\s*(?:{label_pattern})\s*[:\-–—]?\s*', line) and idx + 1 < len(lines):
            return _clean_ocr_value(lines[idx + 1])
    return ''


def _extract_money(value):
    if not value:
        return 0.0
    matches = re.findall(r'(?:₱|PHP|P)?\s*([0-9]{1,3}(?:,[0-9]{3})*(?:\.\d{1,2})|[0-9]+(?:\.\d{1,2})?)', str(value), flags=re.I)
    amounts = [to_float(x) for x in matches]
    return max(amounts) if amounts else 0.0


def parse_aoq_ocr_text(text, pr_row=None):
    """Convert OCR output into editable AOQ winning-offer fields."""
    raw_lines = [re.sub(r'\s+', ' ', x).strip() for x in str(text or '').splitlines()]
    lines = [x for x in raw_lines if x]
    joined = '\n'.join(lines)

    supplier = _find_labeled_value(lines, [
        'winning supplier', 'winning bidder', 'awarded supplier', 'awarded to',
        'name of supplier', 'supplier', 'bidder', 'dealer'
    ])
    item = _find_labeled_value(lines, [
        'winning item name', 'item name', 'article', 'item', 'particulars'
    ])
    description = _find_labeled_value(lines, [
        'winning item description / specification', 'description / specification',
        'description/specification', 'item description', 'description', 'specification'
    ])
    qty_text = _find_labeled_value(lines, ['quantity', 'qty'])
    unit = _find_labeled_value(lines, ['unit of measure', 'uom', 'unit'])
    unit_cost_text = _find_labeled_value(lines, ['unit cost', 'unit price', 'bid price'])
    total_text = _find_labeled_value(lines, [
        'total winning amount', 'winning amount', 'total bid amount', 'bid amount',
        'contract amount', 'grand total', 'total amount', 'total'
    ])
    remarks = _find_labeled_value(lines, ['recommendation', 'result', 'remarks', 'award basis'])

    if not supplier:
        m = re.search(r'(?i)(?:awarded\s+to|winning\s+(?:supplier|bidder)\s*(?:is|:|-)?)[\s:,-]*([^\n]{2,100})', joined)
        if m:
            supplier = _clean_ocr_value(m.group(1))

    qty = 1.0
    qty_match = re.search(r'\d+(?:\.\d+)?', qty_text or '')
    if qty_match:
        qty = to_float(qty_match.group(0)) or 1.0

    unit_cost = _extract_money(unit_cost_text)
    total = _extract_money(total_text)
    if total <= 0:
        currency_values = re.findall(r'(?i)(?:₱|PHP|P)\s*([0-9][0-9,]*(?:\.\d{1,2})?)', joined)
        if currency_values:
            total = max(to_float(x) for x in currency_values)
    if total <= 0 and qty and unit_cost:
        total = qty * unit_cost
    if unit_cost <= 0 and total > 0 and qty > 0:
        unit_cost = total / qty

    pr_items = parse_items(pr_row['items']) if pr_row else []
    first_pr_item = pr_items[0] if pr_items else {}
    if not item:
        item = str(value_any(first_pr_item, 'Item Name', 'Item Description', 'item', default='Winning Offer Item'))
    if not description:
        description = get_item_description_for_nlp(first_pr_item, 'pr') if first_pr_item else ''
    if not unit:
        unit = str(value_any(first_pr_item, 'Unit', 'unit', default='unit')) or 'unit'
    if qty == 1.0:
        pr_qty = to_float(value_any(first_pr_item, 'Quantity', 'qty', default=0))
        if pr_qty > 0:
            qty = pr_qty

    return {
        'winner': supplier,
        'item': item,
        'description': description,
        'qty': qty,
        'unit': unit,
        'unit_cost': round(unit_cost, 2),
        'winning_amount': round(total, 2),
        'remarks': remarks or 'Extracted from uploaded AOQ/bidding result through OCR.',
        'raw_text': str(text or '').strip(),
    }


def _aoq_preview_path(filename):
    name = os.path.basename(str(filename or ''))
    return os.path.join(AOQ_UPLOAD_DIR, name) if name else ''


def load_aoq_preview():
    return durable_ocr.aoq_load(globals())


def clear_aoq_preview(delete_upload=False):
    raise RuntimeError('Use the authenticated PostgreSQL OCR review routes; legacy file-state writes are disabled.')


def save_aoq_preview(preview):
    raise RuntimeError('Use the authenticated PostgreSQL OCR review routes; legacy file-state writes are disabled.')


def _posted_list(name):
    return [str(x or '').strip() for x in request.form.getlist(name)]


def posted_aoq_suppliers(preview):
    from aoq_flexible import review_number

    names = _posted_list('supplier_name')
    amounts = request.form.getlist('supplier_amount')
    remarks = _posted_list('supplier_remarks')
    original = list(preview.get('suppliers') or [])
    suppliers = []
    count = max(len(names), len(amounts), len(remarks), len(original))
    for idx in range(count):
        source = original[idx] if idx < len(original) and isinstance(original[idx], dict) else {}
        name = names[idx] if idx < len(names) else str(source.get('supplier') or '').strip()
        amount = review_number(amounts[idx] if idx < len(amounts) else source.get('amount'))
        if amount is not None and amount < 0:
            amount = None
        note = remarks[idx] if idx < len(remarks) else str(source.get('remarks') or '').strip()
        if not name and not amount and not note:
            continue
        suppliers.append({
            'supplier': name or f'Unidentified Supplier {idx + 1}',
            'amount': round(amount, 2) if amount is not None else '',
            'remarks': note,
            'is_winner': False,
            'items': list(source.get('items') or []),
        })
    return suppliers


def posted_aoq_items():
    from aoq_flexible import review_number

    def number(value):
        parsed = review_number(value)
        return parsed if parsed is not None else str(value if value is not None else '').strip()

    names = _posted_list('offer_item')
    descriptions = _posted_list('offer_description')
    qtys = request.form.getlist('offer_qty')
    units = _posted_list('offer_unit')
    unit_costs = request.form.getlist('offer_unit_cost')
    amounts = request.form.getlist('offer_amount')
    remarks = _posted_list('offer_remarks')
    count = max(len(names), len(descriptions), len(qtys), len(units), len(unit_costs), len(amounts), len(remarks))
    items = []
    for idx in range(count):
        item = names[idx] if idx < len(names) else ''
        desc = descriptions[idx] if idx < len(descriptions) else ''
        qty = number(qtys[idx]) if idx < len(qtys) else ''
        unit = units[idx] if idx < len(units) else ''
        unit_cost = number(unit_costs[idx]) if idx < len(unit_costs) else ''
        amount = number(amounts[idx]) if idx < len(amounts) else ''
        note = remarks[idx] if idx < len(remarks) else ''
        if not any([item, desc, qty, unit, unit_cost, amount, note]):
            continue
        if isinstance(unit_cost, float) and unit_cost >= 0:
            unit_cost = round(unit_cost, 2)
        if amount == '' and isinstance(qty, float) and isinstance(unit_cost, float) and unit_cost > 0:
            amount = qty * unit_cost
        if unit_cost == '' and isinstance(amount, float) and amount > 0 and isinstance(qty, float) and qty > 0:
            unit_cost = amount / qty
        items.append({
            'item': item,
            'description': desc,
            'qty': qty,
            'unit': unit,
            'unit_cost': round(unit_cost, 2) if isinstance(unit_cost, float) and unit_cost >= 0 else unit_cost,
            'amount': round(amount, 2) if isinstance(amount, float) and amount >= 0 else amount,
            'remarks': note,
        })
    return items


def _norm_text(value):
    return re.sub(r'\s+', ' ', re.sub(r'[^a-z0-9]+', ' ', str(value or '').lower())).strip()


def parse_json_object(value):
    if isinstance(value, dict):
        return value
    try:
        parsed = json.loads(value or '{}')
        return parsed if isinstance(parsed, dict) else {}
    except Exception:
        return {}


def parse_json_list(value):
    if isinstance(value, list):
        return value
    try:
        parsed = json.loads(value or '[]')
        return parsed if isinstance(parsed, list) else []
    except Exception:
        return []


def money(v):
    return '₱{:,.2f}'.format(to_float(v))
app.jinja_env.filters['money'] = money

def quantity_text(value):
    """Format numeric quantities without rounding or changing missing values."""
    if value is None or value == '':
        return '—'
    try:
        number = Decimal(str(value))
        if number.is_finite():
            return format(number, 'f').rstrip('0').rstrip('.') if '.' in format(number, 'f') else format(number, 'f')
    except (InvalidOperation, ValueError):
        pass
    return str(value)

def month_year_text(value):
    """Display only unambiguous source months; retain unsupported source wording."""
    text = str(value or '').strip()
    if re.match(r'^\d{4}-\d{2}-\d{2}', text):
        try:
            return datetime.datetime.fromisoformat(text).strftime('%m/%Y')
        except ValueError:
            pass
    for pattern in ('%Y-%m-%d %H:%M:%S', '%Y-%m-%dT%H:%M:%S', '%Y-%m-%d', '%Y-%m', '%B %Y', '%b %Y', '%m/%Y'):
        try:
            return datetime.datetime.strptime(text, pattern).strftime('%m/%Y')
        except ValueError:
            continue
    return text

def document_date_text(value):
    text = str(value or '').strip()
    try:
        return datetime.datetime.fromisoformat(text).strftime('%d %b %Y')
    except ValueError:
        return text

def source_money_text(value):
    """Keep missing/invalid source amounts distinct from explicit zero in summaries."""
    if value is None or value == '':
        return 'Not provided'
    amount = parse_currency_decimal(value)
    return money(amount) if amount is not None else str(value) + ' (unresolved)'

app.jinja_env.filters.update(quantity=quantity_text, month_year=month_year_text,
                             document_date=document_date_text, source_money=source_money_text)

ASUNCION_HEADERS = [
    'General Description and Objective of the Project to be Procured',
    'Type of the Project to be Procured',
    'Quantity and Size of the Project to be Procured',
    'Recommended Mode of Procurement',
    'Pre-Procurement Conference Applicable (Yes/No)',
    'Start of Procurement Activity',
    'End of Procurement Activity',
    'Expected Delivery/Implementation Period',
    'Source of Funds',
    'Estimated Budget Allocation (Php)',
    'Attached Supporting Documents',
    'Remarks'
]

PPMP_FIELD_ALIASES = {
    'General Description and Objective of the Project to be Procured': (
        'General Description and Objective of the Project to be Procured',
        'General Description and Objective', 'Category', 'Item Name', 'Item Description'
    ),
    'Type of the Project to be Procured': (
        'Type of the Project to be Procured', 'Type of Project'
    ),
    'Quantity and Size of the Project to be Procured': (
        'Quantity and Size of the Project to be Procured', 'Quantity and Size',
        'Description / Specification', 'Description'
    ),
    'Recommended Mode of Procurement': (
        'Recommended Mode of Procurement', 'Procurement Method'
    ),
    'Pre-Procurement Conference Applicable (Yes/No)': (
        'Pre-Procurement Conference Applicable (Yes/No)', 'Pre-Procurement Conference'
    ),
    'Start of Procurement Activity': ('Start of Procurement Activity', 'Start'),
    'End of Procurement Activity': ('End of Procurement Activity', 'End'),
    'Expected Delivery/Implementation Period': (
        'Expected Delivery/Implementation Period', 'Delivery Period'
    ),
    'Source of Funds': ('Source of Funds',),
    'Estimated Budget Allocation (Php)': (
        'Estimated Budget Allocation (Php)', 'Estimated Total Cost', 'Total Cost', 'amount'
    ),
    'Attached Supporting Documents': ('Attached Supporting Documents', 'Supporting Documents'),
    'Remarks': ('Remarks',),
}


def _text_value(value):
    return '' if value is None else str(value).replace('\u00a0', ' ').strip()


def canonical_ppmp_fields(item):
    """Return all 12 official PPMP fields from one stored item.

    The canonical field names always win. Aliases are only a compatibility fallback
    for older records created before OCR/native document support existed.
    """
    item = dict(item or {})
    result = {}
    for label in ASUNCION_HEADERS:
        value = ''
        for key in PPMP_FIELD_ALIASES[label]:
            candidate = item.get(key)
            if candidate not in (None, ''):
                value = candidate
                break
        if label == 'Estimated Budget Allocation (Php)':
            amount = parse_currency_decimal(value)
            result[label] = float(amount) if amount is not None else ('' if value in (None, '') else _text_value(value))
        else:
            result[label] = _text_value(value)
    return result


def normalize_reviewed_ocr_fields(fields):
    """Preserve the 12 official fields and optional structured review fields."""
    from procurement_item_contract import ITEM_REVIEW_FIELDS
    source = dict(fields or {})
    result = {label: _text_value(source.get(label))[:5000] for label in ASUNCION_HEADERS}
    result.update({label: _text_value(source[label])[:5000] for label in ITEM_REVIEW_FIELDS if label in source})
    return result


def ppmp_review_labels():
    from procurement_item_contract import ITEM_REVIEW_FIELDS
    return [*ASUNCION_HEADERS, *ITEM_REVIEW_FIELDS]


def validated_ppmp_item_from_review(fields, ptype):
    """Create an official PPMP item from the persisted review record only.

    No OCR, semantic mapping, Excel source or session fallback is consulted here.
    Invalid currency is rejected instead of silently becoming zero.
    """
    reviewed = normalize_reviewed_ocr_fields(fields)
    errors = list(validate_ocr_ppmp_record(reviewed))
    budget_label = OCR_PPMP_FIELDS['budget']['label']
    budget_raw = reviewed.get(budget_label, '')
    budget = parse_currency_decimal(budget_raw)
    if not budget_raw:
        errors.append('Estimated Budget is required.')
    elif budget is None:
        errors.append('Estimated Budget is invalid. Enter a value such as 80000 or PHP 80,000.00.')
    if errors:
        raise ValueError(' '.join(dict.fromkeys(errors)))
    reviewed[budget_label] = float(budget)
    candidate = dict(reviewed)
    from procurement_item_contract import ITEM_REVIEW_FIELDS
    if any(key in candidate for key in ITEM_REVIEW_FIELDS):
        candidate['_Item Contract Version'] = 1
    item = normalize_excel_row(candidate, ptype)
    from procurement_item_contract import item_errors
    errors = item_errors(item)
    from procurement_item_contract import decimal_number
    quantity = decimal_number(reviewed.get('Quantity'))
    cost = decimal_number(reviewed.get('Estimated Unit Cost'), money=True)
    if quantity is not None and cost is not None and budget is not None:
        from decimal import ROUND_HALF_UP
        if quantity * cost > Decimal('1e18') or (quantity * cost).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP) != budget:
            errors.append('Quantity multiplied by estimated unit cost differs from Estimated Budget. Verify all three values; none was replaced.')
    if budget is not None and (budget < 0 or budget.as_tuple().exponent < -2):
        errors.append('Estimated Budget must be non-negative with at most two decimal places.')
    if errors:
        raise ValueError(' '.join(errors))
    # Re-assert all canonical fields after alias generation. This prevents an alias
    # default from ever replacing a human-reviewed official value.
    for label, value in reviewed.items():
        if label not in ASUNCION_HEADERS:
            continue
        item[label] = value
    return item


def items_total(items):
    total = Decimal('0')
    found = False
    for item in items or []:
        canonical = canonical_ppmp_fields(item)
        amount = parse_currency_decimal(canonical.get('Estimated Budget Allocation (Php)'))
        if amount is not None:
            total += amount
            found = True
    return float(total) if found else None


def ppmp_source_payload(row):
    """Normalized server-side source data for the APP PPMP selector."""
    record = dict(row or {})
    items = parse_items(record.get('items'))
    normalized_items = []
    for index, item in enumerate(items, start=1):
        canonical = canonical_ppmp_fields(item)
        normalized_items.append({
            'index': index,
            'item_id': item.get('_PPMP Item ID') or f'ITEM-{index:03d}',
            'fields': canonical,
        })
    computed_total = items_total(items)
    stored_total = parse_currency_decimal(record.get('total'))
    total = computed_total if computed_total is not None else (float(stored_total) if stored_total is not None else 0.0)
    return {
        'id': record.get('id'),
        'no': record.get('no') or '',
        'type': record.get('type') or '',
        'office': record.get('office') or '',
        'year': record.get('year'),
        'status': record.get('status') or '',
        'source_type': record.get('source_type') or 'Manual/Imported',
        'total': total,
        'items': normalized_items,
    }


def repair_procurement_totals(con):
    """Repair historical totals when item JSON contains a valid amount.

    Earlier builds could store PHP-formatted budget text correctly inside ``items``
    but save the parent total as 0. This idempotent repair keeps old records usable.
    """
    for table in ('ppmp', 'app_plan', 'pr'):
        try:
            rows = con.execute(f'select id,items,total from {table}').fetchall()
        except Exception:
            continue
        for row in rows:
            calculated = items_total(parse_items(row.get('items')))
            if calculated is None:
                continue
            stored = to_float(row.get('total'))
            if abs(stored - calculated) > 0.005:
                con.execute(f'update {table} set total=? where id=?', (calculated, row['id']))


def repair_ppmp_workflow_statuses(con):
    """Normalize historical PPMP records that already have a downstream APP.

    Older builds reused the PPMP.status column for downstream milestones such as
    'For APP', 'APP Approved', or 'PR Approved'. In the reviewed/approved model,
    PPMP keeps its own approval status while downstream documents keep theirs.
    Any PPMP that already has an APP is therefore treated as historically approved.
    """
    try:
        con.execute(
            """update ppmp p
               set status='Approved',
                   reviewed_by=coalesce(nullif(reviewed_by,''),'system'),
                   reviewed_at=coalesce(nullif(reviewed_at,''),created_at),
                   approved_by=coalesce(nullif(approved_by,''),'system'),
                   approved_at=coalesce(nullif(approved_at,''),created_at)
             where exists (select 1 from app_plan a where a.ppmp_id=p.id)
               and coalesce(p.status,'') <> 'Approved'"""
        )
    except Exception:
        # Schema upgrade may not yet have been applied in a partially initialized DB.
        return


def repair_ocr_ppmps_from_reviewed_drafts(con):
    """Backfill converted OCR PPMPs from their persisted reviewed drafts.

    Only the human-reviewed database draft is trusted. Invalid drafts are skipped
    rather than converted into blanks, defaults, or zero-valued fields.
    Approved PPMPs must retain their approved contents.
    """
    try:
        rows = con.execute(
            """select d.*, p.no as ppmp_no, p.type as ppmp_type,
                      p.items as ppmp_items, p.total as ppmp_total
               from ppmp_ocr_drafts d
               join ppmp p on p.id=d.official_ppmp_id
               where d.reviewed=true and d.official_ppmp_id is not null
                 and (p.status is null or p.status <> 'Approved')"""
        ).fetchall()
    except Exception:
        return 0
    repaired = 0
    for draft in rows:
        try:
            fields = normalize_reviewed_ocr_fields(_json_dict(draft.get('fields_json')))
            ptype = draft.get('ppmp_type') or draft.get('ppmp_category') or 'NON-DBM'
            item = validated_ppmp_item_from_review(fields, ptype)
            old_items = parse_items(draft.get('ppmp_items'))
            old_item = old_items[0] if old_items else {}
            for key, value in dict(old_item or {}).items():
                if str(key).startswith('_'):
                    item[key] = value
            item['_OCR Source'] = draft.get('extraction_method') or item.get('_OCR Source') or 'OCR'
            item['_OCR Filename'] = draft.get('source_filename') or item.get('_OCR Filename') or ''
            item['_OCR Temp ID'] = draft.get('temp_code') or item.get('_OCR Temp ID') or ''
            item['_OCR Confidence'] = draft.get('average_confidence') or item.get('_OCR Confidence') or 0
            if not item.get('_PPMP Item ID'):
                item['_PPMP Item ID'] = f'{draft.get("ppmp_no")}-ITEM-01'
            total_dec = parse_currency_decimal(item.get(OCR_PPMP_FIELDS['budget']['label']))
            if total_dec is None:
                continue
            new_items = [item]
            old_canonical = canonical_ppmp_fields(old_item) if old_item else {}
            new_canonical = canonical_ppmp_fields(item)
            needs_repair = (
                old_canonical != new_canonical
                or abs(to_float(draft.get('ppmp_total')) - float(total_dec)) > 0.005
            )
            if not needs_repair:
                continue
            result = con.execute(
                """update ppmp set items=?, total=?, source_type='OCR', ocr_verified=true,
                          ocr_verified_by=?, ocr_verified_at=?, ocr_source_filename=?, ocr_raw_text=?,
                          ocr_average_confidence=? where id=?
                          and (status is null or status <> 'Approved')""",
                (json.dumps(new_items, ensure_ascii=False, default=str), float(total_dec),
                 draft.get('reviewed_by') or '', draft.get('reviewed_at') or '',
                 draft.get('source_filename') or '', draft.get('raw_ocr_text') or '',
                 float(draft.get('average_confidence') or 0), draft['official_ppmp_id'])
            )
            if result.rowcount != 1:
                continue
            con.execute(
                'insert into audit_trail(dt,"user",action,module,ref,details) values(?,?,?,?,?,?)',
                (now(), 'system', 'Repaired OCR PPMP from Reviewed Draft', 'PPMP',
                 draft.get('ppmp_no') or '',
                 f'Persistent OCR draft {draft.get("temp_code") or draft.get("id")} restored all reviewed PPMP fields.')
            )
            repaired += 1
        except Exception:
            continue
    return repaired

PPMP_DESTINATIONS = ('DBM', 'NON-DBM', 'LIB')
PPMP_DATASET_META_HEADERS = [
    '_Source Item ID',
    '_Dataset Record ID',
    '_Dataset Record Key',
    '_Dataset Source Sheet',
    '_Dataset Source Row',
    '_Dataset Requesting Office',
]


def excel_meta():
    """Return the exact Excel file used by the system.

    This is shown in the PPMP screen so the user can verify they edited the
    correct file. The app ONLY reads this path:
        data/PPMP_Data.xlsx
    """
    from durable_workbooks import enabled, read_source, Unavailable
    try:
        persistent = enabled()
        source = read_source(globals(), 'ppmp_source') if persistent else None
    except Unavailable:
        return {'path': 'Private workbook source', 'exists': False, 'unavailable': True,
                'modified': 'Source selection temporarily unavailable', 'size': 0}
    if persistent:
        return {'path': 'Private PostgreSQL workbook source', 'exists': bool(source),
                'modified': str(source['created_at']) if source else 'No source items yet',
                'size': len(source['content']) if source else 0}
    exists = os.path.exists(EXCEL_PATH)
    info = {
        'path': EXCEL_PATH,
        'exists': exists,
        'modified': 'File not found',
        'size': 0,
    }
    if exists:
        try:
            stat = os.stat(EXCEL_PATH)
            info['size'] = stat.st_size
            info['modified'] = datetime.datetime.fromtimestamp(stat.st_mtime).strftime('%b %d, %Y %I:%M:%S %p')
        except Exception as e:
            info['modified'] = f'Unable to read modified time: {e}'
    return info


def excel_row_signature(row):
    """Build a simple content signature for an Excel row.

    The database also stores _row for convenience, but row numbers change when
    a user deletes/insert rows in Excel. This signature helps avoid hiding the
    wrong newly-added row after Excel rows shift.
    """
    keys = [
        'General Description and Objective of the Project to be Procured',
        'Type of the Project to be Procured',
        'Quantity and Size of the Project to be Procured',
        'Recommended Mode of Procurement',
        'Source of Funds',
        'Estimated Budget Allocation (Php)',
        'Remarks',
    ]
    return '|'.join(str(row.get(k, '')).strip().lower() for k in keys)

def value_any(row, *keys, default=''):
    """Return the first non-empty value from a dictionary using possible column names."""
    for key in keys:
        val = row.get(key)
        if val not in (None, ''):
            return val
    return default

def normalize_excel_row(row, ptype):
    """Make Asuncion PPMP rows compatible with the rest of the system.

    The connected Excel follows the official Asuncion PPMP columns. This function keeps
    the original columns for printing while adding aliases used by PR, AOQ, PO and NLP.
    """
    general = value_any(row, 'General Description and Objective of the Project to be Procured', 'General Description and Objective', 'Category')
    project_type = value_any(row, 'Type of the Project to be Procured', 'Type of Project', default='Goods' if ptype != 'LIB' else 'Services')
    quantity_size = value_any(row, 'Quantity and Size of the Project to be Procured', 'Quantity and Size', 'Description / Specification', 'Description')
    method = value_any(row, 'Recommended Mode of Procurement', 'Procurement Method')
    preproc = value_any(row, 'Pre-Procurement Conference Applicable (Yes/No)', 'Pre-Procurement Conference', default='No')
    start = value_any(row, 'Start of Procurement Activity', 'Start')
    end = value_any(row, 'End of Procurement Activity', 'End')
    delivery = value_any(row, 'Expected Delivery/Implementation Period', 'Delivery Period')
    funds = value_any(row, 'Source of Funds', default='')
    budget = value_any(row, 'Estimated Budget Allocation (Php)', 'Estimated Total Cost', 'Total Cost', default='')
    docs = value_any(row, 'Attached Supporting Documents', 'Supporting Documents')
    remarks = value_any(row, 'Remarks')

    # item name is derived from the quantity/specification text; this keeps the transaction pages usable
    # while the official print preview still uses the exact PPMP columns.
    from procurement_item_contract import interpret_item, ITEM_REVIEW_FIELDS
    interpreted = interpret_item(quantity_size, general, {key: row[key] for key in ITEM_REVIEW_FIELDS if key in row and (row.get('_Item Contract Version') or row[key] not in (None, ''))})
    item_name = interpreted['name']
    category = str(general).strip() if general else ptype

    row['PPMP Type'] = ptype
    row['Category'] = category
    row['Item Name'] = item_name
    row['Item Description'] = item_name
    row['Description / Specification'] = str(quantity_size or '')
    row['Description'] = str(quantity_size or '')
    row['Type of Project'] = project_type
    row['Procurement Method'] = method
    row['Pre-Procurement Conference'] = preproc
    row['Start'] = start
    row['End'] = end
    row['Delivery Period'] = delivery
    row['Source of Funds'] = funds
    row['Estimated Total Cost'] = budget if budget not in (None, '') else ''
    row['Estimated Unit Cost'] = interpreted['unit_cost']
    row['Quantity'] = interpreted['quantity']
    row['Unit'] = interpreted['unit']
    if 'Unit Cost' in row:
        row['Unit Cost'] = interpreted['unit_cost']
    row['_Item Contract Version'] = 1
    row['_Original Description'] = interpreted['original_description']
    row['_Item Interpretation'] = interpreted
    row['_Unit Cost Basis'] = 'source or explicit human review' if interpreted['unit_cost'] else 'not provided'
    row['Attached Supporting Documents'] = docs
    row['Remarks'] = remarks
    return row


def planning_item_errors(items, *, require_structured=False):
    """New structured items must remain valid at planning transfer boundaries."""
    from procurement_item_contract import item_errors
    return [f'Item {index}: {error}' for index, item in enumerate(items, 1)
            if require_structured or item.get('_Item Contract Version') or item.get('_OCR Source') for error in item_errors(item)]




ASUNCION_APP_SCHEDULE = [
    ('Jan. 14-23', 'Jan. 24', 'Jan. 26', 'Jan. 28'),
    ('Feb. 14-23', 'Feb. 21', 'Feb. 23', 'Feb. 25'),
    ('Mar. 1-7', 'Mar. 8', 'Mar. 29', 'Mar. 14'),
    ('April 4-10', 'Apr. 11', 'Apr. 26', 'Apr. 15'),
    ('May 9-15', 'May 16', 'May 28', 'May 20'),
    ('June 6-12', 'Jun. 13', 'Jun. 25', 'Jun. 17'),
    ('July 4-10', 'Jul. 20', 'Jul. 26', 'Jul. 13'),
    ('Aug. 1-7', 'Aug. 12', 'Aug. 30', 'Aug. 12'),
    ('Sept. 2-8', 'Sept. 12', 'Sept. 24', 'Sept. 14'),
    ('Oct. 3-9', 'Oct. 12', 'Oct. 29', 'Oct. 14'),
    ('Nov. 2-8', 'Nov. 11', 'Nov. 26', 'Nov. 14'),
    ('Dec. 5-11', 'Dec. 14', 'Dec. 27', 'Dec. 16'),
]

def is_capital_outlay(text):
    t = str(text or '').lower()
    return any(w in t for w in ['equipment', 'computer', 'printer', 'vehicle', 'furniture', 'aircon', 'air conditioner', 'laptop', 'desktop'])

def build_app_rows(items, office=''):
    """Build one Asuncion APP line for every PPMP item.

    Important process rule:
    If the user selected 2 PPMP Excel rows, the APP printout must show 2
    procurement program/project lines. We intentionally do NOT group items here
    because grouping can make multiple selected PPMP rows look like only one APP
    item.
    """
    app_rows = []
    for idx, it in enumerate(items, start=1):
        program = value_any(
            it,
            'General Description and Objective of the Project to be Procured',
            'Category',
            'Item Name',
            default=f'Procurement Program/Project {idx}'
        )
        mode = value_any(it, 'Recommended Mode of Procurement', 'Procurement Method', default='Alternative Method/Agency to Agency')
        funds = value_any(it, 'Source of Funds', default='Gen. fund')
        remarks = value_any(
            it,
            'Remarks',
            'Quantity and Size of the Project to be Procured',
            'Description / Specification',
            default='Brief description of Program/Project'
        )
        amount = to_float(value_any(it, 'Estimated Budget Allocation (Php)', 'Estimated Total Cost', 'Total Cost', default=0))
        project_type = value_any(it, 'Type of the Project to be Procured', 'Type of Project', default='')
        classification = str(value_any(it, 'Budget Classification', 'Expenditure Classification', default='')).strip().upper()
        app_rows.append({
            'code': f'PAP-{idx:03d}',
            'program': str(program).strip(),
            'office': office or 'Not recorded',
            'mode': str(mode).strip(),
            'funds': str(funds).strip() or 'Gen. fund',
            'total': amount,
            'mooe': amount if classification == 'MOOE' else None,
            'co': amount if classification == 'CO' else None,
            'remarks': str(remarks).strip(),
            'schedules': [tuple(str(value_any(it, key, default='')).strip() for key in
                               ('Advertisement/Posting', 'Submission/Opening of Bids', 'Notice of Award', 'Contract Signing'))],
        })
    return app_rows

def item_count_from_json(items_json):
    return len(parse_items(items_json))

def item_preview_from_json(items_json, limit=3, include_offer_name=False):
    names = []
    for it in parse_items(items_json):
        names.append(str(it.get('Item Name') or it.get('Item Description') or (it.get('item') if include_offer_name else None) or it.get('General Description and Objective of the Project to be Procured') or 'Item'))
    if len(names) > limit:
        return ', '.join(names[:limit]) + f' + {len(names) - limit} more'
    return ', '.join(names)



def money_text(value):
    return f'₱{to_float(value):,.2f}'


def review_value(value):
    """Convert stored values into readable text for the read-only review screen."""
    if value is None or value == '':
        return '—'
    if isinstance(value, bool):
        return 'Yes' if value else 'No'
    if isinstance(value, (list, dict)):
        return json.dumps(value, ensure_ascii=False, default=str)
    return str(value)


def review_item_rows(items, document_no='DOC'):
    """Normalize different procurement item schemas for one consistent review table.

    The original item dictionary is also preserved as a complete field list so the
    reviewer can expand a line and verify every stored value before approval.
    """
    rows = []
    id_keys = (
        '_PPMP Item ID', '_APP Item ID', '_PR Item ID', '_AOQ Item ID',
        '_PO Item ID', '_IAR Item ID', '_Issue Item ID', 'item_code'
    )
    known_keys = {
        'Item Name', 'Item Description', 'item', 'name',
        'Description / Specification', 'Description', 'description', 'specification',
        'Quantity', 'qty', 'quantity', 'Quantity and Size of the Project to be Procured',
        'Unit', 'unit', 'Unit of Measure',
        'Unit Cost', 'unit_cost', 'Estimated Unit Cost',
        'Amount', 'amount', 'Total Cost', 'Estimated Total Cost',
        'Estimated Budget Allocation (Php)',
        'Remarks', 'remarks', 'General Description and Objective of the Project to be Procured',
    }
    for index, item in enumerate(items or [], start=1):
        item = dict(item or {})
        line_id = next((str(item.get(k)).strip() for k in id_keys if item.get(k)), '')
        if not line_id:
            line_id = f'{document_no}-ITEM-{index:02d}'
        evidence = item.get('_OCR Field Evidence') or {}
        evidence = evidence.get('_source_evidence', {}) if isinstance(evidence, dict) else {}
        if not isinstance(evidence, dict):
            evidence = {}
        source_id = str(item.get('_Source Item ID') or '').strip()
        source_number = str(evidence.get('item_number') or item.get('Item Number') or item.get('Item No') or '').strip()
        item_name = value_any(
            item, 'Item Name', 'Item Description', 'item', 'name',
            'General Description and Objective of the Project to be Procured',
            default='Item'
        )
        description = value_any(
            item, 'Description / Specification', 'Description', 'description',
            'specification', 'Remarks', 'remarks',
            default=''
        )
        quantity = value_any(
            item, 'received_qty', 'Quantity', 'qty', 'quantity',
            'Quantity and Size of the Project to be Procured', default='—'
        )
        unit = value_any(item, 'Unit', 'unit', 'Unit of Measure', default='—')
        unit_cost_raw = value_any(item, 'Unit Cost', 'unit_cost', 'Estimated Unit Cost', default='')
        amount_raw = value_any(
            item, 'Amount', 'amount', 'Total Cost', 'Estimated Total Cost',
            'Estimated Budget Allocation (Php)', default=''
        )
        unit_cost = money_text(unit_cost_raw) if unit_cost_raw not in ('', None) else '—'
        amount = money_text(amount_raw) if amount_raw not in ('', None) else '—'
        details = []
        for key, value in item.items():
            if value in (None, '', [], {}):
                continue
            label = str(key).lstrip('_').replace('_', ' ').strip()
            details.append({'label': label, 'value': review_value(value), 'is_primary': key in known_keys,
                            'diagnostic': str(key).startswith('_') or isinstance(value, (dict, list))})
        rows.append({
            'index': index,
            'line_id': line_id,
            'source_id': source_id or '—',
            'source_number': source_number,
            'item': review_value(item_name),
            'description': review_value(description),
            'quantity': review_value(quantity),
            'unit': review_value(unit),
            'unit_cost': unit_cost,
            'amount': amount,
            'details': details,
        })
    return rows


def review_section(title, items, document_no, note='', optional=False):
    return {
        'title': title,
        'note': note,
        'items': review_item_rows(items, document_no),
        'count': len(items or []),
        'optional': optional,
    }


def render_review_page(*, active, title, document_type, document_no, status,
                       created_at='', linked_records=None, summary=None,
                       item_sections=None, text_blocks=None, approve_url=None,
                       approve_label='', approval_effect='', back_url=None,
                       print_url=None, can_approve=False, review_url=None,
                       review_label='', review_effect='', can_review=False, pricing_diagnostics=None,
                       item_comparison=None, receiving_items=None, approval_details=None):
    return render_template(
        'document_review.html', active=active, title=title,
        document_type=document_type, document_no=document_no,
        status=status or 'Unknown', created_at=created_at,
        linked_records=linked_records or [], summary=summary or [],
        item_sections=item_sections or [], text_blocks=text_blocks or [],
        approve_url=approve_url, approve_label=approve_label,
        approval_effect=approval_effect, back_url=back_url,
        print_url=print_url, can_approve=bool(can_approve),
        review_url=review_url, review_label=review_label,
        review_effect=review_effect, can_review=bool(can_review),
        pricing_diagnostics=pricing_diagnostics,
        item_comparison=item_comparison,
        receiving_items=receiving_items or [],
        approval_details=approval_details or [],
    )

def source_pr_purpose(items):
    """Resolve new PR purpose from every approved source objective, never browser text."""
    objectives = []
    for item in items:
        objective = str(item.get('General Description and Objective of the Project to be Procured') or '').strip()
        if objective and objective not in objectives:
            objectives.append(objective)
    return '\n'.join(objectives) or 'Purpose not recorded in approved source.'


def require_review_confirmation(review_endpoint, record_id):
    """Block approval unless it was submitted from the read-only review page."""
    if request.form.get('review_confirm') != 'yes':
        flash('Open the review page and confirm that all items were checked before approving.')
        return redirect(url_for(review_endpoint, id=record_id))
    return None

def init_db():
    """Initialize the Supabase/PostgreSQL schema and safe application defaults."""
    apply_schema()
    con = conn()
    try:
        existing = con.execute('select count(*) as c from users').fetchone()
        if int((existing or {}).get('c') or 0) == 0:
            con.execute(
                'insert into users(username, password, role, full_name, display_name, status, created_at) values (?, ?, ?, ?, ?, ?, ?)',
                ('admin', generate_password_hash(os.environ.get('PSMS_INITIAL_ADMIN_PASSWORD', 'admin123')), 'Admin', 'System Administrator', 'admin', 'Active', now())
            )
        # Upgrade any legacy plaintext account values imported into Supabase.
        for existing_user in con.execute('select id,password from users').fetchall():
            stored = str(existing_user.get('password') or '')
            if not (stored.startswith('scrypt:') or stored.startswith('pbkdf2:')):
                con.execute('update users set password=? where id=?',
                            (generate_password_hash(stored or 'changeme'), existing_user['id']))

        # Backfill identifiers for imported legacy rows.
        for legacy in con.execute("select id from nlp where no is null or trim(no)='' order by id").fetchall():
            candidate = f'NLP-{current_year()}-{int(legacy["id"]):04d}'
            con.execute('update nlp set no=? where id=?', (candidate, legacy['id']))
        for legacy in con.execute("select id from inventory where item_code is null or trim(item_code)='' order by id").fetchall():
            candidate = f'INV-{current_year()}-{int(legacy["id"]):05d}'
            con.execute('update inventory set item_code=? where id=?', (candidate, legacy['id']))

        # Restore OCR-created PPMPs from their persisted reviewed drafts, then
        # repair derived parent totals from the exact stored item JSON.
        repair_ocr_ppmps_from_reviewed_drafts(con)
        repair_procurement_totals(con)
        repair_ppmp_workflow_statuses(con)
        con.commit()
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()

    ensure_inventory_dataset_schema(DATABASE_URL)
    if os.path.exists(COA_DATASET_PATH):
        try:
            sync_inventory_dataset_workbook(
                DATABASE_URL, COA_DATASET_PATH,
                display_filename=os.path.basename(COA_DATASET_PATH), force=False,
            )
        except Exception as exc:
            print(f'Inventory dataset initialization warning: {exc}')


@app.cli.command('initialize-database')
@click.option('--confirm', is_flag=True,
              help='Confirm explicit schema/account initialization and legacy repairs on the configured target.')
def initialize_database_command(confirm):
    """Explicit maintenance only; never called by startup or a web request."""
    if not confirm:
        raise click.ClickException('Initialization writes data. Verify the target and rerun with --confirm only when authorized.')
    if not os.environ.get('SUPABASE_DB_URL', '').strip():
        raise click.ClickException('Explicit initialization requires SUPABASE_DB_URL; no default target is accepted.')
    password = os.environ.get('PSMS_INITIAL_ADMIN_PASSWORD')
    if (not password or len(password.strip()) < 12
            or password.strip().casefold() in ('replace-with-a-strong-initial-password',
                                               'use-a-strong-password-here')):
        raise click.ClickException('Configure a unique PSMS_INITIAL_ADMIN_PASSWORD of at least 12 non-padding characters. It is used only if users is empty.')
    click.echo('Explicit initialization: schema/account setup, legacy credential upgrades, record repairs and optional workbook sync may run.')
    try:
        init_db()
    except Exception:
        raise click.ClickException('Initialization failed. Inspect the verified target privately; connection details are not displayed.') from None
    click.echo('Explicit database initialization completed.')


@app.cli.command('check-auth-sessions')
@click.option('--confirm-database-target', is_flag=True,
              help='Confirm authorization for read-only session metadata checks on the configured database.')
def check_auth_sessions_command(confirm_database_target):
    """Explicit diagnostic only: no account data, migrations, repairs or logins."""
    if not confirm_database_target:
        raise click.ClickException('Verify the database target and obtain permission for metadata-only access before using --confirm-database-target.')
    if not os.environ.get('SUPABASE_DB_URL', '').strip():
        raise click.ClickException('An explicit SUPABASE_DB_URL is required; no default database is accepted for this diagnostic.')
    result=server_sessions.session_diagnostics(conn)
    click.echo(json.dumps(result, sort_keys=True))
    if not result['ready']:
        raise click.ClickException('Session metadata checks failed. Resolve only the reported requirement after target-specific review; no database changes were made.')


def read_excel(sheet, *, connection=None):
    """Read DBM, NON-DBM or LIB from data/PPMP_Data.xlsx.

    Compatible with the Asuncion PPMP Excel format shown in the official form.
    Row 1 must contain the PPMP columns. Personnel/signatories are intentionally
    excluded from Excel and are injected only in print preview.
    """
    from durable_workbooks import enabled, read_source, Unavailable
    try:
        persistent = enabled()
        source = read_source(globals(), 'ppmp_source', connection=connection) if persistent else None
    except Unavailable:
        if connection is not None:
            raise
        # Workbook availability must not prevent independent OCR draft review.
        # A source mutation/official creation still fails closed on its connection.
        return []
    if persistent:
        if source is None:
            return []
        workbook_source = io.BytesIO(source['content'])
    else:
        if not os.path.exists(EXCEL_PATH):
            return []
        workbook_source = EXCEL_PATH

    wb = load_workbook(workbook_source, data_only=True, read_only=True)
    try:
        from workbook_limits import validate_dimensions, bounded_rows, parsing_budget
        validate_dimensions(wb)
        # Match DBM, NON-DBM, or LIB even if the Excel sheet casing/spaces differ.
        requested = str(sheet or '').strip().lower()
        matches = [name for name in wb.sheetnames if str(name).strip().lower() == requested]
        if requested not in ('dbm', 'non-dbm', 'lib') or len(matches) != 1:
            return []
        sheet_name = matches[0]
        ws = wb[sheet_name]
        rows = list(bounded_rows(ws, parsing_budget()))
        if not rows:
            return []

        headers = [str(h).strip() if h else '' for h in rows[0]]
        data = []
        for i, r in enumerate(rows[1:], start=2):
            if not any(r):
                continue
            raw = {headers[j]: r[j] for j in range(min(len(headers), len(r))) if headers[j]}
            raw['_row'] = i
            raw = normalize_excel_row(raw, sheet_name)
            raw['_excel_sheet'] = sheet_name
            raw['_excel_signature'] = excel_row_signature(raw)
            data.append(raw)
        return data
    finally:
        wb.close()


def ppmp_has_downstream_app(ppmp_id):
    return bool(q('select id from app_plan where ppmp_id=? limit 1', (ppmp_id,), one=True))


def sync_draft_ppmp_from_excel(ptype, *, connection=None, source_rows=None):
    """Synchronize Draft PPMP records with the current Excel file.

    Why this is needed:
    - The PPMP screen hides Excel rows that were already selected.
    - If the user edits/deletes those rows in Excel, the old saved PPMP JSON
      would stay unchanged unless we update it here.
    - Only Draft PPMP records with no generated APP are changed. Records that
      already moved to APP/PR/PO are preserved for audit trail consistency.

    A row number alone is not identity: rows can move or be replaced. Only a
    stable dataset ID or an unambiguous retained source signature may match.
    Missing, partial, ambiguous and OCR sources preserve the entire record.
    """
    from contextlib import nullcontext
    from durable_workbooks import enabled, refresh_drafts
    if enabled() and connection is None:
        return refresh_drafts(globals(), ptype)
    excel_rows = source_rows if source_rows is not None else read_excel(ptype)
    result = dict(updated=0, deleted=0, preserved=0, excel_rows=len(excel_rows),
                  source_unavailable=not bool(excel_rows))
    if not excel_rows:
        return result
    category = str(ptype).strip().upper()
    by_id, by_sig = {}, {}
    for row in excel_rows:
        if str(row.get('_excel_sheet', '')).strip().upper() != category:
            continue
        for index, key in ((by_id, row.get('_Dataset Record ID')), (by_sig, row.get('_excel_signature'))):
            if key not in (None, '', 0, '0'):
                index.setdefault(str(key), []).append(row)
    with (nullcontext((connection, None)) if connection is not None else durable_ocr.transaction(globals())) as (con, uid):
        con.execute('select pg_advisory_xact_lock(hashtextextended(?,0))', ('ppmp-excel:'+ptype,))
        records = con.execute("select * from ppmp where type=? and status='Draft' order by id for update", (ptype,)).fetchall()
        for rec in records:
            old_items = parse_items(rec['items'])
            if (rec.get('status') != 'Draft' or rec.get('type') != ptype or rec.get('source_type') == 'OCR'
                    or not isinstance(old_items, list) or not old_items
                    or any(not isinstance(item, dict) or
                           str(item.get('_excel_sheet', '')).strip().upper() != category or
                           not item.get('_excel_signature') for item in old_items)
                    or con.execute('select id from app_plan where ppmp_id=? limit 1', (rec['id'],)).fetchone()):
                result['preserved'] += 1
                continue
            new_items = []
            for old in old_items:
                identifier = old.get('_Dataset Record ID')
                candidates = (by_id.get(str(identifier), []) if identifier not in (None, '', 0, '0')
                              else by_sig.get(str(old['_excel_signature']), []))
                if len(candidates) != 1:
                    break
                fresh = dict(candidates[0])
                if old.get('_PPMP Item ID'):
                    fresh['_PPMP Item ID'] = old['_PPMP Item ID']
                new_items.append(fresh)
            new_total = items_total(new_items) if len(new_items) == len(old_items) else None
            if new_total is None:
                result['preserved'] += 1
                continue
            if (json.dumps(old_items, default=str, sort_keys=True) != json.dumps(new_items, default=str, sort_keys=True)
                    or parse_currency_decimal(rec['total']) != new_total):
                changed = con.execute("""update ppmp set items=?,total=? where id=? and status='Draft'
                    and items=? and not exists(select 1 from app_plan where ppmp_id=ppmp.id)""",
                    (json.dumps(new_items, default=str), new_total, rec['id'], rec['items'])).rowcount
                if changed != 1:
                    raise RuntimeError('PPMP changed during source refresh. Reopen the category.')
                log('Refreshed PPMP Source', 'PPMP', rec['no'], 'Verified workbook lineage; saved item identifiers preserved', connection=con)
                result['updated'] += 1
    return result


def used_excel_rows_for_ppmp(ptype):
    """Return Excel rows/signatures already converted into PPMP.

    Current records save both _row and _excel_signature. We mainly use row numbers
    to keep selected items hidden. This function is recalculated every request,
    so editing the Excel file then pressing Refresh re-reads the current workbook.
    """
    used_rows = set()
    used_signatures = set()
    for row in q('select items from ppmp where type=?', (ptype,)):
        for item in parse_items(row['items']):
            if item.get('_row') not in (None, ''):
                used_rows.add(str(item.get('_row')))
            if item.get('_excel_signature'):
                used_signatures.add(str(item.get('_excel_signature')))
    return used_rows, used_signatures


def available_excel_rows(ptype):
    """Read Excel and hide rows already saved to PPMP."""
    rows = read_excel(ptype)
    if ppmp_creation.repeat_sources_enabled(globals()):
        return rows, set(), len(rows)
    used_rows, used_signatures = used_excel_rows_for_ppmp(ptype)
    available = []
    for r in rows:
        row_key = str(r.get('_row'))
        sig_key = str(r.get('_excel_signature', ''))
        if row_key in used_rows or (sig_key and sig_key in used_signatures):
            continue
        available.append(r)
    return available, used_rows, len(rows)


def parse_items(items_json):
    try: return json.loads(items_json or '[]')
    except: return []

def next_available_records(kind=None):
    """Return only records that are allowed for the next connected process.
    This prevents users from creating disconnected duplicate documents.
    """
    if kind not in (None, 'ppmp', 'app', 'pr', 'aoq', 'nlp', 'iar'):
        raise ValueError('Unknown procurement selector.')
    ppmps = q('''select p.* from ppmp p
                 left join app_plan a on a.ppmp_id=p.id
                 where p.status='Approved' and a.id is null
                 order by p.id desc''') if kind in (None, 'ppmp') else []
    apps = q('''select a.* from app_plan a
                left join pr r on r.app_id=a.id
                where a.status='Approved' and r.id is null
                order by a.id desc''') if kind in (None, 'app') else []
    prs = q('''select r.* from pr r
               left join aoq aq on aq.pr_id=r.id
               where r.status='Approved' and aq.id is null
               order by r.id desc''') if kind in (None, 'pr') else []
    aoqs = q('''select aq.* from aoq aq
                left join po p on p.aoq_id=aq.id
                where aq.status='Approved' and p.id is null
                order by aq.id desc''') if kind in (None, 'aoq') else []
    pos_for_nlp = q('''select p.* from po p
                       left join nlp n on n.po_id=p.id
                       where p.status='Approved' and n.id is null
                       order by p.id desc''') if kind in (None, 'nlp') else []
    pos_for_iar = q('''select p.* from po p
                       join nlp n on n.po_id=p.id
                       left join iar i on i.po_id=p.id
                       where i.id is null
                         and n.status='Approved'
                         and n.result in ('Acceptable','Needs Review')
                       order by p.id desc''') if kind in (None, 'iar') else []
    return ppmps, apps, prs, aoqs, pos_for_nlp, pos_for_iar

def trace_for_po(po_row):
    if not po_row: return {}
    aoq_row = q('select * from aoq where id=?',(po_row['aoq_id'],),one=True)
    pr_row = q('select * from pr where id=?',(aoq_row['pr_id'],),one=True) if aoq_row else None
    app_row = q('select * from app_plan where id=?',(pr_row['app_id'],),one=True) if pr_row and pr_row['app_id'] else None
    ppmp_row = None
    if pr_row and 'ppmp_id' in pr_row.keys() and pr_row['ppmp_id']:
        ppmp_row = q('select * from ppmp where id=?',(pr_row['ppmp_id'],),one=True)
    elif app_row:
        ppmp_row = q('select * from ppmp where id=?',(app_row['ppmp_id'],),one=True)
    return {'ppmp':ppmp_row,'app':app_row,'pr':pr_row,'aoq':aoq_row}

def get_item_description_for_nlp(item, side='pr'):
    """Return the complete natural-language description/specification."""
    if not item:
        return ''
    preferred = [
        'Description / Specification', 'Item Description', 'Description', 'description',
        'Quantity and Size of the Project to be Procured', 'Item Name', 'item', 'Remarks',
    ]
    values = []
    seen = set()
    for key in preferred:
        value = item.get(key)
        text = re.sub(r'\s+', ' ', str(value or '')).strip()
        if text and text.lower() not in seen:
            values.append(text)
            seen.add(text.lower())
    # Preserve other meaningful specification fields instead of forcing a fixed
    # procurement vocabulary. The trained FastText model receives the original wording.
    ignored = {
        '_row', '_source', '_source_record_id', '_source_item_id', '_source_sheet',
        '_PR Item ID', '_PO Item ID', '_AOQ Item ID', '_IAR Item ID',
        'Quantity', 'quantity', 'qty', 'QTY', 'Requested Quantity', 'Awarded Quantity',
        'Unit', 'unit', 'Unit of Measure', 'unit_of_measure', 'uom',
    }
    for key, value in (item or {}).items():
        if key in preferred or key in ignored or str(key).startswith('_'):
            continue
        text = re.sub(r'\s+', ' ', str(value or '')).strip()
        if not text or text.lower() in seen:
            continue
        key_text = re.sub(r'[_\-]+', ' ', str(key)).strip()
        values.append(f'{key_text}: {text}')
        seen.add(text.lower())
    return ' | '.join(values)


def get_item_quantity_for_nlp(item):
    """Read quantity as text input without deciding whether it is acceptable."""
    if not item:
        return 'not provided'
    for key in ('Quantity', 'quantity', 'qty', 'QTY', 'Requested Quantity', 'Awarded Quantity'):
        value = item.get(key)
        if value not in (None, ''):
            return re.sub(r'\s+', ' ', str(value)).strip()
    return 'not provided'


def get_item_unit_for_nlp(item):
    """Read the original unit wording; synonym understanding belongs to the model."""
    if not item:
        return 'not provided'
    for key in ('Unit', 'unit', 'Unit of Measure', 'unit_of_measure', 'uom'):
        value = item.get(key)
        if value not in (None, ''):
            return re.sub(r'\s+', ' ', str(value)).strip()
    return 'not provided'


def serialize_nlp_items(items, side):
    """Serialize the complete approved document as a natural-language model input."""
    document_name = 'PURCHASE REQUEST' if side == 'pr' else 'PURCHASE ORDER'
    parts = [document_name]
    for index, item in enumerate(items or [], start=1):
        description = get_item_description_for_nlp(item, side) or 'description not provided'
        quantity = get_item_quantity_for_nlp(item)
        unit = get_item_unit_for_nlp(item)
        parts.append(
            f'ITEM {index}. DESCRIPTION AND SPECIFICATION: {description}. '
            f'QUANTITY: {quantity}. UNIT: {unit}.'
        )
    if len(parts) == 1:
        parts.append('NO ITEM INFORMATION WAS PROVIDED.')
    return '\n'.join(parts)


def build_pr_text_for_trained_nlp(items):
    return serialize_nlp_items(items, 'pr')


def build_po_text_for_trained_nlp(items):
    return serialize_nlp_items(items, 'po')


def run_trained_nlp_prediction(pr_text, po_text):
    """Run the Colab-trained AI-only FastText model."""
    from nlp_runtime_selection import runtime, model_root
    return runtime(app.config).predict_pair(model_root(app.config, NLP_FASTTEXT_DIR), pr_text, po_text)


def nlp_predict(pr_items, po_items):
    """Return a direct trained-model PR-to-PO prediction.

    There is no hardcoded discrepancy rule, similarity score, hybrid decision,
    threshold-to-class conversion, or fallback model. If the exported Colab
    model is unavailable, verification stops and no NLP record is created.
    """
    retrained = app.config.get('PSMS_NLP_RUNTIME') == 'retrained_rf'
    if retrained:
        if (not pr_items or not po_items or any(not get_item_description_for_nlp(item, side)
                for side, items in (('pr', pr_items), ('po', po_items)) for item in items)):
            raise RuntimeError('RETRAINED_MISSING_ITEM_DESCRIPTION')
    pr_text = build_pr_text_for_trained_nlp(pr_items)
    po_text = build_po_text_for_trained_nlp(po_items)
    prediction = run_trained_nlp_prediction(pr_text, po_text)
    manifest = prediction.get('model_manifest') or {}
    details = {
        'model': manifest.get('deployed_model') or 'FastText + Logistic Regression',
        'architecture': manifest.get('architecture') or 'Pair-aware FastText embeddings with trained status and issue classifiers',
        'decision_mode': 'AI_ONLY_FASTTEXT_DUAL_CLASSIFIER',
        'final_decision_source': 'Direct prediction from the trained FastText status classifier',
        'trained_model_used': True,
        'runtime_rules_used': False,
        'similarity_score_used': False,
        'hybrid_score_used': False,
        'rule_fallback_used': False,
        'manual_threshold_used': False,
        'trained_status_label': prediction['status'],
        'trained_status_confidence': prediction['status_confidence'],
        'trained_status_probabilities': prediction['status_probabilities'],
        'trained_primary_issue': prediction['issue'],
        'trained_issue_confidence': prediction['issue_confidence'],
        'trained_issue_probabilities': prediction['issue_probabilities'],
        'pr_token_count': prediction.get('pr_token_count'),
        'po_token_count': prediction.get('po_token_count'),
        'feature_dimension': prediction.get('feature_dimension'),
        'model_metadata': manifest,
        'model_path': NLP_FASTTEXT_DIR,
        'pr_text': pr_text,
        'po_text': po_text,
    }
    if retrained:
        from nlp_runtime_selection import model_root
        from pr_po_item_matching_v0_1_0 import compare_documents
        # Keep deterministic evidence active even when its optional display
        # switch is off. It never rewrites the two classifier predictions.
        evidence = compare_documents(json.loads(json.dumps(pr_items), parse_float=Decimal),
                                     json.loads(json.dumps(po_items), parse_float=Decimal))
        details.update(decision_mode='EXPERIMENTAL_FASTTEXT_INDEPENDENT_HEADS',
                       final_decision_source='Experimental classifier assistance; human document review and confirmation required',
                       runtime='retrained_rf', model_path=model_root(app.config, NLP_FASTTEXT_DIR),
                       diagnostics=prediction['diagnostics'], deterministic_comparison=evidence,
                       deterministic_checks_used=True, automatic_approval=False, human_confirmation_required=True)
    return prediction['status'], prediction['status_confidence'], details


def nlp_short_finding(result, details):
    """Display the trained issue classifier's direct prediction."""
    try:
        if isinstance(details, str):
            details = json.loads(details or '{}')
    except Exception:
        details = {}
    issue = str((details or {}).get('trained_primary_issue') or '').strip()
    confidence = to_float((details or {}).get('trained_issue_confidence'))
    if issue:
        return f'Trained issue classifier predicted: {issue} ({confidence:.2f}% model probability).'
    return f'Trained status classifier predicted {result or "a result"}.'


def nlp_dynamic_remarks(result, details=None):
    """Show model outputs without adding a second rule-based NLP decision."""
    if isinstance(details, str):
        try:
            details = json.loads(details)
        except (ValueError, TypeError):
            details = {}
    if (details or {}).get('runtime') == 'retrained_rf':
        return (f'Experimental AI status: {result or "Unavailable"}. {nlp_short_finding(result, details)} '
                'Uncalibrated probabilities; AI_PROPOSED_UNVERIFIED labels. This is not a compliance decision. '
                'Review deterministic differences, warnings and original PR/PO before human confirmation.')
    return (
        f'Trained AI status: {result or "Unavailable"}. '
        f'{nlp_short_finding(result, details)} Review the full PR and PO before approving the recorded result.'
    )


def trained_nlp_model_status():
    """Validate the exported FastText model without loading a fallback."""
    if not app.config.get('PSMS_INFERENCE_ENABLED',True):
        return {'active':False,'message':'OCR/NLP inference is disabled pending an approved release.',
                'diagnostic_code':'INFERENCE_DISABLED'}
    try:
        from nlp_runtime_selection import runtime, model_root
        return runtime(app.config).model_status(model_root(app.config, NLP_FASTTEXT_DIR))
    except Exception as error:
        return {'active': False, 'message': f'Unable to inspect FastText model: {error}'}


def algorithm_comparison():
    """Describe the approved research families separately from installed inference."""
    model_state = trained_nlp_model_status()
    active = bool(model_state.get('active'))
    manifest = model_state.get('manifest') or {}
    selected = manifest.get('deployed_model') or 'FastText + Logistic Regression'
    algorithms = []
    for family in ('Logistic Regression', 'Linear SVM', 'Random Forest'):
        name = 'FastText + ' + family
        deployed = active and selected == name
        algorithms.append({
            'name': name,
            'type': 'FastText subword features with ' + family,
            'role': 'Installed inference and approved research approach' if deployed else 'Approved research comparison approach',
            'status': 'Active' if deployed else 'Experimental only; not deployed',
            'deployed': deployed,
            'accuracy': 'Not validated against independently human-reviewed labels.',
            'notes': 'Compare all three approaches using identical eligible training and evaluation partitions. '
                     'Targets pending human review remain AI_PROPOSED_UNVERIFIED. '
                     'This page does not establish that all three approaches were trained or evaluated. '
                     'The installed model remains unchanged.',
        })
    return algorithms


# ---------------------------
# Editable PPMP Table Save Route
# ---------------------------
# Editable PPMP Table Save Route
@app.route('/ppmp/<ptype>/edit_table', methods=['POST'])
@serialize_workbook(lambda: EXCEL_PATH, when=lambda: local_workbook_writes())
def ppmp_edit_table(ptype):
    """Save PPMP table/modal edits back to Excel and Draft PPMP records.

    The PPMP page displays available rows directly from data/PPMP_Data.xlsx.
    Modal edits therefore must update the Excel row itself, not only a saved
    PPMP database record. Any Draft PPMP JSON that still points to the same
    Excel row is also refreshed so the next APP/PR/AOQ/PO/IAR flow stays intact.
    """
    selected_rows = [str(x) for x in request.form.getlist('rows') if str(x).strip()]
    if not selected_rows:
        flash('Select at least one PPMP item before saving changes.')
        return redirect(url_for('ppmp_type', ptype=ptype))

    field_map = {
        'general': 'General Description and Objective of the Project to be Procured',
        'type': 'Type of the Project to be Procured',
        'quantity': 'Quantity and Size of the Project to be Procured',
        'mode': 'Recommended Mode of Procurement',
        'preproc': 'Pre-Procurement Conference Applicable (Yes/No)',
        'start': 'Start of Procurement Activity',
        'end': 'End of Procurement Activity',
        'delivery': 'Expected Delivery/Implementation Period',
        'source': 'Source of Funds',
        'budget': 'Estimated Budget Allocation (Php)',
        'docs': 'Attached Supporting Documents',
        'remarks': 'Remarks',
    }

    edited_rows = {}
    for key, value in request.form.items():
        if '_' not in key:
            continue
        col, row_id = key.rsplit('_', 1)
        row_id = str(row_id)
        if row_id in selected_rows and col in field_map:
            edited_rows.setdefault(row_id, {})[field_map[col]] = value

    if not edited_rows:
        flash('No editable fields were submitted. Open Edit Table, change the selected item, then click Save Changes.')
        return redirect(url_for('ppmp_type', ptype=ptype))

    from durable_workbooks import enabled, edit
    if enabled():
        edit(globals(), ptype, edited_rows, request.form)
        flash('Saved successfully.')
        return redirect(url_for('ppmp_type', ptype=ptype))

    # Save edits to the real Excel sheet used by PPMP.
    try:
        wb = load_workbook(EXCEL_PATH)
        requested = str(ptype or '').strip().lower()
        sheet_name = next((name for name in wb.sheetnames if str(name).strip().lower() == requested), None)
        if not sheet_name:
            wb.close()
            flash(f'Excel sheet for {ptype} was not found.')
            return redirect(url_for('ppmp_type', ptype=ptype))
        ws = wb[sheet_name]
        headers = [str(c.value).strip() if c.value else '' for c in ws[1]]
        header_to_col = {h: idx + 1 for idx, h in enumerate(headers) if h}
        for row_id, fields in edited_rows.items():
            excel_row = int(row_id)
            for header, value in fields.items():
                col_index = header_to_col.get(header)
                if col_index:
                    ws.cell(row=excel_row, column=col_index).value = to_float(value) if header == 'Estimated Budget Allocation (Php)' else value
        atomic_save(wb, EXCEL_PATH)
        wb.close()
    except Exception as e:
        flash(f'PPMP Excel save failed: {e}')
        return redirect(url_for('ppmp_type', ptype=ptype))

    # Refresh any existing Draft PPMP records that came from the edited rows.
    fresh_rows = {str(r.get('_row')): r for r in read_excel(ptype)}
    updated_db = 0
    for rec in q("select * from ppmp where type=? and status='Draft'", (ptype,)):
        items = parse_items(rec['items'])
        changed = False
        for idx, item in enumerate(items):
            row_key = str(item.get('_row', ''))
            if row_key in edited_rows and row_key in fresh_rows:
                items[idx] = fresh_rows[row_key]
                changed = True
        if changed:
            new_total = sum(to_float(x.get('Estimated Budget Allocation (Php)') or x.get('Estimated Total Cost') or x.get('Total Cost') or 0) for x in items)
            exec_db('update ppmp set items=?, total=? where id=?', (json.dumps(items, default=str), new_total, rec['id']))
            updated_db += 1

    # NLP training is intentionally not performed inside the deployed system.
    # Train and validate in Google Colab, then install the exported model folder.
    flash('Saved successfully.')

    return redirect(url_for('ppmp_type', ptype=ptype))

# ---------------------------
# OCR Upload Route
# ---------------------------
# OCR Upload Route
@app.route('/ppmp/<ptype>/upload_ocr', methods=['POST'])
def ppmp_ocr_upload(ptype):
    return durable_ocr.protected(durable_ocr.ppmp_upload, globals(), ptype)


@app.route('/ppmp/<ptype>/check_ocr_upload', methods=['POST'])
def ppmp_ocr_duplicate_check(ptype):
    return durable_ocr.protected(durable_ocr.ppmp_duplicate_check, globals(), ptype)


@app.route('/ppmp/<ptype>/reprocess_ocr', methods=['POST'])
def ppmp_ocr_reprocess(ptype):
    return durable_ocr.protected(durable_ocr.ppmp_upload, globals(), ptype, True)

managed_signatories.register(app, conn, log)


@app.before_request
def ensure():
    """Validate the current account without initialization or record repairs."""
    if request.endpoint in ('static', 'runtime_liveness'):
        return None
    if session.get('logged_in') is not True:
        if session.get('logged_in') is not None:
            session.clear()
        if request.endpoint == 'login':
            return None
        return redirect(url_for('login'))
    user_id = session.get('user_id')
    if type(user_id) is not int or user_id <= 0:
        session.clear()
        return redirect(url_for('login'))
    try:
        account = server_sessions.account_for_session(conn, session.get(server_sessions.COOKIE_KEY), user_id)
    except Exception:
        # A failed account lookup must not grant access or trigger setup/repair.
        return Response('Account validation is temporarily unavailable. Please try again.',
                        status=503, mimetype='text/plain')
    if (not account or account.get('status') != 'Active'
            or account.get('role') not in ('Admin', 'Staff', 'Inspector')):
        session.clear()
        return redirect(url_for('login'))
    # Existing role guards and templates use these claims. Refresh them from the
    # database so demoted roles and renamed accounts take effect on this request.
    session['role'] = account['role']
    session['username'] = account['username']
    g.psms_account = account
    return None

# Authentication/session readiness runs before CSRF validation. No route exemptions.
csrf_protection = init_csrf(app)

@app.route('/login', methods=['GET', 'POST'])
def login():
    if session.get('logged_in'):
        return redirect(url_for('dashboard'))
    if request.method == 'POST':
        username = (request.form.get('username') or '').strip()
        password = request.form.get('password') or ''
        user = q('select * from users where username=?', (username,), one=True)
        valid = False
        if user:
            stored = str(user['password'] or '')
            try:
                valid = check_password_hash(stored, password)
            except Exception:
                valid = False
            if (user.get('status') != 'Active'
                    or user.get('role') not in ('Admin', 'Staff', 'Inspector')):
                valid = False
            if valid and not (stored.startswith('scrypt:') or stored.startswith('pbkdf2:')):
                exec_db('update users set password=? where id=?', (generate_password_hash(password), user['id']))
        if valid:
            try:
                authenticated, sid = server_sessions.issue(
                    conn, user['id'], stored, int(app.permanent_session_lifetime.total_seconds()))
            except server_sessions.Rejected:
                flash('Account changed during sign-in. Please try again.')
                return render_template('login.html', title='Login')
            except server_sessions.Unavailable as error:
                # Fixed diagnostic codes only. Never log the driver exception,
                # submitted account, password, session token or connection URL.
                app.logger.warning('Sign-in unavailable [%s]; cleanup_failed=%s.',
                                   server_sessions.failure_reason(error),
                                   getattr(error, 'cleanup_failed', False) is True)
                return Response('Sign-in is temporarily unavailable. Please contact the administrator.',
                                status=503, mimetype='text/plain')
            session.clear()
            session['logged_in'] = True
            session['user_id'] = authenticated['id']
            session['username'] = authenticated['username']
            session['role'] = authenticated['role']
            session[server_sessions.COOKIE_KEY] = sid
            try:
                exec_db('update users set last_login=? where id=?', (now(), user['id']))
            except Exception:
                pass
            flash('Login successful.')
            return redirect(url_for('dashboard'))
        flash('Invalid username or password.')
    return render_template('login.html', title='Login')

@app.route('/logout', methods=['POST'])
def logout():
    if session.get('logged_in'):
        try:
            server_sessions.revoke_current(
                conn, session.get(server_sessions.COOKIE_KEY), session.get('user_id'),
                audit=lambda con: log('Signed Out', 'Account', session.get('username',''),
                                      'User signed out', connection=con))
        except (server_sessions.Unavailable, server_sessions.Rejected):
            return Response('Sign-out could not be confirmed. Please try again.',
                            status=503, mimetype='text/plain')
    session.clear()
    flash('Logged out successfully.')
    return redirect(url_for('login'))

@app.after_request
def add_no_cache_headers(response):
    response.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
    response.headers['Pragma'] = 'no-cache'
    response.headers['Expires'] = '0'
    return response

@app.route('/')
def dashboard():
    from dashboard_data import build_dashboard
    dashboard_data = build_dashboard(q)
    return render_template('dashboard.html', active='dashboard', title='Dashboard',
                           dashboard_data=dashboard_data,
                           municipal_banner_available=os.path.isfile(os.path.join(
                               BASE_DIR, 'static', 'images', 'asuncion_municipal.jpg')))

@app.route('/excel-status')
def excel_status():
    from durable_workbooks import enabled, read_source
    if enabled():
        source = read_source(globals(), 'ppmp_source')
        return {'excel_path': 'Private PostgreSQL workbook source', 'exists': bool(source),
                'modified': str(source['created_at']) if source else 'No source items yet',
                'size': len(source['content']) if source else 0,
                'sheets': list(PPMP_DESTINATIONS) if source else [], 'error': ''}
    meta = excel_meta()
    sheets = []
    error = ''
    if meta['exists']:
        try:
            wb = load_workbook(EXCEL_PATH, read_only=True, data_only=True)
            sheets = wb.sheetnames
            wb.close()
        except Exception as e:
            error = str(e)
    return {'excel_path': meta['path'], 'exists': meta['exists'], 'modified': meta['modified'], 'size': meta['size'], 'sheets': sheets, 'error': error}

def parse_json_list(value):
    if isinstance(value, list):
        return value
    try:
        parsed = json.loads(value or '[]')
        return parsed if isinstance(parsed, list) else []
    except Exception:
        return []


def dataset_reference_unit_cost(record):
    value = to_float(record.get('unit_value'))
    qty = to_float(record.get('balance_qty')) or to_float(record.get('on_hand_qty'))
    return round(value / qty, 2) if value > 0 and qty > 1 else round(value, 2)


def sync_active_coa_dataset(force=False, display_filename=None, audit_action=None):
    from durable_workbooks import enabled, refresh_inventory
    if enabled():
        return refresh_inventory(globals())
    if not os.path.exists(COA_DATASET_PATH):
        return None
    return sync_inventory_dataset_workbook(
        DATABASE_URL,
        COA_DATASET_PATH,
        display_filename=display_filename or os.path.basename(COA_DATASET_PATH),
        force=force,
        audit=(lambda con, summary: log(audit_action, 'PPMP Inventory Source', summary['filename'],
            f'{summary["sheet_count"]} sheet(s), {summary["row_count"]} valid row(s)',connection=con)) if audit_action else None,
    )

def dataset_record_to_ppmp_item(record, requested_qty, estimated_unit_cost, ptype):
    '''Convert one inventory-source row into official PPMP columns.'''
    ptype = str(ptype or '').strip().upper()
    if ptype not in PPMP_DESTINATIONS:
        raise ValueError('PPMP destination must be DBM, NON-DBM, or LIB.')
    quantity = parse_currency_decimal(requested_qty)
    cost = parse_currency_decimal(estimated_unit_cost)
    if quantity is None or quantity <= 0:
        raise ValueError('Requested quantity must be a finite positive number.')
    if cost is None or cost < 0:
        raise ValueError('Estimated unit cost must be a finite non-negative number.')
    requested_qty = quantity
    estimated_unit_cost = cost
    unit = str(record.get('unit') or 'unit').strip() or 'unit'
    description = str(record.get('description') or record.get('article') or 'Inventory item').strip()
    article = str(record.get('article') or 'Goods').strip()
    try:
        total = (requested_qty * estimated_unit_cost).quantize(Decimal('0.01'))
    except InvalidOperation:
        raise ValueError('Requested quantity or unit cost exceeds the supported precision.') from None
    source_note = f"Inventory source: {record.get('sheet_name')} row {record.get('source_row')}"
    procurement_method = {
        'DBM': 'Agency to Agency',
        'NON-DBM': 'Small Value Procurement',
        'LIB': 'Direct Contracting',
    }[ptype]
    project_type = 'Services' if ptype == 'LIB' else 'Goods'
    return {
        'General Description and Objective of the Project to be Procured': description,
        'Type of the Project to be Procured': project_type,
        'Quantity and Size of the Project to be Procured': f'{requested_qty:g} {unit} - {description}',
        'Recommended Mode of Procurement': procurement_method,
        'Pre-Procurement Conference Applicable (Yes/No)': 'No',
        'Start of Procurement Activity': '',
        'End of Procurement Activity': '',
        'Expected Delivery/Implementation Period': '',
        'Source of Funds': 'General Fund',
        'Estimated Budget Allocation (Php)': round(total, 2),
        'Attached Supporting Documents': 'Inventory reference dataset',
        'Remarks': source_note,
        'PPMP Type': ptype,
        'Category': article,
        'Item Name': description,
        'Item Description': description,
        'Description / Specification': description,
        'Description': description,
        'Quantity': requested_qty,
        'Unit': unit,
        'Estimated Unit Cost': round(estimated_unit_cost, 2),
        'Unit Cost': round(estimated_unit_cost, 2),
        'Estimated Total Cost': round(total, 2),
        'Historical Unit Value': to_float(record.get('unit_value')),
        'Historical Balance Quantity': to_float(record.get('balance_qty')),
        'Historical On Hand Quantity': to_float(record.get('on_hand_qty')),
        'Property Number': record.get('property_number') or '',
        'Office': record.get('office') or record.get('remarks') or '',
        'Person Accountable': record.get('accountable_person') or '',
        '_Source Item ID': source_item_identifier(record.get('id')),
        '_Dataset Record ID': record.get('id'),
        '_Dataset Record Key': record.get('record_key') or '',
        '_Dataset Source Sheet': record.get('sheet_name') or '',
        '_Dataset Source Row': record.get('source_row') or '',
    }

def local_workbook_writes():
    from durable_workbooks import enabled
    return not enabled()


@serialize_workbook(lambda: EXCEL_PATH, when=lambda: local_workbook_writes())
def append_dataset_items_to_ppmp_excel(ptype, items, requesting_office):
    """Append selected inventory items to the chosen PPMP worksheet.

    Sheet names and row positions are resolved at runtime. Metadata columns are
    added once so the inventory source remains traceable when the user
    later generates the PPMP record from the selected category page.
    """
    from durable_workbooks import enabled, append_items
    if enabled():
        return append_items(globals(), ptype, items, requesting_office)
    ptype = str(ptype or '').strip().upper()
    if ptype not in PPMP_DESTINATIONS:
        raise ValueError('Choose DBM, NON-DBM, or LIB as the PPMP destination.')
    if not os.path.exists(EXCEL_PATH):
        raise FileNotFoundError(f'PPMP workbook was not found: {EXCEL_PATH}')

    wb = load_workbook(EXCEL_PATH)
    try:
        requested = ptype.lower()
        sheet_name = next((name for name in wb.sheetnames if str(name).strip().lower() == requested), None)
        if not sheet_name:
            raise ValueError(f'The {ptype} worksheet was not found in PPMP_Data.xlsx.')
        ws = wb[sheet_name]

        headers = [str(cell.value).strip() if cell.value else '' for cell in ws[1]]
        for header in ASUNCION_HEADERS + PPMP_DATASET_META_HEADERS:
            if header not in headers:
                headers.append(header)
                ws.cell(row=1, column=len(headers), value=header)
        header_index = {header: idx + 1 for idx, header in enumerate(headers) if header}

        key_col = header_index['_Dataset Record Key']
        id_col = header_index['_Dataset Record ID']
        existing_keys = set()
        existing_ids = set()
        for row_no in range(2, ws.max_row + 1):
            key = ws.cell(row=row_no, column=key_col).value
            record_id = ws.cell(row=row_no, column=id_col).value
            if key not in (None, ''):
                existing_keys.add(str(key).strip())
            if record_id not in (None, ''):
                existing_ids.add(str(record_id).strip())

        added = 0
        skipped = 0
        added_rows = []
        for item in items:
            key = str(item.get('_Dataset Record Key') or '').strip()
            record_id = str(item.get('_Dataset Record ID') or '').strip()
            if (key and key in existing_keys) or (record_id and record_id in existing_ids):
                skipped += 1
                continue
            row_no = ws.max_row + 1
            item['_Dataset Requesting Office'] = requesting_office
            for header, col_no in header_index.items():
                value = item.get(header, '')
                if isinstance(value, (list, dict)):
                    value = json.dumps(value, default=str)
                ws.cell(row=row_no, column=col_no, value=value)
            if key:
                existing_keys.add(key)
            if record_id:
                existing_ids.add(record_id)
            added += 1
            added_rows.append(row_no)

        if added:
            atomic_save(wb, EXCEL_PATH)
        return {'added': added, 'skipped': skipped, 'rows': added_rows, 'sheet': sheet_name}
    finally:
        wb.close()


@app.route('/ppmp/dataset', methods=['GET', 'POST'])
@serialize_workbook(lambda: COA_DATASET_PATH, when=lambda: request.method == 'POST' and local_workbook_writes())
@protect_workbook_request
def ppmp_dataset():
    '''Dynamic multi-sheet inventory source for the existing PPMP workflow.'''
    if request.method == 'POST':
        action = request.form.get('action') or ''

        if action == 'upload_dataset':
            uploaded = request.files.get('dataset_file')
            if not uploaded or not uploaded.filename:
                flash('Choose an Excel workbook to upload.')
                return redirect(url_for('ppmp_dataset'))
            original_name = secure_filename(uploaded.filename)
            ext = os.path.splitext(original_name)[1].lower()
            if ext not in ALLOWED_DATASET_EXTENSIONS:
                flash('Upload an .xlsx or .xlsm workbook.')
                return redirect(url_for('ppmp_dataset'))
            from durable_workbooks import enabled, import_inventory
            if enabled():
                try:
                    summary = import_inventory(globals(), uploaded)
                except ValueError:
                    flash('No valid bounded inventory data was imported. The previous source was preserved.')
                    return redirect(url_for('ppmp_dataset'))
                flash(f'Inventory dataset imported: {summary["sheet_count"]} sheet(s) and {summary["row_count"]} valid row(s).'
                      if summary['changed'] else 'This workbook is already the active inventory source. No duplicate version was created.')
                return redirect(url_for('ppmp_dataset'))
            os.makedirs(os.path.dirname(COA_DATASET_PATH), exist_ok=True)
            operation_id = secrets.token_hex(16)
            temp_path = COA_DATASET_PATH + '.uploading-' + operation_id + ext
            backup_path = COA_DATASET_PATH + '.backup-' + operation_id
            from private_document_uploads import validate as validate_private_upload
            _, _, validated_bytes = validate_private_upload(uploaded)
            with open(temp_path, 'wb') as stream:
                stream.write(validated_bytes)
            if os.path.exists(COA_DATASET_PATH):
                shutil.copy2(COA_DATASET_PATH, backup_path)
            committed = False
            try:
                os.replace(temp_path, COA_DATASET_PATH)
                summary = sync_active_coa_dataset(force=True, display_filename=original_name, audit_action='Imported Inventory Dataset')
                committed = True
                if os.path.exists(backup_path):
                    os.remove(backup_path)
                flash(f'Inventory dataset imported: {summary["sheet_count"]} sheet(s) and {summary["row_count"]} valid row(s) were read automatically.')
            except Exception as exc:
                if committed or isinstance(exc, DatasetCommitUncertain):
                    flash('Dataset and audit committed; local backup cleanup was not confirmed. No workbook rollback was attempted.'
                          if committed else 'Dataset commit could not be confirmed. Available workbook versions were retained; reconcile before retrying.')
                    return redirect(url_for('ppmp_dataset'))
                if os.path.exists(backup_path):
                    os.replace(backup_path, COA_DATASET_PATH)
                elif os.path.exists(COA_DATASET_PATH):
                    os.remove(COA_DATASET_PATH)
                if os.path.exists(temp_path):
                    os.remove(temp_path)
                flash('Dataset import failed. The previous workbook was retained. Verify repository readiness and retry.')
            return redirect(url_for('ppmp_dataset'))

        if action == 'refresh_dataset':
            try:
                summary = sync_active_coa_dataset(force=True, audit_action='Refreshed Inventory Dataset')
                flash(f'Inventory source refreshed: {summary["row_count"]} active rows from {summary["sheet_count"]} sheet(s).')
            except (PersistentWorkbookUnavailable, WorkbookStorageUnavailable):
                raise
            except Exception:
                flash('Unable to refresh the inventory source. The previous data was preserved; verify storage readiness.')
            return redirect(url_for('ppmp_dataset'))

        destination_actions = {
            'pass_dbm': 'DBM',
            'pass_non_dbm': 'NON-DBM',
            'pass_lib': 'LIB',
        }
        if action in destination_actions or action == 'generate_ppmp':
            destination = destination_actions.get(action) or str(request.form.get('ppmp_destination') or '').strip().upper()
            if destination not in PPMP_DESTINATIONS:
                flash('Choose where the selected inventory items should appear: DBM, NON-DBM, or LIB.')
                return redirect(request.referrer or url_for('ppmp_dataset'))
            from durable_workbooks import enabled, transfer
            if enabled():
                try:
                    result = transfer(globals(), destination, request.form)
                except ValueError:
                    flash('Enter valid quantities and costs. No items were transferred.')
                    return redirect(url_for('ppmp_dataset'))
                flash(f'{result["added"]} item(s) added to {destination}; {result["skipped"]} duplicate(s) skipped. '
                      'Review the items before creating official PPMP records.')
                return redirect(url_for('ppmp_type', ptype=destination))
            selected = [int(x) for x in request.form.getlist('record_ids') if str(x).isdigit()]
            if not selected:
                flash(f'Select at least one inventory item to display in {destination}.')
                return redirect(request.referrer or url_for('ppmp_dataset'))
            office = (request.form.get('requesting_office') or 'General Services Office').strip()
            items = []
            source_details = []
            for record_id in selected:
                row = q('select * from dataset_records where id=? and active=1', (record_id,), one=True)
                if not row:
                    continue
                record = dict(row)
                requested_qty = request.form.get(f'qty_{record_id}')
                estimated_unit_cost = request.form.get(f'cost_{record_id}')
                if estimated_unit_cost in (None, ''):
                    estimated_unit_cost = dataset_reference_unit_cost(record)
                try:
                    items.append(dataset_record_to_ppmp_item(record, requested_qty, estimated_unit_cost, destination))
                except ValueError as error:
                    flash(str(error))
                    return redirect(url_for('ppmp_dataset'))
                source_details.append(f'{record["sheet_name"]} row {record["source_row"]}')

            if not items:
                flash('The selected inventory records are no longer active. Refresh the dataset and try again.')
                return redirect(url_for('ppmp_dataset'))
            try:
                result = append_dataset_items_to_ppmp_excel(destination, items, office)
            except Exception as exc:
                flash(f'Unable to add the inventory items to {destination}: {exc}')
                return redirect(url_for('ppmp_dataset'))

            if result['added']:
                log('Passed Inventory Items to PPMP Category', 'PPMP Inventory Source', destination,
                    f'{result["added"]} item(s) added to {destination}; sources: {", ".join(source_details)}')
            message = f'{result["added"]} selected inventory item(s) added to the {destination} PPMP list.'
            if result['skipped']:
                message += f' {result["skipped"]} duplicate item(s) were already in that category and were skipped.'
            message += ' Review the displayed items, select them, then click Create Official PPMP.'
            flash(message)
            return redirect(url_for('ppmp_type', ptype=destination))

        abort(400, description='Choose a supported inventory source operation.')

    # Import/refresh writes require an explicit CSRF-protected POST.
    auto_sync_message = None
    status = get_inventory_dataset_status(DATABASE_URL)
    selected_sheet = (request.args.get('sheet') or '').strip()
    search_text = (request.args.get('q') or '').strip()
    try:
        page = max(int(request.args.get('page') or 1), 1)
    except Exception:
        page = 1
    per_page = 100
    where = ['active=1']
    params = []
    if selected_sheet:
        where.append('sheet_name=?')
        params.append(selected_sheet)
    if search_text:
        like = f'%{search_text.lower()}%'
        where.append('''(
            lower(coalesce(article,'')) like ? or lower(coalesce(description,'')) like ? or
            lower(coalesce(property_number,'')) like ? or lower(coalesce(unit,'')) like ? or
            lower(coalesce(remarks,'')) like ? or lower(coalesce(office,'')) like ? or
            lower(coalesce(accountable_person,'')) like ? or lower(coalesce(raw_data,'')) like ?
        )''')
        params.extend([like] * 8)
    where_sql = ' and '.join(where)
    total = q(f'select count(*) c from dataset_records where {where_sql}', tuple(params), one=True)['c']
    offset = (page - 1) * per_page
    raw_rows = q(
        f'''select * from dataset_records where {where_sql}
        order by lower(sheet_name), source_row
        limit ? offset ?''',
        tuple(params + [per_page, offset])
    )
    records = []
    for row in raw_rows:
        d = dict(row)
        d['reference_unit_cost'] = dataset_reference_unit_cost(d)
        d['source_id'] = source_item_identifier(d['id'])
        records.append(d)
    page_count = max((total + per_page - 1) // per_page, 1)
    return render_template(
        'ppmp_dataset.html', active='ppmp', records=records, dataset_status=status,
        selected_sheet=selected_sheet, search_text=search_text,
        page=page, page_count=page_count, total=total,
        auto_sync_message=auto_sync_message, title='Inventory Item Source'
    )


@app.route('/ppmp')
def ppmp_home():
    raw_rows=q('select * from ppmp order by id desc')
    rows=[]
    for r in raw_rows:
        d=dict(r)
        d['items_list']=parse_items(d.get('items'))
        payload = ppmp_source_payload(d)
        d['item_count']=len(payload['items'])
        d['item_preview']=item_preview_from_json(d.get('items'), limit=2, include_offer_name=True)
        d['total']=payload['total']
        first_fields = payload['items'][0]['fields'] if payload['items'] else {}
        d['saved_description'] = first_fields.get('General Description and Objective of the Project to be Procured') or '—'
        d['saved_quantity'] = first_fields.get('Quantity and Size of the Project to be Procured') or '—'
        d['saved_budget'] = first_fields.get('Estimated Budget Allocation (Php)')
        rows.append(d)
    dataset_status = get_inventory_dataset_status(DATABASE_URL)
    return render_template('ppmp_home.html', active='ppmp', rows=rows, dataset_status=dataset_status, title='PPMP')


@app.route('/ppmp/<int:id>/view')
def ppmp_review(id):
    row = q('select * from ppmp where id=?', (id,), one=True)
    if not row:
        flash('PPMP record not found.')
        return redirect(url_for('ppmp_home'))
    d = dict(row)
    items = parse_items(d.get('items'))
    sections = [review_section(
        'PPMP Items', items, d['no'],
        'Verify all stored PPMP fields, specifications, schedules, funding information, supporting documents, and amounts.'
    )]
    status = str(d.get('status') or 'Draft')
    can_review = status == 'Draft'
    can_approve = status == 'Reviewed'
    return render_review_page(
        active='ppmp', title=f'Review PPMP {d["no"]}', document_type='Project Procurement Management Plan',
        document_no=d['no'], status=status, created_at=d.get('created_at'),
        linked_records=[
            {'label': 'PPMP Category', 'value': d.get('type') or '—'},
            {'label': 'Source Type', 'value': d.get('source_type') or 'Manual / Imported'},
        ],
        summary=[
            {'label': 'Office', 'value': d.get('office') or '—'},
            {'label': 'Fiscal Year', 'value': d.get('year') or '—'},
            {'label': 'Number of Items', 'value': len(items)},
            {'label': 'PPMP Total', 'value': money_text(d.get('total'))},
        ],
        item_sections=sections,
        review_url=url_for('ppmp_mark_reviewed', id=id),
        review_label=f'Mark PPMP {d["no"]} as Reviewed',
        review_effect='Completing review records that every PPMP item and stored field was checked. Approval becomes available after this step.',
        can_review=can_review,
        approve_url=url_for('ppmp_approve', id=id),
        approve_label=f'Approve PPMP {d["no"]}',
        approval_effect='Approval finalizes this reviewed PPMP and makes it available as a Source PPMP for APP generation.',
        back_url=url_for('ppmp_home'), print_url=url_for('ppmp_print', id=id),
        can_approve=can_approve,
    )


@app.route('/ppmp/<int:id>/review-complete', methods=['POST'])
def ppmp_mark_reviewed(id):
    row = q('select * from ppmp where id=?', (id,), one=True)
    if not row:
        flash('PPMP record not found.')
        return redirect(url_for('ppmp_home'))
    guard = require_review_confirmation('ppmp_review', id)
    if guard:
        return guard
    status = str(row.get('status') or 'Draft')
    if status == 'Approved':
        flash('This PPMP is already approved.')
        return redirect(url_for('ppmp_review', id=id))
    if status == 'Reviewed':
        flash('This PPMP is already marked as reviewed and is ready for approval.')
        return redirect(url_for('ppmp_review', id=id))
    if status != 'Draft':
        flash(f'PPMP cannot be marked Reviewed while its status is {status}.')
        return redirect(url_for('ppmp_review', id=id))
    items = parse_items(row.get('items'))
    if not items:
        flash('PPMP review cannot be completed because no saved items were found.')
        return redirect(url_for('ppmp_review', id=id))
    errors = planning_item_errors(items, require_structured=row.get('source_type') == 'OCR')
    if errors:
        flash(' '.join(errors) + ' Use Edit items before completing review.')
        return redirect(url_for('ppmp_review', id=id))
    changed = audited_update("update ppmp set status=?, reviewed_by=?, reviewed_at=? where id=? and (status='Draft' or status is null or status='') and items=?",
                      ('Reviewed', session.get('username',''), now(), id, row['items']),
                      'Reviewed PPMP', 'PPMP', row['no'], f'{len(items)} item(s) reviewed; ready for approval')
    if changed != 1:
        flash('PPMP status changed. Reopen the review page before continuing.')
        return redirect(url_for('ppmp_review', id=id))
    flash(f'PPMP {row["no"]} marked as Reviewed. It is now ready for approval.')
    return redirect(url_for('ppmp_review', id=id))


@app.route('/ppmp/<int:id>/approve', methods=['POST'])
def ppmp_approve(id):
    row = q('select * from ppmp where id=?', (id,), one=True)
    if not row:
        flash('PPMP record not found.')
        return redirect(url_for('ppmp_home'))
    guard = require_review_confirmation('ppmp_review', id)
    if guard:
        return guard
    status = str(row.get('status') or 'Draft')
    if status == 'Approved':
        flash('PPMP is already approved.')
        return redirect(url_for('ppmp_review', id=id))
    if status != 'Reviewed':
        flash('PPMP must be marked Reviewed before it can be approved.')
        return redirect(url_for('ppmp_review', id=id))
    errors = planning_item_errors(parse_items(row.get('items')), require_structured=row.get('source_type') == 'OCR')
    if errors:
        flash(' '.join(errors) + ' Correct the item before approval.')
        return redirect(url_for('ppmp_review', id=id))
    changed = audited_update("update ppmp set status=?, approved_by=?, approved_at=? where id=? and status='Reviewed' and items=?",
                      ('Approved', session.get('username',''), now(), id, row['items']),
                      'Approved PPMP', 'PPMP', row['no'], 'PPMP approved after explicit review completion')
    if changed != 1:
        flash('PPMP status changed. Reopen the review page before approving.')
        return redirect(url_for('ppmp_review', id=id))
    flash(f'PPMP {row["no"]} approved. It is now available in APP as a Source PPMP.')
    return redirect(url_for('ppmp_home'))


@app.route('/ppmp/<ptype>', methods=['GET','POST'])
def ppmp_type(ptype):
    if ptype not in PPMP_DESTINATIONS:
        abort(404)
    if request.method == 'POST' and request.form.get('action') in ('create_ppmp', 'generate_ppmp'):
        return durable_ocr.protected(ppmp_creation.create, globals(), ptype)
    if request.method == 'POST' and request.form.get('action') in ('clear_ocr','update_ocr_row','update_ocr_rows','save_ocr_rows'):
        return durable_ocr.protected(durable_ocr.ppmp_post, globals(), ptype)
    if request.method == 'POST' and request.form.get('action') == 'ocr':
        return ppmp_ocr_upload(ptype)
    # Always read Excel from disk, then hide rows that are already saved into PPMP.
    # This keeps the process clean: Excel item -> PPMP -> APP -> PR -> AOQ -> PO.
    if request.method == 'POST' and request.form.get('action') == 'refresh':
        refresh = sync_draft_ppmp_from_excel(ptype)
        if refresh['source_unavailable']:
            flash('No matching workbook source rows are available for this category. Saved PPMP records were preserved.')
        elif refresh['preserved']:
            flash('Saved records with OCR, downstream or uncertain source lineage were preserved. Review source changes explicitly.')
        return redirect(url_for('ppmp_type', ptype=ptype))
    data, used_rows, total_excel_rows = available_excel_rows(ptype)
    for source_row in data:
        source_row['_creation_signature'] = ppmp_creation.source_signature(source_row)
    meta = excel_meta()
    if meta.get('unavailable'):
        flash('Workbook source selection is temporarily unavailable. Saved records and OCR drafts remain available for review.')
    if request.method == 'POST':
        if request.form.get('action') == 'save_table':
            return redirect(url_for('ppmp_edit_table', ptype=ptype))
        abort(400, description='Unknown PPMP action.')
    ocr_state = load_ppmp_ocr_state()
    ocr_rows = durable_ocr.ppmp_rows(globals(), ptype, state=ocr_state)
    if ocr_rows and not ocr_state:
        latest = ocr_rows[0]
        ocr_state = {'text': latest.get('raw_ocr_text') or '', 'method': latest.get('extraction_method') or 'local extraction',
                     'warnings': [], 'filename': latest.get('source_filename') or 'Stored OCR draft'}
    ocr_field_labels = ppmp_review_labels()
    # Keep full extraction payloads server-side; load original bytes on demand.
    for row in ocr_rows:
        row.pop('raw_ocr_text', '')
        row.pop('fields_json', None)
        row.pop('confidence_json', None)
        row['source_url'] = url_for('ppmp_original_source', ptype=ptype, sid=row['source_id']) if row.get('source_id') else ''
        row['diagnostics_url'] = url_for('ppmp_source_diagnostics', ptype=ptype, sid=row['source_id']) if row.get('source_id') and is_admin() else ''
    return render_template('ppmp_type.html', active='ppmp', ptype=ptype, data=data, used_count=len(used_rows), total_excel_rows=total_excel_rows, title=f'PPMP - {ptype}', excel=os.path.basename(EXCEL_PATH), excel_meta=meta, ocr_state=ocr_state, ocr_rows=ocr_rows, ocr_field_labels=ocr_field_labels, creation_token=ppmp_creation.creation_token(ptype))


@app.route('/ppmp/<ptype>/sources/<uuid:sid>')
def ppmp_original_source(ptype, sid):
    return durable_ocr.protected(durable_ocr.ppmp_source, globals(), ptype, sid)


@app.route('/ppmp/<ptype>/sources/<uuid:sid>/diagnostics')
def ppmp_source_diagnostics(ptype, sid):
    return durable_ocr.protected(durable_ocr.ppmp_source, globals(), ptype, sid, True)

@app.route('/ppmp/<int:id>/items', methods=['POST'])
def ppmp_update_saved_items(id):
    row = q('select * from ppmp where id=?', (id,), one=True)
    if not row:
        flash('PPMP record not found.')
        return redirect(url_for('ppmp_home'))
    if row.get('status') == 'Approved':
        flash('Approved PPMP records cannot be edited.')
        return redirect(url_for('ppmp_review', id=id))
    if ppmp_has_downstream_app(id):
        flash('This PPMP already has a downstream APP and can no longer be edited.')
        return redirect(url_for('ppmp_review', id=id))
    items = parse_items(row['items'])
    if not items:
        flash('No PPMP items found to update.')
        return redirect(url_for('ppmp_home'))
    try:
        selected_index = int(request.form.get('item_index') or 0)
    except Exception:
        selected_index = 0
    if selected_index < 0 or selected_index >= len(items):
        selected_index = 0
    field_map = {
        'general': 'General Description and Objective of the Project to be Procured',
        'type': 'Type of the Project to be Procured',
        'quantity': 'Quantity and Size of the Project to be Procured',
        'mode': 'Recommended Mode of Procurement',
        'preproc': 'Pre-Procurement Conference Applicable (Yes/No)',
        'start': 'Start of Procurement Activity',
        'end': 'End of Procurement Activity',
        'delivery': 'Expected Delivery/Implementation Period',
        'source': 'Source of Funds',
        'budget': 'Estimated Budget Allocation (Php)',
        'docs': 'Attached Supporting Documents',
        'remarks': 'Remarks',
    }
    # A changed narrative invalidates its previous structured interpretation.
    from procurement_item_contract import ITEM_REVIEW_FIELDS
    if any(key in request.form and _text_value(request.form[key]) != _text_value(items[selected_index].get(field_map[key])) for key in ('general', 'quantity')):
        for key in ITEM_REVIEW_FIELDS:
            items[selected_index].pop(key, None)
    for key in ITEM_REVIEW_FIELDS:
        if key in request.form:
            items[selected_index][key] = _text_value(request.form[key])
            items[selected_index]['_Item Contract Version'] = 1
    for form_key, item_key in field_map.items():
        if form_key in request.form:
            value = request.form.get(form_key)
            if form_key == 'budget':
                parsed = parse_currency_decimal(value)
                if _text_value(value) and parsed is None:
                    flash('Estimated Budget is invalid. Enter a value such as 80000 or PHP 80,000.00.')
                    return redirect(url_for('ppmp_home'))
                items[selected_index][item_key] = float(parsed) if parsed is not None else ''
            else:
                items[selected_index][item_key] = _text_value(value)
    items[selected_index] = normalize_excel_row(items[selected_index], row['type'])
    errors = planning_item_errors([items[selected_index]])
    if errors:
        flash('Changes were not saved. ' + ' '.join(errors))
        return redirect(url_for('ppmp_home'))
    calculated_total = items_total(items)
    new_total = calculated_total if calculated_total is not None else 0.0
    changed = audited_update("""update ppmp set items=?, total=?, status=?, reviewed_by=null, reviewed_at=null,
                         approved_by=null, approved_at=null where id=?
                         and (status is null or status <> 'Approved')
                         and items=? and not exists (select 1 from app_plan where ppmp_id=ppmp.id)""",
                      (json.dumps(items, default=str), new_total, 'Draft', id, row['items']),
                      'Updated PPMP Item', 'PPMP', row['no'], f'Updated saved PPMP item {selected_index + 1}')
    if changed != 1:
        flash('PPMP was not changed because it was approved, linked to an APP, or removed. Reopen the review page.')
        return redirect(url_for('ppmp_review', id=id))
    flash('PPMP item updated successfully. Review and approval were reset because the PPMP changed.')
    return redirect(url_for('ppmp_home'))

@app.route('/ppmp/<int:id>/print')
def ppmp_print(id):
    row=q('select * from ppmp where id=?',(id,),one=True)
    if not row:
        abort(404)
    items=parse_items(row['items'])
    return render_template('print_ppmp.html', row=row, items=items, **managed_signatories.print_context(conn, 'ppmp', dict(row)), title='Print PPMP')

@app.route('/api/ppmp/<int:id>')
@app.route('/api/ppmp/<int:id>/source')
def ppmp_source_api(id):
    """Return the exact official PPMP selected by APP, including all 12 fields."""
    row = q('select * from ppmp where id=?', (id,), one=True)
    if not row:
        return jsonify({'error': 'PPMP record not found.'}), 404
    payload = ppmp_source_payload(row)
    return jsonify(payload)


@app.route('/app', methods=['GET','POST'])
def app_plan():
    if request.method=='POST':
        pid = request.form.get('ppmp_id')
        p = q("select * from ppmp where id=? and status='Approved'", (pid,), one=True)
        if not p:
            flash('Select an approved Source PPMP. A PPMP must be reviewed and approved before APP generation.')
            return redirect(url_for('app_plan'))

        items = parse_items(p['items'])
        if not items:
            flash('This PPMP has no saved items, so APP cannot be generated.')
            return redirect(url_for('app_plan'))
        errors = planning_item_errors(items, require_structured=p.get('source_type') == 'OCR')
        if errors:
            flash('Cannot generate APP: ' + ' '.join(errors))
            return redirect(url_for('app_plan'))

        prepared = []
        try:
            for item in items:
                canonical = canonical_ppmp_fields(item)
                budget = parse_currency_decimal(canonical.get('Estimated Budget Allocation (Php)'))
                if budget is None:
                    raise ValueError('Estimated Budget is invalid or missing.')
                # Every generated APP item is a copy of the exact official PPMP item.
                # The reviewed PPMP values are never remapped/re-extracted here.
                prepared.append((dict(item), float(budget)))
        except ValueError as exc:
            flash(f'Cannot generate APP from {p["no"]}: {exc} Review the selected PPMP first.')
            return redirect(url_for('app_plan'))

        # Reserve document numbers first. Sequence gaps are acceptable if a later
        # database transaction rolls back; partial APP records are not.
        app_nos = [next_document_no('APP', 'APP', 'app_plan', year=p['year'] or current_year()) for _ in prepared]
        con = conn()
        created_nos = []
        try:
            # Lock the selected PPMP so the exact source record cannot change while
            # APP rows are being generated.
            locked = con.execute('select * from ppmp where id=? for update', (p['id'],)).fetchone()
            if not locked:
                raise ValueError('The selected Source PPMP no longer exists.')
            if str(locked.get('status') or '') != 'Approved':
                raise ValueError('The selected Source PPMP is no longer approved. Review and approve it again before APP generation.')
            # Prevent stale/duplicate browser submissions from generating a second APP.
            if con.execute('select id from app_plan where ppmp_id=? limit 1', (p['id'],)).fetchone():
                raise ValueError('An APP has already been generated from this PPMP.')

            for (item, item_total), app_no in zip(prepared, app_nos):
                app_items = assign_item_identifiers([item], '_APP Item ID', app_no)
                con.execute(
                    'insert into app_plan(no,ppmp_id,year,office,status,created_at,total,items) values(?,?,?,?,?,?,?,?)',
                    (app_no, locked['id'], locked['year'], locked['office'], 'For Review', now(), item_total,
                     json.dumps(app_items, ensure_ascii=False, default=str))
                )
                created_nos.append(app_no)
            con.execute('insert into audit_trail(dt,"user",action,module,ref,details) values(?,?,?,?,?,?)',
                        (now(), session.get('username','admin'), 'Generated APP', 'APP', ', '.join(created_nos),
                         f'from {locked["no"]}; {len(prepared)} APP item row(s); exact selected PPMP id {locked["id"]}'))
            con.commit()
        except Exception as exc:
            con.rollback()
            flash(f'APP generation failed: {exc}')
            return redirect(url_for('app_plan'))
        finally:
            con.close()

        flash('APP generated successfully from the selected Source PPMP.')
        return redirect(url_for('app_plan'))

    raw_plans = q("""select a.*, p.no ppmp_no, p.type ppmp_type, p.items ppmp_items, a.items app_items
                     from app_plan a
                     left join ppmp p on p.id=a.ppmp_id
                     order by a.id desc""")
    plans = []
    for r in raw_plans:
        d = dict(r)
        source_items = d.get('app_items') or d.get('ppmp_items')
        d['item_count'] = item_count_from_json(source_items)
        d['item_preview'] = item_preview_from_json(source_items, include_offer_name=True)
        plans.append(d)
    raw_ppmps, _, _, _, _, _ = next_available_records('ppmp')
    ppmps = []
    for p in raw_ppmps:
        d = dict(p)
        payload = ppmp_source_payload(d)
        d['item_count'] = len(payload['items'])
        d['item_preview'] = item_preview_from_json(d.get('items'), limit=1, include_offer_name=True)
        d['search_terms'] = item_preview_from_json(d.get('items'), limit=200, include_offer_name=True)
        d['source_preview'] = app_source_preview_from_items(d.get('items'))
        # Prefer the value recomputed from the exact item JSON. This also repairs
        # the display of historical OCR PPMPs whose parent total was saved as 0.
        d['total'] = payload['total']
        ppmps.append(d)
    return render_template('app.html', active='app', plans=plans, ppmps=ppmps, title='APP')

@app.route('/app/<int:id>/view')
def app_review(id):
    row = q('''select a.*, p.no ppmp_no, p.type ppmp_type, p.items ppmp_items
               from app_plan a left join ppmp p on p.id=a.ppmp_id
               where a.id=?''', (id,), one=True)
    if not row:
        flash('APP record not found.')
        return redirect(url_for('app_plan'))
    d = dict(row)
    app_items = parse_items(d.get('items') or d.get('ppmp_items'))
    ppmp_items = parse_items(d.get('ppmp_items'))
    sections = [review_section('APP Items for Approval', app_items, d['no'],
                               'Verify every procurement item, amount, schedule, and source information.')]
    if ppmp_items and d.get('items'):
        sections.append(review_section('Compare with Source PPMP', ppmp_items, d.get('ppmp_no') or 'PPMP',
                                       'Original PPMP values for comparison. Item identifiers can differ between documents.', optional=True))
    return render_review_page(
        active='app', title=f'Review APP {d["no"]}', document_type='Annual Procurement Plan',
        document_no=d['no'], status=d['status'], created_at=d.get('created_at'),
        linked_records=[
            {'label': 'Source PPMP', 'value': d.get('ppmp_no') or 'No linked PPMP'},
            {'label': 'PPMP Category', 'value': d.get('ppmp_type') or 'Not recorded'},
        ],
        summary=[
            {'label': 'Office', 'value': d.get('office') or '—'},
            {'label': 'Fiscal Year', 'value': d.get('year') or '—'},
            {'label': 'Number of Items', 'value': len(app_items)},
            {'label': 'APP Total', 'value': money_text(d.get('total'))},
        ],
        item_sections=sections,
        approve_url=url_for('app_approve', id=id),
        approve_label=f'Approve APP {d["no"]}',
        approval_effect='Approval finalizes this APP and makes its planning record officially approved.',
        back_url=url_for('app_plan'), print_url=url_for('app_print', id=id),
        can_approve=d.get('status') != 'Approved',
    )

@app.route('/app/<int:id>/approve', methods=['POST'])
def app_approve(id):
    row=q('select * from app_plan where id=?',(id,),one=True)
    if not row:
        flash('APP record not found.')
        return redirect(url_for('app_plan'))
    guard = require_review_confirmation('app_review', id)
    if guard:
        return guard
    if row['status'] == 'Approved':
        flash('APP is already approved.')
        return redirect(url_for('app_review', id=id))
    changed = audited_update('update app_plan set status=? where id=? and status is not distinct from ? and items is not distinct from ?',
        ('Approved',id,row.get('status'),row.get('items')), 'Approved APP','APP',row['no'],'APP approved after full item review')
    if changed != 1:
        flash('APP changed during approval. Reopen its review page.')
        return redirect(url_for('app_review', id=id))
    flash(f'APP {row["no"]} approved after reviewing all items.')
    return redirect(url_for('app_plan'))

@app.route('/app/<int:id>/print')
def app_print(id):
    row = q('select * from app_plan where id=?', (id,), one=True)
    if not row:
        abort(404)
    p = q('select * from ppmp where id=?', (row['ppmp_id'],), one=True) if row else None
    if row.get('ppmp_id') is not None and not p:
        abort(404)
    # Use the APP row's own item first. Older records without app_plan.items fall back to PPMP items.
    items = parse_items(row['items']) if row and 'items' in row.keys() and row['items'] else (parse_items(p['items']) if p else [])
    app_rows = build_app_rows(items, row['office'] if row else '')
    return render_template('print_app.html', row=row, items=items, app_rows=app_rows, **managed_signatories.print_context(conn, 'app', dict(row)), title='Print APP')

@app.route('/pr', methods=['GET','POST'])
def pr():
    if request.method == 'POST':
        return procurement_creation.create_pr(globals(), request.form.get('app_id'))

    raw_prs = q('''select r.*, p.no ppmp_no, p.type ppmp_type, a.no app_no
                   from pr r
                   left join ppmp p on p.id=r.ppmp_id
                   left join app_plan a on a.id=r.app_id
                   order by r.id desc''')
    prs = []
    for row in raw_prs:
        d = dict(row)
        d['item_preview'] = item_preview_from_json(d.get('items'), limit=2, include_offer_name=True)
        d['item_count'] = item_count_from_json(d.get('items'))
        prs.append(d)

    _, raw_apps, _, _, _, _ = next_available_records('app')
    apps = []
    for row in raw_apps:
        d = dict(row)
        source = d.get('items')
        d['item_preview'] = item_preview_from_json(source, limit=2, include_offer_name=True)
        d['item_count'] = item_count_from_json(source)
        ppmp_row = q('select no,type,items from ppmp where id=?', (d.get('ppmp_id'),), one=True)
        source = source or (ppmp_row.get('items') if ppmp_row else None)
        d['purpose'] = source_pr_purpose(parse_items(source))
        d['search_terms'] = item_preview_from_json(source, limit=200, include_offer_name=True)
        d['ppmp_no'] = ppmp_row['no'] if ppmp_row else 'No linked PPMP'
        d['ppmp_type'] = ppmp_row['type'] if ppmp_row else ''
        apps.append(d)
    return render_template('pr.html', active='pr', prs=prs, apps=apps, title='PR')

@app.route('/pr/<int:id>/view')
def pr_review(id):
    row = q('''select r.*, p.no ppmp_no, p.type ppmp_type, p.items ppmp_items,
                      a.no app_no
               from pr r
               left join ppmp p on p.id=r.ppmp_id
               left join app_plan a on a.id=r.app_id
               where r.id=?''', (id,), one=True)
    if not row:
        flash('PR record not found.')
        return redirect(url_for('pr'))
    d = dict(row)
    pr_items = parse_items(d.get('items'))
    source_items = parse_items(d.get('ppmp_items'))
    sections = [review_section('Purchase Request Items', pr_items, d['no'],
                               'These are the final requirements that will be used for bidding and NLP comparison.')]
    if source_items:
        sections.append(review_section('Compare with Source PPMP', source_items, d.get('ppmp_no') or 'PPMP',
                                       'Original PPMP values for comparison. Review any changed requirements against the PR.', optional=True))
    return render_review_page(
        active='pr', title=f'Review PR {d["no"]}', document_type='Purchase Request',
        document_no=d['no'], status=d['status'], created_at=d.get('created_at'),
        linked_records=[
            {'label': 'Source PPMP', 'value': d.get('ppmp_no') or 'No linked PPMP'},
            {'label': 'Related APP', 'value': d.get('app_no') or 'Not linked'},
            {'label': 'PPMP Category', 'value': d.get('ppmp_type') or 'Not recorded'},
        ],
        summary=[
            {'label': 'Requesting Office', 'value': d.get('office') or '—'},
            {'label': 'Purpose', 'value': d.get('purpose') or '—'},
            {'label': 'Number of Items', 'value': len(pr_items)},
            {'label': 'Requested Total', 'value': money_text(d.get('total'))},
        ],
        item_sections=sections,
        approve_url=url_for('pr_approve', id=id), approve_label=f'Approve PR {d["no"]}',
        approval_effect='Approval locks this PR as the official requirement and makes it available for AOQ bidding-result upload.',
        back_url=url_for('pr'), print_url=url_for('pr_print', id=id),
        can_approve=d.get('status') != 'Approved',
    )

@app.route('/pr/<int:id>/approve', methods=['POST'])
def pr_approve(id):
    row = q('select * from pr where id=?', (id,), one=True)
    if not row:
        flash('PR record not found.')
        return redirect(url_for('pr'))
    guard = require_review_confirmation('pr_review', id)
    if guard:
        return guard
    if row['status'] == 'Approved':
        flash('PR is already approved.')
        return redirect(url_for('pr_review', id=id))
    changed = audited_update('update pr set status=? where id=? and status is not distinct from ? and items is not distinct from ?',
        ('Approved',id,row.get('status'),row.get('items')), 'Approved PR','PR',row['no'],'Purchase Request approved')
    if changed != 1:
        flash('PR changed during approval. Reopen its review page.')
        return redirect(url_for('pr_review', id=id))
    flash('PR approved. Continue to AOQ to upload and review the bidding result.')
    return redirect(url_for('aoq'))

@app.route('/pr/<int:id>/print')
def pr_print(id):
    row=q('select * from pr where id=?',(id,),one=True)
    if not row:
        abort(404)
    items=parse_items(row['items'])
    return render_template('print_pr.html', row=row, items=items, display_items=review_item_rows(items), **managed_signatories.print_context(conn, 'pr', dict(row)), title='Print PR')

@app.route('/aoq', methods=['GET','POST'])
def aoq():
    if request.method == 'POST':
        return durable_ocr.protected(durable_ocr.aoq_post, globals())
    raw_aoq_rows = q('''select aq.*, r.no pr_no, r.office pr_office from aoq aq
                left join pr r on r.id=aq.pr_id
                order by aq.id desc''')
    rows=[]
    for aq in raw_aoq_rows:
        d=dict(aq)
        d['item_preview']=item_preview_from_json(d.get('winning_offer'),limit=2, include_offer_name=True)
        rows.append(d)
    _, _, prs, _, _, _ = next_available_records('pr')
    pr_previews = []
    for r in prs:
        d = dict(r)
        d['item_preview'] = item_preview_from_json(d.get('items'), limit=2, include_offer_name=True)
        d['search_terms'] = item_preview_from_json(d.get('items'), limit=200, include_offer_name=True)
        pr_previews.append(d)
    ocr_data = load_aoq_preview()
    selected_pr = None
    if ocr_data and ocr_data.get('pr_id'):
        selected_pr_row = q('select * from pr where id=?', (ocr_data.get('pr_id'),), one=True)
        if selected_pr_row:
            selected_pr = dict(selected_pr_row)
            selected_pr['items_list'] = parse_items(selected_pr.get('items'))
    return render_template(
        'aoq.html', active='aoq', rows=rows, prs=pr_previews,
        ocr_data=ocr_data or None, selected_pr=selected_pr, title='AOQ',
        supported_aoq_formats=', '.join(sorted(x.upper().lstrip('.') for x in ALLOWED_OCR_EXTENSIONS)),
    )


@app.route('/aoq/<int:id>/view')
def aoq_review(id):
    row = q('''select aq.*, r.no pr_no, r.office pr_office, r.items pr_items,
                      r.total pr_total,
                      exists(select 1 from psms_documents.aoq_drafts ds
                             where ds.official_aoq_id=aq.id and ds.status='CONFIRMED') has_durable_source
               from aoq aq left join pr r on r.id=aq.pr_id
               where aq.id=?''', (id,), one=True)
    if not row:
        flash('AOQ record not found.')
        return redirect(url_for('aoq'))
    d = dict(row)
    pr_items = parse_items(d.get('pr_items'))
    offer_items = parse_items(d.get('winning_offer'))
    suppliers = parse_items(d.get('suppliers'))
    return render_review_page(
        active='aoq', title=f'Review AOQ {d["no"]}', document_type='AOQ / Bidding Result',
        document_no=d['no'], status=d['status'], created_at=d.get('created_at'),
        linked_records=[
            {'label': 'Related PR', 'value': d.get('pr_no') or 'No linked PR'},
            {'label': 'Source File', 'value': d.get('ocr_filename') or 'Legacy/manual record',
             'url': url_for('aoq_source_file', id=id) if d.get('stored_filename') or d.get('has_durable_source') else ''},
            {'label': 'Detected Document Type', 'value': d.get('source_type') or 'Legacy/manual record'},
            {'label': 'Extraction Method', 'value': d.get('extraction_method') or 'Legacy/manual extraction'},
        ],
        summary=[
            {'label': 'Requesting Office', 'value': d.get('pr_office') or '—'},
            {'label': 'Winning Supplier', 'value': d.get('winner') or '—'},
            {'label': 'Winning Amount', 'value': money_text(d.get('winning_amount'))},
            {'label': 'Winning Offer Items', 'value': len(offer_items)},
        ],
        item_sections=[
            review_section('Approved PR Requirements', pr_items, d.get('pr_no') or 'PR',
                           'Use this section to confirm what the procurement originally requested.'),
            review_section('OCR-Reviewed Winning Offer', offer_items, d['no'],
                           'Double-check the supplier offer, description, specification, quantity, unit, and price before approval.'),
        ],
        text_blocks=[
            {'title': 'Supplier / Quotation Records', 'content': json.dumps(suppliers, indent=2, ensure_ascii=False, default=str) if suppliers else 'No supplier list stored.'},
            {'title': 'Flexible Extraction Profile', 'content': json.dumps(parse_json_object(d.get('extraction_profile')), indent=2, ensure_ascii=False, default=str) if d.get('extraction_profile') else 'No extraction profile stored.'},
            {'title': 'Extraction Warnings', 'content': json.dumps(parse_json_list(d.get('extraction_warnings')), indent=2, ensure_ascii=False, default=str) if d.get('extraction_warnings') else 'No extraction warnings stored.'},
            {'title': 'Raw Extracted Text', 'content': d.get('ocr_text') or 'No raw extracted text stored.'},
        ],
        approve_url=url_for('aoq_approve', id=id), approve_label=f'Approve AOQ {d["no"]}',
        approval_effect='Approval locks the reviewed winning offer and makes this AOQ available for Purchase Order generation.',
        back_url=url_for('aoq'), print_url=None, can_approve=d.get('status') != 'Approved',
        approval_details=[('AOQ', d['no']), ('Linked PR', d.get('pr_no') or 'Not linked'),
                          ('Winning supplier', d.get('winner') or 'Not selected'),
                          ('Winning amount', money_text(d.get('winning_amount'))), ('Item count', len(offer_items))],
    )

@app.route('/aoq/<int:id>/source')
def aoq_source_file(id):
    private_source = durable_ocr.aoq_source(globals(), id)
    if private_source is not None:
        return private_source
    row = q('select ocr_filename, stored_filename from aoq where id=?', (id,), one=True)
    if not row or not row['stored_filename']:
        flash('The original AOQ source file is not available for this record.')
        return redirect(url_for('aoq_review', id=id))
    stored = os.path.basename(str(row['stored_filename']))
    path = os.path.join(AOQ_UPLOAD_DIR, stored)
    if not os.path.exists(path):
        flash('The original AOQ source file could not be found.')
        return redirect(url_for('aoq_review', id=id))
    return send_from_directory(
        AOQ_UPLOAD_DIR, stored, as_attachment=False,
        download_name=row['ocr_filename'] or stored,
    )


@app.route('/aoq/<int:id>/approve', methods=['POST'])
def aoq_approve(id):
    from aoq_flexible import winning_offer_error, winning_total_error
    guard = require_review_confirmation('aoq_review', id)
    if guard:
        return guard
    if request.form.get('final_confirm') != 'yes':
        abort(400, description='Explicit final approval confirmation is required.')
    with durable_ocr.transaction(globals()) as (con, uid):
        row = con.execute('select * from aoq where id=? for update', (id,)).fetchone()
        if not row:
            abort(404)
        if row['status'] != 'For Review':
            abort(409, description='AOQ is no longer awaiting approval. Reopen its review page.')
        items = parse_items(row.get('winning_offer'))
        error = winning_offer_error(items) or winning_total_error(items, row.get('winning_amount'))
        if not error and (not isinstance(row.get('winner'), str) or not row['winner'].strip()):
            error = 'Select the official winning supplier before approving this AOQ.'
        if error:
            flash(error)
            return redirect(url_for('aoq_review', id=id))
        con.execute("update aoq set status=? where id=? and status='For Review'", ('Approved', id))
        log('Approved AOQ', 'AOQ', row['no'], 'OCR-created AOQ approved after explicit final confirmation', connection=con)
    flash('AOQ approved. Continue to Purchase Order generation.')
    return redirect(url_for('po'))

@app.route('/po', methods=['GET','POST'])
def po():
    from aoq_flexible import winning_offer_error, winning_total_error, reviewed_total, review_number

    if request.method=='POST':
        return procurement_creation.create_po(globals(), request.form.get('aoq_id'))
    rows=q("""select p.*, aq.no aoq_no, aq.winner, r.no pr_no
              from po p
              left join aoq aq on aq.id=p.aoq_id
              left join pr r on r.id=aq.pr_id
              order by p.id desc""")
    _,_,_,raw_aoqs,_,_ = next_available_records('aoq')
    aoqs=[]
    for a in raw_aoqs:
        d=dict(a)
        pr_row=q('select no,items,office from pr where id=?',(a['pr_id'],),one=True)
        d['pr_no']=pr_row['no'] if pr_row else 'No linked PR'
        d['item_preview']=item_preview_from_json(a['winning_offer'],limit=1, include_offer_name=True)
        d['search_terms']=item_preview_from_json(a['winning_offer'],limit=200, include_offer_name=True)
        d['offer_summary'] = '; '.join(f"{value_any(item, 'item', 'Item Name', default='Item')}: {quantity_text(item.get('qty'))} {item.get('unit') or '—'} at {source_money_text(item.get('unit_cost'))}"
                                      for item in parse_items(a['winning_offer']))
        aoqs.append(d)
    return render_template('po.html', active='po', rows=rows, aoqs=aoqs, title='PO')

@app.route('/po/<int:id>/view')
def po_review(id):
    row = q('''select p.*, aq.no aoq_no, aq.winner aoq_winner,
                      aq.winning_offer aoq_items, r.no pr_no, r.items pr_items,
                      r.office pr_office
               from po p
               left join aoq aq on aq.id=p.aoq_id
               left join pr r on r.id=aq.pr_id
               where p.id=?''', (id,), one=True)
    if not row:
        flash('PO record not found.')
        return redirect(url_for('po'))
    d = dict(row)
    po_items = parse_items(d.get('items'))
    return render_review_page(
        active='po', title=f'Review PO {d["no"]}', document_type='Purchase Order',
        document_no=d['no'], status=d['status'], created_at=d.get('created_at'),
        linked_records=[
            {'label': 'Related PR', 'value': d.get('pr_no') or 'No linked PR'},
            {'label': 'Source AOQ', 'value': d.get('aoq_no') or 'No linked AOQ'},
        ],
        summary=[
            {'label': 'Supplier', 'value': d.get('supplier') or '—'},
            {'label': 'Requesting Office', 'value': d.get('pr_office') or '—'},
            {'label': 'Number of PO Items', 'value': len(po_items)},
            {'label': 'PO Total', 'value': money_text(d.get('total'))},
        ],
        item_sections=[
            review_section('Purchase Order Items for Approval', po_items, d['no'],
                           'Double-check all descriptions, specifications, quantities, units, prices, and totals.'),
            review_section('Compare with Approved AOQ', parse_items(d.get('aoq_items')), d.get('aoq_no') or 'AOQ',
                           'Original winning offer for comparison. Review differences against the PO items above.', optional=True),
            review_section('Compare with Approved PR', parse_items(d.get('pr_items')), d.get('pr_no') or 'PR',
                           'Original requirements. Item correspondence remains subject to separate review.', optional=True),
        ],
        approve_url=url_for('po_approve', id=id), approve_label=f'Approve PO {d["no"]}',
        approval_effect='Approval locks this PO and makes the linked PR–PO pair available for NLP Verification.',
        back_url=url_for('po'), print_url=url_for('po_print', id=id),
        can_approve=d.get('status') != 'Approved',
        approval_details=[('PO', d['no']), ('Supplier', d.get('supplier') or 'Not recorded'),
                          ('Linked AOQ', d.get('aoq_no') or 'Not linked'), ('Amount', money_text(d.get('total')))],
    )

@app.route('/po/<int:id>/approve', methods=['POST'])
def po_approve(id):
    guard = require_review_confirmation('po_review', id)
    if guard:
        return guard
    if request.form.get('final_confirm') != 'yes':
        abort(400, description='Explicit final approval confirmation is required.')
    with durable_ocr.transaction(globals()) as (con, uid):
        row = con.execute('select * from po where id=? for update', (id,)).fetchone()
        if not row:
            abort(404)
        if row['status'] != 'For Review':
            abort(409, description='PO is no longer awaiting approval. Reopen its review page.')
        con.execute("update po set status=? where id=? and status='For Review'", ('Approved', id))
        log('Approved PO', 'PO', row['no'], 'Purchase Order approved after explicit final confirmation', connection=con)
    flash('PO approved. The procurement process is complete through PO. Open PR–PO NLP Verification when you are ready to continue to receiving and inventory.')
    return redirect(url_for('po'))

@app.route('/po/<int:id>/print')
def po_print(id):
    row=q('select p.*, aq.no aoq_no from po p left join aoq aq on aq.id=p.aoq_id where p.id=?',(id,),one=True)
    if not row:
        abort(404)
    items=parse_items(row['items'])
    return render_template('print_po.html', row=row, items=items, display_items=review_item_rows(items), **managed_signatories.print_context(conn, 'po', dict(row)), title='Print PO')

def nlp_item_comparison(pr_items_json, po_items_json):
    """Read original item schedules for advisory display only; never feed inference."""
    if not app.config['PR_PO_ITEM_COMPARISON_ENABLED']:
        return None
    view = {'result': None, 'sources': [], 'error': None}
    try:
        from pr_po_item_matching_v0_1_0 import compare_documents, InputError
        schedules = []
        for side, text in (('PR', pr_items_json), ('PO', po_items_json)):
            if not isinstance(text, str) or len(text) > 1700000:
                raise InputError('INVALID_OR_OVERSIZED_ITEMS')
            # Exact decimal source values are for comparison only. The installed
            # model continues to receive the unchanged parse_items/serializer.
            items = json.loads(text, parse_float=Decimal)
            if type(items) is not list or len(items) > 200 or any(type(i) is not dict for i in items):
                raise InputError('INVALID_OR_OVERSIZED_ITEMS')
            schedules.append(items)
            view['sources'].append({'side': side, 'items': items})
        view['result'] = compare_documents(*schedules)
        # Display document totals only. This never establishes a source-item match.
        totals = []
        for items in schedules:
            amounts = [parse_currency_decimal(value_any(item, 'amount', 'Amount', 'Estimated Budget Allocation (Php)', default='')) for item in items]
            totals.append(sum(amounts, Decimal('0')) if amounts and all(amount is not None for amount in amounts) else None)
        view['price_summary'] = {'requested': totals[0], 'offered': totals[1],
                                 'difference': totals[0] - totals[1] if all(total is not None for total in totals) else None}
        if view['result']['counts']['unresolved_requested']:
            try:
                from semantic_item_alignment import suggest_candidates
                candidates = suggest_candidates(*schedules)
                if candidates:
                    view['result'] = compare_documents(*schedules, semantic_candidates=candidates)
            except Exception:
                # Semantic assistance cannot hide deterministic findings or
                # replace an official classification when the encoder fails.
                pass
    except Exception:
        # Optional diagnostics must not replace an official result, expose
        # payloads/tracebacks, or prevent the existing review workflow.
        view['error'] = 'Technical comparison is unavailable for these source records. Review the original stored fields; no comparison or procurement decision was inferred.'
    return view


@app.route('/nlp', methods=['GET','POST'])
def nlp():
    if request.method=='POST':
        if request.form.get('action') == 'compare':
            if not app.config['PR_PO_ITEM_COMPARISON_ENABLED']:
                abort(404)
            return redirect(url_for('nlp', comparison_po_id=request.form.get('po_id', '')))
        from nlp_runtime_selection import require_available
        require_available(app.config)
        return procurement_creation.create_nlp(globals(), request.form.get('po_id'))
    raw_rows=q('''select n.*, p.no po_no, p.supplier, aq.no aoq_no, r.no pr_no
                  from nlp n
                  join po p on p.id=n.po_id
                  left join aoq aq on aq.id=p.aoq_id
                  left join pr r on r.id=aq.pr_id
                  order by n.id desc''')
    rows=[]
    for row in raw_rows:
        d=dict(row)
        d['findings'] = nlp_short_finding(d.get('result'), d.get('details'))
        d['remarks'] = nlp_dynamic_remarks(d.get('result'), d.get('details'))
        rows.append(d)
    _,_,_,_,raw_pos,_ = next_available_records('nlp')
    pos = []
    for p in raw_pos:
        d = dict(p)
        trace = trace_for_po(p)
        d['pr_no'] = trace.get('pr')['no'] if trace.get('pr') else 'No linked PR'
        d['aoq_no'] = trace.get('aoq')['no'] if trace.get('aoq') else 'No linked AOQ'
        d['item_preview'] = item_preview_from_json(d.get('items'), limit=1, include_offer_name=True)
        pos.append(d)
    comparison_pos, comparison_source, item_comparison = [], None, None
    comparison_enabled = app.config['PR_PO_ITEM_COMPARISON_ENABLED']
    if comparison_enabled:
        comparison_pos = q('''select p.id, p.no, p.supplier, r.no pr_no, p.items, p.total
                              from po p join aoq aq on aq.id=p.aoq_id
                              join pr r on r.id=aq.pr_id
                              where p.status='Approved' and aq.status='Approved' and r.status='Approved'
                              order by p.id desc''')
        if 'comparison_po_id' in request.args:
            po_id = request.args.get('comparison_po_id', type=int)
            if not po_id or po_id < 1:
                abort(404)
            # Derive the PR from the PO's existing AOQ relationship.
            comparison_source = q('''select p.id, p.no po_no, p.items po_items, r.no pr_no, r.items pr_items
                                     from po p join aoq aq on aq.id=p.aoq_id
                                     join pr r on r.id=aq.pr_id
                                     where p.id=? and p.status='Approved'
                                       and aq.status='Approved' and r.status='Approved' ''', (po_id,), one=True)
            if not comparison_source:
                abort(404)
            item_comparison = nlp_item_comparison(comparison_source['pr_items'], comparison_source['po_items'])
    selectable_pos = {str(p['id']): dict(p, can_run=True, can_compare=False) for p in pos}
    for p in comparison_pos:
        key = str(p['id'])
        if key not in selectable_pos:
            selectable_pos[key] = dict(p, can_run=False, item_preview=item_preview_from_json(p.get('items'), limit=1, include_offer_name=True))
        selectable_pos[key]['can_compare'] = True
    for p in selectable_pos.values():
        p['search_terms'] = item_preview_from_json(p.get('items'), limit=200, include_offer_name=True)
    return render_template('nlp.html', active='nlp', rows=rows, pos=pos, selector_pos=list(selectable_pos.values()), model_state=trained_nlp_model_status(), title='PR–PO NLP Verification',
                           comparison_enabled=comparison_enabled, comparison_pos=comparison_pos,
                           comparison_source=comparison_source, item_comparison=item_comparison)

def nlp_pricing_advisory(record_id):
    """Optional read-only diagnostics; unavailable diagnostics cannot change review."""
    try:
        from pricing_advisory_v0_2_0 import build_for_review
        return build_for_review(conn, record_id, session)
    except Exception:
        return {'rows': [{'state': 'Insufficient Evidence', 'check': 'Pricing diagnostics',
                          'message': 'Pricing diagnostics are temporarily unavailable.', 'sources': []}],
                'values': [], 'lines': []}


@app.route('/nlp/<int:id>/view')
def nlp_review(id):
    row = q('''select n.*, p.no po_no, p.items po_items, p.supplier,
                      aq.no aoq_no, r.no pr_no, r.items pr_items
               from nlp n
               join po p on p.id=n.po_id
               left join aoq aq on aq.id=p.aoq_id
               left join pr r on r.id=aq.pr_id
               where n.id=?''', (id,), one=True)
    if not row:
        flash('NLP result not found.')
        return redirect(url_for('nlp'))
    d = dict(row)
    try:
        details = json.loads(d.get('details') or '{}')
    except Exception:
        details = {}
    finding = nlp_short_finding(d.get('result'), details)
    remarks = nlp_dynamic_remarks(d.get('result'), details)
    source = details.get('final_decision_source') or 'AI-only FastText PR–PO verification'
    return render_review_page(
        active='nlp', title=f'Review NLP {d.get("no") or id}', document_type='PR–PO NLP Verification',
        document_no=d.get('no') or f'NLP-{id}', status=d.get('status') or 'For Review',
        created_at=d.get('created_at'),
        linked_records=[
            {'label': 'Compared PR', 'value': d.get('pr_no') or 'No linked PR'},
            {'label': 'Supporting AOQ', 'value': d.get('aoq_no') or 'No linked AOQ'},
            {'label': 'Compared PO', 'value': d.get('po_no') or 'No linked PO'},
        ],
        summary=[
            {'label': 'Supplier', 'value': d.get('supplier') or '—'},
            {'label': 'NLP Result', 'value': d.get('result') or '—'},
            {'label': 'Match / Certainty Score', 'value': f'{to_float(d.get("confidence")):.2f}%'},
            {'label': 'Decision Source', 'value': source},
        ],
        item_sections=[
            review_section('Approved PR Requirements', parse_items(d.get('pr_items')), d.get('pr_no') or 'PR',
                           'Original requirements supplied to the NLP model.'),
            review_section('Approved PO Items', parse_items(d.get('po_items')), d.get('po_no') or 'PO',
                           'Final purchase-order values compared by the NLP model.'),
        ],
        text_blocks=[
            {'title': 'Primary NLP Finding', 'content': finding},
            {'title': 'Recommended Staff Action', 'content': remarks},
            {'title': 'Complete NLP Comparison Details', 'content': json.dumps(details, indent=2, ensure_ascii=False, default=str)},
        ],
        approve_url=url_for('nlp_approve', id=id),
        approve_label=f'Approve NLP Result {d.get("no") or id}',
        approval_effect=('Approval accepts this recorded NLP decision. Acceptable/Needs Review results may proceed to IAR; '
                         'Needs Recanvass/Reject results remain blocked for corrective action.'),
        back_url=url_for('nlp'), print_url=None,
        can_approve=d.get('status') != 'Approved',
        pricing_diagnostics=nlp_pricing_advisory(id),
        item_comparison=nlp_item_comparison(d.get('pr_items'), d.get('po_items')),
    )

@app.route('/nlp/<int:id>/approve', methods=['POST'])
def nlp_approve(id):
    row = q('''select n.*, p.no po_no from nlp n
               join po p on p.id=n.po_id where n.id=?''', (id,), one=True)
    if not row:
        flash('NLP result not found.')
        return redirect(url_for('nlp'))
    guard = require_review_confirmation('nlp_review', id)
    if guard:
        return guard
    if row['status'] == 'Approved':
        flash('NLP result is already approved.')
        return redirect(url_for('nlp_review', id=id))
    changed = audited_update('''update nlp set status=?,staff_decision=? where id=?
        and status is not distinct from ? and result is not distinct from ? and details is not distinct from ?''',
        ('Approved',row['result'],id,row.get('status'),row.get('result'),row.get('details')),
        'Approved NLP Result','NLP',row['po_no'],f'Approved prediction: {row["result"]}')
    if changed != 1:
        flash('NLP result changed during approval. Reopen its review page.')
        return redirect(url_for('nlp_review', id=id))
    if row['result'] in ('Acceptable', 'Needs Review'):
        flash('NLP result approved. The PO is now available for IAR creation.')
    else:
        flash('NLP result approved, but this result blocks IAR and requires corrective procurement action.')
    return redirect(url_for('nlp'))

@app.route('/nlp/algorithms')
def nlp_algorithms():
    return render_template(
        'nlp_algorithms.html',
        active='nlp',
        algorithms=algorithm_comparison(),
        title='NLP Algorithms',
    )

@app.route('/iar', methods=['GET','POST'])
def iar():
    if request.method=='POST':
        return procurement_creation.create_iar(globals(), request.form.get('po_id'))
    rows=q('''select i.*, p.no po_no, p.supplier, n.no nlp_no, aq.no aoq_no, r.no pr_no
              from iar i
              left join po p on p.id=i.po_id
              left join nlp n on n.po_id=p.id
              left join aoq aq on aq.id=p.aoq_id
              left join pr r on r.id=aq.pr_id
              order by i.id desc''')
    _,_,_,_,_,raw_pos = next_available_records('iar')
    pos=[]
    for p in raw_pos:
        d=dict(p)
        trace=trace_for_po(p)
        n=q("select no,result from nlp where po_id=? and status='Approved' order by id desc limit 1",(p['id'],),one=True)
        d['pr_no']=trace.get('pr')['no'] if trace.get('pr') else 'No linked PR'
        d['nlp_no']=n['no'] if n else 'No approved NLP'
        d['nlp_result']=n['result'] if n else ''
        d['item_preview']=item_preview_from_json(d.get('items'),limit=1)
        pos.append(d)
    return render_template('iar.html', active='iar', rows=rows, pos=pos, title='IAR/AIR')

@app.route('/iar/<int:id>/view')
def iar_review(id):
    row = q('''select i.*, p.no po_no, p.items po_items, p.supplier,
                      aq.no aoq_no, r.no pr_no, n.no nlp_no, n.result nlp_result
               from iar i
               join po p on p.id=i.po_id
               left join aoq aq on aq.id=p.aoq_id
               left join pr r on r.id=aq.pr_id
               left join nlp n on n.po_id=p.id and n.status='Approved'
               where i.id=? order by n.id desc limit 1''', (id,), one=True)
    if not row:
        flash('IAR record not found.')
        return redirect(url_for('iar'))
    d = dict(row)
    iar_items = parse_items(d.get('items'))
    return render_review_page(
        active='iar', title=f'Review IAR {d["no"]}', document_type='Inspection and Acceptance Report',
        document_no=d['no'], status=d['status'], created_at=d.get('created_at'),
        linked_records=[
            {'label': 'Related PR', 'value': d.get('pr_no') or 'No linked PR'},
            {'label': 'Related AOQ', 'value': d.get('aoq_no') or 'No linked AOQ'},
            {'label': 'Related PO', 'value': d.get('po_no') or 'No linked PO'},
            {'label': 'Approved NLP', 'value': f'{d.get("nlp_no") or "—"} — {d.get("nlp_result") or "—"}'},
        ],
        summary=[
            {'label': 'Supplier', 'value': d.get('supplier') or '—'},
            {'label': 'Delivery Items', 'value': len(iar_items)},
            {'label': 'Inventory Effect', 'value': 'Accepted items will be added to Inventory after approval.'},
        ],
        item_sections=[
            review_section('Purchase Order Items', parse_items(d.get('po_items')), d.get('po_no') or 'PO',
                           'Confirm what was ordered.'),
            review_section('IAR Delivery / Acceptance Items', iar_items, d['no'],
                           'Verify every delivered item and quantity before accepting the IAR.'),
        ],
        approve_url=url_for('iar_approve', id=id), approve_label=f'Approve IAR {d["no"]}',
        approval_effect='Approval accepts the delivery and adds every listed IAR item to the system Inventory.',
        back_url=url_for('iar'), print_url=url_for('iar_print', id=id),
        can_approve=d.get('status') != 'Approved',
        receiving_items=iar_items,
    )

def confirmed_receiving_items(items, form):
    """Record actual receipts separately, under the IAR approval transaction."""
    from procurement_item_contract import decimal_number, number_text
    if not isinstance(items, list) or not items or any(not isinstance(item, dict) for item in items):
        raise ValueError('IAR items are missing or invalid. Review the delivery before approval.')
    confirmed = []
    for index, source in enumerate(items):
        item = dict(source)
        if item.get('_Receiving Contract Version'):
            ordered = decimal_number(item.get('ordered_qty'))
            received = decimal_number(form.get(f'received_qty_{index}'))
            if ordered is None or ordered <= 0 or received is None or received <= 0 or received > ordered:
                raise ValueError(f'IAR item {index + 1}: enter a positive actual received quantity no greater than the ordered quantity.')
            item['received_qty'] = number_text(received)
            item['_Receiving Quantity Difference'] = number_text(received - ordered)
        category = form.get(f'inventory_category_{index}')
        if category is None:
            category = item.get('_Inventory Category') or item.get('inventory_category') or item.get('category') or item.get('Category')
        if category not in ('Equipment', 'Consumable'):
            raise ValueError(f'IAR item {index + 1}: explicitly review Equipment or Consumable classification before approval.')
        item['_Inventory Category'] = category
        confirmed.append(item)
    return confirmed


def iar_inventory_items(items_json):
    """Validate every receipt before allocating identifiers or writing inventory."""
    from aoq_flexible import review_number

    items = parse_items(items_json)
    if not isinstance(items, list) or not items:
        raise ValueError('IAR items are missing or invalid. Review the delivery before approval.')
    receipts = []
    for index, it in enumerate(items, 1):
        if not isinstance(it, dict):
            raise ValueError(f'IAR item {index} is invalid. Review the delivery before approval.')

        def value(*aliases):
            for alias in aliases:
                candidate = it.get(alias)
                if candidate is not None and candidate != '':
                    return candidate
            return None

        item_name = value('item', 'Item Name')
        unit = value('unit', 'Unit')
        for label, text in (('item name', item_name), ('unit', unit)):
            if not isinstance(text, str) or not text.strip():
                raise ValueError(f'IAR item {index}: {label} is missing or invalid. Correct it before approval.')
        qty = review_number(value('received_qty', 'qty', 'Quantity'))
        unit_cost = review_number(value('unit_cost', 'Unit Cost', 'Estimated Unit Cost'))
        for label, number in (('quantity', qty), ('unit cost', unit_cost)):
            if number is None or number < 0:
                raise ValueError(f'IAR item {index}: {label} must be a valid, non-negative number. Correct it before approval.')
        description = it.get('description') or it.get('Description / Specification') or it.get('Description') or ''
        cat = value('_Inventory Category', 'inventory_category', 'category', 'Category')
        if cat not in ('Equipment', 'Consumable'):
            raise ValueError(f'IAR item {index}: reviewed inventory classification is required before approval.')
        source_dataset_id = it.get('_Dataset Record ID') or it.get('_dataset_record_id')
        receipts.append((item_name, description, cat, qty, unit, unit_cost, source_dataset_id))
    return receipts


@app.route('/iar/<int:id>/approve', methods=['POST'])
def iar_approve(id):
    guard = require_review_confirmation('iar_review', id)
    if guard:
        return guard
    con = conn()
    try:
        # Repeated/concurrent requests must check status after obtaining this lock.
        row = con.execute('''select i.*, p.no po_no from iar i
                             join po p on p.id=i.po_id where i.id=?
                             for update of i''', (id,)).fetchone()
        if not row:
            con.rollback()
            flash('IAR record not found.')
            return redirect(url_for('iar'))
        if row['status'] == 'Approved':
            con.rollback()
            flash('IAR is already approved.')
            return redirect(url_for('iar_review', id=id))
        try:
            confirmed = confirmed_receiving_items(parse_items(row['items']), request.form)
            receipts = iar_inventory_items(json.dumps(confirmed))
        except ValueError as exc:
            con.rollback()
            flash(str(exc))
            return redirect(url_for('iar_review', id=id))

        if json.dumps(confirmed, default=str) != json.dumps(parse_items(row['items']), default=str):
            if con.execute('update iar set items=? where id=?', (json.dumps(confirmed, default=str), id)).rowcount != 1:
                raise RuntimeError('Actual receipt quantities were not recorded.')

        for item_name, description, cat, qty, unit, unit_cost, source_dataset_id in receipts:
            item_code = next_document_no('INVENTORY', 'INV', 'inventory', column='item_code', width=5, connection=con)
            result = con.execute(
                '''insert into inventory(item_code,item,description,category,qty,unit,source_po,status,created_at,unit_cost,source_dataset_record_id)
                values(?,?,?,?,?,?,?,?,?,?,?)''',
                (item_code, item_name, description, cat, qty, unit, row['po_no'], 'Available', now(), unit_cost, source_dataset_id)
            )
            if result.rowcount != 1:
                raise RuntimeError('IAR inventory receipt was not inserted.')
        if con.execute('update iar set status=? where id=?', ('Approved', id)).rowcount != 1:
            raise RuntimeError('IAR approval status was not updated.')
        log('Approved IAR', 'IAR', row['no'], 'Delivery accepted and items added to inventory', connection=con)
        con.commit()
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()
    flash('IAR approved. Delivery items were added to inventory.')
    return redirect(url_for('iar'))

@app.route('/iar/<int:id>/print')
def iar_print(id):
    row=q('select * from iar where id=?',(id,),one=True)
    if not row:
        abort(404)
    po_row=q('select * from po where id=?',(row['po_id'],),one=True)
    if not po_row:
        abort(404)
    items=parse_items(row['items'])
    return render_template('print_iar.html', row=row, po=po_row, items=items, **managed_signatories.print_context(conn, 'iar', dict(row)), title='Print IAR')

@app.route('/inventory')
def inventory():
    search_text = (request.args.get('q') or '').strip()
    category_filter = (request.args.get('category') or '').strip()
    stock_filter = (request.args.get('stock') or '').strip().lower()
    all_rows = [dict(row) for row in q('select * from inventory order by id desc')]

    def stock_state(row):
        quantity = to_float(row.get('qty'))
        if quantity <= 0:
            return 'Out of stock'
        if quantity <= 5:
            return 'Low stock'
        return 'In stock'

    for row in all_rows:
        row['stock_state'] = stock_state(row)

    categories = sorted({str(row.get('category') or '').strip() for row in all_rows if row.get('category')})
    normalized_query = search_text.lower()
    rows = [
        row for row in all_rows
        if (not normalized_query or normalized_query in ' '.join(str(row.get(key) or '') for key in ('item_code', 'item', 'description', 'source_po')).lower())
        and (not category_filter or row.get('category') == category_filter)
        and (not stock_filter or row['stock_state'].lower().startswith(stock_filter))
    ]
    stock_summary = {
        'total': len(all_rows),
        'in_stock': sum(row['stock_state'] == 'In stock' for row in all_rows),
        'low_stock': sum(row['stock_state'] == 'Low stock' for row in all_rows),
        'out_of_stock': sum(row['stock_state'] == 'Out of stock' for row in all_rows),
    }
    transactions = q('''select t.*, i.item_code, i.item from inventory_transactions t
                        left join inventory i on i.id=t.inventory_id order by t.id desc limit 50''')
    return render_template(
        'inventory.html', active='inventory', rows=rows, transactions=transactions,
        title='Inventory', categories=categories, search_text=search_text,
        category_filter=category_filter, stock_filter=stock_filter,
        stock_summary=stock_summary,
    )

@app.route('/ris-ics', methods=['GET','POST'])
def ris_ics():
    if request.method == 'POST':
        from aoq_flexible import review_number

        typ = (request.form.get('type') or '').upper().strip()
        if typ not in ('RIS', 'ICS'):
            flash('Choose RIS for consumables or ICS for equipment.')
            return redirect(url_for('ris_ics'))
        office = (request.form.get('office') or 'General Services Office').strip()
        selected_ids = request.form.getlist('inventory_id')
        if not selected_ids or any(not re.fullmatch(r'[0-9]+', str(x)) or int(x) <= 0 for x in selected_ids):
            flash('Select valid available inventory items.')
            return redirect(url_for('ris_ics'))
        selected_ids = [int(x) for x in selected_ids]
        if len(set(selected_ids)) != len(selected_ids):
            flash('Select each inventory item only once.')
            return redirect(url_for('ris_ics'))
        expected_category = 'Equipment' if typ == 'ICS' else 'Consumable'
        items = []
        for inventory_id in selected_ids:
            inv = q('select * from inventory where id=? and qty>0 and category=?', (inventory_id, expected_category), one=True)
            if not inv:
                flash('A selected inventory item is unavailable or has the wrong category. Review the selection.')
                return redirect(url_for('ris_ics'))
            requested_qty = review_number(request.form.get(f'issue_qty_{inventory_id}'))
            available = review_number(inv['qty'])
            if (requested_qty is None or requested_qty <= 0 or available is None
                    or requested_qty > available or (typ == 'ICS' and requested_qty > 1)):
                flash('Enter a valid positive quantity within available stock (at most one per ICS item).')
                return redirect(url_for('ris_ics'))
            items.append({
                'inventory_id': inv['id'],
                'item_code': inv['item_code'],
                'item': inv['item'],
                'description': inv['description'] or '',
                'category': inv['category'],
                'qty': requested_qty,
                'unit': inv['unit'] or 'unit',
                'unit_cost': to_float(inv['unit_cost']),
                'available_before': to_float(inv['qty']),
                'source_po': inv['source_po'] or '',
            })
        if not items:
            flash(f'No available {expected_category.lower()} inventory items were selected.')
            return redirect(url_for('ris_ics'))
        with durable_ocr.transaction(globals()) as (con, uid):
            no = next_document_no(typ, typ, 'ris_ics', connection=con)
            items = assign_item_identifiers(items, '_Issue Item ID', no)
            con.execute('insert into ris_ics(no,type,office,status,created_at,items) values(?,?,?,?,?,?)',
                        (no, typ, office, 'For Review', now(), json.dumps(items, default=str)))
            log(f'Created {typ}', 'RIS/ICS', no, f'{len(items)} inventory item(s) prepared for {office}', connection=con)
        flash(f'{no} created. Review all selected inventory items before approval.')
        return redirect(url_for('ris_ics'))

    rows = q('select * from ris_ics order by id desc')
    consumables = q("select * from inventory where category='Consumable' and qty>0 order by lower(item)")
    equipment = q("select * from inventory where category='Equipment' and qty>0 order by lower(item)")
    return render_template('ris_ics.html', active='ris', rows=rows, consumables=consumables,
                           equipment=equipment, title='RIS/ICS')

@app.route('/ris-ics/<int:id>/view')
def ris_ics_review(id):
    row = q('select * from ris_ics where id=?', (id,), one=True)
    if not row:
        flash('RIS/ICS record not found.')
        return redirect(url_for('ris_ics'))
    d = dict(row)
    items = parse_items(d.get('items'))
    full_name = 'Requisition and Issue Slip' if d.get('type') == 'RIS' else 'Inventory Custodian Slip'
    return render_review_page(
        active='ris', title=f'Review {d["no"]}', document_type=full_name,
        document_no=d['no'], status=d['status'], created_at=d.get('created_at'),
        linked_records=[{'label': 'Document Type', 'value': d.get('type') or '—'}],
        summary=[
            {'label': 'Receiving Office / Custodian', 'value': d.get('office') or '—'},
            {'label': 'Number of Inventory Items', 'value': len(items)},
            {'label': 'Inventory Effect', 'value': 'Approval deducts the listed quantities from live inventory.'},
        ],
        item_sections=[review_section(f'{d.get("type")} Inventory Items', items, d['no'],
                                      'Verify inventory identifiers, available quantities, issued quantities, units, and receiving office.')],
        approve_url=url_for('ris_ics_approve', id=id), approve_label=f'Approve {d["no"]}',
        approval_effect=f'Approval finalizes this {d.get("type")} and deducts the issued quantities from Inventory.',
        back_url=url_for('ris_ics'), print_url=url_for('ris_ics_print', id=id),
        can_approve=d.get('status') != 'Approved',
    )

def ris_ics_issue_items(items_json, typ):
    """Validate the existing one-selection-per-inventory-item document format."""
    from aoq_flexible import review_number

    if typ not in ('RIS', 'ICS'):
        raise ValueError('Invalid RIS/ICS document type.')
    items = parse_items(items_json)
    if not isinstance(items, list) or not items:
        raise ValueError('RIS/ICS items are missing or invalid. Review the document.')
    selected = {}
    for index, item in enumerate(items, 1):
        if not isinstance(item, dict):
            raise ValueError(f'RIS/ICS item {index} is invalid.')
        raw_id = item.get('inventory_id')
        if isinstance(raw_id, bool) or not re.fullmatch(r'[0-9]+', str(raw_id)) or int(raw_id) <= 0:
            raise ValueError(f'RIS/ICS item {index} has an invalid inventory identifier.')
        inventory_id = int(raw_id)
        if inventory_id in selected:
            raise ValueError('An inventory item appears more than once. Correct the document before approval.')
        qty = review_number(item.get('qty'))
        if qty is None or qty <= 0 or (typ == 'ICS' and qty > 1):
            raise ValueError(f'RIS/ICS item {index} requires a valid positive quantity (at most one for ICS).')
        selected[inventory_id] = (item, qty)
    return selected


@app.route('/ris-ics/<int:id>/approve', methods=['POST'])
def ris_ics_approve(id):
    from aoq_flexible import review_number

    guard = require_review_confirmation('ris_ics_review', id)
    if guard:
        return guard
    con = conn()
    try:
        row = con.execute('select * from ris_ics where id=? for update', (id,)).fetchone()
        if not row:
            con.rollback()
            flash('RIS/ICS record not found.')
            return redirect(url_for('ris_ics'))
        if row['status'] == 'Approved':
            con.rollback()
            flash('RIS/ICS document is already approved; inventory was not deducted again.')
            return redirect(url_for('ris_ics_review', id=id))
        items = ris_ics_issue_items(row['items'], row['type'])
        inventory = {}
        expected_category = 'Equipment' if row['type'] == 'ICS' else 'Consumable'
        # All approvals acquire stock locks in the same order, regardless of item order.
        # Validate the complete document before applying any deduction.
        for inventory_id in sorted(items):
            item, issue_qty = items[inventory_id]
            inv = con.execute('select * from inventory where id=? for update', (inventory_id,)).fetchone()
            if not inv:
                raise ValueError(f'Inventory item {item.get("item_code") or inventory_id} no longer exists.')
            if inv['category'] != expected_category:
                raise ValueError(f'Inventory item {inventory_id} has the wrong category for this document.')
            if item.get('item_code') and item['item_code'] != inv['item_code']:
                raise ValueError(f'Inventory identifier for item {inventory_id} no longer matches the reviewed document.')
            if item.get('unit') and item['unit'] != inv['unit']:
                raise ValueError(f'Inventory unit for item {inventory_id} no longer matches the reviewed document.')
            available = review_number(inv['qty'])
            if available is None or available < 0:
                raise ValueError(f'Available quantity for inventory item {inventory_id} is invalid.')
            if issue_qty > available:
                raise ValueError(f'Insufficient quantity for {inv["item_code"]}: requested {issue_qty:g}, available {available:g}.')
            inventory[inventory_id] = inv

        for inventory_id, (item, issue_qty) in items.items():
            inv = inventory[inventory_id]
            available = review_number(inv['qty'])
            remaining = round(available - issue_qty, 4)
            new_status = 'Available' if remaining > 0 else ('Assigned' if row['type'] == 'ICS' else 'Issued')
            if con.execute('update inventory set qty=?, status=? where id=?', (remaining, new_status, inventory_id)).rowcount != 1:
                raise RuntimeError('Inventory quantity was not updated.')
            movement = con.execute('''insert into inventory_transactions(inventory_id,document_no,transaction_type,qty,unit,office,created_at,created_by)
                           values(?,?,?,?,?,?,?,?)''',
                        (inventory_id, row['no'], row['type'], issue_qty, inv['unit'], row['office'], now(), session.get('username', 'admin')))
            if movement.rowcount != 1:
                raise RuntimeError('Inventory issuance entry was not inserted.')
        if con.execute('update ris_ics set status=? where id=?', ('Approved', id)).rowcount != 1:
            raise RuntimeError('RIS/ICS approval status was not updated.')
        log(f'Approved {row["type"]}', 'RIS/ICS', row['no'], f'Inventory quantities deducted for {len(items)} item(s)', connection=con)
        con.commit()
    except Exception as exc:
        con.rollback()
        flash(f'Approval stopped: {exc}')
        return redirect(url_for('ris_ics_review', id=id))
    finally:
        con.close()
    flash(f'{row["no"]} approved and live inventory quantities were updated.')
    return redirect(url_for('ris_ics'))

@app.route('/ris-ics/<int:id>/print')
def ris_ics_print(id):
    row=q('select * from ris_ics where id=?',(id,),one=True)
    if not row:
        abort(404)
    items=parse_items(row['items'])
    return render_template('print_ris_ics.html', row=row, items=items, **managed_signatories.print_context(conn, 'ris_ics', dict(row)), title='Print RIS/ICS')

@app.route('/reports')
def reports():
    modules = [
        ('PPMP','ppmp'),('APP','app_plan'),('PR','pr'),('AOQ','aoq'),('PO','po'),
        ('NLP','nlp'),('IAR','iar'),('Inventory','inventory'),('RIS/ICS','ris_ics')
    ]
    aggregations = [('ppmp_total','ppmp','total'),('pr_total','pr','total'),
                    ('po_total','po','total'),('inventory_value','inventory','qty*coalesce(unit_cost,0)')]
    # Fixed identifiers only. One statement/connection also gives one snapshot.
    projections = [f'(select count(*) from {table}) as c{index}' for index, (_,table) in enumerate(modules)]
    projections += [f'(select coalesce(sum({expression}),0) from {table}) as t{index}'
                    for index, (_,table,expression) in enumerate(aggregations)]
    metrics = q('select '+','.join(projections), one=True)
    counts = {name:metrics[f'c{index}'] for index,(name,_) in enumerate(modules)}
    totals = {name:to_float(metrics[f't{index}']) for index,(name,_,_) in enumerate(aggregations)}
    return render_template('reports.html', active='reports', totals=totals, counts=counts, title='Reports')


def spreadsheet_safe_cell(value):
    """Preserve numeric cells; prevent untrusted text from becoming formulas."""
    if not isinstance(value, str):
        return value
    stripped = value
    while stripped and (stripped[0].isspace() or ord(stripped[0]) < 32 or stripped[0] == '\ufeff'):
        stripped = stripped[1:]
    if (stripped.startswith(('=', '+', '-', '@', '＝', '＋', '－', '＠')) or
            value.startswith(('\t', '\r', '\n', '\ufeff'))):
        return "'" + value
    return value


def _csv_response(filename, headers, rows):
    output = io.StringIO(newline='')
    writer = csv.writer(output, quoting=csv.QUOTE_ALL)
    writer.writerow([spreadsheet_safe_cell(value) for value in headers])
    for row in rows:
        writer.writerow([spreadsheet_safe_cell(value) for value in row])
    return Response('\ufeff' + output.getvalue(), mimetype='text/csv; charset=utf-8',
                    headers={'Content-Disposition': f'attachment; filename="{filename}"',
                             'Cache-Control': 'private, no-store', 'X-Content-Type-Options': 'nosniff'})


@app.route('/reports/export/<report_type>.csv')
def report_export(report_type):
    report_type = report_type.lower().strip()
    if report_type == 'procurement':
        rows = q('''select r.no pr_no, aq.no aoq_no, aq.winner, p.no po_no, p.total, p.status,
                           n.no nlp_no, n.result nlp_result, n.status nlp_status
                    from pr r left join aoq aq on aq.pr_id=r.id
                    left join po p on p.aoq_id=aq.id left join nlp n on n.po_id=p.id
                    order by r.id desc''')
        return _csv_response('procurement_chain_report.csv',
            ['PR Number','AOQ Number','Winning Supplier','PO Number','PO Total','PO Status','NLP Number','NLP Result','NLP Status'],
            [[r['pr_no'],r['aoq_no'],r['winner'],r['po_no'],r['total'],r['status'],r['nlp_no'],r['nlp_result'],r['nlp_status']] for r in rows])
    if report_type == 'inventory':
        rows = q('select item_code,item,description,category,qty,unit,unit_cost,source_po,status from inventory order by item')
        return _csv_response('inventory_report.csv',
            ['Item Code','Item','Description','Category','Available Quantity','Unit','Unit Cost','Source PO','Status'],
            [[r['item_code'],r['item'],r['description'],r['category'],r['qty'],r['unit'],r['unit_cost'],r['source_po'],r['status']] for r in rows])
    if report_type == 'issuance':
        rows = q('select no,type,office,status,created_at,items from ris_ics order by id desc')
        return _csv_response('ris_ics_report.csv',
            ['Document Number','Type','Office/Custodian','Status','Created At','Items JSON'],
            [[r['no'],r['type'],r['office'],r['status'],r['created_at'],r['items']] for r in rows])
    if report_type == 'audit':
        rows = q('select a.dt,a."user" as "user",a.action,a.module,a.ref,a.details from audit_trail a order by a.id desc')
        return _csv_response('audit_trail_report.csv',
            ['Date/Time','User','Action','Module','Reference','Details'],
            [[r['dt'],r['user'],r['action'],r['module'],r['ref'],r['details']] for r in rows])
    abort(404)


@app.route('/account', methods=['GET','POST'])
def account():
    user_id = session.get('user_id')
    if not user_id:
        flash('Please sign in again to manage your account.')
        return redirect(url_for('login'))
    if request.method == 'POST':
        full_name = (request.form.get('full_name') or '').strip()[:160]
        display_name = (request.form.get('display_name') or '').strip()[:80]
        exec_db('update users set full_name=?, display_name=? where id=?', (full_name, display_name, user_id))
        log('Profile Updated', 'Account', session.get('username',''), 'User updated safe profile fields')
        flash('Account profile updated.')
        return redirect(url_for('account'))
    row = q('select id,username,role,full_name,display_name,status,created_at,last_login from users where id=?', (user_id,), one=True)
    if not row:
        session.clear(); flash('Account no longer exists. Please sign in again.')
        return redirect(url_for('login'))
    return render_template('account.html', active='account', account=dict(row), title='My Account')


@app.route('/account/change-password', methods=['POST'])
def change_password():
    user_id = session.get('user_id')
    user = q('select id,username,password from users where id=?', (user_id,), one=True) if user_id else None
    if not user:
        flash('Please sign in again.')
        return redirect(url_for('login'))
    current = request.form.get('current_password') or ''
    new_password = request.form.get('new_password') or ''
    confirm = request.form.get('confirm_password') or ''
    try:
        valid = check_password_hash(str(user['password'] or ''), current)
    except Exception:
        valid = False
    if not valid:
        flash('Current password is incorrect.')
    elif len(new_password) < 8:
        flash('New password must contain at least 8 characters.')
    elif new_password != confirm:
        flash('New password and confirmation do not match.')
    elif new_password == current:
        flash('Choose a new password that is different from the current password.')
    else:
        try:
            server_sessions.replace_password(
                conn, session.get(server_sessions.COOKIE_KEY), user_id, str(user['password']),
                generate_password_hash(new_password),
                audit=lambda con: log('Password Changed', 'Account', user['username'],
                                      'User changed own password', connection=con))
        except server_sessions.Rejected:
            session.clear()
            flash('Authentication changed. Please sign in again.')
            return redirect(url_for('login'))
        except server_sessions.Unavailable:
            return Response('Password change could not be confirmed. Please try again.',
                            status=503, mimetype='text/plain')
        session.clear()
        flash('Password changed successfully. Please sign in again; existing sessions were revoked.')
        return redirect(url_for('login'))
    return redirect(url_for('account') + '#change-password')


def create_user_account(username, password_hash, role):
    """Commit an authorized account creation and its audit as one operation."""
    with durable_ocr.transaction(globals()) as (con, uid):
        actor = con.execute('select role from users where id=?', (uid,)).fetchone()
        if not actor or actor['role'] != 'Admin':
            abort(403)
        if con.execute('select id from users where username=?', (username,)).fetchone():
            abort(409, description='Account changed during creation. Reopen User Management.')
        changed = con.execute('insert into users(username,password,role,status,created_at) values(?,?,?,?,?)',
                              (username, password_hash, role, 'Active', now())).rowcount
        if changed != 1:
            raise RuntimeError('Account creation was not confirmed.')
        log('Created User', 'User Management', username, f'Role: {role}', connection=con)


@app.route('/users', methods=['GET','POST'])
def users():
    guard = require_admin_access()
    if guard:
        return guard
    if request.method == 'POST':
        action = request.form.get('action') or 'create'
        if action == 'create':
            username = (request.form.get('username') or '').strip()
            password = request.form.get('password') or ''
            role = (request.form.get('role') or 'Staff').strip()
            if role not in ('Admin', 'Staff', 'Inspector'):
                role = 'Staff'
            if len(username) < 3 or len(password) < 8:
                flash('Username must have at least 3 characters and password at least 8 characters.')
            elif q('select id from users where username=?', (username,), one=True):
                flash('That username already exists.')
            else:
                from werkzeug.exceptions import HTTPException
                try:
                    create_user_account(username, generate_password_hash(password), role)
                except HTTPException:
                    raise
                except Exception:
                    return Response('Account creation could not be confirmed. Reopen User Management before retrying.',
                                    status=503, mimetype='text/plain', headers={'Cache-Control': 'private, no-store'})
                flash(f'User {username} created.')
        elif action == 'reset_password':
            user_id = request.form.get('user_id')
            password = request.form.get('password') or ''
            user = q('select * from users where id=?', (user_id,), one=True)
            if not user or len(password) < 8:
                flash('Select a valid user and enter a password with at least 8 characters.')
            else:
                try:
                    server_sessions.reset_password(
                        conn, int(user['id']), generate_password_hash(password),
                        audit=lambda con, target: log('Reset Password', 'User Management', target['username'],
                                                      'Administrator reset account password', connection=con))
                except server_sessions.Rejected:
                    flash('Account changed. Please try again.')
                    return redirect(url_for('users'))
                except server_sessions.Unavailable:
                    return Response('Password reset could not be confirmed. Please try again.',
                                    status=503, mimetype='text/plain')
                flash(f'Password reset for {user["username"]}.')
        return redirect(url_for('users'))
    rows = q('select id,username,role from users order by lower(username)')
    return render_template('users.html', active='users', rows=rows, title='User Management')


@app.route('/audit')
def audit():
    rows = q('select * from audit_trail order by id desc limit 500')
    return render_template('audit.html', active='audit', rows=rows, title='Audit Trail')


@app.route('/health')
def health():
    model_state = trained_nlp_model_status()
    mapper_state = ocr_mapper_model_status()
    database_ok = bool(db_ping())
    tesseract_ok = bool(configure_tesseract() or shutil.which('tesseract'))
    fasttext_ok = bool(model_state.get('active'))
    mapper_ok = bool(mapper_state.get('active'))
    inference_on = app.config.get('PSMS_INFERENCE_ENABLED',True)
    ready = database_ok and (not inference_on or all([tesseract_ok,fasttext_ok,mapper_ok]))
    return {
        'application': 'ready' if ready else 'degraded',
        'inference_enabled': inference_on,
        'database': database_ok,
        'tesseract': tesseract_ok,
        'fasttext_nlp': fasttext_ok,
        'ocr_semantic_mapper': mapper_ok,
        'tesseract_available': tesseract_ok,
        'ocr_semantic_model_active': mapper_ok,
        'nlp_model_active': fasttext_ok,
        'ocr_semantic_message': mapper_state.get('message'),
        'nlp_message': model_state.get('message'),
    }

import historical_signatories
historical_signatories.register(app, conn)

if __name__ == '__main__':
    print('Running PSMS at http://127.0.0.1:5000')
    print('Pre-deployment mode: Waitress WSGI server (offline/local)')
    try:
        from waitress import serve
        serve(app, host='127.0.0.1', port=5000, threads=4)
    except ImportError:
        print('Waitress is not installed. Run: pip install -r requirements.txt')
        print('Fallback server started with debug disabled.')
        app.run(debug=False, host='127.0.0.1', port=5000)
