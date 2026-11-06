"""Connect the approved single-column design to fresh, validated report content.

Summary cards quote whole contributions: no new factual synthesis or truncation.
The full report and its canonical finding/citation identities remain untouched.
"""
from copy import deepcopy


def attach(report):
    result=deepcopy(report)
    inputs=report['canonical_input'];lots=inputs['scope']['selected_lot_ids']
    labels={'DATES':'Срокове според документите','PARTICIPATION':'Ключови условия за участие',
            'FINANCE_CONTRACT':'Финансови условия','RISKS':'Приоритетни рискове'}
    summary=[]
    for key,label in labels.items():
        section=next((s for s in report['sections'] if s['key']==key),None)
        contributions=[c for p in section['paragraphs'] for c in p['contributions']] if section else []
        if contributions:
            c=contributions[0]
            summary.append({'label':label,'text_bg':c['text_bg'],'finding_ids':list(c['finding_ids'])})
    result['presentation']={
        'scope_notice':('Избрани официални позиции: '+', '.join(map(str,lots)) if lots else
            'Обхват: процедура без избрани дъщерни позиции според официалната проверка при приемане. Това не потвърждава приложимостта на всеки документ.'),
        'internal_notice':'Вътрешен MVP тест, не клиентска публикация. Анализът е ограничен до събраните доказателства и извлечения текст. Непълно извлечени документи, неустановена приложимост и евентуално противоречащи условия изискват преглед; задълженията не са правно потвърдени.',
        'executive_summary':summary}
    gaps=inputs.get('internal_review54',{}).get('excluded_findings',[])
    if inputs.get('internal_review54',{}).get('trusted_processing'):
        result['presentation']['internal_notice']+=' Успешното извличане на наличен текст не доказва пълно покритие на документа. PDF ограниченията на подредбата и таблиците се запазват отделно от частичното извличане. Избраният пакет не е пълен преглед; неполучените и непрочетените документи са посочени при източниците.'
    if gaps:
        result['presentation']['internal_notice']+=' Изключени непълни сведения: '+'; '.join(
            e['category']+' / '+e['finding_id']+' — '+e['reason'] for e in gaps)+'. Тези условия не са възстановени; анализът не е пълен.'
    return result
