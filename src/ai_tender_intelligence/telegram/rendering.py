"""Bulgarian presentation; HTML values are escaped, continuations stay literal."""

from datetime import datetime, timedelta
from html import escape
import re
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from .models import ProfileFields, Tender

WELCOME = ("Намерете обществени поръчки според дейността и районите на бизнеса си. "
           "Създайте бизнес профил, за да търсите подходящи възможности и да заявявате "
           "анализ на документацията. Анализът описва условията на поръчката и не "
           "определя дали фирмата Ви отговаря на тях.")
REGIONAL_WARNING = ("ℹ️ Поръчката обхваща няколко области или цялата страна. Отделна оферта "
                    "или възлагане за избраната област не са проверени. Това се установява "
                    "при подробния анализ.")
STATUSES = {"OPEN": "Отворена", "SUSPENDED": "Спряна ОП", "EXPIRED": "Срокът е изтекъл",
            "CANCELLED": "Прекратена", "AWARDED": "Възложена", "UNKNOWN": "Статусът не е установен"}

STATUSES.update({
    'SUSPENSION_REPORTED_AS_PUBLISHED': 'Публикувано спиране — текущият статус изисква потвърждение',
    'ACTIVE_AS_PUBLISHED': 'Активна според последната публикувана информация в TED',
    'EXPIRED_DEADLINE_AS_PUBLISHED': 'Срокът е изтекъл според публикуваните данни',
    'CANCELLED_AS_PUBLISHED': 'Публикувано прекратяване',
    'CLOSED_AS_PUBLISHED': 'Публикувано приключване',
    'AWARDED_AS_PUBLISHED': 'Публикувано възлагане',
    'CONFLICTING': 'Противоречиви данни — статусът изисква потвърждение',
    'REQUIRES_CONFIRMATION': 'Статусът изисква потвърждение',
})


def date_text(value: datetime | None) -> str:
    return value.astimezone(ZoneInfo("Europe/Sofia")).strftime("%d.%m.%Y, %H:%M") if value else "Срокът не е установен"


def profile_review(fields: ProfileFields) -> str:
    return (f"🏢 Преглед на бизнес профила\n\nИме: {fields.name}\n📨 Имейл: {fields.email}\n\n"
            + "\n\n".join(f"📋 {category}\n{activity}" for category, activity in fields.activities)
            + "\n\n📍 Райони: " + (", ".join(sorted(fields.regions)) or "Цяла България"))


def profile_review_parts(fields: ProfileFields) -> tuple[tuple[str, bool], ...]:
    """Bold only the bounded name; split literal content before HTML escaping.

    Continuations remain plain text. Neither an HTML tag nor an entity can be
    split across messages, even for the longest permitted activity input.
    """
    body = profile_review(fields).split("\n📨 Имейл:", 1)[1]
    parts = plain_chunks("📨 Имейл:" + body, max_units=3200)
    heading = f"🏢 Преглед на бизнес профила\n\n🏢 <b>{escape(fields.name)}</b>\n\n"
    return ((heading + escape(parts[0]), True),) + tuple((part, False) for part in parts[1:])


def plain_chunks(text: str, max_units: int = 3500) -> tuple[str, ...]:
    """Keep a long two-activity review intact across safe plain-text messages.

    Count astral characters conservatively as two units, leaving room for the
    simulation banner. Buttons belong only to the final review part.
    """
    chunks, current, units = [], [], 0
    for char in text:
        width = 2 if ord(char) > 0xFFFF else 1
        if units + width > max_units:
            chunks.append("".join(current))
            current, units = [], 0
        current.append(char)
        units += width
    if current:
        chunks.append("".join(current))
    return tuple(chunks)


