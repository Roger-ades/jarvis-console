# Travail en équipe

Objectif : partager des **projets** entre les membres de l'équipe (3 personnes pour
l'instant) — leurs dossiers, des conversations avec Claude, et un fil de discussion
par projet — sans passer par les serveurs d'Anthropic (ni Projets claude.ai, ni
Cowork), avec un serveur Debian de la boîte.

## En place au 23 octobre 2026

La Debian est en service, et le hub tourne avec ce code (reprise faite le 23 octobre
après le push). Un `git push` de `main` dépose une demande : le hub tire la copie et se
relance tout seul. Rien à taper sur la Debian. La fin du push affiche `Hub à jour.`

**Sur la Debian**

- Compte système `jarvis` (sans connexion), répertoires du tableau ci-dessous, dépôt
  nu `/var/repo/jarvis-console.git` (compte `deploy`). Les postes poussent en SSH
  (`jarvis-server`, port 23). GitHub n'est plus l'origine des mises à jour.
- Syncthing `syncthing@jarvis` (v2, canal stable), interface seulement sur
  `127.0.0.1:8384`. Appareil `KBMT5Z4-TRKLGY2-SP6MNVQ-CUTE2A2-7ZMQQWO-FFFBB37-GGSNFDW-SSF2JQE`,
  nom SRV-DEBIAN. Le hub crée lui-même le dossier d'un projet d'équipe à la première
  inscription d'un poste : `/var/lib/jarvis/projets/<id>`, versions échelonnées,
  90 jours (`maxAge` 7776000 secondes). Dossiers présents : `essai` (relié à la main au
  départ) et `essai-equipe` (essai de bout en bout du 23 octobre).
- Hub `jarvis-hub.service` : `127.0.0.1:8770`, données `/var/lib/jarvis-hub/hub.db`.
  Santé vérifiée depuis le poste : `https://10.20.10.249:8443/health`.
- nginx : site à part, port **8443**, nom `jarvis.ades-securite.com`, certificat
  auto-signé `/etc/nginx/jarvis/hub.crt`. Empreinte SHA-256
  `53:1A:7C:BB:DD:2E:3F:CB:99:51:02:EE:68:96:D8:CE:0C:2A:9A:43:6B:55:72:9D:CE:CE:60:1D:8E:02:EB:A0`.
  Réservé à `127.0.0.1`, au LAN `10.20.10.0/24` et au VPN `10.10.0.0/24`.
- nftables : politique drop en entrée ; 8443, 22000 (TCP et UDP) et 21027 (UDP)
  seulement depuis le LAN et le VPN. Les ports déjà publics (22, 80, 443, 8080, 5000,
  5432) restent ouverts. Le SSH écoute sur le 22 ; le 23 des clients est une
  redirection en amont.
- Sauvegarde chaque nuit à 03:15 (`jarvis-hub-backup.timer`), 14 jours,
  `/var/lib/jarvis-hub/sauvegardes`.
- Premier compte administrateur créé (`roger`). Les suivants s'inscrivent depuis JARVIS
  (ci-dessous) ; la ligne de commande reste possible :
  `sudo -u jarvis bash -c 'cd /opt/jarvis-hub && .venv/bin/python -m hub user add prenom'`.

**Dans JARVIS**

- Inscription au premier lancement : l'adresse et l'empreinte du hub sont écrites dans le
  code (`console/hub_client.py`, `HUB_URL` et `HUB_FINGERPRINT`). Si ce poste n'a pas de
  jeton et que le hub répond (4 s au plus), la fenêtre « Rejoindre l'équipe » s'ouvre
  d'elle-même : nom affiché, pseudo (proposé d'après le nom de session Windows ou macOS,
  `paul.martin` → `paul-martin`) et couleur. « M'inscrire » crée le compte, garde le jeton
  dans `data/hub-token` et active l'équipe dans la configuration ; rien d'autre à saisir.
  « Plus tard », ou hors du bureau et du VPN : la fenêtre revient au lancement suivant.
