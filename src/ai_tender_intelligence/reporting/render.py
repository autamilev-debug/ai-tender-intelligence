"""Escaped standalone Bulgarian preview; no active content or email delivery."""
from datetime import datetime
from html import escape
from urllib.parse import urlsplit,urlunsplit
from zoneinfo import ZoneInfo
from ..semantic.contract import SemanticError
from ..assessment.disclosures import EXPLANATION,ACTION

DISCLAIMER=('Този анализ използва официално достъпната информация, събрана през посочените периоди (Europe/Sofia). '
 'Възложителят може да променя поръчката и документацията по време на събирането и обработката или след тях; '
 'такива промени може да не са отразени в анализа. Системата не гарантира непрекъснато актуална информация и '
 'не удостоверява състоянието на поръчката към момента на заявката. За актуализирана информация е необходимо ново търсене и нова заявка за анализ.')

GAP_TEXT={
 'ROW_QUANTITY_NOT_COVERED':'Количеството в обсъдения ред не е запазено изцяло в констатациите.',
 'ROW_UNIT_NOT_COVERED':'Липсва цитирана мерна единица за обсъдения ред.',
 'ROW_WARRANTY_NOT_COVERED':'Гаранционният срок в обсъдения ред не е включен изцяло.',
 'GENERAL_CONDITION_EXCEPTION_NOT_COVERED':'Общото условие за еквивалентност и неговото изключение не са включени изцяло.',
 'COMPARISON_POPULATION_QUALIFIER_NOT_PRESERVED':'Формулировката за ценовото сравнение не запазва уточнението за останалите участници.'}

def safe_url(url):
    try:p=urlsplit(url or '')
    except ValueError:return None
    if p.scheme!='https' or p.hostname not in {'app.eop.bg','ted.europa.eu'} or p.username or p.password:return None
    return urlunsplit((p.scheme,p.netloc,p.path,'',''))

def link(url,label):
    url=safe_url(url)
    return f'<a href="{escape(url,quote=True)}">{escape(str(label))}</a>' if url else escape(str(label))

def stamp(value):
    if not value:return 'НЕУСТАНОВЕН'
    try:
        d=datetime.fromisoformat(value)
        if d.tzinfo is None:return 'НЕУСТАНОВЕН (липсва часова зона)'
        return d.astimezone(ZoneInfo('Europe/Sofia')).isoformat()
    except (ValueError,TypeError):return 'НЕУСТАНОВЕН'


