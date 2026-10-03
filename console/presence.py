"""What Claude is told about the console it runs in: a block at the head of the appended system prompt.

Claude Code's own prompt describes a terminal; the console is not one. This block says where the
answers go, how actions are checked, which console tools show things, then the frame of this
discussion (account, project, folders, permissions). It is always sent, apart from the security
instructions the user can edit, and it only uses what is fixed for a discussion: the prompt stays
the same from one turn to the next (and for the cache keep-warm copy), so the prompt cache holds.
"""
from __future__ import annotations

from pathlib import Path

from .config import Preset, Profile

CONSOLE = """# Environnement : console JARVIS
Tu tournes dans JARVIS, une console locale qui pilote des sessions Claude Code. Il n'y a pas de terminal : l'utilisateur te suit dans une fenêtre de la console, une par discussion.
- Il voit en direct tes réponses, tes actions et celles de tes sous-agents. Le Markdown est mis en forme (titres, listes, tableaux, code, liens) et un chemin de fichier cité s'ouvre d'un clic.
- Ne lui demande pas de taper une commande : fais-le toi-même si tes autorisations le permettent, sinon dis-lui ce qu'il faut faire. Les actions d'un projet (ses commandes « / ») lui apparaissent comme des boutons.
- Chaque action passe par la politique de la console : certaines sont permises d'office, d'autres attendent sa validation dans la fenêtre (elle peut prendre du temps), d'autres sont refusées. Après un refus, ne tente ni variante ni contournement : explique ce qui manque, il peut ajuster les autorisations.
- Pour montrer quelque chose, préfère les outils de la console (serveur « jarvis ») à un long texte : afficher (fichiers, pages web), afficher_resultat (le résultat d'un outil déjà reçu, sans le recopier), presenter (galerie, tableau, graphique, fiche, chronologie, choix ou boutons à cliquer, petite application).
- D'autres discussions peuvent tourner en parallèle dans leurs propres fenêtres ; tu ne vois que la tienne."""

ASK = "- Pour une question qui bloque la suite, AskUserQuestion s'affiche dans la fenêtre et il répond d'un clic."


PROJECT = ("- Tu peux enrichir ce projet, toujours avec son accord : l'outil proposer lui soumet une nouvelle action "
           "(une tâche du projet qu'il relancera d'un clic) ou une routine (une demande, ou une action, lancée à heure "
           "fixe) ; presenter, avec un bloc application, ouvre un petit outil interactif. N'écris pas toi-même dans "
           ".claude/commands ou .claude/skills, et propose plutôt que d'insister.")

CODE_INDEX = ("- Le code de ce dossier est indexé par CodeGraph (index : {root}). Pour comprendre, trouver ou modifier "
              "du code, appelle codegraph_explore AVANT toute lecture ou recherche (Read, Grep, Glob, grep, find, sed, "
              "cat) et avant de déléguer : un appel rend le source numéroté des symboles, leurs appelants et ce qui en "
              "dépend, pour bien moins de jetons que la lecture des fichiers. S'il est différé, charge-le d'abord avec "
              "ToolSearch (select:mcp__codegraph__codegraph_explore, ou la requête « codegraph »). Interroge-le avec des "
              "noms précis (fonctions, fichiers, constantes) ; si un résultat est hors sujet, reformule avec ces noms "
              "plutôt que d'abandonner l'index. Ne lis un fichier qu'en dernier recours, et seulement la partie utile. "
              "Sans le serveur MCP, la commande codegraph explore \"<noms ou question>\" donne le même résultat. Donne "
              "la même consigne à chaque sous-agent.")


def code_index(folder: str) -> str:
    """The folder holding the CodeGraph index of this folder or of one above, or "". The home folder's
    ~/.codegraph holds CodeGraph's own settings, not an index: only a .codegraph/codegraph.db counts."""
    if not folder:
        return ""
    try:
        here = Path(folder).resolve()
    except OSError:
        return ""
    for d in (here, *here.parents):
        if (d / ".codegraph" / "codegraph.db").is_file():
            return str(d)
    return ""


def prompt(t: dict, prof: Profile, pre: Preset, project: str = "", ask_user: bool = True,
           actions: list[dict] | None = None, routines: list[dict] | None = None) -> str:
    """The block for one discussion: the console (the same for every discussion), then this one.

    In a project, its validated actions and its routines follow: they only change when the user
    changes them (no state such as the next run), so the prompt cache holds between turns."""
    lines = [CONSOLE, *([ASK] if ask_user else []), "", "## Cette discussion", f"- Compte : {prof.name}"]
    wd = t.get("workdir") or ""
    lines.append(f"- Projet « {project} », dossier de travail : {wd}" if project else f"- Dossier de travail : {wd}")
    adir = t.get("attachments_dir") or ""
    others = [d for d in t.get("add_dirs") or [] if d and d != adir]
    if others:
        lines.append("- Autres dossiers accessibles : " + " ; ".join(others))
    if adir:
        lines.append(f"- Les fichiers que l'utilisateur joint sont copiés dans : {adir}")
    root = code_index(wd)
    if root:
        lines.append(CODE_INDEX.format(root=root))
    # (the preset's description is written for the user: quoted as such)
    desc = pre.description.strip()
    lines.append(f"- Autorisations : preset « {pre.name} »" + (f", décrit ainsi à l'utilisateur : « {desc} »" if desc else ""))
    routine = t.get("routine") or {}
    if t.get("origin") == "routine":
        name = f" « {routine['name']} »" if routine.get("name") else ""
        lines.append(f"- Lancée automatiquement par la routine{name} : l'utilisateur n'est peut-être pas devant "
                     "l'écran, une validation demandée attendra son retour.")
    if project:
        lines += ["", f"## Ce projet : {project}"]
        if actions:
            lines.append("- Actions (boutons de la console ; l'utilisateur peut aussi les taper) : "
                         + " ; ".join(f"/{a['name']}" + (f" ({a['hint']})" if a.get("hint") else "")
                                      + (f" — {a['description']}" if a.get("description") else "")
                                      for a in sorted(actions, key=lambda a: a["name"])))
        if routines:
            lines.append("- Routines : " + " ; ".join(f"« {r['name']} », {r['schedule_label']}"
                                                      for r in sorted(routines, key=lambda r: (r["name"], r["id"]))))
        lines.append(PROJECT)
    return "\n".join(lines)
