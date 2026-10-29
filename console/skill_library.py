"""The shared skills: one library for every account of the team's hub (hub/db.py library), copied by each JARVIS into
the skills/ of its accounts, where Claude Code loads them like the others.

Nobody is added by hand: every account registered on the hub reads and writes the library. A skill dragged from the
personal part of the page Compétences to the shared part goes to the hub (publish); every JARVIS then installs it
within half a minute (sync, from equipe.loop). Whoever changes it, from the page or through the tool retenir, sends
the new version at the next sync; the others receive it. A skill changed here and on the hub at the same time: the
hub's version wins, this one is kept in skills-anciennes/ and the page says so. Dragged back to the personal part, it
leaves the library: this account keeps it as its own, the others put it aside in skills-anciennes/.

Only the work account receives them, unless the configuration says every account (hub.skills_all_accounts). An
account that stops receiving them loses its copies: removed if untouched, else put aside in skills-anciennes/.

What JARVIS remembers per account folder (store, "skill_library"): for each shared skill, the version it holds and
the fingerprint of its files then, to tell its own changes from the hub's. Claude takes no part in it.
"""
from __future__ import annotations

import hashlib
import secrets
import shutil
import tempfile
import threading
import time
from pathlib import Path
from urllib.parse import quote, urlencode

from . import project_tools, skill_share
from .hub_client import HubError

KEY = "skill_library"
NOTES = 6     # the last notes the page shows (a version put aside, a skill that could not be sent)


def fingerprint(folder: Path) -> str:
    """The files of a skill, as they are: names and contents."""
    h = hashlib.sha256()
    for p in skill_share._files(folder):
        h.update(p.relative_to(folder).as_posix().encode("utf-8") + b"\0")
        h.update(p.read_bytes() + b"\0")
    return h.hexdigest()


def folder_key(base: Path) -> str:
    return str(base.expanduser().resolve()).replace("\\", "/").rstrip("/").lower()


def _q(value: str) -> str:
    return quote(value, safe="")


