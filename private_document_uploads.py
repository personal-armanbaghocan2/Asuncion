"""Bounded local parsing of untrusted documents; never durable filesystem storage."""
from contextlib import contextmanager
from io import BytesIO
from pathlib import Path
import csv
import tempfile
import zipfile

from flask import abort
from PIL import Image, UnidentifiedImageError

LIMIT=8*1024*1024
FORMATS={'.png':'PNG','.jpg':'JPEG','.jpeg':'JPEG','.bmp':'BMP','.tif':'TIFF','.tiff':'TIFF'}
ALLOWED=set(FORMATS)|{'.pdf','.docx','.xlsx','.xlsm','.csv','.txt'}

def validate(upload, only_pdf=False):
    from transfer_limits import document_limit, on_vercel
    limit = document_limit()
    if not upload or not upload.filename:abort(400,description='Choose a supported document.')
    name=upload.filename
    if len(name)>120 or any(c in name for c in ('/','\\','\x00')) or any(ord(c)<32 for c in name):
        abort(400,description='Use a plain document filename of at most 120 characters.')
    ext=Path(name).suffix.lower()
    if ext not in ({'.pdf'} if only_pdf else ALLOWED):abort(400,description='Unsupported document format.')
    content=upload.stream.read(limit+1)
    if not content or len(content)>limit:
        abort(413 if on_vercel() else 400,description=f'Document exceeds the {limit:,}-byte upload limit. Split or reduce the file; no source record was changed.')
    try:
        if ext in FORMATS:
            with Image.open(BytesIO(content)) as image:
                if image.format!=FORMATS[ext] or getattr(image,'n_frames',1)>30:raise ValueError()
                pixels=0
                for index in range(getattr(image,'n_frames',1)):
                    image.seek(index); size=image.width*image.height;pixels+=size
                    if size>20000000 or pixels>80000000:raise ValueError()
                image.seek(0);image.verify()
        elif ext=='.pdf':
            import fitz
            if not content.startswith(b'%PDF-'):raise ValueError()
            with fitz.open(stream=content,filetype='pdf') as doc:
                if doc.needs_pass or not 1<=len(doc)<=30:raise ValueError()
                if any(page.rect.width*page.rect.height>3000000 for page in doc):raise ValueError()
        elif ext in ('.docx','.xlsx','.xlsm'):
            with zipfile.ZipFile(BytesIO(content)) as archive:
                entries=archive.infolist()
                required='word/document.xml' if ext=='.docx' else 'xl/workbook.xml'
                if required not in archive.namelist() or len(entries)>2000:raise ValueError()
                if sum(e.file_size for e in entries)>64*1024*1024:raise ValueError()
                for entry in entries:
                    if entry.flag_bits&1 or entry.file_size>32*1024*1024 or entry.file_size>max(1048576,entry.compress_size*100):raise ValueError()
                    if entry.filename.endswith('.xml'):
                        xml=archive.read(entry)
                        if b'<!DOCTYPE' in xml.upper() or b'<!ENTITY' in xml.upper():raise ValueError()
            if ext in ('.xlsx', '.xlsm'):
                from openpyxl import load_workbook
                from workbook_limits import validate_dimensions
                workbook = load_workbook(BytesIO(content), read_only=True, data_only=True, keep_links=False)
                try:validate_dimensions(workbook)
                finally:workbook.close()
        else:
            if len(content)>2*1024*1024:raise ValueError()
            text=content.decode('utf-8-sig')
            if any(ord(c)<32 and c not in '\r\n\t' for c in text):raise ValueError()
            if ext=='.csv':
                rows=list(csv.reader(text.splitlines()))
                if len(rows)>2000 or any(len(row)>100 for row in rows):raise ValueError()
    except (ValueError,OSError,RuntimeError,UnicodeError,csv.Error,zipfile.BadZipFile,UnidentifiedImageError,SyntaxError,EOFError,Image.DecompressionBombError):
        abort(400,description='Document content is invalid or exceeds page, image, row or decompression limits.')
    return name,ext,content

@contextmanager
def extraction_path(content,ext):
    # Only this uniquely created request directory is ever cleaned up.
    with tempfile.TemporaryDirectory(prefix='psms-parse-') as directory:
        path=Path(directory)/('source'+ext);path.write_bytes(content)
        yield str(path)
