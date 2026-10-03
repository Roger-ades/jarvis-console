# IHM : améliorations possibles (conception)

Note de travail : ce qui est en place est décrit d'abord ; les pistes ne sont pas commencées. L'objectif est
une IHM puissante, où Claude se sert de la console comme d'un outil et ne la subit pas.

Principe retenu, déjà appliqué par `presenter` : **Claude dit ce qu'il veut montrer ou
demander, la console décide du rendu et de la sécurité.** Il ne pilote jamais l'interface
directement.

## Déjà en place

- **Contexte JARVIS** ([console/presence.py](../console/presence.py)) : un bloc placé en tête
  du prompt système dit à Claude ce qu'est la console (pas de terminal, Markdown mis en forme,
  validations, outils `afficher`, `afficher_resultat`, `presenter`). Il précise aussi le cadre
  de la discussion : compte, projet, dossiers, pièces jointes, preset, routine. Le texte reste
  le même d'un tour à l'autre pour ne pas casser le cache de prompt.
- **Questions de Claude par étapes** (`questionCard` dans
  [static/js/taskwin.js](../static/js/taskwin.js)) : avec plusieurs questions, la carte en
  affiche une à la fois, avec un onglet par question. Les boutons restent visibles, et la carte
  ne s'envoie pas tant qu'une question reste sans réponse. Avant, une 2e question cachée dans
  une zone à faire défiler pouvait passer inaperçue.
- **Notes et rappels** ([static/js/notes.js](../static/js/notes.js), table `notes` de
  [console/store.py](../console/store.py), routes `/api/notes`) : une note est générale ou rattachée
  à un projet. Elle a un compte et peut avoir un rappel. On les trouve dans le bouton « Notes » de
  la barre du haut (badge = rappels en attente), dans l'onglet « Notes » du panneau Projet et dans
  Ctrl+K (« Nouvelle note », « Nouvelle note du projet X »). La première ligne d'une note lui sert
  de titre. Les notes ne sont jamais envoyées à Claude. Un rappel arrivé à son heure s'affiche en
  popup en haut à droite. Il passe au-dessus des modales mais se range à côté d'un tiroir ouvert.
  La popup propose Ouvrir, Plus tard… (10 min, 1 h, 3 h, demain 9:00) et Vu. Elle sonne si les
  sons sont activés et devient une notification système quand la page est en arrière-plan. Toutes
  les pages ouvertes suivent les changements (événement `notes`).
- **Couleurs compte × projet** ([static/js/tint.js](../static/js/tint.js)) : une note générale
  prend la couleur de son compte. Dans un projet, une note, une fenêtre de session, ses aperçus
  et affichages, et les modales et le panneau du projet mêlent la couleur du compte et celle du
  projet. Ils ont un liseré en dégradé du compte vers le projet, plus `--pc`, le mélange des deux
  calculé en OKLCH pour garder une teinte vive. `paint(el, tint)` ([static/js/util.js](../static/js/util.js))
  pose `--pc`, `--pc-a`, `--pc-p` et la classe `tinted`. Un changement de couleur d'un compte ou
  d'un projet repeint aussitôt les fenêtres, les pastilles et les rappels.

### Mode projet : Claude enrichit le projet, l'utilisateur valide

Tout vit dans le dossier du projet, au format de Claude Code. Les règles valent pour les quatre
points qui suivent : Claude propose et l'utilisateur valide ; une modification redemande une
validation ; rien ne dépasse le preset de la discussion.

