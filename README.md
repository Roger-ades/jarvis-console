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
- **Ce que je regarde** : l'aperçu ou l'affichage au premier plan et le texte sélectionné
  partent avec ton message ; **différences et annulation** de chaque fichier modifié par Claude.

- **Application de bureau** (Electron) : fenêtres de Windows, barre JARVIS (Ctrl+Alt+J),
  notifications avec Approuver et Refuser, sites connectés avec leur session.

La suite prévue (boîte de réception, déclencheurs, validations depuis le téléphone…) est dans
[docs/feuille-de-route.md](docs/feuille-de-route.md).

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

**Limites des comptes, toujours visibles** : chaque pastille de compte en haut (Travail,
Perso) porte deux jauges, la session de 5 h et la semaine (« 5 · 52 % »), vertes, orange
au-delà de 70 %, rouges au-delà de 90 % ou limite atteinte ; un trait sous chaque compte de
la barre du bas reprend la session. Un clic sur la pastille donne le détail : pourcentages,
heures de réinitialisation, dépassement payant, date de la mesure et **Actualiser**. Les
chiffres arrivent avec chaque réponse de Claude (les mêmes que `/usage`) ; Actualiser, et
la lecture au démarrage si la mesure a plus de 3 h, envoient une toute petite requête Haiku
(désactivable dans Configuration → Général). Passé l'heure de réinitialisation, la jauge
revient à 0.

**Avant de lancer sur un compte plein** : une alerte à 80 % puis 90 % (et à la limite
atteinte) ; au lancement sur un compte presque plein ou bloqué, la console propose de
**lancer sur l'autre compte**, de **lancer à la réinitialisation** (la demande attend,
« Programmée · 15:01 », et part seule, même après un redémarrage de la console) ou de
lancer quand même. Une routine qui tombe sur un compte bloqué est reportée à la
réinitialisation au lieu d'échouer.

**Ctrl+K** (ou le bouton Rechercher) : une seule barre pour tout retrouver — actions,
projets, discussions de la console (titre, demandes et réponses de Claude) et sessions
Claude Code (Desktop, CLI), avec l'extrait trouvé ; flèches et Entrée pour ouvrir.

**Toujours pour ce projet** : dans une demande de validation, ce bouton approuve et
mémorise une règle pour les discussions du dossier (la plus étroite possible et
modifiable : une commande précise, les fichiers du dossier, un outil Odoo, un domaine
web). Les chemins protégés, les refus permanents et les contraintes Odoo passent toujours
avant. Les règles se relisent et se retirent dans Projet → Règles.

| Geste | Effet |
|---|---|
| `Entrée` / `Maj+Entrée` | envoyer / nouvelle ligne |
| `@work …`, `@perso …` | choisir le compte dans le texte |
| `/` | skills et commandes du profil (chargés au test de connexion ou à la première tâche) |
| `Alt+1`, `Alt+2` | changer de profil |
| `↑` / `↓` | rappeler une demande précédente |
| `Ctrl+,` | configuration |
| `Ctrl+Alt+W` | tout fermer : fenêtres de sessions, aperçus et modales (aussi Ranger → Tout fermer) |

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

### Projet : consignes, mémoire, fichiers, discussions

Le bouton **Projet** ouvre le dossier de travail choisi dans la barre du bas, comme un
Projet claude.ai. N'importe quel dossier du disque peut devenir un projet : **Parcourir…**
(ou « Autre dossier… » dans la barre du bas) ouvre un sélecteur avec Bureau, Documents,
Téléchargements, OneDrive, disques et lecteurs réseau, un champ pour coller un chemin et
« Nouveau dossier » ; les dossiers protégés n'y apparaissent pas. Le dossier choisi reste
dans la liste (12 derniers par compte). Le panneau contient :

- **Consignes** : le `CLAUDE.md` du dossier (lu par chaque discussion dans ce dossier,
  console, Claude Desktop onglet Code et CLI) et celui du compte (tous ses dossiers), à
  éditer sur place, avec un modèle pour démarrer ; plus les consignes propres à la console.
- **Mémoire** : ce que Claude Code a retenu d'une session à l'autre dans ce dossier, à
  corriger ou supprimer.
