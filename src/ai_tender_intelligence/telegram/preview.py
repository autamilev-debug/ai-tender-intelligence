"""Foreground local Telegram preview. Import/help never connects or reads keys.

Live use is owner-operated; only --connect enables startup. No unattended run.
The Beta composition supplies the real services and owns activation.
"""

import argparse
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import sys
import time

from .interaction import Interaction
from .models import Clock, Message, PUBLICATION_SKEW_SECONDS, SIMULATION, DEMO
from .transport import CallbackExpired, EditNotModified, TransportFailure, PreviewError, PreviewSettings, TelegramClient, TelegramHTTP, normalize_update
from .diagnostics import Diagnostics, console_timing

PREVIEW_BANNER = "🧪 Telegram преглед: поръчки, анализи и имейли са симулирани. Не се изпраща имейл.\n"


class UTCClock:
    def now(self) -> datetime:
        return datetime.now(timezone.utc)


@dataclass(frozen=True)
class PublishedReference:
    chat_id: int
    message_id: int
    published_at: datetime


class Preview:
    def __init__(self, client: TelegramClient, ui: Interaction, clock: Clock,
                 allowed_users: frozenset[int], driver: object | None = None, *, diagnostics=None, sleeper=time.sleep, banner=PREVIEW_BANNER, demo_banner=None):
        self.client, self.ui, self.clock = client, ui, clock
        self.allowed_users, self.driver = allowed_users, driver
        self.banner, self.demo_banner = banner, demo_banner
        self.references: dict[str, PublishedReference] = {}
        self.callback_references: dict[str, tuple[int, int]] = {}
        self.offset: int | None = None
        self.retired_callbacks: dict[str, tuple[int, int]] = {}
        self.diagnostics = diagnostics or Diagnostics()
        self.sleeper = sleeper
        if hasattr(ui, 'bind_publisher'):
            ui.bind_publisher(self.send)

    def _publication(self, result, chat: int, expected_id: int | None = None) -> PublishedReference:
        if not isinstance(result, dict) or not isinstance(result.get("chat"), dict):
            raise PreviewError("Telegram publication is not confirmed; no replacement was sent.")
        mid, date = result.get("message_id"), result.get("date")
        if (type(mid) is not int or mid <= 0 or type(date) is not int or date <= 0
                or type(result["chat"].get("id")) is not int or result["chat"]["id"] != chat
                or result["chat"].get("type") != "private" or (expected_id is not None and mid != expected_id)):
            raise PreviewError("Telegram publication identity/time is invalid; preview stopped.")
        try:
            at = datetime.fromtimestamp(date, timezone.utc)
        except (ValueError, OverflowError, OSError):
            raise PreviewError("Telegram publication time is invalid.") from None
        skew = (at - self.clock.now()).total_seconds()
        self.diagnostics.record("clock", "publication", skew_ms=skew * 1000)
        if skew > PUBLICATION_SKEW_SECONDS:
            raise PreviewError("Telegram publication is ahead of the local clock. Check the clock; no receipt timer was invented.")
        return PublishedReference(chat, mid, at)

    def send(self, messages: list[Message]) -> None:
        # Render only the last consecutive keyboard state for one known message.
        pending = []
        for message in messages:
            if (pending and message.keyboard_only and pending[-1].keyboard_only
                    and message.edit_of == pending[-1].edit_of):
                pending[-1] = message
            else:
                pending.append(message)
        for message in pending:
            render_started = self.diagnostics.timer()
            if message.chat_id not in self.allowed_users:
                raise PreviewError("Outbound chat is outside the owner allowlist.")
            payload = message.payload()
            payload["link_preview_options"] = {"is_disabled": True}
            # The interaction stays reused; only the transport-specific banner changes.
            payload["text"] = (self.banner + message.text.removeprefix(SIMULATION)
                               if message.text.startswith(SIMULATION) else
                               self.demo_banner + message.text.removeprefix(DEMO)
                               if self.demo_banner is not None and message.text.startswith(DEMO) else message.text)
            if message.edit_of:
                original = self.references.get(message.edit_of)
                if original is None or original.chat_id != message.chat_id:
                    raise PreviewError("Original Telegram message mapping is unavailable; edit was not replaced with a send.")
                payload["message_id"] = original.message_id
                method = "editMessageReplyMarkup" if message.keyboard_only else "editMessageText"
                if message.keyboard_only:
                    payload = {k: payload[k] for k in ("chat_id", "message_id", "reply_markup")}
                for attempt in range(2):
                    try:
                        result = self.client.call(method, payload)
                        self._publication(result, message.chat_id, original.message_id)
                        break
                    except EditNotModified:
                        self.diagnostics.record("recovery", method, stage="not_modified", outcome="recovered")
                        break
                    except TransportFailure as exc:
                        if attempt or exc.operation != method or not exc.recoverable:
                            raise
                        self.diagnostics.record("recovery", method, stage="retry", outcome="uncertain")
                        # Identical known-id edit only; never a new message or timer.
                        continue
                # An edit's response/edit_date cannot restart original publication.
                reference = original
            else:
                result = self.client.call("sendMessage", payload)
                reference = self._publication(result, message.chat_id)
            self.references[message.id] = reference
            removed = {token: bound for token, bound in self.callback_references.items()
                       if bound == (reference.chat_id, reference.message_id)}
            self.retired_callbacks.update(removed)
            self.callback_references = {token: bound for token, bound in self.callback_references.items()
                                        if token not in removed}
            while len(self.retired_callbacks) > 4096:
                self.retired_callbacks.pop(next(iter(self.retired_callbacks)))
            for row in message.keyboard:
                for button in row:
                    if button.callback_data:
                        self.callback_references[button.callback_data] = (reference.chat_id, reference.message_id)
                        self.retired_callbacks.pop(button.callback_data, None)
            if message.delivery_id and not message.edit_of:
                self.ui.published(message, reference.published_at)
            self.diagnostics.record("render", "editMessageReplyMarkup" if message.keyboard_only else "editMessageText" if message.edit_of else "sendMessage", self.diagnostics.timer() - render_started)

    def acknowledge(self, raw):
        incoming = normalize_update(raw, self.allowed_users)
        if incoming is None:
            return False
        if not incoming.callback_id:
            return True
        binding = (incoming.event.chat_id, incoming.source_message_id)
        valid = self.callback_references.get(incoming.event.callback) == binding
        retired = self.retired_callbacks.get(incoming.event.callback) == binding
        acknowledgment = {"callback_query_id": incoming.callback_id}
        if valid and self.ui.selection_pending(incoming.event):
            # Web K's toast captures the next mouse click for dismissal. Clear
            # the native spinner silently; the edited keyboard shows the result.
            acknowledgment.update(cache_time=0, show_alert=False)
        if not valid and not retired:
            acknowledgment["text"] = "Бутонът е от предишен преглед или е невалиден. Използвайте /start."
        started = self.diagnostics.timer()
        try:
            self.client.call("answerCallbackQuery", acknowledgment)
        except CallbackExpired:
            self.diagnostics.record("ack", "answerCallbackQuery", self.diagnostics.timer() - started, outcome="skipped")
            return False
        except TransportFailure:
            # A spinner acknowledgment is not the business action. Do not retry
            # an action or block navigation behind another acknowledgment call.
            self.diagnostics.record("ack", "answerCallbackQuery", self.diagnostics.timer() - started, outcome="uncertain")
            return valid
        self.diagnostics.record("ack", "answerCallbackQuery", self.diagnostics.timer() - started)
        return valid

    def process(self, raw, *, acknowledged=None, defer=False):
        if acknowledged is None:
            acknowledged = self.acknowledge(raw)
        if not acknowledged:
            return []
        incoming = normalize_update(raw, self.allowed_users)
        if incoming is None:
            return []
        started = self.diagnostics.timer()
        messages = self.ui.handle(incoming.event)
        self.diagnostics.record("handle", "interaction", self.diagnostics.timer() - started)
        if not defer:
            self.send(messages)
        return messages

    def read(self, method, payload):
        if method not in {"getWebhookInfo", "getUpdates"}:
            raise PreviewError("Read recovery cannot submit or replay messages.")
        for attempt in range(3):
            try:
                return self.client.call(method, payload)
            except TransportFailure as exc:
                if not exc.recoverable or exc.operation != method:
                    raise
                self.diagnostics.record("recovery", method, stage="retry" if attempt < 2 else "exhausted", outcome="failed")
                if attempt == 2:
                    raise
                self.sleeper(0.2 * (attempt + 1))

    def run(self, should_stop: Callable[[], bool] = lambda: False) -> None:
        try:
            info = self.read("getWebhookInfo", {})
            if not isinstance(info, dict) or not isinstance(info.get("url"), str):
                raise PreviewError("Webhook status could not be verified; preview stopped.")
            if info["url"]:
                raise PreviewError("An existing webhook conflicts with local polling. Nothing was changed; ask the owner how to proceed.")
            while not should_stop():
                parameters = {"timeout": 2, "allowed_updates": ["message", "callback_query"]}
                if self.offset is not None:
                    parameters["offset"] = self.offset
                cycle_started = self.diagnostics.timer()
                updates = self.read("getUpdates", parameters)
                fetched_at = self.diagnostics.timer()
                if not isinstance(updates, list):
                    raise PreviewError("Telegram returned an invalid update list; preview stopped.")
                batch, seen = [], set()
                for raw in updates:
                    uid = raw.get("update_id") if isinstance(raw, dict) else None
                    if type(uid) is int and uid >= 0 and uid not in seen and (self.offset is None or uid >= self.offset):
                        seen.add(uid)
                        self.diagnostics.record("fetched_wait", "interaction", self.diagnostics.timer() - fetched_at)
                        batch.append((uid, raw, self.acknowledge(raw)))
                messages = []
                for uid, raw, acknowledged in batch:
                    if should_stop():
                        return
                    messages.extend(self.process(raw, acknowledged=acknowledged, defer=True))
                self.send(messages)
                self.diagnostics.record("ui_batch", "batch", self.diagnostics.timer() - fetched_at)
                if batch:
                    self.offset = max(uid for uid, _, _ in batch) + 1
                self.diagnostics.record("poll_cycle", "batch", self.diagnostics.timer() - cycle_started)
                if self.driver:
                    self.driver.tick()
                self.send(self.ui.poll())
        finally:
            self.client.close()

