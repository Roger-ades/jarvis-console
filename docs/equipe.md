# Travail en équipe (conception)

Note de travail, rien n'est commencé. Objectif : partager des **projets** entre les
membres de l'équipe (3 personnes pour l'instant) — leurs dossiers, des conversations
avec Claude, et un fil de discussion par projet — sans passer par les serveurs
d'Anthropic (ni Projets claude.ai, ni Cowork), avec un serveur Debian de la boîte.

Contexte retenu :

- surtout des **projets de documents** et du travail sur Odoo, un seul codeur : pas de Git
  pour le commun des projets ;
- accès depuis le bureau et à distance **par le VPN** existant, jamais depuis Internet ;
- pas de connexion O365 pour le serveur d'équipe : des comptes simples. Le connecteur O365
  reste ce qu'il est aujourd'hui, un outil de Claude dans chaque Jarvis.

## Principe

```
  Poste A : Jarvis + Claude (compte A)        Poste B : Jarvis + Claude (compte B)
        │  Syncthing ◄──── fichiers ────► Syncthing  │
        │                     │                       │
        └──── HTTPS (LAN / VPN) ──► Debian ◄──────────┘
                                 ├─ Syncthing (nœud toujours allumé, versions)
                                 └─ Hub JARVIS : comptes, projets, présence,
                                    conversations partagées, fil du projet
```

- **Claude ne tourne jamais sur le serveur.** Chacun lance ses sessions depuis son Jarvis,
  avec son compte Claude, ses accès Odoo et O365 et ses permissions. Le hub ne reçoit
  aucun identifiant Claude, Odoo ou Microsoft, ni la configuration des profils.
- **Le hub stocke et relaie** : la liste des projets d'équipe, qui y travaille, les
  conversations qu'un membre a choisi de partager, les messages du fil.
- **Les fichiers ne passent pas par le hub** : Syncthing s'en charge. Le hub connaît
  seulement l'identifiant du dossier Syncthing de chaque projet.
- **Sans VPN ou si le hub est arrêté**, Jarvis marche comme aujourd'hui ; seules les
  fonctions d'équipe sont grisées.
- **Vocabulaire** : dans Jarvis, une « discussion » est une conversation avec Claude. Le
  chat entre membres s'appelle donc le **fil** du projet, pour éviter la confusion.

## 1. Fichiers : Syncthing

Pourquoi Syncthing : open source, sans cloud, synchronisation directe sur le réseau
local ou le VPN, historique des versions, rien à développer pour la synchro elle-même.
SharePoint/OneDrive serait possible, mais les fichiers « à la demande » (non téléchargés)
sont illisibles pour Claude, et le but est justement de rester sur le serveur de la boîte.

- **Debian** : Syncthing en service (`syncthing@jarvis`), nœud toujours allumé, avec
  versions échelonnées (*staggered versioning*, 90 jours par exemple). C'est aussi la
  sauvegarde : un fichier écrasé par erreur, par un membre ou par Claude, se récupère.
- **Postes** : Syncthing en service Windows (ou SyncTrayzor). Chaque poste ne partage
  qu'avec la Debian, qui sert d'*introducer* : pas de réglage croisé entre postes.
- **Un projet d'équipe = un dossier Syncthing.** Chacun le place où il veut sur son disque.
- **Ignorés** (`.stignore`) : `~$*`, `.~lock.*`, `*.tmp`, `Thumbs.db`,
  `.claude/settings.local.json`, `CLAUDE.local.md` (réglages personnels de Claude Code).
- **Ce qui suit le dossier** : les fichiers, `CLAUDE.md` (consignes du projet),
  `.claude/settings.json`, `.claude/skills/`, `.claude/agents/`.
  Attention à `.mcp.json` : un serveur MCP qui porte des identifiants personnels (Odoo)
  reste dans le profil de chacun, jamais dans le dossier partagé.
