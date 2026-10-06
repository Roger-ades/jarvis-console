# IHM : améliorations possibles (conception)

Note de travail : ce qui est en place est décrit d'abord, puis ce qui est en cours et à venir, chacun avec son statut. L'objectif est
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
- **« Ce que je regarde »** ([static/js/regard.js](../static/js/regard.js),
  [console/regard.py](../console/regard.py)) : l'aperçu ou l'affichage au premier plan et le texte
  sélectionné (aperçu, affichage, réponse de Claude) partent avec le message, jamais dans le prompt
  système. Une puce « Regard » dans la barre utilisée le montre, sa croix le retire ; il est consommé
  à l'envoi. La sélection est citée comme une donnée. Un affichage ou un résultat d'une autre
  discussion du même compte part avec son contenu.
- **Différences et annulation des fichiers** ([console/changes.py](../console/changes.py),
  [static/js/changes.js](../static/js/changes.js)) : copie avant chaque écriture des outils de
  fichiers (hook `PreToolUse`, reprise à la validation), enregistrée au résultat si le fichier a
  changé. Bouton « +n −m » sur l'action, menu ⋯ → Fichiers modifiés, Annuler / Rétablir avec contrôle
  que le fichier n'a pas changé depuis ; Claude l'apprend au message suivant.
- **Le plan de Claude** (`renderPlan` dans [static/js/taskwin.js](../static/js/taskwin.js)) : quand
  l'agent principal écrit un plan (`TodoWrite`), une bande « Plan » sous l'en-tête de la fenêtre
  montre l'avancement (3/7), une barre et l'étape en cours (son intitulé « en cours » quand Claude
  le donne) ; un clic déplie toutes les étapes. Le statut d'une discussion active porte le compteur
  (« En cours · 3/7 ») partout où il s'affiche, et le titre de la fenêtre native aussi. La liste
  d'un sous-agent ne remplace pas celle de l'agent principal.
- **Modifier un fichier texte** ([static/js/viewer.js](../static/js/viewer.js)) : l'aperçu d'un texte,
  Markdown, CSV, JSON, YAML ou format proche a un bouton Modifier. Le fichier s'édite dans la fenêtre
  (le Markdown et le CSV reviennent en aperçu au bouton Aperçu) et Ctrl+S l'enregistre. Fermer ou
  revenir à l'aperçu avec des modifications demande confirmation. Si le fichier change sur le disque
  pendant l'édition, Jarvis le signale et ne remplace la version du disque qu'avec accord. Un binaire,
  un fichier trop gros, un type qui n'est pas du texte ou un chemin protégé ne s'écrit pas.
- **Ouvrir un fichier au bon endroit** ([static/js/highlight.js](../static/js/highlight.js)) : `afficher`
  accepte `passage` (quelques mots exacts) et `page`. L'aperçu (Word, Excel, texte, Markdown, mail) fait
  défiler jusqu'au passage et le surligne ; un PDF s'ouvre à la page. La recherche ignore les accents,
  la casse et les espaces : un passage coupé par un retour à la ligne, du gras ou des cellules est
  retrouvé. `chercher_documents` donne la page de chaque passage d'un PDF (l'index la garde) et rappelle
  comment montrer un passage. Depuis Ctrl+K, le document s'ouvre au passage trouvé avec les mots
  cherchés surlignés (Précédent / Suivant quand il y en a plusieurs). Le lien « Affiché : » de la
  discussion rouvre le même endroit.
- **Désigner un élément** (`pickOf` dans [static/js/regard.js](../static/js/regard.js)) : un clic
  droit sur une ligne de tableau (affichage, aperçu Excel ou CSV, réponse de Claude), une barre, un
  point de courbe ou une part de graphique, une carte, un chiffre clé, un champ de fiche, une étape,
  un résultat ou une image propose « Demander à Claude à propos de ceci ». L'élément est entouré, la
  puce « Regard » le nomme (« point « T3 » · affichage … ») et sa description (en-têtes et valeurs,
  séries du graphique) part avec le message, citée comme une donnée. Un affichage marque ce qui se
  désigne avec `data-pick` (la description) et `data-pick-label` ; une ligne de tableau se décrit
  seule à partir des en-têtes. Maj + clic droit garde le menu du navigateur.
