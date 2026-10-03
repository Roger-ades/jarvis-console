# Application de bureau : JARVIS intégré au bureau

L'application de bureau (Electron, dossier `shell/`) est en place et validée sous Windows (installation,
fenêtre JARVIS). Elle a trois objectifs :

1. **Les fenêtres de JARVIS deviennent de vraies fenêtres de l'OS**, sans le « bureau » de la console
   (son fond) : une discussion, un aperçu ou un affichage se range à côté d'Excel ou d'Outlook,
   s'ancre, passe d'un écran à l'autre et apparaît dans Alt+Tab.
2. **Les sites connectés** (Odoo, SharePoint, Outlook web) s'ouvrent dans des fenêtres de JARVIS, avec
   leur session, ce que Chrome ou Edge seuls ne permettent pas.
3. **JARVIS présent dans l'OS** : une barre flottante appelée par un raccourci global, une icône dans
   la zone de notification (tâches en cours, validations en attente), des notifications natives avec
   Approuver et Refuser.

Le mode navigateur (Chrome ou Edge en mode application) reste disponible : même code, même serveur.
Dans l'application, **Configuration → Interface → Affichage** choisit entre *Intégré au bureau*
(chaque fenêtre est une fenêtre de l'OS) et *Une fenêtre JARVIS* (toute la console dans une fenêtre,
comme dans le navigateur). Changer d'affichage recharge l'interface ; les tâches continuent.

## Installer l'application

Sous Windows, il faut Node.js 22.12 ou plus récent (https://nodejs.org) en plus de ce que demande déjà
la console. Node ne sert qu'à construire l'application.

1. Double-clic sur **`build-app.bat`**. Il installe Electron dans `shell\node_modules` (la première
   fois), construit l'installateur avec electron-builder (`shell\dist\JARVIS-installation-<version>.exe`)
   et le lance. L'installation se fait pour l'utilisateur, sans droits d'administrateur, dans
   `%LOCALAPPDATA%\Programs\jarvis` ; elle crée **JARVIS** dans le menu Démarrer et sur le Bureau.
2. L'application démarre le serveur de la console s'il ne tourne pas (`start.bat --no-browser`), ou
   reprend celui qui tourne. Si l'application de `start-app.bat` tourne déjà, l'application installée
   prend le relais.
3. Configuration → Général : **Ouvrir avec** « Application de bureau JARVIS » fait ouvrir
   l'application par `start.bat` et par le lanceur ; **Démarrer avec la session** la démarre
   discrètement à l'ouverture de session (rien ne s'affiche avant qu'on l'appelle).

Sur Mac : **`build-app.command`** construit l'image disque et l'ouvre ; glisser JARVIS dans
Applications.

**Sans installer** : `start-app.bat` (ou `cd shell && npm install && npm start`) lance l'application
depuis le dossier ; il démarre l'application installée si elle existe. Pour le développement,
`JARVIS_PYTHON` désigne un Python qui lance la console directement, `CONSOLE_PORT` et
`CONSOLE_DATA_DIR` sont lus comme par `python -m console`.

### Au quotidien

- **Ctrl+Alt+J** appelle la **barre JARVIS** en bas de l'écran où se trouve la souris : la barre de
  commande (puces Regard, pièces jointes, contexte, compte, dossier, réglages) et les pastilles de
  tâches. Échap, une demande envoyée ou un clic ailleurs la rangent ; l'épingle la garde affichée.
- Chaque discussion, aperçu, affichage ou fenêtre de différences s'ouvre en fenêtre de Windows.
- La **fenêtre JARVIS** (barre du haut, accueil, tiroirs, configuration, Ctrl+K) s'ouvre par le bouton
  de la barre, l'icône de la zone de notification ou le lanceur. La fermer la range ; fermée, elle le
  reste au démarrage suivant (seule la barre s'affiche).
- Une validation en attente produit une notification avec **Approuver** et **Refuser** ; un clic
  ailleurs sur la notification ouvre la discussion. La pastille de la barre des tâches compte les
  validations en attente.
- Un lien https d'une réponse, ou une page qu'affiche Claude, s'ouvre dans une **fenêtre de site** avec
  sa propre session : on y reste connecté. Configuration → Interface → Application de bureau →
  **Se déconnecter des sites** (ou l'icône de la zone de notification) vide ces sessions.
- « Quitter l'application » (zone de notification) ferme l'application, pas la console : les tâches et
  les routines continuent.

### Mises à jour

L'application installée exécute le `shell/main.js` du dossier de JARVIS (voir « Installation et mises
à jour ») : la mise à jour en un clic, ou `git pull`, met aussi l'application à jour, sans la
réinstaller. Il ne faut relancer `build-app.bat` que lorsque l'application le demande (une notification
au démarrage), c'est-à-dire quand le dossier attend une version d'Electron plus récente que celle
installée ; en attendant, l'application continue avec la copie qu'elle contient.

