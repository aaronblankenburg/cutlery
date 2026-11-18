"""Optional notifications when a job ends or pauses, Claude asks for a domain, or an analysis is done: a webhook (HTTP
POST with JSON), MQTT, and/or the user's Telegram account (telegram.py).

MQTT is a minimal MQTT 3.1.1 publisher (QoS 0, optional user and password) so that no broker library is needed;
a broker without authentication works too. Failures are logged and never affect the job.
"""
from __future__ import annotations

import json
import logging
import socket
import struct
import threading
import urllib.request

log = logging.getLogger("cutlery.notify")


def _remaining_length(n: int) -> bytes:
    out = bytearray()
    while True:
        byte, n = n % 128, n // 128
        out.append(byte | (0x80 if n else 0))
        if not n:
            return bytes(out)


def _utf8(text: str) -> bytes:
    data = text.encode("utf-8")
    return struct.pack(">H", len(data)) + data


def mqtt_packets(client_id: str, topic: str, payload: bytes, user: str | None = None, password: str | None = None,
                 keepalive: int = 30) -> tuple[bytes, bytes, bytes]:
    """CONNECT, PUBLISH (QoS 0) and DISCONNECT packets."""
    flags = 0x02 | (0x80 if user else 0) | (0x40 if user and password else 0)       # clean session
    var = _utf8("MQTT") + bytes([4, flags]) + struct.pack(">H", keepalive)
    body = _utf8(client_id) + (_utf8(user) if user else b"") + (_utf8(password) if user and password else b"")
    connect = bytes([0x10]) + _remaining_length(len(var) + len(body)) + var + body
    pub = _utf8(topic) + payload
    publish = bytes([0x30]) + _remaining_length(len(pub)) + pub
    return connect, publish, b"\xe0\x00"


def mqtt_publish(host, port, topic, payload: bytes, user=None, password=None, timeout=10) -> None:
    connect, publish, disconnect = mqtt_packets("cutlery", topic, payload, user, password)
    with socket.create_connection((host, port), timeout=timeout) as s:
        s.sendall(connect)
        ack = s.recv(4)
        if len(ack) < 4 or ack[0] != 0x20 or ack[3] != 0:
            raise OSError(f"MQTT broker refused the connection (CONNACK {ack.hex()})")
        s.sendall(publish)
        s.sendall(disconnect)


class Notifier:
    def __init__(self, cfg, telegram=None):
        self.cfg, self.telegram = cfg, telegram

    @property
    def enabled(self) -> bool:
        return bool(self.cfg.webhook_url or self.cfg.mqtt_host)

    def _url(self, project) -> str:
        return f"\n{self.cfg.public_url.rstrip('/')}/projects/{project['id']}" if self.cfg.public_url else ""

    def _cut(self, job) -> str:
        import re
        m = re.findall(r"\d+", job.get("cut") or "")
        return m[-1] if m else (job.get("cut") or "")

    def egress_asked(self, job, project, domain) -> None:
        if self.telegram:
            data = f"egress:{project['id']}:{domain}"
            buttons = [("telegram.allow", data + ":1"), ("telegram.refuse", data + ":0")] if len(data) <= 60 else None
            self.telegram.send("telegram.msg.egress", buttons, project=project["name"], domain=domain,
                               url=self._url(project))
        if self.enabled:
            data = {"event": "egress_asked", "project": {"id": project["id"], "name": project["name"]},
                    "job": {"id": job["id"], "cut": job.get("cut")}, "domain": domain}
            threading.Thread(target=self.send, args=(data,), daemon=True).start()

    def analysis_done(self, project, clips: int) -> None:
        if self.telegram:
            self.telegram.send("telegram.msg.analysis_done", project=project["name"], n=clips, url=self._url(project))

    def message(self, job, project) -> dict:
        return {"event": f"job_{job['state']}", "project": {"id": project["id"], "name": project["name"]},
                "job": {k: job.get(k) for k in ("id", "kind", "cut", "state", "reason", "model", "resume_after",
                                                "cost_usd", "turns", "started", "ended")},
                "message": (job.get("message") or "")[:500],
                "url": f"{self.cfg.public_url.rstrip('/')}/projects/{project['id']}/footage" if self.cfg.public_url else None}

    def job_ended(self, job, project) -> None:
        if self.telegram and job.get("state") in ("done", "paused", "failed"):
            self.telegram.send(f"telegram.msg.job_{job['state']}", project=project["name"], n=self._cut(job),
                               message=(job.get("message") or job.get("reason") or "")[:300], url=self._url(project))
        if not self.enabled:
            return
        data = self.message(job, project)
        threading.Thread(target=self.send, args=(data,), daemon=True).start()

    def send(self, data: dict) -> None:
        body = json.dumps(data, ensure_ascii=False).encode()
        if self.cfg.webhook_url:
            try:
                req = urllib.request.Request(self.cfg.webhook_url, data=body, method="POST",
                                             headers={"content-type": "application/json", "user-agent": "cutlery"})
                with urllib.request.urlopen(req, timeout=15) as r:
                    r.read()
            except Exception as e:
                log.warning("webhook failed: %s", e)
        if self.cfg.mqtt_host:
            try:
                mqtt_publish(self.cfg.mqtt_host, self.cfg.mqtt_port, self.cfg.mqtt_topic, body, self.cfg.mqtt_user,
                             self.cfg.mqtt_password)
            except Exception as e:
                log.warning("MQTT failed: %s", e)
