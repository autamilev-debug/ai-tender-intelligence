"""Synchronous interaction state machine over service ports, never Bot API I/O.

All storage here is volatile. Production auth, locking, durable dedup/outbox and
publication evidence must be supplied by later verified ingress/persistence.
"""

from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from html import escape
import re
import secrets

from .models import (Button, CATEGORIES, Clock, DEMO, Delivery, HELP, InputEvent,
                     InteractionError, MENU, Message, ProfileFields, REGIONS, SIMULATION,
                     Tender, button_label)
from .rendering import WELCOME, profile_review_parts, safe_eop_url, safe_tender_url, tender_text
from .services import Services


@dataclass(repr=False)
class Wizard:
    mode: str
    step: str
    company: str | None = None
    name: str = ""
    email: str = ""
    categories: list[str] = field(default_factory=list)
    activities: dict[str, str] = field(default_factory=dict)
    regions: set[str] = field(default_factory=set)
    revision: int = 0
    touched: datetime | None = None
    rendered_step: str | None = None
    selection_message: str | None = None
    selection_text: str | None = None
    review_message: str | None = None
    selection_tokens: dict = field(default_factory=dict)
    id: str = field(default_factory=lambda: secrets.token_hex(8))
    base_revision: int | None = None

    def fields(self) -> ProfileFields:
        return ProfileFields(self.name, self.email, tuple(self.categories),
                             tuple((c, self.activities[c]) for c in self.categories), frozenset(self.regions))


@dataclass(repr=False)
class Action:
    owner: int
    company: str | None
    name: str
    target: str
    context: int
    expires: datetime | None
    wizard_revision: int | None = None
    used: bool = False
    source_message: str | None = None
    wizard_id: str | None = None
    stored_reference: str | None = None
    operation_id: str = field(default_factory=lambda: secrets.token_hex(16))


@dataclass(repr=False)
class Snapshot:
    owner: int
    company: str
    tenders: tuple[Tender, ...]
    created: datetime
    cursor: int = 0
    stored_reference: str | None = None