## Pourquoi Electron, et avant le reste

- **Le modèle de fenêtres conditionne la suite.** Toutes les fenêtres passent par `wm.register`
  ([static/js/wm.js](../static/js/wm.js)) dans un seul document. La boîte de réception,
  `lancer_discussion` et le bloc `formulaire` vont créer ou piloter des fenêtres : les construire sur
  le modèle actuel puis les porter ferait travailler deux fois.
- **La principale difficulté de l'ancien plan disparaît.** Il prévoyait des vues de sites natives
  posées dans la fenêtre unique de la console. Une vue native passe toujours au-dessus du DOM : il
  aurait fallu la masquer et la remplacer par une capture dès qu'un menu ou une autre fenêtre de la
  console passait devant. Avec une vraie fenêtre par site, ce problème n'existe plus.
- **Plusieurs points de la feuille de route en dépendent** : icône de notification et notifications
  pour les validations (boîte de réception), raccourci global, sites connectés.

Les déclencheurs (dossier surveillé, enchaînements) sont côté serveur et peuvent avancer en parallèle.

## Ce que le navigateur seul ne permet pas

- La console est une page : une barre du haut, un « bureau » où `wm.js` dessine les fenêtres
  (tâches, aperçus, affichages, différences), la barre de commande et les pastilles de tâches en bas,
  des tiroirs (Historique, Sessions, Routines, Projet, Notes) et des modales (Configuration, Ctrl+K).
