"""Bounded views of existing extracted rows, never a parser or recalculator."""
from copy import deepcopy
import re
from .retrieval import digest
from ..extraction.models import canonical


def row_text(row):
    return ' '.join(str(c.get('text',c.get('value',''))) for c in row['cells'])


def windows(span,*,maximum_bytes=9300):
    rows=span['rows'];base={k:deepcopy(v) for k,v in span.items() if k!='rows'}
    # These are context candidates, not a certified column interpretation.
    marked=[r for r in rows if r.get('header_marked')]
    lexical=[r for r in rows if sum(bool(re.search(p,row_text(r),re.I)) for p in
        [r'наименован|описание|description|оборудване',r'количеств|quantity',r'мярка|единиц|unit',r'цена|стойност|price'])>=2]
    candidates=[]
    for row in marked+lexical[:4]+rows[:2]:
        if row not in candidates:candidates.append(row)
    headers=[]
    for row in candidates:
        if len(headers)<4 and len(canonical(headers+[row]))<=3500:headers.append(row)
    header_ids={r['row'] for r in headers};body=[r for r in rows if r['row'] not in header_ids]
    result=[];skipped=[];current=[]
    def make(data):
        selected=sorted(headers+data,key=lambda r:r['row'])
        metadata={'source_row_count':len(rows),'body_rows':[r['row'] for r in data],
                  'context_rows':[r['row'] for r in headers],
                  'header_status':'CONTEXT_CANDIDATES_NOT_INDEPENDENTLY_VERIFIED',
                  'original_header_status':span.get('table_header_status'),
                  'formula_policy':'ORIGINAL_EXTRACTED_VALUES_ONLY_NO_RECALCULATION_OR_INVENTED_RESULTS'}
        return {**deepcopy(base),'rows':deepcopy(selected),'table_window':metadata,
                'limitations':list(dict.fromkeys(base['limitations']+[
                    'TABLE_SPLIT_INTO_BOUNDED_ORIGINAL_ROW_WINDOWS','TABLE_WINDOW_NOT_COMPLETE_SHEET',
                    'TABLE_HEADER_CONTEXT_HEURISTIC_NOT_VERIFIED','TABLE_FORMULA_RESULTS_NOT_INFERRED']))}
    for row in body:
        trial=make(current+[row])
        if len(canonical(trial))<=maximum_bytes:current.append(row);continue
        if current:result.append(make(current));current=[]
        if len(canonical(make([row])))<=maximum_bytes:current=[row]
        else:skipped.append(row['row'])
    if current:result.append(make(current))
    for item in result:
        item['table_window'].update(omitted_oversized_row_count=len(skipped),omitted_rows_digest=digest(skipped),
            context_candidates_not_included=len(candidates)-len(headers))
        if skipped:item['limitations'].append('OVERSIZED_INDIVIDUAL_ROWS_OMITTED')
        if len(candidates)>len(headers):item['limitations'].append('TABLE_CONTEXT_CANDIDATES_BOUNDED')
    return result
