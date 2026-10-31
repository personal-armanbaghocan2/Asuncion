"""Offline, schema-driven PPMP OCR/document mapper.

The mapper never calls a cloud API.  It combines a local sentence-transformer
(when installed), fuzzy similarity, value-shape validation and source context.
The destination PPMP schema is fixed; source wording/layout is not.
"""
from __future__ import annotations

import math
import os
import re
from threading import RLock
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

try:
    from rapidfuzz.fuzz import ratio as fuzzy_ratio
except Exception:  # optional until setup is completed
    fuzzy_ratio = None

BASE_DIR = Path(__file__).resolve().parent
DEFAULT_MODEL_DIR = BASE_DIR / "models" / "ocr_mapper" / "all-MiniLM-L6-v2"
MODEL_DIR = Path(os.environ.get("OCR_SEMANTIC_MODEL_PATH", str(DEFAULT_MODEL_DIR)))
PARSER_VERSION = 'offline-document-structure-v6'

PPMP_FIELDS: Dict[str, Dict[str, Any]] = {
    "description": {
        "label": "General Description and Objective of the Project to be Procured",
        "meaning": "general description, objective, particulars or requirement of the procurement project, goods, works or services",
        "type": "text",
        "aliases": ["description", "project description", "procurement description", "particulars", "project particulars", "requirement", "project requirement", "item description", "objective"],
    },
    "project_type": {
        "label": "Type of the Project to be Procured",
        "meaning": "classification, category or type of the procurement project, goods, services, infrastructure or equipment",
        "type": "category",
        "aliases": ["project type", "type of project", "classification", "category", "procurement type", "project classification"],
    },
    "quantity_size": {
        "label": "Quantity and Size of the Project to be Procured",
        "meaning": "quantity, number of units, size, dimensions, technical specifications or item specification",
        "type": "quantity_spec",
        "aliases": ["quantity", "qty", "units", "required units", "units needed", "size", "specification", "specifications", "technical specification", "technical specs", "technical requirement"],
    },
    "procurement_mode": {
        "label": "Recommended Mode of Procurement",
        "meaning": "method or mode used to procure goods equipment works or services including bidding shopping or negotiated procurement",
        "type": "procurement_mode",
        "aliases": ["mode of procurement", "procurement method", "method of procurement", "bidding method", "procurement mode", "planned acquisition", "acquisition method"],
    },
    "pre_procurement": {
        "label": "Pre-Procurement Conference Applicable (Yes/No)",
        "meaning": "whether a pre procurement, pre-bid or procurement conference is applicable or required",
        "type": "boolean",
        "aliases": ["pre procurement conference", "pre-bid conference", "prebid conference", "conference applicable", "pre-bid requirement", "pre bid requirement", "conference", "pre procurement requirement"],
    },
    "start_date": {
        "label": "Start of Procurement Activity",
        "meaning": "date or period when procurement activity begins or starts",
        "type": "date",
        "aliases": ["start date", "procurement start", "date started", "start of procurement", "target start", "planned start"],
    },
    "end_date": {
        "label": "End of Procurement Activity",
        "meaning": "date or period when procurement activity ends, closes or is completed",
        "type": "date",
        "aliases": ["end date", "procurement end", "completion date", "end of procurement", "completion target", "target finish", "planned completion"],
    },
    "delivery_period": {
        "label": "Expected Delivery/Implementation Period",
        "meaning": "expected delivery schedule, implementation period, delivery date or implementation schedule",
        "type": "date_or_period",
        "aliases": ["expected delivery", "delivery period", "implementation period", "delivery schedule", "expected implementation", "implementation", "need by"],
    },
    "fund_source": {
        "label": "Source of Funds",
        "meaning": "source, account or origin of funding or budget for the procurement",
        "type": "fund_source",
        "aliases": ["source of funds", "fund source", "funding source", "budget source", "fund", "funding"],
    },
    "budget": {
        "label": "Estimated Budget Allocation (Php)",
        "meaning": "estimated or approved monetary budget, allocation, project cost, total cost or approved budget for procurement",
        "type": "currency",
        "aliases": ["budget", "estimated budget", "approved budget", "approved budget for the contract", "abc", "allocation", "total allocation", "project cost", "cost estimate", "budget ceiling", "financial requirement", "total cost", "amount"],
    },
    "supporting_documents": {
        "label": "Attached Supporting Documents",
        "meaning": "supporting documents, attachments, requirements or documentary evidence for procurement",
        "type": "text",
        "aliases": ["supporting documents", "supporting files", "attachments", "attached documents", "requirements", "documents attached"],
    },
    "remarks": {
        "label": "Remarks",
        "meaning": "additional comments, notes, remarks or other information about the procurement item",
        "type": "text",
        "aliases": ["remarks", "notes", "additional notes", "comments", "additional information"],
    },
}

LABEL_TO_KEY = {v["label"]: k for k, v in PPMP_FIELDS.items()}
KEY_TO_LABEL = {k: v["label"] for k, v in PPMP_FIELDS.items()}
from procurement_document_layout import (header_role, is_summary_label, register_schema,
    SHARED_ROLES, classify_table_row, text_region_role)
register_schema(PPMP_FIELDS)

_MODEL = None
_MODEL_LOAD_LOCK = RLock()
_MODEL_ERROR = None
_SCHEMA_EMBEDDINGS = None


def _clean(value: Any) -> str:
    return re.sub(r"\s+", " ", str('' if value is None else value).replace("\u00a0", " ")).strip(" \t\r\n:|-;")


