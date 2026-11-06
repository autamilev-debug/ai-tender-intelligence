"""Deterministic coverage hints; not semantic completeness or lot-scope proof."""
import re


def coverage_guide(packet):
    rows=[]; conditions=[]
    for span in packet['spans']:
        if span['kind']=='TABLE':
            for row in span['rows']:
                cells=[{'column':c.get('locator',{}).get('column',c.get('column',i)),
                        'text':str(c.get('text',c.get('value','')))} for i,c in enumerate(row['cells'],1)]
                units=[c for c in cells if re.fullmatch(r'(?:бр\.?|броя|кг|kg|м[²³23]?|л|pcs\.?|units?)',c['text'].strip(),re.I)]
                numbers=[c for c in cells if re.fullmatch(r'\d+(?:[.,]\d+)?',c['text'].strip())]
                # Adjacent unit/value pairs only. A row's ordinal is not a quantity.
                quantities=[]
                for unit in units:
                    match=next((c for delta in (1,-1) for c in numbers if c['column']==unit['column']+delta),None)
                    if match and match not in quantities:quantities.append(match)
                warranties=[{'column':c['column'],'quote':line.strip()} for c in cells for line in c['text'].splitlines()
                            if re.search(r'гаранц|warrant',line,re.I) and re.search(r'\d',line) and len(line.strip())<=800]
                if units and quantities:
                    rows.append({'citation_id':span['citation_id'],'row':row['row'],
                                 'quantity_cells':quantities,'unit_cells':units,'warranty_clauses':warranties})
        elif span.get('text') and re.search(r'еквивалент|equivalent|останалите участници|other participants|индикатив',span['text'],re.I):
            conditions.append({'citation_id':span['citation_id'],'instruction':'Review full source wording and exceptions; preserve comparison population and indicative status.'})
    return {'table_rows':rows,'general_conditions':conditions,'scope':'CHECKLIST_NOT_LEGAL_APPLICABILITY_OR_COMPLETENESS_PROOF'}


def audit_coverage(data,packet):
    """Explain omissions in rows actually discussed, without inventing requirements."""
    issues=[]; guide=coverage_guide(packet); findings=data['findings']
    for row in guide['table_rows']:
        related=[f for f in findings if any(e['citation_id']==row['citation_id'] and e['table_row']==row['row'] for e in f['evidence'])]
        if not related:continue  # Unselected/uncertain rows are not universal obligations.
        for kind,items in [('QUANTITY',row['quantity_cells']),('UNIT',row['unit_cells']),('WARRANTY',row['warranty_clauses'])]:
            for cell in items:
                value=cell.get('quote',cell.get('text','')).strip()
                present=any(any(e['citation_id']==row['citation_id'] and e['table_row']==row['row'] and
                            e['table_column']==cell['column'] and value in e['quote'] for e in f['evidence']) and
                            (kind!='QUANTITY' or re.search(r'(?<!\d)'+re.escape(value)+r'(?!\d)',f['statement_bg'])) for f in related)
                if not present:issues.append({'kind':'ROW_'+kind+'_NOT_COVERED','citation_id':row['citation_id'],'row':row['row'],'column':cell['column']})
    if packet['category']=='TECHNICAL_SPECIFICATIONS':
        for span in packet['spans']:
            text=span.get('text') or ''
            if re.search(r'еквивалент',text,re.I) and re.search(r'ако изрично|unless',text,re.I):
                if not any('еквивалент' in f['statement_bg'].casefold() and re.search(r'изрично|освен|друго',f['statement_bg'],re.I)
                           and any(e['citation_id']==span['citation_id'] for e in f['evidence']) for f in findings):
                    issues.append({'kind':'GENERAL_CONDITION_EXCEPTION_NOT_COVERED','citation_id':span['citation_id']})
    for f in findings:
        quoted=' '.join(e['quote'] for e in f['evidence']).casefold()
        if 'останалите участници' in quoted and not re.search(r'останал|другите участници',f['statement_bg'],re.I):
            issues.append({'kind':'COMPARISON_POPULATION_QUALIFIER_NOT_PRESERVED','finding_id':f['finding_id']})
    return {'status':'GAPS_FLAGGED' if issues else 'NO_CHECKLIST_GAP_DETECTED','issues':issues,
            'semantic_completeness':'NOT_ESTABLISHED','legal_applicability':'NOT_ESTABLISHED'}
