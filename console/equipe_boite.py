"""What the team brings to the inbox (docs/boite-de-reception.md, docs/equipe.md).

The console asks the hub every 30 s (equipe.loop) and keeps only what the inbox needs:
- an invitation: a team project this account was added to and that has no folder on this computer;
- the Fil and the shared conversations of a linked project: what the others wrote or shared since the user looked;
- a project the account lost (removed from its members): the folder can be detached;
- for a hub administrator, a colleague who registered from their JARVIS (the account can be revoked from there);
- a module a colleague sent through the hub: one click installs it (equipe.py), Claude takes no part;
- skills a colleague sent through the hub, with their groups: installed in the account picked (equipe.py).
Each one is signalled once: "Lu" moves the mark to the latest message, "Ignorer" forgets the invitation.
Nothing from the hub reaches Claude: these entries are the console's, like the rest of the inbox.
"""
from __future__ import annotations

import re
import threading
import time

from . import inbox as inbox_mod

KEY = "team_inbox"
KINDS = ("team_invite", "team_message", "team_mention", "team_share", "team_removed", "team_signup", "team_module",
         "team_skills")


class TeamFeed:
    KINDS = KINDS

    def __init__(self, store, changed=lambda: None, installed=lambda info: ""):
        self.store = store
        self.changed = changed            # the engine publishes the inbox again
        self.installed = installed        # what installing a received module does (engine._installed)
        self._lock = threading.Lock()
        state = store.kv_get(KEY, {}) or {}
        # seen: team -> {"msg": ts, "conv": ts} (what the user has read); dismissed: invitations put away
        self.seen: dict[str, dict] = state.get("seen") or {}
        self.dismissed: set[str] = set(state.get("dismissed") or [])
        self.gone_dismissed: set[str] = set(state.get("gone_dismissed") or [])
        self.gone_since: dict[str, float] = state.get("gone_since") or {}
        # accounts whose registration the administrator has seen; None until the first read (the past is not news)
        known = state.get("signups_seen")
        self.signups_seen: set[str] | None = set(known) if known is not None else None
        self.signups: list[dict] = []     # the hub's user.register entries, as last read (administrator only)
        self.people: dict[str, str] = {}  # hub account -> name, for the authors
        self.me: dict = {}
        self.projects: list[dict] = []    # the hub's list, as last read
        self.linked: dict[str, str] = {}  # team -> local folder
        self.messages: dict[str, list] = {}
        self.conversations: dict[str, list] = {}
        self.gone: dict[str, str] = {}    # team linked here that the hub no longer lists -> folder
        self.names: dict[str, str] = {}   # local names of the linked projects
        self.colors: dict[str, str] = {}
        self.modules: list[dict] = []     # modules sent to this account, waiting on the hub
        self.skill_packs: list[dict] = [] # skills sent to this account, waiting on the hub

    # ------------------------------------------------------------ reading the hub
    def poll(self, call, me: dict, linked: dict[str, dict]) -> None:
        """call(method, path) -> json. linked: team -> {"folder", "name", "color"} (the projects of this computer).
        A failed read keeps the last state: the hub may be out of reach for a while."""
        projects = call("GET", "/api/v1/projects").get("projects", [])
        try:
            people = {u["id"]: u.get("name") or u["id"] for u in call("GET", "/api/v1/users").get("users", [])}
        except Exception:  # noqa: BLE001 - the ids then
            people = self.people
        signups = None
        if (me or {}).get("admin"):
            try:
                signups = [e for e in call("GET", "/api/v1/audit").get("entries", []) if e.get("kind") == "user.register"]
            except Exception:  # noqa: BLE001 - the last ones then
                signups = None
        try:
            modules = call("GET", "/api/v1/modules").get("modules", [])
        except Exception:  # noqa: BLE001 - an older hub, or out of reach: the last ones then
            modules = self.modules
        try:
            packs = call("GET", "/api/v1/competences").get("packs", [])
        except Exception:  # noqa: BLE001 - an older hub, or out of reach: the last ones then
            packs = self.skill_packs
        listed = {p["id"] for p in projects}
        messages, conversations = {}, {}
        for team in linked:
            if team not in listed:
                continue
            try:
                messages[team] = call("GET", f"/api/v1/projects/{team}/messages").get("messages", [])
                conversations[team] = call("GET", f"/api/v1/projects/{team}/conversations").get("conversations", [])
            except Exception:  # noqa: BLE001 - one project unreadable: the others still come
                messages[team] = self.messages.get(team, [])
                conversations[team] = self.conversations.get(team, [])
        with self._lock:
            before = self._signature()
            self.me = me or {}
            self.people = people
            self.projects = projects
            self.linked = {t: v["folder"] for t, v in linked.items()}
            self.names = {t: v.get("name") or "" for t, v in linked.items()}
            self.colors = {t: v.get("color") or "" for t, v in linked.items()}
            self.messages, self.conversations = messages, conversations
            self.modules = modules
            self.skill_packs = packs
            self.gone = {t: v["folder"] for t, v in linked.items() if t not in listed}
            for team in messages:
                if team not in self.seen:
                    # first look at this project: its history is not news
                    self.seen[team] = {"msg": _latest(messages[team], "ts"), "conv": _latest(conversations[team], "updated")}
            # an invitation joined, or a project lost then given back: forget the old marks
            self.dismissed &= {p["id"] for p in projects if p["id"] not in linked}
            self.gone_dismissed &= set(self.gone)
            self.gone_since = {t: self.gone_since.get(t) or time.time() for t in self.gone}
            if signups is not None:
                if self.signups_seen is None:
                    self.signups_seen = {e.get("user") for e in signups}
                self.signups = signups
            elif not (me or {}).get("admin"):
                self.signups = []
            self._save()
            changed = self._signature() != before
        if changed:
            self.changed()

    def _signature(self):
        return (tuple(sorted(e["id"] + str(e.get("count", "")) for e in self._entries(light=True))),)

    # ------------------------------------------------------------ the entries
    def entries(self, light: bool = False) -> list[dict]:
        with self._lock:
            return self._entries(light)

    def _entries(self, light: bool) -> list[dict]:
        out = []
        my_id = self.me.get("id") or ""
        for p in self.projects:
            team = p["id"]
            if team in self.linked or team in self.dismissed:
                continue
            by = p.get("added_by") or ""
            by = self.people.get(by, by) if by != my_id else ""
            out.append({
                "id": f"equipe-invitation:{team}", "section": "todo", "kind": "team_invite",
                "ts": p.get("joined") or p.get("created") or 0, "team_id": team, "title": p.get("name") or team,
                "color": p.get("color") or "", "folder": "", "profile": "", "profile_name": "",
                "summary": (f"{by} t'a ajouté à ce projet d'équipe" if by
                            else "Projet d'équipe dont tu es membre, pas encore sur ce poste"),
                "role": p.get("role") or "", "members": p.get("member_count") or 0,
                "actions": ["join", "dismiss"],
            })
        for team, folder in self.linked.items():
            if team in self.gone:
                continue
            seen = self.seen.get(team) or {}
            title = self.names.get(team) or team
            fresh = [m for m in self.messages.get(team, [])
                     if (m.get("ts") or 0) > (seen.get("msg") or 0) and m.get("author") != my_id and not m.get("deleted")]
            if fresh:
                last = max(fresh, key=lambda m: m.get("ts") or 0)
                mention = any(_mentions(m.get("text") or "", self.me) for m in fresh)
                out.append({
                    "id": f"equipe-fil:{team}", "section": "todo" if mention else "read",
                    "kind": "team_mention" if mention else "team_message", "ts": last.get("ts") or 0,
                    "team_id": team, "title": title, "folder": folder, "color": self.colors.get(team) or "",
                    "profile": "", "profile_name": "", "count": len(fresh),
                    "authors": sorted({self._who(m.get("author")) for m in fresh} - {""}),
                    "excerpt": "" if light else f"{self._who(last.get('author'))} : {inbox_mod.plain(last.get('text') or '', 200)}",
                    "upto": last.get("ts") or 0, "actions": ["open", "read"],
                })
            shared = [c for c in self.conversations.get(team, [])
                      if (c.get("updated") or 0) > (seen.get("conv") or 0) and c.get("author") != my_id]
            if shared:
                last = max(shared, key=lambda c: c.get("updated") or 0)
                out.append({
                    "id": f"equipe-partage:{team}", "section": "read", "kind": "team_share",
                    "ts": last.get("updated") or 0, "team_id": team, "title": title, "folder": folder,
                    "color": self.colors.get(team) or "", "profile": "", "profile_name": "", "count": len(shared),
                    "authors": sorted({self._who(c.get("author")) for c in shared} - {""}),
                    "excerpt": "" if light else f"{self._who(last.get('author'))} : « {last.get('title')} »"
                                                + (f" (révision {last.get('rev')})" if (last.get("rev") or 1) > 1 else ""),
                    "upto": last.get("updated") or 0, "actions": ["open", "read"],
                })
        for e in self.signups:
            uid = e.get("user") or ""
            if uid in (self.signups_seen or set()) or uid not in self.people:
                continue    # seen, or revoked since
            name = self.people.get(uid) or (e.get("detail") or {}).get("name") or uid
            out.append({
                "id": f"equipe-inscription:{uid}", "section": "read", "kind": "team_signup", "ts": e.get("ts") or 0,
                "user": uid, "title": name, "folder": "", "color": "", "profile": "", "profile_name": "",
                "summary": f"{name} ({uid}) s'est inscrit sur le hub depuis JARVIS. Il ne voit aucun projet tant "
                           "que personne ne l'y ajoute (Projet → Équipe → Membres).",
                "actions": ["read", "revoke"],
            })
        for m in self.modules:
            who = self._who(m.get("from"))
            version = f" v{m['version']}" if m.get("version") else ""
            try:
                state = "" if light or not m.get("addon") else self.installed({"id": m["addon"], "version": m.get("version") or ""})
            except Exception:  # noqa: BLE001 - the module folder unreadable: say nothing of it
                state = ""
            out.append({
                "id": f"equipe-module:{m['id']}", "section": "todo", "kind": "team_module", "ts": m.get("created") or 0,
                "module_id": m["id"], "addon": m.get("addon") or "", "version": m.get("version") or "",
                "title": m.get("name") or m.get("addon") or "", "folder": "", "color": "", "profile": "",
                "profile_name": "", "from": who, "description": m.get("description") or "",
                "permissions": m.get("permissions") or [], "size": m.get("size") or 0,
                "state": "" if state == "pas encore installé" else state,
                "summary": f"{who} t'envoie le module « {m.get('name') or m.get('addon')} »{version}.",
                "actions": ["install", "dismiss"],
            })
        for pk in self.skill_packs:
            who = self._who(pk.get("from"))
            skills = [x for x in pk.get("skills") or [] if isinstance(x, dict) and x.get("nom")]
            groups = sorted({x.get("groupe") or "" for x in skills} - {""}, key=str.lower)
            what = (f"la compétence « {skills[0]['nom']} »" if len(skills) == 1
                    else f"{len(skills)} compétences")
            where = (f" (groupe{'s' if len(groups) > 1 else ''} {', '.join(g.replace('/', ' › ') for g in groups[:3])}"
                     f"{'…' if len(groups) > 3 else ''})") if groups else ""
            out.append({
                "id": f"equipe-competences:{pk['id']}", "section": "todo", "kind": "team_skills",
                "ts": pk.get("created") or 0, "pack_id": pk["id"], "skills": skills,
                "title": skills[0]["nom"] if len(skills) == 1 else f"{len(skills)} compétences",
                "folder": "", "color": "", "profile": "", "profile_name": "", "from": who, "size": pk.get("size") or 0,
                "summary": f"{who} t'envoie {what}{where}.",
                "actions": ["install", "dismiss"],
            })
        for team, folder in self.gone.items():
            if team in self.gone_dismissed:
                continue
            out.append({
                "id": f"equipe-retrait:{team}", "section": "todo", "kind": "team_removed",
                "ts": self.gone_since.get(team) or 0,
                "team_id": team, "title": self.names.get(team) or team, "folder": folder,
                "color": self.colors.get(team) or "", "profile": "", "profile_name": "",
                "summary": "Tu ne fais plus partie de ce projet d'équipe : ses fichiers ne se synchronisent plus avec ce poste.",
                "actions": ["unlink", "dismiss"],
            })
        return out

    def _who(self, user_id) -> str:
        return self.people.get(user_id or "", user_id or "")

    # ------------------------------------------------------------ the marks
    def mark(self, picked: list[dict], dismiss: bool) -> int:
        """Lu: the mark moves to the latest message or conversation shown. Ignorer: an invitation or a lost
        project is put away (it comes back only if it changes)."""
        n = 0
        with self._lock:
            for e in picked:
                team, kind = e.get("team_id") or "", e.get("kind")
                if kind in ("team_message", "team_mention"):
                    self.seen.setdefault(team, {})["msg"] = max(e.get("upto") or 0, (self.seen.get(team) or {}).get("msg") or 0)
                elif kind == "team_share":
                    self.seen.setdefault(team, {})["conv"] = max(e.get("upto") or 0, (self.seen.get(team) or {}).get("conv") or 0)
                elif kind == "team_invite" and dismiss:
                    self.dismissed.add(team)
                elif kind == "team_removed" and dismiss:
                    self.gone_dismissed.add(team)
                elif kind == "team_signup":
                    self.signups_seen = (self.signups_seen or set()) | {e.get("user") or ""}
                else:
                    continue
                n += 1
            if n:
                self._save()
        if n:
            self.changed()
        return n

    def read_team(self, team: str, msg: float | None = None, conv: float | None = None) -> None:
        """The page showed the Fil up to `msg` (its latest message) or the conversations up to `conv`: read up to
        there (what came after stays news). Neither given: all that the console knows is read."""
        with self._lock:
            if msg is None and conv is None:
                msg = _latest(self.messages.get(team, []), "ts")
                conv = _latest(self.conversations.get(team, []), "updated")
            seen = self.seen.setdefault(team, {})
            if msg is not None:
                seen["msg"] = max(seen.get("msg") or 0, msg)
            if conv is not None:
                seen["conv"] = max(seen.get("conv") or 0, conv)
            self._save()
        self.changed()

    def forget(self, mod_id: str) -> None:
        """A module installed or put away: the hub no longer lists it, the inbox drops it now."""
        with self._lock:
            self.modules = [m for m in self.modules if m.get("id") != mod_id]
        self.changed()

    def forget_skills(self, pack_id: str) -> None:
        """Skills installed or put away: the inbox drops them now."""
        with self._lock:
            self.skill_packs = [p for p in self.skill_packs if p.get("id") != pack_id]
        self.changed()

    def pending_invitations(self) -> int:
        with self._lock:
            return sum(1 for p in self.projects if p["id"] not in self.linked and p["id"] not in self.dismissed)

    def _save(self):
        self.store.kv_set(KEY, {"seen": self.seen, "dismissed": sorted(self.dismissed),
                                "gone_dismissed": sorted(self.gone_dismissed), "gone_since": self.gone_since,
                                "signups_seen": sorted(self.signups_seen) if self.signups_seen is not None else None})


def _latest(rows: list[dict], key: str) -> float:
    return max((r.get(key) or 0 for r in rows), default=0)


def _mentions(text: str, me: dict) -> bool:
    """@roger, or @Roger Dupont: the message names this account."""
    words = {w for w in (me.get("id"), me.get("name")) if w}
    low = text.lower()
    return any(re.search(r"(?<![\w@])@" + re.escape(w.lower()) + r"(?![\w-])", low) for w in words)