def _norm(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", " ", _clean(value).lower()).strip()


def _cosine(a, b) -> float:
    try:
        import numpy as np
        a = np.asarray(a, dtype=float); b = np.asarray(b, dtype=float)
        den = float(np.linalg.norm(a) * np.linalg.norm(b))
        return float(np.dot(a, b) / den) if den else 0.0
    except Exception:
        return 0.0


def load_local_model():
    with _MODEL_LOAD_LOCK:
        return _load_local_model()


def _load_local_model():
    """Load only from disk. Never triggers a runtime network download."""
    global _MODEL, _MODEL_ERROR, _SCHEMA_EMBEDDINGS
    if _MODEL is not None:
        return _MODEL
    if not MODEL_DIR.exists():
        _MODEL_ERROR = f"Local OCR semantic model not found at {MODEL_DIR}"
        return None
    try:
        from sentence_transformers import SentenceTransformer
        try:
            model = SentenceTransformer(str(MODEL_DIR), local_files_only=True)
        except TypeError:  # older compatible versions
            os.environ.setdefault("HF_HUB_OFFLINE", "1")
            model = SentenceTransformer(str(MODEL_DIR))
        meanings = [PPMP_FIELDS[k]["meaning"] for k in PPMP_FIELDS]
        _SCHEMA_EMBEDDINGS = model.encode(meanings, normalize_embeddings=True)
        _MODEL = model
        _MODEL_ERROR = None
        return model
    except Exception as exc:
        _MODEL_ERROR = str(exc)
        return None


def model_status() -> Dict[str, Any]:
    model = load_local_model()
    return {
        "active": bool(model),
        "path": str(MODEL_DIR),
        "message": "Local semantic mapper active." if model else (_MODEL_ERROR or "Local semantic mapper unavailable."),
        "rapidfuzz": bool(fuzzy_ratio),
    }


def _semantic_scores(label: str) -> Dict[str, float]:
    model = load_local_model()
    if not model or _SCHEMA_EMBEDDINGS is None or not _clean(label):
        return {k: 0.0 for k in PPMP_FIELDS}
    cache = _DOCUMENT_SEMANTIC_SCORES.get()
    key = _clean(label)
    if cache is not None and key in cache:
        return dict(cache[key])
    vec = model.encode([key], normalize_embeddings=True)[0]
    scores = {k: max(0.0, min(1.0, _cosine(vec, emb))) for k, emb in zip(PPMP_FIELDS.keys(), _SCHEMA_EMBEDDINGS)}
    if cache is not None:
        cache[key] = scores
    return dict(scores)


def _fuzzy_score(label: str, key: str) -> float:
    if not fuzzy_ratio or not _clean(label):
        return 0.0
    choices = [PPMP_FIELDS[key]["label"], PPMP_FIELDS[key]["meaning"], *PPMP_FIELDS[key]["aliases"]]
    return max(fuzzy_ratio(_norm(label), _norm(c)) / 100.0 for c in choices if _norm(c))


MONTHS = r"jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|jul(?:y)?|aug(?:ust)?|sep(?:tember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?"
DATE_RE = re.compile(rf"\b(?:{MONTHS})\s+\d{{1,2}}(?:,\s*\d{{4}})?\b|\b(?:{MONTHS})\s+\d{{4}}\b|\b\d{{1,4}}[-/]\d{{1,2}}[-/]\d{{1,4}}\b", re.I)
CURRENCY_RE = re.compile(r"(?:₱|PHP|Php|php|P\s*)?\s*\d{1,3}(?:,\d{3})+(?:\.\d{1,2})?|(?:₱|PHP|Php|php)\s*\d+(?:\.\d{1,2})?", re.I)
QTY_RE = re.compile(r"\b\d+(?:\.\d+)?\s*(?:unit|units|pc|pcs|piece|pieces|box|boxes|bottle|bottles|roll|rolls|lot|lots|set|sets|ream|reams|pack|packs)\b", re.I)


def _value_type_score(value: str, key: str) -> float:
    v = _clean(value)
    if not v:
        return 0.0
    typ = PPMP_FIELDS[key]["type"]
    low = v.lower()
    if typ == "currency":
        if CURRENCY_RE.search(v): return 1.0
        if re.fullmatch(r"\d+(?:[,.]\d+)*", v): return 0.70
        return 0.05
    if typ == "quantity_spec":
        if QTY_RE.search(v): return 1.0
        if re.search(r"\b\d+(?:\.\d+)?\b", v) and len(v) > 4: return 0.65
        return 0.25 if len(v) > 10 else 0.05
    if typ in ("date", "date_or_period"):
        if DATE_RE.search(v): return 1.0
        if re.search(r"\b\d+\s*(?:day|days|week|weeks|month|months)\b", low): return 0.85
        return 0.05
    if typ == "boolean":
        return 1.0 if low in {"yes", "no", "applicable", "not applicable", "n/a", "na"} else 0.1
    if typ == "procurement_mode":
        terms = ["bidding", "shopping", "procurement", "negotiated", "contracting", "quotation", "small value"]
        return 0.95 if any(t in low for t in terms) else 0.15
    if typ == "fund_source":
        terms = ["fund", "gf", "sef", "trust", "general fund", "special education"]
        return 0.9 if any(t in low for t in terms) else 0.2
    if typ == "category":
        terms = ["goods", "service", "services", "equipment", "works", "infrastructure", "consulting"]
        return 0.9 if any(t in low for t in terms) else 0.35
    return 0.65 if len(v) >= 2 else 0.1


def _context_score(label: str, value: str, key: str, neighbors: str = "") -> float:
    text = _norm(" ".join([label, value, neighbors]))
    if not text:
        return 0.0
    # This is intentionally generic: compare words from the schema meaning rather than file-specific phrases.
    schema_words = {w for w in _norm(PPMP_FIELDS[key]["meaning"]).split() if len(w) > 3}
    words = set(text.split())
    overlap = len(schema_words & words) / max(1, min(6, len(schema_words)))
    return min(1.0, overlap * 1.8)


def classify_field(label: str, value: str, neighbors: str = "") -> Tuple[str, float, Dict[str, float]]:
    semantic = _semantic_scores(label or neighbors)
    best_key, best_score, best_parts = "", -1.0, {}
    for key in PPMP_FIELDS:
        sem = semantic.get(key, 0.0)
        fuzzy = _fuzzy_score(label or neighbors, key)
        vtype = _value_type_score(value, key)
        context = _context_score(label, value, key, neighbors)
        # When the semantic model is missing, redistribute semantic weight to the other local signals.
        if load_local_model():
            score = sem * 0.45 + vtype * 0.25 + context * 0.20 + fuzzy * 0.10
        else:
            score = vtype * 0.40 + context * 0.30 + fuzzy * 0.30
        if score > best_score:
            best_key, best_score = key, score
            best_parts = {"semantic": sem, "value_type": vtype, "context": context, "fuzzy": fuzzy}
    return best_key, max(0.0, min(1.0, best_score)), best_parts


def confidence_label(score: float) -> str:
    if score >= 0.85: return "High"
    if score >= 0.65: return "Medium"
    if score >= 0.45: return "Low"
    return "Unmapped"


def _split_lines(text: str) -> List[str]:
    """Return readable OCR lines while preserving label punctuation such as ':'."""
    out = []
    from procurement_document_layout import ALIASES
    explicit_labels = ['Fiscal Year', 'Office'] + [label for field in PPMP_FIELDS.values()
        for label in [field['label'], *field['aliases']]]
    explicit_labels += [label for role in ('name', 'quantity', 'quantity_size', 'unit', 'unit_cost', 'amount') for label in ALIASES[role]]
    inline_boundary = re.compile(r'\s+(?=(?:' + '|'.join(re.escape(label) for label in sorted(explicit_labels, key=len, reverse=True)) + r')\s*[:=])', re.I)
    # Split explicit neighboring form cells before whitespace normalization.
    for raw in re.split(r'[\r\n]+|\s*\|\s*|\t+', str(text or "")):
        line = re.sub(r"\s+", " ", raw.replace("\u00a0", " ")).strip()
        if line:
            separator = re.search(r'[:=]', line)
            if separator:
                # Never split inside a label such as "Received Quantity".
                parts = inline_boundary.split(line[separator.end():])
                out.append(line[:separator.end()] + parts[0])
                out.extend(part for part in parts[1:] if part)
            else:
                out.append(line)
    return out


def _label_text(value: str) -> str:
    return re.sub(r"\s*[:=\-]+\s*$", "", str(value or "")).strip()


def _lexical_label_score(label: str, key: str) -> float:
    """Schema-driven lexical similarity for a potential source label.

    This does not depend on a particular uploaded document.  It compares the
    candidate against the destination label, semantic description and aliases.
    """
    src = _norm(_label_text(label))
    if not src:
        return 0.0
    field = PPMP_FIELDS[key]
    candidates = [field["label"], field["meaning"], *field.get("aliases", [])]
    best = 0.0
    src_tokens = set(src.split())
    for cand in candidates:
        cn = _norm(cand)
        if not cn:
            continue
        if src == cn:
            return 1.0
        # Exact phrase containment is strong, but require a meaningful phrase.
        if len(cn) >= 5 and (cn in src or src in cn):
            best = max(best, 0.93 if len(cn.split()) >= 2 else 0.82)
        ct = set(cn.split())
        overlap = len(src_tokens & ct) / max(1, min(len(src_tokens), len(ct)))
        best = max(best, min(0.90, overlap * 0.90))
    if fuzzy_ratio:
        best = max(best, _fuzzy_score(src, key))
    return max(0.0, min(1.0, best))


def _best_label_key(label: str) -> Tuple[str, float, Dict[str, float]]:
    """Classify a *label*, not a label/value pair.

    Separating label recognition from value recognition prevents values such as
    'Procurement of ICT Equipment' from being mistaken for the next field name.
    """
    from procurement_item_contract import field_kind
    if _norm(label) in {'no', 'period', 'activity', 'of procurement', 'procurement activity'}:
        return '', 0.0, {}
    # Numeric column roles must not be collapsed into budget or quantity/size.
    if field_kind(label) in {'unit', 'unit_cost', 'amount', 'different_meaning'}:
        return '', 0.0, {}
    exact = {'name':'description','quantity':'quantity_size','specification':'quantity_size'}.get(header_role(label),header_role(label))
    if exact in PPMP_FIELDS:
        return exact,1.0,{'label_lexical':1.0,'label_semantic':0.0,'exact_source_label':True}
    semantic = _semantic_scores(_label_text(label))
    best_key, best_score, best_parts = "", 0.0, {}
    for key in PPMP_FIELDS:
        lexical = _lexical_label_score(label, key)
        sem = semantic.get(key, 0.0)
        if load_local_model():
            score = lexical * 0.65 + sem * 0.35
        else:
            score = lexical
        if score > best_score:
            best_key, best_score = key, score
            best_parts = {"label_lexical": lexical, "label_semantic": sem}
    return best_key, max(0.0, min(1.0, best_score)), best_parts


def _looks_like_value(line: str) -> bool:
    clean = _clean(line)
    low = clean.lower()
    if not clean:
        return False
    if CURRENCY_RE.fullmatch(clean) or QTY_RE.search(clean) or DATE_RE.fullmatch(clean):
        return True
    if low in {"yes", "no", "applicable", "not applicable", "n/a", "na", "gf", "sef"}:
        return True
    # Long descriptive/specification text is much more likely a value than a label.
    if len(clean) > 100:
        return True
    return False


def _label_value_candidates(lines: List[str]) -> List[Tuple[str, str, str, str, float]]:
    """Extract conservative label/value pairs from OCR text.

    Returns (label, value, neighbors, key, label_confidence).  Explicit colon/
    equals labels are strongly preferred.  Crucially, we do NOT pair every line
    with the next line; that behavior caused values to be misread as labels.
    """
    out: List[Tuple[str, str, str, str, float]] = []
    consumed_value_indexes = set()

    for i, original in enumerate(lines):
        if i in consumed_value_indexes:
            continue
        line = original.strip()
        if text_region_role(line) in {'document_header','document_title','signature','page_boundary','shared_metadata'}:
            # Exact shared labels still need their following source value.
            if header_role(line) not in SHARED_ROLES and not re.match(r'^.{2,140}?\s*[:=]',line):
                continue
        neighbors = " ".join(lines[max(0, i-1):min(len(lines), i+3)])

        # 1) Strongest form: explicit 'Label: value' / 'Label = value'.
        m = re.match(r"^(.{2,140}?)[\s]*[:=][\s]*(.*)$", line)
        if m:
            label = _label_text(m.group(1))
            inline_value = _clean(m.group(2))
            key, lscore, _ = _best_label_key(label)
            explicit = header_role(label)
            explicit = {'name': 'description', 'quantity': 'quantity_size', 'specification': 'quantity_size'}.get(explicit, explicit)
            if explicit in PPMP_FIELDS:
                key, lscore = explicit, 1.0
            elif lscore < 0.85:
                key = ''
            if key and lscore >= 0.42:
                value = inline_value
                if not value:
                    # Find the next non-label line as the value.
                    for j in range(i + 1, min(len(lines), i + 4)):
                        nxt = lines[j].strip()
                        if not nxt:
                            continue
                        nkey, nscore, _ = _best_label_key(_label_text(nxt))
                        is_explicit_next_label = bool(re.match(r"^.{2,140}?\s*[:=]\s*.*$", nxt))
                        if is_explicit_next_label:
                            break
                        value = _clean(nxt)
                        consumed_value_indexes.add(j)
                        break
                if value:
                    out.append((label, value, neighbors, key, lscore))
            continue

        # 2) Label on its own line without punctuation. Be conservative.
        if _looks_like_value(line):
            continue
        key, lscore, _ = _best_label_key(line)
        if not key or lscore < 0.68:
            continue
        for j in range(i + 1, min(len(lines), i + 3)):
            nxt = lines[j].strip()
            if not nxt:
                continue
            nkey, nscore, _ = _best_label_key(_label_text(nxt))
            if nscore >= 0.72 and not _looks_like_value(nxt):
                break
            out.append((_label_text(line), _clean(nxt), neighbors, key, lscore))
            consumed_value_indexes.add(j)
            break
    return out

def _empty_record(temp_id: str) -> Dict[str, Any]:
    return {
        "temp_id": temp_id,
        "fields": {v["label"]: "" for v in PPMP_FIELDS.values()},
        "field_confidence": {v["label"]: {"score": 0.0, "level": "Unmapped", "source_label": ""} for v in PPMP_FIELDS.values()},
        "average_confidence": 0.0,
        "status": "Needs Review",
    }


def _finalize(record: Dict[str, Any]) -> Dict[str, Any]:
    scores = [x["score"] for label, x in record["field_confidence"].items() if record["fields"].get(label)]
    avg = sum(scores) / len(scores) if scores else 0.0
    record["average_confidence"] = round(avg, 4)
    populated = sum(1 for x in record["fields"].values() if _clean(x))
    lows = any(x["level"] in {"Low", "Unmapped"} for label, x in record["field_confidence"].items() if record["fields"].get(label))
    record["status"] = "High Confidence" if avg >= 0.85 and populated >= 4 and not lows else "Needs Review"
    return record


def _apply_candidate(
    record: Dict[str, Any],
    label: str,
    value: str,
    neighbors: str = "",
    *,
    forced_key: Optional[str] = None,
    source_priority: int = 1,
    label_score: Optional[float] = None,
) -> None:
    """Apply a candidate without allowing weak heuristics to overwrite explicit fields."""
    if not _clean(value):
        return

    # Recognized text is not proof of field ownership. In particular, never
    # accept an entire item line as a monetary, boolean or date value.
    from procurement_item_contract import decimal_number
    if forced_key == 'budget' and decimal_number(_clean(value), money=True) is None:
        record.setdefault('warnings', []).append('Budget source is not a single numeric value; manual review required.')
        return
    if forced_key == 'pre_procurement' and _clean(value).lower() not in {'yes', 'no', 'applicable', 'not applicable', 'n/a', 'na'}:
        record.setdefault('warnings', []).append('Conference applicability is ambiguous; manual review required.')
        return
    if forced_key == 'procurement_mode' and re.search(r'\b(?:yes|no)\s*$', value, re.I):
        record.setdefault('warnings', []).append('Procurement mode includes an adjacent applicability value; confirm the separate fields.')
        return
    if forced_key in {'start_date', 'end_date', 'delivery_period'} and ('|' in value or re.search(r'\b(?:fund|funding|source)\b', value, re.I)):
        return
    if forced_key in {'start_date', 'end_date'} and len(DATE_RE.findall(value)) > 1:
        record.setdefault('warnings', []).append('Multiple dates occupy one activity field. Verify start and end separately.')
        return

    if forced_key:
        key = forced_key
        lexical = _lexical_label_score(label, key) if label_score is None else label_score
        exact = {'name':'description','quantity':'quantity_size','specification':'quantity_size'}.get(header_role(label),header_role(label))
        sem = _semantic_scores(label).get(key, 0.0) if _clean(label) and exact != key else 0.0
        vtype = _value_type_score(value, key)
        context = _context_score(label, value, key, neighbors)
        # Explicit labels should be trusted primarily for *mapping* while the
        # value-shape/context signals still influence confidence.
        score = lexical * 0.60 + vtype * 0.25 + max(context, sem) * 0.15
        parts = {"label": lexical, "semantic": sem, "value_type": vtype, "context": context}
    else:
        key, score, parts = classify_field(label, value, neighbors)

    if key == 'budget' and decimal_number(_clean(value), money=True) is None:
        return

    if not key or score < 0.45:
        return
    target = KEY_TO_LABEL[key]
    current_meta = record["field_confidence"].get(target, {})
    existing = float(current_meta.get("score", 0.0) or 0.0)
    existing_priority = int(current_meta.get("source_priority", 0) or 0)

    # Explicit schema labels outrank contextual/free-text guesses, even if a
    # heuristic gets a slightly higher numerical score.
    should_replace = (
        not record["fields"].get(target)
        or source_priority > existing_priority
        or (source_priority == existing_priority and score > existing + 0.015)
    )
    if should_replace:
        display = _clean(value)
        if key in {'start_date', 'end_date', 'delivery_period'}:
            display = re.sub(r'\b(January|February|March|April|May|June|July|August|September|October|November|December)(20\d{2})\b',
                             r'\1 \2', display, flags=re.I)
        record["fields"][target] = display
        record["field_confidence"][target] = {
            "score": round(score, 4),
            "level": confidence_label(score),
            "source_label": _clean(label),
            "source_value": str(value),
            "normalized_display": display != _clean(value),
            "source_priority": source_priority,
            "signals": parts,
        }

def _key_value_table_rows(table: Dict[str, Any], start_index: int) -> List[Dict[str, Any]]:
    """Recognize two/few-column form tables where each row is Label -> Value.

    DOCX/XLSX/PDF procurement forms are often represented as a vertical table rather
    than paragraphs. Treating each table row as a separate procurement item loses the
    document structure, so detect several distinct PPMP labels before accepting this
    interpretation.
    """
    raw_rows = table.get("rows") or []
    rows = [(index, [_clean(c) for c in row]) for index, row in enumerate(raw_rows)
            if isinstance(row, (list, tuple)) and any(_clean(c) for c in row)]
    if len(rows) < 3:
        return []
    pairs = []
    keys_seen = set()
    for index, row in rows:
        nonempty = [(i, cell) for i, cell in enumerate(row) if _clean(cell)]
        if len(nonempty) < 2:
            continue
        label = nonempty[0][1]
        value = " | ".join(cell for _, cell in nonempty[1:] if _clean(cell))
        role = header_role(label)
        key = {'name': 'description', 'quantity': 'quantity_size', 'specification': 'quantity_size', 'amount': 'budget'}.get(role, role)
        if not role:
            continue
        if value:
            pairs.append((index, label, value, key, role))
            if key in PPMP_FIELDS:
                keys_seen.add(key)
    # A vertical form should expose several *different* schema labels down its
    # first populated column. Ordinary item tables normally have only one header
    # label followed by item names, so they fail this test.
    if len(pairs) < 3 or len(keys_seen) < 3 or (len(pairs) / max(1, len(rows))) < 0.45:
        return []
    groups, shared, current, seen_name = [], [], [], False
    for pair in pairs:
        role = pair[4]
        if (role == 'name' and seen_name) or (role == 'item_no' and seen_name):
            groups.append(current)
            current, seen_name = [], False
        if role in {'name', 'item_no'} or current:
            current.append(pair)
            seen_name = seen_name or role == 'name'
        else:
            shared.append(pair)
    if current:
        groups.append(current)
    if not groups:
        return []
    if len(groups) > 1:
        shared = [pair for pair in shared if pair[3] in PPMP_FIELDS and pair[3] not in {'description', 'quantity_size', 'budget'}]
    result = []
    for group in groups:
        if classify_table_row([pair[1] for pair in group], [pair[2] for pair in group])['role'] != 'item_row':
            continue
        rec = _empty_record(f"OCR-TEMP-{start_index + len(result):03d}")
        selected = shared + group
        for index, label, value, key, role in selected:
            if key not in PPMP_FIELDS:
                continue
            target = KEY_TO_LABEL[key]
            if key == 'quantity_size' and rec['fields'][target]:
                rec['fields'][target] += '; ' + value
            else:
                _apply_candidate(rec, label, value, forced_key=key, source_priority=4, label_score=1.0)
        rec['_source_cells'] = [(label, value) for _, label, value, _, _ in selected]
        indices = {pair[0] for pair in selected}
        physical_rows = table.get('row_numbers') or list(range(1, len(raw_rows)+1))
        rec['field_confidence']['_source_evidence'] = dict(source=table.get('source', ''), page=table.get('page'),
            worksheet=table.get('worksheet'), rows=[physical_rows[index] for index in sorted(indices)],
            item_number=next((value for _, _, value, _, role in group if role == 'item_no'), ''),
            text='\n'.join(f'{label}: {value}' for _, label, value, _, _ in selected),
            cells=[cell for cell in table.get('cells', []) if cell.get('row', 0)-1 in indices or
                   cell.get('worksheet_row') in {physical_rows[index] for index in indices}])
        result.append(_finalize(rec))
    return result


def _detect_table_header(rows: List[List[str]], *, start_only=False) -> Tuple[int, List[str], Dict[int, Tuple[str, float]]]:
    """Find the row that most plausibly contains PPMP-related column headings."""
    best_index = -1
    best_headers: List[str] = []
    best_map: Dict[int, Tuple[str, float]] = {}
    best_score = -1.0
    from procurement_document_layout import table_header
    index, labels = table_header(rows, start_only=start_only)
    for idx, row in [(index, labels)] if index >= 0 else []:
        mapped: Dict[int, Tuple[str, float]] = {}
        distinct = set()
        strength = 0.0
        for col, cell in enumerate(row):
            key = header_role(cell)
            key = {'name': 'description', 'quantity': 'quantity_size', 'specification': 'quantity_size'}.get(key, key)
            score = 1.0 if key in PPMP_FIELDS else 0.0
            if key and score >= 0.40:
                mapped[col] = (key, score)
                distinct.add(key)
                strength += score
        # Require multiple semantic columns for a real item table. This avoids
        # treating title lines and ordinary data rows as headers.
        roles = {header_role(cell) for cell in row} - {''}
        score_total = len(roles) * 10.0 + strength
        # Use the first explicit item header. A richer repeated header later in
        # the document must not discard the items preceding it.
        if mapped and 'name' in roles and roles & {'quantity', 'quantity_size', 'budget', 'amount', 'unit_cost'}:
            return idx, row, mapped
        if len(distinct) >= 2 and score_total > best_score:
            best_index, best_headers, best_map, best_score = idx, row, mapped, score_total
    return -1, [], {}


def _rows_from_table(table: Dict[str, Any], start_index: int, candidates=None, non_items=None) -> List[Dict[str, Any]]:
    candidates = candidates if candidates is not None else []
    non_items = non_items if non_items is not None else []
    rows = table.get("rows") or []
    rows = [list(row) for row in rows]
    uncertain_headers = set()
    for cell in table.get('cells', []):
        r, c = cell.get('row', 0)-1, cell.get('column', 0)-1
        # A legible exact header with low engine confidence may guide review,
        # but its column remains explicitly uncertain. Never fill a data cell.
        if 0 <= r < len(rows) and 0 <= c < len(rows[r]) and cell.get('uncertain') and header_role(cell.get('raw_text')):
            if not rows[r][c]:rows[r][c] = cell['raw_text']
            uncertain_headers.add((r, c))
    # Empty physical rows keep their indices so item evidence cannot shift.
    rows = [[_clean(c) for c in row] for row in rows if isinstance(row, (list, tuple))]
    if len(rows) < 2:
        return []

    # First handle a form represented as a vertical key/value table.
    kv = _key_value_table_rows(dict(table, rows=rows), start_index)
    if kv:
        return kv

    header_index, headers, header_map = _detect_table_header(rows)
    if header_index < 0:
        # Conservative fallback: only use the first row as a header when it is
        # mostly textual. Otherwise leave the data for the free-text reviewer.
        header = rows[0]
        textual = sum(1 for c in header if c and not CURRENCY_RE.search(c) and not re.fullmatch(r"[\d,.]+", c))
        # Unsupported columns remain raw evidence, never semantic guesses.
        return []

    body = rows[header_index + 1:]
    result = []
    header_through = header_index
    for body_index, row in enumerate(body, start=header_index + 1):
        if body_index <= header_through:
            continue
        if not any(row):
            captured=[cell for cell in table.get('cells',[]) if cell.get('row')==body_index+1 and cell.get('raw_text')]
            if captured:
                # Withheld cells still belong to a recoverable source region.
                # Never promote uncertain numbers, or silently erase the row.
                candidates.append(dict(role='uncertain_region',indicators=['withheld_source_cells'],
                    warning='Source row has boundary/confidence conflicts. Compare its original cells before manual review.',
                    evidence=dict(source=table.get('source',''),page=table.get('page'),headers=headers,
                        row=body_index+1,cells=captured,text=' | '.join(cell['raw_text'] for cell in captured))))
            continue
        repeated_index, repeated_headers, repeated_map = _detect_table_header(rows[body_index:body_index+3], start_only=True)
        if repeated_index >= 0:
            header_index, headers, header_map = body_index+repeated_index, repeated_headers, repeated_map
            header_through = header_index
            continue
        if [_norm(cell) for cell in row] == [_norm(cell) for cell in headers]:
            continue
        first = next((_norm(value) for value in row if value), '')
        source_summaries = [cell.get('raw_text', '') for cell in table.get('cells', []) if cell.get('row') == body_index+1]
        if any(is_summary_label(text) for text in source_summaries):
            continue
        if is_summary_label(first):
            continue
        if sum(bool(header_role(value)) for value in row) >= 3:
            continue
        roles = [header_role(label) for label in headers]
        for cell in table.get('cells', []):
            column = cell.get('column', 0)-1
            if cell.get('row') == body_index+1 and cell.get('inherited_merged') and 0 <= column < len(row):
                if column >= len(roles) or roles[column] not in SHARED_ROLES | {'project_type'}:
                    row[column] = ''
        # Only a description/specification-only line can continue an item.
        # A populated item number or numeric column always starts another row.
        if result and any(row) and all(not value or (i < len(roles) and roles[i] == 'specification') for i, value in enumerate(row)):
            previous = result[-1]
            for i, value in enumerate(row):
                if not value:
                    continue
                label = headers[i]
                previous['_source_cells'] = [(h, (v + ' ' + value).strip() if h == label else v)
                                              for h, v in previous['_source_cells']]
                key = KEY_TO_LABEL['description' if roles[i] == 'name' else 'quantity_size']
                previous['fields'][key] = (previous['fields'].get(key, '') + ' ' + value).strip()
            evidence = previous['field_confidence'].get('_source_evidence', {})
            evidence.setdefault('continuation_rows', []).append(
                (table.get('row_numbers') or list(range(1, len(rows)+1)))[body_index])
            evidence.setdefault('cells', []).extend(cell for cell in table.get('cells', []) if cell.get('row') == body_index+1)
            evidence['text'] = evidence.get('text', '') + '\n' + ' | '.join(row)
            continue
        assessment = classify_table_row(headers, row)
        if assessment['role'] != 'item_row':
            evidence = dict(source=table.get('source', ''), page=table.get('page'), worksheet=table.get('worksheet'),
                row=(table.get('row_numbers') or list(range(1, len(rows)+1)))[body_index],
                headers=headers, text=' | '.join(row),
                cells=[cell for cell in table.get('cells', []) if cell.get('row') == body_index+1])
            content = dict(role=assessment['role'], indicators=assessment['indicators'], evidence=evidence)
            non_items.append(content)
            if assessment['role'] == 'uncertain_region':
                candidates.append(content)
            continue
        rec = _empty_record(f"OCR-TEMP-{start_index + len(result):03d}")
        rec['_source_cells'] = [(headers[i], value) for i, value in enumerate(row) if i < len(headers)]
        rec['field_confidence']['_table_source'] = dict(source=table.get('source', ''), page=table.get('page'),
            extracted_row_index=body_index, header_row_index=header_index,
            worksheet=table.get('worksheet'),
            worksheet_row=(table.get('row_numbers') or [None] * len(rows))[body_index],
            uncertain_header_columns=[c for r, c in uncertain_headers if r == header_index],
            columns=[dict(index=i, label=label) for i, label in enumerate(headers)])
        rec['field_confidence']['_source_evidence'] = dict(
            source=table.get('source', ''), page=table.get('page'), worksheet=table.get('worksheet'),
            row=rec['field_confidence']['_table_source']['worksheet_row'] or body_index+1,
            item_number=next((row[i] for i, role in enumerate(roles) if role == 'item_no' and i < len(row)), ''),
            headers=headers, text=' | '.join(row),
            header_cells=[cell for cell in table.get('cells', []) if cell.get('row') == header_index+1],
            cells=[cell for cell in table.get('cells', []) if cell.get('row') == body_index+1 or
                   (cell.get('worksheet_row') and cell.get('worksheet_row') == rec['field_confidence']['_table_source']['worksheet_row'])])
        for i, value in enumerate(row):
            if not _clean(value):
                continue
            label = headers[i] if i < len(headers) else f"Column {i+1}"
            if i in header_map:
                key, label_score = header_map[i]
                target_label = KEY_TO_LABEL[key]
                if key == 'quantity_size' and roles[i] == 'quantity' and not re.search(r'\b(?:size|specifications?|specs)\b', label, re.I):
                    narrative = any(value and re.search(r'\b(?:size|specifications?|specs)\b', header, re.I)
                                    and header_role(header) in {'quantity', 'quantity_size'}
                                    for header, value in rec['_source_cells'])
                    if narrative:
                        continue
                # Tables commonly split Qty and Technical Specification into two
                # columns even though PPMP stores them in one Quantity/Size field.
                # Preserve both values instead of letting the stronger one overwrite.
                if key == "quantity_size" and _clean(rec["fields"].get(target_label)):
                    existing = _clean(rec["fields"][target_label])
                    incoming = _clean(value)
                    if incoming and incoming.lower() != existing.lower():
                        rec["fields"][target_label] = f"{existing}; {incoming}"
                    continue
                _apply_candidate(rec, label, value, " | ".join(row), forced_key=key, source_priority=2, label_score=label_score)
            else:
                from procurement_item_contract import field_kind
                # A PPMP item Amount cell is its estimate; AOQ supplier columns
                # and generic Line Total evidence never become planning budgets.
                if header_role(label) == 'amount' and 'supplier' not in roles:
                    _apply_candidate(rec, label, value, forced_key='budget', source_priority=2, label_score=1.0)
        populated = [k for k, v in rec["fields"].items() if _clean(v)]
        # A real multi-item line should contribute more than one mapped field.
        # This filters table notes/totals from becoming fake procurement rows.
        from procurement_item_contract import field_kind
        typed = {field_kind(label) for label, value in rec['_source_cells'] if value}
        if assessment['role'] == 'item_row':
            for r, c in uncertain_headers:
                if r == header_index:
                    rec.setdefault('warnings', []).append(f'Column {c+1} header has uncertain OCR confidence. Verify field ownership against the source.')
                    if c in header_map:
                        meta = rec['field_confidence'][KEY_TO_LABEL[header_map[c][0]]]
                        meta.update(score=min(meta.get('score', 0), .5), level='Low')
            result.append(_finalize(rec))
    return result

_DOCUMENT_SEMANTIC_SCORES = ContextVar('ppmp_document_semantic_scores', default=None)


def _table_shared_pairs(table):
    """Read label/value cells outside item rows without flattening column owners."""
    rows = table.get('rows') or []
    _, headers, _ = _detect_table_header(rows)
    pairs = []
    for row in rows:
        if _detect_table_header([row])[0] >= 0 or (headers and classify_table_row(headers, row)['role'] == 'item_row'):
            continue
        cells = [_clean(value) for value in row if _clean(value)]
        index = 0
        while index+1 < len(cells):
            label, value = cells[index:index+2]
            role = header_role(label)
            if role in SHARED_ROLES | {'project_type'} and (not header_role(value) or value.lower() in {'yes', 'no'}):
                pairs.append((label, value, '', role, 1.0))
                index += 2
            else:
                index += 1
    return pairs


def map_document(document: Dict[str, Any]) -> Dict[str, Any]:
    """Reuse identical label embeddings only within this document's mapping call.

    Recursive multi-item mapping shares the request-local dictionary. No source
    text or embeddings survive this call or cross another user's request.
    """
    if _DOCUMENT_SEMANTIC_SCORES.get() is not None:
        return _map_document(document)
    token = _DOCUMENT_SEMANTIC_SCORES.set({})
    try:
        return _map_document(document)
    finally:
        _DOCUMENT_SEMANTIC_SCORES.reset(token)


def _map_document(document: Dict[str, Any]) -> Dict[str, Any]:
    """Map extract_document() output to temporary PPMP rows.

    Structured tables are processed first. Explicit form labels from surrounding
    text are then used as shared metadata for table items without overwriting
    row-specific values.
    """
    raw_text = str(document.get("raw_text") or "")
    if not document.get('tables'):
        matrices = []
        for line in raw_text.splitlines():
            if '|' in line or '\t' in line:
                matrices.append(re.split(r'\s*\|\s*|\t', line))
        if matrices and _detect_table_header(matrices)[0] >= 0:
            document = dict(document, tables=[dict(source='Delimited source text', rows=matrices)])
    # Repeated explicit item labels delimit text records. Never merge later
    # quantities/prices into the first item or allocate a document budget per row.
    from procurement_item_contract import field_kind
    if not document.get('tables'):
        groups, current, shared, seen_name = [], [], [], False
        for line in _split_lines(raw_text):
            pair = re.match(r'^(.{2,140}?)\s*[:=]\s*(.*)$', line)
            is_name = bool(pair and field_kind(pair[1]) == 'name' and not re.search(r'\bspecifications?\b', pair[1], re.I))
            if is_name and seen_name:
                groups.append(current)
                current = []
            if is_name:
                seen_name = True
            if seen_name:
                current.append(line)
            else:
                shared.append(line)
        if groups and current:
            groups.append(current)
            shared = [line for line in shared if field_kind(line.split(':', 1)[0]) not in {'budget', 'amount', 'unit_cost', 'quantity', 'unit'}]
            rows, warnings, candidates, non_items = [], list(document.get('warnings') or []), [], []
            for group in groups:
                mapped = map_document(dict(raw_text='\n'.join(shared + group), warnings=[]))
                rows.extend(mapped['rows'])
                warnings.extend(mapped['warnings'])
                candidates.extend(mapped.get('review_candidates', []))
                non_items.extend(mapped.get('non_item_content', []))
            for index, row in enumerate(rows, 1):
                row['temp_id'] = f'OCR-TEMP-{index:03d}'
            return dict(rows=rows, model=model_status(), parser=PARSER_VERSION, raw_text=raw_text,
                document_metadata=_document_metadata(document, _label_value_candidates(_split_lines(raw_text)), warnings),
                review_candidates=candidates, non_item_content=non_items,
                source_evidence=dict(ocr_cells=document.get('ocr_cells') or [], manual_review_required=True),
                warnings=list(dict.fromkeys(warnings + ['Multiple explicit text items were separated. Verify each row and its own budget.'])))
    mapped_rows: List[Dict[str, Any]] = []
    candidates, non_items = [], []
    next_index = 1

    for table in document.get("tables") or []:
        table_rows = _rows_from_table(table, next_index, candidates, non_items)
        mapped_rows.extend(table_rows)
        next_index += len(table_rows)

    context_text = document.get('mapping_context_text',document.get('context_text', raw_text))
    if 'mapping_context_text' not in document and document.get('tables') and all('non_table_text' in table for table in document['tables']):
        context_text = '\n'.join(table['non_table_text'] for table in document['tables'])
    lines = _split_lines(context_text)
    positioned = document.get('shared_field_evidence',[])
    owned_roles={entry.get('role') for entry in positioned if entry.get('value') and
        (entry.get('minimum_word_confidence') is None or entry['minimum_word_confidence']>=55)}
    # Every required shared field already has independent label/value geometry.
    # Do not spend embeddings trying to reinterpret unrelated headings/notes.
    # Raw context and non-item evidence remain unchanged and available for review.
    pairs = [] if mapped_rows and SHARED_ROLES <= owned_roles else _label_value_candidates(lines)
    pairs.extend((entry['label'],entry['value'],'',entry['role'],1.0)
                 for entry in document.get('shared_field_evidence',[]) if entry.get('role') in SHARED_ROLES)
    # A vertical metadata table is context, not a collection of item rows.
    scoped_pairs = {}
    for table in document.get('tables') or []:
        local = _table_shared_pairs(table)
        if table.get('worksheet'):
            scoped_pairs.setdefault(table['worksheet'], []).extend(local)
        else:
            pairs.extend(local)
    form_record = _empty_record(f"OCR-TEMP-{next_index:03d}")
    for label, value, neighbors, key, label_score in pairs:
        if _norm(label) in {'category', 'ppmp category'} and re.fullmatch(r'(?:NON[- ]?)?DBM|LIB', value, re.I):
            continue
        if document.get('tables') and key not in SHARED_ROLES | {'project_type'}:
            continue
        _apply_candidate(
            form_record, label, value, neighbors,
            forced_key=key, source_priority=3, label_score=label_score
        )
    # Typed item amounts stay separate from unit prices. A source amount is a
    # planning estimate only when explicitly attached to this text item.
    if not document.get('tables'):
        for line in lines:
            pair = re.match(r'^(.{2,140}?)\s*[:=]\s*(.*)$', line)
            if pair and header_role(pair[1]) == 'amount':
                _apply_candidate(form_record, pair[1], pair[2], forced_key='budget', source_priority=3, label_score=1.0)

    # Contextual value-shape patterns help documents that have weak/implicit labels.
    # They remain lower priority and therefore cannot replace explicit mappings.
    for i, line in enumerate(lines if not document.get('tables') else []):
        neighbors = " ".join(lines[max(0, i-1):min(len(lines), i+2)])
        from procurement_item_contract import field_kind
        role = field_kind(line.split(':', 1)[0]) if ':' in line else ''
        if CURRENCY_RE.search(line) and role not in {'unit_cost', 'amount', 'different_meaning'}:
            _apply_candidate(form_record, neighbors, line, neighbors, source_priority=1)
        if QTY_RE.fullmatch(line):
            _apply_candidate(form_record, neighbors, line, neighbors, source_priority=1)
        if DATE_RE.search(line):
            _apply_candidate(form_record, neighbors, line, neighbors, source_priority=1)

    form_record = _finalize(form_record)
    for entry in positioned:
        if entry.get('role') in SHARED_ROLES and entry.get('minimum_word_confidence') is not None and entry['minimum_word_confidence']<55:
            info=form_record['field_confidence'].get(KEY_TO_LABEL[entry['role']],{})
            info.update(score=min(info.get('score',0),.5),level='Low',uncertain_source=True)

    # Explicit labels outside a multi-item table (for example Funding: GF or
    # Procurement Method: Shopping) are shared metadata. Inherit them into every
    # table item only when that row does not already have a value for the field.
    scope_cache = {}
    if mapped_rows:
        shared_context = _document_metadata(document, pairs, [])
        for target_row in mapped_rows:
            scoped_form, scoped_context = form_record, shared_context
            worksheet = target_row['field_confidence'].get('_source_evidence', {}).get('worksheet')
            if worksheet:
                if worksheet not in scope_cache:
                    local_pairs = pairs + scoped_pairs.get(worksheet, [])
                    local_tables = [table for table in document.get('tables', []) if table.get('worksheet') == worksheet]
                    scoped_context = _document_metadata(dict(document, tables=local_tables,
                        context_text='\n'.join(' | '.join(_clean(value) for value in source_row)
                            for table in local_tables for source_row in table.get('rows', []))), local_pairs, [])
                    scoped_form = _empty_record('shared-context')
                    for label, value, neighbors, key, score in local_pairs:
                        _apply_candidate(scoped_form, label, value, neighbors, forced_key=key, source_priority=3, label_score=score)
                    scope_cache[worksheet] = scoped_form, scoped_context
                scoped_form, scoped_context = scope_cache[worksheet]
            for field_label, value in scoped_form["fields"].items():
                if field_label not in {KEY_TO_LABEL[key] for key in SHARED_ROLES | {'project_type'}}:
                    continue
                if field_label in scoped_context['shared_fields'] and not scoped_context['shared_fields'][field_label]:
                    continue
                meta = scoped_form["field_confidence"].get(field_label, {})
                if not _clean(value) or int(meta.get("source_priority", 0) or 0) < 3:
                    continue
                if not _clean(target_row["fields"].get(field_label)):
                    target_row["fields"][field_label] = value
                    copied_meta = dict(meta)
                    copied_meta["inherited_shared_context"] = True
                    target_row["field_confidence"][field_label] = copied_meta
            _finalize(target_row)

    form_populated = sum(1 for v in form_record["fields"].values() if _clean(v))
    if form_populated >= 2 and not document.get('tables'):
        desc_label = PPMP_FIELDS["description"]["label"]
        desc = _norm(form_record["fields"].get(desc_label))
        duplicate = bool(desc) and any(_norm(r["fields"].get(desc_label)) == desc for r in mapped_rows)
        # Do not append a second form row when the table itself was a key/value form.
        if not mapped_rows or (not duplicate and not document.get('tables') and any(
            int(m.get("source_priority", 0) or 0) >= 3
            for label, m in form_record["field_confidence"].items()
            if _clean(form_record["fields"].get(label))
        )):
            # If table rows already exist and the form record contains only shared
            # metadata (no distinct description), it is not a separate item.
            form_assessment = classify_table_row(list(form_record['fields']), list(form_record['fields'].values()))
            if desc and form_assessment['role'] == 'item_row':
                mapped_rows.append(form_record)

    if not mapped_rows and lines:
        rec = _empty_record("OCR-TEMP-001")
        rec["fields"][PPMP_FIELDS["description"]["label"]] = lines[0][:5000]
        rec["field_confidence"][PPMP_FIELDS["description"]["label"]] = {
            "score": 0.35, "level": "Unmapped", "source_label": "Free text", "signals": {}
        }
        candidates.append(dict(role='uncertain_region', indicators=[], evidence=dict(text=context_text),
            warning='No item identity with independent row evidence was established. No item draft was created.'))

    # Renumber display temp IDs deterministically. Database persistence later assigns
    # durable OCR-TEMP-000001 style identifiers.
    for idx, row in enumerate(mapped_rows, start=1):
        row["temp_id"] = f"OCR-TEMP-{idx:03d}"

    from procurement_item_contract import enrich_ocr_record
    source_pairs = []
    for i, line in enumerate(lines):
        pair = re.match(r'^(.{2,140}?)\s*[:=]\s*(.*)$', line)
        if pair:
            value = pair[2].strip()
            if not value and i + 1 < len(lines) and not re.match(r'^.{2,140}?\s*[:=]', lines[i + 1]):
                value = lines[i + 1]
            source_pairs.append((pair[1].strip(), value))
    warnings = list(document.get('warnings') or [])
    for row in mapped_rows:
        cells = row.pop('_source_cells', None)
        enrich_ocr_record(row, cells if cells is not None else source_pairs)
        interpretation = row['field_confidence']['_item_interpretation']
        table_source = row['field_confidence'].get('_table_source', {})
        uncertain_targets = dict(name=[KEY_TO_LABEL['description'], 'Item Name'], quantity=[KEY_TO_LABEL['quantity_size'], 'Quantity'],
            specification=[KEY_TO_LABEL['quantity_size']], unit=['Unit'], unit_cost=['Estimated Unit Cost'], amount=[KEY_TO_LABEL['budget']])
        for col in table_source.get('uncertain_header_columns', []):
            headers = row['field_confidence'].get('_source_evidence', {}).get('headers', [])
            if col < len(headers):
                for target in uncertain_targets.get(header_role(headers[col]), []):
                    meta = row['field_confidence'].get(target, {})
                    meta.update(score=min(meta.get('score', 0), .5), level='Low', uncertain_header=True)
        warnings.extend(f"{row['temp_id']}: {warning}" for warning in interpretation['warnings'])
        row_warnings = row.setdefault('warnings', [])
        row_warnings.extend(interpretation['warnings'])
        from procurement_item_contract import decimal_number
        qty = decimal_number(row['fields'].get('Quantity'))
        cost = decimal_number(row['fields'].get('Estimated Unit Cost'), money=True)
        amount = decimal_number(row['fields'].get(KEY_TO_LABEL['budget']), money=True)
        if qty is not None and cost is not None and amount is not None and qty * cost != amount:
            row_warnings.append('Quantity multiplied by unit cost differs from the source budget. Verify all three values; none was replaced.')
        evidence = row['field_confidence'].setdefault('_source_evidence', dict(text='\n'.join(f'{k}: {v}' for k, v in source_pairs)))
        evidence['warnings'] = list(dict.fromkeys(row_warnings))
        warnings.extend(f"{row['temp_id']}: {warning}" for warning in row_warnings)
        _finalize(row)

    metadata = _document_metadata(document, pairs + [pair for local in scoped_pairs.values() for pair in local], warnings)
    for row in mapped_rows:
        worksheet = row['field_confidence'].get('_source_evidence', {}).get('worksheet')
        row['field_confidence']['_document_metadata'] = scope_cache[worksheet][1] if worksheet else metadata
        row['status'] = 'Needs Review'
    non_items.extend(dict(role=text_region_role(line), evidence=dict(text=line)) for line in lines
                     if text_region_role(line) != 'uncertain_region' or not mapped_rows)
    if candidates:
        warnings.append(f'{len(candidates)} uncertain source region(s) retained separately; no item drafts were created from them.')
    return {
        "rows": mapped_rows,
        "document_metadata": metadata,
        "review_candidates": candidates,
        "non_item_content": non_items,
        "model": model_status(),
        "parser": PARSER_VERSION,
        "raw_text": raw_text,
        "source_evidence": {"ocr_cells": document.get('ocr_cells') or [],
                            "pages": document.get('page_evidence') or [],
                            "manual_review_required": bool(document.get('manual_review_required'))},
        "warnings": list(dict.fromkeys(warnings)),
    }


def _document_metadata(document, pairs, warnings):
    """Retain explicit document values separately from row fields and raw OCR."""
    text = str(document.get('context_text', document.get('raw_text')) or '')
    # Native spreadsheets retain label and value as adjacent cells, not colon
    # text. Only explicit document-header rows qualify as document context.
    cell_context = []
    for table in document.get('tables') or []:
        for source_row in table.get('rows') or []:
            cells = [_clean(value) for value in source_row if _clean(value)]
            label = norm_document_label(cells[0]) if cells else ''
            if len(cells) == 2 and label in {'office', 'office end user', 'requesting office', 'fiscal year', 'fy', 'category', 'ppmp category'}:
                cell_context.append(('Office' if label == 'office end user' else cells[0])+': '+cells[1])
    text += '\n'+'\n'.join(cell_context)
    metadata = dict(source_reference=document.get('source_reference', ''), source_filename=document.get('filename', ''),
        document_type='PPMP', category='', fiscal_year='', office='', shared_fields={}, field_evidence={},
        extraction_method=document.get('extraction_method', ''), warnings=[])
    metadata['positioned_fields'] = document.get('shared_field_evidence',[])
    patterns = dict(category=r'(?:PPMP\s+)?Category\s*[:=]\s*((?:NON[- ]?)?DBM|LIB)\b',
                    fiscal_year=r'(?:Fiscal\s+Year|F\.?Y\.?)\s*[:=]?\s*(20\d{2})\b',
                    office=r'(?:Requesting\s+)?Office\s*[:=]\s*([^\n|]+)')
    for key, pattern in patterns.items():
        values = list(dict.fromkeys(match.strip() for match in re.findall(pattern, text, re.I)))
        if len(values) == 1:
            metadata[key] = values[0]
        elif values:
            metadata['warnings'].append(f'Conflicting document {key} values require review.')
        metadata['field_evidence'][key] = values
    if metadata['category']:
        metadata['category'] = re.sub(r'NON[- ]?DBM', 'NON-DBM', metadata['category'].upper())
    for role in SHARED_ROLES | {'project_type'}:
        values = list(dict.fromkeys(value for _, value, _, key, score in pairs if key == role and score >= .85))
        label = KEY_TO_LABEL[role]
        metadata['shared_fields'][label] = values[0] if len(values) == 1 else ''
        metadata['field_evidence'][label] = values
        if len(values) > 1:
            metadata['warnings'].append(f'Conflicting shared {label} values require review; none was selected as document-wide fact.')
    warnings.extend(metadata['warnings'])
    return metadata


def norm_document_label(value):
    return re.sub(r'[^a-z0-9]+', ' ', str(value).lower()).strip()

def validate_record(fields: Dict[str, Any]) -> List[str]:
    errors = []
    description = _clean(fields.get(PPMP_FIELDS["description"]["label"]))
    if not description:
        errors.append("General Description is required.")
    budget = _clean(fields.get(PPMP_FIELDS["budget"]["label"]))
    if budget and not re.search(r"\d", budget):
        errors.append("Estimated Budget must contain a numeric amount.")
    return errors
