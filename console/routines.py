"""Routines: tasks the console launches on a schedule (while it is running).

A routine is an ordinary console task created at a given time, with its
profile, preset, model and folder: the same permissions and validations apply,
and each run appears in the history like any other task.
"""
from __future__ import annotations

import re
import secrets
import time
from datetime import datetime, timedelta
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

DAYS = ["lun.", "mar.", "mer.", "jeu.", "ven.", "sam.", "dim."]


class Schedule(BaseModel):
    kind: Literal["daily", "interval", "once"] = "daily"
    time: str = "08:00"                                    # daily: HH:MM local time
    days: list[int] = Field(default_factory=lambda: [0, 1, 2, 3, 4])  # daily: 0 = Monday
    every_min: int = Field(60, ge=5, le=60 * 24 * 7)       # interval
    at: float | None = None                                # once: epoch seconds

    @field_validator("time")
    @classmethod
    def _time(cls, v: str) -> str:
        m = re.fullmatch(r"(\d{1,2}):(\d{2})", v.strip())
        if not m or int(m.group(1)) > 23 or int(m.group(2)) > 59:
            raise ValueError("heure attendue au format HH:MM")
        return f"{int(m.group(1)):02d}:{m.group(2)}"

    @field_validator("days")
    @classmethod
    def _days(cls, v: list[int]) -> list[int]:
        v = sorted({d for d in v if 0 <= d <= 6})
        return v

    @model_validator(mode="after")
    def _check(self):
        if self.kind == "daily" and not self.days:
            raise ValueError("choisis au moins un jour")
        if self.kind == "once" and not self.at:
            raise ValueError("date et heure requises")
        return self

    def label(self) -> str:
        if self.kind == "daily":
            days = "tous les jours" if len(self.days) == 7 else ("en semaine" if self.days == [0, 1, 2, 3, 4] else ", ".join(DAYS[d] for d in self.days))
            return f"{days} à {self.time}"
        if self.kind == "interval":
            m = self.every_min
            return f"toutes les {m // 60} h" if m % 60 == 0 else f"toutes les {m} min"
        return "le " + datetime.fromtimestamp(self.at or 0).strftime("%d/%m/%Y à %H:%M")

    def next_after(self, after: float, last_run: float | None = None) -> float | None:
        if self.kind == "once":
            return self.at if self.at and self.at > after and not last_run else None
        if self.kind == "interval":
            base = last_run or after
            nxt = base + self.every_min * 60
            while nxt <= after:
                nxt += self.every_min * 60
            return nxt
        hh, mm = (int(x) for x in self.time.split(":"))
        start = datetime.fromtimestamp(after)
        for delta in range(0, 8):
            day = start + timedelta(days=delta)
            cand = day.replace(hour=hh, minute=mm, second=0, microsecond=0)
            if cand.weekday() in self.days and cand.timestamp() > after:
                return cand.timestamp()
        return None


class Routine(BaseModel):
    id: str = Field(default_factory=lambda: secrets.token_hex(4))
    name: str = Field(min_length=1, max_length=80)
    prompt: str = Field(min_length=1, max_length=20000)
    profile: str
    preset: str
    model: str = ""
    effort: Literal["", "low", "medium", "high", "xhigh", "max"] = ""
    workdir: str = ""
    schedule: Schedule = Field(default_factory=Schedule)
    enabled: bool = True
    open_window: bool = True
    catch_up: bool = False
    team: bool = False
    created: float = Field(default_factory=time.time)
    last_run: float | None = None
    next_run: float | None = None
    runs: list[dict] = Field(default_factory=list)

    @field_validator("model")
    @classmethod
    def _model(cls, v: str) -> str:
        if v and not re.fullmatch(r"[A-Za-z0-9._:\-\[\]]{1,80}", v):
            raise ValueError("nom de modèle invalide")
        return v

    def public(self) -> dict:
        d = self.model_dump()
        d["schedule_label"] = self.schedule.label()
        return d