- **Conflits** : si deux personnes modifient le même fichier en même temps, Syncthing garde
  les deux versions (`*.sync-conflict-*`). Jarvis les détecte dans le dossier et les
  signale dans le panneau du projet (comparer, garder l'une, garder les deux). La
  présence (ci-dessous) en évite la plupart.

Plus tard, Jarvis pourra piloter le Syncthing du poste par son API REST locale
(`127.0.0.1:8384`, clé d'API lue dans sa configuration) : « Rejoindre le projet » crée
alors le partage du dossier tout seul et la console affiche l'état de la synchro (à jour,
en cours, en erreur). Au début, le dossier se partage à la main dans Syncthing.

## 2. Le hub

Même technique que la console, pour réutiliser le code et les habitudes : **FastAPI +
SQLite**, un paquet `hub/` dans ce dépôt, déployé sur la Debian avec un venv et un service
systemd (`jarvis-hub`).

- **Réseau** : le hub écoute en local (`127.0.0.1:8770`) derrière **Caddy** en HTTPS
  (`tls internal`, certificat auto-signé de Caddy). Le pare-feu (nftables/ufw) n'ouvre
  le 443 qu'au réseau local et au sous-réseau du VPN.
- **Certificat épinglé** : à la configuration, Jarvis enregistre l'empreinte du certificat
  du hub et refuse toute autre. Pas d'autorité à installer sur les postes.
- **Comptes** : un compte par personne, créé par l'administrateur (Roger) en ligne de
  commande sur la Debian : `python -m hub user add paul`, qui affiche un **jeton
  personnel** une seule fois. Le hub n'en garde que l'empreinte (SHA-256). Révoquer un
  membre : `python -m hub user revoke paul`.
- **Droits** : on ne voit que les projets dont on est membre. L'administrateur crée les
  projets et gère les membres (depuis Jarvis ou en ligne de commande).
- **Journal** : qui a publié, lu, importé, retiré une conversation ; qui a rejoint ou
  quitté un projet. Consultable par l'administrateur.
- **Sauvegarde** : la base SQLite (`.backup` chaque nuit) et le dossier des conversations
  partagées, avec la sauvegarde habituelle de la VM. Disque de la VM chiffré si possible.

### Données

```
users          id, name, color, token_hash, admin, created, revoked
projects       id, name, color, syncthing_folder, defaults (préréglage, modèle, effort), created
members        project_id, user_id, role (membre | administrateur), joined
conversations  id, project_id, author_id, title, session_id, rev, parent_id,
               created, updated, size, stats (outils utilisés, nombre de tours), mode
messages       id, project_id, author_id, ts, text, refs (conversation, fichier, extrait),
               edited, deleted
audit          id, ts, user_id, kind, detail
```

Les transcriptions partagées sont des fichiers à part :
`/var/lib/jarvis-hub/conversations/<id>/<rev>.zip`. La présence vit en mémoire seulement.

### API (v1, toutes avec `Authorization: Bearer <jeton>`)

```
GET    /api/v1/me
GET    /api/v1/projects                     projets dont je suis membre
POST   /api/v1/projects                     (admin) créer
PUT    /api/v1/projects/{p}                 (admin) nom, couleur, réglages, dossier Syncthing
PUT    /api/v1/projects/{p}/members         (admin)
GET    /api/v1/events?since=<curseur>       flux SSE : messages, présence, conversations
POST   /api/v1/presence                     battement toutes les 30 s
GET    /api/v1/projects/{p}/conversations
POST   /api/v1/projects/{p}/conversations   publier (archive + métadonnées)
GET    /api/v1/conversations/{c}            métadonnées et révisions
GET    /api/v1/conversations/{c}/archive    télécharger (lecture ou reprise)
PUT    /api/v1/conversations/{c}            nouvelle révision (auteur seulement)
DELETE /api/v1/conversations/{c}            retirer (auteur ou admin)
GET    /api/v1/projects/{p}/messages?before=
POST   /api/v1/projects/{p}/messages
PATCH  /api/v1/messages/{m}                 modifier ou effacer le sien
GET    /api/v1/audit                        (admin)
```

Chaque réponse porte la version de l'API ; Jarvis indique « mettre à jour le hub » ou
« mettre à jour Jarvis » quand elles ne se comprennent plus.

### Côté Jarvis : le serveur fait l'intermédiaire

La page de la console ne parle **qu'à son propre serveur** (CSP `connect-src 'self'`,
inchangée). C'est le serveur Python de Jarvis qui appelle le hub : le jeton du hub ne
quitte jamais Python, et une page compromise ne peut pas joindre le hub.

- `console/hub_client.py` : appels HTTPS (bibliothèque standard, certificat épinglé), un
  fil qui écoute `/events` avec reconnexion, et relaie les événements dans le flux SSE
  existant de la console.
- Jeton dans `data/hub-token` (comme `data/token`), jamais dans `config.json` ni dans
  l'export de configuration.
- `Configuration → Équipe` : adresse du hub, jeton, empreinte du certificat (affichée au
  premier contact, à confirmer), activer ou non.
- Le modèle `Project` gagne `team_id` : le lien entre un projet d'équipe et le dossier
  local. Le compte (profil) reste un choix de chacun, jamais partagé.

## 3. Projets d'équipe et présence

