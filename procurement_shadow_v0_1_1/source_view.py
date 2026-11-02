"""Deliberately finite source grammar. Unknown prose stays visible/unsupported.

Structured input: {pr: {items: [{description, quantity?, unit?}], terms?: []},
                   po: ...}. A side can instead be exact text or {text: text}.
No target labels, gold correspondences or converted targets are accepted.
"""

from dataclasses import replace
import re
from .contracts import Clause, Document, Evidence, InputError, Item, ScopeIssue, decimal_value

NUM = r"[+-]?(?:\d+(?:\.\d+)?|\.\d+)"
OPS = {
    "at least": "GE", "minimum": "GE", ">=": "GE",
    "greater than": "GT", ">": "GT",
    "at most": "LE", "no more than": "LE", "maximum": "LE", "<=": "LE",
    "less than": "LT", "<": "LT", "exactly": "EQ", "exact": "EQ", "=": "EQ",
}
OP_PATTERN = "|".join(re.escape(x) for x in sorted(OPS, key=len, reverse=True))
NUMERIC = re.compile(
    rf"(?P<attr>[A-Za-z][A-Za-z /_-]{{0,100}}?)\s*:?\s+"
    rf"(?:(?P<op>{OP_PATTERN})\s*)?(?P<n>{NUM})\s*(?P<u>[A-Za-z]+)", re.I)
RANGE = re.compile(
    rf"(?P<attr>[A-Za-z][A-Za-z /_-]{{0,100}}?)\s+between\s+"
    rf"(?P<lo>{NUM})\s+and\s+(?P<hi>{NUM})\s*(?P<u>[A-Za-z]+)\s+inclusive", re.I)
ATTRIBUTE_ALIASES = {
    "installed memory": "memory", "memory capacity": "memory", "system memory": "memory",
    "warranty": "supplier warranty", "service warranty": "supplier warranty",
    "body": "body material", "enclosure": "enclosure material",
    "exact quantity": "quantity", "offered quantity": "quantity",
    "requested quantity": "quantity", "order quantity": "quantity",
}


def canonical_attribute(value):
    text = re.sub(r"\s+", " ", value.lower()).strip(" :")
    text = re.sub(r"\s+(?:must (?:be|have|last)|required|offered|is)$", "", text)
    return ATTRIBUTE_ALIASES.get(text, text)


def term_expression(text):
    """Explicit source locator, never a first-item or attribute-based guess."""
    match = re.fullmatch(r'For (item|line) "([^"\n]+)",\s*(?:the\s+)?(.+)', text, re.I)
    if match:
        return (match[1].lower(), match[2]), match[3]
    match = re.fullmatch(r'For the main requested item,\s*(?:the\s+)?(.+)', text, re.I)
    if match:
        return ('main', ''), match[1]
    # Unrecognized scope locators must not become generic attribute names.
    if re.match(r'For\b', text, re.I):
        return ('unknown', ''), text
    return None, text


def term_owners(term, items):
    locator, _ = term_expression(term.quote.strip().rstrip('.').strip())
    if locator and locator[0] == 'item':
        identity = re.sub(r'\s+', ' ', locator[1]).strip().casefold()
        return [i for i, item in enumerate(items) if item.identity == identity]
    if locator and locator[0] == 'line':
        return [i for i, item in enumerate(items) if item.item_id == locator[1]]
    if locator and locator[0] == 'unknown':
        return []
    # A generic or "main" term is unambiguous only in a one-item document.
    return [0] if len(items) == 1 else []