- Le compte créé n'a aucun projet : il ne voit rien tant qu'un administrateur ne l'ajoute
  pas à un projet. Seule une copie de JARVIS peut s'inscrire : elle envoie une clé
  (`REGISTER_KEY`) dont le hub ne garde que l'empreinte (`REGISTER_KEY_HASH` dans
  `hub/app.py`, ou la variable `JARVIS_HUB_REGISTER_KEY_HASH`). Dix essais refusés en une
  minute bloquent les inscriptions une minute (429). Changer de clé : nouvelle clé dans
  `console/hub_client.py`, son SHA-256 dans `hub/app.py`, puis pousser ; les anciennes
  copies de JARVIS reçoivent « clé d'inscription refusée : mets JARVIS à jour ».
- Un pseudo pris, même par un compte révoqué, ne se réinscrit pas : une personne retirée
  ne retrouve pas ses projets en revenant. Un nouveau poste pour quelqu'un qui a déjà un
  compte : copier son `data/hub-token`, ou le coller dans Configuration → Équipe.
- L'administrateur du hub voit chaque inscription dans sa boîte de réception
  (« Inscription au hub », Révoquer…, Vu). Révoquer coupe le jeton et arrête le partage
  Syncthing des dossiers de ses projets avec ce poste (`DELETE /api/v1/users/{id}`).
- Configuration → Équipe : adresse, activation, empreinte à confirmer, jeton (fichier
  `data/hub-token`, jamais dans `config.json` ni dans la page), « Démarrer la synchro ».
- Un projet d'équipe se reconnaît partout sans fenêtre à part : badge « Équipe » dans
  le panneau Projet, section « Projets d'équipe » sur l'accueil, icône et mention
  « équipe » dans les listes de dossiers et la recherche (Ctrl+K).
- Projet → Équipe, pour un projet d'équipe : Fil (ouvert par défaut), Conversations,
  Membres (qui est là en ce moment, membres, « Relancer la synchro »). Les autres projets
  d'équipe n'y sont pas listés.
- Projet → Équipe, pour un projet personnel : un seul écran, « Partager avec l'équipe… »
  et « Rejoindre un projet d'équipe… ». Sans hub configuré, l'onglet n'apparaît pas.
- Partager (administrateur du hub) : le nom sur le hub et **les collègues choisis**, pas
  toute l'équipe ; celui qui partage en est l'administrateur. Le hub crée le projet avec
  ces membres, ce poste le rejoint aussitôt. Si un projet d'équipe du même nom existe déjà
  et qu'on en est membre (un dossier détaché, par exemple), la fenêtre propose de le
  rejoindre plutôt que d'en créer un second.
- Rejoindre se fait depuis l'accueil (« + Rejoindre un projet »), Ctrl+K, la boîte de
  réception ou l'écran ci-dessus : les projets du hub dont on est membre et pas encore sur
  ce poste (« ajouté par … · 3 membres »), avec recherche quand ils sont nombreux, puis le
  choix du dossier local. Le projet rejoint s'ouvre sur son Fil.
- Membres : chacun voit qui fait partie du projet, son rôle, qui l'a ajouté et quand.
  L'administrateur du hub ou du projet ajoute des collègues (liste à cocher, avec
  recherche), change un rôle (administrateur, membre) ou retire quelqu'un ; le dernier
  administrateur ne peut être ni retiré ni rétrogradé. Le hub cesse aussitôt de partager
  le dossier Syncthing avec le poste d'une personne retirée. Les jetons ne sortent jamais
  du hub.
- Quitter le projet (soi-même, `DELETE /api/v1/projects/{id}/members/me`) : le dernier
  administrateur nomme d'abord quelqu'un d'autre. Détacher de ce poste : le dossier
  redevient un projet personnel, ses fichiers restent, la synchro s'arrête ; on reste
  membre et on peut le rejoindre à nouveau.
- Invitations : un collègue ajouté à un projet le trouve dans sa boîte de réception
  (Rejoindre…, Ignorer) et sur l'accueil, où « + Rejoindre un projet » compte les
  invitations et présélectionne la première. Retiré puis ajouté à nouveau : l'invitation
  revient.
- Retiré d'un projet relié à ce poste : la boîte le signale (Détacher le dossier…) et
  l'onglet Équipe du projet le dit à la place du Fil et des membres, avec le même bouton.
  Rien n'est effacé sur le poste.
- Rejoindre écrit `.jarvis-projet` et `.stignore`, pose `team_id` sur le projet (le
  projet JARVIS est créé avec le nom et la couleur du hub s'il n'existe pas), lance
  Syncthing du poste (binaire dans les données de JARVIS, `127.0.0.1:8384`), demande au
  hub d'inscrire ce poste, puis partage le dossier avec la Debian en lui donnant son
  adresse (`tcp://<hub>:22000`, utile quand le VPN bloque la découverte). Tout est sans
  effet s'il est déjà fait ; un même projet ne se relie pas à deux dossiers.
