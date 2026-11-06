"""Strict versioned dataclass codec; no arbitrary class names or source documents."""
from dataclasses import asdict, fields, is_dataclass
from hashlib import sha256
import json
from types import UnionType
from typing import get_args, get_origin, get_type_hints
from ..discovery.fresh_models import FreshResult
from ..telegram.models import InteractionError

FORMAT = 'private-search-v1'
MAX_BYTES = 8_000_000  # reviewable engineering bound, not measured capacity
ERROR = 'Запазените резултати не са достъпни. Извършете ново търсене.'


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def digest(value):
    return sha256(canonical(value).encode()).hexdigest()


def _decode(kind, value):
    origin, args = get_origin(kind), get_args(kind)
    if origin is UnionType:
        if value is None and type(None) in args:
            return None
        return _decode(next(t for t in args if t is not type(None)), value)
    if origin is tuple:
        if not isinstance(value, (list, tuple)):
            raise ValueError()
        if len(args) == 2 and args[1] is Ellipsis:
            return tuple(_decode(args[0], v) for v in value)
        if len(value) != len(args):
            raise ValueError()
        return tuple(_decode(t, v) for t, v in zip(args, value))
    if is_dataclass(kind):
        if not isinstance(value, dict) or set(value) != {f.name for f in fields(kind)}:
            raise ValueError()
        hints = get_type_hints(kind)
        return kind(**{k: _decode(hints[k], v) for k, v in value.items()})
    if type(value) is not kind:
        raise ValueError()
    return value


def validate(result):
    opportunities = result.matches.opportunities
    if (result.outcome not in {'COMPLETE_WITHIN_BOUNDS', 'PARTIAL'} or len(opportunities) > 15
            or len({o.procedure_id for o in opportunities}) != len(opportunities)
            or result.current_openness_verified or result.production_telegram_ready
            or result.matches.availability_verified or result.matches.production_telegram_ready
            or any(o.availability_verified or not o.lots or o.relation not in {'DIRECT','RELATED','BROADER'} for o in opportunities)
            or any(a.current_openness != 'UNVERIFIED' for a in result.assessments)):
        raise ValueError()
    return result


def encode(result):
    try:
        value = asdict(validate(result))
        if len(canonical(value).encode()) > MAX_BYTES:
            raise ValueError()
        # Round-trip validates nested types before any persistence.
        decode(value)
        return value
    except (ValueError, TypeError, KeyError, AttributeError):
        raise InteractionError(ERROR) from None


def decode(value):
    try:
        if len(canonical(value).encode()) > MAX_BYTES:
            raise ValueError()
        return validate(_decode(FreshResult, value))
    except (ValueError, TypeError, KeyError, AttributeError):
        raise InteractionError(ERROR) from None
