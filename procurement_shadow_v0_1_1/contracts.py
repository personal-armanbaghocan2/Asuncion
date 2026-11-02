"""Bounded, source-grounded diagnostic contracts; no procurement status logic."""

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from enum import Enum
import hashlib
import json
import re


class ProcessingState(str, Enum):
    DISABLED = "DISABLED"
    EVALUATED = "EVALUATED"
    PARTIAL = "PARTIAL"
    NON_EVALUATED = "NON_EVALUATED"
    FAILED = "FAILED"


class Result(str, Enum):
    SATISFIED = "SATISFIED"
    VIOLATED = "VIOLATED"
    MISSING_EVIDENCE = "MISSING_EVIDENCE"
    CONTRADICTORY_EVIDENCE = "CONTRADICTORY_EVIDENCE"
    UNSUPPORTED = "UNSUPPORTED"
    UNCERTAIN_ALIGNMENT = "UNCERTAIN_ALIGNMENT"
    UNCERTAIN_SCOPE = "UNCERTAIN_SCOPE"


class InputError(ValueError):
    """Codes only: never retain raw input in errors/logs."""


@dataclass(frozen=True)
class Evidence:
    side: str
    path: tuple
    start: int
    end: int
    quote: str

    def as_dict(self):
        return {"side": self.side, "path": list(self.path),
                "start": self.start, "end": self.end, "quote": self.quote}


@dataclass(frozen=True)
class Clause:
    attribute: str
    operator: str
    values: tuple
    unit: str
    kind: str
    evidence: Evidence
    role: str
    unit_evidence: Evidence | None = None


@dataclass(frozen=True)
class Item:
    item_id: str
    identity: str
    description: Evidence
    clauses: tuple
    unknown: tuple
    quantity: Clause | None = None


@dataclass(frozen=True)
class Document:
    side: str
    items: tuple
    terms: tuple
    unknown: tuple
    split_permission: Evidence | None = None
    pack_permission: Evidence | None = None
    scope_issues: tuple = ()


@dataclass(frozen=True)
class ScopeIssue:
    evidence: Evidence
    clause: Clause | None
    reason: str


def decimal_value(value):
    if type(value) is not str or len(value) > 48:
        raise InputError("UNSUPPORTED_NUMBER")
    if not re.fullmatch(r"[+-]?(?:\d+(?:\.\d+)?|\.\d+)", value):
        raise InputError("UNSUPPORTED_NUMBER")
    try:
        number = Decimal(value)
    except InvalidOperation:
        raise InputError("UNSUPPORTED_NUMBER") from None
    if not number.is_finite() or number.copy_abs() > Decimal("1e24"):
        raise InputError("NUMBER_OUT_OF_BOUNDS")
    return number


def clone_json(value, *, max_chars=500000):
    """Reject custom objects, nonfinite numbers and excessive depth/size."""
    budget = [0, 0]

    def walk(obj, depth):
        budget[0] += 1
        if depth > 12 or budget[0] > 30000:
            raise InputError("INPUT_LIMIT")
        if type(obj) is str:
            budget[1] += len(obj)
            if budget[1] > max_chars:
                raise InputError("INPUT_LIMIT")
            return obj
        if type(obj) is int and obj.bit_length() > 128:
            raise InputError("NUMBER_OUT_OF_BOUNDS")
        if obj is None or type(obj) in (bool, int):
            return obj
        if type(obj) is float:
            import math
            if not math.isfinite(obj):
                raise InputError("NONFINITE_INPUT")
            return obj
        if type(obj) is list:
            return [walk(x, depth + 1) for x in obj]
        if type(obj) is dict and all(type(k) is str for k in obj):
            budget[1] += sum(len(k) for k in obj)
            if budget[1] > max_chars:
                raise InputError("INPUT_LIMIT")
            return {k: walk(v, depth + 1) for k, v in obj.items()}
        raise InputError("UNSUPPORTED_TYPE")

    result = walk(value, 0)
    encoded = json.dumps(result, ensure_ascii=False, allow_nan=False)
    if len(encoded) > max_chars:
        raise InputError("INPUT_LIMIT")
    return result


def input_hash(snapshot):
    payload = json.dumps(snapshot, ensure_ascii=False, sort_keys=True,
                         separators=(",", ":"), allow_nan=False).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def validate_evidence(snapshot, evidence):
    """Exact offsets into actual supplied strings; no invented quotes/paths."""
    if not isinstance(evidence, Evidence) or evidence.side not in ("pr", "po"):
        return False
    if type(evidence.start) is not int or type(evidence.end) is not int:
        return False
    try:
        value = snapshot[evidence.side]
        for key in evidence.path:
            if type(value) is list and type(key) is int:
                if key < 0:
                    return False
            elif type(value) is dict and type(key) is str:
                pass
            else:
                return False
            value = value[key]
        return (type(value) is str and 0 <= evidence.start < evidence.end <= len(value)
                and value[evidence.start:evidence.end] == evidence.quote)
    except (KeyError, IndexError, TypeError):
        return False
