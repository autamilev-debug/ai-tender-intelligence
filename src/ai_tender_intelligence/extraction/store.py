"""Public derivatives; every lookup/publication uses original acquisition authorization."""
from datetime import datetime,timezone
from hashlib import sha256
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from .schema import derivatives
from .models import canonical,ExtractionError
from ..acquisition.schema import occurrences,items
from ..acquisition.store import EvidenceStore


class ExtractionStore:
    def __init__(self,evidence:EvidenceStore):
        self.evidence=evidence
        self.db=evidence.db

    def _source(self,s,actor,company,collection,occurrence):
        access=self.evidence._access(s,actor,company,collection)
        from uuid import UUID
        row=s.execute(select(occurrences).join(items,items.c.occurrence_id==occurrences.c.id).where(
            items.c.collection_id==access['id'],occurrences.c.id==UUID(str(occurrence)))).mappings().first()
        if row is None or not row['validation'].get('valid') or not row['blob_sha256']:
            raise ExtractionError('EXTRACTION_SOURCE_UNAVAILABLE')
        return row

    def load(self,actor,company,collection,occurrence,fingerprint):
        with self.db.transaction() as s:
            source=self._source(s,actor,company,collection,occurrence)
            row=s.execute(select(derivatives).where(derivatives.c.source_sha256==source['blob_sha256'],
                                                  derivatives.c.extractor_sha256==fingerprint)).mappings().first()
            if row is None:return None
            if sha256(canonical(row['result'])).hexdigest()!=row['result_sha256']:
                raise ExtractionError('EXTRACTION_DERIVATIVE_INTEGRITY_FAILED')
            return dict(row)

    def publish(self,actor,company,collection,occurrence,fingerprint,identity,result):
        if sha256(canonical(identity)).hexdigest()!=fingerprint:
            raise ExtractionError('EXTRACTION_IDENTITY_MISMATCH')
        if (result.get('status') not in {'EXTRACTED','PARTIAL','UNREADABLE','UNSUPPORTED','FAILED'}
                or not isinstance(result.get('blocks'),list) or not isinstance(result.get('units'),list)
                or any(not isinstance(b,dict) or not isinstance(b.get('locator'),dict) or not b['locator'] for b in result['blocks'])):
            raise ExtractionError('INVALID_EXTRACTION_RESULT_OR_LOCATOR')
        if result['status']=='FAILED':return  # transient process failures are not a permanently cached conclusion
        h=sha256(canonical(result)).hexdigest()
        with self.db.transaction() as s:
            source=self._source(s,actor,company,collection,occurrence)
            if identity['format']!=source['validation']['kind']:
                raise ExtractionError('EXTRACTION_IDENTITY_MISMATCH')
            values=dict(source_sha256=source['blob_sha256'],extractor_sha256=fingerprint,
                        format=source['validation']['kind'],identity=identity,result=result,result_sha256=h,
                        created_at=datetime.now(timezone.utc))
            s.execute(insert(derivatives).values(**values).on_conflict_do_nothing())
            old=s.execute(select(derivatives).where(derivatives.c.source_sha256==source['blob_sha256'],
                                                  derivatives.c.extractor_sha256==fingerprint)).mappings().one()
            if old['result_sha256']!=h or old['result']!=result or old['identity']!=identity:
                raise ExtractionError('CONFLICTING_EXTRACTION_DERIVATIVE')
