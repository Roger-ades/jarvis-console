"""The inbox: what waits for the user, whatever the discussion, window, account or project (docs/boite-de-reception.md).

Nothing here is stored: the entries are computed from the tasks (their approvals, expired approvals,
displays waiting for a click, turns ended and not read yet), the routines' runs and the notes' reminders.
Only the marks live elsewhere: `read_at` on a task, `read` on a routine run, the lists a dismiss empties.
Claude never sees the inbox: no prompt, no tool.

Three sections, in this order: "todo" (blocks or waits for a decision), "read" (arrived while the user
looked elsewhere), "reminder" (the notes' reminders due). Above them, "head": the latest result of the
routines set to show at the top (the morning briefs), until their next run; it is not counted.
"""
from __future__ import annotations

import re

SECTIONS = ("head", "todo", "read", "reminder")
COUNTED = ("todo", "read", "reminder")
APPROVE = ("hook", "permission")        # tool calls: approved or refused from the inbox
ENDED = ("done", "error", "interrupted")  # a cancelled task never comes: the user stopped it
CHOICE_BLOCKS = ("choix", "actions", "formulaire")    # display blocks that wait for a click
EXCERPT = 300
RUN_STATUSES = ("non lancée", "manquée", "reportée")

# todo: what expires first, then what was refused for lack of an answer, the displays, the failures
_RANK = {"hook": 0, "permission": 0, "question": 1, "plan": 1, "proposal": 1, "expired": 2, "choice": 3,
         "error": 4, "interrupted": 4}


def plain(text: str, limit: int = EXCERPT) -> str:
    """A short plain-text excerpt of Markdown (an answer may quote a mail or a page: no link, no image)."""
    s = re.sub(r"```.*?```", " ", text or "", flags=re.S)
    s = re.sub(r"!\[[^\]]*\]\([^)]*\)", " ", s)
    s = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", s)
    s = re.sub(r"<[^>]+>", " ", s)
    s = re.sub(r"^[ \t]*(?:#{1,6}|>|[-*+]|\d+[.)])[ \t]+", "", s, flags=re.M)
    s = re.sub(r"\*\*|__|~~|`|\*|(?<!\w)_|_(?!\w)", "", s)
    s = " ".join(s.replace("|", " ").split())
    return s if len(s) <= limit else s[:limit - 1].rstrip() + "…"


def _first_line(text: str, limit: int = 200) -> str:
    line = next((x.strip() for x in str(text or "").splitlines() if x.strip()), "")
    return plain(line, limit)


def _approval_summary(a: dict) -> str:
    kind, inp = a.get("kind"), a.get("input") or {}
    if kind == "question":
        qs = inp.get("questions") if isinstance(inp, dict) else None
        q = qs[0] if isinstance(qs, list) and qs and isinstance(qs[0], dict) else {}
        return _first_line(q.get("question") or "Question de Claude")
    if kind == "plan":
        return _first_line(inp.get("plan") if isinstance(inp, dict) else "") or "Plan à approuver"
    if kind == "proposal":
        return _first_line(a.get("reason") or "Proposition de Claude")
    return a.get("target") or a.get("tool") or ""


def expired_label(rec: dict) -> str:
    """What was refused for lack of an answer, in a few words (the entry, and the message of Reprendre)."""
    kind = rec.get("kind")
    if kind == "question":
        return "ta question"
    if kind == "plan":
        return "ton plan"
    if kind == "proposal":
        return "ta proposition"
    target = rec.get("target") or ""
    return f"{rec.get('tool') or 'outil'}{f' · {target}' if target else ''}"


def resume_message(rec: dict, timeout_min: int) -> str:
    """Reprendre: the console's own words, never a text from Claude or from something it read."""
    return (f"[Console JARVIS] L'utilisateur est de retour. Ta demande « {expired_label(rec)[:200]} » a été refusée "
            f"faute de réponse dans le délai de validation ({timeout_min} min). Si elle est toujours utile, refais-la "
            "maintenant : l'utilisateur est là pour la valider.")


