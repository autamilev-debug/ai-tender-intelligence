"""P43 email styling with dynamic validated content; no tender-specific edits."""
from html import escape
import re
from . import presentation42 as style,validation40_offline as validation,synthesis36
from .render import safe_url,stamp,DISCLAIMER
from ..classification.contract import Reply
from ..extraction.models import canonical

BETA_TITLE='БЕТА ВЕРСИЯ — ДЕМОНСТРАЦИОНЕН АНАЛИЗ'
BETA_NOTICE=(
    'Този доклад е генериран автоматично и има демонстрационна цел. Системата не е преминала цялостно тестване върху представителна извадка от обществени поръчки. Възможни са пропуски, неточности и непълна информация поради ограничения в извличането на документи и автоматичния анализ.',
    'Преди вземане на решения проверете оригиналната документация.',
)


def render_mail(report):
    inputs=report['canonical_input']
    data={k:report[k] for k in validation.Synthesis.model_fields}
    if inputs.get('beta59'):
        from .beta65 import technical_validate
        technical_validate(Reply(canonical(data)),inputs)
    else:
        validation.validate(Reply(canonical(data)),inputs)
        from ..semantic.internal54 import report_guard
        report_guard(data,inputs)
    if report['publishable'] is not False:raise ValueError('RUNTIME_PUBLICATION_BLOCKED')
    font='font:16px/26px Arial,Helvetica,sans-serif;color:#27364b;'
    refs={};by={f['qualified_finding_id']:f for f in inputs['findings']};links={}
    for fid,f in by.items():
        links[fid]=[]
        for e in f['validated_evidence']:
            key=(e['occurrence'],e['source_sha256'],e['extractor_sha256'])
            if key not in refs:refs[key]={'number':len(refs)+1,'evidence':e,'locators':[]}
            ref=refs[key];locator=e.get('locator') or {}
            loc=('стр. '+str(locator['page']) if locator.get('page') else
                 'лист '+str(locator.get('sheet',''))+', ред '+str(e['table_row'])+', колона '+str(e['table_column'])
                 if e.get('table_row') is not None else 'раздел в оригиналния документ')
            if loc not in ref['locators']:ref['locators'].append(loc)
            links[fid].append(ref['number'])
    def p(text):return '<p style="'+font+'margin:0 0 14px;">'+escape(text)+'</p>'
    presentation=report.get('presentation') or {}
    lead=tuple(presentation.get('lead_contribution',()))
    def citations(ids):
        numbers=sorted({n for fid in ids for n in links[fid]})
        return ' '.join('<a style="color:#165aab;" href="#source-'+str(n)+'">['+str(n)+']</a>' for n in numbers)
    def display_text(text,section):
        if section=='OVERVIEW' and text.startswith('Здравейте, '):
            text=text[len('Здравейте, '):];text=text[:1].upper()+text[1:]
        return text
    def block(c,section):
        text=display_text(c['text_bg'],section)
        # Whitespace-only visual segmentation. Keep punctuation, requirements
        # and qualifications together in their original contribution block.
        chunks=[]
        for piece in re.split(r'(?<=[.!?])\s+(?=[А-Я])|(?<=;)\s+',text):
            if chunks and re.search(r'\b(?:с|гр|общ|чл|ал|т|г)\.$',chunks[-1],re.I):chunks[-1]+=' '+piece
            else:chunks.append(piece)
        return ('<div style="margin:0 0 20px;padding:0 0 16px;border-bottom:1px solid #e3e9ef;">'
                +''.join(p(chunk) for chunk in chunks)
                +'<p style="'+font+'margin:0;color:#52647a;">'+citations(c['finding_ids'])+'</p></div>')
    title='Анализ на обществена поръчка '+str(inputs['identity']['parent_tender'])
    html=['<!doctype html><html lang="bg"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>'+escape(title)+'</title><style>@media screen and (max-width:600px){.pad{padding-left:16px!important;padding-right:16px!important}th,td{word-wrap:break-word}}</style></head><body style="margin:0;background:#edf2f6;'+font+'overflow-wrap:break-word;word-wrap:break-word;"><table role="presentation" width="100%" style="border-collapse:collapse;table-layout:fixed;"><tr><td align="center" style="padding:20px 0;"><table role="presentation" width="100%" bgcolor="#ffffff" style="width:100%;max-width:720px;table-layout:fixed;border-collapse:collapse;">',
        '<tr><td style="height:5px;background:#0b6472;"></td></tr><tr><td class="pad" style="padding:28px;'+font+'">'+('' if inputs.get('beta59') else p('ВЪТРЕШЕН ТЕСТ · РАЗВОЕН ПРЕГЛЕД'))+'<h1 style="font: bold 28px/36px Arial,sans-serif;color:#132d46;">'+escape(title)+'</h1>']
    if inputs.get('beta59'):
        # Presentation only. Processing/exclusion details remain unchanged in
        # canonical_input and terra_review62; never render them as email content.
        notice='<div style="background:#fff2cc;border:2px solid #bb8610;padding:18px;margin:0 0 22px;"><p style="'+font+'font-weight:bold;margin:0 0 14px;">'+escape(BETA_TITLE)+'</p>'+''.join(p(t) for t in BETA_NOTICE)+'</div>'
        marker='<h1 style='
        pos=html[-1].index(marker)
        html[-1]=html[-1][:pos]+notice+html[-1][pos:]
    if presentation.get('title'):html.append(p(presentation['title']))
    if inputs.get('beta59'):
        lots=inputs['scope']['selected_lot_ids']
        html.append(p('Обхват на анализа: '+('избрани позиции '+', '.join(map(str,lots)) if lots else 'процедурата')+'.'))
    else:
        html.append(p(presentation.get('scope_notice') or ('Избрани официални позиции: '+', '.join(map(str,inputs['scope']['selected_lot_ids'])))))
    if presentation.get('internal_notice') and not inputs.get('beta59'):html.append(p(presentation['internal_notice']))
    if lead:
        section=next(s for s in data['sections'] if s['key']==lead[0])
        c=section['paragraphs'][lead[1]]['contributions'][lead[2]]
        html.append('<div style="background:#fff6df;border-left:4px solid #d4a840;padding:16px;margin:20px 0;">'+p('Обща уговорка за документите')+p(c['text_bg'])+citations(c['finding_ids'])+'</div>')
    elif not inputs.get('beta59'):html.append(p('Приложимостта и достатъчността изискват потвърждение. Параметри с неустановена приложимост не са потвърдени задължения за избраната позиция. Не е разрешена клиентска публикация.'))
    summary=presentation.get('executive_summary',[])
    if summary:
        html.append('<h2 style="font:bold 24px/32px Arial,sans-serif;color:#132d46;">Най-важното за решението за участие</h2>')
        for item in summary:
            html.append('<div style="background:#f3f6f9;padding:14px 16px;margin:0 0 12px;border-left:3px solid #0b6472;">'
                        +'<p style="'+font+'font-weight:bold;margin:0 0 8px;">'+escape(item['label'])+'</p>'+p(item['text_bg'])+citations(item['finding_ids'])+'</div>')
    html.append('</td></tr>')
    for number,(section,(_,heading)) in enumerate(zip(data['sections'],synthesis36.SECTIONS),1):
        html.append('<tr><td class="pad" style="padding:26px 28px;border-top:1px solid #e3e9ef;'+font+'"><p style="color:#0b6472;font-weight:bold;">'+str(number).zfill(2)+'</p><h2 style="font:bold 24px/32px Arial,sans-serif;color:#132d46;">'+escape(heading)+'</h2>')
        for pi,paragraph in enumerate(section['paragraphs']):
            for ci,c in enumerate(paragraph['contributions']):
                key=(section['key'],pi,ci)
                if key==lead:continue  # Displayed once near the beginning.
                note=presentation.get('notes',{}).get(':'.join(map(str,key)))
                if note and not inputs.get('beta59'):html.append('<div style="background:#fff6df;padding:14px 16px;margin:0 0 12px;">'+p(note)+'</div>')
                html.append(block(c,section['key']))
        if section['key']=='SOURCES':
            coverage=inputs.get('document_coverage') or {}
            gaps=[d['display_name'] for d in coverage.get('review_documents',[]) if d.get('incomplete_processing') or d.get('unavailable_file')]
            if gaps and not inputs.get('beta59'):html.append(p('Необходимо е преглеждане на непълни или неполучени документи: '+'; '.join(gaps)))
            for ref in refs.values():
                e=ref['evidence'];url=safe_url(e['source'].get('official_reference'))
                label='['+str(ref['number'])+'] '+e['source']['filename']
                html.append('<p id="source-'+str(ref['number'])+'" style="'+font+'word-break:break-word;">'+('<a style="color:#165aab;" href="'+escape(url,quote=True)+'">'+escape(label)+'</a>' if url else escape(label))+' — '+escape('; '.join(ref['locators']))+'</p>')
            for period in inputs['content_provenance']['collection_intervals']:
                html.append(p('Събиране: '+stamp(period['start'])+' — '+stamp(period['end'])))
            if not inputs.get('beta59'):html.append(p(DISCLAIMER))
        html.append('</td></tr>')
    html.append('</table></td></tr></table></body></html>')
    html='\n'.join(html);reader=style._PlainEmail();reader.feed(html);reader.close()
    plain=re.sub(r'\n{3,}','\n\n',''.join(reader.parts)).strip()+'\n'
    return {'html':html,'text':plain}