- La synchro reprend d'elle-même au démarrage de la console ; « Relancer la synchro »
  (écran Équipe) et « Démarrer la synchro » (Configuration) relient à nouveau chaque
  projet d'équipe du poste.
- Windows (amd64, arm64) et macOS (universel) : Syncthing v2.1.7 téléchargé à la
  première synchro, vérifié contre la somme SHA-256 publiée avec la version.
- Partager une discussion du dossier (aperçu de ce qui est sensible, mode complet ou
  sans résultats d'outils, chemins remplacés par `{{PROJET}}`). Reprendre la ramène
  dans le dossier et continue avec le compte Claude de la personne.
- Fil : envoyer (Ctrl+Entrée) et lire les messages du projet, avec le nom et l'initiale
  de chacun ; il se relit toutes les 15 secondes tant qu'il est affiché. `@nom` mentionne
  un collègue. Ce qui est affiché est lu (`/api/hub/seen`) : la boîte et le compteur de
  l'onglet suivent. Une modification vide est refusée (422). Mémoire : « Copier dans la mémoire
  d'équipe » écrit `MEMOIRE-EQUIPE.md` et l'ajoute au `CLAUDE.md` du dossier.

**Pas encore**

- Lire une conversation partagée sans la reprendre ; mettre à jour le partage ; la
  retirer depuis l'écran ; lier une reprise à la précédente (« suite de … »). L'archive
  ne contient que la transcription, pas le dossier des sous-agents.
- Le fil ne fait pas encore les références, ni modifier ou effacer un message depuis
  l'écran (l'API le permet).
- La présence dit qu'une session est ouverte. Elle n'envoie pas encore les fichiers
  que Claude modifie, et JARVIS n'avertit pas avant d'écrire un fichier déjà ouvert
  chez un collègue. Les conflits `*.sync-conflict-*` ne sont pas signalés.
- La console interroge le hub toutes les 30 secondes (boîte de réception) ; elle ne suit
  pas encore le flux `/events`.
- Renommer un compte, ou rendre administrateur : seulement en ligne de commande sur la
  Debian. Renommer ou supprimer un projet d'équipe : ni écran ni route (`essai-equipe` reste
  donc sur le hub et sur le serveur).
- Validations depuis le téléphone, et les scripts de projet : hors de ce chantier.

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
- **Postes** : pas de logiciel de synchro à part. JARVIS embarque le moteur Syncthing,
  le lance en arrière-plan et parle à son API locale (`127.0.0.1:8384`). La personne
  ne voit que JARVIS : « Rejoindre » crée le partage, la console affiche l'état
  (à jour, en cours, en erreur). Chaque poste ne partage qu'avec la Debian, qui sert
  d'*introducer* : pas de réglage croisé entre postes.
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

Le partage ne se fait pas à la main dans une autre application. « Rejoindre » écrit le
marqueur et le `.stignore`, puis Syncthing lancé par JARVIS (binaire dans les données
de la console, interface seulement sur `127.0.0.1:8384`) partage le dossier avec
l'appareil Debian. Le hub, qui tourne sous `jarvis`, inscrit ce poste sur le dossier
du projet par l'API REST de Syncthing (clé lue dans sa `config.xml`), et crée ce
dossier, versionné, s'il n'existe pas encore. « Rejoindre » lance ce moteur ; Configuration → Équipe, « Démarrer la
synchro », le fait aussi sans rejoindre un projet.

## 2. Le hub

Même technique que la console, pour réutiliser le code et les habitudes : **FastAPI +
SQLite**, un paquet `hub/` dans ce dépôt, déployé sur la Debian avec un venv et un service
systemd (`jarvis-hub`).

### Répertoires sur la Debian

Un seul compte système, `jarvis` (sans connexion), pour le hub et pour Syncthing
(`syncthing@jarvis`). Le code, les données et les fichiers des projets ne sont pas au
même endroit.

