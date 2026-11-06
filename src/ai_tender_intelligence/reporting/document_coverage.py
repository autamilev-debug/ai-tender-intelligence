"""Separate source observations, available bytes, extraction and limited AI use.

Presentation grouping never updates acquisition evidence, sufficiency or source
identity. Filename similarity alone is never a deduplication key.
"""
from collections import Counter,defaultdict
from hashlib import sha256,md5
from pathlib import PurePosixPath
import re
from ..semantic.contract import SemanticError
from ..semantic.retrieval import digest


def observed_version(reference):
    m=reference.get('metadata') or {}
    return {k:m[k] for k in ('Guid','Version','IsPreviousVersion','PreviousVersionId','ModifiedDate') if k in m}


def copy_bindings(documents,read_original):
    """Observed public MD5+size links an unavailable route to retained bytes.

    Local SHA-256 verifies retained bytes; MD5 is only a legacy source metadata
    comparison, not collision-resistant authentication, historical membership or
    current-source freshness. Scope and document ID in the export member must
    agree too. The original NOT_ACQUIRED record remains unchanged.
    """
    if read_original is None:return []
    cached={};bindings=[]
    for d in documents:
        ref=d['reference'];meta=ref.get('metadata') or {};docid=ref.get('document_id')
        if d.get('source_sha256') or ref['kind']!='REQUIREMENT' or type(docid) is not int:continue
        if type(meta.get('Size')) is not int or not re.fullmatch(r'[a-fA-F0-9]{32}',str(meta.get('MD5Hash',''))):continue
        for candidate in documents:
            cr=candidate['reference'];h=candidate.get('source_sha256')
            if not h or cr['kind']!='ARCHIVE_MEMBER' or cr.get('lot')!=ref.get('lot') or cr.get('tender')!=ref.get('tender'):continue
            if not re.search(r'\('+str(docid)+r'\)(?=\.[^.]+$)',cr['filename']):continue
            occurrence=candidate['occurrence']
            if occurrence not in cached:
                raw=read_original(occurrence)
                if not isinstance(raw,bytes) or sha256(raw).hexdigest()!=h:raise SemanticError('COVERAGE_ORIGINAL_INTEGRITY_FAILED')
                cached[occurrence]=(len(raw),md5(raw).hexdigest())
            size,legacy=cached[occurrence]
            if size==meta['Size'] and legacy.casefold()==meta['MD5Hash'].casefold():
                bindings.append({'unavailable_occurrence':d['occurrence'],'available_occurrence':occurrence,
                    'source_sha256':h,'document_id':docid,'metadata_version':observed_version(ref),
                    'metadata_size':meta['Size'],'observed_size':size,
                    'metadata_md5':meta['MD5Hash'],'observed_md5':legacy,
                    'basis':'OBSERVED_ID_SIZE_MD5_AND_VERIFIED_LOCAL_SHA256','historical_or_current_version_proven':False})
    return bindings


def extraction_reason(d):
    state=d['status'];flags=set(d.get('flags',[]))
    if state=='PARTIAL':
        if 'WORD_COMPLEX_CONTENT_NOT_FULLY_COVERED' in flags:
            return 'Основният текст и наличните таблици са извлечени; вградените елементи на Word не са покрити изцяло.'
        if 'XLS_BINARY_FORMULA_TEXT_NOT_DECODED' in flags:
            return 'Наличните стойности са извлечени; формулите на стария XLS формат не са декодирани.'
        return 'Частично извличане; не е установено покритие на цялото съдържание.'
    return {'EXTRACTED':'Поддържаният текст е извлечен; това не удостоверява пълнота на документа или правен смисъл.',
            'UNSUPPORTED':'Форматът не се поддържа; съдържанието не е извлечено.',
            'UNREADABLE':'Не е получен използваем текст.', 'FAILED':'Обработката е неуспешна.',
            'NOT_ACQUIRED':'По този път не е получен файл.', 'CONTAINER':'Архивен контейнер; съдържанието се отчита по отделни файлове.',
            'OUT_OF_SELECTED_SCOPE':'Източникът е за друга официално идентифицирана позиция.'}.get(state,'Обработката не е установена.')


