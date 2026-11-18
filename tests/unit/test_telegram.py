"""Telegram notifications against a fake Bot API."""
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from cutlery.telegram import Telegram, TelegramError


class FakeBot:
    def __init__(self):
        self.sent, self.updates = [], []
        bot = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_POST(self):
                token, method = self.path.split("/")[1][3:], self.path.split("/")[2]
                body = json.loads(self.rfile.read(int(self.headers["content-length"])) or b"{}")
                if token != "123:abc":
                    return self.reply(401, {"ok": False, "description": "Unauthorized"})
                if method == "getMe":
                    return self.reply(200, {"ok": True, "result": {"username": "cutlery_test_bot"}})
                if method == "getUpdates":
                    end = time.time() + 2
                    while not bot.updates and time.time() < end:
                        time.sleep(0.02)
                    out, bot.updates = bot.updates, []
                    return self.reply(200, {"ok": True, "result": out})
                bot.sent.append((method, body))
                return self.reply(200, {"ok": True, "result": {}})

            def reply(self, code, obj):
                data = json.dumps(obj).encode()
                self.send_response(code)
                self.send_header("content-length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"

    def wait(self, n, timeout=5):
        end = time.time() + timeout
        while len(self.sent) < n and time.time() < end:
            time.sleep(0.02)
        assert len(self.sent) >= n, self.sent
        return self.sent


@pytest.fixture
def bot():
    b = FakeBot()
    yield b
    b.server.shutdown()


def test_connect_notify_and_answer(cfg, bot):
    tg = Telegram(cfg, env={}, api=bot.url)
    with pytest.raises(TelegramError):
        tg.set_token("123:wrong")
    assert tg.set_token("123:abc")["bot"] == "cutlery_test_bot"
    assert oct((cfg.data_dir / "telegram.json").stat().st_mode)[-3:] == "600"
    link = tg.link_code("de")
    assert link.startswith("https://t.me/cutlery_test_bot?start=")
    code = link.split("=", 1)[1]
    bot.updates.append({"update_id": 1, "message": {"chat": {"id": 77}, "from": {"username": "aaron"}, "text": "/start wrong"}})
    bot.updates.append({"update_id": 2, "message": {"chat": {"id": 42}, "from": {"username": "aaron"}, "text": f"/start {code}"}})
    sent = bot.wait(2)
    assert tg.status()["linked"] and tg.status()["user"] == "aaron"
    assert sent[0][1]["chat_id"] == 77 and "not connected" in sent[0][1]["text"]
    assert sent[1][1]["chat_id"] == 42 and "verbunden" in sent[1][1]["text"]
    # a question with buttons, answered in Telegram
    answers = []
    tg.on_answer = lambda *a: answers.append(a)
    tg.send("telegram.msg.egress", [("telegram.allow", "egress:p1:example.org:1")], project="Namibia",
            domain="example.org", url="")
    sent = bot.wait(3)
    assert "example.org" in sent[2][1]["text"] and sent[2][1]["reply_markup"]["inline_keyboard"][0][0]["text"] == "Erlauben"
    bot.updates.append({"update_id": 3, "callback_query": {"id": "q", "data": "egress:p1:example.org:1",
                        "message": {"chat": {"id": 42}, "message_id": 5, "text": "?"}}})
    bot.wait(5)
    assert answers == [("p1", "example.org", True)]
    # another chat cannot answer
    bot.updates.append({"update_id": 4, "callback_query": {"id": "r", "data": "egress:p1:evil.org:1",
                        "message": {"chat": {"id": 77}, "message_id": 6, "text": "?"}}})
    bot.wait(6)
    assert answers == [("p1", "example.org", True)]
    tg.close()


def test_without_token_nothing_is_sent(cfg):
    tg = Telegram(cfg, env={})
    tg.send("telegram.msg.test")                     # no error, no thread to a server
    assert tg.status() == {"configured": False, "from_env": False, "bot": None, "linked": False, "user": None,
                           "error": None}