def coverage_html(report):
    audit=report.get('document_coverage')
    h=['<h2>9. Обхват и ограничения на анализа</h2>','<h3>Анализирани документи</h3>']
    if audit is None:
        # Legacy preview inspection cannot reconstruct source identity from names.
        # Keep distinct observations, disclose the missing audit, never certify it.
        rows=[{'display_name':d['filename'],'source_reference':d['source_reference'],
               'reason_bg':'Файлът не е получен.' if d['kind']=='NOT_ACQUIRED' else EXPLANATION,
               'processing_label_bg':'Не е получен' if d['kind']=='NOT_ACQUIRED' else 'Непълна обработка',
               'ai_label_bg':'Неустановен обхват','attention_bg':'Файлът не е получен.' if d['kind']=='NOT_ACQUIRED' else 'Прегледайте оригиналния документ.',
               'scope_note_bg':'Обхватът не е установен в този стар преглед.',
               'incomplete_processing':d['kind']=='INCOMPLETE_PROCESSING'}
              for d in report['document_disclosures'] if d['kind'] in {'INCOMPLETE_PROCESSING','NOT_ACQUIRED','UNPROCESSED_OR_INVALID_SOURCE'}]
        analyzed=[];limitations=['Този стар преглед няма подробен одит на документния обхват; пълнота не е потвърдена.'];publications=[]
    else:
        rows=audit['review_documents'];analyzed=audit['analyzed_documents'];limitations=audit['limitations_bg'];publications=audit['publication_records']
    if analyzed:
        h.append('<ul>')
        for d in analyzed:
            mode=' Симулация на допълнената оценка; отделни по-ранни реални отговори.' if set(d.get('model_modes',[]))=={'LIVE','MOCK_ONLY'} else ' Симулация, не реален нов отговор на модел.' if 'MOCK_ONLY' in d.get('model_modes',[]) else ''
            h.append('<li>'+link(d['source_reference'],d['filename'])+' — предоставени и оценени са '+str(d['span_count'])+
                     ' избрани откъса.'+mode+' '+escape(d['scope_note_bg'])+'</li>')
        h.append('</ul>')
    else:h.append('<p>Не е установен документен обхват на оценените откъси в този запис.</p>')
    h.append('<p>Оценката обхваща само посочените откъси в ограничени категории. Предоставяне на откъс не означава анализ на целия документ или независимо установена точност.</p>')
    h.append('<h3>Документи, изискващи допълнителна проверка</h3>')
    if rows:
        h.append('<table><thead><tr><th>Документ</th><th>Получаване и извличане</th><th>AI обхват</th><th>За проверка</th></tr></thead><tbody>')
        for d in rows:
            h.append('<tr><td>'+link(d['source_reference'],d['display_name'])+'<small>'+escape(d['scope_note_bg'])+'</small></td><td>'+
                     escape(d.get('processing_label_bg',d['reason_bg']))+'</td><td>'+escape(d.get('ai_label_bg','Неустановен обхват'))+
                     '</td><td>'+escape(d.get('attention_bg',d['reason_bg']))+'</td></tr>')
        h.append('</tbody></table>')
    else:h.append('<p>В този запис няма отделно идентифицирани файлове за проверка; това не удостоверява пълнота на документацията.</p>')
    if any(d['incomplete_processing'] for d in rows):
        h.append('<p><strong>За файловете, обозначени с непълно извличане или неподдържан формат:</strong> '+escape(EXPLANATION)+'</p><p><strong>'+escape(ACTION)+'</strong></p>')
    if audit and audit['copy_bindings']:
        h.append('<p>За част от файловете има налични копия в официалните архиви, съпоставени със запазените метаданни на пряката препратка. Те не са отделни липсващи документи. Това съпоставяне не потвърждава актуалността или историческата приложимост на версията.</p>')
    if publications:h.append('<p>Публикациите в официалния регистър не са проверени изцяло като съдържание. Те не са преброени като отделни липсващи файлове; възможни промени и тяхното значение остават за проверка.</p>')
    h.append('<h3>Ограничения на анализа</h3><ul>')
    not_established=[s['title_bg'] for s in report.get('sections',[]) if not any(f['validated_evidence'] for f in s['findings'])]
    if not_established:
        h.append('<li>Без достатъчно оценени доказателства в този преглед: '+escape('; '.join(not_established))+
                 '. Това не означава, че тези категории нямат приложими условия.</li>')
    for value in limitations:h.append('<li>'+escape(value)+'</li>')
    if audit and not audit['confirmed_missing_essential_occurrences']:
        h.append('<li>Няма потвърден списък на липсващи задължителни документи; това не доказва, че съществени пропуски липсват. Пълнотата и приложимостта остават неустановени.</li>')
    if audit and audit['confirmed_missing_essential_occurrences']:
        h.append('<li><strong>Потвърдена е липса на съществено доказателство. Завършен клиентски доклад е блокиран.</strong></li>')
    issues={issue['kind'] for check in report['coverage']['checks'] for issue in check['issues']}
    for issue in sorted(issues):h.append('<li>'+escape(GAP_TEXT.get(issue,'Остава неуточнено покритие на източниците.'))+'</li>')
    h.append('</ul><p><strong>Това е непълен развоен преглед и не може да бъде публикуван като завършен клиентски анализ.</strong></p>')
    return h

