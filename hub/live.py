"""Présence et événements du hub. La présence vit en mémoire, pas dans la base."""
from __future__ import annotations

import queue
import threading
import time

PRESENCE_TTL = 45


class Live:
    def __init__(self):
        self._lock = threading.Lock()
        self._seq = 0
        self.presence: dict[tuple[str, str], dict] = {}
        self._subs: list[queue.Queue] = []

    def publish(self, kind: str, payload: dict) -> dict:
        with self._lock:
            self._seq += 1
            event = {"id": self._seq, "kind": kind, "ts": time.time(), **payload}
            stale = []
            for sub in self._subs:
                try:
                    sub.put_nowait(event)
                except queue.Full:
                    stale.append(sub)
            for sub in stale:
                self._subs.remove(sub)
            return event

    def subscribe(self) -> queue.Queue:
        sub = queue.Queue(maxsize=200)
        with self._lock:
            self._subs.append(sub)
        return sub

    def unsubscribe(self, sub: queue.Queue):
        with self._lock:
            if sub in self._subs:
                self._subs.remove(sub)

    def beat(self, user: str, projects: list[dict]) -> list[dict]:
        now = time.time()
        sent = []
        with self._lock:
            for item in projects:
                key = (item["id"], user)
                state = {
                    "project": item["id"], "user": user, "sessions": int(item.get("sessions") or 0),
                    "files": list(item.get("files") or []), "ts": now,
                }
                self.presence[key] = state
                sent.append(state)
        for state in sent:
            self.publish("presence", state)
        return sent

    def snapshot(self, projects: set[str]) -> list[dict]:
        now = time.time()
        with self._lock:
            dead = [key for key, state in self.presence.items() if now - state["ts"] > PRESENCE_TTL]
            for key in dead:
                self.presence.pop(key, None)
            return [dict(state) for (project, _user), state in self.presence.items() if project in projects]