def parse_clause(evidence, role):
    """Return only a fully consumed explicit supported expression, or None."""
    text = evidence.quote.strip().rstrip(".").strip()
    locator, text = term_expression(text)
    if locator and locator[0] == 'unknown':
        return None
    # Alternative/unselected plans and amendment precedence are not commitments.
    if re.search(r"\b(?:alternative plan|supersedes|revision|not selected|unless|if)\b", text, re.I):
        return None
    match = RANGE.fullmatch(text)
    if match:
        lo, hi = match.group("lo", "hi")
        if decimal_value(lo) > decimal_value(hi):
            return None
        return Clause(canonical_attribute(match['attr']), "INTERVAL", (lo, hi),
                      match['u'], "numeric", evidence, role)
    # Compact operators need no intervening whitespace: memory >=16 GB.
    compact = re.sub(r"(>=|<=|>|<|=)\s*", r"\1 ", text)
    match = NUMERIC.fullmatch(compact)
    if match:
        attr = canonical_attribute(match['attr'])
        # Numeric-looking identifiers are never promoted to a measurement.
        if re.search(r"\b(?:model|code|reference|serial|asset|item id|price|amount)\b", attr):
            return None
        decimal_value(match['n'])
        return Clause(attr, OPS.get((match['op'] or "").lower(), "EQ"),
                      (match['n'],), match['u'], "numeric", evidence, role)
    match = re.fullmatch(
        r"(?P<attr>[A-Za-z][A-Za-z _/-]{0,80}?)\s+may be\s+(?P<values>[A-Za-z -]+(?:\s+OR\s+[A-Za-z -]+)+)",
        text, re.I)
    if match and role == "pr":
        values = tuple(v.strip().lower() for v in re.split(r"\s+OR\s+", match['values'], flags=re.I))
        return Clause(canonical_attribute(match['attr']), "ALLOWED_SET", values,
                      "", "categorical", evidence, role)
    match = re.fullmatch(
        r"(?P<attr>[A-Za-z][A-Za-z _/-]{0,80}?)\s*(?:required\s*:|must be\s+|:\s*)(?P<value>[A-Za-z0-9][A-Za-z0-9 -]{0,80})",
        text, re.I)
    if match:
        attr, value = canonical_attribute(match['attr']), match['value'].strip().lower()
        # Negation/composites are not parsed into a positive atomic claim.
        if re.search(r"\b(?:not|except|with|and|or|rigid|excluded|prohibited)\b", value):
            return None
        return Clause(attr, "EQ", (value,), "", "categorical", evidence, role)
    return None


def segments(evidence):
    """Offsets stay in the original field; decimal points are not delimiters."""
    pattern = r"[^;\n]+"
    for match in re.finditer(pattern, evidence.quote):
        raw = match.group(); left = len(raw) - len(raw.lstrip())
        right = len(raw.rstrip())
        if right > left:
            start = evidence.start + match.start() + left
            yield Evidence(evidence.side, evidence.path, start,
                           evidence.start + match.start() + right, raw[left:right])


def _parse_item(item_id, desc, qty=None, unit=None):
    parts = list(segments(desc))
    if not parts or len(desc.quote) > 8000:
        raise InputError("EMPTY_OR_OVERSIZED_DESCRIPTION")
    # The first semicolon-delimited segment is the supplied item identity.
    identity = re.sub(r"\s+", " ", parts[0].quote).strip().casefold()
    clauses, unknown = [], []
    if re.search(r"(?:at least|at most|minimum|maximum|must be|>=|<=|between|\d\s*(?:GB|Gb|W|cm|months)\b)", parts[0].quote, re.I):
        unknown.append(parts[0])  # Do not hide requirements inside identity prose.
    for part in parts[1:]:
        try:
            # A locator inside another item's description is not authority to
            # transplant that clause to its enclosing line.
            clause = None if term_expression(part.quote)[0] else parse_clause(part, desc.side)
        except InputError:
            clause = None
        if clause:
            clauses.append(clause)
        else:
            unknown.append(part)
    quantity = None
    if qty is not None:
        try:
            decimal_value(qty.quote)
            if unit is None or not unit.quote.strip():
                unknown.append(qty)
            else:
                quantity = Clause("quantity", "EQ", (qty.quote,), unit.quote,
                                  "quantity", qty, desc.side, unit)
        except InputError:
            unknown.append(qty)
    return Item(item_id, identity, desc, tuple(clauses), tuple(unknown), quantity)


def _field(side, path, value):
    if type(value) is not str or not value.strip():
        raise InputError("MALFORMED_SOURCE_FIELD")
    return Evidence(side, path, 0, len(value), value)