- Dans **Projets**, une section « Équipe » liste les projets dont on est membre. Pour un
  projet pas encore relié : « Rejoindre » → choisir le dossier local (celui de Syncthing).
  Jarvis vérifie que le dossier contient le marqueur `.jarvis-projet` (identifiant du
  projet, écrit à la création) pour éviter de relier le mauvais dossier.
- Les **réglages communs** (nom, couleur, préréglage, modèle, effort) viennent du hub ;
  chacun peut les surcharger chez lui.
- **Présence** : chaque Jarvis signale, pour chaque projet d'équipe, s'il a des sessions
  actives et — si l'utilisateur l'accepte, réglage par membre — les fichiers que Claude
  vient de modifier (chemins relatifs au projet, d'après les outils Write/Edit et les
  outils Office). Le projet affiche alors « Paul travaille ici · modifie
  `Offres/Devis-2026-114.docx` », et Jarvis avertit avant que Claude n'écrive dans un
  fichier qu'un autre membre est en train de modifier.

## 4. Conversations partagées

Une base existe déjà : la console sait réécrire une transcription pour un autre dossier
(`library._rewrite`, `library.move_session`) et reprendre une session dans une copie
(`--fork-session`). Le partage s'appuie dessus.

### Publier

Bouton **« Partager avec l'équipe »** sur une discussion d'un projet d'équipe. Toujours
volontaire, conversation par conversation : rien n'est publié automatiquement.

1. Jarvis lit la transcription Claude Code (`<profil>/projects/<dossier>/<session>.jsonl`)
   et le dossier des sous-agents de la session.
