# Feuille de route produit et architecture

## Cap produit

OnionPi doit se comporter comme une appliance, pas comme un serveur Linux à
administrer au quotidien : on la branche, elle dit clairement si la protection
est effective, elle refuse de fournir un faux sentiment de sécurité et elle se
répare ou revient à la version précédente sans écran ni clavier.

Trois critères guident chaque évolution :

1. **Pratique** — installation guidée, état compréhensible, diagnostic et
   récupération locale documentés.
2. **Sûr par défaut** — en cas de doute, le Wi-Fi client est coupé ou confiné ;
   aucun trafic ne sort directement et aucun état écrit par l’application ne
   devient implicitement fiable pour root.
3. **Modulaire** — l’interface consomme des capacités stables ; Tor, les
   transports, le filtrage et la plateforme matérielle restent remplaçables
   derrière leurs adaptateurs.

## Socle consolidé dans cette branche

- Un état de protection unique (`demo`, `degraded`, `contained`, `protected`)
  agrège pare-feu, Tor, DNS et point d’accès. L’interface ne déduit plus la
  sécurité du seul voyant Tor.
- Le point d’accès n’est plus lancé directement par NetworkManager : l’unité
  `onionpi-ap.service` dépend du coupe-circuit. Un échec de chargement nftables
  empêche donc le Wi-Fi client de rester exposé.
- Le remplacement des règles nftables est validé puis appliqué dans une seule
  transaction.
- Les résultats root et la zone de préparation des mises à jour vivent dans
  des répertoires root, séparés de l’espace dont l’application peut renommer
  les entrées.
- L’authentification réserve atomiquement son quota avant scrypt et limite le
  nombre de vérifications simultanées. Les corps HTTP, y compris segmentés,
  sont bornés avant parsing ; un import multipart n’est analysé qu’après
  session et CSRF.
- Logout, expiration et changement de mot de passe révoquent aussi les
  WebSockets. Les archives de mise à jour ont une taille maximale et leur
  manifeste signé est vérifié avant le gros téléchargement.
- L’image Raspberry Pi OS téléchargée est contrôlée par l’empreinte publiée ;
  une image personnalisée exige une empreinte explicite.

## Priorités suivantes

### P0 — rendre la mise à jour entièrement reproductible

- Construire en CI un **wheelhouse Python signé** et l’inclure dans l’archive.
  `install.sh --upgrade` devra installer avec `pip --no-index --find-links`,
  sans télécharger du code après la vérification OpenPGP.
- Remplacer la mise à niveau en place par des versions immuables sous
  `/opt/onionpi/releases/<version>`, un lien `current` basculé atomiquement et
  un journal des mutations système. Le rollback devra également retirer les
  fichiers introduits par une version interrompue.
- Séparer la signature stable du dépôt : clé hors ligne ou environnement de
  publication protégé avec approbation et règles empêchant un workflow modifié
  d’accéder directement à la clé.

Critère de sortie : couper le courant à chaque étape d’une mise à jour laisse
soit l’ancienne version démarrable, soit la nouvelle version complètement
validée, jamais un mélange des deux.

### P1 — rendre l’appareil évident à exploiter

- Assistant de première ouverture : mot de passe administrateur, confirmation
  des interfaces WAN/AP, test du coupe-circuit, vérification de l’heure et
  sauvegarde d’un code de récupération.
- Page « Protection » centrée sur quatre états et une action immédiate :
  réparer Tor, corriger le DNS, relancer le pare-feu ou télécharger le rapport.
- Mode maintenance physique, activé par console ou bouton pendant une fenêtre
  courte, pour récupérer l’appareil sans laisser SSH ou une porte de secours
  ouverte en permanence.
- Sauvegarde chiffrée de la configuration et restauration avec aperçu des
  changements avant application.

Critère de sortie : une personne non spécialiste doit identifier la cause
d’un état non protégé et l’action sûre à prendre en moins de deux minutes.

### P2 — modulariser sans créer un système de plugins privilégiés

- Extraire progressivement les routes de `main.py` en modules `auth`,
  `protection`, `network`, `files`, `chat` et `updates`, injectés via une
  structure `AppServices` testable plutôt que des singletons globaux.
- Définir des interfaces internes pour `TorBackend`, `FirewallBackend`,
  `AccessPointBackend` et `UpdateBackend`. Une plateforme de démonstration et
  une plateforme Raspberry Pi implémentent les mêmes contrats.
- Rendre enfichables uniquement les composants non privilégiés (catalogues de
  ponts, fournisseurs de listes DNS, vues de diagnostic). Toute nouvelle
  capacité root reste un verbe fermé, validé dans un helper installé et signé.
- Versionner les schémas API de statut/configuration et générer les types
  TypeScript depuis OpenAPI afin d’éviter la dérive frontend/backend.

Critère de sortie : remplacer NetworkManager ou ajouter un transport Tor ne
change ni les routes HTTP ni le modèle de protection.

## Garde-fous de livraison

Chaque version doit conserver les tests actuels et ajouter :

- un test sur Raspberry Pi ou VM qui prouve l’absence de trafic direct quand
  Tor, dnsmasq ou le rechargement nftables échoue ;
- une matrice de coupure/interruption pour installation et rollback ;
- un budget de ressources sur Pi (mémoire au repos, connexions scrypt
  simultanées, taille des historiques et des requêtes) ;
- une vérification de l’image et une nomenclature des composants générée avec
  l’archive de publication.

La métrique produit principale n’est pas « Tor connecté », mais le temps passé
en état `protected`, complété par le nombre d’entrées en `contained` et le taux
de récupération sans intervention SSH.