- Les pages HTML montrées (mails, fichiers, résultats d'outils) sont servies depuis une origine séparée
  `http://apercu.localhost:<port>` : styles conservés, pas de scripts, adresses aléatoires qui
  expirent, images du web au clic ([console/content.py](../console/content.py)).
- Claude n'ouvre seul que les sites des **domaines approuvés** ; les autres sont proposés.

Limites : les fenêtres de la console ne se mêlent pas à celles des autres applications ; les sites
qui refusent l'intégration (`X-Frame-Options`, `frame-ancestors` : Odoo, SharePoint, Outlook web)
restent dans le navigateur ; une iframe n'a pas les cookies du site, on y est rarement connecté.

## Le choix : de vraies fenêtres, pas un calque transparent

| | Calque transparent plein écran | Vraies fenêtres de l'OS |
|---|---|---|
| Principe | Une fenêtre transparente couvre l'écran ; les fenêtres JARVIS y sont dessinées comme aujourd'hui | Chaque fenêtre JARVIS est une fenêtre de l'OS |
| Effort | Faible | Moyen |
| Ordre avec les autres applications | Tout ou rien : toutes les fenêtres JARVIS au-dessus d'Excel, ou toutes en dessous | Normal : elles s'intercalent |
| Alt+Tab, ancrage, multi-écran | Non (un calque par écran, une fenêtre ne change pas d'écran) | Oui |
| Clics | Basculer sans cesse le « clic à travers » du calque : fragile | Rien à gérer |
| Sous Windows | Une fenêtre transparente ne se redimensionne pas nativement | Ombres, coins et redimensionnement natifs |

Le calque ferait une démonstration rapide mais un mauvais outil de tous les jours : il est écarté.

## Architecture

### Processus

- **Processus principal (Node).** Il démarre le serveur Python ou réutilise celui qui tourne, comme
  `python -m console` aujourd'hui ([console/__main__.py](../console/__main__.py)) : `/api/ping`, puis
  le jeton de `data/token` pour obtenir un code d'accès à usage unique (`/api/auth/code`). Il possède
  l'icône de notification, les raccourcis globaux, les fenêtres natives, les vues de sites, les
  permissions et les téléchargements.
- **Page moteur : la fenêtre JARVIS.** Elle charge `http://127.0.0.1:<port>/#code=…` et fait tourner
  tout le JavaScript : état, flux temps réel, regard, affichages. Elle reste vivante cachée
  (`backgroundThrottling: false`, pour que Chromium ne ralentisse pas une page qu'il croit en
  arrière-plan) : fermer la fenêtre JARVIS la range, et au démarrage suivant elle reste cachée si on
  l'avait fermée ; barre, fenêtres, notifications et icône de notification continuent sans elle.
- **Fenêtres JARVIS : des fenêtres enfants de la page moteur.** Une fenêtre s'ouvre par
  `window.open` depuis la page moteur ; le processus principal lui donne son apparence
  (`setWindowOpenHandler`, `overrideBrowserWindowOptions`) ; la page moteur déplace dans son document
  l'élément qu'elle a construit (même technique que les fenêtres flottantes de VS Code). Fenêtre
  enfant et moteur ont la même origine et une relation d'ouverture : Chromium les garde dans **le même
  processus de rendu**, avec **un seul contexte JavaScript**.

Ce choix évite trois écueils d'une page indépendante par fenêtre :

- **un seul flux temps réel** : Chromium limite à 6 connexions HTTP/1.1 par origine, toutes fenêtres
  confondues ; avec un flux par fenêtre, la 7ᵉ bloquerait la console ;
- **la mémoire** : dix fenêtres ne coûtent pas dix processus de rendu ;
- **pas de réécriture** : les modules qui supposent une seule page (regard, affichages, délégation des
  clics) continuent de marcher.

Un **preload minimal**, seulement pour la page moteur, expose `window.jarvis` : fenêtres (déplacer,
épingler, réduire, ranger, barre JARVIS), compteurs de l'icône de notification, raccourcis,
notifications, ouverture d'un site, lanceur. Le processus principal revalide chaque appel :
l'expéditeur doit être la page de la console.

### `wm.js` : deux moteurs

L'API ne change pas : `register`, `unregister`, `focus`, `minimize`, `restore`, `togglePin`,
`setSize`, `toggleMax`, `arrange`, `meta`, `onFocus`, `flag`. Le moteur est choisi au démarrage :

- **DOM** (navigateur, ou mode « Bureau JARVIS ») : comme aujourd'hui.
- **Natif** (Electron, `window.jarvis` présent) : `register` ouvre une fenêtre enfant et y place
  l'élément ; `togglePin` devient « toujours au premier plan » ; `toggleMax` et `arrange` passent par
  le processus principal (zones de travail des écrans) ; `onFocus` suit le focus de la fenêtre ; les
  positions restent mémorisées par fenêtre (écran compris), comme aujourd'hui dans `ui-state`.

Ce qu'il faut adapter pour les documents enfants :

- **Styles** : chaque document enfant reçoit la feuille de style et l'attribut de thème.
- **Écouteurs posés sur `document`** : délégation des clics et raccourcis
  ([static/js/app.js](../static/js/app.js)), Échap des aperçus
  ([static/js/viewer.js](../static/js/viewer.js)), sélection du regard
  ([static/js/regard.js](../static/js/regard.js)), glisser-déposer de fichiers sur la page. Ils se
  posent aussi sur chaque document enfant.
- **Éléments ajoutés à la page** : menus (`popupMenu`, `popover` de
  [static/js/taskwin.js](../static/js/taskwin.js)), boîtes de dialogue (`dialog`), toasts. Ils
  s'ajoutent au document de la fenêtre concernée (`anchor.ownerDocument`) et se placent dans ses
  limites (`innerWidth` de cette fenêtre).
- **Le CSS de `.win`** (position absolue, ombre, halo, animation) est neutralisé dans une fenêtre
  native : la fenêtre de l'OS fait ce travail (voir « Style visuel »).

### Ce qui remplace le fond

- **Barre JARVIS** ([static/js/bar.js](../static/js/bar.js)) : la barre de commande (puces Regard,
  pièces jointes et contexte, compte, dossier, réglages) avec les pastilles de tâches, déplacée dans
  une fenêtre sans cadre, transparente, toujours au premier plan (`jarvis-win:barre`). Ctrl+Alt+J
  l'appelle en bas de l'écran où se trouve la souris, comme PowerToys Run ; Échap, une demande
  envoyée ou un clic ailleurs la rangent, sauf si elle est épinglée (pas pendant le choix d'un
  fichier). Elle grandit vers le haut, bord du bas fixe, quand une liste (skills, profils), les
  réglages ou un message s'ouvrent au-dessus. Une boîte de dialogue ou Ctrl+K demandés depuis la barre
  s'ouvrent dans la fenêtre JARVIS, puis la barre revient ; les toasts restent dans la barre.
- **Icône de la zone de notification** : tâches en cours, en file et à valider ; menu Ouvrir JARVIS,
  Nouvelle demande (la barre), Se déconnecter des sites, Quitter. Une pastille sur les boutons de la
  barre des tâches de Windows (`setOverlayIcon`, sur chaque fenêtre de JARVIS) signale une validation
  en attente.
- **Fenêtre JARVIS** : la barre du haut, l'accueil (projets, reprendre), les tiroirs (Historique,
  Sessions, Routines, Projet, Notes), la configuration et Ctrl+K. C'est la page sans son bureau ; elle
  s'ouvre quand on en a besoin (bouton de la barre, icône de notification, boîte de dialogue). Un
  rappel des notes l'amène devant sans prendre le clavier, avec une notification.
- **Toasts** : dans la fenêtre de la dernière action (barre, discussion, fenêtre JARVIS) ; une tâche
  terminée ou en erreur, quand on ne la regarde pas, en notification native.
- **Mode « Bureau JARVIS »** : l'interface actuelle dans une seule fenêtre, pour qui la préfère
  (moteur DOM de `wm.js`).

### Sites connectés

- Une **fenêtre JARVIS par site**, qui contient une `WebContentsView` dans une session propre au site :
  `persist:site:<domaine approuvé>` (sous-domaines compris), sinon `persist:site:<hôte>`. Cookies
  séparés de la console et entre sites : on reste connecté à Odoo et SharePoint sans rien partager
  avec la page de la console. Une deuxième adresse du même site réutilise sa fenêtre.
- Ce qui ouvre une fenêtre de site : un lien https d'une réponse (quand l'aperçu des liens est
  activé), une page qu'affiche Claude (`afficher`, domaines approuvés), « Ouvrir » dans un aperçu web.