def entries(tasks, routines, notes, *, now: float, since: float | None, approval_timeout: float,
            profiles: dict | None = None, light: bool = False) -> list[dict]:
    """Every entry, sorted by section then urgency. `since`: what ended before is read (None: only what
    is live, the approvals waiting and the reminders due). `light`: no excerpt nor input (the counters)."""
    profiles = profiles or {}
    out: list[dict] = []
    after = since if since is not None else float("inf")
    groups: dict[str, list[dict]] = {}
    heads = {r.get("id"): r for r in routines if r.get("headline")}
    latest: dict[str, dict] = {}   # the latest result of each routine shown at the top

    def base(t: dict) -> dict:
        pid = t.get("profile") or ""
        prof = profiles.get(pid) or {}
        return {"task_id": t["id"], "title": t.get("title") or "", "profile": pid,
                "profile_name": prof.get("name") or t.get("profile_name") or "",
                "color": prof.get("color") or t.get("color") or "", "folder": t.get("workdir") or "",
                "routine": t.get("routine") or None}

    for t in tasks:
        tid = t["id"]
        for a in t.get("pending") or []:
            kind = a.get("kind") or "permission"
            e = {"id": f"valider:{tid}:{a.get('id')}", "section": "todo", "kind": kind, "ts": a.get("created") or now,
                 "expires": (a.get("created") or now) + approval_timeout, **base(t),
                 "summary": _approval_summary(a), "tool": a.get("tool") or "", "target": a.get("target") or "",
                 "reason": a.get("reason") or "",
                 "actions": ["approve", "deny", "open"] if kind in APPROVE else ["open"]}
            if kind in APPROVE and not light:
                e["input"] = a.get("input")
            out.append(e)
        for rec in t.get("expired") or []:
            if (rec.get("ts") or 0) < after:
                continue
            out.append({"id": f"expiree:{tid}:{rec.get('aid')}", "section": "todo", "kind": "expired",
                        "ts": rec.get("ts"), **base(t), "summary": expired_label(rec), "tool": rec.get("tool") or "",
                        "target": rec.get("target") or "", "approval_kind": rec.get("kind") or "",
                        "actions": ["resume" if t.get("session_started") else "retry", "open", "dismiss"]})
        for d in t.get("waiting_displays") or []:
            if (d.get("ts") or 0) < after:
                continue
            out.append({"id": f"affichage:{tid}:{d.get('key')}", "section": "todo", "kind": "choice",
                        "ts": d.get("ts"), "key": d.get("key"), **base(t), "summary": d.get("titre") or "",
                        "actions": ["open", "dismiss"]})
        ended = t.get("ended") or 0
        rid = (t.get("routine") or {}).get("id")
        if rid in heads and t.get("status") == "done":
            if ended > ((latest.get(rid) or {}).get("ended") or 0):
                latest[rid] = t
            continue   # shown at the top, not among the results to read
        if t.get("status") not in ENDED or ended < after or ended <= (t.get("read_at") or 0) or t.get("origin") == "reglage":
            continue   # (a search made from the configuration answers there)
        if t["status"] != "done":
            out.append({"id": f"fin:{tid}", "section": "todo", "kind": t["status"], "ts": ended, **base(t),
                        "summary": plain(t.get("error") or "", EXCERPT) if not light else "",
                        "actions": ["retry", "open", "dismiss"]})
        elif (t.get("routine") or {}).get("id"):
            groups.setdefault(t["routine"]["id"], []).append(t)
        else:
            out.append({"id": f"fin:{tid}", "section": "read", "kind": "done", "ts": ended, **base(t),
                        "excerpt": "" if light else plain(t.get("result") or ""), "actions": ["open", "read"]})

    # the results of a routine, together: its latest one shown, all marked read at once
    for rid, group in groups.items():
        group.sort(key=lambda t: t.get("ended") or 0, reverse=True)
        last = group[0]
        out.append({"id": f"routine:{rid}", "section": "read", "kind": "routine", "ts": last.get("ended"),
                    **base(last), "title": (last.get("routine") or {}).get("name") or last.get("title") or "",
                    "count": len(group), "task_ids": [t["id"] for t in group],
                    "excerpt": "" if light else plain(last.get("result") or ""), "actions": ["open", "read"]})

    for rid, t in latest.items():
        if t.get("headline_hidden"):
            continue
        r = heads[rid]
        out.append({"id": f"une:{rid}", "section": "head", "kind": "headline", "ts": t.get("ended"), **base(t),
                    "title": r.get("name") or t.get("title") or "", "routine": {"id": rid, "name": r.get("name")},
                    "brief": r.get("brief") or "", "display": t.get("last_display") or None,
                    "unread": (t.get("ended") or 0) > (t.get("read_at") or 0),
                    "excerpt": "" if light else plain(t.get("result") or "", 600), "actions": ["open", "hide"]})

    for r in routines:
        for run in r.get("runs") or []:
            if run.get("read") is not False or run.get("status") not in RUN_STATUSES or (run.get("ts") or 0) < after:
                continue
            prof = profiles.get(r.get("profile") or "") or {}
            out.append({"id": f"execution:{r.get('id')}:{run.get('ts')}", "section": "read", "kind": "run",
                        "ts": run.get("ts"), "task_id": run.get("task_id"), "title": r.get("name") or "",
                        "profile": r.get("profile") or "", "profile_name": prof.get("name") or "",
                        "color": prof.get("color") or "", "folder": r.get("workdir") or "",
                        "routine": {"id": r.get("id"), "name": r.get("name")}, "status": run.get("status"),
                        "summary": run.get("error") or "", "actions": ["routine", "run", "read"]})

    for n in notes:
        at = n.get("remind_at")
        if not at or n.get("reminded") or at > now:
            continue
        prof = profiles.get(n.get("profile") or "") or {}
        out.append({"id": f"rappel:{n.get('id')}", "section": "reminder", "kind": "reminder", "ts": at,
                    "note_id": n.get("id"), "title": _first_line(n.get("text") or "", 120),
                    "profile": n.get("profile") or "", "profile_name": prof.get("name") or "",
                    "color": prof.get("color") or "", "folder": n.get("folder") or "",
                    "actions": ["open", "later", "dismiss"]})

    def key(e: dict):
        sec = SECTIONS.index(e["section"])
        if e["section"] == "todo":
            rank = _RANK.get(e["kind"], 5)
            return (sec, rank, e["expires"] if rank == 0 else -(e.get("ts") or 0))
        if e["section"] == "reminder":
            return (sec, 0, e.get("ts") or 0)
        return (sec, 0, -(e.get("ts") or 0))
    out.sort(key=key)
    return out


def counts(items: list[dict]) -> dict:
    return {s: sum(1 for e in items if e["section"] == s) for s in COUNTED}
