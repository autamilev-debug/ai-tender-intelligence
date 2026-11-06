from ..persistence.identity import freeze_users
"""Short authorization transactions. Public originals never contain private profiles."""
from dataclasses import asdict
from datetime import datetime,timezone
from hashlib import sha256,md5
import json
from uuid import UUID,uuid4
from sqlalchemy import select,insert,update,text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from .schema import collections,occurrences,items
from .models import AcquisitionError
from ..discovery.storage import evidence
from ..persistence.profiles import PostgresProfiles


def digest(value):return sha256(json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest()

class EvidenceStore:
 def __init__(self,db,allowed_users):self.db=db;self.profiles=PostgresProfiles(db,freeze_users(allowed_users))
 def _company(self,s,actor,company):
  owner=self.profiles._owner(s,actor);row=self.profiles._company(s,owner,company,lock=True)
  return owner,row
 def _access(self,s,actor,company,collection):
  owner,c=self._company(s,actor,company)
  r=s.execute(select(collections).where(collections.c.id==UUID(str(collection)),collections.c.company_id==c['id'],collections.c.owner_user_id==owner['id']).with_for_update()).mappings().first()
  if r is None or r['generation']!=c['revocation_generation']:raise AcquisitionError('COLLECTION_UNAVAILABLE')
  return r
 def authorize(self,actor,company,collection):
  with self.db.transaction() as s:self._access(s,actor,company,collection)
 def begin(self,actor,company,parameters,key):
  if not isinstance(key,str) or not 16<=len(key)<=200:raise AcquisitionError('REQUEST_ID_INVALID')
  with self.db.transaction() as s:
   owner,c=self._company(s,actor,company)
   r=s.execute(select(collections).where(collections.c.owner_user_id==owner['id'],collections.c.company_id==c['id'],collections.c.request_hash==digest(key))).mappings().first()
   if r:
    if r['parameters']!=parameters or r['generation']!=c['revocation_generation']:raise AcquisitionError('REQUEST_ID_CONFLICT')
    return str(r['id']),False
   identifier=uuid4();s.execute(insert(collections).values(id=identifier,owner_user_id=owner['id'],company_id=c['id'],generation=c['revocation_generation'],request_hash=digest(key),parameters=parameters,status='COLLECTING',started_at=datetime.now(timezone.utc)))
   return str(identifier),True
 def add(self,actor,company,collection,parent,reference,raw,validation,container=None,member=None):
  h=sha256(raw).hexdigest() if raw is not None else None
  public=asdict(reference);public.pop('inline',None)
  public['official_reference']=('https://ted.europa.eu/en/notice/-/detail/'+reference.metadata['publication']
      if reference.kind=='NOTICE' else 'https://app.eop.bg/today/'+str(reference.tender))
  validation=dict(validation)
  if raw is not None and reference.metadata.get('MD5Hash'):
   expected=reference.metadata['MD5Hash']
   validation['metadata_checksum']={'algorithm':'MD5','comparison':'MATCH_OBSERVED' if md5(raw).hexdigest().lower()==str(expected).lower() else 'DIFF_SEMANTICS_UNVERIFIED','semantics':'NOT_PROVEN_FOR_ALL_OBJECTS'}
  if raw is not None and type(reference.metadata.get('Size')) is int:
   validation['metadata_size_matches']=len(raw)==reference.metadata['Size']
  identity_validation=dict(validation)
  if 'retrieval' in identity_validation:identity_validation['retrieval']={k:v for k,v in validation['retrieval'].items() if k!='retrieved_at'}
  identity=digest({'parent':parent,'reference':public,'sha256':h,'validation':identity_validation,'container':container,'member':member,'validator':'structural-v1'})
  with self.db.transaction() as s:
   r=self._access(s,actor,company,collection)
   if r['status']!='COLLECTING':raise AcquisitionError('COLLECTION_CLOSED')
   if h:
    s.execute(pg_insert(evidence).values(sha256=h,raw_payload=raw).on_conflict_do_nothing())
    stored=s.scalar(select(evidence.c.raw_payload).where(evidence.c.sha256==h))
    if stored!=raw or sha256(stored).hexdigest()!=h:raise AcquisitionError('STORAGE_INTEGRITY_FAILED')
   identifier=uuid4()
   s.execute(pg_insert(occurrences).values(id=identifier,identity_hash=identity,parent_tender=parent,reference=public,blob_sha256=h,byte_size=len(raw) if raw is not None else None,validation=validation,observed_at=datetime.now(timezone.utc),container_occurrence=UUID(container) if container else None,member_path=member).on_conflict_do_nothing(index_elements=['identity_hash']))
   actual=s.scalar(select(occurrences.c.id).where(occurrences.c.identity_hash==identity))
   observed=datetime.fromisoformat(validation['retrieval']['retrieved_at']) if validation.get('retrieval',{}).get('retrieved_at') else datetime.now(timezone.utc)
   s.execute(pg_insert(items).values(collection_id=UUID(collection),occurrence_id=actual,observed_at=observed).on_conflict_do_nothing())
   return str(actual)
 def finish(self,actor,company,collection,status,summary):
  with self.db.transaction() as s:
   r=self._access(s,actor,company,collection)
   if r['status']!='COLLECTING':raise AcquisitionError('COLLECTION_CLOSED')
   s.execute(update(collections).where(collections.c.id==r['id']).values(status=status,finished_at=datetime.now(timezone.utc),summary=summary))
 def manifest(self,actor,company,collection):
  with self.db.transaction() as s:
   r=self._access(s,actor,company,collection)
   values=s.execute(select(occurrences,items.c.observed_at.label('collection_observed_at')).join(items,items.c.occurrence_id==occurrences.c.id).where(items.c.collection_id==r['id']).order_by(items.c.observed_at,occurrences.c.id)).mappings().all()
   return {'collection':str(r['id']),'selection':r['parameters'],'status':r['status'],'collection_started':r['started_at'].isoformat(),
    'collection_finished':r['finished_at'].isoformat() if r['finished_at'] else None,'summary':r['summary'],'ai_ready':False,
    'artifacts':[{k:(str(v) if isinstance(v,UUID) else v.isoformat() if isinstance(v,datetime) else v) for k,v in a.items() if k!='identity_hash'} for a in values]}
 def retrieve(self,actor,company,collection,occurrence):
  with self.db.transaction() as s:
   r=self._access(s,actor,company,collection)
   a=s.execute(select(occurrences).join(items,items.c.occurrence_id==occurrences.c.id).where(items.c.collection_id==r['id'],occurrences.c.id==UUID(str(occurrence)))).mappings().first()
   if a is None or not a['validation'].get('valid') or not a['blob_sha256']:raise AcquisitionError('VALIDATED_ARTIFACT_UNAVAILABLE')
   raw=s.scalar(select(evidence.c.raw_payload).where(evidence.c.sha256==a['blob_sha256']))
   if raw is None or len(raw)!=a['byte_size'] or sha256(raw).hexdigest()!=a['blob_sha256']:raise AcquisitionError('STORAGE_INTEGRITY_FAILED')
   return raw
