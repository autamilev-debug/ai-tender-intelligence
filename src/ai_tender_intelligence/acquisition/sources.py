"""Observed anonymous EOP public-read profile; no authenticated/generation methods."""
import json,re
from hashlib import sha256
from urllib.parse import urlsplit
from .models import Reference,AcquisitionError,integer,metadata,safe_text,now
from .http import allowed,METHODS
from ..discovery.ted import load_json,normalize_xml

class EOPDocuments:
    def __init__(self,http):self.http=http;self.receipts=[];self.download_facts={}
    def call(self,method,body):
        if method not in METHODS:raise AcquisitionError('FORBIDDEN_SOURCE_METHOD')
        data,_=self.http.request('POST','https://service.eop.bg/NX1Service.svc/'+method,body=body)
        try:value=load_json(data)
        except ValueError:raise AcquisitionError('MALFORMED_SOURCE_JSON') from None
        if isinstance(value,dict) and set(value)=={'d'}:value=value['d']
        if method!='GetSignedUrlByDocumentId':
            self.receipts.append({'method':method,'arguments':body,'sha256':sha256(data).hexdigest(),'observed_at':now()})
        return value
    def rows(self,method,body):
        value=self.call(method,body)
        if not isinstance(value,list) or len(value)>self.http.bounds.metadata_rows or any(not isinstance(r,dict) for r in value):
            raise AcquisitionError('UNSUPPORTED_SOURCE_INVENTORY')
        return value
    def discover(self,parent,lots):
        integer(parent)
        if len(lots)>10 or len(set(lots))!=len(lots):raise AcquisitionError('LOT_SELECTION_BOUND')
        for lot in lots:integer(lot)
        body={'tenderId':parent,'ianaTimeZone':'Europe/Sofia'}
        detail=self.call('GetPublishedTenderDetails',body)
        if not isinstance(detail,dict) or detail.get('TenderId')!=parent or detail.get('ParentTender') not in (None,0):
            raise AcquisitionError('PARENT_IDENTITY_NOT_VERIFIED')
        known=self.rows('GetPublishedLots',body)
        ids={integer(r.get('TenderId')) for r in known}
        if not set(lots)<=ids:raise AcquisitionError('LOT_IDENTITY_NOT_VERIFIED')
        refs=[];issues=[]
        def optional(method,args):
            try:return self.rows(method,args)
            except AcquisitionError as e:issues.append(method+':'+str(e));return []
        for r in optional('GetPublishedTenderExportsByTenderId',body):
            tid=integer(r.get('TenderId'))
            if tid not in {parent,*lots}:continue
            export=integer(r.get('Id'));did=integer(r.get('DocumentId'))
            refs.append(Reference('EXPORT',tid,'export:'+str(export),safe_text(r.get('Name',''),512),did,export_id=export,
                lot=tid if tid!=parent else None,applicability='LOT_SOURCE_SCOPE' if tid!=parent else 'GENERAL_CANDIDATE',metadata=metadata(r)))
        requirements=detail.get('TenderDescriptionDocuments')
        if not isinstance(requirements,list) or len(requirements)>self.http.bounds.metadata_rows:issues.append('REQUIREMENT_LIST_UNSUPPORTED');requirements=[]
        for r in requirements:
            if not isinstance(r,dict):raise AcquisitionError('REQUIREMENT_IDENTITY_INVALID')
            did=integer(r.get('Id'))
            refs.append(Reference('REQUIREMENT',parent,'document:'+str(did),safe_text(r.get('Name',''),512),did,
                                  applicability='GENERAL_CANDIDATE',metadata=metadata(r)))
        publications=detail.get('TenderPublicationDetails')
        if not isinstance(publications,list) or len(publications)>self.http.bounds.metadata_rows:issues.append('PUBLICATION_LIST_UNSUPPORTED');publications=[]
        for r in publications:
            if not isinstance(r,dict) or r.get('TenderId') not in {parent,*lots}:continue
            pub=integer(r.get('TenderPublicationId'));did=r.get('DocumentId')
            if not did:issues.append('PUBLICATION_DOCUMENT_NOT_ESTABLISHED');continue
            did=integer(did)
            refs.append(Reference('PUBLICATION',r['TenderId'],'publication:'+str(pub),'publication-'+str(pub),did,
                lot=r['TenderId'] if r['TenderId']!=parent else None,metadata={'publication_id':pub,'parent_publication_id':r.get('ParentTenderPublicationId')}))
        for tid in (parent,*lots):
            rows=optional('GetPublicTenderAnnouncementsByTenderId',{'tenderId':tid,'ianaTimeZone':'Europe/Sofia'})
            for r in rows:
                aid=integer(r.get('Id'));text=safe_text(r.get('Text') or '');title=safe_text(r.get('Title') or '',1000)
                inline=json.dumps({'Id':aid,'Title':title,'Text':text,'CreatedDate':r.get('CreatedDate')},ensure_ascii=False,sort_keys=True).encode()
                app='UNRESOLVED_PARENT_ANNOUNCEMENT' if tid==parent else 'LOT_SOURCE_SCOPE'
                refs.append(Reference('ANNOUNCEMENT',tid,'announcement:'+str(aid),'announcement-'+str(aid)+'.json',
                    announcement_id=aid,lot=tid if tid!=parent else None,applicability=app,
                    metadata={'CreatedDate':r.get('CreatedDate'),'text_url_queries_redacted':text!=(r.get('Text') or '')},inline=inline))
                for doc in optional('RetrieveTenderAnnouncementDocuments',{'tenderAnnouncementId':aid}):
                    did=integer(doc.get('Id'))
                    refs.append(Reference('ATTACHMENT',tid,'announcement:'+str(aid)+':document:'+str(did),safe_text(doc.get('Name',''),512),did,
                        announcement_id=aid,lot=tid if tid!=parent else None,applicability=app,metadata=metadata(doc)))
        if len(refs)>self.http.bounds.metadata_rows:raise AcquisitionError('SOURCE_INVENTORY_BOUND')
        return refs,issues
    def download(self,ref):
        if ref.inline is not None:return ref.inline,'application/json'
        did=integer(ref.document_id)
        if ref.export_id:
            value=self.call('GetPublishedTenderExportDocument',{'publishedTenderExportId':ref.export_id})
            if type(value) is not int or value!=did:raise AcquisitionError('EXPORT_DOCUMENT_IDENTITY_MISMATCH')
        for attempt in range(2):
            link=self.call('GetSignedUrlByDocumentId',{'documentId':did})
            if not isinstance(link,dict) or not isinstance(link.get('Url'),str):raise AcquisitionError('DOWNLOAD_LINK_UNSUPPORTED')
            url=link['Url'];allowed(url)
            u=urlsplit(url)
            if (u.hostname!='storage.eop.bg' or u.path!='/'+str(link.get('Container'))+'/'+str(link.get('CloudName'))
                    or link.get('Uploading')):raise AcquisitionError('DOWNLOAD_LINK_IDENTITY_MISMATCH')
            self.download_facts[ref.identity]={'retrieved_at':now(),'official_host':u.hostname,'official_path':u.path,'observed_filename':safe_text(link.get('Name') or '',512),'signed_query_retained':False}
            try:
                result=self.http.request('GET',url)
                self.download_facts[ref.identity]['retrieved_at']=now()
                return result
            except AcquisitionError as e:
                if attempt or str(e)!='SOURCE_RESTRICTED':raise
        raise AcquisitionError('SOURCE_RESTRICTED')


def ted_reference(http,publication,parent):
    if not re.fullmatch(r'\d{1,7}-\d{4}',publication):raise AcquisitionError('TED_IDENTITY_INVALID')
    data,_=http.request('GET','https://ted.europa.eu/en/notice/'+publication+'/xml')
    try:n=normalize_xml(data,{'publication-number':publication})
    except ValueError:raise AcquisitionError('TED_NOTICE_INVALID') from None
    if 'eop:tender:'+str(parent) not in n.identities:raise AcquisitionError('TED_EOP_LINK_NOT_VERIFIED')
    return Reference('NOTICE',parent,'ted:'+publication,'ted-'+publication+'.xml',metadata={
        'publication':publication,'notice_version':n.version,'profile':n.profile,'official_url':n.official_url,
        'procedure_identities':list(n.identities)},inline=data)
