# Passage à Electron : JARVIS intégré au bureau

Note de conception. C'est la prochaine étape de la [feuille de route](feuille-de-route.md), juste après
« Ce que je regarde » et les différences de fichiers. **Un prototype est en place** (`shell/`, voir
« Essayer le prototype ») ; il reste à le valider sous Windows. Trois objectifs :

1. **Les fenêtres de JARVIS deviennent de vraies fenêtres de l'OS**, sans le « bureau » de la console
   (son fond) : une discussion, un aperçu ou un affichage se range à côté d'Excel ou d'Outlook,
   s'ancre, passe d'un écran à l'autre et apparaît dans Alt+Tab.
2. **Les sites connectés** (Odoo, SharePoint, Outlook web) s'ouvrent dans des fenêtres de JARVIS, avec
   leur session, ce que Chrome ou Edge seuls ne permettent pas.
3. **JARVIS présent dans l'OS** : une barre flottante appelée par un raccourci global, une icône dans
   la zone de notification (tâches en cours, validations en attente), des notifications natives.

Le mode navigateur actuel (Chrome ou Edge en mode application) reste disponible : même code, même
serveur. Dans l'application, **Configuration → Interface → Affichage** choisit entre *Intégré au bureau*
(chaque fenêtre est une fenêtre de l'OS) et *Une fenêtre JARVIS* (toute la console dans une fenêtre,
comme dans le navigateur). Changer d'affichage recharge l'interface ; les tâches continuent.

## Essayer le prototype

Sous Windows, il faut Node.js 22.12 ou plus récent (https://nodejs.org) en plus de ce que demande déjà
la console.

1. Double-clic sur **`start-app.bat`**. La première fois, il installe Electron dans `shell\node_modules`
   (environ 100 Mo).
2. L'application démarre le serveur de la console s'il ne tourne pas (`start.bat --no-browser`), ou
   reprend celui qui tourne, et ouvre la fenêtre JARVIS.
3. En mode *Intégré au bureau* (par défaut), chaque discussion, aperçu, affichage ou fenêtre de
   différences s'ouvre en fenêtre de Windows. **Ctrl+Alt+J** ramène la fenêtre JARVIS sur une
   nouvelle demande. Fermer la fenêtre JARVIS la range dans la zone de notification ; « Quitter
   l'application » ferme l'application, pas la console.

Sur Mac ou Linux : `cd shell && npm install && npm start`. Pour le développement, `JARVIS_PYTHON`
désigne un Python qui lance la console directement, `CONSOLE_PORT` et `CONSOLE_DATA_DIR` sont lus comme
par `python -m console`.

Le prototype ne fait pas encore : la barre JARVIS flottante (la barre de commande reste dans la fenêtre
JARVIS), les fenêtres de sites connectés, les notifications avec Approuver et Refuser, l'installateur
et la signature. La boîte de dialogue Ctrl+K et la configuration s'ouvrent dans la fenêtre JARVIS.

## Pourquoi maintenant

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

## Où on en est (navigateur seul)

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
- **Fenêtre moteur, cachée.** Elle charge `http://127.0.0.1:<port>/#code=…` et fait tourner tout le
  JavaScript actuel : état, flux temps réel, regard, affichages. `show: false`, et
  `backgroundThrottling: false` pour que Chromium ne ralentisse pas une page qu'il croit en
  arrière-plan.
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
épingler, réduire, ranger), compteurs de l'icône de notification, raccourcis, notifications, ouverture
d'un site. Le processus principal revalide chaque appel : l'expéditeur doit être la page de la console.

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

- **Barre JARVIS** : la barre de commande actuelle (puces Regard, pièces jointes et contexte, compte,
  dossier, réglages) avec les pastilles de tâches, dans une petite fenêtre sans cadre. Un raccourci
  global l'appelle et Échap la range, comme PowerToys Run. Raccourci réglable ; il faut en choisir un
  par défaut qui n'entre pas en conflit avec Windows ni PowerToys.
- **Icône de la zone de notification** : tâches en cours, validations en attente, limites des
  comptes ; menu Nouvelle demande, Ouvrir JARVIS, Arrêt d'urgence, Quitter. Une pastille sur le bouton
  de la barre des tâches de Windows (`setOverlayIcon`) signale une validation en attente.
- **Fenêtre JARVIS** : la barre du haut, les tiroirs (Historique, Sessions, Routines, Projet, Notes),
  la configuration et Ctrl+K. C'est la page actuelle sans son bureau ; elle s'ouvre quand on en a
  besoin. Ctrl+K a aussi son raccourci global.
- **Toasts** : dans la barre JARVIS, ou en notification native quand aucune fenêtre JARVIS n'a le
  focus.
- **Mode « Bureau JARVIS »** : l'interface actuelle dans une seule fenêtre, pour qui la préfère
  (moteur DOM de `wm.js`).

### Sites connectés

- Une **fenêtre JARVIS par site**, qui contient une `WebContentsView` (Electron 30 et plus) dans une
  session propre au site : `session.fromPartition("persist:odoo")`, `"persist:o365"`… Cookies séparés
  de la console et entre sites : on reste connecté à Odoo et SharePoint sans rien partager avec la
  page de la console.
- La barre de navigation (précédent, suivant, adresse, ouvrir dans le navigateur) est dessinée par
  JARVIS dans l'en-tête de la fenêtre ; la vue occupe le reste. Seuls les menus de cette fenêtre
  doivent tenir dans son en-tête, ou devenir des menus natifs.
