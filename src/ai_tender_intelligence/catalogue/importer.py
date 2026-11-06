"""Explicit admin import: whole-source validation and one atomic activation."""

from dataclasses import dataclass

from sqlalchemy import select, insert, update, text

from ..persistence.database import Database
from ..persistence.schema import catalogues, codes, CATALOGUE_LOCK
from .source import CatalogueError, load_pinned

# Serialize only this administrative operation, including concurrent first import.
IMPORT_LOCK = CATALOGUE_LOCK


@dataclass(frozen=True)
class ImportResult:
    status: str
    version_id: str
    source_sha256: str
    verified_records: int
    imported_records: int
    stored_records: int
    rejected_records: int = 0


class CatalogueImporter:
    def __init__(self, db: Database):
        self.db = db

    @staticmethod
    def _insert_batch(session, values):
        session.execute(insert(codes), values)

    def run(self, source_path=None) -> ImportResult:
        # All local parsing completes before any database mutation/transaction.
        source = load_pinned(source_path)
        with self.db.transaction() as s:
            privileged = s.scalar(text("SELECT has_table_privilege(current_user, 'cpv_catalogue_versions', 'INSERT') AND has_table_privilege(current_user, 'cpv_catalogue_versions', 'UPDATE') AND has_table_privilege(current_user, 'cpv_codes', 'INSERT')"))
            if not privileged:
                raise CatalogueError('IMPORT_PERMISSION_REQUIRED')
            s.execute(text('SELECT pg_advisory_xact_lock(:key)'), {'key':IMPORT_LOCK})
            active = s.execute(select(catalogues).where(catalogues.c.status == 'ACTIVE').with_for_update()).mappings().first()
            if active and active['id'] != source.version_id:
                raise CatalogueError('ACTIVE_REPLACEMENT_REFUSED')
            existing = s.execute(select(catalogues).where(catalogues.c.id == source.version_id).with_for_update()).mappings().first()
            if existing:
                expected = (source.release, source.source_url, source.source_sha256)
                if (existing['official_release'],existing['source_url'],existing['sha256']) != expected:
                    raise CatalogueError('STORED_VERSION_MISMATCH')
                rows = s.execute(select(codes.c.code,codes.c.check_digit,codes.c.label_bg)
                    .where(codes.c.version_id == source.version_id).order_by(codes.c.code)).all()
                if rows != [(e.code,e.check_digit,e.description_bg) for e in source.entries]:
                    raise CatalogueError('STORED_VERSION_MISMATCH')
                if existing['status'] == 'ACTIVE':
                    return ImportResult('unchanged',source.version_id,source.source_sha256,len(rows),0,len(rows))
            else:
                s.execute(insert(catalogues).values(id=source.version_id,official_release=source.release,
                    source_url=source.source_url,sha256=source.source_sha256,status='STAGED'))
                for start in range(0,len(source.entries),500):
                    self._insert_batch(s,[{'version_id':source.version_id,'code':e.code,
                        'check_digit':e.check_digit,'label_bg':e.description_bg} for e in source.entries[start:start+500]])
            s.execute(update(catalogues).where(catalogues.c.id == source.version_id).values(status='ACTIVE'))
            return ImportResult('imported' if not existing else 'activated',source.version_id,source.source_sha256,
                len(source.entries),len(source.entries) if not existing else 0,len(source.entries))