- La barre de navigation (précédent, suivant, recharger, adresse, ouvrir dans le navigateur, session)
  est une page de l'application ([shell/site.html](../shell/site.html)) au-dessus de la vue ; elle ne
  reçoit du site que son adresse et son titre, écrits comme du texte. Alt+← / Alt+→ et F5 marchent
  dans la vue.
- **Durcissement des vues** : `sandbox: true`, `contextIsolation: true`, `nodeIntegration: false`,
  aucun preload pour les sites tiers ; les permissions se limitent à l'écriture du presse-papiers et
  au plein écran (jamais caméra, micro, notifications, position). La vue suit toute adresse https
  (les pages de connexion sont souvent ailleurs) ; une adresse http, ou un lien vers un autre site
  ouvert dans un nouvel onglet, part dans le navigateur du système ; une fenêtre surgissante du site
  (connexion, impression) garde sa session. Un téléchargement demande où l'enregistrer.
  « Se déconnecter des sites » ferme leurs fenêtres et vide leurs sessions.
- **Exemple : un devis Odoo.** Claude lit le devis (MCP Odoo) et appelle `afficher` avec
  `https://<odoo>/odoo/sales/<id>` ; la console l'ouvre dans la fenêtre Odoo, déjà connectée.

### Notifications et validations