def build_document_coverage(assessment,packets,findings,*,selected_sources=(),read_original=None,evaluation_modes=None):
    docs=assessment['documents'];by_id={d['occurrence']:d for d in docs}
    if len(by_id)!=len(docs):raise SemanticError('DUPLICATE_COVERAGE_OCCURRENCE')
    scope=assessment['inputs']['selection'];selected=set(scope['selected_eop_lots'])
    # Packet and finding scopes are already validated by the owning workflow.
    spans=defaultdict(list);citations=defaultdict(list)
    for p in packets:
        if p['scope']!={'parent_tender':scope['parent_tender'],'lot_ids':scope['selected_eop_lots']}:raise SemanticError('COVERAGE_SCOPE_MISMATCH')
        for s in p['spans']:
            if s['source']['occurrence'] not in by_id:raise SemanticError('COVERAGE_SOURCE_OUTSIDE_COLLECTION')
            spans[s['source']['occurrence']].append({'packet_id':p['packet_id'],'category':p['category'],'citation_id':s['citation_id'],'block_index':s['block_index'],
                'execution_mode':(evaluation_modes or {}).get(p['packet_id'],'NOT_RECORDED')})
    for f in findings:
        for e in f['validated_evidence']:citations[e['occurrence']].append(e['citation_id'])
    bindings=copy_bindings([d for d in docs if d['reference'].get('lot') is None or d['reference']['lot'] in selected],read_original);bound=defaultdict(list)
    for link in bindings:bound[link['unavailable_occurrence']].append(link)
    rows=[]
    for d in docs:
        ref=d['reference'];oid=d['occurrence'];lot=ref.get('lot');state=d['status']
        relevant=lot is None or lot in selected
        essential=d.get('essentiality') in {'ESSENTIAL','REQUIRED','KNOWN_ESSENTIAL'}
        available=bool(d.get('source_sha256'));links=bound.get(oid,[])
        rows.append({'occurrence':oid,'filename':ref['filename'],'source_reference':ref['official_reference'],
            'source_kind':ref['kind'],'source_identity':ref.get('identity'),'source_sha256':d.get('source_sha256'),
            'source_lot':lot,'scope_state':'UNRESOLVED_PARENT_APPLICABILITY' if lot is None else 'SELECTED_SOURCE_LOT' if relevant else 'OTHER_SOURCE_LOT',
            'source_version':observed_version(ref),'discovered':True,'acquired_this_route':available,
            'content_available_from_observed_archive':bool(links),'acquisition_status':state if not available else 'BYTES_RETAINED',
            'extraction_status':state,'extraction_complete':'NOT_CERTIFIED','extraction_reason_bg':extraction_reason(d),
            'format':d.get('format'),'extraction_revision':d.get('extractor_sha256'),
            'flags':list(d.get('flags',[])),'blocks':d.get('blocks'),
            'selected_for_retrieval':oid in selected_sources,'retrieved_spans':spans[oid],
            'included_in_successful_evaluation_input':bool(spans[oid]),'cited_in_validated_findings':bool(citations[oid]),
            'whole_document_analyzed':False,'relevant_source_scope':relevant,'legal_applicability':'UNRESOLVED',
            'essentiality':'CONFIRMED_ESSENTIAL' if essential else 'NOT_ESTABLISHED',
            'confirmed_missing_essential_content':essential and not available and not links,
            'essential_information_unavailable':'CONFIRMED' if essential and not available and not links else 'NOT_ESTABLISHED',
            'container_occurrence':d.get('container_occurrence'),'member_path':d.get('member_path'),
            'record_status_preserved':True})

    # Group duplicate observations only by actual content+scope, or by exact
    # official identity/version when there were no bytes. Same names are irrelevant.
    groups={};occ_group={}
    for row in rows:
        if row['occurrence'] in bound:continue
        identity=('BYTES',row['source_sha256'],row['source_kind'],row['source_lot'],digest(row['source_version']),row['format'],row['extraction_revision'],row['extraction_status']) if row['source_sha256'] else (
                  'REFERENCE',row['source_kind'],row['source_identity'] or row['occurrence'],row['source_lot'],digest(row['source_version']))
        group=groups.setdefault(identity,{'records':[],'bindings':[]});group['records'].append(row);occ_group[row['occurrence']]=group
    for row in rows:
        links=bound.get(row['occurrence'])
        if not links:continue
        # Multiple distinct matching byte versions are not silently resolved.
        targets={link['source_sha256'] for link in links}
        if len(targets)!=1:
            groups[('UNRESOLVED_COPY',row['occurrence'])]={'records':[row],'bindings':links};continue
        target=occ_group[links[0]['available_occurrence']]
        existing_versions={digest(r['source_version']) for r in target['records'] if r['source_version']}
        incoming=digest(row['source_version']) if row['source_version'] else None
        if existing_versions and incoming and incoming not in existing_versions:
            variant=('DISTINCT_VERSION',row['source_kind'],row['source_identity'],row['source_lot'],incoming,tuple(sorted(targets)))
            group=groups.setdefault(variant,{'records':[],'bindings':[]});group['records'].append(row);group['bindings'].extend(links);continue
        target['records'].append(row);target['bindings'].extend(links);occ_group[row['occurrence']]=target

    display=[];analyzed=[];publications=[];containers=[];excluded=[]
    for group in groups.values():
        records=group['records'];acquired=[r for r in records if r['source_sha256']];rep=(acquired or records)[0]
        kinds={r['source_kind'] for r in records};sourcekind=rep['source_kind']
        item={'filename':rep['filename'],'source_reference':rep['source_reference'],'source_lot':rep['source_lot'],
              'source_sha256':rep['source_sha256'],'occurrences':[r['occurrence'] for r in records],
              'source_versions':[r['source_version'] for r in records],
              'record_count':len(records),'copy_binding_basis':group['bindings'],
              'incomplete_processing':bool(acquired) and any(r['extraction_status'] in {'PARTIAL','UNSUPPORTED','UNREADABLE','FAILED'} for r in acquired),
              'known_missing_essential_content':any(r['confirmed_missing_essential_content'] for r in records),
              'scope_note_bg':'Обща документация; приложимостта към избраната позиция не е установена.' if rep['source_lot'] is None else f'Източник за позиция {rep["source_lot"]}; правната приложимост остава неустановена.'}
        if not any(r['relevant_source_scope'] for r in records):excluded.append(item);continue
        if sourcekind=='PUBLICATION':publications.append(item);continue
        if sourcekind=='EXPORT' or any(r['extraction_status']=='CONTAINER' for r in acquired):containers.append(item);continue
        sent=[s for r in records for s in r['retrieved_spans']]
        item['span_count']=len({(s['packet_id'],s['citation_id']) for s in sent})
        item['categories']=sorted({s['category'] for s in sent})
        item['model_modes']=sorted({s['execution_mode'] for s in sent})
        if sent:analyzed.append({**item,'coverage_bg':'Предоставени и оценени са само избрани откъси; не целият документ.'})
        reasons=[]
        if acquired:reasons.append(rep['extraction_reason_bg'])
        elif any(r['content_available_from_observed_archive'] for r in records):reasons.append('Налично е архивно копие; отделната версия изисква проверка.')
        else:reasons.append('Файлът не е получен; съдържанието му не е включено.')
        reasons.append('Само избрани откъси са включени в ограничената AI оценка.' if sent else 'Не е включен в ограничената AI оценка. Статусът на извличане е посочен отделно.')
        if group['bindings']:reasons.append('Налично архивно копие съответства на записаните идентификатор, размер и MD5; отделният пряк път не е изтеглен повторно. Това не потвърждава актуалност.')
        item['reason_bg']=' '.join(reasons)
        item['extraction_statuses']=sorted({r['extraction_status'] for r in acquired}) or ['NOT_ACQUIRED']
        item['unavailable_file']=not acquired and not any(r['content_available_from_observed_archive'] for r in records)
        states=set(item['extraction_statuses']);flags={f for r in acquired for f in r['flags']}
        item['processing_label_bg']=('Не е получен' if item['unavailable_file'] else
            'Налично архивно копие; версията е за проверка' if not acquired else
            'Получен; неподдържан формат' if 'UNSUPPORTED' in states else
            'Получен; нечетим/неуспешно обработен' if states & {'UNREADABLE','FAILED'} else
            'Получен; частично извлечен' if 'PARTIAL' in states else 'Получен; извлечен поддържан текст')
        item['ai_label_bg']='Само избрани откъси' if sent else 'Извън ограничената оценка'
        item['attention_bg']=('Няма използваемо съдържание; значението му остава неустановено.' if item['unavailable_file'] else
            'Вградените елементи на Word не са покрити изцяло.' if 'WORD_COMPLEX_CONTENT_NOT_FULLY_COVERED' in flags else
            'Формулите на стария XLS формат не са декодирани.' if 'XLS_BINARY_FORMULA_TEXT_NOT_DECODED' in flags else
            'Актуалността на кешираните стойности на формулите не е установена.' if 'FORMULA_CACHE_FRESHNESS_NOT_ESTABLISHED' in flags else
            'Форматът не се поддържа.' if 'UNSUPPORTED' in states else
            'Подредбата на текста и пълното покритие на таблиците не са потвърдени.' if 'PDF_UNRULED_TABLE_COVERAGE_NOT_ESTABLISHED' in flags else
            'Покритието на условията не е проверено.')
        if sourcekind in {'ANNOUNCEMENT','NOTICE'}:
            # Public structured notices stay in detailed audit, not a filename dump.
            if item['known_missing_essential_content']:display.append(item)
            continue
        if item['incomplete_processing'] or not sent or item['known_missing_essential_content']:display.append(item)

    # Explicit display-only variants distinguish names shared by different bytes/scope.
    names=Counter(PurePosixPath(i['filename'].replace('\\','/')).name for i in display)
    index=Counter()
    for item in display:
        name=PurePosixPath(item['filename'].replace('\\','/')).name;index[name]+=1
        item['display_name']=name+(f' — отделен източник {index[name]}' if names[name]>1 else '')
    return {'revision':'document-coverage-audit-v1','parent_tender':scope['parent_tender'],'selected_lot_ids':list(selected),
        'records':rows,'groups':list(groups.values()),'copy_bindings':bindings,'analyzed_documents':analyzed,
        'review_documents':display,'publication_records':publications,'export_containers':containers,'out_of_scope':excluded,
        'summary':{'source_records':len(rows),'logical_groups':len(groups),'duplicate_observations_collapsed':len(rows)-len(groups),
                   'archive_copy_reference_links':len(bindings),'review_rows':len(display),
                   'shared_incomplete_warning_applies_to':sum(i['incomplete_processing'] for i in display),
                   'unavailable_file_rows':sum(i['unavailable_file'] for i in display),
                   'documents_with_evaluated_excerpts':len(analyzed),
                   'publication_reference_groups':len(publications),'export_container_groups':len(containers)},
        'limitations_bg':['Развоен преглед на избрани откъси в ограничени категории, а не пълен анализ на документацията.',
            'Пълнотата на източниците, правната приложимост и значението на неполучените материали не са установени.',
            'Извлечен поддържан текст не означава независимо потвърдена пълнота на документа.'],
        'confirmed_missing_essential_occurrences':[r['occurrence'] for r in rows if r['confirmed_missing_essential_content']],
        'unknown_essentiality_is_not_nonessential':True,'sufficiency_unchanged':assessment['outcome'],
        'customer_publication_allowed':False,'whole_document_analysis_claim':False}