1. **Routines proposées par Claude** (outil `proposer`,
   [console/project_tools.py](../console/project_tools.py), `_propose` dans
   [console/engine.py](../console/engine.py)). Claude envoie le nom, la planification, la
   consigne ou une action du projet à lancer. La proposition est vérifiée tout de suite :
   planification, nom de l'action, discussion d'un projet. Elle s'affiche ensuite sur une carte
   de validation (`proposalCard` dans [static/js/taskwin.js](../static/js/taskwin.js)) avec deux
   boutons, « Ajouter désactivée » et « Ajouter et activer ». Elle n'est jamais activée sans ce
   clic. La routine prend le compte, le preset et le modèle de la discussion : elle ne peut pas
   en avoir plus. Une routine qui lance une action est bloquée si l'action a changé depuis sa
   validation. Les routines du projet se gèrent dans l'onglet « Actions » de la page projet.
2. **Actions du projet en boutons** ([static/js/actions.js](../static/js/actions.js)). Ce sont
   les commandes `.claude/commands/<nom>.md` et les skills `.claude/skills/<nom>/SKILL.md` du
   dossier : on peut aussi les taper dans Claude Desktop ou la CLI.
   - Elles apparaissent dans l'onglet « Actions » de la page projet et dans Ctrl+K.
   - Le champ `argument-hint` devient un formulaire de saisie.
   - Un lancement ouvre une discussion normale du projet (`/nom arguments`) : même compte,
     même preset, mêmes validations.
   - Chaque action est épinglée par l'empreinte de ses fichiers, scripts d'une skill compris.
     Une action nouvelle ou modifiée (« à valider », « modifiée ») montre son contenu avant de
     pouvoir être lancée. Tant qu'elle n'est pas validée, elle n'est pas non plus citée à Claude.
   - Claude peut proposer une action avec `proposer`. Si elle en remplace une, la carte montre
     aussi le contenu actuel. Le fichier n'est écrit qu'après l'accord de l'utilisateur, qui
     vaut validation.
3. **Section « ## Ce projet »** du prompt système ([console/presence.py](../console/presence.py)).
   Elle donne à Claude les actions validées et les routines du projet, et lui rappelle qu'il
   peut en proposer. Le texte ne change que si les actions ou les routines changent, pour
   garder le cache de prompt.
4. **Mini-application** (bloc `application` de `presenter`,
   [console/content.py](../console/content.py)). Claude écrit une petite page HTML :
   calculateur, simulateur, tri, saisie…
   - Elle tourne sur l'origine des aperçus, dans un cadre `sandbox` sans `allow-same-origin`.
   - Elle n'a ni réseau (CSP `connect-src 'none'`, pas de cadres, de workers ni de
     formulaires sortants), ni jeton de la console, ni accès aux fichiers.
   - Le pont `jarvis.envoyer(...)` envoie un message à Claude dans la discussion (40 au plus
     par application).
   - Le pont `jarvis.action(nom, arguments)` propose de lancer une action du projet. Le
     lancement est confirmé dans une boîte de dialogue, puis suit le chemin d'un bouton,
     validation de l'action comprise.
   - La console n'écoute que ce cadre précis, et seulement quand l'utilisateur est dedans : le
     focus est dans l'application, avec au plus un envoi toutes les 1,5 s.

## Pistes, de la plus utile à la plus ambitieuse

