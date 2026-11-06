"""Minimal Telegram HTTPS transport and strict normalization; no startup I/O."""

from collections.abc import Mapping
from dataclasses import dataclass
import http.client
import json
import os
import re
import ssl
from typing import Protocol
from urllib import error, request
from urllib.parse import urlsplit

from .models import InputEvent
from .diagnostics import Diagnostics


class PreviewError(RuntimeError):
    """Only constant, credential-free operational messages belong here."""


class CallbackExpired(PreviewError):
    """An unacknowledgeable callback is discarded, never executed or replayed."""


class EditNotModified(PreviewError):
    """A known-id edit already has the requested text/keyboard, not a new send."""


class TransportFailure(PreviewError):
    def __init__(self, operation, stage, kind="other"):
        self.operation, self.stage = operation, stage
        self.kind = kind
        self.recoverable = operation in {"getUpdates", "getWebhookInfo", "answerCallbackQuery",
                                         "editMessageText", "editMessageReplyMarkup"}
        self.ambiguous = operation == "sendMessage"
        super().__init__(f"Telegram {operation} failed at {stage} ({kind}); " +
                         ("submission outcome is unknown; no automatic resend." if self.ambiguous
                          else "bounded recovery is available for this operation."))


@dataclass(frozen=True, repr=False)
class PreviewSettings:
    token: str
    allowed_users: frozenset[int]

    @classmethod
    def from_environment(cls, environ: Mapping[str, str] | None = None):
        source = os.environ if environ is None else environ
        token = source.get("TELEGRAM_BOT_TOKEN", "")
        ids = source.get("TELEGRAM_ALLOWED_USER_IDS", "")
        if not token or not re.fullmatch(r"[A-Za-z0-9:_-]{10,512}", token):
            raise PreviewError("Set TELEGRAM_BOT_TOKEN in the local environment; its value is never printed.")
        # This ticket authorizes only the owner's one account, not a public bot.
        if not re.fullmatch(r"[1-9][0-9]{0,18}", ids.strip()):
            raise PreviewError("Set TELEGRAM_ALLOWED_USER_IDS to the owner's single numeric Telegram User ID.")
        return cls(token, frozenset({int(ids.strip())}))


class TelegramClient(Protocol):
    def call(self, method: str, payload: dict): ...
    def close(self) -> None: ...