- **Fichiers** : le contenu du dossier (les fichiers protégés n'apparaissent pas), avec
  aperçu, « Citer » (ajoute le chemin à ta demande) et « Afficher dans le dossier ».
- **Discussions** : celles de la console dans ce dossier, avec Contexte et Copie, et
  les sessions Claude Desktop et CLI du dossier, et **Déplacer une session ici…**.

**Déplacer une session dans un projet** : c'est la même session, pas une copie. Claude
Code range chaque session sous son dossier ; la console la déplace sous le dossier du
projet (même identifiant, références au dossier mises à jour) et met à jour la fiche de
Claude Desktop, qui la retrouve aussi dans le nouveau dossier (ferme-la d'abord dans
Claude Desktop si elle y est ouverte). Trois accès : Projet → Discussions → « Déplacer une
session ici… », Sessions → bouton du dossier (« Dans … · déplacer… »), ou menu ⋯ d'une
fenêtre → « Déplacer vers un projet… » (la même fenêtre continue dans le projet). Une
discussion en cours ne se déplace pas. Cela permet aussi de reprendre une session dont le
dossier d'origine a été supprimé. Reprendre une session continue la même par défaut ;
« Continuer dans une copie » reste possible.

Les doublons créés par une ancienne version (une copie par déplacement) sont signalés en
haut du panneau Sessions : **Réunir en une seule session** redonne à chacun son
identifiant d'origine, avec toute la suite, dans le dossier du projet.

### Contexte d'autres discussions, copie d'une discussion

**Contexte**, dans la barre du bas : choisis jusqu'à 5 discussions du même compte
(console, Claude Desktop, CLI). Leur transcription propre (questions, réponses, actions,
sans les résultats bruts des outils) est jointe à la demande ; Claude la lit si elle
l'aide, ce qui coûte bien moins que de recharger toute une conversation. Aussi depuis le
menu ⋯ d'une fenêtre : « Utiliser comme contexte d'une demande ».

**Nouvelle discussion à partir d'ici** (menu ⋯ d'une fenêtre) : une copie repart avec
tout le contexte, dans le même dossier ; l'originale ne change pas.

### Ce que je regarde

Pour dire « corrige ce paragraphe » ou « explique ce tableau » sans copier-coller : quand tu
sélectionnes du texte (dans un aperçu, un affichage de Claude ou une de ses réponses) ou que tu
mets un aperçu ou un affichage au premier plan, une puce **Regard** apparaît dans la barre de
saisie (barre du bas, et suite de la fenêtre que tu utilises). Elle dit ce qui partira avec ton
message ; sa croix le retire. À l'envoi, la console ajoute au message le fichier ou la page
regardés (chemin, adresse), l'affichage (titre) et le texte sélectionné, présenté à Claude
comme une donnée et non comme une consigne. Ta bulle le rappelle (« Regard : rapport.md »).

- Un affichage ou un résultat d'outil (un mail…) d'une **autre discussion du même compte**
  part avec son contenu : la nouvelle discussion ne l'a jamais vu. D'un compte à l'autre,
  seuls le titre et ta sélection passent.
- Le regard ne donne aucun droit : lire le fichier passe par les autorisations de la
  discussion. Le texte sélectionné dans un PDF, un mail ou une page web (affichés dans un
  cadre isolé) n'est pas lisible par la console : seul le fichier ou la page est cité.
- Envoyé une fois : la puce revient dès que tu sélectionnes autre chose ou reviens sur un
  aperçu. Désactivable dans Configuration → Interface → « Joindre ce que je regarde ».

### Différences et annulation des modifications de fichiers