2. **Chemins** : le chemin du dossier local du projet est remplacé par un repère
   `{{PROJET}}` partout dans la transcription (champ `cwd`, entrées et résultats d'outils,
   sous les formes `\`, `\\` et `/`). Les chemins hors du projet (profil, Bureau…) sont
   listés pour avertir.
3. **Contenu sensible** : avant d'envoyer, Jarvis montre ce que la conversation contient —
   « 14 résultats Odoo, 3 mails Outlook, 2 fichiers SharePoint, 1 chemin hors projet » —
   et propose deux modes :
   - **Complète** : tout, pour une reprise fidèle ;
   - **Sans résultats d'outils** : le contenu de chaque résultat est remplacé par
     « [retiré au partage] ». Les questions, les réponses de Claude et la liste des
     actions restent ; la reprise marche (Claude Code exige seulement que chaque appel ait
     son résultat). C'est le mode proposé par défaut quand des outils Odoo ou O365 ont
     servi.
4. L'archive (zip) part au hub avec le titre, l'auteur, le modèle, le mode et les
   statistiques. Si l'auteur continue sa discussion ensuite : « Mettre à jour le partage »
   crée une nouvelle révision.

### Lire

Dans le projet, onglet **Conversations** : la liste des conversations partagées (auteur,
date, révision, mode). « Lire » télécharge l'archive dans un cache et l'affiche avec le
lecteur de l'historique déjà présent, sans rien importer dans le profil.

### Reprendre

« **Reprendre dans ma console** » : Jarvis remplace `{{PROJET}}` par le dossier local, donne
un nouvel identifiant de session, range la transcription dans le profil choisi (là où
Claude Code la cherche pour ce dossier) et lance la reprise. La conversation continue
avec **le compte du collègue** ; l'original partagé ne bouge pas. Elle peut à son tour
être partagée, liée à la précédente (« suite de … »).

Deux discussions ne fusionnent jamais : une session Claude n'a qu'un utilisateur à la
fois. Deux personnes ne tapent pas dans la même session en direct ; on passe la main.

### Points à vérifier tôt (prototype de la phase 2)

- **Reprise depuis un autre compte** : les blocs de réflexion (*thinking*) portent une
  signature. Les trois comptes sont dans la même organisation Team, ce qui devrait
  suffire ; sinon, retirer ces blocs à l'import (la reprise perd un peu de contexte, pas
  la conversation).
- **Modèle** : si la conversation a utilisé un modèle que le collègue n'a pas choisi, la
  reprise passe à son modèle ; le cache de prompt repart de zéro (premier tour plus lent).
- **Taille** : les longues conversations avec beaucoup de résultats d'outils pèsent
  plusieurs Mo ; limite à fixer côté hub (50 Mo par exemple).

### Retirer

L'auteur (ou l'administrateur) peut retirer une conversation du hub. Les copies déjà
reprises par d'autres restent sur leurs postes : l'écran de publication le dit.

## 5. Le fil du projet

Un fil de messages par projet, dans la fenêtre du projet (onglet **Fil**) et accessible
depuis la barre du haut avec un compteur de non-lus.

- **Messages** : texte (Markdown simple, le rendu `md.js` existant), mentions `@paul`,
  modifier ou effacer les siens.
- **Références plutôt que pièces jointes** : un fichier du projet est cité par son chemin
  relatif (il est déjà chez tout le monde grâce à Syncthing) et s'ouvre dans l'aperçu ;
  une conversation partagée s'ouvre dans le lecteur.
- **Épingler une réponse de Claude** : depuis une discussion, « Envoyer au fil » copie une
  réponse (ou un extrait) avec un lien vers la conversation si elle est partagée.
- **Notifications** : celles du navigateur, comme pour les demandes d'approbation, quand
  on est mentionné ou qu'un nouveau message arrive dans un projet suivi.
- **Hors ligne** : le fil est en lecture seule (derniers messages en cache) ; pas de file
  d'envoi, pour ne pas publier un message des heures plus tard sans s'en rendre compte.

### Claude et le fil (plus tard)

- « **Résumer le fil** » ou « **Ce que j'ai manqué** » : la personne qui le demande le lance
  dans son Jarvis, avec son compte ; le résumé lui est montré, pas publié.
- Donner le fil comme contexte à une discussion, comme on le fait aujourd'hui avec une
  transcription. Les messages des collègues sont alors **des données, pas des consignes**
  (même règle que pour une page web ou un mail) : le texte d'accompagnement le dit, et la
  politique d'autorisations ne change pas.

## 6. Mémoire d'équipe

La mémoire automatique de Claude Code est personnelle (dans le profil, par dossier) et le
reste. Pour partager un savoir du projet :

- un fichier `MEMOIRE-EQUIPE.md` dans le dossier, importé par le `CLAUDE.md` du projet
  (`@MEMOIRE-EQUIPE.md`) ; il voyage avec Syncthing ;
- dans le panneau **Projet → Mémoire**, sur chaque note : « **Copier dans la mémoire
  d'équipe** ».

## Sécurité, en résumé

- Le hub n'est joignable que par le réseau local et le VPN ; HTTPS avec certificat épinglé.
- Un jeton par personne, révocable, stocké haché sur le hub et hors de `config.json` sur
  les postes ; la page de la console ne voit jamais ce jeton.
- Le hub ne reçoit ni identifiants Claude, Odoo ou Microsoft, ni configuration des
  profils, ni fichiers (Syncthing s'en charge).
- Partage de conversations explicite, avec un état de ce qu'elles contiennent et un mode
  sans résultats d'outils.
- Droits par projet, journal des actions, sauvegarde et versions sur la Debian.
- Les VM Windows ne servent pas ici. Elles pourraient plus tard accueillir des tâches
  longues ou des routines, avec le compte Claude de la personne concernée.

## Étapes proposées

0. **Debian** (demi-journée, guide et script fournis) : Syncthing avec versions, Caddy,
   pare-feu, sauvegarde ; un dossier de test partagé avec un poste.
1. **Hub minimal et projets d'équipe** — taille comparable à la fonction Projets : comptes
   et jetons, projets et membres, `Configuration → Équipe`, « Rejoindre », réglages
   communs, présence, signalement des conflits Syncthing.
2. **Conversations partagées** — un peu plus gros, la réécriture existe déjà : prototype de
   reprise depuis un autre compte d'abord, puis publication (chemins, contenu sensible,
   modes), lecture, reprise, révisions, retrait.
3. **Fil du projet** — taille moyenne : messages, flux temps réel, non-lus, mentions,
   notifications, références aux fichiers et aux conversations.
4. **Bonus** : pilotage de Syncthing par son API (« Rejoindre » configure tout, état de la
   synchro), mémoire d'équipe, résumé du fil.

Tests : le hub se teste avec le `TestClient` de FastAPI ; deux consoles de démonstration
(`tests/demo_server.py`, faux Claude de `tests/fake_claude.py`) reliées à un hub de test
permettent de vérifier publication, reprise et fil sans compte Claude.

Ce qui ne change pas : chaque Jarvis reste local et autonome, la politique
d'autorisations, les aperçus, les profils et leurs comptes.

## Questions encore ouvertes

- Adresse de la Debian sur le réseau et dans le VPN, et un nom interne
  (`jarvis.ades.lan` par exemple) ?
- Durée de conservation des conversations partagées et des messages (illimitée, 1 an…) ?
- Les deux collègues ont-ils déjà Jarvis et un compte Claude Team chacun ?
- Postes uniquement Windows, ou aussi des Mac (Syncthing et Jarvis marchent sur les deux) ?