class _NoRedirect(request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class _PersistentHTTPS:
    """One serial TLS connection, fixed Telegram host, no replay or redirects.

    Fully consumed responses permit reuse. A stale/broken socket is discarded;
    only the outer operation-specific recovery policy may retry a request.
    """

    def __init__(self, connection_factory=http.client.HTTPSConnection):
        self.factory = connection_factory
        self.context = ssl.create_default_context()
        self.context.set_alpn_protocols(["http/1.1"])
        self.connection = None

    def open(self, req, timeout):
        parsed = urlsplit(req.full_url)
        if (parsed.scheme != "https" or parsed.netloc != "api.telegram.org"
                or parsed.query or parsed.fragment or not parsed.path.startswith("/bot")):
            raise PreviewError("Unsupported Telegram transport destination.")
        if self.connection is None:
            self.connection = self.factory("api.telegram.org", timeout=timeout, context=self.context)
        connection = self.connection
        connection.timeout = timeout
        if connection.sock is not None:
            connection.sock.settimeout(timeout)
        try:
            connection.request("POST", parsed.path, body=req.data, headers=dict(req.header_items()))
            response = connection.getresponse()
            if response.status >= 300:
                # The outer boundary reads/classifies safely, never prints this URL.
                raise error.HTTPError(req.full_url, response.status, "Telegram HTTP rejection", response.headers, response)
            return response
        except error.HTTPError:
            raise  # Keep its response readable until the safe outer boundary.
        except Exception:
            self.discard()
            raise

    def discard(self):
        if self.connection is not None:
            self.connection.close()
            self.connection = None

    def close(self):
        self.discard()


class TelegramHTTP:
    """Token-bearing URLs never leave this class through logs or exceptions.

    No proxies, redirects, credential files, automatic retries or debug logging.
    A failed/unknown send stops the preview instead of blindly replaying it.
    """

    METHODS = frozenset({"getWebhookInfo", "getUpdates", "sendMessage",
                         "editMessageText", "editMessageReplyMarkup", "answerCallbackQuery"})
    RESPONSE_LIMIT = 2 * 1024 * 1024

    def __init__(self, token: str, *, opener=None, diagnostics=None):
        self._token = token
        self._opener = opener if opener is not None else _PersistentHTTPS()
        self._closed = False
        self.diagnostics = diagnostics or Diagnostics()

    def _rejection(self, method, code, description=""):
        if method == "answerCallbackQuery" and code == 400:
            return CallbackExpired("Callback acknowledgment expired or was rejected; no action was executed.")
        if method in {"editMessageText", "editMessageReplyMarkup"} and code == 400 and "message is not modified" in description.lower():
            return EditNotModified("The known message already has the requested state.")
        if method in {"getUpdates", "getWebhookInfo"} and type(code) is int and code >= 500:
            return TransportFailure(method, "api", "api")
        return self._api_error(code)

    @staticmethod
    def _api_error(code) -> PreviewError:
        if code == 409:
            return PreviewError("Telegram conflict: an existing webhook or another poller is active. No configuration was changed.")
        if code == 401:
            return PreviewError("Telegram rejected authorization. Check the local token without printing it.")
        if code == 429:
            return PreviewError("Telegram rate limit reached. Preview stopped; wait before an owner-approved restart.")
        return PreviewError("Telegram rejected the request. Preview stopped; no automatic replay was attempted.")

    def call(self, method: str, payload: dict):
        if self._closed or method not in self.METHODS:
            raise PreviewError("Unsupported Telegram operation or closed transport.")
        url = "https://api.telegram.org/bot" + self._token + "/" + method
        started = self.diagnostics.timer()
        stage, outcome, kind = "open", "ok", None
        try:
            req = request.Request(url, data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                                  headers={"Content-Type": "application/json"}, method="POST")
            with self._opener.open(req, timeout=2 if method == "answerCallbackQuery" else 8) as response:
                stage = "read"
                body = response.read(self.RESPONSE_LIMIT + 1)
            if len(body) > self.RESPONSE_LIMIT:
                raise PreviewError("Telegram response exceeded the preview safety bound.")
            stage = "decode"
            result = json.loads(body)
            if not isinstance(result, dict) or type(result.get("ok")) is not bool:
                raise PreviewError("Telegram returned an invalid response; preview stopped.")
            if not result["ok"]:
                stage = "api"
                raise self._rejection(method, result.get("error_code"), str(result.get("description", "")))
            if "result" not in result:
                raise PreviewError("Telegram returned no result; publication is not confirmed.")
            return result["result"]
        except error.HTTPError as exc:
            # Do not stringify HTTPError: it contains the token-bearing URL.
            code = exc.code
            description = ""
            try:
                body = json.loads(exc.read(self.RESPONSE_LIMIT + 1))
                if isinstance(body, dict):
                    description = str(body.get("description", ""))
            except Exception:
                pass
            exc.close()
            stage, outcome, kind = "api", "failed", "api"
            raise self._rejection(method, code, description) from None
        except PreviewError:
            outcome = "failed"
            raise
        except Exception as exc:
            # No provider body, submitted text, request URL or exception details.
            outcome = "uncertain"
            cause = getattr(exc, "reason", exc)
            kind = ("timeout" if isinstance(cause, TimeoutError) else "tls" if isinstance(cause, ssl.SSLError)
                    else "decode" if stage == "decode" else "connection" if isinstance(exc, OSError) else "other")
            raise TransportFailure(method, stage, kind) from None
        finally:
            if outcome != "ok" and isinstance(self._opener, _PersistentHTTPS):
                self._opener.discard()
            self.diagnostics.record("http", method, self.diagnostics.timer() - started, stage=stage,
                                    outcome=outcome, failure_kind=kind)

    def close(self) -> None:
        self._closed = True
        self._token = ""
        closer = getattr(self._opener, "close", None)
        if closer:
            closer()


def _positive_int(value) -> bool:
    return type(value) is int and value > 0


@dataclass(frozen=True, repr=False)
class Incoming:
    event: InputEvent
    callback_id: str | None = None
    source_message_id: int | None = None


def normalize_update(raw, allowed_users: frozenset[int]) -> Incoming | None:
    """Only ordinary owner/private messages and mapped callback candidates.

    This input comes from authenticated Bot API HTTPS, never a public webhook.
    Business/guest/inline/anonymous and unsupported updates are not admitted.
    """
    if not isinstance(raw, dict) or type(raw.get("update_id")) is not int or raw["update_id"] < 0:
        return None
    if ("message" in raw) == ("callback_query" in raw):
        return None
    callback = raw.get("callback_query")
    if callback is not None:
        if not isinstance(callback, dict):
            return None
        message, sender = callback.get("message"), callback.get("from")
    else:
        message = raw.get("message")
        sender = message.get("from") if isinstance(message, dict) else None
    if not isinstance(message, dict) or not isinstance(sender, dict):
        return None
    if any(message.get(k) for k in ("sender_chat", "business_connection_id", "guest_query_id", "is_ephemeral")):
        return None
    user = sender.get("id")
    chat = message.get("chat")
    if not _positive_int(user) or user not in allowed_users or sender.get("is_bot") is not False:
        return None
    if not isinstance(chat, dict) or chat.get("type") != "private" or type(chat.get("id")) is not int or chat["id"] != user:
        return None
    if not _positive_int(message.get("message_id")):
        return None
    if callback is not None:
        data, cid = callback.get("data"), callback.get("id")
        if not isinstance(data, str) or not isinstance(cid, str) or not cid or len(cid) > 256:
            return None
        try:
            if not 1 <= len(data.encode("utf-8")) <= 64:
                return None
        except UnicodeError:
            return None
        event = InputEvent(user, user, raw["update_id"], callback=data)
        return Incoming(event, cid, message["message_id"])
    text = message.get("text")
    if not isinstance(text, str):
        return None
    if re.fullmatch(r"/start(?:@[A-Za-z0-9_]+)?(?:\s.*)?", text):
        text = "/start"
    return Incoming(InputEvent(user, user, raw["update_id"], text=text))
