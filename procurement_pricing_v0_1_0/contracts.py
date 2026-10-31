"""Strict bounded JSON and decimal contracts; no locale or currency inference."""

from decimal import Decimal
import json
import re


class InputError(ValueError):
    pass


def clone_snapshot(value):
    count = [0, 0]

    def clone(obj, depth=0):
        count[0] += 1
        if depth > 12 or count[0] > 25000:
            raise InputError("INPUT_LIMIT")
        if type(obj) is str:
            count[1] += len(obj)
            if len(obj) > 10000 or count[1] > 500000:
                raise InputError("INPUT_LIMIT")
            return obj
        if obj is None or type(obj) is bool:
            return obj
        if type(obj) is int:
            if obj.bit_length() > 128:
                raise InputError("INPUT_LIMIT")
            return obj
        if type(obj) is list:
            return [clone(x, depth + 1) for x in obj]
        if type(obj) is dict:
            if any(type(k) is not str or len(k) > 160 for k in obj):
                raise InputError("INVALID_KEY")
            count[1] += sum(map(len, obj))
            return {k: clone(v, depth + 1) for k, v in obj.items()}
        # Floats (even finite), Decimal objects and custom objects are deliberately
        # rejected at the external JSON boundary. Amounts must be decimal strings.
        raise InputError("UNSUPPORTED_TYPE_USE_DECIMAL_STRINGS")

    result = clone(value)
    if type(result) is not dict:
        raise InputError("SNAPSHOT_MUST_BE_OBJECT")
    if len(json.dumps(result, ensure_ascii=False)) > 600000:
        raise InputError("INPUT_LIMIT")
    return result


def read_path(snapshot, path):
    current = snapshot
    for key in path:
        if type(current) is dict and type(key) is str and key in current:
            current = current[key]
        elif type(current) is list and type(key) is int and 0 <= key < len(current):
            current = current[key]
        else:
            return False, None
    return True, current


def evidence(snapshot, path):
    present, value = read_path(snapshot, path)
    pointer = "/" + "/".join(str(k).replace("~", "~0").replace("/", "~1") for k in path)
    # Container evidence establishes shape/existence without copying whole records.
    if type(value) in (dict, list):
        return {"path": list(path), "pointer": pointer, "present": present,
                "container_type": type(value).__name__, "length": len(value)}
    return {"path": list(path), "pointer": pointer, "present": present, "value": value}


def number(value):
    # Avoid ambiguous grouping, exponent notation, signs, floats and implicit PHP.
    if type(value) is not str or not re.fullmatch(r"(?:0|[1-9][0-9]{0,17})(?:\.[0-9]{1,9})?", value):
        raise InputError("INVALID_NONNEGATIVE_DECIMAL_STRING")
    return Decimal(value)


def text(value):
    return type(value) is str and bool(value.strip())
