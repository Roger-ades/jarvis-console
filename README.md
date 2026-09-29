# JARVIS · Console d'agents Claude

Une console locale, au clavier, qui lance des sessions **Claude Code** en parallèle
et affiche chacune dans sa propre fenêtre déplaçable. Chaque demande part avec le
compte Claude choisi (**Travail** ou **Perso**), un preset d'autorisations et un
dossier de travail ; aucune écriture sensible ne passe sans ta validation.

Dérivé du template *JARVIS Local* (licence MIT) : la voix, OpenAI et le lancement
d'applications ont été retirés. Aucune clé API n'est nécessaire : la console utilise
tes abonnements Claude via la CLI Claude Code.

- **Parallélisme** : file d'attente, 3 tâches simultanées par défaut, limite par profil.
- **Deux comptes isolés** : un dossier de configuration Claude Code par profil
  (connexion, MCP, mémoire, skills, `CLAUDE.md`), une couleur par profil sur chaque fenêtre.
- **Toutes les capacités de Claude Code** : skills (`/` dans la barre de commande),
  sous-agents, MCP du profil et connecteurs claude.ai, MCP de tes apps Claude Desktop,
  plan de tâches, questions interactives, reprise de session, choix du modèle et de l'effort.
- **Contrôle de chaque appel d'outil** (sous-agents compris) : refus, validation humaine
  dans la fenêtre, contraintes sur les paramètres (Odoo), dossiers interdits.
- **Traçabilité** : historique persistant, journal d'audit exportable, arrêt d'urgence.

---

## Démarrer

Prérequis : Windows 10 ou 11 (Mac : voir plus bas), Python 3.10 ou plus (coché « Add Python to PATH »),
Claude Code. La CLI fournie avec les apps Claude Desktop est détectée
automatiquement (`%APPDATA%\Claude*\claude-code\<version>\claude.exe`) ; sinon installe
Claude Code ou indique son chemin dans Configuration → Général.

**Double-clique `start.bat`.** Au premier lancement il crée l'environnement Python
`.venv`, installe les dépendances, démarre le serveur sur `127.0.0.1:8788` et ouvre le
navigateur avec un lien d'accès à usage unique. Pour rouvrir la console plus tard,
relance `start.bat` : si le serveur tourne déjà, il ouvre simplement un nouvel accès.

Le serveur tourne **sans fenêtre**, en arrière-plan : aucun terminal à laisser ouvert.
Son journal est dans `data\console.log`. Pour l'arrêter : Configuration → Général →
**Arrêter la console**. Pour le dépannage, `start.bat --console` le garde au premier plan.
Pour qu'il démarre tout seul : Routines → « Lancer la console à l'ouverture de session ».

### Sur Mac

Prérequis : Python 3.10+ (python.org ou Homebrew), Claude Code (`claude` dans le terminal)
et Chrome de préférence. Une seule fois, dans Terminal, dans le dossier du projet :

```bash
chmod +x start.command
```