- **Durcissement des vues** : `sandbox: true`, `contextIsolation: true`, `nodeIntegration: false`,
  aucun preload pour les sites tiers ; `setPermissionRequestHandler` refuse caméra, micro,
  notifications, géolocalisation… ; `will-navigate` et `setWindowOpenHandler` gardent la vue dans les
  domaines approuvés et renvoient le reste au navigateur du système ; `will-download` demande avant
  chaque téléchargement ; « Se déconnecter des sites » vide les sessions.
- **Exemple : un devis Odoo.** Claude lit le devis (MCP Odoo) et appelle `afficher` avec
  `https://<odoo>/odoo/sales/<id>` ; la console l'ouvre dans la fenêtre Odoo, déjà connectée.

### Notifications et validations

- Une validation en attente produit une notification native qui montre l'outil et sa cible, avec
  **Approuver** et **Refuser**. Sous Windows, les boutons d'une notification passent par un XML de
  notification (`toastXml`) et une activation par protocole (`jarvis://…`, enregistré par
  l'application) ; sur Mac, par les actions de notification. À valider dans le prototype.
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
| Flou des barres | `backdrop-filter` sur le fond de la console | Pour la barre JARVIS, le matériau Mica ou Acrylic de Windows 11 (`backgroundMaterial`, à vérifier), qui floute ce qu'il y a derrière : bureau, autres applications |
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

- **Coquille Electron** : un dossier `shell/` du dépôt (`package.json`, processus principal, preload),
  empaqueté avec electron-builder (installateur Windows par utilisateur, sans droits
  d'administrateur ; application Mac). Environ 100 Mo. L'outillage Node ne sert qu'à construire la
  coquille.
- **La partie Python ne change pas de mode de mise à jour** : `git pull` en un clic, comme aujourd'hui.
  La coquille, qui change rarement, a ses propres mises à jour (electron-updater).
- **Démarrage** : la coquille lance le serveur du dépôt (`.venv`, créé au besoin par `start.bat` ou
  `start.command`), sans fenêtre. Elle remplace le démarrage à l'ouverture de session
  (`app.setLoginItemSettings`) et le lanceur ; un identifiant d'application regroupe ses fenêtres
  sous l'icône JARVIS dans la barre des tâches.
- **Signature** : sous Windows, SmartScreen avertit tant que l'exécutable n'est pas signé (certificat
  de signature de code) ; sur Mac, la notarisation est nécessaire.
- **Tests** : Playwright sait piloter Electron (`_electron.launch`) ; les tests Python restent.

## Ce que le prototype valide déjà

Vérifié avec Electron 44 sous Linux (affichage virtuel), piloté par Playwright sur le serveur de démo :

- une discussion, un aperçu, un affichage ou une fenêtre de différences s'ouvrent en fenêtres de l'OS,
  avec leur titre (statut, discussion, compte) ;
- **un seul processus de rendu** : avec 10 fenêtres de discussion ouvertes, toujours un processus de
  rendu, pour environ +260 Mo au total (rendu logiciel) ;
- menus (⋯) et boîtes de dialogue (renommer, annuler une modification) s'ouvrent dans la fenêtre d'où
  on les demande ;
- regard d'une fenêtre à l'autre : texte sélectionné dans un aperçu natif, puce dans la fenêtre de la
  discussion, message envoyé avec le fichier et l'extrait ;
- le bouton de fermeture natif passe par la console (une discussion en cours demande d'abord) ;
- les deux affichages, l'option de la configuration, la proposition de recharger, la relance de
  l'application ;
- le mode navigateur ne change pas (scénarios rejoués dans Chromium).

## Points à valider sous Windows

- Barre de titre avec boutons natifs (`titleBarOverlay`) : couleurs, ancrage de Windows 11, Alt+Tab,
  passage d'un écran à l'autre, mémorisation des positions, coins arrondis.
- Démarrage du serveur par `start.bat --no-browser` quand il ne tourne pas ; icône de la zone de
  notification et pastille de la barre des tâches quand une validation attend.
- Glisser-déposer de fichiers depuis l'Explorateur vers une fenêtre native, et entre fenêtres.
- Raccourci Ctrl+Alt+J (pas de conflit avec d'autres outils).
- Pour la suite : notification avec Approuver et Refuser (XML de notification, activation par
  protocole), matériau Mica ou Acrylic, couleur de bordure native.

## Étapes proposées

1. **Prototype** : coquille qui lance le serveur, fenêtres natives, moteur natif de `wm.js` et
   adaptations aux documents enfants, choix de l'affichage. *Fait ; à valider sous Windows.* Dans le
   prototype, la page moteur est la fenêtre JARVIS elle-même, cachée quand on la ferme.
2. **Finitions du moteur natif** selon les retours sous Windows ; page moteur vraiment cachée.
3. **Barre JARVIS**, raccourci global, icône de notification, pastille de la barre des tâches.
4. **Fenêtre JARVIS** : barre du haut, tiroirs, configuration, Ctrl+K.
5. **Sites connectés** : fenêtres de sites, sessions séparées, domaines approuvés, navigation,
   permissions, téléchargements.
6. **Notifications** avec Approuver et Refuser.
7. **Installateur**, démarrage avec la session, signature, mises à jour de la coquille.

## Ce qui ne change pas

Le serveur Python, l'API, la politique d'autorisations, l'origine des aperçus, le contenu de toutes
les fenêtres (discussions, aperçus, affichages, différences) et les tests. Le mode navigateur reste
disponible.