def _text_items(side, text, path):
    if not text.strip() or "NO ITEM INFORMATION WAS PROVIDED." in text:
        raise InputError("MISSING_ITEM_INFORMATION")
    items, terms, unknown = [], [], []
    for line in re.finditer(r"[^\r\n]+", text):
        raw = line.group(); stripped = raw.strip()
        if stripped.upper() in ("PURCHASE REQUEST", "PURCHASE ORDER"):
            continue
        if stripped.startswith("TERM | "):
            start = line.start() + raw.index("TERM | ") + 7
            terms.append(Evidence(side, path, start, line.end(), text[start:line.end()]))
            continue
        match = re.fullmatch(
            r"ITEM(?:\s+\d+)?\. DESCRIPTION AND SPECIFICATION: (?P<d>.*?)\. QUANTITY: (?P<q>.*?)\. UNIT: (?P<u>.*?)\.", stripped)
        if not match:
            match = re.fullmatch(r"ITEM \| (?P<d>.*?) \| QUANTITY (?P<q>.*?) \| UNIT (?P<u>.*)", stripped)
        if match:
            offset = line.start() + raw.index(stripped)
            ev = {key: Evidence(side, path, offset + match.start(key), offset + match.end(key),
                                match[key]) for key in ("d", "q", "u") if match[key]}
            qty = ev.get('q') if match['q'] != 'not provided' else None
            unit = ev.get('u') if match['u'] != 'not provided' else None
            items.append(_parse_item(f"{side}{len(items)+1}", ev['d'], qty, unit))
        elif len(text.splitlines()) == 1:
            ev = Evidence(side, path, line.start(), line.end(), raw)
            items.append(_parse_item(f"{side}1", ev))
        else:
            unknown.append(Evidence(side, path, line.start(), line.end(), raw))
    if not items:
        raise InputError("NO_SUPPORTED_ITEM_LAYOUT")
    return items, terms, unknown


def parse_document(snapshot, side):
    value = snapshot[side]
    items, terms, unknown = [], [], []
    if type(value) is str:
        items, terms, unknown = _text_items(side, value, ())
    elif type(value) is dict and set(value) == {'text'} and type(value['text']) is str:
        items, terms, unknown = _text_items(side, value['text'], ('text',))
    elif type(value) is dict and set(value) <= {'items', 'terms'} and 'items' in value:
        if type(value['items']) is not list or not value['items'] or len(value['items']) > 200:
            raise InputError("MISSING_OR_OVERSIZED_ITEMS")
        ids = set()
        for index, row in enumerate(value['items']):
            if type(row) is not dict or not set(row) <= {'line_id', 'description', 'quantity', 'unit'}:
                raise InputError("UNSUPPORTED_ITEM_STRUCTURE")
            item_id = row.get('line_id', f'{side}{index+1}')
            if type(item_id) is not str or not item_id or item_id in ids:
                raise InputError("INVALID_OR_DUPLICATE_LINE_ID")
            ids.add(item_id)
            desc = _field(side, ('items', index, 'description'), row.get('description'))
            qty = _field(side, ('items', index, 'quantity'), row['quantity']) if row.get('quantity') not in (None, '') else None
            unit = _field(side, ('items', index, 'unit'), row['unit']) if row.get('unit') not in (None, '') else None
            items.append(_parse_item(item_id, desc, qty, unit))
        raw_terms = value.get('terms', [])
        if type(raw_terms) is not list or len(raw_terms) > 100:
            raise InputError("INVALID_TERMS")
        terms = [_field(side, ('terms', i), text) for i, text in enumerate(raw_terms)]
    else:
        raise InputError("UNSUPPORTED_DOCUMENT_STRUCTURE")
    if len(items) > 200:
        raise InputError("ITEM_LIMIT")
    split_permission = None
    pack_permission = None
    scope_issues = []
    for term in terms:
        if re.fullmatch(r"Split deliveries (?:are )?(?:explicitly )?allowed\.?", term.quote, re.I):
            split_permission = term
            continue
        if re.fullmatch(r"Packs (?:are )?(?:explicitly )?allowed\.?", term.quote, re.I):
            pack_permission = term
            continue
        try:
            clause = parse_clause(term, side)
        except InputError:
            clause = None
        matches = term_owners(term, items)
        if clause and len(matches) == 1:
            i = matches[0]
            items[i] = replace(items[i], clauses=items[i].clauses + (clause,))
        else:
            unknown.append(term)
            if len(matches) != 1 and (clause or term_expression(term.quote)[0]):
                scope_issues.append(ScopeIssue(term, clause, 'DOCUMENT_TERM_ITEM_SCOPE_NOT_JUSTIFIED'))
    return Document(side, tuple(items), tuple(terms), tuple(unknown), split_permission, pack_permission, tuple(scope_issues))


