"""Notifications to the user's Telegram account through a bot the operator set up (optional).

The operator creates a bot with @BotFather and enters its token on the settings page (or sets
CUTLERY_TELEGRAM_BOT_TOKEN / _FILE). The user connects their account there: "Connect Telegram" opens
t.me/<bot>?start=<one-time code>, Telegram sends the bot "/start <code>", and the chat is linked. From then on Cutlery
sends a message when a cut is done, pauses or fails, when the analysis is done, and when Claude asks for a domain
(with buttons to allow or refuse it).

Updates are read by long polling (getUpdates), so the bot needs no public address. Only the linked chat is answered;
the state (token, chat, language) is DATA/telegram.json, readable by the app only.
"""
from __future__ import annotations

import json
import logging
import os
import pathlib
import secrets
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

from . import i18n
from .storage import write_atomic

log = logging.getLogger("cutlery.telegram")
API = "https://api.telegram.org"
CODE_TTL = 15 * 60


class TelegramError(Exception):
    pass


class Telegram:
    def __init__(self, cfg, env=None, api=API):
        self.cfg, self.api = cfg, api
        env = os.environ if env is None else env
        self.env_token = (env.get("CUTLERY_TELEGRAM_BOT_TOKEN") or "").strip()
        f = env.get("CUTLERY_TELEGRAM_BOT_TOKEN_FILE")
        if not self.env_token and f:
            try:
                self.env_token = pathlib.Path(f).read_text().strip()
            except OSError as e:
                log.warning("cannot read the Telegram bot token file: %s", e)
        self.file = cfg.data_dir / "telegram.json"
        self.lock = threading.RLock()
        self.codes: dict[str, tuple[float, str]] = {}     # one-time code -> (expiry, language)
        self.on_answer = None                             # (project_id, domain, allow) -> None, set by the app
        self.stop_event = threading.Event()
        self.thread = None
        self.offset = 0

    # ------------------------------------------------------------ state

    def _load(self) -> dict:
        try:
            return json.loads(self.file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def _save(self, d: dict) -> None:
        write_atomic(self.file, json.dumps(d, indent=1).encode())
        os.chmod(self.file, 0o600)

    @property
    def token(self) -> str:
        return self.env_token or self._load().get("token", "")

    def status(self) -> dict:
        d = self._load()
        return {"configured": bool(self.token), "from_env": bool(self.env_token), "bot": d.get("bot"),
                "linked": bool(d.get("chat_id")), "user": d.get("user"), "error": d.get("error")}

    # ------------------------------------------------------------ Bot API

    def call(self, method: str, token: str | None = None, **params):
        data = json.dumps(params).encode()
        req = urllib.request.Request(f"{self.api}/bot{token or self.token}/{method}", data=data, method="POST",
                                     headers={"content-type": "application/json"})
        timeout = params.get("timeout", 0) + 15
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                body = json.loads(r.read())
        except urllib.error.HTTPError as e:
            try:
                body = json.loads(e.read())
            except ValueError:
                body = {"description": str(e)}
            raise TelegramError(body.get("description") or str(e)) from None
        except (OSError, ValueError) as e:
            raise TelegramError(str(e)) from None
        if not body.get("ok"):
            raise TelegramError(body.get("description") or "Telegram refused the request")
        return body.get("result")

    # ------------------------------------------------------------ settings page

    def set_token(self, token: str) -> dict:
        token = (token or "").strip()
        if self.env_token:
            raise TelegramError("the bot token is set in the environment")
        if not token or len(token) > 100 or ":" not in token:
            raise TelegramError("this is not a bot token (it looks like 123456:ABC…)")
        me = self.call("getMe", token=token)
        with self.lock:
            self._save({"token": token, "bot": me.get("username")})
        self.start()
        return self.status()

    def clear(self) -> None:
        with self.lock:
            d = self._load()
            self._save({} if not self.env_token else {"bot": d.get("bot")})

    def unlink(self) -> None:
        with self.lock:
            d = self._load()
            for k in ("chat_id", "user", "lang"):
                d.pop(k, None)
            self._save(d)

    def link_code(self, lang: str) -> str:
        """A deep link that connects the Telegram account which opens it."""
        bot = self._bot()
        code = secrets.token_urlsafe(18)
        with self.lock:
            now = time.time()
            self.codes = {k: v for k, v in self.codes.items() if v[0] > now}
            self.codes[code] = (now + CODE_TTL, lang)
        self.start()
        return f"https://t.me/{bot}?start={code}"

    def _bot(self) -> str:
        d = self._load()
        if d.get("bot"):
            return d["bot"]
        if not self.token:
            raise TelegramError("no bot token")
        bot = self.call("getMe")["username"]
        with self.lock:
            d = self._load()
            d["bot"] = bot
            self._save(d)
        return bot

    # ------------------------------------------------------------ sending

    def send(self, key: str, buttons=None, **values) -> None:
        """A message to the linked chat in its language (in a thread; failures are logged only)."""
        d = self._load()
        if not self.token or not d.get("chat_id"):
            return
        text = i18n.translate(d.get("lang") or "en", key, **values)
        params = {"chat_id": d["chat_id"], "text": text, "disable_web_page_preview": True}
        if buttons:
            params["reply_markup"] = {"inline_keyboard": [[
                {"text": i18n.translate(d.get("lang") or "en", label), "callback_data": data} for label, data in buttons]]}

        def go():
            try:
                self.call("sendMessage", **params)
            except TelegramError as e:
                log.warning("Telegram message failed: %s", e)
        threading.Thread(target=go, daemon=True).start()

    def test(self) -> None:
        d = self._load()
        if not d.get("chat_id"):
            raise TelegramError("no Telegram account is connected")
        self.call("sendMessage", chat_id=d["chat_id"],
                  text=i18n.translate(d.get("lang") or "en", "telegram.msg.test"))

    # ------------------------------------------------------------ receiving

    def start(self) -> None:
        with self.lock:
            if self.thread and self.thread.is_alive() or not self.token:
                return
            self.stop_event.clear()
            self.thread = threading.Thread(target=self._poll, name="telegram", daemon=True)
            self.thread.start()

    def close(self) -> None:
        self.stop_event.set()

    def _poll(self) -> None:
        delay = 5
        while not self.stop_event.is_set() and self.token:
            try:
                updates = self.call("getUpdates", offset=self.offset, timeout=50,
                                    allowed_updates=["message", "callback_query"])
                delay = 5
            except TelegramError as e:
                log.warning("Telegram: %s", e)
                self.stop_event.wait(delay)
                delay = min(delay * 2, 300)
                continue
            for u in updates or []:
                self.offset = max(self.offset, int(u.get("update_id", 0)) + 1)
                try:
                    self.handle(u)
                except Exception:
                    log.exception("Telegram update failed")

    def handle(self, u: dict) -> None:
        d = self._load()
        if "message" in u:
            m = u["message"]
            chat = (m.get("chat") or {}).get("id")
            text = (m.get("text") or "").strip()
            if text.startswith("/start"):
                code = text[6:].strip()
                with self.lock:
                    exp, lang = self.codes.pop(code, (0, None))
                if code and exp > time.time():
                    who = m.get("from") or {}
                    with self.lock:
                        d = self._load()
                        d.update(chat_id=chat, lang=lang, user=who.get("username") or who.get("first_name") or "")
                        self._save(d)
                    self.call("sendMessage", chat_id=chat, text=i18n.translate(lang, "telegram.msg.linked"))
                    log.info("Telegram account connected")
                    return
            if chat != d.get("chat_id"):
                self.call("sendMessage", chat_id=chat, text=i18n.translate("en", "telegram.msg.not_linked"))
            return
        q = u.get("callback_query")
        if q:
            chat = ((q.get("message") or {}).get("chat") or {}).get("id")
            lang = d.get("lang") or "en"
            if chat != d.get("chat_id"):
                self.call("answerCallbackQuery", callback_query_id=q["id"])
                return
            parts = str(q.get("data") or "").split(":")
            if len(parts) == 4 and parts[0] == "egress" and self.on_answer:
                _, project, domain, yes = parts
                self.on_answer(project, domain, yes == "1")
                key = "telegram.msg.allowed" if yes == "1" else "telegram.msg.refused"
                self.call("answerCallbackQuery", callback_query_id=q["id"], text=i18n.translate(lang, key, domain=domain))
                m = q.get("message") or {}
                self.call("editMessageText", chat_id=chat, message_id=m.get("message_id"),
                          text=f"{m.get('text', '')}\n\n→ {i18n.translate(lang, key, domain=domain)}")
                return
            self.call("answerCallbackQuery", callback_query_id=q["id"])