| Rôle | Chemin | Contenu |
| --- | --- | --- |
| Dépôt git | `/var/repo/jarvis-console.git` | Dépôt nu, compte `deploy`, comme PassLock. Les postes poussent et tirent en SSH. Plus de GitHub |
| Service | `/opt/jarvis-hub` | Copie de travail tirée de ce dépôt, `.venv`, `WorkingDirectory` de `jarvis-hub` |
| Configuration | `/etc/jarvis-hub/hub.env` | Adresse d'écoute et réglages du service, pas les jetons des personnes |
| Données du hub | `/var/lib/jarvis-hub` | `hub.db`, sauvegardes, conversations partagées |
| Compte `jarvis` | `/var/lib/jarvis` | Home du compte : configuration Syncthing |
| Projets | `/var/lib/jarvis/projets/<id>` | Dossiers Syncthing, un par projet d'équipe |

Le dépôt nu appartient au compte `deploy`, comme `/var/repo/passlock.git`. Les postes s'y connectent en SSH (port 23, clé dédiée). GitHub n'est plus une origine.

Les transcriptions partagées restent des fichiers :
`/var/lib/jarvis-hub/conversations/<id>/<rev>.zip`. La présence vit en mémoire seulement.
Le hub ne lit pas `/var/lib/jarvis/projets` : Syncthing seul y écrit.

- **Réseau** : le hub écoute en local (`127.0.0.1:8770`). nginx le publie en HTTPS
  sur le port **8443**, nom `jarvis.ades-securite.com`, adresse LAN `10.20.10.249`.
  Empreinte SHA-256 du certificat : `53:1A:7C:BB:DD:2E:3F:CB:99:51:02:EE:68:96:D8:CE:0C:2A:9A:43:6B:55:72:9D:CE:CE:60:1D:8E:02:EB:A0`.
  nftables n'ouvre ce port que pour le LAN `10.20.10.0/24` et le VPN `10.10.0.0/24`.
  Le port 443 reste celui de nginx pour les sites déjà en ligne. Les ports déjà publics
  (22, 80, 443, 8080, 5000, 5432) restent ouverts. Le SSH écoute sur le **22** ;
  le port 23 des clients est une redirection en amont vers ce 22. Syncthing (TCP/UDP
  22000 et UDP 21027) n'est joignable que depuis le LAN et le VPN. Son interface
  reste sur `127.0.0.1:8384`.
- **Certificat épinglé** : à la configuration, Jarvis enregistre l'empreinte du certificat
  du hub et refuse toute autre. Pas d'autorité à installer sur les postes.
- **Comptes** : un compte par personne, créé par JARVIS à l'inscription (clé intégrée,
  compte sans projet) ou par l'administrateur en ligne de commande sur la Debian :
  `python -m hub user add paul`, qui affiche un **jeton personnel** une seule fois. Le hub
  n'en garde que l'empreinte (SHA-256). Révoquer : depuis la boîte de réception de
  l'administrateur, ou `python -m hub user revoke paul`.
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

### API (v1, toutes avec `Authorization: Bearer <jeton>` sauf l'inscription)

