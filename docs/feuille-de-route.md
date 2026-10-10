# Feuille de route : tout faire depuis Jarvis

Note de travail. Objectif : qu'une fois dans Jarvis, l'utilisateur n'ait plus de raison d'en sortir,
et qu'il ait le sentiment que Claude et l'interface ne font qu'un : on parle à JARVIS, et JARVIS ouvre,
range, retrouve et relie lui-même. Jarvis couvre déjà très bien le pilotage de Claude (sessions
parallèles, validations, projets, routines, `presenter`). Cette note recense ce qui oblige encore à
quitter la console ou à faire soi-même ce que JARVIS devrait faire, les pistes pour y remédier et
l'ordre proposé. Le détail de conception des pistes d'interface est dans [ihm.md](ihm.md), celui de
l'intégration au bureau (Electron) dans [electron.md](electron.md), celui de la boîte de réception
dans [boite-de-reception.md](boite-de-reception.md), celui du travail en équipe dans
[equipe.md](equipe.md).

Statuts : **Fait** (en place et utilisé), **En cours**, **Partiel** (une partie existe, le reste est
décrit), **À faire** (conçu, pas commencé), **À trancher** (une décision à prendre avant).

## Point au 5 octobre 2026

| Chantier | Statut |
|---|---|
| « Ce que je regarde », différences et annulation des fichiers | Fait |
| JARVIS intégré au bureau (Electron), sites connectés, notifications | Fait |
| Boîte de réception, brief du matin, actions du compte en boutons | Fait (à essayer encore sous Windows) |
| Aperçu Office, index des documents | Fait |
| Bloc `formulaire`, cartes du rapport | Fait |
| Widgets du bureau (fenêtres natives en mode intégré) | Fait |
| Brief de projet | En cours (cartes et BRIEF.md en place ; clôturer dans Odoo ou Office 365 sans repasser par Claude reste à faire, voir [ihm.md](ihm.md)) |
| **Office 365 dans le quotidien** (tâches, brouillons, relances) | En cours (onglet Suivi du projet : lecture commencée ; le fil du compte, hors projet, reste à faire) |
| **La conversation pilote JARVIS** (projet, activité, Odoo, OneDrive) : axe 0 | En cours (Odoo relié au projet : fait) |
| **Regard partout** | Partiel |
| **Archivage des discussions** | Fait (reste à trancher : la purge des archives) |
| **Reprise d'une discussion inactive** (cache, compactage) | Partiel (« Garder au chaud » existe, à la main) |
| Déclencheurs (dossier surveillé, enchaînement) | À faire |
| Validations depuis le téléphone | À faire |
| `lancer_discussion`, outils-scripts | À faire |
| Vue « Consommation » | À faire |
| Hub d'équipe | À faire |

## Ce qui fait encore sortir de Jarvis (ou faire à la main)

