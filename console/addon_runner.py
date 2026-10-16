"""The process of a module's Python task (console/addon_tasks.py starts it): runs the module's script with a
module "jarvis" that speaks to the console.

    python -I -X utf8 addon_runner.py <module folder> <script>

The console writes one JSON line on stdin (the task, its parameters), then answers each request of the script
on the same pipe; the requests go out on the original stdout, one JSON line each. What the script prints goes
to stderr, which the console keeps (the last lines are shown when the task fails). Standard library only: this
file runs outside the console's package.
"""
import json
import os
import runpy
import sys
import threading
import types


class JarvisErreur(Exception):
    """A request refused or failed on the console's side (Odoo error, missing permission...)."""


def main():
    folder, script = os.path.abspath(sys.argv[1]), sys.argv[2]
    proto_out = os.fdopen(os.dup(1), "w", encoding="utf-8", newline="\n", buffering=1)
    proto_in = os.fdopen(os.dup(0), "r", encoding="utf-8", newline="\n")
    os.dup2(2, 1)  # print() of the script: to the console's log of the task, not into the protocol
    null = os.open(os.devnull, os.O_RDONLY)
    os.dup2(null, 0)
    os.close(null)
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
        except (AttributeError, ValueError):
            pass
    init = json.loads(proto_in.readline() or "{}")
    lock = threading.Lock()
    seq = [0]

    def send(msg):
        proto_out.write(json.dumps(msg, ensure_ascii=False, default=str) + "\n")
        proto_out.flush()

    def request(op, **kw):
        with lock:
            seq[0] += 1
            send({"id": seq[0], "op": op, **kw})
            line = proto_in.readline()
        if not line:
            raise JarvisErreur("La console a interrompu la tâche.")
        r = json.loads(line)
        if not r.get("ok"):
            raise JarvisErreur(r.get("erreur") or "Refusé par la console.")
        return r.get("valeur")

    def note(op, **kw):
        with lock:
            send({"op": op, **kw})

    class Odoo:
        """Odoo, read only, through the console's API key (permission "odoo")."""

        def chercher(self, modele, domaine=None, champs=None, ordre=None, limite=None, decalage=0):
            """search_read; every matching record (page after page) when limite is None."""
            return request("chercher", modele=modele, domaine=domaine or [], champs=champs or [], ordre=ordre,
                           limite=limite, decalage=decalage)

        def compter(self, modele, domaine=None):
            return request("compter", modele=modele, domaine=domaine or [])

        def lire(self, modele, ids, champs=None):
            return request("lire", modele=modele, ids=list(ids), champs=champs or [])

        def grouper(self, modele, domaine=None, groupby=None, agregats=None, ordre=None, limite=None):
            """Totals per group: [{"<groupby>": valeur, "__count": n, "<champ>:sum": total}]."""
            return request("grouper", modele=modele, domaine=domaine or [], groupby=groupby or [],
                           agregats=agregats or [], ordre=ordre, limite=limite)

        def champs(self, modele):
            return request("champs", modele=modele)

        def appeler(self, modele, methode, ids=None, **kwargs):
            """Any other reading method (search, name_search, read_group...); writing ones are refused."""
            return request("appeler", modele=modele, methode=methode, ids=ids, kwargs=kwargs)

        def version(self):
            return request("version")

    j = types.ModuleType("jarvis")
    j.__doc__ = "What a module's Python task can ask the console (console/addons_readme.md)."
    j.dossier = folder
    j.tache = init.get("tache") or ""
    j.params = init.get("params") or {}
    j.permissions = list(init.get("permissions") or [])
    j.JarvisErreur = JarvisErreur
    j.odoo = Odoo()
    j.progres = lambda texte: note("progres", texte=str(texte)[:500])
    j.resultat = lambda valeur: note("resultat", valeur=valeur)
    sys.modules["jarvis"] = j

    os.chdir(folder)
    sys.path.insert(0, folder)
    sys.argv = [script]
    runpy.run_path(os.path.join(folder, script), run_name="__main__")


if __name__ == "__main__":
    main()