```
POST   /api/v1/register                     inscription : clé de JARVIS, pseudo, nom, couleur → jeton
GET    /api/v1/me
GET    /api/v1/users                        comptes actifs (id, nom, couleur)
DELETE /api/v1/users/{u}                    (admin) révoquer un compte
GET    /api/v1/projects                     projets dont je suis membre
POST   /api/v1/projects                     (admin) créer
PUT    /api/v1/projects/{p}                 (admin) nom, couleur, réglages, dossier Syncthing
GET    /api/v1/projects/{p}/members         membres et rôles
PUT    /api/v1/projects/{p}/members         (admin du hub ou du projet)
GET    /api/v1/events?since=<curseur>       flux SSE : messages, présence, conversations
POST   /api/v1/presence                     battement toutes les 30 s
GET    /api/v1/syncthing                    identifiant de l'appareil Debian
POST   /api/v1/projects/{p}/sync            inscrit le poste sur le dossier du projet
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

- `console/hub_client.py` : appels HTTPS (bibliothèque standard, certificat épinglé).
  La console interroge le hub ; le flux `/events` existe côté hub, il n'est pas encore
  relayé dans la console.
- Jeton dans `data/hub-token` (comme `data/token`), jamais dans `config.json` ni dans
  l'export de configuration.
- `Configuration → Équipe` : adresse du hub, jeton, empreinte du certificat (affichée au
  premier contact, à confirmer), activer ou non.
- Le modèle `Project` gagne `team_id` : le lien entre un projet d'équipe et le dossier
  local. Le compte (profil) reste un choix de chacun, jamais partagé.

## 3. Projets d'équipe et présence

*En place : liste, création depuis un projet JARVIS, membres, Rejoindre, ligne de
présence. Pas encore : réglages communs repris
chez soi, fichiers dans la présence, avertissement avant écriture, conflits Syncthing.*

- Sur l'**accueil**, la section « Projets d'équipe » montre ceux reliés à ce poste ;
  « + Rejoindre un projet » liste les autres → choisir le dossier local (celui de Syncthing).
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

*En place : Partager (chemins, aperçu, deux modes) et Reprendre. Pas encore : Lire sans
reprendre, mettre à jour, retirer depuis l'écran, sous-agents dans l'archive, « suite de ».*

La console sait réécrire une transcription pour un autre dossier
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

*Pas encore.* Dans le projet, onglet **Conversations** : la liste des conversations
partagées est en place (auteur, date, révision, mode). « Lire » doit encore télécharger
l'archive dans un cache et l'afficher avec le lecteur de l'historique, sans rien
importer dans le profil. Aujourd'hui le seul geste est **Reprendre**.

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
  plusieurs Mo. Le hub refuse une archive au-delà de 50 Mo.

### Retirer

L'auteur (ou l'administrateur) peut retirer une conversation du hub. Les copies déjà
reprises par d'autres restent sur leurs postes : l'écran de publication le dit.

## 5. Le fil du projet

*En place : onglet Fil, envoyer et relire les messages (relu toutes les 15 s). Pas encore : compteur de non-lus,
mentions, modifier ou effacer depuis l'écran, références, notifications, cache hors ligne.*

Un fil de messages par projet, dans la fenêtre du projet (onglet **Fil**). Le compteur
de non-lus dans la barre du haut n'est pas encore là.

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

*En place : le bouton copie la note. Le résumé du fil reste pour plus tard.*

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

## Où on en est

0. **Debian.** *En service* : Syncthing avec versions, pare-feu, nginx sur le 8443,
   sauvegarde de nuit, hub systemd. Le hub crée le dossier de chaque projet d'équipe.
1. **Hub et projets d'équipe.** *En place* pour les comptes, les jetons, les projets
   (créés depuis JARVIS avec les collègues choisis), les membres (ajout, rôle, retrait,
   quitter, détacher), les invitations dans la boîte de réception, Configuration → Équipe
   et Rejoindre, sur Windows et macOS. La présence signale une session ouverte. Restent
   les réglages communs repris chez soi, le signalement des conflits, renommer ou
   supprimer un projet.
2. **Conversations partagées.** *En place* pour publier et reprendre. Restent la lecture
   sans import, la mise à jour, le retrait depuis l'écran et la reprise liée (« suite de »).
3. **Fil du projet.** *En place* pour écrire, lire, mentionner ; les non-lus et les
   mentions arrivent dans la boîte de réception. Restent le temps réel et les références.
4. **Mémoire d'équipe.** *En place* (copie d'une note). Le résumé du fil reste à faire,
   avec le compte Claude de la personne qui le demande, sans publier le résumé.

Tests : `tests/test_hub.py`, `tests/test_equipe.py`, `tests/test_share.py`,
`tests/test_equipe_boite.py` (jeton jamais en clair, membres et dernier administrateur,
quitter, archives, masquage des chemins, inscription Syncthing, création avec membres et
rejointe d'un projet, entrées de la boîte). Essai réel le 23 octobre avec un poste : projet `essai-equipe`
créé, poste inscrit, fichier reçu par le serveur (100 %), fil et membres relus. Pas
encore d'essai avec deux postes.

Ce qui ne change pas : chaque Jarvis reste local et autonome, la politique
d'autorisations, les aperçus, les profils et leurs comptes.

## Questions encore ouvertes

- Durée de conservation des conversations partagées et des messages (illimitée, 1 an…) ?
  Les versions de fichiers, elles, sont à 90 jours.
- Les deux collègues ont-ils déjà Jarvis et un compte Claude Team chacun ?

Tranché : l'adresse du hub est `https://10.20.10.249:8443`
(`jarvis.ades-securite.com`), LAN `10.20.10.0/24`, VPN `10.10.0.0/24`. Les mises à jour
de JARVIS partent du dépôt Debian, pas de GitHub.