def unknown_attribute(evidence):
    """Finite subject extraction, not a semantic negation/exclusion oracle.

    A compound subject (e.g. battery warranty) stays distinct from warranty.
    Unsupported prose is still emitted even when no subject is recognized.
    """
    _, text = term_expression(evidence.quote.strip().rstrip('.').strip())
    text = re.sub(r'^the\s+', '', text, flags=re.I)
    text = re.sub(r'^(?:no|without)\s+', '', text, flags=re.I)
    match = re.match(
        r'([A-Za-z][A-Za-z /_-]*?)(?:\s*:|\s+(?:must|at least|at most|exactly|between|'
        r'is|are|does|do|not|excluded|exclusion|except|unless|if|required|offered|'
        r'no|only|limited|unavailable|without)\b|\s+[+-]?(?:\d|I\d))', text, re.I)
    return canonical_attribute(match[1]) if match else None


def _could_affect(evidence, attribute):
    subject = unknown_attribute(evidence)
    if subject:
        return subject == attribute
    _, text = term_expression(evidence.quote)
    # Less regular qualifications are uncertainty, never an invented negative
    # value. A recognized distinct subject above (battery warranty, support,
    # alternative plan) is not silently collapsed into this attribute.
    phrases = [attribute] + [a for a, canonical in ATTRIBUTE_ALIASES.items() if canonical == attribute]
    if any(re.search(r'(?<!\w)' + re.escape(a) + r'(?!\w)', text, re.I) for a in phrases):
        return True
    return bool(re.fullmatch(r'(?:excluded|not included|not covered|subject to .+|unless .+)[.!]?', text.strip(), re.I))


def _additional_positive_requirement(evidence):
    """A further PR obligation cannot erase a proven inline shortfall.

    This does not bind the term or prove its scope. Exceptions/amendments and
    unknown compound prose still require review. Only an atomic obligation,
    optionally followed by simple explicit prohibitions, is recognized here.
    """
    text = evidence.quote
    if re.search(r'\b(?:except|unless|if|supersedes|revision|excluded|waived|optional|instead)\b|not required', text, re.I):
        return False
    parts = re.split(r'(?<=[A-Za-z])\.\s+', text)
    first = parts[0].strip()
    start = evidence.start + len(parts[0]) - len(parts[0].lstrip())
    span = Evidence(evidence.side, evidence.path, start, start + len(first), first)
    try:
        clause = parse_clause(span, 'pr')
    except InputError:
        return False
    return bool(clause and all(re.fullmatch(r'[A-Za-z][A-Za-z -]* (?:is|are) not acceptable\.?', p.strip(), re.I)
                               for p in parts[1:]))


def unresolved_evidence(document, item, attribute, *, proven_violation=False):
    """Relevant unknown evidence cannot silently certify an atomic claim.

    A document term with unknown scope may affect any item mentioning its
    attribute. A uniquely located term affects only that source item.
    Unrelated attributes and bare benign negations are not contradictions.
    """
    spans = []
    for ev in item.unknown:
        if _could_affect(ev, attribute):
            spans.append(ev)
    for ev in document.unknown:
        owners = term_owners(ev, document.items)
        if len(owners) == 1 and document.items[owners[0]].item_id != item.item_id:
            continue
        if (document.side == 'pr' and proven_violation and len(owners) != 1
                and _additional_positive_requirement(ev)):
            # Keep the scope warning in the report. Do not suppress an already
            # grounded violation of a separate inline requirement merely
            # because an additional positive PR obligation has unknown scope.
            continue
        issue = next((s for s in document.scope_issues if s.evidence == ev), None)
        affects = issue.clause.attribute == attribute if issue and issue.clause else _could_affect(ev, attribute)
        if affects:
            spans.append(ev)
    return tuple(dict.fromkeys(spans))
