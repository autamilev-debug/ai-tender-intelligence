"""Small interaction DTOs shared by presentation and replaceable services."""

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Protocol

CATEGORIES = ("Строителство", "Електро", "ВиК", "ИТ", "Медицина", "Консултиране",
              "Почистване", "Транспорт", "Храни", "Охрана", "Доставки", "Енергетика")
REGIONS = ("Благоевград", "Бургас", "Варна", "Велико Търново", "Видин", "Враца",
           "Габрово", "Добрич", "Кърджали", "Кюстендил", "Ловеч", "Монтана",
           "Пазарджик", "Перник", "Плевен", "Пловдив", "Разград", "Русе", "Силистра",
           "Сливен", "Смолян", "София-град", "София-област", "Стара Загора",
           "Търговище", "Хасково", "Шумен", "Ямбол")
MENU = ("Търси поръчки", "Запазени поръчки", "Изтекли поръчки", "Редактирай профил",
        "Създай нов бизнес", "Смени бизнес", "Помощ")
HELP = ("Не получих имейл", "Проблем при създаване на бизнес профил",
        "Проблем при търсене на поръчки", "Проблем с подробния анализ",
        "Проблем с редактиране на профил", "Друг технически проблем")
TEST_RECIPIENT = "reports@example.invalid"
SIMULATION = "🧪 Локална симулация — няма реални заявки или изпращане.\n"
DEMO = "🧪 Демо\n\n"
PUBLICATION_SKEW_SECONDS = 5  # Preview clock tolerance; timestamps are never clamped.
BUTTON_ICONS = {
    "Търси поръчки": "🔎", "Запазени поръчки": "💾", "Изтекли поръчки": "📅",
    "Редактирай профил": "✏️", "Създай нов бизнес": "🏢", "Създай бизнес профил": "🏢",
    "Смени бизнес": "🔄", "Помощ": "❓", "Подробен анализ": "📑",
    "Изпрати анализа отново": "📨", "Запази": "💾", "Премахни": "🗑️",
    "Меню": "🏠", "Още 5": "➡️", "Получих": "✅", "Не получих": "📭",
    "Потвърди": "✅", "Отказ": "↩️", "Готово": "✅", "Изчисти": "🧹",
    "Опитай отново": "🔄",
    "Назад": "⬅️",
}


def canonical_label(text: str) -> str:
    text = text.removeprefix("✅ ").removeprefix("📩 ")
    for label, icon in BUTTON_ICONS.items():
        if text == icon + " " + label:
            return label
    return text


def button_label(text: str) -> str:
    label = canonical_label(text)
    return BUTTON_ICONS.get(label, "") + " " + label if label in BUTTON_ICONS else text


class Clock(Protocol):
    def now(self) -> datetime: ...


@dataclass
class ManualClock:
    instant: datetime = datetime(2030, 4, 10, 7, tzinfo=timezone.utc)

    def now(self) -> datetime:
        return self.instant

    def advance(self, *, seconds: int = 0, minutes: int = 0) -> None:
        self.instant += timedelta(seconds=seconds, minutes=minutes)


class InteractionError(ValueError):
    """Safe Bulgarian guidance; never raw exceptions or provider payloads."""


@dataclass(frozen=True)
class InputEvent:
    user_id: int
    chat_id: int
    update_id: int
    text: str | None = field(default=None, repr=False)
    callback: str | None = field(default=None, repr=False)
    chat_type: str = "private"


@dataclass(frozen=True)
class Button:
    text: str
    callback_data: str | None = None
    url: str | None = None

    def payload(self) -> dict[str, str]:
        if (self.callback_data is None) == (self.url is None):
            raise ValueError("A button needs exactly one action")
        return {"text": self.text, **({"callback_data": self.callback_data}
                                     if self.callback_data else {"url": self.url})}


@dataclass(frozen=True, repr=False)
class Message:
    id: str
    chat_id: int
    text: str
    keyboard: tuple[tuple[Button, ...], ...] = ()
    delivery_id: str | None = None
    edit_of: str | None = None
    html: bool = False
    keyboard_only: bool = False

    def payload(self) -> dict:
        # HTML is opt-in; presentation escapes all dynamic fields before use.
        result = {"chat_id": self.chat_id, "text": self.text,
                  "reply_markup": {"inline_keyboard": [[b.payload() for b in row]
                                                         for row in self.keyboard]}}
        if self.edit_of:
            result["message_id"] = self.edit_of
        if self.html:
            result["parse_mode"] = "HTML"
        return result


@dataclass(frozen=True, repr=False)
class ProfileFields:
    name: str
    email: str
    categories: tuple[str, ...]
    activities: tuple[tuple[str, str], ...]
    regions: frozenset[str] = frozenset()


@dataclass(repr=False)
class Company:
    id: str
    owner: int
    fields: ProfileFields
    cpv: tuple[str, ...] = ()
    classification: str = "pending"
    revision: int = 0


@dataclass(frozen=True)
class Lot:
    id: str
    name: str
    status: str
    deadline: datetime | None
    deadline_label: str | None = None


@dataclass(frozen=True)
class Tender:
    id: str
    title: str
    authority: str
    area: str
    lots: tuple[Lot, ...]
    matched_lots: tuple[str, ...]
    value: str
    size_band: str
    summary: str | None
    eop_url: str
    regional_warning: bool = False
    search_notice: str | None = None
    match_label: str | None = None
    source_reference: str | None = None

    def open_lots(self, now: datetime) -> tuple[str, ...]:
        return tuple(l.id for l in self.lots if l.id in self.matched_lots
                     and l.status == "OPEN" and l.deadline is not None and l.deadline > now)

    def terminal(self, now: datetime) -> bool:
        relevant = [l for l in self.lots if l.id in self.matched_lots]
        return bool(relevant) and all(l.status in {"CANCELLED", "AWARDED", "EXPIRED"}
                                     or (l.status == "OPEN" and l.deadline is not None
                                         and l.deadline <= now) for l in relevant)


@dataclass(repr=False)
class Job:
    id: str
    owner: int
    company_id: str
    tender_id: str
    lots: tuple[str, ...]
    recipient: str
    total_lots: int = 0
    deadlines: tuple[tuple[str, datetime], ...] = ()
    historical_lots: tuple[str, ...] = ()
    status: str = "accepted"
    manual_used: bool = False


@dataclass(repr=False)
class Delivery:
    id: str
    owner: int
    company_id: str
    job_id: str
    recipient: str
    status: str
    historical: bool = False
    published_at: datetime | None = None
    prompt_id: str | None = None
    response: str | None = None
    resend_used: bool = False


@dataclass(frozen=True)
class HistoryEntry:
    tender: Tender
    saved: bool
    report_job_id: str | None
