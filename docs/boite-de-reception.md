# Boîte de réception

Note de travail : en place (étapes 1 à 6 ci-dessous), à essayer sous Windows (voir « À vérifier sous
Windows »). C'est l'étape 3 de la [feuille de route](feuille-de-route.md).
Objectif : un seul endroit pour tout ce qui attend l'utilisateur, quels que soient la discussion, la
fenêtre, le compte ou le projet ; et les commandes du compte en boutons, comme celles des projets.

## Ce qui est aujourd'hui éparpillé

| Ce qui attend | Où on le voit aujourd'hui | Ce qui manque |
|---|---|---|
| Validations, questions, plans, propositions | Fenêtre de la discussion (rouverte d'office), compteur « à valider », notification avec Approuver et Refuser | Une liste de toutes celles en attente, avec l'heure où elles expirent |
| Validation expirée (`approval_timeout_min`, 30 min par défaut) | Refusée par la console ; une ligne dans la discussion et le journal | Rien ne le signale : une routine de 7 h qui attendait une validation échoue sans qu'on le sache |
| Discussion terminée pendant qu'on regardait ailleurs | Pastille de la tâche réduite (`S.attention` de [static/js/app.js](../static/js/app.js)), notification | La marque vit dans la page : perdue au rechargement, différente d'une page à l'autre, inconnue de l'icône de notification |
| Résultat d'une routine sans fenêtre (`open_window: false`) | Historique, ou les 20 dernières exécutions dans le panneau Routines | Rien ne dit qu'un résultat est arrivé, ni ce qu'il contient |
| Routine non lancée, manquée ou reportée | Panneau Routines, ligne de l'exécution | Idem |
| Affichage `presenter` dont un choix ou un bouton attend un clic | Dans la conversation ou dans sa fenêtre | Claude a fini son tour en attendant : fenêtre fermée, plus rien ne le rappelle |
| Rappel d'une note arrivé à son heure | Popup au-dessus de la barre, badge du bouton Notes | Bien couvert ; à réunir avec le reste |

## Principes

- **Une vue calculée par le serveur, pas une copie.** Les entrées se déduisent des tâches, de leurs
  validations, des routines et des notes. Seules les marques « lu » et « ignoré » s'enregistrent, dans
  la tâche (comme `closed` ou `pinned`) ou dans l'exécution de routine. Toutes les pages ouvertes,
  l'icône de notification et, plus tard, le téléphone (étape 5) voient la même chose.
- **Rien pour Claude.** La boîte n'entre dans aucun prompt et aucun outil ne la lit ni ne la modifie :
  elle ne coûte aucun token, et un contenu piégé ne peut pas y marquer une entrée comme lue pour la
  cacher. Seul l'utilisateur marque lu, ignore ou décide.
- **Pas plus de pouvoir que les fenêtres.** Approuver depuis la boîte passe par la même route qu'un
  clic dans la fenêtre ou dans la notification, journal compris. Ce qui demande de relire un contenu
  long (question, plan, proposition) ouvre sa fenêtre.
- **Les fenêtres restent le lieu du travail.** Une entrée dit quoi et où ; un clic ouvre la
  discussion. La boîte ne recopie pas la conversation et ne remplace pas l'historique : elle ne garde
  que ce qui attend.

## Les entrées

Trois sections, dans cet ordre.

**À faire** : ce qui bloque ou attend une décision.

| Entrée | Source | Boutons |
|---|---|---|
| Validation d'un appel d'outil | `pending` de la tâche (`hook`, `permission`) | Approuver, Refuser, Détail, Ouvrir ; « expire à 10:42 » |
| Question, plan, proposition | `pending` (`question`, `plan`, `proposal`) | Ouvrir |
| Validation expirée | notée par le délai de validation (voir plus bas) | Reprendre, Ouvrir, Ignorer |
| Choix en attente | affichage dont un bloc `choix` ou `actions` n'a pas de réponse, sans message de l'utilisateur depuis | Ouvrir l'affichage, Ignorer |
| En erreur, interrompue | statut `error` ou `interrupted`, non lue | Relancer, Ouvrir, Ignorer |

**À lire** : ce qui est arrivé pendant qu'on regardait ailleurs.

| Entrée | Source | Boutons |
|---|---|---|
| Discussion terminée | statut `done`, fin de tour non lue | Ouvrir, Marquer comme lu ; extrait de la réponse |
| Résultat de routine | idem, tâche `origin: "routine"` ; regroupé par routine (« 3 exécutions ») | Ouvrir, Marquer comme lu ; extrait |
| Routine non lancée, manquée ou reportée | `runs` de la routine (sans tâche, ou programmée plus tard) | Ouvrir la routine, Lancer maintenant, Marquer comme lu |

**Rappels** : les rappels des notes arrivés à leur heure (`remind_at` passé, `reminded` faux), avec
Ouvrir, Plus tard… et Vu, comme la popup actuelle.

Une tâche annulée par l'utilisateur n'y entre jamais : c'est lui qui l'a arrêtée. Une discussion qui
continue (message de suite) redevient non lue à chaque fin de tour qu'on n'a pas regardée.

### Approuver depuis la boîte

Décidé : une validation d'appel d'outil s'approuve ou se refuse sur place, et Ouvrir reste à côté pour
passer par la fenêtre.

- La ligne montre l'outil, sa cible et la raison donnée par la politique. **Détail** déplie ce que
  montre la carte de la fenêtre : les paramètres complets de l'appel (le contenu à écrire, la
  commande, les valeurs envoyées à Odoo).