- Une validation en attente produit une notification native qui montre l'outil et sa cible, avec
  **Approuver** et **Refuser**. Sous Windows, les boutons d'une notification passent par un XML de
  notification (`toastXml`) et une activation par protocole (`jarvis://valider?…`, enregistré par
  l'application) ; sur Mac, par les actions de notification. Pas de notification pendant qu'on
  regarde la discussion ; elle disparaît quand la validation est décidée ailleurs.
- Chaque notification porte un secret à elle (128 bits) : un lien `jarvis://` fabriqué ailleurs (une
  page web, un autre programme) ne décide rien ; l'application revérifie la tâche et la validation,
  puis passe par l'API de la console comme un clic dans la fenêtre (journal compris).
- Approuver depuis une notification reste un clic de l'utilisateur : la règle « rien d'irréversible
  sans son clic » tient. Les validations qui demandent de relire un contenu long (proposition
  d'action, plan) ouvrent leur fenêtre au lieu d'offrir un bouton.

## Style visuel

JARVIS garde son style : l'intérieur des fenêtres est le même HTML et le même CSS. Ce qui change se
trouve aux bords, là où la fenêtre de l'OS prend le relais.

**À l'identique**

- Tout l'intérieur des fenêtres : couleurs, typographie, thème sombre ou clair, chronologie, cartes de
  validation, affichages, différences, aperçus.
- Les en-têtes de fenêtres : compte et projet, titre, statut, boutons de la tâche.
- Le liseré à la couleur du compte et du projet (la barre de 3 px à gauche, le dégradé compte →
  projet).
- La barre de commande, ses puces et les pastilles de tâches, dans la barre JARVIS ; le logo animé ;
  la fenêtre JARVIS (barre du haut, tiroirs, configuration).

**Ce qui change**

| Élément | Aujourd'hui | Fenêtres natives |
|---|---|---|
| Ombre portée, halo de focus | Dessinés autour de la fenêtre, dans la page | L'ombre de Windows ; le focus se marque par une bordure intérieure à la couleur du compte (une couleur de bordure native est à vérifier) |
| Coins arrondis | Rayon de JARVIS | Ceux de Windows 11, un peu plus petits |
| Réduire, agrandir, fermer | Boutons de JARVIS | Boutons natifs aux couleurs de JARVIS (`titleBarOverlay`), pour garder l'ancrage de Windows 11 ; ou les boutons de JARVIS, sans ces dispositions d'ancrage |
| Animation d'ouverture | Celle de JARVIS | Celle de Windows |
| Flou des barres | `backdrop-filter` sur le fond de la console | Rien à flouter derrière une fenêtre de l'OS : la barre JARVIS a un fond presque opaque. Le matériau Mica ou Acrylic de Windows 11 (`backgroundMaterial`) reste à essayer |
| Fond (dégradés, grille de points) | Celui de la console | Le fond d'écran de l'utilisateur ; gardé dans le mode « Bureau JARVIS » |
| Menus contextuels | Peuvent dépasser d'une fenêtre | Restent dans leur fenêtre, repositionnés à l'intérieur |

## Sécurité

- Le jeton de la console ne va jamais dans une vue tierce ; la page moteur et les fenêtres JARVIS sont
  les seules à charger l'origine de la console. La page moteur ne navigue pas ailleurs
  (`will-navigate`) : un lien externe s'ouvre dans le navigateur du système.
- Le preload n'est donné qu'à la page de la console, jamais à une vue de site ; le processus
  principal vérifie l'expéditeur de chaque appel.
- Aucune fenêtre JARVIS n'a Node : `nodeIntegration: false`, `contextIsolation: true`, `sandbox: true`.
- L'origine des aperçus (`apercu.localhost`), la politique d'autorisations et les domaines approuvés
  ne changent pas.

## Installation et mises à jour

- **Construction** : electron-builder (configuration `build` de [shell/package.json](../shell/package.json),
  lancé par `npx` : rien de plus dans `shell/node_modules`). Windows : installateur NSIS par
  utilisateur, en un clic, sans droits d'administrateur, raccourcis « JARVIS » avec l'identifiant
  d'application `local.jarvis.console` (même identité que `app.setAppUserModelId` : fenêtres
  regroupées, notifications). Mac : image disque. Environ 100 Mo.
- **L'application installée exécute le shell du dossier** : son point d'entrée,
  [shell/loader.js](../shell/loader.js), retrouve le dossier de JARVIS (`JARVIS_ROOT`, sinon
  `dossier.json` dans les données de l'application, écrit à chaque démarrage et par `build-app.bat`,
  sinon il le demande une fois) et exécute son `shell/main.js`. La partie Python et l'application se
  mettent donc à jour ensemble, en un clic ou par `git pull`. Si le dossier attend une version
  d'Electron plus récente que celle installée (`devDependencies` de `shell/package.json`),
  l'application continue avec la copie de `main.js` qu'elle contient et demande, par une
  notification, de relancer `build-app.bat`.
- **Pas d'electron-updater** : il lui faudrait un serveur de versions publiées (le dépôt est privé), et
  ce qui change souvent (le shell) suit déjà le dossier ; reste Electron lui-même, que
  `build-app.bat` met à jour quand il le faut.
- **Démarrage** : l'application lance le serveur du dossier (`.venv`, créé au besoin par `start.bat`
  ou `start.command`), sans fenêtre. Le démarrage avec la session (Configuration → Général) lance
  l'application avec `--demarrage` quand elle est enregistrée (`data/app.json`) ; après
  l'installation, le raccourci de démarrage suit l'application installée.
