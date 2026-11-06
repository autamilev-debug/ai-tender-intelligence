"""Bounded foreground collection only; semantic sufficiency/freeze/AI are absent."""
from dataclasses import replace
from io import BytesIO
import zipfile
from .models import Bounds,Reference,AcquisitionError,now
from .sources import EOPDocuments,ted_reference
from .validator import validate

class Collector:
 def __init__(self,store,http,*,validator=validate):self.store,self.http,self.validator=store,http,validator
 def run(self,actor,company,parent,lots,*,key,ted=None,max_downloads=10,document_ids=()):
  bounds=self.http.bounds
  if type(max_downloads) is not int or not 1<=max_downloads<=bounds.files:raise AcquisitionError('DOWNLOAD_COUNT_BOUND')
  if len(document_ids)>bounds.files or any(type(i) is not int or i<=0 for i in document_ids):raise AcquisitionError('DOCUMENT_SELECTION_INVALID')
  parameters={'selected_document_ids':list(document_ids),'parent_tender':parent,'selected_eop_lots':list(lots),'ted_publication':ted,'bounds':bounds.__dict__,'max_downloads':max_downloads,'profile':'EOP-public-documents-observed-2026-10-29'}
  collection,created=self.store.begin(actor,company,parameters,key)
  if not created:return self.store.manifest(actor,company,collection)
  initial_calls,initial_bytes=self.http.calls,self.http.bytes
  source=EOPDocuments(self.http);issues=[];failures=0;downloaded=0;stored_bytes=0;members=0
  def save(ref,raw,result,container=None,member=None):
   nonlocal stored_bytes
   if raw is not None and stored_bytes+len(raw)>bounds.total_bytes:
    raise AcquisitionError('COLLECTION_STORAGE_BOUND')
   if raw is not None:stored_bytes+=len(raw)
   return self.store.add(actor,company,collection,parent,ref,raw,result,container,member)
  def unpack(ref,raw,result,root,depth=0):
   nonlocal failures,members
   if result.get('kind')!='ZIP' or not result.get('valid'):return
   with zipfile.ZipFile(BytesIO(raw),metadata_encoding='cp866') as z:
    for child in result.get('children',[]):
     members+=1
     path=child['name'];child_ref=replace(ref,kind='ARCHIVE_MEMBER',identity=ref.identity+':member:'+path,filename=path,document_id=None,metadata={'archive_document_id':ref.document_id})
     if members>bounds.files:
      save(child_ref,None,{'valid':False,'kind':'UNKNOWN','error':'COLLECTION_MEMBER_BOUND'},root,path);failures+=1;return
     original=child.get('archive_name',path)
     entry=next(i for i in z.infolist() if i.orig_filename==original)
     with z.open(entry) as f:data=f.read(bounds.file_bytes+1)
     if len(data)>bounds.file_bytes:raise AcquisitionError('MEMBER_READ_BOUND')
     report={k:v for k,v in child.items() if k!='name'}
     child_id=save(child_ref,data,report,root,path)
     if not report.get('valid'):failures+=1
     unpack(child_ref,data,report,child_id,depth+1)
  try:
   refs=[]
   if ted:
    self.store.authorize(actor,company,collection)
    ref=ted_reference(self.http,ted,parent)
    result=self.validator(ref.inline,ref.filename,bounds);save(ref,ref.inline,result)
    if not result.get('valid'):failures+=1
   self.store.authorize(actor,company,collection)
   found,discovery_issues=source.discover(parent,tuple(lots));refs+=found;issues+=discovery_issues
   if not refs:issues.append('SOURCE_INVENTORY_EMPTY')
   if set(document_ids)-{r.document_id for r in refs}:issues.append('SELECTED_DOCUMENT_NOT_IN_VERIFIED_INVENTORY')
   for ref in refs:
    self.store.authorize(actor,company,collection)
    if ref.inline is None and document_ids and ref.document_id not in document_ids:
     save(ref,None,{'valid':False,'kind':'UNKNOWN','error':'NOT_FETCHED_DEVELOPMENT_SELECTION'});failures+=1;continue
    if ref.inline is None and (downloaded>=max_downloads or stored_bytes>=bounds.total_bytes):
     save(ref,None,{'valid':False,'kind':'UNKNOWN','error':'NOT_FETCHED_DOWNLOAD_BOUND'});failures+=1;continue
    try:
     if ref.inline is None:downloaded+=1
     data,content_type=source.download(ref)
     result=self.validator(data,ref.filename,bounds)
     result['observed_content_type']=content_type
     result['retrieval']=source.download_facts.get(ref.identity,{'retrieved_at':now(),'source':'OFFICIAL_INLINE_EVIDENCE'})
     root=save(ref,data,result)
     if not result.get('valid'):failures+=1
     unpack(ref,data,result,root)
    except AcquisitionError as e:
     save(ref,None,{'valid':False,'kind':'UNKNOWN','error':str(e)});failures+=1
   summary={'inventory_completeness':'UNKNOWN_SOURCE_COMPLETENESS','all_enumerated_obtained':not failures and not issues,
            'applicability':'SOURCE_SCOPE_RECORDED_SEMANTIC_APPLICABILITY_NOT_VALIDATED','sufficiency':'NOT_ASSESSED','corpus_frozen':False,
            'issues':issues,'failed_or_unobtained':failures,'download_attempts':downloaded,'stored_bytes':stored_bytes,'http_requests':self.http.calls-initial_calls,'http_bytes':self.http.bytes-initial_bytes,
            'metadata_receipts':source.receipts,'scope_limitations':['PARENT_ANNOUNCEMENTS_MAY_APPLY_TO_SELECTED_LOTS','EMPTY_LOT_LIST_DOES_NOT_EXCLUDE_PARENT_CLARIFICATIONS','NO_LEGAL_HISTORICAL_OR_AI_COMPLETENESS_CLAIM']}
   self.store.finish(actor,company,collection,'PARTIAL' if failures or issues else 'ACQUIRED',summary)
  except BaseException as e:
   code=str(e) if isinstance(e,AcquisitionError) else 'COLLECTION_INTERRUPTED' if isinstance(e,KeyboardInterrupt) else 'COLLECTION_TECHNICAL_FAILURE'
   self.store.finish(actor,company,collection,'INTERRUPTED' if isinstance(e,KeyboardInterrupt) else 'FAILED',{'issues':[code],'sufficiency':'NOT_ASSESSED','metadata_receipts':source.receipts})
   if isinstance(e,KeyboardInterrupt):raise
  return self.store.manifest(actor,company,collection)