- **Refuser** ouvre un champ pour un message à Claude, facultatif, comme dans la fenêtre.
- « Toujours pour ce projet » (mémoriser une règle) reste dans la fenêtre : c'est un réglage, qui se
  relit avant d'être accepté.
- Questions, plans et propositions s'ouvrent dans leur fenêtre : on y répond ou on les relit en entier.

### Validation expirée : Reprendre

Passé le délai, le chien de garde du moteur (`_watchdog` dans [console/engine.py](../console/engine.py))
refuse à la place de l'utilisateur ; Claude continue sans l'action, ou s'arrête. C'est le cas d'une
routine de 7 h dont la validation attendait un utilisateur pas encore arrivé.

- Le chien de garde note l'expiration dans la tâche (`expired` : validation, outil, cible, heure). On ne
  peut pas la déduire du refus lui-même : `by: "console"` sert aussi quand une tâche est annulée,
  arrêtée ou interrompue par l'arrêt du serveur (`_release_approvals`).
- **Reprendre** envoie à la session un message de suite rédigé par la console : « L'utilisateur est
  là : refais l'action refusée faute de validation (outil, cible), si elle est toujours utile. » La
  nouvelle demande de validation arrive aussitôt, cette fois devant l'utilisateur, et la session garde
  son contexte : rien n'est refait depuis le début. Si la session n'a pas démarré, Reprendre devient
  Relancer.
- Pourquoi ne pas simplement attendre plus longtemps : une tâche à valider garde sa place parmi les
  tâches simultanées (3 par défaut) ; attendre des heures bloquerait la file. On garde le délai actuel
  et Reprendre ; une attente longue aura un sens avec les validations depuis le téléphone (étape 5).

### Lu, non lu

- Une tâche porte `read_at`. Elle est non lue quand elle a fini un tour (`ended`) après `read_at`.
- La page marque lu quand l'utilisateur regarde la discussion : sa fenêtre prend le focus, ou le tour
  se termine pendant qu'elle a le focus et n'est pas réduite (la même règle `looking` que pour les
  notifications). Ouvrir l'entrée, ou la notification d'une tâche terminée, marque aussi lu.
- La première fois qu'une page demande la boîte, tout ce qui est déjà fini compte comme lu
  (`inbox_since` dans la table `kv`) : la boîte ne s'ouvre pas sur tout l'historique. D'ici là, seul
  ce qui est en cours compte (validations en attente, rappels arrivés).
- **Tout marquer comme lu** par section ; **Ignorer** sur une entrée. Une validation en attente ne
  s'ignore pas : on décide.
