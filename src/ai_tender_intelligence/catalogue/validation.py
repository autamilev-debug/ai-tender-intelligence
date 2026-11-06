"""Read-only catalogue validity. Does not determine activity relevance."""

from dataclasses import dataclass
from types import MappingProxyType
from time import perf_counter
from sqlalchemy import select

from ..persistence.database import Database
from ..persistence.schema import catalogues, codes
from .source import (CPVEntry, CatalogueError, IDENTIFIER, NORMALIZED, VERSION_ID,
                     PINNED_SHA256, RELEASE, SOURCE_URL, entries_digest, PINNED_ENTRIES_SHA256)

DERIVATION = 'TED_PREFIX_MEMBERSHIP_V1'


def valid_format(value) -> bool:
    return isinstance(value,str) and bool(NORMALIZED.fullmatch(value) or IDENTIFIER.fullmatch(value))


def _parts(value):
    if not valid_format(value):
        raise CatalogueError('INVALID_CODE_FORMAT')
    return (value[:8],value[-1] if len(value)==10 else None)


def _validate_values(values):
    if not isinstance(values,(tuple,list)) or not 1 <= len(values) <= 20:
        raise CatalogueError('INVALID_CODE_SET')
    normalized = [_parts(v)[0] for v in values]
    if len(set(normalized)) != len(normalized):
        raise CatalogueError('INVALID_CODE_SET')


def ancestor_candidates(code):
    """Documented prefix/zero-padding only, never manufactured catalogue rows."""
    return tuple(dict.fromkeys(code[:n]+'0'*(8-n) for n in range(2,8) if code[:n]+'0'*(8-n) != code))


def level(code):
    precision = max(2,len(code.rstrip('0')))
    return {2:'division',3:'group',4:'class',5:'category'}.get(precision,f'detail_{precision}')


@dataclass(frozen=True)
class ValidatedCode:
    version_id: str
    entry: CPVEntry


@dataclass(frozen=True)
class Hierarchy:
    version_id: str
    entry: CPVEntry
    level: str
    ancestors: tuple[CPVEntry, ...]
    parent: CPVEntry | None
    children: tuple[CPVEntry, ...]
    derivation: str = DERIVATION


class CatalogueReader:
    def __init__(self, db: Database):
        self.db = db

    def _snapshot(self, timings=None):
        # One SELECT gives a coherent version+entries snapshot without needing
        # UPDATE privileges for a row lock. Runtime connections stay read-only.
        started = perf_counter()
        with self.db.transaction() as s:
            rows = s.execute(select(catalogues.c.id,catalogues.c.sha256,catalogues.c.official_release,
                catalogues.c.source_url,codes.c.code,codes.c.check_digit,codes.c.label_bg)
                .join(codes,catalogues.c.id == codes.c.version_id).where(catalogues.c.status == 'ACTIVE')
                .order_by(codes.c.code)).all()
        if not rows:
            raise CatalogueError('CATALOGUE_UNAVAILABLE')
        if rows[0][:4] != (VERSION_ID,PINNED_SHA256,RELEASE,SOURCE_URL):
            raise CatalogueError('STORED_VERSION_MISMATCH')
        entries = {r.code:CPVEntry(r.code,r.check_digit,r.label_bg) for r in rows}
        digest_started = perf_counter()
        if entries_digest(entries.values()) != PINNED_ENTRIES_SHA256:
            raise CatalogueError('STORED_VERSION_MISMATCH')
        if timings is not None:
            timings.update(total_ms=(perf_counter()-started)*1000,
                           digest_ms=(perf_counter()-digest_started)*1000, entries=len(entries))
        return VERSION_ID,entries

    def snapshot(self, timings=None):
        """One integrity-verified immutable read for a bounded classification run."""
        version, entries = self._snapshot(timings)
        return CatalogueSnapshot(version, MappingProxyType(entries))

    @staticmethod
    def _read(entries,value):
        code,digit = _parts(value)
        entry = entries.get(code)
        if entry is None:
            raise CatalogueError('CODE_NOT_FOUND')
        if digit is not None and digit != entry.check_digit:
            raise CatalogueError('CHECK_DIGIT_MISMATCH')
        return entry

    def read_code(self, value) -> ValidatedCode:
        _parts(value)
        version,entries = self._snapshot()
        return ValidatedCode(version,self._read(entries,value))

    def exists(self,value) -> bool:
        if not valid_format(value):
            return False
        try:
            self.read_code(value)
            return True
        except CatalogueError as exc:
            if exc.reason in {'CODE_NOT_FOUND','CHECK_DIGIT_MISMATCH'}:
                return False
            raise  # Unavailable/corrupt catalogue is not verified absence.

    def description_bg(self,value):
        return self.read_code(value).entry.description_bg

    def validate_set(self,values) -> tuple[ValidatedCode, ...]:
        _validate_values(values)  # Reject malformed input before database I/O.
        return self.snapshot().validate_set(values)

    def hierarchy(self,value) -> Hierarchy:
        _parts(value)
        version,entries = self._snapshot()
        entry = self._read(entries,value)
        def ancestors(code):
            return tuple(entries[c] for c in ancestor_candidates(code) if c in entries)
        chain = ancestors(entry.code)
        children = []
        for e in entries.values():
            if e.code != entry.code and e.code.startswith(entry.code[:max(2,len(entry.code.rstrip('0')))]):
                candidate_chain = ancestors(e.code)
                if candidate_chain and candidate_chain[-1].code == entry.code:
                    children.append(e)
        return Hierarchy(version,entry,level(entry.code),chain,chain[-1] if chain else None,tuple(children))


@dataclass(frozen=True)
class CatalogueSnapshot:
    version_id: str
    entries: object

    def validate_set(self, values):
        _validate_values(values)
        return tuple(ValidatedCode(self.version_id,CatalogueReader._read(self.entries,v)) for v in values)