| Moment | Aujourd'hui | Où | Statut |
|---|---|---|---|
| Travailler à côté des autres applications | Fenêtres natives, barre JARVIS | [electron.md](electron.md) | Fait |
| Ouvrir Odoo, SharePoint, Outlook web connectés | Sites connectés de l'application | [electron.md](electron.md) | Fait |
| Lire ou modifier un Word, un Excel | Aperçu du texte ; la modification passe par « Ouvrir avec l'application » | `static/js/viewer.js` | Partiel |
| Retoucher un fichier écrit par Claude, revenir en arrière | Différences et annulation ; pas d'éditeur | `console/changes.py` | Partiel |
| Dire « on travaille dans le projet Network » | Reconnu dans la barre avant l'envoi (puce « Projet : Network »), panneau du projet ouvert à côté ; en cours de discussion, Claude propose de rattacher sur une carte (outil `projet`) | `static/js/mention.js`, `console/project_nav.py` | Fait |
| Demander « sur quel fichier on a travaillé en dernier » | Claude n'a pas accès à l'activité du projet (discussions, fichiers modifiés) : il fouille le dossier | — | À faire |
| Lier un projet Odoo, ses tâches et sous-tâches | Onglet « Suivi » du panneau Projet (lier, tâches en arbre, Ouvrir dans Odoo), ou demandé à Claude et validé sur une carte | `console/odoo_link.py`, `static/js/odoo.js` | Fait |
| Voir les tâches Office 365, les brouillons et les relances d'un projet | Onglet « Suivi » : lecture seule par le connecteur du compte. Un brouillon n'est jamais envoyé | `console/office_link.py`, `static/js/office.js` | En cours |
| Le même fil Office 365 pour le compte, hors d'un projet | Pas encore de vue | — | À faire |
| Synchroniser un dossier du projet avec OneDrive / SharePoint | Hors de JARVIS (client OneDrive) ; seul « en ligne seulement » est reconnu par l'index | `console/documents.py` | À faire |
| Montrer à Claude un passage d'un PDF, d'une page, d'un panneau | Le texte sélectionné dans un PDF, une page web ou un mail HTML (cadres), dans les panneaux, Ctrl+K, les widgets et les fenêtres de dialogue n'est pas lu | `static/js/regard.js` | Partiel |
| S'y retrouver dans des centaines de discussions | Archivage à la main ou après N jours d'inactivité ; les archivées restent dans l'historique et Ctrl+K | `Engine.archive_tasks`, `Engine.auto_archive` | Fait |
| Reprendre une discussion après 2 ou 3 heures | Le cache a expiré : tout le contexte est réécrit en cache, sauf « Garder au chaud » activé à la main | `Engine.keep_warm` | Partiel |
| Être loin du PC | Le serveur n'écoute que `127.0.0.1` ; une validation demandée par une routine attend le retour de l'utilisateur | `console/__main__.py`, `console/presence.py` | À faire |
| Réagir à un événement (mail reçu, fichier déposé) | Les routines ne partent qu'à heure fixe ou à intervalle | `console/routines.py` | À faire |
| Retrouver ce qu'une routine a produit | Boîte de réception | [boite-de-reception.md](boite-de-reception.md) | Fait |
| Lancer une commande générale du compte d'un clic | Actions du compte en boutons | [boite-de-reception.md](boite-de-reception.md) | Fait |

## Axes d'amélioration

### 0. La conversation pilote JARVIS (direction actuelle)

Ce qu'on demande à JARVIS en langage courant, il le fait dans l'interface : ouvrir le projet, y
ranger la discussion, retrouver le dernier fichier, relier Odoo ou OneDrive. Conception détaillée
dans [ihm.md](ihm.md#claude-et-jarvis-ne-font-quun-à-venir). Principe constant : la console fait le
geste d'interface tout de suite quand il est sans risque (ouvrir une fenêtre), et passe par une carte
d'approbation dès que quelque chose est enregistré (rattacher, lier, synchroniser).

- **Le projet suit la conversation.** *Fait.* « On va travailler dans le projet Network » tapé dans
  la barre : la console reconnaît le nom d'un projet avant l'envoi (sans modèle, sans token,
  `static/js/mention.js`), propose le projet dans la barre, lance la discussion dans son dossier et
  ouvre le panneau du projet à côté de la fenêtre de session. Quand la mention est implicite ou
  arrive en cours de discussion, Claude passe par l'outil `projet` (`console/project_nav.py` : ouvrir,
  rattacher) ; rattacher demande un clic et se fait à la fin du tour (une discussion en cours ne se
  déplace pas).
- **La mémoire de travail du projet.** *À faire.* « Sur quel fichier on a travaillé en dernier ? » :
  l'outil `projet` (activité) répond à partir des données de la console, sans fouiller le dossier :
  dernières discussions du projet, fichiers modifiés par Claude (avec la discussion), fichiers ouverts
  en aperçu, fichiers récemment modifiés sur le disque. Claude peut ensuite l'`afficher`.
- **Odoo relié au projet.** *Fait.* « Lie le projet Odoo Network V2 à ce projet » : Claude cherche
  dans Odoo (lecture), propose le lien sur une carte (`proposer`, `quoi: "odoo"`) ; le clic
  l'enregistre dans le projet (`Project.odoo` : identifiants, plus seulement des noms). Le lien se fait
  aussi depuis l'onglet « Suivi » du panneau Projet (recherche, cases à cocher). L'onglet montre les
  tâches et sous-tâches des projets liés en arbre (étape, échéance, responsables, priorité, en retard),
  avec « Ouvrir dans Odoo » et le clic droit de Regard. La console lit Odoo par une courte discussion
  sans fenêtre (Haiku, preset lecture, sans session gardée, supprimée après lecture). Le brief et les
  discussions du projet connaissent les projets liés. Créer ou clôturer une tâche reste une demande à
  Claude sous les validations du preset.