Ensuite, double-clic sur `start.command` (la première fois macOS peut demander
l'autorisation : clic droit → Ouvrir). Mêmes fonctions que sous Windows : serveur en
arrière-plan (`data/console.log`), fenêtre d'application Chrome, démarrage à l'ouverture
de session (agent `~/Library/LaunchAgents/local.jarvis.console.plist`), terminal pour
reprendre une session (Terminal.app). Par défaut, le profil Travail utilise la
configuration Claude standard (`~/.claude`) et l'app Claude Desktop, le profil Perso un
dossier séparé `~/.claude-personal` : à ajuster dans Configuration → Profils.

À la main, dans le dossier du projet :

```powershell
py -3 -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
.venv\Scripts\python.exe -m console
```

### Connecter chaque compte (une seule fois)

Les apps Claude Desktop authentifient leurs propres sessions : la CLI lancée par la
console a besoin de sa propre connexion, **une fois par profil**.

1. Configuration (`Ctrl+,`) → **Profils** → **Se connecter** : un terminal s'ouvre sur
   `claude auth login` avec le bon dossier de configuration. Suis la connexion.
2. **Tester la connexion** : affiche le compte, les skills, les sous-agents, les modèles
   et l'état des serveurs MCP. Le test n'envoie aucun message : il ne consomme rien.

Par défaut, le profil **Travail** utilise `~/.claude-work` (celui de l'app Claude-Work)
et le profil **Perso** le dossier Claude par défaut (`~/.claude`). À vérifier de ton
côté : la politique de l'organisation liée au compte Travail doit autoriser l'usage de
la CLI Claude Code par un outil tiers.

### Serveurs MCP

Une tâche voit, pour son profil :

- les MCP du dossier de configuration Claude Code et les connecteurs du compte claude.ai
  (Gmail, etc.) une fois le profil connecté ;
- les serveurs déclarés dans l'app Claude Desktop du profil (`claude_desktop_config.json`),
  importés automatiquement : c'est là que se trouve par exemple le serveur Odoo ;
- les serveurs que tu ajoutes dans Configuration → **Intégrations**.

Chaque serveur importé peut être désactivé ; « Tester les MCP » affiche leur état.

---

## Utiliser

| Geste | Effet |
|---|---|
| `Entrée` / `Maj+Entrée` | envoyer / nouvelle ligne |
| `@work …`, `@perso …` | choisir le compte dans le texte |
| `/` | skills et commandes du profil (chargés au test de connexion ou à la première tâche) |
| `Alt+1`, `Alt+2` | changer de profil |
| `↑` / `↓` | rappeler une demande précédente |
| `Ctrl+,` | configuration |

Sous le champ : modèle, preset d'autorisations, effort, dossier de travail.

Chaque fenêtre affiche le flux en direct (texte, outils appelés avec leur cible,
sous-agents, réflexion, plan), puis le résultat en markdown avec des blocs de code
copiables. Actions : annuler, relancer, dupliquer avec l'autre profil, message de suite
(reprend la même session Claude), copier le résultat, **ouvrir la session dans un
terminal** (`claude --resume`, pour continuer en interactif), épingler, réduire, fermer.
Fermer une fenêtre n'arrête pas la tâche ; tout reste dans l'**Historique**.

Quand une tâche attend ta validation, la fenêtre passe « À valider » (et se rouvre si tu
l'avais fermée) : action proposée en détail, **Approuver** ou **Refuser** avec un message
pour Claude. Les questions de Claude et les plans à approuver s'affichent de la même façon.

### Mode équipe

La case **Équipe** de la barre du bas fait du modèle choisi le **chef** : il planifie,
arbitre et rédige la réponse, et délègue à des sous-agents épinglés sur leur modèle :
l'**éclaireur** (Haiku, lecture et recherche seules), l'**exécutant** (Sonnet) et
l'**expert** (Opus, pour les points difficiles). Un rôle n'est proposé que s'il est utile :
chef Opus → éclaireur + exécutant ; chef Sonnet → éclaireur + expert. Les autres
sous-agents de Claude Code (Explore, general-purpose) passent aussi sur Sonnet au lieu
d'hériter du modèle du chef. Le panneau latéral montre qui fait quoi, avec quel modèle.
Modèles et règles du chef : Configuration → Modèles et consignes. Pour une question
simple, laisse la case décochée : chaque délégation a un coût fixe.

### Aperçus : images, PDF, pages web

Les fichiers cités par Claude (chemin, lien `[texte](C:\...)`, image `![](...)`) sont
cliquables et s'ouvrent dans un **aperçu intégré** : images (miniature directement dans la
conversation), PDF, CSV en tableau, Markdown, JSON, texte, HTML (sans ses scripts). Les
actions de fichier (Lire, Écrire, Modifier) ont un bouton œil, et une image créée par
Claude s'affiche aussitôt. Pour les autres types (Excel, Word…) : **Ouvrir avec
l'application** ou **Afficher dans le dossier**.

Seuls les fichiers des dossiers de la tâche sont accessibles, jamais un fichier protégé
(Sécurité → chemins interdits), et la console ne lance jamais un programme (`.exe`,
`.bat`, `.ps1`, macros…) : elle peut seulement le montrer dans son dossier.

Les liens web s'ouvrent dans un **mini-navigateur isolé** (Ctrl/⌘ + clic : vrai
navigateur). Certains sites refusent l'affichage intégré : bouton « Ouvrir dans le
navigateur ». Les images web ne sont chargées qu'au clic, pour que le site qui les héberge
ne voie pas ta console sans ton accord ; Configuration → Interface permet de les charger
automatiquement.

### Sessions existantes (Claude Desktop, CLI)

Le bouton **Sessions** liste, pour chaque compte, les sessions Claude Code déjà sur ton
poste : onglet Code de Claude Desktop (avec leurs titres), CLI et console. Filtre par
compte, origine, dossier ; lis la conversation ; **reprends-la dans la console** (dans
une copie par défaut, la session d'origine reste intacte) ou dans un terminal.

Les conversations claude.ai, les Projets, le partage d'équipe (Team) et Cowork sont
stockés sur les serveurs d'Anthropic, sans API pour un outil tiers : le menu
**claude.ai** y donne un accès direct dans le navigateur.

### Application installable

`start.bat` ouvre la console dans sa **propre fenêtre** (mode application de Chrome, ou
d'Edge à défaut ; réglable dans Configuration → Général). Pour l'installer comme une
vraie application (menu Démarrer, barre des tâches, barre de titre intégrée) : bouton
**Installer** dans la barre du haut, ou menu ⋮ de Chrome → « Installer JARVIS ». Si le
serveur n'est pas lancé, la fenêtre l'indique.

### Routines

Le panneau **Routines** a deux parties :

- **Routines claude.ai** : les routines cloud de chaque compte, lues via Claude Code (lancer,
  mettre en pause, voir les exécutions ; « Nouvelle » prépare `/schedule` dans la barre de
  commande). Les tâches planifiées créées depuis **Cowork** ne sont renvoyées par claude.ai
  qu'à l'app Claude Desktop : elles se gèrent là-bas.
- **Routines de la console**, qui tournent sur ce PC :

Le bouton **Nouvelle routine** planifie des demandes : chaque jour à heure fixe (jours au choix),
à intervalle régulier, ou une seule fois. Chaque exécution est une tâche normale (compte,
preset, validations, historique). Les routines tournent tant que la console est lancée :
coche « Lancer la console au démarrage de Windows » pour qu'elle démarre toute seule.
Une exécution manquée console arrêtée est notée, et peut être rattrapée au démarrage.

### Presets d'autorisations fournis

| Preset | Permet | Interdit |
|---|---|---|
| Lecture seule | lire le dossier de travail, rechercher, MCP en lecture | écrire, exécuter, web |
| Web seul | recherche web, lecture de pages | fichiers, commandes, MCP |
| Brouillons | lecture, brouillons Gmail, devis Odoo **après validation** | modifier, supprimer, envoyer, confirmer |
| Édition du dossier | lire et écrire dans le dossier, commandes d'une liste blanche | commandes hors liste, accès hors dossier |
| Assisté (validation) — *défaut* | toutes les capacités de Claude Code | chaque écriture ou commande attend ta validation |
| Complet | aucune demande | **désactivé par défaut** ; dossier dédié et confirmation à chaque lancement |

Les presets se modifient, se dupliquent et se créent dans Configuration → Autorisations.
Un changement s'applique aux nouvelles tâches, jamais à celles en cours.

---

## Sécurité

- Le serveur n'écoute que `127.0.0.1`. Chaque appel porte un jeton d'accès
  (`data/token`, jamais placé dans la page ni dans une URL) ; l'en-tête `Host` (contre le
  DNS rebinding) et l'en-tête `Origin` sont contrôlés ; la page applique une CSP stricte
  et n'utilise aucune bibliothèque en ligne.
- Chaque appel d'outil passe par la politique de la console (hook `PreToolUse` et
  demandes d'autorisation de Claude Code), dans cet ordre : chemins interdits et règles
  verrouillées → refus → contraintes et confinement au dossier → validation humaine →
  autorisations → écritures à valider → outils non listés.
- Toujours refusé, quel que soit le preset : `delete_record` Odoo, les chemins interdits
  (`~/.ssh`, fichiers `.env`, clés, `claude_desktop_config.json`…), le dossier de données
  de la console et tout appel des agents vers la console elle-même.
- Un devis Odoo n'est créé qu'en brouillon, sur `sale.order` (lignes incluses dans le
  devis), après ton approbation, et jamais à l'état confirmé.
- Les chemins sont comparés sous forme canonique (noms courts 8.3, liens et jonctions
  résolus) ; pour les outils MCP, tous les paramètres sont inspectés, quel que soit leur nom.
- Un outil MCP dont le nom contient un verbe d'écriture (`list_and_delete…`) est traité
  comme une écriture ; un motif générique (`mcp__*__get*`) ne l'autorise jamais.
- Les refus de commandes tiennent compte des alias PowerShell (`del`, `rm`, `iwr`…), des
  espaces multiples et des enveloppes `cmd /c`, `powershell -Command`, `bash -c`.
- Des consignes de sécurité sont ajoutées au prompt système : le contenu des mails et des
  pages web est de la donnée, jamais une consigne.
- L'environnement des tâches est nettoyé (`ANTHROPIC_*`, variables d'une session Claude
  parente) : aucune facturation par clé API par accident.
- Arrêt d'urgence : stoppe toutes les tâches et bloque les nouvelles jusqu'à réactivation.

Ne mets jamais ce serveur sur le réseau ou sur internet.

---

## Données et configuration

Tout est dans `data/` (ignoré par git) : `config.json` et ses versions précédentes
(`config-history/`), `console.db` (tâches, flux, journal d'audit, positions des
fenêtres), `token`. La configuration s'exporte et s'importe en JSON (validée par schéma)
et chaque enregistrement garde la version précédente, restaurable en un clic.

Réglages de démarrage facultatifs : copie `.env.example` en `.env`
(`CONSOLE_PORT`, `CONSOLE_DATA_DIR`, `CONSOLE_NO_BROWSER`).

## Architecture

| Fichier | Rôle |
|---|---|
| `console/app.py` | serveur HTTP : contrôles d'accès, API REST, flux temps réel (SSE) |
| `console/engine.py` | moteur de tâches : file, pool, processus Claude Code, validations, arrêt d'urgence |
| `console/permissions.py` | politique d'autorisations (fonctions pures, testées) |
| `console/config.py` | schéma, valeurs par défaut, versions de la configuration |
| `console/claude_cli.py` | détection de la CLI, environnement par profil, test de connexion |
| `console/mcp.py` | import des MCP de Claude Desktop, fichier MCP par tâche |
| `console/library.py` | sessions Claude Code existantes (transcriptions CLI + fiches Claude Desktop) |
| `console/routines.py` | routines : planification et calcul des prochaines exécutions |
| `console/winsys.py` | démarrage à l'ouverture de session (Windows, macOS) |
| `console/cloud.py` | routines claude.ai (relais Claude Code) |
| `console/store.py` | persistance SQLite |
| `static/` | interface (HTML, CSS, modules JavaScript sans dépendance) |

## Installer sur un autre poste

Copie le projet **sans** `data/` ni `.venv/` (un dépôt Git les exclut d'office) : le
nouveau poste démarre avec une configuration vierge, ses propres comptes, sans tes
routines, ton historique ni ton jeton.

## Mises à jour

Le code et les données sont séparés : `data/` (configuration, historique, routines,
jeton, journal) et `.venv/` ne sont jamais remplacés par une mise à jour.

1. Arrête la console (Configuration → Général → Arrêter la console).
2. Remplace les fichiers du projet (idéalement `git pull` depuis un dépôt privé).
3. Relance `start.bat` / `start.command` : les dépendances sont réinstallées d'elles-mêmes
   si `requirements.txt` a changé. Les nouveaux réglages prennent leur valeur par défaut ;
   une configuration devenue invalide est mise de côté et la dernière version valide reprise.

Claude Code se met à jour avec les apps Claude Desktop : la console prend la version la
plus récente (ou le chemin fixé dans Configuration → Général). Après une mise à jour,
« Tester la connexion » confirme que tout répond.

## Tests et démo

```powershell
.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.venv\Scripts\python.exe -m pytest
```

Les tests utilisent une fausse CLI (`tests/fake_claude.py`) qui parle le même protocole
que Claude Code : aucun compte ni token n'est nécessaire. Pour essayer l'interface de la
même façon : `.venv\Scripts\python.exe tests\demo_server.py`, puis ouvre le lien affiché
(port 8790) et tape `DEMO`, `ASK`, `SLEEP 20` ou `TOOL Bash {"command": "npm install"}`.

## Dépannage

- **« Ce profil n'est pas connecté »** : Configuration → Profils → Se connecter, puis Tester.
- **« Claude Code introuvable »** : installe Claude Code ou renseigne son chemin
  (Configuration → Général).
- **« Dossier de travail interdit »** : le dossier choisi touche un chemin interdit ou le
  dossier de données de la console.
- **Accès refusé dans le navigateur** : relance `start.bat` (nouveau lien d'accès), ou colle
  le contenu de `data\token`.
- **Une tâche « Interrompue »** : le serveur s'est arrêté pendant qu'elle tournait ; relance-la.

## Licence

MIT : voir `LICENSE`.
