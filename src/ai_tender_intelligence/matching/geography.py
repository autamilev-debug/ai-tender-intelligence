"""Versioned official NUTS-to-approved-oblast labels, no buyer-location inference."""
import re

MAPPING = 'BG-NUTS2024-TED1.15-NSI2026-verified-2026-10-28'
SOURCE = 'https://docs.ted.europa.eu/eforms/latest/reference/code-lists/nuts-bgr-lvl3.html'
NUTS = dict(zip(
    ('BG311','BG312','BG313','BG314','BG315','BG321','BG322','BG323','BG324','BG325',
     'BG331','BG332','BG333','BG334','BG341','BG342','BG343','BG344','BG411','BG412',
     'BG413','BG414','BG415','BG421','BG422','BG423','BG424','BG425'),
    ('Видин','Монтана','Враца','Плевен','Ловеч','Велико Търново','Габрово','Русе','Разград','Силистра',
     'Варна','Добрич','Шумен','Търговище','Бургас','Сливен','Ямбол','Стара Загора','София-град','София-област',
     'Благоевград','Перник','Кюстендил','Пловдив','Хасково','Пазарджик','Смолян','Кърджали')))


def geography(lot, regions):
    codes = set(lot.geography)
    areas = tuple(sorted({NUTS[c] for c in codes if c in NUTS}))
    # Exact codes establish an intersection, not completeness of a multi-area scope.
    if areas:
        overlap = tuple(a for a in areas if not regions or a in regions)
        if overlap:
            unresolved = codes - NUTS.keys() - {'BG', 'BGR'}
            partial = len(areas) > 1 or bool(unresolved)
            warnings = ('REGIONAL_AWARD_INDEPENDENCE_NOT_ESTABLISHED',) if partial else ()
            if unresolved:
                warnings += ('OTHER_PERFORMANCE_AREAS_NOT_RESOLVED',)
            return ('EXACT_OBLAST', overlap, 1 if partial else 0, warnings)
        return None
    # Require an explicitly labeled performance statement, not an occurrence in
    # buyer/capability/qualification prose or a bare country NUTS code.
    phrase = re.search(r'(?im)^\s*място на изпълнение\s*:\s*(цялата територия на (?:република )?българия|на територията на цялата страна)\s*[.\r\n]*$',
                       lot.description or '')
    if phrase and ('българия' in phrase[1].lower() or codes & {'BG','BGR'}):
        return ('EXPLICIT_NATIONWIDE', tuple(sorted(regions)), 1,
                ('REGIONAL_AWARD_INDEPENDENCE_NOT_ESTABLISHED',))
    # Country-only, broad NUTS, extra-regio and unknown values are not nationwide.
    return None
