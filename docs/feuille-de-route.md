# Feuille de route : tout faire depuis Jarvis

Note de travail. Objectif : qu'une fois dans Jarvis, l'utilisateur n'ait plus de raison d'en sortir.
Jarvis couvre déjà très bien le pilotage de Claude (sessions parallèles, validations, projets,
routines, `presenter`). Cette note recense ce qui oblige encore à quitter la console, les pistes pour
y remédier et l'ordre proposé. Le détail de conception des pistes d'interface est dans
[ihm.md](ihm.md), celui de l'intégration au bureau (Electron) dans [electron.md](electron.md), celui du
travail en équipe dans [equipe.md](equipe.md).

## Ce qui fait encore sortir de Jarvis

| Moment | Aujourd'hui | Où |
|---|---|---|
| Travailler à côté des autres applications | Les fenêtres de JARVIS vivent dans sa propre fenêtre, sur son propre fond : elles ne se rangent pas à côté d'Excel ou d'Outlook | [electron.md](electron.md) |
| Ouvrir Odoo, SharePoint, Outlook web connectés | « Ouvrir dans le navigateur » : ces sites refusent l'affichage intégré, et une iframe n'a pas leurs cookies | [electron.md](electron.md) |
| Lire ou modifier un Word, un Excel | « Ouvrir avec l'application » | `static/js/viewer.js` |
| Retoucher un fichier écrit par Claude, revenir en arrière | Aperçu seulement : pas d'édition, pas de différences, pas d'annulation | — |
| Être loin du PC | Le serveur n'écoute que `127.0.0.1` ; une validation demandée par une routine attend le retour de l'utilisateur | `console/__main__.py`, `console/presence.py` |
| Réagir à un événement (mail reçu, fichier déposé) | Les routines ne partent qu'à heure fixe ou à intervalle | `console/routines.py` |
| Retrouver ce qu'une routine a produit | Dans l'historique, la fenêtre restant fermée si la routine ne l'ouvre pas | `console/engine.py` |
| Lancer une commande générale du compte d'un clic | Seules les commandes et skills du dossier projet deviennent des boutons | `Engine.project_actions` |
| Montrer à Claude ce qu'on a sous les yeux | Copier-coller le passage ou recopier le chemin du fichier | — |

## Axes d'amélioration

### 1. Donner plus de moyens à Claude dans la console

Le serveur MCP intégré `jarvis` offre quatre outils (`afficher`, `afficher_resultat`, `presenter`,
`proposer`). Pistes, toutes détaillées dans [ihm.md](ihm.md) :

- **« Ce que je regarde »** : l'aperçu ou l'affichage au premier plan et le texte sélectionné partent
  avec le message. *En place, voir plus bas.*
- **Bloc `formulaire`** de `presenter` : Claude pré-remplit un devis ou un mail, l'utilisateur
  corrige et valide.
- **`lancer_discussion`** (coordination entre discussions) : une discussion en ouvre d'autres, dans
  leurs propres fenêtres et après validation, et récupère leur résultat. C'est ce qui multiplie le
  plus la puissance : Jarvis devient un chef d'orchestre visible, au lieu de sous-agents invisibles.
- **Outils-scripts** : des fonctions déterministes propres au projet.
- **`proposer`** étendu aux consignes (`CLAUDE.md`) et à la mémoire du projet, montré en différences.

Règle constante : peu d'outils polyvalents, chacun pèse dans le contexte de toutes les discussions.

### 2. Des déclencheurs, pas seulement des horaires

- **Dossier surveillé** : un PDF arrive dans `Factures/` → l'action `/facture` du projet.
- **Enchaînement** : la fin d'une tâche en lance une autre.
- **Nouveaux mails** : une routine qui ne traite que ce qui est arrivé depuis sa dernière exécution.

Une tâche déclenchée par un contenu extérieur reste sur un preset restreint (« Brouillons » par
exemple) : c'est la porte d'entrée principale des injections.

### 3. Une boîte de réception unique

