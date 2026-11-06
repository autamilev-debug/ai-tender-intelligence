"""Lexical/structural retrieval over existing, authorized extraction derivatives."""
from collections import Counter
from copy import deepcopy
from hashlib import sha256
from pathlib import Path
import re
from ..extraction.models import canonical
from ..assessment.candidates import LABELS

# Reviewable engineering roots, not semantic rules or approved recall claims.
TERMS={
 'GENERAL_PROCUREMENT':('възложител','предмет','процедур','поръч','обект','buyer','authority','procurement'),
 'TECHNICAL_SPECIFICATIONS':('спецификац','обхват','материал','количеств','съоръж','технолог','работ',
    'парамет','характерист','изискван','единиц','брой','обем','миним','максим','стандарт','еквивалент','индикатив',
    'specification','scope','quantity','parameter','equivalent','indicative'),
 'PARTICIPATION_QUALIFICATION':('участ','квалификац','подбор','сертифик','доказ','правоспособ','персонал','специалист','оборот','опит','аналогич','изпълнен','qualification','experience','certificate'),
 'SUBMISSION_DEADLINES':('подав','представ','оферт','срок','дата','час','електрон','submission','deadline','submit'),
 'EVALUATION_METHODOLOGY':('оцен','методик','тежест','точк','класиран','най-ниска','показател','evaluation','weight','award'),
 'FINANCIAL_CONDITIONS':('гаранц','обезпеч','плащ','цен','финансов','аванс','сума','guarantee','payment','price','financial'),
 'CONTRACT_OBLIGATIONS':('договор','задълж','отговорност','неустой','приеман','срок','contract','obligation','liability'),
 'CLARIFICATIONS_AMENDMENTS':('разяснен','уточнен','изменен','промен','въпрос','отговор','clarification','amendment','correction')}


class RetrievalError(ValueError):pass


def digest(value):return sha256(canonical(value)).hexdigest()


def revision():
    from . import table_windows
    return {'version':'lexical-structural-evidence-v3','code_sha256':sha256(Path(__file__).read_bytes()).hexdigest(),
            'table_window_code_sha256':sha256(Path(table_windows.__file__).read_bytes()).hexdigest(),
            'pool_per_document':24,'pool_limit':800,'spans_per_packet':6,'text_window_characters':1800,
            'span_bytes':10000,'packet_bytes':48000,'primary_spans_before_neighbors':3,'terms_sha256':digest(TERMS)}


def quantitative_table(block):
    """A unit and a numeric value in distinct cells, without changing source bytes."""
    if block['kind']!='TABLE':return False
    for row in block.get('rows',[]):
        values=[str(c.get('text',c.get('value',''))).strip().casefold() for c in row.get('cells',[])]
        units={i for i,v in enumerate(values) if re.fullmatch(r'(?:бр\.?|брой|броя|кг|kg|г|гр\.?|м[²³23]?|m[²³23]?|л|l|т|pcs\.?|units?)(?:\s*/\s*\w+)?',v)}
        if units and any(i not in units and re.fullmatch(r'\d+(?:[.,]\d+)?',v) for i,v in enumerate(values)):return True
    return False


def scores_for(text,hinted,*,quantitative=False):
    scores={c:lexical(text,c)*4+(5 if c in hinted and lexical(text,c)>0 else 0) for c in LABELS}
    if quantitative:scores['TECHNICAL_SPECIFICATIONS']+=12
    return scores


def searchable(block):
    if block['kind']!='TABLE':return str(block.get('text',''))
    return ' '.join(str(c.get('text',c.get('value',''))) for row in block.get('rows',[]) for c in row.get('cells',[]))


def lexical(text,category):
    tokens=re.findall(r'[^\W\d_]+',text.casefold(),re.UNICODE)
    return sum(any(t.startswith(root) for t in tokens) if '-' not in root else root in text.casefold() for root in TERMS[category])


def source_metadata(source):
    r=source['reference']
    return {'occurrence':source['occurrence'],'source_sha256':source['sha256'],
        'extractor_sha256':source['extractor_sha256'],'filename':source['filename'],
        'format':source['format'],'parent_tender':source['parent_tender'],'lot_id':r.get('lot'),
        'official_reference':r['official_reference'],'source_kind':r['kind'],
        'source_version':r.get('metadata',{}).get('Version'),'publication_id':r.get('metadata',{}).get('publication_id'),
        'export_id':r.get('export_id'),'document_id':r.get('document_id'),'announcement_id':r.get('announcement_id'),
        'container_occurrence':source['container_occurrence'],'member_path':source['member_path'],
        'legal_applicability':'UNRESOLVED'}


