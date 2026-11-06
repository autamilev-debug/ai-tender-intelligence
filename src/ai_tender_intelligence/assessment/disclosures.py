"""Document-level disclosure data only; no report renderer or sufficiency override."""

HEADING = 'Документ с непълна обработка: {filename}'
EXPLANATION = 'Информацията от този документ не е извлечена изцяло и съответно не е напълно включена в анализа.'
ACTION = 'Моля, прегледайте оригиналния документ ръчно, за да се запознаете с цялото му съдържание.'


def document_disclosure(document):
    reference=document['reference']
    filename=document.get('original_filename') or reference['filename']
    base={'filename':filename,'source_reference':reference['official_reference'],
          'occurrence':document['occurrence'],'essentiality':document.get('essentiality','NOT_ESTABLISHED'),
          'changes_sufficiency':False,'report_completion_authorized':False}
    state=document['status']
    if state=='NOT_ACQUIRED':
        return {**base,'kind':'NOT_ACQUIRED','heading_bg':None,'explanation_bg':None,'action_bg':None}
    if state in {'PARTIAL','UNSUPPORTED','UNREADABLE','FAILED'}:
        return {**base,'kind':'INCOMPLETE_PROCESSING','heading_bg':HEADING.format(filename=filename),
                'explanation_bg':EXPLANATION,'action_bg':ACTION}
    if state in {'INVALID_SOURCE','TECHNICAL_INTEGRITY_FAILURE','NOT_ASSESSED_BOUND'}:
        return {**base,'kind':'UNPROCESSED_OR_INVALID_SOURCE','heading_bg':None,'explanation_bg':None,'action_bg':None}
    return {**base,'kind':'NO_INCOMPLETE_PROCESSING_WARNING','heading_bg':None,'explanation_bg':None,'action_bg':None}