| Piste | Ce que ça apporte | Points d'attention |
|---|---|---|
| **« Ce que je regarde »** : l'aperçu ouvert, le texte sélectionné et l'affichage actif partent avec le message | « Corrige ce paragraphe », « explique ce tableau » sans copier-coller. C'est le gain le plus net au quotidien. | Le joindre au message, pas au prompt système, pour ne pas casser le cache. Montrer dans la barre de saisie ce qui sera envoyé et permettre de le retirer. |
| **Bloc `formulaire` dans `presenter`** : champs typés et réponse structurée | Claude prépare un devis Odoo ou un mail pré-rempli ; on corrige puis on valide. Plus efficace qu'une suite de questions. | La réponse revient comme un message de l'utilisateur. Une action irréversible reste soumise à la validation habituelle. |
| **`demander_validation`** : aperçu ou différences, relié aux validations existantes | Une décision claire avant une action sensible, au lieu d'un refus brut. | Ne doit jamais permettre de valider à la place de l'utilisateur. |
| **Vue « Consommation »** : par compte, projet et discussion, cache lu et écrit, compactions, part des sous-agents | Voir où partent les tokens. L'analyse du 2 octobre l'a montré : c'est le mode équipe qui coûte, pas la console. | Les données viennent des transcriptions (`<config>/projects/…`) : pas de nouvel appel. |
| **Signaler ce qui fait perdre le cache** : pause de plus d'une heure, compaction automatique | Comprendre une consommation inattendue. Proposer « Garder au chaud » sur une discussion importante. | La compaction est normale : il s'agit d'informer, pas d'alarmer. |
| **`notifier` / progression** dans la barre des tâches | Les tâches longues en arrière-plan tiennent l'utilisateur au courant. | Limiter la fréquence des notifications. |
| **Coordination entre discussions** : voir les autres, leur écrire, proposer une discussion qui s'ouvre dans sa propre fenêtre | Un vrai poste multi-agents, et non des sous-agents invisibles. | La piste la plus puissante, mais aussi la plus coûteuse en tokens. Lancer une discussion reste soumis à validation. |
| **Proposer une note de projet** (consignes, mémoire), que l'utilisateur valide | Les routines et les actions se proposent déjà : il reste le texte du projet. | Toujours une proposition, montrée en différences, jamais une écriture directe. |
| **Outils-scripts** (voir plus bas) | Des fonctions déterministes du projet que Claude appelle comme des outils. | Du code exécuté : la validation la plus stricte. |

## Outils-scripts (à venir)

Conception seulement : rien n'est commencé. L'objectif est de donner au projet ses propres
outils, appelés par Claude comme `afficher` ou `presenter`. Ce sont des fonctions déterministes :
lire un fichier de cours, calculer une valeur de portefeuille, convertir un export…

- **Écriture.** Claude écrit le script dans le dossier du projet, par exemple
  `.claude/outils/<nom>.py` avec un fichier `<nom>.json`. Ce fichier déclare la description et
  les paramètres typés, au format `inputSchema` d'un outil MCP. Il passe par `proposer`
  (`quoi: "outil"`), comme une action : il n'écrit jamais lui-même le fichier.
- **Validation.** La carte montre le code complet, les paramètres et l'interpréteur. Pour un
  remplacement, elle montre aussi l'ancien code. Rien n'est écrit sans le clic.
- **Épinglage.** Comme une action, l'outil est épinglé par l'empreinte du script et de sa
  déclaration. S'ils changent, l'outil disparaît de la liste de Claude jusqu'à une nouvelle
  validation.
- **Exposition.** Les outils validés du projet s'ajoutent au serveur MCP intégré `jarvis`
  (`mcp__jarvis__<projet>_<nom>`), seulement dans les discussions de ce projet. Leur
  description pèse dans le contexte : en limiter le nombre (une dizaine) et la taille.
- **Exécution.** Elle passe par le même contrôle qu'une commande Bash : le preset de la
  discussion, les chemins protégés, les refus permanents et la validation si le preset la
  demande. Les conditions :
  - le dossier du projet comme répertoire courant ;
  - les arguments passés en JSON sur l'entrée standard, jamais dans une ligne de commande ;
  - un délai limite et une taille de sortie bornée ;
  - pas d'héritage des jetons de la console.

  En preset « lecture », un outil qui écrit reste refusé. La console ne peut pas le savoir à
  l'avance : la déclaration porte `ecrit: true|false`, affichée sur la carte de validation.
  Un outil déclaré en lecture seule qui écrit quand même est arrêté par la politique de
  fichiers habituelle.
- **Journal.** Validation, appels et erreurs sont écrits dans le journal d'audit, comme les
  actions.

Points ouverts :
- isoler davantage l'exécution (pas de réseau par défaut) : sous Windows, c'est délicat sans
  conteneur ;
