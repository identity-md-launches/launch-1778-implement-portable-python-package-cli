"""Strict portable serialization and content addressing."""

import hashlib
import json
import math
from pathlib import Path


def _pairs(items):
    result = {}
    for key, value in items:
        if key in result:
            raise ValueError("duplicate JSON key: " + key)
        result[key] = value
    return result


def loads(text):
    def invalid(value):
        raise ValueError("non-finite JSON: " + value)
    return json.loads(text, object_pairs_hook=_pairs, parse_constant=invalid)


def load(path):
    return loads(Path(path).read_text(encoding="utf-8"))


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False).encode("utf-8")


def digest(value):
    return hashlib.sha256(encoded(value)).hexdigest()


def file_hash(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def write_json(path, value):
    Path(path).write_bytes(encoded(value) + b"\n")


def write_jsonl(path, values):
    with Path(path).open("wb") as stream:
        for value in values:
            stream.write(encoded(value) + b"\n")


def number(value, label, positive=False):
    try:
        finite = type(value) in (int, float) and math.isfinite(value)
    except OverflowError:
        finite = False
    if not finite:
        raise ValueError(label + " must be a finite number")
    if positive and value <= 0:
        raise ValueError(label + " must be positive")
    return value


def text(value, label):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(label + " must be nonempty text")
    return value


def sample(items, limit):
    """Deterministic ordered samples, including both endpoints."""
    if len(items) <= limit:
        return list(items)
    if limit == 1:
        return [items[len(items) // 2]]
    return [items[round(i * (len(items) - 1) / (limit - 1))] for i in range(limit)]