def tender_text(tender: Tender, now: datetime) -> str:
    if tender.search_notice:
        lines = [f"<b>🏗️ {escape(tender.title)}</b>",
                 f"🏢 <b>Възложител:</b> {escape(tender.authority)}",
                 f"📍 <b>Място на изпълнение:</b> {escape(tender.area)}",
                 '<b>Съвпадение с дейността на фирмата:</b> '+escape({
                     'Пряко съвпадение':'Пряко','Свързана възможност':'Свързано',
                     'По-широк сектор':'По-широко секторно'}.get(tender.match_label,tender.match_label or 'Не е установено')),
                 f"🔗 {escape(tender.source_reference or '')}"]
        for lot in tender.lots:
            if lot.id in tender.matched_lots:
                lines.append(f"📋 {escape(lot.id)} — {escape(lot.name)}")
                lines.append(f"📅 Краен срок: {escape(lot.deadline_label) if lot.deadline_label else date_text(lot.deadline)}")
                if lot.status == 'ACTIVE_AS_PUBLISHED':
                    lines.append('Активна според публикацията')
                elif lot.status == 'REQUIRES_CONFIRMATION':
                    lines.append('Статусът изисква потвърждение')
                else:
                    lines.append(STATUSES.get(lot.status, 'Проверете статуса в официалния източник.'))
        lines += [f"💶 Прогнозна стойност: {escape(tender.value)}", '\n' + escape(tender.summary or '')]
        return '\n'.join(lines)
    lines = [f"<b>🏗️ {escape(tender.title)}</b>\n",
             f"🏢 <b>Възложител:</b> {escape(tender.authority)}",
             f"📍 <b>Място на изпълнение:</b> {escape(tender.area)}",
             (f"🔗 Препратка: {escape(tender.source_reference or tender.id)}" if tender.search_notice else f"🔗 Препратка (синтетична): {escape(tender.id)}"),
             f"📋 <b>Съответстващи позиции:</b> {escape(', '.join(tender.matched_lots))} от общо {len(tender.lots)}\n"]
    for lot in tender.lots:
        if lot.id not in tender.matched_lots:
            continue
        status = "EXPIRED" if lot.status == "OPEN" and lot.deadline and lot.deadline <= now else lot.status
        lines.append(f"{escape(lot.id)} — {escape(lot.name)}\n{STATUSES.get(status, STATUSES['UNKNOWN'])} · 📅 Краен срок: {date_text(lot.deadline)}")
    lines += [f"\n💶 <b>Прогнозна стойност:</b> {escape(tender.value)}", f"Размер: {escape(tender.size_band)}",
              "\n📝 <b>Кратко описание:</b>\n" + escape(tender.summary or "Краткото описание не е налично в момента.")]
    deadlines = [l.deadline for l in tender.lots if l.id in tender.open_lots(now)]
    if deadlines and timedelta(0) < min(deadlines) - now <= timedelta(days=7):
        lines.append(f"\n⚠️ <b>Внимание:</b>\nКрайният срок е в следващите 7 дни: {date_text(min(deadlines))}. "
                     "Преценете дали разполагате с достатъчно време за подготовка. Подробният анализ не удължава срока.")
    if tender.regional_warning:
        lines.append("\nℹ️ <b>Регионални условия:</b>\n" + REGIONAL_WARNING.removeprefix("ℹ️ "))
    if tender.search_notice:
        lines += ["\n" + escape(tender.match_label or ''), escape(tender.search_notice)]
    return "\n".join(lines)


def safe_eop_url(url: str) -> str | None:
    if any(ord(c) < 33 or ord(c) == 127 for c in url):
        return None
    parsed = urlsplit(url)
    if parsed.scheme == "https" and parsed.netloc == "app.eop.bg" and parsed.path.startswith("/today/") and not parsed.query and not parsed.fragment:
        return url
    return None


def safe_tender_url(url: str) -> str | None:
    if safe_eop_url(url):
        return url
    if any(ord(c) < 33 or ord(c) == 127 for c in url):
        return None
    parsed = urlsplit(url)
    if (parsed.scheme == 'https' and parsed.netloc == 'ted.europa.eu' and not parsed.query and not parsed.fragment
            and re.fullmatch(r'/[a-z]{2}/notice/(?:-/detail/[0-9]+-[0-9]{4}|[0-9]+-[0-9]{4}/xml)', parsed.path)):
        return url
    return None