- `S.attention` (pastilles des tâches réduites) laisse la place à ces marques : même sens, mais partagé
  entre les pages et gardé au rechargement.
- Les marques suivent la tâche et partent avec elle à la purge de l'historique.

## Serveur

- **[console/inbox.py](../console/inbox.py)** : une fonction pure `entries(tasks, routines, notes, …)`
  qui rend la liste triée, testable sans moteur. Identifiants stables : `valider:<tâche>:<validation>`,
  `expiree:<tâche>:<validation>`, `affichage:<tâche>:<clé>`, `fin:<tâche>`, `routine:<routine>` (ses
  résultats regroupés, avec `count` et `task_ids`), `execution:<routine>:<horodatage>`,
  `rappel:<note>`. Les sortes (`kind`) : `hook`, `permission`, `question`, `plan`, `proposal`,
  `expired`, `choice`, `error`, `interrupted`, `done`, `routine`, `run`, `reminder`.
- Forme d'une entrée :

```json
{"id": "valider:3fa2c1d0:9b1e", "section": "todo", "kind": "permission",
 "ts": 1759561200.0, "expires": 1759563000.0,
 "task_id": "3fa2c1d0", "title": "Relance des devis", "profile": "work", "color": "#ffb347",
 "folder": "C:/Users/…/Ventes", "routine": {"id": "a1b2c3d4", "name": "Relances du matin"},
 "summary": "Bash · npm install", "excerpt": "", "actions": ["approve", "deny", "open"]}
```

- **Moteur** ([console/engine.py](../console/engine.py)) :
  - `_watchdog` refuse une validation trop longue avec `timed_out` ; l'attente (`_park`) la note alors
    dans la tâche (`expired`). Une validation libérée par une annulation ou un arrêt n'y entre pas ;
  - `present` et `display_answer` tiennent à jour les affichages en attente de la tâche
    (`waiting_displays` : clé, titre) ; un message de l'utilisateur (`followup`) les vide ;
  - les exécutions automatiques d'une routine naissent non lues (`read: false`) ; celles qui restent
    non lancées, manquées ou reportées sont des entrées. Une exécution manuelle a eu sa réponse au clic ;
  - `state()` ajoute les compteurs `inbox: {todo, read, reminder}`. `state` part déjà à chaque
    transition (`_event`) : la barre, le titre de la page et l'icône de notification les reçoivent sans
    rien de plus ;
  - un événement `inbox` (la liste entière et les compteurs, regroupés à 300 ms près) part quand une
    entrée change : statut, validation, affichage, message, routine, note, marque ; et toutes les 30 s
    pour un rappel qui arrive à son heure. Rien ne part si la liste n'a pas changé.
- **API** :
  - `GET /api/inbox` : les entrées et les compteurs ;
  - `POST /api/inbox/read` `{ids}`, `{section}` ou `{tasks}` (les discussions que l'utilisateur vient
    de regarder) : marquer lu ;
  - `POST /api/inbox/dismiss` `{ids}` : ignorer (enlève aussi une validation expirée, un affichage en
    attente, un rappel) ;
  - `POST /api/tasks/{tid}/resume-expired` `{aid}` : Reprendre ;
  - Approuver et Refuser : la route existante `/api/tasks/{tid}/approvals/{aid}`, avec `via: "boite"`
    pour le journal (« par : utilisateur (boîte de réception) »).
- Calcul en mémoire sur les tâches chargées (2 000 au plus), à chaque changement et regroupé : aucune
  requête à la base.

## Interface

### Le panneau

Un panneau comme Historique ou Notes : un tiroir dans le navigateur et en *Une fenêtre JARVIS*, une
fenêtre de l'OS (fenêtre cadre de [static/js/wm.js](../static/js/wm.js)) en *Intégré au bureau*,
position et taille gardées.

- En haut, **Aujourd'hui**, sans Claude : rappels à venir dans la journée, routines prévues, limites
  des comptes si l'une dépasse 70 %. Puis le brief du jour de chaque compte qui l'a activé (voir plus
  bas).