- **Widgets du bureau** ([console/widgets.py](../console/widgets.py), [static/js/widgets.js](../static/js/widgets.js)) :
  le bouton « Épingler au bureau » d'un affichage (dans la discussion ou sa fenêtre) le garde dans une
  colonne à droite du bureau, sous les fenêtres, même discussion fermée (12 au plus, `kv` « widgets »).
  Un widget suit son affichage : tout appel à `presenter` avec le même `id`, dans une discussion du même
  compte, remplace son contenu. « Actualiser » demande à Claude une nouvelle version dans une discussion
  à part (même compte, dossier, modèle et autorisations que l'originale ; la demande d'origine est citée
  comme donnée). Le menu ⋯ règle une actualisation automatique (toutes les heures, chaque matin, en
  semaine) : une routine « Widget · titre » qui ne signale que ses erreurs dans la boîte ; détacher le
  widget supprime sa routine. En mode « Intégré au bureau » (Electron), où la page de JARVIS est cachée,
  chaque widget est une fenêtre à lui sur le bureau de Windows : transparente et sans bordure (la carte garde
  l'allure qu'elle a sur le bureau JARVIS), hors de la barre des tâches,
  affichée sans prendre le clavier ; on la déplace par son en-tête et on la redimensionne par ses bords,
  et sa place est retenue (préférences du bureau, `prefs.widgets`). « Ranger » et la superposition ne la touchent pas.
  La croix de son en-tête, comme Alt+F4, le retire du bureau (comme « Détacher du bureau »). Electron ne sait pas la clouer sous toutes les
  fenêtres : c'est une fenêtre ordinaire qui ne passe devant que si on clique dessus (Win+D la masque).
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
   Elle donne à Claude les actions validées, les routines, les critères de mails (dont la consigne)
   et les sources du brief. « Où j'en suis » se répond tout de suite : `BRIEF.md`, les mails selon
   ces critères, les sources cochées, puis un affichage avec des cartes. Ajouter ou corriger une
   consigne, une routine, une action ou une tâche passe par une carte d'approbation. Un mail lu
   peut être mis sur une carte ; il n'écrit rien. Le texte ne change que si l'utilisateur change
   le projet, pour garder le cache de prompt.
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

## Brief de projet (en cours)

Le brief du compte reste le tour d'horizon du matin : mails, devis Odoo en brouillon, agenda. Chaque dossier a en plus **son** brief, plus fin, lancé seul. « Déménagement » est un projet comme un autre.

Le brief qui part tout seul **lit et prépare**. Il affiche le rapport. Il propose une ligne à retenir ou une tâche à créer. Le clic **écrit**. Un mail lu reste une donnée : il ne s'inscrit pas tout seul dans les suivis.

### Deux niveaux

- **Compte** (Configuration → Profils → Brief du matin) : inchangé dans son rôle. Ses critères de mails gagnent les mots du corps, comme ceux du projet.
- **Projet** (réglages du projet → Brief du projet) : désactivé par défaut. Heure, jours, modèle, effort, autorisations. Sources à cocher : mails du projet, tâches Office 365, calendrier, un ou plusieurs projets Odoo et leurs tâches. Une routine « Brief · *nom* », en tête de la boîte, lançable depuis les réglages ou depuis Routines. Le compte du projet (ou le compte par défaut) la porte. Les autorisations sont Lecture seule par défaut ; un autre preset du compte s'applique à ce brief, avec les validations qu'il demande.

Les critères « Mails à suivre » servent les deux. **Suivre dans le brief** les fait compter dans le brief du compte. La source Mails du brief de projet les reprend même si cette case est décochée. Mots de l'objet, mots du corps, expéditeur, dossier, consigne.

### Fichier de suivi

`BRIEF.md` à la racine du dossier. La console le lit à chaque lancement et le donne à Claude comme une donnée, jamais comme une consigne. Claude propose des lignes dans un bloc `choix` dont la question est exactement « Ajouter au fichier de suivi ». Les lignes cochées sont ajoutées par la console, pas par Claude. En Lecture seule, la session ne crée ni tâche Office 365 ni tâche Odoo : elle les propose. Avec un autre preset, écrire, envoyer ou créer suit ce preset et la validation habituelle.

Créer vraiment la tâche ou le mail, après ce suivi, passe par le bloc `formulaire` (ci-dessous) : une feuille préremplie, puis la validation habituelle.

### Depuis un mail affiché