class Interaction:
    """Small local UI controller; adapters own mocked domain outcomes."""

    def __init__(self, services: Services, clock: Clock, allowed_users: set[int]):
        self.services, self.clock = services, clock
        self.allowed_users = frozenset(allowed_users)
        self.wizards: dict[int, Wizard] = {}
        self.selected: dict[int, str] = {}
        self.contexts: dict[int, int] = {}
        self.actions: dict[str, Action] = {}
        self.snapshots: dict[str, Snapshot] = {}
        self.seen_updates: dict[tuple[int, int], datetime] = {}
        self.classification_watch: dict[tuple[int, str], tuple[datetime, bool, bool]] = {}
        self.job_watch: dict[tuple[int, str, str], str] = {}
        self.receipt_messages: dict[str, Message] = {}
        self.receipt_contexts: dict[str, tuple[int, str]] = {}
        self.help_contacts: dict[int, tuple[str, str]] = {}
        self.cards: dict[str, Message] = {}
        self.card_search_references: dict[str, str] = {}
        self.session_notified: set[int] = set()
        self._counter = 0

    def _id(self) -> str:
        self._counter += 1
        return f"local-{self._counter}"

    def _message(self, user: int, text: str, rows=(), *, delivery: str | None = None,
                 edit_of=None, html=False, demo=False) -> Message:
        if user not in self.session_notified:
            prefix = SIMULATION
            self.session_notified.add(user)
        else:
            prefix = DEMO if demo else ""
        # Status decoration never interprets dynamic user/profile fields as HTML.
        if not html:
            icon = next((icon for start, icon in (
                ("Анализът не беше", "⚠️"), ("Възникна", "⚠️"), ("Анализът е завършен", "✅"),
                ("Анализът е в процес", "⏳"), ("Имейлът", "📨"), ("Заявката", "📑"),
                ("Профилът е", "🏢"), ("Въведете име", "🏢"), ("Въведете имейл", "📨"),
            ) if text.startswith(start)), "")
            text = (icon + " " if icon else "") + text
        return Message(self._id(), user, prefix + text, tuple(tuple(row) for row in rows),
                       delivery, edit_of, html=html)

    def _button(self, user: int, title: str, name: str, target: str = "", company: str | None = None,
                *, wizard: bool = False, receipt: bool = False) -> Button:
        token = "p5:" + secrets.token_urlsafe(12)
        # Proposed local cache bound, not a customer quota or production capacity.
        if len(self.actions) >= 4096:
            self.actions.pop(next(iter(self.actions)))
        self.actions[token] = Action(user, company, name, target, self.contexts.get(user, 0),
                                     None if receipt else self.clock.now() + timedelta(minutes=30),
                                     self.wizards[user].revision if wizard else None,
                                     wizard_id=self.wizards[user].id if wizard else None)
        return Button(button_label(title), callback_data=token)

    def _menu(self, user: int, company: str | None = None) -> list[Message]:
        company = company or self.selected.get(user)
        if company is None:
            return self._start(user)
        c = self.services.profiles.company(user, company)
        pending = "\nОбработката на дейностите продължава; търсенето временно не е достъпно." if self.services.classifications.classification_pending(user, company) else ""
        return [self._message(user, f"🏢 <b>Бизнес:</b> {escape(c.fields.name)}" + pending,
                             [(self._button(user, title, "menu", str(i), company),) for i, title in enumerate(MENU)], html=True)]

    def _start(self, user: int) -> list[Message]:
        businesses = self.services.profiles.companies(user)
        if not businesses:
            return [self._message(user, WELCOME, [
                [self._button(user, "Създай бизнес профил", "new")],
                [self._button(user, "Помощ", "help")]])]
        if len(businesses) == 1:
            self.selected[user] = businesses[0].id
            return self._menu(user)
        return self._selector(user)

    def _selector(self, user: int) -> list[Message]:
        rows = [[self._button(user, c.fields.name, "select", c.id)] for c in self.services.profiles.companies(user)]
        rows.append([self._button(user, "Създай нов бизнес", "new")])
        if user in self.selected:
            rows += self._return_rows(user, self.selected[user])
        return [self._message(user, "Изберете бизнес:", rows)]

    def _return_rows(self, user, company=None, *, back=None):
        row = [self._button(user, "Назад", "back", back, company)] if back else []
        if back and not company and user not in self.selected:
            return [row]
        row.append(self._button(user, "Меню" if company or user in self.selected else "Назад",
                                "home", company=company))
        return [row]

    def _edit_menu(self, user, company):
        titles = ("Име", "Имейл", "Категории и дейности", "Райони", "Изтрий бизнес")
        rows = [[self._button(user, t, "edit", str(i), company)] for i, t in enumerate(titles)]
        return [self._message(user, "Редактирай профил", rows + self._return_rows(user, company))]

    def _leave(self, user, company=None, destination="home"):
        wizard = self.wizards.pop(user, None)
        self.help_contacts.pop(user, None)
        if destination == "edit" and company:
            output = self._edit_menu(user, company)
        elif destination == "help":
            output = self._help(user, company)
        else:
            output = self._menu(user)  # Home always means the currently active business.
        root = (wizard.selection_message or wizard.review_message) if wizard else None
        return self._close_controls(user, root, output)

    def _wizard_back(self, user):
        w = self.wizards[user]
        old = w.selection_message or w.review_message
        if w.step == "review":
            w.step = "activity" if w.mode == "categories" else "regions"
            if w.step == "activity":
                w.activities.pop(w.categories[-1], None)
        elif w.step == "regions" and w.mode == "new":
            w.step = "activity"
            w.activities.pop(w.categories[-1], None)
        elif w.step == "activity":
            if w.activities:
                w.activities.pop(next(reversed(w.activities)))
            else:
                w.step = "categories"
        elif w.step in {"categories", "email"} and w.mode == "new":
            w.step = "email" if w.step == "categories" else "name"
        else:
            return self._leave(user, w.company, "edit" if w.company else "home")
        return self._close_controls(user, old, self._wizard_prompt(user))

    def _new_context(self, user: int) -> None:
        self.contexts[user] = self.contexts.get(user, 0) + 1
        self.wizards.pop(user, None)
        self.help_contacts.pop(user, None)

    def selection_pending(self, event: InputEvent) -> bool:
        """Read-only gate for neutral callback feedback, never a success claim."""
        now = self.clock.now()
        a, w = self.actions.get(event.callback), self.wizards.get(event.user_id)
        if (event.user_id not in self.allowed_users or event.chat_type != "private"
                or event.chat_id != event.user_id or not a or not w or a.used
                or a.owner != event.user_id or a.context != self.contexts.get(event.user_id, 0)
                or a.wizard_id != w.id or a.wizard_revision != w.revision or now - w.touched >= timedelta(minutes=20)
                or (a.expires and now >= a.expires)
                or (event.user_id, event.update_id) in self.seen_updates
                or a.name not in {"category", "region", "clear_categories", "clear_regions", "nationwide"}):
            return False
        try:
            if a.company:
                self.services.profiles.company(event.user_id, a.company)
        except InteractionError:
            return False
        return True

    def handle(self, event: InputEvent) -> list[Message]:
        # Identity is normalized by the caller. This is not webhook authentication.
        if event.user_id not in self.allowed_users or event.chat_type != "private" or event.chat_id != event.user_id:
            return []
        if (event.text is None) == (event.callback is None):
            return [self._message(event.user_id, "Невалидно действие.")]
        user, now = event.user_id, self.clock.now()
        self.seen_updates = {k: at for k, at in self.seen_updates.items() if now - at < timedelta(minutes=30)}
        key = (user, event.update_id)
        if key in self.seen_updates:
            return []
        self.seen_updates[key] = now  # Identifiers only, never raw update/text.
        w = self.wizards.get(user)
        if w and now - w.touched >= timedelta(minutes=20):
            self.wizards.pop(user)
            return [self._message(user, "Незавършеното въвеждане изтече. Започнете отново с /start.")]
        if w:
            w.touched = now
        try:
            if event.text == "/start":
                self._new_context(user)
                self.session_notified.discard(user)
                return self._start(user)
            if event.callback is not None:
                a = self.actions.get(event.callback)
                if a is None or a.owner != user or (a.expires and now >= a.expires):
                    raise InteractionError("Действието е невалидно, изтекло или вече е обработено. Използвайте /start.")
                if a.used:
                    return []  # An authorized duplicate never repeats an effect or emits a false expiry.
                if a.name not in {"received", "not_received"} and a.context != self.contexts.get(user, 0):
                    raise InteractionError("Бутонът е от предишен екран. Използвайте текущото меню.")
                if a.company:
                    self.services.profiles.company(user, a.company)
                if a.wizard_revision is not None:
                    if w is None or w.id != a.wizard_id or w.revision != a.wizard_revision:
                        return []  # Retired wizard step: no mutation and no misleading duplicate message.
                    w.touched = now
                result = self._dispatch(user, a)
                a.used = a.name not in {"category", "region", "clear_categories", "clear_regions", "nationwide"}
                return result
            return self._text(user, event.text or "")
        except InteractionError as error:
            action = self.actions.get(event.callback) if event.callback else None
            if (w and w.step in {"categories", "regions"} and
                    (event.text is not None or (action and action.owner == user
                     and action.context == self.contexts.get(user, 0)
                     and action.wizard_id == w.id and action.wizard_revision == w.revision
                     and (action.expires is None or now < action.expires)
                     and action.name in {"category", "categories_done"}))):
                return self._wizard_prompt(user, notice=str(error))
            return [self._message(user, str(error), self._return_rows(user, self.selected.get(user)))]

    def _wizard_prompt(self, user: int, *, notice: str | None = None) -> list[Message]:
        w = self.wizards[user]
        selection = w.step in {"categories", "regions"}
        if w.rendered_step != w.step or not selection:
            w.revision += 1
            w.rendered_step = w.step
            w.selection_message = None
            w.selection_text = None
            w.review_message = None
            w.selection_tokens.clear()
        w.touched = self.clock.now()

        def btn(title, name, target=""):
            key = (name, target)
            old = w.selection_tokens.get(key) if selection else None
            action = self.actions.get(old.callback_data) if old else None
            if action and (action.expires is None or action.expires > self.clock.now()):
                return replace(old, text=button_label(title))
            button = self._button(user, title, name, target, w.company, wizard=True)
            if selection:
                w.selection_tokens[key] = button
            return button

        def selector(text, rows):
            if notice:
                text += "\n\n⚠️ " + notice
            message = self._message(user, text, rows)
            if w.selection_message:
                message = replace(message, edit_of=w.selection_message, keyboard_only=w.selection_text == text)
            else:
                w.selection_message = message.id
            w.selection_text = text
            return [message]
        if w.step == "categories":
            rows = [[btn(("✅ " if c in w.categories else "") + c, "category", str(i))]
                    for i, c in enumerate(CATEGORIES)]
            rows.append([btn("Изчисти", "clear_categories"), btn("Готово", "categories_done"), btn("Отказ", "cancel"), btn("Назад", "wizard_back")])
            return selector("📋 Изберете една или две равнопоставени категории.", rows)
        if w.step == "regions":
            buttons = [btn(("✅ " if r in w.regions else "") + r, "region", str(i)) for i, r in enumerate(REGIONS)]
            rows = [buttons[i:i + 3] for i in range(0, len(buttons), 3)]
            rows.append([btn("Цяла България", "nationwide"), btn("Изчисти", "clear_regions"), btn("Готово", "regions_done")])
            rows.append([btn("Назад", "wizard_back"), btn("Отказ", "cancel")])
            return selector("📍 Изберете райони.\n\nПразен избор = цяла България.", rows)
        if w.step == "review":
            parts = profile_review_parts(w.fields())
            messages = [self._message(user, part,
                                  [[btn("Потвърди", "confirm"), btn("Отказ", "cancel"), btn("Назад", "wizard_back")]] if i == len(parts) - 1 else [], html=html)
                    for i, (part, html) in enumerate(parts)]
            w.review_message = messages[-1].id
            return messages
        if w.step == "activity":
            category = next(c for c in w.categories if c not in w.activities)
            prompt = (f"Опишете конкретните услуги или продукти за „{category}“. Достатъчна е и една дейност, "
                      "например „ремонт на покриви“, „сондажи за вода“ или „доставка на електроматериали“. "
                      "Конкретното описание помага за по-точни резултати.")
        else:
            prompt = {"name": "Въведете име на бизнеса.", "email": "Въведете имейл за отчетите. Реалните тестови отчети са разрешени само до reports@example.invalid; адресът Ви няма да бъде подменен."}[w.step]
        return [self._message(user, prompt, [[btn("Назад", "wizard_back"), btn("Отказ", "cancel")]])]

    @staticmethod
    def _valid_text(text: str, limit: int) -> str:
        if not text.strip() or len(text) > limit or any(ord(c) < 32 or ord(c) == 127 or 0xD800 <= ord(c) <= 0xDFFF for c in text):
            raise InteractionError(f"Въведете непразен текст до {limit} знака без управляващи символи.")
        return text.strip()

    def _text(self, user: int, text: str) -> list[Message]:
        w = self.wizards.get(user)
        if not w:
            if user in self.help_contacts:
                issue, _ = self.help_contacts[user]
                self._email(text)
                self.help_contacts.pop(user)
                return [self._message(user, f"Сигналът „{issue}“ е приет само в локалната симулация. Реален имейл не е изпратен.", self._return_rows(user, back="help"))]
            raise InteractionError("Използвайте бутоните на менюто или /start.")
        if w.step in {"name", "email"}:
            value = self._email(text) if w.step == "email" else self._valid_text(text, 120)
            setattr(w, w.step, value)
            if w.mode == "field":
                self.services.profiles.edit_fields(user, w.company, **{w.step: value})
                self.wizards.pop(user)
                return [self._message(user, "Профилът е обновен.")] + self._menu(user)
            w.step = "email" if w.step == "name" else "categories"
        elif w.step == "activity":
            category = next(c for c in w.categories if c not in w.activities)
            w.activities[category] = self._valid_text(text, 2000)
            if len(w.activities) == len(w.categories):
                w.step = "review" if w.mode == "categories" else "regions"
        else:
            raise InteractionError("Използвайте бутоните от текущата стъпка.")
        return self._wizard_prompt(user)

    def _email(self, text: str) -> str:
        value = self._valid_text(text, 254)
        if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", value):
            raise InteractionError("Въведете валиден имейл адрес.")
        return value

    def _dispatch(self, user: int, a: Action) -> list[Message]:
        name, company = a.name, a.company
        if name == "new":
            self._new_context(user)
            self.wizards[user] = Wizard("new", "name")
            return self._wizard_prompt(user)
        if name == "select":
            self.services.profiles.company(user, a.target)
            self._new_context(user)
            self.selected[user] = a.target
            return self._menu(user)
        if name == "cancel":
            wizard = self.wizards.get(user)
            return self._leave(user, wizard.company if wizard else company, "edit" if wizard and wizard.company else "home")
        if name == "wizard_back":
            return self._wizard_back(user)
        if name == "back":
            return self._leave(user, company, a.target)
        if name in {"category", "clear_categories", "categories_done", "region", "nationwide", "clear_regions", "regions_done", "confirm"}:
            return self._wizard_action(user, a)
        if name == "menu":
            return self._menu_action(user, company, int(a.target), request_key=a.operation_id)
        if name == "home":
            return self._leave(user, company)
        if name == "more":
            return self._batch(user, company, a.target)
        if name in {"save", "remove"}:
            if a.stored_reference is not None:
                self.services.search.stored_results(user, company, a.stored_reference)
            self.services.history.set_saved(user, company, a.target, name == "save")
            original = self.cards.get(a.source_message)
            if original:
                replacement = self._button(user, "Премахни" if name == "save" else "Запази",
                                           "remove" if name == "save" else "save", a.target, company)
                rows = tuple(tuple(replacement if self.actions.get(b.callback_data) is a else b
                                   for b in row) for row in original.keyboard)
                message = replace(original, id=self._id(), keyboard=rows, edit_of=a.source_message, keyboard_only=True)
                self.cards[a.source_message] = message
                self._bind_card(message, a.source_message)
                return [message]
            return [self._message(user, "Поръчката е запазена." if name == "save" else "Поръчката е премахната от запазените.")]
        if name == "deep":
            snapshot_id, tender_id = a.target.split("/", 1)
            snap = self.snapshots.get(snapshot_id)
            if snap is None or snap.owner != user or snap.company != company or self.clock.now() >= snap.created + timedelta(minutes=30):
                raise InteractionError("Резултатите са изтекли. Направете ново търсене.")
            if snap.stored_reference is not None:
                self.services.search.stored_results(user, company, snap.stored_reference)
                raise InteractionError('Подробният анализ не е активиран за тези резултати.')
            if tender_id not in {t.id for t in snap.tenders}:
                raise InteractionError("Невалидна поръчка.")
            j = self.services.analyses.accept_analysis(user, company, tender_id, a.target)
            if (user, company, j.id) in self.job_watch or j.status != "accepted":
                rows = [[self._button(user, "📩 Изпрати анализа отново", "historical", j.id, company)]] if j.status == "complete" else []
                return [self._message(user, "Заявката вече е приета. " + {"accepted": "Обработката предстои.", "running": "Анализът е в процес.", "failed": "Опитът не беше завършен; използвайте съобщението за повторен опит.", "complete": "Анализът е завършен."}.get(j.status, "Проверете съобщенията за резултата."), rows + self._return_rows(user, company))]
            self.job_watch[(user, company, j.id)] = j.status
            return [self._message(user, f"Заявката за анализ е приета за позиции {', '.join(j.lots)} от общо {j.total_lots}. "
                                  "Събираме официално достъпната документация. Анализът ще използва проверения събран набор, "
                                  "а в имейла ще бъде посочен периодът на събиране. Затварянето на Telegram не прекъсва обработката.", self._return_rows(user, company), demo=True)]
        if name == "retry":
            j = self.services.analyses.retry_analysis(user, company, a.target)
            self.job_watch[(user, company, j.id)] = j.status
            return [self._message(user, "Повторният опит е приет. Обработката продължава.", self._return_rows(user, company), demo=True)]
        if name in {"received", "not_received"}:
            d = self.services.deliveries.respond(user, company, a.target, name == "received")
            original = self.receipt_messages.get(d.id)
            text = ("Благодарим за потвърждението." if name == "received" else
                    "Имейлът е изпратен повторно (симулация). Проверете „Входящи“ и „Спам“. Ако не го получите в рамките на 5–10 минути, отворете „Помощ“ → „Не получих имейл“.")
            if name == "not_received" and d.status != "ACCEPTED":
                text = "Не можем да потвърдим резултата от изпращането. Анализът е запазен. Проверете „Входящи“ и „Спам“ и използвайте „Помощ“."
            return [self._message(user, text, self._return_rows(user, company), edit_of=original.id if original else None, demo=True)]
        if name == "historical":
            d = self.services.deliveries.deliver(user, company, a.target, historical=True)
            return self._delivery_messages(user, company, d)
        if name == "edit":
            return self._edit(user, company, a.target)
        if name == "delete":
            self.services.profiles.delete_company(user, company)
            self._new_context(user)
            self.selected.pop(user, None)
            return [self._message(user, "Личните данни и достъпът за този бизнес са премахнати в симулацията.")] + self._start(user)
        if name == "help":
            return self._help(user, company)
        if name == "help_issue":
            issue = HELP[int(a.target)]
            email = self.services.profiles.company(user, company).fields.email if company else None
            if email:
                return [self._message(user, f"Сигналът „{issue}“ е приет само в локалната симулация. Реален имейл не е изпратен; контакт: {email}.", self._return_rows(user, company, back="help"))]
            self.help_contacts[user] = (issue, "")
            return [self._message(user, "Въведете имейл за контакт само за този сигнал. Това не създава бизнес профил.", self._return_rows(user, back="help"))]
        raise InteractionError("Действието не е достъпно.")

    def _wizard_action(self, user: int, a: Action) -> list[Message]:
        w = self.wizards[user]
        previous_selection = w.selection_message
        if a.name == "category":
            c = CATEGORIES[int(a.target)]
            if c in w.categories:
                w.categories.remove(c)
            elif len(w.categories) < 2:
                w.categories.append(c)
            else:
                raise InteractionError("Можете да изберете най-много две категории.")
        elif a.name == "clear_categories":
            w.categories.clear()
        elif a.name == "categories_done":
            if not w.categories:
                raise InteractionError("Изберете поне една категория.")
            w.activities.clear()
            w.step = "activity"
        elif a.name == "region":
            r = REGIONS[int(a.target)]
            (w.regions.remove if r in w.regions else w.regions.add)(r)
        elif a.name in {"nationwide", "clear_regions"}:
            w.regions.clear()
        elif a.name == "regions_done":
            w.step = "review"
        elif a.name == "confirm":
            fields = w.fields()
            if w.mode == "new":
                c = self.services.profiles.create_company(user, fields, confirmation_key=w.id)
                self.selected[user] = c.id
                edit = False
            elif w.mode == "categories":
                c = self.services.profiles.company(user, w.company)
                self.services.classifications.reclassify(user, c.id, fields, confirmation_key=w.id, expected_revision=w.base_revision)
                edit = True
            else:
                self.services.profiles.edit_fields(user, w.company, regions=fields.regions)
                self.wizards.pop(user)
                return self._close_controls(user, w.review_message, [self._message(user, "Районите са обновени.")] + self._menu(user))
            self.classification_watch[(user, c.id)] = (self.clock.now(), edit, False)
            self.wizards.pop(user)
            return self._close_controls(user, w.review_message, [self._message(user, "Профилът е създаден. Подготвяме кодовете за търсене според категориите и дейностите Ви." if not edit else "Промяната е приета. Старите категории, дейности и кодове остават в сила до успешна обработка.", self._return_rows(user, c.id))])
        output = self._wizard_prompt(user)
        if previous_selection and a.name in {"categories_done", "regions_done"}:
            return self._close_controls(user, previous_selection, output)
        return output

    def _close_controls(self, user, root, output):
        if not root:
            return output
        return [Message(self._id(), user, "", edit_of=root, keyboard_only=True)] + output

    def _menu_action(self, user: int, company: str, choice: int, *, request_key: str | None = None) -> list[Message]:
        self.services.profiles.company(user, company)
        if choice == 0:
            if hasattr(self.services.search, 'start_search'):
                if request_key is None:
                    raise InteractionError('Използвайте бутона за ново търсене от текущото меню.')
                self.services.search.start_search(user, company, request_key)
            tenders = self.services.search.search_results(user, company)[:15]
            if not tenders and hasattr(self.services.search, 'empty_notice'):
                return [self._message(user, self.services.search.empty_notice(user, company))] + self._menu(user, company)
            if not tenders:
                return [self._message(user, "Не са намерени поръчки, съответстващи на дейностите и районите Ви.")] + self._menu(user, company)
            key = self._id()
            reference = (self.services.search.snapshot_reference(user, company)
                         if hasattr(self.services.search, 'stored_results') else None)
            self.snapshots[key] = Snapshot(user, company, tenders, self.clock.now(), stored_reference=reference)
            return self._batch(user, company, key)
        if choice in {1, 2}:
            entries = [e for e in self.services.history.entries(user, company)
                       if e.tender.terminal(self.clock.now()) == (choice == 2)]
            if not entries:
                return [self._message(user, "Няма поръчки в този списък.")] + self._menu(user, company)
            messages = []
            key = self._id()
            self.snapshots[key] = Snapshot(user, company, tuple(e.tender for e in entries), self.clock.now())
            for e in entries:
                messages.append(self._card(user, company, e.tender, key, saved=e.saved, report=e.report_job_id))
            return self._navigation(user, company, messages)
        if choice == 3:
            return self._edit_menu(user, company)
        if choice == 4:
            return self._dispatch(user, Action(user, None, "new", "", 0, None))
        if choice == 5:
            return self._selector(user)
        return self._help(user, company)

    def _home(self, user: int, company: str) -> Message:
        return self._message(user, "Меню", [[self._button(user, "Меню", "home", company=company)]])

    def _batch(self, user: int, company: str, snapshot: str) -> list[Message]:
        snap = self.snapshots.get(snapshot)
        if snap is None or snap.owner != user or snap.company != company or self.clock.now() >= snap.created + timedelta(minutes=30):
            raise InteractionError("Резултатите са изтекли. Направете ново търсене.")
        if snap.stored_reference is not None:
            snap.tenders = self.services.search.stored_results(user, company, snap.stored_reference)
        batch = snap.tenders[snap.cursor:snap.cursor + 5]
        snap.cursor += len(batch)
        messages = [self._card(user, company, t, snapshot) for t in batch]
        if batch and batch[0].search_notice:
            messages[-1] = replace(messages[-1], text=messages[-1].text + '\n\n' + escape(batch[0].search_notice))
        return self._navigation(user, company, messages,
                                snapshot if snap.cursor < len(snap.tenders) else None)

    def _navigation(self, user, company, messages, more=None):
        if not messages:
            return []
        buttons = ([self._button(user, "Още 5", "more", more, company)] if more else [])
        buttons.append(self._button(user, "Меню", "home", company=company))
        last = replace(messages[-1], keyboard=messages[-1].keyboard + (tuple(buttons),))
        messages[-1] = last
        self.cards[last.id] = last
        self._bind_card(last, last.id)
        return messages

    def _bind_card(self, message, root):
        for row in message.keyboard:
            for button in row:
                if button.callback_data in self.actions:
                    self.actions[button.callback_data].source_message = root
                    self.actions[button.callback_data].stored_reference = self.card_search_references.get(root)

    def _card(self, user: int, company: str, tender: Tender, snapshot: str, *, saved=False, report=None) -> Message:
        buttons = [self._button(user, "Премахни" if saved else "Запази", "remove" if saved else "save", tender.id, company)]
        if not report and not tender.search_notice and tender.open_lots(self.clock.now()):
            buttons.append(self._button(user, "Подробен анализ", "deep", snapshot + "/" + tender.id, company))
        rows = [buttons]
        if report:
            rows.append([self._button(user, "📩 Изпрати анализа отново", "historical", report, company)])
        url = safe_tender_url(tender.eop_url) if tender.search_notice else safe_eop_url(tender.eop_url)
        if url:
            rows.append([Button("Виж в TED" if tender.search_notice and url.startswith("https://ted.europa.eu/") else "Виж в ЕОП", url=url)])
        message = self._message(user, tender_text(tender, self.clock.now()), rows, html=True, demo=True)
        self.cards[message.id] = message
        snap = self.snapshots.get(snapshot)
        if snap and snap.stored_reference:
            self.card_search_references[message.id] = snap.stored_reference
        self._bind_card(message, message.id)
        return message

    def _edit(self, user: int, company: str, choice: str) -> list[Message]:
        c = self.services.profiles.company(user, company)
        if choice == "4":
            return [self._message(user, "Изтриването премахва личните запазени поръчки, историята и достъпа до анализи за този бизнес. Неизпратените операции се отменят, доколкото е възможно. Вече изпратени имейли не могат да бъдат върнати.",
                                  [[self._button(user, "Потвърди изтриването", "delete", company=company)],
                                   [self._button(user, "Отказ", "back", "edit", company)],
                                   *self._return_rows(user, company, back="edit")])]
        if choice == "2" and self.services.classifications.classification_pending(user, company):
            raise InteractionError("Изчакайте текущата обработка на дейностите.")
        mode, step = {"0": ("field", "name"), "1": ("field", "email"),
                      "2": ("categories", "categories"), "3": ("regions", "regions")}[choice]
        self.wizards[user] = Wizard(mode, step, company, c.fields.name, c.fields.email,
                                     [] if mode == "categories" else list(c.fields.categories),
                                     {} if mode == "categories" else dict(c.fields.activities), set(c.fields.regions), base_revision=c.revision)
        return self._wizard_prompt(user)

    def _help(self, user: int, company=None) -> list[Message]:
        rows = [[self._button(user, title, "help_issue", str(i), company)] for i, title in enumerate(HELP)]
        return [self._message(user, "Помощ", rows + self._return_rows(user, company))]

    def published(self, message: Message, at: datetime) -> None:
        """Trusted transport acknowledgment, not a customer-supplied update."""
        if message.delivery_id:
            stored = self.receipt_messages.get(message.delivery_id)
            if stored is None or stored.id != message.id:
                raise InteractionError("Невалидно потвърждение за публикуване.")
            user, company = self.receipt_contexts[message.delivery_id]
            self.services.deliveries.published(user, company, message.delivery_id, message.id, at)

    def _delivery_messages(self, user: int, company: str, d: Delivery) -> list[Message]:
        if d.status != "ACCEPTED":
            return [self._message(user, "Не можем да потвърдим резултата от изпращането. Анализът е запазен. Проверете „Входящи“ и „Спам“ и използвайте „Помощ“ → „Не получих имейл“.", self._return_rows(user, company), demo=True)]
        text = f"Имейлът е приет за изпращане до {d.recipient}. Проверете „Входящи“ и „Спам“."
        if d.historical:
            return [self._message(user, "Историческият анализ е запазен с оригиналния период и източници. " + text, self._return_rows(user, company), demo=True)]
        text += " Потвърдете получаването в следващите 15 минути."
        message = self._message(user, text, [[self._button(user, "Получих", "received", d.id, company, receipt=True),
                                            self._button(user, "Не получих", "not_received", d.id, company, receipt=True)],
                                           *self._return_rows(user, company)], delivery=d.id, demo=True)
        self.receipt_messages[d.id] = message
        self.receipt_contexts[d.id] = (user, company)
        return [message]

    def poll(self) -> list[Message]:
        """Observe fake status changes; no jobs, sleeps, provider reads or timer thread."""
        result = []
        now = self.clock.now()
        for (user, cid), (started, edit, slow_sent) in list(self.classification_watch.items()):
            try:
                c = self.services.profiles.company(user, cid)
            except InteractionError:
                del self.classification_watch[(user, cid)]
                continue
            if self.services.classifications.classification_pending(user, cid):
                if not slow_sent and now - started >= timedelta(seconds=30):
                    result.append(self._message(user, "Обработката на дейностите продължава. Ще Ви уведомим при готовност. Ако срещате проблем, използвайте „Помощ“.", self._return_rows(user, cid)))
                    self.classification_watch[(user, cid)] = (started, edit, True)
                continue
            del self.classification_watch[(user, cid)]
            if self.services.classifications.classification_result(user, cid) == "success":
                text = ("Категориите и дейностите са обновени. Можете да търсите поръчки с новите настройки." if edit else "Профилът е готов. Можете да търсите поръчки.")
                result.append(self._message(user, text + "\nСинтетичен резултат: " + ", ".join(c.cpv) + ". Това не са официални CPV кодове."))
            else:
                result.append(self._message(user, "Промяната не беше завършена. Старите категории, дейности и кодове остават в сила. Моля, опитайте отново по-късно или използвайте „Помощ“." if edit else "Обработката не беше завършена. Използвайте „Помощ“."))
            result += self._menu(user, cid)
        for (user, cid, jid), previous in list(self.job_watch.items()):
            try:
                j = self.services.analyses.job(user, cid, jid)
            except InteractionError:
                del self.job_watch[(user, cid, jid)]
                continue
            if j.status == previous:
                continue
            self.job_watch[(user, cid, jid)] = j.status
            if j.status == "running":
                result.append(self._message(user, "Анализът е в процес. Затварянето на Telegram не прекъсва обработката.", self._return_rows(user, cid), demo=True))
            elif j.status == "failed":
                rows = [[self._button(user, "Опитай отново", "retry", j.id, cid)]] if not j.manual_used else []
                result.append(self._message(user, "Анализът не беше завършен, защото необходим документ или съществена информация не е достъпна или не може да бъде обработена. Не е изпратен непълен анализ. Извършената обработка е запазена." + (" Няма оставащо право за повторен опит. Използвайте „Помощ“." if j.manual_used else ""), rows + self._return_rows(user, cid), demo=True))
            elif j.status == "needs_attention":
                result.append(self._message(user, "Възникна технически проблем, който изисква намеса за възстановяване. Извършената обработка е запазена. Можете да използвате „Помощ“.", self._return_rows(user, cid), demo=True))
            elif j.status == "complete":
                historical = (" Исторически анализ за позиции с изтекъл срок: " + ", ".join(j.historical_lots) + "." if j.historical_lots else "")
                result.append(self._message(user, "Анализът е завършен (синтетичен резултат)." + historical, self._return_rows(user, cid), demo=True))
                try:
                    result += self._delivery_messages(user, cid, self.services.deliveries.deliver(user, cid, jid))
                except InteractionError as error:
                    result.append(self._message(user, str(error)))
        for did, message in list(self.receipt_messages.items()):
            user, company = self.receipt_contexts[did]
            try:
                d = self.services.deliveries.delivery(user, company, did)
            except InteractionError:
                continue
            if self.services.deliveries.expire_receipt(user, company, did):
                result.append(self._message(d.owner, message.text.removeprefix(SIMULATION), self._return_rows(user, company), edit_of=message.id))
        return result
