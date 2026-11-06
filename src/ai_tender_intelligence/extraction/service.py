"""Collection coverage is not semantic completeness, admission or a frozen corpus."""
from collections import Counter
from .models import Limits,ExtractionError,fingerprint
from .runner import run
from .store import ExtractionStore

SECTION_TARGETS=['TECHNICAL_SPECIFICATIONS','PARTICIPATION_QUALIFICATION','SUBMISSION_INSTRUCTIONS',
                 'DEADLINES','EVALUATION_CRITERIA','CONTRACT_CONDITIONS','FINANCIAL_GUARANTEES','CLARIFICATIONS']


def source_link(item,identity_hash,identity):
    ref=item['reference']
    return {'occurrence':item['id'],'sha256':item['blob_sha256'],'filename':ref['filename'],
            'format':item['validation']['kind'],'parent_tender':item['parent_tender'],
            'reference':ref,'container_occurrence':item['container_occurrence'],'member_path':item['member_path'],
            'extractor_sha256':identity_hash,'extraction_method':identity}


def bind(result,source):
    return {**result,'source':source,'blocks':[{**block,'source':source} for block in result['blocks']]}


def in_scope(manifest,item):
    parent=manifest['selection']['parent_tender'];lots=manifest['selection']['selected_eop_lots']
    return (item['parent_tender']==parent and item['reference']['tender'] in {parent,*lots}
            and (item['reference'].get('lot') is None or item['reference']['lot'] in lots))


class Extractor:
    def __init__(self,evidence,*,parser=run,limits=Limits()):
        self.evidence=evidence;self.cache=ExtractionStore(evidence);self.parser=parser;self.limits=limits

    def document(self,actor,company,collection,occurrence):
        manifest=self.evidence.manifest(actor,company,collection)
        item=next((a for a in manifest['artifacts'] if a['id']==str(occurrence)),None)
        if item is None:raise ExtractionError('EXTRACTION_SOURCE_UNAVAILABLE')
        if not in_scope(manifest,item):raise ExtractionError('EXTRACTION_OUT_OF_SELECTED_SCOPE')
        kind=item['validation']['kind']
        # This verifies original length/hash and access even on derivative-cache hits.
        raw=self.evidence.retrieve(actor,company,collection,occurrence)
        identity_hash,identity=fingerprint(kind,self.limits)
        cached=self.cache.load(actor,company,collection,occurrence,identity_hash)
        if cached is None:
            result=self.parser(raw,kind,self.limits)
            self.cache.publish(actor,company,collection,occurrence,identity_hash,identity,result)
        else:
            if cached['identity']!=identity or cached['format']!=kind:
                raise ExtractionError('EXTRACTION_IDENTITY_MISMATCH')
            result=cached['result']
        self.evidence.authorize(actor,company,collection)
        return bind(result,source_link(item,identity_hash,identity))

    def passage(self,actor,company,collection,occurrence,index):
        if type(index) is not int or index<0:raise ExtractionError('PASSAGE_UNAVAILABLE')
        doc=self.document(actor,company,collection,occurrence)
        if index>=len(doc['blocks']):raise ExtractionError('PASSAGE_UNAVAILABLE')
        return doc['blocks'][index]

    def collection(self,actor,company,collection,*,max_documents=100):
        if type(max_documents) is not int or not 1<=max_documents<=100:raise ExtractionError('EXTRACTION_DOCUMENT_BOUND')
        m=self.evidence.manifest(actor,company,collection)
        results=[];processed=0
        parent=m['selection']['parent_tender'];lots=m['selection']['selected_eop_lots']
        for item in m['artifacts']:
            self.evidence.authorize(actor,company,collection)
            ref=item['reference'];entry={'occurrence':item['id'],'reference':ref,'sha256':item['blob_sha256'],
                                       'format':item['validation']['kind'],'container_occurrence':item['container_occurrence'],
                                       'semantic_applicability':'NOT_VALIDATED'}
            if not in_scope(m,item):
                entry.update(status='OUT_OF_SELECTED_SCOPE',flags=['UNRELATED_LOT_NOT_EXTRACTED'])
            elif not item['blob_sha256']:
                entry.update(status='NOT_ACQUIRED',flags=[item['validation'].get('error','SOURCE_UNAVAILABLE')])
            elif not item['validation'].get('valid'):
                entry.update(status='INVALID_SOURCE',flags=[item['validation'].get('error','SOURCE_INVALID')])
            elif item['validation']['kind']=='ZIP':
                entry.update(status='CONTAINER',flags=['MEMBER_RESULTS_LISTED_SEPARATELY'])
            elif processed>=max_documents:
                entry.update(status='NOT_PROCESSED_BOUND',flags=['COLLECTION_EXTRACTION_BOUND'])
            else:
                processed+=1
                doc=self.document(actor,company,collection,item['id'])
                entry.update(status=doc['status'],flags=doc['flags'],units=doc['units'],blocks=len(doc['blocks']),
                             extractor_sha256=doc['source']['extractor_sha256'])
            results.append(entry)
        self.evidence.authorize(actor,company,collection)
        counts=Counter(r['status'] for r in results)
        page_units=[u for r in results for u in r.get('units',[]) if 'page' in u]
        sheet_units=[u for r in results for u in r.get('units',[]) if 'sheet' in u]
        return {'collection':str(collection),'selected_parent':parent,'selected_lots':lots,
                'collection_period':[m['collection_started'],m['collection_finished']],
                'inventory':results,'counts':dict(counts),'occurrences_total':len(results),
                'discovered_top_level_sources':sum(a['container_occurrence'] is None for a in m['artifacts']),
                'archive_members':sum(a['container_occurrence'] is not None for a in m['artifacts']),
                'acquired_occurrences':sum(a['blob_sha256'] is not None for a in m['artifacts']),
                'page_coverage':{'pages_visited':len(page_units),
                    'declared_pages':sum(r.get('units',[{}])[0].get('total_pages',0) for r in results if r.get('units')),
                    'statuses':dict(Counter(u['status'] for u in page_units))},
                'worksheet_coverage':{'sheets_visited':len(sheet_units),
                    'declared_sheets':sum(r.get('units',[{}])[0].get('total_sheets',0) for r in results if r.get('units')),
                    'statuses':dict(Counter(u['status'] for u in sheet_units))},
                'general_or_unresolved_scope_occurrences':sum(r['reference'].get('applicability')!='LOT_SOURCE_SCOPE' for r in results),
                'source_completeness':'UNKNOWN','semantic_applicability':'NOT_VALIDATED',
                'required_sections':{category:'NOT_ASSESSED' for category in SECTION_TARGETS},
                'corpus_frozen':False,'ai_ready':False,'tender_analysis_completeness':'NOT_ASSESSED'}