Chaque écriture de Claude par ses outils de fichiers (Write, Edit, MultiEdit, NotebookEdit,
sous-agents compris) est suivie : la console garde le fichier tel qu'il était juste avant
(au moment où tu valides, si l'écriture attendait ta validation) et tel que Claude l'a
laissé, dans ses données, inaccessibles aux agents.

- Sur l'action, un bouton **+2 −1** ouvre les **différences** (lignes retirées et ajoutées).
- Menu ⋯ d'une fenêtre → **Fichiers modifiés** : les fichiers touchés par la discussion,
  leurs modifications, **Tout annuler** pour un fichier (de la plus récente à la plus
  ancienne : il revient à son état d'avant la discussion).
- **Annuler** remet le fichier dans son état d'avant (un fichier créé par Claude est
  supprimé), après confirmation, seulement s'il n'a pas changé depuis : sinon la console le
  dit (« Modifié depuis », « Modifiée ensuite » quand une modification plus récente de Claude
  suit) et propose de forcer. **Rétablir** défait l'annulation, y compris une annulation
  forcée (ce qu'elle a écrasé est gardé).
- Claude l'apprend avec ton message suivant (« l'utilisateur a annulé tes modifications
  de … »), pour ne pas tenir le fichier pour ce qu'il a écrit.

Ne sont pas suivis : les fichiers écrits par une commande (Bash, PowerShell), ceux de plus
de 2 Mo. Les copies suivent la tâche : elles partent quand elle est supprimée ou purgée.

### Pièces jointes

Trombone de la barre du bas ou d'une fenêtre, **glisser-déposer** (sur la barre, sur une
fenêtre ou n'importe où dans la console) ou **coller une capture d'écran** (Ctrl+V).
Chaque fichier part aussitôt (barre de progression) ; à l'envoi, il est rangé dans le
dossier de la tâche, `~/ClaudeConsole/pieces-jointes/<date>_<tâche>/` (réglable dans
Configuration → Général), ajouté à ses dossiers de travail, et le message indique à Claude
où le lire : il ouvre lui-même PDF, images et textes. Les fichiers restent visibles dans
ta bulle (clic : aperçu), suivent la tâche si tu la relances, et ne sont jamais effacés
automatiquement. 20 fichiers de 100 Mo au plus par message.

### Aperçus : images, PDF, pages web

Les fichiers cités par Claude (chemin, lien `[texte](C:\...)`, image `![](...)`) sont
cliquables et s'ouvrent dans un **aperçu intégré** : images (miniature directement dans la
conversation), PDF, CSV en tableau, Markdown, JSON, texte, HTML (avec ses styles, sans ses scripts). Les
actions de fichier (Lire, Écrire, Modifier) ont un bouton œil, et une image créée par
Claude s'affiche aussitôt. Pour les autres types (Excel, Word…) : **Ouvrir avec
l'application** ou **Afficher dans le dossier**.

L'aperçu couvre les dossiers de la tâche, le dossier de travail du profil et les
fichiers que la conversation de la tâche a elle-même cités (réponse de Claude, ses
actions), par exemple un PDF copié ailleurs. Jamais un fichier protégé (Sécurité →
chemins interdits, données de la console), et la console ne lance jamais un programme
(`.exe`, `.bat`, `.ps1`, macros…) : elle peut seulement le montrer dans son dossier.

Les liens web s'ouvrent dans un **mini-navigateur isolé** (Ctrl/⌘ + clic : vrai
navigateur). Certains sites refusent l'affichage intégré : bouton « Ouvrir dans le
navigateur ». Les images web ne sont chargées qu'au clic, pour que le site qui les héberge
ne voie pas ta console sans ton accord ; Configuration → Interface permet de les charger
automatiquement.

**Claude peut aussi te montrer quelque chose** pendant une tâche, avec trois outils de la
console toujours autorisés (ils ne font que montrer) :

- `afficher` : un fichier s'ouvre aussitôt. Une page web s'ouvre seule si son site est dans
  **Configuration → Sécurité → Domaines approuvés** (ton Odoo, ton SharePoint ;
  `monentreprise.odoo.com` couvre aussi ses sous-domaines, https seulement), ou si c'est
  l'application d'un de tes serveurs MCP : l'adresse que tu lui as donnée (`ODOO_URL`,
  `--url https://…`), dans la configuration du profil ou celle de Claude Code. « Affiche-moi
  le dernier devis de X » ouvre ainsi le devis dans Odoo, « montre-moi ce mail » le mail
  dans une fenêtre (`afficher_resultat`). Sinon la tâche
  affiche « Claude veut ouvrir *site* » avec **Ouvrir**, **Toujours autoriser** (ajoute le
  domaine) et **Copier le lien** : rien n'est chargé tant que tu ne cliques pas.
- `afficher_resultat` : le résultat d'un outil que Claude a déjà reçu (un mail Office 365,
  un enregistrement Odoo, une recherche). Claude le désigne (outil, texte qu'il contient,
  rang) sans le recopier, et la console l'affiche tel quel depuis la conversation, même un
  résultat trop long que Claude Code a rangé dans un fichier. Un mail s'affiche avec son
  en-tête (De, À, Cc, date, pièces jointes), sa mise en forme d'origine et un bouton
  « Ouvrir dans Outlook » ; un JSON ou un texte, tel quel.