- choisir les interpréteurs autorisés (Python du projet, Node) ;
- montrer le résultat d'un outil avec `afficher_resultat`.

## Risques résiduels du mode projet

- **Injection persistante.** C'est le risque principal : un mail ou une page piégés poussent
  Claude à proposer une action ou une routine malveillante. La carte montre toujours le contenu
  complet, et rien n'est écrit ni activé sans clic. Rester attentif aux propositions qui
  arrivent juste après la lecture d'un contenu extérieur.
- **Écriture directe dans `.claude/commands`.** Claude peut écrire dans ces fichiers sans
  passer par `proposer`, si son preset l'autorise à écrire dans le dossier. L'action passe
  alors « modifiée » : elle n'est plus lancée (ni par un bouton, ni par une routine) ni citée à
  Claude avant d'avoir été relue.
- **Les commandes tapées à la main** (`/nom` dans la barre de saisie, Claude Desktop, CLI) ne
  passent pas par l'épinglage : c'est le fonctionnement normal de Claude Code. Seuls les
  boutons, Ctrl+K et les routines de la console vérifient l'empreinte.
- **Mini-application : fuites restantes.**
  - WebRTC : les constructeurs sont retirés avant le code de Claude, et les cadres et workers
    sont interdits. Ces parades réduisent le risque sans le garantir.
  - Préchargement DNS : un lien créé dynamiquement peut déclencher une résolution DNS. Les
    indications `dns-prefetch`/`preconnect` écrites dans la page sont retirées, mais pas
    celles qu'un script ajoute.
  - `'unsafe-eval'` et les boîtes `alert()` sont permis. Sans conséquence dans le bac à sable,
    mais une application peut agacer. Il suffit de la fermer.
  - Un message envoyé par `jarvis.envoyer` arrive à Claude comme une saisie de l'utilisateur,
    préfixée par « [Affichage …] ». La page vient de Claude, mais ses données peuvent venir
    d'un contenu lu : Claude doit traiter ce texte comme une donnée.

## Petites améliorations repérées

- **Questions** : garder les réponses déjà cochées si la fenêtre se recharge. Écrire les
  réponses dans le journal (`approval_done`), qui ne note aujourd'hui que « allow » ou « deny ».
- **Validations** : quand plusieurs cartes attendent, les présenter elles aussi l'une après
  l'autre, avec un compteur, plutôt qu'empilées dans la zone limitée à 55 % de la fenêtre.
- **Longs contenus** dans les cartes : replier les descriptions au-delà de quelques lignes.

## À éviter

- **Laisser Claude modifier la configuration, les permissions ou les domaines approuvés.** Il
  pourrait s'accorder des droits, ou y être poussé par un mail ou une page piégés. C'est aussi
  pourquoi une routine proposée garde le preset de la discussion.
- **Des extensions d'interface avec du JavaScript dans la console elle-même.** Le code de
  Claude ne tourne que dans la mini-application isolée, jamais dans la page de la console (qui
  détient le jeton).
- **Le contrôle fin de l'interface** (déplacer des fenêtres, cliquer dans la console) : c'est
  un gadget qui coûte des tokens.
- **Multiplier les outils** : chaque description pèse dans le contexte de toutes les
  discussions. Mieux vaut quelques outils polyvalents (`presenter` et ses blocs) qu'une dizaine
  d'outils spécialisés.

Le risque principal est l'injection : un contenu lu par Claude qui le pousse à se servir de
l'interface contre l'utilisateur. Les règles actuelles restent la base de toute nouvelle
piste : rien d'irréversible sans un clic de l'utilisateur, les adresses non approuvées lui sont
proposées, une nouvelle discussion demande sa validation.

## Ordre proposé

1. « Ce que je regarde ».
2. Bloc `formulaire`.
3. Vue « Consommation » et signalement des pertes de cache.
4. `demander_validation`, puis la coordination entre discussions.
