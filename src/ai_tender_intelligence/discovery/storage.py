"""Shared public metadata store. No company/profile writes and no schema startup."""
from datetime import datetime, timezone
import json
from uuid import uuid4

from sqlalchemy import (MetaData, Table, Column, Text, DateTime, ForeignKey, UniqueConstraint,
                        CheckConstraint, LargeBinary, select, insert, update, text)
from sqlalchemy.dialects.postgresql import JSONB, UUID, insert as pg_insert
from .models import DiscoveryError, content_hash, validate_notice

metadata = MetaData()
procedures = Table('discovery_procedures', metadata,
    Column('id', UUID(as_uuid=True), primary_key=True), Column('created_at', DateTime(timezone=True), nullable=False))
identities = Table('discovery_identities', metadata,
    Column('identity', Text, primary_key=True), Column('procedure_id', UUID(as_uuid=True), ForeignKey(procedures.c.id), nullable=False))
evidence = Table('discovery_evidence', metadata,
    Column('sha256', Text, primary_key=True), Column('raw_payload', LargeBinary, nullable=False),
    CheckConstraint("sha256 ~ '^[0-9a-f]{64}$'"))
notices = Table('discovery_notices', metadata,
    Column('id', UUID(as_uuid=True), primary_key=True),
    Column('procedure_id', UUID(as_uuid=True), ForeignKey(procedures.c.id), nullable=False),
    Column('source', Text, nullable=False), Column('original_id', Text, nullable=False),
    Column('version_key', Text, nullable=False), Column('sha256', Text, ForeignKey(evidence.c.sha256), nullable=False),
    Column('payload', JSONB, nullable=False),
    Column('first_seen', DateTime(timezone=True), nullable=False), Column('last_seen', DateTime(timezone=True), nullable=False),
    UniqueConstraint('source', 'original_id', 'version_key', 'sha256'),
    CheckConstraint("source IN ('TED','EOP')"), CheckConstraint("sha256 ~ '^[0-9a-f]{64}$'"))
lots = Table('discovery_lots', metadata,
    Column('notice_id', UUID(as_uuid=True), ForeignKey(notices.c.id), primary_key=True),
    Column('source_lot_id', Text, primary_key=True), Column('payload', JSONB, nullable=False))


class NoticeStore:
    def __init__(self, db):
        self.db = db

    def ingest(self, notice, raw: bytes):
        return self.ingest_many([(notice, raw)])[0]

    def ingest_many(self, records):
        records = list(records)
        if not records or len(records) > 1000:
            raise DiscoveryError('Invalid ingestion batch.')
        for notice, raw in records:
            validate_notice(notice)
            if not isinstance(raw, bytes) or not raw or len(raw) > 8_000_000:
                raise DiscoveryError('Invalid source evidence.')
        results = []
        with self.db.transaction() as session:
            # Serialize exact identity association only; HTTP never holds DB locks.
            session.execute(text("SELECT pg_advisory_xact_lock(512012)"))
            for notice, raw in records:
                ids = set(notice.identities) | {notice.source.lower() + ':notice:' + notice.original_id}
                if len(ids) > 100 or any(len(i) > 300 for i in ids):
                    raise DiscoveryError('Invalid source aliases.')
                known = set(session.scalars(select(identities.c.procedure_id).where(identities.c.identity.in_(ids))))
                if len(known) > 1:
                    # No silent merge of conflicting established procedures.
                    raise DiscoveryError('Conflicting official procedure identities; batch rolled back.')
                pid = next(iter(known), None)
                if pid is None:
                    pid = uuid4()
                    session.execute(insert(procedures).values(id=pid, created_at=datetime.now(timezone.utc)))
                for identity in sorted(ids):
                    session.execute(pg_insert(identities).values(identity=identity, procedure_id=pid).on_conflict_do_nothing())
                digest, version = content_hash(raw), notice.version or 'UNKNOWN'
                session.execute(pg_insert(evidence).values(sha256=digest, raw_payload=raw).on_conflict_do_nothing())
                existing = session.execute(select(notices).where(notices.c.source == notice.source,
                    notices.c.original_id == notice.original_id, notices.c.version_key == version,
                    notices.c.sha256 == digest)).mappings().first()
                seen = datetime.fromisoformat(notice.retrieved_at)
                if existing:
                    # Repeated same raw version cannot silently change normalized facts.
                    old = dict(existing['payload']); new = notice.payload()
                    old.pop('retrieved_at', None); new.pop('retrieved_at', None)
                    if json.dumps(old, sort_keys=True) != json.dumps(new, sort_keys=True):
                        raise DiscoveryError('Same source evidence has inconsistent normalization.')
                    session.execute(update(notices).where(notices.c.id == existing['id']).values(last_seen=max(existing['last_seen'], seen)))
                    results.append((existing['id'], False))
                    continue
                nid = uuid4()
                session.execute(insert(notices).values(id=nid, procedure_id=pid, source=notice.source,
                    original_id=notice.original_id, version_key=version, sha256=digest, payload=notice.payload(),
                    first_seen=seen, last_seen=seen))
                session.execute(insert(lots), [{'notice_id': nid, 'source_lot_id': lot.id, 'payload': notice.payload()['lots'][i]}
                                               for i, lot in enumerate(notice.lots)])
                results.append((nid, True))
        return tuple(results)

    def versions(self, source, original_id):
        with self.db.transaction() as session:
            return tuple(session.execute(select(notices.c.payload).where(notices.c.source == source,
                notices.c.original_id == original_id).order_by(notices.c.first_seen, notices.c.id)).scalars())