def html(report):
    if report.get('status')!='DEVELOPMENT_PREVIEW' or report.get('publishable') is not False:raise SemanticError('PREVIEW_RENDERER_ONLY')
    h=['<!doctype html><html lang="bg"><meta charset="utf-8"><title>Развоен преглед на поръчка</title>',
       '<style>body{font:17px/1.6 system-ui;max-width:1000px;margin:30px auto;padding:20px;color:#172338}h1,h2{line-height:1.25}aside{background:#fff0ce;padding:18px;border-left:5px solid #ae6900}article{border-bottom:1px solid #ddd;padding:12px 0}small{display:block}code{overflow-wrap:anywhere}table{border-collapse:collapse;width:100%}td,th{border:1px solid #ddd;padding:10px;text-align:left;vertical-align:top;overflow-wrap:anywhere}</style><body>',
       '<h1>Развоен преглед — непълен анализ</h1><aside><strong>САМО ЗА РАЗРАБОТКА — НЕ Е ГОТОВ КЛИЕНТСКИ ДОКЛАД.</strong> '
       'Доказателствата са непълни. Това не е проверен изчерпателен анализ. Необходим е ръчен преглед на оригиналната документация. '
       'Не е установена правната приложимост на неуточнените констатации. Те не са задължения за избрания лот.</aside>',
       '<p>Синтез: '+escape(report['generation']['execution_mode'])+'; '+escape(report['generation']['model'])+' / Medium. '
       'При MOCK_ONLY организацията е симулирана и не доказва качество на Sol. Произходът на всяка Luna констатация е посочен отделно.</p>',
       '<p>ЕОП '+str(report['identity']['parent_tender'])+'; избрани лотове: '+escape(str(report['scope']['selected_lot_ids']))+
       '; общ брой лотове: неустановен в този вход.</p>']
    if report.get('development_context_bg'):h.append('<aside>'+escape(report['development_context_bg'])+'</aside>')
    for i,section in enumerate(report['sections'],1):
        if not any(f['validated_evidence'] for f in section['findings']):continue
        h.append(f'<h2>{i}. {escape(section["title_bg"])}</h2>')
        for f in section['findings']:
            h.append('<article><strong>'+escape(f['statement_bg'])+'</strong>')
            state={'SUPPORTED':'подкрепено от цитирания откъс','NOT_ESTABLISHED':'неустановено','CONTRADICTED':'опровергано от цитирания откъс','UNRESOLVED':'неизяснено'}[f['verification_state']]
            scope={'UNRESOLVED':'неустановена','PROCEDURE':'проверена за процедурата','SELECTED_LOTS':'проверена за посочените лотове'}[f['applicability']]
            origin='предходен реален отговор' if f['luna_execution_mode']=='LIVE' else 'референтен MOCK — не е реален отговор'
            h.append('<small>Документално наблюдение; приложимост: '+scope+'; състояние: '+state+'; Luna: '+origin+'</small>')
            for key in ['missing_evidence','contradictions']:
                for value in f[key]:h.append('<p>'+escape(value)+'</p>')
            for e in f['validated_evidence']:
                s=e['source'];loc='блок '+str(e['block_index'])
                if e['table_row'] is not None:loc+=f'; ред {e["table_row"]}, колона {e["table_column"]}'
                h.append('<blockquote>'+escape(e['quote'])+'</blockquote><small>'+link(s['official_reference'],s['filename'])+' — '+escape(loc)+
                         '<details><summary>Идентификатор на оригиналния документ</summary>SHA-256: <code>'+escape(e['source_sha256'])+'</code></details></small>')
            h.append('</article>')
    h.extend(coverage_html(report))
    h.append('<h2>10. Източници и ограничения на анализа</h2>')
    for s in report['sources']:h.append('<p>'+link(s['official_reference'],s['filename'])+'</p>')
    h.append('<p>Периоди на събиране (не момент на заявката; повторната употреба не е ново изтегляне):</p>')
    for period in report['content_provenance'].get('collection_intervals',[]):h.append('<p>'+escape(stamp(period.get('start')))+' — '+escape(stamp(period.get('end')))+'</p>')
    if not report['content_provenance'].get('collection_intervals'):h.append('<p>Период на събиране: НЕУСТАНОВЕН — не се замества с час на синтез или заявка.</p>')
    h.append('<p><strong>Информационна граница:</strong> Фактически преглед, а не правен съвет, препоръка за участие или установена допустимост.</p>')
    h.append('<p>'+escape(DISCLAIMER)+'</p></body></html>')
    return '\n'.join(h)