- Les trois sections, chacune avec son compteur ; filtres par compte et par projet ; « Tout marquer
  comme lu ».
- Chaque ligne : liseré compte × projet (`paint`), titre de la discussion, ce qui attend (outil et
  cible, question, titre de l'affichage), âge, échéance d'une validation, boutons (voir « Approuver
  depuis la boîte »).
- L'extrait d'une réponse est du texte brut, borné à 300 caractères, sans liens ni images : il peut
  venir d'un mail ou d'une page.
- Les validations d'une même discussion se regroupent, comme les exécutions d'une même routine.
- Au clavier : flèches pour se déplacer, Entrée pour ouvrir. Pas de touche pour approuver : un clic
  sur le bouton, comme ailleurs.

### Où on la trouve

- **Barre du haut** (navigateur, *Une fenêtre JARVIS*) : un bouton Boîte de réception avec un badge (à
  faire + à lire) ; un clic sur le compteur « à valider » l'ouvre aussi.
- **Menu JARVIS** (*Intégré au bureau*, [static/js/bar.js](../static/js/bar.js)) : en tête du menu,
  les trois entrées les plus urgentes et « Tout voir (n) » ; l'emblème de la barre porte le nombre
  d'entrées à faire. Ctrl+Alt+J puis l'emblème suffit pour faire le tour.
- **Icône de la zone de notification** (`updateTray` de [shell/main.js](../shell/main.js)) : info-bulle
  et menu « 2 à valider · 3 à lire », avec « Boîte de réception ». La pastille de la barre des tâches
  reste réservée aux validations : ce qui bloque.
- **Ctrl+K** : « Boîte de réception ».
- **Au retour** : après un verrouillage de la session ou une mise en veille (`powerMonitor`
  d'Electron ; dans le navigateur, une page restée cachée plus de 15 min), si des entrées sont arrivées
  entre-temps, une ligne au-dessus de la barre : « Pendant ton absence : 1 validation expirée,
  2 résultats », avec Ouvrir. Une seule fois, sans son.

### Notifications

Rien ne change pour les validations (notification avec Approuver et Refuser) ni pour une discussion
terminée qu'on ne regardait pas. Ouvrir une notification marque l'entrée lue. Une routine réglée sur
« seulement les erreurs » ne notifie pas ses réussites (voir Routines).

## Routines

- Nouveau réglage **Dans la boîte de réception** : *chaque résultat* (par défaut) ou *seulement les
  erreurs*. Une routine qui tourne toutes les 15 min n'inonde pas la boîte : une réussite y est
  marquée lue d'office. Validations, questions et erreurs y arrivent toujours.
- Une routine sans fenêtre (`open_window: false`) n'est plus perdue : son résultat attend dans la
  boîte, avec son extrait.
- Les exécutions non lancées, manquées ou reportées deviennent des entrées « à lire » qui disent
  pourquoi : preset désactivé, console arrêtée à l'heure prévue, limite du compte.
- Une routine peut être **en tête de la boîte** : son dernier résultat (son affichage, s'il en a un)
  s'y montre en haut jusqu'à l'exécution suivante. C'est le principe du brief du matin.
- Champs ajoutés à `Routine` ([console/routines.py](../console/routines.py)) : `inbox`
  (`"always"` ou `"errors"`), `headline` (booléen) et `brief` (le compte d'un brief du matin, géré par
  la console).

## Brief du matin

Un brief **par compte**, quel que soit le nombre de comptes, **désactivé par défaut** : l'utilisateur
l'active compte par compte. Ce n'est pas une consigne à écrire : un réglage du compte, dont la console
tire la demande envoyée à Claude.

### Réglages

Configuration → Profils → *compte* → **Brief du matin** :

- **Activer** (non par défaut), **heure** et **jours** (en semaine à 7:45 au départ).
- **Sources** : mails, devis Odoo, agenda, chacune à cocher. Claude les lit avec les serveurs MCP du
  compte (Office 365 ou Gmail pour les mails et l'agenda, Odoo pour les devis) ; une source
  indisponible est signalée en une ligne dans le brief.
- **Mails importants** : des critères, pas une liste figée, parce qu'ils suivent les dossiers en
  cours :
  - au niveau du compte, ce qui compte toujours : expéditeurs ou domaines, mots de l'objet, dossiers
    de la boîte mail, et une consigne libre (« les demandes de devis, les relances de paiement ») ;
  - au niveau de chaque projet, ce qui ne compte que pendant le dossier : ses correspondants, ses
    mots-clés, sa consigne. Ils se règlent avec le projet (« Mails à suivre », dans ses réglages) et
    comptent tant que la case **Suivre dans le brief** est cochée. Un dossier qui se termine : on la
    décoche, les critères restent pour plus tard. Ceux d'un projet sans compte attitré valent pour les
    briefs de tous les comptes.
- **Devis en attente** : les devis pas encore envoyés (état `draft`) dont le vendeur est l'utilisateur
  Odoo choisi ici (**Mon utilisateur Odoo**). Les devis des autres vendeurs ne comptent pas. Le champ
  prend un nom, ou un utilisateur de la liste que **Chercher dans Odoo** lit : une courte discussion du
  compte, en lecture seule, qui reste dans l'historique et n'entre pas dans la boîte. Vide : l'utilisateur
  avec lequel le serveur Odoo est connecté.
- **Modèle et effort** : ceux du compte par défaut ; un modèle moyen et un effort faible suffisent pour
  une exécution par jour.

### Exécution

- Une routine gérée par la console ([console/brief.py](../console/brief.py), `brief-<compte>`), visible
  dans le panneau Routines (« Brief du matin · Travail ») avec **Réglages** au lieu de l'interrupteur :
  le compte l'active ou la retire. Sans fenêtre, en tête de la boîte, rattrapée au démarrage si la
  console était arrêtée à l'heure prévue. Une réussite n'entre pas dans « À lire » (elle est en tête) ;
  une erreur arrive dans « À faire ». **Lancer maintenant** dans ses réglages, ou **Lancer** dans
  Routines.
- À chaque exécution, la console écrit la demande à partir des réglages du moment : sources cochées,
  critères du compte, critères des projets suivis (avec leur nom), définition des devis en attente.
  Changer un critère vaut dès le brief suivant.
- Toujours avec le preset **Lecture seule**, quel que soit celui du compte : rien n'est écrit ni
  envoyé.
- Claude rend un seul affichage `presenter` d'id `brief` : chiffres clés, chronologie pour l'agenda,
  tableau des devis, liste des mails avec le critère qui les a retenus (« projet Dupont »,
  « expéditeur client-x.fr »). `afficher_resultat` ouvre un mail tel quel, `afficher` un devis dans la
  fenêtre Odoo.
- La boîte montre en tête le brief de chaque compte activé, replié sur ses chiffres clés et à la
  couleur du compte ; Ouvrir le met dans sa fenêtre. Ses choix et boutons reviennent à la session du
  brief, comme ailleurs.

### Sécurité

- Le brief lit des mails, la porte d'entrée principale des injections : d'où le preset Lecture seule
  imposé. Une proposition éventuelle (`proposer`) reste une carte à valider.
- Les critères sont écrits par l'utilisateur, jamais par Claude : un mail piégé ne doit pas pouvoir
  faire ignorer un expéditeur. Plus tard, Claude pourra suggérer un critère depuis le brief
  (« ajouter ce correspondant au projet Dupont »), toujours comme une proposition à valider.
- Les mails sont des données, pas des consignes (déjà dit à Claude dans le prompt système).

La partie sans Claude (validations en attente, échecs de la nuit, rappels et routines du jour) est la
boîte elle-même : elle ne coûte rien et reste là même pour un compte sans brief.

## Actions du compte en boutons

Aujourd'hui, seules les commandes et skills du dossier d'un projet deviennent des boutons
(`project_tools.scan`, épinglés par empreinte). Celles du compte, dans son dossier de configuration
(`commands/<nom>.md` et `skills/<nom>/SKILL.md` de `~/.claude-work` par exemple), ne se lancent qu'en
les tapant après `/`.

- **Pour chaque compte, quel que soit leur nombre, au choix de l'utilisateur** : Configuration →
  Profils → *compte* → **Actions du compte en boutons**, désactivé par défaut. Tant que la case n'est
  pas cochée, la console ne lit pas ces fichiers et ne montre rien ; `/` dans la barre marche comme
  aujourd'hui.
- **Mêmes règles que les actions de projet** : lues par la même fonction (`scan` sur un dossier de
  base), épinglées par empreinte (`action_pins`, clé `compte:<profil>`). Une action nouvelle ou modifiée
  montre son contenu avant de pouvoir être lancée. Modèle et effort réglables par action.
- **Lancement** : une discussion normale `/nom arguments` sur ce compte, dans le dossier choisi dans la
  barre. Si ce dossier est un projet, avec son preset, son modèle et son effort, sinon ceux du compte ;
  le modèle et l'effort de l'action passent avant.
- **Où** : Configuration → Profils → *compte* → Actions, une fois activées (liste, validation,
  réglages, case « Dans le menu ») ; Ctrl+K ; une ligne de boutons dans le menu JARVIS pour celles
  cochées « Dans le menu » ; l'onglet Actions d'un projet, section « Du compte ». Une routine peut en
  lancer une (bloquée si l'action a changé depuis sa validation, comme pour un projet).
- **Même nom dans le projet et dans le compte** : on ne sait pas d'avance laquelle Claude Code
  exécutera. Le bouton ne part que si les deux sont validées, et la console signale le doublon.
- **Hors champ** : les skills des plugins, gérées par Claude Code. `/` dans la barre reste comme
  aujourd'hui (tout ce que propose Claude Code, sans épinglage : son fonctionnement normal).
- `proposer` reste limité au projet : Claude ne propose pas d'action du compte, qui vaudrait pour tous
  ses dossiers.

## Sécurité

- La boîte n'ajoute aucun moyen d'agir : Approuver, Refuser, Relancer et Reprendre sont les gestes des
  fenêtres, par les mêmes routes, avec le même journal.
- Claude ne lit pas la boîte et ne peut y marquer, ignorer ou décider quoi que ce soit.
- Extraits en texte brut, bornés, sans liens actifs.
- Reprendre envoie un message rédigé par la console, jamais un texte venu de Claude ou d'un contenu lu.
- Une action du compte vaut pour tous les dossiers du compte : même validation par empreinte que pour
  un projet, et Claude ne peut pas en proposer.

## Étapes

1. **Serveur** : [console/inbox.py](../console/inbox.py), marques de lecture, validations expirées et
   Reprendre, affichages en attente, exécutions de routines, compteurs dans `state`, événement et API.
   *Fait.*
2. **Interface** ([static/js/inbox.js](../static/js/inbox.js)) : panneau (tiroir, ou fenêtre de l'OS en
   *Intégré au bureau*), bouton **Boîte** et son badge, compteur « à valider », résumé en tête du menu
   JARVIS (et de l'accueil du navigateur) avec un badge sur l'emblème, Ctrl+K, icône de la zone de
   notification (« Boîte de réception (2 à faire · 3 à lire) »), `#boite` dans l'adresse. `S.attention`
   laisse la place aux marques du serveur : une pastille de tâche réduite montre « • » quand elle est
   non lue. *Fait.*
3. **Routines** : réglage « Dans la boîte de réception » (chaque résultat ou seulement les erreurs ;
   une réussite d'une routine « erreurs » ne sonne ni ne notifie), case « En tête de la boîte de
   réception », exécutions non lancées et manquées, Reprendre. *Fait.*
4. **Brief du matin** : réglages par compte (`Profile.brief` dans
   [console/config.py](../console/config.py)), « Mails à suivre » des projets (`Project.mails`, dans la
   fenêtre de réglages du projet), demande écrite par la console ([console/brief.py](../console/brief.py)),
   routine gérée, Chercher dans Odoo, section Aujourd'hui (rappels et routines du jour, comptes au-delà de
   70 %), affichage en tête de la boîte (replié, **Déplier**). *Fait.*
5. **Actions du compte** ([static/js/accountactions.js](../static/js/accountactions.js)) : case par
   compte (`Profile.account_actions`), lecture et épinglage (`compte:<id>`), Configuration → Profils
   (Voir ou Relire, Dans le menu, Lancer), Ctrl+K, boutons « Actions » de l'accueil et du menu JARVIS,
   onglet Actions du projet (« Du compte … »), routines, doublons de nom. *Fait.*
6. **Finitions** : « Pendant ton absence » (verrouillage ou veille de la session dans l'application de
   bureau, `powerMonitor` ; page cachée plus de 15 min dans le navigateur), regroupements. *Fait.*

## Ce qui est vérifié

- **Tests Python** : [tests/test_inbox.py](../tests/test_inbox.py) (entrées, lu et non lu, validation
  décidée depuis la boîte et notée au journal, expiration et Reprendre, annulation qui n'est pas une
  expiration, affichage en attente, routines « erreurs » et en tête, rappels, événement regroupé, API)
  et [tests/test_brief_actions.py](../tests/test_brief_actions.py) (réglages du brief, demande écrite,
  routine qui suit le compte, liste des utilisateurs Odoo, actions du compte : désactivées par défaut,
  validation, modification, doublons, routines, API).
- **Dans Chromium**, sur le serveur de démo (fausse CLI) : badge, panneau, Approuver depuis la boîte
  (journal « utilisateur (boîte de réception) »), Détail, Lu, carte en tête avec son affichage,
  section Aujourd'hui, « Pendant ton absence », bouton d'action du compte sur l'accueil et dans
  Ctrl+K, éditeur de routine, réglages du brief et des actions du compte ; aucune erreur dans la console
  du navigateur.

## À vérifier sous Windows

- Application de bureau, *Intégré au bureau* : la boîte dans sa fenêtre (position gardée), le résumé
  en tête du menu JARVIS et le badge de l'emblème, l'entrée « Boîte de réception » de l'icône de la zone
  de notification.
- « Pendant ton absence » après un verrouillage (Windows+L) et après une mise en veille.
- Approuver et Refuser depuis la boîte une validation qui a aussi sa notification (la notification
  disparaît).
- Un vrai brief : Office 365 et Odoo du compte Travail, **Chercher dans Odoo**, un projet avec des
  « Mails à suivre ».
- Une commande de `~/.claude-work/commands` en bouton, lancée depuis le menu JARVIS et depuis un projet.

Les déclencheurs (étape 4 de la feuille de route) arriveront dans la boîte comme le résultat d'une
routine.

## Pour la suite

- **Déclencheurs** (étape 4) : « fichier déposé dans Factures/ → /facture lancée » devient une entrée,
  avec le même réglage de signalement que les routines.
- **Téléphone** (étape 5) : la page mobile reprend `GET /api/inbox` et les décisions ; c'est là qu'une
  attente de validation plus longue que 30 min aura un sens.
- **Hub d'équipe** (étape 8) : les mentions dans le fil d'un projet.
- **Routines claude.ai** : leurs exécutions restent dans le panneau Routines. Les faire entrer dans la
  boîte demanderait d'interroger claude.ai régulièrement.

## Ce qui ne change pas

La politique d'autorisations, le délai de validation, les fenêtres des discussions, les notifications,
l'historique, les notes et leurs rappels.

## Décisions

- Brief du matin : par compte, désactivé par défaut ; mails importants définis par des critères du
  compte et des projets suivis ; devis en attente = devis pas encore envoyés (`draft`) de l'utilisateur
  Odoo choisi dans les réglages du compte.
- Approuver et refuser depuis la boîte, avec Détail et Ouvrir à côté.
- Actions du compte en boutons : pour chaque compte, activées au choix de l'utilisateur.

## Questions ouvertes

Aucune pour l'instant.