- **Signature** : construit sur le poste même, l'installateur n'est pas marqué comme venant d'Internet
  et SmartScreen ne l'arrête pas. Pour l'installer sur d'autres PC sans avertissement, il faut un
  certificat de signature de code : `CSC_LINK` (fichier .pfx) et `CSC_KEY_PASSWORD` avant
  `build-app.bat`, electron-builder signe alors l'exécutable et l'installateur. Sur Mac, signature et
  notarisation : `CSC_LINK`, `CSC_KEY_PASSWORD`, `APPLE_ID`, `APPLE_APP_SPECIFIC_PASSWORD`,
  `APPLE_TEAM_ID`.
- **Tests** : Playwright pilote Electron (`_electron.launch`) ; les tests Python restent.

## Ce qui est vérifié

**Sous Windows** (par l'utilisateur) : installation d'Electron par `start-app.bat`, ouverture de la
fenêtre JARVIS, fenêtres natives et affichage intégré.

**Sous Linux** (affichage virtuel, Electron 44, piloté par Playwright sur le serveur de démo) :

- fenêtres de l'OS pour les discussions, aperçus, affichages et différences, avec leur titre ;
  **un seul processus de rendu** (10 fenêtres de discussion : environ +260 Mo au total) ; menus et
  boîtes de dialogue dans la fenêtre d'où on les demande ; regard d'une fenêtre à l'autre ; bouton de
  fermeture natif ; les deux affichages et le passage de l'un à l'autre ;
- démarrage discret (`--demarrage`) : rien ne s'affiche, puis tout apparaît quand on appelle JARVIS ;
  enregistrement de l'application (`data/app.json`) et proposition de l'utiliser ;
- fenêtres de sites : session séparée de la console, réutilisation de la fenêtre d'un site,
  navigation, liens vers un autre site dans le navigateur, http refusé, déconnexion ;
- notifications : un lien `jarvis://` fabriqué sans le secret ne décide rien, le bouton Approuver de
  la notification valide (journal : « allow ») ;
- barre JARVIS : elle grandit vers le haut (liste, réglages), Échap la range, l'épingle la garde, une
  demande envoyée crée la tâche et la range, boîte de dialogue et Ctrl+K dans la fenêtre JARVIS puis
  retour de la barre, toasts dans la barre ; rien ne change en affichage *Une fenêtre JARVIS* ;
- fenêtre JARVIS fermée qui reste fermée au démarrage suivant, rappel qui l'amène devant ;
- application empaquetée (electron-builder, cible Linux) : elle retrouve le dossier, exécute son
  `shell/main.js`, retombe sur sa propre copie quand le dossier attend un Electron plus récent, et
  prend le relais de l'application lancée par `start-app.bat` ;
- le mode navigateur ne change pas (scénarios rejoués dans Chromium).

## Points à vérifier sous Windows

- Installateur : `build-app.bat`, raccourcis « JARVIS » (menu Démarrer, Bureau), désinstallation
  (Paramètres → Applications), démarrage avec la session qui suit l'application installée.
- Barre JARVIS : transparence et coins, position sur plusieurs écrans, Ctrl+Alt+J (pas de conflit
  avec d'autres outils), clic ailleurs qui la range.
- Notifications avec Approuver et Refuser (XML de notification, activation par protocole), pastille
  de la barre des tâches.
- Fenêtres de sites : connexion à Odoo, SharePoint, Outlook web (pages de connexion Microsoft,
  fenêtres surgissantes), téléchargements.
- Glisser-déposer de fichiers depuis l'Explorateur vers une fenêtre native.
- Pour la suite : matériau Mica ou Acrylic, couleur de bordure native.

## Étapes

1. **Prototype** : application qui lance le serveur, fenêtres natives, moteur natif de `wm.js` et
   adaptations aux documents enfants, choix de l'affichage. *Fait, validé sous Windows.*
2. **Démarrage avec la session et lanceur** qui ouvrent l'application. *Fait.*
3. **Sites connectés** : fenêtres de sites, sessions séparées, navigation, permissions. *Fait.*
4. **Notifications** avec Approuver et Refuser. *Fait.*
5. **Barre JARVIS** flottante, raccourci global, icône de notification. *Fait.*
6. **Finitions** : fenêtre JARVIS qui reste cachée, rappels, pastille sur chaque fenêtre. *Fait.*
7. **Installateur**, signature, mises à jour de l'application. *Fait ; à essayer sous Windows.*

## Ce qui ne change pas

Le serveur Python, l'API, la politique d'autorisations, l'origine des aperçus, le contenu de toutes
les fenêtres (discussions, aperçus, affichages, différences) et les tests. Le mode navigateur reste
disponible.