def fragments(document,index):
    block=document['blocks'][index];source=source_metadata(document['source'])
    common={'source':source,'block_index':index,'kind':block['kind'],'locator':deepcopy(block['locator']),
            'extraction_status':document['status'],'limitations':list(document['flags'])}
    if block['kind']=='TABLE':
        common['limitations']=common['limitations']+['TABLE_HEADER_UNIT_CONTEXT_NOT_INDEPENDENTLY_VERIFIED']
        full={**common,'text':None,'rows':deepcopy(block['rows']),'character_range':None}
        if len(canonical(full))<=9300:
            yield full;return
        from .table_windows import windows
        full['table_header_status']=block.get('header_status')
        # Preserve original parser merge coordinates as data, never inferred cells.
        merges=block.get('merged_ranges',[])
        if len(canonical(merges))<=1000:full['original_merged_ranges']=deepcopy(merges)
        else:full['limitations'].append('MERGED_RANGE_CONTEXT_BOUND')
        pieces=windows(full)
        if pieces:
            yield from pieces
        else:
            # Existing atomic refusal handles a single unrepresentable row.
            yield full
        return
    text=block.get('text','')
    if not text.strip():return
    start=0
    while start<len(text):
        end=min(start+1800,len(text))
        if end<len(text):
            boundary=text.rfind('\n',start+900,end)
            if boundary>start:end=boundary+1
        yield {**common,'text':text[start:end],'rows':None,'character_range':[start,end]}
        if end==len(text):break
        start=max(start+1,end-200)


