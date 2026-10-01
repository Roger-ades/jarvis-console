# Passage à Electron (à voir plus tard)

Note de travail, rien n'est commencé. Objectif : pouvoir **naviguer dans un vrai site
connecté** (Odoo, SharePoint, Outlook web…) à l'intérieur de la console, de façon isolée,
ce que Chrome ou Edge seuls ne permettent pas.

## Où on en est (navigateur seul)

La console tourne dans Chrome ou Edge (mode application). Déjà en place :

- les pages HTML montrées (mails, fichiers, résultats d'outils) sont servies depuis une
  origine séparée `http://apercu.localhost:<port>` : styles conservés, pas de scripts,
  adresses aléatoires qui expirent, images du web au clic (`console/content.py`) ;
- Claude n'ouvre seul que les sites des **domaines approuvés** (Configuration → Sécurité) ;
  les autres sont proposés (« Claude veut ouvrir… ») ;
- `afficher_resultat` montre un mail ou un enregistrement que Claude a lu, sans qu'il le
  recopie (`console/results.py`).

Limites qui restent, et qu'Electron lèverait :

- **Sites qui refusent l'intégration** (`X-Frame-Options`, `frame-ancestors`) : Odoo
  (back-office), SharePoint, Outlook web ne s'affichent pas dans une fenêtre de la
  console, d'où le bouton « Ouvrir dans le navigateur ».
- **Connexion** : un site affiché dans une iframe est un tiers, ses cookies sont bloqués
  ou partitionnés par le navigateur ; on y est rarement connecté.
- **Pas de vraie navigation** dans le mini-navigateur : ni précédent/suivant fiable, ni
  barre d'adresse, ni contrôle des téléchargements ou des permissions par site.

## Ce qu'apporterait Electron

- **`WebContentsView`** (Electron 30 et plus, remplace `BrowserView`) : une vue native
  posée dans la fenêtre, hors du DOM. N'importe quel site s'y affiche, `X-Frame-Options`
  ne s'applique pas.
- **Une session par site** : `session.fromPartition("persist:odoo")`,
  `"persist:o365"`… Cookies séparés de la console et entre sites : on reste connecté à
  Odoo et SharePoint, sans rien partager avec la page de la console.
- **Durcissement des vues** : `sandbox: true`, `contextIsolation: true`,
  `nodeIntegration: false`, aucun preload pour les sites tiers.
- **Contrôle fin** :
  - `setPermissionRequestHandler` : refuser caméra, micro, notifications, géolocalisation… ;
  - `will-navigate` et `setWindowOpenHandler` : rester dans les domaines approuvés et
    renvoyer le reste au navigateur du système ;
  - `will-download` : demander avant chaque téléchargement et choisir le dossier ;
  - `webRequest` : bloquer les traqueurs connus.
- **Exemple : un devis Odoo**. Claude lit le devis (MCP Odoo) et appelle `afficher` avec
  `https://<odoo>/odoo/sales/<id>` ; la console l'ouvre dans la partition Odoo, déjà
  connectée, avec une barre de navigation.

## Esquisse d'architecture

- Le **processus principal** lance le serveur Python (comme `start.bat` aujourd'hui), puis
  charge `http://127.0.0.1:<port>` dans la fenêtre principale (`contextIsolation`, sans
  Node).
- Un **preload minimal** pour la seule page de la console, qui expose
  `window.jarvis.openSite({ url, bounds })`, `moveSite`, `closeSite`, `navigate`. Le
  processus principal revalide chaque appel : l'origine de l'expéditeur doit être la
  console, et l'URL un domaine approuvé (relu depuis l'API de la console) ou une demande
  validée par l'utilisateur.
- Le **gestionnaire de fenêtres** (`static/js/wm.js`) dessine toujours le cadre (barre de
  titre, boutons) et envoie le rectangle du contenu ; le processus principal y place la
  `WebContentsView`.
- **Difficulté principale** : une vue native est toujours au-dessus du DOM. Quand une
  autre fenêtre, un menu ou une modale de la console passe devant, il faut masquer la vue
  et afficher à sa place une capture (`webContents.capturePage()`), puis la remontrer.
- **Repli** : sans Electron (`window.jarvis` absent), tout marche comme aujourd'hui dans
  Chrome ou Edge.
- Les aperçus HTML restent sur `apercu.localhost` (ou un protocole maison
  `jarvis-apercu://` via `protocol.handle`) : la logique côté serveur ne change pas.

## Points d'attention

- **Sécurité** : le jeton de la console ne va jamais dans une vue tierce ; les vues n'ont
  jamais le preload ; prévoir « Se déconnecter des sites » (vider les partitions).
- **Poids et mises à jour** : environ 100 Mo par installation. Le shell Electron change
  rarement, donc la partie Python peut continuer à se mettre à jour comme aujourd'hui ;
  `electron-updater` seulement pour le shell.
- **Signature** : notarisation sur Mac ; sous Windows, SmartScreen tant que l'exécutable
  n'est pas signé.
- **Tests** : Playwright sait piloter Electron (`_electron.launch`).

## Étapes proposées

1. Shell minimal : lance le serveur, ouvre la même interface (parité avec Chrome), lanceur
   et démarrage avec la session.
2. Vues de sites : `WebContentsView`, partitions par site, domaines approuvés, barre de
   navigation, permissions et téléchargements.
3. Cohabitation avec le gestionnaire de fenêtres : superposition, réduction, plein écran.
4. Paquets d'installation (`electron-builder`) pour Windows et Mac.

Ce qui ne change pas : le serveur Python, l'API, la politique d'autorisations et l'origine
des aperçus.