Dans l'aperçu d'un mail, **Suivre** ajoute l'adresse de l'expéditeur (lue par la console dans l'en-tête, pas recopiée par Claude) aux mails suivis du projet choisi. Le projet de la discussion est proposé. Un clic, une adresse.

### Cartes du rapport

Le brief demande un bloc `cartes` : une carte par mail, tâche ou oubli, avec des boutons. La console exécute le clic.

- **Ouvrir** retrouve le résultat d'outil déjà lu (l'extrait doit y figurer tel quel) et ouvre le mail. Pas de nouveau tour.
- **Retenir** ajoute la ligne à `BRIEF.md`.
- **Terminée** écrit la ligne tout de suite. En Lecture seule, Office 365 et Odoo ne sont pas modifiés. Avec un autre preset, la console redemande à Claude de clôturer cette tâche, sous les validations de ce preset.
- **Routine** et **Consigne** ouvrent la carte de validation habituelle. Rien n'est enregistré avant « Ajouter » ou « Ajouter la consigne ». Pour corriger une consigne, la carte dit « Remplacer la consigne » et montre l'ancienne. Une consigne ne vient que d'une demande de l'utilisateur : un mail lu ne l'écrit pas.

Le bloc choix « Ajouter au fichier de suivi » reste pour les lignes qui n'ont pas leur carte.

### À l'écran

Configuration → Interface → **Afficher le rapport dans une fenêtre**, décoché par défaut : le rapport reste en tête de la boîte, comme aujourd'hui. Coché, la fenêtre du rapport s'ouvre à la fin du brief (compte ou projet). Chaque projet peut forcer oui ou non.

Les notifications à boutons Approuver et Refuser restent celles des validations. Les propositions du brief sont les choix de son affichage.

### Ce qui reste pour la suite de ce brief

La synchro qui crée ou clôt la tâche dans Office 365 ou Odoo sans repasser par Claude. Le bouton « Terminée » écrit la ligne dans `BRIEF.md` tout de suite ; si le brief n'est pas en lecture seule, il redemande à Claude de la clôturer, avec les validations du preset. Les tâches Office 365, les brouillons et les relances d'un projet se lisent déjà dans l'onglet Suivi ; le même fil pour le compte, hors projet, reste à faire.

## Bloc formulaire

En place. Claude prépare une feuille (un mail, un devis) avec `presenter`, bloc `formulaire` : champs typés (`texte`, `zone`, `nombre`, `date`, `liste`, `case`), préremplis. On corrige, on valide. La console renvoie les champs comme un message de l'utilisateur, préfixé par `[Affichage « … »]`. Le formulaire n'envoie ni ne crée rien : le mail ou l'enregistrement qui suit reste sur la validation habituelle. Tant qu'il n'est pas validé, il attend dans la boîte, comme un choix.

Un mail lu reste une donnée. Les champs proposés se corrigent ici, puis le clic Valider les renvoie. Claude ne change ni la configuration, ni les permissions, ni les domaines approuvés.

## Claude et JARVIS ne font qu'un (à venir)

Direction actuelle : un chef d'entreprise qui a beaucoup à gérer parle à JARVIS, et JARVIS ouvre,
range, retrouve et relie lui-même. Statuts et ordre dans [feuille-de-route.md](feuille-de-route.md)
(axe 0). Règle : un geste d'interface sans conséquence (ouvrir une fenêtre) se fait tout de suite ;
ce qui s'enregistre (rattacher, lier, synchroniser) passe par une carte d'approbation.

### Le projet suit la conversation

*Fait.* « On va travailler dans le projet Network » tapé dans la barre JARVIS :

1. **Avant l'envoi, sans modèle** (`static/js/mention.js`). La barre reconnaît le nom ou le dossier
   d'un projet connu dans la demande, après « projet », « dossier » ou « dans » (« projet Network »,
   « dans le dossier network », sans accents ni majuscules ; le plus long nom l'emporte, un nom
   ambigu ne propose rien). Une puce « Projet : Network » apparaît, à la couleur du projet, avec une
   croix pour la retirer : elle remplit le sélecteur de dossier comme un choix à la main (compte,
   preset, modèle du projet). La croix remet la barre comme avant et écarte le projet pour ce texte ;
   choisir un autre dossier ou un autre compte à la main garde ce choix. Pas de token, pas de délai.