- **Le quotidien Office 365.** *En cours.* Jarvis reste l'assistant du quotidien : dans un projet
  comme pour le compte, il montre ce qu'il y a à faire dans Office 365. Trois choses, au même endroit
  que les tâches Odoo : les tâches, les mails laissés en brouillon, les relances. Un mail ne part
  jamais de lui-même, il reste un brouillon jusqu'au clic. L'onglet du panneau Projet s'appelle
  **Suivi** (il s'appelait « Odoo ») : Odoo y est déjà, la lecture Office 365 du projet commence, sur
  le même principe (courte discussion en lecture seule du connecteur du compte, rien n'est envoyé).
  Le même fil au niveau du compte, hors projet, reste à faire. Créer ou clôturer une tâche sans
  repasser par Claude vient ensuite.
- **Un dossier du projet synchronisé avec OneDrive / SharePoint.** *À faire.* La synchronisation
  reste celle du client OneDrive de Windows (fiable, hors ligne, conflits gérés) : JARVIS ne recopie
  jamais de fichiers lui-même. Il montre si un dossier du projet est synchronisé, et sur demande
  (« synchronise le dossier Devis avec le Drive ») propose sur une carte : déplacer le dossier dans
  OneDrive, ou faire synchroniser une bibliothèque SharePoint par le client OneDrive puis l'ajouter au
  projet.
- **Claude développe des modules pour JARVIS.** *Fait.* « Fais-moi un outil de suivi des devis » :
  Claude écrit un module (outil, application, panneau) dans `addons/<id>/` (manifeste `addon.json` +
  page web), le vérifie et l'ouvre par l'outil `modules` (lister, verifier, ouvrir, donnees). Le
  format est décrit dans `addons/README.md`, réécrit au démarrage depuis `console/addons_readme.md` ;
  le dossier est ouvert à toutes les discussions. Un module s'ouvre dans sa fenêtre (bouton
  **Modules**, Ctrl+K) sur l'origine d'aperçu, dans un iframe isolé (origine opaque, sans réseau, ses
  seuls fichiers) ; il parle à la console par `window.jarvis` : stockage propre (1 Mo), texte mis dans
  la barre de commande, notification, lien web confirmé, et `demander` (question à Claude en courte
  discussion sans fenêtre, seulement après un clic dans le module et si l'utilisateur a autorisé la
  permission `claude` du manifeste). Sans demande, Claude peut aussi **proposer** un module utile
  (`modules` → `proposer`) : une carte dans la discussion, au plus une par discussion ;
  le clic vaut demande et il l'écrit dans le même tour. Le dossier `addons/` n'est pas
  versionné (`.gitignore`).

### 1. Donner plus de moyens à Claude dans la console

Le serveur MCP intégré `jarvis` offre `afficher`, `afficher_resultat`, `presenter`, `proposer` et
`chercher_documents`. Pistes, toutes détaillées dans [ihm.md](ihm.md) :

- **« Ce que je regarde »** : l'aperçu ou l'affichage au premier plan et le texte sélectionné partent
  avec le message. *Fait, voir plus bas.* **Regard partout** *(partiel)* : le texte sélectionné dans
  un PDF, une page web, un mail HTML, un panneau (Projet, Notes, Historique, boîte), un résultat de
  Ctrl+K, un widget ou une fenêtre de dialogue doit partir aussi, avec son origine ; en mode intégré,
  la puce se montre dans la barre JARVIS quelle que soit la fenêtre où l'on a sélectionné
  ([ihm.md](ihm.md#regard-partout)).
- **Bloc `formulaire`** de `presenter` : Claude pré-remplit un devis ou un mail, l'utilisateur
  corrige et valide. *Fait ([ihm.md](ihm.md)).*
- **Outil `projet`** : ouvrir, rattacher, activité (axe 0). *À faire.* Un seul outil polyvalent
  plutôt que trois.
- **`lancer_discussion`** (coordination entre discussions) : une discussion en ouvre d'autres, dans
  leurs propres fenêtres et après validation, et récupère leur résultat. C'est ce qui multiplie le
  plus la puissance : Jarvis devient un chef d'orchestre visible, au lieu de sous-agents invisibles.
  *À faire.*
- **Outils-scripts** : des fonctions déterministes propres au projet. *À faire (conception dans
  [ihm.md](ihm.md)).*
- **`proposer`** étendu aux consignes (`CLAUDE.md`) et à la mémoire du projet, montré en différences.
  *Partiel : les consignes du brief se proposent déjà ; `CLAUDE.md` et la mémoire, à faire.* Il
  s'étend aussi aux liens du projet (Odoo, dossier OneDrive) de l'axe 0.

Règle constante : peu d'outils polyvalents, chacun pèse dans le contexte de toutes les discussions.

### 2. Des déclencheurs, pas seulement des horaires

*À faire.*

- **Dossier surveillé** : un PDF arrive dans `Factures/` → l'action `/facture` du projet.
- **Enchaînement** : la fin d'une tâche en lance une autre.
- **Nouveaux mails** : une routine qui ne traite que ce qui est arrivé depuis sa dernière exécution.

Une tâche déclenchée par un contenu extérieur reste sur un preset restreint (« Brouillons » par
exemple) : c'est la porte d'entrée principale des injections.

### 3. Une boîte de réception unique

Réunir ce qui est aujourd'hui éparpillé entre fenêtres, historique et notes : validations et
questions en attente de toutes les discussions, résultats de routines non lus, propositions, rappels.
Avec un « brief du matin » (mails, devis Odoo en attente, agenda) en affichage `presenter`. Avec
l'application de bureau, elle prend la forme de compteurs sur l'icône de la zone de notification, d'un
panneau et d'une place en tête du menu JARVIS. Au passage, les commandes du compte (`~/.claude/commands`) en boutons
partout. *Fait : [boite-de-reception.md](boite-de-reception.md).*

### 4. Les fichiers sans quitter Jarvis

- **Différences et annulation des modifications de Claude**. *Fait, voir plus bas.*
- Éditeur intégré pour texte, Markdown et CSV. *Fait : dans l'aperçu, Modifier ouvre le fichier
  (texte, Markdown, CSV, JSON, YAML…), Ctrl+S l'enregistre. Un fichier binaire, trop gros ou protégé
  ne s'écrit pas ; un changement sur le disque pendant l'édition est signalé.*
- Aperçu de Word, Excel, PowerPoint, OpenDocument et des mails `.eml`. *Fait : le texte seul
  (un tableau par feuille pour Excel), lu par la console sans dépendance, avec « Ouvrir avec
  l'application » pour la mise en forme.*
- Skills docx, xlsx et pdf installées dans chaque profil, pour que Claude produise de vrais fichiers
  Office. *À faire.*

### 5. JARVIS intégré au bureau, et le web connecté (Electron)

Détail dans [electron.md](electron.md). *Fait : `build-app.bat` installe l'application.*

- **Les fenêtres de JARVIS deviennent de vraies fenêtres de l'OS**, sans le fond de la console :
  elles s'intercalent avec les autres applications, s'ancrent, changent d'écran, apparaissent dans
  Alt+Tab. Une barre flottante (la barre de commande) s'appelle par un raccourci global ; une icône
  de la zone de notification montre les tâches et les validations en attente. Le style de JARVIS est
  conservé : seuls les bords des fenêtres (ombre, coins, boutons) deviennent ceux de Windows. *Fait,
  widgets du bureau compris (fenêtres transparentes, fermées par leur croix).*
- **Sites connectés** : une fenêtre par site (Odoo, SharePoint, Outlook web) avec sa propre session.
  *Fait.*
- **Notifications** avec Approuver et Refuser. *Fait.*
- En attendant : l'option `--chrome` existe par profil (`Profile.chrome`) ; un interrupteur par
  discussion laisserait Claude agir dans Odoo avec la session Chrome de l'utilisateur. *À faire
  (l'interrupteur par discussion).*

### 6. Jarvis loin du bureau

*À faire.*

- Validations et rappels sur le téléphone, avec Approuver et Refuser, par le serveur Debian et le VPN
  prévus dans [equipe.md](equipe.md), jamais par Internet.
- Dictée vocale avec une transcription locale (Whisper), plutôt que la reconnaissance vocale de
  Chrome qui envoie l'audio à Google.

### 7. Mémoire et connaissance

- Index des documents (PDF, Word, mails) sur le modèle de CodeGraph pour le code : l'activité est
  surtout documentaire. *Fait, voir « Index des documents » plus bas.*
- À suivre : les mails d'Outlook (aujourd'hui seulement les `.eml` d'un dossier), la recherche par le
  sens (embeddings locaux) en plus des mots, les pièces jointes des mails. *À faire.*
- L'activité du projet (dernières discussions, derniers fichiers) donnée à Claude par l'outil
  `projet` : axe 0. *À faire.*
- Propositions de notes de projet (voir l'axe 1). *À faire.*

### 8. Garder la consommation en main

- Vue « Consommation » ([ihm.md](ihm.md)). *À faire.*
- Choix automatique du modèle et de l'effort selon la demande (Haiku pour une question simple).
  *À faire.*
- **Reprise d'une discussion inactive** : « Garder au chaud » *(fait, à la main)* ; garder au chaud
  ou compacter automatiquement avant l'expiration du cache, selon la taille du contexte *(à faire,
  voir ci-dessous)*.

### 9. Des discussions qui ne s'entassent pas

*Fait, sauf la purge (à trancher). Détail dans [ihm.md](ihm.md#archivage-des-discussions).*

- **Archiver** une discussion (menu ⋯ de la fenêtre, historique avec sélection multiple, onglet
  Discussions du projet, « Archiver les terminées ») : elle quitte les listes courantes, l'écran
  d'accueil, la boîte et la barre des tâches ; sa fenêtre se ferme. Elle reste dans le filtre
  « Archivées » de l'historique et de la liste du projet, et dans Ctrl+K (groupe « Archivées »).
  La rouvrir ou y écrire la désarchive. *Fait.*
- **Archivage automatique** : Configuration → Historique, « Archiver les discussions inactives depuis
  N jours » (0 = jamais, par défaut), au démarrage puis toutes les heures. Jamais une discussion en
  cours, en attente d'une validation ou d'un clic, gardée au chaud, épinglée, programmée ou affichée
  par un widget. *Fait.*
- **À trancher** : la purge (`history.retention_days`, 90 jours) supprime toujours les discussions
  terminées, archivées comprises. Avec l'archivage, elle pourrait ne plus toucher que les archives, et
  seulement si l'utilisateur l'a demandé (« Supprimer les archives de plus de N jours », désactivé par
  défaut).

## Reprise d'une discussion inactive : faut-il compacter avant l'expiration du cache ?

Le cache de prompt d'une session dure environ une heure sur les abonnements Claude. Au-delà, le
message suivant réécrit tout le contexte en cache. Repères de prix (en multiples du prix d'un token
d'entrée) : écrire en cache (durée d'une heure) ≈ 2, relire le cache ≈ 0,1, un token produit ≈ 5.

Exemple : une discussion de 150 k tokens de contexte, reprise après 3 h, puis 10 échanges ; un
compactage produit un résumé d'environ 12 k tokens, et le contexte compacté pèse environ 35 k
(prompt système, outils, résumé).

| Stratégie | Calcul approché | Coût (k unités) | Perte d'information |
|---|---|---|---|
| Ne rien faire | réécriture 150 k × 2 = 300 ; 10 échanges × 150 k × 0,1 = 150 | **≈ 450** | non |
| Garder au chaud 3 h | 4 maintiens × 15 = 60 ; reprise 15 ; 10 échanges 150 | **≈ 225** | non |
| Compacter à 55 min | lecture 15 + résumé 12 k × 5 = 60 ; réécriture 35 k × 2 = 70 ; 10 échanges × 3,5 = 35 | **≈ 180** | oui (résumé) |
| Compacter, mais pas de reprise | 15 + 60 | **≈ 75 perdus** | — |

Ce qu'il faut en retenir :

- **Oui, compacter juste avant l'expiration fait économiser des tokens, mais seulement si la
  discussion est reprise** et si son contexte est gros. Le gain vient surtout des échanges suivants,
  qui relisent un contexte quatre fois plus petit. Compacter après l'expiration ne sert à rien pour la
  reprise : il faut relire tout le contexte sans cache pour le résumer.
- **Pour une pause de 2 à 3 heures, garder au chaud coûte presque autant et ne perd rien.** Le
  compactage est un résumé : des détails (chemins, chiffres, décisions) peuvent disparaître.
- **Pour une pause d'une nuit ou plus**, garder au chaud n'a plus de sens (12 maintiens et plus) ;
  compacter avant l'expiration est la bonne option pour une discussion qu'on sait vouloir reprendre.
- Sur un abonnement, l'économie porte sur les quotas plutôt que sur une facture. Elle se mesurera
  avec la vue « Consommation ».

Décision proposée *(à faire)* : une option par compte, « Reprise des discussions inactives », avec
trois réglages : rien (comme aujourd'hui), garder au chaud jusqu'à N heures, ou garder au chaud puis
compacter juste avant la dernière expiration. Elle ne concerne que les discussions dont le contexte
dépasse un seuil (60 k tokens par défaut), ni archivées ni terminées par l'utilisateur, et la fenêtre
de la discussion dit ce qui a été fait (« Contexte compacté à 18:55 pour une reprise moins chère »).
Un bouton « Compacter et mettre en pause » couvre le cas où l'on sait qu'on reprendra demain.

## Ordre proposé

1. **« Ce que je regarde », différences et annulation des fichiers**. *Fait.*
2. **Electron : JARVIS intégré au bureau** ([electron.md](electron.md)). *Fait : application de
   bureau (`build-app.bat`), fenêtres natives, barre JARVIS, sites connectés, notifications avec
   Approuver et Refuser, widgets du bureau ; validée sous Windows.*
3. **Boîte de réception, actions du compte en boutons**. *Fait
   ([boite-de-reception.md](boite-de-reception.md)) : boîte (à faire, à lire, rappels, Approuver depuis
   la boîte, Reprendre une validation expirée), routines « seulement les erreurs » et « en tête », brief
   du matin par compte, actions du compte en boutons, « Pendant ton absence » ; à essayer sous Windows.*
4. **Aperçu Office, index des documents**. *Fait (voir plus bas).*
5. **Le quotidien Office 365**, dans un projet puis pour le compte ([ihm.md](ihm.md)). *En cours.*
   Le brief de projet a déjà ses cartes et son fichier de suivi. L'onglet du projet s'appelle
   **Suivi** : les tâches Odoo des projets liés y sont, et la lecture Office 365 commence (tâches,
   brouillons, relances ; un mail reste un brouillon). Restent la vue du compte hors projet, et la
   synchro qui crée ou clôt une tâche dans Office 365 ou Odoo sans repasser par Claude.
6. **La conversation pilote JARVIS (axe 0)**, dans cet ordre : *en cours.*
   1. le projet suit la conversation (reconnaissance dans la barre, ouverture de la fenêtre du
      projet, outil `projet` : ouvrir et rattacher) : *fait* ;
   2. l'activité du projet (outil `projet` : activité) ;
   3. Regard partout ;
   4. Odoo relié au projet (lien proposé, onglet « Suivi ») : *fait* ;
   5. dossier synchronisé avec OneDrive / SharePoint.
7. **Archivage des discussions** et archivage automatique. *Fait* (reste à trancher : la purge des
   archives).
8. **Reprise des discussions inactives** (garder au chaud ou compacter automatiquement) avec la vue
   « Consommation » pour en mesurer l'effet. *À faire.*
9. Déclencheurs : dossier surveillé, enchaînement. *À faire.* Côté serveur : peut avancer en
   parallèle.
10. Validations depuis le téléphone : les notifications d'Electron couvrent le PC ; sans le téléphone,
    routines et déclencheurs restent bloqués dès que l'utilisateur s'en éloigne. *À faire.*
11. `lancer_discussion`, puis les outils-scripts. *À faire.*
12. Hub d'équipe ([equipe.md](equipe.md)). *À faire.*

## Ce qui est en place (étape 1)

### « Ce que je regarde »

Quand l'utilisateur sélectionne du texte (un aperçu, un affichage, une réponse de Claude) ou met un
aperçu ou un affichage au premier plan, une puce « Regard » apparaît dans la barre de saisie qu'il
utilise (barre du bas ou suite d'une fenêtre) : elle dit ce qui partira avec le message, et une croix
la retire. À l'envoi, la console ajoute au message (jamais au prompt système, pour garder le cache) :

- le fichier ou la page regardés (chemin, adresse), ou l'affichage (titre, identifiant, discussion) ;
- le texte sélectionné, borné, présenté comme une donnée affichée à l'écran et non comme une
  consigne (il peut venir d'un mail ou d'une page).

Un affichage ou un résultat d'outil d'une autre discussion du même compte part avec son contenu (la
discussion qui reçoit le message ne l'a jamais vu) ; d'un compte à l'autre, seuls le titre et la
sélection passent. Le regard ne donne aucun droit : lire le fichier passe par les autorisations de la
discussion, comme d'habitude. Il est consommé à l'envoi : la puce revient dès que l'utilisateur
sélectionne autre chose ou revient sur un aperçu. Désactivable dans Configuration → Interface. Code :
`static/js/regard.js`, `console/regard.py`.

Limites actuelles (voir « Regard partout ») : le texte sélectionné dans un cadre (PDF, page web, mail
HTML) ne se lit pas, seul le fichier ou la page sont nommés ; une sélection dans un panneau, Ctrl+K, un
widget ou une fenêtre de dialogue n'est pas reprise.

### Différences et annulation des modifications de fichiers

Avant chaque écriture de Claude par les outils de fichiers (`Write`, `Edit`, `MultiEdit`,
`NotebookEdit`, sous-agents compris), le hook `PreToolUse` de la console garde une copie du fichier
dans les données de la console (inaccessibles aux agents). Au résultat de l'outil, la modification est
enregistrée si le fichier a bien changé. Dans la fenêtre de la discussion :

- chaque action d'écriture a un bouton « +n −m » qui ouvre les différences (lignes retirées et
  ajoutées) ;
- le menu ⋯ → « Fichiers modifiés » liste les fichiers touchés par la discussion, avec « Tout
  annuler » pour un fichier ;
- **Annuler** remet le fichier dans son état d'avant (ou le supprime si Claude l'avait créé), après
  confirmation, et seulement si le fichier n'a pas changé depuis : sinon la console le dit et propose
  de forcer. L'annulation est elle-même annulable (« Rétablir »), ce qu'elle a écrasé étant gardé ;
- Claude apprend avec le message suivant quels fichiers l'utilisateur a remis en l'état.

Limites : une commande (`Bash`, PowerShell) qui écrit un fichier n'est pas suivie ; les fichiers de
plus de 2 Mo non plus. Les copies suivent la tâche : elles partent quand elle est supprimée ou purgée.
Code : `console/changes.py`, `Engine.file_changes`.

## Index des documents (étape 7)

Configuration → Documents. La console lit le texte des dossiers des projets et des dossiers ajoutés
(PDF, Word, Excel, PowerPoint, OpenDocument, mails `.eml`, textes, pages HTML) et le range dans un
index plein texte sur le poste (`data/documents.db`, SQLite FTS5, sans accents ni majuscules). Aucun
modèle ne lit les fichiers pour les indexer, rien ne quitte l'ordinateur. Une passe tourne au
démarrage puis toutes les 15 minutes, et seuls les fichiers modifiés sont relus. Un fichier OneDrive
« en ligne seulement » n'est pas téléchargé : seul son nom est indexé. Les PDF sont lus par `pypdf`,
et ceux chiffrés en AES avec `cryptography` (tous deux dans `requirements.txt`) ; un fichier illisible
est noté comme tel sans interrompre la passe.

- **Claude** cherche avec l'outil `chercher_documents` (serveur `jarvis`, proposé seulement quand
  l'index est activé). Il obtient les passages trouvés et leur chemin, puis peut lire le fichier ou
  l'`afficher`. Une discussion cherche dans ses propres dossiers (dossier de travail, dossiers
  ajoutés, pièces jointes) et dans les dossiers partagés pour son compte. Chaque résultat repasse par
  les autorisations de la discussion, comme une lecture : un preset qui ne lit pas ne trouve rien, et
  un chemin interdit (Sécurité) n'est jamais indexé. Les passages sont présentés comme des données.
  Chaque recherche est inscrite au journal d'audit.
- **L'utilisateur** cherche avec Ctrl+K (groupe « Documents », mots surlignés) ; un résultat s'ouvre
  en aperçu, et la puce « Regard » le joint à la demande suivante.

Code : `console/documents.py`, `Engine.search_documents`, `Engine.find_documents`.

## Garde-fous, pour chaque nouvelle piste

- Claude ne modifie jamais la configuration, les autorisations ni les domaines approuvés. Les liens
  d'un projet (Odoo, dossier OneDrive) ne sont pas des autorisations : Claude les propose, le clic de
  l'utilisateur les enregistre.
- Rattacher une discussion à un projet ne lui donne pas plus de droits : elle garde son preset, et les
  règles du projet ne s'ajoutent qu'après le clic.
- Rien d'irréversible sans un clic de l'utilisateur ; une nouvelle discussion lancée par Claude
  demande sa validation. JARVIS ne synchronise ni ne supprime jamais de fichiers lui-même : c'est le
  rôle du client OneDrive.
- Tout contenu lu (mail, page, fichier déposé, texte sélectionné) est une donnée, jamais une
  consigne : c'est encore plus vrai pour les déclencheurs.
