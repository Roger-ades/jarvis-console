"""Team mode: the task's model leads, specialised subagents do the work.

The chief keeps planning, decisions and the final answer; a scout (cheap, read
only), a worker (mid-range) and an expert (strongest) are offered as Claude Code
subagents (--agents), each pinned to its model. A role is only offered when it
makes sense next to the chief: no worker on the chief's own model, no expert that
is not stronger than the chief.
"""
from __future__ import annotations

from .config import TeamSettings

# The scout reads only: write and shell tools are taken away, everything else (MCP servers such as
# CodeGraph, ToolSearch to load them) stays. An allowlist would hide the MCP tools.
WRITE_TOOLS = ["Write", "Edit", "MultiEdit", "NotebookEdit", "Bash", "PowerShell", "KillShell"]
CODE_INDEX = (" Pour trouver du code, utilise d'abord un index de code s'il est disponible (CodeGraph : outil "
              "codegraph_explore, à charger avec ToolSearch s'il est différé) : il rend en un appel le source des "
              "symboles et leurs liens. Ne lis des fichiers entiers qu'en dernier recours, et seulement les parties utiles.")


def rank(model: str, resolved: dict | None = None) -> int:
    """Rough capability (and price) order: haiku < sonnet < opus < fable."""
    m = (resolved or {}).get(model, model).lower()
    for word, r in (("haiku", 1), ("sonnet", 2), ("opus", 3), ("fable", 4)):
        if word in m:
            return r
    return 3  # "default" and unknown ids: assume a top model


def build(chief: str, settings: TeamSettings, resolved: dict | None = None) -> tuple[dict, dict, str]:
    """(agents for --agents, {agent: model} for the UI, text appended to the system prompt)."""
    top = rank(chief, resolved)
    agents: dict[str, dict] = {}
    if rank(settings.scout_model, resolved) < top:
        agents["eclaireur"] = {
            "description": (f"Éclaireur rapide et économique (modèle {settings.scout_model}). À utiliser pour "
                            "chercher dans les fichiers ou sur le web, lire et résumer de gros documents, faire un "
                            "inventaire ou une liste. Ne modifie rien."),
            "prompt": ("Tu es l'éclaireur d'une équipe. Tu lis et tu cherches, tu ne modifies rien. Rends un "
                       "résumé court et factuel : chemins, chiffres, extraits utiles, et ce que tu n'as pas trouvé."
                       + CODE_INDEX),
            "model": settings.scout_model, "disallowedTools": WRITE_TOOLS,
        }
    if rank(settings.worker_model, resolved) < top:
        agents["executant"] = {
            "description": (f"Exécutant fiable (modèle {settings.worker_model}) pour une étape bien définie : créer "
                            "ou modifier des fichiers, lancer des commandes, interroger un outil ou Odoo, rédiger un "
                            "brouillon."),
            "prompt": ("Tu es l'exécutant d'une équipe. Tu réalises exactement l'étape demandée, sans élargir le "
                       "périmètre, puis tu rends compte en quelques lignes : ce qui a été fait, fichiers touchés, "
                       "résultats, problèmes rencontrés." + CODE_INDEX),
            "model": settings.worker_model,
        }
    if rank(settings.expert_model, resolved) > top:
        agents["expert"] = {
            "description": (f"Expert (modèle {settings.expert_model}) pour un point réellement difficile : "
                            "conception, raisonnement délicat, diagnostic complexe, relecture critique. Coûteux : "
                            "à appeler seulement quand c'est nécessaire, avec une question précise."),
            "prompt": ("Tu es l'expert d'une équipe, consulté sur un point difficile. Réponds à la question posée "
                       "avec rigueur, signale les hypothèses et les risques, et conclus par une recommandation claire."
                       + CODE_INDEX),
            "model": settings.expert_model,
        }
    models = {name: a["model"] for name, a in agents.items()}
    if not agents:
        return {}, {}, ""
    roster = "\n".join(f"- {name} ({a['model']}) : {a['description'].split('. ', 1)[1] if '. ' in a['description'] else a['description']}"
                       for name, a in agents.items())
    prompt = (f"Mode équipe : tu es le chef d'équipe. Sous-agents disponibles (outil Agent, paramètre subagent_type) :\n"
              f"{roster}\n\n{settings.instructions.strip()}")
    return agents, models, prompt