- `presenter` : un affichage composé de blocs typés, que la console vérifie puis dessine
  elle-même (Claude n'envoie ni HTML ni script) : texte, images (fichiers du projet ou du
  web), résultats de recherche, tableau triable, graphique (barres, courbe, secteurs, avec
  infobulles et vue tableau), fiche, chronologie, chiffres clés, progression, schéma SVG
  (affiché comme une image), fichiers, choix et boutons. Il s'affiche **dans la
  conversation**, **dans une fenêtre** ou **au premier plan** (une modale, seulement si tu
  regardes cette tâche ; sinon une fenêtre). Avec un `id`, Claude met à jour le même
  affichage (une progression, un tableau qui se remplit). Un choix ou un bouton cliqué
  revient à la session comme un nouveau message (« [Affichage « titre »] question →
  réponse »), une seule fois par bloc. Les images du web hors des domaines approuvés restent
  à charger d'un clic.

Les aperçus et les affichages s'ouvrent dans des fenêtres que l'on peut **épingler au
premier plan** (icône punaise ; un second clic les libère) ; la punaise d'une modale la
transforme en fenêtre épinglée, qui reste devant sans bloquer le reste de la console. Un
fichier ouvert en aperçu **suit ses modifications** : quand Claude (ou toi) le réécrit, il
se recharge à sa place, défilement conservé, avec « Mis à jour à … » sous le titre ; s'il
disparaît, l'aperçu garde sa dernière version et le signale. La console ne demande pour
cela que la date du fichier (après chaque outil, et toutes les 3 s tant qu'elle est
visible), jamais son contenu tant qu'il n'a pas changé.

Les pages HTML (mails, fichiers `.html`, résultats d'outils) sont servies depuis une
**autre origine**, `http://apercu.localhost:<port>` (Chrome et Edge envoient tout
`*.localhost` vers ce PC) : elles gardent leurs styles mais n'ont jamais accès au jeton,
aux données ni à l'API de la console. Chacune est derrière une adresse aléatoire qui expire
après 12 h, sans scripts, sans redirection automatique, et ses liens s'ouvrent dans un
onglet du navigateur. Ses images et polices du web ne sont chargées qu'au clic sur
**Afficher les images** (pas de pixel espion ni d'accusé de lecture) ; les images du même
dossier s'affichent directement.

Pour naviguer dans Odoo ou SharePoint en restant connecté, l'application de bureau ouvre
ces sites dans des fenêtres de JARVIS, chacune avec sa session (voir plus bas).

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

**Lanceur à épingler** : Configuration → Général → **Créer le lanceur**. Sous Windows,
« JARVIS Console » apparaît dans le menu Démarrer et sur le Bureau, avec l'icône JARVIS :
clic droit → Épingler à la barre des tâches. Sur Mac, une app « JARVIS Console » dans ton
dossier Applications, à glisser dans le Dock. Le lanceur démarre la console si besoin et
l'ouvre ; si l'app est installée (ci-dessus), il ouvre l'app installée, dont la fenêtre
porte aussi l'icône JARVIS au lieu de celle du navigateur.

### Application de bureau

**`build-app.bat`** (il faut Node.js 22.12 ou plus) construit et installe l'application de bureau
JARVIS (Electron), pour l'utilisateur, sans droits d'administrateur ; `start-app.bat` la lance sans
l'installer. En affichage **Intégré au bureau** :

- chaque discussion, aperçu ou affichage devient une vraie fenêtre de Windows (Alt+Tab, ancrage,
  plusieurs écrans) ;
- plus de fenêtre principale : **Ctrl+Alt+J** appelle la **barre JARVIS** (la barre de commande) en
  bas de l'écran, Échap la range ; son emblème ouvre le **menu JARVIS** (compteurs, comptes,
  Rechercher, Projet, Notes, Historique, Routines, Configuration, projets, discussions à reprendre) ;
- l'historique, les notes, les routines, la configuration, la recherche (Ctrl+K) et les questions
  s'ouvrent dans des fenêtres de Windows à elles ;
- une validation en attente produit une notification avec **Approuver** et **Refuser** ;
- les liens et les pages qu'affiche Claude (Odoo, SharePoint, Outlook web) s'ouvrent dans des
  fenêtres de sites, chacune avec sa session : on y reste connecté ;
- l'icône de la zone de notification montre les tâches en cours et à valider.

Configuration → Général → **Ouvrir avec** « Application de bureau JARVIS » la fait ouvrir par
`start.bat` et le lanceur ; **Démarrer avec la session** la démarre discrètement. Configuration →
Interface → **Affichage** repasse à une seule fenêtre. L'application se met à jour avec JARVIS (en un
clic ou `git pull`). Détails : [docs/electron.md](docs/electron.md).

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
- Les pages HTML montrées (mails, fichiers, résultats d'outils) vivent sur une origine
  séparée (`apercu.localhost`), sans scripts ni accès à la console ; leurs ressources du
  web attendent ton clic. Claude n'ouvre seul que les sites des domaines approuvés et des
  applications de tes serveurs MCP : les autres te sont proposés.
- L'environnement des tâches est nettoyé (`ANTHROPIC_*`, variables d'une session Claude
  parente) : aucune facturation par clé API par accident.
