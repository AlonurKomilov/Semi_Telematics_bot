"""Shared input limits for every Chat transport."""
from unicodedata import normalize
from .models import ChatError


def text(value, limit: int, *, required=False, trim=True) -> str:
    if not isinstance(value, str):
        raise ChatError('invalid_text')
    value = normalize('NFC', value)
    if trim:
        value = value.strip()
    if '\x00' in value or len(value) > limit or (required and not value.strip()):
        raise ChatError('invalid_text')
    return value


def version(record: dict, expected: int) -> None:
    if type(expected) is not int or expected != record['version']:
        raise ChatError('version_conflict')


def positive(value, *, zero=False):
    if type(value) is not int or value < (0 if zero else 1) or value > 2**63 - 1:
        raise ChatError('invalid_cursor')
    return value


def page(limit, before=None):
    if type(limit) is not int or not 1 <= limit <= 100:
        raise ChatError('invalid_limit')
    if before is not None:
        positive(before)


def mention_ids(values):
    if not isinstance(values, (list, tuple)) or len(values) > 50:
        raise ChatError('invalid_mentions')
    for value in values:
        positive(value)
    return sorted(set(values))