class Library:
    def __init__(self, store, data_dir: Path, accounts, changed, receives=lambda pid: True):
        """accounts(): [(profile id, configuration folder[, name])] of every account; changed(): the page reads again;
        receives(profile id): whether that account gets the shared skills."""
        self.store, self.data_dir, self.accounts, self.changed = store, Path(data_dir), accounts, changed
        self.receives = receives
        self._lock = threading.RLock()
        self.cache: dict = {}        # the hub's last list: {"competences", "groupes"}
        self.error = ""
        self.synced = 0.0

    # ------------------------------------------------------------ what JARVIS remembers
    def _state(self) -> dict:
        return self.store.kv_get(KEY, {}) or {}

    def _save(self, st: dict) -> None:
        self.store.kv_set(KEY, st)

    def _note(self, st: dict, text: str) -> None:
        st["notes"] = [{"ts": time.time(), "texte": text}, *(st.get("notes") or [])][:NOTES]

    def held(self, base: Path) -> dict:
        """The shared skills of this account folder: {nom: {"rev", "hash"}}."""
        return dict((self._state().get("comptes") or {}).get(folder_key(base)) or {})

    def _folders(self) -> dict:
        """{folder_key: (configuration folder, receives, names)}: two accounts on one folder receive if either does."""
        out = {}
        for pid, base, *name in self.accounts():
            k = folder_key(base)
            _, on, names = out.get(k, (base, False, []))
            out[k] = (base, on or bool(self.receives(pid)), [*names, name[0] if name else pid])
        return out

    def covers(self, base: Path) -> bool:
        """Whether this account folder receives the shared skills."""
        return self._folders().get(folder_key(base), (None, False))[1]

    def view(self, enabled: bool = True) -> dict:
        """For the page: the library as last read, and the notes."""
        with self._lock:
            live = [c for c in self.cache.get("competences") or [] if not c.get("retiree")]
            return {"actif": enabled, "competences": live, "groupes": list(self.cache.get("groupes") or []),
                    "erreur": self.error if enabled else "", "synchro": self.synced,
                    "notes": (self._state().get("notes") or [])[:NOTES]}

    # ------------------------------------------------------------ the sync, every half-minute
    def sync(self, call) -> dict:
        """call(method, path, **kw) → the hub's answer (HubClient.call with the hub bound). Brings every account up
        to the library, sends what changed here."""
        with self._lock:
            try:
                lib = call("GET", "/api/v1/bibliotheque")
            except HubError as exc:
                self.error = exc.message
                raise
            self.cache, self.error = {"competences": lib.get("competences") or [], "groupes": lib.get("groupes") or []}, ""
            items = {c["nom"]: c for c in self.cache["competences"]}
            st = self._state()
            comptes = st.setdefault("comptes", {})
            moved = False
            for k, (base, receives, names) in self._folders().items():
                if not receives:
                    moved |= self._withdraw(base, " / ".join(names), comptes.pop(k, None) or {}, st)
                    continue
                mine = comptes.setdefault(k, {})
                moved |= self._sync_folder(call, base, items, mine, st)
                if not mine:
                    comptes.pop(k, None)
            self._save(st)
            self.synced = time.time()
        if moved:
            self.changed()
        return self.view()

    def _sync_folder(self, call, base: Path, items: dict, mine: dict, st: dict) -> bool:
        local = {s["name"]: s for s in project_tools.skills(base)}
        moved = False
        for nom, c in items.items():
            had, here = mine.get(nom), local.get(nom)
            if c.get("retiree"):
                if had and here:     # left the library: put aside, not lost
                    self._put_aside(base, here["folder"], nom)
                    moved = True
                mine.pop(nom, None)
                continue
            now = fingerprint(base / "skills" / here["folder"]) if here else ""
            if had and here and had["rev"] == c["rev"]:
                if now != had["hash"]:      # changed here: the new version goes to the hub
                    try:
                        row = self._send(call, base, nom, c.get("groupe") or "", had["rev"])
                        mine[nom] = {"rev": row["rev"], "hash": now}
                    except HubError as exc:
                        if exc.status != 409:   # 409: changed meanwhile; the next sync takes the hub's
                            self._note(st, f"« {nom} » n'a pas pu partir : {exc.message}")
                continue
            # the hub has a version this folder does not hold yet
            edited = bool(here and had and now != had["hash"])
            keep = bool(here and (not had or edited))   # a personal one of the same name, or changes made meanwhile
            try:
                done = self._fetch(call, base, nom, keep)
            except (HubError, skill_share.ShareError, OSError) as exc:
                self._note(st, f"« {nom} » n'a pas pu être reçue : {getattr(exc, 'message', exc)}")
                continue
            fresh = next((s for s in project_tools.skills(base) if s["name"] == nom), None)
            mine[nom] = {"rev": c["rev"], "hash": fingerprint(base / "skills" / fresh["folder"]) if fresh else ""}
            moved = True
            if keep and done.get("ancien"):
                why = "modifiée ici en même temps qu'un collègue" if edited else "une compétence personnelle du même nom"
                self._note(st, f"« {nom} » : la version partagée est installée ; {why}, gardée dans "
                               f"{done['ancien']}.")
        for nom in [n for n in mine if n not in items]:   # a hub started again from nothing: they stay, as personal
            mine.pop(nom)
        return moved

    def _withdraw(self, base: Path, account: str, mine: dict, st: dict) -> bool:
        """An account that no longer receives the shared skills: the copies it got go; one changed here is put
        aside, not lost."""
        if not mine:
            return False
        local = {s["name"]: s for s in project_tools.skills(base)}
        gone, aside = [], []
        for nom, had in mine.items():
            here = local.get(nom)
            if not here:
                continue
            folder = base / "skills" / here["folder"]
            if fingerprint(folder) == had["hash"]:
                shutil.rmtree(folder, ignore_errors=True)
                gone.append(nom)
            else:
                aside.append(f"« {nom} » gardée dans {self._put_aside(base, here['folder'], nom)}")
        if gone or aside:
            self._note(st, f"Compte {account} : il ne reçoit plus les compétences partagées, leurs copies en sont "
                           "retirées (" + ", ".join([*(f"« {n} »" for n in gone), *aside]) + ").")
        return bool(gone or aside)

    def _send(self, call, base: Path, nom: str, group: str, rev: int) -> dict:
        work = Path(tempfile.mkdtemp(prefix="bibliotheque-", dir=self.data_dir))
        try:
            path, _ = skill_share.export(base, [nom], {nom: group}, work)
            return call("PUT", f"/api/v1/bibliotheque/{_q(nom)}", content=path.read_bytes(),
                        content_type="application/zip", query=urlencode({"base": rev}))["competence"]
        finally:
            shutil.rmtree(work, ignore_errors=True)

    def _fetch(self, call, base: Path, nom: str, keep: bool) -> dict:
        work = Path(tempfile.mkdtemp(prefix="bibliotheque-", dir=self.data_dir))
        try:
            path = work / "competence.zip"
            path.write_bytes(call("GET", f"/api/v1/bibliotheque/{_q(nom)}/archive", raw=True))
            return skill_share.install(path, base, [nom], keep=keep)["installees"][0]
        finally:
            shutil.rmtree(work, ignore_errors=True)

    @staticmethod
    def _put_aside(base: Path, folder: str, nom: str) -> Path:
        to = base / skill_share.OLD / f"{nom}-{time.strftime('%Y%m%d-%H%M%S')}"
        to = to if not to.exists() else to.with_name(f"{to.name}-{secrets.token_hex(2)}")
        to.parent.mkdir(parents=True, exist_ok=True)
        (base / "skills" / folder).rename(to)
        return to

    # ------------------------------------------------------------ the page: personal ↔ shared
    def publish(self, call, base: Path, names: list[str], groups: dict[str, str], account: bool) -> list[str]:
        """Personal skills dragged to the shared part: each goes to the library, with its group. From an account,
        its copy becomes the shared one; from a project, the account gets it at the sync and the project's copy is
        put aside (Claude Code would follow the project's)."""
        known = {s["name"]: s for s in project_tools.skills(base)}
        taken = {c["nom"] for c in self.cache.get("competences") or [] if not c.get("retiree")}
        done, errors = [], []
        with self._lock:
            st = self._state()
            mine = st.setdefault("comptes", {}).setdefault(folder_key(base), {}) if account else {}
            for nom in names:
                s = known.get(nom)
                if not s:
                    errors.append(f"« {nom} » introuvable")
                    continue
                if nom in taken:
                    errors.append(f"une compétence partagée s'appelle déjà « {nom} »")
                    continue
                try:
                    row = self._send(call, base, nom, groups.get(nom, ""), 0)
                except HubError as exc:
                    errors.append(f"« {nom} » : {'déjà partagée' if exc.status == 409 else exc.message}")
                    continue
                except skill_share.ShareError as exc:
                    errors.append(f"« {nom} » : {exc}")
                    continue
                if account:
                    mine[nom] = {"rev": row["rev"], "hash": fingerprint(base / "skills" / s["folder"])}
                else:
                    self._put_aside(base, s["folder"], nom)
                done.append(nom)
            self._save(st)
        if errors and not done:
            raise HubError("Rien n'a été partagé : " + " ; ".join(errors) + ".", 409)
        return done

    def unshare(self, call, base: Path, nom: str) -> None:
        """Dragged back to the personal part: it leaves the library; this account keeps it as its own."""
        with self._lock:
            call("DELETE", f"/api/v1/bibliotheque/{_q(nom)}")
            st = self._state()
            (st.get("comptes") or {}).get(folder_key(base), {}).pop(nom, None)
            self._save(st)