- Arrêt d'urgence : stoppe toutes les tâches et bloque les nouvelles jusqu'à réactivation.

Ne mets jamais ce serveur sur le réseau ou sur internet.

---

## Données et configuration

Tout est dans `data/` (ignoré par git) : `config.json` et ses versions précédentes
(`config-history/`), `console.db` (tâches, flux, journal d'audit, positions des
fenêtres), `modifications/` (copies avant / après des fichiers modifiés par Claude), `token`. La configuration s'exporte et s'importe en JSON (validée par schéma)
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
| `console/content.py` | origine des aperçus HTML (`apercu.localhost`) : adresses à durée limitée, CSP, images du web au clic |
| `console/results.py` | lecture d'un résultat d'outil (mail, HTML, JSON, texte, image) pour `afficher_resultat` |
| `console/display.py` | affichages de `presenter` : vérification des blocs, limites, réponses aux choix et boutons |
| `console/regard.py` | « Ce que je regarde » : vérification et mise en forme de ce qui part avec le message |
| `console/changes.py` | modifications de fichiers : copies avant / après, différences, annuler et rétablir |
| `console/routines.py` | routines : planification et calcul des prochaines exécutions |
| `console/winsys.py` | démarrage à l'ouverture de session (Windows, macOS) |
| `console/cloud.py` | routines claude.ai (relais Claude Code) |
| `console/store.py` | persistance SQLite |
| `static/` | interface (HTML, CSS, modules JavaScript sans dépendance) |
| `shell/` | application de bureau Electron : fenêtres natives, barre JARVIS, sites connectés, notifications, installateur |

## Installer sur un autre poste

Copie le projet **sans** `data/` ni `.venv/` (un dépôt Git les exclut d'office) : le
nouveau poste démarre avec une configuration vierge, ses propres comptes, sans tes
routines, ton historique ni ton jeton. Au premier lancement, un **assistant** guide la mise
en route : Claude Code trouvé, connexion de chaque compte (Se connecter / Tester), dossiers
de travail et premier projet, lanceur et démarrage avec la session. Il se rouvre depuis
Configuration → Général → Assistant de démarrage, ou Ctrl+K. Le plus simple : `git clone`
du dépôt, pour profiter ensuite des mises à jour en un clic.

## Mises à jour

Le code et les données sont séparés : `data/` (configuration, historique, routines,
jeton, journal) et `.venv/` ne sont jamais remplacés par une mise à jour.

**En un clic** (installation faite avec `git clone`) : la console regarde sur GitHub au
démarrage puis toutes les 6 h ; un bandeau **« Nouvelle version disponible »** propose
**Mettre à jour** (aussi dans Configuration → Général → Rechercher une mise à jour) : elle
récupère la version (`git pull`), réinstalle les dépendances si `requirements.txt` a
changé, puis redémarre. Elle refuse si des fichiers du programme ont été modifiés sur le
poste (rien n'est écrasé). Pour un dépôt privé, les identifiants GitHub doivent avoir été
enregistrés une fois (un premier `git pull` à la main).

À la main :

1. Arrête la console (Configuration → Général → Arrêter la console).
2. Remplace les fichiers du projet (idéalement `git pull` depuis un dépôt privé).
3. Relance `start.bat` / `start.command` : les dépendances sont réinstallées d'elles-mêmes
   si `requirements.txt` a changé. Les nouveaux réglages prennent leur valeur par défaut ;
   une configuration devenue invalide est mise de côté et la dernière version valide reprise.

La page se recharge avec les nouveaux fichiers, mais le serveur garde son ancien code tant
qu'il n'a pas redémarré : un bandeau **« Mise à jour installée »** l'indique alors, avec un
bouton **Redémarrer** (aussi dans Configuration → Général). Les tâches en cours sont
interrompues, la page se recharge d'elle-même. Si `requirements.txt` a changé, passe
plutôt par l'arrêt et `start.bat`, qui réinstalle les dépendances.

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