2. **Au lancement.** La fenêtre de la session s'ouvre dans le dossier du projet, et le panneau du
   projet s'ouvre à côté, sur l'onglet Discussions où la nouvelle apparaît en tête. Réglable :
   Configuration → Interface → « Ouvrir le projet avec ses discussions » (le panneau ne s'ouvre que
   pour un projet nommé dans la demande, pas pour un dossier choisi à la main).
3. **Mention implicite ou en cours de discussion.** Claude passe par l'outil `projet`
   (`console/project_nav.py`, `Engine._project_tool`) :
   - `ouvrir` (nom du projet) : ouvre le panneau du projet, tout de suite, même si le réglage
     ci-dessus est coupé (c'est demandé) ;
   - `rattacher` (nom du projet) : une carte « Passer cette discussion dans le projet Network »
     (dossier, règles du projet qui s'appliqueront) ; au clic, la console déplace la session à la fin
     du tour (`Engine.move_task` : une discussion en cours ne se déplace pas), puis la suite continue
     dans le dossier du projet. La discussion garde son preset : elle n'obtient jamais plus de droits.
     Un message de suite envoyé entre-temps attend le déplacement, puis part dans le nouveau dossier.

   La liste des projets (noms, dossiers) est dans la section « JARVIS » du prompt système, fixe tant
   que les projets ne changent pas, pour garder le cache.

### L'activité du projet

*À faire.* « Sur quel fichier on a travaillé en dernier dans le projet Network ? » : l'outil `projet`
(`activite`) répond à partir des données de la console, sans fouiller le dossier ni lancer de
commande :

- les dernières discussions du projet (titre, date, état, compte) ;
- les fichiers modifiés par Claude (`console/changes.py`), avec la discussion et l'heure ;
- les fichiers ouverts en aperçu ou affichés (`afficher`), et ceux joints à une demande ;
- les fichiers du dossier récemment modifiés sur le disque (l'index des documents connaît leur date).

Le résultat est une donnée, filtrée par les autorisations de la discussion comme une lecture (un
chemin interdit n'apparaît pas). Claude peut enchaîner avec `afficher` ; la console peut aussi
montrer la réponse en cartes (`presenter`) avec « Ouvrir » et « Reprendre la discussion ».

### Odoo relié au projet

*Fait (`console/odoo_link.py`, `static/js/odoo.js`).* Deux chemins pour lier un projet Odoo :

- **En le demandant à Claude**, dans une discussion du projet (« lie le projet Odoo Network V2 à ce
  projet ») : Claude cherche le projet dans Odoo (lecture, `project.project`), puis le propose avec
  `proposer` (`quoi: "odoo"`, `projets_odoo` : identifiant et nom lus dans Odoo, `remplace` pour
  remplacer les liens existants). La carte montre les projets à lier, ceux déjà liés (ou ceux qui ne
  le seront plus) ; rien n'est enregistré avant le clic, et rien ne change dans Odoo.
- **Depuis l'onglet « Suivi » du panneau Projet** : « Lier un projet Odoo… » cherche par nom (ou les
  plus récents) et liste les projets trouvés, avec client et nombre de tâches ; on coche, on lie.
  « Délier » retire un lien. L'onglet s'appelait « Odoo » ; il réunit maintenant Odoo et Office 365.

Les liens sont enregistrés dans `Project.odoo` (identifiant, nom, serveur Odoo du compte s'il y en a
plusieurs ; dix au plus). Le brief du projet et chaque discussion du projet les connaissent : une
tâche Odoo créée depuis le dossier va dans l'un d'eux.

L'onglet « Suivi » montre les tâches et sous-tâches (`parent_id`) des projets liés, en arbre : étape,
échéance (en rouge si dépassée), responsables, priorité ; les terminées sont masquées par défaut.
Un clic ouvre la tâche dans Odoo (site connecté), un clic droit la montre à Claude (Regard). La
lecture se fait à l'ouverture de l'onglet si les tâches datent de plus de 6 heures, ou avec
« Actualiser ».

La console n'a pas de client Odoo à elle : elle lit par une courte discussion sans fenêtre du compte
du projet (Haiku, preset lecture, sans session gardée), dont elle lit la réponse JSON, puis la
supprime. Ces discussions de réglage n'apparaissent pas dans les discussions du projet. La réponse
est une donnée : ce qui ne ressemble pas à une tâche est écarté.

Créer, modifier ou clôturer une tâche Odoo reste une demande à Claude, sous les validations du
preset (le bouton « Terminée » du brief en est le modèle).

### Office 365 dans le suivi

*En cours (`console/office_link.py`, `static/js/office.js`).* Le même onglet **Suivi** montre, sous
les tâches Odoo, ce qu'il y a à faire pour ce projet dans Office 365 : tâches, mails laissés en
brouillon, relances. La console n'a pas de client Microsoft à elle : elle lit par une courte
discussion sans fenêtre du compte du projet (Haiku, preset lecture), comme pour Odoo. Un brouillon
n'est jamais envoyé. Le clic ouvre la page quand l'adresse est connue ; le clic droit la montre à
Claude.

Ce fil existe d'abord dans un projet. Le même, pour le compte entier et hors projet, reste à faire.
Créer ou clôturer une tâche dans Office 365 sans repasser par Claude aussi.

### Un dossier du projet synchronisé avec OneDrive ou SharePoint

*À faire.* La synchronisation reste le travail du client OneDrive de Windows : il gère le hors-ligne,
les conflits et les suppressions. JARVIS ne recopie jamais de fichiers par Microsoft Graph.

- **Voir.** La console reconnaît les dossiers synchronisés (racines OneDrive et SharePoint déclarées
  par le client dans le registre de l'utilisateur) : le panneau Projet indique « Synchronisé avec
  OneDrive · Ades » ou « Sur ce PC seulement » pour le dossier du projet et ses dossiers ajoutés.
- **Demander.** « Synchronise le dossier Devis avec le Drive » : Claude propose (`proposer`,
  `quoi: "lien"`) l'une des deux voies, sur une carte :
  - le dossier est sur le PC seulement : le déplacer dans OneDrive (la carte montre la destination ;
    le déplacement est fait par la console après le clic, le projet suit son nouveau chemin) ;
  - le dossier est dans une bibliothèque SharePoint ou un OneDrive partagé : la console ouvre le lien
    de synchronisation du client OneDrive (`odopen://`), puis ajoute le dossier local au projet une
    fois qu'il apparaît.
- Un fichier « en ligne seulement » reste reconnu par l'index (seul son nom est indexé).

## Regard partout

*Partiel.* Aujourd'hui, une sélection compte dans une fenêtre de discussion, un aperçu lu par la
console (texte, Markdown, Word, Excel, mail `.eml`) et un affichage (dans la discussion, sa fenêtre,
la modale ou un widget). Ce qui manque, et comment :

- **Les panneaux et fenêtres de la console** (Projet, Notes, Historique, boîte de réception, Ctrl+K,
  fenêtres de dialogue) : toute sélection devient un regard « texte sélectionné dans Notes » (son
  origine pour titre), au lieu d'être ignorée (`sourceOf` dans
  [static/js/regard.js](../static/js/regard.js)). Un résultat de Ctrl+K désigné devient le fichier,
  la discussion ou la note qu'il montre.
- **Les cadres** : PDF, pages web, mails HTML. La console ne lit pas ce qui y est sélectionné :
  - les pages et mails HTML servis par l'origine des aperçus reçoivent un petit script de la console
    qui envoie la sélection (bornée, texte seul) par `postMessage` à la fenêtre parente, qui ne
    l'accepte que de ce cadre ;
  - les PDF passent par pdf.js (une couche de texte) au lieu du lecteur du navigateur : la sélection
    devient lisible, avec la page, et le passage surligné par `afficher` reste possible ;
  - à défaut, dans l'application de bureau, Ctrl+C dans un cadre puis la barre propose « Joindre le
    texte copié » (le presse-papiers n'est lu qu'à ce clic).
- **En mode intégré**, la puce se montre dans la barre JARVIS quelle que soit la fenêtre où le texte
  a été sélectionné (aujourd'hui, seulement dans la barre de la fenêtre qui a le focus), et Ctrl+Alt+J
  ouvre la barre avec la puce déjà prête.
- Un clic droit sur une sélection propose « Demander à Claude à propos de ceci », comme pour un
  élément désigné.

Ce qui ne change pas : la sélection est citée comme une donnée, bornée, et jamais dans le prompt
système ; le regard ne donne aucun droit sur le fichier.

## Archivage des discussions

*Fait, sauf la purge (à trancher).* L'historique et la liste des discussions d'un projet grossissent vite.

- **Archiver** : « Archiver » dans le menu ⋯ de la fenêtre ; dans l'historique, une case par ligne
  puis « Archiver (n) » ; dans l'onglet Discussions du projet, « Archiver » sur la ligne ou « Archiver
  les terminées ». La discussion quitte les listes courantes, l'écran d'accueil (« Reprendre »), la
  boîte de réception et la barre des tâches ; sa fenêtre se ferme. Une discussion en cours ne
  s'archive pas.
- **Retrouver** : le filtre « Courantes / Archivées / Toutes » de l'historique, le bouton « Archivées
  (n) » de l'onglet Discussions du projet (avec la date d'archivage et « Désarchiver »), et Ctrl+K,
  qui les trouve dès qu'on tape (groupe « Archivées », après les autres). Les sessions de Claude ne
  sont pas touchées : rouvrir une discussion archivée ou y écrire la désarchive, et la reprise
  continue la même session.
- **Archivage automatique** : Configuration → Historique, « Archiver les discussions inactives depuis
  N jours » (0 = jamais, par défaut ; 14 jours conseillé). La passe tourne au démarrage, après la
  purge, puis toutes les heures. Jamais une discussion en cours, en attente d'une validation ou d'un
  clic, gardée au chaud, épinglée, programmée, ni celle d'un widget. Chaque passe est inscrite au
  journal d'audit.
- **La purge** (`history.retention_days`, 90 jours aujourd'hui) supprime toujours les discussions
  terminées, archivées comprises. À trancher : avec l'archivage, ne plus purger que les archives, et
  seulement sur demande (« Supprimer les archives de plus de N jours », désactivé par défaut).
- Côté serveur : un champ `archived` (date, 0 sinon) sur la tâche, `PATCH /api/tasks/{id}`
  (`archived`) pour une, `POST /api/tasks/archive` (`ids`, `archived`) pour plusieurs ; la recherche
  renvoie `archived` ; l'événement `task` met à jour toutes les pages.

## Pistes, de la plus utile à la plus ambitieuse

Le brief de projet, ci-dessus, est le chantier en cours ; « Claude et JARVIS ne font qu'un », Regard
partout et l'archivage sont les suivants.

| Piste | Ce que ça apporte | Points d'attention |
|---|---|---|
| **`demander_validation`** : aperçu ou différences, relié aux validations existantes | Une décision claire avant une action sensible, au lieu d'un refus brut. | Ne doit jamais permettre de valider à la place de l'utilisateur. |
| **Vue « Consommation »** : par compte, projet et discussion, cache lu et écrit, compactions, part des sous-agents | Voir où partent les tokens. L'analyse du 2 octobre l'a montré : c'est le mode équipe qui coûte, pas la console. | Les données viennent des transcriptions (`<config>/projects/…`) : pas de nouvel appel. |
| **Signaler ce qui fait perdre le cache** : pause de plus d'une heure, compaction automatique | Comprendre une consommation inattendue. « Garder au chaud » existe (à la main) ; l'étape suivante, garder au chaud ou compacter automatiquement avant l'expiration, est chiffrée dans [feuille-de-route.md](feuille-de-route.md#reprise-dune-discussion-inactive--faut-il-compacter-avant-lexpiration-du-cache-). | La compaction est normale : il s'agit d'informer, pas d'alarmer. Compacter n'est rentable que si la discussion est reprise. |
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

1. ~~« Ce que je regarde »~~ (fait, avec les différences et l'annulation des fichiers).
2. Brief de projet (en cours, voir plus haut) : lancé seul, sources mails (objet et corps), tâches Office 365, calendrier, projets Odoo, fichier `BRIEF.md`, suivi d'un interlocuteur depuis un mail, affichage du rapport en fenêtre.
3. ~~Bloc `formulaire`~~ (fait).
4. Claude et JARVIS ne font qu'un (en cours) : le projet suit la conversation (fait), l'activité du projet, puis Regard partout, Odoo relié au projet (fait, onglet Suivi), Office 365 dans ce même onglet (lecture commencée : tâches, brouillons, relances), dossier OneDrive.
5. Archivage des discussions (fait ; reste à trancher la purge des archives).
6. Vue « Consommation », signalement des pertes de cache et reprise des discussions inactives.
7. `demander_validation`, puis la coordination entre discussions.

La feuille de route d'ensemble (déclencheurs, boîte de réception, validations depuis le téléphone,
Electron…) est dans [feuille-de-route.md](feuille-de-route.md).
