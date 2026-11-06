"""Bounded label discovery. Negation and context remain visible, never interpreted as absence."""
import re
from copy import deepcopy

LABELS={
 'GENERAL_PROCUREMENT':r'\b(?:възложител\w*|предмет|обществен\w*\s+поръч\w*|contracting authority|procurement subject)\b',
 'TECHNICAL_SPECIFICATIONS':r'\b(?:техническ\w*\s+(?:спецификац\w*|изискван\w*|предложен\w*|услов\w*)|обхват\w*|technical specifications?|scope of work)\b',
 'PARTICIPATION_QUALIFICATION':r'\b(?:участни\w*|критерии\s+за\s+подбор|квалификац\w*|qualification|participation)\b',
 'SUBMISSION_DEADLINES':r'\b(?:подаван\w*|представян\w*\s+на\s+оферт\w*|краен\s+срок|дата\s+и\s+час|submission|deadline)\b',
 'EVALUATION_METHODOLOGY':r'\b(?:методик\w*|оценяван\w*|критер\w*\s+за\s+възлаган\w*|evaluation|award criteria)\b',
 'FINANCIAL_CONDITIONS':r'\b(?:гаранци\w*|плащан\w*|финансов\w*\s+услов\w*|guarantee\w*|payment\w*|financial conditions?)\b',
 'CONTRACT_OBLIGATIONS':r'\b(?:договор\w*|задължен\w*|contract conditions?|obligations?)\b',
 'CLARIFICATIONS_AMENDMENTS':r'\b(?:разяснени\w*|изменени\w*|уточнени\w*|clarification\w*|amendment\w*)\b'}
PATTERNS={k:re.compile(v,re.I) for k,v in LABELS.items()}
NEGATION=re.compile(r'\b(?:не|няма|без|not|no|without)\b',re.I)


def fragments(block):
    if block['kind']!='TABLE':
        if isinstance(block.get('text'),str):yield block['text'],None
        return
    for row in block.get('rows',[]):
        for cell in row.get('cells',[]):
            text=cell.get('text',cell.get('value'))
            if text is not None:
                yield str(text),cell.get('locator',{'table_row':row['row'],'table_column':cell.get('column')})


def locate(document):
    result={k:[] for k in LABELS};truncated=set()
    source=document['source']
    for index,block in enumerate(document['blocks']):
        for text,cell in fragments(block):
            for category,pattern in PATTERNS.items():
                match=pattern.search(text)
                if not match:continue
                if len(result[category])>=8:
                    truncated.add(category);continue
                result[category].append({'occurrence':source['occurrence'],'source_sha256':source['sha256'],
                    'extractor_sha256':source['extractor_sha256'],'block_index':index,
                    'locator':deepcopy(block['locator']),'cell_locator':deepcopy(cell),
                    'kind':block['kind'],'matched_label':match[0][:120],
                    'excerpt':text[max(0,match.start()-60):max(0,match.start()-60)+240],
                    'excerpt_truncated':match.end()+160>max(0,match.start()-60)+240,
                    'contains_negation':bool(NEGATION.search(text)),
                    'semantic_status':'UNRESOLVED','basis':'DETERMINISTIC_LABEL_CANDIDATE_ONLY'})
    return result,sorted(truncated)
