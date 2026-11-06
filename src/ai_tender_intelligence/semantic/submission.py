"""Source-driven submission coverage candidates, not legal or semantic proof."""
import re
from .retrieval import digest

# Generic terminology, never tender IDs, block numbers or expected case answers.
TOPICS={
 'TECHNICAL_PROPOSAL':r'техническ\w*\s+предложен\w*|technical\s+(?:proposal|offer)',
 'SUPPORTING_MATERIALS':r'катало[гз]\w*|брошур\w*|техническ\w*\s+(?:материал\w*|документац\w*)|(?:придружаващ|съпътстващ)\w*\s+документ\w*|catalog\w*|brochure\w*|supporting\s+document\w*|technical\s+documentation',
 'PAGE_REFERENCES':r'страниц\w*|page\s+(?:number|reference)\w*',
 'LANGUAGE':r'български\s+език|на\s+български|език\w*|language|translation|превод\w*',
 'PRICE_RESTRICTION':r'(?:без|не\s+се|забран\w*)[^.!?\n]{0,45}цен\w*|цени[^.!?\n]{0,45}(?:не|забран)|without\s+prices?|no\s+prices?',
 'ENVIRONMENTAL_EVIDENCE':r'еколог\w*|еко\w*маркиров\w*|environment\w*|ecolabel\w*',
 'DELIVERY_DECLARATION':r'срок\w*\s+(?:за|на)\s+доставк\w*|delivery\s+(?:period|time|declaration)',
 'DECLARATIONS':r'деклар\w*|declar\w*',
 'SUBMISSION_DATES':r'подаван\w*\s+на\s+оферт\w*|submit\w*|submission\s+(?:date|deadline)',
 'OPENING_DATES':r'отварян\w*|opening\s+(?:date|time|of)',
 'OFFICIAL_TIMESTAMPS':r'експорт\w*|времев\w*\s+печат|електрон\w*\s+подпис|export\w*|timestamp\w*',
 'NOTIFICATION_PROCESS':r'уведом\w*|връчен\w*|връчван\w*|notification|receipt',
}
NEGATION=r'\bне\b|\bбез\b|забран\w*|\bnot\b|\bwithout\b|\bno\b'
CONDITION=r'\bако\b|\bосвен\b|\bкогато\b|считано|регистриран\w*|\bunless\b|\bif\b|\bprovided\b'

def units(packet):
    for s in packet['spans']:
        if s['kind']=='TABLE':
            for r in s['rows']:
                for i,c in enumerate(r['cells'],1):
                    yield s,r['row'],c.get('locator',{}).get('column',c.get('column',i)),str(c.get('text',c.get('value','')))
        else:yield s,None,None,s.get('text','')

def guide(packet):
    candidates=[]
    if packet['category']=='SUBMISSION_DEADLINES':
        for s,row,col,text in units(packet):
            if not text.strip():continue
            # The supplied source itself is the context; never infer an obligation
            # merely because some other tender uses this topic.
            for topic,pattern in TOPICS.items():
                if not re.search(pattern,text,re.I):continue
                ref={k:s['source'][k] for k in ['occurrence','source_sha256','extractor_sha256']}
                candidate={'topic':topic,'citation_id':s['citation_id'],'block_index':s['block_index'],
                    'table_row':row,'table_column':col,'source':ref,'source_text_sha256':digest(text),
                    'negation_present':bool(re.search(NEGATION,text,re.I)),
                    'condition_present':bool(re.search(CONDITION,text,re.I)),
                    'interpretation':'SOURCE_TOPIC_CANDIDATE_NOT_CONFIRMED_OBLIGATION',
                    'applicability':s['source']['legal_applicability']}
                # Some saved PDF tables duplicate a supplied text block. Permit
                # that exact same-source row evidence without a duplicate task.
                equivalents=[]
                if row is not None:
                    normalize=lambda value:re.sub(r'\s+',' ',str(value)).strip()
                    values=[normalize(cell.get('text',cell.get('value',''))) for r in s['rows'] if r['row']==row for cell in r['cells']]
                    values=[v for v in values if v]
                    equivalents=[other['citation_id'] for other in packet['spans'] if other['kind']!='TABLE'
                        and all(other['source'][k]==s['source'][k] for k in ['occurrence','source_sha256','extractor_sha256'])
                        and values and all(v in normalize(other.get('text','')) for v in values)]
                candidate['equivalent_same_source_text_citations']=equivalents
                candidate['candidate_id']='submission-'+digest(candidate)
                candidates.append(candidate)
    return {'revision':'submission-source-topics-v1','candidates':candidates,
        'unobserved_topics':[t for t in TOPICS if not any(c['topic']==t for c in candidates)],
        'unobserved_meaning':'NOT_OBSERVED_IN_SUPPLIED_EVIDENCE_NOT_PROVEN_ABSENT',
        'groups':{'PROCESS':['SUBMISSION_DATES','OPENING_DATES','OFFICIAL_TIMESTAMPS','NOTIFICATION_PROCESS'],
                  'BID_DOCUMENTS':[t for t in TOPICS if t not in {'SUBMISSION_DATES','OPENING_DATES','OFFICIAL_TIMESTAMPS','NOTIFICATION_PROCESS'}]}}

def audit(data,packet):
    g=guide(packet);checks=[]
    for c in g['candidates']:
        related=[f for f in data['findings'] if any((e['citation_id']==c['citation_id'] and
            e['table_row']==c['table_row'] and (c['table_row'] is not None or e['table_column']==c['table_column']))
            or (e['citation_id'] in c['equivalent_same_source_text_citations'] and e['table_row'] is None and e['table_column'] is None)
            for e in f['evidence'])]
        represented=[f for f in related if re.search(TOPICS[c['topic']],f['statement_bg'],re.I)]
        combined=' '.join(f['statement_bg'] for f in related)
        qualifiers=represented if (not c['negation_present'] or re.search(NEGATION,combined,re.I)) and (not c['condition_present'] or re.search(CONDITION,combined,re.I)) else []
        status='TOPIC_REPRESENTED_NOT_SEMANTICALLY_CERTIFIED' if qualifiers else 'INTERPRETATION_MISSING' if not represented else 'QUALIFIER_REVIEW_REQUIRED'
        checks.append({**c,'status':status,'finding_ids':[f['finding_id'] for f in qualifiers],
            'citation_support_relation':'SAME_SOURCE_TABLE_ROW' if c['table_row'] is not None else 'EXACT_SUPPLIED_TEXT_SPAN',
            'gap_type':None if qualifiers else 'EVIDENCE_PRESENT_INTERPRETATION_GAP',
            'legal_obligation_confirmed':False})
    gaps=[c for c in checks if c['gap_type']]
    return {'revision':g['revision'],'status':'GAPS_FLAGGED' if gaps else 'NO_DETECTED_TOPIC_GAP_NOT_COMPLETENESS_PROOF',
        'checks':checks,'gaps':gaps,'unobserved_topics':g['unobserved_topics'],'semantic_completeness':'NOT_ESTABLISHED',
        'can_label_submission_complete':False,'legal_applicability':'NOT_ESTABLISHED',
        'limitation':'Lexical source-topic/citation/qualifier checks can miss paraphrases and cannot prove entailment, full original-document coverage or legal applicability.'}