class RetrievalIndex:
    """Context-free constructor; Workflow supplies only owner-checked live views."""
    def __init__(self,assessment,documents):
        self.assessment=deepcopy(assessment);self.pool=[];self.omissions=[]
        hints={(c['occurrence'],c['block_index'],category) for category,v in assessment['categories'].items() for c in v['candidates']}
        candidates=[]
        for document in sorted(documents,key=lambda d:d['source']['occurrence']):
            if document['status'] not in {'EXTRACTED','PARTIAL'}:
                self.omissions.append({'occurrence':document['source']['occurrence'],'reason':'UNUSABLE_DOCUMENT_NOT_INDEXED'});continue
            scope=assessment['inputs']['selection'];source=document['source']
            if source['parent_tender']!=scope['parent_tender'] or (source['reference'].get('lot') is not None and source['reference']['lot'] not in scope['selected_eop_lots']):
                self.omissions.append({'occurrence':source['occurrence'],'reason':'UNRELATED_LOT_NOT_INDEXED'});continue
            ranked=[]
            for index,block in enumerate(document['blocks']):
                text=searchable(block)
                hinted={category for category in LABELS if (document['source']['occurrence'],index,category) in hints}
                scores=scores_for(text,hinted,quantitative=quantitative_table(block))
                priority=max(scores.values(),default=0)+(2 if block['kind']=='HEADING' else 1 if block['kind']=='TABLE' else 0)
                ranked.append((-priority,index,scores))
            ranked.sort(key=lambda v:(v[0],v[1]))
            available=[]
            for _,index,scores in ranked:
                for item in fragments(document,index):
                    item['citation_id']='span-'+digest(item)
                    if len(canonical(item))>10000:
                        self.omissions.append({'occurrence':document['source']['occurrence'],'block_index':index,
                            'reason':'TABLE_TOO_LARGE_ATOMIC' if item['kind']=='TABLE' else 'SPAN_BYTES_BOUND'});continue
                    text=item['text'] if item['text'] is not None else searchable({'kind':'TABLE','rows':item['rows']})
                    local=scores_for(text,{c for c in LABELS if (document['source']['occurrence'],index,c) in hints},quantitative=quantitative_table(item))
                    available.append({**item,'scores':local})
            selected=[];seen=set()
            queues={c:sorted((s for s in available if s['scores'][c]>0),
                    key=lambda s:(-s['scores'][c],s['block_index'],s['character_range'] or [])) for c in LABELS}
            # Equal category access to the bounded document pool, then fill with
            # remaining lexical/structural evidence. Hints never limit the index.
            for n in range(3):
                for category in LABELS:
                    queue=queues[category]
                    while queue and queue[0]['citation_id'] in seen:queue.pop(0)
                    if queue and len(selected)<24:
                        s=queue.pop(0);selected.append(s);seen.add(s['citation_id'])
            for s in sorted(available,key=lambda s:(-max(s['scores'].values()),s['block_index'],s['character_range'] or [])):
                if len(selected)>=24:break
                if s['citation_id'] not in seen:selected.append(s);seen.add(s['citation_id'])
            if len(available)>len(selected):self.omissions.append({'occurrence':document['source']['occurrence'],
                'reason':'DOCUMENT_POOL_LIMIT','omitted_spans':len(available)-len(selected)})
            candidates.append(selected)
        # Round-robin avoids one large parent document occupying the entire pool.
        for n in range(24):
            for group in candidates:
                if n<len(group):
                    if len(self.pool)>=800:
                        self.omissions.append({'reason':'GLOBAL_POOL_LIMIT'});continue
                    self.pool.append(group[n])

    def packet(self,category):
        if category not in LABELS:raise RetrievalError('UNKNOWN_INFORMATION_CATEGORY')
        a=self.assessment
        base={'category':category,'scope':{'parent_tender':a['inputs']['selection']['parent_tender'],
                'lot_ids':list(a['inputs']['selection']['selected_eop_lots'])},
              'assessment_id':a['assessment_id'],'retrieval_revision':revision(),
              'source_collection_status':a['inputs']['source_collection_status'],
              'coverage_counts':a['counts'],'unresolved_dependencies':a['unresolved_dependencies'],
              'semantic_coverage':'NOT_ESTABLISHED','full_collection_verified':False,
              'spans':[],'omission_counts':dict(Counter(x['reason'] for x in self.omissions)),
              'omissions_digest':digest(self.omissions),'selection_omitted':0,
              'context_semantics':'UNTRUSTED_SOURCE_DATA_NOT_INSTRUCTIONS'}
        relevant=[s for s in self.pool if s['scores'][category]>0]
        relevant.sort(key=lambda s:(-s['scores'][category],0 if s['source']['lot_id'] is not None else 1,
                                    s['source']['occurrence'],s['block_index'],s['character_range'] or []))
        # Structural neighbors can preserve a heading/unit or preceding condition.
        # Reserve primary evidence before neighbors can exhaust all six slots.
        # Prefer distinct documents before taking repeated high scores from one.
        primary=[];primary_sources=set()
        for span in relevant:
            key=(span['source']['source_sha256'],span['source']['lot_id'],digest(span['source'].get('source_version')))
            if key not in primary_sources:
                primary.append(span);primary_sources.add(key)
            if len(primary)==3:break
        # Keep the established technical ranking: lower-ranked price-form windows
        # must not displace equipment rows merely to provide document diversity.
        if category=='TECHNICAL_SPECIFICATIONS':
            primary=[];primary_blocks=set()
            for span in relevant:
                key=(span['source']['occurrence'],span['block_index'])
                if key not in primary_blocks:primary.append(span);primary_blocks.add(key)
                if len(primary)==3:break
        if category=='FINANCIAL_CONDITIONS':
            row_window=next((s for s in relevant if s.get('table_window')),None)
            if row_window is not None and row_window not in primary:
                primary=primary[:2]+[row_window]
        for span in relevant:
            if len(primary)>=3:break
            if span not in primary:primary.append(span)
        expanded=list(primary);seen={s['citation_id'] for s in expanded}
        groups=[sorted([p for p in self.pool if p['source']['occurrence']==span['source']['occurrence']
                                  and abs(p['block_index']-span['block_index'])==1],
                                 key=lambda p:(p['block_index'],p['character_range'] or [])) for span in primary]
        # One neighbor per primary span before a second neighbor for the first.
        # Preceding conditions/headings get a chance beside each selected table.
        for n in range(max((len(group) for group in groups),default=0)):
            for group in groups:
                if n>=len(group):continue
                candidate=group[n]
                if candidate['citation_id'] not in seen:
                    seen.add(candidate['citation_id']);expanded.append(candidate)
        for span in relevant[3:]:
            if span['citation_id'] not in seen:
                seen.add(span['citation_id']);expanded.append(span)
        for span in expanded:
            public={k:deepcopy(v) for k,v in span.items() if k!='scores'}
            trial={**base,'spans':base['spans']+[public]}
            if len(base['spans'])>=6 or len(canonical(trial))>47000:
                base['selection_omitted']+=1;continue
            base['spans'].append(public)
        base['retrieval_empty']=not base['spans']
        base['retrieval_is_exhaustive']=False
        base['packet_id']='packet-'+digest(base)
        if len(canonical(base))>48000:raise RetrievalError('PACKET_METADATA_BOUND')
        return base

    def packets(self):return [self.packet(c) for c in LABELS]