Réunir ce qui est aujourd'hui éparpillé entre fenêtres, historique et notes : validations et
questions en attente de toutes les discussions, résultats de routines non lus, propositions, rappels.
Avec un « brief du matin » (mails, devis Odoo en attente, agenda) en affichage `presenter`. Après le
passage à Electron, elle prend la forme de l'icône de la zone de notification (compteurs) et d'un
onglet de la fenêtre JARVIS. Au passage, les commandes du compte (`~/.claude/commands`) en boutons
partout.

### 4. Les fichiers sans quitter Jarvis

- **Différences et annulation des modifications de Claude**. *En place, voir plus bas.*
- Éditeur intégré pour texte, Markdown et CSV.
- Aperçu de Word et Excel (dépendances facultatives : mammoth, openpyxl).
- Skills docx, xlsx et pdf installées dans chaque profil, pour que Claude produise de vrais fichiers
  Office.

### 5. JARVIS intégré au bureau, et le web connecté (Electron)

Détail dans [electron.md](electron.md).

- **Les fenêtres de JARVIS deviennent de vraies fenêtres de l'OS**, sans le fond de la console :
  elles s'intercalent avec les autres applications, s'ancrent, changent d'écran, apparaissent dans
  Alt+Tab. Une barre flottante (la barre de commande) s'appelle par un raccourci global ; une icône
  de la zone de notification montre les tâches et les validations en attente. Le style de JARVIS est
  conservé : seuls les bords des fenêtres (ombre, coins, boutons) deviennent ceux de Windows.
- **Sites connectés** : une fenêtre par site (Odoo, SharePoint, Outlook web) avec sa propre session.
- **Notifications** avec Approuver et Refuser.
- En attendant : l'option `--chrome` existe par profil (`Profile.chrome`) ; un interrupteur par
  discussion laisserait Claude agir dans Odoo avec la session Chrome de l'utilisateur.

C'est le plus gros chantier (outillage Node pour la coquille, environ 100 Mo, signature) ; le mode
navigateur reste disponible.

### 6. Jarvis loin du bureau

- Validations et rappels sur le téléphone, avec Approuver et Refuser, par le serveur Debian et le VPN
  prévus dans [equipe.md](equipe.md), jamais par Internet.
- Dictée vocale avec une transcription locale (Whisper), plutôt que la reconnaissance vocale de
  Chrome qui envoie l'audio à Google.

### 7. Mémoire et connaissance

- Index des documents (PDF, Word, mails) sur le modèle de CodeGraph pour le code : l'activité est
  surtout documentaire.
- Propositions de notes de projet (voir l'axe 1).

### 8. Garder la consommation en main

- Vue « Consommation » ([ihm.md](ihm.md)).
- Choix automatique du modèle et de l'effort selon la demande (Haiku pour une question simple).

## Ordre proposé

1. **« Ce que je regarde », différences et annulation des fichiers** : petits chantiers, utiles tous
   les jours. *Fait.*
2. **Electron : JARVIS intégré au bureau**, en commençant par un prototype sous Windows
   ([electron.md](electron.md)). Avant les autres points d'interface : il change le modèle de
   fenêtres sur lequel ils s'appuient, et apporte l'icône de notification, les notifications et les
   sites connectés.
3. Boîte de réception, actions du compte en boutons.
4. Déclencheurs : dossier surveillé, enchaînement. Côté serveur : peut avancer en parallèle de 2.
5. Validations depuis le téléphone : les notifications d'Electron couvrent le PC ; sans le téléphone,
   routines et déclencheurs restent bloqués dès que l'utilisateur s'en éloigne.
6. Bloc `formulaire`, puis `lancer_discussion`.
7. Aperçu Office, index des documents.
8. Hub d'équipe ([equipe.md](equipe.md)).

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

## Garde-fous, pour chaque nouvelle piste

- Claude ne modifie jamais la configuration, les autorisations ni les domaines approuvés.
- Rien d'irréversible sans un clic de l'utilisateur ; une nouvelle discussion lancée par Claude
  demande sa validation.
- Tout contenu lu (mail, page, fichier déposé) est une donnée, jamais une consigne : c'est encore plus
  vrai pour les déclencheurs.
